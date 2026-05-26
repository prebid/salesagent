"""Tests for the FreeWheel adapter's static creative format declaration."""

from __future__ import annotations

from adcp.types import Format

from src.adapters.freewheel.formats import freewheel_creative_formats
from src.core.canonical_formats import DEFAULT_CREATIVE_AGENT_URL


class TestFreeWheelCreativeFormats:
    def test_returns_canonical_vast_formats(self):
        formats = freewheel_creative_formats(tenant_id="t1")
        assert len(formats) == 2
        assert {f["format_id"]["id"] for f in formats} == {"video_vast"}
        assert {f["format_id"]["duration_ms"] for f in formats} == {15000, 30000}

    def test_agent_url_uses_reference_creative_agent(self):
        formats = freewheel_creative_formats(tenant_id="talpa")
        for fmt in formats:
            assert fmt["format_id"]["agent_url"] == DEFAULT_CREATIVE_AGENT_URL

    def test_agent_url_stays_canonical_when_tenant_is_none(self):
        formats = freewheel_creative_formats(tenant_id=None)
        for fmt in formats:
            assert fmt["format_id"]["agent_url"] == DEFAULT_CREATIVE_AGENT_URL

    def test_each_format_validates_against_adcp_format_schema(self):
        """Every declared format must parse cleanly as an adcp.types.Format."""
        for fmt in freewheel_creative_formats(tenant_id="t1"):
            Format.model_validate(fmt)  # raises if invalid

    def test_format_carries_vast_tag_asset(self):
        for fmt in freewheel_creative_formats(tenant_id="t1"):
            assert fmt["type"] == "video"
            assert len(fmt["assets"]) == 1
            asset = fmt["assets"][0]
            assert asset["asset_id"] == "vast_tag"
            assert asset["asset_type"] == "vast"
            assert asset["required"] is True


class TestAdapterIntegration:
    def test_adapter_get_creative_formats_returns_static_list(self):
        from unittest.mock import MagicMock

        from src.adapters.freewheel import FreeWheelAdapter

        principal = MagicMock()
        principal.principal_id = "p1"
        principal.get_adapter_id.return_value = "1356511"
        principal.platform_mappings = {"freewheel": {"advertiser_id": "1356511"}}

        adapter = FreeWheelAdapter(
            config={"api_token": "test-token"},
            principal=principal,
            dry_run=True,
            tenant_id="talpa",
        )

        formats = adapter.get_creative_formats()
        assert len(formats) == 2
        assert all(f["format_id"]["agent_url"] == DEFAULT_CREATIVE_AGENT_URL for f in formats)
