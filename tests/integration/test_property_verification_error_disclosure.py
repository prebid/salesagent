"""GH #1802: adagents verification echoes the adcp library's raw error
text back to the admin and into the DB.

``src/services/property_verification_service.py``'s ``_verify_property_async``
catches ``AdagentsNotFoundError`` / ``AdagentsTimeoutError`` /
``AdagentsValidationError`` and interpolates ``str(e)`` directly into the
message it BOTH persists to ``AuthorizedProperty.verification_error`` (via
``_update_verification_status``) AND returns to the admin caller as
``(False, msg)``.

The severity question the ticket leaves open -- "can these SDK error strings
carry a resolved IP address, the dialled URL, or a range classification" --
is answered YES by reading the installed ``adcp==6.6.0`` source directly:
``adcp.signing.jwks``'s SSRF validator raises
``SSRFValidationError(f"resolved IP {ip} is in a reserved range")`` /
``SSRFValidationError(f"cloud metadata IP {ip} blocked")``, and
``adcp.adagents._owned_pinned_client`` re-raises that verbatim inside
``AdagentsValidationError(f"SSRF validation failed for {url!r}: {e}")`` --
which is exactly the exception class our code catches and echoes. This test
reproduces precisely that message shape (rather than a synthetic placeholder)
so the case is grounded in a real, confirmed disclosure path, not a
hypothetical.

``fetch_adagents`` (the adcp library's own network call) is the one thing
mocked here -- it is an external boundary this service does not control, so
grading OUR code's handling of whatever the library raises is the right
level, matching this repo's SSRF-opacity precedent at
``tests/integration/test_outbound_http.py::test_validate_url_refusal_envelope_hides_the_resolved_address_and_the_reason``
(same "the refusal reason must not name the resolved address or its range"
obligation, at a different seam). Everything else -- the database read/write,
the service's own message-building logic -- is real.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from adcp import AdagentsValidationError
from sqlalchemy import select

from src.core.database.models import AuthorizedProperty
from src.services.property_verification_service import PropertyVerificationService
from tests.factories import AuthorizedPropertyFactory, TenantFactory
from tests.harness._base import IntegrationEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

TENANT_ID = "property_verification_disclosure"

# A real message shape confirmed reachable from adcp==6.6.0's SSRF validator
# (adcp/signing/jwks.py) via adcp.adagents._owned_pinned_client's re-raise --
# see module docstring. Not a placeholder: this is what the library actually
# says when a publisher's domain resolves (e.g. via DNS rebinding) to a
# cloud-metadata address.
LEAKED_IP = "169.254.169.254"
LIBRARY_MESSAGE = f"SSRF validation failed for 'https://leaky.example.com/.well-known/adagents.json': cloud metadata IP {LEAKED_IP} blocked"

FORBIDDEN_FRAGMENTS = (LEAKED_IP, "cloud metadata", "SSRF validation failed")


@pytest.fixture
def seeded_property(integration_db):
    """A committed AuthorizedProperty for the service to verify and update."""
    with IntegrationEnv() as env:
        tenant = TenantFactory(tenant_id=TENANT_ID)
        prop = AuthorizedPropertyFactory(
            tenant=tenant,
            publisher_domain="leaky.example.com",
            verification_status="pending",
        )
        yield env, prop


class TestVerificationErrorDisclosure:
    """The admin-facing message and the persisted DB row must not repeat the
    adcp library's raw exception text.
    """

    def test_returned_error_does_not_leak_library_internals(self, seeded_property):
        env, prop = seeded_property
        service = PropertyVerificationService()

        with patch(
            "src.services.property_verification_service.fetch_adagents",
            new_callable=AsyncMock,
            side_effect=AdagentsValidationError(LIBRARY_MESSAGE),
        ):
            is_verified, error_message = service.verify_property(
                tenant_id=TENANT_ID, property_id=prop.property_id, agent_url="https://sales-agent.example.com"
            )

        assert is_verified is False
        assert error_message is not None
        for fragment in FORBIDDEN_FRAGMENTS:
            assert fragment not in error_message, (
                f"{fragment!r} leaked into the admin-facing message: {error_message!r}"
            )

    def test_persisted_verification_error_does_not_leak_library_internals(self, seeded_property):
        env, prop = seeded_property
        service = PropertyVerificationService()

        with patch(
            "src.services.property_verification_service.fetch_adagents",
            new_callable=AsyncMock,
            side_effect=AdagentsValidationError(LIBRARY_MESSAGE),
        ):
            service.verify_property(
                tenant_id=TENANT_ID, property_id=prop.property_id, agent_url="https://sales-agent.example.com"
            )

        session = env.get_session()
        session.rollback()
        row = session.scalars(
            select(AuthorizedProperty).filter_by(tenant_id=TENANT_ID, property_id=prop.property_id)
        ).one()

        assert row.verification_status == "failed"
        assert row.verification_error is not None
        for fragment in FORBIDDEN_FRAGMENTS:
            assert fragment not in row.verification_error, (
                f"{fragment!r} leaked into the persisted verification_error: {row.verification_error!r}"
            )
