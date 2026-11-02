"""The seam a transport-boundary test substitutes at: the registry row.

There is no per-tool MCP wrapper left to patch. ``TOOLS`` is the one declaration all three
transports derive from, and the registered ``RegistryTool`` reads its row on every call --
so a test that wants a stub implementation replaces the ROW, and gets it on every transport
at once. Patching ``src.core.tools.<mod>._<tool>_impl`` does nothing: the row captured the
function object at import, so the module attribute and the thing the transports invoke are
two different names for what used to be one.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from functools import wraps
from typing import Any, get_type_hints
from unittest.mock import DEFAULT, AsyncMock, patch

# _TOOLS, not TOOLS: the public registry is a read-only MappingProxyType, and the proxy
# reads through to this dict -- so patching it substitutes the row everywhere TOOLS is
# consulted, without the public surface being mutable.
from src.core.tools.registry import _TOOLS as TOOLS


def _conforming_impl(row: Any, replacement: Any, dto: Any) -> Any:
    """*replacement*, wrapped so the registry row still validates if it carries no signature.

    ``ToolSpec.__post_init__`` derives the tool's credential policy from its
    implementation's ``identity`` ANNOTATION and refuses a row whose annotation disagrees
    with the DTO. That refusal is correct and load-bearing — it is what makes the identity
    type the one statement of the policy — so this does not loosen it. A test substituting
    an ``AsyncMock`` is simply substituting something that answers no signature question at
    all (``get_type_hints`` on a mock raises), and the fix is to give the mock a conforming
    seam rather than to stop asking.

    The annotations are COPIED FROM THE ROW being replaced, so the shim asserts nothing of
    its own: the DTO is the row's DTO and the identity type is the one the real
    implementation declares. Substituting an implementation therefore cannot change the
    tool's credential policy, which is the property the registry check exists to protect.

    Calls are forwarded verbatim as ``(req=..., identity=...)``, so
    ``assert_called_once_with(req=..., identity=...)`` on the mock reads exactly as it did.
    """
    if getattr(replacement, "__annotations__", None) and "identity" in replacement.__annotations__:
        return replacement

    declared_identity = get_type_hints(row.impl)["identity"]

    async def _shim(*, req: Any, identity: Any) -> Any:
        result = replacement(req=req, identity=identity)
        return await result if inspect.isawaitable(result) else result

    _shim.__annotations__ = {"req": dto, "identity": declared_identity, "return": Any}
    # Keep the substitute reachable for a test that holds the context manager rather than
    # the mock it passed in.
    _shim.wrapped_stub = replacement  # type: ignore[attr-defined]
    return _shim


@contextmanager
def registry_row(tool_name: str, **fields: Any) -> Iterator[None]:
    """Run the block with ``tool_name``'s row fields (``impl``, ``dto``, ...) replaced."""
    row = TOOLS[tool_name]
    if "impl" in fields:
        fields = {**fields, "impl": _conforming_impl(row, fields["impl"], fields.get("dto", row.dto))}
    with patch.dict(TOOLS, {tool_name: replace(row, **fields)}):
        yield


def registry_impl(tool_name: str, impl: Any) -> Any:
    """Run the block with ``tool_name``'s implementation replaced by ``impl``."""
    return registry_row(tool_name, impl=impl)


def mcp_tool(tool_name: str) -> Any:
    """``await mcp_tool(name)(credential=..., **arguments)`` -> the REGISTERED tool's ``ToolResult``.

    Runs the tool object the server actually serves, not a reconstruction of it, so a test
    exercises the same validate-invoke-serialize path a buyer reaches. ``RegistryTool.run``
    reads its row per call, so this picks up a :func:`registry_impl` substitution without
    being rebuilt.

    ``credential`` is the headers dict the request presents (``env.credential()`` or
    ``credential_headers(...)``); it is supplied by patching the header dependency ``run``
    resolves rather than passed as an argument, because a buyer sends arguments only.
    Omit it to run as an unauthenticated caller with no headers at all, which is what the
    server sees for an auth-optional tool called without a token.
    """
    from src.core import main

    async def _run(credential: dict[str, str] | None = None, **arguments: Any) -> Any:
        tool = await main.mcp.get_tool(tool_name)
        assert tool is not None, f"{tool_name} is not registered"
        with patch("fastmcp.server.dependencies.get_http_headers", return_value=dict(credential or {})):
            return await tool.run(arguments)

    return _run


def capture_req_via_wrapper(
    *,
    tool_name: str,
    stub_response: Any,
    wrapper_kwargs: dict[str, Any],
) -> Any:
    """Run a tool's MCP boundary with a stub impl; return the request handed to that impl."""
    captured: dict[str, Any] = {}

    async def _impl(req: Any, identity: Any = None, **kwargs: Any) -> Any:
        captured["req"] = req
        return stub_response

    with registry_impl(tool_name, _impl):
        asyncio.run(mcp_tool(tool_name)(**wrapper_kwargs))
    return captured["req"]


