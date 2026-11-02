"""Meta-tests for AddressTable — the DERIVED (not hand-maintained) tool address map.

Covers two things:

1. Resolution against the REAL production registration objects (``mcp``,
   ``create_agent_card()``, ``app.routes``) for tools that already exist —
   proving the map reads live data, not a copy.
2. The core invariant from the design doc (§4): a tool registered on a
   FRESH registration object at test time — one ``AddressTable`` has never
   seen before — becomes addressable with ZERO map edits. This is the
   direct proof that nothing in this file (or in ``address_table.py``)
   needs updating when a new tool is registered in production; only
   ``AddressTable``'s constructor accepts injected registration objects (a
   testability seam), the derivation logic itself is unconditional.

No database required — building the table only reads in-memory registration
objects (``mcp.list_tools()``, ``create_agent_card()``, ``app.routes``).
"""

from __future__ import annotations

import asyncio

import pytest

from src.core.tools.registry import TOOLS
from tests.harness.address_table import (
    PATH_PARAM_RE,
    AddressTable,
    NoAddressForTransport,
    ToolAddress,
    UnresolvedRestHandlerName,
)
from tests.harness.transport import Transport


class TestAddressTableAgainstLiveProduction:
    """Resolution against the real, already-registered production objects."""

    def test_resolves_get_products_on_mcp(self):
        table = AddressTable()
        address = table.resolve("get_products", Transport.MCP)
        assert address == ToolAddress(Transport.MCP, name="get_products")

    def test_resolves_get_products_on_a2a(self):
        table = AddressTable()
        address = table.resolve("get_products", Transport.A2A)
        assert address == ToolAddress(Transport.A2A, name="get_products")

    def test_resolves_get_products_on_rest_with_method_and_path(self):
        table = AddressTable()
        address = table.resolve("get_products", Transport.REST)
        assert address.method == "post"
        assert address.path_template == "/api/v1/products"

    def test_resolves_update_media_buy_rest_path_param(self):
        """update_media_buy's REST route has a {media_buy_id} path param — the
        concrete case the REST WRAP path-param peeling (tests/harness/client.py)
        generalizes from MediaBuyDualEnv's hand-coded version (design doc §4)."""
        table = AddressTable()
        address = table.resolve("update_media_buy", Transport.REST)
        assert address.method == "put"
        assert address.path_template == "/api/v1/media-buys/{media_buy_id}"
        assert address.path_params == ("media_buy_id",)

    def test_e2e_family_shares_the_same_address_as_in_process(self):
        """WRAP/UNWRAP are transport-FAMILY functions (design doc §5) — the
        address table mirrors that by registering the identical name/path/method
        under both the in-process and E2E transport keys."""
        table = AddressTable()
        mcp_addr = table.resolve("get_products", Transport.MCP)
        e2e_mcp_addr = table.resolve("get_products", Transport.E2E_MCP)
        assert mcp_addr.name == e2e_mcp_addr.name
        assert e2e_mcp_addr.transport == Transport.E2E_MCP

    def test_no_address_for_transport_on_a_single_transport_tool(self):
        """A tool on FEWER transports than all of them resolves on the ones it has and
        raises NoAddressForTransport on the ones it does not.

        The exemplar is DERIVED from the live registries, not named here. A previous version
        named ``approve_creative`` as an A2A-only skill; the card no longer carries it, so the
        assertion had decayed into a pass on a tool that exists nowhere -- the hand-copied
        example rot NoAddressForTransport's docstring warns about.

        The derivation was ``MCP - A2A - REST``, which is now empty: every registry row is on
        all three transports. That is a real property worth stating rather than a reason to
        fail, so this looks for a tool missing ANY transport and, finding none, asserts the
        uniformity instead. Either branch is a statement about production.
        """
        table = AddressTable()
        by_transport = {t: set(table.all_tools(t)) for t in (Transport.MCP, Transport.A2A, Transport.REST)}
        everywhere = set.intersection(*by_transport.values())
        partial = set.union(*by_transport.values()) - everywhere

        if not partial:
            assert set.union(*by_transport.values()) == everywhere, (
                "every tool is expected on every transport here -- if that stops being true, "
                "this test grades the partial tool instead"
            )
            return

        for name in sorted(partial):
            for transport, names in by_transport.items():
                if name in names:
                    assert table.resolve(name, transport).name == name
                else:
                    # An expected miss, not a silent one.
                    with pytest.raises(NoAddressForTransport):
                        table.resolve(name, transport)

    def test_no_address_for_unknown_tool(self):
        table = AddressTable()
        with pytest.raises(NoAddressForTransport):
            table.resolve("this_tool_does_not_exist", Transport.MCP)

    def test_every_live_mcp_tool_resolves(self):
        """Full-coverage check against the LIVE registry, not a hardcoded subset —
        if a tool is added to src/core/main.py tomorrow, this test keeps passing
        with zero edits, because it enumerates mcp.list_tools() itself rather than
        naming tools by hand."""
        from src.core.main import mcp

        table = AddressTable()
        live_tool_names = {t.name for t in asyncio.run(mcp.list_tools())}
        assert live_tool_names, "sanity: production MCP registry should not be empty"
        for name in live_tool_names:
            assert table.resolve(name, Transport.MCP).name == name


