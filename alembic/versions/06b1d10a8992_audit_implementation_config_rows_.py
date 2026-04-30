"""Audit implementation_config rows against reconciled schemas.

Revision ID: 06b1d10a8992
Revises: 9cc36dfc54f6
Create Date: 2026-04-30 13:32:37.398166

Validation-only migration — no schema changes. Iterates every row in
``products`` whose ``implementation_config`` is non-null, joins to the
tenant's ``adapter_type``, and validates the dict against the registered
``*ProductConfig`` Pydantic schema for that adapter (#1240, #1241, #1242,
#1244). Fails the migration with a row-by-row report of malformed configs
so operators are forced to clean up bad data before deploy completes.

Rationale
---------

The reconciled schemas (BaseProductConfig with ``extra='forbid'``) reject
configs that have unknown fields or invalid values. The adapter-boundary
read code (post-#1244) raises ``ValidationError`` on bad configs. Without
this audit, the first request after deploy would fail at runtime with a
poor error surface (cascading errors, possible partial state).

This migration runs the same validation up-front, in a transactional
context, with a comprehensive report. Operators see every bad row at
once and can fix the data before the runtime path ever sees it.

Adapters covered: mock, broadstreet, google_ad_manager (and "gam" alias).
Other adapter types (kevel, triton, xandr) are skipped with a debug
log — schemas haven't been reconciled for them yet (out of scope for
#1239). Empty/missing implementation_config rows are skipped.

Imports of the reconciled schemas are deferred into upgrade() so the
migration file loads cleanly even on revisions where the schema modules
don't exist yet (e.g., during stacked-PR review).

downgrade() is a no-op — this migration changes no schema.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "06b1d10a8992"
down_revision: str | Sequence[str] | None = "9cc36dfc54f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Validate every product.implementation_config row against the registered schema."""
    # Deferred imports: keep this migration file loadable even if the schemas
    # don't exist yet (e.g., during stacked-PR review of #1240/#1241/#1242/#1244).
    from pydantic import ValidationError

    from src.adapters.broadstreet.schemas import BroadstreetProductConfig
    from src.adapters.gam.schemas import GAMProductConfig
    from src.adapters.mock_ad_server import MockProductConfig

    schema_for_adapter: dict[str, type] = {
        "mock": MockProductConfig,
        "broadstreet": BroadstreetProductConfig,
        "google_ad_manager": GAMProductConfig,
        "gam": GAMProductConfig,
    }

    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            """
            SELECT p.tenant_id, p.product_id, p.implementation_config, ac.adapter_type
            FROM products p
            JOIN adapter_config ac ON ac.tenant_id = p.tenant_id
            WHERE p.implementation_config IS NOT NULL
              AND p.implementation_config::text != '{}'::text
            """
        )
    ).fetchall()

    failures: list[tuple[str, str, str, str]] = []
    skipped_unknown_adapter: list[tuple[str, str, str]] = []

    for tenant_id, product_id, impl_config, adapter_type in rows:
        schema = schema_for_adapter.get(adapter_type)
        if schema is None:
            skipped_unknown_adapter.append((tenant_id, product_id, adapter_type))
            continue

        try:
            schema.model_validate(impl_config)
        except ValidationError as exc:
            failures.append((tenant_id, product_id, adapter_type, str(exc)))

    if skipped_unknown_adapter:
        # Not a failure — just adapters that don't have reconciled schemas yet.
        # Logged so operators see what the audit didn't cover.
        op.execute(
            sa.text(
                f"SELECT 1 -- audit: skipped {len(skipped_unknown_adapter)} rows "
                f"with unrecognized adapter_types: "
                f"{sorted({a for _, _, a in skipped_unknown_adapter})}"
            )
        )

    if failures:
        report_lines = [
            "",
            "=" * 80,
            "implementation_config audit FAILED — fix the rows below in the admin UI",
            "(or via direct SQL update) and re-run `alembic upgrade head`.",
            "=" * 80,
            "",
        ]
        for tenant_id, product_id, adapter_type, error in failures:
            report_lines.append(f"tenant={tenant_id} product={product_id} adapter={adapter_type}:")
            for line in error.splitlines():
                report_lines.append(f"  {line}")
            report_lines.append("")
        report_lines.append(f"Total: {len(failures)} row(s) failed validation.")
        report_lines.append("=" * 80)
        raise RuntimeError("\n".join(report_lines))


def downgrade() -> None:
    """No-op — this migration changes no schema, only validates data.

    Re-running is harmless; rolling back is meaningless. Re-validation on
    upgrade catches any data drift introduced after the initial audit.
    """
    op.execute("SELECT 1 -- audit migration: validation-only, nothing to roll back")
