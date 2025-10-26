"""add_pricing_option_constraints

Revision ID: a098c8bb42ed
Revises: e38f2f6f395a
Create Date: 2025-10-27 12:59:52.544166

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a098c8bb42ed'
down_revision: Union[str, Sequence[str], None] = 'e38f2f6f395a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add database constraints to enforce pricing option validation rules.

    These constraints ensure:
    1. Auction pricing (is_fixed=false) must have price_guidance with a floor value
    2. Fixed pricing (is_fixed=true) must have a rate

    This prevents invalid pricing options from being created at the database level,
    matching the Pydantic schema validation rules.
    """
    # First, fix any existing invalid data (auction pricing without price_guidance)
    op.execute("""
        UPDATE pricing_options
        SET price_guidance = jsonb_build_object('floor', COALESCE(rate, 0.0))
        WHERE is_fixed = false
          AND (price_guidance IS NULL OR NOT price_guidance ? 'floor')
    """)

    # Add constraint: auction pricing requires price_guidance with floor
    op.create_check_constraint(
        'check_auction_has_price_guidance',
        'pricing_options',
        "(is_fixed = true) OR (is_fixed = false AND price_guidance IS NOT NULL AND price_guidance ? 'floor')"
    )

    # Add constraint: fixed pricing requires rate
    op.create_check_constraint(
        'check_fixed_has_rate',
        'pricing_options',
        "(is_fixed = false) OR (is_fixed = true AND rate IS NOT NULL)"
    )


def downgrade() -> None:
    """Remove pricing option constraints."""
    op.drop_constraint('check_fixed_has_rate', 'pricing_options')
    op.drop_constraint('check_auction_has_price_guidance', 'pricing_options')
