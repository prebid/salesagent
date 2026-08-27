"""Integration tests for the Postgres-backed RFC 9421 replay store (A4, #1291).

TDD RED. None of the production surface these tests import exists yet:

* ``adcp_replay`` table + ``src.core.database.models.ReplayNonce``
* ``src.core.database.repositories.replay_nonce.ReplayNonceRepository``
* ``src.core.signing.replay_store.PostgresReplayStore``
* ``src.core.config.SigningConfig``

The names above ARE the surface this test fixes; the implement atom
(salesagent-ayfn.15) may not rename them without updating these tests.

Spec grounding — AdCP 3.1.1 via ``adcp==6.6.0``:
``dist/compliance/3.1.1/test-kits/signed-requests-runner.yaml`` (stateful contract:
``min_replay_ttl_seconds: 10``, ``max_interval_seconds: 5``, ``window_seconds: 60``,
``grading_target_per_keyid_cap_requests: 100``,
``production_min_per_keyid_cap_requests: 1000000``), graded at the wire by vectors
``016-replayed-nonce`` (``request_signature_replayed``) and ``020-rate-abuse``
(``request_signature_rate_abuse``).

**Level: integration, deliberately.** A4 is infrastructure BELOW the wire. The wire
grading of 016/020 belongs to B3 (salesagent-z6nr.14) and the ``signed-requests``
storyboard — these tests must not duplicate it. This project writes no unit tests.

Test-data note: every test mints a unique ``keyid`` (``a4-<uuid>``), so tests never
collide and no cleanup is required — rows self-expire and every read the store does
filters ``expires_at > now()``. No factory exists (or is needed) for ``adcp_replay``:
the production store is what writes the rows, which is the point.

That is also why these tests take a plain ``Session`` fixture instead of an
``IntegrationEnv`` subclass (the ``_RepoEnv`` shape in
``tests/integration/test_account_repository.py``). The env's job is binding factories
to a session and patching external services; this module seeds no factory data and
patches nothing, so all it would contribute is a global factory-session binding and
its no-nesting assertion — both pure hazard in a test that runs eight threads. The
session is built exactly the way the env builds its own (``tests/harness/_base.py``
binds ``Session(bind=get_engine())``); a test body may not call ``get_db_session()``
(``test_architecture_repository_pattern.py`` invariant 3) and this module does not.

beads: salesagent-z6nr.10 (design + refinement R1-R7), atom salesagent-ayfn.14
"""

from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
import sqlalchemy as sa
from adcp.signing.crypto import ALG_ED25519, load_private_key_pem
from adcp.signing.errors import SignatureVerificationError
from adcp.signing.jwks import StaticJwksResolver
from adcp.signing.keygen import generate_ed25519
from adcp.signing.signer import sign_request
from adcp.signing.verifier import VerifierCapability, VerifyOptions, verify_request_signature
from sqlalchemy.orm import Session as SASession

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

SIGNED_URL = "https://sales.example.com/mcp/"
SIGNED_BODY = b'{"adcp_version":"3.1.1","brief":"video"}'


@pytest.fixture
def keyid() -> str:
    """A keyid unique to this test, so no two tests share replay state."""
    return f"a4-{uuid.uuid4().hex[:16]}"


@pytest.fixture
def replay_session(integration_db):
    """A session on the shared engine — the OBSERVER in these tests.

    Production writes the rows; the test reads them back on this session.
    """
    from src.core.database.database_session import get_engine

    with SASession(bind=get_engine()) as session:
        yield session


def _signing_config(**overrides):
    """Build a ``SigningConfig`` with A4's replay knobs explicit.

    Defaults are the spec floors: the 1,000,000-entry per-keyid cap from
    ``security.mdx`` §per-keyid cap, no per-keyid overrides.
    """
    from src.core.config import SigningConfig

    settings = {
        "per_keyid_cap": 1_000_000,
        "per_keyid_cap_overrides": {},
        "replay_ttl_overrides": {},
    }
    settings.update(overrides)
    return SigningConfig(**settings)


