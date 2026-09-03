"""The retry-attempt policy shared by both ``send`` and ``asend``.

Owns the retry SCHEDULE (BR-RULE-029: 1s/2s/4s + jitter, Retry-After as a
floor-respecting lengthener only) and the 3-way per-attempt decision
(retry / success / terminal) as pure, steppable values — no I/O, no
sleeping, no ``httpx`` import. :class:`Attempts` is the state machine both
``send`` and ``asend`` drive identically; before this module existed, the
value-level decisions were already single-sourced functions but the LOOP
that called them in sequence was written twice (GH #1802).
"""

from __future__ import annotations

import logging
import os
import random
from collections.abc import Iterator
from enum import Enum, auto
from typing import ClassVar

from src.core.exceptions import AdCPServiceUnavailableError, clamp_retry_after
from src.core.security.egress.policy import OutboundError

logger = logging.getLogger(__name__)

# Retry backoff. BR-RULE-029 INV-3: a retried delivery waits 1s, 2s, 4s, each
# plus jitter, so a fleet of clients retrying the same failed endpoint does not
# thunder back in lockstep. That is production's schedule, and it is decided
# here rather than at any call site.
_BACKOFF_BASE_SECONDS = 1.0

# Test-speed override for the base only — the shape (x2 per attempt) and the
# jitter are not negotiable. Deliberately absent from tox.ini pass_env and from
# both compose files: no deployed or CI environment has any business shortening
# production backoff.
_BACKOFF_BASE_ENV = "ADCP_OUTBOUND_BACKOFF_BASE_SECONDS"

# The most of a counterparty's Retry-After this seam will actually wait. The
# header is a request, not an instruction: honouring an unbounded value lets any
# origin pin a worker for an hour with one response header. Retry-After can only
# ever LENGTHEN a wait beyond BR-RULE-029 — never shorten it — and only this far.
_MAX_HONOURED_RETRY_AFTER_SECONDS = 60.0

# Statuses worth trying again. Everything else — including every 4xx and every
# 3xx — is terminal: retrying a rejected or redirected request only doubles the
# damage.
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})

# Spec point 6: one fixed message for every DELIVERY-failure refusal (a
# destination reached but not delivered). The dial-time ADDRESS/SCHEME refusal
# message lives in egress/policy.py, beside the OutboundRequestBlocked raise
# sites that use it; this one is a different failure mode entirely (a
# destination that WAS reached).
_DELIVERY_FAILED_MESSAGE = "Outbound request to the supplied URL could not be delivered."


def env_float(name: str, default: float) -> float:
    """Read a positive float env knob, falling back loudly.

    Public because it is the ONE reader of this shape. A verbatim copy lived in
    ``src/services/webhook_delivery_service.py`` with the env name and default
    closed over as module constants; the two had already drifted apart by one
    word of warning text, which is what a second copy is for.

    Read at CALL time: tests flip these with ``monkeypatch.setenv`` and an
    import-time read would freeze the first value.

    The value must be STRICTLY positive. Zero is rejected rather than honoured
    because "no base delay, jitter only" is not a schedule this module offers,
    and silently substituting the production default for it would surprise in
    exactly the direction this seam exists to close. Every rejection is logged
    at WARNING naming the variable — an operator who cannot see which knob was
    ignored cannot fix it.
    """
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("%s=%r is not a number — using %ss", name, raw, default)
        return default
    if value <= 0:
        logger.warning("%s=%r is not strictly positive — using %ss", name, raw, default)
        return default
    return value


def _should_retry_status(status: int) -> bool:
    return status in _RETRYABLE_STATUSES


