"""Integration tests pinning salesagent-z6nr.11 (#1291 A5) — checklist STEP 9,
revocation, decided by exactly one callable handed to
``VerifyOptions.revocation_checker``.

These are TDD-red: ``src.core.signing.revocation`` does not exist yet and
``SigningConfig`` carries none of the Group-C revocation knobs, so this module
fails at collection. That is legitimate red — what matters is that each test
below encodes the behavior the REFINED plan creates
(``bd show salesagent-z6nr.11`` → "## Refinement (atom salesagent-srpm.20,
post-review)"). The refinement AMENDS the original Implementation Plan and wins
wherever the two differ.

Spec grounding (reproducible):
``git -C ~/projects/adcp show v3.1.1:docs/building/by-layer/L1/security.mdx``
— :1238 (step 9: membership then staleness), :1328 (one combined list at the
brand.json origin), :1333 (fetch-failure safe default, grace = 4x the previous
polling interval), :1686-1689 (the stale-then-membership pseudo-code).
Compliance vector ``dist/compliance/3.1.1/test-vectors/request-signing/
negative/017-key-revoked.json`` is graded at the WIRE by B3
(``salesagent-z6nr.14``); nothing here re-grades its corpus.

What is graded here, and why each one exists
--------------------------------------------

**The three that would otherwise be production incidents.** Wiring a bare
``CachingRevocationChecker`` into ``_run_verifier`` turns three ordinary
conditions into 500s, because the SDK raises plain ``Exception`` subclasses on
a path whose only catch is ``except SignatureVerificationError``
(``request_verifier_middleware.py:341``):

1. *A stale list* — ``RevocationListFreshnessError`` (``revocation_fetcher.py:122``)
   escapes ``verifier.py:294`` untranslated. The graded answer is a 401 carrying
   ``request_signature_revocation_stale``; the SDK docstring at :600-602 claims
   the verifier maps it and the verifier does not.
   :class:`TestStaleRevocationList`.
2. *An unresolvable (or blocked) revocation host* — R-H1. ``default_revocation_list_fetcher``
   calls ``build_ip_pinned_transport`` OUTSIDE its ``except httpx.HTTPError``
   (``revocation_fetcher.py:262``) and that raises ``SSRFValidationError``
   — a bare ``Exception`` (``ip_pinned_transport.py:78-79`` turns
   ``OSError``/gaierror into it) — which neither ``_ensure_fresh``'s warm path
   (``except (RevocationListFetchError, RevocationListParseError)``, :800) nor
   its cold path (no ``except`` at all) catches. A transient DNS failure on one
   counterparty's revocation endpoint would take down every signed request from
   that counterparty. :class:`TestRevocationHostThatCannotBeDialed`.
3. *A counterparty that publishes no list at all* — the COLD path.
   ``_ensure_fresh:779-781`` calls ``_refresh`` with no ``except`` when
   ``_current_list is None``. Nobody in the ecosystem publishes a list today, so
   this is the DEFAULT case, not an edge case. :class:`TestAbsentRevocationList`.

**R-M3 — the static set is consulted FIRST.** The spec contradicts itself here
(:1238 lists membership first, the pseudo-code at :1686-1689 checks staleness
first) and no vector grades the combination. Under fetched-first a deployment
that ever set ``require_revocation_list=True`` would answer
``request_signature_revocation_stale`` for the seeded ``test-revoked-2026``,
turning vector 017 into a graded FAIL caused by an unrelated config flag.
:class:`TestStaticSetIsConsultedFirst` pins the ORDERING, not just the outcome:
the wire code discriminates the two orderings, and the unavailable-metric
proves the fetched checker was never consulted at all.

**F7 — the ordering canary.** Vector 017's ``$comment`` says its ``Signature``
bytes are "a placeholder copied from positive/001 and do not verify
cryptographically", and that a crypto-first verifier answers
``request_signature_invalid`` which graders should surface as a step-ordering
bug. Revoked keyid + deliberately corrupt signature must still answer
``request_signature_key_revoked`` (step 9 at ``verifier.py:294``, before crypto
verify at :311). Cheap, permanent. :class:`TestRevokedKeyidRejects`.

**R-M1 — no permanent JWKS snapshot.** Keying the registry on issuer origin
while constructing with ``StaticJwksResolver(resolution.jwks)`` breaks two ways,
both flowing from :1328's ONE-combined-list-per-operator-origin model: an
operator rotating its list-signing key breaks revocation for that origin until
process restart (the snapshot never refreshes even though ``AgentResolution``
re-resolves every ``agent_resolution_ttl_seconds``), and two counterparties
sharing an operator origin 401 each other. :class:`TestResolverReadsThroughTheResolutionCache`.

**R-M2 — no cross-counterparty head-of-line blocking.** ``_run_verifier`` runs
under ``asyncio.to_thread`` (``request_verifier_middleware.py:329``), which is
the thread-pool case ``revocation_fetcher.py:609-618`` says needs an external
lock — but a module-level lock held around ``__call__`` spans the 10s-default
HTTPS fetch inside ``_ensure_fresh``, so one dead counterparty stalls every
signed request in the process. :class:`TestNoCrossCounterpartyBlocking`.

**R-L — grace multiplier 4.0.** ``DEFAULT_GRACE_MULTIPLIER`` is 2.0
(``revocation_fetcher.py:71``); security.mdx :1333 requires 4x. Asserted on a
``checker_for``-BUILT entry, never an injected one: injecting bypasses the
single place the argument is passed, so a reverted argument would silently
halve outage tolerance with every test still green.
:class:`TestCheckerForBuildsTheSpecCompliantChecker`.

Why these tests are not vacuous
-------------------------------

Every wire test runs the REAL SDK checklist over a REAL Ed25519 signature and
observes the REAL 401/200 plus ``WWW-Authenticate``; the bucket is pinned to
``supported`` in every case, because a rejection inside ``warn_for`` is
swallowed to a 200 by ``_handle_rejection`` and would grade nothing (R8). The
only thing ever substituted is the HTTP fetch of the counterparty's list —
through the SDK's own documented ``fetcher=`` seam on a real
``CachingRevocationChecker``, or not at all (the SSRF tests dial for real).

Contract the implement atom (salesagent-srpm.7) must satisfy
------------------------------------------------------------

``src.core.config.SigningConfig``
  ``revoked_keyids: str``            comma-separated, ``revoked_keyid_list`` property
  ``require_revocation_list: bool``  default False (R1 posture flag)
  ``revocation_grace_multiplier: float``  default 4.0
  (``revocation_issuer_origin`` is in the plan but no test pins it here.)

``src.core.signing.revocation``
  ``CounterpartyRevocationChecker(*, static_revoked, fetched, require_list)``
      the ONE translation site; implements the SDK ``RevocationChecker``
      Protocol (``__call__(keyid) -> bool``)
  ``checker_for(resolution, config) -> CounterpartyRevocationChecker``
      the ONE construction site — the only place ``grace_multiplier`` is passed
  ``REVOCATION_CHECKER_CACHE: dict[str, CachingRevocationChecker]``
      process-level registry keyed by the ``scheme://netloc`` origin derived
      from ``AgentResolution.brand_json_url``, holding the SDK checker itself
      (plan step 2). A checker already present for an origin is REUSED as-is —
      which is also what R3 requires, since constructing per request would
      hammer the counterparty's endpoint. Any per-checker lock must therefore be
      obtained by origin with ``setdefault`` semantics, so an entry that appears
      without a paired lock (a test seeding one, or a concurrent builder) is
      still usable.

``src.core.signing.request_verifier_middleware``
  ``checker_for``                    imported into the middleware namespace and
                                     passed as ``VerifyOptions.revocation_checker``
                                     in ``_run_verifier`` (plan step 3)

``src.core.metrics``
  ``adcp_request_revocation_unavailable_total``  incremented on the R1
      fail-open path. These tests assert the FAMILY TOTAL and deliberately not
      the label set: the review's cardinality finding (``issuer`` is
      counterparty-supplied and unbounded, vs a bounded ``reason``) was not
      settled by the refinement, and pinning a label here would prejudge it.

Covers: salesagent-z6nr.11 (Core Invariant + Refinement R-H1, R-M1, R-M2,
R-M3, R-L, and F7's ordering canary).
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from adcp.signing import (
    REQUEST_SIGNATURE_INVALID,
    REQUEST_SIGNATURE_KEY_REVOKED,
    REQUEST_SIGNATURE_REVOCATION_STALE,
    StaticJwksResolver,
)
from adcp.signing.revocation_fetcher import (
    CachingRevocationChecker,
    FetchResult,
    RevocationListFetchError,
)

from src.core.config import SigningConfig
from src.core.signing import request_verifier_middleware as mw
from src.core.signing.revocation import (
    REVOCATION_CHECKER_CACHE,
    CounterpartyRevocationChecker,
    checker_for,
)
from tests.harness._base import BareIntegrationEnv
from tests.helpers.log_capture import LogCaptureHandler

# tests/helpers/signing.py owns the wire plumbing for signed requests
# (declaration seam, counterparty resolution seam, signing, rejection-code
# extraction, counter reads) — salesagent-z6nr.14 step 2. Reusing it is what
# keeps step 9's tests from re-deriving B1's harness: a second copy would be the
# duplication class CLAUDE.md's DRY invariant exists to prevent, and it would
# drift the moment the wire changed. What stays below is only this suite's own —
# the operator origins, the ageing checkers, the revocation JWS.
from tests.helpers.signing import (
    BODYLESS_ADCP_PATH,
    COUNTERPARTY_AGENT_URL,
    COUNTERPARTY_KID,
    LADDER_OPERATIONS,
    SIGNING_PRINCIPAL_ID,
    SIGNING_TENANT_ID,
    always_authorized_brand_resolver,
    assert_counter_delta,
    bucketed_declaration,
    counterparty_key,
    keypair_for,
    seed_principal,
    seeded_cache_entry,
    signed_headers,
)
from tests.helpers.signing import (
    counter_total as _counter_total,
)
from tests.helpers.signing import (
    declared_posture as _declared_posture,
)
from tests.helpers.signing import (
    rejection_code as _rejection_code,
)
from tests.helpers.signing import (
    signing_config as _signing_config,
)

#: Metric for the R1 fail-open path (plan step 4). Family total only — see the
#: module docstring on why the label set is deliberately not asserted.
_UNAVAILABLE_METRIC = "adcp_request_revocation_unavailable_total"

#: The logger the R1 WARNING must reach. The handler is attached to the PACKAGE
#: logger, not to ``src.core.signing.revocation``, so the assertion grades that
#: the fail-open is announced — not which module announced it.
_SIGNING_LOGGER = "src.core.signing"

#: An operator origin whose list is served by an injected fetcher: the stub
#: intercepts before any socket, so this host is never dialed.
_STUBBED_ORIGIN = "https://operator.example.com"

#: Two origins that raise ``SSRFValidationError`` inside
#: ``build_ip_pinned_transport`` — the R-H1 escape path. The link-local address
#: is the cloud-metadata block and needs NO DNS at all, so it is deterministic
#: offline; the ``.invalid`` host is RFC 2606-reserved and exercises the
#: gaierror → ``SSRFValidationError`` conversion that R-H1 is actually about.
_BLOCKED_ORIGIN = "https://169.254.169.254"
_UNRESOLVABLE_ORIGIN = "https://revocation-host.invalid"

#: A second counterparty served by the SAME operator origin (R-M1, :1328).
_OTHER_AGENT_URL = "https://other-buyer.example.com/a2a"


# --------------------------------------------------------------------------
# Seams
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_registry() -> Iterator[None]:
    """The checker registry is process-level; no test may inherit another's."""
    REVOCATION_CHECKER_CACHE.clear()
    yield
    REVOCATION_CHECKER_CACHE.clear()


