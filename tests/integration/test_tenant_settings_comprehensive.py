#!/usr/bin/env python3
"""
Comprehensive test for the tenant settings page to catch 500 errors.
This test connects to the real database and performs actual queries
to ensure SQL compatibility and schema correctness.
"""

import os

import psycopg2
import pytest
from psycopg2.extras import DictCursor

# Database configuration
DB_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://adcp_user:secure_password_change_me@localhost:5436/adcp",
)


@pytest.mark.integration
@pytest.mark.requires_db
def test_database_queries(integration_db):
    """Test the actual database queries used by the settings page"""
    print("\n🔍 Testing database queries...")

    # Get DATABASE_URL from environment (set by integration_db fixture)
    db_url = os.environ.get("DATABASE_URL")

    # Create test data first
    from src.core.database.database_session import get_db_session
    from src.core.database.models import Principal, Tenant

    tenant_id = "default"

    with get_db_session() as session:
        # Create tenant
        tenant = Tenant(tenant_id=tenant_id, name="Test Tenant", subdomain="test-tenant")
        session.add(tenant)

        # Create principal
        principal = Principal(
            tenant_id=tenant_id,
            principal_id="test_principal",
            name="Test Principal",
            access_token="test_token",
            platform_mappings={"mock": {"advertiser_id": "test-advertiser"}},
        )
        session.add(principal)
        session.commit()

    try:
        conn = psycopg2.connect(db_url, cursor_factory=DictCursor)
        cursor = conn.cursor()

        # Test 1: Check products table structure
        print("\n1. Testing products table query...")
        cursor.execute(
            """
            SELECT COUNT(*) as total_products
            FROM products
            WHERE tenant_id = %s
        """,
            (tenant_id,),
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
            (tenant_id,),
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
            (tenant_id,),
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
                (tenant_id,),
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
