"""The two pure helpers behind the admin channel picker (GH #1532).

Kept apart from ``test_architecture_channel_selector_enum.py`` so that a missing helper
cannot mask the template guard: these tests import the helpers, the guard does not.
"""

from __future__ import annotations

import json

import pytest

from src.core.helpers.channel_helpers import media_channel_options, parse_media_channels
from tests.unit._architecture_helpers import REPO_ROOT

_PINNED = REPO_ROOT / "tests/fixtures/adcp_schemas_pinned/3.1.1/enums/channels.json"


class TestMediaChannelOptions:
    def test_values_match_the_pinned_enum_in_order(self):
        """Authority is the pinned schema file, not the enum compared to itself."""
        assert [value for value, _ in media_channel_options()] == json.loads(_PINNED.read_text())["enum"]

    @pytest.mark.parametrize(
        ("value", "label"),
        [
            ("olv", "OLV (Online Video)"),
            ("ctv", "CTV (Connected TV)"),
            ("dooh", "DOOH (Digital Out-of-Home)"),
            ("ooh", "OOH (Out-of-Home)"),
            ("linear_tv", "Linear TV"),
            ("streaming_audio", "Streaming Audio"),  # title-case fallback
        ],
    )
    def test_labels(self, value: str, label: str):
        assert dict(media_channel_options())[value] == label


class TestParseMediaChannels:
    def test_members_pass_through_in_order_without_duplicates(self):
        assert parse_media_channels(["olv", "display", "olv"]) == ["olv", "display"]

    def test_empty_is_empty(self):
        assert parse_media_channels([]) == []

    @pytest.mark.parametrize("posted", [["video"], ["display", "retail"]])
    def test_a_non_member_refuses_the_whole_post(self, posted: list[str]):
        """Reject, never translate: after the repair migration there is no legitimate source of an alias."""
        with pytest.raises(ValueError, match="MediaChannel"):
            parse_media_channels(posted)

    def test_the_refusal_names_the_offender(self):
        with pytest.raises(ValueError, match="video"):
            parse_media_channels(["video"])