class TestAddressTableDerivationInvariant:
    """The core invariant (design doc §4): NEW registrations become addressable
    with no hand-maintained map edit — proven by registering a tool at test
    time on a THROWAWAY registration object an AddressTable has never seen,
    not by asserting against tools someone already wired into address_table.py.
    """

    def test_new_mcp_tool_becomes_addressable_without_a_map_edit(self):
        from fastmcp import FastMCP

        temp_app = FastMCP("throwaway-test-server")

        @temp_app.tool()
        def brand_new_tool_never_seen_before(x: int) -> int:
            return x

        table = AddressTable(mcp_app=temp_app)
        address = table.resolve("brand_new_tool_never_seen_before", Transport.MCP)
        assert address.name == "brand_new_tool_never_seen_before"
        # And the E2E sibling gets it too, for free — same derivation pass.
        assert table.resolve("brand_new_tool_never_seen_before", Transport.E2E_MCP).name == (
            "brand_new_tool_never_seen_before"
        )

    def test_new_a2a_skill_becomes_addressable_without_a_map_edit(self):
        from a2a.types import AgentCapabilities, AgentCard, AgentSkill

        def fake_agent_card() -> AgentCard:
            return AgentCard(
                name="throwaway",
                description="throwaway",
                version="0.0.0",
                capabilities=AgentCapabilities(),
                default_input_modes=["message"],
                default_output_modes=["message"],
                skills=[AgentSkill(id="brand_new_skill_never_seen_before", name="Brand New Skill", tags=[])],
            )

        table = AddressTable(agent_card_factory=fake_agent_card)
        address = table.resolve("brand_new_skill_never_seen_before", Transport.A2A)
        assert address.name == "brand_new_skill_never_seen_before"

    def test_new_rest_route_becomes_addressable_without_a_map_edit(self):
        """A brand-new, SELF-CONSISTENT tool (same name registered on MCP and
        exposed via REST) needs zero address_table.py map edits. Since the
        loud-miss check (AC2) validates REST handler names against known
        MCP/A2A tool names, the injected MCP registry must register the same
        tool name — otherwise this would (correctly) raise
        UnresolvedRestHandlerName, which is a different invariant
        (TestRestHandlerNamesAndAbsence), not the one this test proves."""
        from fastapi import FastAPI
        from fastmcp import FastMCP

        temp_mcp_app = FastMCP("throwaway-test-server")

        @temp_mcp_app.tool(name="brand_new_rest_tool_never_seen_before")
        def _brand_new_rest_tool_mcp_side(x: int) -> int:
            return x

        temp_app = FastAPI()

        @temp_app.post("/api/v1/brand-new-tool/{widget_id}")
        def brand_new_rest_tool_never_seen_before(widget_id: str) -> dict:
            return {"widget_id": widget_id}

        table = AddressTable(mcp_app=temp_mcp_app, rest_app=temp_app)
        address = table.resolve("brand_new_rest_tool_never_seen_before", Transport.REST)
        assert address.name == "brand_new_rest_tool_never_seen_before"
        assert address.method == "post"
        assert address.path_template == "/api/v1/brand-new-tool/{widget_id}"
        assert address.path_params == ("widget_id",)

    def test_non_api_v1_routes_are_not_indexed(self):
        """The REST indexer only reads /api/v1/* — confirms it isn't blindly
        vacuuming every FastAPI route (e.g. /admin/*, /mcp/*, health checks)."""
        from fastapi import FastAPI

        temp_app = FastAPI()

        @temp_app.get("/healthz")
        def healthcheck() -> dict:
            return {"ok": True}

        table = AddressTable(rest_app=temp_app)
        with pytest.raises(NoAddressForTransport):
            table.resolve("healthcheck", Transport.REST)