def _enter_all(stack: ExitStack, patchings: list[Any]) -> list[Any]:
    """Enter every patching on ``stack``; return the mocks meant to be injected as arguments.

    A patching that names an attribute (``patch.object(x, "y", new=z)``) supplies its own
    value and injects nothing, which is the rule ``unittest.mock`` applies and pytest counts.
    """
    injected = []
    for patching in patchings:
        entered = stack.enter_context(patching)
        if not patching.attribute_name and patching.new is DEFAULT:
            injected.append(entered)
    return injected


def stub_account_for(account_ref: Any, tenant_id: str, principal: Any) -> Any:
    """The resolver's account read, without a database: an active Account named by the ref.

    Shared by :class:`stub_impl` and the unit harness's resolver substitutes, so the two
    database-free paths answer the same account for the same reference.
    """
    from src.core.schemas.account import Account

    inner = account_ref.root
    account_id = getattr(inner, "account_id", None) or "acct_stub"
    return Account(account_id=account_id, name=f"Stub account {account_id}", status="active")


class stub_impl:  # noqa: N801 -- reads as a patch()-style decorator at every call site
    """Substitute ``tool_name``'s implementation with an ``AsyncMock``.

    The replacement for ``patch("<module>.<tool>_raw")``,
    ``patch("src.a2a_server.adcp_a2a_server.core_<tool>_tool")`` and
    ``patch("<module>._<tool>_impl")``. The first two names no longer exist; the third does
    but no longer reaches anything, because every transport calls the function object the
    registry row holds, which was captured at import.

    Usable as a context manager (``with stub_impl("get_products") as mock_impl:``) or as a
    decorator, where it injects the mock like ``patch`` does -- bottom decorator first::

        @patch("src.core.resolved_identity._load_account")
        @stub_impl("get_products")
        def test_x(self, mock_impl, mock_load_account, ...):

    The stub is called exactly as the boundary calls a real implementation --
    ``impl(req=..., identity=...)`` -- so ``assert_called_once_with(req=..., identity=...)``
    reads the same as it did against a wrapper.

    The boundary's two database-backed steps are stubbed out for the duration: idempotency
    storage and account resolution. A substituted implementation means the tool did not
    really run, so its answer has no business entering a replay cache, and neither the replay
    probe nor the account lookup has a database in a unit test. Tests that grade replay or
    account resolution ITSELF run against a real database and use :func:`registry_impl`
    directly instead.
    """

    #: Shaped like a ``unittest.mock`` patching entry so this can live in a ``patchings``
    #: list beside real ones: falsy ``attribute_name`` plus the ``DEFAULT`` sentinel is what
    #: marks an entry as "yields a mock to inject".
    attribute_name = None
    new = DEFAULT

    def __init__(self, tool_name: str, **mock_kwargs: Any) -> None:
        self.tool_name = tool_name
        self.mock_kwargs = mock_kwargs
        self._stack: ExitStack | None = None

    def __enter__(self) -> AsyncMock:
        stub = AsyncMock(**self.mock_kwargs)
        self._stack = ExitStack()
        self._stack.enter_context(registry_row(self.tool_name, impl=stub))
        self._stack.enter_context(patch("src.core.tools._boundary.lookup_cached_replay", return_value=None))
        self._stack.enter_context(patch("src.core.tools._boundary.cache_success"))
        self._stack.enter_context(patch("src.core.tools._boundary.maybe_evict_expired"))
        # The resolver's account read: answers a schema Account named by the reference (or a
        # fixed id for the natural-key form), so a request that names an account resolves
        # without a database and the identity still carries one.
        self._stack.enter_context(patch("src.core.resolved_identity._load_account", side_effect=stub_account_for))
        return stub

    def __exit__(self, *exc: Any) -> None:
        assert self._stack is not None
        self._stack.close()
        self._stack = None

    def __call__(self, fn: Any) -> Any:
        """Decorator form, stackable with ``patch`` in either order.

        ``patch`` does not wrap a function that already carries ``patchings`` -- it appends
        itself to that list and hands the function back. So a decorator that means to sit in
        a ``patch`` stack has to own the list AND apply every entry in it, which is what this
        wrapper does: enter each patching in order (bottom decorator first, matching
        ``patch``'s own convention) and append the mocks each one yields.

        Carrying ``patchings`` is also what tells pytest these trailing parameters are
        injected rather than fixtures -- ``_pytest.compat.num_mock_patch_args`` counts the
        entries whose ``attribute_name`` is falsy and whose ``new`` is the mock sentinel, and
        subtracts them from the end of the signature.
        """

        @wraps(fn)
        def inner(*args: Any, **kwargs: Any) -> Any:
            with ExitStack() as stack:
                return fn(*args, *_enter_all(stack, inner.patchings), **kwargs)

        @wraps(fn)
        async def async_inner(*args: Any, **kwargs: Any) -> Any:
            with ExitStack() as stack:
                return await fn(*args, *_enter_all(stack, async_inner.patchings), **kwargs)

        wrapper = async_inner if asyncio.iscoroutinefunction(fn) else inner
        wrapper.patchings = [*getattr(fn, "patchings", []), self]
        return wrapper
