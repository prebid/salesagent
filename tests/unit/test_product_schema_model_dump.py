"""Unit tests for Product schema model_dump branches.

Covers three untested branches in src/core/schemas/product.py:
1. publisher_properties validator — raises ValueError when empty
2. formats → format_ids rename in model_dump() — ensures correct wire format
3. Empty pricing_options=[] — response shape contract for anonymous users

These are pure Pydantic schema tests — no database or transport required.

Covers: salesagent-xsn4
"""

from __future__ import annotations

from typing import get_args

import pytest
from pydantic import ValidationError

from src.core.schemas import Product
from tests.helpers import pinned_schema
from tests.helpers.adcp_factories import (
    create_test_cpm_pricing_option,
    create_test_format_id,
    create_test_product,
    create_test_publisher_properties_by_tag,
)


class TestPublisherPropertiesValidator:
    """Product validator rejects empty publisher_properties."""

    def test_empty_list_raises_validation_error(self):
        """publisher_properties=[] raises ValidationError per AdCP spec (line 121).

        The library enforces min_length=1 on publisher_properties, which
        catches the empty list before our after-validator at line 120.
        Either way, empty publisher_properties is rejected.
        """
        with pytest.raises(ValidationError):
            create_test_product(publisher_properties=[])

    def test_none_raises_validation_error(self):
        """publisher_properties=None raises ValidationError per AdCP spec (line 120).

        The library field is non-optional with min_length=1, so None is
        rejected at the field level. Must bypass factory (it sets a default).
        """
        with pytest.raises(ValidationError):
            Product(
                product_id="test",
                name="Test",
                description="Test",
                publisher_properties=None,
                format_ids=[create_test_format_id("display_300x250")],
                delivery_type="guaranteed",
                pricing_options=[create_test_cpm_pricing_option()],
                delivery_measurement={"provider": "test", "notes": "Test"},
            )

    def test_valid_publisher_properties_accepted(self):
        """Non-empty publisher_properties passes validation."""
        product = create_test_product()
        assert len(product.publisher_properties) > 0


class TestFormatIdsRenameInModelDump:
    """model_dump() renames internal 'formats' to 'format_ids' for wire format."""

    def test_output_has_format_ids_not_formats(self):
        """model_dump() outputs 'format_ids', not 'formats' (line 203)."""
        product = create_test_product()
        data = product.model_dump()

        assert "format_ids" in data, "Wire format must use 'format_ids'"
        assert "formats" not in data, "'formats' must be renamed to 'format_ids'"

    def test_format_ids_preserves_values(self):
        """Renamed format_ids contains the correct values."""
        product = create_test_product(format_ids=["display_300x250", "video_1920x1080"])
        data = product.model_dump()

        assert "format_ids" in data
        assert len(data["format_ids"]) == 2


class TestEmptyPricingOptionsInModelDump:
    """model_dump() includes pricing_options=[] for anonymous user path.

    In production, products are constructed with valid pricing_options
    (min_length=1), then pricing_options is set to [] for anonymous users
    (see src/core/tools/products.py:852). model_dump() must preserve the
    empty list in the output to maintain the response shape contract.
    """

    def test_empty_pricing_options_included(self):
        """pricing_options=[] appears in model_dump() output.

        Simulates the anonymous user path: product created with pricing,
        then pricing_options cleared before serialization. The dump drops
        null values only, so a falsy-but-present [] survives.
        """
        product = create_test_product()
        # Simulate anonymous path: clear pricing after construction
        product.pricing_options = []
        data = product.model_dump()

        assert "pricing_options" in data, "Empty pricing_options must be present in output"
        assert data["pricing_options"] == []

    def test_populated_pricing_options_included(self):
        """Non-empty pricing_options also appear in output."""
        product = create_test_product()
        data = product.model_dump()

        assert "pricing_options" in data
        assert len(data["pricing_options"]) > 0


class TestRequiredFieldsCannotBeNull:
    """The invariant model_dump()'s null-stripping pass depends on.

    model_dump() drops every key whose value is None. That is only safe
    because no field the pinned schema requires unconditionally can BE None:
    each is non-nullable on the model (product_id/name/description/
    delivery_type inherit that straight from the adcp library base; the
    container fields likewise), so validation rejects a null long before
    serialization sees one.

    That invariant used to be re-stated by hand as a `core_fields` set that
    force-included four names — provably a no-op, and actively wrong if it had
    ever fired, since forcing a null into the output emits schema-invalid data.
    The set is gone; this test carries the invariant instead. If a future adcp
    bump (or a local override) makes one of these nullable, this fails and
    forces the dump logic to be re-derived, rather than someone re-adding a
    safety net that cannot work.
    """

    def test_required_fields_are_non_nullable(self):
        """Every unconditionally-required field in the pin is non-nullable on Product."""
        required = pinned_schema.load("core/product.json")["required"]
        assert required, "pinned core/product.json declares no required fields — pin is wrong"

        for field_name in required:
            field = Product.model_fields.get(field_name)
            assert field is not None, (
                f"pinned core/product.json requires {field_name!r} but Product does not declare it"
            )
            assert type(None) not in get_args(field.annotation), (
                f"Product.{field_name} is required by the pinned schema but is now Optional "
                f"({field.annotation}). model_dump() drops None values, so it would silently "
                f"omit a required field. Re-derive the dump's inclusion logic."
            )


