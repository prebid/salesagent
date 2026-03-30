"""Regression test for empty creative agent registry format validation.

When the creative agent registry returns 0 formats (agent unreachable but no
exception raised), the product form validation should skip format ID checking
instead of rejecting all submitted formats as invalid.

Bug: The registry's list_all_formats_with_errors() catches all exceptions
internally and returns FormatFetchResult(formats=[], errors=[...]). The
ADCPConnectionError/ADCPTimeoutError catch in products.py never fires, so
valid_format_ids is empty and every format ID is rejected.

Fix: Skip validation when valid_format_ids is empty.
"""

import pytest

from src.admin.blueprints.products import _parse_format_entries

AGENT_URL = "https://creative.adcontextprotocol.org"


class TestParseFormatEntries:
    """Tests for _parse_format_entries helper."""

    def test_parses_video_formats_with_duration(self):
        """Video template expansion produces entries with duration_ms."""
        formats_parsed = [
            {"agent_url": AGENT_URL, "id": "video_standard", "duration_ms": 15000},
            {"agent_url": AGENT_URL, "id": "video_vast", "duration_ms": 15000},
            {"agent_url": AGENT_URL, "id": "video_standard", "duration_ms": 30000},
            {"agent_url": AGENT_URL, "id": "video_vast", "duration_ms": 30000},
        ]

        result = _parse_format_entries(formats_parsed)

        assert len(result) == 4
        assert result[0] == {"agent_url": AGENT_URL, "id": "video_standard", "duration_ms": 15000.0}
        assert result[1] == {"agent_url": AGENT_URL, "id": "video_vast", "duration_ms": 15000.0}

    def test_parses_display_formats_with_dimensions(self):
        """Display template expansion produces entries with width/height."""
        formats_parsed = [
            {"agent_url": AGENT_URL, "id": "display_image", "width": 300, "height": 250},
            {"agent_url": AGENT_URL, "id": "display_html", "width": 300, "height": 250},
            {"agent_url": AGENT_URL, "id": "display_js", "width": 300, "height": 250},
        ]

        result = _parse_format_entries(formats_parsed)

        assert len(result) == 3
        assert result[0] == {"agent_url": AGENT_URL, "id": "display_image", "width": 300, "height": 250}

    def test_supports_legacy_format_id_key(self):
        """Legacy form data uses 'format_id' instead of 'id'."""
        formats_parsed = [
            {"agent_url": AGENT_URL, "format_id": "video_standard"},
        ]

        result = _parse_format_entries(formats_parsed)

        assert len(result) == 1
        assert result[0]["id"] == "video_standard"

    def test_skips_entries_without_agent_url(self):
        formats_parsed = [
            {"id": "video_standard"},  # Missing agent_url
            {"agent_url": AGENT_URL, "id": "video_vast"},
        ]

        result = _parse_format_entries(formats_parsed)

        assert len(result) == 1
        assert result[0]["id"] == "video_vast"

    def test_skips_entries_without_format_id(self):
        formats_parsed = [
            {"agent_url": AGENT_URL},  # Missing id
            {"agent_url": AGENT_URL, "id": "video_vast"},
        ]

        result = _parse_format_entries(formats_parsed)

        assert len(result) == 1

    def test_empty_input_returns_empty(self):
        assert _parse_format_entries([]) == []

    def test_omits_optional_params_when_none(self):
        """Entries without dimensions/duration should not include those keys."""
        formats_parsed = [
            {"agent_url": AGENT_URL, "id": "native_standard"},
        ]

        result = _parse_format_entries(formats_parsed)

        assert result[0] == {"agent_url": AGENT_URL, "id": "native_standard"}
        assert "width" not in result[0]
        assert "height" not in result[0]
        assert "duration_ms" not in result[0]


class TestEmptyRegistrySkipsValidation:
    """Regression: empty registry should not reject all format IDs.

    This simulates the bug where list_all_formats() returns [] because the
    creative agent is unreachable (exception caught internally by the registry).
    The product save should succeed, not flash "Invalid format IDs".
    """

    @pytest.fixture
    def video_formats_json(self):
        """JSON payload matching what the frontend sends for a video product."""
        import json

        return json.dumps(
            [
                {"agent_url": AGENT_URL, "id": "video_standard", "duration_ms": 15000},
                {"agent_url": AGENT_URL, "id": "video_vast", "duration_ms": 15000},
                {"agent_url": AGENT_URL, "id": "video_standard", "duration_ms": 30000},
                {"agent_url": AGENT_URL, "id": "video_vast", "duration_ms": 30000},
            ]
        )

    def test_empty_registry_preserves_all_format_entries(self, video_formats_json):
        """When registry returns 0 formats, all submitted entries should be kept."""
        import json

        formats_parsed = json.loads(video_formats_json)

        # This is what happens when valid_format_ids is empty:
        # the fix calls _parse_format_entries instead of rejecting
        result = _parse_format_entries(formats_parsed)

        assert len(result) == 4
        format_ids = [f["id"] for f in result]
        assert format_ids.count("video_standard") == 2
        assert format_ids.count("video_vast") == 2

    def test_display_formats_preserved_with_empty_registry(self):
        """Display formats also preserved when registry returns 0 formats."""
        import json

        formats_parsed = json.loads(
            json.dumps(
                [
                    {"agent_url": AGENT_URL, "id": "display_image", "width": 300, "height": 250},
                    {"agent_url": AGENT_URL, "id": "display_html", "width": 300, "height": 250},
                    {"agent_url": AGENT_URL, "id": "display_js", "width": 300, "height": 250},
                    {"agent_url": AGENT_URL, "id": "display_image", "width": 728, "height": 90},
                    {"agent_url": AGENT_URL, "id": "display_html", "width": 728, "height": 90},
                    {"agent_url": AGENT_URL, "id": "display_js", "width": 728, "height": 90},
                ]
            )
        )

        result = _parse_format_entries(formats_parsed)

        assert len(result) == 6
        format_ids = [f["id"] for f in result]
        assert format_ids.count("display_image") == 2
        assert format_ids.count("display_html") == 2
        assert format_ids.count("display_js") == 2
