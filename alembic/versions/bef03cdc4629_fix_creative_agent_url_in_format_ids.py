"""fix_creative_agent_url_in_format_ids

Revision ID: bef03cdc4629
Revises: b51bbaf5a6ba
Create Date: 2025-11-16 21:26:55.793170

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "bef03cdc4629"
down_revision: Union[str, Sequence[str], None] = "b51bbaf5a6ba"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Fix agent URLs in format_ids: creatives → creative."""
    # Update all format_ids in all products with wrong agent URL
    # Must iterate through array elements to update ALL formats, not just first one
    op.execute(
        """
        UPDATE products
        SET format_ids = (
            SELECT COALESCE(
                jsonb_agg(
                    jsonb_build_object(
                        'agent_url', REPLACE(
                            elem->>'agent_url',
                            'creatives.adcontextprotocol.org',
                            'creative.adcontextprotocol.org'
                        ),
                        'id', elem->>'id'
                    )
                ),
                '[]'::jsonb
            )
            FROM jsonb_array_elements(format_ids) elem
        )
        WHERE format_ids::text LIKE '%creatives.adcontextprotocol.org%'
    """
    )


def downgrade() -> None:
    """Revert agent URLs back to creatives."""
    # Revert all format_ids in all products back to old URL
    op.execute(
        """
        UPDATE products
        SET format_ids = (
            SELECT COALESCE(
                jsonb_agg(
                    jsonb_build_object(
                        'agent_url', REPLACE(
                            elem->>'agent_url',
                            'creative.adcontextprotocol.org',
                            'creatives.adcontextprotocol.org'
                        ),
                        'id', elem->>'id'
                    )
                ),
                '[]'::jsonb
            )
            FROM jsonb_array_elements(format_ids) elem
        )
        WHERE format_ids::text LIKE '%creative.adcontextprotocol.org%'
    """
    )
