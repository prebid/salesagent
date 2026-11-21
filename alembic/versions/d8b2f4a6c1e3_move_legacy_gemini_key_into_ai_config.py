"""move a tenant's legacy gemini_api_key into its ai_config

A tenant's AI key lives in ``tenants.ai_config``: the admin AI settings form
(``settings.update_ai``) writes it there, and both the creative AI review and the
generative creative build read it (``src.services.ai.config.tenant_google_api_key``).
Tenants configured before ``ai_config`` existed hold their key in the encrypted
``tenants.gemini_api_key`` column instead, and until now the AI review rebuilt
``{"provider": "gemini", "api_key": <that key>}`` from it every time it ran on a tenant
with no ``ai_config``. That read-time fallback is deleted with this revision, so the rows
have to carry the configuration themselves.

UPGRADE: every tenant with a ``gemini_api_key`` and an empty ``ai_config`` (NULL, JSON
``null`` or ``{}``) gets ``ai_config = {"provider": "gemini", "api_key": <decrypted key>}``,
exactly the configuration the deleted fallback built, so the AI review answers the same
before and after. A tenant whose ``ai_config`` already says anything is left as it is,
as the fallback left it. A legacy key that does not decrypt with this deployment's
``ENCRYPTION_KEY`` stops the upgrade, naming the tenants: the application could not read
that key either, and there is no value to write.

``gemini_api_key`` itself is left in place; other readers still use it.

DOWNGRADE clears ``ai_config`` on exactly the rows the upgrade wrote: those whose
``ai_config`` is ``{"provider": "gemini", "api_key": K}`` where ``K`` is the row's own
decrypted ``gemini_api_key``. The previous revision's fallback rebuilds that same
configuration from the column.

Revision ID: d8b2f4a6c1e3
Revises: b6e2d94f1a07
Create Date: 2026-10-29 20:00:00.000000

"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa

from alembic import op
from src.core.database.json_type import JSONType

# revision identifiers, used by Alembic.
revision: str = "d8b2f4a6c1e3"
down_revision: str | Sequence[str] | None = "b6e2d94f1a07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_tenants = sa.table(
    "tenants",
    sa.column("tenant_id", sa.String),
    sa.column("gemini_api_key", sa.String),
    sa.column("ai_config", JSONType()),
)


def _legacy_rows() -> list[Any]:
    """Every tenant that holds a legacy key, with its current ``ai_config``."""
    stmt = (
        sa.select(_tenants.c.tenant_id, _tenants.c.gemini_api_key, _tenants.c.ai_config)
        .where(_tenants.c.gemini_api_key.isnot(None))
        .order_by(_tenants.c.tenant_id)
    )
    return list(op.get_bind().execute(stmt))


def _configuration(api_key: str) -> dict[str, str]:
    """The ``ai_config`` the deleted AI-review fallback built from a legacy key."""
    return {"provider": "gemini", "api_key": api_key}


def upgrade() -> None:
    """Write each legacy key into an empty ``ai_config``."""
    from src.core.utils.encryption import decrypt_api_key

    writes: dict[str, dict[str, str]] = {}
    undecryptable: list[str] = []
    for tenant_id, ciphertext, ai_config in _legacy_rows():
        if ai_config:
            continue
        try:
            writes[tenant_id] = _configuration(decrypt_api_key(ciphertext))
        except ValueError:
            undecryptable.append(tenant_id)

    if undecryptable:
        raise RuntimeError(
            f"Cannot move the legacy gemini_api_key of {len(undecryptable)} tenant(s) into ai_config: "
            f"the key does not decrypt with this deployment's ENCRYPTION_KEY: {', '.join(undecryptable)}. "
            "Set the ENCRYPTION_KEY the keys were written with, or clear or re-enter those tenants' "
            "AI settings, then re-run the migration."
        )

    bind = op.get_bind()
    for tenant_id, configuration in writes.items():
        bind.execute(sa.update(_tenants).where(_tenants.c.tenant_id == tenant_id).values(ai_config=configuration))


def downgrade() -> None:
    """Clear ``ai_config`` where it is exactly the configuration the upgrade wrote."""
    from src.core.utils.encryption import decrypt_api_key

    bind = op.get_bind()
    for tenant_id, ciphertext, ai_config in _legacy_rows():
        try:
            written = _configuration(decrypt_api_key(ciphertext))
        except ValueError:
            # The upgrade refuses a key that does not decrypt, so it wrote nothing here.
            continue
        if ai_config == written:
            bind.execute(sa.update(_tenants).where(_tenants.c.tenant_id == tenant_id).values(ai_config=sa.null()))