@contextmanager
def _counterparty_at(origin: str, jwks: dict[str, Any]) -> Iterator[None]:
    """Seed :data:`COUNTERPARTY_AGENT_URL`'s resolution with its operator origin set.

    Layered on the shared ``counterparty_key`` (which owns the whole
    :class:`AgentResolution` shape) because A5 needs exactly one field of it to
    vary: ``brand_json_url`` is where the revocation issuer origin comes from
    (F5, security.mdx :1328 — the combined list is served at the brand.json
    origin, so the issuer is per-counterparty and not one per process).

    Rebinding ``brand_json_url`` also repoints Tier 3 (#1291 hksr): ``_run_verifier``
    succeeding hands this resolution to ``_check_brand_authorization``, which builds
    its resolver from the (now-rebound) ``brand_json_url`` — a DIFFERENT cache key
    than the one ``counterparty_key`` seeded an authorizing resolver under. None of
    this suite grades Tier 3, and some of *origin*'s values (an SSRF-blocked IP, an
    unresolvable ``.invalid`` host) are not real registrable domains at all, so the
    real ``BrandJsonAuthorizationResolver`` would refuse them via its own
    ``registrable_domain`` validation regardless of mocking the fetch. An
    unconditional double is seeded here instead, under the SAME rebound key.
    """
    with counterparty_key(jwks):
        cached = mw.AGENT_RESOLUTION_CACHE[COUNTERPARTY_AGENT_URL]
        rebound_brand_json_url = f"{origin}/.well-known/brand.json"
        mw.AGENT_RESOLUTION_CACHE[COUNTERPARTY_AGENT_URL] = cached.model_copy(
            update={"brand_json_url": rebound_brand_json_url}
        )
        with seeded_cache_entry(
            mw._BRAND_AUTHZ_RESOLVER_CACHE, rebound_brand_json_url, always_authorized_brand_resolver()
        ):
            yield


