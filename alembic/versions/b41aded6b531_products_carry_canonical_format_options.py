"""products carry canonical format options

``core/product.json``'s root ``anyOf`` has TWO arms: "Named-format product" (requires
``format_ids``, and marked ``deprecated: true``) and "Canonical-format product" (requires
``format_options``). This seller carried only the deprecated half -- ``format_options`` had zero
references under ``src/`` -- so a product could describe itself to a 3.x peer and not at all to a
canonical one.

One column, because one declaration list is one value. ``core/product-format-declaration.json``
requires ``format_kind`` and ``params`` per entry and admits fifteen more members, and the
generated type for it is a sixteen-branch discriminated union over ``format_kind``; a document
is the only faithful way to store that, and ``JSONType`` serializes the models the DTO already
declares without a second representation in between.

Nullable, and ``format_ids`` stays NOT NULL. The spec's ``anyOf`` is satisfied by either half,
so a column-level NOT NULL on both would be stricter than the protocol -- it would refuse the
``format_ids``-only product the pin still admits for the 3.x migration window (the ``format_ids``
description scopes it exactly: "Deprecated in AdCP 3.2; removed in AdCP 4.0"). Which half a
product carries is a property of that product, not of the schema.

``v1_format_ref`` rides inside each declaration rather than beside it. It is what makes serving
a legacy peer a LOOKUP instead of a synthesis: the one-way projection rule forbids inventing a
v1 ``format_id`` by inverting a structural match, so a seller that wants a legacy projection
must assert one, and ``canonical_formats_only: true`` is the explicit way to say there is none.

Revision ID: b41aded6b531
Revises: 346edbd6f12b
Create Date: 2026-10-07 23:59:19.469019

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

from src.core.database.json_type import JSONType

# revision identifiers, used by Alembic.
revision: str = "b41aded6b531"
down_revision: str | Sequence[str] | None = "346edbd6f12b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the canonical half of the product's format declaration."""
    op.add_column("products", sa.Column("format_options", JSONType(), nullable=True))


def downgrade() -> None:
    """Drop it, leaving the deprecated half the table has always had."""
    op.drop_column("products", "format_options")
