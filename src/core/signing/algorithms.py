"""The signing value-sets, with exactly one source of truth each.

THE LAYER'S DEPENDENCY-FREE LEAF. Everything here imports only ``adcp.signing.*`` and
the error vocabulary, so a module BELOW the signing layer — ``src.core.database.models``,
``src.core.config``, ``alembic/versions/e7a2c40b91d5_add_signing_keys_table.py`` — can
reach it by dotted path without dragging the layer in behind it.

There is no forwarding shim and no separate ``signing_contract`` package any more. Both
existed to break one cycle: Python runs a package's ``__init__`` before any submodule, so
while :mod:`src.core.signing` re-exported ``keys``, importing this leaf from
``database.models`` ran ``keys`` -> ``database.models`` -> back into a half-initialised
module. On #1721 :mod:`src.core.signing` re-exports NOTHING by design, so the cycle cannot
form and the leaf lives in the package it belongs to.

Two value-sets govern every signing key we mint, and they are sourced differently on
purpose:

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

RELATED DEFECT, filed not fixed: ``e7a2c40b91d5`` interpolates these LIVE constants into
the DDL it emits, so two databases at the same alembic revision can carry different CHECK
constraints with nothing recording the divergence — ``salesagent-89p27``. Freezing it
needs a NEW migration pinning the values as literals, which is its own atom.
"""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import Literal, NoReturn, cast

from adcp.signing.crypto import ALG_ED25519, ALG_ES256, ALLOWED_ALGS
from adcp.signing.provider import SigningAlgorithm

from src.core.errors.details import ConfigurationDetails
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


def _refuse_alg(alg: str) -> NoReturn:
    """Refuse an ``alg`` outside the AdCP profile.

    ONE raise site for the two ways an out-of-profile algorithm arrives — a stored
    column value (:func:`narrow_alg`) and a keygen translation (:func:`keygen_alg`) —
    so the two cannot carry different typed details for one fault. There is no
    ``message`` (ADR-010): CODE_TABLE owns the buyer-facing sentence, and the
    offending value plus the profile it missed travel in ``details``.
    """
    raise AdCPConfigurationError(
        field="alg",
        details=ConfigurationDetails(
            capability="signing_alg",
            rejected_value=alg,
            accepted_values=list(SIGNING_ALG_VALUES),
            tracked_by="the AdCP 3.1.1 signing algorithm profile (adcp.signing.crypto.ALLOWED_ALGS)",
        ),
    ) from None


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
        _refuse_alg(alg)


def narrow_alg(alg: str) -> SigningAlgorithm:
    """Validate a stored ``alg`` and return it as the SDK's ``SigningAlgorithm``.

    The column is ``Mapped[str]`` while ``InMemorySigningProvider(algorithm=...)``
    wants a ``Literal`` — so this is what keeps mypy honest at the boundary. It is
    also the runtime guard for a row written before the CHECK existed.
    """
    if alg not in SIGNING_ALG_VALUES:
        _refuse_alg(alg)
    return cast(SigningAlgorithm, alg)


def narrow_purpose(purpose: str) -> Literal["request-signing", "webhook-signing"]:
    """Validate a stored ``purpose`` and return it as the SDK's ``Literal``.

    Same boundary problem as :func:`narrow_alg` — ``pem_to_adcp_jwk(purpose=...)``
    and ``generate_signing_keypair(purpose=...)`` are typed against the SDK's two
    values. Membership is checked against OUR narrowed set, not the SDK's, so a
    row carrying the deprecated purpose is refused rather than re-published.
    """
    if purpose not in MINTABLE_PURPOSES:
        raise AdCPConfigurationError(
            field="purpose",
            details=ConfigurationDetails(
                capability="signing_purpose",
                rejected_value=purpose,
                accepted_values=list(MINTABLE_PURPOSES),
                tracked_by="adcp#5555 — webhook-signing is deprecated; this agent mints request-signing only",
            ),
        )
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
        raise AdCPConfigurationError(
            field="tenant_id",
            details=ConfigurationDetails(
                capability="signing_key",
                tracked_by="a kid is unique per UNIQUE(tenant_id, kid); an unscoped one cannot be published",
            ),
        )
    return f"{tenant_id}-{now.strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(_KID_ENTROPY_BYTES)}"


# The cache TTL we publish on every trust-root response, and the ONE constant the
# revocation grace window is derived from (``SigningSettings.grace_seconds`` defaults to
# 2x this). ``security.mdx``:1103 bounds the brand.json TTL by the revocation polling
# interval (floor 1 min, ceiling 30 min) and ``core/agent-signing-key.json`` recommends a
# 5-minute cache TTL; publishing it explicitly stops a proxy inventing its own and
# masking a rotation.
#
# It lives HERE, not in ``signing.trust_root`` where it was written, for the same reason
# the value-sets do: ``src.core.config`` needs it (``_cache_max_age_seconds``) and modules
# INSIDE the signing package import ``src.core.config`` back. A leaf reaching only
# ``adcp.signing.*`` and the error vocabulary is what keeps that a straight line.
CACHE_MAX_AGE_SECONDS = 300


def sql_value_list(values: tuple[str, ...]) -> str:
    """Render *values* as a SQL ``IN`` body, for a DERIVED CHECK constraint.

    One home for the rendering so the ORM constraint and its migration cannot
    render the same value-set differently.

    Callers inside ``src/`` take a whole CLAUSE from
    :func:`signing_alg_check_clause` / :func:`signing_purpose_check_clause` below;
    reaching for this instead takes a primitive plus the prose telling a caller how
    to combine it with the right value-set, which is how the ORM constraint and its
    migration came to compose the same clause independently (#1521).
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
