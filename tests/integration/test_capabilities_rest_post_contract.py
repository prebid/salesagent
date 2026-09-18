"""Integration tests pinning salesagent-5yik's Core Invariant (C3 capabilities contract).

Core Invariant (salesagent-5yik): ``_get_adcp_capabilities_impl`` stays
transport-agnostic and honest -- every field on the wire (context echo,
filtered sections) is derived directly from the typed
``GetAdcpCapabilitiesRequest`` the caller sent, and REST carries that same
request shape via a real request body (owner decision 2026-07-24: a NEW
``POST /api/v1/capabilities`` route with a ``GetCapabilitiesBody`` JSON
body, matching the codebase's RPC-over-REST convention). It is now the
tool's ONLY REST route: the parameterless GET was a second shape for the
same tool and was deleted (salesagent-prkv.106.4).

These tests exercise the REAL REST transport end to end (real Postgres via
``CapabilitiesEnv``/``IntegrationEnv``, real FastAPI ``TestClient``, real
route table -- nothing patched about the route itself) so they fail today
for the right reason: no ``POST /api/v1/capabilities`` route exists yet
(405 Method Not Allowed), the ``context`` field is never echoed onto the
response, and ``protocols`` is never used to filter response sections.

Covers: salesagent-5yik (Core Invariant + Implementation Plan steps 1-6).
"""

from __future__ import annotations

import pytest

from tests.harness.capabilities import CapabilitiesEnv


@pytest.mark.requires_db
class TestCapabilitiesRestPostRoute:
    """POST /api/v1/capabilities must exist, echo context, and filter by protocols."""

    def test_post_capabilities_echoes_context_verbatim(self, integration_db):
        """The typed request's ``context`` field must be echoed byte-for-byte
        onto the wire response -- @T-UC-010-ext-e-echo (context:
        preserve-byte-for-byte) via the new REST POST body, not silently
        dropped like the current bare ``GetAdcpCapabilitiesRequest()``
        construction (capabilities.py:397).
        """
        with CapabilitiesEnv() as env:
            env.setup_default_data()
            client = env.get_rest_client()

            response = client.post(
                "/api/v1/capabilities",
                json={"context": {"request_id": "buyer-echo-123"}},
                # The request has to NAME a seller: one naming none is refused
                # (CONFIGURATION_ERROR), because there is no tenant whose rules to apply.
                headers=env.credential(),
            )

            assert response.status_code == 200, (
                f"Expected 200 from POST /api/v1/capabilities, got {response.status_code}: {response.text}"
            )
            data = response.json()
            assert data["context"] == {"request_id": "buyer-echo-123"}, (
                "context must be echoed verbatim from the typed request onto "
                f"the wire response; got {data.get('context')!r}"
            )

    # The protocols filter (media_buy kept, signals / governance / sponsored_intelligence
    # nulled, adcp invariant) is graded on the wire by @T-UC-010-ext-d-filter on rest,
    # mcp, a2a and e2e_rest. The copy that stood here posted without credential headers
    # and was deleted rather than migrated.
