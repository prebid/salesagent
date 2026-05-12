"""replace buyer_ref with media_buy_id in order_name_template

Revision ID: b4e2bffdd4f8
Revises: cf421634d3c8, 9cc36dfc54f6
Create Date: 2026-05-01 10:28:42.996271

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4e2bffdd4f8"
down_revision: str | Sequence[str] | None = ("cf421634d3c8", "9cc36dfc54f6")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Replace {buyer_ref} with {media_buy_id} in existing order_name_template values.

    buyer_ref was removed from CreateMediaBuyRequest (adcp 3.12).
    Existing tenants may have templates referencing {buyer_ref} from older migrations
    (31ff6218695a, faaed3b71428). These now render as empty string, causing
    double-space artifacts in order names.
    """
    # Update existing templates that reference {buyer_ref}
    op.execute(
        """
        UPDATE tenants
        SET order_name_template = REPLACE(order_name_template, '{buyer_ref}', '{media_buy_id}')
        WHERE order_name_template LIKE '%{buyer_ref}%'
        """
    )

    # Update server_default to use {media_buy_id} instead of {buyer_ref}
    op.alter_column(
        "tenants",
        "order_name_template",
        server_default="{campaign_name|brand_name} - {media_buy_id} - {date_range}",
    )


def downgrade() -> None:
    """Revert {media_buy_id} back to {buyer_ref} in order_name_template."""
    op.execute(
        """
        UPDATE tenants
        SET order_name_template = REPLACE(order_name_template, '{media_buy_id}', '{buyer_ref}')
        WHERE order_name_template LIKE '%{media_buy_id}%'
        """
    )

    op.alter_column(
        "tenants",
        "order_name_template",
        server_default="{campaign_name|brand_name} - {buyer_ref} - {date_range}",
    )