@contextmanager
def _warnings() -> Iterator[LogCaptureHandler]:
    """Capture WARNING records from the signing package for the duration."""
    handler = LogCaptureHandler()
    logger = logging.getLogger(_SIGNING_LOGGER)
    logger.addHandler(handler)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)


def _revocation_warned(handler: LogCaptureHandler) -> bool:
    return any("revocation" in record.lower() for record in handler.records)


def _jwks_for(kid: str) -> dict[str, Any]:
    """A one-key JWK set for *kid*, when the private half is not needed."""
    return keypair_for(kid)[1]


@pytest.fixture(scope="module")
def counterparty_keypair() -> tuple[Any, dict[str, Any]]:
    """The counterparty's request-signing keypair, under :data:`COUNTERPARTY_KID`.

    The MATERIAL comes from the shared ``keypair_for``; only the fixture is
    redeclared rather than imported, because a fixture imported into a module is
    then shadowed by every test parameter that requests it (ruff F811), which is
    noise that would have to be silenced once per test.
    """
    return keypair_for(COUNTERPARTY_KID)


# --------------------------------------------------------------------------
# Signed requests through the real middleware
# --------------------------------------------------------------------------


def _with_corrupt_signature(headers: dict[str, str]) -> dict[str, str]:
    """Flip one base64 character of the ``Signature`` value.

    Deliberately NOT a malformed header: the RFC 8941 byte-sequence still parses
    and still decodes, so the checklist reaches step 9 exactly as it does for a
    good signature and only step 10 (``verifier.py:311``) can reject it. That is
    vector 017's own construction — its signature bytes are a documented
    placeholder that does not verify.
    """
    label, _, wrapped = headers["Signature"].partition("=:")
    encoded = wrapped.rstrip(":")
    flipped = ("B" if encoded[0] == "A" else "A") + encoded[1:]
    return {**headers, "Signature": f"{label}=:{flipped}:"}


class _SignedCaller:
    """One counterparty, signing real requests at a REST client on demand."""

    def __init__(self, env: BareIntegrationEnv, keypair: tuple[Any, dict[str, Any]]) -> None:
        self.private_key, self.jwks = keypair
        self._token = seed_principal(env)
        self._client = env.get_rest_client()

    def send(self, *, corrupt_signature: bool = False) -> Any:
        """Sign a POST over its own wire bytes and send it.

        Signed fresh per call — ``sign_request`` mints a new ``nonce`` every
        time, so two requests inside one test do not collide in A4's replay
        store.
        """
        body = json.dumps({"context": {"request_id": "revocation-probe"}}).encode()
        headers = signed_headers(
            self.private_key,
            self._token,
            method="POST",
            path=BODYLESS_ADCP_PATH,
            body=body,
            extra={"Content-Type": "application/json"},
        )
        if corrupt_signature:
            headers = _with_corrupt_signature(headers)
        return self._client.post(BODYLESS_ADCP_PATH, content=body, headers=headers)


