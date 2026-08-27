"""The combined revocation list we PUBLISH (#1291 A5 follow-up, salesagent-z6nr.27).

TDD RED. Nothing here passes until ``GOVERNANCE_REVOCATIONS_PATH`` exists in
``src/core/agent_identity.py``, ``src/core/signing/revocation_list.py`` exists
beside ``trust_root.py``, ``SigningKeyRepository`` grows the revoked-rows query,
``SigningConfig`` grows the declared cadence, and ``src/routes/well_known.py``
serves the document through the narrowed builder seam.

Core Invariant under test: the combined revocation list is a THIRD projection of
the same ``signing_keys`` rows the JWKS and the adagents pin already project —
but its retention is UNBOUNDED where the JWKS grace window is not, because a kid
dropped from ``revoked_kids`` is a kid UN-REVOKED.

Spec grounding (reproducible:
``git -C ~/projects/adcp show v3.1.1:docs/building/by-layer/L1/security.mdx``):

* **:1543 is the MUST that binds us** — "Signers MUST publish revocations via the
  same combined revocation list used for request signing". It binds us as a
  SIGNER of outbound requests and webhooks, which is why this is the publisher
  half and lives next to ``trust_root.py`` rather than inside A5's inbound
  verifier.
* **:1328** scopes the combined list: ``revoked_kids`` is what governs request
  signatures. ``revoked_jtis`` is NOT used for them, so its absence from our
  payload is a decision this suite asserts, not an omission it tolerates.
* **:717** bounds the declared cadence (floor 1 min, ceiling 30 min) and
  **:1103** binds the published brand.json cache TTL by that same interval.
* **:1079** (governance keys served from a separate origin) is knowingly
  diverged from: this document is signed with the tenant's request-signing key,
  self-consistent with our OWN consumer — ``CounterpartyRevocationChecker``
  resolves a published list's ``kid`` through the counterparty's request-signing
  JWKS — but not the governance-key profile. Follow-up: ``salesagent-z6nr.37``.

Why the assertions are written the way they are:

* **On the SERVED BYTES, never solely "our consumer accepts it".** The whole
  point of publishing is that a THIRD PARTY can verify. A suite that grades the
  document only by handing it back to ``CachingRevocationChecker`` lets producer
  and consumer agree on a wrong format together — the
  encoder-tested-by-its-own-decoder trap. So the protected header and the
  payload are decoded from the wire and their member sets pinned by name, and
  the signature is checked with RAW ``cryptography`` primitives against the
  public key off our OWN published JWKS. The SDK's ``verify_detached_jws`` runs
  too, but as a SECOND opinion from outside ``src/``, not as the primary
  authority.
* **The revoked key is aged past the grace window on purpose.** That is what
  separates the new query from ``publishable_at``: the same kid must be GONE
  from the JWKS and STILL PRESENT in ``revoked_kids``. A test that revoked a key
  and read both documents one second later would pass against a revocation list
  that was merely ``publishable_at`` filtered to revoked rows.
* **The active key is minted through production** (``provision_signing_key``),
  not fabricated by a factory: the route has to RESOLVE a private half to sign
  with, and a factory row's ``env:`` reference has no material behind it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from adcp.signing.crypto import b64url_decode

from tests.harness._base import BareIntegrationEnv
from tests.helpers.signing import (
    b64url_json,
    deployment_kek,
    get_trust_root_document,
    jws_parts,
    provision_key,
    signing_key_repo,
)

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_JWKS_PATH = "/.well-known/jwks.json"

#: The five members ``security.mdx``:1328 names for a combined revocation list.
#: ``revoked_jtis`` is deliberately NOT among them — see the module docstring.
_SPEC_PAYLOAD_MEMBERS = frozenset({"version", "issuer", "updated", "next_update", "revoked_kids"})

#: The exact member set of a JWS protected header in this profile. The SDK's
#: ``_validate_header`` reads ``alg``/``typ``/``kid`` and REFUSES a non-empty
#: ``crit``; pinning the set by equality is what stops an extra header member
#: from riding along unnoticed into a document counterparties must parse.
_PROTECTED_HEADER_MEMBERS = frozenset({"alg", "typ", "kid"})


def _revocations_path() -> str:
    """The served path, from the production constant rather than a literal."""
    from src.core.agent_identity import GOVERNANCE_REVOCATIONS_PATH

    return GOVERNANCE_REVOCATIONS_PATH


def _interval_seconds() -> int:
    """The declared cadence this deployment publishes ``next_update`` on."""
    from src.core.config import SigningConfig

    return int(SigningConfig().revocation_interval_seconds)


#: #1291 mp53.6 promoted the JWS envelope helpers to tests/helpers/signing.py so
#: this suite and the e2e key-lifecycle module read the same served JWS through
#: ONE decoder rather than two. Aliased locally (not re-defined) so every call
#: site below keeps its established name.
_b64url_json = b64url_json
_jws_parts = jws_parts


def _seed(env: BareIntegrationEnv, slug: str) -> tuple[Any, str, str]:
    """A tenant with ONE active minted key and ONE key revoked long ago.

    Returns ``(tenant, active_kid, revoked_kid)``.

    Each test gets its own slug: this suite runs against a database that is not
    rolled back between tests, and two live tenants sharing a ``virtual_host``
    would make host routing resolve an arbitrary one.

    The host is DOTTED so ``canonical_agent_url`` derives ``https://`` for it —
    the issuer this document declares is that URL, and an ``http://`` issuer is
    not an origin any counterparty would pin.

    The revoked key's ``revoked_at`` is 30 days back — far outside any grace
    window (``SigningConfig.grace_seconds`` defaults to 2x a 300s cache TTL) —
    which is exactly what makes the retention assertion below non-vacuous.
    """
    from tests.factories import SigningKeyFactory, TenantFactory

    tenant = TenantFactory(
        tenant_id=f"rev_{slug}",
        subdomain=f"revlist-{slug}",
        virtual_host=f"revlist-{slug}.example.com",
    )
    revoked_kid = f"adcp-revoked-{slug}"
    SigningKeyFactory(
        tenant=tenant,
        kid=revoked_kid,
        purpose="request-signing",
        not_before=datetime.now(UTC) - timedelta(days=90),
        revoked_at=datetime.now(UTC) - timedelta(days=30),
    )

    active_kid = f"adcp-active-{slug}"
    provision_key(signing_key_repo(env, tenant.tenant_id), tenant.tenant_id, active_kid)
    env.get_session().commit()
    return tenant, active_kid, revoked_kid


def _published_jwk(client: Any, tenant: Any, kid: str) -> dict[str, Any]:
    """The public JWK for *kid* off our OWN published JWKS.

    Verification goes through the document a counterparty would fetch, not
    through the row: that is the only way the assertion grades the chain a third
    party actually walks — ``kid`` in the revocation list resolves through the
    request-signing JWKS the brand.json hop lands on.
    """
    jwks = get_trust_root_document(client, _JWKS_PATH, tenant)
    matches = [jwk for jwk in jwks["keys"] if jwk["kid"] == kid]
    assert len(matches) == 1, (
        f"the revocation list's signing kid {kid!r} must resolve through our published JWKS — "
        f"a consumer has no other way to check the signature; got kids {[j['kid'] for j in jwks['keys']]}"
    )
    return matches[0]


class TestServedRevocationListIsAVerifiableJws:
    """The published document is a real JWS a third party can check."""

    def test_protected_header_and_signature_verify_against_our_published_jwks(self, integration_db, monkeypatch):
        """The served bytes carry an ``{alg, typ, kid}`` header and a signature
        that verifies under the JWK that ``kid`` resolves to.

        The signature check uses RAW ``cryptography`` — no SDK, no ``src`` — so a
        green here means the bytes are cryptographically sound, not that our
        encoder and our decoder agree with each other.
        """
        from adcp.signing.jws import ALLOWED_JWS_ALGS
        from adcp.signing.revocation_fetcher import REVOCATION_LIST_TYP
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        with deployment_kek(monkeypatch), BareIntegrationEnv(tenant_id="rev_env_header") as env:
            env.setup_default_data()
            tenant, active_kid, _ = _seed(env, "header")
            client = env.get_rest_client()

            document = get_trust_root_document(client, _revocations_path(), tenant)
            b64_protected, b64_payload, signature = _jws_parts(document)

            header = _b64url_json(b64_protected)
            assert set(header) == _PROTECTED_HEADER_MEMBERS, (
                "the protected header carries exactly alg, typ and kid — nothing else is read by "
                f"this profile and an unread member is one a consumer must guess about; got {sorted(header)}"
            )
            assert header["typ"] == REVOCATION_LIST_TYP, (
                f"typ must be the SDK's REVOCATION_LIST_TYP {REVOCATION_LIST_TYP!r} byte-for-byte "
                f"(verify_jws_document compares it with no normalization); got {header['typ']!r}"
            )
            assert header["alg"] in ALLOWED_JWS_ALGS, (
                f"alg must be one the AdCP governance profile allows {sorted(ALLOWED_JWS_ALGS)}; got {header['alg']!r}"
            )
            assert header["kid"] == active_kid, (
                "the list is signed by the tenant's ACTIVE request-signing key, so a consumer can "
                f"resolve it through the same JWKS as a request signature; got {header['kid']!r}"
            )

            jwk = _published_jwk(client, tenant, active_kid)
            public_key = Ed25519PublicKey.from_public_bytes(b64url_decode(jwk["x"]))
            # RFC 7515 §5.2: the signing input is the ORIGINAL base64url strings
            # joined by a dot — never a decode-and-re-encode, which is lenient
            # and can produce different bytes than the wire form.
            public_key.verify(signature, f"{b64_protected}.{b64_payload}".encode("ascii"))

    def test_payload_carries_exactly_the_five_spec_members_and_omits_revoked_jtis(self, integration_db, monkeypatch):
        """``security.mdx``:1328's five members, with ``revoked_jtis`` ASSERTED absent.

        The omission is a decision — ``revoked_jtis`` does not govern request
        signatures — so it is asserted rather than left to whatever the builder
        happened to emit.
        """
        with deployment_kek(monkeypatch), BareIntegrationEnv(tenant_id="rev_env_payload") as env:
            env.setup_default_data()
            tenant, active_kid, revoked_kid = _seed(env, "payload")
            client = env.get_rest_client()

            from src.core.agent_identity import canonical_agent_url

            issuer = canonical_agent_url(tenant)

            document = get_trust_root_document(client, _revocations_path(), tenant)
            _, b64_payload, _ = _jws_parts(document)
            payload = _b64url_json(b64_payload)

            assert set(payload) == _SPEC_PAYLOAD_MEMBERS, (
                "the payload carries exactly the five members security.mdx:1328 names; "
                f"revoked_jtis is a deliberate omission (it does not govern request signatures) "
                f"and any extra member is unspecified. Got {sorted(payload)}"
            )
            assert isinstance(payload["version"], int) and payload["version"] > 0, (
                "version is a POSITIVE int — the consumer's anti-rollback check compares it "
                f"across refreshes; got {payload['version']!r}"
            )
            assert payload["issuer"] == issuer, (
                "issuer is the tenant's canonical agent origin, the same string every other "
                f"trust-root document is derived from; expected {issuer!r}, got {payload['issuer']!r}"
            )
            assert payload["revoked_kids"] == [revoked_kid], (
                "revoked_kids is exactly the tenant's revoked request-signing kids — the active "
                f"signer must never appear in it (it signed this very document). Got "
                f"{payload['revoked_kids']!r}; active kid is {active_kid!r}"
            )

            for field in ("updated", "next_update"):
                moment = datetime.fromisoformat(payload[field])
                assert moment.tzinfo is not None, (
                    f"{field} must be RFC 3339 with an EXPLICIT offset — format: date-time requires "
                    f"it, and a naive datetime's isoformat() emits none; got {payload[field]!r}"
                )

    def test_a_conformant_sdk_consumer_verifies_the_served_document(self, integration_db, monkeypatch):
        """Second opinion from outside ``src/``: the SDK's own JWS verifier accepts it.

        ``verify_detached_jws`` re-runs the whole header policy (alg allowlist,
        exact ``typ``, non-empty ``crit`` refused, ``kid`` resolvable) and the
        cryptographic check against the JWKS a counterparty fetches. It runs
        AFTER the structural assertions above, never instead of them.
        """
        from adcp.signing.jwks import StaticJwksResolver
        from adcp.signing.jws import verify_detached_jws
        from adcp.signing.revocation_fetcher import REVOCATION_LIST_TYP

        with deployment_kek(monkeypatch), BareIntegrationEnv(tenant_id="rev_env_consumer") as env:
            env.setup_default_data()
            tenant, active_kid, revoked_kid = _seed(env, "consumer")
            client = env.get_rest_client()

            document = get_trust_root_document(client, _revocations_path(), tenant)
            b64_protected, b64_payload, signature = _jws_parts(document)
            jwks = get_trust_root_document(client, _JWKS_PATH, tenant)

            # StaticJwksResolver takes a full JWKS ({"keys": [...]}), not a
            # bare JWK — the resolved single JWK must be re-wrapped.
            payload = verify_detached_jws(
                b64_protected=b64_protected,
                b64_payload=b64_payload,
                signature=signature,
                jwks_resolver=StaticJwksResolver({"keys": [_published_jwk(client, tenant, active_kid)]}),
                expected_typ=REVOCATION_LIST_TYP,
            )
            assert payload["revoked_kids"] == [revoked_kid], (
                "the payload the SDK verifier returns must be the same document the structural "
                f"assertions graded; got {payload['revoked_kids']!r}"
            )
            assert jwks["keys"], "the JWKS the consumer resolves the kid through must not be empty"


class TestRevocationRetentionIsUnbounded:
    """A kid dropped from ``revoked_kids`` is a kid UN-REVOKED."""

    def test_a_kid_aged_out_of_the_jwks_grace_window_stays_in_revoked_kids(self, integration_db, monkeypatch):
        """The same kid: GONE from the JWKS, PRESENT in the revocation list.

        This is the assertion that separates the new repository query from
        ``publishable_at``. ``publishable_at`` drops a revoked key once the grace
        window elapses — correctly, because the JWKS is a key set. The combined
        list is a permanent RECORD (``security.mdx``:1328), so the same elapsed
        window must change nothing about it.
        """
        with deployment_kek(monkeypatch), BareIntegrationEnv(tenant_id="rev_env_retention") as env:
            env.setup_default_data()
            tenant, _, revoked_kid = _seed(env, "retention")
            client = env.get_rest_client()

            jwks_kids = {jwk["kid"] for jwk in get_trust_root_document(client, _JWKS_PATH, tenant)["keys"]}
            assert revoked_kid not in jwks_kids, (
                f"precondition: {revoked_kid!r} was revoked far outside the grace window, so "
                f"publishable_at must already have dropped it from the JWKS; got {sorted(jwks_kids)}"
            )

            _, b64_payload, _ = _jws_parts(get_trust_root_document(client, _revocations_path(), tenant))
            assert revoked_kid in _b64url_json(b64_payload)["revoked_kids"], (
                f"{revoked_kid!r} is gone from the JWKS but MUST remain in revoked_kids — dropping it "
                "would tell every consumer the key is no longer revoked, which is the inverse of the "
                "fact. The list's retention is unbounded; the JWKS grace window is not"
            )


class TestDeclaredCadence:
    """``updated``/``next_update`` are quantized onto the declared interval."""

    def test_next_update_is_one_declared_interval_after_a_quantized_updated(self, integration_db, monkeypatch):
        """``updated`` sits on an interval boundary and ``next_update`` is one interval later.

        Quantization is what makes the document byte-stable within a bucket, so
        an HTTP cache and the consumer's anti-rollback check both see an
        unchanged document until something actually changes. The interval itself
        is bounded below by our own published brand.json TTL and above by
        ``security.mdx``:717's 30-minute ceiling.
        """
        from src.core.signing import CACHE_MAX_AGE_SECONDS

        interval = _interval_seconds()
        assert CACHE_MAX_AGE_SECONDS <= interval <= 1800, (
            "the declared cadence must be at least our own published brand.json cache TTL "
            "(security.mdx:1103 bounds the TTL by the interval, and CACHE_MAX_AGE_SECONDS is a "
            f"fixed constant that cannot shrink) and at most :717's 30-minute ceiling; got {interval}"
        )

        with deployment_kek(monkeypatch), BareIntegrationEnv(tenant_id="rev_env_cadence") as env:
            env.setup_default_data()
            tenant, _, _ = _seed(env, "cadence")
            client = env.get_rest_client()

            _, b64_payload, _ = _jws_parts(get_trust_root_document(client, _revocations_path(), tenant))
            payload = _b64url_json(b64_payload)

            updated = datetime.fromisoformat(payload["updated"])
            next_update = datetime.fromisoformat(payload["next_update"])

            assert int(updated.timestamp()) % interval == 0, (
                f"updated must be floor(now/interval)*interval so the document is byte-stable within "
                f"a bucket; {payload['updated']!r} is not on a {interval}s boundary"
            )
            assert next_update - updated == timedelta(seconds=interval), (
                f"next_update must be exactly one declared interval ({interval}s) after updated; got "
                f"{(next_update - updated).total_seconds()}s"
            )

    def test_two_gets_in_one_bucket_return_the_identical_signed_document(self, integration_db, monkeypatch):
        """Same bucket, same bytes — including the signature.

        An ``updated`` read off the raw clock would re-sign a different payload
        on every GET, defeating the caching the ``Cache-Control`` header asks for
        and making every refresh look like a change to the consumer.

        Asserted flatly rather than guarded by "unless the two GETs straddled a
        bucket boundary": the interval floor is ``CACHE_MAX_AGE_SECONDS`` (300s)
        and these two GETs are milliseconds apart, so a straddle is a ~1-in-10⁵
        event — and a conditional escape hatch would silently stop grading the
        thing on the run where it mattered.
        """
        with deployment_kek(monkeypatch), BareIntegrationEnv(tenant_id="rev_env_stable") as env:
            env.setup_default_data()
            tenant, _, _ = _seed(env, "stable")
            client = env.get_rest_client()

            first = get_trust_root_document(client, _revocations_path(), tenant)
            second = get_trust_root_document(client, _revocations_path(), tenant)

            assert first == second, (
                "two GETs inside one quantization bucket must return the IDENTICAL document, "
                "signature included — otherwise the quantization buys no cacheability at all. "
                f"updated moved {_b64url_json(_jws_parts(first)[1])['updated']!r} -> "
                f"{_b64url_json(_jws_parts(second)[1])['updated']!r}"
            )


class TestUnpublishableWithoutAnActiveSigner:
    """Fail closed: revoke-without-rotate withdraws the document, it does not fake one."""

    def test_the_route_404s_once_the_only_request_signing_key_is_revoked(self, integration_db, monkeypatch):
        """200 while a signer exists; 404 the moment it is revoked.

        The control half is what makes this non-vacuous: a 404 alone is equally
        true of a route that was never registered. Serving an UNSIGNED or
        stale-signed list would be the security hole; withdrawing it is the
        documented invariant — rotate BEFORE you revoke.
        """
        with deployment_kek(monkeypatch), BareIntegrationEnv(tenant_id="rev_env_unpublishable") as env:
            env.setup_default_data()
            tenant, active_kid, _ = _seed(env, "unpublishable")
            client = env.get_rest_client()

            get_trust_root_document(client, _revocations_path(), tenant)

            repo = signing_key_repo(env, tenant.tenant_id)
            repo.revoke(active_kid, at=datetime.now(UTC))
            env.get_session().commit()

            get_trust_root_document(client, _revocations_path(), tenant, expect_status=404)