def _build_store(session: SASession, config):
    """Construct the production store over a caller-owned session.

    Matches the wiring B1 (salesagent-z6nr.12) will do inside its
    ``asyncio.to_thread`` hop: one short-lived session, one store, three calls.
    """
    from src.core.database.repositories.replay_nonce import ReplayNonceRepository
    from src.core.signing.replay_store import PostgresReplayStore

    return PostgresReplayStore(ReplayNonceRepository(session), config)


def _live_row_count(session: SASession, keyid: str, nonce: str | None = None) -> int:
    """Count rows in ``adcp_replay`` for a keyid (optionally one nonce)."""
    from src.core.database.models import ReplayNonce

    stmt = sa.select(sa.func.count()).select_from(ReplayNonce).where(ReplayNonce.keyid == keyid)
    if nonce is not None:
        stmt = stmt.where(ReplayNonce.nonce == nonce)
    # READ COMMITTED: each statement re-snapshots, so a sibling session's
    # committed claim is visible here without ending this session's transaction.
    return session.execute(stmt).scalar_one()


def _seconds_until_expiry(session: SASession, keyid: str, nonce: str) -> float:
    """Row lifetime remaining, measured against the SERVER clock.

    ``now()`` on the database is the only shared time authority across workers, so
    the assertion boundary must be computed there, never in Python.
    """
    from src.core.database.models import ReplayNonce

    return session.execute(
        sa.select(sa.extract("epoch", ReplayNonce.expires_at - sa.func.now())).where(
            ReplayNonce.keyid == keyid, ReplayNonce.nonce == nonce
        )
    ).scalar_one()


def _sign(keyid: str, *, nonce: str | None = None, signing_jwk_pair=None):
    """Produce a validly signed request plus the JWK a verifier resolves for it.

    Returns ``(headers, jwk)``. Pass ``signing_jwk_pair`` to sign with one keypair
    while publishing another's public JWK under the same kid — the cheapest way to
    fail crypto verify at step 10 without touching the body.
    """
    pem, jwk = signing_jwk_pair or generate_ed25519(keyid)
    headers = sign_request(
        method="POST",
        url=SIGNED_URL,
        headers={},
        body=SIGNED_BODY,
        private_key=load_private_key_pem(pem),
        key_id=keyid,
        alg=ALG_ED25519,
        cover_content_digest=True,
        nonce=nonce,
    ).as_dict()
    return headers, jwk


def _verify(headers: dict[str, str], jwk: dict, store) -> None:
    """Run the SDK verifier checklist with OUR replay store wired in."""
    verify_request_signature(
        method="POST",
        url=SIGNED_URL,
        headers=headers,
        body=SIGNED_BODY,
        options=VerifyOptions(
            now=time.time(),
            capability=VerifierCapability(),
            operation="get_products",
            jwks_resolver=StaticJwksResolver({"keys": [jwk]}),
            replay_store=store,
        ),
    )


