"""merge format_parameters and gam_network_currency

Revision ID: 46b1bf1c82c5
Revises: 28cdf399fb73, add_gam_network_currency
Create Date: 2025-12-28 22:25:23.872932

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "46b1bf1c82c5"
down_revision: Union[str, Sequence[str], None] = ("28cdf399fb73", "add_gam_network_currency")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
