"""creative format identity: the canonical kind, and the legacy assertion beside it

AdCP 3.2 is canonical-first, and the projection between the two identities runs ONE WAY:
``registries/v1-canonical-mapping.json`` is "authoritative for v1 -> v2 projection only" and
a seller MUST NOT synthesize a v1 ``format_id`` by inverting a structural match. So the
canonical ``format_kind`` is the only representation every layer can share, and the
deprecated ``format_id`` is a RETAINED BUYER ASSERTION: stored verbatim, echoed to a legacy
peer, never what business logic keys on.

Four columns, each with one job:

* ``format_kind`` -- THE identity. Nullable, because a legacy reference only has one once
  something projects it, and the sixth normative step is to fail closed rather than guess.
* ``format_option_ref`` -- the option that narrows the kind. JSON because the thing it
  stores is an object: ``core/format-option-ref.json`` is itself a oneOf over publisher
  scope (``publisher_domain`` + ``format_option_id``) and product scope.
* ``legacy_format_id`` -- the assertion, as ONE faithful value: ``agent_url``, ``id``, and
  every parameter ``FormatId`` declares.
* ``legacy_format_key`` -- that assertion's canonical comparison key
  (its ``FormatKey``), indexed. Two obligations collide on a stored reference
  and one value cannot serve both: the echo must be VERBATIM (the sync storyboard grades
  "seller must accept its own format_id", and canonicalizing on the write path broke that
  round-trip -- measured), while a COMPARISON must canonicalize, which
  ``core/format-id.json`` makes a MUST. Hence two columns.

``agent_url``, ``format`` and ``format_parameters`` go, and with them
``idx_creatives_format_namespace``, which spanned two of them. They were ONE value
decomposed into three, reassembled on every read -- and the reassembly is where
``pixel_ratio`` was dropped, because the parameter blob did not know which fields the model
declares. A ``FormatId`` now goes in and comes back out; nothing is decomposed, so nothing
can be lost putting it together again.

The two-arm exclusivity is not a CHECK constraint. It is a protocol rule the request and
state models both enforce as a oneOf on the way in. A constraint here would be another place
to state the same rule and another place for it to drift.

No data is preserved. ``upgrade()`` changes the structure and ``downgrade()`` reverts it;
the legacy pair was NOT NULL before this revision, so the revert restores it that way and a
non-empty table refuses it -- which is honest, because the values are gone and the old
structure forbids the NULL that would stand in for them.

Revision ID: 346edbd6f12b
Revises: 7f31c0ab94d2
Create Date: 2026-10-07 11:54:16.822830

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from src.core.database.json_type import JSONType

# revision identifiers, used by Alembic.
revision: str = "346edbd6f12b"
down_revision: str | Sequence[str] | None = "7f31c0ab94d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the canonical identity and the retained assertion; drop the decomposed legacy trio."""
    op.add_column("creatives", sa.Column("format_kind", sa.String(100), nullable=True))
    op.add_column("creatives", sa.Column("format_option_ref", JSONType(), nullable=True))
    op.add_column("creatives", sa.Column("legacy_format_id", JSONType(), nullable=True))
    op.add_column("creatives", sa.Column("legacy_format_key", sa.String(), nullable=True))
    op.create_index("idx_creatives_legacy_format_key", "creatives", ["legacy_format_key"])

    op.drop_index("idx_creatives_format_namespace", table_name="creatives")
    op.drop_column("creatives", "format_parameters")
    op.drop_column("creatives", "format")
    op.drop_column("creatives", "agent_url")


def downgrade() -> None:
    """Restore the decomposed legacy trio; drop the canonical identity and the assertion."""
    op.add_column("creatives", sa.Column("agent_url", sa.String(500), nullable=False))
    op.add_column("creatives", sa.Column("format", sa.String(100), nullable=False))
    op.add_column("creatives", sa.Column("format_parameters", JSONType(), nullable=True))
    op.create_index("idx_creatives_format_namespace", "creatives", ["agent_url", "format"])

    op.drop_index("idx_creatives_legacy_format_key", table_name="creatives")
    op.drop_column("creatives", "legacy_format_key")
    op.drop_column("creatives", "legacy_format_id")
    op.drop_column("creatives", "format_option_ref")
    op.drop_column("creatives", "format_kind")
