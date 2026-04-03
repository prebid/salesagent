"""Integration tests: list_creative_formats input validation (Extension B).

Covers:
- salesagent-3w1w (UC-005-EXT-B-03): non-integer dimension values
- salesagent-s7yx (UC-005-EXT-B-04): invalid WCAG level
- salesagent-w83l (UC-005-EXT-B-05): multi-field validation errors

These tests verify that invalid request parameters produce VALIDATION_ERROR
responses with per-field error messages. Validation happens at the Pydantic
schema layer (ListCreativeFormatsRequest) and the MCP wrapper layer.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.core.schemas import ListCreativeFormatsRequest
from src.core.validation_helpers import format_validation_error
from tests.factories import TenantFactory
from tests.harness import CreativeFormatsEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


# ---------------------------------------------------------------------------
# UC-005-EXT-B-03: Non-integer dimension values (salesagent-3w1w)
# ---------------------------------------------------------------------------


class TestNonIntegerDimensionValues:
    """Non-integer dimension values must produce VALIDATION_ERROR."""

    def test_non_integer_max_width_raises_validation_error(self, integration_db):
        """Covers: UC-005-EXT-B-03 — max_width='not_a_number' raises ValidationError.

        The ListCreativeFormatsRequest schema declares max_width as int | None.
        Passing a non-numeric string triggers Pydantic validation failure.
        """
        with pytest.raises(ValidationError) as exc_info:
            ListCreativeFormatsRequest(max_width="not_a_number")

        errors = exc_info.value.errors()
        field_paths = [".".join(str(loc) for loc in e["loc"]) for e in errors]
        assert any("max_width" in p for p in field_paths), f"Expected 'max_width' in error fields, got: {field_paths}"

    def test_non_integer_min_width_raises_validation_error(self, integration_db):
        """Covers: UC-005-EXT-B-03 — min_width='abc' raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            ListCreativeFormatsRequest(min_width="abc")

        errors = exc_info.value.errors()
        field_paths = [".".join(str(loc) for loc in e["loc"]) for e in errors]
        assert any("min_width" in p for p in field_paths)

    def test_non_integer_max_height_raises_validation_error(self, integration_db):
        """Covers: UC-005-EXT-B-03 — max_height='tall' raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            ListCreativeFormatsRequest(max_height="tall")

        errors = exc_info.value.errors()
        field_paths = [".".join(str(loc) for loc in e["loc"]) for e in errors]
        assert any("max_height" in p for p in field_paths)

    def test_non_integer_min_height_raises_validation_error(self, integration_db):
        """Covers: UC-005-EXT-B-03 — min_height=[] raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            ListCreativeFormatsRequest(min_height=[100])

        errors = exc_info.value.errors()
        field_paths = [".".join(str(loc) for loc in e["loc"]) for e in errors]
        assert any("min_height" in p for p in field_paths)

    def test_dimension_type_mismatch_via_mcp_wrapper(self, integration_db):
        """Covers: UC-005-EXT-B-03 — MCP rejects dimension type mismatch.

        Invalid max_width is rejected regardless of which layer catches it.
        """
        from tests.harness.assertions import assert_rejected
        from tests.harness.transport import Transport

        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats([])
            result = env.call_via(Transport.MCP, max_width="not_a_number")
            assert_rejected(result, field="max_width", reason="valid integer")

    def test_formatted_error_identifies_dimension_field(self, integration_db):
        """Covers: UC-005-EXT-B-03 — error message identifies the dimension field.

        The format_validation_error helper produces a message that names the
        invalid field and explains the type mismatch.
        """
        with pytest.raises(ValidationError) as exc_info:
            ListCreativeFormatsRequest(max_width="not_a_number")

        msg = format_validation_error(exc_info.value, context="list_creative_formats request")
        assert "max_width" in msg
        assert "Invalid list_creative_formats request" in msg


# ---------------------------------------------------------------------------
# UC-005-EXT-B-04: Invalid WCAG level (salesagent-s7yx)
# ---------------------------------------------------------------------------


