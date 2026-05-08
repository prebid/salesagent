"""add push_notification_configs signing_mode

Revision ID: 789f9d88265e
Revises: ee6fe59f5407
Create Date: 2026-05-07 10:18:26.114920

Slice 3 of the per-buyer-agent refactor. Adds
``push_notification_configs.signing_mode`` so a buyer can declare which
authentication method they require on their webhook endpoint:

* ``hmac`` — legacy HMAC-SHA256 with shared secret (default)
* ``rfc9421`` — RFC 9421 signed request (asymmetric, JWKS-published key)
* ``both`` — server signs with RFC 9421 *and* attaches the legacy HMAC
  header during the migration window

Default is ``hmac`` to preserve current behavior for existing rows. The
CHECK constraint pins the allowed values at the DB layer so a bad row
can't slip in via a future bug or hand-edit.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "789f9d88265e"
down_revision: str | Sequence[str] | None = "ee6fe59f5407"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Width 32 (not the 4-char minimum) leaves room for future variants
    # like ``rfc9421+mtls`` or versioned profiles without forcing a
    # widening migration on a populated table. Three valid values today;
    # the CHECK constraint below pins them.
    op.add_column(
        "push_notification_configs",
        sa.Column(
            "signing_mode",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'hmac'"),
        ),
    )
    op.create_check_constraint(
        "ck_push_notification_configs_signing_mode",
        "push_notification_configs",
        "signing_mode IN ('hmac', 'rfc9421', 'both')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_push_notification_configs_signing_mode",
        "push_notification_configs",
        type_="check",
    )
    op.drop_column("push_notification_configs", "signing_mode")
