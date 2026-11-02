"""Shared capture helper for the create_media_buy transport boundary.

Runs create_media_buy through :func:`src.core.tools._boundary.invoke_tool` -- the one path
every transport takes -- with a stub implementation substituted at the registry row, and
returns the ``push_notification_config`` that implementation received.

There used to be two of these, one per transport, because each transport had its own wrapper
and the two could forward different things. They cannot any more: MCP, A2A and REST all reach
the implementation through ``invoke_tool``, so "what MCP forwards" and "what A2A forwards"
are one question with one answer.

Returns the model, not a dict: Epic D lane C3 moved the wire-type conversion out of the
wrappers and into ValidatedWebhookRegistration, so what _impl receives is the typed model and
what persistence receives is plain str.

Used by:
  - tests/unit/test_create_media_buy_behavioral.py  (serialization obligations)
  - tests/unit/test_push_notification_forwarding.py  (forwarding parity)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from src.core.schemas import CreateMediaBuyRequest
from tests.helpers.adcp_factories import create_test_media_buy_request_dict
from tests.helpers.capture_wrapper_req import stub_impl


async def capture_a2a_forwarded_pnc(pnc: Any) -> Any:
    """Run create_media_buy at the boundary with *pnc* and return what the impl received.

    Args:
        pnc: A PushNotificationConfig model instance or plain dict to place on the request.

    Returns:
        The push_notification_config value received by _impl, or None if _impl
        was not called.
    """
    from src.core.schemas import CreateMediaBuyResult
    from src.core.tools._boundary import invoke_tool
    from tests.harness._base import BaseTestEnv

    req_dict = create_test_media_buy_request_dict()
    mock_result = MagicMock(spec=CreateMediaBuyResult)
    mock_result.__str__ = lambda self: "mock_result"
    # The boundary reads the protocol status off every result it stamps; a spec'd mock
    # does not expose the pydantic field, so it is set to the value a success carries.
    mock_result.status = "completed"

    captured: dict[str, Any] = {}

    async def _capture(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return mock_result

    # The boundary RESOLVES the identity from the credential; it is not handed one. A unit
    # env substitutes the resolver's database reads, so the env's own credential resolves to
    # its principal with no database, and ``stub_impl`` stubs the boundary's other two
    # database steps (the idempotency probe and account resolution) for the same reason.
    with BaseTestEnv() as env, stub_impl("create_media_buy", side_effect=_capture):
        # Everything, push_notification_config included, travels ON the request -- it is a
        # request FIELD (1f13cca0a), not a kwarg forwarded beside the request.
        await invoke_tool(
            "create_media_buy",
            CreateMediaBuyRequest(
                brand=req_dict["brand"],
                packages=req_dict["packages"],
                start_time=req_dict["start_time"],
                end_time=req_dict["end_time"],
                idempotency_key=req_dict["idempotency_key"],
                account=req_dict.get("account"),
                push_notification_config=pnc,
            ),
            env.credential(),
            "a2a",
        )

    # READ OFF THE REQUEST. push_notification_config is a request field, not a kwarg
    # forwarded beside the request, so parity means "the value lands on req", not "both
    # transports pass the same kwarg". Returning the model keeps every caller's comparison
    # unchanged.
    req = captured.get("req")
    return getattr(req, "push_notification_config", None) if req is not None else None
