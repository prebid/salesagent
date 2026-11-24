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

from typing import Any

import pytest
from adcp.types._generated import MediaChannel
from sqlalchemy import text

from src.core.media_channels import parse_media_channels
from tests.integration.migration_helpers import (
    reset_to_revision,
    row_xmins,
    run_alembic_downgrade,
    run_alembic_upgrade,
    seed_product,
    stored_column,
)

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

# Written red against "head" (then 7f31c0ab94d2, so every case failed on the stored
# value rather than on an unknown revision id); pinned now so a later revision cannot run
# inside this module.
_REVISION = "b9b8e637b56c"
_PREVIOUS = "9fdf18a56063"

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


def _seed(engine, product_id: str, channels: list[Any] | None) -> None:
    seed_product(engine, tenant_id=_TENANT, product_id=product_id, channels=channels)


def _stored(engine, product_id: str):
    return stored_column(engine, "products", "channels", tenant_id=_TENANT, product_id=product_id)


def _xmins(engine) -> dict[str, str]:
    return row_xmins(engine, table="products", key_column="product_id", tenant_id=_TENANT)


def _as_the_form_writes(*values: str) -> list[str] | None:
    """What saving the edit form with *values* selected stores: the column's rule."""
    return parse_media_channels(values)


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
        """Expected spelled from the SDK member, so a typo in the revision cannot be mirrored here.

        Exact order, the form's: saving a repaired product unchanged then writes the same array
        back. ``affiliate`` sorts before each target alphabetically and after it by enum
        position, so a repair that orders alphabetically fails here.
        """
        engine, db_url = at_previous
        _seed(engine, f"prod_{legacy}", ["affiliate", legacy])

        run_alembic_upgrade(db_url, _REVISION)

        assert _stored(engine, f"prod_{legacy}") == _as_the_form_writes(member.value, "affiliate")

    def test_a_non_string_element_is_left_as_it_is(self, at_previous):
        """Not cast to text and not dropped; it sorts after the members, so the array stays deterministic."""
        engine, db_url = at_previous
        _seed(engine, "prod_mixed", ["video", 1, None])

        run_alembic_upgrade(db_url, _REVISION)

        assert _stored(engine, "prod_mixed") == [MediaChannel.olv.value, None, 1]

    def test_native_is_dropped_and_the_other_channels_kept(self, at_previous):
        """``native`` is a format category, orthogonal to channels; exact equality also refuses native->social."""
        engine, db_url = at_previous
        _seed(engine, "prod_native", ["display", "native"])

        run_alembic_upgrade(db_url, _REVISION)

        assert _stored(engine, "prod_native") == ["display"]

    def test_a_row_holding_only_native_becomes_null(self, at_previous):
        """SQL NULL, not ``'[]'`` and not ``'null'::jsonb`` (``stored_column`` reads that as ``JSON_NULL``).

        NULL is what production stores for "no claim".
        """
        engine, db_url = at_previous
        _seed(engine, "prod_only_native", ["native"])

        run_alembic_upgrade(db_url, _REVISION)

        assert _stored(engine, "prod_only_native") is None

    def test_an_alias_beside_its_canonical_member_is_deduplicated(self, at_previous):
        engine, db_url = at_previous
        _seed(engine, "prod_dup", ["video", "olv"])

        run_alembic_upgrade(db_url, _REVISION)

        assert _stored(engine, "prod_dup") == [MediaChannel.olv.value]


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
        assert _stored(engine, "prod_valid") == ["display", "ctv"]


class TestTheDowngradeIsALoggedNoOp:
    def test_the_downgrade_keeps_remapped_rows_and_the_chain_stays_traversable(self, at_previous):
        """A remapped ``olv`` is indistinguishable from one that was always ``olv``; nothing is re-mangled."""
        engine, db_url = at_previous
        _seed(engine, "prod_down", ["video"])
        run_alembic_upgrade(db_url, _REVISION)

        run_alembic_downgrade(db_url, _PREVIOUS)

        assert _stored(engine, "prod_down") == [MediaChannel.olv.value]
        with engine.connect() as conn:
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == _PREVIOUS
