"""Auth AUTH_REQUIRED raises must carry error.json's top-level suggestion (#1417 round-8 review, items 3-4).

Core Invariant: every AUTH_REQUIRED rejection carries a non-empty TOP-LEVEL
``suggestion`` in the two-layer wire envelope (AdCP 3.1, pinned ref
v3.1-04f59d2d5, static/schemas/source/core/error.json — "Suggested action to
resolve the error"). ``require_identity`` already passes
``suggestion=AUTH_REQUIRED_SUGGESTION``; its four siblings in
``src/core/auth.py`` (invalid-token, ``resolve_principal_or_raise``,
``require_principal_id``, ``require_tenant``) raised with the hint only in
message text, leaving the graded top-level ``suggestion`` field EMPTY
(PR #1417 review round 8, item 4 → #1417 round-8 review item 4).

Wire-first per tests/CLAUDE.md § Error Verification Policy: the
``require_principal_id`` case drives the REAL A2A wire (it is the
account-resolution boundary the PR newly routes onto); the remaining helpers
are graded on the envelope the production boundary translator builds for
their raise (``build_two_layer_error_envelope`` — the same builder every
transport dispatcher calls).
"""

import pytest

from src.core.exceptions import AdCPError, build_two_layer_error_envelope
from tests.helpers import assert_envelope_shape

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def _assert_auth_required_with_suggestion(envelope: dict) -> None:
    from tests.harness.transport import extract_wire_suggestion

    assert_envelope_shape(envelope, "AUTH_REQUIRED", recovery="correctable")
    suggestion = extract_wire_suggestion(envelope)
    assert suggestion, (
        "Expected a non-empty TOP-LEVEL suggestion in the AUTH_REQUIRED wire "
        f"envelope (error.json @v3.1-04f59d2d5), got: {envelope}"
    )


class TestRequirePrincipalIdA2ASuggestion:
    """require_principal_id rejection on the real A2A wire carries a suggestion."""

    def test_missing_principal_a2a_envelope_carries_suggestion(self, integration_db):
        """An identity with no principal_id rejected on the A2A wire must
        produce the AUTH_REQUIRED envelope WITH a top-level ``suggestion`` —
        parity with ``require_identity``.
        """
        from tests.factories import PrincipalFactory, TenantFactory
        from tests.harness.media_buy_list import MediaBuyListEnv
        from tests.harness.transport import Transport

        with MediaBuyListEnv(tenant_id="t1", principal_id="p1") as env:
            TenantFactory(tenant_id="t1")
            identity = PrincipalFactory.make_identity(principal_id=None, tenant_id="t1")

            result = env.call_via(Transport.A2A, identity=identity)

            assert result.is_error, (
                f"A missing principal_id must be rejected on the A2A wire, got success payload: {result.payload!r}"
            )
            result.assert_wire_error(
                "AUTH_REQUIRED",
                recovery="correctable",
                require_suggestion=True,
            )


class TestInvalidTokenA2ANoDisclosure:
    """A present-but-invalid token rejected on the REAL A2A wire discloses no tenant id.

    Non-disclosure is a WIRE contract — what the buyer actually receives — so it
    is graded here on ``result.wire_error_envelope`` (the A2A rejection's
    ``data``), not on an envelope rebuilt in-process from a caught exception.
    ``get_media_buys`` is not a DISCOVERY_SKILL, so ``on_message_send`` requires
    auth and ``_run_a2a_handler`` takes its real-token branch: the AuthContext
    state is populated from the identity's token and the production chain runs
    for real (``_get_auth_token`` → ``_resolve_a2a_identity`` →
    ``resolve_identity`` → ``reject_invalid_token``). No identity monkeypatch.
    """

    def test_invalid_token_a2a_envelope_does_not_disclose_tenant(self, integration_db):
        from tests.factories import PrincipalFactory, TenantFactory
        from tests.harness.media_buy_list import MediaBuyListEnv
        from tests.harness.transport import Transport
        from tests.helpers import assert_no_tenant_disclosure

        tenant_uuid = "5c1d2f6a-9b3e-4a71-8f04-2d6b7c9e1a35"
        with MediaBuyListEnv(tenant_id=tenant_uuid, principal_id="p1") as env:
            TenantFactory(tenant_id=tenant_uuid)
            # A token that resolves NO principal — the invalid-token rejection,
            # not the missing-token one (that gate fires earlier and is a
            # different raise site).
            identity = PrincipalFactory.make_identity(
                principal_id="p1",
                tenant_id=tenant_uuid,
                auth_token="not-a-real-token",
            )

            result = env.call_via(Transport.A2A, identity=identity)

            assert result.is_error, (
                f"An invalid token must be rejected on the A2A wire, got success payload: {result.payload!r}"
            )
            # require_real_wire: without it the A2A dispatcher would REBUILD an
            # envelope from the reconstructed exception and this would pass even
            # though the buyer received a bare protocol error with no AdCP
            # envelope — a synthesized envelope masquerading as the wire, which
            # is the same in-process grading the review flagged.
            result.assert_wire_error(
                "AUTH_REQUIRED",
                recovery="correctable",
                require_suggestion=True,
                require_real_wire=True,
            )
            assert_no_tenant_disclosure(result.wire_error_envelope, tenant_uuid)


