"""rename_gam_axe_fields_to_adapter_agnostic

Revision ID: 240284b2f169
Revises: 5b7de72d6110
Create Date: 2025-11-14 11:02:09.729490

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "240284b2f169"
down_revision: Union[str, Sequence[str], None] = "5b7de72d6110"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Rename gam_axe_* fields to axe_* to make them adapter-agnostic.

    Per user feedback, AXE segment targeting should work for all adapters (GAM, Kevel, Mock, etc.),
    not just GAM. Renaming fields to remove GAM-specific prefix.
    """
    # Rename the three AXE key fields to be adapter-agnostic
    op.alter_column("adapter_config", "gam_axe_include_key", new_column_name="axe_include_key")
    op.alter_column("adapter_config", "gam_axe_exclude_key", new_column_name="axe_exclude_key")
    op.alter_column("adapter_config", "gam_axe_macro_key", new_column_name="axe_macro_key")


def downgrade() -> None:
    """Revert field names back to gam_axe_* prefix."""
    op.alter_column("adapter_config", "axe_include_key", new_column_name="gam_axe_include_key")
    op.alter_column("adapter_config", "axe_exclude_key", new_column_name="gam_axe_exclude_key")
    op.alter_column("adapter_config", "axe_macro_key", new_column_name="gam_axe_macro_key")
