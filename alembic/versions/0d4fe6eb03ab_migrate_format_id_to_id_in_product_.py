"""Migrate format_id to id in product formats

Revision ID: 0d4fe6eb03ab
Revises: 6ac7f95b69c6
Create Date: 2025-10-16 13:11:37.726255

This migration fixes a schema inconsistency where product formats stored
format_id instead of id (the AdCP spec-compliant field name).

The FormatReference Pydantic model uses serialization_alias="id" to ensure
JSON serialization uses "id", but older database records have "format_id".

This migration:
1. Renames "format_id" → "id" in all format objects in products.formats JSONB column
2. Preserves all other fields (agent_url, etc.)
3. Handles both cases: format_id and id (idempotent)

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0d4fe6eb03ab"
down_revision: str | Sequence[str] | None = "6ac7f95b69c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Migrate format_id to id in product formats JSONB."""

    # PostgreSQL JSONB update to rename format_id → id in each format object
    # This uses jsonb_set and jsonb_build_object to transform each array element
    op.execute(
        """
        UPDATE products
        SET formats = (
            SELECT jsonb_agg(
                CASE
                    -- If format has format_id but not id, rename it
                    WHEN format_obj ? 'format_id' AND NOT format_obj ? 'id' THEN
                        jsonb_set(
                            format_obj - 'format_id',
                            '{id}',
                            format_obj->'format_id'
                        )
                    -- Otherwise keep as-is (already has id, or missing both)
                    ELSE format_obj
                END
            )
            FROM jsonb_array_elements(formats) AS format_obj
        )
        WHERE formats IS NOT NULL
          AND formats != '[]'::jsonb
          -- Only update products that have format_id (not id) in any format
          AND EXISTS (
              SELECT 1
              FROM jsonb_array_elements(formats) AS fmt
              WHERE fmt ? 'format_id' AND NOT fmt ? 'id'
          )
    """
    )


def downgrade() -> None:
    """Revert id back to format_id in product formats JSONB."""

    # Reverse migration: rename id → format_id
    op.execute(
        """
        UPDATE products
        SET formats = (
            SELECT jsonb_agg(
                CASE
                    -- If format has id but not format_id, rename it back
                    WHEN format_obj ? 'id' AND NOT format_obj ? 'format_id' THEN
                        jsonb_set(
                            format_obj - 'id',
                            '{format_id}',
                            format_obj->'id'
                        )
                    -- Otherwise keep as-is
                    ELSE format_obj
                END
            )
            FROM jsonb_array_elements(formats) AS format_obj
        )
        WHERE formats IS NOT NULL
          AND formats != '[]'::jsonb
          AND EXISTS (
              SELECT 1
              FROM jsonb_array_elements(formats) AS fmt
              WHERE fmt ? 'id' AND NOT fmt ? 'format_id'
          )
    """
    )