class TestAuthHelperFamilySuggestion:
    """The remaining AUTH_REQUIRED raise sites in src/core/auth.py carry a suggestion.

    Each case drives the production helper and asserts on the envelope the
    production boundary translator builds for its raise — the same
    ``build_two_layer_error_envelope`` every transport dispatcher calls.
    """

    def test_resolve_principal_not_found_carries_suggestion(self, integration_db):
        from src.core.auth import resolve_principal_or_raise
        from tests.factories import TenantFactory
        from tests.harness._base import BareIntegrationEnv

        with BareIntegrationEnv(tenant_id="auth_sugg_t1") as env:
            TenantFactory(tenant_id="auth_sugg_t1")
            env.get_session()  # commit factory data

            with pytest.raises(AdCPError) as exc_info:
                resolve_principal_or_raise("nonexistent-principal", tenant_id="auth_sugg_t1")

        _assert_auth_required_with_suggestion(build_two_layer_error_envelope(exc_info.value))

    def test_require_tenant_missing_carries_suggestion(self):
        from src.core.auth import require_tenant
        from tests.factories import PrincipalFactory

        identity = PrincipalFactory.make_identity(tenant=None)

        with pytest.raises(AdCPError) as exc_info:
            require_tenant(identity)

        _assert_auth_required_with_suggestion(build_two_layer_error_envelope(exc_info.value))

    def test_invalid_token_carries_suggestion(self, integration_db):
        """get_principal_from_context with an invalid token (require_valid_token=True)
        raises AUTH_REQUIRED whose envelope must carry the top-level suggestion.

        FAST IN-PROCESS GUARD, not the wire pin. ``get_principal_from_context`` is
        the ``auth.py`` sibling of ``resolve_identity``: it is called directly by
        sync MCP tools and has no transport of its own here, so this test can only
        grade the envelope ``build_two_layer_error_envelope`` rebuilds from the
        caught exception. That is one step removed from what a buyer receives.
        The buyer-facing contract is pinned on the REAL A2A wire by
        ``TestInvalidTokenA2ANoDisclosure`` above; this covers the second raise
        site cheaply so a regression in it is caught without a transport round
        trip.
        """
        from src.core.auth import get_principal_from_context
        from tests.factories import TenantFactory
        from tests.harness._base import BareIntegrationEnv
        from tests.helpers import assert_no_tenant_disclosure

        tenant_uuid = "5c1d2f6a-9b3e-4a71-8f04-2d6b7c9e1a35"
        with BareIntegrationEnv(tenant_id=tenant_uuid) as env:
            TenantFactory(tenant_id=tenant_uuid)
            env.get_session()  # commit factory data

            class _HeaderCarrier:
                """Duck-typed context: get_http_headers() returns {} outside an
                HTTP request, so get_principal_from_context falls back to
                ``context.headers`` — the documented sync-tool seam."""

                headers = {
                    "x-adcp-auth": "not-a-real-token",
                    "x-adcp-tenant": tenant_uuid,
                }

            with pytest.raises(AdCPError) as exc_info:
                get_principal_from_context(_HeaderCarrier())

        _assert_auth_required_with_suggestion(build_two_layer_error_envelope(exc_info.value))
        # Shared with the A2A wire pin and both tenant-isolation tests so all four
        # grade non-disclosure through one assertion instead of four copies.
        assert_no_tenant_disclosure(exc_info.value, tenant_uuid)
