"""add account billing_entity column

Adds ``accounts.billing_entity`` (JSONType, nullable) — the legal/billing entity
a ``sync_accounts`` entry may carry.

Permitted in BOTH entry modes ("sellers MAY accept refinements in
settings-update mode (e.g., updated bank details)") and echoed back on the
response account item. Before this column the field was accepted on the wire by
both request arms and then silently dropped by every application site — a quiet
failure with a success response.

Whole-object declarative replace, so a column rather than a table: no
cross-account query, no per-field lifecycle.

``bank`` IS persisted here (the seller needs it to bill) and stripped only on
the way out — the response documents bank details as write-only, and
``_scrub_business_entity`` in ``src/core/tools/accounts.py`` is the single place
a persisted entity becomes a response object.

No backfill: every existing account was created before the field could be
stored, which is exactly NULL.

Revision ID: d3f7a5c81e46
Revises: c4d8e1b73a52
Create Date: 2026-07-27 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from src.core.database.json_type import JSONType

# revision identifiers, used by Alembic.
revision: str = "d3f7a5c81e46"
down_revision: str | Sequence[str] | None = "c4d8e1b73a52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add accounts.billing_entity (JSONType, nullable, no backfill)."""
    op.add_column(
        "accounts",
        sa.Column(
            "billing_entity",
            JSONType,
            nullable=True,
            comment="Legal/billing entity echoed from the sync_accounts request; bank details are write-only",
        ),
    )


def downgrade() -> None:
    """Drop accounts.billing_entity."""
    op.drop_column("accounts", "billing_entity")
