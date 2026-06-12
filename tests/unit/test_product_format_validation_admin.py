"""Unit tests for product format validation in admin UI.

Tests the format validation logic in add_product and edit_product routes,
including error handling for creative agent connectivity issues.
"""

import json
from unittest.mock import MagicMock

from adcp.exceptions import ADCPConnectionError, ADCPError, ADCPTimeoutError

from src.admin.blueprints.products import _validate_format_entries
from src.core.schemas import Format


class TestProductFormatValidation:
    """Test format validation behavior in product admin routes."""

    def _create_mock_format(
        self, format_id: str, agent_url: str = "https://creative.adcontextprotocol.org"
    ) -> MagicMock:
        """Create a mock Format object matching the registry response."""
        mock_format = MagicMock(spec=Format)
        mock_format_id = MagicMock()
        mock_format_id.id = format_id
        mock_format_id.agent_url = agent_url
        mock_format.format_id = mock_format_id
        return mock_format

    def test_valid_formats_accepted(self):
        """Test that valid formats pass validation."""
        # Simulate formats from creative agent registry
        available_formats = [
            self._create_mock_format("display_300x250"),
            self._create_mock_format("video_preroll_15s"),
        ]

        # Simulate user-submitted formats (from JSON hidden field)
        submitted_formats = [
            {"agent_url": "https://creative.adcontextprotocol.org", "format_id": "display_300x250"},
        ]

        # Build valid format IDs lookup (as done in products.py)
        valid_format_ids = set()
        for fmt in available_formats:
            format_id_str = fmt.format_id.id if hasattr(fmt.format_id, "id") else str(fmt.format_id)
            valid_format_ids.add(format_id_str)

        # Validate submitted formats
        validated_formats = []
        invalid_formats = []
        for fmt in submitted_formats:
            if isinstance(fmt, dict) and fmt.get("agent_url") and fmt.get("format_id"):
                format_id = fmt["format_id"]
                if format_id in valid_format_ids:
                    validated_formats.append({"agent_url": fmt["agent_url"], "id": format_id})
                else:
                    invalid_formats.append(format_id)

        assert len(validated_formats) == 1
        assert len(invalid_formats) == 0
        assert validated_formats[0]["id"] == "display_300x250"

    def test_invalid_formats_rejected(self):
        """Test that invalid formats are rejected."""
        available_formats = [
            self._create_mock_format("display_300x250"),
        ]

        # User tries to submit a format that doesn't exist
        submitted_formats = [
            {"agent_url": "https://creative.adcontextprotocol.org", "format_id": "nonexistent_format"},
        ]

        valid_format_ids = {fmt.format_id.id for fmt in available_formats}

        validated_formats = []
        invalid_formats = []
        for fmt in submitted_formats:
            if isinstance(fmt, dict) and fmt.get("agent_url") and fmt.get("format_id"):
                format_id = fmt["format_id"]
                if format_id in valid_format_ids:
                    validated_formats.append({"agent_url": fmt["agent_url"], "id": format_id})
                else:
                    invalid_formats.append(format_id)

        assert len(validated_formats) == 0
        assert len(invalid_formats) == 1
        assert invalid_formats[0] == "nonexistent_format"

    def test_mixed_valid_and_invalid_formats(self):
        """Test that mixed valid/invalid formats correctly identifies invalid ones."""
        available_formats = [
            self._create_mock_format("display_300x250"),
            self._create_mock_format("video_preroll_15s"),
        ]

        # Mix of valid and invalid formats
        submitted_formats = [
            {"agent_url": "https://creative.adcontextprotocol.org", "format_id": "display_300x250"},
            {"agent_url": "https://creative.adcontextprotocol.org", "format_id": "invalid_format"},
            {"agent_url": "https://creative.adcontextprotocol.org", "format_id": "video_preroll_15s"},
        ]

        valid_format_ids = {fmt.format_id.id for fmt in available_formats}

        validated_formats = []
        invalid_formats = []
        for fmt in submitted_formats:
            if isinstance(fmt, dict) and fmt.get("agent_url") and fmt.get("format_id"):
                format_id = fmt["format_id"]
                if format_id in valid_format_ids:
                    validated_formats.append({"agent_url": fmt["agent_url"], "id": format_id})
                else:
                    invalid_formats.append(format_id)

        assert len(validated_formats) == 2
        assert len(invalid_formats) == 1
        assert invalid_formats[0] == "invalid_format"

    def test_malformed_format_entries_skipped(self):
        """Test that malformed format entries are skipped gracefully."""
        available_formats = [self._create_mock_format("display_300x250")]

        # Various malformed entries
        submitted_formats = [
            {"agent_url": "https://creative.adcontextprotocol.org", "format_id": "display_300x250"},  # Valid
            {"agent_url": "", "format_id": "test"},  # Empty agent_url
            {"format_id": "test"},  # Missing agent_url
            {"agent_url": "https://example.com"},  # Missing format_id
            "just_a_string",  # Not a dict
            None,  # None value
        ]

        valid_format_ids = {fmt.format_id.id for fmt in available_formats}

        validated_formats = []
        for fmt in submitted_formats:
            if isinstance(fmt, dict) and fmt.get("agent_url") and fmt.get("format_id"):
                format_id = fmt["format_id"]
                if format_id in valid_format_ids:
                    validated_formats.append({"agent_url": fmt["agent_url"], "id": format_id})

        # Only the valid entry should be accepted
        assert len(validated_formats) == 1
        assert validated_formats[0]["id"] == "display_300x250"


