"""Integration tests pinning salesagent-5yik's Core Invariant (C3 capabilities contract).

Core Invariant (salesagent-5yik): ``_get_adcp_capabilities_impl`` stays
transport-agnostic and honest -- every field on the wire (context echo,
filtered sections) is derived directly from the typed
``GetAdcpCapabilitiesRequest`` the caller sent, and REST carries that same
request shape via a real request body (owner decision 2026-07-24: a NEW
``POST /api/v1/capabilities`` route with a ``GetCapabilitiesBody`` JSON
body, matching the codebase's RPC-over-REST convention -- the existing
parameterless ``GET /api/v1/capabilities`` stays unchanged).

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
            )

            assert response.status_code == 200, (
                f"Expected 200 from POST /api/v1/capabilities, got {response.status_code}: {response.text}"
            )
            data = response.json()
            assert data["context"] == {"request_id": "buyer-echo-123"}, (
                "context must be echoed verbatim from the typed request onto "
                f"the wire response; got {data.get('context')!r}"
            )

    def test_post_capabilities_filters_sections_by_protocols(self, integration_db):
        """When ``protocols`` names only ``media_buy``, the response must
        include the ``media_buy`` section and NULL OUT the other
        protocol-domain sections (``signals``, ``governance``,
        ``sponsored_intelligence``, ``creative``) -- @T-UC-010-ext-d-filter.
        Protocol-invariant fields (``adcp``, ``supported_protocols``) must
        survive untouched.
        """
        with CapabilitiesEnv() as env:
            env.setup_default_data()
            client = env.get_rest_client()

            response = client.post(
                "/api/v1/capabilities",
                json={"protocols": ["media_buy"]},
            )

            assert response.status_code == 200, (
                f"Expected 200 from POST /api/v1/capabilities, got {response.status_code}: {response.text}"
            )
            data = response.json()
            assert data.get("media_buy") is not None, "media_buy section must be present when protocols=['media_buy']"
            assert data.get("signals") is None, (
                f"signals section must be filtered out for protocols=['media_buy'], got {data.get('signals')!r}"
            )
            assert data.get("governance") is None, (
                f"governance section must be filtered out for protocols=['media_buy'], got {data.get('governance')!r}"
            )
            assert data.get("sponsored_intelligence") is None, (
                "sponsored_intelligence section must be filtered out for "
                f"protocols=['media_buy'], got {data.get('sponsored_intelligence')!r}"
            )
            assert data.get("adcp") is not None, "adcp is protocol-invariant and must survive filtering"
