"""add_ai_config_to_tenants

Add ai_config JSONB column to tenants table for Pydantic AI multi-model support.

This column stores per-tenant AI configuration including:
- provider: LLM provider (gemini, openai, anthropic, etc.)
- model: Model name (e.g., gemini-2.0-flash, claude-sonnet-4-20250514)
- api_key: Encrypted API key (uses existing encryption infrastructure)
- logfire_token: Optional Logfire observability token
- settings: Model behavior settings (temperature, max_tokens, etc.)

Revision ID: 4b11f64bbebe
Revises: a1b2c3d4e5f6
Create Date: 2025-12-20 07:33:19.548203

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision: str = "4b11f64bbebe"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add ai_config column to tenants table."""
    op.add_column(
        "tenants",
        sa.Column(
            "ai_config",
            JSONB,
            nullable=True,
            comment="Pydantic AI configuration: provider, model, api_key (encrypted), logfire_token, settings",
        ),
    )


def downgrade() -> None:
    """Remove ai_config column from tenants table."""
    op.drop_column("tenants", "ai_config")
