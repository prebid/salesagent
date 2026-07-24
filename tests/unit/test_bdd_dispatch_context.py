"""BDD dispatch adapters preserve the canonical TransportResult."""

from unittest.mock import MagicMock

from pydantic import BaseModel

from tests.bdd.steps._outcome_helpers import wire_dict
from tests.bdd.steps.generic.when_request import _call_via
from tests.harness.transport import Transport, TransportResult


class _Payload(BaseModel):
    creative_agents: list[dict[str, object]]


def _ctx_for(result: TransportResult) -> dict:
    env = MagicMock()
    env.call_via.return_value = result
    return {"env": env}


def test_creative_formats_dispatch_preserves_result_for_wire_assertions() -> None:
    """The alternate BDD dispatcher must satisfy the shared wire-reader contract."""
    wire = {"creative_agents": [{"agent_url": "https://creative.example"}]}
    result = TransportResult(
        payload=_Payload(creative_agents=wire["creative_agents"]),
        envelope={"transport": Transport.REST},
        wire_response=wire,
    )
    ctx = _ctx_for(result)

    _call_via(ctx, Transport.REST)

    assert ctx["result"] is result
    assert wire_dict(ctx) == wire


def test_creative_formats_dispatch_preserves_error_result() -> None:
    """Error Then steps also need the canonical result and its wire envelope."""
    error = RuntimeError("wire rejection")
    result = TransportResult(error=error, envelope={"transport": Transport.REST})
    ctx = _ctx_for(result)

    _call_via(ctx, Transport.REST)

    assert ctx["result"] is result
    assert ctx["error"] is error