@contextmanager
def _caller(keypair: tuple[Any, dict[str, Any]]) -> Iterator[_SignedCaller]:
    """A tenant, its principal and a signing counterparty, ready to send."""
    with BareIntegrationEnv(tenant_id=SIGNING_TENANT_ID, principal_id=SIGNING_PRINCIPAL_ID) as env:
        yield _SignedCaller(env, keypair)


@contextmanager
def _step_nine(*, origin: str, jwks: dict[str, Any], **config: Any) -> Iterator[None]:
    """Everything step 9 is decided under: bucket, operator origin, posture.

    The bucket is ``supported`` in every test and never ``warn``: a rejection in
    a ``warn_for`` operation is swallowed to a 200 by ``_handle_rejection``
    (``request_verifier_middleware.py:366-377``), so a revocation test declared
    into the warn bucket would grade nothing at all (R8).
    """
    with (
        _declared_posture(**bucketed_declaration("supported", *LADDER_OPERATIONS)),
        _counterparty_at(origin, jwks),
        _signing_config(**config),
    ):
        yield


# --------------------------------------------------------------------------
# Revocation-list fixtures (the SDK's own ``fetcher=`` seam)
# --------------------------------------------------------------------------


def _revocation_jws(private_key: Any, *, issuer: str, updated: datetime, next_update: datetime) -> str:
    """A COMPACT JWS the SDK will accept as a COUNTERPARTY's revocation list.

    Deliberately NOT repointed at ``src.core.signing.sign_revocation_list``
    (#1291 A5 follow-up, salesagent-z6nr.27's production producer): that
    function signs OUR OWN document (payload derived from a real ``Tenant``
    row) and emits GENERAL-JSON serialization, matching
    ``parse_general_json_jws``. This fixture signs as an ARBITRARY
    COUNTERPARTY with an arbitrary issuer and must stay on the COMPACT path
    — the one thing ``CachingRevocationChecker``'s ``fetcher=`` seam here
    returns as an HTTP response BODY (a string), and the one shape this A5
    suite exercises so repointing the producer elsewhere doesn't silently
    move every consumer test off compact JWS (architect review,
    salesagent-js3z.28). What IS shared with production: the SDK's own
    ``b64url_encode``/``REVOCATION_LIST_TYP`` primitives and the RFC3339
    formatter, instead of hand-rolled base64/strftime — the actual dedup
    target the codebase-scan atom flagged.
    """
    from adcp.signing.crypto import b64url_encode
    from adcp.signing.revocation_fetcher import REVOCATION_LIST_TYP

    from src.core.signing._rfc3339 import rfc3339

    def _segment(obj: dict[str, Any]) -> str:
        return b64url_encode(json.dumps(obj).encode())

    header = _segment({"alg": "EdDSA", "typ": REVOCATION_LIST_TYP, "kid": COUNTERPARTY_KID})
    payload = _segment(
        {
            "version": int(updated.timestamp()),
            "issuer": issuer,
            "updated": rfc3339(updated),
            "next_update": rfc3339(next_update),
            "revoked_kids": [],
            "revoked_jtis": [],
        }
    )
    signature = private_key.sign(f"{header}.{payload}".encode("ascii"))
    return f"{header}.{payload}.{b64url_encode(signature)}"


def _install_checker(origin: str, checker: CachingRevocationChecker) -> None:
    """Seed the registry so ``checker_for`` reuses this checker for *origin*."""
    REVOCATION_CHECKER_CACHE[origin] = checker


def _dead_endpoint_checker(origin: str, jwks: dict[str, Any]) -> CachingRevocationChecker:
    """A real SDK checker whose endpoint never serves a list.

    This is the COLD path exactly: ``_ensure_fresh`` sees ``_current_list is
    None`` and calls ``_refresh`` with no ``except``, so the real
    ``RevocationListFetchError`` propagates out of ``__call__``. Nothing about
    the SDK's behavior is faked — only the socket is.
    """

    def _fetcher(uri: str, **_kwargs: Any) -> FetchResult:
        raise RevocationListFetchError(f"revocation list GET {uri!r} failed: no list published")

    return CachingRevocationChecker.from_issuer_origin(
        origin,
        jwks_resolver=StaticJwksResolver(jwks),
        fetcher=_fetcher,
        grace_multiplier=4.0,
    )


class _AgeingChecker:
    """A real SDK checker over a list that loads once and then ages out.

    Drives the genuine ``RevocationListFreshnessError`` lifecycle
    (``_ensure_fresh:775-816``) through the SDK's own injectable ``clock`` /
    ``wall_clock`` / ``fetcher`` parameters: first call loads a fresh list,
    :meth:`age_past_grace` moves wall time past ``next_update + grace`` and
    monotonic time past the 60s refetch cooldown, and the refetch then fails.
    """

    INTERVAL_SECONDS = 120.0
    GRACE_MULTIPLIER = 4.0

    def __init__(self, private_key: Any, *, origin: str, jwks: dict[str, Any]) -> None:
        self._origin = origin
        self._wall = datetime.now(UTC)
        self._mono = 10_000.0
        self._served = False
        body = _revocation_jws(
            private_key,
            issuer=origin,
            updated=self._wall,
            next_update=self._wall + timedelta(seconds=self.INTERVAL_SECONDS),
        )

        def _fetcher(uri: str, **_kwargs: Any) -> FetchResult:
            if self._served:
                raise RevocationListFetchError(f"revocation list GET {uri!r} failed: operator down")
            self._served = True
            return FetchResult(body=body, etag=None, last_modified=None, not_modified=False)

        self.checker = CachingRevocationChecker.from_issuer_origin(
            origin,
            jwks_resolver=StaticJwksResolver(jwks),
            fetcher=_fetcher,
            grace_multiplier=self.GRACE_MULTIPLIER,
            clock=lambda: self._mono,
            wall_clock=lambda: self._wall,
        )

    def age_past_grace(self) -> None:
        """Move past ``next_update + grace``, with the refetch cooldown elapsed."""
        grace = self.INTERVAL_SECONDS * self.GRACE_MULTIPLIER
        self._wall = self._wall + timedelta(seconds=self.INTERVAL_SECONDS + grace + 60)
        self._mono += self.INTERVAL_SECONDS + grace + 60


