"""UC-003 harness defaults for the required update idempotency key."""

from src.core.schemas import UpdateMediaBuyRequest
from tests.harness._idempotency import OMIT_IDEMPOTENCY_KEY
from tests.harness.media_buy_dual import MediaBuyDualEnv


def test_update_wire_builders_default_key_and_preserve_explicit_omission() -> None:
    """Normal UC-003 requests stay valid while omission regressions stay possible."""
    env = object.__new__(MediaBuyDualEnv)
    req = UpdateMediaBuyRequest(media_buy_id="mb-1", paused=True)

    a2a_mcp = env._flatten_update_request({"req": req})
    rest = env._build_update_rest_body(req=req)
    assert a2a_mcp["idempotency_key"].startswith("test-key-")
    assert rest["idempotency_key"].startswith("test-key-")

    omitted_a2a_mcp = env._flatten_update_request({"req": req, "idempotency_key": OMIT_IDEMPOTENCY_KEY})
    omitted_rest = env._build_update_rest_body(req=req, idempotency_key=OMIT_IDEMPOTENCY_KEY)
    assert "idempotency_key" not in omitted_a2a_mcp
    assert "idempotency_key" not in omitted_rest
