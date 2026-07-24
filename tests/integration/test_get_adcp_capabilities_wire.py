"""Cross-transport WIRE coverage for the get_adcp_capabilities `account` capability.

The `account`/`sandbox` honesty declaration (#1329: sandbox=False until behavioral
isolation ships; supported_billing = the seller's accepted billing parties) is
otherwise asserted only by `model_dump()` unit tests. These tests assert it on the
ACTUAL serialized wire across MCP, A2A, and REST — the shape a buyer receives —
so a serialization regression (e.g. an omitted/renamed account section, or a
dishonest `sandbox=true`) is caught at the transport boundary.

Covers the BR-UC-010 obligation "the response should include account section with
sandbox flag and billing models" at the wire level.
"""

from __future__ import annotations

import pytest

from tests.harness.capabilities import CAPABILITIES_REST_ENDPOINT, CapabilitiesEnv
from tests.harness.transport import Transport

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def _assert_account_section(account: dict) -> None:
    """Assert the honest account capability shape on a serialized wire body."""
    assert account is not None, "capabilities response must include an account section"
    # Honesty declaration: sandbox is False until behavioral isolation ships (#1329).
    assert account.get("sandbox") is False, f"account.sandbox must be an honest False, got {account.get('sandbox')!r}"
    # Billing models the seller accepts — a non-empty list of billing parties.
    billing = account.get("supported_billing")
    assert isinstance(billing, list) and billing, f"account.supported_billing must be a non-empty list, got {billing!r}"


class TestGetAdcpCapabilitiesAccountWire:
    """The account/sandbox capability is honestly declared on the real wire."""

    @pytest.mark.parametrize("transport", [Transport.MCP, Transport.A2A])
    def test_account_section_on_mcp_and_a2a_wire(self, transport, integration_db):
        with CapabilitiesEnv() as env:
            env.setup_default_data()
            result = env.call_via(transport)

        assert result.is_success, f"{transport}: expected success, got {result.error!r}"
        assert result.wire_response is not None, f"{transport}: env did not stash success-path wire"
        _assert_account_section(result.wire_response.get("account"))

    def test_account_section_on_rest_wire(self, integration_db):
        with CapabilitiesEnv() as env:
            tenant, _principal = env.setup_default_data()
            client = env.get_rest_client()
            env._configure_rest_auth_override(env.identity)

            response = client.get(CAPABILITIES_REST_ENDPOINT)

        assert response.status_code == 200, f"REST capabilities returned {response.status_code}: {response.text}"
        _assert_account_section(response.json().get("account"))
