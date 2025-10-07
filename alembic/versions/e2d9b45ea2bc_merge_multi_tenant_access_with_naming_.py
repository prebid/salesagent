"""merge_multi_tenant_access_with_naming_templates

Revision ID: e2d9b45ea2bc
Revises: aff9ca8baa9c, ede76bc258af
Create Date: 2025-10-07 05:48:17.609476

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "e2d9b45ea2bc"
down_revision: str | Sequence[str] | None = ("aff9ca8baa9c", "ede76bc258af")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