class TestNoForcedInvalidNull:
    """model_dump() must never emit a null for a field the pinned schema types
    non-nullable (#1868 review).

    Per the pinned core/product.json: format_ids is typed "array", which
    rejects null. An unset format_ids must be OMITTED rather than serialized
    as null. model_dump() now drops every null unconditionally, so this holds
    by construction — the test pins the behavior against a future override
    that force-includes a field again.
    """

    def test_format_ids_omitted_when_unset(self):
        """format_ids=None must not appear as an explicit null.

        Spec: core/product.json properties.format_ids is typed "array",
        which rejects null. Constructed directly (bypassing the factory,
        which always defaults format_ids) to get a Product with format_ids
        genuinely unset.
        """
        product = Product(
            product_id="test",
            name="Test",
            description="Test",
            publisher_properties=[create_test_publisher_properties_by_tag()],
            format_ids=None,
            delivery_type="guaranteed",
            pricing_options=[create_test_cpm_pricing_option()],
        )
        data = product.model_dump()

        assert "format_ids" not in data, (
            "format_ids=None must be omitted, not force-included as null "
            "(the pinned schema types it as a non-nullable array)"
        )


class TestReportingCapabilitiesAlwaysPresent:
    """reporting_capabilities is unconditionally required by the pinned
    core/product.json's top-level required array — unlike format_ids (only
    required via anyOf with format_options), it must never be absent from
    model_dump(). The field carries a validated default_factory, so the
    attribute, the dump and the persisted row always agree; nothing is
    fabricated at serialization time.
    """

    def test_present_and_non_null_when_unset(self):
        """Omitting the kwarg leaves a real default on the attribute AND in the dump."""
        product = create_test_product()

        assert product.reporting_capabilities is not None, (
            "the field's default_factory must populate the attribute, not just the dump"
        )
        assert product.reporting_capabilities.expected_delay_minutes == 1440

        data = product.model_dump()
        assert "reporting_capabilities" in data, (
            "reporting_capabilities is schema-required and must never be omitted, even when unset on the model"
        )
        assert data["reporting_capabilities"]["expected_delay_minutes"] == 1440

    def test_preserves_explicit_value_when_set(self):
        """An explicitly-set reporting_capabilities is not overwritten by the default."""
        rc = {
            "available_reporting_frequencies": ["hourly"],
            "expected_delay_minutes": 30,
            "timezone": "America/New_York",
            "supports_webhooks": True,
            "available_metrics": ["impressions", "clicks"],
            "date_range_support": "date_range",
        }
        product = create_test_product(reporting_capabilities=rc)
        data = product.model_dump()

        assert data["reporting_capabilities"]["expected_delay_minutes"] == 30
        assert data["reporting_capabilities"]["timezone"] == "America/New_York"

    def test_none_is_unconstructible(self):
        """reporting_capabilities=None has no representation — the model rejects it.

        The former behavior (accept None on the model, fabricate a value inside
        model_dump()) let the attribute, the wire and the DB row disagree.
        """
        with pytest.raises(ValidationError):
            create_test_product(reporting_capabilities=None)

    def test_partial_shape_is_unconstructible(self):
        """A dict missing required subfields is rejected at the construction site."""
        with pytest.raises(ValidationError):
            create_test_product(reporting_capabilities={"metrics": ["impressions"]})


class TestOptionalFieldsOmittedWhenUnset:
    """delivery_measurement and is_custom get the same omit-when-unset
    treatment as format_ids above, but unlike format_ids and
    reporting_capabilities they are genuinely optional per the pinned
    core/product.json (not required) — so omission here is already correct
    behavior, not a bug. Pure test-coverage; no production code change.
    """

    def test_delivery_measurement_omitted_when_unset(self):
        """delivery_measurement=None must not appear as an explicit null.

        create_test_product(delivery_measurement=None) still defaults it to a
        populated dict (the factory treats None as "not provided"), so this
        constructs Product directly to get a genuinely unset field, same as
        test_format_ids_omitted_when_unset above.
        """
        product = Product(
            product_id="test",
            name="Test",
            description="Test",
            publisher_properties=[create_test_publisher_properties_by_tag()],
            format_ids=[create_test_format_id("display_300x250")],
            delivery_type="guaranteed",
            pricing_options=[create_test_cpm_pricing_option()],
            delivery_measurement=None,
        )
        data = product.model_dump()

        assert "delivery_measurement" not in data, (
            "delivery_measurement=None must be omitted from model_dump() output when unset"
        )

    def test_is_custom_omitted_when_unset(self):
        """is_custom=None must not appear as an explicit null.

        create_test_product() never sets is_custom, so it is unset by default.
        """
        product = create_test_product()
        data = product.model_dump()

        assert "is_custom" not in data, "is_custom=None must be omitted from model_dump() output when unset"