def _backoff_seconds(attempt: int) -> float:
    """Seconds to wait before the attempt after ``attempt`` (1-based).

    BR-RULE-029 INV-3, in the one place both ``send`` and ``asend`` reach: the
    base doubles per attempt (1s, 2s, 4s) and each wait carries its own
    ``uniform(0, 1)`` draw. Because the schedule is computed here and nowhere
    else, no call site can migrate onto this seam and quietly keep a different
    one. Deliberately private: the one sanctioned external consumer gets
    ``outbound_http.sleep_backoff``, which performs the wait itself so no call
    site ever holds a number it could scale or replace.

    Imports the ``random`` MODULE (not ``from random import uniform``) so that
    ``tests.integration.test_outbound_http.pin_jitter`` and the UC-004 circuit-
    breaker harness, which patch ``egress.attempts.random.uniform`` as a module
    attribute, pin the SAME object this function reads — a ``from``-import
    would bind a separate name the patch never touches.
    """
    base = env_float(_BACKOFF_BASE_ENV, _BACKOFF_BASE_SECONDS)
    return base * (2 ** (attempt - 1)) + random.uniform(0, 1)


def _wait_seconds(attempt: int, retry_after: float | None) -> float:
    """How long to wait before the attempt after ``attempt``.

    BR-RULE-029 is a FLOOR: this returns the geometric wait, raised to the
    origin's Retry-After when that asks for longer, and the ceiling clamps the
    RETRY-AFTER CONTRIBUTION only.

    The order matters and the obvious spelling is wrong. ``min(max(backoff,
    retry_after), CEILING)`` applies the ceiling to the whole wait, so any time
    the geometric wait exceeds the ceiling the seam would sleep LESS than the
    rule — silently, in the one module that owns that rule, and reachable through
    the public ``max_attempts`` parameter (the schedule is 1, 2, 4, 8, 16, 32,
    64s, so it crosses a 60s ceiling at seven attempts).
    """
    honoured = min(retry_after or 0.0, _MAX_HONOURED_RETRY_AFTER_SECONDS)
    return max(_backoff_seconds(attempt), honoured)


class OutboundDeliveryFailed(OutboundError, AdCPServiceUnavailableError):
    """The destination was reachable but the request was not delivered.

    ``attempts`` is how many times it was tried; ``http_status`` is the last
    HTTP status observed, or ``None`` when the failure was a transport
    exception and there was never a response to read a status from.
    """

    # Narrower than the base's ``int | None``: a delivery that reaches this
    # class was tried at least once (``__init__`` requires ``attempts: int``,
    # no default), so callers that have already caught this concrete type
    # read a plain ``int``, not ``int | None``. ``http_status`` stays inherited
    # — it is genuinely optional even here (a transport exception vs a
    # response).
    attempts: int

    # Only these two fields ride to the buyer. `details` is buyer-visible —
    # build_two_layer_error_envelope passes it straight into the adcp_error
    # payload — so nothing derived from the origin's response or from the httpx
    # error string may be added here (spec point 6).
    #
    # The KEY stays ``last_status`` while the ATTRIBUTE is ``http_status``. That
    # is not drift: the wire payload is a pre-existing buyer-facing contract, of
    # exactly the same character as the ``WebhookDeliveryLog.http_status_code``
    # column, and renaming it would be a behaviour change this step is not
    # allowed to make. So the seam carries ONE internal name and maps it to each
    # external name exactly once, at the boundary that owns that name — here for
    # the wire, and at the delivery repository for the column.
    _DETAIL_KEYS: ClassVar[tuple[str, str]] = ("attempts", "last_status")

    def __init__(self, *, attempts: int, http_status: int | None, retry_after: int | None = None) -> None:
        super().__init__(
            _DELIVERY_FAILED_MESSAGE,
            details={"attempts": attempts, "last_status": http_status},
            retry_after=retry_after,
        )
        self.attempts = attempts
        self.http_status = http_status
        self.retry_after = retry_after


def _fail(attempts: int, http_status: int | None, retry_after: float | None = None) -> OutboundDeliveryFailed:
    return OutboundDeliveryFailed(
        attempts=attempts,
        http_status=http_status,
        retry_after=clamp_retry_after(retry_after) if retry_after is not None else None,
    )


