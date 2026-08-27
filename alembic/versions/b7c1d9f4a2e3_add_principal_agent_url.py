"""add principals.agent_url

Revision ID: b7c1d9f4a2e3
Revises: 04c04fef9503
Create Date: 2026-07-28 10:40:00.000000

The counterparty's own AdCP agent URL, recorded at onboarding (#1291 B1).

Load-bearing for inbound RFC 9421 verification: it is the key the verifier resolves a
signing counterparty's JWKS from (agent_url -> capabilities -> brand.json -> jwks_uri).
``security.mdx`` @ v3.1.1 :1212/:1216 forbid taking that URL from a request header, a
body field or any other self-assertion — a signer that picks its own agent URL picks
which key set it is checked against — so the onboarding record is the only source the
spec allows.

Nullable, with no backfill: every existing principal predates request signing, and NULL
is the honest value for "we have not recorded this counterparty's agent URL". It means
no key can be resolved for that principal, never that its requests are trusted.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7c1d9f4a2e3"
down_revision: str | Sequence[str] | None = "04c04fef9503"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "principals",
        sa.Column(
            "agent_url",
            sa.String(length=500),
            nullable=True,
            comment=(
                "Counterparty's AdCP agent URL from onboarding; the resolution key for its "
                "RFC 9421 signing JWKS. Never taken from a request (security.mdx :1212/:1216)"
            ),
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("principals", "agent_url")
