"""Unit tests for the local standard-formats catalog.

Verifies:
- SDK beta.4 canonical reference formats and local canonical aliases parse as
  valid :class:`Format` objects.
- ``get_standard_format`` returns the cached object for known IDs and
  ``None`` for unknown IDs.
- ``is_standard_agent`` matches the reference creative agent URL with
  trailing-slash + scheme tolerance.
- ``CreativeAgentRegistry.get_format`` short-circuits to the catalog
  without a network call when the agent_url is the reference agent.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from adcp import Format

from src.core.canonical_formats import CANONICAL_FORMAT_IDS
from src.core.standard_formats import (
    STANDARD_AGENT_URL,
    STANDARD_FORMAT_IDS,
    STANDARD_FORMATS,
    get_standard_format,
    is_standard_agent,
)


class TestStandardFormatCatalog:
    def test_catalog_has_sdk_reference_formats_canonical_aliases_and_legacy_aliases(self):
        """The SDK reference catalog is available alongside local canonical and legacy IDs."""
        sdk_expected = {
            "display_300x250_image",
            "display_300x250_html",
            "display_300x250_generative",
            "display_image",
            "display_html",
            "display_js",
            "native_standard",
            "audio_standard_30s",
            "audio_vast",
            "video_vast",
            "sponsored_recommendation",
        }
        legacy_expected = {
            "display_300x250",
            "display_728x90",
            "display_160x600",
            "display_300x600",
            "display_320x50",
            "display_970x250",
            "video_640x480",
            "audio_15s",
            "audio_30s",
            "audio_60s",
            "native_1x1",
        }
        assert sdk_expected <= STANDARD_FORMAT_IDS
        assert CANONICAL_FORMAT_IDS <= STANDARD_FORMAT_IDS
        assert legacy_expected <= STANDARD_FORMAT_IDS

    def test_every_entry_is_a_real_format_object(self):
        """All catalog entries deserialize as Format objects."""
        for fmt_id, fmt in STANDARD_FORMATS.items():
            assert isinstance(fmt, Format), f"{fmt_id} is not a Format"
            assert fmt.format_id.id == fmt_id
            # Pydantic AnyUrl normalizes by adding trailing slash; strip
            # before comparing to avoid the cosmetic mismatch.
            assert str(fmt.format_id.agent_url).rstrip("/") == STANDARD_AGENT_URL

    def test_every_entry_serializes_to_adcp_output_schema(self):
        """Published local catalog entries must validate at the MCP output boundary."""
        for fmt_id, fmt in STANDARD_FORMATS.items():
            payload = fmt.model_dump(mode="json", exclude_none=True)
            validated = Format.model_validate(payload)
            assert validated.format_id.id == fmt_id

            for asset in payload.get("assets") or []:
                if asset.get("item_type") == "individual":
                    assert asset.get("asset_type") != "pixel_tracker", fmt_id

    def test_get_known_format_returns_object(self):
        fmt = get_standard_format("display_image")
        assert fmt is not None
        assert fmt.format_id.id == "display_image"
        assert fmt.type == "display"

    def test_get_sdk_reference_format_returns_object(self):
        fmt = get_standard_format("display_300x250_image")
        assert fmt is not None
        assert fmt.format_id.id == "display_300x250_image"
        assert fmt.type == "display"

    def test_carousel_format_uses_repeatable_asset_group(self):
        fmt = get_standard_format("image_slideshow_5s_each")
        assert fmt is not None
        assert fmt.format_id.id == "image_slideshow_5s_each"
        assert fmt.type == "display"
        assert fmt.assets is not None
        group = fmt.assets[0]
        assert group.item_type == "repeatable_group"
        assert group.asset_group_id == "slide"
        assert group.min_count == 3
        assert group.max_count == 8

    def test_get_unknown_format_returns_none(self):
        assert get_standard_format("display_1x1_unknown_size") is None


class TestIsStandardAgent:
    def test_exact_match(self):
        assert is_standard_agent(STANDARD_AGENT_URL) is True

    def test_trailing_slash_tolerated(self):
        assert is_standard_agent(STANDARD_AGENT_URL + "/") is True

    def test_other_url_rejected(self):
        assert is_standard_agent("https://example.com") is False
        assert is_standard_agent("https://creative.scope3.com") is False

    def test_empty_rejected(self):
        assert is_standard_agent("") is False
        assert is_standard_agent(None) is False  # type: ignore[arg-type]


class TestExtendedFormatRegression:
    """Lock in the Critical Pattern #1 fix from PR #40 (issue #49).

    The catalog used to import Format from the **adcp library** instead of
    src.core.schemas.Format. The library Format lacks the salesagent-extended
    ``platform_config`` field. The GAM adapter at
    ``src/adapters/gam/managers/orders.py`` reads ``format_obj.platform_config``
    to build creative placeholders, so the wrong import threw AttributeError
    at line-item creation time.

    The class-level ``isinstance(fmt, Format)`` check above passes regardless
    (Format -> ExtendedFormat is a subclass relationship), so we need a
    dedicated regression that fails if the import drifts back.
    """

    def test_catalog_entries_are_salesagent_extended_format(self):
        from src.core.schemas import Format as ExtendedFormat

        for fid, fmt in STANDARD_FORMATS.items():
            assert isinstance(fmt, ExtendedFormat), (
                f"format {fid} is {type(fmt).__module__}.{type(fmt).__name__}; "
                "expected src.core.schemas.Format. "
                "standard_formats.py likely imported Format from the adcp library again."
            )

    def test_catalog_entries_expose_platform_config_attr(self):
        for fid, fmt in STANDARD_FORMATS.items():
            assert hasattr(fmt, "platform_config"), (
                f"format {fid} missing platform_config - "
                "the GAM adapter reads this attribute when building creative placeholders"
            )


class TestRegistryShortCircuit:
    def test_get_format_short_circuits_for_standard_agent(self):
        """Standard agent + standard format returns from catalog without a network round trip."""
        from src.core.creative_agent_registry import CreativeAgentRegistry

        registry = CreativeAgentRegistry()

        with patch.object(registry, "get_formats_for_agent", new=AsyncMock(return_value=[])) as mock_network:
            fmt = asyncio.run(registry.get_format(STANDARD_AGENT_URL, "display_image"))
            assert fmt is not None
            assert fmt.format_id.id == "display_image"
            mock_network.assert_not_called()

    def test_get_format_falls_through_for_custom_agent(self):
        """A non-standard agent_url skips the catalog and hits the live registry lookup."""
        from src.core.creative_agent_registry import CreativeAgentRegistry

        registry = CreativeAgentRegistry()

        with patch.object(registry, "get_formats_for_agent", new=AsyncMock(return_value=[])) as mock_network:
            fmt = asyncio.run(registry.get_format("https://creative.example.com", "display_image"))
            assert fmt is None
            mock_network.assert_called_once()

    def test_get_format_falls_through_for_unknown_format_on_standard_agent(self):
        """Standard agent but unknown format ID still hits the network."""
        from src.core.creative_agent_registry import CreativeAgentRegistry

        registry = CreativeAgentRegistry()

        with patch.object(registry, "get_formats_for_agent", new=AsyncMock(return_value=[])) as mock_network:
            asyncio.run(registry.get_format(STANDARD_AGENT_URL, "exotic_unknown_format"))
            mock_network.assert_called_once()
