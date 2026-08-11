"""widen ck_accounts_billing to billing-party enum

``ck_accounts_billing`` (born in 51d4f9009db4) allowed only
``('operator', 'agent')`` while the AdCP 3.1.1 billing-party enum is
``["operator", "agent", "advertiser"]`` — a spec-valid
``billing="advertiser"`` create failed the CHECK (#1521, #1592).
Widen the constraint to the full enum. The literals below are a frozen
snapshot by design; the ORM constraint derives from the SDK
``BillingParty`` enum via ``src.core.billing_policy.BILLING_PARTY_VALUES``,
guarded by ``tests/unit/test_billing_party_parity.py``.

The downgrade is documented-lossy: rows with ``billing='advertiser'``
cannot satisfy the two-value constraint, so they are NULLed before the
narrow CHECK is recreated.

Revision ID: e381618812f1
Revises: 823974a5553e
Create Date: 2026-07-14 22:45:17.675465

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e381618812f1"
down_revision: str | Sequence[str] | None = "823974a5553e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Recreate ck_accounts_billing with the full 3.1.1 billing-party enum."""
    op.drop_constraint("ck_accounts_billing", "accounts", type_="check")
    op.create_check_constraint(
        "ck_accounts_billing",
        "accounts",
        "billing IS NULL OR billing IN ('operator', 'agent', 'advertiser')",
    )


def downgrade() -> None:
    """Recreate the two-value constraint (LOSSY: advertiser rows are NULLed)."""
    op.execute("UPDATE accounts SET billing = NULL WHERE billing = 'advertiser'")
    op.drop_constraint("ck_accounts_billing", "accounts", type_="check")
    op.create_check_constraint(
        "ck_accounts_billing",
        "accounts",
        "billing IS NULL OR billing IN ('operator', 'agent')",
    )