class TestRestHandlerNamesAndAbsence:
    """AC(2): a REST handler name that is not a known MCP/A2A tool name must
    fail table construction loudly (:class:`UnresolvedRestHandlerName`), not
    silently register under the wrong (unresolved) name — the actual bug this
    ticket fixes."""

    def test_rest_handler_not_named_after_a_tool_raises_unresolved_rest_handler_name(self):
        """Simulates a REST handler named for something other than the tool it
        implements (fetch_products), while the real tool (get_products)
        genuinely exists on MCP. Left unchecked this silently registers
        `fetch_products` as its own address, so `get_products` looks
        unavailable on REST even though a route for it exists under the wrong
        name. It must raise at table-build time instead of degrading into that
        silent miss."""
        from fastapi import FastAPI
        from fastmcp import FastMCP

        temp_mcp_app = FastMCP("throwaway-test-server")

        @temp_mcp_app.tool()
        def get_products(brief: str) -> dict:
            return {}

        temp_rest_app = FastAPI()

        @temp_rest_app.post("/api/v1/products")
        def fetch_products() -> dict:
            return {}

        table = AddressTable(mcp_app=temp_mcp_app, rest_app=temp_rest_app)
        with pytest.raises(UnresolvedRestHandlerName):
            table.resolve("get_products", Transport.REST)

    def test_duplicate_resolved_tool_name_from_two_routes_raises(self):
        """One tool name reaching the table from two different paths must raise,
        not silently last-write-wins. Registering one handler at two paths is
        how that happens now the handler name IS the tool name."""
        from fastapi import FastAPI
        from fastmcp import FastMCP

        temp_mcp_app = FastMCP("throwaway-test-server")

        # Registered UNDER the tool name but not NAMED it, so the REST handler below can
        # take that name — which it must, since the handler name is now the tool identity.
        @temp_mcp_app.tool(name="get_products")
        def mcp_get_products(brief: str) -> dict:
            return {}

        temp_rest_app = FastAPI()

        @temp_rest_app.post("/api/v1/products")
        @temp_rest_app.post("/api/v1/products-again")
        def get_products() -> dict:
            return {}

        table = AddressTable(mcp_app=temp_mcp_app, rest_app=temp_rest_app)
        # Matched on the message: the handler name IS a real tool, so this must be
        # the two-paths raise and not the not-a-tool-name raise above it.
        with pytest.raises(UnresolvedRestHandlerName, match="more than one route"):
            table.resolve("get_products", Transport.REST)


class TestCrossRegistryConsistencyGuard:
    """AC(4): for every tool present on more than one registry, it resolves
    under the same AdCP name everywhere — the guard this ticket adds."""

    def test_rest_tool_names_are_a_subset_of_known_mcp_or_a2a_names(self):
        """Not just 'build succeeded' (which is guaranteed once the loud-miss
        check is in place) — an explicit, independently-checkable statement of
        the invariant for the next reader."""
        table = AddressTable()
        rest_names = table.all_tools(Transport.REST)
        known_names = table.all_tools(Transport.MCP) | table.all_tools(Transport.A2A)
        assert rest_names <= known_names

    def test_every_registry_row_with_a_rest_binding_resolves_on_rest(self):
        """The REST surface IS the registry's rest bindings — derived, not pinned.

        This replaces two tests that pinned a MOMENT rather than a property:

        ``test_day_one_registry_contents_pinned`` asserted ``len(rest_names) == 13``
        and existed, by its own docstring, to give the subset check above some
        teeth. It fails the moment a route is added — which is exactly what the
        one-tool-registry work does on purpose, so it alarmed on intended change
        and said nothing about correctness.

        ``test_no_address_for_transport_on_a_single_transport_tool`` asserted
        ``mcp_only`` was non-empty as a SANITY PRECONDITION: it could only run
        while some tool was missing from A2A and REST. A test that requires
        production to be inconsistent in order to execute pins the defect. Its
        actual invariant — a miss is loud, not silent — is already graded twice
        without that requirement, by ``test_no_address_for_unknown_tool`` and by
        the constructed-app case in ``TestRestHandlerNamesAndAbsence``.

        What is left is the thing worth knowing: a row declaring a RestBinding
        must be reachable over REST. That is not a tautology — it fails if route
        generation drops a row.
        """
        table = AddressTable()
        rest_names = table.all_tools(Transport.REST)
        declared = {name for name, spec in TOOLS.items() if spec.rest is not None}

        assert rest_names == declared, (
            f"the REST surface and the registry disagree: "
            f"declared-not-reachable={sorted(declared - rest_names)}, "
            f"reachable-not-declared={sorted(rest_names - declared)}"
        )


class TestPathParamRegex:
    def test_extracts_single_param(self):
        assert PATH_PARAM_RE.findall("/api/v1/media-buys/{media_buy_id}") == ["media_buy_id"]

    def test_extracts_multiple_params(self):
        assert PATH_PARAM_RE.findall("/api/v1/a/{a_id}/b/{b_id}") == ["a_id", "b_id"]

    def test_no_params(self):
        assert PATH_PARAM_RE.findall("/api/v1/products") == []
