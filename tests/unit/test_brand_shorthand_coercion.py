"""Brand string shorthand coercion for get_products (#1324, #1247 gap #2)."""

import pytest

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
