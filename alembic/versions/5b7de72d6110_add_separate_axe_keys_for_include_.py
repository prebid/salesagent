"""add_separate_axe_keys_for_include_exclude_macro

Revision ID: 5b7de72d6110
Revises: 039d59477ab4
Create Date: 2025-11-14 10:17:03.312132

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "5b7de72d6110"
down_revision: Union[str, Sequence[str], None] = "039d59477ab4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add separate GAM custom targeting keys for AXE include, exclude, and macro segments.

    Per AdCP spec, AXE (Audience Exchange) requires three distinct custom targeting keys:
    - gam_axe_include_key: For axe_include_segment targeting
    - gam_axe_exclude_key: For axe_exclude_segment targeting
    - gam_axe_macro_key: For creative macro substitution

    The old gam_axe_custom_targeting_key field is kept for backwards compatibility.
    """
    # Add three new columns for separate AXE key types
    op.add_column("adapter_config", sa.Column("gam_axe_include_key", sa.String(100), nullable=True))
    op.add_column("adapter_config", sa.Column("gam_axe_exclude_key", sa.String(100), nullable=True))
    op.add_column("adapter_config", sa.Column("gam_axe_macro_key", sa.String(100), nullable=True))


def downgrade() -> None:
    """Remove separate AXE key columns."""
    op.drop_column("adapter_config", "gam_axe_macro_key")
    op.drop_column("adapter_config", "gam_axe_exclude_key")
    op.drop_column("adapter_config", "gam_axe_include_key")
