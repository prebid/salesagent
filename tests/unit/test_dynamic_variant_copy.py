"""Generated variants retain seller terms without sharing mutable template data."""

from src.services.dynamic_products import create_variant_from_template
from tests.factories import ProductFactory, SignalFactory


def test_variant_carries_catalog_and_forecast_fields():
    template = ProductFactory.build(
        catalog_match={"nested": {"seller_value": "match"}},
        forecast={"nested": {"seller_value": "forecast"}},
        targeting_template={"geo_targets": ["US"]},
    )
    variant = create_variant_from_template(
        template,
        SignalFactory.payload(name="Readers"),
        {"type": "key_value", "key": "audience", "value": "readers"},
        "variant",
        "brief",
    )
    assert variant.catalog_match == template.catalog_match
    assert variant.forecast == template.forecast
    assert variant.stored_publisher_properties == template.stored_publisher_properties
    assert all(
        inherited is not original
        for inherited, original in zip(variant.publisher_selections, template.publisher_selections, strict=True)
    )
    variant.forecast["nested"]["seller_value"] = "changed"
    variant.targeting_template["geo_targets"].append("CA")
    assert template.forecast["nested"]["seller_value"] == "forecast"
    assert template.targeting_template == {"geo_targets": ["US"]}


def test_variant_does_not_snapshot_parent_access():
    template = ProductFactory.build(allowed_principal_ids=["buyer"])
    variant = create_variant_from_template(
        template,
        SignalFactory.payload(name="Readers"),
        {"type": "key_value", "key": "audience", "value": "readers"},
        "variant",
        "brief",
    )
    assert variant.allowed_principal_ids is None


def test_overlong_variant_name_has_a_dedicated_refusal():
    import pytest

    template = ProductFactory.build(name="Template")
    with pytest.raises(ValueError) as caught:
        create_variant_from_template(template, SignalFactory.payload(name="x" * 300), {}, "variant", "brief")
    assert type(caught.value).__name__ == "VariantNameTooLong"
