"""Given steps for entity setup (seller agent, creative agents, registries).

These steps establish the pre-conditions for scenarios — a running seller agent,
registered creative agents, and format catalogs.

Steps configure the harness registry with real Format objects via
``_sync_registry(ctx)``.
"""

from __future__ import annotations

from pytest_bdd import given, parsers

from tests.bdd.steps.generic._registry import sync_registry as _sync_registry

# ── Background steps (apply to every scenario) ──────────────────────


@given("a Seller Agent is operational and accepting requests")
def given_seller_operational(ctx: dict) -> None:
    """Seller agent is up and accepting requests (Background)."""
    ctx["seller_operational"] = True


@given("at least one creative agent is registered with format definitions")
def given_creative_agent_registered(ctx: dict) -> None:
    """At least one creative agent has format definitions (Background)."""
    ctx["creative_agents_registered"] = True
    ctx.setdefault("registry_formats", [])


# ── Creative agent registry: multi-category / type-specific ──────────


@given("the creative agent registry has formats across multiple categories")
def given_registry_multi_categories(ctx: dict) -> None:
    """Registry has formats spanning multiple categories (display, video, etc.)."""
    ctx["registry_formats"] = [
        {"name": "banner", "type": "display"},
        {"name": "pre-roll", "type": "video"},
        {"name": "audio-spot", "type": "audio"},
    ]
    _sync_registry(ctx)


@given(parsers.parse('the creative agent registry has formats of types "{type_a}" and "{type_b}"'))
def given_registry_two_types(ctx: dict, type_a: str, type_b: str) -> None:
    """Registry has formats of exactly two specified types."""
    ctx["registry_formats"] = [
        {"name": f"{type_a}-format", "type": type_a},
        {"name": f"{type_b}-format", "type": type_b},
    ]
    _sync_registry(ctx)


@given("the seller has additional creative agents beyond the default")
def given_additional_creative_agents(ctx: dict) -> None:
    """Seller has additional creative agent referrals."""
    ctx["creative_agent_referrals"] = [
        {
            "agent_url": "https://extra-creatives.example.com",
            "capabilities": ["display", "video"],
        },
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
    """Seller has formats across various type categories (partition/boundary)."""
    ctx["registry_formats"] = [
        {"name": "display-ad", "type": "display"},
        {"name": "video-ad", "type": "video"},
        {"name": "native-card", "type": "native"},
    ]
    _sync_registry(ctx)


@given("a seller with known format IDs in the catalog")
def given_seller_known_ids(ctx: dict) -> None:
    """Seller has formats with known IDs (partition/boundary)."""
    from src.core.schemas import FormatId

    ctx["registry_formats"] = [
        {"name": "fmt-a", "format_id": {"agent_url": "https://a.example.com", "id": "fmt-001"}},
        {"name": "fmt-b", "format_id": {"agent_url": "https://a.example.com", "id": "fmt-002"}},
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
        {"name": "image-ad", "assets": [{"type": "image"}]},
        {"name": "video-ad", "assets": [{"type": "video"}]},
        {"name": "rich-ad", "assets": [{"type": "image"}, {"type": "html"}]},
    ]
    _sync_registry(ctx)


@given("a seller with formats of various render dimensions")
def given_seller_various_dimensions(ctx: dict) -> None:
    """Seller has formats with various render dimensions (partition/boundary)."""
    ctx["registry_formats"] = [
        {"name": "banner", "renders": [{"width": 728, "height": 90}]},
        {"name": "skyscraper", "renders": [{"width": 160, "height": 600}]},
    ]
    _sync_registry(ctx)


@given("a seller with both responsive and fixed-dimension formats")
def given_seller_responsive_and_fixed(ctx: dict) -> None:
    """Seller has both responsive and fixed-dimension formats (partition/boundary)."""
    ctx["registry_formats"] = [
        {"name": "responsive-banner", "responsive": True},
        {"name": "fixed-banner", "responsive": False},
    ]
    _sync_registry(ctx)


@given(parsers.parse('a seller with formats named "{name_a}", "{name_b}", "{name_c}"'))
def given_seller_named_formats(ctx: dict, name_a: str, name_b: str, name_c: str) -> None:
    """Seller has formats with specific names (partition/boundary)."""
    ctx["registry_formats"] = [
        {"name": name_a},
        {"name": name_b},
        {"name": name_c},
    ]
    ctx["named_formats"] = [name_a, name_b, name_c]
    _sync_registry(ctx)


@given("a seller with formats at various accessibility conformance levels")
def given_seller_various_wcag(ctx: dict) -> None:
    """Seller has formats at various WCAG accessibility levels (partition/boundary)."""
    ctx["registry_formats"] = [
        {"name": "level-a", "wcag_level": "A"},
        {"name": "level-aa", "wcag_level": "AA"},
        {"name": "level-aaa", "wcag_level": "AAA"},
    ]
    _sync_registry(ctx)


@given("a seller with formats supporting various disclosure positions")
def given_seller_various_disclosure(ctx: dict) -> None:
    """Seller has formats with various disclosure positions (partition/boundary)."""
    ctx["registry_formats"] = [
        {"name": "prominent-ad", "supported_disclosure_positions": ["prominent"]},
        {"name": "footer-ad", "supported_disclosure_positions": ["footer"]},
    ]
    _sync_registry(ctx)


@given("a seller with formats that produce various output formats")
def given_seller_various_output_formats(ctx: dict) -> None:
    """Seller has formats with various output_format_ids (partition/boundary)."""
    from src.core.schemas import FormatId

    ctx["registry_formats"] = [
        {"name": "builder-a", "output_format_ids": [{"agent_url": "https://a.example.com", "id": "fmt-1"}]},
        {"name": "builder-b", "output_format_ids": [{"agent_url": "https://a.example.com", "id": "fmt-2"}]},
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

    ctx["registry_formats"] = [
        {"name": "resizer", "input_format_ids": [{"agent_url": "https://a.example.com", "id": "fmt-1"}]},
        {"name": "transcoder", "input_format_ids": [{"agent_url": "https://a.example.com", "id": "fmt-2"}]},
    ]
    ctx["known_input_format_ids"] = [
        FormatId(agent_url="https://a.example.com", id="fmt-1"),
        FormatId(agent_url="https://a.example.com", id="fmt-2"),
    ]
    _sync_registry(ctx)


@given("a seller with creative agent formats of various types")
def given_seller_creative_agent_various_types(ctx: dict) -> None:
    """Seller has creative agent formats of various types (partition/boundary)."""
    ctx["creative_agent_formats"] = [
        {"name": "audio-format", "type": "audio"},
        {"name": "video-format", "type": "video"},
        {"name": "display-format", "type": "display"},
        {"name": "dooh-format", "type": "dooh"},
    ]


@given("a seller with creative agent formats containing various asset types")
def given_seller_creative_agent_various_assets(ctx: dict) -> None:
    """Seller has creative agent formats with various asset types (partition/boundary)."""
    ctx["creative_agent_formats"] = [
        {"name": "image-format", "assets": [{"type": "image"}]},
        {"name": "video-format", "assets": [{"type": "video"}]},
        {"name": "text-format", "assets": [{"type": "text"}]},
    ]