class TestClaimAtomicity:
    """The accept decision and the record of it are ONE atomic statement."""

    def test_exactly_one_concurrent_worker_claims_a_nonce(self, replay_session, keyid):
        """8 workers on 8 connections race for one (keyid, nonce); exactly one wins.

        This is the whole point of A4. The SDK's own ``PgReplayStore`` does a bare
        ``SELECT`` in ``seen()`` and a separate ``INSERT ... ON CONFLICT`` in
        ``remember()`` (``adcp/signing/pg/replay_store.py:215,221``) — a read-then-write
        race in which two workers both observe "not seen" and both accept the same
        nonce. Its docstring's "Postgres provides the cross-instance locking" claim is
        false for the PAIR: conflict resolution protects the write, not the check.

        Each thread opens its OWN session, which really is its own connection:
        ``scoped_session`` at ``src/core/database/database_session.py:151`` takes no
        ``scopefunc``, so registries are thread-local (exemplar:
        ``tests/integration/test_get_products_database_integration.py:209-232``, which
        uses ``get_db_session()``; a test body may not, per
        ``test_architecture_repository_pattern.py`` invariant 3, so the threads bind
        sessions to the same engine directly, as the harness itself does).

        Both invariants are asserted, because only the pair is ordering-independent:
        exactly one worker saw ``False`` AND the table holds exactly one row.

        MUTATION THRESHOLD (refinement R6 — required before this test may be believed):
        replace the atomic claim with SELECT-then-INSERT and re-run. The losing window
        is a few hundred microseconds, so a single red run proves nothing — the mutant
        must fail in **>= 15 of the 20 iterations** below (or the mutant must be widened
        with a deliberate sleep between its SELECT and its INSERT). Paste that run's
        output into the PR body; nothing in the repo keeps the claim honest otherwise.
        """
        from src.core.database.database_session import get_engine

        workers = 8
        iterations = 20
        engine = get_engine()
        config = _signing_config()

        for i in range(iterations):
            nonce = f"race-{i}"
            barrier = threading.Barrier(workers, timeout=30)

            def claim_once(_nonce: str = nonce, _barrier: threading.Barrier = barrier) -> bool:
                with SASession(bind=engine) as session:
                    store = _build_store(session, config)
                    _barrier.wait()
                    return store.seen(keyid, _nonce)

            with ThreadPoolExecutor(max_workers=workers) as pool:
                results = [f.result() for f in [pool.submit(claim_once) for _ in range(workers)]]

            assert results.count(False) == 1, (
                f"iteration {i}: {results.count(False)} of {workers} workers claimed "
                f"(keyid={keyid}, nonce={nonce}) — a nonce is accepted at most ONCE "
                f"deployment-wide; results={results}"
            )
            rows = _live_row_count(replay_session, keyid, nonce)
            assert rows == 1, (
                f"iteration {i}: adcp_replay holds {rows} rows for (keyid={keyid}, nonce={nonce}), expected exactly 1"
            )


class TestCrossConnectionRejection:
    """A nonce accepted on one worker is a replay on every other worker."""

    def test_claim_on_one_session_is_seen_on_another(self, replay_session, keyid):
        """Session A claims; session B on a distinct connection sees it as replayed.

        This is the ticket's literal acceptance line and the reason
        ``InMemoryReplayStore`` is wrong for salesagent: it is process-local, so with
        multiple workers a nonce accepted on worker A stays replayable on worker B for
        the whole signature window. It also pins that the claim COMMITS before the
        response is sent — an uncommitted claim is invisible to session B.
        """
        from src.core.database.database_session import get_engine

        config = _signing_config()
        nonce = "cross-connection"

        assert _build_store(replay_session, config).seen(keyid, nonce) is False, (
            "first sighting of a nonce must be accepted"
        )

        with SASession(bind=get_engine()) as session_b:
            assert _build_store(session_b, config).seen(keyid, nonce) is True, (
                "a nonce claimed on session A must be rejected as a replay on "
                "session B — the store is shared state, not process-local"
            )


class TestTtlExpiry:
    """The store is a replay CACHE, not a permanent nonce ledger."""

    def test_nonce_is_claimable_again_after_its_ttl(self, replay_session, keyid):
        """Claim, let the TTL lapse, claim again — the second claim is accepted.

        A permanent nonce ledger would grow without bound and would reject a
        legitimate nonce reuse long after any signature carrying it could still
        verify. Expiry is measured by the server's ``now()``, which with multiple
        workers is the only shared time authority — so this test pays for a real
        (short) sleep rather than freezing a Python clock.
        """
        config = _signing_config(replay_claim_ttl_seconds=1.0)
        nonce = "expiring"
        store = _build_store(replay_session, config)

        assert store.seen(keyid, nonce) is False, "first claim must be accepted"
        assert store.seen(keyid, nonce) is True, "a live claim must be reported as a replay while it is live"

        time.sleep(1.3)

        assert store.seen(keyid, nonce) is False, (
            "after the TTL lapses the entry is dead and the nonce is claimable "
            "again — the store must not be a permanent ledger"
        )


