"""repair accounts poisoned by str(RootModel) in the brand_id

Revision ID: f4d31a97c058
Revises: b2e94f7c1a03
Create Date: 2026-07-29 10:05:00.000000

``sync_accounts`` used to build the natural key by stringifying ``BrandId``, a
pydantic RootModel, so the value persisted into ``accounts.brand->>'brand_id'``
was the model repr ``root='brand_one'`` rather than ``brand_one``. The write
path is fixed; rows already written that way are not, and such a row cannot be
read back through the ORM AT ALL: ``DBAccount.brand`` is
``JSONType(model=BrandReference)`` and ``BrandId``'s pattern is ``^[a-z0-9_]+$``,
so ``process_result_value`` raises ``ValidationError`` on every load.

Detection query (runnable standalone by an operator)::

    SELECT tenant_id, account_id, brand->>'brand_id', name
      FROM accounts
     WHERE brand->>'brand_id' LIKE 'root=%';

WHY THAT QUERY IS COMPLETE — this is load-bearing, not a nicety. ``brand`` is in
``AccountRepository._IMMUTABLE_FIELDS`` (src/core/database/repositories/account.py),
so a poisoned ``brand`` can never have been repaired by any other path. No row
can therefore exist with a repaired ``brand`` and a still-poisoned ``name``, and
the predicate above finds every affected row. ``name``, by contrast, IS mutable
(admin ``edit_account`` -> ``update_fields(name=...)``), which is exactly why the
name rewrite must be conditional rather than unconditional — see ``upgrade()``.

Two columns are poisoned, not one. ``_generate_account_name`` was called with
the mangled value on the sync create path and the name is always generated
there, so every poisoned row also stores
``acme.com:root='brand_one' c/o example.com``. ``name`` is the value the buyer
actually reads back (create/update/unchanged results echo it), so repairing only
``brand`` would leave the mangling on the wire.

Ambiguity is never remediated: an unrecognised ``root=%`` shape, or a repaired
key that would collide with an existing account, ABORTS the migration naming the
rows. Choosing between two of a tenant's live accounts is not a decision a
migration gets to make.
"""

import logging
import re
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from src.core.database.migration_guards import abort_if_rows

# revision identifiers, used by Alembic.
revision: str = "f4d31a97c058"
down_revision: str | Sequence[str] | None = "b2e94f7c1a03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

_INDEX = "uq_accounts_natural_key"

#: The poison predicate. This ``LIKE`` is safe: the prefix ``root=`` contains
#: neither ``_`` nor ``%``. (The same is NOT true of a brand_id — see the note
#: on the UPDATE below.)
_POISONED = "brand ->> 'brand_id' LIKE 'root=%'"

#: The single shared extraction expression, used by BOTH the collision survey and
#: the UPDATE so the two can never disagree about what the repaired value is.
#: ``BrandId``'s pattern ``^[a-z0-9_]+$`` is enforced at request parse, so the
#: inner value can never contain a quote and this regex is a total, unambiguous
#: extractor for every shape the defect could have produced.
_EXTRACTED = "substring(brand ->> 'brand_id' from '^root=''([a-z0-9_]+)''$')"

_DETECTION_SQL = f"""
    SELECT tenant_id,
           account_id,
           (brand ->> 'brand_id') AS brand_id,
           name
      FROM accounts
     WHERE {_POISONED}
"""

_UNPARSEABLE_SQL = f"""
    SELECT tenant_id,
           account_id,
           (brand ->> 'brand_id') AS brand_id
      FROM accounts
     WHERE {_POISONED}
       AND {_EXTRACTED} IS NULL
"""

#: Copied VERBATIM from b2e94f7c1a03 (the revision that created the index this
#: survey has to model), not re-derived from the ORM index definition —
#: re-derivation is how the two drift apart. ``_KEY_SQL`` must keep rendering
#: byte-identical to b2e94f7c1a03's ``_KEY_SQL``.
_KEY_COMPONENTS = (
    "tenant_id",
    "operator",
    "(brand ->> 'domain')",
    "(brand ->> 'brand_id')",
    "COALESCE(sandbox, false)",
)
_KEY_SQL = ", ".join(_KEY_COMPONENTS)
_KEYED_ONLY = "(brand ->> 'domain') IS NOT NULL"


