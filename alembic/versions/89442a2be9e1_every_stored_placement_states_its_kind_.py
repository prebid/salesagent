"""every stored placement states its kind and mode

``core/placement.json`` at the pin requires ``kind``, ``placement_id`` and ``mode`` of every
product placement, ``name`` of a ``seller_inline`` one and ``publisher_domain`` of a
``publisher_ref`` one. Placements stored before ``kind`` and ``mode`` existed carry neither,
and until now ``product_conversion`` supplied them every time a row was read: ``kind``
``seller_inline``, ``mode`` ``targetable``, and ``name`` taken from ``placement_id`` when the
row had none. That read-time defaulting is deleted with this revision, so the rows have to
state the values themselves.

UPGRADE, in three steps:

1. Every stored placement object gains the members it lacks, set to exactly what the wire
   served for it: ``kind`` ``seller_inline``, ``mode`` ``targetable``, and, for a
   ``seller_inline`` placement with no ``name`` (or an empty one), ``name`` equal to its
   ``placement_id``. A member the row already states is never overwritten, so
   ``get_products`` serves every product the same before and after.
2. Any row that still fails the CHECK stops the upgrade, named. That is a placement the read
   path already refused (no ``placement_id`` and no ``name``, an element that is not an
   object, a document that is not an array): there is no value the wire served to write, and
   inventing one is not this migration's decision.
3. ``ck_products_placements_explicit`` refuses every later write that omits one of them
   (``src.core.json_validators.PLACEMENTS_ARE_EXPLICIT_SQL``, which the ``Product`` model
   declares too).

DOWNGRADE drops the CHECK. The members step 1 wrote stay on the rows: the migration rules
revert the structure and nothing else, and the previous revision's read path accepts a row
that states them.

Revision ID: 89442a2be9e1
Revises: 69a5a533f157
Create Date: 2026-10-29 00:40:36.517074

"""

from collections.abc import Sequence

from alembic import op
from src.core.database.migration_guards import abort_if_rows
from src.core.json_validators import PLACEMENTS_ARE_EXPLICIT_CHECK, PLACEMENTS_ARE_EXPLICIT_SQL

# revision identifiers, used by Alembic.
revision: str = "89442a2be9e1"
down_revision: str | Sequence[str] | None = "69a5a533f157"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Step 1. ``defaults || p`` keeps every member ``p`` states; the trailing object adds
#: ``name`` only where the row served ``placement_id`` in its place. Element order is kept.
_STATE_KIND_AND_MODE_SQL = """
    UPDATE products
       SET placements = (
           SELECT jsonb_agg(
                      CASE WHEN jsonb_typeof(p) = 'object' THEN
                          jsonb_build_object('kind', 'seller_inline', 'mode', 'targetable')
                          || p
                          || CASE WHEN COALESCE(p ->> 'kind', 'seller_inline') = 'seller_inline'
                                   AND COALESCE(p ->> 'name', '') = ''
                                   AND COALESCE(p ->> 'placement_id', '') <> ''
                                  THEN jsonb_build_object('name', p -> 'placement_id')
                                  ELSE '{}'::jsonb
                             END
                      ELSE p
                      END
                      ORDER BY idx)
             FROM jsonb_array_elements(placements) WITH ORDINALITY AS element(p, idx))
     WHERE jsonb_typeof(placements) = 'array'
       AND placements <> '[]'::jsonb
"""

#: Step 2. The rows the CHECK would refuse, by the CHECK's own predicate.
_UNSTATEABLE_SQL = f"""
    SELECT tenant_id, product_id, placements::text AS placements
      FROM products
     WHERE NOT ({PLACEMENTS_ARE_EXPLICIT_SQL})
     ORDER BY tenant_id, product_id
"""


def upgrade() -> None:
    """State kind and mode on every stored placement, then refuse a placement without them."""
    bind = op.get_bind()
    op.execute(_STATE_KIND_AND_MODE_SQL)
    abort_if_rows(
        bind,
        _UNSTATEABLE_SQL,
        describe=lambda row: f"  tenant={row.tenant_id!r} product={row.product_id!r} placements={row.placements}",
        headline=(
            "Cannot state kind and mode on {count} product(s): their placements fail core/placement.json "
            "even after the values the wire served were written."
        ),
        remedy=(
            "get_products already refused these rows (a placement with neither placement_id nor name, an "
            "element that is not an object, or a document that is not an array), so there is no served value "
            "to write. Repair or remove each placement with direct SQL, then re-run this migration."
        ),
    )
    op.create_check_constraint(PLACEMENTS_ARE_EXPLICIT_CHECK, "products", PLACEMENTS_ARE_EXPLICIT_SQL)


def downgrade() -> None:
    """Drop the CHECK; the stated members stay on the rows."""
    op.drop_constraint(PLACEMENTS_ARE_EXPLICIT_CHECK, "products", type_="check")
