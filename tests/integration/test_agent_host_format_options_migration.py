"""Revision f9c29da38437 makes every format option under the agent host product-local, and drops the tenant catalog.

``core/product-format-declaration.json`` ``publisher_domain`` (v3.2.7) namespaces an option
"from that publisher's adagents.json top-level formats[]", and this agent publishes no
``formats[]``. So a stored declaration naming its tenant's agent host loses
``publisher_domain`` and keeps its ``format_option_id``, on the product and on the inventory
profile alike, and ``tenants.publisher_format_options`` goes. A document whose rewrite would
repeat a ``format_option_id`` is left as stored.

Runs against a MIGRATED database (``migration_db``), at the revision before this one, with
rows inserted as that schema stores them.
"""

from __future__ import annotations

import json

import pytest
import sqlalchemy as sa
from sqlalchemy import text

from src.core.http_utils import hostname_of
from tests.integration.migration_helpers import (
    reset_to_revision,
    run_alembic_downgrade,
    run_alembic_upgrade,
    seed_inventory_profile,
    stored_format_options,
)
from tests.utils.database_helpers import vhost_for

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_REVISION = "f9c29da38437"
_PREVIOUS = "f7c2e8a13d64"
_TENANT = "mig_agent_host_fmt"
_HOST = hostname_of(vhost_for(_TENANT))

_LOCAL = {"format_kind": "image", "format_option_id": "homepage_mrec", "params": {"width": 300, "height": 250}}
_UNDER_HOST = {**_LOCAL, "publisher_domain": _HOST}
_UNDER_PUBLISHER = {**_LOCAL, "publisher_domain": "news.example"}
_PROPERTIES = [{"publisher_domain": "news.example", "selection_type": "all"}]


def _insert_product(engine, product_id: str, format_options: list[dict]) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO products (tenant_id, product_id, name, description, delivery_type, targeting_template, "
                "delivery_measurement, format_options, properties) VALUES (:tid, :pid, :pid, 'test', 'guaranteed', "
                "'{}'::jsonb, '{\"provider\": \"publisher\"}'::jsonb, CAST(:options AS jsonb), CAST(:properties AS jsonb))"
            ),
            {
                "tid": _TENANT,
                "pid": product_id,
                "options": json.dumps(format_options),
                "properties": json.dumps(_PROPERTIES),
            },
        )


_options = stored_format_options


def _tenant_columns(engine) -> set[str]:
    return {column["name"] for column in sa.inspect(engine).get_columns("tenants")}


def test_agent_host_options_become_product_local_and_the_catalog_column_goes(migration_db):
    engine, db_url = reset_to_revision(
        migration_db, revision=_PREVIOUS, tenant_id=_TENANT, tenant_name="Agent host formats", subdomain="mig-fmt"
    )
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE tenants SET publisher_format_options = CAST(:catalog AS jsonb) WHERE tenant_id = :tid"),
            {"tid": _TENANT, "catalog": json.dumps([_LOCAL])},
        )
    second = {**_LOCAL, "format_option_id": "leaderboard", "params": {"width": 728, "height": 90}}
    _insert_product(engine, "under_host", [_UNDER_HOST, second])
    _insert_product(engine, "under_publisher", [_UNDER_PUBLISHER])
    _insert_product(engine, "would_collide", [_UNDER_HOST, _LOCAL])
    profile = seed_inventory_profile(
        engine,
        tenant_id=_TENANT,
        profile_id="profile_under_host",
        format_options=[_UNDER_HOST],
        publisher_properties=_PROPERTIES,
    )

    run_alembic_upgrade(db_url, _REVISION)

    assert _options(engine, "products", "product_id", "under_host") == [_LOCAL, second]
    assert _options(engine, "products", "product_id", "under_publisher") == [_UNDER_PUBLISHER]
    assert _options(engine, "products", "product_id", "would_collide") == [_UNDER_HOST, _LOCAL]
    assert _options(engine, "inventory_profiles", "id", profile) == [_LOCAL]
    assert "publisher_format_options" not in _tenant_columns(engine)

    run_alembic_downgrade(db_url, _PREVIOUS)

    assert "publisher_format_options" in _tenant_columns(engine)
    assert _options(engine, "products", "product_id", "under_host") == [_LOCAL, second]
