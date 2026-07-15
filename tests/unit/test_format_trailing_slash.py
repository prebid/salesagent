"""Format-identity comparison canonicalizes URL variants (one shared key, #1172).

Exercises the PRODUCTION canonicalization (``format_id_identity`` /
``supported_format_keys``) rather than re-implementing rstrip locally — a
regression in the shared canonicalizer must fail here.
"""

from src.core.helpers.creative_helpers import format_key, supported_format_keys
from src.core.schemas import FormatId, format_id_identity


def test_format_comparison_with_trailing_slash():
    """FormatIds with and without trailing slashes share one identity key."""
    with_slash = FormatId(agent_url="https://creative.adcontextprotocol.org/", id="display_300x250_image")
    without_slash = FormatId(agent_url="https://creative.adcontextprotocol.org", id="display_300x250_image")

    assert format_id_identity(with_slash) == format_id_identity(without_slash)
    assert format_id_identity(with_slash) == ("https://creative.adcontextprotocol.org", "display_300x250_image")


def test_format_set_comparison_with_mixed_slashes():
    """supported_format_keys canonicalizes both sides of a set comparison."""
    product_keys = supported_format_keys(
        [
            FormatId(agent_url="https://creative.adcontextprotocol.org/", id="display_300x250_image"),
            FormatId(agent_url="https://creative.adcontextprotocol.org/", id="display_728x90_image"),
        ]
    )

    requested = format_key("https://creative.adcontextprotocol.org", "display_300x250_image")
    assert requested in product_keys

    unsupported = format_key("https://creative.adcontextprotocol.org", "video_30s")
    assert unsupported not in product_keys


def test_transport_suffix_and_case_variants_share_one_key():
    """/mcp, /a2a and host-case variants canonicalize to the same identity."""
    base = format_key("https://creative.adcontextprotocol.org", "display_300x250_image")
    for variant in (
        "https://creative.adcontextprotocol.org/mcp",
        "https://creative.adcontextprotocol.org/a2a",
        "https://creative.adcontextprotocol.org/.well-known/adcp/sales",
        "https://CREATIVE.ADCONTEXTPROTOCOL.ORG/",
    ):
        assert format_key(variant, "display_300x250_image") == base, variant
