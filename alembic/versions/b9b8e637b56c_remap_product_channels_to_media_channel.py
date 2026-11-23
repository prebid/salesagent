"""remap legacy admin channel picker values onto the MediaChannel enum

Revision ID: b9b8e637b56c
Revises: 9fdf18a56063
Create Date: 2026-10-16 12:00:00.000000

The admin channel picker (templates/components/channel_selector.html) once offered four
option values that are not members of the AdCP ``MediaChannel`` enum -- ``video``,
``audio``, ``native`` and ``retail`` -- and both product handlers stored whatever the form
posted. A product tagged with one had the string dropped on read
(``product_conversion.py`` logs and skips an unknown value), so ``get_products`` matched it
on the adapter's default channels instead of the publisher's tag, while
``get_adcp_capabilities`` aliased ``video``/``audio`` through ``CHANNEL_MAPPING`` and so
advertised a channel the filter refused (GH #1532). The picker is enum-driven and the
handlers refuse a non-member now; the rows already written are this revision's job.

What each legacy value becomes is declared once, in ``_REMAP`` and ``_DROPPED`` below, with
the spec's reason beside it; every statement here is generated from them. A row left with no
channel becomes NULL, which hands it to the "absence of a claim" rule in
``channel_helpers.effective_channel_names`` -- the rule ``get_products`` was already applying
to it. The rebuilt array is de-duplicated, because ``core/product.json#/properties/channels``
is ``uniqueItems``, and ordered by enum position, the order the picker posts and
``parse_media_channels`` keeps, so saving a repaired product unchanged writes the same array.

Every element outside the enum is surveyed and logged with its row and what becomes of it
before the UPDATE, and the UPDATE touches only rows holding a legacy value. An element that is
none of those (``Video``, a typo, a non-string) is left as it is -- the read path already
skips it, and rewriting a tenant's live row on a guess is not this revision's call. Such an
element sorts after the members, by jsonb order, so a row always gets the same array.

Detection: ``_SURVEY_SQL`` is the query, runnable standalone by an operator; print it with
``python -c "import runpy; print(runpy.run_path('<this file>')['_SURVEY_SQL'])"``.
"""

import logging
from collections.abc import Iterable, Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b9b8e637b56c"
down_revision: str | Sequence[str] | None = "9fdf18a56063"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

#: channels.json#/enum at the pinned spec, inlined: a revision keeps behaving the way it did
#: when it ran, and the SDK enum is free to grow.
_MEMBERS = (
    "display",
    "olv",
    "social",
    "search",
    "ctv",
    "linear_tv",
    "radio",
    "streaming_audio",
    "podcast",
    "dooh",
    "ooh",
    "print",
    "cinema",
    "email",
    "gaming",
    "retail_media",
    "influencer",
    "affiliate",
    "product_placement",
    "sponsored_intelligence",
)

#: docs/reference/migration/channels.mdx @ v3.2.7, the spec's channel migration guide.
#: ``retail`` is "Renamed for clarity". ``video`` and ``audio`` are "Split by distribution",
#: into ``olv``, ``linear_tv`` and ``cinema``, and into ``radio`` and ``streaming_audio``. One
#: target is right for this seller: the old picker offered ``ctv`` and ``podcast`` as options of
#: their own, so its ``video`` and ``audio`` meant the rest of each, and ``olv`` and
#: ``streaming_audio`` are the only channels of those splits any adapter here reports
#: (``default_channels``; none names ``linear_tv``, ``cinema`` or ``radio``).
_REMAP = {"video": "olv", "audio": "streaming_audio", "retail": "retail_media"}

#: The same guide: ``native`` is "Removed | Use format-level properties instead", assigned per
#: product "based on how the budget is planned" (``display``, ``social`` or ``affiliate``),
#: which a stored row does not say -- "Do not derive the declaration from creative format
#: alone". media-channel-taxonomy.mdx's legacy table maps it to ``display``; the guide, the
#: document written for this migration, governs.
_DROPPED = ("native",)


