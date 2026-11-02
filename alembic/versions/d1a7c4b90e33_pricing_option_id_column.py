"""store pricing_option_id on pricing_options

Revision ID: d1a7c4b90e33
Revises: c93f5a2e81d7
Create Date: 2026-09-08 10:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd1a7c4b90e33'
down_revision: Union[str, Sequence[str], None] = 'c93f5a2e81d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: The id ``product_conversion.py`` announced for a row before this column existed, as SQL.
#: The backfill MUST reproduce it exactly: buyers hold these ids (a ``PackageRequest``
#: names one), so a backfill that spelled them differently would strand every id already
#: in the wild. CPA keeps the ``fixed`` suffix regardless of ``is_fixed``, matching the
#: conversion branch that produced it.
_ANNOUNCED_ID = """
    lower(pricing_model) || '_' || lower(currency) || '_' ||
    CASE WHEN is_fixed OR lower(pricing_model) = 'cpa' THEN 'fixed' ELSE 'auction' END
"""


def upgrade() -> None:
    """Store the identifier the spec requires instead of recomputing it at five sites.

    ``pricing_option_id`` is in ``required`` for all nine ``pricing-options/*.json``
    members, and ``media-buy/package-request.json`` marks the buyer's copy
    ``x-entity: product_pricing_option`` — an entity reference. This table had no such
    column, so five call sites rebuilt the string ``{model}_{currency}_{fixed|auction}``
    from three other columns, and they did not agree: the resolver in
    ``media_buy_delivery`` left ``pricing_model`` in its stored case while the announcer
    lowercased it, so a row stored as ``CPM`` was announced as ``cpm_usd_auction`` and
    then resolved against ``CPM_usd_auction``, matching nothing.

    The backfill reproduces the announced id verbatim so ids already held by buyers keep
    resolving. Where a product has two rows that announced the SAME id -- possible today,
    since nothing prevented it, and the reader's dict silently dropped one of them -- the
    later row gets a ``_2``, ``_3`` suffix so the unique constraint can be created. That
    renames an id no buyer could have used reliably anyway: it named two rows.
    """
    op.add_column(
        "pricing_options",
        sa.Column("pricing_option_id", sa.String(length=100), nullable=True),
    )
    op.execute(f"UPDATE pricing_options SET pricing_option_id = {_ANNOUNCED_ID}")
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY tenant_id, product_id, pricing_option_id ORDER BY id
                   ) AS n
            FROM pricing_options
        )
        UPDATE pricing_options AS po
        SET pricing_option_id = po.pricing_option_id || '_' || ranked.n
        FROM ranked
        WHERE ranked.id = po.id AND ranked.n > 1
        """
    )
    op.alter_column("pricing_options", "pricing_option_id", nullable=False)
    op.create_unique_constraint(
        "uq_pricing_options_option_id",
        "pricing_options",
        ["tenant_id", "product_id", "pricing_option_id"],
    )


def downgrade() -> None:
    """Drop the column; readers fall back to recomputing the id from the other columns."""
    op.drop_constraint("uq_pricing_options_option_id", "pricing_options", type_="unique")
    op.drop_column("pricing_options", "pricing_option_id")
