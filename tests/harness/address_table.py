"""Tool address derivation — MCP/A2A/REST addresses read from live registration.

Builds the tool -> address map from the THREE objects production itself uses to
route real traffic: ``mcp.list_tools()`` (what a real MCP client sees),
``_derived_skills()`` (the skills a real A2A buyer's discovery request receives),
and ``app.routes`` (FastAPI's own dispatch table). There is no fourth,
hand-maintained list of TOOLS to keep in sync — a tool registered on any of
those three sites becomes resolvable through :data:`ADDRESS_TABLE`
automatically; a tool NOT registered on a transport raises
:class:`NoAddressForTransport` instead of silently being unreachable.

A REST route's handler function IS named after the AdCP tool it implements, so
the handler name is the tool identity and nothing translates between them. A
handler name that is not a known tool name raises
:class:`UnresolvedRestHandlerName` at table-build time — it is NEVER silently
registered under the wrong name. Tools with no REST route at all are recorded in
validated against live registration by tests (see
``tests/harness/test_address_table.py::TestRestHandlerNamesAndAbsence``). The
design this implements is stated in ``tests/harness/client.py``'s module
docstring.

Usage::

    from tests.harness.address_table import ADDRESS_TABLE
    from tests.harness.transport import Transport

    address = ADDRESS_TABLE.resolve("get_products", Transport.MCP)
    # address.name == "get_products"

    from tests.harness.address_table import NoAddressForTransport
    try:
        ADDRESS_TABLE.resolve("list_tasks", Transport.REST)
    except NoAddressForTransport:
        ...  # expected: list_tasks has no REST route
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from tests.harness.transport import Transport

# {name} groups in a REST path template, e.g. "media_buy_id" from
# "/api/v1/media-buys/{media_buy_id}". Single source of truth for both the
# ADDRESS derivation (informational) and REST WRAP's path-param peeling
# (tests/harness/client.py) — see design doc §4 "path_template handling".
PATH_PARAM_RE = re.compile(r"\{(\w+)\}")


class NoAddressForTransport(LookupError):
    """Tool has no registered address on the requested transport.

    This is EXPECTED, not a bug — not every tool exists on every transport.
    Callers that hit this for a real scenario should treat it as "this
    scenario is scoped to fewer transports than the default", not paper over
    it with a broader except. Which tools resolve on which transport drifts
    over time — read it from :meth:`AddressTable.all_tools` rather than
    trusting a hand-copied example list here (a hand-copied list is exactly
    the disease this module exists to stop reintroducing).
    """


class UnresolvedRestHandlerName(RuntimeError):
    """A REST route's handler name resolves to no known AdCP tool.

    Unlike :class:`NoAddressForTransport` (an EXPECTED per-tool-per-transport
    miss), this is a BUG: a route exists, but its handler name is not a known
    MCP/A2A tool name. Fix by renaming the handler to the tool it implements —
    never by weakening this check.
    If the route is legitimately NOT an AdCP tool (e.g. a future webhook
    receiver or internal helper mounted under ``/api/v1``), it is out of
    this indexer's scope; consult the design doc
    (see ``tests/harness/client.py``) before adding
    a broad exclusion, since ``/api/v1`` is documented as the AdCP tool
    surface.
    """


@dataclass(frozen=True)
class ToolAddress:
    """One transport's resolved address for one tool. Frozen/hashable — safe to cache."""

    transport: Transport
    # MCP: tool name. A2A: skill id. REST: the AdCP tool name, which is the
    # route's Python handler name. REST DELIVER (tests/harness/client.py) reads
    # only path_template/method to dispatch, never `name`.
    name: str
    path_template: str | None = None  # REST only, e.g. "/api/v1/media-buys/{media_buy_id}"
    method: str | None = None  # REST only, lowercase HTTP verb, e.g. "put"

    @property
    def path_params(self) -> tuple[str, ...]:
        """Path-param names captured by ``path_template`` (REST only, else empty)."""
        if not self.path_template:
            return ()
        return tuple(PATH_PARAM_RE.findall(self.path_template))


# Transport families: WRAP/UNWRAP are shared across an in-process transport and
# its E2E sibling (see design doc §5) — ADDRESS derivation mirrors that by
# registering the same ToolAddress under both keys of a family.
_MCP_FAMILY = (Transport.MCP, Transport.E2E_MCP)
_A2A_FAMILY = (Transport.A2A, Transport.E2E_A2A)
_REST_FAMILY = (Transport.REST, Transport.E2E_REST)


