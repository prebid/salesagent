"""inventory profiles carry canonical format options

An inventory profile supplies the format contract of every product linked to it, and until
now it could only supply the deprecated half: ``inventory_profiles.format_ids`` was NOT NULL
and the table had no canonical column. ``core/product.json`` on ``format_ids``: "New products
MUST author canonical format_options[] ... Do not author a new product from format_ids
alone." A product authored through a profile could not satisfy that, because the profile had
nowhere to put a canonical declaration.

Two changes, both structural and both mirroring what the ``products`` table already has
(b41aded6b531 added ``products.format_options``; 08cd415b3d91 dropped the NOT NULL on
``products.format_ids``):

* ``format_options``, a nullable ``JSONType`` column holding the profile's canonical
  declarations as documents, the same shape ``products.format_options`` stores.
* ``format_ids`` drops NOT NULL, so a canonically-authored profile carries no legacy arm.

The ``inventory_profiles_format_ids_valid`` CHECK stays as it is. It calls the same
``validate_format_ids`` function the products CHECK calls, and 08cd415b3d91 measured that
function returning true for NULL while still refusing a malformed array.

STRUCTURE ONLY, both ways. No profile's ``format_options`` is stamped from its
``format_ids``: the projection runs v1 -> v2 only through a registry this migration must not
consult, and the migration rules forbid data rescue. The downgrade drops the column and
re-adds the NOT NULL, and it fails if a canonical-only profile exists by then; that is
accepted, as it is for 08cd415b3d91.

Revision ID: a34a2ff137da
Revises: b4f1a9c27d53
Create Date: 2026-10-28 13:39:09.148000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from src.core.database.json_type import JSONType

# revision identifiers, used by Alembic.
revision: str = "a34a2ff137da"
down_revision: str | Sequence[str] | None = "b4f1a9c27d53"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Give inventory profiles a canonical arm and make their legacy arm optional."""
    op.add_column("inventory_profiles", sa.Column("format_options", JSONType(), nullable=True))
    op.alter_column("inventory_profiles", "format_ids", nullable=True)


def downgrade() -> None:
    """Drop the canonical arm and require the legacy one again.

    Fails on a canonical-only profile, by design -- see the module docstring.
    """
    op.alter_column("inventory_profiles", "format_ids", nullable=False)
    op.drop_column("inventory_profiles", "format_options")
