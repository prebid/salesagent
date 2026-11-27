"""Revision a3a9e980762e records the booked format contract of each stored package that selected none.

The approval and the retroactive push hold a package to the contract it was booked on, read
off its row (``format_resolver.booked_format_contract``): its ``formats_to_provide``, or for a
package that selected no declaration, ``booked_format_contract``. Create writes the second
from this change on, and the revision writes it for the packages stored before, from each
product's contract at migration time (owner ruling 2026-11-27, #2582 F3). This module is the
revision's only grading: the integration suite builds its schema with
``Base.metadata.create_all``, so nothing else executes a migration's SQL.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from sqlalchemy import text

from tests.integration.migration_helpers import (
    reset_to_revision,
    run_alembic_downgrade,
    run_alembic_upgrade,
    seed_inventory_profile,
    seed_product,
    stored_column,
)

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_REVISION = "a3a9e980762e"
_PREVIOUS = "e36bcd73e4b0"
_TENANT = "mig_booked_tenant"
_MEDIA_BUY = "mb_booked"
_KEY = "booked_format_contract"

_MREC = {"format_kind": "image", "format_option_id": "mrec", "params": {"width": 300, "height": 250}}
_LEADERBOARD = {"format_kind": "image", "format_option_id": "leaderboard", "params": {"width": 728, "height": 90}}
_LEGACY = {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250_image"}


@pytest.fixture
def at_previous(migration_db):
    """The module-scoped database at the revision before this one, with one tenant, principal and media buy."""
    engine, db_url = reset_to_revision(
        migration_db,
        revision=_PREVIOUS,
        tenant_id=_TENANT,
        tenant_name="Migration booked contract",
        subdomain="mig-booked",
    )
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO principals (tenant_id, principal_id, name, platform_mappings, token_hash, token_prefix) "
                "VALUES (:tid, 'p_booked', 'Principal', '{}'::jsonb, 'hash_booked', 'pfx_booked')"
            ),
            {"tid": _TENANT},
        )
        conn.execute(
            text(
                "INSERT INTO media_buys (media_buy_id, tenant_id, principal_id, order_name, advertiser_name, "
                "start_date, end_date, status, raw_request) VALUES (:mbid, :tid, 'p_booked', 'Order', "
                "'Advertiser', DATE '2026-01-10', DATE '2026-01-20', 'pending_approval', '{}'::jsonb)"
            ),
            {"mbid": _MEDIA_BUY, "tid": _TENANT},
        )
    return engine, db_url


def _seed_package(engine, package_id: str, product_id: str, **config: Any) -> None:
    """A package row as create stored it before this revision: its product, and whatever else *config* says."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO media_packages (media_buy_id, package_id, package_config) "
                "VALUES (:mbid, :pid, CAST(:config AS jsonb))"
            ),
            {
                "mbid": _MEDIA_BUY,
                "pid": package_id,
                "config": json.dumps({"package_id": package_id, "product_id": product_id, **config}),
            },
        )


def _config(engine, package_id: str) -> dict[str, Any]:
    return stored_column(engine, "media_packages", "package_config", media_buy_id=_MEDIA_BUY, package_id=package_id)


class TestTheUpgradeRecordsTheProductContract:
    def test_a_canonical_product_records_its_declarations(self, at_previous):
        engine, db_url = at_previous
        seed_product(engine, tenant_id=_TENANT, product_id="prod_canonical", format_options=[_MREC])
        _seed_package(engine, "pkg_canonical", "prod_canonical")

        run_alembic_upgrade(db_url, _REVISION)

        assert _config(engine, "pkg_canonical")[_KEY] == {"format_options": [_MREC]}

    def test_a_legacy_only_product_records_its_format_ids(self, at_previous):
        engine, db_url = at_previous
        seed_product(engine, tenant_id=_TENANT, product_id="prod_legacy", format_ids=[_LEGACY], format_options=None)
        _seed_package(engine, "pkg_legacy", "prod_legacy")

        run_alembic_upgrade(db_url, _REVISION)

        assert _config(engine, "pkg_legacy")[_KEY] == {"format_ids": [_LEGACY]}

    def test_a_profile_linked_product_records_its_profiles_declarations(self, at_previous):
        engine, db_url = at_previous
        profile = seed_inventory_profile(
            engine, tenant_id=_TENANT, profile_id="prof_booked", format_options=[_LEADERBOARD]
        )
        seed_product(
            engine,
            tenant_id=_TENANT,
            product_id="prod_profiled",
            inventory_profile_id=profile,
            format_options=[_MREC],
        )
        _seed_package(engine, "pkg_profiled", "prod_profiled")

        run_alembic_upgrade(db_url, _REVISION)

        assert _config(engine, "pkg_profiled")[_KEY] == {"format_options": [_LEADERBOARD]}


class TestTheUpgradeLeavesOtherPackagesAlone:
    def test_a_package_that_selected_a_declaration_keeps_its_row(self, at_previous):
        engine, db_url = at_previous
        seed_product(engine, tenant_id=_TENANT, product_id="prod_selected", format_options=[_MREC])
        snapshot = {**_MREC, "product_id": "prod_selected"}
        _seed_package(engine, "pkg_selected", "prod_selected", formats_to_provide=[snapshot])
        before = _config(engine, "pkg_selected")

        run_alembic_upgrade(db_url, _REVISION)

        assert _config(engine, "pkg_selected") == before

    def test_a_package_whose_product_is_gone_keeps_its_row(self, at_previous):
        engine, db_url = at_previous
        _seed_package(engine, "pkg_orphan", "prod_deleted")
        before = _config(engine, "pkg_orphan")

        run_alembic_upgrade(db_url, _REVISION)

        assert _config(engine, "pkg_orphan") == before


class TestTheDowngradeRemovesTheRecord:
    def test_a_recorded_package_returns_to_what_was_stored(self, at_previous):
        engine, db_url = at_previous
        seed_product(engine, tenant_id=_TENANT, product_id="prod_canonical", format_options=[_MREC])
        _seed_package(engine, "pkg_canonical", "prod_canonical")
        before = _config(engine, "pkg_canonical")
        run_alembic_upgrade(db_url, _REVISION)
        assert _KEY in _config(engine, "pkg_canonical")

        run_alembic_downgrade(db_url, _PREVIOUS)

        assert _config(engine, "pkg_canonical") == before
