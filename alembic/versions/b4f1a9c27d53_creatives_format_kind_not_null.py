"""creatives_format_kind_not_null

``format_kind`` is the canonical identifier every layer inside this seller keys on, and the
column allowed NULL. salesagent-jzhea: the library upsert path had no satisfaction gate, so a
creative whose legacy ``format_id`` had no canonical projection was STORED with
``format_kind`` NULL instead of refused -- and ``CreativeFormatIdentity.from_row``, the one
reader of these four columns, then had no identity to key on.

The write gate that refuses such a creative landed with this change. This makes the illegal
state unrepresentable rather than merely guarded, which is the user's ruling: "it can't be
null now that we always project it. Migration, data layer fix, NOT NULL, validation rules."

THE SPEC REFUSES THE STATE OUTRIGHT, so the constraint rests on the pin rather than on this
seller's preference. ``manifest.json`` and ``enums/error-code.json``: "An unresolved option
reference or an unprojectable legacy ID is rejected with UNSUPPORTED_FEATURE."
``core/product-format-declaration.json`` closes the other direction too -- the protocol "does
NOT mint synthetic v1 format_ids for unmappable declarations" -- so nothing is invented
either way. A column that can hold NULL is a column that can hold a state the pin says must
have been refused at the door.

EXISTING ROWS ARE MIGRATED BY PROJECTION, NOT DELETED. Also the owner's ruling, which sets
aside the repo's "migrations do not preserve data" rule for this chain only: "Don't delete
-- migrate using the projection. Those that can't be migrated would be migration errors and
the seller would take care of them on the data level, no problem." ``346edbd6f12b`` carried
each row's reference into ``legacy_format_id`` before dropping the old columns. ``upgrade``
projects it through ``canonical_kind_for`` -- the same function the write path uses, not a
retyped copy of the registry -- and ABORTS naming the rows it could not project. No deletion,
no sentinel kind, no backup table.

THE PROJECTION READS THE PINNED FORMAT CAPTURE. ``canonical_kind_for`` needs a format's
declaration for most reference-agent ids, and a migration cannot dial the creative agent. So a
reference naming the reference agent is projected with that format's captured declaration as
``spec``, from ``tests/fixtures/creative_formats/reference_formats.json``, which the production
image carries (``Dockerfile`` copies the tree and ``.dockerignore`` does not exclude it). The
capture is read only when a row needs it, so an empty database does not depend on it. An id the
capture does not hold falls back to the mapping registry alone.

WHAT AN ABORT MEANS. A creative at any other agent has no declaration here and no registry
projection, because another agent's id projects only through that agent's own declaration
(``canonical_projection``); so does a reference-agent id that neither the capture nor the
registry holds. No code resolves such a row, so it is seller data for the operator to fix or
delete. The whole upgrade runs in one transaction (``alembic/env.py``), so the abort rolls it
back: ``format_kind`` and ``legacy_format_id`` do not exist afterwards, the row is in its old
columns, and it is fixed or deleted there before the upgrade runs again.

Structure only on the way down: ``downgrade`` drops the constraint and leaves every projected
value in place.

Revision ID: b4f1a9c27d53
Revises: a7c31e0f45b2
Create Date: 2026-10-16

"""

from collections.abc import Iterable, Sequence
from typing import Any

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4f1a9c27d53"
down_revision: str | Sequence[str] | None = "a7c31e0f45b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def plan_projection(
    rows: Iterable[tuple[Any, Any, Any, Any]],
) -> tuple[list[dict[str, str]], list[str]]:
    """``(rows to update, descriptions of the rows that cannot project)`` for the NULL rows handed in.

    Imported inside, not at module scope: alembic imports every revision in the directory to
    build its graph, and a module-scope import of ``src/`` would make that graph depend on
    the application package being importable.
    """
    from src.core.format_cache import load_reference_formats
    from src.core.format_resolver import canonical_kind_for, format_ref_id, names_reference_agent

    projected: list[dict[str, str]] = []
    unprojectable: list[str] = []
    captured: dict[str, Any] | None = None
    for creative_id, tenant_id, principal_id, legacy_format_id in rows:
        try:
            if not legacy_format_id:
                # No kind AND no reference: nothing to project FROM. The same abort, because
                # the operator's action is the same.
                raise ValueError("no reference")
            spec = None
            if names_reference_agent(legacy_format_id):
                if captured is None:
                    captured = {entry.format_id.id: entry for entry in load_reference_formats()}
                spec = captured.get(format_ref_id(legacy_format_id) or "")
            projected.append(
                {
                    "kind": canonical_kind_for(legacy_format_id, spec=spec),
                    "creative_id": str(creative_id),
                    "tenant_id": str(tenant_id),
                    "principal_id": str(principal_id),
                }
            )
        except ValueError:
            unprojectable.append(f"{tenant_id}/{principal_id}/{creative_id} ({legacy_format_id})")
    return projected, unprojectable


def upgrade() -> None:
    """Project every NULL ``format_kind``, then make the column ``NOT NULL``."""
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT creative_id, tenant_id, principal_id, legacy_format_id FROM creatives WHERE format_kind IS NULL"
        )
    ).fetchall()

    projected, unprojectable = plan_projection(rows)

    if unprojectable:
        raise RuntimeError(
            f"{len(unprojectable)} creative(s) (tenant/principal/creative) have a format reference with no "
            f"canonical projection, in the pinned format capture or the mapping registry: "
            f"{', '.join(sorted(unprojectable))}. "
            "This upgrade ran in one transaction and has rolled back, so format_kind and legacy_format_id do "
            "not exist now. In the creatives table, change each listed row's agent_url, format and "
            "format_parameters to a format that projects, or delete the row, then run the upgrade again."
        )

    # The composite key: creatives is keyed (creative_id, tenant_id, principal_id), and
    # creative_id alone is not unique across tenants or principals.
    if projected:
        bind.execute(
            sa.text(
                "UPDATE creatives SET format_kind = :kind "
                "WHERE creative_id = :creative_id AND tenant_id = :tenant_id AND principal_id = :principal_id"
            ),
            projected,
        )

    op.alter_column("creatives", "format_kind", existing_type=sa.String(length=100), nullable=False)


def downgrade() -> None:
    """Drop the constraint. The projected values stay -- see the module docstring."""
    op.alter_column("creatives", "format_kind", existing_type=sa.String(length=100), nullable=True)
