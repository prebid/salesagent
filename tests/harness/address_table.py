"""Tool address derivation — MCP/A2A/REST addresses read from live registration.

Builds the tool -> address map from the THREE objects production itself uses to
route real traffic: ``mcp.list_tools()`` (what a real MCP client sees),
``create_agent_card()`` (what a real A2A buyer's discovery request receives),
and ``app.routes`` (FastAPI's own dispatch table). There is no fourth,
hand-maintained list of TOOLS to keep in sync — a tool registered on any of
those three sites becomes resolvable through :data:`ADDRESS_TABLE`
automatically; a tool NOT registered on a transport raises
:class:`NoAddressForTransport` instead of silently being unreachable.

The one place hand-maintenance CAN be required: a REST route's Python handler
function name does not always equal the AdCP tool name it implements (e.g.
the ``/api/v1/capabilities`` handler is named ``get_capabilities`` but
implements the ``get_adcp_capabilities`` tool). The route declares that
identity itself via ``operation_id`` (preferred — no hand-maintained entry
needed); :data:`REST_TOOL_ALIASES` is the fallback for a route that hasn't
adopted ``operation_id`` yet, and tools with no REST route at all are
recorded in :data:`REST_ABSENT_TOOLS` — both reviewed-growth-only, both
validated against live registration by tests (see
``tests/harness/test_address_table.py::TestRestAliasesAndAbsence``). A REST
handler name that matches neither a known tool name, a declared
``operation_id``, nor an alias raises :class:`UnresolvedRestHandlerName` at
table-build time — it is NEVER silently registered under the wrong name. The
design this implements is stated in ``tests/harness/client.py``'s module
docstring.

Usage::

    from tests.harness.address_table import ADDRESS_TABLE
    from tests.harness.transport import Transport

    address = ADDRESS_TABLE.resolve("get_products", Transport.MCP)
    # address.name == "get_products"

    from tests.harness.address_table import NoAddressForTransport
    try:
        ADDRESS_TABLE.resolve("approve_creative", Transport.REST)
    except NoAddressForTransport:
        ...  # expected: approve_creative is an A2A-only skill
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from tests.harness.transport import Transport

# {name} groups in a REST path template, e.g. "media_buy_id" from
# "/api/v1/media-buys/{media_buy_id}". Single source of truth for both the
# ADDRESS derivation (informational) and REST WRAP's path-param peeling
# (tests/harness/client.py) — see design doc §4 "path_template handling".
PATH_PARAM_RE = re.compile(r"\{(\w+)\}")

# REST handler function name -> AdCP tool name, for the (hopefully rare) case
# where a route's Python handler is not named after the tool it implements
# AND the route does not (yet) self-declare its AdCP tool identity via
# ``operation_id``. ``_index_rest`` prefers a route's declared ``operation_id``
# over this map whenever it resolves to a known tool name — see
# ``_index_rest``. This map is the fallback for routes that haven't adopted
# ``operation_id`` yet; it is reviewed-growth-only: pinned exactly by
# TestRestAliasesAndAbsence.test_rest_tool_aliases_pinned_exactly, so adding
# an entry requires a deliberate, reviewed edit to that test too. It reached
# empty, and stays empty: every /api/v1 handler is now named after the AdCP tool
# it implements, so the handler name IS the tool identity and nothing needs
# aliasing. AdCP does not define REST -- this project does -- so the tool name is
# the canonical name across every transport, and a REST handler diverging from it
# is a defect, not a naming choice.
REST_TOOL_ALIASES: dict[str, str] = {}

# AdCP tool names known to have NO REST route at all (as opposed to a REST
# route under a divergent name — that's REST_TOOL_ALIASES above). Documents
# the absence as a KNOWN, intentional fact so the cross-registry consistency
# guard (TestRestAliasesAndAbsence) treats it as expected rather than an
# accident of naming drift. Validated bidirectionally by
# test_rest_absent_tools_stay_off_rest_and_are_real_tools: every entry here
# must (a) NOT resolve on REST — else it's stale and should shrink — and (b)
# resolve on MCP or A2A — else it's a typo, not a real tool.
REST_ABSENT_TOOLS: frozenset[str] = frozenset(
    {
        "complete_task",
        "get_task",
        "list_tasks",
    }
)


class NoAddressForTransport(LookupError):
    """Tool has no registered address on the requested transport.

    This is EXPECTED, not a bug — not every tool exists on every transport.
    Callers that hit this for a real scenario should treat it as "this
    scenario is scoped to fewer transports than the default", not paper over
    it with a broader except. The live A2A-only / REST-absent tool sets drift
    over time — read them from :meth:`AddressTable.all_tools` /
    :data:`REST_ABSENT_TOOLS` rather than trusting a hand-copied example list
    here (a hand-copied list is exactly the disease this module exists to
    stop reintroducing).
    """


class UnresolvedRestHandlerName(RuntimeError):
    """A REST route's handler name resolves to no known AdCP tool.

    Unlike :class:`NoAddressForTransport` (an EXPECTED per-tool-per-transport
    miss), this is a BUG: a route exists, but its handler name is neither a
    known MCP/A2A tool name, a declared ``operation_id`` that resolves to
    one, nor an entry in :data:`REST_TOOL_ALIASES`. Fix by declaring
    ``operation_id="<tool_name>"`` on the route (preferred), or by adding a
    ``{handler_name: tool_name}`` entry to ``REST_TOOL_ALIASES`` if the
    route can't yet adopt ``operation_id`` — never by weakening this check.
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
    # MCP: tool name. A2A: skill id. REST: the RESOLVED AdCP tool name (post
    # REST_TOOL_ALIASES substitution — may differ from the route's Python
    # handler name). REST DELIVER (tests/harness/client.py) reads only
    # path_template/method to dispatch, never `name`, so alias substitution
    # here is transparent to dispatch.
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
    objects by default (``src.core.main.mcp``, ``create_agent_card()``,
    ``src.app.app``). Tests that need to prove the "derived, not
    hand-maintained" invariant directly — a NEW tool registered at test time
    becomes addressable with zero map edits — inject a throwaway
    ``mcp_app``/``agent_card_factory``/``rest_app`` via the constructor
    instead of mutating the real production singletons.

    ``_index_rest`` is the one exception to "no hand-maintained list": a
    REST handler name that doesn't match a known MCP/A2A tool name is
    resolved through ``REST_TOOL_ALIASES`` or raises
    :class:`UnresolvedRestHandlerName` — see module docstring.
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
            from src.a2a_server.adcp_a2a_server import create_agent_card

            agent_card_factory = create_agent_card

        for skill in agent_card_factory().skills:
            for t in _A2A_FAMILY:
                self._by_tool_transport[(skill.id, t)] = ToolAddress(t, name=skill.id)

    @staticmethod
    def _pick_rest_method(methods: set[str]) -> str:
        """Deterministic verb choice for a multi-verb route: prefer POST (the
        AdCP body-carrying shape), else the alphabetically-first remaining
        verb — never ``next(iter(...))`` on an unordered set. No route in
        ``src/routes/api_v1.py`` registers more than one non-HEAD verb today,
        so the POST-preference branch is currently exercised only by a
        synthetic test (``TestRestVerbDeterminism``), not observed production
        behavior — kept deterministic anyway so a future multi-verb route
        does not introduce nondeterministic test flakiness.
        """
        candidates = methods - {"HEAD"}
        if "POST" in candidates:
            return "post"
        return sorted(candidates)[0].lower()

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
            handler_name = endpoint.__name__
            declared_operation_id = getattr(route, "operation_id", None)
            if declared_operation_id in known_tool_names:
                # Route self-declares its AdCP tool identity — prefer it over
                # both the raw handler name and REST_TOOL_ALIASES (the
                # endgame the alias map's docstring describes).
                tool_name = declared_operation_id
            else:
                tool_name = REST_TOOL_ALIASES.get(handler_name, handler_name)
            if tool_name not in known_tool_names:
                raise UnresolvedRestHandlerName(
                    f"REST route {path!r} handler {handler_name!r} resolves to tool name "
                    f"{tool_name!r}, which is not a known MCP/A2A tool name. If "
                    f"{handler_name!r} implements an existing AdCP tool under a divergent "
                    f"name, add {handler_name!r}: <tool_name> to REST_TOOL_ALIASES "
                    f"(tests/harness/address_table.py). If this route is not an AdCP tool "
                    f"at all, it is out of this indexer's scope — see UnresolvedRestHandlerName."
                )
            existing = self._by_tool_transport.get((tool_name, Transport.REST))
            if existing is not None and existing.path_template != path:
                raise UnresolvedRestHandlerName(
                    f"REST tool name {tool_name!r} resolves from more than one route: "
                    f"{existing.path_template!r} and {path!r} (via handler {handler_name!r}). "
                    f"A resolved AdCP tool name must be unique per REST family — check "
                    f"REST_TOOL_ALIASES for a collision."
                )
            method = self._pick_rest_method(methods)
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
