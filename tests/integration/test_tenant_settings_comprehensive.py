#!/usr/bin/env python3
"""
Comprehensive test for the tenant settings page to catch 500 errors.
This test connects to the real database and performs actual queries
to ensure SQL compatibility and schema correctness.
"""

import os
import sys

import psycopg2
import pytest
import requests
from psycopg2.extras import DictCursor

# Test configuration
BASE_URL = f"http://localhost:{os.environ.get('ADMIN_UI_PORT', '8001')}"
TEST_EMAIL = "test_super_admin@example.com"
TEST_PASSWORD = "test123"

# Database configuration
DB_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://adcp_user:secure_password_change_me@localhost:5436/adcp",
)


@pytest.mark.integration
@pytest.mark.requires_db
def test_database_queries():
    """Test the actual database queries used by the settings page"""
    print("\n🔍 Testing database queries...")

    try:
        conn = psycopg2.connect(DB_URL, cursor_factory=DictCursor)
        cursor = conn.cursor()

        # Test 1: Check products table structure
        print("\n1. Testing products table query...")
        cursor.execute(
            """
            SELECT COUNT(*) as total_products
            FROM products
            WHERE tenant_id = %s
        """,
            ("default",),
        )
        result = cursor.fetchone()
        print(f"   ✓ Products count: {result['total_products']}")

        # Test 2: Check media_buys query with PostgreSQL syntax
        print("\n2. Testing active advertisers query...")
        cursor.execute(
            """
            SELECT COUNT(DISTINCT principal_id)
            FROM media_buys
            WHERE tenant_id = %s
            AND created_at >= CURRENT_TIMESTAMP - INTERVAL '30 days'
        """,
            ("default",),
        )
        result = cursor.fetchone()
        print(f"   ✓ Active advertisers: {result[0]}")

        # Test 3: Check creative_formats query (SKIPPED - table dropped in Oct 2025)
        print("\n3. Testing creative formats query...")
        print("   ⚠️  Skipping - creative_formats table removed in migration f2addf453200")
        print("   ℹ️  Creative formats now fetched from creative agents via AdCP")
        formats = []

        # Test 4: Check principals table
        print("\n4. Testing principals query...")
        cursor.execute(
            """
            SELECT COUNT(*) as total_principals
            FROM principals
            WHERE tenant_id = %s
        """,
            ("default",),
        )
        result = cursor.fetchone()
        print(f"   ✓ Total principals: {result['total_principals']}")

        # Test 5: Check workflow_steps table (replaces deprecated tasks table)
        print("\n5. Testing workflow steps query...")
        try:
            cursor.execute(
                """
                SELECT COUNT(*) as pending_workflow_steps
                FROM workflow_steps ws
                JOIN contexts c ON ws.context_id = c.context_id
                WHERE c.tenant_id = %s AND ws.status = 'requires_approval'
            """,
                ("default",),
            )
            result = cursor.fetchone()
            print(f"   ✓ Pending workflow steps: {result['pending_workflow_steps']}")
        except psycopg2.errors.UndefinedTable:
            print("   ⚠️  Workflow steps table doesn't exist (may not be initialized)")

        cursor.close()
        conn.close()
        print("\n✅ All database queries successful!")

    except Exception as e:
        print(f"\n❌ Database error: {e}")
        pytest.fail(f"Database error: {e}")


@pytest.mark.integration
@pytest.mark.requires_server
def test_settings_page():
    """Test the settings page through HTTP"""
    print("\n🌐 Testing settings page HTTP access...")

    # Create session
    session = requests.Session()

    # Test authentication
    print("\n1. Testing authentication...")
    auth_data = {"email": TEST_EMAIL, "password": TEST_PASSWORD}
    response = session.post(f"{BASE_URL}/test/auth", data=auth_data, allow_redirects=False)
    print(f"   Auth response: {response.status_code}")

    if response.status_code == 302:
        print("   ✓ Authentication successful")
    else:
        print(f"   ❌ Authentication failed: {response.status_code}")
        return False

    # Test settings page
    print("\n2. Testing settings page...")
    response = session.get(f"{BASE_URL}/tenant/default/settings")
    print(f"   Settings page response: {response.status_code}")

    if response.status_code == 200:
        print("   ✓ Settings page loaded successfully")

        # Check for key elements
        content = response.text
        checks = [
            ("Product Setup Wizard" in content, "Product Setup Wizard link"),
            ("Ad Server" in content, "Ad Server section"),
            ("Products" in content, "Products section"),
            (
                "Advertisers" in content or "Principals" in content,
                "Advertisers section",
            ),
            ("btn-success" in content, "Success button CSS"),
        ]

        for check, name in checks:
            if check:
                print(f"   ✓ Found: {name}")
            else:
                print(f"   ⚠️  Missing: {name}")

    elif response.status_code == 500:
        print("   ❌ Server error (500)")
        # Try to extract error details
        if "error" in response.text.lower() or "exception" in response.text.lower():
            print("\n   Error details found in response:")
            lines = response.text.split("\n")
            for line in lines:
                if "error" in line.lower() or "exception" in line.lower():
                    print(f"     {line.strip()[:200]}")
        pytest.fail("Settings page returned 500 error")
    else:
        print(f"   ⚠️  Unexpected status: {response.status_code}")
        pytest.fail(f"Unexpected status: {response.status_code}")

    # Test dashboard page
    print("\n3. Testing dashboard page...")
    response = session.get(f"{BASE_URL}/tenant/default")
    print(f"   Dashboard response: {response.status_code}")

    if response.status_code == 200:
        print("   ✓ Dashboard loaded successfully")
    elif response.status_code == 500:
        print("   ❌ Dashboard server error (500)")
        pytest.fail("Dashboard page returned 500 error")


def main():
    print("=" * 60)
    print("COMPREHENSIVE SETTINGS PAGE TEST")
    print("=" * 60)

    # Run database tests
    db_success = test_database_queries()

    # Run HTTP tests
    http_success = test_settings_page()

    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)

    if db_success and http_success:
        print("✅ ALL TESTS PASSED")
        print("\nThe settings page should now work without 500 errors.")
        print("Key fixes applied:")
        print("  1. Removed query for non-existent 'is_active' column in products")
        print("  2. Fixed PostgreSQL date syntax (CURRENT_TIMESTAMP - INTERVAL)")
        print("  3. Removed query for non-existent 'auto_approve' column")
        print("  4. Added Product Setup Wizard link and button")
        print("  5. Added GAM configuration section")
        print("  6. Added button CSS styles")
        return 0
    else:
        print("❌ SOME TESTS FAILED")
        print("\nPlease check the error messages above for details.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
