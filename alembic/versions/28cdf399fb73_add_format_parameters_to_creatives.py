"""add format_parameters to creatives

Revision ID: 28cdf399fb73
Revises: 4b11f64bbebe
Create Date: 2025-12-25 14:37:08.561847

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "28cdf399fb73"
down_revision: Union[str, Sequence[str], None] = "4b11f64bbebe"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add format_parameters JSONB column to creatives table.

    This column stores parameterized FormatId fields (width, height, duration_ms)
    for creative format templates (AdCP 2.5). When present, these parameters
    combined with agent_url and format create a parameterized format ID.

    Example values:
    - {"width": 300, "height": 250}  # Display creative
    - {"duration_ms": 15000}  # Video creative (15 seconds)
    - {"width": 1920, "height": 1080, "duration_ms": 30000}  # Video with dimensions
    - NULL  # Template format without parameters
    """
    op.add_column(
        "creatives",
        sa.Column(
            "format_parameters",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="Parameterized FormatId fields (width, height, duration_ms) for format templates",
        ),
    )


def downgrade() -> None:
    """Remove format_parameters column."""
    op.drop_column("creatives", "format_parameters")