# --------------------------------------------------------------------------
# Membership — the graded outcome, and F7's ordering canary
# --------------------------------------------------------------------------


@pytest.mark.requires_db
class TestRevokedKeyidRejects:
    """A revoked keyid answers 401 ``request_signature_key_revoked`` at step 9."""

    def test_a_revoked_keyid_is_rejected_with_the_spec_code(self, integration_db, counterparty_keypair):
        """The locally-seeded revocation set decides step 9 on the wire.

        ``revoked_keyids`` is the monotone fail-closed knob the B4 grading
        deployment sets to ``test-revoked-2026``; it can only ADD rejections,
        which is what makes it a posture and not a backdoor (the SDK applies the
        same asymmetry to fetched data at ``_post_jws_validation:452-462`` —
        "the spec doesn't permit un-revocation").
        """
        with _caller(counterparty_keypair) as caller:
            with _step_nine(origin=_BLOCKED_ORIGIN, jwks=caller.jwks, revoked_keyids=COUNTERPARTY_KID):
                response = caller.send()

            assert _rejection_code(response) == REQUEST_SIGNATURE_KEY_REVOKED, (
                f"a signed request from a revoked keyid must be rejected with "
                f"{REQUEST_SIGNATURE_KEY_REVOKED!r}; got status {response.status_code}, "
                f"WWW-Authenticate={response.headers.get('WWW-Authenticate')!r}"
            )

    def test_revocation_is_decided_before_the_signature_is_verified(self, integration_db, counterparty_keypair):
        """F7's ordering canary — the cheapest permanent guard on §8 invariant 1.

        Vector 017's ``Signature`` bytes are a placeholder that does NOT verify
        cryptographically, so a verifier that ran crypto first would answer
        ``request_signature_invalid`` and the graders surface that as a
        step-ordering bug. Revoked keyid + corrupt signature must answer
        ``request_signature_key_revoked``: step 9 (``verifier.py:294``) runs
        before step 10 (:311), and nothing in salesagent may reorder it.
        """
        with _caller(counterparty_keypair) as caller:
            with _step_nine(origin=_BLOCKED_ORIGIN, jwks=caller.jwks, revoked_keyids=COUNTERPARTY_KID):
                response = caller.send(corrupt_signature=True)

            code = _rejection_code(response)
            assert code != REQUEST_SIGNATURE_INVALID, (
                "step 9 must decide before step 10: a revoked keyid carrying a signature "
                f"that does not verify answered {REQUEST_SIGNATURE_INVALID!r}, which is the "
                "step-ordering bug vector 017 is designed to surface"
            )
            assert code == REQUEST_SIGNATURE_KEY_REVOKED, (
                f"expected {REQUEST_SIGNATURE_KEY_REVOKED!r} on the wire; got status "
                f"{response.status_code}, WWW-Authenticate={response.headers.get('WWW-Authenticate')!r}"
            )


# --------------------------------------------------------------------------
# R-M3 — the static set is consulted FIRST
# --------------------------------------------------------------------------


@pytest.mark.requires_db
class TestStaticSetIsConsultedFirst:
    """A locally-configured revocation is a permanent fact with no staleness question."""

    def test_a_seeded_keyid_is_revoked_without_consulting_the_fetched_list(self, integration_db, counterparty_keypair):
        """R-M3, pinned as an ORDERING and not merely an outcome.

        The counterparty publishes no list (its origin cannot even be dialed)
        and ``require_revocation_list=True``, so the two orderings answer with
        two different spec codes: static-first → ``request_signature_key_revoked``,
        fetched-first → ``request_signature_revocation_stale``. The unchanged
        fail-open metric is the second half of the evidence — under static-first
        the fetched checker is never reached, so nothing can have been recorded
        about its absence.

        This is why the ordering matters at all: under fetched-first, flipping
        an unrelated posture flag turns compliance vector 017 into a graded FAIL.
        """
        with _caller(counterparty_keypair) as caller:
            with (
                assert_counter_delta(
                    _UNAVAILABLE_METRIC,
                    0,
                    why="the static revocation set answers first, so the list is never consulted "
                    "and its unavailability is never counted",
                ),
                _step_nine(
                    origin=_UNRESOLVABLE_ORIGIN,
                    jwks=caller.jwks,
                    revoked_keyids=COUNTERPARTY_KID,
                    require_revocation_list=True,
                ),
            ):
                response = caller.send()

            assert _rejection_code(response) == REQUEST_SIGNATURE_KEY_REVOKED, (
                "the static revocation set must be checked FIRST and answer immediately: a "
                f"seeded keyid must yield {REQUEST_SIGNATURE_KEY_REVOKED!r} even when "
                "require_revocation_list is True and the counterparty publishes no list. Got "
                f"{_rejection_code(response)!r} (status {response.status_code}) — "
                f"{REQUEST_SIGNATURE_REVOCATION_STALE!r} here means the fetched checker was "
                "consulted first, which makes vector 017 fail on a config flag"
            )


# --------------------------------------------------------------------------
# The cold path — a counterparty that publishes no list
# --------------------------------------------------------------------------