class TestPerKeyidCap:
    """``at_capacity`` backs ``request_signature_rate_abuse`` at verifier step 9a."""

    def test_cap_override_drives_verifier_to_rate_abuse(self, replay_session, keyid):
        """At the per-keyid cap, a valid signature is rejected with rate_abuse/9a.

        The cap is reached at ``count >= cap`` (``InMemoryReplayStore.at_capacity``,
        ``adcp/signing/replay.py:67``), so the one-below-cap assertion is load-bearing:
        it pins the boundary that the OFFSET/LIMIT short-circuit form (refinement R3)
        is easy to get off by one on.

        The cap is lowered per-keyid ONLY. The test-kit's
        ``grading_target_per_keyid_cap_requests: 100`` applies to the test-kit
        counterparty; ``production_min_per_keyid_cap_requests: 1000000`` stays the
        global floor (see the config test below).
        """
        config = _signing_config(per_keyid_cap_overrides={keyid: 2})
        store = _build_store(replay_session, config)

        assert store.at_capacity(keyid) is False, "no live rows: not at capacity"
        store.seen(keyid, "cap-1")
        assert store.at_capacity(keyid) is False, (
            "1 live row against a cap of 2 is BELOW capacity — the cap is reached at count >= cap, not count > cap"
        )
        store.seen(keyid, "cap-2")
        assert store.at_capacity(keyid) is True, "2 live rows against a cap of 2 is AT capacity"

        headers, jwk = _sign(keyid)
        with pytest.raises(SignatureVerificationError) as exc_info:
            _verify(headers, jwk, store)

        assert exc_info.value.code == "request_signature_rate_abuse"
        assert exc_info.value.step == "9a"

    def test_uncapped_keyid_is_unaffected_by_another_keyids_cap(self, replay_session, keyid):
        """The cap is per keyid: one signer at its cap must not reject another.

        Guards against a store that counts rows without the keyid predicate — which
        would pass the test above while turning any busy signer into a global outage.
        """
        other_keyid = f"a4-{uuid.uuid4().hex[:16]}"
        config = _signing_config(per_keyid_cap_overrides={keyid: 1})
        store = _build_store(replay_session, config)

        store.seen(keyid, "cap-1")
        assert store.at_capacity(keyid) is True
        assert store.at_capacity(other_keyid) is False, (
            "a different keyid's rows must not count toward this keyid's cap"
        )


class TestClaimHappensAfterCryptoVerify:
    """Pin (research F4): the claim only ever fires for a verified request."""

    def test_a_signature_that_fails_crypto_verify_writes_no_row(self, replay_session, keyid):
        """A step-10 failure leaves ZERO rows in adcp_replay; a valid one leaves exactly one.

        Our design makes ``seen()`` a WRITING claim. That is safe only because at
        ``adcp==6.6.0`` the verifier calls ``at_capacity`` at ``verifier.py:304``
        (step 9a), ``verify_signature`` at ``:329``, the Content-Digest check at
        ``:339``, and only then ``seen`` at ``:348`` and ``remember`` at ``:358``.
        So an attacker without a valid signature cannot burn nonces or inflate the
        per-keyid count.

        That ordering is a property of the pinned SDK, NOT of the ``ReplayStore``
        Protocol. If an SDK bump moves ``seen()`` before crypto verify, the store
        silently becomes a DoS amplifier — this test is the tripwire that makes that
        bump fail loudly instead. The positive control in the same test is what keeps
        the "zero rows" assertion from passing vacuously when the store is never called
        at all.
        """
        config = _signing_config()
        store = _build_store(replay_session, config)

        good_headers, good_jwk = _sign(keyid, nonce="accepted")
        _verify(good_headers, good_jwk, store)
        assert _live_row_count(replay_session, keyid) == 1, (
            "positive control: an accepted request must leave exactly one row"
        )

        # Sign with one keypair, publish another's public JWK under the same kid:
        # everything up to step 10 succeeds, the signature does not verify.
        impostor_pem, _ = generate_ed25519(keyid)
        bad_headers, _ = _sign(keyid, nonce="rejected", signing_jwk_pair=(impostor_pem, None))

        with pytest.raises(SignatureVerificationError) as exc_info:
            _verify(bad_headers, good_jwk, store)

        assert exc_info.value.code == "request_signature_invalid"
        assert exc_info.value.step == 10
        assert _live_row_count(replay_session, keyid, "rejected") == 0, (
            "a request that failed crypto verify must leave NO trace in "
            "adcp_replay — seen() must run after verify_signature"
        )
        assert _live_row_count(replay_session, keyid) == 1, (
            "the rejected request must not have inflated the per-keyid count "
            "either, or step 9a becomes an unauthenticated DoS lever"
        )


