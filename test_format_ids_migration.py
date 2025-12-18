#!/usr/bin/env python3
"""Test script for format_ids migration and validation.

This script tests:
1. Database column rename (formats → format_ids)
2. PostgreSQL CHECK constraint validation
3. Type safety at database level
4. Code compatibility with new field names

Run with: uv run python test_format_ids_migration.py
"""

import sys

from src.core.database.database_session import get_db_session
from src.core.database.models import InventoryProfile, Product, Tenant


def test_product_format_ids_valid():
    """Test that Product.format_ids accepts valid FormatId objects."""
    with get_db_session() as session:
        # Create test tenant
        tenant = Tenant(
            tenant_id="test_fmt_valid",
            name="Test Tenant",
            subdomain="test",
            adapter_type="mock",
        )
        session.add(tenant)
        session.flush()

        # Valid format_ids per AdCP spec
        valid_format_ids = [
            {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"},
            {"agent_url": "https://creative.adcontextprotocol.org", "id": "video_standard_30s"},
        ]

        product = Product(
            tenant_id="test_fmt_valid",
            product_id="prod_valid",
            name="Test Product",
            description="Test",
            format_ids=valid_format_ids,
            targeting_template={},
            delivery_type="guaranteed",
        )

        session.add(product)
        session.commit()

        # Verify saved correctly
        saved = session.query(Product).filter_by(product_id="prod_valid").first()
        assert saved is not None
        assert saved.format_ids == valid_format_ids

        # Cleanup
        session.delete(saved)
        session.delete(tenant)
        session.commit()

    print("✅ test_product_format_ids_valid passed")


def test_product_format_ids_invalid_structure():
    """Test that Product.format_ids rejects invalid structures."""
    with get_db_session() as session:
        tenant = Tenant(
            tenant_id="test_fmt_invalid",
            name="Test Tenant",
            subdomain="test2",
            adapter_type="mock",
        )
        session.add(tenant)
        session.flush()

        # Invalid: missing agent_url
        invalid_format_ids = [{"id": "display_300x250"}]

        product = Product(
            tenant_id="test_fmt_invalid",
            product_id="prod_invalid",
            name="Test Product",
            description="Test",
            format_ids=invalid_format_ids,
            targeting_template={},
            delivery_type="guaranteed",
        )

        session.add(product)

        try:
            session.commit()
            print("❌ test_product_format_ids_invalid_structure FAILED: Should have raised exception")
            sys.exit(1)
        except Exception as e:
            # Expected: CHECK constraint violation
            assert "format_ids" in str(e).lower() or "agent_url" in str(e).lower()
            session.rollback()

        # Cleanup
        session.delete(tenant)
        session.commit()

    print("✅ test_product_format_ids_invalid_structure passed (correctly rejected)")


def test_product_format_ids_invalid_empty_strings():
    """Test that Product.format_ids rejects empty strings."""
    with get_db_session() as session:
        tenant = Tenant(
            tenant_id="test_fmt_empty",
            name="Test Tenant",
            subdomain="test3",
            adapter_type="mock",
        )
        session.add(tenant)
        session.flush()

        # Invalid: empty agent_url
        invalid_format_ids = [{"agent_url": "", "id": "display_300x250"}]

        product = Product(
            tenant_id="test_fmt_empty",
            product_id="prod_empty",
            name="Test Product",
            description="Test",
            format_ids=invalid_format_ids,
            targeting_template={},
            delivery_type="guaranteed",
        )

        session.add(product)

        try:
            session.commit()
            print("❌ test_product_format_ids_invalid_empty_strings FAILED: Should have raised exception")
            sys.exit(1)
        except Exception as e:
            # Expected: CHECK constraint violation
            assert "format_ids" in str(e).lower() or "empty" in str(e).lower()
            session.rollback()

        # Cleanup
        session.delete(tenant)
        session.commit()

    print("✅ test_product_format_ids_invalid_empty_strings passed (correctly rejected)")


def test_product_format_ids_additional_properties():
    """Test that Product.format_ids rejects additional properties."""
    with get_db_session() as session:
        tenant = Tenant(
            tenant_id="test_fmt_extra",
            name="Test Tenant",
            subdomain="test4",
            adapter_type="mock",
        )
        session.add(tenant)
        session.flush()

        # Invalid: extra property
        invalid_format_ids = [
            {
                "agent_url": "https://creative.adcontextprotocol.org",
                "id": "display_300x250",
                "extra_field": "not_allowed",
            }
        ]

        product = Product(
            tenant_id="test_fmt_extra",
            product_id="prod_extra",
            name="Test Product",
            description="Test",
            format_ids=invalid_format_ids,
            targeting_template={},
            delivery_type="guaranteed",
        )

        session.add(product)

        try:
            session.commit()
            print("❌ test_product_format_ids_additional_properties FAILED: Should have raised exception")
            sys.exit(1)
        except Exception as e:
            # Expected: CHECK constraint violation
            assert "format_ids" in str(e).lower() or "extra_field" in str(e).lower() or "exactly" in str(e).lower()
            session.rollback()

        # Cleanup
        session.delete(tenant)
        session.commit()

    print("✅ test_product_format_ids_additional_properties passed (correctly rejected)")


def test_inventory_profile_format_ids_valid():
    """Test that InventoryProfile.format_ids accepts valid FormatId objects."""
    with get_db_session() as session:
        tenant = Tenant(
            tenant_id="test_profile_fmt",
            name="Test Tenant",
            subdomain="test5",
            adapter_type="mock",
        )
        session.add(tenant)
        session.flush()

        valid_format_ids = [
            {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_728x90"},
        ]

        profile = InventoryProfile(
            tenant_id="test_profile_fmt",
            profile_id="prof_test",
            name="Test Profile",
            inventory_config={},
            format_ids=valid_format_ids,
            publisher_properties=[],
        )

        session.add(profile)
        session.commit()

        # Verify
        saved = session.query(InventoryProfile).filter_by(profile_id="prof_test").first()
        assert saved is not None
        assert saved.format_ids == valid_format_ids

        # Cleanup
        session.delete(saved)
        session.delete(tenant)
        session.commit()

    print("✅ test_inventory_profile_format_ids_valid passed")


def test_tenant_auto_approve_format_ids_valid():
    """Test that Tenant.auto_approve_format_ids accepts list of strings."""
    with get_db_session() as session:
        # Valid: array of strings (just IDs, not full FormatId objects)
        valid_format_ids = ["display_300x250", "video_standard_30s"]

        tenant = Tenant(
            tenant_id="test_tenant_fmt",
            name="Test Tenant",
            subdomain="test6",
            adapter_type="mock",
            auto_approve_format_ids=valid_format_ids,
        )

        session.add(tenant)
        session.commit()

        # Verify
        saved = session.query(Tenant).filter_by(tenant_id="test_tenant_fmt").first()
        assert saved is not None
        assert saved.auto_approve_format_ids == valid_format_ids

        # Cleanup
        session.delete(saved)
        session.commit()

    print("✅ test_tenant_auto_approve_format_ids_valid passed")


def test_effective_format_ids_property():
    """Test that Product.effective_format_ids property works correctly."""
    with get_db_session() as session:
        tenant = Tenant(
            tenant_id="test_effective_fmt",
            name="Test Tenant",
            subdomain="test7",
            adapter_type="mock",
        )
        session.add(tenant)
        session.flush()

        product_format_ids = [
            {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"},
        ]

        profile_format_ids = [
            {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_728x90"},
        ]

        # Create inventory profile
        profile = InventoryProfile(
            tenant_id="test_effective_fmt",
            profile_id="prof_effective",
            name="Test Profile",
            inventory_config={},
            format_ids=profile_format_ids,
            publisher_properties=[],
        )
        session.add(profile)
        session.flush()

        # Create product WITHOUT profile (should use own format_ids)
        product1 = Product(
            tenant_id="test_effective_fmt",
            product_id="prod_no_profile",
            name="Product Without Profile",
            description="Test",
            format_ids=product_format_ids,
            targeting_template={},
            delivery_type="guaranteed",
        )
        session.add(product1)
        session.flush()

        # Test: effective_format_ids should return product's own format_ids
        assert product1.effective_format_ids == product_format_ids

        # Create product WITH profile (should use profile's format_ids)
        product2 = Product(
            tenant_id="test_effective_fmt",
            product_id="prod_with_profile",
            name="Product With Profile",
            description="Test",
            format_ids=product_format_ids,  # Has its own, but profile overrides
            targeting_template={},
            delivery_type="guaranteed",
            inventory_profile_id=profile.id,
        )
        session.add(product2)
        session.flush()

        # Refresh to load relationship
        session.refresh(product2)

        # Test: effective_format_ids should return profile's format_ids
        assert product2.effective_format_ids == profile_format_ids

        # Cleanup
        session.delete(product1)
        session.delete(product2)
        session.delete(profile)
        session.delete(tenant)
        session.commit()

    print("✅ test_effective_format_ids_property passed")


if __name__ == "__main__":
    print("\n🧪 Testing format_ids migration and validation...\n")

    try:
        test_product_format_ids_valid()
        test_product_format_ids_invalid_structure()
        test_product_format_ids_invalid_empty_strings()
        test_product_format_ids_additional_properties()
        test_inventory_profile_format_ids_valid()
        test_tenant_auto_approve_format_ids_valid()
        test_effective_format_ids_property()

        print("\n✅ All tests passed! format_ids migration is working correctly.\n")
        print("Database-level validation ensures:")
        print("  • format_ids must be array of objects")
        print("  • Each object must have exactly 'agent_url' and 'id' properties")
        print("  • Both properties must be non-empty strings")
        print("  • No additional properties allowed")
        print("\nThis matches the AdCP FormatId spec exactly! 🎉\n")

    except Exception as e:
        print(f"\n❌ Test failed with error: {e}\n")
        import traceback

        traceback.print_exc()
        sys.exit(1)
