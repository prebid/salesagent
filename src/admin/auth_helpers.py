"""Shared API key authentication helpers for admin blueprints.

Provides a parameterized auth decorator factory used by both
tenant_management_api and sync_api to avoid duplicating the
header-read → key-lookup → compare flow.

An operator API key is stored the way a buyer token is: ``sha256`` of the key plus a
display prefix, never the key (``src/core/credentials.py``,
``TenantManagementConfigRepository``). So the compare below hashes what the request
presented instead of fetching something it could also display back. The one plaintext
comparison left is against the key an OPERATOR set in the environment — a deployment
secret this process was handed, not a row this application minted and stored.
"""

from __future__ import annotations

import hmac
import logging
from functools import wraps
from typing import Any

from flask import jsonify, request

from src.core.config import get_settings
from src.core.credentials import hash_token, mint_api_key, token_prefix
from src.core.database.database_session import get_db_session
from src.core.database.repositories.tenant_management_config import TenantManagementConfigRepository

logger = logging.getLogger(__name__)


def api_key_is_configured(configured: str | None, config_key: str) -> bool:
    """Whether an API key exists at all — in the settings or as a stored digest.

    Answers the 503 branch WITHOUT recovering a key. This used to be answered by a
    function that returned the usable key, which is the reason a stored plaintext had
    to exist in the first place.
    """
    if configured:
        return True
    with get_db_session() as session:
        return TenantManagementConfigRepository(session).api_key_digest(config_key) is not None


def api_key_prefix(config_key: str) -> str | None:
    """The display head of the stored API key at *config_key*, or None when unset.

    The whole of what an operator can learn about a key already in place: enough to
    say "one exists, and it starts like this", useless to present as one.
    """
    with get_db_session() as session:
        return TenantManagementConfigRepository(session).api_key_prefix(config_key)


def api_key_matches(presented: str, configured: str | None, config_key: str) -> bool:
    """Whether *presented* is the API key for *config_key*.

    The settings value wins when set, and it is a plaintext an operator handed this
    process, so it is compared as one. Otherwise the stored digest is compared against
    the hash of what the request presented — the row holds nothing else to compare.
    """
    if configured:
        return hmac.compare_digest(presented, configured)
    with get_db_session() as session:
        stored = TenantManagementConfigRepository(session).api_key_digest(config_key)
    if stored is None:
        return False
    return hmac.compare_digest(hash_token(presented), stored)


def mint_stored_api_key(config_key: str, description: str) -> str:
    """Mint the API key for *config_key*, store its digest and prefix, return it ONCE.

    Issuing and rotating are the same operation: whatever digest was there is replaced,
    so a previously issued key stops verifying the moment a new one is minted. That is
    deliberate — the old plaintext is unrecoverable, so "mint another and keep both" is
    not a state this can be in.

    The caller is the only holder of the return value; nothing reads it back.
    """
    api_key = mint_api_key()
    with get_db_session() as session:
        TenantManagementConfigRepository(session).store_api_key(
            config_key,
            digest=hash_token(api_key),
            prefix=token_prefix(api_key),
            description=description,
        )
        session.commit()
    logger.info(f"Minted API key for {config_key} (prefix {token_prefix(api_key)})")
    return api_key


def require_api_key_auth(*, setting: str, config_key: str, header: str) -> Any:
    """Factory that returns a Flask decorator for API key authentication.

    Args:
        setting: The ``AuthSettings`` field carrying the key. It is read per request,
            so a settings reload is seen without rebuilding the decorator. The field
            name upper-cased is the variable an operator sets, which is what the
            503 body names.
        config_key: The ``superadmin_config`` row whose stored digest is the fallback
        header: HTTP header name to read the key from
    """
    env_var = setting.upper()

    def decorator(f: Any) -> Any:
        @wraps(f)
        def decorated_function(*args: Any, **kwargs: Any) -> Any:
            api_key = request.headers.get(header)

            if not api_key:
                return jsonify({"error": "Missing API key"}), 401

            configured: str | None = getattr(get_settings().auth, setting)
            if not api_key_is_configured(configured, config_key):
                logger.error(f"API key not configured (setting: {env_var}, db: {config_key})")
                return jsonify({"error": f"API not configured. Set {env_var} environment variable."}), 503

            if not api_key_matches(api_key, configured, config_key):
                logger.warning(f"Invalid API key attempted (header: {header})")
                return jsonify({"error": "Invalid API key"}), 401

            return f(*args, **kwargs)

        return decorated_function

    return decorator
