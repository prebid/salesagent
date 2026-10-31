#!/usr/bin/env python3
"""
Schema-Database Field Mapping Validation Tests

These tests validate that all Pydantic schema fields have corresponding database fields
and catch invalid field access patterns that could cause AttributeError at runtime.

This directly addresses the issue #161 root cause: missing validation that schema
fields map to valid database fields, allowing 'Product' object has no attribute 'pricing'
errors to reach production.
"""

import pytest
from adcp.types import LegacyProduct as LibraryProduct
from sqlalchemy import delete

from src.core.database.database_session import get_db_session
from src.core.database.models import Creative, MediaBuy, Principal, Tenant
from src.core.database.models import PricingOption as DBPricingOption
from src.core.database.models import Product as ProductModel
from src.core.product_conversion import convert_product_model_to_schema, default_reporting_capabilities
from src.core.schemas import Principal as PrincipalSchema
from src.core.schemas import Product
from tests.factories.product import PricingOptionFactory, ProductFactory
from tests.helpers.adcp_factories import create_test_db_product
from tests.utils.database_helpers import (
    create_tenant_with_timestamps,
)


@pytest.mark.requires_db
class TestSchemaFieldMapping:
    """Test that schema fields map correctly to database fields."""

    def test_every_attribute_the_converter_reads_resolves_on_the_orm_model(self, factory_session):
        """Converting a real product row exercises every ORM attribute the converter reads.

        RENAMED from ``test_product_schema_database_field_alignment``, which is what it
        no longer does. It grades converter-to-ORM resolution, not schema-to-database
        alignment, and nothing covers the latter — by design, see WHAT IT USED TO GRADE
        below. A name promising the old coverage would leave the next reader believing
        an obligation is enforced that is deliberately not.

        WHAT THIS GRADES, and why it RUNS the converter instead of comparing name sets.
        The fault this module exists for is named in its own header: ``'Product' object
        has no attribute 'pricing'`` reaching production (#161). That fault needs a
        READER — a line naming an attribute the ORM model does not declare. There is
        exactly one such reader, :func:`~src.core.product_conversion.convert_product_model_to_schema`,
        the single ORM-to-wire converter. So the obligation is graded by driving it over
        a real row: an attribute it names and the model lacks raises ``AttributeError``
        right here.

        One conversion covers the WHOLE read set, not just the fields this fixture
        populates, because every optional read in the converter is guarded by
        ``if product_model.X:`` — and the guard itself performs the read. A falsy value
        skips the assignment, never the attribute access. Measured with a recording proxy
        over this fixture: 26 of the converter's 26 distinct ``product_model`` reads
        execute. It was 26 of 27 until the dead ``elif hasattr(...)`` fallback at
        ``product_conversion.py``:530-535 was deleted; that branch held the only read no
        fixture could reach, so removing it closed the gap rather than hiding it.

        WHAT IT USED TO GRADE, and why that was wrong. The expected set was
        ``Product.model_fields`` — the WHOLE pinned schema surface — minus a
        hand-maintained 36-name exemption list, which asserted that this seller stores
        every field in the pin. That is the inverse of critical pattern #7: the DTO is
        the ACCEPTED shape, and ``Product`` inherits the library parent's fields by
        construction (pattern #1), so ``model_fields`` is the pin's ceiling and never a
        floor for the ``products`` table. ``_schemas/3.2/core/product.json`` requires
        seven properties — ``product_id``, ``name``, ``description``, ``delivery_type``,
        ``pricing_options``, ``publisher_properties``, ``reporting_capabilities`` — and
        obliges a column for none of the rest.

        The exemption list was the evidence against the rule it served: 26 of its 36
        entries were already annotated "inherited from library Product, not yet stored
        in database", appended once per SDK bump (3.9, 3.10, 3.12, 3.1.1, 5.7, 6.6).
        The adcp 9.0.0b1 bump (spec 3.2.1) added eleven more — ``identity``,
        ``targeting_resolution``, ``overlay_support``, ``media_buy_support``,
        ``demographic_targeting``, ``audience_activation``, ``audience_evidence``,
        ``audience_evidence_selections``, ``execution_requirements``,
        ``list_applications``, ``acceptance_policy_profile_ids`` — every one optional in
        the pin, read or written NOWHERE under ``src/``, and graded by no storyboard in
        ``_compliance/3.2.1/``. Four of them (``targeting_resolution``,
        ``overlay_support``, ``list_applications``, ``audience_evidence_selections``) are
        resolved per REQUEST by the pin's own wording, so a column would be a copy that
        drifts; three are ``x-status: experimental`` and carry a MUST to declare a
        matching ``experimental_features`` id this seller does not declare, so emitting
        them would be the non-conformant move. A pinned field this seller has not
        implemented is legitimately absent. A converter line with no backing attribute
        never is.
        """
        product = ProductFactory()
        PricingOptionFactory(product=product)
        factory_session.commit()
        factory_session.refresh(product)

        # The primary assertion. An unbacked attribute read raises AttributeError out of
        # this call; a read that resolves but carries the wrong shape fails Product's own
        # validation. Both are the defect this module was opened for.
        converted = convert_product_model_to_schema(product, adapter_type="mock")

        # A guard that grades less and stays green is the failure mode to design against,
        # and "it did not raise" is exactly that: a converter gutted to return only the
        # pin's seven required fields reads almost nothing and still passes the call
        # above. So assert the TRANSFER, not just the absence of an exception. Measured
        # on this fixture, the converter supplies 14 fields; the floor sits just under
        # that so a legitimately-absent optional does not trip it, while dropping to the
        # required-only set does.
        supplied = converted.model_fields_set
        assert len(supplied) >= 12, (
            f"the converter supplied only {len(supplied)} fields ({sorted(supplied)}); it supplies 14 "
            f"on this fixture. A sharp drop means it stopped reading the row, which makes the "
            f"AttributeError check above vacuous — it would pass on a converter that reads nothing."
        )

        # A count alone is satisfiable by garbage, so pin values that can only have come
        # off this row. Compared by IDENTIFYING VALUE rather than by object: the row holds
        # format_ids as raw dicts and Product coerces them to FormatReference models, so
        # asserting the two containers equal would be grading pydantic's coercion rather
        # than the converter's read.
        assert converted.product_id == product.product_id, (
            f"the converter returned product_id {converted.product_id!r} for the row "
            f"{product.product_id!r}; it read the wrong attribute"
        )
        assert converted.delivery_type == product.delivery_type, (
            f"delivery_type {converted.delivery_type!r} does not match the row's {product.delivery_type!r}"
        )
        assert [reference.id for reference in converted.format_ids] == [
            entry["id"] for entry in list(product.format_contract.format_ids)
        ], (
            f"format_ids {converted.format_ids!r} does not carry the row's effective format ids "
            f"{list(product.format_contract.format_ids)!r}"
        )

        db_columns = {column.name for column in ProductModel.__table__.columns}

        # The floor, hand-named on purpose: no derivation from one side can assert that a
        # field exists on BOTH. Four are required by _schemas/3.2/core/product.json;
        # `format_ids` is this seller's own hard requirement — the converter raises
        # ValueError on a product with no effective format (product_conversion.py:407).
        schema_fields = set(Product.model_fields.keys())
        critical_fields = {"product_id", "name", "description", "format_ids", "delivery_type"}
        for field in critical_fields:
            assert field in schema_fields, f"Critical field '{field}' missing from Product schema"
            assert field in db_columns, f"Critical field '{field}' missing from ProductModel database"

    def test_every_field_this_repo_adds_to_product_is_backed_by_storage(self):
        """A field OUR subclass adds -- not inherited from the pin -- must be stored.

        The companion to the converter test above, and the other direction. That one
        grades the READER: an attribute the converter names and the ORM model lacks.
        This grades the WRITER: a field this seller declares for itself and then has
        nowhere to put. Nothing upstream will ever supply such a field, so a conversion
        that reads one off a database row raises AttributeError -- the #161 fault
        arriving from the opposite side.

        Scoped to what this seller declares for ITSELF, which is the distinction
        critical pattern #7 draws. ``Product`` inherits the pinned parent's fields by
        construction (pattern #1), so ``model_fields`` is the pin's CEILING and never a
        floor for the ``products`` table -- asserting a column for every pinned field is
        the inverted obligation the 41-name allowlist used to encode. Subtracting the
        parent's fields leaves only what we added, and that set we do owe storage for.

        Derived from the models rather than listed, so an SDK bump that adds a spec field
        changes nothing here. Measured at adcp 9.0.0b1: the set is ``allowed_principal_ids``,
        ``countries``, ``implementation_config`` -- all columns -- and the 38 unmapped
        inherited fields are correctly out of scope.
        """
        schema_fields = set(Product.model_fields.keys())
        library_fields = set(LibraryProduct.model_fields.keys())

        # Storage is columns UNION relationship-backed attributes. ``pricing_options``
        # lives in its own table, so a columns-only check would call it missing.
        storage = {column.name for column in ProductModel.__table__.columns} | set(
            ProductModel.__mapper__.relationships.keys()
        )

        locally_added = schema_fields - library_fields

        # A derivation that matches nothing is green, so prove the populations are real
        # before trusting the subtraction below.
        assert locally_added, (
            "derivation is vacuous: Product declares no field the library parent lacks, "
            "so this test would pass no matter what storage looked like. Either the "
            "LibraryProduct import no longer names the pinned parent, or the local "
            "subclass stopped adding fields -- fix the derivation, do not delete it."
        )
        assert storage, "derivation is vacuous: ProductModel exposes no columns or relationships"

        unbacked = locally_added - storage

        assert not unbacked, (
            f"Fields added by this repo with no database column and no relationship: {unbacked}. "
            f"These are not inherited from the pinned AdCP Product, so nothing upstream will "
            f"ever supply them -- a conversion that reads one off a database row raises "
            f"AttributeError. Add the column (with a migration) or drop the field."
        )

    def test_database_field_access_validation(self, integration_db):
        """Test that all database fields can be accessed without AttributeError."""
        # Create a test tenant and product
        tenant_id = "test_field_access"
        with get_db_session() as session:
            # Clean up any existing test data
            session.execute(delete(DBPricingOption).where(DBPricingOption.tenant_id == tenant_id))
            session.execute(delete(ProductModel).where(ProductModel.tenant_id == tenant_id))
            session.execute(delete(Tenant).where(Tenant.tenant_id == tenant_id))

            # Create test tenant
            tenant = create_tenant_with_timestamps(
                tenant_id=tenant_id, name="Field Access Test", subdomain="field-test"
            )
            session.add(tenant)

            # Create minimal product
            product = create_test_db_product(
                tenant_id=tenant_id,
                product_id="field_test_001",
                name="Field Test Product",
                description="Test product for field access validation",
                format_ids=[{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}],
                delivery_type="non_guaranteed",
            )
            session.add(product)
            session.commit()
            session.refresh(product)

            # Test that all database columns can be accessed
            for column in ProductModel.__table__.columns:
                field_name = column.name
                try:
                    value = getattr(product, field_name)
                    # Access successful - this is good
                except AttributeError as e:
                    pytest.fail(f"Cannot access database field '{field_name}': {e}")

            # Test that non-existent fields raise AttributeError
            non_existent_fields = ["pricing", "cost", "revenue", "profit_margin"]
            for field_name in non_existent_fields:
                with pytest.raises(AttributeError, match=f".*{field_name}.*"):
                    getattr(product, field_name)

            # Cleanup
            session.delete(product)
            session.delete(tenant)
            session.commit()

    def test_principal_schema_database_alignment(self):
        """Test Principal schema aligns with database model."""
        schema_fields = set(PrincipalSchema.model_fields.keys())
        db_columns = {column.name for column in Principal.__table__.columns}

        # Fields that should be in schema but computed/derived
        computed_fields = {"adapter_mappings"}  # Derived from platform_mappings

        # Internal database fields not in external schema
        internal_fields = {"tenant_id", "access_token"}

        # Check alignment
        required_schema_fields = schema_fields - computed_fields
        missing_db_fields = required_schema_fields - db_columns - internal_fields

        assert not missing_db_fields, f"Principal schema fields missing from database: {missing_db_fields}"

    def test_schema_to_database_conversion_safety(self, integration_db):
        """Test that schema-to-database conversion only uses existing fields."""
        # This simulates the conversion logic in DatabaseProductCatalog
        tenant_id = "test_conversion_safety"

        with get_db_session() as session:
            # Create test data
            session.execute(delete(DBPricingOption).where(DBPricingOption.tenant_id == tenant_id))
            session.execute(delete(ProductModel).where(ProductModel.tenant_id == tenant_id))
            session.execute(delete(Tenant).where(Tenant.tenant_id == tenant_id))

            tenant = create_tenant_with_timestamps(
                tenant_id=tenant_id, name="Conversion Safety Test", subdomain="conversion-test"
            )
            session.add(tenant)

            product = create_test_db_product(
                tenant_id=tenant_id,
                product_id="conversion_test_001",
                name="Conversion Test Product",
                description="Product for testing safe conversion",
                format_ids=[{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}],
                delivery_type="non_guaranteed",
            )
            session.add(product)
            session.commit()
            session.refresh(product)

            # Test safe field access pattern (what the code should do)
            safe_fields = [
                "product_id",
                "name",
                "description",
                "format_ids",
                "delivery_type",
                "property_tags",
            ]

            conversion_data = {}
            for field in safe_fields:
                if hasattr(product, field):
                    conversion_data[field] = getattr(product, field)
                else:
                    pytest.fail(f"Safe field '{field}' not found in database model")

            # Verify we got the expected data
            assert conversion_data["product_id"] == "conversion_test_001"
            assert conversion_data["name"] == "Conversion Test Product"
            assert conversion_data["property_tags"] == ["all_inventory"]

            # Test that unsafe field access (what caused the bug) fails predictably
            unsafe_fields = ["pricing", "cost_basis", "margin"]
            for field in unsafe_fields:
                assert not hasattr(product, field), f"Database model should not have unsafe field '{field}'"

            # Cleanup
            session.delete(product)
            session.delete(tenant)
            session.commit()

    def test_all_database_models_have_required_fields(self):
        """Test that all database models have their required fields."""
        models_to_test = [
            (ProductModel, ["product_id", "name", "tenant_id"]),
            (Tenant, ["tenant_id", "name"]),
            (Principal, ["tenant_id", "principal_id", "name"]),
            (MediaBuy, ["tenant_id", "media_buy_id"]),
            (Creative, ["tenant_id", "creative_id"]),
        ]

        for model_class, required_fields in models_to_test:
            db_columns = {column.name for column in model_class.__table__.columns}

            for field in required_fields:
                assert field in db_columns, (
                    f"Required field '{field}' missing from {model_class.__name__} database model"
                )

    def test_pydantic_model_field_access_patterns(self):
        """Test patterns for safely accessing Pydantic model fields."""
        # Create a Product schema instance
        product_data = {
            "product_id": "test_pydantic_001",
            "name": "Pydantic Test Product",
            "description": "Testing Pydantic field access",
            "format_ids": [{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}],
            "delivery_type": "non_guaranteed",
            "publisher_properties": [
                {
                    "selection_type": "by_id",
                    "publisher_domain": "example.com",
                    "property_ids": ["all_inventory"],
                }
            ],
            "pricing_options": [
                {
                    "pricing_option_id": "cpm_usd_fixed",
                    "pricing_model": "cpm",
                    # V3 shape: fixed pricing carries fixed_price. Was
                    # "rate" + "is_fixed" citing adcp 2.4.0+ -- both replaced when
                    # V3 made presence the discriminator, and both accepted only
                    # because the SDK DTO allowed extras.
                    "fixed_price": 10.0,
                    "currency": "USD",
                }
            ],
            "delivery_measurement": {"provider": "Test Provider", "notes": "Test measurement methodology"},
            "reporting_capabilities": default_reporting_capabilities(),
        }

        product = Product(**product_data)

        # Test safe field access methods
        # Method 1: Direct attribute access (safe for known fields)
        assert product.product_id == "test_pydantic_001"

        # Method 2: getattr with default (safe for optional fields)
        cpm_value = getattr(product, "cpm", None)
        assert cpm_value is None  # Not set in product_data

        # Method 3: model_dump() to get all fields as dict
        product_dict = product.model_dump()
        assert "product_id" in product_dict
        assert product_dict["product_id"] == "test_pydantic_001"

        # Method 4: Check if field exists before access
        if hasattr(product, "brief_relevance"):
            brief_relevance = product.brief_relevance
        else:
            brief_relevance = None
        assert brief_relevance is None  # Not set in product_data

        # Test that accessing non-existent fields fails
        with pytest.raises(AttributeError):
            _ = product.non_existent_field

    def test_database_json_field_handling(self, integration_db):
        """Test that JSON fields in database are handled correctly in schema conversion."""
        tenant_id = "test_json_handling"

        with get_db_session() as session:
            # Cleanup
            session.execute(delete(DBPricingOption).where(DBPricingOption.tenant_id == tenant_id))
            session.execute(delete(ProductModel).where(ProductModel.tenant_id == tenant_id))
            session.execute(delete(Tenant).where(Tenant.tenant_id == tenant_id))

            tenant = create_tenant_with_timestamps(
                tenant_id=tenant_id, name="JSON Handling Test", subdomain="json-test"
            )
            session.add(tenant)

            # Test with various JSON field formats
            product = create_test_db_product(
                tenant_id=tenant_id,
                product_id="json_test_001",
                name="JSON Test Product",
                description="Testing JSON field handling",
                format_ids=[
                    {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"},
                    {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_728x90"},
                ],
                delivery_type="non_guaranteed",
            )
            # Manually set additional JSON fields that aren't part of the factory defaults
            product.targeting_template = {"geo": ["US"], "device": ["mobile"]}
            product.measurement = {"viewability": True, "brand_safety": True}
            product.countries = ["US", "CA", "UK"]
            session.add(product)
            session.commit()
            session.refresh(product)

            # Test that JSON fields are accessible and have correct types
            assert hasattr(product, "format_ids")
            assert hasattr(product, "targeting_template")
            assert hasattr(product, "measurement")
            assert hasattr(product, "countries")

            # Access the fields to ensure no AttributeError
            formats = product.format_ids
            targeting = product.targeting_template
            measurement = product.measurement
            countries = product.countries

            # Verify types are as expected (may be dicts/lists from JSONB or strings from JSON)
            assert formats is not None
            assert targeting is not None

            # Cleanup
            session.delete(product)
            session.delete(tenant)
            session.commit()

    def test_schema_validation_with_database_data(self, integration_db):
        """Test that data from database can be validated against Pydantic schemas."""
        tenant_id = "test_schema_validation"

        with get_db_session() as session:
            # Cleanup
            session.execute(delete(DBPricingOption).where(DBPricingOption.tenant_id == tenant_id))
            session.execute(delete(ProductModel).where(ProductModel.tenant_id == tenant_id))
            session.execute(delete(Tenant).where(Tenant.tenant_id == tenant_id))

            tenant = create_tenant_with_timestamps(
                tenant_id=tenant_id, name="Schema Validation Test", subdomain="schema-validation"
            )
            session.add(tenant)

            product = create_test_db_product(
                tenant_id=tenant_id,
                product_id="validation_test_001",
                name="Schema Validation Product",
                description="Testing schema validation with database data",
                format_ids=[{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}],
                delivery_type="non_guaranteed",
            )
            session.add(product)
            session.commit()
            session.refresh(product)

            # Extract data in the same way the real conversion code does
            conversion_data = {
                "product_id": product.product_id,
                "name": product.name,
                "description": product.description,
                "format_ids": product.format_ids,
                "delivery_type": product.delivery_type,
                "is_custom": product.is_custom if product.is_custom is not None else False,
                "publisher_properties": [
                    {
                        "selection_type": "by_id",
                        "publisher_domain": "example.com",
                        "property_ids": getattr(product, "property_tags", ["all_inventory"]),
                    }
                ],
                "pricing_options": [
                    {
                        "pricing_option_id": "cpm_usd_fixed",
                        "pricing_model": "cpm",
                        # V3 shape: fixed pricing carries fixed_price. Was
                        # "rate" + "is_fixed" citing adcp 2.5.0 -- both replaced when
                        # V3 made presence the discriminator, and both accepted only
                        # because the SDK DTO allowed extras.
                        "fixed_price": 7.25,
                        "currency": "USD",
                    }
                ],
                "delivery_measurement": {"provider": "Test Provider", "notes": "Test measurement methodology"},
                "reporting_capabilities": default_reporting_capabilities(),
            }

            # This should succeed without validation errors
            try:
                validated_product = Product(**conversion_data)
                assert validated_product.product_id == "validation_test_001"
                # The element IS the union member: Product.pricing_options is typed with
                # the arms, so its fields are read directly. This went through ``.root``
                # for a local RootModel wrapper that was never what this list held.
                pricing = validated_product.pricing_options[0]
                assert pricing.fixed_price == 7.25
                assert pricing.pricing_model == "cpm"
            except Exception as e:
                pytest.fail(f"Schema validation failed with database data: {e}")

            # Cleanup
            session.delete(product)
            session.delete(tenant)
            session.commit()


class TestFieldAccessPatterns:
    """Test different patterns for accessing model fields safely."""

    def test_safe_field_access_patterns(self):
        """Test recommended patterns for safe field access."""
        product_data = {
            "product_id": "pattern_test_001",
            "name": "Pattern Test Product",
            "description": "Testing safe access patterns",
            "format_ids": [{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}],
            "delivery_type": "non_guaranteed",
            "publisher_properties": [
                {
                    "selection_type": "by_id",
                    "publisher_domain": "example.com",
                    "property_ids": ["all_inventory"],
                }
            ],
            "pricing_options": [
                {
                    "pricing_option_id": "cpm_usd_fixed",
                    "pricing_model": "cpm",
                    # V3 shape: fixed pricing carries fixed_price. Was
                    # "rate" + "is_fixed" citing adcp 2.4.0+ -- both replaced when
                    # V3 made presence the discriminator, and both accepted only
                    # because the SDK DTO allowed extras.
                    "fixed_price": 10.0,
                    "currency": "USD",
                }
            ],
            "delivery_measurement": {"provider": "Test Provider", "notes": "Test measurement methodology"},
            "reporting_capabilities": default_reporting_capabilities(),
        }

        product = Product(**product_data)

        # Pattern 1: Direct access for required fields (safe)
        assert product.product_id == "pattern_test_001"
        assert product.name == "Pattern Test Product"

        # Pattern 2: Conditional access for optional fields
        cpm = getattr(product, "cpm", None)
        assert cpm is None  # Optional field, not provided

        # Pattern 3: hasattr check before access
        if hasattr(product, "brief_relevance"):
            brief_relevance = product.brief_relevance
        else:
            brief_relevance = None
        assert brief_relevance is None

        # Pattern 4: Using model_dump() for safe dict access
        product_dict = product.model_dump()
        assert product_dict.get("product_id") == "pattern_test_001"
        assert product_dict.get("non_existent_field") is None

        # Pattern 5: Using model_fields to check field existence
        assert "product_id" in Product.model_fields
        assert "non_existent_field" not in Product.model_fields

    def test_unsafe_field_access_patterns(self):
        """Test patterns that would cause AttributeError (what to avoid)."""
        product_data = {
            "product_id": "unsafe_test_001",
            "name": "Unsafe Test Product",
            "description": "Testing unsafe access patterns",
            "format_ids": [{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}],
            "delivery_type": "non_guaranteed",
            "publisher_properties": [
                {
                    "selection_type": "by_id",
                    "publisher_domain": "example.com",
                    "property_ids": ["all_inventory"],
                }
            ],
            "pricing_options": [
                {
                    "pricing_option_id": "cpm_usd_fixed",
                    "pricing_model": "cpm",
                    # V3 shape: fixed pricing carries fixed_price. Was
                    # "rate" + "is_fixed" citing adcp 2.4.0+ -- both replaced when
                    # V3 made presence the discriminator, and both accepted only
                    # because the SDK DTO allowed extras.
                    "fixed_price": 10.0,
                    "currency": "USD",
                }
            ],
            "delivery_measurement": {"provider": "Test Provider", "notes": "Test measurement methodology"},
            "reporting_capabilities": default_reporting_capabilities(),
        }

        product = Product(**product_data)

        # Unsafe Pattern 1: Direct access to non-existent field
        with pytest.raises(AttributeError):
            _ = product.pricing  # This would have caused the original bug

        # Unsafe Pattern 2: Direct access to non-existent field that was removed
        # Legacy fields like min_spend no longer exist in Product schema (moved to pricing_options)
        with pytest.raises(AttributeError):
            _ = product.min_spend  # Field removed, should raise AttributeError

        # Demonstrate what would happen with ORM object (simulated)
        class MockORM:
            def __init__(self):
                self.product_id = "mock_001"
                self.name = "Mock Product"

        mock_orm = MockORM()

        # This is safe - field exists
        assert mock_orm.product_id == "mock_001"

        # This would cause AttributeError (simulates the original bug)
        with pytest.raises(AttributeError):
            _ = mock_orm.pricing  # Field doesn't exist in ORM object

    def test_database_orm_field_validation(self):
        """Test validation against actual ORM model fields."""
        # Get all column names from the actual ProductModel
        actual_db_fields = {column.name for column in ProductModel.__table__.columns}

        # Fields that should exist in database
        # Note: Legacy pricing fields (cpm, min_spend, is_fixed_price) have been removed
        # in favor of the pricing_options relationship table
        expected_fields = {
            "tenant_id",
            "product_id",
            "name",
            "description",
            "format_ids",
            "targeting_template",
            "delivery_type",
            "measurement",
            "creative_policy",
            "price_guidance",
            "is_custom",
            "expires_at",
            "countries",
            "implementation_config",
        }

        # Verify all expected fields exist
        missing_fields = expected_fields - actual_db_fields
        assert not missing_fields, f"Expected database fields missing: {missing_fields}"

        # Fields that should NOT exist (would cause the original bug)
        forbidden_fields = {"pricing", "cost_basis", "margin"}

        # Verify forbidden fields don't exist
        existing_forbidden = forbidden_fields & actual_db_fields
        assert not existing_forbidden, f"Forbidden fields found in database: {existing_forbidden}"
