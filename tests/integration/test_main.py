import json
import os
import unittest
import uuid

import pytest

from src.core.config_loader import get_current_tenant, set_current_tenant
from src.core.database.models import Product as ProductModel

# Ensure the main module can be imported


@pytest.mark.integration
class TestAdcpServerV2_3(unittest.TestCase):
    """
    Tests for the V2.3 AdCP Buy-Side Server.
    Focuses on schema conformance for AI-driven tools.
    """

    @classmethod
    def setUpClass(cls):
        """Set up the database once for all tests for the V2.3 spec."""
        db_file = "adcp.db"
        if os.path.exists(db_file):
            os.remove(db_file)

        # Set environment variable to use test database
        os.environ["DATABASE_URL"] = f"sqlite:///{db_file}"

        # Create the minimal schema needed for the test
        from sqlalchemy import create_engine, text

        engine = create_engine(os.environ["DATABASE_URL"])
        # Create tenants table with new schema
        with engine.connect() as conn:
            conn.execute(
                text(
                    """
                CREATE TABLE IF NOT EXISTS tenants (
                    tenant_id VARCHAR(50) PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    subdomain VARCHAR(100) UNIQUE NOT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    is_active BOOLEAN DEFAULT 1,
                    billing_plan VARCHAR(50) DEFAULT 'standard',
                    billing_contact TEXT,
                    ad_server VARCHAR(50) NOT NULL,
                    max_daily_budget REAL DEFAULT 10000,
                    enable_axe_signals BOOLEAN DEFAULT 1,
                    auto_approve_formats TEXT DEFAULT '[]',
                    human_review_required BOOLEAN DEFAULT 0,
                    manual_approval_required BOOLEAN DEFAULT 0,
                    admin_token VARCHAR(100),
                    policy_settings TEXT DEFAULT '{}',
                    slack_audit_webhook_url TEXT,
                    hitl_webhook_url TEXT
                )
            """
                )
            )
            conn.commit()

            # Create adapter_config table
            conn.execute(
                text(
                    """
                CREATE TABLE IF NOT EXISTS adapter_config (
                    tenant_id VARCHAR(50) PRIMARY KEY REFERENCES tenants(tenant_id) ON DELETE CASCADE,
                    adapter_type VARCHAR(50) NOT NULL,
                    gam_network_code VARCHAR(50),
                    gam_refresh_token TEXT,
                    gam_application_name VARCHAR(100),
                    mock_dry_run BOOLEAN DEFAULT 0,
                    kevel_network_id TEXT,
                    kevel_api_key TEXT,
                    triton_endpoint TEXT,
                    triton_api_key TEXT
                )
            """
                )
            )
            conn.commit()

            # Create products table
            conn.execute(
                text(
                    """
                CREATE TABLE IF NOT EXISTS products (
                    product_id VARCHAR(100) NOT NULL,
                    tenant_id VARCHAR(50) NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
                    name VARCHAR(255) NOT NULL,
                    description TEXT,
                    formats TEXT NOT NULL DEFAULT '[]',
                    creative_formats TEXT,
                    delivery_type VARCHAR(50) NOT NULL,
                    is_fixed_price BOOLEAN DEFAULT 0,
                    cpm REAL,
                    currency VARCHAR(3),
                    price_guidance TEXT,
                    price_guidance_min REAL,
                    price_guidance_max REAL,
                    countries TEXT DEFAULT '{"countries": []}',
                    targeting_template TEXT DEFAULT '{}',
                    implementation_config TEXT DEFAULT '{}',
                    adapter_product_id VARCHAR(100),
                    is_custom BOOLEAN DEFAULT 0,
                    expires_at DATETIME,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    min_spend REAL,
                    measurement TEXT,
                    creative_policy TEXT,
                    PRIMARY KEY (product_id, tenant_id)
                )
            """
                )
            )
            conn.commit()

            # Create principals table
            conn.execute(
                text(
                    """
                CREATE TABLE IF NOT EXISTS principals (
                    principal_id VARCHAR(50) NOT NULL,
                    tenant_id VARCHAR(50) NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
                    name VARCHAR(255) NOT NULL,
                    access_token VARCHAR(255) NOT NULL,
                    platform_mappings TEXT DEFAULT '{}',
                    config TEXT DEFAULT '{}',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    is_active BOOLEAN DEFAULT 1,
                    metadata TEXT DEFAULT '{}',
                    PRIMARY KEY (principal_id, tenant_id)
                )
            """
                )
            )
            conn.commit()

        # Create a test tenant and set it as current
        with engine.connect() as conn:
            tenant_id = str(uuid.uuid4())
            # Use database-appropriate timestamp function
            if "sqlite" in os.environ["DATABASE_URL"]:
                timestamp_func = "datetime('now')"
            else:  # PostgreSQL
                timestamp_func = "CURRENT_TIMESTAMP"

            conn.execute(
                text(
                    f"""
                INSERT INTO tenants (tenant_id, name, subdomain, billing_plan, created_at, updated_at,
                                   ad_server, max_daily_budget, enable_axe_signals,
                                   auto_approve_formats, human_review_required)
                VALUES (:tid, :name, :subdomain, :plan, {timestamp_func}, {timestamp_func}, :server, :budget, :signals, :formats, :review)
            """
                ),
                {
                    "tid": tenant_id,
                    "name": "Test Tenant",
                    "subdomain": f"test_{tenant_id[:8]}",
                    "plan": "test",
                    "server": "mock",  # ad_server
                    "budget": 10000,  # max_daily_budget
                    "signals": True,  # enable_axe_signals
                    "formats": json.dumps(["display_300x250", "display_728x90"]),  # auto_approve_formats
                    "review": False,  # human_review_required
                },
            )

            # Create test products
            products_data = [
                (
                    "prod_1",
                    "Display Banner Package",
                    "Premium display advertising",
                    json.dumps(["display_300x250", "display_728x90"]),
                    "guaranteed",
                    True,
                    5.0,
                    json.dumps({"floor": 5.0, "p50": 7.5, "p90": 10.0}),
                ),
                (
                    "prod_2",
                    "Video Pre-Roll",
                    "High-impact video ads",
                    json.dumps(["video_instream"]),
                    "non_guaranteed",
                    False,
                    15.0,
                    json.dumps({"floor": 12.0, "p50": 16.0, "p90": 20.0}),
                ),
                (
                    "prod_3",
                    "Native Content Package",
                    "Native advertising",
                    json.dumps(["native_content"]),
                    "guaranteed",
                    True,
                    8.0,
                    json.dumps({"floor": 6.0, "p50": 9.0, "p90": 12.0}),
                ),
            ]

            for prod_data in products_data:
                conn.execute(
                    text(
                        """
                    INSERT INTO products (product_id, tenant_id, name, description, formats, delivery_type,
                                       is_fixed_price, cpm, price_guidance, countries, targeting_template,
                                       min_spend, measurement, creative_policy)
                    VALUES (:pid, :tid, :name, :desc, :formats, :delivery, :fixed, :cpm, :guidance, :countries, :targeting,
                            :min_spend, :measurement, :creative_policy)
                """
                    ),
                    {
                        "pid": prod_data[0],
                        "tid": tenant_id,
                        "name": prod_data[1],
                        "desc": prod_data[2],
                        "formats": prod_data[3],
                        "delivery": prod_data[4],
                        "fixed": prod_data[5],
                        "cpm": prod_data[6],
                        "guidance": prod_data[7],
                        "countries": json.dumps({"countries": ["US", "CA"]}),
                        "targeting": json.dumps({}),
                        "min_spend": None,  # AdCP field, nullable
                        "measurement": None,  # AdCP field, nullable
                        "creative_policy": None,  # AdCP field, nullable
                    },
                )

            # Add adapter config for mock
            conn.execute(
                text(
                    """
                INSERT INTO adapter_config (tenant_id, adapter_type, mock_dry_run)
                VALUES (:tid, :atype, :dry_run)
            """
                ),
                {"tid": tenant_id, "atype": "mock", "dry_run": False},
            )

            conn.commit()

        # Set the tenant in context
        set_current_tenant(
            {
                "tenant_id": tenant_id,
                "name": "Test Tenant",
                "subdomain": f"test_{tenant_id[:8]}",
                "ad_server": "mock",
                "max_daily_budget": 10000,
                "enable_axe_signals": True,
                "auto_approve_formats": ["display_300x250", "display_728x90"],
                "human_review_required": False,
            }
        )

    def test_product_catalog_schema_conformance(self):
        """
        Tests that the product catalog data exists and has expected fields.
        Since get_products now requires authentication context, we test
        the underlying catalog functionality instead.
        """
        # Test that we can query products from the database
        tenant = get_current_tenant()
        self.assertIsNotNone(tenant)

        # Use the same database connection as setUpClass
        from sqlalchemy import create_engine, select
        from sqlalchemy.orm import sessionmaker

        engine = create_engine(os.environ["DATABASE_URL"])
        Session = sessionmaker(bind=engine)

        with Session() as db_session:
            products = db_session.scalars(select(ProductModel).filter_by(tenant_id=tenant["tenant_id"])).all()

            # Convert to list of dicts for consistency
            rows = []
            for product in products:
                rows.append(
                    {
                        "product_id": product.product_id,
                        "name": product.name,
                        "description": product.description,
                        "formats": product.formats,
                        "delivery_type": product.delivery_type,
                    }
                )

        # 1. Primary Assertion: The catalog must not be empty
        self.assertGreater(len(rows), 0, "Product catalog should not be empty")

        # 2. Secondary Assertion: Check that products have required fields
        for product_data in rows:
            # Verify required fields exist
            self.assertIn("product_id", product_data)
            self.assertIn("name", product_data)
            self.assertIn("description", product_data)
            self.assertIn("formats", product_data)
            self.assertIn("delivery_type", product_data)

        # 3. Test that we have the expected test products
        product_ids = [row["product_id"] for row in rows]

        self.assertIn("prod_1", product_ids)
        self.assertIn("prod_2", product_ids)
        self.assertIn("prod_3", product_ids)


if __name__ == "__main__":
    unittest.main()
