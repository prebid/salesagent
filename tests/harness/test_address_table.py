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
from unittest import mock

import pytest

from tests.harness.address_table import (
    PATH_PARAM_RE,
    REST_ABSENT_TOOLS,
    REST_TOOL_ALIASES,
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

    def test_no_address_for_transport_on_a2a_only_skill(self):
        """approve_creative is A2A-only. The live A2A-only set drifts over time —
        confirmed directly against ADDRESS_TABLE.all_tools() at write time rather
        than trusted from a hand-copied example list (see NoAddressForTransport's
        docstring for why this file deliberately avoids repeating one)."""
        table = AddressTable()
        with pytest.raises(NoAddressForTransport):
            table.resolve("approve_creative", Transport.MCP)
        with pytest.raises(NoAddressForTransport):
            table.resolve("approve_creative", Transport.REST)
        # But it DOES resolve on the transport it actually exists on:
        assert table.resolve("approve_creative", Transport.A2A).name == "approve_creative"

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
        (TestRestAliasesAndAbsence), not the one this test proves."""
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


class TestRestAliasesAndAbsence:
    """AC(2): a REST handler name that matches neither a known MCP/A2A tool
    name nor an entry in REST_TOOL_ALIASES must fail table construction
    loudly (:class:`UnresolvedRestHandlerName`), not silently register under
    the wrong (unresolved) name — the actual bug this ticket fixes."""

    def test_renamed_rest_handler_without_alias_raises_unresolved_rest_handler_name(self):
        """Simulates a REST handler that was renamed (fetch_products) without
        a matching REST_TOOL_ALIASES entry, while the real tool it should map
        to (get_products) genuinely exists on MCP. Today this silently
        succeeds and registers `fetch_products` as its own address, so
        `get_products` looks unavailable on REST even though a route for it
        exists under the wrong name. The fix must raise at table-build time
        instead of degrading into that silent miss."""
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
        """Two REST routes resolving to the SAME tool name (a real route plus a
        stale alias pointing at it, or two routes that both alias to one tool)
        must raise, not silently last-write-wins — aliasing makes this MORE
        likely, not less, so it needs its own guard (not just the loud-miss
        check above)."""
        from fastapi import FastAPI
        from fastmcp import FastMCP

        temp_mcp_app = FastMCP("throwaway-test-server")

        @temp_mcp_app.tool()
        def get_products(brief: str) -> dict:
            return {}

        temp_rest_app = FastAPI()

        @temp_rest_app.post("/api/v1/products")
        def get_products_v1() -> dict:  # noqa: F811 - route only, not a real handler collision
            return {}

        @temp_rest_app.post("/api/v1/products-again")
        def get_products_v2() -> dict:
            return {}

        aliases_with_collision = {"get_products_v1": "get_products", "get_products_v2": "get_products"}
        with mock.patch("tests.harness.address_table.REST_TOOL_ALIASES", aliases_with_collision):
            table = AddressTable(mcp_app=temp_mcp_app, rest_app=temp_rest_app)
            with pytest.raises(UnresolvedRestHandlerName):
                table.resolve("get_products", Transport.REST)

    def test_get_adcp_capabilities_resolves_on_rest_via_operation_id(self):
        """AC(1): get_adcp_capabilities genuinely resolves on REST — the ONE
        true rename mismatch (get_capabilities REST handler -> get_adcp_capabilities
        AdCP tool name), resolved via the route's self-declared operation_id —
        against the REAL production registries, not a synthetic app."""
        table = AddressTable()
        address = table.resolve("get_adcp_capabilities", Transport.REST)
        assert address.name == "get_adcp_capabilities"
        assert address.path_template == "/api/v1/capabilities"
        assert address.method == "get"

    def test_raw_rest_handler_name_does_not_resolve_as_a_tool_name(self):
        """`get_capabilities` is not an AdCP tool name and must not resolve.

        Every /api/v1 handler is now named after the tool it implements, so the
        old divergent handler name is not an identity anything can address."""
        table = AddressTable()
        with pytest.raises(NoAddressForTransport):
            table.resolve("get_capabilities", Transport.REST)

    def test_rest_absent_tools_stay_off_rest_and_are_real_tools(self):
        """AC(1): the four task tools are correctly, EXPLICITLY documented as
        REST-absent (not silently missing due to naming happenstance) — and the
        registry itself cannot rot: every entry must genuinely have no REST
        route AND genuinely be a real tool on MCP or A2A."""
        table = AddressTable()
        for tool_name in REST_ABSENT_TOOLS:
            with pytest.raises(NoAddressForTransport):
                table.resolve(tool_name, Transport.REST)
            resolves_elsewhere = False
            for transport in (Transport.MCP, Transport.A2A):
                try:
                    table.resolve(tool_name, transport)
                    resolves_elsewhere = True
                except NoAddressForTransport:
                    pass
            assert resolves_elsewhere, f"{tool_name!r} is in REST_ABSENT_TOOLS but resolves on neither MCP nor A2A"

    def test_rest_tool_aliases_pinned_exactly(self):
        """Reviewed-growth-only (CLAUDE.md allowlist convention): adding an
        alias requires a deliberate edit to this test, not a silent map growth.

        EMPTY, and it stays empty: every /api/v1 handler is named after the AdCP
        tool it implements, so the handler name is the tool identity and nothing
        needs aliasing. AdCP does not define REST -- this project does -- so the
        tool name is canonical across every transport."""
        assert REST_TOOL_ALIASES == {}

    def test_rest_tool_aliases_source_and_target_stay_live(self):
        """Mirror-direction staleness guard: an alias's source must still be a
        live REST handler name in production, and its target must still be a
        real tool — otherwise the alias entry is dead weight (or a typo) that
        would rot unnoticed."""
        from src.app import app

        live_handler_names = {
            endpoint.__name__
            for route in app.routes
            if getattr(route, "path", "").startswith("/api/v1") and getattr(route, "endpoint", None) is not None
            for endpoint in [route.endpoint]
        }
        table = AddressTable()
        table.resolve("get_products", Transport.MCP)  # force _build()
        known_tool_names = table.all_tools(Transport.MCP) | table.all_tools(Transport.A2A)
        for source, target in REST_TOOL_ALIASES.items():
            assert source in live_handler_names, f"alias source {source!r} is no longer a live REST handler name"
            assert target in known_tool_names, f"alias target {target!r} is not a known MCP/A2A tool name"


class TestRestVerbDeterminism:
    """AC(3): the chosen HTTP verb for a multi-verb route is deterministic —
    prefer POST, else sorted order — never ``next(iter(set))``."""

    def test_prefers_post_when_present(self):
        from fastapi import FastAPI
        from fastmcp import FastMCP

        temp_mcp_app = FastMCP("throwaway-test-server")

        @temp_mcp_app.tool()
        def get_products(brief: str) -> dict:
            return {}

        temp_rest_app = FastAPI()

        @temp_rest_app.api_route("/api/v1/products", methods=["GET", "POST"])
        def get_products_route() -> dict:
            return {}

        with mock.patch("tests.harness.address_table.REST_TOOL_ALIASES", {"get_products_route": "get_products"}):
            for _ in range(5):
                table = AddressTable(mcp_app=temp_mcp_app, rest_app=temp_rest_app)
                assert table.resolve("get_products", Transport.REST).method == "post"

    def test_sorted_fallback_when_no_post(self):
        from fastapi import FastAPI
        from fastmcp import FastMCP

        temp_mcp_app = FastMCP("throwaway-test-server")

        @temp_mcp_app.tool()
        def get_products(brief: str) -> dict:
            return {}

        temp_rest_app = FastAPI()

        @temp_rest_app.api_route("/api/v1/products", methods=["GET", "PUT"])
        def get_products_route() -> dict:
            return {}

        with mock.patch("tests.harness.address_table.REST_TOOL_ALIASES", {"get_products_route": "get_products"}):
            for _ in range(5):
                table = AddressTable(mcp_app=temp_mcp_app, rest_app=temp_rest_app)
                assert table.resolve("get_products", Transport.REST).method == "get"


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

    def test_day_one_registry_contents_pinned(self):
        """Pins the day-1 REST tool-name surface so this guard fails on a REAL
        registry change (a route added/removed, a tool renamed), not only on
        deletion of the loud-miss raise — strengthens the otherwise-
        tautological subset check above. Checks cardinality + representative
        membership rather than the full literal name list, to avoid a second
        near-copy of the route-name list tests/unit/test_rest_depends_auth.py
        already carries for a different purpose (this project's DRY
        invariant, CLAUDE.md)."""
        table = AddressTable()
        rest_names = table.all_tools(Transport.REST)
        # 13 includes get_media_buys (POST /api/v1/media-buys/query — PR #1950 / #1830).
        assert len(rest_names) == 13, rest_names
        assert "get_media_buys" in rest_names
        assert "get_adcp_capabilities" in rest_names  # handler is named after its tool
        assert "get_capabilities" not in rest_names  # raw handler name, not a tool identity
        for absent_tool in REST_ABSENT_TOOLS:
            assert absent_tool not in rest_names
        assert REST_TOOL_ALIASES == {}
        assert REST_ABSENT_TOOLS == frozenset({"complete_task", "get_task", "list_tasks"})


class TestPathParamRegex:
    def test_extracts_single_param(self):
        assert PATH_PARAM_RE.findall("/api/v1/media-buys/{media_buy_id}") == ["media_buy_id"]

    def test_extracts_multiple_params(self):
        assert PATH_PARAM_RE.findall("/api/v1/a/{a_id}/b/{b_id}") == ["a_id", "b_id"]

    def test_no_params(self):
        assert PATH_PARAM_RE.findall("/api/v1/products") == []
