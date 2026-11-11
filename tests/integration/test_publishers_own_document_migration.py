"""Revision e2af0c242a7f gives each publisher its stored adagents.json and moves selections onto foreign keys.

Partners become publishers (canonical domain, never fetched, no document); every domain an
authorized property or a product / profile selector named becomes a publisher too; the
selectors move into the child tables; ``authorized_properties``, ``publisher_partners``, the
legacy ``property_tags`` / ``property_ids`` columns and the JSON selector columns go.
``downgrade()`` restores the structure.

A product that selected its publishers by the legacy columns gets explicit selection rows
(owner decision, review of #2330): tags become ``by_tag`` per publisher carrying them, ids
become ``by_id`` per publisher, an empty selection becomes ``all`` per publisher, counting
authorized properties of any verification status. A row matching no authorized property
stops the upgrade, named, and nothing changes.

A deployed schema never held a ``property_ids`` row: ``ck_product_properties_xor`` wants
exactly one of ``properties`` / ``property_tags`` and ``ck_product_authorization`` exactly one
of the three, so an ids row satisfies neither. The ids conversion is graded on the wire by
``@T-UC-GET-PRODUCTS``/``@T-ADMIN-INVPROFILE`` scenarios that build a product "migrated from
legacy property ids" with the revision's own ``legacy_selectors``.

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
_PREVIOUS = "f9c29da38437"
_TENANT = "mig_publishers"

#: The four properties the legacy-selection tests convert against, as (id, domain, tags, status).
_LEGACY_AUTHORIZED = (
    ("news_home", "news-psatest.com", ["premium"], "verified"),
    ("sports_home", "Sports-Psatest.com", ["premium"], "verified"),
    ("weather_home", "weather-psatest.com", ["standard"], "verified"),
    ("podcast_home", "podcast-psatest.com", ["premium"], "pending"),
)
_LEGACY_DOMAINS = ("news-psatest.com", "podcast-psatest.com", "sports-psatest.com", "weather-psatest.com")


@pytest.fixture
def at_previous(migration_db):
    """The module-scoped database at the revision before this one, holding one empty tenant."""
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
        # virtual_host and its derived name are NOT NULL from revision 7f31c0ab94d2.
        conn.execute(
            text(
                "INSERT INTO tenants (tenant_id, name, subdomain, virtual_host, virtual_host_name, ad_server, "
                "is_active) VALUES (:tid, 'Publishers', 'mig-publishers', :vhost, :vhost, 'mock', true)"
            ),
            {"tid": _TENANT, "vhost": vhost_for(_TENANT)},
        )
    return engine, db_url


def _seed_authorized(engine, properties) -> None:
    """Store *properties*, each ``(property_id, publisher_domain, tags, verification_status)``."""
    with engine.begin() as conn:
        for property_id, domain, tags, status in properties:
            conn.execute(
                text(
                    "INSERT INTO authorized_properties (property_id, tenant_id, property_type, name, identifiers, "
                    "tags, publisher_domain, verification_status) VALUES (:pid, :tid, 'website', :pid, "
                    "CAST(:ident AS jsonb), CAST(:tags AS jsonb), :domain, :status)"
                ),
                {
                    "pid": property_id,
                    "tid": _TENANT,
                    "ident": json.dumps([{"type": "domain", "value": domain}]),
                    "tags": None if tags is None else json.dumps(tags),
                    "domain": domain,
                    "status": status,
                },
            )


def _seed_product(engine, product_id: str, *, property_tags=None, property_ids=None, properties=None) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO products (tenant_id, product_id, name, description, delivery_type, format_ids, "
                "targeting_template, delivery_measurement, properties, property_tags, property_ids) "
                "VALUES (:tid, :pid, :pid, 'x', 'guaranteed', '[]'::jsonb, '{}'::jsonb, "
                '\'{"provider": "publisher"}\'::jsonb, CAST(:props AS jsonb), CAST(:tags AS jsonb), '
                "CAST(:ids AS jsonb))"
            ),
            {
                "tid": _TENANT,
                "pid": product_id,
                "props": None if properties is None else json.dumps(properties),
                "tags": None if property_tags is None else json.dumps(property_tags),
                "ids": None if property_ids is None else json.dumps(property_ids),
            },
        )


def _rows(engine, sql: str, **params) -> list[tuple]:
    with engine.connect() as conn:
        return [tuple(row) for row in conn.execute(text(sql), {"tid": _TENANT, **params})]


def _product_selections(engine, product_id: str) -> list[tuple]:
    return _rows(
        engine,
        "SELECT publisher_domain, selection_type, property_ids, property_tags FROM product_publisher_selections "
        "WHERE tenant_id = :tid AND product_id = :pid ORDER BY id",
        pid=product_id,
    )


def test_partners_become_publishers_and_selectors_move_to_child_tables(at_previous):
    engine, db_url = at_previous
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO publisher_partners (tenant_id, publisher_domain, display_name, is_verified, sync_status) "
                "VALUES (:tid, 'News-Psatest.com', 'News', true, 'success')"
            ),
            {"tid": _TENANT},
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
    _seed_authorized(engine, [("typed_home", "typed-psatest.com", None, "verified")])
    _seed_product(
        engine,
        "sells",
        properties=[
            {"publisher_domain": "news-psatest.com", "selection_type": "all"},
            {"publisher_domain": "sports-psatest.com", "property_ids": ["a", "b"], "selection_type": "by_id"},
        ],
    )

    run_alembic_upgrade(db_url, _REVISION)

    assert _rows(
        engine, "SELECT domain, display_name, fetch_status, adagents, fetched_at FROM publishers ORDER BY domain"
    ) == [
        ("news-psatest.com", "News", "never_fetched", None, None),
        ("sports-psatest.com", "sports-psatest.com", "never_fetched", None, None),
        ("typed-psatest.com", "typed-psatest.com", "never_fetched", None, None),
        ("weather-psatest.com", "weather-psatest.com", "never_fetched", None, None),
    ]
    assert _product_selections(engine, "sells") == [
        ("news-psatest.com", "all", None, None),
        ("sports-psatest.com", "by_id", ["a", "b"], None),
    ]
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
    assert not {"properties", "property_tags", "property_ids"} & {c["name"] for c in inspector.get_columns("products")}
    assert not {c["name"] for c in inspector.get_check_constraints("products")} & {
        "ck_product_properties_xor",
        "ck_product_authorization",
    }
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
    assert {"properties", "property_tags", "property_ids"} <= {c["name"] for c in inspector.get_columns("products")}
    assert {"ck_product_properties_xor", "ck_product_authorization"} <= {
        c["name"] for c in inspector.get_check_constraints("products")
    }
    assert "publisher_properties" in {c["name"] for c in inspector.get_columns("inventory_profiles")}


def test_every_legacy_selection_becomes_explicit_selection_rows(at_previous):
    engine, db_url = at_previous
    _seed_authorized(engine, _LEGACY_AUTHORIZED)
    _seed_product(engine, "by_tags", property_tags=["premium"])
    _seed_product(engine, "everything", property_tags=["all_inventory"])
    _seed_product(engine, "nothing_selected", property_tags=[])
    _seed_product(engine, "explicit", properties=[{"publisher_domain": "news-psatest.com", "selection_type": "all"}])

    run_alembic_upgrade(db_url, _REVISION)

    assert _product_selections(engine, "by_tags") == [
        ("news-psatest.com", "by_tag", None, ["premium"]),
        ("podcast-psatest.com", "by_tag", None, ["premium"]),
        ("sports-psatest.com", "by_tag", None, ["premium"]),
    ]
    assert _product_selections(engine, "everything") == [
        (domain, "by_tag", None, ["all_inventory"]) for domain in _LEGACY_DOMAINS
    ]
    assert _product_selections(engine, "nothing_selected") == [
        (domain, "all", None, None) for domain in _LEGACY_DOMAINS
    ]
    assert _product_selections(engine, "explicit") == [("news-psatest.com", "all", None, None)]
    # Every domain a converted selection names is a publisher the rows reference.
    assert _rows(engine, "SELECT domain FROM publishers ORDER BY domain") == [(d,) for d in _LEGACY_DOMAINS]


def test_a_legacy_selection_matching_no_authorized_property_stops_the_upgrade_named(at_previous):
    engine, db_url = at_previous
    _seed_authorized(engine, _LEGACY_AUTHORIZED)
    _seed_product(engine, "converts", property_tags=["premium"])
    _seed_product(engine, "orphan_tag", property_tags=["podcast_only_tag"])
    _seed_product(engine, "orphan_empty_tenant_tag", property_tags=["standard", "podcast_only_tag"])

    with pytest.raises(RuntimeError) as refusal:
        run_alembic_upgrade(db_url, _REVISION)

    message = str(refusal.value)
    assert f"tenant={_TENANT} product=orphan_tag" in message
    assert "product=orphan_empty_tenant_tag" not in message  # "standard" matches weather_home
    assert "product=converts" not in message
    # Nothing changed: the convertible row keeps its legacy selection and no structure moved.
    assert _rows(
        engine, "SELECT properties, property_tags FROM products WHERE tenant_id = :tid AND product_id = 'converts'"
    ) == [(None, ["premium"])]
    inspector = sa.inspect(engine)
    assert {"property_tags", "property_ids", "properties"} <= {c["name"] for c in inspector.get_columns("products")}
    tables = set(inspector.get_table_names())
    assert "publishers" not in tables
    assert {"authorized_properties", "publisher_partners"} <= tables
