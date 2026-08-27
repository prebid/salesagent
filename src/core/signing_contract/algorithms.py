"""The signing value-sets, with exactly one source of truth each.

Two value-sets govern every signing key we mint, and they are sourced
differently on purpose:

* ``SIGNING_ALG_VALUES`` — DERIVED from ``adcp.signing.crypto.ALLOWED_ALGS``.
  The ``signing_keys.alg`` CHECK constraint is built from it, never from a
  hand-copied literal: migration ``e381618812f1`` exists because a hand-written
  CHECK froze while a spec enum grew and a spec-valid value then IntegrityError'd
  on INSERT (#1521). ``tests/unit/test_signing_alg_parity.py`` pins the constant
  against the AdCP 3.1.1 algorithms enum, so an SDK bump that gains or loses a
  value fails loudly instead of drifting.

* ``MINTABLE_PURPOSES`` — a deliberate NARROWING below the SDK's accepted set,
  so it cannot be derived. See its own comment.

Three ``alg`` namespaces exist and they do not agree with each other. Every
mis-mapping is a signature every conformant verifier rejects, so the mapping
lives here once:

===============================  ====================  ===============
Namespace                        Ed25519               ECDSA P-256
===============================  ====================  ===============
RFC 9421 / this DB column        ``ed25519``           ``ecdsa-p256-sha256``
``adcp.signing.keygen``          ``ed25519``           ``es256``
the published JWK's ``alg``      ``EdDSA``             ``ES256``
===============================  ====================  ===============

Only the first is stored, and ``public_jwk`` is published verbatim as the SDK
emitted it — nothing here re-derives a JWK ``alg``. Anywhere a JWK must yield an
RFC 9421 name, use ``adcp.signing.alg_for_jwk``.
"""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import Literal, cast

# ``BrandAgentType`` is re-exported for the same reason the value-sets live here:
# ``src.core.config`` types ``counterparty_agent_type`` with it, and the boundary guard
# forbids a caller outside the layer importing ``adcp.signing`` directly, because the
# layer owns the signing contract and callers never see whether the SDK or a vendored
# copy provides the Literal. ``signing.request_verifier_middleware`` cannot be its home:
# it imports ``src.core.config`` back, which is the cycle documented below.
from adcp.signing.agent_resolver import BrandAgentType as BrandAgentType
from adcp.signing.crypto import ALG_ED25519, ALG_ES256, ALLOWED_ALGS
from adcp.signing.provider import SigningAlgorithm

from src.core.exceptions import AdCPConfigurationError

# ``ALLOWED_ALGS`` is a ``frozenset[str]``, and CPython randomizes str hashes per
# process — iterating it yields a different order on every run. This value lands
# in DDL text (the ``ck_signing_keys_alg`` CHECK body), so the order is part of
# the contract: unsorted, the ORM DDL and the migration DDL can disagree and
# alembic autogenerate churns.
SIGNING_ALG_VALUES: tuple[str, ...] = tuple(sorted(ALLOWED_ALGS))

# The SDK's keygen accepts ("request-signing", "webhook-signing"); we mint the
# first ONLY. ``webhook-signing`` is deprecated pending removal (security.mdx
# "adcp_use" §, adcp#5555) and webhooks are signed with a request-signing key
# (security.mdx "One JWK per adcp_use") — blast-radius isolation comes from a
# second key under a distinct ``kid``, not from a distinct purpose. So this is a
# hand-written literal ON PURPOSE: deriving it from ``keygen._ADCP_USE_VALUES``
# would wrongly admit the value we are refusing to stamp into published material.
# The direction a narrowing cannot self-check — that the SDK still accepts what
# we DO mint — is pinned by tests/unit/test_signing_alg_parity.py.
MINTABLE_PURPOSES: tuple[str, ...] = ("request-signing",)

REQUEST_SIGNING = "request-signing"

# Column value -> the name ``adcp.signing.keygen`` speaks. The single keygen call
# site (src/core/signing/keys.py) reaches it through ``keygen_alg`` — passing a
# column value straight to the SDK raises ValueError, and storing a keygen value
# in the column publishes a non-schema algorithm.
_KEYGEN_ALG: dict[str, Literal["ed25519", "es256"]] = {
    ALG_ED25519: "ed25519",
    ALG_ES256: "es256",
}


def keygen_alg(alg: str) -> Literal["ed25519", "es256"]:
    """Translate a stored ``alg`` into the name ``adcp.signing.keygen`` accepts."""
    try:
        return _KEYGEN_ALG[alg]
    except KeyError:
        raise AdCPConfigurationError(
            f"Signing algorithm {alg!r} is not in the AdCP profile {SIGNING_ALG_VALUES!r}"
        ) from None


