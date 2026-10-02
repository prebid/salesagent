"""Given steps for entity setup (seller agent, creative agents, registries).

These steps establish the pre-conditions for scenarios — a running seller agent,
registered creative agents, and format catalogs.

Steps construct real Format objects via FormatFactory and push them
to the harness via ``_sync_registry(ctx)``.
"""

from __future__ import annotations

from pytest_bdd import given, parsers

from tests.bdd.steps.generic._registry import sync_registry as _sync_registry
from tests.factories.format import (
    ASSET_TYPES,
    CATEGORY_MAP,
    FormatFactory,
    FormatIdFactory,
    make_asset,
    make_fixed_renders,
    make_renders,
    make_responsive_renders,
    pick_reference_format,
)

# ── Background steps (apply to every scenario) ──────────────────────


@given("a Seller Agent is operational and accepting requests")
def given_seller_operational(ctx: dict) -> None:
    """Seller agent is up and accepting requests (Background).

    The harness env IS the seller agent, so "operational" is not something this
    sentence configures -- it is something it can check: a scenario reaches this
    Background with a env carrying the tenant its requests resolve against. The
    ctx flag it used to set was read by no step.
    """
    assert ctx["env"]._tenant_id, (
        "Background claims a Seller Agent is operational and accepting requests, "
        "but the harness env carries no tenant for a request to resolve against."
    )


@given("a tenant is resolvable from the request context")
def given_tenant_resolvable(ctx: dict) -> None:
    """Seed the seller, so the sentence is true.

    It used to set a ctx string and nothing else, which named a tenant without creating
    one. A request addressing that id resolved anyway, through the loopback fallback to the
    ``default`` tenant that #2259 removed — so the scenarios reading this Background were
    served by a tenant they never mentioned, and once the fallback went they were refused
    TENANT_UNDEFINED for want of a seller. That refusal is correct; the Background was
    the thing that was wrong.

    ``setup_default_data`` is get-or-create, so an env that already seeded (every e2e env,
    via ``_seed_identity``) is unaffected, and it seeds the env's OWN tenant id — the one
    ``env.credential()`` addresses — rather than the ctx string, which is what makes the
    request resolve.
    """
    ctx.setdefault("tenant_id", "test_tenant")
    ctx["env"].setup_default_data()


@given("a tenant exists with completed setup checklist")
def given_tenant_setup_complete(ctx: dict) -> None:
    """Tenant has completed all setup steps (Background)."""
    ctx.setdefault("tenant_id", "test_tenant")


@given(parsers.parse('the principal "{principal_id}" exists in the tenant database'))
def given_principal_exists(ctx: dict, principal_id: str) -> None:
    """Principal exists in the tenant database (Background).

    Actual DB record creation happens in the harness autouse fixture.
    This step records the principal_id for later use.
    """
    ctx.setdefault("principal_id", principal_id)


@given("at least one creative agent is registered with format definitions")
def given_creative_agent_registered(ctx: dict) -> None:
    """At least one creative agent has format definitions (Background)."""
    ctx.setdefault("registry_formats", [])


# ── Creative agent registry: multi-category / type-specific ──────────


@given("the creative agent registry has formats across multiple categories")
def given_registry_multi_categories(ctx: dict) -> None:
    """Registry has formats spanning multiple categories, drawn from the reference catalog.

    Real formats rather than minted ids: the live e2e stack serves only the captured
    reference catalog, so a minted ``fmt_N`` could never be realized there
    (E2EUnsupportedSetup, the scenario graded nothing), and a minted format carries no
    assets, so POST-S2 graded nothing in-process either. The three are chosen without
    ``pixel_tracker`` assets, which the pinned Format.assets union does not admit
    (adcp#7338); the catalog has no such audio format, so native stands in for it.
    """
    ctx["registry_formats"] = [
        pick_reference_format(category, without_asset_type="pixel_tracker")
        for category in ("display", "video", "native")
    ]
    _sync_registry(ctx)


