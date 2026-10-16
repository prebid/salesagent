"""The channel-repair revision remaps legacy picker values onto the ``MediaChannel`` enum.

Covers the alembic revision that repairs ``products.channels`` (GH #1532). The admin picker
once offered ``video``, ``audio``, ``retail`` and ``native``; none is a member of the AdCP
``MediaChannel`` enum, so a product tagged with one had the tag dropped on read and was
matched on adapter defaults instead. The template is fixed and the handlers now refuse a
non-member; the rows already written are this revision's job.

This module is the migration's ONLY grading. The integration suite builds its schema with
``Base.metadata.create_all``, so nothing else in the suite executes a revision's SQL.

Runs on ``migration_db`` (Alembic-built, module-scoped, randomized order), so every test
states its own starting revision through ``reset_to_revision``.
"""

from __future__ import annotations

import pytest
from adcp.types._generated import MediaChannel
from sqlalchemy import text

from tests.integration.migration_helpers import (
    reset_to_revision,
    row_xmins,
    run_alembic_downgrade,
    run_alembic_upgrade,
    seed_product,
)

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

# Written red against "head" (then 7f31c0ab94d2, so every case failed on the stored
# value rather than on an unknown revision id); pinned now so a later revision cannot run
# inside this module.
_REVISION = "b9b8e637b56c"
_PREVIOUS = "7f31c0ab94d2"

_TENANT = "mig_channels_tenant"


@pytest.fixture
def at_previous(migration_db):
    """The database one revision back, with one empty tenant."""
    return reset_to_revision(
        migration_db,
        revision=_PREVIOUS,
        tenant_id=_TENANT,
        tenant_name="Migration Channels",
        subdomain="mig-channels",
    )


def _seed(engine, product_id: str, channels: list[str] | None) -> None:
    seed_product(engine, tenant_id=_TENANT, product_id=product_id, channels=channels)


def _stored(engine, product_id: str):
    """``(channels, is_sql_null)`` -- psycopg2 reads SQL NULL and ``'null'::jsonb`` both as None."""
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT channels, channels IS NULL AS is_sql_null "
                "  FROM products WHERE tenant_id = :tid AND product_id = :pid"
            ),
            {"tid": _TENANT, "pid": product_id},
        ).one()


def _xmins(engine) -> dict[str, str]:
    return row_xmins(engine, table="products", key_column="product_id", tenant_id=_TENANT)


class TestLegacyPickerValuesAreRemapped:
    @pytest.mark.parametrize(
        ("legacy", "member"),
        [
            ("video", MediaChannel.olv),
            ("audio", MediaChannel.streaming_audio),
            ("retail", MediaChannel.retail_media),
        ],
    )
    def test_each_picker_alias_is_remapped_onto_its_enum_member(self, at_previous, legacy, member):
        """Expected spelled from the SDK member, so a typo in the revision cannot be mirrored here."""
        engine, db_url = at_previous
        _seed(engine, f"prod_{legacy}", ["display", legacy])

        run_alembic_upgrade(db_url, _REVISION)

        assert sorted(_stored(engine, f"prod_{legacy}").channels) == sorted(["display", member.value])

    def test_native_is_dropped_and_the_other_channels_kept(self, at_previous):
        """``native`` is a format category, orthogonal to channels; exact equality also refuses native->social."""
        engine, db_url = at_previous
        _seed(engine, "prod_native", ["display", "native"])

        run_alembic_upgrade(db_url, _REVISION)

        assert _stored(engine, "prod_native").channels == ["display"]

    def test_a_row_holding_only_native_becomes_null(self, at_previous):
        """SQL NULL, not ``'[]'`` and not ``'null'::jsonb``: NULL is what production stores for "no claim"."""
        engine, db_url = at_previous
        _seed(engine, "prod_only_native", ["native"])

        run_alembic_upgrade(db_url, _REVISION)

        row = _stored(engine, "prod_only_native")
        assert row.channels is None
        assert row.is_sql_null is True

    def test_an_alias_beside_its_canonical_member_is_deduplicated(self, at_previous):
        engine, db_url = at_previous
        _seed(engine, "prod_dup", ["video", "olv"])

        run_alembic_upgrade(db_url, _REVISION)

        assert _stored(engine, "prod_dup").channels == [MediaChannel.olv.value]


class TestOnlyAffectedRowsAreWritten:
    def test_only_rows_holding_a_legacy_value_are_rewritten(self, at_previous):
        """``xmin`` tells a targeted UPDATE apart from one that churns every row to itself."""
        engine, db_url = at_previous
        _seed(engine, "prod_valid", ["display", "ctv"])
        _seed(engine, "prod_null", None)
        _seed(engine, "prod_video", ["display", "video"])
        before = _xmins(engine)

        run_alembic_upgrade(db_url, _REVISION)

        after = _xmins(engine)
        assert after["prod_valid"] == before["prod_valid"]
        assert after["prod_null"] == before["prod_null"]
        assert after["prod_video"] != before["prod_video"], "the row the revision must repair was never written"
        assert _stored(engine, "prod_valid").channels == ["display", "ctv"]


class TestTheDowngradeIsALoggedNoOp:
    def test_the_downgrade_keeps_remapped_rows_and_the_chain_stays_traversable(self, at_previous):
        """A remapped ``olv`` is indistinguishable from one that was always ``olv``; nothing is re-mangled."""
        engine, db_url = at_previous
        _seed(engine, "prod_down", ["video"])
        run_alembic_upgrade(db_url, _REVISION)

        run_alembic_downgrade(db_url, _PREVIOUS)

        assert _stored(engine, "prod_down").channels == [MediaChannel.olv.value]
        with engine.connect() as conn:
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == _PREVIOUS
