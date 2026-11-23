"""The two pure helpers behind the admin channel picker (GH #1532).

Kept apart from ``test_architecture_channel_selector_enum.py`` so that a missing helper
cannot mask the template guard: these tests import the helpers, the guard does not.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from src.core.media_channels import media_channel_options, parse_media_channels
from src.core.operator_errors import InvalidFormInput
from tests.helpers.adcp_pin import EXPECTED_SPEC_VERSION
from tests.helpers.pinned_schema import load
from tests.unit._architecture_helpers import REPO_ROOT

#: The spec repo's own channels.json at the pinned tag (the vendored tree), not the SDK's copy.
_PINNED = f"{EXPECTED_SPEC_VERSION}/enums/channels.json"


class TestMediaChannelOptions:
    def test_values_match_the_pinned_enum_in_order(self):
        """Authority is the pinned schema file, not the enum compared to itself."""
        assert [value for value, _ in media_channel_options()] == load(_PINNED)["enum"]

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
    def test_members_come_back_in_enum_order_without_duplicates(self):
        """Whatever order they were posted in: enum order is what the picker posts and the repair writes."""
        assert parse_media_channels(["olv", "display", "olv"]) == ["display", "olv"]

    def test_empty_is_none(self):
        """SQL NULL is what the column stores for "no channels declared"."""
        assert parse_media_channels([]) is None

    @pytest.mark.parametrize("posted", [["video"], ["display", "retail"]])
    def test_a_non_member_refuses_the_whole_post(self, posted: list[str]):
        """Reject, never translate: after the repair migration there is no legitimate source of an alias."""
        with pytest.raises(InvalidFormInput):
            parse_media_channels(posted)

    def test_the_refusal_names_each_offender_on_the_channels_field(self):
        with pytest.raises(InvalidFormInput) as refused:
            parse_media_channels(["display", "video", "retail", "video"])
        assert [issue.field for issue in refused.value.issues] == ["channels", "channels"]
        assert refused.value.details() == (
            "channels: 'video' is not an AdCP media channel",
            "channels: 'retail' is not an AdCP media channel",
        )


def test_the_vocabulary_loads_neither_the_models_nor_the_adapters():
    """``Product.channels``' validator imports this module at module top, beneath the models.

    An import here that reached ``src.core.helpers`` would load the adapters, which import the
    models: the cycle the validator once hid behind a deferred import. Asked of a fresh
    interpreter, because this process has imported both already.
    """
    script = "import sys, src.core.media_channels; print(*sys.modules, sep='\\n')"
    loaded = subprocess.run(
        [sys.executable, "-c", script], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    assert "src.core.media_channels" in loaded
    assert [name for name in loaded if name.startswith(("src.adapters", "src.core.database", "src.core.helpers"))] == []
