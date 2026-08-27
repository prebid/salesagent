"""Postgres-backed :class:`adcp.signing.replay.ReplayStore` (#1291 A4, salesagent-z6nr.10).

A nonce is accepted at most once DEPLOYMENT-WIDE within its signature window. The
SDK's ``InMemoryReplayStore`` cannot promise that — it is process-local, so with
several workers a nonce accepted on worker A stays replayable on worker B for the
whole window; its own docstring says as much. This store puts the state in Postgres,
where every worker shares it.

Backend choice (recorded per the ticket): implemented over SQLAlchemy rather than
adopting ``adcp[pg]``'s ``PgReplayStore``, which hard-requires psycopg3 and opens its
own ``ConnectionPool`` beside SQLAlchemy's. One driver, one pool — and the SDK store
would have had to be fixed anyway (see ``ReplayNonceRepository`` on the seen/remember
race).

**``seen()`` is a WRITING atomic claim, and that is only safe because of the pinned
SDK's call order.** At ``adcp==6.6.0`` the verifier calls ``at_capacity`` at
``verifier.py:304`` (step 9a), ``verify_signature`` at ``:329``, the Content-Digest
check at ``:339``, and only then ``seen`` at ``:348`` and ``remember`` at ``:358``.
So a claim only ever fires for a cryptographically valid, digest-matching request:
an attacker cannot burn nonces or inflate the per-keyid count without holding a valid
signature. That is a property of the SDK VERSION, not of the ``ReplayStore`` Protocol
(``adcp/signing/replay.py:21-28`` is structural and does not forbid ``seen`` from
writing). ``tests/integration/test_replay_store.py::TestClaimHappensAfterCryptoVerify``
is the tripwire that makes an SDK bump moving ``seen()`` earlier fail loudly instead
of silently turning this store into a DoS amplifier.

**Row lifetime belongs to ``remember()``, not to the claim.** The verifier hands
``remember`` a TTL computed from the SIGNATURE (``ttl = expires - now + max_skew``,
``verifier.py:354``) and the only-extend predicate raises any shorter claim to it. The
claim TTL only has to bridge ``seen()`` to ``remember()`` plus a crash in between.

Ordering hazard this creates for conformance grading (designed for, not accidental —
B3/salesagent-z6nr.14 inherits it): the signed-requests test-kit's vectors carry a
300s window, so without a clamp every accepted request for ``test-ed25519-2026`` would
leave a row live ~360s — 6x the kit's ``window_seconds: 60``. Vector ``020-rate-abuse``
drives that keyid to its overridden cap of 100, and those rows would then trip step 9a
for every LATER vector, including ``016-replayed-nonce``'s first (must-be-accepted)
submission. ``replay_ttl_overrides`` clamps the runner counterparties to ~70s, which is
above ``min_replay_ttl_seconds: 10`` and ``max_interval_seconds: 5`` (so 016 still
grades and cannot false-green) and above ``window_seconds: 60`` (so 020 still reaches
the cap), while draining between vectors. The clamp is sound ONLY per-counterparty —
it shortens replay protection below the signature's own validity window — which is why
:class:`~src.core.config.SigningConfig` refuses a global one.

Spec grounding: AdCP 3.1.1 via ``adcp==6.6.0``;
``dist/compliance/3.1.1/test-kits/signed-requests-runner.yaml``; graded at the wire by
vectors ``016-replayed-nonce`` (``request_signature_replayed``) and ``020-rate-abuse``
(``request_signature_rate_abuse``).
"""

from __future__ import annotations

import logging
import random

from src.core.config import SigningConfig
from src.core.database.repositories.replay_nonce import ReplayNonceRepository

logger = logging.getLogger(__name__)

# Fraction of claims that also pay for storage reclamation, and the row budget when
# they do. The product is what matters: the sweep must reclaim MORE than the one row
# a signed request inserts, or the table grows monotonically. 0.001 * 20_000 = 20 rows
# reclaimed per signed request on average — comfortably above 1, with the batch still
# small enough that the DELETE's locks stay brief. A periodic `pg_cron` sweep
# (`DELETE FROM adcp_replay WHERE expires_at <= now()`, the SDK's SQL header) remains
# the preferred production setup; this is the always-on floor beneath it.
_REAP_PROBABILITY = 0.001
_REAP_BATCH = 20_000


