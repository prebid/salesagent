"""encrypt the GAM refresh token at rest

``adapter_config.gam_refresh_token`` holds a tenant's OAuth refresh token for Google Ad
Manager: a credential to the publisher's ad server. The service account JSON beside it has
always been stored encrypted with the deployment's ``ENCRYPTION_KEY``; the refresh token was
stored in plaintext. From this revision ``AdapterConfig.gam_refresh_token`` encrypts on write
and decrypts on read, the same as ``gam_service_account_json``, so every stored token has to
be ciphertext.

UPGRADE: every non-null token that does not decrypt with this deployment's
``ENCRYPTION_KEY`` is encrypted with it. A token that already decrypts is left as it is, so
the upgrade can run twice. Fernet tokens are authenticated, so a successful decrypt means
the value was written with this key; a plaintext refresh token cannot pass by accident. If
there are tokens to encrypt and no usable ``ENCRYPTION_KEY`` is configured, the upgrade
stops and names the tenants: leaving them in plaintext would make the application fail on
every read of the token.

A value that does not decrypt but has the form of a Fernet token (``gAAAAA``…; a Google
refresh token starts ``1//``) was encrypted with a different key, for example after a
restore or a key rotation. Encrypting it again would make the application decrypt one layer
and send ciphertext to Google, so the upgrade stops and names those tenants too. The
downgrade does the same instead of leaving such a value as if it were plaintext.

DOWNGRADE: every token that decrypts with ``ENCRYPTION_KEY`` is written back as plaintext,
which is what the previous revision's model reads. A value that does not decrypt is already
plaintext and is left as it is. With tokens stored and no ``ENCRYPTION_KEY`` configured,
the downgrade stops rather than leave ciphertext the previous model would send to Google
as a token.

Revision ID: 9fdf18a56063
Revises: e2af0c242a7f
Create Date: 2026-11-17 12:00:00.000000

"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9fdf18a56063"
down_revision: str | Sequence[str] | None = "e2af0c242a7f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_adapter_config = sa.table(
    "adapter_config",
    sa.column("tenant_id", sa.String),
    sa.column("gam_refresh_token", sa.Text),
)


def _stored_tokens() -> list[Any]:
    """Every ``(tenant_id, gam_refresh_token)`` with a token stored."""
    stmt = (
        sa.select(_adapter_config.c.tenant_id, _adapter_config.c.gam_refresh_token)
        .where(_adapter_config.c.gam_refresh_token.isnot(None))
        .where(_adapter_config.c.gam_refresh_token != "")
        .order_by(_adapter_config.c.tenant_id)
    )
    return list(op.get_bind().execute(stmt))


def _decrypted(value: str) -> str | None:
    """The plaintext of *value* if it decrypts with ``ENCRYPTION_KEY``, else ``None``."""
    from src.core.utils.encryption import decrypt_api_key

    try:
        return decrypt_api_key(value)
    except ValueError:
        return None


def _is_fernet_token(value: str) -> bool:
    """Whether *value* has the form of a Fernet token: version byte 0x80 and a 64-bit timestamp
    whose high bytes are zero, which base64-encode to ``gAAAAA``."""
    return value.startswith("gAAAAA")


def _refuse_foreign_ciphertext(tenant_ids: list[str]) -> None:
    if tenant_ids:
        raise RuntimeError(
            f"The GAM refresh token of {len(tenant_ids)} tenant(s) is encrypted, but not with this deployment's "
            f"ENCRYPTION_KEY: {', '.join(tenant_ids)}. Set the ENCRYPTION_KEY those tokens were written with, or "
            "clear and re-enter those tenants' GAM credentials, then re-run the migration."
        )


def _write(tenant_id: str, value: str) -> None:
    op.get_bind().execute(
        sa.update(_adapter_config).where(_adapter_config.c.tenant_id == tenant_id).values(gam_refresh_token=value)
    )


def upgrade() -> None:
    """Encrypt every stored refresh token that is not already encrypted."""
    from src.core.utils.encryption import encrypt_api_key

    writes: dict[str, str] = {}
    unencryptable: list[str] = []
    foreign: list[str] = []
    for tenant_id, token in _stored_tokens():
        if _decrypted(token) is not None:
            continue
        if _is_fernet_token(token):
            foreign.append(tenant_id)
            continue
        try:
            writes[tenant_id] = encrypt_api_key(token)
        except ValueError:
            unencryptable.append(tenant_id)

    _refuse_foreign_ciphertext(foreign)
    if unencryptable:
        raise RuntimeError(
            f"Cannot encrypt the GAM refresh token of {len(unencryptable)} tenant(s): no usable "
            f"ENCRYPTION_KEY is configured: {', '.join(unencryptable)}. The application now reads this "
            "token encrypted, so it cannot be left in plaintext. Set ENCRYPTION_KEY (the same key the "
            "deployment already uses for service account and OIDC secrets, if it has one), then re-run "
            "the migration."
        )

    for tenant_id, ciphertext in writes.items():
        _write(tenant_id, ciphertext)


def downgrade() -> None:
    """Write every encrypted refresh token back as plaintext."""
    from src.core.config import get_settings

    rows = _stored_tokens()
    if rows and not get_settings().auth.encryption_key:
        raise RuntimeError(
            f"Cannot decrypt the GAM refresh token of {len(rows)} tenant(s): ENCRYPTION_KEY is not set: "
            f"{', '.join(tenant_id for tenant_id, _ in rows)}. Set the ENCRYPTION_KEY the tokens were "
            "written with, then re-run the downgrade."
        )

    decrypted = {tenant_id: _decrypted(token) for tenant_id, token in rows}
    _refuse_foreign_ciphertext(
        [tenant_id for tenant_id, token in rows if decrypted[tenant_id] is None and _is_fernet_token(token)]
    )
    for tenant_id, plaintext in decrypted.items():
        if plaintext is not None:
            _write(tenant_id, plaintext)
