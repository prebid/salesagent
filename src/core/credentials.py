"""Credentials: minted once, stored as a hash, matched by hash.

A credential this seller issues is a 256-bit random value. It is shown to the operator
exactly once, when it is minted or rotated, and what the database keeps is ``sha256(value)``
plus a short display prefix so an operator can tell two apart. A random value of that size
is not a password, so no slow hash and no salt: an attacker with the table cannot invert
SHA-256 on 256 bits of entropy, and the equality lookup stays an index hit.

This module is the one place the facts about a credential are spelled out: how one is
minted, how one is hashed, and how much of one is shown. The resolver hashes what a request
presents (``src/core/auth_utils.get_principal_from_token``); the ``Principal`` row hashes
what it stores; the two operator API keys in ``superadmin_config`` store the same way
(``src/admin/auth_helpers.py``); nothing else touches a plaintext.

Two kinds, one treatment. A buyer token (``tok_``) authenticates a principal; an operator
API key (``sk_``) authenticates a human running the tenant-management and sync surfaces.
The prefix is the only difference — the entropy, the hash and the shown-once rule are
shared, which is why they are written once here rather than twice.
"""

from __future__ import annotations

import hashlib
import secrets

#: How much of a credential the admin UI shows. Enough to tell two apart, useless to present.
TOKEN_PREFIX_LENGTH = 12

#: Bytes of randomness behind every credential this module mints.
_ENTROPY_BYTES = 32


def mint_token() -> str:
    """A fresh buyer token: ``tok_`` and 32 URL-safe random bytes."""
    return f"tok_{secrets.token_urlsafe(_ENTROPY_BYTES)}"


def mint_api_key() -> str:
    """A fresh operator API key: ``sk_`` and 32 URL-safe random bytes.

    Same entropy and same storage as a buyer token — an operator key opens the
    tenant-management and sync surfaces, which is not a smaller thing to hold.
    """
    return f"sk_{secrets.token_urlsafe(_ENTROPY_BYTES)}"


def hash_token(token: str) -> str:
    """The stored form of *token*: hex SHA-256 of its UTF-8 bytes."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_prefix(token: str) -> str:
    """The displayable head of *token*."""
    return token[:TOKEN_PREFIX_LENGTH]
