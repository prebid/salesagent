"""Real-wire application-context fidelity across every Sales Agent transport."""

import pytest

from tests.harness.capabilities import CapabilitiesEnv
from tests.harness.transport import Transport

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


@pytest.mark.parametrize("transport", [Transport.MCP, Transport.A2A, Transport.REST])
def test_capabilities_preserves_explicit_null_context_on_success(
    integration_db,
    transport: Transport,
) -> None:
    expected = {
        "correlation_id": f"ctx-null-{transport.value}",
        "nullable": None,
        "nested": {"value": None},
    }

    with CapabilitiesEnv() as env:
        env.setup_default_data()
        result = env.call_via(transport, context=expected)

    assert result.is_success, f"{transport.value}: {result.error!r}"
    assert result.wire_response is not None
    assert result.wire_response.get("context") == expected
