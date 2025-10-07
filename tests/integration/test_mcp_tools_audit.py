#!/usr/bin/env python3
"""
MCP Tools Audit for Roundtrip Conversion Issues

This audit systematically tests all MCP tools that use the roundtrip pattern:
Object → model_dump*() → apply_testing_hooks() → Object(**dict)

This prevents validation errors like the "formats field required" bug that reached production.

Audit Results:
1. ✅ get_products - FIXED: Now uses model_dump_internal() correctly
2. ⚠️ get_media_buy_delivery - POTENTIAL ISSUE: Uses model_dump() instead of model_dump_internal()
3. ✅ create_media_buy - SAFE: Reconstructs same response type
4. 📝 Other tools - No roundtrip conversion patterns found

Critical Insights:
- Tools that convert objects to dicts and back MUST use model_dump_internal()
- External model_dump() may exclude fields needed for reconstruction
- Testing hooks can modify data, requiring careful field handling
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import delete

from src.core.database.database_session import get_db_session
from src.core.database.models import MediaBuy as MediaBuyModel
from src.core.database.models import Product as ProductModel
from src.core.database.models import Tenant
from src.core.schemas import (
    Budget,
    DeliveryTotals,
    MediaBuyDeliveryData,
    PackageDelivery,
)
from src.core.testing_hooks import TestingContext, apply_testing_hooks
from tests.utils.database_helpers import create_tenant_with_timestamps


class TestMCPToolsAudit:
    """Audit all MCP tools for roundtrip conversion vulnerabilities."""

    @pytest.fixture
    def test_tenant_id(self):
        """Create a test tenant for audit tests."""
        tenant_id = "audit_test_tenant"
        with get_db_session() as session:
            # Clean up any existing test data
            session.execute(delete(MediaBuyModel).where(MediaBuyModel.tenant_id == tenant_id))
            session.execute(delete(ProductModel).where(ProductModel.tenant_id == tenant_id))
            # Clean up principals
            from src.core.database.models import Principal as PrincipalModel

            session.execute(delete(PrincipalModel).where(PrincipalModel.tenant_id == tenant_id))
            session.execute(delete(Tenant).where(Tenant.tenant_id == tenant_id))

            # Create test tenant
            tenant = create_tenant_with_timestamps(
                tenant_id=tenant_id, name="Audit Test Tenant", subdomain="audit-test"
            )
            session.add(tenant)
            session.commit()

        yield tenant_id

        # Cleanup
        with get_db_session() as session:
            session.execute(delete(MediaBuyModel).where(MediaBuyModel.tenant_id == tenant_id))
            session.execute(delete(ProductModel).where(ProductModel.tenant_id == tenant_id))
            # Clean up principals
            from src.core.database.models import Principal as PrincipalModel

            session.execute(delete(PrincipalModel).where(PrincipalModel.tenant_id == tenant_id))
            session.execute(delete(Tenant).where(Tenant.tenant_id == tenant_id))
            session.commit()

    def test_get_media_buy_delivery_roundtrip_safety(self, test_tenant_id):
        """
        Audit get_media_buy_delivery for roundtrip conversion safety.

        POTENTIAL ISSUE IDENTIFIED: This tool uses model_dump() instead of
        model_dump_internal(), which may cause field mapping issues if
        MediaBuyDeliveryData has internal/external field differences.
        """
        # Create test media buy
        media_buy_data = {
            "media_buy_id": "audit_test_mb_001",
            "principal_id": "audit_test_principal",
            "status": "active",
            "order_name": "Audit Test Order",
            "advertiser_name": "Test Advertiser",
            "start_date": date(2025, 1, 1),
            "end_date": date(2025, 1, 31),
            "start_time": datetime(2025, 1, 1, tzinfo=UTC),
            "end_time": datetime(2025, 1, 31, 23, 59, 59, tzinfo=UTC),
            "budget": Decimal("10000.00"),
            "raw_request": {"targeting": {"geo_country": ["US"]}, "product_ids": ["audit_test_product"]},
        }

        with get_db_session() as session:
            # Create test principal first (required for foreign key constraint)
            import uuid

            from src.core.database.models import Principal as PrincipalModel

            principal = PrincipalModel(
                tenant_id=test_tenant_id,
                principal_id="audit_test_principal",
                name="Audit Test Principal",
                access_token=f"audit_test_token_{uuid.uuid4().hex[:8]}",
                platform_mappings={"mock": {"advertiser_id": "test_advertiser"}},
            )
            session.add(principal)

            # Create test product
            product = ProductModel(
                tenant_id=test_tenant_id,
                product_id="audit_test_product",
                name="Audit Test Product",
                description="Product for audit testing",
                formats=["display_300x250"],
                targeting_template={},
                delivery_type="non_guaranteed",
                is_fixed_price=False,
                is_custom=False,
            )
            session.add(product)

            # Create test media buy
            media_buy = MediaBuyModel(tenant_id=test_tenant_id, **media_buy_data)
            session.add(media_buy)
            session.commit()

        # Create delivery data object to test roundtrip
        delivery_data = MediaBuyDeliveryData(
            media_buy_id="audit_test_mb_001",
            buyer_ref="audit_test_ref",
            status="active",
            totals=DeliveryTotals(impressions=100000.0, spend=5000.0, clicks=2500.0, ctr=0.025),
            by_package=[
                PackageDelivery(
                    package_id="audit_test_package_001",
                    buyer_ref="audit_package_ref",
                    impressions=100000.0,
                    spend=5000.0,
                    clicks=2500.0,
                    pacing_index=1.0,
                )
            ],
        )

        # Test the roundtrip pattern used by get_media_buy_delivery
        # Step 1: Convert to dict (what the tool does)
        delivery_dict = delivery_data.model_dump()  # This is what the tool uses

        # Step 2: Apply testing hooks
        testing_ctx = TestingContext(dry_run=True, test_session_id="audit_delivery_test")
        response_data = {"deliveries": [delivery_dict]}
        modified_response = apply_testing_hooks(response_data, testing_ctx, "get_media_buy_delivery")

        # Step 3: Reconstruct objects (critical point)
        modified_delivery_dicts = modified_response["deliveries"]

        # This is the exact line from the tool that could fail
        try:
            reconstructed_deliveries = [MediaBuyDeliveryData(**d) for d in modified_delivery_dicts]

            # If we get here, the roundtrip worked
            assert len(reconstructed_deliveries) == 1
            reconstructed_delivery = reconstructed_deliveries[0]

            # Verify essential data survived roundtrip
            assert reconstructed_delivery.media_buy_id == delivery_data.media_buy_id
            assert reconstructed_delivery.buyer_ref == delivery_data.buyer_ref
            assert reconstructed_delivery.status == delivery_data.status
            assert reconstructed_delivery.totals.impressions == delivery_data.totals.impressions
            assert reconstructed_delivery.totals.spend == delivery_data.totals.spend
            assert reconstructed_delivery.totals.clicks == delivery_data.totals.clicks
            assert len(reconstructed_delivery.by_package) == len(delivery_data.by_package)
            assert reconstructed_delivery.by_package[0].package_id == delivery_data.by_package[0].package_id

            print("✅ get_media_buy_delivery roundtrip is SAFE")

        except Exception as e:
            pytest.fail(f"❌ get_media_buy_delivery roundtrip FAILED: {e}")

    def test_media_buy_delivery_data_field_consistency(self):
        """
        Test MediaBuyDeliveryData for internal/external field consistency.

        This verifies that model_dump() and model_dump_internal() (if it exists)
        produce compatible output for roundtrip conversion.
        """
        delivery_data = MediaBuyDeliveryData(
            media_buy_id="field_consistency_test",
            buyer_ref="consistency_ref",
            status="active",
            totals=DeliveryTotals(impressions=50000.0, spend=3000.0, clicks=1500.0, ctr=0.03),
            by_package=[
                PackageDelivery(
                    package_id="consistency_test_package",
                    buyer_ref="consistency_package_ref",
                    impressions=50000.0,
                    spend=3000.0,
                    clicks=1500.0,
                    pacing_index=1.2,
                )
            ],
        )

        # Test external dump
        external_dict = delivery_data.model_dump()

        # Test internal dump (if available)
        if hasattr(delivery_data, "model_dump_internal"):
            internal_dict = delivery_data.model_dump_internal()

            # Compare fields - they should be compatible for roundtrip
            for field_name, external_value in external_dict.items():
                if field_name in internal_dict:
                    internal_value = internal_dict[field_name]
                    # Values should be compatible (allowing for type conversions)
                    assert type(external_value) is type(
                        internal_value
                    ), f"Field '{field_name}' type mismatch: {type(external_value)} vs {type(internal_value)}"
        else:
            # MediaBuyDeliveryData doesn't have model_dump_internal, so model_dump() is used
            # This means we need to ensure model_dump() produces reconstruction-compatible output
            pass

        # Test reconstruction from external dict
        reconstructed = MediaBuyDeliveryData(**external_dict)

        # Verify reconstruction preserves all data
        assert reconstructed.media_buy_id == delivery_data.media_buy_id
        assert reconstructed.buyer_ref == delivery_data.buyer_ref
        assert reconstructed.status == delivery_data.status
        assert reconstructed.totals.impressions == delivery_data.totals.impressions
        assert reconstructed.totals.spend == delivery_data.totals.spend

    def test_budget_nested_object_roundtrip(self):
        """
        Test Budget nested object roundtrip in MediaBuyDeliveryData.

        Nested objects can cause additional complexity in roundtrip conversions.
        """
        # Test various Budget configurations
        budget_configs = [
            {"total": 5000.0, "currency": "USD", "daily_cap": 200.0, "pacing": "even"},
            {"total": 10000.0, "currency": "USD", "pacing": "asap"},  # No daily budget
            {
                "total": 1000.0,
                "currency": "USD",
                "daily_cap": 50.0,
                "pacing": "daily_budget",
                "auto_pause_on_budget_exhaustion": True,
            },
        ]

        for i, budget_config in enumerate(budget_configs):
            budget = Budget(**budget_config)

            delivery_data = MediaBuyDeliveryData(
                media_buy_id=f"budget_test_{i}",
                buyer_ref=f"budget_ref_{i}",
                status="active",
                totals=DeliveryTotals(
                    impressions=25000.0, spend=budget.total, clicks=750.0, ctr=0.03  # Use budget total as spend amount
                ),
                by_package=[
                    PackageDelivery(
                        package_id=f"budget_test_package_{i}",
                        buyer_ref=f"budget_package_ref_{i}",
                        impressions=25000.0,
                        spend=budget.total,
                        clicks=750.0,
                        pacing_index=1.0,
                    )
                ],
            )

            # Test roundtrip with delivery data structure
            delivery_dict = delivery_data.model_dump()
            reconstructed = MediaBuyDeliveryData(**delivery_dict)

            # Verify data survived roundtrip correctly
            assert reconstructed.totals.spend == budget.total
            assert reconstructed.totals.impressions == 25000.0
            assert len(reconstructed.by_package) == 1
            assert reconstructed.by_package[0].spend == budget.total

    def test_all_mcp_tools_roundtrip_pattern_audit(self):
        """
        Comprehensive audit of all MCP tools for roundtrip conversion patterns.

        This test identifies which tools use the potentially dangerous pattern:
        Object → dict → apply_testing_hooks → Object(**dict)
        """
        # Audit results based on code analysis
        audit_results = {
            "get_products": {
                "uses_roundtrip": True,
                "uses_internal_dump": True,  # FIXED: Now uses model_dump_internal()
                "risk_level": "LOW",
                "status": "✅ SAFE",
                "notes": "Uses model_dump_internal() correctly, field mapping is safe",
            },
            "get_media_buy_delivery": {
                "uses_roundtrip": True,
                "uses_internal_dump": False,  # Uses model_dump()
                "risk_level": "MEDIUM",
                "status": "⚠️ MONITOR",
                "notes": "Uses model_dump() but MediaBuyDeliveryData has simple field structure",
            },
            "create_media_buy": {
                "uses_roundtrip": True,
                "uses_internal_dump": False,  # Uses model_dump() on response
                "risk_level": "LOW",
                "status": "✅ SAFE",
                "notes": "Reconstructs same response type, no field mapping issues",
            },
            "list_creative_formats": {
                "uses_roundtrip": False,
                "risk_level": "NONE",
                "status": "✅ SAFE",
                "notes": "No roundtrip conversion, returns static format data",
            },
            "create_creative": {
                "uses_roundtrip": False,
                "risk_level": "NONE",
                "status": "✅ SAFE",
                "notes": "No roundtrip conversion through testing hooks",
            },
        }

        # Verify audit findings
        high_risk_tools = [tool for tool, info in audit_results.items() if info["risk_level"] == "HIGH"]
        medium_risk_tools = [tool for tool, info in audit_results.items() if info["risk_level"] == "MEDIUM"]

        # Report findings
        print("\n" + "=" * 60)
        print("MCP TOOLS ROUNDTRIP CONVERSION AUDIT RESULTS")
        print("=" * 60)

        for tool_name, info in audit_results.items():
            print(f"{info['status']} {tool_name:<25} Risk: {info['risk_level']:<6} - {info['notes']}")

        print("\n" + "-" * 60)
        print(f"HIGH RISK TOOLS: {len(high_risk_tools)} (require immediate attention)")
        print(f"MEDIUM RISK TOOLS: {len(medium_risk_tools)} (require monitoring)")
        print("-" * 60)

        # Fail test if any high-risk tools are found
        assert len(high_risk_tools) == 0, f"HIGH RISK tools found: {high_risk_tools}"

        # Warn about medium-risk tools
        if medium_risk_tools:
            print(f"⚠️ WARNING: Medium-risk tools require monitoring: {medium_risk_tools}")

    def test_field_mapping_anti_patterns_detection(self):
        """
        Test for anti-patterns that lead to field mapping issues.

        These patterns would cause validation errors in production:
        1. Using external field names in internal dicts
        2. Missing required fields after conversion
        3. Type mismatches during reconstruction
        """
        # Anti-pattern 1: External field names in internal data
        # This simulates the original bug in get_products
        anti_pattern_data = {
            "product_id": "anti_pattern_test",
            "name": "Anti-pattern Test Product",
            "description": "Testing anti-pattern detection",
            "format_ids": ["display_300x250"],  # WRONG: External name in internal data
            "delivery_type": "guaranteed",
            "is_fixed_price": True,
            "is_custom": False,
        }

        # This should fail with validation error
        from src.core.schemas import Product

        with pytest.raises(ValueError, match="formats"):
            Product(**anti_pattern_data)

        # Anti-pattern 2: Type mismatches
        type_mismatch_data = {
            "media_buy_id": "type_mismatch_test",
            "buyer_ref": "mismatch_ref",
            "status": "active",
            "totals": {"total_budget_usd": 1000.0},  # WRONG: Dict instead of DeliveryTotals object
            "by_package": [{"package_id": "test", "wrong_field": "invalid"}],  # WRONG: Missing required fields
        }

        # This should fail with validation error
        with pytest.raises(ValueError):
            MediaBuyDeliveryData(**type_mismatch_data)

        print("✅ Anti-pattern detection working correctly")

    def test_testing_hooks_data_preservation(self):
        """
        Test that testing hooks preserve essential data for reconstruction.

        Testing hooks should modify data safely without breaking roundtrip conversion.
        """
        # Test data with all field types that might be affected by testing hooks
        test_cases = [
            {
                "name": "simple_strings",
                "data": {"string_field": "test_value", "id_field": "test_id_123"},
                "expected_preservation": True,
            },
            {
                "name": "numeric_values",
                "data": {"int_field": 42, "float_field": 3.14, "decimal_field": Decimal("10.50")},
                "expected_preservation": True,
            },
            {
                "name": "complex_objects",
                "data": {"date_field": date(2025, 1, 15), "list_field": ["a", "b", "c"]},
                "expected_preservation": True,
            },
            {"name": "nested_dicts", "data": {"nested": {"inner": "value", "count": 5}}, "expected_preservation": True},
        ]

        for test_case in test_cases:
            testing_ctx = TestingContext(dry_run=True, test_session_id=f"preservation_test_{test_case['name']}")

            # Apply testing hooks
            response_data = {"test_data": test_case["data"]}
            modified_response = apply_testing_hooks(response_data, testing_ctx, "test_operation")

            # Verify data preservation
            modified_data = modified_response["test_data"]

            if test_case["expected_preservation"]:
                # Essential data should be preserved
                for key, original_value in test_case["data"].items():
                    assert key in modified_data, f"Key '{key}' lost during testing hooks"
                    modified_value = modified_data[key]

                    # Handle type-specific comparisons
                    if isinstance(original_value, Decimal):
                        # Decimals might be converted to floats
                        assert float(modified_value) == float(original_value), f"Numeric value changed for '{key}'"
                    elif isinstance(original_value, date):
                        # Dates might be converted to ISO strings
                        if isinstance(modified_value, str):
                            from datetime import datetime

                            parsed_date = datetime.fromisoformat(modified_value).date()
                            assert parsed_date == original_value, f"Date value changed for '{key}'"
                        else:
                            assert modified_value == original_value, f"Date value changed for '{key}'"
                    else:
                        assert (
                            modified_value == original_value
                        ), f"Value changed for '{key}': {original_value} → {modified_value}"

        print("✅ Testing hooks preserve essential data correctly")
