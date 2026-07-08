"""Brand string shorthand coercion for get_products (#1324, #1247 gap #2)."""

import pytest

from src.core.exceptions import AdCPValidationError
from src.core.schema_helpers import brand_shorthand_to_domain, create_get_products_request, to_brand_reference


@pytest.mark.parametrize(
    ("shorthand", "expected_domain"),
    [
        ("https://test.example", "test.example"),
        ("http://test.example/path", "test.example"),
        ("acme.com", "acme.com"),
        ("ACME.COM", "acme.com"),
    ],
)
def test_brand_shorthand_to_domain(shorthand: str, expected_domain: str) -> None:
    assert brand_shorthand_to_domain(shorthand) == expected_domain


def test_to_brand_reference_string_url_shorthand() -> None:
    ref = to_brand_reference("https://test.example")
    assert ref is not None
    assert ref.domain == "test.example"


def test_to_brand_reference_dict_form_unchanged() -> None:
    ref = to_brand_reference({"domain": "test.example"})
    assert ref is not None
    assert ref.domain == "test.example"


def test_string_and_dict_shorthand_produce_identical_brand() -> None:
    from_string = create_get_products_request(brand="https://test.example", brief="test")
    from_dict = create_get_products_request(brand={"domain": "test.example"}, brief="test")
    assert from_string.brand is not None
    assert from_dict.brand is not None
    assert from_string.brand.domain == from_dict.brand.domain == "test.example"


@pytest.mark.parametrize(
    "malformed_url",
    [
        "https://[",
        "http://[::1",
    ],
)
def test_brand_shorthand_to_domain_malformed_url_non_raising(malformed_url: str) -> None:
    """Malformed URL shorthands must not raise (graceful degradation)."""
    assert brand_shorthand_to_domain(malformed_url) == ""


def test_to_brand_reference_malformed_url_raises_validation_error() -> None:
    with pytest.raises(AdCPValidationError, match="Invalid brand") as exc_info:
        to_brand_reference("https://[")
    assert exc_info.value.field == "brand"


@pytest.mark.parametrize(
    "invalid_brand",
    [
        "acme.com/products",
        "my_brand.com",
        "https://münchen.de",
    ],
)
def test_to_brand_reference_invalid_domain_raises_typed_error(invalid_brand: str) -> None:
    with pytest.raises(AdCPValidationError) as exc_info:
        to_brand_reference(invalid_brand)
    assert exc_info.value.field == "brand"


def test_dict_uppercase_domain_normalized_like_string() -> None:
    ref = to_brand_reference({"domain": "ACME.COM"})
    assert ref is not None
    assert ref.domain == "acme.com"


def test_dict_url_domain_normalized_like_string() -> None:
    from_dict = to_brand_reference({"domain": "https://acme.com"})
    from_string = to_brand_reference("https://acme.com")
    assert from_dict is not None and from_string is not None
    assert from_dict.domain == from_string.domain == "acme.com"
