"""Brand string shorthand coercion for get_products and create_media_buy (#1324)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastmcp.server.context import Context

from src.core.exceptions import AdCPValidationError
from src.core.schema_helpers import (
    brand_shorthand_to_domain,
    create_get_products_request,
    is_url_shorthand,
    to_brand_reference,
)
from src.core.tools.media_buy_create import _build_create_media_buy_request


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://acme.com", True),
        ("//cdn.example.com", True),
        ("acme.com", False),
        ("ACME.COM", False),
    ],
)
def test_is_url_shorthand(value: str, expected: bool) -> None:
    assert is_url_shorthand(value) is expected


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
    """Malformed URL shorthands must not raise (graceful degradation for brand_manifest)."""
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


def _minimal_create_media_buy_kwargs() -> dict:
    from tests.helpers.adcp_factories import create_test_media_buy_request_dict

    req_dict = create_test_media_buy_request_dict()
    return {
        "packages": req_dict["packages"],
        "start_time": req_dict["start_time"],
        "end_time": req_dict["end_time"],
        "po_number": req_dict.get("po_number"),
        "reporting_webhook": None,
        "context": None,
        "ext": None,
        "account": None,
        "idempotency_key": req_dict["idempotency_key"],
    }


@pytest.mark.parametrize(
    ("brand_input", "expected_domain"),
    [
        ("acme.com", "acme.com"),
        ("ACME.COM", "acme.com"),
        ("https://test.example", "test.example"),
        ("http://acme.com/path", "acme.com"),
        ({"domain": "acme.com"}, "acme.com"),
        ({"domain": "ACME.COM"}, "acme.com"),
        ({"domain": "https://acme.com"}, "acme.com"),
    ],
)
def test_build_create_media_buy_request_brand_shorthand(brand_input, expected_domain) -> None:
    req = _build_create_media_buy_request(brand=brand_input, **_minimal_create_media_buy_kwargs())
    assert req.brand is not None
    assert req.brand.domain == expected_domain


@pytest.mark.parametrize(
    "invalid_brand",
    ["https://[", "acme.com/products", "my_brand.com", "https://münchen.de"],
)
def test_build_create_media_buy_request_invalid_brand_raises(invalid_brand: str) -> None:
    with pytest.raises(AdCPValidationError) as exc_info:
        _build_create_media_buy_request(brand=invalid_brand, **_minimal_create_media_buy_kwargs())
    assert exc_info.value.field == "brand"


def _capture_req_via_create_media_buy(brand):
    """Run the real MCP create_media_buy wrapper with `brand`; return the req handed to the impl."""
    captured: dict = {}

    async def _impl(req, identity, **kwargs):
        captured["req"] = req
        from src.core.schemas import CreateMediaBuyResult
        from src.core.schemas._base import CreateMediaBuySuccess

        return CreateMediaBuyResult(
            response=CreateMediaBuySuccess(media_buy_id="mb_test", buyer_ref="buyer-1", packages=[]),
            status="completed",
        )

    mock_ctx = MagicMock(spec=Context)
    mock_ctx.get_state = AsyncMock(return_value=None)
    with patch("src.core.tools.media_buy_create._create_media_buy_impl", side_effect=_impl):
        from src.core.tools.media_buy_create import create_media_buy
        from tests.helpers.adcp_factories import create_test_media_buy_request_dict

        req_dict = create_test_media_buy_request_dict(brand={"domain": "placeholder.com"})
        asyncio.run(
            create_media_buy(
                brand=brand,
                packages=req_dict["packages"],
                start_time=req_dict["start_time"],
                end_time=req_dict["end_time"],
                idempotency_key=req_dict["idempotency_key"],
                ctx=mock_ctx,
            )
        )
    return captured["req"]


def test_mcp_create_media_buy_coerces_string_url_brand_before_impl() -> None:
    req = _capture_req_via_create_media_buy("https://test.example")
    assert req.brand is not None
    assert req.brand.domain == "test.example"


def test_mcp_create_media_buy_string_and_dict_brand_identical_downstream() -> None:
    from_string = _capture_req_via_create_media_buy("https://test.example")
    from_dict = _capture_req_via_create_media_buy({"domain": "test.example"})
    assert from_string.brand is not None and from_dict.brand is not None
    assert from_string.brand.domain == from_dict.brand.domain == "test.example"
