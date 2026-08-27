"""Integration tests for signing-key provisioning and SigningProvider resolution.

TDD RED for salesagent-z6nr.8 (A2 — signing key lifecycle). Nothing here passes
until ``src/core/signing/{keys,provider,algorithms}.py``, the ``SigningKey``
model and its migration exist.

This file grades the KEY-MATERIAL half of A2's core invariant: that the key we
sign with, the key we publish, and the key material we hold can never silently
disagree. Concretely —

* Two overlapping active ``request-signing`` keys, distinguished only by ``kid``,
  BOTH resolve through production and BOTH produce signatures that verify against
  their own STORED ``public_jwk`` (and not against the other's). This is the one
  mechanism that serves both rotation overlap and the webhook blast-radius
  isolation security.mdx:955 describes ("isolation comes from the ``kid``").
  Resolution goes through ``_resolve_signing_provider(..., kid=...)`` — a test
  that hand-constructs ``InMemorySigningProvider`` grades nothing, and skips the
  tripwire it is supposed to be exercising.
* The private half never leaves the row: it is stored as the PKCS#8
  ``BEGIN ENCRYPTED PRIVATE KEY`` PEM the SDK returned, under the deployment KEK,
  and provisioning refuses outright when no KEK is configured. Provisioning over
  a live ``kid`` fails rather than clobbering key material every counterparty
  already holds the public half of.
* The init tripwire (security.mdx:951) fires on a GENUINE mismatch and — the
  failure mode actually worth guarding — does NOT false-alarm on a healthy
  passphrase-encrypted PEM.

Note the SDK's provider contract: ``sign`` is ASYNC, and ``key_id()`` /
``algorithm()`` are METHODS. Attribute access yields a truthy bound method, which
is how ``<bound method ...>`` ends up in a ``keyid`` header.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from adcp.signing import alg_for_jwk, public_key_from_jwk, verify_signature
from sqlalchemy.exc import IntegrityError

from tests.harness._base import BareIntegrationEnv
from tests.helpers.signing import (
    REQUEST_SIGNING,
    SIGNATURE_BASE,
    deployment_kek,
    just_after_provisioning,
    provision_key,
    resolve_provider,
    signing_key_repo,
)

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

#: The ciphertext ``generate_signing_keypair(passphrase=...)`` returns verbatim.
_ENCRYPTED_PEM_HEADER = b"-----BEGIN ENCRYPTED PRIVATE KEY-----"


@pytest.fixture(autouse=True)
def _kek(monkeypatch):
    """Every provisioning call in this module needs the deployment KEK.

    ``db:`` is the scheme this agent mints and it REFUSES without a KEK — there is
    no plaintext fallback — so a suite that provisions through production has to
    configure one. Autouse because the alternative is the same three lines in
    every test.
    """
    with deployment_kek(monkeypatch):
        yield


# NULL not_after means open-ended, so a key provisioned today must still resolve
# at an arbitrarily distant instant.
_FAR_FUTURE = datetime(2099, 1, 1, tzinfo=UTC)


def _verifies(row, signature: bytes) -> bool:
    """Verify *signature* against the row's STORED public JWK, via SDK crypto only.

    Zero hand-rolled crypto: ``public_key_from_jwk`` rehydrates the key,
    ``alg_for_jwk`` maps the JWK's own ``alg`` member ("EdDSA"/"ES256") back to
    the RFC 9421 name the profile uses ("ed25519"/"ecdsa-p256-sha256").
    """
    return verify_signature(
        alg=alg_for_jwk(row.public_jwk),
        public_key=public_key_from_jwk(row.public_jwk),
        signature_base=SIGNATURE_BASE,
        signature=signature,
    )


class TestOverlappingActiveKeys:
    """Two active request-signing keys under one tenant, distinguished by kid."""

    def test_both_keys_resolve_and_both_signatures_verify(self, integration_db):
        from tests.factories import TenantFactory

        tenant_id = "sk_two_t1"
        with BareIntegrationEnv() as env:
            TenantFactory(tenant_id=tenant_id)
            repo = signing_key_repo(env, tenant_id)

            row_a = provision_key(repo, tenant_id, "adcp-key-a")
            row_b = provision_key(repo, tenant_id, "adcp-key-b", alg="ecdsa-p256-sha256")
            now = just_after_provisioning()

            # Distinct rows, distinct published material, same purpose.
            assert {row_a.kid, row_b.kid} == {"adcp-key-a", "adcp-key-b"}
            assert row_a.public_jwk != row_b.public_jwk
            assert row_a.purpose == row_b.purpose == REQUEST_SIGNING

            signatures = {}
            for row, expected_alg in ((row_a, "ed25519"), (row_b, "ecdsa-p256-sha256")):
                provider = resolve_provider(repo, tenant_id, now=now, kid=row.kid)

                # key_id()/algorithm() are METHODS on the SDK provider.
                assert provider.key_id() == row.kid
                assert provider.algorithm() == expected_alg
                assert row.alg == expected_alg

                # The JWK we publish must announce the same algorithm the column
                # stores — the "third alg namespace" trap (JWK alg is EdDSA/ES256).
                assert alg_for_jwk(row.public_jwk) == row.alg
                # Publication shape: the JWK carries its own kid and adcp_use.
                assert row.public_jwk["kid"] == row.kid
                assert row.public_jwk["adcp_use"] == REQUEST_SIGNING

                signature = asyncio.run(provider.sign(SIGNATURE_BASE))
                assert _verifies(row, signature), f"{row.kid} signature failed its own JWK"
                signatures[row.kid] = signature

            # Each signature verifies ONLY under its own published key — proof the
            # kid axis selected different key material, not the same key twice.
            assert not _verifies(row_b, signatures["adcp-key-a"])
            assert not _verifies(row_a, signatures["adcp-key-b"])

    def test_open_ended_key_resolves_without_an_explicit_kid(self, integration_db):
        """not_after IS NULL is open-ended: the sole active key still resolves.

        Without an explicit kid the resolver falls back to ``active_at``, and the
        current key is always open-ended — so a NULL-blind window predicate leaves
        the tenant with no signable key at all.
        """
        from tests.factories import TenantFactory

        tenant_id = "sk_open_t1"
        with BareIntegrationEnv() as env:
            TenantFactory(tenant_id=tenant_id)
            repo = signing_key_repo(env, tenant_id)
            row = provision_key(repo, tenant_id, "adcp-only-key")

            assert row.not_after is None, "provisioning must mint the current key open-ended"

            provider = resolve_provider(repo, tenant_id, now=_FAR_FUTURE)

            assert provider.key_id() == "adcp-only-key"
            assert _verifies(row, asyncio.run(provider.sign(SIGNATURE_BASE)))


class TestPrivateKeyStorage:
    """The private half lives on the row as ciphertext — nowhere else, once only.

    Rewritten from ``TestPrivateKeyFileHandling`` when the storage design changed
    (salesagent-7x8t): the application writes key material to no filesystem, so
    the 0600/``O_EXCL`` contract those tests graded no longer exists. The
    PROPERTIES they protected do: the row still carries a reference and never
    plaintext, and provisioning still refuses to overwrite live key material.
    ``tests/integration/test_signing_key_provisioning.py`` grades the "no file is
    created anywhere" half positively, with a filesystem snapshot.
    """

    def test_the_row_carries_encrypted_ciphertext_and_a_reference_never_material(self, integration_db):
        """The ref locates; the ciphertext is what a reader has to decrypt.

        The ref is asserted to be ``db:<kid>`` — its own row's kid — because that
        is what makes a ref copied onto another row detectable at resolve time.
        """
        from tests.factories import TenantFactory

        tenant_id = "sk_mode_t1"
        with BareIntegrationEnv() as env:
            TenantFactory(tenant_id=tenant_id)
            repo = signing_key_repo(env, tenant_id)
            row = provision_key(repo, tenant_id, "adcp-mode-key")

            assert row.private_key_ref == "db:adcp-mode-key"
            assert "PRIVATE KEY" not in row.private_key_ref

            # Encrypted under the deployment KEK, verbatim as the SDK returned it.
            assert bytes(row.private_key_pem_encrypted).startswith(_ENCRYPTED_PEM_HEADER)

    def test_second_provision_of_the_same_kid_raises_and_preserves_the_key(self, integration_db):
        """Provisioning over a live key must fail, not overwrite it.

        Overwriting would strand every counterparty holding the published public
        half, with no local signal until signatures start being rejected.
        ``UNIQUE(tenant_id, kid)`` is what enforces it now that the key material
        lives on the row rather than at a path.
        """
        from tests.factories import TenantFactory

        tenant_id = "sk_excl_t1"
        with BareIntegrationEnv() as env:
            TenantFactory(tenant_id=tenant_id)
            repo = signing_key_repo(env, tenant_id)
            provision_key(repo, tenant_id, "adcp-first")

            # Commit the first key, so the collision below is against material
            # that is really persisted and the rollback cannot take it with it.
            session = env.get_session()
            original = bytes(repo.get_by_kid("adcp-first").private_key_pem_encrypted)

            with pytest.raises(IntegrityError):
                provision_key(repo, tenant_id, "adcp-first")
            session.rollback()

            assert bytes(repo.get_by_kid("adcp-first").private_key_pem_encrypted) == original


class TestInitTripwire:
    """security.mdx:951 — assert the public key at signer init, fail loudly on drift."""

    def test_mismatched_pem_behind_an_unchanged_ref_raises(self, integration_db):
        """The row's stored JWK and the PEM the ref resolves to must agree.

        This is the silent failure the tripwire exists for: change the key
        material behind an unchanged ``private_key_ref`` and every signature is
        rejected by every counterparty, with nothing wrong locally to look at.
        The ``db:`` scheme narrows the ways that can happen but does not remove
        them — the ciphertext and the ``public_jwk`` are two columns on one row,
        and a rotation that writes one without the other lands exactly here.
        """
        from src.core.exceptions import AdCPConfigurationError
        from tests.factories import SigningKeyFactory, TenantFactory

        tenant_id = "sk_trip_t1"
        with BareIntegrationEnv() as env:
            tenant = TenantFactory(tenant_id=tenant_id)
            repo = signing_key_repo(env, tenant_id)

            row_a = provision_key(repo, tenant_id, "adcp-real-a")
            row_b = provision_key(repo, tenant_id, "adcp-real-b")

            # A row holding A's key material while publishing B's public half —
            # exactly the state a half-completed rotation leaves behind. The ref
            # is the row's OWN kid, so this is a genuine material/JWK mismatch and
            # not the copied-ref case the locator check catches earlier.
            SigningKeyFactory(
                tenant=tenant,
                kid="adcp-drifted",
                alg=row_b.alg,
                public_jwk={**row_b.public_jwk, "kid": "adcp-drifted"},
                private_key_ref="db:adcp-drifted",
                private_key_pem_encrypted=row_a.private_key_pem_encrypted,
            )
            now = just_after_provisioning()

            with pytest.raises(AdCPConfigurationError):
                resolve_provider(repo, tenant_id, now=now, kid="adcp-drifted")

            # Control: the untouched rows still resolve, so the tripwire is
            # discriminating between drifted and healthy, not failing everything.
            assert resolve_provider(repo, tenant_id, now=now, kid="adcp-real-a").key_id() == "adcp-real-a"

    def test_passphrase_encrypted_pem_passes_the_tripwire(self, integration_db):
        """A healthy ENCRYPTED PEM must pass — the guarded failure is a FALSE alarm.

        ``pem_to_adcp_jwk`` takes ``password``; omitting it means that the moment
        ``ADCP_SIGNING_KEY_PASSPHRASE_ENV`` is configured, the tripwire raises on a
        perfectly healthy key at provider init — reading exactly like a genuine key
        mismatch. The passphrase must be resolved once and threaded into both
        ``load_private_key_pem`` and ``pem_to_adcp_jwk``.

        Every key in this module is now encrypted (``db:`` refuses to mint without
        a KEK), so the ``_kek`` fixture supplies what this test used to configure
        for itself.
        """
        from tests.factories import TenantFactory

        tenant_id = "sk_pass_t1"
        with BareIntegrationEnv() as env:
            TenantFactory(tenant_id=tenant_id)
            repo = signing_key_repo(env, tenant_id)
            row = provision_key(repo, tenant_id, "adcp-encrypted")

            # Non-vacuity: the PEM really is encrypted, so the passphrase path ran.
            assert bytes(row.private_key_pem_encrypted).startswith(_ENCRYPTED_PEM_HEADER)

            provider = resolve_provider(repo, tenant_id, now=just_after_provisioning(), kid=row.kid)

            assert provider.key_id() == "adcp-encrypted"
            assert _verifies(row, asyncio.run(provider.sign(SIGNATURE_BASE)))


class TestRevocationBeatsTheProviderCache:
    """A revoked key stops resolving IMMEDIATELY — inside the 60s cache window.

    The mechanism is ordering, not invalidation: ``_resolve_cached`` selects the row
    BEFORE it consults the cache, and ``_select_row`` refuses a revoked row on both
    paths (``active_at`` excludes it in SQL; the explicit-``kid`` path raises). So the
    cache is never reached for a revoked key.

    THIS TEST EXISTS BECAUSE PRODUCTION SAID OTHERWISE. The admin revoke route claimed
    the cache bust "is not optional… a revoke that skips it keeps signing with the
    retired key for up to a minute". That was false, and nothing graded either claim —
    two comments in the same tree disagreed about whether a 60-second window existed.

    IT DELIBERATELY DOES NOT CALL ``clear_signing_provider_cache()``. Clearing first
    would make this pass for the wrong reason: it would prove the cache can be emptied,
    not that revocation beats it. The key is resolved FIRST so the entry is genuinely
    cached and unexpired, then revoked, then resolved again — all well inside
    ``_CACHE_TTL_SECONDS`` and with no clock manipulation, so a pass cannot be explained
    by expiry.

    MUTATION: drop ``SigningKey.revoked_at.is_(None)`` from
    :meth:`SigningKeyRepository.active_at` and this goes RED — the revoked row is selected
    again and resolves from the warm entry, which is the 60-second window the route's
    comment imagined.

    NOT the mutation you would reach for first, and the reason is worth keeping: reversing
    the ORDER in ``_resolve_cached`` (cache before row) does NOT falsify this test, because
    the cache key is ``(tenant_id, row.kid)`` — you must read the row to know the key. On
    the ``active_at`` path the ordering is therefore FORCED, not chosen, so the property
    rests on the row filter rather than on statement order. A mutation aimed at the order
    passes and proves nothing; that was measured, not assumed.
    """

    def test_a_revoked_key_stops_resolving_while_its_entry_is_still_warm(self, integration_db):
        from src.core.exceptions import AdCPConfigurationError
        from src.core.signing import revoke_signing_key
        from src.core.signing.provider import _CACHE_TTL_SECONDS, _provider_cache
        from tests.factories import TenantFactory

        tenant_id = "sk_revoke_cache_t1"
        with BareIntegrationEnv() as env:
            TenantFactory(tenant_id=tenant_id)
            repo = signing_key_repo(env, tenant_id)
            provision_key(repo, tenant_id, "adcp-revoke-cache-key")
            now = just_after_provisioning()

            # Warm the cache and PROVE it is warm — otherwise the second resolve could
            # miss for a reason unrelated to revocation.
            provider = resolve_provider(repo, tenant_id, now=now)
            assert provider.key_id() == "adcp-revoke-cache-key"
            assert (tenant_id, "adcp-revoke-cache-key") in _provider_cache, (
                "precondition: the resolution must be cached, or this test cannot "
                "distinguish revocation from a cold lookup"
            )
            assert _CACHE_TTL_SECONDS >= 60.0, (
                "precondition: the window must be wide enough that a pass cannot be "
                f"explained by expiry; TTL is {_CACHE_TTL_SECONDS}s"
            )

            revoked = revoke_signing_key(repo, kid="adcp-revoke-cache-key")
            assert revoked is not None and revoked.revoked_at is not None

            # Same instant, same process, entry still inside its TTL.
            with pytest.raises(AdCPConfigurationError):
                resolve_provider(repo, tenant_id, now=now)
