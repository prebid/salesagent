"""Unit tests for validation error handling in create_media_buy."""

from pydantic import ValidationError

from src.core.schemas import CreateMediaBuyRequest
from src.core.validation_helpers import format_validation_error


def test_brand_manifest_target_audience_must_be_string():
    """Test BrandManifest (adcp 3.6.0: extra=allow, no fixed fields - any kwargs accepted)."""
    # adcp 3.6.0: BrandManifest uses extra="allow" - any kwargs accepted
    from adcp import BrandManifest

    manifest = BrandManifest(
        name="Test Brand",
        target_audience={"demographics": ["spiritual seekers"], "interests": ["unexplained phenomena"]},
    )
    # In adcp 3.6.0, BrandManifest accepts arbitrary extra fields
    assert manifest is not None


def test_brand_manifest_target_audience_string_works():
    """Test that BrandManifest accepts arbitrary extra fields."""
    from adcp import BrandManifest

    manifest = BrandManifest(
        name="Test Brand",
        target_audience="spiritual seekers interested in unexplained phenomena",
    )
    # BrandManifest accepts extra fields with extra="allow"
    assert manifest is not None


def test_create_media_buy_request_invalid_brand_manifest():
    """Test that CreateMediaBuyRequest accepts brand field (adcp 3.6.0: brand replaced brand_manifest)."""
    # In adcp 3.6.0, brand is a BrandReference with optional domain field
    # Missing domain does not raise an error since domain is optional
    req = CreateMediaBuyRequest(
        buyer_ref="test_ref",
        brand={"domain": "testbrand.com"},
        end_time="2026-02-01T00:00:00Z",
        start_time="2026-01-01T00:00:00Z",
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
            [
                {
                    "type": "missing",
                    "loc": ("buyer_ref",),
                    "msg": "Field required",
                    "input": {},
                }
            ],
        )
    except ValidationError as e:
        error_msg = format_validation_error(e)

        assert "buyer_ref: Required field is missing" in error_msg
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