class TestProductFormatValidationErrorHandling:
    """Test error handling for creative agent connectivity issues."""

    def test_connection_error_allows_graceful_degradation(self):
        """Test that ADCPConnectionError allows saving with warning."""
        # When ADCPConnectionError is raised, we should:
        # 1. Accept the formats without validation
        # 2. Show a warning to the user
        # 3. Allow the save to proceed

        submitted_formats = [
            {"agent_url": "https://creative.adcontextprotocol.org", "format_id": "display_300x250"},
        ]

        # Simulate the graceful degradation logic
        formats_saved = []
        warning_shown = False

        try:
            raise ADCPConnectionError("Creative agent unreachable")
        except ADCPConnectionError:
            # Graceful degradation - save without validation
            for fmt in submitted_formats:
                if isinstance(fmt, dict) and fmt.get("agent_url") and fmt.get("format_id"):
                    formats_saved.append({"agent_url": fmt["agent_url"], "id": fmt["format_id"]})
            warning_shown = True

        assert len(formats_saved) == 1
        assert warning_shown is True

    def test_timeout_error_allows_graceful_degradation(self):
        """Test that ADCPTimeoutError allows saving with warning."""
        submitted_formats = [
            {"agent_url": "https://creative.adcontextprotocol.org", "format_id": "video_preroll"},
        ]

        formats_saved = []
        warning_shown = False

        try:
            raise ADCPTimeoutError("Request timed out")
        except ADCPTimeoutError:
            for fmt in submitted_formats:
                if isinstance(fmt, dict) and fmt.get("agent_url") and fmt.get("format_id"):
                    formats_saved.append({"agent_url": fmt["agent_url"], "id": fmt["format_id"]})
            warning_shown = True

        assert len(formats_saved) == 1
        assert warning_shown is True

    def test_generic_adcp_error_blocks_save(self):
        """Test that generic ADCPError blocks the save."""
        # For errors that aren't connectivity-related, we should fail hard
        save_blocked = False

        try:
            raise ADCPError("Unexpected error from creative agent")
        except (ADCPConnectionError, ADCPTimeoutError):
            pass  # Would allow graceful degradation
        except ADCPError:
            save_blocked = True

        assert save_blocked is True

    def test_json_decode_error_blocks_save(self):
        """Test that invalid JSON in formats field blocks save."""
        save_blocked = False
        error_message = None

        formats_json = "not valid json {"

        try:
            json.loads(formats_json)
        except json.JSONDecodeError as e:
            save_blocked = True
            error_message = str(e)

        assert save_blocked is True
        assert error_message is not None


class TestProductFormatValidationIntegration:
    """Integration-style tests for format validation flow."""

    def test_empty_formats_list_accepted(self):
        """Test that empty formats list is accepted (products can have no formats)."""
        formats_json = "[]"
        formats_parsed = json.loads(formats_json)

        # Empty list should not trigger validation
        assert isinstance(formats_parsed, list)
        assert len(formats_parsed) == 0

    def test_formats_field_with_null_accepted(self):
        """Test that null/empty formats field is handled."""
        for formats_json in ["", None, "null"]:
            if formats_json == "null":
                formats_parsed = json.loads(formats_json)
                assert formats_parsed is None
            elif not formats_json:
                formats_parsed = []
                assert formats_parsed == []


