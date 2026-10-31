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

EXISTING ROWS ARE MIGRATED BY PROJECTION, NOT DELETED. Also the user's ruling: "Don't delete
-- migrate using the projection. Those that can't be migrated would be migration errors and
the seller would take care of them on the data level, no problem." So ``upgrade`` projects
each NULL row's stored ``legacy_format_id`` through ``canonical_kind_for`` -- the same
function the write path uses, not a retyped copy of the registry -- and ABORTS naming the
``creative_id`` values it could not project. No deletion, no sentinel kind, no backup table
(CLAUDE.md forbids the last one outright, and the first two lose or invent a buyer's data).

THE PROJECTION AVAILABLE HERE IS THE REGISTRY GLOB ONLY, and an operator reading an abort
needs to know why. ``canonical_kind_for`` resolves in two steps: the agent's own declared
projection (step 1), then the registry glob (step 2). Step 1 needs the FETCHED format
declaration, and a migration cannot dial the creative agent -- so only step 2 runs here.
Measured against the pinned capture of 71 formats: 15 project from the glob alone, 64 with
the declaration. So a row this migration cannot project may still be perfectly resolvable at
sync time, and the remedy for such a row is to RE-SYNC the creative (which reaches step 1)
or to set its ``format_kind`` by hand -- not to delete it. The abort names the ids so the
operator can do either and re-run.

WHAT AN ABORT ACTUALLY MEANS, because the obvious reading is wrong. It is NOT "some
creatives legitimately have no kind" -- the pin's own refusal clause above says no such
creative should exist. It is that SEVEN formats in the published reference catalog carry no
``canonical`` annotation, so their v1 ids project through neither route, and that is an
upstream omission filed as adcontextprotocol/adcp#8277 (the card family:
``product_card_standard``, ``product_card_detailed``, ``proposal_card_*``,
``native_product_card``, ``format_card_*``). 64 of the 71 ARE annotated, and the 45 registry
mappings cover none of the seven. So an abort is a loud failure pointing at someone else's
missing annotation, not a design decision here -- and when #8277 lands, a re-sync picks the
fix up for free.

NO CATALOG SNAPSHOT IS BAKED INTO THIS REVISION, deliberately. Freezing a creative-agent
capture here would freeze those seven missing annotations with it, so the migration would
keep aborting on them after upstream fixed them -- a stale copy of someone else's bug,
embedded in an immutable revision. Abort-and-re-sync needs no snapshot and loses nothing,
because the operator re-syncs through the live path that holds the catalog.

THE NUMBER OF AFFECTED ROWS IS UNKNOWN AND IS NOT KNOWABLE FROM A WORKTREE. ``creatives``
does not exist in the shared test Postgres -- ``to_regclass('creatives')`` returns false --
because ``tests/conftest_db.py`` builds the schema per test with
``Base.metadata.create_all``. So only a dev database or production could hold NULL rows, and
neither is reachable from here. Recorded rather than re-discovered: a previous lane spent a
cycle finding this out.

Structure only, so nothing preserves data across it: ``downgrade`` drops the constraint and
leaves every projected value in place. A row that was NULL before the upgrade is NOT restored
to NULL, and that is correct -- the projected kind is the row's true identity, derived from
the reference it already carried.

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


def plan_projection(rows: Iterable[tuple[Any, Any, Any]]) -> tuple[list[tuple[str, str, str]], list[str]]:
    """``(rows to update, creative_ids that cannot project)`` for the NULL rows handed in.

    SEPARATE FROM THE DDL so that the part which can be WRONG is testable without a
    database. The ``ALTER COLUMN`` either runs or does not; this decides which rows get a
    kind, which kind each gets, and which ones abort the whole migration -- and that is
    where a mistake silently writes a wrong identity onto a buyer's creative. Graded by
    ``tests/unit/test_creatives_format_kind_migration_plan.py``; the schema round-trip is
    graded separately against a migrated database, which is the only place a
    migration-created constraint exists at all.

    Imported inside, not at module scope: alembic imports every revision in the directory to
    build its graph, and a module-scope import of ``src/`` would make that graph depend on
    the application package being importable.
    """
    from src.core.format_resolver import canonical_kind_for

    projected: list[tuple[str, str, str]] = []
    unprojectable: list[str] = []
    for creative_id, tenant_id, legacy_format_id in rows:
        if not legacy_format_id:
            # No kind AND no reference: there is nothing to project FROM. Collected into the
            # same abort rather than given its own, because the operator's action is the same
            # -- look at the row -- and two abort paths would be two messages to keep in step.
            unprojectable.append(str(creative_id))
            continue
        try:
            projected.append((str(creative_id), str(tenant_id), canonical_kind_for(legacy_format_id, spec=None)))
        except ValueError:
            unprojectable.append(str(creative_id))
    return projected, unprojectable


def upgrade() -> None:
    """Project every NULL ``format_kind``, then make the column ``NOT NULL``."""
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT creative_id, tenant_id, legacy_format_id FROM creatives WHERE format_kind IS NULL")
    ).fetchall()

    projected, unprojectable = plan_projection(rows)

    if unprojectable:
        raise RuntimeError(
            f"{len(unprojectable)} creative(s) have format_kind IS NULL and a legacy_format_id "
            f"that the canonical registry cannot project, so this migration cannot derive their "
            f"identity: {', '.join(sorted(unprojectable))}. "
            "Nothing has been changed. Only the registry glob is available here -- a migration "
            "cannot fetch a format's declaration -- so a row listed above may still resolve at "
            "sync time, where the agent's own projection is reachable. Re-sync those creatives, "
            "or set creatives.format_kind by hand, then re-run this migration."
        )

    # The composite key: creatives is keyed (creative_id, tenant_id, principal_id), and
    # creative_id alone is not unique across tenants.
    for creative_id, tenant_id, kind in projected:
        bind.execute(
            sa.text(
                "UPDATE creatives SET format_kind = :kind WHERE creative_id = :creative_id AND tenant_id = :tenant_id"
            ),
            {"kind": kind, "creative_id": creative_id, "tenant_id": tenant_id},
        )

    op.alter_column("creatives", "format_kind", existing_type=sa.String(length=100), nullable=False)


def downgrade() -> None:
    """Drop the constraint. The projected values stay -- see the module docstring."""
    op.alter_column("creatives", "format_kind", existing_type=sa.String(length=100), nullable=True)