#: Column names appearing inside ``_KEY_COMPONENTS``. Qualifying a component for a
#: table alias means rewriting the column reference INSIDE the expression —
#: ``(brand ->> 'domain')`` becomes ``(p.brand ->> 'domain')``, not
#: ``p.(brand ->> 'domain')``, which is not valid SQL.
_KEY_COLUMNS = ("tenant_id", "operator", "brand", "sandbox")


def _qualify(expression: str, alias: str) -> str:
    """Bind every column reference in a key expression to ``alias``."""
    return re.sub(
        rf"\b({'|'.join(_KEY_COLUMNS)})\b",
        rf"{alias}.\1",
        expression,
    )


def _collision_sql() -> str:
    """Self-join finding an occupant already holding each poisoned row's REPAIRED key.

    ``IS NOT DISTINCT FROM`` per component, NOT ``=``: ``uq_accounts_natural_key``
    is ``NULLS NOT DISTINCT``, so two rows with a NULL ``operator`` DO collide,
    while an ``=`` join reports nothing. With ``=`` the migration would sail past
    the survey and hit a raw ``IntegrityError`` mid-UPDATE instead of the
    named-rows abort this migration promises. ``operator`` is the only genuinely
    NULL-able component today (``_KEYED_ONLY`` guarantees a domain,
    ``COALESCE(sandbox, false)`` collapses NULL, and a repaired ``brand_id`` is
    never NULL), but the row-wise form covers the whole tuple at once so a future
    nullable component cannot silently reintroduce the hole.
    """
    # The poisoned row's key is its key AFTER repair — that is the key that will
    # actually be written, and therefore the one that can collide.
    poisoned_extracted = _qualify(_EXTRACTED, "p")
    repaired = tuple(
        poisoned_extracted if component == "(brand ->> 'brand_id')" else _qualify(component, "p")
        for component in _KEY_COMPONENTS
    )
    occupant = tuple(_qualify(component, "o") for component in _KEY_COMPONENTS)
    predicate = "\n       AND ".join(
        f"{left} IS NOT DISTINCT FROM {right}" for left, right in zip(repaired, occupant, strict=True)
    )
    return f"""
        SELECT p.tenant_id                AS tenant_id,
               p.account_id               AS poisoned_account_id,
               o.account_id               AS occupant_account_id,
               (p.brand ->> 'domain')     AS brand_domain,
               {poisoned_extracted}       AS repaired_brand_id,
               p.operator                 AS operator,
               COALESCE(p.sandbox, false) AS sandbox
          FROM accounts p
          JOIN accounts o
            ON {predicate}
         WHERE {_qualify(_POISONED, "p")}
           AND NOT ({_qualify(_POISONED, "o")})
           AND o.account_id <> p.account_id
           AND {_qualify(_KEYED_ONLY, "p")}
    """


#: PostgreSQL evaluates every SET expression against the OLD row, so
#: ``brand ->> 'brand_id'`` inside the ``name`` expression still reads the MANGLED
#: value even though the same statement is rewriting ``brand``. The name repair is
#: correct ONLY because of that.
#:
#: The name restriction lives in the SET expression, never in the WHERE. Putting
#: "name still contains the mangled fragment" in the WHERE would exclude a
#: poisoned row an operator had renamed via the admin form from the whole UPDATE —
#: its ``brand`` would never be repaired and the row would stay ORM-unreadable,
#: which is the PRIMARY defect this migration exists to fix. ``replace()`` is
#: already a no-op when the fragment is absent, so it IS the restriction.
#:
#: Do NOT reach for a ``LIKE`` to express that restriction: ``BrandId``'s pattern
#: permits ``_``, a LIKE single-character wildcard (the canonical ``brand_one``
#: contains one), so a LIKE predicate would match names that do not literally
#: contain the fragment. Use ``replace()``, or ``strpos(name, ...) > 0`` if a
#: predicate is ever genuinely needed.
_REPAIR_SQL = f"""
    UPDATE accounts
       SET brand = jsonb_set(brand, '{{brand_id}}', to_jsonb({_EXTRACTED})),
           name  = replace(name, brand ->> 'brand_id', {_EXTRACTED})
     WHERE {_POISONED}
"""


