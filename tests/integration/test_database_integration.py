#!/usr/bin/env python3
"""Test with REAL database connection to catch SQL errors our mocked tests miss."""

import os
import sys

import psycopg2
import pytest
from psycopg2.extras import DictCursor

# Get database URL from environment
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://adcp_user:secure_password_change_me@localhost:5479/adcp")


@pytest.mark.integration
@pytest.mark.requires_db
def test_settings_queries():
    """Test the actual SQL queries used in the settings page."""

    print(f"\n{'=' * 60}")
    print("TESTING REAL DATABASE QUERIES")
    print(f"{'=' * 60}\n")

    print(f"Database: {DATABASE_URL.split('@')[1] if '@' in DATABASE_URL else DATABASE_URL}\n")

    errors = []
    tenant_id = "default"

    try:
        # Connect to real database
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=DictCursor)
        cursor = conn.cursor()

        # Test 1: Check if tenant exists
        print("1. Testing tenant query...")
        try:
            cursor.execute("SELECT tenant_id, name FROM tenants WHERE tenant_id = %s", (tenant_id,))
            tenant = cursor.fetchone()
            if tenant:
                print(f"   ✓ Tenant found: {tenant['name']}")
            else:
                print("   ⚠️  Tenant not found")
        except Exception as e:
            print(f"   ❌ ERROR: {e}")
            errors.append(f"Tenant query: {e}")

        # Test 2: Products query
        print("\n2. Testing products query...")
        try:
            cursor.execute("SELECT COUNT(*) FROM products WHERE tenant_id = %s", (tenant_id,))
            count = cursor.fetchone()[0]
            print(f"   ✓ Products count: {count}")
        except Exception as e:
            print(f"   ❌ ERROR: {e}")
            errors.append(f"Products query: {e}")

        # Test 3: Creatives query (creative_formats table was dropped in migration f2addf453200)
        print("\n3. Testing creatives query...")
        try:
            cursor.execute(
                """
                SELECT COUNT(*)
                FROM creatives
                WHERE tenant_id = %s
            """,
                (tenant_id,),
            )
            count = cursor.fetchone()[0]
            print(f"   ✓ Creatives count: {count}")
        except Exception as e:
            print(f"   ❌ ERROR: {e}")
            errors.append(f"Creatives query: {e}")

        # Test 4: Media buys with date query
        print("\n4. Testing media buys date query...")
        try:
            cursor.execute(
                """
                SELECT COUNT(DISTINCT principal_id)
                FROM media_buys
                WHERE tenant_id = %s
                AND created_at >= CURRENT_TIMESTAMP - INTERVAL '30 days'
            """,
                (tenant_id,),
            )
            count = cursor.fetchone()[0]
            print(f"   ✓ Active advertisers in last 30 days: {count}")
        except Exception as e:
            print(f"   ❌ ERROR: {e}")
            errors.append(f"Media buys date query: {e}")

        # Test 5: Principals query
        print("\n5. Testing principals query...")
        try:
            cursor.execute("SELECT COUNT(*) FROM principals WHERE tenant_id = %s", (tenant_id,))
            count = cursor.fetchone()[0]
            print(f"   ✓ Principals count: {count}")
        except Exception as e:
            print(f"   ❌ ERROR: {e}")
            errors.append(f"Principals query: {e}")

        # Test 6: Human tasks query
        print("\n6. Testing human tasks query...")
        try:
            cursor.execute(
                """
                SELECT COUNT(*) FROM human_tasks
                WHERE tenant_id = %s AND status IN ('pending', 'in_progress')
            """,
                (tenant_id,),
            )
            count = cursor.fetchone()[0]
            print(f"   ✓ Open tasks: {count}")
        except Exception as e:
            print(f"   ❌ ERROR: {e}")
            errors.append(f"Human tasks query: {e}")

        conn.close()

    except psycopg2.OperationalError as e:
        print(f"❌ Cannot connect to database: {e}")
        print("\nMake sure:")
        print("1. PostgreSQL container is running")
        print("2. DATABASE_URL is correct")
        print("3. Port 5479 is the right port for your PostgreSQL")
        pytest.fail(f"Cannot connect to database: {e}")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        pytest.fail(f"Unexpected error: {e}")

    # Summary
    print(f"\n{'=' * 60}")
    print("SUMMARY")
    print(f"{'=' * 60}")

    if errors:
        print(f"\n❌ {len(errors)} queries failed:")
        for error in errors:
            print(f"   - {error}")
        pytest.fail(f"{len(errors)} queries failed: {errors}")
    else:
        print("\n✅ All queries passed!")
        print("The settings page should work without 500 errors.")


if __name__ == "__main__":
    success = test_settings_queries()
    sys.exit(0 if success else 1)
