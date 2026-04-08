"""Given steps for entity setup (seller agent, creative agents, registries).

These steps establish the pre-conditions for scenarios — a running seller agent,
registered creative agents, and format catalogs.

Steps construct real Format objects via FormatFactory and push them
to the harness via ``_sync_registry(ctx)``.
"""

from __future__ import annotations

from pytest_bdd import given, parsers

from tests.bdd.steps.generic._registry import load_real_catalog
from tests.bdd.steps.generic._registry import sync_registry as _sync_registry
from tests.factories.format import FormatFactory, FormatIdFactory


def _group_by_type(formats: list[object]) -> dict[str, list[object]]:
    """Group Format objects by their type value string."""
    by_type: dict[str, list[object]] = {}
    for f in formats:
        t = f.type.value if f.type else "unknown"
        by_type.setdefault(t, []).append(f)
    return by_type


# ── Background steps (apply to every scenario) ──────────────────────


@given("a Seller Agent is operational and accepting requests")
def given_seller_operational(ctx: dict) -> None:
    """Seller agent is up and accepting requests (Background)."""
    ctx["seller_operational"] = True


@given("a tenant is resolvable from the request context")
def given_tenant_resolvable(ctx: dict) -> None:
    """Tenant can be resolved from request context (Background)."""
    ctx["has_tenant"] = True
    ctx.setdefault("tenant_id", "test_tenant")


@given("a tenant has completed setup checklist")
@given("a tenant exists with completed setup checklist")
def given_tenant_setup_complete(ctx: dict) -> None:
    """Tenant has completed all setup steps (Background)."""
    ctx["tenant_setup_complete"] = True
    ctx.setdefault("tenant_id", "test_tenant")


@given(parsers.parse('an authenticated Buyer with principal_id "{principal_id}"'))
def given_authenticated_buyer(ctx: dict, principal_id: str) -> None:
    """Buyer is authenticated with the given principal_id (Background)."""
    ctx["principal_id"] = principal_id
    ctx["has_auth"] = True


@given(parsers.parse('the principal "{principal_id}" exists in the tenant database'))
def given_principal_exists(ctx: dict, principal_id: str) -> None:
    """Principal exists in the tenant database (Background).

    Actual DB record creation happens in the harness autouse fixture.
    This step records the principal_id for later use.
    """
    ctx.setdefault("principal_id", principal_id)
    ctx["principal_exists"] = True


@given(parsers.parse('an authenticated request with principal_id "{principal_id}"'))
def given_authenticated_request(ctx: dict, principal_id: str) -> None:
    """An authenticated request with a specific principal_id."""
    ctx["principal_id"] = principal_id
    ctx["has_auth"] = True


@given("at least one creative agent is registered with format definitions")
def given_creative_agent_registered(ctx: dict) -> None:
    """Background precondition: creative agents exist with real catalog.

    Loads the 49-format real catalog from .creative-agent-catalog.json and
    feeds it to the harness mock. Scenarios that need specific formats
    override via their own Given steps (which call _sync_registry).
    """
    real_formats = load_real_catalog()
    ctx["env"].set_registry_formats(real_formats)
    ctx["real_catalog"] = real_formats
    ctx["real_catalog_by_type"] = _group_by_type(real_formats)
    ctx["registry_formats"] = list(real_formats)
    ctx["creative_agents_registered"] = True


# ── Creative agent registry: multi-category / type-specific ──────────


@given("the creative agent registry has formats across multiple categories")
def given_registry_multi_categories(ctx: dict) -> None:
    """Registry has formats spanning multiple categories (display, video, etc.).

    Uses the real catalog loaded in Background and asserts it has the
    required category diversity rather than building fake formats.
    """
    real_formats = load_real_catalog()
    categories = {f.type.value for f in real_formats if f.type}
    assert len(categories) >= 3, f"Real catalog needs 3+ categories, got: {categories}"
    ctx["registry_formats"] = list(real_formats)
    _sync_registry(ctx)


@given(parsers.parse('the creative agent registry has formats of types "{type_a}" and "{type_b}"'))
def given_registry_two_types(ctx: dict, type_a: str, type_b: str) -> None:
    """Registry has formats of exactly two specified types, selected from the real catalog."""
    real_formats = load_real_catalog()
    selected = [f for f in real_formats if f.type and f.type.value in (type_a, type_b)]
    assert len(selected) >= 2, f"Real catalog needs formats of types {type_a} and {type_b}"
    ctx["registry_formats"] = selected
    _sync_registry(ctx)


