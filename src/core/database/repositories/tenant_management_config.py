"""The global ``superadmin_config`` key/value rows, and the operator API keys in them.

Deliberately NOT tenant-scoped: ``superadmin_config`` is the installation's own
configuration, not a tenant's. There is one row per named fact and no tenant column
to scope by.

Two of those facts are credentials — the tenant-management API key and the sync API
key — and this repository owns the rule that makes them credentials rather than
strings: what is stored is ``sha256`` of the key plus a display prefix, written
TOGETHER, so a rotation can never leave a prefix behind that belongs to a key that no
longer verifies. The key itself is returned to the operator once, by the caller that
minted it, and is unrecoverable afterwards (``src/core/credentials.py``).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.database.models import TenantManagementConfig


def prefix_config_key(config_key: str) -> str:
    """The companion row holding *config_key*'s display prefix.

    Derived from the key it belongs to rather than spelled out at each site: the two
    rows have to name each other, or a reader has no way to tell which key a prefix
    describes.
    """
    return f"{config_key}_prefix"


class TenantManagementConfigRepository:
    """Read and write access to the global ``superadmin_config`` rows.

    Args:
        session: SQLAlchemy session (caller manages lifecycle).
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_value(self, config_key: str) -> str | None:
        """The value at *config_key*, or None when there is no such row (or it is empty)."""
        row = self._session.scalars(select(TenantManagementConfig).filter_by(config_key=config_key)).first()
        return row.config_value if row and row.config_value else None

    def api_key_digest(self, config_key: str) -> str | None:
        """The stored ``sha256`` of the API key at *config_key*, or None when unset.

        Named for what it is. Nothing in this table holds an API key in a form that
        can be presented, so there is no method that returns one.
        """
        return self.get_value(config_key)

    def api_key_prefix(self, config_key: str) -> str | None:
        """The display head of the API key at *config_key*, or None when unset."""
        return self.get_value(prefix_config_key(config_key))

    def store_api_key(self, config_key: str, *, digest: str, prefix: str, description: str) -> None:
        """Write *digest* and *prefix* for *config_key* as one unit. The caller commits.

        Upsert, because issuing and rotating are the same operation: the previous
        digest is replaced, so the key it belonged to stops verifying immediately.
        """
        for key, value, row_description in (
            (config_key, digest, description),
            (prefix_config_key(config_key), prefix, f"Display prefix of {config_key}"),
        ):
            row = self._session.scalars(select(TenantManagementConfig).filter_by(config_key=key)).first()
            if row is None:
                self._session.add(
                    TenantManagementConfig(
                        config_key=key,
                        config_value=value,
                        description=row_description,
                        updated_by="system",
                    )
                )
            else:
                row.config_value = value
                row.updated_by = "system"
