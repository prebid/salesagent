"""Cross-transport WIRE coverage for the get_adcp_capabilities declared-honesty envelope.

The honesty declaration (#1329: account sandbox/require_operator_auth/financials False
until the behavior ships; supported_billing = the seller's accepted parties;
adcp.idempotency.supported=False; specialisms) is otherwise asserted only by
`model_dump()` unit tests. This asserts it on the ACTUAL serialized wire across MCP,
A2A, and REST — the shape a buyer receives — via the SINGLE coupling grader
`assert_declared_capabilities`, so a serialization regression (an omitted/renamed
section, a dishonest `sandbox=true`, a re-flipped `idempotency.supported=true`, or a new
emitted-but-ungraded field) is caught at the transport boundary (#1329 finding 1).

Covers the BR-UC-010 obligation "the response should include account section with
sandbox flag and billing models" — plus the idempotency posture — at the wire level.
"""

from __future__ import annotations

import pytest

from tests.harness.capabilities import CapabilitiesEnv
from tests.harness.transport import Transport
from tests.helpers import assert_declared_capabilities

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class TestGetAdcpCapabilitiesDeclaredWire:
    """The declared-honesty capabilities are graded on the real wire, one grader."""

    @pytest.mark.parametrize("transport", [Transport.MCP, Transport.A2A, Transport.REST])
    def test_declared_capabilities_on_wire(self, transport, integration_db):
        with CapabilitiesEnv() as env:
            env.setup_default_data()
            result = env.call_via(transport)

        assert result.is_success, f"{transport}: expected success, got {result.error!r}"
        assert result.wire_response is not None, f"{transport}: env did not stash success-path wire"
        assert_declared_capabilities(result.wire_response)