@given(parsers.parse('the creative agent registry has formats of types "{type_a}" and "{type_b}"'))
def given_registry_two_types(ctx: dict, type_a: str, type_b: str) -> None:
    """Registry has formats of exactly two specified types."""
    ctx["registry_formats"] = [
        FormatFactory.build(name=f"{type_a}-format", type=CATEGORY_MAP.get(type_a)),
        FormatFactory.build(name=f"{type_b}-format", type=CATEGORY_MAP.get(type_b)),
    ]
    _sync_registry(ctx)


@given("the seller has additional creative agents beyond the default")
def given_additional_creative_agents(ctx: dict) -> None:
    """One extra creative agent, seeded as the operator ROW production reads.

    ``creative_agents`` on the response is built from
    ``registry._get_tenant_agents(tenant_id)`` (src/core/tools/creative_formats.py), and
    that method reads the tenant's enabled ``creative_agents`` rows through
    ``CreativeAgentRepository``. A referral is therefore ordinary operator
    configuration, and a factory row is ONE mechanism that realizes this sentence in both
    worlds -- in process the env's factories write the per-test database, over e2e_rest
    they write the live server's own -- so no ``@realize_e2e`` branch is needed. Same
    shape, and the same reason, as the UC-010 channel Given.

    It used to put a DICT in ``ctx`` and write nothing anywhere. Nothing realized it, so
    the response carried the default agent alone (or, in process, nothing at all), and the
    comparison in ``then_has_referrals`` -- ``{str(a.agent_url) for a in given_agents}`` --
    would have been an ``AttributeError`` on a dict rather than an assertion. The ctx value
    is the ROW.

    No ``capabilities`` here: production advertises
    ``ADVERTISED_CREATIVE_AGENT_CAPABILITIES`` for every referral it emits, so a per-agent
    list in the Given would have been a number this step invented and nothing read.
    """
    from tests.factories import CreativeAgentFactory

    env = ctx["env"]
    tenant, _principal = env.setup_default_data()
    ctx["creative_agent_referrals"] = [
        CreativeAgentFactory(tenant=tenant, agent_url="https://extra-creatives.example.com/mcp")
    ]
    env._commit_factory_data()


@given("no creative agents have any registered formats")
def given_no_formats(ctx: dict) -> None:
    """No creative agents have any formats registered."""
    ctx["registry_formats"] = []
    _sync_registry(ctx)


# ── Partition / boundary: seller-with-various-X stubs ────────────────


@given("a seller with formats of various types")
def given_seller_various_types(ctx: dict) -> None:
    """Seller has formats across various type categories (partition/boundary)."""
    ctx["registry_formats"] = [
        FormatFactory.build(name="display-ad", type=CATEGORY_MAP["display"]),
        FormatFactory.build(name="video-ad", type=CATEGORY_MAP["video"]),
        FormatFactory.build(name="native-card", type=CATEGORY_MAP["native"]),
    ]
    _sync_registry(ctx)


@given("a seller with known format IDs in the catalog")
def given_seller_known_ids(ctx: dict) -> None:
    """Seller has formats with known IDs (partition/boundary)."""
    from src.core.schemas import FormatId

    fid_1 = FormatIdFactory.build(agent_url="https://a.example.com", id="fmt-001")
    fid_2 = FormatIdFactory.build(agent_url="https://a.example.com", id="fmt-002")
    ctx["registry_formats"] = [
        FormatFactory.build(name="fmt-a", format_id=fid_1),
        FormatFactory.build(name="fmt-b", format_id=fid_2),
    ]
    ctx["known_format_ids"] = [
        FormatId(agent_url="https://a.example.com", id="fmt-001"),
        FormatId(agent_url="https://a.example.com", id="fmt-002"),
    ]
    _sync_registry(ctx)


@given("a seller with formats containing various asset types")
def given_seller_various_assets(ctx: dict) -> None:
    """Seller has formats with various asset types (partition/boundary)."""
    ctx["registry_formats"] = [
        FormatFactory.build(name="image-ad", assets=[make_asset("image")]),
        FormatFactory.build(name="video-ad", assets=[make_asset("video")]),
        FormatFactory.build(name="rich-ad", assets=[make_asset("image"), make_asset("html")]),
    ]
    _sync_registry(ctx)


