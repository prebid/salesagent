"""Unit tests for channel string normalization."""

from src.core.tools.capabilities import normalize_channel_strings


class TestNormalizeChannelStrings:
    def test_maps_aliases_to_canonical_values(self):
        assert normalize_channel_strings(["video", "audio"]) == ["olv", "streaming_audio"]

    def test_deduplicates_and_sorts(self):
        assert normalize_channel_strings(["ctv", "display", "ctv"]) == ["ctv", "display"]

    def test_skips_unknown_values(self, caplog):
        import logging

        with caplog.at_level(logging.WARNING):
            result = normalize_channel_strings(["display", "native"])

        assert result == ["display"]
        assert any("native" in record.message for record in caplog.records)
