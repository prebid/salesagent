"""Unit tests for channel string normalization."""

import pytest

from src.core.tools.capabilities import (
    InvalidChannelInput,
    canonicalize_supported_channels,
    normalize_channel_strings,
)


class TestNormalizeChannelStrings:
    def test_maps_aliases_to_canonical_values(self):
        assert normalize_channel_strings(["video", "audio"]) == ["olv", "streaming_audio"]

    def test_deduplicates_and_sorts(self):
        assert normalize_channel_strings(["ctv", "display", "ctv"]) == ["ctv", "display"]

    def test_sponsored_intelligence_is_canonical(self):
        assert normalize_channel_strings(["sponsored_intelligence"]) == ["sponsored_intelligence"]

    def test_skips_unknown_values(self, caplog):
        import logging

        with caplog.at_level(logging.WARNING, logger="src.core.tools.capabilities"):
            result = normalize_channel_strings(["display", "native"])

        assert result == ["display"]
        assert any("native" in record.getMessage() for record in caplog.records)


class TestCanonicalizeSupportedChannels:
    def test_canonicalizes_aliases(self):
        assert canonicalize_supported_channels(["video", "ctv"]) == ["ctv", "olv"]

    def test_rejects_non_list(self):
        with pytest.raises(InvalidChannelInput, match="list of strings"):
            canonicalize_supported_channels("display,ctv")

    def test_rejects_non_string_members(self):
        with pytest.raises(InvalidChannelInput, match="list of strings"):
            canonicalize_supported_channels(["display", 1])

    def test_rejects_unknown_values(self):
        with pytest.raises(InvalidChannelInput, match="native"):
            canonicalize_supported_channels(["display", "native"])
