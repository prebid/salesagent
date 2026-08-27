"""Provision this agent's own signing keys. Zero hand-rolled crypto.

``adcp.signing.keygen.generate_signing_keypair`` mints the keypair — it is the
programmatic counterpart of the ``adcp-keygen`` CLI and emits a JWK whose
``kty``/``crv``/``alg``/``use``/``key_ops``/``adcp_use``/``kid`` members are
already byte-shape-identical to the publication shape the spec mandates. We
assemble no JWK and touch no curve.

What this module owns is the part the SDK deliberately leaves to the caller:
where the private half goes, and how the row that references it is written.

**The application writes key material to no filesystem.** The default scheme is
``db:``: the private half is stored on the row as the PKCS#8
``BEGIN ENCRYPTED PRIVATE KEY`` PEM ``generate_signing_keypair(passphrase=...)``
already returns, encrypted under the deployment KEK. The ciphertext IS the PEM —
there is no envelope format here and no encryption code of ours. ``env:`` is the
single-tenant alternative, where the operator holds the PEM and this function
hands it back exactly once. ``file:`` is READ-ONLY, for material an orchestrator
mounted; it is not mintable.

This function is the ONE birth site for a ``signing_keys`` row, and it does not
complete unless the private key behind the row resolves and round-trips to the
public JWK the row is about to publish — so the agent can never publish a key it
cannot sign with.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import NamedTuple

from adcp.signing import generate_signing_keypair

from src.core.database.models import SigningKey
from src.core.database.repositories.signing_key import SigningKeyRepository
from src.core.exceptions import AdCPConfigurationError
from src.core.signing.provider import DB_SCHEME, assert_pem_publishes_jwk, assert_ref_scheme_allowed
from src.core.signing_contract import REQUEST_SIGNING, keygen_alg, mint_kid, narrow_alg, narrow_purpose

#: Schemes a key can be MINTED under. ``file:`` is absent on purpose: it names
#: material someone else provisioned, so minting into it would mean writing a
#: private key to a filesystem — the thing this module does not do.
MINTABLE_REF_SCHEMES: tuple[str, ...] = (DB_SCHEME, "env")

#: POSIX environment variable names. The ``env:`` locator is the caller's to
#: supply because the operator has to export that exact name; this function never
#: invents it, and a name the shell cannot export is a row that never resolves.
_ENV_VAR_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")


class ProvisionedKey(NamedTuple):
    """The persisted row, plus the PEM only an ``env:`` mint hands back.

    ``private_key_pem`` is ``None`` for a ``db:`` mint — the ciphertext is on the
    row and nothing needs to leave this call. For ``env:`` it is the one-time
    handoff: the operator must export it under the name they chose, and this is
    the only moment the material exists outside the process that made it.
    """

    row: SigningKey
    private_key_pem: bytes | None


def provision_signing_key(
    repo: SigningKeyRepository,
    *,
    tenant_id: str,
    alg: str,
    kid: str | None = None,
    purpose: str = REQUEST_SIGNING,
    ref_scheme: str = DB_SCHEME,
    env_var_name: str | None = None,
) -> ProvisionedKey:
    """Mint a keypair for *tenant_id* and persist the row that publishes it.

    Pass *kid* to name the key explicitly; otherwise
    :func:`~src.core.signing.algorithms.mint_kid` names it. Either way it is
    OURS: the SDK's default kid documents itself as "collision-resistant within a
    single process but does not guarantee uniqueness across processes" and states
    that callers managing rotation MUST supply their own — we manage rotation, so
    the default never fires.

    The key is minted open-ended (``not_after`` NULL): a newly provisioned key is
    the current key, and the current key has no upper bound. Retirement is a
    later :meth:`~src.core.database.repositories.signing_key.SigningKeyRepository.revoke`.

    Order of operations, and every step is a precondition for the next:

    1. the requested scheme is one this DEPLOYMENT will resolve — checked BEFORE
       any key material exists, because ``publishable_at`` is resolvability-blind
       and would publish a row the resolver later refuses;
    2. a ``db:`` mint requires the KEK, so the stored PEM is ciphertext;
    3. the keypair is minted;
    4. the private half is loaded back and re-derives the public JWK about to be
       stored (:func:`~src.core.signing.provider.assert_pem_publishes_jwk`) — for
       ``db:`` this also proves the KEK round-trips;
    5. only then is the row created.

    Raises:
        AdCPConfigurationError: *alg*/*purpose*/*ref_scheme* are outside the
            profile, the deployment forbids the scheme, a ``db:`` mint has no KEK
            configured, *tenant_id* does not match the repository's tenant scope,
            or the minted key fails its own round-trip.
    """
    if tenant_id != repo.tenant_id:
        raise AdCPConfigurationError(
            f"Cannot provision a signing key for tenant {tenant_id!r} through a repository scoped to {repo.tenant_id!r}"
        )

    # Validate against OUR value-sets before the SDK sees them, so an off-profile
    # value fails with our message and never reaches the DB CHECK.
    stored_alg = narrow_alg(alg)
    stored_purpose = narrow_purpose(purpose)

    if ref_scheme not in MINTABLE_REF_SCHEMES:
        raise AdCPConfigurationError(
            f"Signing keys cannot be minted under the {ref_scheme!r} scheme "
            f"(mintable: {MINTABLE_REF_SCHEMES!r}); 'file' names material provisioned elsewhere"
        )
    assert_ref_scheme_allowed(ref_scheme)

    now = datetime.now(UTC)
    kid = kid or mint_kid(tenant_id, now)
    if not kid:
        raise AdCPConfigurationError("A signing key kid must be a non-empty string")

    # Resolved once here and threaded through — the round-trip below and the
    # resolver in src/core/signing/provider.py need the same passphrase.
    from src.core.config import get_config

    passphrase = get_config().signing.key_passphrase
    if ref_scheme == DB_SCHEME and passphrase is None:
        raise AdCPConfigurationError(
            "Refusing to mint a db: signing key with no key encryption key configured: set "
            "key_passphrase_env (ADCP_SIGNING_KEY_PASSPHRASE_ENV) to the name of the environment "
            "variable holding the passphrase. There is no plaintext fallback — storing an "
            "unencrypted PEM would turn 'encrypted PEM in Postgres' into 'private keys in the "
            "database' with no signal."
        )

    private_key_ref = _private_key_ref(ref_scheme, kid=kid, env_var_name=env_var_name)

    pem, public_jwk = generate_signing_keypair(
        alg=keygen_alg(stored_alg),
        kid=kid,
        purpose=stored_purpose,
        passphrase=passphrase,
    )

    # The row is not written unless the key we are about to publish is a key we
    # can sign with. Same tripwire the resolver runs, so mint and resolve cannot
    # disagree about what "matches" means.
    assert_pem_publishes_jwk(
        pem,
        kid=kid,
        purpose=stored_purpose,
        public_jwk=public_jwk,
        tenant_id=tenant_id,
        passphrase=passphrase,
    )

    row = repo.create_from_keypair(
        kid=kid,
        alg=stored_alg,
        purpose=stored_purpose,
        public_jwk=public_jwk,
        private_key_ref=private_key_ref,
        private_key_pem_encrypted=pem if ref_scheme == DB_SCHEME else None,
        not_before=now,
        not_after=None,
    )
    return ProvisionedKey(row=row, private_key_pem=None if ref_scheme == DB_SCHEME else pem)


def revoke_signing_key(
    repo: SigningKeyRepository,
    *,
    kid: str,
    at: datetime | None = None,
) -> SigningKey | None:
    """Retire *kid*: stamp the row AND drop the cached provider, as ONE act.

    Returns the revoked row, or ``None`` when this tenant has no key by that kid.

    The sibling of :func:`provision_signing_key`, and it exists for the same reason
    :meth:`SigningKeyRepository.canonical_origin` does: a caller should not be able to
    perform HALF of an operation. Revocation is two effects — the row transition and the
    cache drop — and they were previously two statements at one call site, which is a
    shape that only stays correct while every future call site remembers both.

    WHAT THE CACHE DROP IS, AND IS NOT. It is HOUSEKEEPING, not the safety mechanism, and
    the distinction matters because the admin route used to claim the opposite ("a revoke
    that skips it keeps signing with the retired key for up to a minute"). That was
    FALSE. :func:`~src.core.signing.provider._resolve_cached` selects the row BEFORE it
    consults the cache, and :func:`~src.core.signing.provider._select_row` refuses a
    revoked row on BOTH paths — ``active_at`` excludes it in SQL, and the explicit-``kid``
    path raises. So a revoked key stops signing IMMEDIATELY, cache or no cache; what the
    drop removes is a now-unreachable entry holding decrypted key material in memory for
    up to the TTL.

    Keeping the drop is still right — retired material should not linger — but the
    guarantee callers depend on comes from the row check, and a comment claiming
    otherwise sends the next reader looking for a 60-second window that does not exist.
    """
    from src.core.signing.provider import clear_signing_provider_cache

    revoked = repo.revoke(kid, at=at or datetime.now(UTC))
    if revoked is None:
        return None
    clear_signing_provider_cache()
    return revoked


def _private_key_ref(ref_scheme: str, *, kid: str, env_var_name: str | None) -> str:
    """The scheme-prefixed locator the row will carry.

    A ``db:`` ref locates the row's own ciphertext by its ``kid``, which is what
    makes a ref copied between rows detectable at resolve time.
    """
    if ref_scheme == DB_SCHEME:
        if env_var_name is not None:
            raise AdCPConfigurationError("env_var_name is meaningless for a db: signing key — its material is the row")
        return f"{DB_SCHEME}:{kid}"

    if not env_var_name or not _ENV_VAR_NAME.match(env_var_name):
        raise AdCPConfigurationError(
            f"An env: signing key needs the name of the environment variable the operator will "
            f"export, matching {_ENV_VAR_NAME.pattern!r}; got {env_var_name!r}"
        )
    return f"env:{env_var_name}"
