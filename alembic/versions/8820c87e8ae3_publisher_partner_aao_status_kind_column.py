"""publisher_partner_aao_status_kind_column

Revision ID: 8820c87e8ae3
Revises: 17423a1b551e
Create Date: 2026-05-12 19:33:37.615102

Adds ``aao_status_kind`` to ``publisher_partners`` so the Publisher
Partnerships UI can render distinct chips for the operationally-different
adagents.json states the prior derivation collapsed into "Pending 0/0":
``unbound`` (file fetched, products bind permissively to top-level
properties, but the publisher's entry isn't spec-conformant) vs
``no_properties`` (file fetched but exposes zero inventory) vs
``pending`` (file fetched, publisher just hasn't authorized us). See
salesagent#377 for the four-state operator model.

Stores the literal returned by
``aao_lookup_service.PublisherPartnerStatusKind`` (``authorized`` |
``unbound`` | ``pending`` | ``no_properties`` | ``unreachable``). NULL
for rows that haven't been refreshed since this migration — those fall
back to legacy derivation in ``_partner_to_dict``.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '8820c87e8ae3'
down_revision: Union[str, Sequence[str], None] = '17423a1b551e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "publisher_partners",
        sa.Column("aao_status_kind", sa.String(length=20), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("publisher_partners", "aao_status_kind")
