"""Provision this agent's own signing keys. Zero hand-rolled crypto.

``adcp.signing.keygen.generate_signing_keypair`` mints the keypair — it is the
programmatic counterpart of the ``adcp-keygen`` CLI and emits a JWK whose
``kty``/``crv``/``alg``/``use``/``key_ops``/``adcp_use``/``kid`` members are
already byte-shape-identical to the publication shape the spec mandates. We
assemble no JWK and touch no curve.

What this module owns is the part the SDK deliberately leaves to the caller:
where the private half goes, and how the row that holds it is written.

**The private half lives in the database, and nowhere else.** It is stored on the
key's own row as the PKCS#8 ``BEGIN ENCRYPTED PRIVATE KEY`` PEM
``generate_signing_keypair(passphrase=...)`` already returns, encrypted under the
deployment KEK. The ciphertext IS the PEM — there is no envelope format here and
no encryption code of ours.

There is no storage choice to make, so there is no locator, no scheme and no
setting: the application writes key material to no filesystem, reads none from
the environment, and hands none back to a caller.

This function is the ONE birth site for a ``signing_keys`` row, and it does not
complete unless the private key behind the row resolves and round-trips to the
public JWK the row is about to publish — so the agent can never publish a key it
cannot sign with.
"""

from __future__ import annotations

from datetime import UTC, datetime

from adcp.signing import generate_signing_keypair

from src.core.database.models import SigningKey
from src.core.database.repositories.signing_key import SigningKeyRepository
from src.core.errors.details import ConfigurationDetails
from src.core.exceptions import AdCPConfigurationError
from src.core.signing.algorithms import REQUEST_SIGNING, keygen_alg, mint_kid, narrow_alg, narrow_purpose
from src.core.signing.provider import assert_pem_publishes_jwk


def provision_signing_key(
    repo: SigningKeyRepository,
    *,
    tenant_id: str,
    alg: str,
    kid: str | None = None,
    purpose: str = REQUEST_SIGNING,
) -> str:
    """Mint a keypair for *tenant_id*, persist the row that publishes it, return its ``kid``.

    A ``str`` CANNOT carry private key material, and that is the whole point
    (salesagent-9misv). It used to be a runtime property of which ``ref_scheme``
    the caller passed — ``env:`` stored the private half nowhere and handed the PEM
    back, and the admin route flashed it into Flask's client-side session cookie.
    Returning the row instead would not have settled it: a ``SigningKey`` carries
    ``private_key_pem_encrypted``, so "the return type has nowhere to put a PEM"
    would have been a claim about which field callers happen to read rather than a
    property of the type. A ``kid`` makes it true by construction.

    It also keeps the row's lifetime out of the caller's hands. The row is an ORM
    instance owned by *repo*'s session; handing it out makes every attribute read a
    question about whether that session is still open, and a caller reading
    ``.kid`` after the unit of work closes gets ``DetachedInstanceError`` rather
    than an answer. A caller that wants the row reads it back — see
    ``tests.helpers.signing.provision_key``, which does exactly that.

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

    1. minting requires the KEK, so the stored PEM is ciphertext — checked BEFORE
       any key material exists, because ``publishable_at`` is resolvability-blind
       and would otherwise publish a row nothing can open;
    2. the keypair is minted;
    3. the private half is loaded back and re-derives the public JWK about to be
       stored (:func:`~src.core.signing.provider.assert_pem_publishes_jwk`), which
       also proves the KEK round-trips;
    4. only then is the row created.

    Every refusal below is an ``AdCPConfigurationError`` carrying a typed
    :class:`~src.core.errors.details.ConfigurationDetails`: ``capability`` names the
    axis, ``rejected_value``/``accepted_values`` carry the offending value and the
    profile, and ``tracked_by`` carries the operator-actionable sentence. There is no
    ``message=`` (ADR-010) — ``CODE_TABLE`` owns the buyer-facing text — so an operator
    surface that renders only ``str(exc)`` shows the generic sentence and must read
    these fields to name the knob.

    Returns:
        The ``kid`` of the minted key — server-side, and the same value whether
        *kid* was supplied or :func:`~src.core.signing.algorithms.mint_kid` named
        it. Read the row back through *repo* if you need it.

    Raises:
        AdCPConfigurationError: *alg*/*purpose* are outside the profile, the
            deployment forbids ``db:``, no KEK is configured, *tenant_id* does not
            match the repository's tenant scope, or the minted key fails its own
            round-trip.
    """
    if tenant_id != repo.tenant_id:
        raise AdCPConfigurationError(
            details=ConfigurationDetails(
                tenant_id=tenant_id,
                capability="signing_key",
                rejected_value=tenant_id,
                accepted_values=[repo.tenant_id],
                tracked_by=(
                    "A signing key is minted through a repository already scoped to its tenant; "
                    "open the unit of work for that tenant rather than passing a different tenant_id."
                ),
            ),
        )

    # Validate against OUR value-sets before the SDK sees them, so an off-profile
    # value fails with our message and never reaches the DB CHECK.
    stored_alg = narrow_alg(alg)
    stored_purpose = narrow_purpose(purpose)

    now = datetime.now(UTC)
    kid = kid or mint_kid(tenant_id, now)
    if not kid:
        raise AdCPConfigurationError(
            details=ConfigurationDetails(
                capability="signing_key",
                rejected_value=kid,
                tracked_by="A signing key kid must be a non-empty string.",
            ),
        )

    # Resolved once here and threaded through — the round-trip below and the
    # resolver in src/core/signing/provider.py need the same passphrase.
    from src.core.config import get_settings

    passphrase = get_settings().signing.key_passphrase
    if passphrase is None:
        raise AdCPConfigurationError(
            details=ConfigurationDetails(
                capability="signing_key",
                # The knob name lives HERE and nowhere else a caller can reach: CODE_TABLE's
                # sentence names no knob, so an admin surface rendering only str(exc) would
                # leave the operator with nothing to set.
                tracked_by=(
                    "Refusing to mint a signing key with no key encryption key configured: set "
                    "key_passphrase_env (ADCP_SIGNING_KEY_PASSPHRASE_ENV) to the name of the "
                    "environment variable holding the passphrase. There is no plaintext fallback — "
                    "storing an unencrypted PEM would turn 'encrypted PEM in Postgres' into "
                    "'private keys in the database' with no signal."
                ),
            ),
        )

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

    repo.create_from_keypair(
        kid=kid,
        alg=stored_alg,
        purpose=stored_purpose,
        public_jwk=public_jwk,
        private_key_pem_encrypted=pem,
        not_before=now,
        not_after=None,
    )
    # The row is deliberately NOT returned: see the type's rationale above.
    return kid


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
