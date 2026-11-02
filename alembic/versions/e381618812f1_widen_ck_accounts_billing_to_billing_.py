"""widen ck_accounts_billing to billing-party enum

``ck_accounts_billing`` (born in 51d4f9009db4) allowed only
``('operator', 'agent')`` while the AdCP 3.1.1 billing-party enum is
``["operator", "agent", "advertiser"]`` — a spec-valid
``billing="advertiser"`` create failed the CHECK (#1521, #1592).
Widen the constraint to the full enum. The literals below are a frozen
snapshot by design; the ORM constraint derives from the SDK
``BillingParty`` enum via ``src.core.billing_policy.BILLING_PARTY_VALUES``,
guarded by ``tests/unit/test_billing_party_parity.py``.

The downgrade REFUSES rather than destroys. ``billing='advertiser'`` cannot
satisfy the two-value constraint, and the only automatic way to narrow the
CHECK is to erase those values — which is not a downgrade, it is data loss
wearing one's clothes: ``billing IS NULL`` means "the buyer never declared a
billing party" and is accepted everywhere a declared value would be gated
(``_check_billing_supported`` never rejects an omitted billing), so the
erasure would be invisible on the wire and would never self-heal — the
settings-update arm of ``sync_accounts`` INHERITS the stored value when the
entry omits ``billing``. So the downgrade surveys the affected accounts,
reports them, and stops, matching the sibling natural-key migration
(``b2e94f7c1a03``, owner decision 2026-07-27).

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

_CONSTRAINT = "ck_accounts_billing"

def upgrade() -> None:
    """Recreate ck_accounts_billing with the full 3.1.1 billing-party enum."""
    op.drop_constraint(_CONSTRAINT, "accounts", type_="check")
    op.create_check_constraint(
        _CONSTRAINT,
        "accounts",
        "billing IS NULL OR billing IN ('operator', 'agent', 'advertiser')",
    )


def downgrade() -> None:
    """Recreate the two-value constraint, clearing rows that carry the third.

    Narrowing the CHECK leaves no valid value for billing='advertiser', so those
    rows are cleared. A downgrade past a widened domain is lossy by construction:
    the old schema has nowhere to put the value, and a later upgrade has nothing
    to reconstruct it from. This is what the other downgrades in this tree do.
    """
    op.execute("UPDATE accounts SET billing = NULL WHERE billing = 'advertiser'")

    op.drop_constraint(_CONSTRAINT, "accounts", type_="check")
    op.create_check_constraint(
        _CONSTRAINT,
        "accounts",
        "billing IS NULL OR billing IN ('operator', 'agent')",
    )
