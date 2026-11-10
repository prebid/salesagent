"""Revision e2af0c242a7f gives each publisher its stored adagents.json and moves selections onto foreign keys.

Partners become publishers (canonical domain, never fetched, no document); every domain an
authorized property or a product / profile selector named becomes a publisher too; the
selectors move into the child tables; ``authorized_properties``, ``publisher_partners`` and the
two JSON columns go. ``downgrade()`` restores the structure.

This module is the revision's grading: the integration schema is built with
``Base.metadata.create_all``, so nothing else executes the migration's SQL.
"""

from __future__ import annotations

import json

import pytest
import sqlalchemy as sa
from sqlalchemy import text

from tests.integration.migration_helpers import run_alembic_downgrade, run_alembic_upgrade
from tests.utils.database_helpers import vhost_for

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_REVISION = "e2af0c242a7f"
_PREVIOUS = "3c38b1ee241f"
_TENANT = "mig_publishers"


@pytest.fixture
def at_previous(migration_db):
    """The module-scoped database at the revision before this one, holding one tenant's publisher data."""
    engine, db_url = migration_db
    run_alembic_upgrade(db_url, _PREVIOUS)
    run_alembic_downgrade(db_url, _PREVIOUS)
    with engine.begin() as conn:
        for table in (
            "pricing_options",
            "products",
            "inventory_profiles",
            "authorized_properties",
            "publisher_partners",
            "accounts",
            "tenants",
        ):
            conn.execute(text(f"DELETE FROM {table}"))
        conn.execute(
            text(
                "INSERT INTO tenants (tenant_id, name, subdomain, virtual_host, virtual_host_name, ad_server, "
                "is_active) VALUES (:tid, 'Publishers', 'mig-publishers', :vhost, :vhost, 'mock', true)"
            ),
            {"tid": _TENANT, "vhost": vhost_for(_TENANT)},
        )
        conn.execute(
            text(
                "INSERT INTO publisher_partners (tenant_id, publisher_domain, display_name, is_verified, sync_status) "
                "VALUES (:tid, 'News-Psatest.com', 'News', true, 'success')"
            ),
            {"tid": _TENANT},
        )
        conn.execute(
            text(
                "INSERT INTO authorized_properties (property_id, tenant_id, property_type, name, identifiers, "
                "publisher_domain, verification_status) VALUES ('typed_home', :tid, 'website', 'Typed', "
                "CAST(:ident AS jsonb), 'typed.example', 'verified')"
            ),
            {"tid": _TENANT, "ident": json.dumps([{"type": "domain", "value": "typed.example"}])},
        )
        conn.execute(
            text(
                "INSERT INTO products (tenant_id, product_id, name, description, delivery_type, format_ids, "
                "targeting_template, delivery_measurement, properties) VALUES (:tid, 'sells', 'Sells', 'x', "
                "'guaranteed', '[]'::jsonb, '{}'::jsonb, '{\"provider\": \"publisher\"}'::jsonb, CAST(:props AS jsonb))"
            ),
            {
                "tid": _TENANT,
                "props": json.dumps(
                    [
                        {"publisher_domain": "news-psatest.com", "selection_type": "all"},
                        {
                            "publisher_domain": "sports-psatest.com",
                            "property_ids": ["a", "b"],
                            "selection_type": "by_id",
                        },
                    ]
                ),
            },
        )
        conn.execute(
            text(
                "INSERT INTO inventory_profiles (tenant_id, profile_id, name, inventory_config, publisher_properties, "
                "created_at, updated_at) VALUES (:tid, 'profile', 'Profile', '{}'::jsonb, CAST(:props AS jsonb), "
                "now(), now())"
            ),
            # A profile's JSON mode stored selectors without selection_type.
            {
                "tid": _TENANT,
                "props": json.dumps([{"publisher_domain": "weather-psatest.com", "property_tags": ["x"]}]),
            },
        )
    return engine, db_url


def _rows(engine, sql: str) -> list[tuple]:
    with engine.connect() as conn:
        return [tuple(row) for row in conn.execute(text(sql), {"tid": _TENANT})]


def test_partners_become_publishers_and_selectors_move_to_child_tables(at_previous):
    engine, db_url = at_previous

    run_alembic_upgrade(db_url, _REVISION)

    assert _rows(
        engine, "SELECT domain, display_name, fetch_status, adagents, fetched_at FROM publishers ORDER BY domain"
    ) == [
        ("news-psatest.com", "News", "never_fetched", None, None),
        ("sports-psatest.com", "sports-psatest.com", "never_fetched", None, None),
        ("typed.example", "typed.example", "never_fetched", None, None),
        ("weather-psatest.com", "weather-psatest.com", "never_fetched", None, None),
    ]
    assert _rows(
        engine,
        "SELECT product_id, publisher_domain, selection_type, property_ids, property_tags "
        "FROM product_publisher_selections ORDER BY id",
    ) == [("sells", "news-psatest.com", "all", None, None), ("sells", "sports-psatest.com", "by_id", ["a", "b"], None)]
    assert _rows(
        engine,
        "SELECT publisher_domain, selection_type, property_ids, property_tags "
        "FROM inventory_profile_publisher_selections ORDER BY id",
    ) == [("weather-psatest.com", "by_tag", None, ["x"])]
    # Properties come only from a publisher's file: none survive the upgrade.
    assert _rows(engine, "SELECT count(*) FROM publisher_properties") == [(0,)]

    inspector = sa.inspect(engine)
    tables = set(inspector.get_table_names())
    assert not tables & {"authorized_properties", "publisher_partners"}
    assert "properties" not in {c["name"] for c in inspector.get_columns("products")}
    assert "publisher_properties" not in {c["name"] for c in inspector.get_columns("inventory_profiles")}
    selection_fks = inspector.get_foreign_keys("product_publisher_selections")
    assert {"referred_table": "publishers", "referred_columns": ["tenant_id", "domain"]} in [
        {"referred_table": fk["referred_table"], "referred_columns": fk["referred_columns"]} for fk in selection_fks
    ]

    run_alembic_downgrade(db_url, _PREVIOUS)

    inspector = sa.inspect(engine)
    tables = set(inspector.get_table_names())
    assert {"authorized_properties", "publisher_partners"} <= tables
    assert not tables & {
        "publishers",
        "publisher_properties",
        "publisher_format_options",
        "product_publisher_selections",
        "inventory_profile_publisher_selections",
    }
    assert "properties" in {c["name"] for c in inspector.get_columns("products")}
    assert "publisher_properties" in {c["name"] for c in inspector.get_columns("inventory_profiles")}