class PostgresReplayStore:
    """``ReplayStore`` over the shared ``adcp_replay`` table.

    Args:
        repository: Statement layer, holding a short-lived caller-owned session.
        config: Agent-level replay posture (cap, per-counterparty overrides, claim TTL).

    Wiring (B1, salesagent-z6nr.12): construct this INSIDE one
    ``asyncio.to_thread`` hop around the whole synchronous
    ``verify_request_signature``, over one ``with get_db_session() as session:``. The
    Protocol's three methods are ``def``, not ``async def``, and the verifier calls
    them inline — one hop means the three statements share one connection checkout
    instead of three, which matters under the PgBouncer branch's ``pool_size=2``.
    """

    def __init__(self, repository: ReplayNonceRepository, config: SigningConfig) -> None:
        self._repository = repository
        self._config = config

    def seen(self, keyid: str, nonce: str) -> bool:
        """True iff ``(keyid, nonce)`` is already live — i.e. this is a replay.

        Claims the nonce as a side effect, atomically; see
        :meth:`ReplayNonceRepository.claim` and the module docstring for why that is
        both necessary and safe at the pinned SDK version.
        """
        replayed = self._repository.claim(keyid, nonce, self._claim_ttl(keyid))
        self._maybe_reap_expired()
        return replayed

    def remember(self, keyid: str, nonce: str, ttl_seconds: float) -> None:
        """Extend the claim to the signature's own TTL (clamped per counterparty)."""
        self._repository.extend(keyid, nonce, self._clamped_ttl(keyid, ttl_seconds))

    def at_capacity(self, keyid: str) -> bool:
        """True iff ``keyid`` holds at least its cap in live entries (verifier step 9a)."""
        cap = self._config.per_keyid_cap_overrides.get(keyid, self._config.per_keyid_cap)
        return self._repository.at_or_above_cap(keyid, cap)

    def _claim_ttl(self, keyid: str) -> float:
        """Lifetime the claim writes, before ``remember()`` raises it to the signature's.

        The trade-off the default encodes: a LONG claim TTL covers a crash between
        the claim (``verifier.py:348``) and ``remember`` (``:358``), during which the
        nonce would otherwise become claimable again; a SHORT one keeps the step-9a
        count honest, because a floor above a counterparty's actual signature window
        holds rows longer than its traffic warrants and walks a legitimate
        high-volume signer toward a spurious ``request_signature_rate_abuse``. The
        window it must bridge is two statements wide, so the default sits near the
        RFC profile's max skew rather than at window+skew — well above the test-kit's
        ``min_replay_ttl_seconds: 10`` floor, well below a 300s window's row cost.
        """
        return self._clamped_ttl(keyid, self._config.replay_claim_ttl_seconds)

    def _clamped_ttl(self, keyid: str, ttl_seconds: float) -> float:
        """Apply this counterparty's TTL clamp, if it has one.

        Both the claim and ``remember()`` clamp, because clamping only one of them
        leaves the other free to set the row's real lifetime.
        """
        override = self._config.replay_ttl_overrides.get(keyid)
        return ttl_seconds if override is None else min(ttl_seconds, override)

    def _maybe_reap_expired(self) -> None:
        """Probabilistically reclaim expired rows in a SEPARATE session and transaction.

        Never in the claim's transaction: a deployment-wide DELETE takes locks on the
        same ``expires_at`` index concurrent claims are inserting into, and inheriting
        the claim's transaction would let that housekeeping roll back — or hold the
        pooled connection for — a signature check that has already succeeded. Same
        reason ``_maybe_evict_expired`` (``src/core/tools/media_buy_create.py:1806``)
        runs in its own transaction. Best-effort: correctness never depends on it,
        because every read filters ``expires_at > now()``.
        """
        if random.random() >= _REAP_PROBABILITY:
            return
        from src.core.database.database_session import get_db_session

        try:
            with get_db_session() as session:
                ReplayNonceRepository(session).reap(_REAP_BATCH)
        except Exception:
            logger.warning("Best-effort replay-cache sweep failed", exc_info=True)
