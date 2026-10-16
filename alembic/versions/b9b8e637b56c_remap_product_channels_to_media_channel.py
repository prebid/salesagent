"""remap legacy admin channel picker values onto the MediaChannel enum

Revision ID: b9b8e637b56c
Revises: 7f31c0ab94d2
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

Three values have one member they stood for and are remapped: ``video -> olv``,
``audio -> streaming_audio`` (the aliases ``CHANNEL_MAPPING`` already applies),
``retail -> retail_media``. ``native`` is dropped: the pinned taxonomy
(docs/reference/media-channel-taxonomy.mdx @ v3.1.1) calls it a format category,
"orthogonal to channels", and lists native units under both ``display`` and ``social``,
so no single channel is inferable from it. A row left with no channel becomes NULL, which
hands it to the "absence of a claim" rule in ``channel_helpers.effective_channel_names``
-- the rule ``get_products`` was already applying to it. The rebuilt array is
de-duplicated because ``core/product.json#/properties/channels`` is ``uniqueItems``.

Detection query (runnable standalone by an operator)::

    SELECT tenant_id, product_id, channels
      FROM products
     WHERE jsonb_typeof(channels) = 'array'
       AND channels ?| array['video', 'audio', 'retail', 'native'];

Every element outside the enum is surveyed and logged with its row before the UPDATE, the
four legacy values included; the UPDATE touches only rows holding one of the four. An
element that is none of those (``Video``, a typo) is left as it is and logged -- the read
path already skips it, and rewriting a tenant's live row on a guess is not this revision's
call. shortcut: non-string array elements are neither surveyed (``NULL NOT IN`` is NULL)
nor rewritten; every writer stores strings, revisit if a writer ever does not.
"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b9b8e637b56c"
down_revision: str | Sequence[str] | None = "7f31c0ab94d2"
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

_SURVEY_SQL = f"""
    SELECT p.tenant_id, p.product_id, e.value AS channel
      FROM products p, jsonb_array_elements_text(p.channels) AS e(value)
     WHERE jsonb_typeof(p.channels) = 'array'
       AND e.value NOT IN ({", ".join(f"'{member}'" for member in _MEMBERS)})
     ORDER BY p.tenant_id, p.product_id, e.value
"""

#: ORDER BY inside the aggregate: a DISTINCT aggregate happens to sort, but nothing
#: promises it, and the wire list must be deterministic for the same row on every read.
_REMAP_SQL = """
    UPDATE products
       SET channels = (
           SELECT jsonb_agg(c ORDER BY c)
             FROM (SELECT DISTINCT CASE e.value
                                     WHEN 'video' THEN 'olv'
                                     WHEN 'audio' THEN 'streaming_audio'
                                     WHEN 'retail' THEN 'retail_media'
                                     ELSE e.value
                                   END AS c
                     FROM jsonb_array_elements_text(products.channels) AS e(value)
                    WHERE e.value <> 'native') AS remapped
       )
     WHERE jsonb_typeof(channels) = 'array'
       AND channels ?| array['video', 'audio', 'retail', 'native']
"""


def upgrade() -> None:
    """Survey first, act second; a clean database issues no row-touching statement."""
    bind = op.get_bind()

    outside_enum = bind.execute(sa.text(_SURVEY_SQL)).fetchall()
    if not outside_enum:
        logger.info("No product declares a channel outside the MediaChannel enum - nothing to repair.")
        return

    for row in outside_enum:
        logger.info(
            "products.channels outside the MediaChannel enum: tenant=%s product=%s channel=%r",
            row.tenant_id,
            row.product_id,
            row.channel,
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