@pytest.mark.requires_db
class TestAbsentRevocationList:
    """R1 — the absent-list posture is DECIDED, logged and metered; never a 500."""

    def test_absent_list_fails_open_with_a_warning_and_a_metric(self, integration_db, counterparty_keypair):
        """Default posture: a counterparty publishing no list still gets served.

        ``_ensure_fresh:779-781`` calls ``_refresh`` with no ``except`` on the
        cold path, so a bare ``CachingRevocationChecker`` would raise
        ``RevocationListFetchError`` out of ``verifier.py:294``, past
        ``request_verifier_middleware.py:341``, and 500 EVERY signed request
        from that counterparty. Nobody in the ecosystem publishes a list today,
        so this is the default case.

        Fail-open here is spec-legitimate and not a quiet failure: :1333's MUST
        is conditioned on "have not refreshed within ``next_update`` + grace",
        and with no list ever loaded there is no ``next_update`` for it to bite
        on. The WARNING and the counter are what make it a posture.
        """
        with _caller(counterparty_keypair) as caller:
            _install_checker(_STUBBED_ORIGIN, _dead_endpoint_checker(_STUBBED_ORIGIN, caller.jwks))

            before = _counter_total(_UNAVAILABLE_METRIC)
            with (
                _step_nine(origin=_STUBBED_ORIGIN, jwks=caller.jwks, require_revocation_list=False),
                _warnings() as warnings,
            ):
                response = caller.send()
            after = _counter_total(_UNAVAILABLE_METRIC)

            assert response.status_code == 200, (
                "a counterparty that publishes no revocation list must not break its signed "
                f"traffic; got {response.status_code}"
                + (
                    " — a 500 means the cold-path fetch error escaped the translation site"
                    if response.status_code >= 500
                    else ""
                )
                + f": {response.text[:300]}"
            )
            assert after == before + 1, (
                f"the fail-open must be metered exactly once — it is the evidence for flipping "
                f"require_revocation_list as the ecosystem starts publishing; {_UNAVAILABLE_METRIC} "
                f"went {before} -> {after}"
            )
            assert _revocation_warned(warnings), (
                "the fail-open must be announced at WARNING (no quiet failures); the "
                f"{_SIGNING_LOGGER!r} logger recorded {warnings.records!r}"
            )

    def test_require_revocation_list_turns_absence_into_the_stale_rejection(self, integration_db, counterparty_keypair):
        """The strict posture, which is what makes the default a DECISION.

        Same request, same absent list, one flag: the deployment that has
        finished waiting for the ecosystem rejects instead of serving, with the
        one spec code step 9 has for "I could not decide".
        """
        with _caller(counterparty_keypair) as caller:
            _install_checker(_STUBBED_ORIGIN, _dead_endpoint_checker(_STUBBED_ORIGIN, caller.jwks))

            with _step_nine(origin=_STUBBED_ORIGIN, jwks=caller.jwks, require_revocation_list=True):
                response = caller.send()

            assert _rejection_code(response) == REQUEST_SIGNATURE_REVOCATION_STALE, (
                f"under require_revocation_list the absent list must reject with "
                f"{REQUEST_SIGNATURE_REVOCATION_STALE!r}; got status {response.status_code}, "
                f"WWW-Authenticate={response.headers.get('WWW-Authenticate')!r}"
            )


# --------------------------------------------------------------------------
# R-H1 — the revocation host that cannot be dialed
# --------------------------------------------------------------------------


@pytest.mark.requires_db
class TestRevocationHostThatCannotBeDialed:
    """``SSRFValidationError`` must take the R1 path, never escape as a 500."""

    @pytest.mark.parametrize(
        "origin",
        [_UNRESOLVABLE_ORIGIN, _BLOCKED_ORIGIN],
        ids=["host-does-not-resolve", "host-in-a-blocked-range"],
    )
    def test_an_undialable_revocation_host_fails_open_instead_of_500ing(
        self, integration_db, counterparty_keypair, origin
    ):
        """R-H1, graded through the REAL default fetcher — no stub anywhere.

        ``default_revocation_list_fetcher`` calls ``build_ip_pinned_transport``
        OUTSIDE its ``except httpx.HTTPError`` (``revocation_fetcher.py:262``),
        and that raises ``SSRFValidationError`` — a bare ``Exception`` — when the
        host does not resolve (``ip_pinned_transport.py:78-79`` converts
        ``OSError``/gaierror) or lands in a blocked range. It is caught by
        neither ``_ensure_fresh``'s warm path (:800) nor its cold path (no
        ``except``), so without an explicit member in the translation site's
        exception tuple a transient DNS failure on ONE counterparty's revocation
        endpoint takes down EVERY signed request from that counterparty.

        Both parameters reach the identical escape; the blocked range needs no
        DNS at all, so the guard holds even on an isolated runner.
        """
        with _caller(counterparty_keypair) as caller:
            before = _counter_total(_UNAVAILABLE_METRIC)
            with (
                _step_nine(origin=origin, jwks=caller.jwks, require_revocation_list=False),
                _warnings() as warnings,
            ):
                response = caller.send()
            after = _counter_total(_UNAVAILABLE_METRIC)

            assert response.status_code == 200, (
                f"a revocation endpoint at {origin!r} that cannot be dialed must fail open "
                f"(200 + WARNING + metric), not fail the request; got {response.status_code}"
                + (
                    " — a 500 is the R-H1 escape: SSRFValidationError is not in the translation site's exception tuple"
                    if response.status_code >= 500
                    else ""
                )
                + f": {response.text[:300]}"
            )
            assert after == before + 1, (
                f"the undialable endpoint must be metered like any other unavailable list; "
                f"{_UNAVAILABLE_METRIC} went {before} -> {after}"
            )
            assert _revocation_warned(warnings), (
                f"the fail-open must be announced at WARNING; the {_SIGNING_LOGGER!r} logger "
                f"recorded {warnings.records!r}"
            )