def upgrade() -> None:
    """Rewrite ``root='x'`` back to ``x`` in accounts.brand->>'brand_id' and name.

    Survey first, act second — the same order b2e94f7c1a03 uses, and for the same
    reason: this runs unattended on startup, so everything it cannot repair
    unambiguously must stop it rather than be guessed at.

    A clean database issues no row-touching statement at all.
    """
    bind = op.get_bind()

    poisoned = bind.execute(sa.text(_DETECTION_SQL)).fetchall()
    if not poisoned:
        logger.info("No accounts carry a mangled brand_id — nothing to repair.")
        return

    abort_if_rows(
        bind,
        _UNPARSEABLE_SQL,
        describe=lambda row: f"  tenant={row.tenant_id!r} account={row.account_id!r} brand_id={row.brand_id!r}",
        headline=(
            "Cannot repair the mangled brand_id: {count} row(s) match 'root=%' but not the "
            "expected root='<brand_id>' shape."
        ),
        remedy=(
            "These are an unknown mangling, so rewriting them would be a guess about a tenant's live "
            "account. Inspect and hand-repair them with direct SQL, then re-run this migration. "
            "Skipping them silently is not an option: they are unreadable through the ORM."
        ),
    )

    abort_if_rows(
        bind,
        _collision_sql(),
        describe=lambda row: (
            f"  tenant={row.tenant_id!r} operator={row.operator!r} brand.domain={row.brand_domain!r} "
            f"brand.brand_id={row.repaired_brand_id!r} sandbox={row.sandbox!r} -> "
            f"poisoned={row.poisoned_account_id} occupant={row.occupant_account_id}"
        ),
        headline=(
            "Cannot repair the mangled brand_id: {count} repaired key(s) are already occupied by another account."
        ),
        remedy=(
            "Repairing the poisoned row would make it collide with the occupant under "
            f"{_INDEX}. The occupant is the account the buyer's sync_accounts now maintains (after the "
            "write-path fix their payload can only reach the correct key), and the poisoned row is the "
            "orphan left behind by the defect.\n"
            "This migration will not choose for you, and the usual remedies do not apply here: the "
            "losing row cannot be re-keyed (brand, operator and sandbox are immutable on "
            "AccountRepository) and 'closed' is terminal in the status transitions, so a wrong choice "
            "is not reversible by the downgrade.\n"
            "Resolve each pair with direct SQL against the POISONED account — delete it, or hand-write "
            "its brand and name — then re-run. Note the urgency: migrations run automatically on "
            "startup and scripts/ops/migrate.py exits non-zero on failure, so until this is resolved "
            "the service will not boot."
        ),
    )

    result = bind.execute(sa.text(_REPAIR_SQL))
    logger.info("Repaired the mangled brand_id on %d account(s).", result.rowcount)


def downgrade() -> None:
    """Intentionally does not re-mangle the repaired rows.

    A repaired row is byte-identical to a row that was always correct — the write
    path produced no marker distinguishing them — so a reverse UPDATE would poison
    accounts this migration never touched. It logs rather than raising because a
    raising downgrade would block rolling back PAST this revision at all,
    including b2e94f7c1a03's index. Precedent for a deliberately asymmetric
    downgrade: e381618812f1.
    """
    logger.info(
        "Downgrade of %s is a no-op by design: repaired brand_id values are indistinguishable from "
        "values that were always correct, so re-mangling would corrupt rows this migration never "
        "touched. Repaired data stays repaired.",
        revision,
    )