class Attempts:
    """Pure, steppable retry state machine both ``send()`` and ``asend()`` drive.

    No I/O, no sleeping, no ``httpx`` import — status codes and floats only.
    Before this class existed, the value-level decisions (retryable? how long
    to wait? build the failure object?) were already single-sourced functions,
    but the LOOP that called them in sequence — the ``for attempt in
    range(...)``, the try/except/else, the retry/success/terminal fork — was
    written twice, once per transport. Reifying the attempt sequence as a
    value is what makes "a retry loop beside the driver" unconstructible.
    """

    class Outcome(Enum):
        RETRY = auto()
        SUCCESS = auto()
        TERMINAL = auto()

    def __init__(self, max_attempts: int) -> None:
        self.max_attempts = max_attempts
        self.attempt = 0
        self.last_status: int | None = None
        self.last_retry_after: float | None = None

    def next_attempt(self) -> Iterator[int]:
        """Yields 1..max_attempts, updating ``self.attempt`` on each yield.

        The iteration protocol both loops drive their ``for attempt in
        attempts.next_attempt():`` from, replacing ``range(1, max_attempts +
        1)`` at both call sites. Call sites use ONLY the yielded value in the
        loop body — never also read ``self.attempt``/``attempts.attempt``
        there — one name for one value; the internal state exists for
        :meth:`wait_seconds` and :meth:`failure` to read.
        """
        for n in range(1, self.max_attempts + 1):
            self.attempt = n
            yield n

    def record_transport_failure(self) -> None:
        """Absorbs the ``except _RETRYABLE_EXCEPTIONS`` arm's bookkeeping.

        Resets ``last_status``/``last_retry_after`` to ``None`` — nothing to
        read off a transport exception. Logging stays at the call site: it
        names the exception and the attempt number in a format string, which
        is not this pure class's business.

        Takes no exception, deliberately. It used to accept one and never read
        it, which reads at the call site as "the exception is recorded" while
        recording nothing — a third thing, neither used nor absent. The caller
        that has the exception logs it.
        """
        self.last_status = None
        self.last_retry_after = None

    def record_response(self, status: int, retry_after: float | None) -> Attempts.Outcome:
        """The full 3-way decision from one status code.

        No ``httpx.Response`` needed — ``is_success`` is restated inline as
        ``200 <= status < 300`` (a stdlib-shaped fact, not an httpx-owned
        decision) rather than importing httpx into this pure module.
        """
        self.last_status = status
        self.last_retry_after = retry_after
        if _should_retry_status(status):
            return Attempts.Outcome.RETRY
        if 200 <= status < 300:
            return Attempts.Outcome.SUCCESS
        return Attempts.Outcome.TERMINAL

    def record_oversized_response(self, status: int) -> None:
        """Records a size-cap abort: ``last_status=status``, ``last_retry_after=None``.

        Deliberately does NOT route through :meth:`record_response` — an
        oversized 200 must never read as ``Outcome.SUCCESS``, and
        ``last_retry_after`` must NOT carry over from a prior attempt (a 429
        Retry-After on attempt 1 followed by an oversized 200 on attempt 2
        must report ``retry_after=None``, matching the honest fact that
        nothing about the CURRENT abort asked for a delay).
        """
        self.last_status = status
        self.last_retry_after = None

    def wait_seconds(self) -> float:
        """The wait before the next attempt, reading recorded state."""
        return _wait_seconds(self.attempt, self.last_retry_after)

    def failure(self) -> OutboundDeliveryFailed:
        """The failure to raise, built entirely from recorded state.

        Zero-arg at all four raise sites (both size-cap aborts, the terminal-
        status raise, and the post-loop exhaustion raise): ``self.attempt`` is
        always current (set by the generator on every yield, and unchanged
        after the final yield at exhaustion), and ``self.last_status``/
        ``self.last_retry_after`` are whatever :meth:`record_response` or
        :meth:`record_oversized_response` last recorded.
        """
        return _fail(self.attempt, self.last_status, self.last_retry_after)
