"""Shared mock-impl capture for MCP transport wrapper boundary tests (#1324)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from fastmcp.server.context import Context


def capture_req_via_wrapper(
    *,
    impl_patch_target: str,
    wrapper: Callable[..., Any],
    stub_response: Any,
    wrapper_kwargs: dict[str, Any],
) -> Any:
    """Run an MCP tool wrapper with a patched ``_impl``; return the request handed to it."""
    captured: dict[str, Any] = {}

    async def _impl(req: Any, identity: Any, **kwargs: Any) -> Any:
        captured["req"] = req
        return stub_response

    mock_ctx = MagicMock(spec=Context)
    mock_ctx.get_state = AsyncMock(return_value=None)
    with patch(impl_patch_target, side_effect=_impl):
        asyncio.run(wrapper(**wrapper_kwargs, ctx=mock_ctx))
    return captured["req"]
