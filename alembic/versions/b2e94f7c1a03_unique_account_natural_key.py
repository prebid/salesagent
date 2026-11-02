"""enforce uniqueness of the account natural key

Revision ID: b2e94f7c1a03
Revises: d3f7a5c81e46
Create Date: 2026-07-27 20:40:00.000000

"""

from collections.abc import Sequence

from alembic import op
from src.core.database.migration_guards import abort_if_rows

# revision identifiers, used by Alembic.
revision: str = "b2e94f7c1a03"
down_revision: str | Sequence[str] | None = "d3f7a5c81e46"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_INDEX = "uq_accounts_natural_key"

#: The key exactly as every resolver reads it. ``COALESCE(sandbox, false)``
#: because ``get_by_natural_key`` filters ``sandbox IS NULL OR sandbox = false``
#: — NULL and false are one key — and NULLS NOT DISTINCT cannot express that
#: (it equates NULLs to each other, never to a non-NULL value).
_KEY_SQL = "tenant_id, operator, (brand ->> 'domain'), (brand ->> 'brand_id'), COALESCE(sandbox, false)"

#: An account with no brand domain has no natural key at all — the admin form
#: permits one, and no resolver can reach it since every lookup supplies a
#: domain. Partial for the same reason ``idx_media_buys_idempotency_key`` is
#: partial on ``idempotency_key IS NOT NULL``: constraining keyless rows would
#: forbid a legitimate shape while preventing no ambiguity.
_KEYED_ONLY = "(brand ->> 'domain') IS NOT NULL"

_SURVEY_SQL = f"""
    SELECT tenant_id,
           operator,
           (brand ->> 'domain')   AS brand_domain,
           (brand ->> 'brand_id') AS brand_id,
           COALESCE(sandbox, false) AS sandbox,
           array_agg(account_id ORDER BY created_at) AS account_ids
      FROM accounts
     WHERE {_KEYED_ONLY}
     GROUP BY {_KEY_SQL}
    HAVING count(*) > 1
"""


def upgrade() -> None:
    """Enforce (tenant, operator, brand.domain, brand.brand_id, sandbox) uniqueness.

    salesagent-8sfr made the natural-key components immutable so an account could
    not be RE-KEYED. This closes the other side: nothing stopped a second CREATE
    from landing on an occupied key, after which ``get_by_natural_key().first()``
    answers non-deterministically and ``list_by_natural_key`` reports the key
    unresolvable. ``AccountRepository.create`` now refuses a colliding key; this
    index is the invariant behind it, and the only thing that closes the
    check-then-insert race between two concurrent creates.

    Pre-existing collisions ABORT the migration rather than being remediated.
    Accounts are source-of-truth rows: the sibling index rebuild for
    ``idempotency_attempts`` (revision 7a8c3e1170a5) could simply DELETE its way
    out because that table is an explicitly TTL-bounded replay cache, and no
    equivalent move exists here. Choosing which duplicate survives is a decision
    about a tenant's live accounts, so this reports them and stops (owner
    decision, 2026-07-27). Resolve the extras — close or re-key them — and re-run.
    """
    abort_if_rows(
        op.get_bind(),
        _SURVEY_SQL,
        describe=lambda row: (
            f"  tenant={row.tenant_id!r} operator={row.operator!r} "
            f"brand.domain={row.brand_domain!r} brand.brand_id={row.brand_id!r} "
            f"sandbox={row.sandbox!r} -> accounts {list(row.account_ids)}"
        ),
        headline="Cannot enforce the account natural key: {count} colliding key(s) already exist.",
        remedy=(
            "Each group must collapse to ONE account before this index can be created. Close or "
            "re-key the extras (the buyer's account is the one their sync_accounts maintains — "
            "normally the oldest, and the one carrying an agent_account_access row), then re-run "
            "this migration. This migration will not choose for you: 'closed' is terminal in the "
            "status transitions, so a wrong choice is not reversible by the downgrade."
        ),
    )

    op.execute(
        f"CREATE UNIQUE INDEX {_INDEX} ON accounts ({_KEY_SQL}) NULLS NOT DISTINCT WHERE {_KEYED_ONLY}",
    )


def downgrade() -> None:
    """Drop the natural-key uniqueness index.

    Nothing to unwind beyond the index itself: the upgrade never mutated a row
    (it refuses to run over dirty data instead of remediating), so dropping the
    index restores the prior state exactly.
    """
    op.execute(f"DROP INDEX IF EXISTS {_INDEX}")
