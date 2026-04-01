"""create accounts and agent_account_access tables

Revision ID: 51d4f9009db4
Revises: aa2e905fe772
Create Date: 2026-03-19 00:21:42.350574

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from src.core.database.json_type import JSONType

# revision identifiers, used by Alembic.
revision: str = '51d4f9009db4'
down_revision: Union[str, Sequence[str], None] = 'aa2e905fe772'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create accounts and agent_account_access tables."""
    # --- accounts table ---
    op.create_table(
        "accounts",
        sa.Column("tenant_id", sa.String(50), sa.ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False),
        sa.Column("account_id", sa.String(100), nullable=False),
        # Required fields (AdCP spec)
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        # Optional fields (AdCP spec)
        sa.Column("advertiser", sa.String(255), nullable=True),
        sa.Column("billing_proxy", sa.String(255), nullable=True),
        sa.Column("operator", sa.String(255), nullable=True),
        sa.Column("billing", sa.String(20), nullable=True),
        sa.Column("rate_card", sa.String(255), nullable=True),
        sa.Column("payment_terms", sa.String(20), nullable=True),
        sa.Column("account_scope", sa.String(20), nullable=True),
        sa.Column("brand", JSONType, nullable=True),
        sa.Column("credit_limit", JSONType, nullable=True),
        sa.Column("setup", JSONType, nullable=True),
        sa.Column("governance_agents", JSONType, nullable=True),
        sa.Column("sandbox", sa.Boolean, nullable=True, server_default="false"),
        sa.Column("ext", JSONType, nullable=True),
        # Internal fields (not in AdCP spec)
        sa.Column("principal_id", sa.String(50), nullable=True),
        sa.Column("platform_mappings", JSONType, nullable=True),
        # Timestamps
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        # Composite primary key
        sa.PrimaryKeyConstraint("tenant_id", "account_id"),
        # Check constraints
        sa.CheckConstraint(
            "status IN ('active', 'pending_approval', 'rejected', 'payment_required', 'suspended', 'closed')",
            name="ck_accounts_status",
        ),
        sa.CheckConstraint(
            "billing IS NULL OR billing IN ('operator', 'agent')",
            name="ck_accounts_billing",
        ),
        sa.CheckConstraint(
            "payment_terms IS NULL OR payment_terms IN ('net_15', 'net_30', 'net_45', 'net_60', 'net_90', 'prepay')",
            name="ck_accounts_payment_terms",
        ),
        sa.CheckConstraint(
            "account_scope IS NULL OR account_scope IN ('operator', 'brand', 'operator_brand', 'agent')",
            name="ck_accounts_account_scope",
        ),
    )
    op.create_index("idx_accounts_tenant", "accounts", ["tenant_id"])
    op.create_index("idx_accounts_status", "accounts", ["status"])
    op.create_index("idx_accounts_operator", "accounts", ["operator"])

    # --- agent_account_access table ---
    op.create_table(
        "agent_account_access",
        sa.Column("tenant_id", sa.String(50), nullable=False),
        sa.Column("principal_id", sa.String(50), nullable=False),
        sa.Column("account_id", sa.String(100), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        # Composite primary key
        sa.PrimaryKeyConstraint("tenant_id", "principal_id", "account_id"),
        # Foreign keys
        sa.ForeignKeyConstraint(
            ["tenant_id", "principal_id"],
            ["principals.tenant_id", "principals.principal_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "account_id"],
            ["accounts.tenant_id", "accounts.account_id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index("idx_agent_account_access_account", "agent_account_access", ["tenant_id", "account_id"])


def downgrade() -> None:
    """Drop agent_account_access and accounts tables."""
    op.drop_index("idx_agent_account_access_account", "agent_account_access")
    op.drop_table("agent_account_access")
    op.drop_index("idx_accounts_operator", "accounts")
    op.drop_index("idx_accounts_status", "accounts")
    op.drop_index("idx_accounts_tenant", "accounts")
    op.drop_table("accounts")