class TestCapacityHappensBeforeCryptoVerify:
    """The other half of the pair (#1291 B1, refinement R-L).

    ``TestClaimHappensAfterCryptoVerify`` pins the LATE end of the SDK's call order.
    This pins the EARLY end: ``at_capacity`` runs at ``verifier.py:304`` (step 9a),
    BEFORE ``verify_signature`` at ``:329``, precisely so a signer at its cap is
    rejected without paying for an Ed25519 verify. If an SDK bump moved step 9a after
    step 10, a keyid at capacity would become an amplifier — every abusive request
    would buy a full crypto verify before being told no — and nothing else in the suite
    would notice, because the rejection code on the happy path is unchanged.
    """

    def test_a_keyid_at_capacity_is_rejected_without_crypto_verify(self, replay_session, keyid):
        """A request that is BOTH over cap and cryptographically invalid answers 9a.

        The two failures are ordered: ``request_signature_rate_abuse`` (step 9a) can
        only be the answer if capacity was checked first. If the checks swapped, the
        same request would raise ``request_signature_invalid`` (step 10) and this test
        fails — which is the tripwire. The contrast test below keeps the assertion from
        passing vacuously.

        The cap is reached by REAL accepted traffic rather than by a zero override
        (which ``SigningConfig`` rejects, correctly — a zero cap would reject every
        request from a counterparty forever).
        """
        honest_pem, honest_jwk = generate_ed25519(keyid)
        filler_headers = _sign(keyid, nonce="fills-the-cap", signing_jwk_pair=(honest_pem, honest_jwk))[0]
        _verify(filler_headers, honest_jwk, _build_store(replay_session, _signing_config()))
        assert _live_row_count(replay_session, keyid) == 1, "setup: the accepted request must occupy the cap"

        store = _build_store(replay_session, _signing_config(per_keyid_cap_overrides={keyid: 1}))
        impostor_pem, _ = generate_ed25519(keyid)
        headers, _ = _sign(keyid, nonce="at-capacity", signing_jwk_pair=(impostor_pem, None))

        with pytest.raises(SignatureVerificationError) as exc_info:
            _verify(headers, honest_jwk, store)

        assert exc_info.value.code == "request_signature_rate_abuse", (
            "a keyid at capacity must be rejected at step 9a, before the crypto verify "
            f"this signature would also have failed; got {exc_info.value.code!r} at step "
            f"{exc_info.value.step!r}"
        )
        assert _live_row_count(replay_session, keyid, "at-capacity") == 0, (
            "a request rejected at step 9a must not have claimed a nonce either"
        )

    def test_the_same_request_under_a_normal_cap_reaches_the_crypto_verify(self, replay_session, keyid):
        """The contrast: without the cap override the SAME request fails at step 10.

        This is what makes the ordering assertion above non-vacuous — the request is
        genuinely capable of reaching (and failing) the crypto verify, so answering 9a
        can only mean 9a ran first.
        """
        store = _build_store(replay_session, _signing_config())

        impostor_pem, _ = generate_ed25519(keyid)
        _, honest_jwk = generate_ed25519(keyid)
        headers, _ = _sign(keyid, nonce="under-capacity", signing_jwk_pair=(impostor_pem, None))

        with pytest.raises(SignatureVerificationError) as exc_info:
            _verify(headers, honest_jwk, store)

        assert exc_info.value.code == "request_signature_invalid", (
            f"expected the step-10 failure, got {exc_info.value.code!r} at step {exc_info.value.step!r}"
        )