@given("the seller has additional creative agents beyond the default")
def given_additional_creative_agents(ctx: dict) -> None:
    """Seller has additional creative agent referrals."""
    from adcp.types import CreativeAgent as LibraryCreativeAgent
    from adcp.types.generated_poc.enums.creative_agent_capability import CreativeAgentCapability

    ctx["creative_agent_referrals"] = [
        LibraryCreativeAgent(
            agent_url="https://extra-creatives.example.com",
            capabilities=[CreativeAgentCapability.assembly, CreativeAgentCapability.delivery],
        ),
    ]


@given("no creative agents have any registered formats")
def given_no_formats(ctx: dict) -> None:
    """No creative agents have any formats registered."""
    ctx["registry_formats"] = []
    ctx["creative_agents_registered"] = False
    _sync_registry(ctx)


# ── Partition / boundary: seller-with-various-X stubs ────────────────


@given("a seller with formats of various types")
def given_seller_various_types(ctx: dict) -> None:
    """Seller has formats across various type categories, from real catalog."""
    real_formats = load_real_catalog()
    # Select one format per type to keep partition tests focused
    seen_types: set[str] = set()
    selected: list[object] = []
    for f in real_formats:
        t = f.type.value if f.type else None
        if t and t not in seen_types:
            seen_types.add(t)
            selected.append(f)
    assert len(selected) >= 3, f"Need 3+ types, got {seen_types}"
    ctx["registry_formats"] = selected
    _sync_registry(ctx)


@given("a seller with known format IDs in the catalog")
def given_seller_known_ids(ctx: dict) -> None:
    """Seller has formats with known IDs from the real catalog."""
    from src.core.schemas import FormatId

    real_formats = load_real_catalog()
    # Pick two formats from the real catalog
    selected = real_formats[:2]
    ctx["registry_formats"] = list(real_formats)
    ctx["known_format_ids"] = [FormatId(agent_url=str(f.format_id.agent_url), id=f.format_id.id) for f in selected]
    _sync_registry(ctx)


@given("a seller with formats containing various asset types")
def given_seller_various_assets(ctx: dict) -> None:
    """Seller has formats with various asset types from the real catalog."""
    real_formats = load_real_catalog()
    # Select formats that have assets with various types
    ctx["registry_formats"] = [f for f in real_formats if f.assets]
    _sync_registry(ctx)


@given("a seller with formats of various render dimensions")
def given_seller_various_dimensions(ctx: dict) -> None:
    """Seller has formats with various render dimensions from the real catalog."""
    real_formats = load_real_catalog()
    ctx["registry_formats"] = [f for f in real_formats if f.renders]
    _sync_registry(ctx)


@given("a seller with both responsive and fixed-dimension formats")
def given_seller_responsive_and_fixed(ctx: dict) -> None:
    """Seller has both responsive and fixed-dimension formats from the real catalog."""
    real_formats = load_real_catalog()
    ctx["registry_formats"] = [f for f in real_formats if f.renders]
    # Verify the real catalog has both responsive and fixed
    has_responsive = any(
        r.dimensions and r.dimensions.responsive and r.dimensions.responsive.width
        for f in ctx["registry_formats"]
        for r in (f.renders or [])
        if hasattr(r, "dimensions") and r.dimensions
    )
    has_fixed = any(
        r.dimensions and r.dimensions.width and not (r.dimensions.responsive and r.dimensions.responsive.width)
        for f in ctx["registry_formats"]
        for r in (f.renders or [])
        if hasattr(r, "dimensions") and r.dimensions
    )
    assert has_responsive and has_fixed, "Real catalog needs both responsive and fixed formats"
    _sync_registry(ctx)


@given(parsers.parse('a seller with formats named "{name_a}", "{name_b}", "{name_c}"'))
def given_seller_named_formats(ctx: dict, name_a: str, name_b: str, name_c: str) -> None:
    """Seller has formats with specific names (partition/boundary)."""
    ctx["registry_formats"] = [
        FormatFactory.build(name=name_a),
        FormatFactory.build(name=name_b),
        FormatFactory.build(name=name_c),
    ]
    ctx["named_formats"] = [name_a, name_b, name_c]
    _sync_registry(ctx)


