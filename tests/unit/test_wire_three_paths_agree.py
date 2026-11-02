"""The three serialization paths agree on every wire model (salesagent-3cs7o.14, item 3).

A model is serialized in three ways -- ``model_dump()`` (what ``to_wire`` calls),
``model_dump_json()``, and ``pydantic_core.to_json`` (what the JSON column type and every
nested parent use) -- and only a ``@model_serializer`` runs on all three. A ``def model_dump``
override, a per-class finishing hook or a per-class strip set runs on one path and not the
others, so a shape written there exists on one wire and not the rest. These tests build the
models that used to carry such hooks with None-bearing inputs and assert the three paths
produce one document. Break it on purpose: add a ``def model_dump`` override to ``Product``
and the Product case fails.

``PricingOption`` is a RootModel over the local pricing members and reaches the buyer only
nested inside a ``Product``, whose dump propagates ``exclude_none`` into it. Standalone, the
SDK's RootModel base keeps a None-valued key on a bare ``model_dump()``, so its case asserts
the two paths that carry ``exclude_none`` agree and that the nested wire carries no null.
"""

import json

import pydantic_core

from src.core.schemas import Creative, GetProductsResponse, Product, SyncCreativeResult, SyncCreativesResponse
from src.core.schemas.pricing import PricingOption
from src.core.tools._wire import to_wire
from tests.helpers.adcp_factories import create_test_cpm_pricing_option, create_test_publisher_properties_by_tag

_FORMAT_ID = {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}


def _three_paths(model):
    """The wire body, the JSON-string path and the pydantic-core path, as parsed documents."""
    return (
        to_wire(model),
        json.loads(model.model_dump_json(exclude_none=True)),
        json.loads(pydantic_core.to_json(model, exclude_none=True)),
    )


def _assert_three_paths_agree(model) -> dict:
    wire, dumped_json, core_json = _three_paths(model)
    assert wire == dumped_json == core_json, (wire, dumped_json, core_json)
    return wire


def _product(**overrides) -> Product:
    fields = {
        "product_id": "p1",
        "name": "Product",
        "description": "A product with None-bearing optional fields",
        "format_ids": [_FORMAT_ID],
        "delivery_type": "guaranteed",
        "delivery_measurement": {"provider": "publisher"},
        "publisher_properties": [create_test_publisher_properties_by_tag(publisher_domain="example.com")],
        "pricing_options": [
            create_test_cpm_pricing_option(pricing_option_id="cpm_usd_fixed", currency="USD", rate=10.0),
        ],
        "reporting_capabilities": {
            "available_reporting_frequencies": ["daily"],
            "expected_delay_minutes": 1440,
            "timezone": "UTC",
            "supports_webhooks": False,
            "available_metrics": ["impressions"],
            "date_range_support": "date_range",
        },
        "expires_at": None,
        "product_card": None,
    }
    return Product(**{**fields, **overrides})


def test_product_three_paths_agree_and_omit_none() -> None:
    wire = _assert_three_paths_agree(_product())
    assert "expires_at" not in wire
    assert "product_card" not in wire
    assert "implementation_config" not in wire  # Field(exclude=True)


def test_sync_creatives_response_omits_unset_lists_on_every_path() -> None:
    response = SyncCreativesResponse(creatives=[SyncCreativeResult(creative_id="c1", action="created")])
    wire = _assert_three_paths_agree(response)
    (result,) = wire["creatives"]
    for absent in ("changes", "warnings", "errors"):
        assert absent not in result, result  # absent: not null, not []


def test_creative_with_a_none_inside_assets_three_paths_agree() -> None:
    creative = Creative(
        creative_id="c1",
        name="Creative",
        format_id=_FORMAT_ID,
        assets={
            "image": [
                {"asset_type": "image", "url": "https://x.example/a.png", "width": 300, "height": 250, "alt_text": None}
            ]
        },
    )
    wire = _assert_three_paths_agree(creative)
    (image,) = wire["assets"]["image"]
    assert "alt_text" not in image


def test_pricing_option_paths_agree_and_the_nested_wire_carries_no_null() -> None:
    option = PricingOption.model_validate(
        {
            "pricing_option_id": "po",
            "pricing_model": "cpm",
            "currency": "USD",
            "fixed_price": 5.0,
            "min_spend_per_package": None,
        }
    )
    _, dumped_json, core_json = _three_paths(option)
    assert dumped_json == core_json
    assert "min_spend_per_package" not in dumped_json

    # The path the buyer sees: nested in a Product, inside the get_products envelope.
    response = GetProductsResponse(products=[_product(pricing_options=[option])])
    wire = _assert_three_paths_agree(response)
    (product,) = wire["products"]
    (nested,) = product["pricing_options"]
    assert "min_spend_per_package" not in nested
    assert None not in nested.values()
