"""hash the operator API keys in superadmin_config

Revision ID: c7e2a5b40d18
Revises: b3d9c41e7a02
Create Date: 2026-09-14 10:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7e2a5b40d18"
down_revision: str | Sequence[str] | None = "b3d9c41e7a02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The two credential rows in this key/value table. Every other row holds
#: configuration, not a secret, and is left alone.
_API_KEY_ROWS = ("api_key", "tenant_management_api_key")


def upgrade() -> None:
    """Store sha256(key) and a display prefix instead of the key itself.

    ``superadmin_config.config_value`` held the operator API keys in plaintext, and the
    sync-API initializer returned the stored value on every call — the same
    displayed-back shape e4b7c2a91f05 removed for principal tokens. These are operator
    credentials rather than buyer ones, which is why they were out of that migration's
    scope, not why they should be readable.

    Existing rows are hashed IN PLACE from their plaintext, so every key already issued
    keeps working; a companion ``<key>_prefix`` row gets the first twelve characters,
    which is all an operator can learn about a key afterwards.
    """
    for config_key in _API_KEY_ROWS:
        op.execute(
            f"""
            INSERT INTO superadmin_config (config_key, config_value, description, updated_by, updated_at)
            SELECT '{config_key}_prefix', left(config_value, 12),
                   'Display prefix of {config_key}', 'migration', NOW()
            FROM superadmin_config
            WHERE config_key = '{config_key}' AND config_value IS NOT NULL
            ON CONFLICT (config_key) DO UPDATE SET config_value = EXCLUDED.config_value
            """
        )
        op.execute(
            f"""
            UPDATE superadmin_config
            SET config_value = encode(sha256(convert_to(config_value, 'UTF8')), 'hex'),
                updated_by = 'migration',
                updated_at = NOW()
            WHERE config_key = '{config_key}' AND config_value IS NOT NULL
            """
        )


def downgrade() -> None:
    """Drop the prefix rows and mark the key rows as needing re-issue.

    The plaintext keys cannot be recovered from their hashes. Each ``config_value``
    comes back as ``rehash:`` plus the hash — which is not a valid key and says so —
    so an operator reading the table sees that the key must be minted again rather
    than a string that looks usable. This is the shape e4b7c2a91f05's downgrade used
    for ``principals.access_token``, for the same reason.
    """
    for config_key in _API_KEY_ROWS:
        op.execute(f"DELETE FROM superadmin_config WHERE config_key = '{config_key}_prefix'")
        op.execute(
            f"""
            UPDATE superadmin_config
            SET config_value = 'rehash:' || config_value,
                updated_by = 'migration',
                updated_at = NOW()
            WHERE config_key = '{config_key}' AND config_value IS NOT NULL
            """
        )