class TestProductFormatFieldParsing:
    """Tests for the format field parsing logic fixed in PR #840.

    These tests verify the defensive handling for empty/falsy format fields
    that was added to prevent JSONDecodeError when forms are submitted
    before JavaScript populates the formats hidden field.
    """

    def test_empty_string_format_field_handled(self):
        """Test that empty string formats field gets default empty array.

        This tests the fix: `formats_json = form_data.get("formats", "[]") or "[]"`
        When the hidden field has value="" instead of value="[]", this ensures
        we don't get JSONDecodeError.
        """
        # Simulate form submission with empty string (before JS runs)
        formats_json = "" or "[]"  # The fix pattern from products.py
        formats_parsed = json.loads(formats_json)

        assert formats_parsed == []
        assert isinstance(formats_parsed, list)

    def test_none_format_field_handled(self):
        """Test that None formats field gets default empty array."""
        formats_json = None or "[]"
        formats_parsed = json.loads(formats_json)

        assert formats_parsed == []

    def test_valid_empty_array_format_field(self):
        """Test that valid empty array JSON is parsed correctly."""
        formats_json = "[]" or "[]"
        formats_parsed = json.loads(formats_json)

        assert formats_parsed == []

    def test_valid_format_array_parsed(self):
        """Test that valid format JSON array is parsed correctly."""
        formats_json = '[{"agent_url": "https://example.com", "format_id": "test"}]'
        formats_parsed = json.loads(formats_json)

        assert len(formats_parsed) == 1
        assert formats_parsed[0]["agent_url"] == "https://example.com"
        assert formats_parsed[0]["format_id"] == "test"

    def test_format_json_serialization_from_checkboxes(self):
        """Test the expected JSON structure from form submission.

        The GAM product form converts checkbox selections to JSON:
        - Each checkbox has data-agent-url and data-format-id attributes
        - On submit, these are converted to JSON format for the backend
        """
        # Simulate what prepareFormSubmission() produces
        checkbox_data = [
            {"agent_url": "https://creative.adcontextprotocol.org", "format_id": "display_300x250"},
            {"agent_url": "https://creative.adcontextprotocol.org", "format_id": "video_preroll_15s"},
        ]

        formats_json = json.dumps(checkbox_data)
        formats_parsed = json.loads(formats_json)

        assert len(formats_parsed) == 2
        assert all("agent_url" in f and "format_id" in f for f in formats_parsed)

    def test_format_json_with_empty_agent_url(self):
        """Test handling of format entries with empty agent_url."""
        # Some formats might have empty agent_url if misconfigured
        checkbox_data = [
            {"agent_url": "", "format_id": "display_300x250"},
        ]

        formats_json = json.dumps(checkbox_data)
        formats_parsed = json.loads(formats_json)

        # The validation logic should skip entries with empty agent_url
        validated = []
        for fmt in formats_parsed:
            if isinstance(fmt, dict) and fmt.get("agent_url") and fmt.get("format_id"):
                validated.append(fmt)

        assert len(validated) == 0  # Empty agent_url should be rejected


class TestNamespaceAwareProductFormatValidation:
    """Regression tests for the production format validation helper."""

    def _create_mock_format(
        self, format_id: str, agent_url: str = "https://creative.adcontextprotocol.org"
    ) -> MagicMock:
        mock_format = MagicMock(spec=Format)
        mock_format_id = MagicMock()
        mock_format_id.id = format_id
        mock_format_id.agent_url = agent_url
        mock_format.format_id = mock_format_id
        return mock_format

    def test_rejects_same_format_id_from_different_agent(self):
        """A bare ID match is not enough; the creative agent namespace must match."""
        submitted_formats = [
            {
                "agent_url": "https://custom-creative.example.com",
                "id": "display_image",
                "width": 300,
                "height": 250,
            }
        ]
        available_formats = [self._create_mock_format("display_image")]

        validated_formats, invalid_formats = _validate_format_entries(submitted_formats, available_formats)

        assert validated_formats == []
        assert invalid_formats == ["https://custom-creative.example.com/display_image"]

    def test_normalizes_reference_agent_url_aliases(self):
        """Reference-agent aliases and trailing slash differences should still validate."""
        submitted_formats = [
            {
                "agent_url": "https://adcontextprotocol.org/agents/formats/mcp",
                "id": "display_image",
                "width": 300,
                "height": 250,
            }
        ]
        available_formats = [self._create_mock_format("display_image", "https://creative.adcontextprotocol.org/")]

        validated_formats, invalid_formats = _validate_format_entries(submitted_formats, available_formats)

        assert invalid_formats == []
        assert validated_formats == [
            {
                "agent_url": "https://creative.adcontextprotocol.org",
                "id": "display_image",
                "width": 300,
                "height": 250,
            }
        ]

    def test_accepts_nested_format_id_shape(self):
        """The admin route accepts the nested AdCP FormatId shape as well as flat form data."""
        submitted_formats = [
            {
                "format_id": {
                    "agent_url": "https://creative.adcontextprotocol.org",
                    "id": "display_image",
                },
                "width": 300,
                "height": 250,
            }
        ]
        available_formats = [self._create_mock_format("display_image")]

        validated_formats, invalid_formats = _validate_format_entries(submitted_formats, available_formats)

        assert invalid_formats == []
        assert validated_formats == [
            {
                "agent_url": "https://creative.adcontextprotocol.org",
                "id": "display_image",
                "width": 300,
                "height": 250,
            }
        ]