@given("a seller with formats of various render dimensions")
def given_seller_various_dimensions(ctx: dict) -> None:
    """Seller has formats with various render dimensions (partition/boundary)."""
    ctx["registry_formats"] = [
        FormatFactory.build(name="banner", renders=[make_renders(width=728, height=90)]),
        FormatFactory.build(name="skyscraper", renders=[make_renders(width=160, height=600)]),
    ]
    _sync_registry(ctx)


@given("a seller with both responsive and fixed-dimension formats")
def given_seller_responsive_and_fixed(ctx: dict) -> None:
    """Seller has both responsive and fixed-dimension formats (partition/boundary)."""
    ctx["registry_formats"] = [
        FormatFactory.build(name="responsive-banner", renders=[make_responsive_renders()]),
        FormatFactory.build(name="fixed-banner", renders=[make_fixed_renders()]),
    ]
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
    """Seller has formats with various output_format_ids (partition/boundary)."""
    from src.core.schemas import FormatId

    out_1 = FormatIdFactory.build(agent_url="https://a.example.com", id="fmt-1")
    out_2 = FormatIdFactory.build(agent_url="https://a.example.com", id="fmt-2")
    ctx["registry_formats"] = [
        FormatFactory.build(name="builder-a", output_format_ids=[out_1]),
        FormatFactory.build(name="builder-b", output_format_ids=[out_2]),
    ]
    ctx["known_output_format_ids"] = [
        FormatId(agent_url="https://a.example.com", id="fmt-1"),
        FormatId(agent_url="https://a.example.com", id="fmt-2"),
    ]
    _sync_registry(ctx)


@given("a seller with formats that accept various input formats")
def given_seller_various_input_formats(ctx: dict) -> None:
    """Seller has formats with various input_format_ids (partition/boundary)."""
    from src.core.schemas import FormatId

    in_1 = FormatIdFactory.build(agent_url="https://a.example.com", id="fmt-1")
    in_2 = FormatIdFactory.build(agent_url="https://a.example.com", id="fmt-2")
    ctx["registry_formats"] = [
        FormatFactory.build(name="resizer", input_format_ids=[in_1]),
        FormatFactory.build(name="transcoder", input_format_ids=[in_2]),
    ]
    ctx["known_input_format_ids"] = [
        FormatId(agent_url="https://a.example.com", id="fmt-1"),
        FormatId(agent_url="https://a.example.com", id="fmt-2"),
    ]
    _sync_registry(ctx)


@given("a seller with creative agent formats of various types")
def given_seller_creative_agent_various_types(ctx: dict) -> None:
    """Seller has creative agent formats of various types (partition/boundary).

    The catalog these scenarios query is the harness default reference set, and it
    has to stay that way: T-UC-005-partition-agent-type and its boundary sibling
    are ledgered against upstream adcp#7338 precisely because the REFERENCE
    formats declare ``pixel_tracker``. Replacing the registry here would erase the
    gap the ledger entry grades.

    So what the sentence can establish is that the variety it claims is real --
    every type it names must be one the pinned format vocabulary admits. The list
    of raw dicts this replaced was written into a ctx key no step read (and raw
    dicts in a registry Given are what ``test_architecture_bdd_no_dict_registry``
    exists to refuse).
    """
    for creative_type in ("audio", "video", "display", "dooh"):
        assert creative_type in CATEGORY_MAP, (
            f"Step claims a seller catalog 'of various types', but {creative_type!r} is "
            f"not a known creative format category: {sorted(CATEGORY_MAP)}"
        )


@given("a seller with creative agent formats containing various asset types")
def given_seller_creative_agent_various_assets(ctx: dict) -> None:
    """Seller has creative agent formats with various asset types (partition/boundary).

    Same reasoning as ``given_seller_creative_agent_various_types``: the served
    catalog stays the reference set, so this checks the claimed variety against
    the pinned asset vocabulary instead of recording it into a dead ctx key.
    """
    for asset_type in ("image", "video", "text"):
        assert asset_type in ASSET_TYPES, (
            f"Step claims format assets 'of various types', but {asset_type!r} is not "
            f"a known format asset type: {sorted(ASSET_TYPES)}"
        )
