"""Add tmp_providers table with auth fields for Trusted Match Protocol provider registrations

Revision ID: 2fa4c97166ab
Revises: e6bb3ee6ae13
Create Date: 2026-05-21 09:31:00.000000

Schema aligned with the pinned provider-registration schema — see
``src.routes.tmp_providers.PROVIDER_REGISTRATION_SCHEMA``, the one declaration
of that file's path (#1197 review):
  - status string (active/inactive/draining) instead of is_active boolean
  - countries (JSONB, conditional on identity_match)
  - uid_types (JSONB, conditional on identity_match)
  - properties (JSONB, optional property RIDs)
  - priority (integer, default 0)
  - auth_type (string) — nullable; constrained by the registration record to the
    schemes an outbound call actually implements (``VALID_AUTH_SCHEMES``, today
    just "bearer"), so the column accepts no others through any write surface
  - auth_credentials (text, stores token/key value) — nullable
  - health_status (string, written by background scheduler) — nullable
  - last_health_checked_at (datetime, written by background scheduler) — nullable

``provider_id`` is ``varchar(64)``, not ``uuid``: the pinned
provider-registration schema constrains it to ``^[A-Za-z0-9_]+$`` with
``maxLength: 64`` — a charset deliberately safe for logs, metrics, dashboards
and cache keys. A Postgres ``uuid`` column cannot satisfy that, because whatever
is written is rendered back in canonical hyphenated form and ``-`` is outside
the character class, so every entry the discovery endpoint returned would be
rejected by the schema it declares conformance to (#1197 review). The server
default is the hyphen-free rendering of ``gen_random_uuid()``; the ORM assigns
``uuid4().hex`` client-side.

TMP Provider sync always uses the standard Authorization: Bearer header,
so auth_header is intentionally omitted (unlike CreativeAgent/SignalsAgent).
Both auth columns are nullable — existing rows have no auth configured.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2fa4c97166ab"
down_revision: str | Sequence[str] | None = "e6bb3ee6ae13"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create tmp_providers table with auth fields aligned with provider-registration.json schema."""
    op.create_table(
        "tmp_providers",
        sa.Column(
            "provider_id",
            sa.String(length=64),
            server_default=sa.text("replace(gen_random_uuid()::text, '-', '')"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("endpoint", sa.String(length=500), nullable=False),
        sa.Column("context_match", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("identity_match", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("countries", postgresql.JSONB(), nullable=True),
        sa.Column("uid_types", postgresql.JSONB(), nullable=True),
        sa.Column("properties", postgresql.JSONB(), nullable=True),
        sa.Column("timeout_ms", sa.Integer(), nullable=False, server_default=sa.text("50")),
        sa.Column("priority", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
        sa.Column("auth_type", sa.String(length=50), nullable=True),
        sa.Column("auth_credentials", sa.Text(), nullable=True),
        sa.Column("health_status", sa.String(length=20), nullable=True),
        sa.Column("last_health_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("provider_id"),
    )

    # Create indexes
    op.create_index("idx_tmp_providers_tenant", "tmp_providers", ["tenant_id"])
    op.create_index("idx_tmp_providers_status", "tmp_providers", ["status"])


def downgrade() -> None:
    """Drop tmp_providers table."""
    op.drop_index("idx_tmp_providers_status", table_name="tmp_providers")
    op.drop_index("idx_tmp_providers_tenant", table_name="tmp_providers")
    op.drop_table("tmp_providers")