# --------------------------------------------------------------------------
# F3 — the stale list is a 401, not a 500
# --------------------------------------------------------------------------


@pytest.mark.requires_db
class TestStaleRevocationList:
    """A list past ``next_update + grace`` rejects unconditionally (:1333)."""

    def test_a_list_aged_past_grace_yields_the_stale_rejection(self, integration_db, counterparty_keypair):
        """The translation F3 found missing, graded end to end.

        The SDK raises ``RevocationListFreshnessError`` — a bare ``Exception``
        (``revocation_fetcher.py:122``) that ``verifier.py:294`` calls with no
        ``try`` and that ``request_verifier_middleware.py:341`` cannot catch. The
        SDK's own docstring (:600-602) claims the verifier maps it to
        ``request_signature_revocation_stale``; it does not, so A5 owns the
        translation and this is the test that proves it exists.

        Two requests, one checker: the first loads a fresh list and passes
        through (which is what makes the second non-vacuous — the 401 is caused
        by AGEING, not by the list being unavailable all along). Once a list HAS
        loaded, ageing past grace is NOT configurable: ``require_revocation_list``
        is left at its default and the rejection stands anyway.
        """
        with _caller(counterparty_keypair) as caller:
            ageing = _AgeingChecker(caller.private_key, origin=_STUBBED_ORIGIN, jwks=caller.jwks)
            _install_checker(_STUBBED_ORIGIN, ageing.checker)

            with _step_nine(origin=_STUBBED_ORIGIN, jwks=caller.jwks, require_revocation_list=False):
                fresh = caller.send()
                ageing.age_past_grace()
                stale = caller.send()

            assert fresh.status_code == 200, (
                "a fresh revocation list that does not name this keyid must let the request "
                f"through; got {fresh.status_code}: {fresh.text[:300]}"
            )
            assert stale.status_code != 500, (
                "RevocationListFreshnessError must be translated at the checker, not escape to "
                f"ServerErrorMiddleware; got a 500: {stale.text[:300]}"
            )
            assert _rejection_code(stale) == REQUEST_SIGNATURE_REVOCATION_STALE, (
                f"a list past next_update + grace must reject with "
                f"{REQUEST_SIGNATURE_REVOCATION_STALE!r} (security.mdx :1333); got status "
                f"{stale.status_code}, WWW-Authenticate={stale.headers.get('WWW-Authenticate')!r}"
            )


# --------------------------------------------------------------------------
# R-M1 — the checker must not snapshot the counterparty JWKS forever
# --------------------------------------------------------------------------


class TestResolverReadsThroughTheResolutionCache:
    """The list-signing JWKS is read per call, not frozen at construction.

    Both halves flow from :1328's model of ONE combined list per operator
    origin. With a construction-time ``StaticJwksResolver(resolution.jwks)``:
    an operator rotating its list-signing key breaks revocation for that origin
    until process restart, and two counterparties sharing an operator origin
    share whichever resolution arrived first — if the list JWS ``kid`` lives
    only in the other's JWKS the result is ``RevocationListSignatureError`` →
    grace → ``request_signature_revocation_stale`` 401 for BOTH.

    Graded on the resolver the SDK checker actually holds
    (``revocation_fetcher.py`` stores the constructor argument as
    ``_jwks_resolver``), because that is the object every list verification
    goes through; a resolver built correctly but never handed over would be a
    green test and a broken deployment.
    """

    _ORIGIN = "https://operator-rotating.example.com"

    def _build_resolver(self) -> Any:
        """Build this origin's checker through the real factory; return its resolver.

        The counterparty's resolution must already be seeded — the resolver is
        expected to read it per call, so every assertion on it belongs inside a
        :func:`_counterparty_at` block.
        """
        checker_for(mw.AGENT_RESOLUTION_CACHE[COUNTERPARTY_AGENT_URL], SigningConfig())
        return REVOCATION_CHECKER_CACHE[self._ORIGIN]._jwks_resolver

    def test_a_rotated_list_signing_key_heals_without_a_restart(self):
        """``AgentResolution`` re-resolves every ``agent_resolution_ttl_seconds``
        (default 3600); the checker must pick that up on its next call.
        """
        original = _jwks_for("operator-list-signing-1")
        rotated = _jwks_for("operator-list-signing-2")

        with _counterparty_at(self._ORIGIN, original):
            resolver = self._build_resolver()
            assert resolver("operator-list-signing-1") is not None, (
                "the checker's resolver must resolve the list-signing kid present at build time"
            )

        # The same checker — the registry is untouched — but the counterparty has
        # since re-resolved with new key material, exactly as the TTL makes it.
        with _counterparty_at(self._ORIGIN, rotated):
            assert resolver("operator-list-signing-2") is not None, (
                "the checker must read the JWKS through AGENT_RESOLUTION_CACHE on every call: "
                "after the counterparty re-resolved with a rotated list-signing key the "
                "checker still holds a construction-time snapshot, so revocation for this "
                "origin stays broken until the process restarts"
            )

    def test_two_counterparties_at_one_operator_origin_do_not_lock_each_other_out(self):
        """:1328 — one combined list per operator origin, many counterparties.

        The registry is keyed by that origin, so the checker built for the first
        counterparty to arrive is the one both use. If its resolver carries only
        that counterparty's JWKS, a list signed with the other's kid fails
        verification and both counterparties get a 401 nobody can explain.
        """
        first = _jwks_for("operator-list-signing-1")
        second = _jwks_for("operator-list-signing-9")

        with _counterparty_at(self._ORIGIN, first):
            resolver = self._build_resolver()
            neighbour = mw.AGENT_RESOLUTION_CACHE[COUNTERPARTY_AGENT_URL].model_copy(
                update={"agent_url": _OTHER_AGENT_URL, "jwks": second}
            )
            mw.AGENT_RESOLUTION_CACHE[_OTHER_AGENT_URL] = neighbour
            try:
                assert resolver("operator-list-signing-9") is not None, (
                    "a second counterparty resolved at the SAME operator origin must be visible "
                    "to that origin's checker; otherwise a list signed with its kid fails JWS "
                    "verification and both counterparties are rejected as revocation-stale"
                )
                assert resolver("operator-list-signing-1") is not None, (
                    "and the first counterparty must keep working — the read-through must union "
                    "the origin's resolutions, not replace one with the other"
                )
            finally:
                mw.AGENT_RESOLUTION_CACHE.pop(_OTHER_AGENT_URL, None)