class TestPerKeyidTtlOverride:
    """Refinement R1: the row lifetime comes from ``remember()``, and it is clampable
    per counterparty only."""

    def test_override_clamps_a_long_signature_window(self, replay_session, keyid):
        """An overridden keyid's row does not outlive its override; an unoverridden one does.

        ``remember()`` is handed ``ttl = max(expires - now + max_skew, 0)`` computed
        from the SIGNATURE (``verifier.py:354``), and its only-extend predicate raises
        any shorter claim to that value. Both runner vectors carry a 300s window, so
        without a clamp every accepted request for ``test-ed25519-2026`` leaves a row
        live ~360s — 6x the test-kit's ``window_seconds: 60``. Vector 020 drives that
        keyid to its cap of 100, and those rows then trip step 9a for every LATER
        vector, including 016's first (must-be-accepted) submission. The suite would
        only pass if the grader happened to run 020 last, which
        ``signed-requests-runner.yaml`` does not specify.

        70s is the value that satisfies every side at once: ``>= min_replay_ttl_seconds``
        (10) and ``> max_interval_seconds`` (5) so 016 still grades and cannot
        false-green, ``>= window_seconds`` (60) so 020 still reaches the cap, and short
        enough that the cap drains between vectors.
        """
        unclamped_keyid = f"a4-{uuid.uuid4().hex[:16]}"
        config = _signing_config(
            replay_claim_ttl_seconds=360.0,
            replay_ttl_overrides={keyid: 70.0},
        )
        nonce = "clamped"

        store = _build_store(replay_session, config)

        assert store.seen(keyid, nonce) is False
        store.remember(keyid, nonce, 300.0)
        clamped = _seconds_until_expiry(replay_session, keyid, nonce)
        assert 60.0 <= clamped <= 70.0, (
            f"an overridden keyid's row lives {clamped:.1f}s; the override must "
            "clamp BOTH the claim TTL and remember()'s incoming ttl to 70s, and "
            "must still cover the test-kit's 60s window"
        )

        assert store.seen(unclamped_keyid, nonce) is False
        store.remember(unclamped_keyid, nonce, 300.0)
        unclamped = _seconds_until_expiry(replay_session, unclamped_keyid, nonce)
        assert unclamped > 70.0, (
            f"an unoverridden keyid's row lives {unclamped:.1f}s; clamping must "
            "never apply globally — it shortens replay protection below the "
            "signature's own validity window"
        )

    def test_config_refuses_a_global_cap_or_a_global_clamp(self):
        """The config makes "lowered for the test counterparty only" mechanical.

        Both lowered values — the cap and the TTL clamp — are permitted by the
        test-kit for the test-kit COUNTERPARTY only. The ``field_validator`` refuses
        the global form of each at load, so a misconfiguration fails at startup rather
        than at the first signature: the cap below the spec's
        ``production_min_per_keyid_cap_requests: 1000000`` floor, and any override
        entry that would apply to every keyid (a wildcard/prefix key — overrides name
        explicit keyids, never patterns).

        This test needs no database; it lives here because it is the other half of the
        clamp mechanism asserted above, and splitting it would hide that.
        """
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            _signing_config(per_keyid_cap=100)

        with pytest.raises(ValidationError):
            _signing_config(per_keyid_cap_overrides={"*": 100})

        with pytest.raises(ValidationError):
            _signing_config(replay_ttl_overrides={"*": 70.0})

        explicit = _signing_config(
            per_keyid_cap_overrides={"test-ed25519-2026": 100, "test-es256-2026": 100},
            replay_ttl_overrides={"test-ed25519-2026": 70.0, "test-es256-2026": 70.0},
        )
        assert explicit.per_keyid_cap_overrides["test-ed25519-2026"] == 100
        assert explicit.replay_ttl_overrides["test-es256-2026"] == 70.0