class TestInvalidWcagLevel:
    """Invalid WCAG level must produce VALIDATION_ERROR listing valid levels."""

    def test_invalid_wcag_level_raises_validation_error(self, integration_db):
        """Covers: UC-005-EXT-B-04 — wcag_level='INVALID' raises ValidationError.

        The ListCreativeFormatsRequest schema declares wcag_level as
        WcagLevel | None (enum: A, AA, AAA). Passing 'INVALID' triggers
        Pydantic validation failure.
        """
        with pytest.raises(ValidationError) as exc_info:
            ListCreativeFormatsRequest(wcag_level="INVALID")

        errors = exc_info.value.errors()
        field_paths = [".".join(str(loc) for loc in e["loc"]) for e in errors]
        assert any("wcag_level" in p for p in field_paths), f"Expected 'wcag_level' in error fields, got: {field_paths}"

    def test_invalid_wcag_level_error_suggests_valid_values(self, integration_db):
        """Covers: UC-005-EXT-B-04 — error message suggests valid WCAG levels.

        The raw Pydantic error message should reference the valid enum values.
        """
        with pytest.raises(ValidationError) as exc_info:
            ListCreativeFormatsRequest(wcag_level="INVALID")

        # Pydantic enum validation errors include the valid values
        error_str = str(exc_info.value)
        # Check that at least one valid level is mentioned in the error
        valid_levels_mentioned = any(level in error_str for level in ("'A'", "'AA'", "'AAA'"))
        assert valid_levels_mentioned, f"Expected valid WCAG levels (A, AA, AAA) in error message, got: {error_str}"

    def test_valid_wcag_levels_accepted(self, integration_db):
        """Covers: UC-005-EXT-B-04 — valid WCAG levels A, AA, AAA are accepted."""
        for level in ("A", "AA", "AAA"):
            req = ListCreativeFormatsRequest(wcag_level=level)
            assert req.wcag_level is not None, f"wcag_level={level} should be accepted"


# ---------------------------------------------------------------------------
# UC-005-EXT-B-05: Multi-field validation errors (salesagent-w83l)
# ---------------------------------------------------------------------------


class TestMultiFieldValidationErrors:
    """Multiple invalid parameters must produce per-field validation messages."""

    def test_multiple_invalid_fields_produce_per_field_errors(self, integration_db):
        """Covers: UC-005-EXT-B-05 — multiple invalid params produce per-field messages.

        POST-F2: Error identifies which parameters are invalid and why.
        POST-F3: Error provides valid values or format guidance.
        """
        with pytest.raises(ValidationError) as exc_info:
            ListCreativeFormatsRequest(
                max_width="not_a_number",
                min_height="also_invalid",
                type="nonexistent_category",
            )

        errors = exc_info.value.errors()
        # At least two distinct fields must be reported
        field_paths = {".".join(str(loc) for loc in e["loc"]) for e in errors}
        invalid_fields_found = {p for p in field_paths if any(f in p for f in ("max_width", "min_height", "type"))}
        assert len(invalid_fields_found) >= 2, (
            f"Expected at least 2 distinct invalid fields, got: {invalid_fields_found}"
        )

    def test_multi_field_errors_formatted_with_per_field_messages(self, integration_db):
        """Covers: UC-005-EXT-B-05 — format_validation_error produces per-field messages.

        POST-F2: Each invalid field gets its own error detail line.
        POST-F3: The formatted message includes AdCP spec reference for guidance.
        """
        with pytest.raises(ValidationError) as exc_info:
            ListCreativeFormatsRequest(
                max_width="not_a_number",
                wcag_level="INVALID",
            )

        msg = format_validation_error(exc_info.value, context="list_creative_formats request")

        # POST-F2: per-field messages identifying which params are invalid
        assert "max_width" in msg, "Error should identify max_width as invalid"
        assert "wcag_level" in msg, "Error should identify wcag_level as invalid"

        # POST-F3: guidance via AdCP spec reference
        assert "adcontextprotocol.org" in msg, "Error should reference AdCP spec"

    def test_dimension_and_enum_errors_reported_together(self, integration_db):
        """Covers: UC-005-EXT-B-05 — dimension + enum errors reported simultaneously.

        Verifies that Pydantic reports all validation failures, not just the first.
        """
        with pytest.raises(ValidationError) as exc_info:
            ListCreativeFormatsRequest(
                min_width="wide",
                max_height="short",
                type="invalid_type",
                wcag_level="BOGUS",
            )

        errors = exc_info.value.errors()
        error_fields = set()
        for e in errors:
            for loc in e["loc"]:
                error_fields.add(str(loc))

        assert "min_width" in error_fields, "min_width should be reported"
        assert "max_height" in error_fields, "max_height should be reported"
        # type and wcag_level may produce multiple sub-errors due to union validation
        assert len(errors) >= 2, f"Expected multiple errors, got {len(errors)}"

    def test_mcp_wrapper_multi_field_error_contains_field_details(self, integration_db):
        """Covers: UC-005-EXT-B-05 — MCP rejects multiple invalid fields.

        Both invalid field names are present in the rejection error.
        """
        from tests.harness.assertions import assert_rejected
        from tests.harness.transport import Transport

        with CreativeFormatsEnv() as env:
            TenantFactory(tenant_id="test_tenant")
            env.set_registry_formats([])
            result = env.call_via(
                Transport.MCP,
                max_width="not_a_number",
                min_height="also_invalid",
            )
            assert_rejected(result, field="max_width", reason="valid integer")
            assert_rejected(result, field="min_height", reason="valid integer")
