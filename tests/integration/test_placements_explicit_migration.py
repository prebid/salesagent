"""Revision 89442a2be9e1 writes on every stored placement the kind and mode the wire served.

Before it, ``product_conversion`` filled a placement's missing members each time the row was
read: ``kind`` ``seller_inline``, ``mode`` ``targetable``, and ``name`` from
``placement_id`` for a seller_inline placement with none. The revision writes exactly those
values into the rows, so ``get_products`` serves the same document before and after, and
then adds ``ck_products_placements_explicit`` so no later write can omit them.

Runs against a MIGRATED database (``migration_db``): the ordinary integration fixtures build
the schema with ``Base.metadata.create_all``, which declares the CHECK whether or not this
revision ever runs.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tests.integration.migration_helpers import reset_to_revision, run_alembic_downgrade, run_alembic_upgrade

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_REVISION = "89442a2be9e1"
_PREVIOUS = "69a5a533f157"
_TENANT = "mig_placements"
_CHECK = "ck_products_placements_explicit"


def _at(migration_db, revision: str):
    return reset_to_revision(
        migration_db, revision=revision, tenant_id=_TENANT, tenant_name="Placements", subdomain="mig-placements"
    )


def _insert_product(engine, product_id: str, placements: Any) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO products (tenant_id, product_id, name, description, delivery_type, "
                "format_ids, targeting_template, property_tags, placements) "
                "VALUES (:tid, :pid, :pid, 'test', 'guaranteed', '[]'::jsonb, '{}'::jsonb, "
                "'[\"all_inventory\"]'::jsonb, CAST(:placements AS jsonb))"
            ),
            {"tid": _TENANT, "pid": product_id, "placements": None if placements is None else json.dumps(placements)},
        )


def _placements(engine, product_id: str) -> Any:
    with engine.begin() as conn:
        return conn.execute(
            text("SELECT placements FROM products WHERE product_id = :pid"), {"pid": product_id}
        ).scalar_one()


def _has_check(engine) -> bool:
    with engine.begin() as conn:
        return (
            conn.execute(
                text("SELECT count(*) FROM pg_constraint WHERE conname = :name"), {"name": _CHECK}
            ).scalar_one()
            == 1
        )


#: (stored before, stored after) -- "after" is what get_products served for "before".
_BACKFILL = {
    "legacy": (
        [{"placement_id": "homepage_atf", "name": "Homepage"}],
        [{"kind": "seller_inline", "mode": "targetable", "placement_id": "homepage_atf", "name": "Homepage"}],
    ),
    "nameless": (
        [{"placement_id": "sidebar_btf"}],
        [{"kind": "seller_inline", "mode": "targetable", "placement_id": "sidebar_btf", "name": "sidebar_btf"}],
    ),
    "blank_name": (
        [{"placement_id": "footer", "name": ""}],
        [{"kind": "seller_inline", "mode": "targetable", "placement_id": "footer", "name": "footer"}],
    ),
    "explicit": (
        [{"kind": "seller_inline", "mode": "included", "placement_id": "hero", "name": "Hero"}],
        [{"kind": "seller_inline", "mode": "included", "placement_id": "hero", "name": "Hero"}],
    ),
    "publisher_ref_without_mode": (
        [{"kind": "publisher_ref", "placement_id": "pub", "publisher_domain": "publisher.example"}],
        [
            {
                "kind": "publisher_ref",
                "mode": "targetable",
                "placement_id": "pub",
                "publisher_domain": "publisher.example",
            }
        ],
    ),
    "order_and_other_members": (
        [
            {
                "placement_id": "b",
                "name": "B",
                "description": "second",
                "format_ids": [{"agent_url": "https://a", "id": "x"}],
            },
            {"placement_id": "a", "name": "A", "mode": "included"},
        ],
        [
            {
                "kind": "seller_inline",
                "mode": "targetable",
                "placement_id": "b",
                "name": "B",
                "description": "second",
                "format_ids": [{"agent_url": "https://a", "id": "x"}],
            },
            {"kind": "seller_inline", "mode": "included", "placement_id": "a", "name": "A"},
        ],
    ),
    "empty": ([], []),
    "null": (None, None),
}


def test_the_upgrade_writes_what_the_wire_served(migration_db):
    """Every stored placement gains only the members it lacked, set to the served value."""
    engine, db_url = _at(migration_db, _PREVIOUS)
    for product_id, (before, _) in _BACKFILL.items():
        _insert_product(engine, product_id, before)

    run_alembic_upgrade(db_url, _REVISION)

    stored = {product_id: _placements(engine, product_id) for product_id in _BACKFILL}
    assert stored == {product_id: after for product_id, (_, after) in _BACKFILL.items()}
    assert _has_check(engine), "the upgrade completed without adding the CHECK"


@pytest.mark.parametrize(
    ("placements", "why"),
    [
        ([{"description": "neither placement_id nor name"}], "an unidentifiable placement"),
        (["homepage_atf"], "an element that is not an object"),
        ({"placement_id": "p"}, "a document that is not an array"),
    ],
)
def test_the_upgrade_refuses_and_names_a_row_it_cannot_state(migration_db, placements, why):
    """A placement with no served value to write stops the upgrade, naming the product."""
    engine, db_url = _at(migration_db, _PREVIOUS)
    _insert_product(engine, "unstateable", placements)

    with pytest.raises(RuntimeError) as raised:
        run_alembic_upgrade(db_url, _REVISION)

    assert "unstateable" in str(raised.value), f"the refusal of {why} did not name the product: {raised.value}"
    assert not _has_check(engine), f"the CHECK was added despite {why}"
    assert _placements(engine, "unstateable") == placements, f"the refused row holding {why} was rewritten"


@pytest.mark.parametrize(
    ("placement", "refused"),
    [
        ({"kind": "seller_inline", "placement_id": "p", "name": "P", "mode": "targetable"}, False),
        ({"kind": "publisher_ref", "placement_id": "p", "publisher_domain": "pub.example", "mode": "included"}, False),
        ({"placement_id": "p", "name": "P", "mode": "targetable"}, True),
        ({"kind": "seller_inline", "placement_id": "p", "name": "P"}, True),
        ({"kind": "seller_inline", "name": "P", "mode": "targetable"}, True),
        ({"kind": "seller_inline", "placement_id": "p", "mode": "targetable"}, True),
        ({"kind": "publisher_ref", "placement_id": "p", "mode": "targetable"}, True),
    ],
)
def test_after_the_upgrade_a_write_must_state_every_required_member(migration_db, placement, refused):
    """The CHECK refuses a placement missing kind, placement_id, mode, or its kind's identity."""
    engine, db_url = _at(migration_db, _PREVIOUS)
    run_alembic_upgrade(db_url, _REVISION)

    if refused:
        with pytest.raises(IntegrityError, match=_CHECK):
            _insert_product(engine, "written", [placement])
    else:
        _insert_product(engine, "written", [placement])
        assert _placements(engine, "written") == [placement]


def test_the_downgrade_drops_the_check_and_keeps_the_rows(migration_db):
    """The structure reverts; the members the upgrade wrote stay on the rows."""
    engine, db_url = _at(migration_db, _PREVIOUS)
    before, after = _BACKFILL["legacy"]
    _insert_product(engine, "legacy", before)
    run_alembic_upgrade(db_url, _REVISION)

    run_alembic_downgrade(db_url, _PREVIOUS)

    assert not _has_check(engine), "the downgrade left the CHECK in place"
    assert _placements(engine, "legacy") == after
    _insert_product(engine, "bare", [{"placement_id": "bare", "name": "Bare"}])