class AddressTable:
    """Tool -> address map, built once per process from live registration objects.

    Not a hand-maintained dict of TOOLS — rebuilding costs nothing (no I/O),
    so it is built lazily on first ``resolve()`` call to avoid import-order
    issues with ``src.core.main`` / ``src.app`` (importing either pulls in
    the full app wiring, which some unit-test contexts must not trigger at
    module-import time).

    The three ``_index_*`` methods read production's own registration
    objects by default (``src.core.main.mcp``, ``_derived_skills()``,
    ``src.app.app``). Tests that need to prove the "derived, not
    hand-maintained" invariant directly — a NEW tool registered at test time
    becomes addressable with zero map edits — inject a throwaway
    ``mcp_app``/``agent_card_factory``/``rest_app`` via the constructor
    instead of mutating the real production singletons.

    ``_index_rest`` raises :class:`UnresolvedRestHandlerName` for a REST
    handler name that doesn't match a known MCP/A2A tool name — see module
    docstring.
    """

    def __init__(
        self,
        *,
        mcp_app: Any = None,
        agent_card_factory: Callable[[], Any] | None = None,
        rest_app: Any = None,
    ) -> None:
        self._mcp_app: Any = mcp_app
        self._agent_card_factory = agent_card_factory
        self._rest_app: Any = rest_app
        self._by_tool_transport: dict[tuple[str, Transport], ToolAddress] = {}
        self._built = False

    def _build(self) -> None:
        self._index_mcp()
        self._index_a2a()
        self._index_rest()
        self._built = True

    def _index_mcp(self) -> None:
        import asyncio

        mcp_app: Any = self._mcp_app
        if mcp_app is None:
            from src.core.main import mcp as mcp_app  # noqa: PLC0414

        for tool in asyncio.run(mcp_app.list_tools()):
            for t in _MCP_FAMILY:
                self._by_tool_transport[(tool.name, t)] = ToolAddress(t, name=tool.name)

    def _index_a2a(self) -> None:
        agent_card_factory = self._agent_card_factory
        if agent_card_factory is None:
            # _derived_skills, not a rendered card: a card describes a resolved
            # TENANT now, and the A2A skill set does not vary by tenant -- it is
            # derived from TOOLS, which is what this table indexes.
            from src.a2a_server.adcp_a2a_server import _derived_skills

            agent_card_factory = lambda: SimpleNamespace(skills=_derived_skills())  # noqa: E731

        for skill in agent_card_factory().skills:
            for t in _A2A_FAMILY:
                self._by_tool_transport[(skill.id, t)] = ToolAddress(t, name=skill.id)

    def _index_rest(self) -> None:
        rest_app: Any = self._rest_app
        if rest_app is None:
            from src.app import app as rest_app  # noqa: PLC0414

        known_tool_names = {name for (name, t) in self._by_tool_transport if t in (Transport.MCP, Transport.A2A)}

        for route in rest_app.routes:
            path = getattr(route, "path", "")
            endpoint = getattr(route, "endpoint", None)
            methods = getattr(route, "methods", None)
            if not path.startswith("/api/v1") or endpoint is None or not methods:
                continue
            tool_name = endpoint.__name__
            if tool_name not in known_tool_names:
                raise UnresolvedRestHandlerName(
                    f"REST route {path!r} handler {tool_name!r} is not a known MCP/A2A tool "
                    f"name. A REST handler is named after the AdCP tool it implements — if "
                    f"this one implements an existing tool, rename it to that tool. If this "
                    f"route is not an AdCP tool at all, it is out of this indexer's scope — "
                    f"see UnresolvedRestHandlerName."
                )
            existing = self._by_tool_transport.get((tool_name, Transport.REST))
            if existing is not None and existing.path_template != path:
                raise UnresolvedRestHandlerName(
                    f"REST tool name {tool_name!r} resolves from more than one route: "
                    f"{existing.path_template!r} and {path!r}. An AdCP tool name must be "
                    f"unique per REST family."
                )
            (verb,) = methods
            method = verb.lower()
            for t in _REST_FAMILY:
                self._by_tool_transport[(tool_name, t)] = ToolAddress(
                    t, name=tool_name, path_template=path, method=method
                )

    def resolve(self, tool: str, transport: Transport) -> ToolAddress:
        """Return the live-registered address for *tool* on *transport*.

        Raises :class:`NoAddressForTransport` when *tool* has no address on
        *transport* — an expected, per-tool-per-transport possibility (see
        class docstring), not a hand-maintained-map bug.
        """
        if not self._built:
            self._build()
        key = (tool, transport)
        if key not in self._by_tool_transport:
            raise NoAddressForTransport(f"{tool!r} has no registered address on {transport!r}")
        return self._by_tool_transport[key]

    def all_tools(self, transport: Transport) -> frozenset[str]:
        """All tool names addressable on *transport* — for coverage assertions."""
        if not self._built:
            self._build()
        return frozenset(name for (name, t) in self._by_tool_transport if t == transport)


# Module-level singleton over the REAL production registration objects — the
# one every step definition / client should import. Lazily built on first use.
ADDRESS_TABLE = AddressTable()