def _sql_list(values: Iterable[str]) -> str:
    """*values* as a comma-separated list of SQL string literals; each is a constant of this module."""
    return ", ".join(f"'{value}'" for value in values)


#: An element's text; NULL for a JSON null.
_TEXT = "e #>> '{}'"
_MEMBER_ARRAY = f"ARRAY[{_sql_list(_MEMBERS)}]::text[]"

_SURVEY_SQL = f"""
    SELECT p.tenant_id, p.product_id, jsonb_typeof(e) AS kind, {_TEXT} AS value, e::text AS literal
      FROM products p, jsonb_array_elements(p.channels) AS e
     WHERE jsonb_typeof(p.channels) = 'array'
       AND (jsonb_typeof(e) <> 'string' OR {_TEXT} NOT IN ({_sql_list(_MEMBERS)}))
     ORDER BY p.tenant_id, p.product_id, e
"""

_REMAP_ARMS = "\n".join(
    f"                                              WHEN '{legacy}' THEN to_jsonb('{member}'::text)"
    for legacy, member in _REMAP.items()
)

#: Only a string goes through the CASE (``jsonb_array_elements_text`` would cast ``1`` to
#: ``"1"`` and drop ``null``). ORDER BY inside the aggregate: a DISTINCT aggregate happens to
#: sort, but nothing promises it, and the wire list must be deterministic for the same row.
_REMAP_SQL = f"""
    UPDATE products
       SET channels = (
           SELECT jsonb_agg(c ORDER BY array_position({_MEMBER_ARRAY}, c #>> '{{}}') NULLS LAST, c)
             FROM (SELECT DISTINCT CASE
                                     WHEN jsonb_typeof(e) <> 'string' THEN e
                                     ELSE CASE {_TEXT}
{_REMAP_ARMS}
                                              ELSE e
                                          END
                                   END AS c
                     FROM jsonb_array_elements(products.channels) AS e
                    WHERE jsonb_typeof(e) <> 'string' OR {_TEXT} NOT IN ({_sql_list(_DROPPED)})) AS remapped
       )
     WHERE jsonb_typeof(channels) = 'array'
       AND channels ?| array[{_sql_list((*_REMAP, *_DROPPED))}]
"""


def _fate(row: sa.Row) -> str:
    """What the UPDATE does to one surveyed element, for the log."""
    if row.kind == "string" and row.value in _REMAP:
        return f"{row.value!r} -> {_REMAP[row.value]!r}"
    if row.kind == "string" and row.value in _DROPPED:
        return f"{row.value!r} dropped"
    return f"{row.literal} left as it is"


def upgrade() -> None:
    """Survey first, act second; a clean database issues no row-touching statement."""
    bind = op.get_bind()

    outside_enum = bind.execute(sa.text(_SURVEY_SQL)).fetchall()
    if not outside_enum:
        logger.info("No product declares a channel outside the MediaChannel enum - nothing to repair.")
        return

    for row in outside_enum:
        logger.info(
            "products.channels outside the MediaChannel enum: tenant=%s product=%s %s",
            row.tenant_id,
            row.product_id,
            _fate(row),
        )
    result = bind.execute(sa.text(_REMAP_SQL))
    logger.info("Remapped legacy picker channels on %d product(s).", result.rowcount)


def downgrade() -> None:
    """Intentionally does not restore the legacy values.

    A remapped ``olv`` is byte-identical to one that was always ``olv``, so a reverse UPDATE
    would rewrite rows this revision never touched. It logs rather than raising because a
    raising downgrade would block rolling back PAST this revision at all. Precedent:
    f4d31a97c058.
    """
    logger.info(
        "Downgrade of %s is a no-op by design: remapped channels are indistinguishable from values "
        "that were always members, so restoring the legacy strings would corrupt rows this revision "
        "never touched. Repaired data stays repaired.",
        revision,
    )
