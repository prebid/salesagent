"""Integration test: MCP update error path surfaces the wire error envelope.

Before salesagent-ihwl, MediaBuyDualEnv._call_update_mcp invokes the raw
``update_media_buy`` tool function directly (asyncio.run), bypassing the
production ``with_error_logging`` decorator that real MCP registration applies
(src/core/main.py). On error the raw AdCPError propagates instead of becoming an
AdCPToolError carrying the two-layer envelope, so McpDispatcher's
``_envelope_from_mcp_error`` returns None and ``result.wire_error_envelope`` is
None — the MCP update error path cannot be asserted at the wire layer.

After ihwl, the harness wraps the call with ``with_error_logging`` (the same
decorator production uses), so a raised AdCPError surfaces as the wire
ToolError envelope and ``result.wire_error_envelope`` is populated.

A2A and REST already capture the envelope (verified here as a guard); MCP is
the gap this test pins.

beads: salesagent-ihwl
"""

from __future__ import annotations

import pytest

from tests.harness.transport import Transport
from tests.helpers import assert_envelope_shape

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

# All three wire transports must surface the wire error envelope on update errors.
_WIRE_TRANSPORTS = [Transport.A2A, Transport.MCP, Transport.REST]


class TestUC003McpUpdateErrorCapture:
    """Wire transports must surface the two-layer error envelope on update errors.

    Core Invariant (salesagent-ihwl): the harness MCP update path must exercise
    the same production boundary error-translation (with_error_logging ->
    AdCPToolError) that real MCP registration applies, so a raised AdCPError
    surfaces as the wire ToolError envelope captured on
    ``result.wire_error_envelope`` — never a raw exception yielding None.
    """

    @pytest.fixture
    def env_with_media_buy(self, integration_db):
        from tests.bdd.conftest import _setup_existing_media_buy
        from tests.harness.media_buy_dual import MediaBuyDualEnv

        with MediaBuyDualEnv() as env:
            tenant, principal, product, _ = env.setup_media_buy_data()
            ctx: dict = {}
            _setup_existing_media_buy(ctx, env, tenant, principal, product)
            env._seeded_media_buy_id = ctx["existing_media_buy"].media_buy_id
            yield env, ctx["existing_media_buy"]

    @pytest.mark.parametrize("transport", _WIRE_TRANSPORTS)
    def test_empty_update_surfaces_wire_error_envelope(self, env_with_media_buy, transport):
        """An empty update (no updatable fields) is a VALIDATION_ERROR whose
        two-layer envelope must be captured at the wire layer on every transport.

        Fails on MCP before ihwl: wire_error_envelope is None because the raw
        AdCPError never becomes an AdCPToolError.
        """
        from src.core.schemas import UpdateMediaBuyRequest

        env, media_buy = env_with_media_buy
        # No updatable fields -> production raises AdCPValidationError at the boundary.
        req = UpdateMediaBuyRequest(media_buy_id=media_buy.media_buy_id)

        result = env.call_via(transport, req=req)

        assert result.is_error, f"{transport}: expected an error for an empty update"
        assert result.wire_error_envelope is not None, (
            f"{transport}: wire_error_envelope NOT captured — the MCP update error "
            f"path bypasses with_error_logging (salesagent-ihwl)."
        )
        assert_envelope_shape(
            result.wire_error_envelope,
            "VALIDATION_ERROR",
            recovery="correctable",
            message_substr="at least one updatable field",
        )
