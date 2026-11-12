"""Reserve scheduler media-buy delivery sequences (#1636).

The delivery_report sender fans out one sequence to multiple endpoints and is
excluded until #1642. Historical scheduler duplicates require explicit operator
resolution; this migration never deletes or renumbers delivery evidence.

AdCP 3.2.7 optimization-reporting.mdx: sequence numbers start at 1, while arrival
order is best effort. This local storage prerequisite is ungraded; #1638 owns
the HTTP delivery and retry integration.

Revision ID: b74fd8c260ae
Revises: e2af0c242a7f
Create Date: 2026-11-11 15:24:03.832034

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from src.core.database.migration_guards import abort_if_rows

# revision identifiers, used by Alembic.
revision: str = "b74fd8c260ae"
down_revision: str | Sequence[str] | None = "e2af0c242a7f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Survey under the same lock that protects installing the unique index."""
    bind = op.get_bind()
    bind.execute(sa.text("LOCK TABLE webhook_delivery_log IN ACCESS EXCLUSIVE MODE"))
    abort_if_rows(
        bind,
        """
        SELECT tenant_id, media_buy_id, task_type, sequence_number,
               array_agg(id ORDER BY id) AS log_ids
          FROM webhook_delivery_log
         WHERE task_type = 'media_buy_delivery'
         GROUP BY tenant_id, media_buy_id, task_type, sequence_number
        HAVING count(*) > 1
         ORDER BY tenant_id, media_buy_id, sequence_number
        """,
        describe=lambda row: (
            f"tenant={row.tenant_id!r} media_buy={row.media_buy_id!r} task_type={row.task_type!r} "
            f"sequence={row.sequence_number} logs={row.log_ids!r}"
        ),
        headline="Cannot reserve scheduler sequences: {count} duplicate stream sequence(s).",
        remedy="No delivery history was changed. Resolve the listed duplicates explicitly before retrying.",
    )
    op.create_index(
        "uq_webhook_log_scheduler_sequence",
        "webhook_delivery_log",
        ["tenant_id", "media_buy_id", "task_type", "sequence_number"],
        unique=True,
        postgresql_where=sa.text("task_type = 'media_buy_delivery'"),
    )


def downgrade() -> None:
    """Keep unresolved scheduler reservations protected from sequence reuse."""
    bind = op.get_bind()
    bind.execute(sa.text("LOCK TABLE webhook_delivery_log IN ACCESS EXCLUSIVE MODE"))
    abort_if_rows(
        bind,
        """
        SELECT tenant_id, media_buy_id, id, status
          FROM webhook_delivery_log
         WHERE task_type = 'media_buy_delivery' AND status IN ('reserved', 'retrying')
         ORDER BY tenant_id, media_buy_id, id
        """,
        describe=lambda row: (
            f"tenant={row.tenant_id!r} media_buy={row.media_buy_id!r} log={row.id!r} status={row.status!r}"
        ),
        headline="Cannot remove scheduler sequence protection: {count} unresolved reservation(s).",
        remedy="No delivery history was changed. Settle the listed reservations before downgrading.",
    )
    op.drop_index("uq_webhook_log_scheduler_sequence", table_name="webhook_delivery_log")