def narrow_alg(alg: str) -> SigningAlgorithm:
    """Validate a stored ``alg`` and return it as the SDK's ``SigningAlgorithm``.

    The column is ``Mapped[str]`` while ``InMemorySigningProvider(algorithm=...)``
    wants a ``Literal`` — so this is what keeps mypy honest at the boundary. It is
    also the runtime guard for a row written before the CHECK existed.
    """
    if alg not in SIGNING_ALG_VALUES:
        raise AdCPConfigurationError(f"Signing algorithm {alg!r} is not in the AdCP profile {SIGNING_ALG_VALUES!r}")
    return cast(SigningAlgorithm, alg)


def narrow_purpose(purpose: str) -> Literal["request-signing", "webhook-signing"]:
    """Validate a stored ``purpose`` and return it as the SDK's ``Literal``.

    Same boundary problem as :func:`narrow_alg` — ``pem_to_adcp_jwk(purpose=...)``
    and ``generate_signing_keypair(purpose=...)`` are typed against the SDK's two
    values. Membership is checked against OUR narrowed set, not the SDK's, so a
    row carrying the deprecated purpose is refused rather than re-published.
    """
    if purpose not in MINTABLE_PURPOSES:
        raise AdCPConfigurationError(f"Signing purpose {purpose!r} is not mintable by this agent {MINTABLE_PURPOSES!r}")
    return cast(Literal["request-signing", "webhook-signing"], purpose)


#: Bytes of randomness in a minted ``kid``. 64 bits of entropy makes a collision
#: with a key minted in the same second for the same tenant unreachable in
#: practice, which is what lets the UNIQUE constraint stay a backstop rather than
#: a retry loop.
_KID_ENTROPY_BYTES = 8


def mint_kid(tenant_id: str, now: datetime) -> str:
    """Mint the ``kid`` a freshly provisioned key is published under.

    ONE generator, here with the other signing value-sets, so the admin route and
    the ops script cannot grow two kid shapes.

    security.mdx @ v3.1.1 (Agent key publication) requires a ``kid`` "Unique
    within the JWKS. MUST NOT collide with any other entry's kid regardless of
    ``adcp_use``". ``UNIQUE(tenant_id, kid)`` is the backstop for that, not the
    generator — and the SDK's own default kid documents itself as
    "collision-resistant within a single process" and tells callers managing
    rotation to supply their own. We manage rotation, so we supply our own.

    The timestamp is in the name because a kid is what an operator reads in a
    ``keyid`` header and in a published JWKS while deciding which key a rotation
    retired; the random suffix is what makes it unique.
    """
    if not tenant_id:
        raise AdCPConfigurationError("A signing key kid cannot be minted for an empty tenant_id")
    return f"{tenant_id}-{now.strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(_KID_ENTROPY_BYTES)}"


# The cache TTL we publish on every trust-root response, and the ONE constant the
# revocation grace window is derived from (``SigningConfig.grace_seconds`` defaults to
# 2x this). ``security.mdx``:1103 bounds the brand.json TTL by the revocation polling
# interval (floor 1 min, ceiling 30 min) and ``core/agent-signing-key.json`` recommends a
# 5-minute cache TTL; publishing it explicitly stops a proxy inventing its own and
# masking a rotation.
#
# It lives HERE, not in ``signing.trust_root`` where it was written, for the same reason
# the value-sets do: ``src.core.config`` needs it, and three modules INSIDE the signing
# package import ``src.core.config`` back (``request_verifier_middleware``,
# ``replay_store``, ``revocation``). While the constant sat behind the package's
# ``__init__``, that was a cycle — and it was the SECOND one the lazy exports were
# masking, found only once they were removed (salesagent-n78j0.3).
CACHE_MAX_AGE_SECONDS = 300


def sql_value_list(values: tuple[str, ...]) -> str:
    """Render *values* as a SQL ``IN`` body, for a DERIVED CHECK constraint.

    One home for the rendering so the ORM constraint and its migration cannot
    render the same value-set differently.

    NOT on the signing facade's public surface. Callers take a whole CLAUSE from
    :func:`signing_alg_check_clause` / :func:`signing_purpose_check_clause` below;
    exporting this instead would export a primitive plus the prose telling a caller
    how to combine it with the right value-set, which is how the ORM and the
    migration came to compose the same clause independently.
    """
    return ", ".join(repr(value) for value in values)


def signing_alg_check_clause() -> str:
    """The full ``ck_signing_keys_alg`` CHECK body.

    An OPERATION, not a value-set: the caller asks for the clause and cannot
    assemble a different one, because choosing the column name, the operator and the
    rendering is no longer its job.
    """
    return f"alg IN ({sql_value_list(SIGNING_ALG_VALUES)})"


def signing_purpose_check_clause() -> str:
    """The full ``ck_signing_keys_purpose`` CHECK body. See :func:`signing_alg_check_clause`."""
    return f"purpose IN ({sql_value_list(MINTABLE_PURPOSES)})"
