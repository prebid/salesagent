"""Unit tests for validation error handling in create_media_buy."""

import pytest
from pydantic import BaseModel, ValidationError

from src.core.exceptions import AdCPValidationError
from src.core.schemas import CreateMediaBuyRequest
from src.core.validation_helpers import first_validation_error_field, format_validation_error


def test_first_validation_error_field_uses_bracket_notation():
    """first_validation_error_field renders list indices as [i] (bracket form).

    The boundary-derived field path must match the hand-rolled field= strings
    raised inside the _impl layer (e.g. packages[].budget), so the wire
    envelope's `field` attribute has one consistent shape regardless of where
    the validation error originated.
    """

    class _Pkg(BaseModel):
        budget: float

    class _Req(BaseModel):
        packages: list[_Pkg]

    with pytest.raises(ValidationError) as exc_info:
        _Req(packages=[{"budget": "not-a-number"}])

    assert first_validation_error_field(exc_info.value) == "packages[0].budget"


def test_first_validation_error_field_is_owned_by_exception_leaf_module():
    """The field-path helper must not recreate an exceptions/helpers import cycle."""
    assert first_validation_error_field.__module__ == "src.core.exceptions"


def test_create_media_buy_boundary_validation_preserves_field_suggestion():
    """Boundary request construction keeps the current field-specific hint."""
    from src.core.tools.media_buy_create import _build_create_media_buy_request

    with pytest.raises(AdCPValidationError) as exc_info:
        _build_create_media_buy_request(
            brand={"domain": "wiretest.example"},
            packages=None,
            start_time=None,
            end_time=None,
            po_number=None,
            reporting_webhook=None,
            context=None,
            ext=None,
            account=None,
            idempotency_key=None,
            paused=None,
        )

    error = exc_info.value
    assert error.field == "idempotency_key"
    assert error.suggestion == (
        "Provide a client-generated idempotency_key (16-255 characters, using only [A-Za-z0-9_.:-])."
    )


def test_brand_target_audience_must_be_string():
    """Test Brand target_audience field accepts strings (adcp 3.12: Brand replaced BrandManifest)."""
    from adcp.types.generated_poc.brand import Brand, LocalizedName  # TODO: no stable alias in adcp.types

    brand = Brand(
        id="test_brand",
        names=[LocalizedName(name="Test Brand", language="en")],
        target_audience="spiritual seekers interested in unexplained phenomena",
    )
    assert brand.target_audience == "spiritual seekers interested in unexplained phenomena"


def test_brand_accepts_extra_fields():
    """Test that Brand accepts arbitrary extra fields (extra=allow)."""
    from adcp.types.generated_poc.brand import Brand, LocalizedName  # TODO: no stable alias in adcp.types

    brand = Brand(
        id="test_brand",
        names=[LocalizedName(name="Test Brand", language="en")],
        custom_field="custom_value",
    )
    # Brand accepts extra fields with extra="allow"
    assert brand is not None


def test_create_media_buy_request_invalid_brand_manifest():
    """Test that CreateMediaBuyRequest accepts brand field (adcp 3.6.0: brand replaced brand_manifest)."""
    # In adcp 3.6.0, brand is a BrandReference with optional domain field
    # Missing domain does not raise an error since domain is optional
    req = CreateMediaBuyRequest(
        brand={"domain": "testbrand.com"},
        end_time="2026-02-01T00:00:00Z",
        start_time="2026-01-01T00:00:00Z",
        idempotency_key="unit-test-key-invalid-brand-mfst",
    )
    assert req.brand is not None


def test_validation_error_formatting():
    """Test that our validation error formatting provides helpful messages."""
    # Test the format_validation_error helper function
    try:
        raise ValidationError.from_exception_data(
            "CreateMediaBuyRequest",
            [
                {
                    "type": "string_type",
                    "loc": ("brand_manifest", "BrandManifest", "target_audience"),
                    "msg": "Input should be a valid string",
                    "input": {"demographics": ["test"], "interests": ["test"]},
                }
            ],
        )
    except ValidationError as e:
        # Use the shared helper function
        error_msg = format_validation_error(e, context="test request")

        # Check that we got a helpful error message
        assert "Invalid test request:" in error_msg
        assert "brand_manifest.BrandManifest.target_audience" in error_msg
        assert "Expected string, got object" in error_msg
        assert "AdCP spec requires this field to be a simple string" in error_msg
        assert "https://adcontextprotocol.org/schemas/v1/" in error_msg


def test_validation_error_formatting_missing_field():
    """Test formatting for missing required fields."""
    try:
        raise ValidationError.from_exception_data(
            "CreateMediaBuyRequest",
            [{"type": "missing", "loc": ("brand",), "msg": "Field required", "input": {}}],
        )
    except ValidationError as e:
        error_msg = format_validation_error(e)

        assert "brand: Required field is missing" in error_msg
        assert "Invalid request:" in error_msg


def test_validation_error_formatting_extra_field():
    """Test formatting for extra forbidden fields shows the actual value."""
    try:
        raise ValidationError.from_exception_data(
            "CreateMediaBuyRequest",
            [
                {
                    "type": "extra_forbidden",
                    "loc": ("unknown_field",),
                    "msg": "Extra inputs are not permitted",
                    "input": "some_value",
                }
            ],
        )
    except ValidationError as e:
        error_msg = format_validation_error(e)

        assert "unknown_field: Extra field not allowed by AdCP spec" in error_msg
        # Now we show the actual value for debugging
        assert "some_value" in error_msg
        assert "Received value:" in error_msg


def test_validation_error_formatting_extra_field_with_dict():
    """Test formatting for extra forbidden fields with dict values shows full structure."""
    # This tests the scenario from the bug where format_ids had an agent_url key
    # that was incorrectly placed, and Pydantic truncated it
    try:
        raise ValidationError.from_exception_data(
            "Package",
            [
                {
                    "type": "extra_forbidden",
                    "loc": ("format_ids", "agent_url"),
                    "msg": "Extra inputs are not permitted",
                    "input": {"agent_url": "https://creative.adcontextprotocol.org/", "id": "display_300x250"},
                }
            ],
        )
    except ValidationError as e:
        error_msg = format_validation_error(e)

        # Error message should show the full value, not truncated
        assert "format_ids.agent_url: Extra field not allowed by AdCP spec" in error_msg
        assert "Received value:" in error_msg
        # The full URL should be visible, not truncated like "ht...id"
        assert "https://creative.adcontextprotocol.org/" in error_msg
        assert "display_300x250" in error_msg
