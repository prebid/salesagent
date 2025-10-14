"""Tests for adapter schema validation script."""

import ast
from pathlib import Path

import pytest

from scripts.validate_adapter_usage import (
    AdapterSchema,
    ConstructorCall,
    ValidationError,
    _extract_call_fields,
    _extract_fields,
    _find_closest_match,
    _inherits_from_basemodel,
    _is_optional_annotation,
    extract_adapter_schemas,
    find_response_constructors,
    format_errors,
    validate_constructor_calls,
)


class TestSchemaExtraction:
    """Tests for extracting adapter schemas from AST."""

    def test_extract_required_fields(self):
        """Test extracting required fields from adapter class."""
        code = """
class GetMediaBuyDeliveryResponse(BaseModel):
    media_buy_deliveries: list[Any]
    buyer_ref: str
    optional_field: str | None = None
"""
        tree = ast.parse(code)
        class_node = tree.body[0]
        schema = _extract_fields(class_node)

        assert set(schema.required) == {"media_buy_deliveries", "buyer_ref"}
        assert set(schema.optional) == {"optional_field"}

    def test_extract_optional_fields_with_default(self):
        """Test optional fields with default values."""
        code = """
class MyResponse(BaseModel):
    required: str
    with_default: str = "default"
    with_none: int = None
"""
        tree = ast.parse(code)
        class_node = tree.body[0]
        schema = _extract_fields(class_node)

        assert set(schema.required) == {"required"}
        assert set(schema.optional) == {"with_default", "with_none"}

    def test_skip_model_config(self):
        """Test that model_config is skipped."""
        code = """
class MyResponse(BaseModel):
    model_config = {"arbitrary_types_allowed": True}
    field: str
"""
        tree = ast.parse(code)
        class_node = tree.body[0]
        schema = _extract_fields(class_node)

        assert "model_config" not in schema.required
        assert "model_config" not in schema.optional
        assert "field" in schema.required

    def test_inherits_from_basemodel(self):
        """Test detecting BaseModel inheritance."""
        code = """
class MyResponse(BaseModel):
    pass

class NotAResponse:
    pass
"""
        tree = ast.parse(code)
        my_response = tree.body[0]
        not_a_response = tree.body[1]

        assert _inherits_from_basemodel(my_response) is True
        assert _inherits_from_basemodel(not_a_response) is False

    def test_is_optional_annotation_union(self):
        """Test detecting T | None annotations."""
        code = """
field: str | None
"""
        tree = ast.parse(code)
        ann_assign = tree.body[0]
        annotation = ann_assign.annotation

        assert _is_optional_annotation(annotation) is True

    def test_is_optional_annotation_not_optional(self):
        """Test detecting non-optional annotations."""
        code = """
field: str
"""
        tree = ast.parse(code)
        ann_assign = tree.body[0]
        annotation = ann_assign.annotation

        assert _is_optional_annotation(annotation) is False


class TestConstructorDetection:
    """Tests for finding constructor calls in implementation code."""

    def test_find_constructor_calls(self):
        """Test finding response constructor calls."""
        code = """
def create_media_buy():
    return GetMediaBuyDeliveryResponse(
        media_buy_deliveries=deliveries,
        buyer_ref="ref123"
    )
"""
        # Create a temporary file for testing
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(code)
            temp_path = Path(f.name)

        try:
            calls = find_response_constructors(temp_path, {"GetMediaBuyDeliveryResponse"})

            assert len(calls) == 1
            assert calls[0].class_name == "GetMediaBuyDeliveryResponse"
            assert set(calls[0].fields) == {"media_buy_deliveries", "buyer_ref"}
            assert calls[0].location[0] == temp_path
            assert calls[0].location[1] > 0  # Valid line number
        finally:
            temp_path.unlink()

    def test_extract_call_fields(self):
        """Test extracting field names from constructor call."""
        code = """
MyResponse(field1="value1", field2="value2")
"""
        tree = ast.parse(code)
        call_node = tree.body[0].value

        fields = _extract_call_fields(call_node)

        assert fields == ["field1", "field2"]

    def test_extract_call_fields_ignores_kwargs(self):
        """Test that **kwargs are ignored."""
        code = """
MyResponse(field1="value1", **extra_fields)
"""
        tree = ast.parse(code)
        call_node = tree.body[0].value

        fields = _extract_call_fields(call_node)

        # Should only extract explicit field names, not **kwargs
        assert fields == ["field1"]


