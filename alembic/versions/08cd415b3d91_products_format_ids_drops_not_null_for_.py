"""products.format_ids drops NOT NULL so a canonical-only product can exist

core/product.json makes a product's format declaration a root ``anyOf`` over TWO arms --
"Named-format product" (requires ``format_ids``, and marked ``deprecated: true``) and
"Canonical-format product" (requires ``format_options``) -- and satisfying either admits
the row, so neither column can be NOT NULL on the strength of the spec, and ``format_ids``
held one only because it predated ``format_options``. A canonically-authored product
declares ``format_options`` and nothing in the legacy arm, which the NOT NULL made
unrepresentable.

The permission to carry either arm is time-boxed, and it lives in the ``format_ids``
property description rather than at the root: "Deprecated in AdCP 3.2; removed in AdCP 4.0.
... Products MUST carry format_ids, format_options, or both during the 3.x migration
window. New products MUST author canonical format_options[] ... Do not author a new product
from format_ids alone." Dropping the NOT NULL is therefore what lets the CANONICAL arm
stand alone; it is not a statement that the two arms are interchangeable for a new product.

The ``products_format_ids_valid`` CHECK is deliberately left alone. It calls
``validate_format_ids(format_ids)``, which raises when ``jsonb_typeof(input) != 'array'``
and therefore reads as though a NULL would trip it. MEASURED against the real function
text in postgres:16, with both controls so the probe could return either answer --

    validate_format_ids(NULL)                                -> true    (no raise)
    validate_format_ids('[{"agent_url":"https://a","id":"x"}]') -> true    (valid passes)
    validate_format_ids('[{"id":"x"}]')                      -> RAISES  (bad still refused)

    the same CHECK on a NULLABLE column:
      INSERT NULL        -> accepted
      INSERT valid array -> accepted
      INSERT bad array   -> refused

-- NULL passes for two independent reasons, which is why the behaviour is not fragile:
``jsonb_typeof(NULL)`` is NULL so the IF never takes the raise branch and
``jsonb_array_elements(NULL)`` yields no rows, and separately a CHECK passes unless its
predicate is FALSE. So the constraint keeps enforcing structure on every non-NULL value
and needs no drop-and-recreate.

STRUCTURE ONLY, both ways, per the migration rules: no data is stamped, copied or
rescued. The downgrade re-adds the constraint and will FAIL if a canonical-only product
exists by then. That is accepted rather than worked around -- a downgrade reverts the
structure, and if data cannot survive that, the data does not survive. Adding a
conditional rewrite so the revert "always works" is precisely the backup-table pattern
the rules forbid.

Revision ID: 08cd415b3d91
Revises: c3f71a0d58e4
Create Date: 2026-10-09 21:25:28.136819

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "08cd415b3d91"
down_revision: str | Sequence[str] | None = "c3f71a0d58e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Let products.format_ids be NULL."""
    op.alter_column("products", "format_ids", nullable=True)


def downgrade() -> None:
    """Require products.format_ids again.

    Fails on a canonical-only product, by design -- see the module docstring.
    """
    op.alter_column("products", "format_ids", nullable=False)