# --------------------------------------------------------------------------
# R-M2 — one dead counterparty must not stall the others
# --------------------------------------------------------------------------


class TestNoCrossCounterpartyBlocking:
    """Locking is per checker; a blocked fetch holds nothing process-wide.

    ``_run_verifier`` runs under ``asyncio.to_thread``
    (``request_verifier_middleware.py:329``), which is exactly the thread-pool
    case ``revocation_fetcher.py:609-618`` says needs an external lock. The
    obvious answer — one module-level ``threading.Lock`` around every
    ``__call__`` — is held across the 10s-default HTTPS fetch inside
    ``_ensure_fresh``, so ONE dead counterparty serializes and stalls every
    signed request in the process.
    """

    def test_a_stalled_counterparty_does_not_block_a_different_one(self):
        entered = threading.Event()
        release = threading.Event()
        finished = threading.Event()

        def _stalled(_keyid: str) -> bool:
            entered.set()
            release.wait(10)
            return False

        stalled = CounterpartyRevocationChecker(
            static_revoked=frozenset(),
            fetched=_stalled,
            require_list=False,
        )
        healthy = CounterpartyRevocationChecker(
            static_revoked=frozenset(),
            fetched=lambda _keyid: False,
            require_list=False,
        )

        blocker = threading.Thread(target=stalled, args=("stalled-counterparty-kid",), daemon=True)
        blocker.start()
        try:
            assert entered.wait(10), "the stalled counterparty's checker never started"

            def _run_healthy() -> None:
                healthy("healthy-counterparty-kid")
                finished.set()

            threading.Thread(target=_run_healthy, daemon=True).start()

            assert finished.wait(5), (
                "a revocation check for one counterparty must complete while ANOTHER "
                "counterparty's check is blocked inside its fetch. It did not, which means the "
                "checkers share a process-wide lock held across the fetch — one dead "
                "counterparty stalls every signed request in the process (R-M2)"
            )
        finally:
            release.set()
            blocker.join(10)


# --------------------------------------------------------------------------
# R-L — the single construction site passes the spec's grace multiplier
# --------------------------------------------------------------------------


class TestCheckerForBuildsTheSpecCompliantChecker:
    """``checker_for`` is the one place ``grace_multiplier`` is passed (R7)."""

    def test_a_built_checker_uses_the_spec_grace_multiplier_of_four(self):
        """security.mdx :1333 — "grace = 4x the previous polling interval".

        ``DEFAULT_GRACE_MULTIPLIER`` is 2.0 (``revocation_fetcher.py:71``), so
        the 4x is ours to pass and ours to keep. Asserted on a
        ``checker_for``-BUILT entry precisely because every injected checker in
        this module carries a multiplier the TEST chose: a reverted or forgotten
        argument would silently halve every deployment's outage tolerance with
        the rest of this file still green.

        The arithmetic in :721 confirms 4x is exact rather than a rounding —
        ceiling 30 min → 30 + 4x30 = 150 min ("~2.5 h of tolerance"), floor
        1 min → 1 + 4x1 = 5 min ("~5 min").
        """
        origin = "https://operator-grace.example.com"
        with _counterparty_at(origin, _jwks_for("operator-list-signing-1")):
            checker_for(mw.AGENT_RESOLUTION_CACHE[COUNTERPARTY_AGENT_URL], SigningConfig())

        built = REVOCATION_CHECKER_CACHE[origin]
        assert built._grace_multiplier == 4.0, (
            "checker_for must pass grace_multiplier=4.0 (security.mdx :1333); the built "
            f"checker carries {built._grace_multiplier!r}. 2.0 is the SDK default "
            "(revocation_fetcher.py:71), i.e. the argument was dropped and outage "
            "tolerance is halved"
        )

    def test_the_registry_reuses_one_checker_per_issuer_origin(self):
        """R3 — the checker IS the cache; a fresh one per request re-fetches
        every time and hammers the counterparty's endpoint.
        """
        origin = "https://operator-reuse.example.com"
        jwks = _jwks_for("operator-list-signing-1")

        with _counterparty_at(origin, jwks):
            resolution = mw.AGENT_RESOLUTION_CACHE[COUNTERPARTY_AGENT_URL]
            checker_for(resolution, SigningConfig())
            first = REVOCATION_CHECKER_CACHE[origin]
            checker_for(resolution, SigningConfig())
            second = REVOCATION_CHECKER_CACHE[origin]

        assert first is second, (
            "the fetched checker must be built once per issuer origin and reused; a new "
            "instance per request discards the cached list and re-fetches on every signed "
            f"request (got {first!r} then {second!r})"
        )
