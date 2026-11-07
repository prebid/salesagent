"""Revision 3c38b1ee241f converts legacy publisher selections to explicit selectors, or refuses.

A product used to select its publishers by the ``property_tags`` / ``property_ids``
columns, resolved against the tenant's authorized properties on every read. The revision
rewrites each such row into ``properties`` selectors and drops both columns (owner
decision, review of #2330): tags become ``by_tag`` per publisher carrying them, ids become
``by_id`` per publisher, an empty selection becomes ``all`` per publisher, counting
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

_REVISION = "3c38b1ee241f"
_PREVIOUS = "7f31c0ab94d2"
_TENANT = "mig_legacy_sel"


@pytest.fixture
def at_previous(migration_db):
    """The module-scoped database at the revision before this one, holding one empty tenant."""
    engine, db_url = migration_db
    run_alembic_upgrade(db_url, _PREVIOUS)
    run_alembic_downgrade(db_url, _PREVIOUS)
    with engine.begin() as conn:
        for table in ("pricing_options", "products", "authorized_properties", "accounts", "tenants"):
            conn.execute(text(f"DELETE FROM {table}"))
        # virtual_host and its derived name are NOT NULL from revision 7f31c0ab94d2.
        conn.execute(
            text(
                "INSERT INTO tenants (tenant_id, name, subdomain, virtual_host, virtual_host_name, ad_server, "
                "is_active) VALUES (:tid, 'Legacy selections', 'legacy-sel', :vhost, :vhost, 'mock', true)"
            ),
            {"tid": _TENANT, "vhost": vhost_for(_TENANT)},
        )
        for property_id, domain, tags, status in (
            ("news_home", "news.example", ["premium"], "verified"),
            ("sports_home", "Sports.Example", ["premium"], "verified"),
            ("weather_home", "weather.example", ["standard"], "verified"),
            ("podcast_home", "podcast.example", ["premium"], "pending"),
        ):
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
                    "tags": json.dumps(tags),
                    "domain": domain,
                    "status": status,
                },
            )
    return engine, db_url


def _seed_product(engine, product_id: str, *, property_tags=None, property_ids=None, properties=None) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO products (tenant_id, product_id, name, description, delivery_type, format_ids, "
                "targeting_template, delivery_measurement, properties, property_tags, property_ids) "
                "VALUES (:tid, :pid, :pid, 'legacy', 'guaranteed', '[]'::jsonb, '{}'::jsonb, "
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


def _properties(engine, product_id: str):
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT properties FROM products WHERE tenant_id = :tid AND product_id = :pid"),
            {"tid": _TENANT, "pid": product_id},
        ).scalar_one()


def test_every_legacy_selection_becomes_explicit_selectors_and_the_columns_go(at_previous):
    engine, db_url = at_previous
    _seed_product(engine, "by_tags", property_tags=["premium"])
    _seed_product(engine, "everything", property_tags=["all_inventory"])
    _seed_product(engine, "nothing_selected", property_tags=[])
    explicit = [{"publisher_domain": "news.example", "selection_type": "all"}]
    _seed_product(engine, "explicit", properties=explicit)

    run_alembic_upgrade(db_url, _REVISION)

    assert _properties(engine, "by_tags") == [
        {"publisher_domain": "news.example", "property_tags": ["premium"], "selection_type": "by_tag"},
        {"publisher_domain": "podcast.example", "property_tags": ["premium"], "selection_type": "by_tag"},
        {"publisher_domain": "sports.example", "property_tags": ["premium"], "selection_type": "by_tag"},
    ]
    assert _properties(engine, "everything") == [
        {"publisher_domain": domain, "property_tags": ["all_inventory"], "selection_type": "by_tag"}
        for domain in ("news.example", "podcast.example", "sports.example", "weather.example")
    ]
    assert _properties(engine, "nothing_selected") == [
        {"publisher_domain": domain, "selection_type": "all"}
        for domain in ("news.example", "podcast.example", "sports.example", "weather.example")
    ]
    assert _properties(engine, "explicit") == explicit

    columns = {column["name"]: column for column in sa.inspect(engine).get_columns("products")}
    assert "property_tags" not in columns and "property_ids" not in columns
    assert columns["properties"]["nullable"] is False
    constraints = {c["name"] for c in sa.inspect(engine).get_check_constraints("products")}
    assert not constraints & {"ck_product_properties_xor", "ck_product_authorization"}

    run_alembic_downgrade(db_url, _PREVIOUS)
    columns = {column["name"] for column in sa.inspect(engine).get_columns("products")}
    assert {"property_tags", "property_ids"} <= columns
    assert _properties(engine, "by_tags")[0]["publisher_domain"] == "news.example"


def test_a_legacy_selection_matching_no_authorized_property_stops_the_upgrade_named(at_previous):
    engine, db_url = at_previous
    _seed_product(engine, "converts", property_tags=["premium"])
    _seed_product(engine, "orphan_tag", property_tags=["podcast_only_tag"])
    _seed_product(engine, "orphan_empty_tenant_tag", property_tags=["standard", "podcast_only_tag"])

    with pytest.raises(RuntimeError) as refusal:
        run_alembic_upgrade(db_url, _REVISION)

    message = str(refusal.value)
    assert f"tenant={_TENANT} product=orphan_tag" in message
    assert "product=orphan_empty_tenant_tag" not in message  # "standard" matches weather_home
    assert "product=converts" not in message
    # One transaction: the convertible row was not rewritten and the columns remain.
    assert _properties(engine, "converts") is None
    columns = {column["name"] for column in sa.inspect(engine).get_columns("products")}
    assert {"property_tags", "property_ids"} <= columns