@given("a seller with formats at various accessibility conformance levels")
def given_seller_various_wcag(ctx: dict) -> None:
    """Seller has formats at various WCAG accessibility levels (partition/boundary)."""
    ctx["registry_formats"] = [
        FormatFactory.build(name="level-a", wcag_level="A"),
        FormatFactory.build(name="level-aa", wcag_level="AA"),
        FormatFactory.build(name="level-aaa", wcag_level="AAA"),
    ]
    _sync_registry(ctx)


@given("a seller with formats supporting various disclosure positions")
def given_seller_various_disclosure(ctx: dict) -> None:
    """Seller has formats with various disclosure positions (partition/boundary)."""
    ctx["registry_formats"] = [
        FormatFactory.build(name="prominent-ad", supported_disclosure_positions=["prominent"]),
        FormatFactory.build(name="footer-ad", supported_disclosure_positions=["footer"]),
    ]
    _sync_registry(ctx)


@given("a seller with formats that produce various output formats")
def given_seller_various_output_formats(ctx: dict) -> None:
    """Seller has formats with various output_format_ids.

    Uses real catalog as base and adds two formats with specific
    output_format_ids for partition/boundary testing.
    """
    from src.core.schemas import FormatId

    real_formats = load_real_catalog()
    out_1 = FormatIdFactory.build(agent_url="https://a.example.com", id="fmt-1")
    out_2 = FormatIdFactory.build(agent_url="https://a.example.com", id="fmt-2")
    extra = [
        FormatFactory.build(name="builder-a", output_format_ids=[out_1]),
        FormatFactory.build(name="builder-b", output_format_ids=[out_2]),
    ]
    ctx["registry_formats"] = list(real_formats) + extra
    ctx["known_output_format_ids"] = [
        FormatId(agent_url="https://a.example.com", id="fmt-1"),
        FormatId(agent_url="https://a.example.com", id="fmt-2"),
    ]
    _sync_registry(ctx)


@given("a seller with formats that accept various input formats")
def given_seller_various_input_formats(ctx: dict) -> None:
    """Seller has formats with various input_format_ids.

    Uses real catalog as base and adds two formats with specific
    input_format_ids for partition/boundary testing.
    """
    from src.core.schemas import FormatId

    real_formats = load_real_catalog()
    in_1 = FormatIdFactory.build(agent_url="https://a.example.com", id="fmt-1")
    in_2 = FormatIdFactory.build(agent_url="https://a.example.com", id="fmt-2")
    extra = [
        FormatFactory.build(name="resizer", input_format_ids=[in_1]),
        FormatFactory.build(name="transcoder", input_format_ids=[in_2]),
    ]
    ctx["registry_formats"] = list(real_formats) + extra
    ctx["known_input_format_ids"] = [
        FormatId(agent_url="https://a.example.com", id="fmt-1"),
        FormatId(agent_url="https://a.example.com", id="fmt-2"),
    ]
    _sync_registry(ctx)


@given("a seller with creative agent formats of various types")
def given_seller_creative_agent_various_types(ctx: dict) -> None:
    """Seller has creative agent formats of various types from the real catalog."""
    real_formats = load_real_catalog()
    # Select one format per type for the creative agent format list
    seen_types: set[str] = set()
    selected: list[object] = []
    for f in real_formats:
        t = f.type.value if f.type else None
        if t and t not in seen_types:
            seen_types.add(t)
            selected.append(f)
    ctx["creative_agent_formats"] = selected


@given("a seller with creative agent formats containing various asset types")
def given_seller_creative_agent_various_assets(ctx: dict) -> None:
    """Seller has creative agent formats with various asset types from the real catalog."""
    real_formats = load_real_catalog()
    # Select formats that have distinct asset types
    seen_asset_types: set[str] = set()
    selected: list[object] = []
    for f in real_formats:
        if f.assets:
            asset_type = getattr(f.assets[0], "asset_type", None)
            if asset_type and asset_type not in seen_asset_types:
                seen_asset_types.add(asset_type)
                selected.append(f)
    ctx["creative_agent_formats"] = selected
