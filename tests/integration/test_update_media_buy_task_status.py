"""Integration tests: update_media_buy wire transports surface TaskStatus.

Before #1417, _update_media_buy_impl returns UpdateMediaBuySuccess
directly (domain object). Wire transports (MCP/A2A/REST) expose the domain
object as result.payload, so result.payload.status is the domain MediaBuyStatus
("active", "approved", etc.) — NOT the ProtocolEnvelope TaskStatus ("completed").

After egnl, _update_media_buy_impl returns UpdateMediaBuyResult wrapping
the domain response with the protocol TaskStatus. result.payload.status == "completed".

beads: salesagent-egnl
"""

from __future__ import annotations

import pytest

from tests.harness.transport import Transport

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

# The three wire transports that must surface TaskStatus.
_WIRE_TRANSPORTS = [Transport.MCP, Transport.A2A, Transport.REST]


class TestUpdateMediaBuyWireTransportStatus:
    """Wire transports expose ProtocolEnvelope TaskStatus, not domain status.

    This test class verifies the Core Invariant for egnl:
    Every wire transport (MCP/A2A/REST) MUST surface ProtocolEnvelope.status
    (TaskStatus='completed') as the root status field on the harness response
    object; _impl is exempt (no wire).
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

    def _build_update_req(self, media_buy: object) -> object:
        from src.core.schemas import UpdateMediaBuyRequest

        return UpdateMediaBuyRequest(
            media_buy_id=media_buy.media_buy_id,
            budget=9999.0,
        )

    @pytest.mark.parametrize("transport", _WIRE_TRANSPORTS)
    def test_wire_transport_status_is_task_status_completed(
        self,
        env_with_media_buy,
        transport: Transport,
    ) -> None:
        """Wire transport result.payload.status must be TaskStatus 'completed'.

        Fails before egnl because result.payload is UpdateMediaBuySuccess
        whose domain 'status' field is not the protocol TaskStatus.
        Passes after egnl when result.payload is UpdateMediaBuyResult
        with status='completed'.
        """
        env, media_buy = env_with_media_buy
        req = self._build_update_req(media_buy)
        result = env.call_via(transport, req=req)

        assert result.is_success, f"Transport {transport}: expected success but got error: {result.error!r}"
        assert hasattr(result.payload, "status"), (
            f"Transport {transport}: result.payload ({type(result.payload).__name__}) "
            f"has no 'status' attribute — UpdateMediaBuyResult not wired."
        )
        actual = result.payload.status
        actual_str = actual.value if hasattr(actual, "value") else str(actual)
        assert actual_str == "completed", (
            f"Transport {transport}: expected protocol TaskStatus 'completed', "
            f"got '{actual_str}'. This is the domain MediaBuyStatus, not the "
            f"ProtocolEnvelope TaskStatus. Fix: wrap _update_media_buy_impl return "
            f"in UpdateMediaBuyResult(status=TaskStatus.COMPLETED, response=...)."
        )