class TestValidation:
    """Tests for validation logic."""

    def test_validate_unknown_field(self):
        """Test detecting unknown field in constructor."""
        schemas = {"MyResponse": AdapterSchema(required=["correct_field"], optional=[])}
        calls = [
            ConstructorCall(
                class_name="MyResponse",
                fields=["wrong_field"],
                location=(Path("test.py"), 10, 5),
            )
        ]

        errors = validate_constructor_calls(schemas, calls)

        # Should have 2 errors: unknown field + missing required field
        assert len(errors) == 2
        unknown_errors = [e for e in errors if e.error_type == "unknown_field"]
        missing_errors = [e for e in errors if e.error_type == "missing_field"]

        assert len(unknown_errors) == 1
        assert unknown_errors[0].field_name == "wrong_field"

        assert len(missing_errors) == 1
        assert missing_errors[0].field_name == "correct_field"

    def test_validate_missing_required_field(self):
        """Test detecting missing required field."""
        schemas = {"MyResponse": AdapterSchema(required=["required_field"], optional=[])}
        calls = [
            ConstructorCall(
                class_name="MyResponse",
                fields=[],  # Missing required field
                location=(Path("test.py"), 10, 5),
            )
        ]

        errors = validate_constructor_calls(schemas, calls)

        assert len(errors) == 1
        assert errors[0].error_type == "missing_field"
        assert errors[0].field_name == "required_field"

    def test_validate_optional_fields_allowed(self):
        """Test that optional fields can be omitted."""
        schemas = {"MyResponse": AdapterSchema(required=["required"], optional=["optional"])}
        calls = [
            ConstructorCall(
                class_name="MyResponse",
                fields=["required"],  # Omitting optional field
                location=(Path("test.py"), 10, 5),
            )
        ]

        errors = validate_constructor_calls(schemas, calls)

        assert len(errors) == 0  # No errors - optional fields can be omitted

    def test_validate_all_fields_correct(self):
        """Test validation passes with all correct fields."""
        schemas = {"MyResponse": AdapterSchema(required=["field1"], optional=["field2", "field3"])}
        calls = [
            ConstructorCall(
                class_name="MyResponse",
                fields=["field1", "field2"],  # All correct
                location=(Path("test.py"), 10, 5),
            )
        ]

        errors = validate_constructor_calls(schemas, calls)

        assert len(errors) == 0

    def test_typo_suggestion(self):
        """Test suggesting corrections for typos."""
        field = "deliveries"
        valid_fields = {"media_buy_deliveries", "buyer_ref"}

        suggestion = _find_closest_match(field, valid_fields)

        assert suggestion == "media_buy_deliveries"

    def test_typo_suggestion_no_close_match(self):
        """Test no suggestion for completely different field name."""
        field = "xyz"
        valid_fields = {"media_buy_deliveries", "buyer_ref"}

        suggestion = _find_closest_match(field, valid_fields)

        assert suggestion is None


class TestErrorFormatting:
    """Tests for error reporting."""

    def test_format_no_errors(self):
        """Test formatting when no errors found."""
        errors = []

        output = format_errors(errors)

        assert "✅" in output
        assert "valid" in output.lower()

    def test_format_unknown_field_error(self):
        """Test formatting unknown field error."""
        errors = [
            ValidationError(
                class_name="MyResponse",
                location=(Path("src/core/main.py"), 100, 5),
                error_type="unknown_field",
                field_name="wrong_field",
                suggestion="correct_field",
            )
        ]

        output = format_errors(errors)

        assert "❌" in output
        assert "src/core/main.py:100:5" in output
        assert "MyResponse" in output
        assert "Unknown field: 'wrong_field'" in output
        assert "Did you mean: 'correct_field'?" in output

    def test_format_missing_field_error(self):
        """Test formatting missing field error."""
        errors = [
            ValidationError(
                class_name="MyResponse",
                location=(Path("src/core/main.py"), 200, 10),
                error_type="missing_field",
                field_name="required_field",
                suggestion=None,
            )
        ]

        output = format_errors(errors)

        assert "❌" in output
        assert "src/core/main.py:200:10" in output
        assert "Missing required field: 'required_field'" in output

    def test_format_multiple_errors_sorted(self):
        """Test that multiple errors are sorted by location."""
        errors = [
            ValidationError(
                class_name="Response2",
                location=(Path("main.py"), 200, 5),
                error_type="unknown_field",
                field_name="field2",
                suggestion=None,
            ),
            ValidationError(
                class_name="Response1",
                location=(Path("main.py"), 100, 5),
                error_type="unknown_field",
                field_name="field1",
                suggestion=None,
            ),
        ]

        output = format_errors(errors)

        # Check that errors appear in sorted order (line 100 before line 200)
        line100_pos = output.find("main.py:100:5")
        line200_pos = output.find("main.py:200:5")

        assert line100_pos < line200_pos


class TestRealFilesIntegration:
    """Integration tests with actual project files."""

    def test_extract_real_adapter_schemas(self):
        """Test parsing actual schema_adapters.py."""
        adapter_file = Path("src/core/schema_adapters.py")

        if not adapter_file.exists():
            pytest.skip("schema_adapters.py not found in expected location")

        schemas = extract_adapter_schemas(adapter_file)

        # Verify known schemas exist
        assert "GetMediaBuyDeliveryResponse" in schemas
        assert "CreateMediaBuyResponse" in schemas

        # Verify field extraction works
        delivery_schema = schemas["GetMediaBuyDeliveryResponse"]
        assert "media_buy_deliveries" in delivery_schema.required

    def test_find_real_constructor_calls(self):
        """Test parsing actual main.py."""
        impl_file = Path("src/core/main.py")

        if not impl_file.exists():
            pytest.skip("main.py not found in expected location")

        adapter_classes = {"GetMediaBuyDeliveryResponse", "CreateMediaBuyResponse"}
        calls = find_response_constructors(impl_file, adapter_classes)

        # Should find actual constructor calls
        assert len(calls) > 0

        # Verify structure
        for call in calls:
            assert call.class_name in adapter_classes
            # Note: Some calls might have 0 fields if using **kwargs pattern
            # That's okay - validation script will flag missing required fields
            assert call.location[1] > 0  # Valid line number
