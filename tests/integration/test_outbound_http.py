"""Integration tests for the outbound egress seam (`src.core.security.outbound_http`).

Every case drives the real seam against a real local origin. Nothing is mocked:
no patched ``getaddrinfo``, no substituted transport, no faked ``httpx``. The
refusals under test are genuinely produced by the live ``adcp.signing``
validator against real addresses — ``127.0.0.1`` really is in a reserved range
and ``169.254.169.254`` really is a cloud-metadata address — so a mock could
only restate the assertion, never grade it. The same goes for the two facts
that matter most here: "the redirect was not followed" and "the 404 was not
retried" are only provable by counting hits on an origin that actually ran.

Spec grounding: AdCP 3.1.1, ``building/by-layer/L1/security.mdx``, "Webhook URL
validation (SSRF)". A fetcher MUST (1) reject non-HTTPS in production, (2)
reject reserved ranges, (3) pin the connection to the validated IP, (4) refuse
redirects, (5) cap size and timeouts, (6) not echo fetch errors back to the
party that supplied the URL. Conformance storyboard: ungraded — this is an L1
security obligation, not a wire contract.

Every case runs for BOTH ``send`` and ``asend``: the async twin is a separate
code path and an untested one would ship the epic's defect in half the repo.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
from typing import Any
from urllib.parse import urlparse

import pytest
from adcp.signing.canonical import build_signature_base, parse_signature_input_header
from adcp.signing.crypto import (
    alg_for_jwk,
    extract_signature_bytes,
    load_private_key_pem,
    public_key_from_jwk,
    verify_signature,
)
from adcp.signing.digest import content_digest_matches
from adcp.signing.keygen import generate_signing_keypair
from adcp.webhook_auth import JwkSignerStrategy

from src.core.exceptions import AdCPBlockedUrlError
from src.core.security.outbound_http import CounterpartyUrl
from tests.helpers import assert_backoff_schedule, assert_envelope_shape
from tests.helpers.egress_backoff import (
    _RATE_LIMITED_BODY,
    fast_backoff,
    pin_jitter,
    rate_limited,
    set_backoff_base,
    set_flags,
)
from tests.helpers.envelope_assertions import envelope_for
from tests.helpers.local_http_origin import hangs_up, responds, sends_chunked_body

# Both entry points get every case. Parametrising instead of duplicating the
# module keeps the two paths literally the same test.
SEAM_CALLS = ["send", "asend"]

# A cloud-metadata address: refused unconditionally, escape hatches or not.
METADATA_URL = "https://169.254.169.254/"

# A JSONPath-lite path a CALLER supplies for a refused URL that came out of its
# own request payload. The seam only carries it — the value belongs to the call
# site's namespace (this one is the property-list resolver's), which is why the
# seam cannot derive it and why this file grades the carrying, not the path.
_CALLER_FIELD_PATH = "property_list.agent_url"

# The settings FIELD the seam reads for its retry backoff base
# (``src/core/security/outbound_http.py`` -> ``egress.attempts._backoff_seconds``).
#
# It is a field name, not the ``ADCP_OUTBOUND_BACKOFF_BASE_SECONDS`` environment variable
# this file used to write. The comment that stood here said the tests "drive the seam's env
# surface from outside, not through its privates" — but the seam HAS no env surface: no
# production code reads the environment (``ruff-environment.toml``), only the settings

# The spec bound on the value CARRIED to the buyer: ``core/error.json`` @3.1.1
# declares ``retry_after`` top-level, ``"type": "number"``, ``minimum: 1``,
# ``maximum: 3600`` — "Sellers MUST return values between 1 and 3600. Clients
# MUST clamp values outside this range." Written here as the two literals the
# spec states, because that is what the assertions are about; the seam is free
# to source them from wherever it keeps the constant.
_SPEC_RETRY_AFTER_MIN = 1
_SPEC_RETRY_AFTER_MAX = 3600


def _seam():
    """Import the seam lazily.

    A module-level import would turn "the seam does not exist yet" into a
    collection error for the whole file, which would also stop the local-origin
    fixture from ever running. Importing per-call keeps each case an
    independent, individually diagnosable failure.
    """
    from src.core.security import outbound_http

    return outbound_http


def call_seam(seam_call: str, url: str, **kwargs: Any):
    """Dispatch to ``send`` or ``asend`` — the one place the two differ."""
    seam = _seam()
    if seam_call == "send":
        return seam.send(url, **kwargs)
    return asyncio.run(seam.asend(url, **kwargs))


def record_sleeps(monkeypatch, seam_call: str) -> list[float]:
    """Capture the durations the seam actually sleeps, without waiting them out.

    Magnitudes of 1s/2s/4s cannot be graded by wall-clock measurement in a test
    suite, so the sleep itself is the observation point. This is the ONLY place
    the two seam paths differ in this file: ``send`` blocks on ``time.sleep`` and
    ``asend`` awaits ``asyncio.sleep``, so a test that patched only one would
    grade half the module.

    The async replacement must be an ``async def`` (or an ``AsyncMock``): a plain
    ``MagicMock`` returns a non-awaitable and the seam would ``TypeError`` on the
    await instead of failing on the schedule.
    """
    seam = _seam()
    durations: list[float] = []

    if seam_call == "send":
        monkeypatch.setattr(seam.time, "sleep", durations.append)
        return durations

    async def _record(seconds: float) -> None:
        durations.append(seconds)

    monkeypatch.setattr(seam.asyncio, "sleep", _record)
    return durations


def honoured_ceiling() -> float:
    """The seam's cap on how much of a ``Retry-After`` it will actually wait.

    Read off the module rather than restated, for the same reason
    ``_MAX_RESPONSE_BYTES`` is: the cases below grade the FORM of the wait
    computation — that the ceiling clamps the Retry-After CONTRIBUTION and never
    the geometric floor — and a hardcoded 60 would turn a form regression into a
    passing test the day someone lowers the constant.
    """
    return _seam()._MAX_HONOURED_RETRY_AFTER_SECONDS


def assert_blocked(seam_call: str, url: str, **kwargs: Any):
    """Assert the call is refused before any request leaves, and return the error."""
    with pytest.raises(_seam().OutboundRequestBlocked) as excinfo:
        call_seam(seam_call, url, **kwargs)
    return excinfo.value


def assert_delivery_failed(seam_call: str, url: str, **kwargs: Any):
    """Assert the call reached the network but did not deliver, and return the error."""
    with pytest.raises(_seam().OutboundDeliveryFailed) as excinfo:
        call_seam(seam_call, url, **kwargs)
    return excinfo.value


def was_refused_before_connecting(call) -> bool:
    """Whether ``call`` was refused by pre-connection policy, rather than after reaching the wire.

    ``OutboundDeliveryFailed`` and a plain return are both *not* refusals: they
    mean the URL got past scheme and address policy and the outcome was decided
    on the network. Collapsing every outcome to this one boolean is what lets
    the send path and the validate-only path be compared case by case.
    """
    try:
        call()
    except _seam().OutboundRequestBlocked:
        return True
    except _seam().OutboundDeliveryFailed:
        return False
    return False


# ---------------------------------------------------------------------------
# 1. Scheme policy — the one address-adjacent rule the seam owns itself
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/webhook",
        "ftp://example.com/webhook",
        "file:///etc/passwd",
        "/no/scheme",
    ],
)
def test_non_https_scheme_is_refused_by_default(seam_call, url, monkeypatch):
    """Anything that is not https:// is refused — unconditionally (GH #1757).

    ``example.com`` is a public address, so nothing but the scheme rule can be
    refusing these — that is what makes this a scheme test rather than a
    restatement of the address test below. No DNS lookup happens either: the
    scheme is checked before the transport is built.
    """
    set_flags(monkeypatch)

    error = assert_blocked(seam_call, url)

    assert isinstance(error, _seam().OutboundError)


# ---------------------------------------------------------------------------
# 2. Address policy — delegated to adcp.signing, graded here
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
def test_loopback_over_https_is_refused_without_connecting(seam_call, monkeypatch, local_origin):
    """A reserved-range destination is refused, and the origin is never reached."""
    set_flags(monkeypatch)

    assert_blocked(seam_call, f"https://127.0.0.1:{local_origin.port}/")

    assert local_origin.hits == 0, f"seam connected to a refused address: {local_origin.requests}"


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
def test_cloud_metadata_address_is_refused(seam_call, monkeypatch):
    """The cloud-metadata address is refused under default flags."""
    set_flags(monkeypatch)

    assert_blocked(seam_call, METADATA_URL)


# ---------------------------------------------------------------------------
# 3. The private-range escape hatch (GH #1757: the SCHEME hatch is gone
#    entirely — https is required unconditionally, no flag relaxes it)
#
# Under default flags http://127.0.0.1:<port>/ is refused by BOTH the scheme
# rule (unconditionally) and the address rule, so a bare `raises` cannot say
# which one fired. test_allow_private_alone_does_not_open_plain_http below
# isolates the address rule alone, against a plain-http origin, and confirms
# the scheme rule STILL refuses it — there is no flag combination left that
# would make that assertion false.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
def test_allow_private_alone_does_not_open_plain_http(seam_call, monkeypatch, local_origin):
    """ALLOW_PRIVATE relaxes the address rule only — the scheme rule still refuses http://."""
    set_flags(monkeypatch, private=True)

    assert_blocked(seam_call, f"{local_origin.base_url}/webhook")

    assert local_origin.hits == 0


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
def test_cloud_metadata_stays_refused_with_the_private_hatch_open(seam_call, monkeypatch):
    """Metadata blocking is unconditional — no flag reaches it, private-range hatch included."""
    set_flags(monkeypatch, private=True)

    assert_blocked(seam_call, METADATA_URL)


# ---------------------------------------------------------------------------
# 4. Redirects are not followed — the regression that matters most
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
def test_redirect_to_metadata_address_is_not_followed(seam_call, monkeypatch, local_origin_tls):
    """A 302 towards the metadata address is returned, never chased.

    ``requests.Session.request`` defaults ``allow_redirects=True``, so the code
    this seam replaces validated the URL and then followed a 302 to an internal
    address unchecked. The exact hit count is the proof: one hit means the
    seam stopped at the redirect.
    """
    set_flags(monkeypatch, private=True)
    local_origin_tls.redirect_to(METADATA_URL, status=302)

    error = assert_delivery_failed(seam_call, f"{local_origin_tls.base_url}/webhook", max_attempts=3)

    assert error.http_status == 302
    assert local_origin_tls.hits == 1, f"seam followed the redirect: {local_origin_tls.requests}"


# ---------------------------------------------------------------------------
# 5. Retry classification by status
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
def test_server_error_is_retried_to_max_attempts(seam_call, monkeypatch, local_origin_tls):
    """A 5xx is retried up to max_attempts, then reported as a delivery failure."""
    set_flags(monkeypatch, private=True)
    fast_backoff(monkeypatch)
    local_origin_tls.respond_with(503, body=b'{"error": "unavailable"}')

    error = assert_delivery_failed(seam_call, f"{local_origin_tls.base_url}/webhook", max_attempts=3)

    assert error.attempts == 3
    assert error.http_status == 503
    assert local_origin_tls.hits == 3


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
def test_client_error_is_terminal_and_not_retried(seam_call, monkeypatch, local_origin_tls):
    """A 4xx fails on the first attempt — retrying a rejected request only doubles the damage."""
    set_flags(monkeypatch, private=True)
    local_origin_tls.respond_with(404, body=b'{"error": "no such hook"}')

    error = assert_delivery_failed(seam_call, f"{local_origin_tls.base_url}/webhook", max_attempts=3)

    assert error.attempts == 1
    assert error.http_status == 404
    assert local_origin_tls.hits == 1


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
def test_max_attempts_one_sends_exactly_once(seam_call, monkeypatch, local_origin_tls):
    """max_attempts counts TOTAL attempts, not retries after the first."""
    set_flags(monkeypatch, private=True)
    local_origin_tls.respond_with(503)

    error = assert_delivery_failed(seam_call, f"{local_origin_tls.base_url}/webhook", max_attempts=1)

    assert error.attempts == 1
    assert local_origin_tls.hits == 1


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
def test_retry_recovers_when_the_origin_recovers(seam_call, monkeypatch, local_origin_tls):
    """503, 503, 200 succeeds on the third attempt — a retry that actually recovers.

    Every other retry case here programs a failure that never clears, so all of
    them would still pass if the seam gave up early and reported the failure.
    Only a per-attempt response sequence grades the other half of the contract:
    that attempt N+1 is really sent and its response is really the one returned.
    The origin serves the sequence itself, so the recovery is observed on the
    wire rather than staged by a mock's ``side_effect`` list.
    """
    set_flags(monkeypatch, private=True)
    fast_backoff(monkeypatch)
    local_origin_tls.respond_in_sequence(
        [
            (503, b'{"error": "unavailable"}'),
            (503, b'{"error": "still unavailable"}'),
            (200, b'{"ok": true}'),
        ]
    )

    result = call_seam(seam_call, f"{local_origin_tls.base_url}/webhook", max_attempts=3)

    assert result.http_status == 200
    assert result.json() == {"ok": True}
    assert result.attempts == 3
    assert local_origin_tls.hits == 3


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
def test_response_sequence_repeats_its_last_entry_past_the_end(seam_call, monkeypatch, local_origin_tls):
    """A queue shorter than the attempt count keeps answering with its final entry.

    This pins the origin's own contract, which the recovery case above depends
    on: a test programs the recovery point without having to know how many
    attempts the caller will make. Four attempts against a two-entry queue must
    report the SECOND entry's status, not the first and not a queue-exhausted
    error.
    """
    set_flags(monkeypatch, private=True)
    fast_backoff(monkeypatch)
    local_origin_tls.respond_in_sequence([(429, b'{"error": "slow down"}'), (503, b'{"error": "unavailable"}')])

    error = assert_delivery_failed(seam_call, f"{local_origin_tls.base_url}/webhook", max_attempts=4)

    assert error.attempts == 4
    assert error.http_status == 503
    assert local_origin_tls.hits == 4


# ---------------------------------------------------------------------------
# 6. Retry BACKOFF schedule — BR-RULE-029, graded at the seam
#
# The section above grades HOW MANY times the seam tries. This one grades how
# long it waits between those tries, which is a separate invariant with a
# separate history: production honoured 1s/2s/4s + jitter before the seam
# existed (src/core/webhook_delivery.py), so a seam that waits 0.1s/0.2s/0.4s
# would REGRESS it on both magnitude and randomisation the moment a call site
# migrated across.
#
# The schedule is graded through tests/helpers/backoff_assertions.py, the same
# grader the UC-004 BDD steps and the webhook integration tests use. Encoding
# 1/2/4 a second time here is exactly the drift that grader exists to prevent —
# one copy ends up checking a ratio while the other checks magnitudes, and a
# ratio check passes for 0.1/0.2/0.4.
#
# Both cases run on a plain 503 origin at max_attempts=4: three sleeps, which is
# the only attempt count that reaches the rule's third (4s) step. No
# ``local_origin.delay()`` here — nothing in these tests may reach a real sleep
# through the module a second time.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
def test_backoff_schedule_is_br_rule_029_by_default(seam_call, monkeypatch, local_origin_tls):
    """With no knob set, the seam waits BR-RULE-029's 1s, 2s, 4s — each randomised.

    ``jitter=None`` is the grader's live-jitter mode: every delay must land in
    ``[base, base + 1)`` AND at least one must differ from its base. So this one
    case grades the magnitudes and proves randomisation is actually applied,
    without the test knowing what was drawn.

    The base is INJECTED as the shipped default rather than assumed — the explicit form
    of "unconfigured". Reading it off the settings field means an ambient value cannot
    turn a passing default into a silently unrelated assertion, and the default itself
    is never restated here.
    """
    set_flags(monkeypatch, private=True)
    set_backoff_base(monkeypatch)
    local_origin_tls.respond_with(503, body=b'{"error": "unavailable"}')
    durations = record_sleeps(monkeypatch, seam_call)

    assert_delivery_failed(seam_call, f"{local_origin_tls.base_url}/webhook", max_attempts=4)

    assert_backoff_schedule(durations, jitter=None)


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
def test_backoff_draws_one_uniform_0_1_jitter_per_sleep(seam_call, monkeypatch, local_origin_tls):
    """Each delay is its base PLUS one ``uniform(0, 1)`` draw — additive, once per sleep.

    Pinning the draw turns the window check above into an exact one, so a
    magnitude regression cannot hide inside the jitter slack. The draw itself is
    graded too, and it is the shape of the draw that matters, not just that some
    randomness happened:

    * ``(0, 1)`` as two positional numbers — the same call signature the UC-004
      step asserts (``call.args == (0, 1)``), so the seam and the BDD suite pin
      one implementation rather than two compatible-looking ones.
    * exactly one draw per sleep — which is what putting the draw inside the ONE
      shared ``_backoff_seconds`` helper buys. A draw per attempt, or a copy in
      each of ``send``/``asend``, shows up here as the wrong count.
    * additive, not multiplicative — ``base + pinned``, which is what the UC-004
      step's ``_pinned_jitter`` grading requires of production.
    """
    set_flags(monkeypatch, private=True)
    set_backoff_base(monkeypatch)
    local_origin_tls.respond_with(503, body=b'{"error": "unavailable"}')
    durations = record_sleeps(monkeypatch, seam_call)
    draws = pin_jitter(monkeypatch, 0.25)

    assert_delivery_failed(seam_call, f"{local_origin_tls.base_url}/webhook", max_attempts=4)

    assert_backoff_schedule(durations, jitter=0.25)
    assert draws == [(0, 1), (0, 1), (0, 1)], (
        f"expected one uniform(0, 1) draw per backoff sleep, got {draws} for {len(durations)} sleeps"
    )


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
def test_backoff_base_moves_the_base_and_nothing_else(seam_call, monkeypatch, local_origin_tls):
    """A configured base scales the base; the doubling and the jitter term are untouched.

    This case grades the SETTING'S EFFECT, not the rule — the rule is graded two cases
    above, by ``assert_backoff_schedule``. That is why the expected delays are spelled
    out here rather than taken from the grader: the grader encodes 1/2/4, which
    is precisely what an overridden base is not.

    The jitter is pinned as well as the base, because the two are independent: an
    unpinned ``uniform(0, 1)`` dwarfs a 10ms base, and asserting on the sum would
    then be an assertion about the draw, not about the setting.

    This is the one shape worth a test for this knob, and the reason the three env-var
    cases above it are deleted: the value is injected typed, and what is graded is what
    the seam DID with it — three real waits against a real origin.
    """
    set_flags(monkeypatch, private=True)
    base = set_backoff_base(monkeypatch, 0.01)
    local_origin_tls.respond_with(503, body=b'{"error": "unavailable"}')
    durations = record_sleeps(monkeypatch, seam_call)
    pin_jitter(monkeypatch, 0.005)

    assert_delivery_failed(seam_call, f"{local_origin_tls.base_url}/webhook", max_attempts=4)

    expected = [base * 2**step + 0.005 for step in range(3)]
    assert durations == pytest.approx(expected), (
        f"expected base {base} doubled per attempt plus a pinned 0.005 jitter, got {durations}"
    )


# ``test_unusable_backoff_base_*`` IS DELETED, all three of its cases, and nothing
# replaces it. It asked for a fallback-and-warn on an unusable backoff base — the pattern
# § No Quiet Failures bans — so its premise had no subject; and repaired into "the value is
# refused" it graded the settings LOADER parsing a string, not any seller behaviour:
# ``"abc"`` grades pydantic refusing a non-numeric float, and ``"-1"`` / ``"0"`` grade
# pydantic enforcing our own ``Field(gt=0)`` declaration. No production code reads the
# environment at all (``ruff-environment.toml``), so there was no path from any of those
# strings to an obligation. What the base DOES to a delivery — the 1s/2s/4s schedule and the
# base moving it — is graded below by ``test_backoff_schedule_is_br_rule_029_by_default``
# and ``test_backoff_base_moves_the_base_and_nothing_else``, both driven by injecting the
# typed value rather than by writing a string into the environ.


# ---------------------------------------------------------------------------
# 7. Retry classification by exception — httpx signals these as exceptions,
#    never as a status, and they must not escape the seam
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
def test_timeout_is_retried_and_surfaces_as_delivery_failure(seam_call, monkeypatch, local_origin_tls):
    """A stalled origin trips the per-request timeout, is retried, then fails typed.

    ``http_status`` is None because there was never a response to read a status
    from. If this leaked a raw ``httpx.ReadTimeout`` instead, every migrated
    call site would have to keep its own ``except httpx...`` — which is the
    duplication the seam exists to delete.
    """
    set_flags(monkeypatch, private=True)
    fast_backoff(monkeypatch)
    local_origin_tls.delay(2.0)

    error = assert_delivery_failed(
        seam_call,
        f"{local_origin_tls.base_url}/webhook",
        timeout=0.5,
        max_attempts=2,
    )

    assert error.attempts == 2
    assert error.http_status is None
    assert local_origin_tls.hits == 2


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
def test_origin_closing_without_responding_is_retried_then_fails_typed(seam_call, monkeypatch, local_origin_tls):
    """An origin that hangs up mid-exchange is a retryable transport failure, not a leak.

    The origin accepts the request, records the hit, and closes the socket
    before writing a status line, so httpx raises ``RemoteProtocolError``
    ("Server disconnected without sending a response") of its own accord. That
    is the distinction from the timeout case above: a different httpx exception
    class reaching the same classification, from a real protocol violation on
    the wire rather than a stalled read.

    ``http_status`` is None for the same reason as a timeout — there was never a
    response to read a status from — and the raw httpx error must not escape,
    or every migrated call site keeps its own ``except httpx...``.
    """
    set_flags(monkeypatch, private=True)
    fast_backoff(monkeypatch)
    local_origin_tls.close_without_responding()

    error = assert_delivery_failed(seam_call, f"{local_origin_tls.base_url}/webhook", max_attempts=2)

    assert error.attempts == 2
    assert error.http_status is None
    assert local_origin_tls.hits == 2


# ---------------------------------------------------------------------------
# 8. Response-size cap — spec point 5's other half
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
def test_oversized_response_body_is_refused_and_not_retried(seam_call, monkeypatch, local_origin_tls):
    """A chunked body past the cap aborts the read; retrying it would only re-read it.

    httpx applies no default body limit, so an unbounded counterparty response
    is a memory-exhaustion vector. The body is chunked, not Content-Length'd,
    so the cap has to be enforced while accumulating rather than by reading a
    declared length.
    """
    set_flags(monkeypatch, private=True)
    cap = _seam()._MAX_RESPONSE_BYTES
    local_origin_tls.respond_chunked(cap + 1, chunk_size=64 * 1024)

    error = assert_delivery_failed(seam_call, f"{local_origin_tls.base_url}/webhook", max_attempts=3)

    assert error.attempts == 1
    assert local_origin_tls.hits == 1


# ---------------------------------------------------------------------------
# 9. Error opacity, asserted on the wire envelope (spec point 6)
#
# The repo's error-verification policy makes the envelope the authority, not
# `.message` — `details` rides to the buyer too: the boundary builds an
# `AdcpErrorResponse` from the exception and `to_wire` serializes it, which is
# what `envelope_for` does here.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
def test_blocked_envelope_hides_the_resolved_address_and_the_reason(seam_call, monkeypatch):
    """A refused address yields one indistinguishable envelope, whatever the real cause.

    The SDK's own messages say "resolved IP <IP> is in a reserved range" versus
    "cannot resolve host '<host>'". Echoing either back tells whoever supplied
    the URL both the resolved address and whether the name exists — an internal
    port and host scanner. The two envelopes must be identical.

    INDISTINGUISHABILITY is the whole grade. This used to also deny a word list
    ("reserved", "resolve", "metadata", "private") in the message, which was a
    proxy that neither implied the property nor followed from it: a per-cause
    message avoiding those words discloses just as much, and a CONSTANT message
    containing one discloses nothing. AdCPBlockedUrlError now owns the sentence
    and takes no message argument, so per-cause variation is unrepresentable and
    the equality below is a structural fact rather than a hope.
    """
    set_flags(monkeypatch)

    reserved = assert_blocked(seam_call, "https://127.0.0.1/webhook")
    unresolvable = assert_blocked(seam_call, "https://no-such-host.invalid/webhook")

    reserved_envelope = envelope_for(reserved)
    unresolvable_envelope = envelope_for(unresolvable)

    assert_envelope_shape(reserved_envelope, "VALIDATION_ERROR", recovery="correctable")
    assert_envelope_shape(unresolvable_envelope, "VALIDATION_ERROR", recovery="correctable")

    # The WHOLE envelope, not its message. Indistinguishability is the property;
    # reading one field to compare prose was a narrower way of asking the same
    # question, and it missed every other field the envelope carries.
    assert reserved_envelope == unresolvable_envelope, (
        "the envelope distinguishes an unresolvable host from a reserved address: "
        f"{unresolvable_envelope!r} vs {reserved_envelope!r}"
    )

    for envelope, forbidden in (
        (reserved_envelope, "127.0.0.1"),
        (unresolvable_envelope, "no-such-host.invalid"),
    ):
        assert forbidden not in json.dumps(envelope), f"{forbidden!r} leaked into {envelope}"


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
def test_carried_field_does_not_discriminate_the_refusal_cause(seam_call, monkeypatch):
    """A carried ``field`` names the caller's input on both layers — and nothing else.

    ``field`` is a PASSTHROUGH: the seam sees a URL string, not a request
    document, so it cannot compute a JSONPath-lite path and does not try. It
    carries the one the caller supplies, because the caller is the only party
    that knows whether the refused URL came off the wire and under which name
    (``core/error.json`` @3.1.1: ``field`` is a path into the REQUEST PAYLOAD).

    The grading is EQUALITY of the two whole envelopes, not a per-cause field
    assertion, and that is the point of putting this beside
    ``test_blocked_envelope_hides_the_resolved_address_and_the_reason``: once
    ``field`` rides the refusal it becomes a candidate scheme-vs-address
    discriminator, exactly the spec point 6 side channel that test exists to
    close. Two per-cause assertions would both pass while the two envelopes
    disagreed. Whole-envelope equality is also strictly stronger than the
    neighbour's whole-envelope comparison — it covers ``details``,
    ``suggestion``, ``recovery`` and ``field`` on both layers at once.

    The second half pins the default: a caller that supplies no ``field`` gets
    no ``field`` KEY, not a null. That default is what almost every call site
    wants: there are no unmigrated sites left, and the migrated ones that fetch
    stored operator config or a registered vendor endpoint have no
    request-payload path to name at all. Only a caller whose URL arrived in the caller's own request document
    passes one (``src/core/property_list_resolver.py`` is the first). Emitting
    a path for the others would invent one the buyer never sent, which Level 2
    makes worse than silence (``error-handling.mdx:16`` — ``field`` exists so an
    agent can SELF-CORRECT, and it cannot correct a path absent from its own
    document).
    """
    set_flags(monkeypatch)

    reserved = assert_blocked(
        seam_call, "https://127.0.0.1/webhook", provenance=CounterpartyUrl(field=_CALLER_FIELD_PATH)
    )
    unresolvable = assert_blocked(
        seam_call, "https://no-such-host.invalid/webhook", provenance=CounterpartyUrl(field=_CALLER_FIELD_PATH)
    )

    reserved_envelope = envelope_for(reserved)
    unresolvable_envelope = envelope_for(unresolvable)

    assert_envelope_shape(reserved_envelope, "VALIDATION_ERROR", recovery="correctable", field=_CALLER_FIELD_PATH)
    assert reserved_envelope == unresolvable_envelope, (
        "the refusal envelope distinguishes an unresolvable host from a reserved address: "
        f"{unresolvable_envelope} vs {reserved_envelope}"
    )

    for envelope, forbidden in (
        (reserved_envelope, "127.0.0.1"),
        (unresolvable_envelope, "no-such-host.invalid"),
    ):
        assert forbidden not in json.dumps(envelope), f"{forbidden!r} leaked into {envelope}"

    fieldless = envelope_for(assert_blocked(seam_call, "https://127.0.0.1/webhook"))

    assert "field" not in fieldless["adcp_error"], f"adcp_error carries a field key with no caller field: {fieldless}"
    assert "field" not in fieldless["errors"][0], f"errors[0] carries a field key with no caller field: {fieldless}"


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
@pytest.mark.parametrize(
    ("label", "field"),
    [
        ("the url itself", "https://127.0.0.1/webhook"),
        ("a url with other text", "agent_url=https://127.0.0.1/webhook"),
        ("a bare scheme", "ftp://"),
    ],
)
def test_a_field_carrying_a_url_is_refused_outright(seam_call, label, field, monkeypatch):
    """The seam refuses to carry a ``field`` that would leak the destination.

    ``field`` reaches the buyer, and the refusal message deliberately says
    nothing about the cause (spec point 6). A call site that passed the URL as
    the field would route straight around that silence, in a value the message
    never touches — so the rule cannot be documentation only.

    It raises rather than dropping the value: naming a URL where a request path
    belongs is a call-site bug, and swallowing it would ship that bug as a
    quietly fieldless envelope nobody notices.
    """
    set_flags(monkeypatch)

    with pytest.raises(ValueError, match="not a URL"):
        call_seam(seam_call, "https://127.0.0.1/webhook", provenance=CounterpartyUrl(field=field))


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
def test_a_jsonpath_lite_field_is_carried(seam_call, monkeypatch):
    """The legitimate form is not caught by the URL check — the guard has to let real paths through."""
    set_flags(monkeypatch)

    error = assert_blocked(seam_call, "https://127.0.0.1/webhook", provenance=CounterpartyUrl(field=_CALLER_FIELD_PATH))

    assert_envelope_shape(
        envelope_for(error),
        "VALIDATION_ERROR",
        recovery="correctable",
        field=_CALLER_FIELD_PATH,
    )


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
def test_delivery_failure_envelope_hides_the_origin_response(seam_call, monkeypatch, local_origin_tls):
    """A failed fetch reports attempts and status — never the origin's body or address."""
    set_flags(monkeypatch, private=True)
    local_origin_tls.respond_with(503, body=b'{"detail": "LEAKED-ORIGIN-BODY-MARKER"}')

    error = assert_delivery_failed(seam_call, f"{local_origin_tls.base_url}/webhook", max_attempts=2)
    envelope = envelope_for(error)

    assert_envelope_shape(envelope, "SERVICE_UNAVAILABLE", recovery="transient")
    assert envelope["errors"][0]["details"] == {"attempts": 2, "last_status": 503}

    serialized = json.dumps(envelope)
    assert "LEAKED-ORIGIN-BODY-MARKER" not in serialized
    assert local_origin_tls.host not in serialized
    assert str(local_origin_tls.port) not in serialized


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
def test_transport_failure_envelope_hides_the_httpx_error(seam_call, monkeypatch, local_origin_tls):
    """A timeout reports last_status=None — never the httpx error string, which names the address."""
    set_flags(monkeypatch, private=True)
    local_origin_tls.delay(2.0)

    error = assert_delivery_failed(
        seam_call,
        f"{local_origin_tls.base_url}/webhook",
        timeout=0.5,
        max_attempts=1,
    )
    envelope = envelope_for(error)

    assert_envelope_shape(envelope, "SERVICE_UNAVAILABLE", recovery="transient")
    assert envelope["errors"][0]["details"] == {"attempts": 1, "last_status": None}

    serialized = json.dumps(envelope)
    assert local_origin_tls.host not in serialized
    assert str(local_origin_tls.port) not in serialized
    for term in ("timeout", "timed out", "readtimeout", "connecterror"):
        assert term not in serialized.lower(), f"httpx internals {term!r} leaked into {envelope}"


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
def test_disconnect_envelope_is_indistinguishable_from_a_timeout_envelope(seam_call, monkeypatch, local_origin_tls):
    """A hang-up and a timeout produce the same envelope — which failure mode it was stays inside.

    Both are ``last_status: None`` after the same attempt count. Telling the two
    apart would tell whoever supplied the URL whether the destination accepted
    the connection and then hung up, or never answered at all — a liveness probe
    for internal hosts (spec point 6). httpx's own wording for this one is
    "Server disconnected without sending a response", which names the failure
    mode outright and must not reach the wire.
    """
    set_flags(monkeypatch, private=True)
    local_origin_tls.close_without_responding()

    error = assert_delivery_failed(seam_call, f"{local_origin_tls.base_url}/webhook", max_attempts=1)
    envelope = envelope_for(error)

    assert_envelope_shape(envelope, "SERVICE_UNAVAILABLE", recovery="transient")
    assert envelope["errors"][0]["details"] == {"attempts": 1, "last_status": None}

    serialized = json.dumps(envelope)
    assert local_origin_tls.host not in serialized
    assert str(local_origin_tls.port) not in serialized
    for term in ("disconnect", "remoteprotocolerror", "protocol", "without sending"):
        assert term not in serialized.lower(), f"httpx internals {term!r} leaked into {envelope}"


# ---------------------------------------------------------------------------
# 10. validate_url — the same policy, for URLs that are STORED rather than sent
#
# A webhook URL accepted at ingest is persisted now and fetched later, possibly
# by a background worker, so the refusal a buyer can act on has to be issued at
# ingest — before there is a request to attach it to. The obligation is not
# "validate_url refuses bad URLs" (a second hand-written copy of address policy
# would also do that, and that copy is the recurrence this seam exists to make
# impossible). It is that validate_url's verdict is IDENTICAL to send's, case
# for case, and that it reaches nothing while producing it.
# ---------------------------------------------------------------------------

# Every pre-connection decision the seam makes, as (url factory, escape hatches).
# The factory takes the live local origin so a case can target a real address.
# Nothing here leaves the machine: every case is refused, loopback, or both.
PRE_CONNECTION_CASES = {
    "plain-http-public": (lambda origin: "http://example.com/webhook", {}),
    "ftp-scheme": (lambda origin: "ftp://example.com/webhook", {}),
    "file-scheme": (lambda origin: "file:///etc/passwd", {}),
    "no-scheme": (lambda origin: "/no/scheme", {}),
    "https-unresolvable-host": (lambda origin: "https://no-such-host.invalid/webhook", {}),
    "https-loopback": (lambda origin: f"https://127.0.0.1:{origin.port}/", {}),
    "cloud-metadata": (lambda origin: METADATA_URL, {}),
    "cloud-metadata-private-hatch-open": (lambda origin: METADATA_URL, {"private": True}),
    "http-loopback-private-hatch-only": (lambda origin: f"{origin.base_url}/webhook", {"private": True}),
    # "http-loopback-insecure-hatch-only" and "http-loopback-both-hatches" were
    # deleted (GH #1757): there is no insecure hatch left to construct
    # either combination with, and the latter collapsed into an exact duplicate
    # of "http-loopback-private-hatch-only" the moment it did.
}


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
@pytest.mark.parametrize("case_id", sorted(PRE_CONNECTION_CASES))
def test_validate_url_reaches_the_same_verdict_as_the_send_path(seam_call, case_id, monkeypatch, local_origin):
    """For every pre-connection case, validate_url refuses iff the send path refuses.

    Asserting the two verdicts AGAINST EACH OTHER rather than against a
    hardcoded expectation is the point: a re-implementation that happened to
    agree on the cases someone thought to list would pass a hardcoded test and
    diverge on the next one. This can only pass while both paths make the
    decision in the same place — the escape hatches included, since a validator
    that read the flags differently would surface here as a disagreement rather
    than as a silent ingest-time refusal of URLs the fetcher would accept.

    The accepted case is graded too: a validator that refused everything would
    satisfy "refuses what send refuses" and quietly break every ingest path.
    """
    make_url, flags = PRE_CONNECTION_CASES[case_id]
    url = make_url(local_origin)

    # Validate first, on a still-untouched origin, so hits made by the send call
    # below can never be mistaken for hits made by the validator.
    set_flags(monkeypatch, **flags)
    validate_refused = was_refused_before_connecting(lambda: _seam().validate_url(url))

    assert local_origin.hits == 0, f"validate_url opened a connection: {local_origin.requests}"

    set_flags(monkeypatch, **flags)
    send_refused = was_refused_before_connecting(lambda: call_seam(seam_call, url, max_attempts=1))

    assert validate_refused == send_refused, (
        f"validate_url and {seam_call} disagree on {case_id} ({url!r}): "
        f"validate_url {'refused' if validate_refused else 'accepted'}, "
        f"{seam_call} {'refused' if send_refused else 'accepted'}"
    )


def test_validate_url_accepts_a_reachable_destination_without_reaching_it(monkeypatch, local_origin_tls):
    """An acceptable URL passes validation, returns nothing, and the origin never hears about it.

    Returning None rather than the resolved address is deliberate. Handing a
    call site an IP invites it to store or log the resolution and to connect by
    it later, and a resolution reused across the ingest/fetch gap is exactly the
    DNS-rebinding window the SDK's resolve-once-then-pin closes *within* one
    request. The later fetch has to resolve again through its own send call.
    """
    set_flags(monkeypatch, private=True)

    assert _seam().validate_url(f"{local_origin_tls.base_url}/webhook") is None

    assert local_origin_tls.hits == 0, f"validate_url opened a connection: {local_origin_tls.requests}"


def test_validate_url_refusal_envelope_hides_the_resolved_address_and_the_reason(monkeypatch):
    """Ingest-time refusals are as opaque as send-time ones — same message, same envelope.

    An ingest-time validator is the easier place to leak, because its whole job
    is to explain to the caller why the URL is unacceptable. Spec point 6 does
    not relax there: distinguishing "reserved range" from "does not resolve"
    turns URL submission into an internal host and port scanner.
    """
    set_flags(monkeypatch)
    seam = _seam()

    with pytest.raises(seam.OutboundRequestBlocked) as reserved_info:
        seam.validate_url("https://127.0.0.1/webhook")
    with pytest.raises(seam.OutboundRequestBlocked) as unresolvable_info:
        seam.validate_url("https://no-such-host.invalid/webhook")

    reserved_envelope = envelope_for(reserved_info.value)
    unresolvable_envelope = envelope_for(unresolvable_info.value)

    assert_envelope_shape(reserved_envelope, "VALIDATION_ERROR", recovery="correctable")
    assert_envelope_shape(unresolvable_envelope, "VALIDATION_ERROR", recovery="correctable")

    assert reserved_envelope == unresolvable_envelope

    for envelope, forbidden in (
        (reserved_envelope, "127.0.0.1"),
        (unresolvable_envelope, "no-such-host.invalid"),
    ):
        assert forbidden not in json.dumps(envelope), f"{forbidden!r} leaked into {envelope}"


# ---------------------------------------------------------------------------
# 11. Reserved-range equivalence pin — the delegation's soundness, executable
#
# Everywhere else in this file, "the seam refuses SSRF" is graded case by case
# against whatever URLs a scenario happens to construct. This is the one place
# that pins the underlying claim directly: adcp.signing's reserved-range
# coverage, one representative address per class, so a regression in the SDK's
# own classification (not a bug in this repo's call sites) goes red here
# first. No address-classification logic of its own — every verdict below is
# produced by calling validate_url, never recomputed.
#
# The classes adcp.signing does NOT classify — the #974 supplement, CGNAT
# included — are graded in section 12, against this repo's own policy object
# rather than against the SDK.
# ---------------------------------------------------------------------------

# One representative literal-IP URL per reserved class. IP literals (not
# hostnames) so the case tests the SDK's classification directly, with no DNS
# resolution step to also be right about.
#
# The last three arrived from tests/unit/test_ssrf_url_validator.py, which grades
# them against ``check_url_ssrf`` — the second copy of address policy
# GH #974 deletes. They are not redundant here: they are the classes
# whose refusal that suite attributed to this repo's own CIDR list and which the
# SDK in fact classifies on its own (multicast, multicast, reserved), so keeping
# them means the deletion loses no live coverage.
_RESERVED_RANGE_URLS = {
    "loopback": "https://127.0.0.1/",
    "link-local": "https://169.254.1.1/",
    "cloud-metadata": "https://169.254.169.254/",
    "rfc1918-10": "https://10.0.0.1/",
    "rfc1918-172": "https://172.16.0.1/",
    "rfc1918-192168": "https://192.168.1.1/",
    "zero-net": "https://0.0.0.0/",
    "ipv6-ula": "https://[fc00::1]/",
    "ipv4-mapped-ipv6": "https://[::ffff:127.0.0.1]/",
    "ipv4-multicast": "https://224.0.0.1/",
    "ipv6-multicast": "https://[ff02::1]/",
    "nat64-well-known": "https://[64:ff9b::a9fe:a9fe]/",
}


@pytest.mark.parametrize("range_name", sorted(_RESERVED_RANGE_URLS))
def test_reserved_range_is_refused_by_validate_url(range_name, monkeypatch):
    """One representative address per reserved class is refused, via the real seam.

    Pins adcp.signing's delegation, not application logic: goes red the day
    the SDK's classification for any of these classes regresses.
    """
    set_flags(monkeypatch)

    refused = was_refused_before_connecting(lambda: _seam().validate_url(_RESERVED_RANGE_URLS[range_name]))

    assert refused, f"{range_name} ({_RESERVED_RANGE_URLS[range_name]!r}) is no longer refused by validate_url"


# ---------------------------------------------------------------------------
# 12. Verdict parity — one address predicate, two verdicts (GH #974)
#
# The seam offers a resolve-and-dial verdict only. A URL that is STORED at ingest
# and fetched later needs a second, DNS-FREE verdict at registration time,
# because an unresolvable but public hostname must be ACCEPTED then and
# re-checked when it is actually dialled. That second verdict was built OUTSIDE
# the seam (``src/core/security/url_validator.py``), and once outside it grew its
# own range set — which then drifted: the six ranges below are refused at ingest
# and dialled happily (GH #974, the live gap this section closes).
#
# So the obligation is not "each verdict refuses bad URLs". Two independent
# copies of address policy satisfy that too, and that IS the disease. It is that
# the two verdicts agree row for row, diverging only on the two axes they are
# DEFINED to differ on: DNS (registration performs none) and the loopback
# allowance (registration-only, for capture servers under ADCP_TESTING).
#
# The tables are stated HERE and never derived from the policy module's own
# frozenset. A row read out of production would vanish along with the CIDR a
# mutation deletes and the test would stay green — and the mutations are where
# this section's teeth are:
#
#   * drop ONE entry from the policy's supplement frozenset and the matching row
#     reddens in BOTH ``test_a_supplement_range_is_refused_at_registration`` and
#     ``test_a_supplement_range_is_refused_at_dial``. Two rows, one edit: that is
#     the proof the address predicate is genuinely shared rather than two copies
#     that happen to agree today. At HEAD, with two range sets, no single edit
#     can do it.
#   * drop the FLAG half of the shared predicate (leaving only the supplement
#     frozenset) and ``test_a_reserved_range_is_refused_at_registration`` reddens
#     while its dial twin ``test_reserved_range_is_refused_by_validate_url``
#     (section 11) stays green — adcp.signing catches those classes
#     independently, and registration never reaches adcp.signing. That asymmetry
#     is the proof the flag half is load-bearing for the DNS-free verdict
#     specifically rather than decorative.
#   * put the supplement half of the dial check back behind the private-range
#     hatch and ``test_a_supplement_range_stays_refused_with_the_private_hatch_
#     open`` reddens on all six rows while its hatch-CLOSED twin stays green.
#     That pair is the proof the supplement set is outside the hatch rather
#     than merely refused in the default posture: the hatch is the seam's ONE
#     remaining posture knob, so a set no posture can open is a set nothing can
#     open. Registration's column is unaffected by construction —
#     ``check_registration`` takes no ``allow_private`` parameter at all.
#   * add a range to the policy's frozenset without adding a row here, or
#     delete a row here whose CIDR is still in production, and
#     ``test_the_supplement_oracle_table_covers_the_production_set_exactly``
#     reddens. That case is the CONVERSE of the hand-stated rule above and the
#     only one that reads production: it computes no verdict, it asserts the
#     two sets correspond one-to-one, so a new range cannot arrive ungraded and
#     a row cannot outlive the range it grades.
#
# The dial half of every row is graded through ``validate_url`` rather than
# ``send``/``asend`` for one reason: an accepted row would otherwise open a real
# socket to a reserved address and hang until the connect timeout. Section 10
# already pins validate_url's verdict against send's and asend's case for case,
# so nothing is lost by grading the address decision on the entry point that
# makes no connection.
#
# Every case writes the private-range hatch explicitly, dial-only rows included.
# The registration verdict takes its loopback allowance as a PARAMETER and reads
# no environment of its own — so a future implementation that quietly read the
# seam's hatch instead would be graded here rather than passing by accident.
# ---------------------------------------------------------------------------

# The #974 supplement: reserved allocations ``adcp.signing`` does not classify,
# so this repo carries them itself (``src/core/security/url_validator.py`` at
# HEAD; ``src/core/security/egress/policy.py`` after GH #974). CGNAT
# leads the list because this file previously pinned it as an ACCEPTED gap
# (``test_cgnat_is_the_one_documented_gap_not_refused_by_validate_url``, deleted
# here per its own docstring instruction now that the gap closes rather than
# staying open to keep a canary green).
# FIXME(adcontextprotocol/adcp-client-python#974): drop this table once we adopt
# a release that carries these ranges upstream — every row then belongs in
# _RESERVED_RANGE_URLS above, where the SDK's own coverage is pinned.
_SUPPLEMENT_RANGE_URLS = {
    "cgnat": "https://100.64.0.1/",  # RFC 6598 shared address space
    "6to4-relay": "https://192.88.99.1/",  # RFC 7526
    "as112-v4": "https://192.31.196.1/",  # RFC 7535
    "amt": "https://192.52.193.1/",  # RFC 7450
    "as112-direct": "https://192.175.48.1/",  # RFC 7534
    "orchidv2": "https://[2001:20::1]/",  # RFC 7343
}

# Hostnames refused by NAME, before any address is known: cloud-metadata aliases
# and the Docker-internal names that resolve to a private address on a developer
# machine and to nothing at all in CI. Migrated from
# tests/unit/test_ssrf_url_validator.py, which grades them against the
# ``check_url_ssrf`` copy this lane deletes.
#
# The two halves of these rows refuse for different REASONS — registration knows
# the name is blocked, while dial resolves it and refuses either the private
# address it gets back or the NXDOMAIN. Both are refusals, which is what parity
# is about, and ``test_blocked_envelope_hides_the_resolved_address_and_the_reason``
# already pins that the two are indistinguishable on the wire, so the difference
# is not one any caller can act on.
_BLOCKED_HOSTNAME_URLS = {
    "gcp-metadata": "https://metadata.google.internal/computeMetadata/v1/",
    "docker-host": "https://host.docker.internal/",
    "docker-gateway": "https://gateway.docker.internal/",
}

# The one rule with three spellings at HEAD (``outbound_http._require_tls``,
# ``url_validator._scheme_error``, ``WebhookURLValidator._require_https``) and
# one spelling after this lane. Both verdicts must refuse all of it: an ingest
# gate that admitted a scheme the fetcher refuses accepts a buyer's webhook URL
# with a success envelope and then never delivers to it — the one failure mode
# the buyer can neither see nor correct.
_NON_HTTPS_URLS = {
    "plain-http": "http://example.com/webhook",
    "ftp": "ftp://example.com/webhook",
    "file": "file:///etc/passwd",
    "no-scheme": "/no/scheme",
}

# The loopback allowance's two shapes. They travel different branches of the
# registration verdict — ``localhost`` is refused by NAME and ``127.0.0.1`` by
# ADDRESS — so an allowance implemented as a flag on the address predicate alone
# would rescue one and not the other.
_LOOPBACK_URLS = {
    "loopback-ip": "https://127.0.0.1/",
    "localhost-name": "https://localhost/",
}

# The registration-side testing hatch, written as a literal for the same reason
# ``set_flags`` writes the seam's: these tests drive the env surface from
# outside. The registration verdict itself takes ``allow_loopback`` as a
# parameter; this name exists here only so a case can prove the DIAL verdict is
# deaf to it.
ADCP_TESTING_ENV = "ADCP_TESTING"


def _policy():
    """Import the registration verdict lazily, for the same reason as :func:`_seam`.

    A module-level import would turn "the policy object does not exist yet" into
    a collection error for the whole file — which would also stop the local
    origin fixture from ever running and take the dial-side rows down with it,
    including the ones that grade the live gap independently of this import.
    """
    from src.core.security.egress.policy import EgressPolicy

    return EgressPolicy


def registration_refused(url: str, *, allow_loopback: bool = False) -> bool:
    """Whether the DNS-free registration verdict refuses ``url``.

    Graded on ``AdCPBlockedUrlError``, which is the ONE refusal type both
    verdicts raise — ``OutboundRequestBlocked`` is a subclass of it — rather than
    on two types that happen to map to the same wire code today.
    """
    try:
        _policy().check_registration(url, allow_loopback=allow_loopback)
    except AdCPBlockedUrlError:
        return True
    return False


def dial_refused(url: str) -> bool:
    """Whether the dial-time verdict refuses ``url``, without opening a socket."""
    return was_refused_before_connecting(lambda: _seam().validate_url(url))


@pytest.mark.parametrize("range_name", sorted(_SUPPLEMENT_RANGE_URLS))
def test_a_supplement_range_is_refused_at_registration(range_name, monkeypatch):
    """Every #974 range the repo compensates for is refused by the DNS-free verdict.

    This is the half that works at HEAD, through ``check_url_ssrf``'s literal-IP
    branch. It is restated against the policy object because the lane's mutation
    proof needs a registration row and a dial row reading ONE table: with the two
    range sets HEAD has, no single edit can redden both.
    """
    set_flags(monkeypatch)
    url = _SUPPLEMENT_RANGE_URLS[range_name]

    assert registration_refused(url), f"{range_name} ({url!r}) is accepted by the registration verdict"


@pytest.mark.parametrize("range_name", sorted(_SUPPLEMENT_RANGE_URLS))
def test_a_supplement_range_is_refused_at_dial(range_name, monkeypatch):
    """The same six ranges are refused when the destination is actually dialled.

    RED at HEAD for all six, and that failure IS GH #974: the seam never
    imports the supplement, so ``adcp.signing`` alone decides — and none of these
    ranges trips any flag it tests. A URL refused at ingest and dialled happily
    is not a partial fix, it is the gap wearing the fix's clothes: the buyer's
    webhook was refused, while a range that reaches the same networks is fetched
    by anything already holding one (a stored creative agent_url, a vendor
    endpoint) with no ingest gate in front of it.
    """
    set_flags(monkeypatch)
    url = _SUPPLEMENT_RANGE_URLS[range_name]

    assert dial_refused(url), f"{range_name} ({url!r}) is accepted by the dial-time verdict"


@pytest.mark.parametrize("range_name", sorted(_SUPPLEMENT_RANGE_URLS))
def test_a_supplement_range_stays_refused_with_the_private_hatch_open(range_name, monkeypatch):
    """The supplement set is outside the hatch — no flag reaches it, ALLOW_PRIVATE included.

    The twin of ``test_cloud_metadata_stays_refused_with_the_private_hatch_open``
    for the OTHER unconditional refusal, and the two are unconditional for
    different reasons. Metadata immunity is the SDK's: ``resolve_and_validate_
    host`` checks its metadata set BEFORE it reads ``allow_private`` at all, so
    the seam inherits that immunity without owning it. This immunity is the
    SEAM'S OWN: ``adcp.signing`` does not classify these six ranges (they
    evaluate False on every flag it tests, which is why the supplement set
    exists at all), so nothing but the seam's own predicate can refuse them —
    and if that predicate sits behind the hatch, opening the hatch accepts them.

    That is exactly what happens at HEAD: with ``ADCP_OUTBOUND_ALLOW_PRIVATE``
    open, all six ranges are ACCEPTED at dial while ``check_registration``
    refuses all six under every posture. The two verdicts of section 12 are
    supposed to differ only on DNS and the loopback allowance; a posture that
    flips one column and not the other is a third divergence nothing declared.

    No socket in either state: ``dial_refused`` goes through ``validate_url``,
    which reaches its verdict before any connection is attempted, so the RED
    state costs a refused address rather than a connect timeout.
    """
    set_flags(monkeypatch, private=True)
    url = _SUPPLEMENT_RANGE_URLS[range_name]

    assert dial_refused(url), f"{range_name} ({url!r}) is accepted by the dial-time verdict with the private hatch open"


def test_the_supplement_oracle_table_covers_the_production_set_exactly():
    """``_SUPPLEMENT_RANGE_URLS`` and the production frozenset are in exact bijection.

    The converse guard for the hand-stated table. The rows above stay hand-
    stated so that deleting a CIDR from production reddens them (a row read out
    of production would vanish with the CIDR it grades) — but hand-stated rows
    have the opposite blind spot: a range ADDED to production with no row, or a
    row whose address no longer lands in any production range, is graded by
    nothing at all.

    This is MEMBERSHIP, never derivation: no verdict is computed here. The rows
    keep asserting refusal against the addresses they name; this case only
    asserts that the set of names and the set of production CIDRs correspond
    one-to-one, so every future range arrives with its own refusal rows.
    """
    from src.core.security.egress.policy import _SUPPLEMENT_NETWORKS

    graded_ips = {
        range_name: ipaddress.ip_address(urlparse(url).hostname) for range_name, url in _SUPPLEMENT_RANGE_URLS.items()
    }

    rows_by_network: dict[Any, list[str]] = {network: [] for network in _SUPPLEMENT_NETWORKS}
    ungraded_rows = {}
    for range_name, ip in graded_ips.items():
        matches = [network for network in _SUPPLEMENT_NETWORKS if ip in network]
        if not matches:
            ungraded_rows[range_name] = str(ip)
        for network in matches:
            rows_by_network[network].append(range_name)

    assert not ungraded_rows, (
        f"table rows whose address is in no production range: {ungraded_rows} — "
        "the row grades nothing, or the range was deleted from _SUPPLEMENT_NETWORKS"
    )
    assert all(len(rows) == 1 for rows in rows_by_network.values()), (
        "every production supplement range needs exactly one graded row, got "
        f"{ {str(network): rows for network, rows in rows_by_network.items()} }"
    )


@pytest.mark.parametrize("range_name", sorted(_RESERVED_RANGE_URLS))
def test_a_reserved_range_is_refused_at_registration(range_name, monkeypatch):
    """The DNS-free verdict refuses the SDK's classes too — from its own predicate.

    ``check_registration`` never reaches ``adcp.signing``: it has no address to
    resolve, only the literal one already in the URL. So the flag half of the
    shared predicate (``is_private``/``is_loopback``/``is_link_local``/
    ``is_multicast``/``is_reserved``/``is_unspecified``, the SDK's own list) is
    what refuses these here, and it is load-bearing exactly once — on this side.

    THIS is the row that carries the second mutation obligation: delete the flag
    half of the shared predicate and every case here reddens, while its dial twin
    ``test_reserved_range_is_refused_by_validate_url`` stays green because the SDK
    catches these independently. A predicate reduced to the supplement frozenset
    would therefore regress registration silently — accepting literal 10.0.0.1 —
    and pass every dial-side test in this file.
    """
    set_flags(monkeypatch)
    url = _RESERVED_RANGE_URLS[range_name]

    assert registration_refused(url), f"{range_name} ({url!r}) is accepted by the registration verdict"


@pytest.mark.parametrize("case_id", sorted(_BLOCKED_HOSTNAME_URLS))
def test_a_blocked_hostname_is_refused_by_both_verdicts(case_id, monkeypatch):
    """A hostname on the blocklist is refused at registration and at dial.

    Asserted as "both refuse" rather than "the two agree": two verdicts that both
    ACCEPTED would agree perfectly, and a parity table graded only on agreement
    is satisfied by a policy object that refuses nothing.
    """
    set_flags(monkeypatch)
    url = _BLOCKED_HOSTNAME_URLS[case_id]

    assert registration_refused(url), f"{case_id} ({url!r}) is accepted by the registration verdict"
    assert dial_refused(url), f"{case_id} ({url!r}) is accepted by the dial-time verdict"


@pytest.mark.parametrize("case_id", sorted(_NON_HTTPS_URLS))
def test_a_non_https_url_is_refused_by_both_verdicts(case_id, monkeypatch):
    """https is required by both verdicts, from one predicate.

    ``example.com`` is a public address and ``/no/scheme`` has none at all, so
    nothing but the scheme rule can be refusing these — which is what makes this
    a scheme row rather than a restatement of the address rows above.
    """
    set_flags(monkeypatch)
    url = _NON_HTTPS_URLS[case_id]

    assert registration_refused(url), f"{case_id} ({url!r}) is accepted by the registration verdict"
    assert dial_refused(url), f"{case_id} ({url!r}) is accepted by the dial-time verdict"


def test_an_unresolvable_public_hostname_is_the_first_documented_divergence(monkeypatch):
    """Accepted at registration, refused at dial — the whole reason two verdicts exist.

    A buyer registering ``https://hooks.buyer.example/`` for an endpoint that is
    not serving yet must not be told their URL is invalid; the fetcher decides
    that when it dials. Losing this row collapses the two verdicts into one and
    makes every registration surface reject public-but-not-yet-resolvable URLs.

    The dial half also carries what the dropped ``resolve_dns=True`` branch of
    ``check_url_ssrf`` used to express (``test_unresolvable_hostname_rejected``
    in tests/unit/test_ssrf_url_validator.py): an unresolvable host IS refused —
    at the verdict that resolves, by the SDK's single pinned resolution rather
    than by a second ``socket.gethostbyname``.
    """
    set_flags(monkeypatch)
    url = "https://no-such-host.invalid/webhook"

    assert not registration_refused(url), (
        f"{url!r} is refused at registration: a public hostname that does not resolve YET "
        "must be accepted at ingest and re-checked when it is dialled"
    )
    assert dial_refused(url), f"{url!r} is accepted by the dial-time verdict"


@pytest.mark.parametrize("case_id", sorted(_LOOPBACK_URLS))
def test_the_loopback_allowance_is_the_second_documented_divergence(case_id, monkeypatch):
    """``allow_loopback`` rescues a capture server at registration, and never at dial.

    Both shapes are graded because they travel different branches: ``localhost``
    is on the hostname blocklist and has no address for the predicate to test,
    while ``127.0.0.1`` is refused by the predicate. An allowance implemented as
    a flag on the address predicate would rescue the second and not the first,
    and the ADCP_TESTING capture-server flow uses both spellings.

    ``ADCP_TESTING`` is set here to pin the other half of the asymmetry: the dial
    verdict does not read it. Two hatches, one per verdict — the registration
    allowance is a parameter its caller derives from ADCP_TESTING, and the dial
    path has only ``ADCP_OUTBOUND_ALLOW_PRIVATE``. A single hatch reaching both
    would make a test-mode process fetch loopback URLs in earnest.
    """
    set_flags(monkeypatch)
    monkeypatch.setenv(ADCP_TESTING_ENV, "true")
    url = _LOOPBACK_URLS[case_id]

    assert not registration_refused(url, allow_loopback=True), (
        f"{case_id} ({url!r}) is refused at registration with the loopback allowance open"
    )
    assert registration_refused(url, allow_loopback=False), (
        f"{case_id} ({url!r}) is accepted at registration with the allowance CLOSED — "
        "the allowance is not the default posture"
    )
    assert dial_refused(url), f"{case_id} ({url!r}) is accepted by the dial-time verdict under ADCP_TESTING"


@pytest.mark.parametrize(
    ("label", "url"),
    [
        ("a base private range", _RESERVED_RANGE_URLS["rfc1918-10"]),
        ("a supplement range", _SUPPLEMENT_RANGE_URLS["cgnat"]),
        ("a blocked hostname", _BLOCKED_HOSTNAME_URLS["gcp-metadata"]),
        ("plain http", _NON_HTTPS_URLS["plain-http"]),
    ],
)
def test_the_loopback_allowance_rescues_nothing_but_loopback(label, url, monkeypatch):
    """``allow_loopback`` is not ``allow_private`` — it is a rescue, not a posture.

    The tempting implementation threads the allowance into the address predicate
    as its ``allow_private`` argument, which would make an ADCP_TESTING process
    accept ``10.0.0.1``, every #974 range, the metadata hostnames and plain http
    at registration — a test-mode knob that quietly disarms the whole ingest
    gate, on the one code path a buyer's URL enters through.
    """
    set_flags(monkeypatch)

    assert registration_refused(url, allow_loopback=True), (
        f"the loopback allowance rescued {label} ({url!r}) at registration"
    )


# ---------------------------------------------------------------------------
# 13. Retry-After — the origin's own instruction, honoured in ONE direction
#
# Section 6 grades the schedule the seam picks for itself. This section grades
# what an origin is allowed to do to that schedule, and it is deliberately
# asymmetric:
#
#   * Retry-After can only ever make the seam wait LONGER. BR-RULE-029 is a
#     FLOOR, so an origin answering "Retry-After: 0" cannot talk the seam out of
#     the 1s/2s/4s it owes every other client of that endpoint.
#   * The honoured contribution is capped, because an unbounded Retry-After is a
#     DoS lever: a counterparty answering "Retry-After: 3600" would otherwise
#     pin the calling task for an hour.
#   * That cap clamps the CONTRIBUTION, never the whole wait. The rejected form
#     ``min(max(backoff, retry_after), CEILING)`` sleeps LESS than BR-RULE-029
#     whenever the geometric wait exceeds the ceiling — silently, in the one
#     module that owns the invariant, and reachable through the PUBLIC
#     ``max_attempts`` parameter.
#
# The value the BUYER receives is a separate decision from the wait: it is
# clamped to the spec's [1, 3600] (``core/error.json`` @3.1.1) and rides the
# envelope's TOP LEVEL, not ``details``. Fusing the two would floor every wait
# at one second and make an origin unable to say "come straight back".
#
# Every case records sleeps instead of serving them, exactly as section 6 does:
# these grade magnitudes, and a suite that really waited them out would take
# minutes.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
def test_retry_after_zero_does_not_shorten_the_br_rule_029_floor(seam_call, monkeypatch, local_origin_tls):
    """An origin asking for no wait at all still gets BR-RULE-029's 1s/2s/4s.

    This is the floor half of the asymmetry, and it is the half an origin can
    attack: a busy endpoint that answers ``Retry-After: 0`` to every 429 would,
    under a "the origin decides" reading, get the whole fleet hammering it in
    lockstep — precisely the thundering herd the rule exists to prevent.

    Graded through ``assert_backoff_schedule``, the same grader section 6 and the
    UC-004 steps use. Re-encoding 1/2/4 here would be the drift that grader
    exists to prevent.
    """
    set_flags(monkeypatch, private=True)
    set_backoff_base(monkeypatch)
    rate_limited(local_origin_tls, retry_after="0")
    durations = record_sleeps(monkeypatch, seam_call)
    pin_jitter(monkeypatch, 0.25)

    assert_delivery_failed(seam_call, f"{local_origin_tls.base_url}/webhook", max_attempts=4)

    assert_backoff_schedule(durations, jitter=0.25)


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
def test_retry_after_lengthens_a_wait_the_geometric_base_would_have_cut_short(seam_call, monkeypatch, local_origin_tls):
    """A Retry-After above the geometric wait is what the seam actually waits.

    The other half of the asymmetry: when the origin asks for longer than the
    schedule would have waited, the seam obeys. Without this the header is
    decoration — the seam would be parsing a value it never acts on.

    The base knob is turned right down so the geometric term cannot be what
    produces the observed delays: at a 0.001s base every wait below is three
    orders of magnitude above anything the schedule would have chosen.
    """
    set_flags(monkeypatch, private=True)
    set_backoff_base(monkeypatch, 0.001)
    rate_limited(local_origin_tls, retry_after="5")
    durations = record_sleeps(monkeypatch, seam_call)
    pin_jitter(monkeypatch, 0.0)

    assert_delivery_failed(seam_call, f"{local_origin_tls.base_url}/webhook", max_attempts=3)

    assert durations == pytest.approx([5.0, 5.0]), (
        f"expected both waits to be the origin's 5s Retry-After, got {durations} "
        "(the 0.001s geometric base cannot produce these)"
    )


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
def test_an_enormous_retry_after_cannot_pin_the_caller(seam_call, monkeypatch, local_origin_tls):
    """``Retry-After: 3600`` is honoured only up to the seam's ceiling.

    Honouring a counterparty's number without a cap hands it a lever to hold a
    worker for as long as it likes, which is a DoS with a polite header on it.
    The cap is the seam's, not the spec's: the spec's [1, 3600] bounds what a
    seller may SEND, and 3600 seconds of a held task is exactly the outcome this
    refuses.
    """
    set_flags(monkeypatch, private=True)
    set_backoff_base(monkeypatch, 0.001)
    rate_limited(local_origin_tls, retry_after=str(_SPEC_RETRY_AFTER_MAX))
    durations = record_sleeps(monkeypatch, seam_call)
    pin_jitter(monkeypatch, 0.0)

    assert_delivery_failed(seam_call, f"{local_origin_tls.base_url}/webhook", max_attempts=2)

    assert durations == pytest.approx([honoured_ceiling()]), (
        f"expected the wait to stop at the seam's honoured ceiling {honoured_ceiling()}s, got {durations}"
    )


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
def test_the_retry_after_ceiling_never_cuts_the_geometric_wait(seam_call, monkeypatch, local_origin_tls):
    """At ``max_attempts=5`` every geometric wait survives the ceiling intact.

    This is the case that grades the FORM of the wait computation rather than
    its arithmetic at today's constants:

    * correct — ``max(backoff, min(retry_after, CEILING))``: the ceiling clamps
      the Retry-After CONTRIBUTION, so the geometric floor is untouched.
    * rejected — ``min(max(backoff, retry_after), CEILING)``: the ceiling clamps
      the WHOLE wait, so every wait above it collapses to the ceiling and the
      seam sleeps LESS than BR-RULE-029.

    The two forms agree until ``backoff > CEILING``, which is why the base knob
    is raised until that is true on the very first sleep instead of waiting for
    an attempt count nobody uses. ``max_attempts`` is public, so this is
    reachable from any call site — and the collision arrives sooner the moment
    anyone lowers the ceiling or raises the attempts. The form has to be right;
    the arithmetic at today's numbers is not the invariant.
    """
    set_flags(monkeypatch, private=True)
    base = set_backoff_base(monkeypatch, honoured_ceiling() * 2)
    rate_limited(local_origin_tls, retry_after=str(int(honoured_ceiling()) + 1))
    durations = record_sleeps(monkeypatch, seam_call)
    pin_jitter(monkeypatch, 0.0)

    assert_delivery_failed(seam_call, f"{local_origin_tls.base_url}/webhook", max_attempts=5)

    expected = [base * (2**step) for step in range(4)]
    assert durations == pytest.approx(expected), (
        f"expected the geometric schedule {expected} to survive the "
        f"{honoured_ceiling()}s Retry-After ceiling, got {durations}"
    )
    assert all(duration > honoured_ceiling() for duration in durations), (
        f"a wait was cut down to the Retry-After ceiling {honoured_ceiling()}s: {durations}. "
        "The ceiling must clamp the Retry-After contribution, never the BR-RULE-029 floor."
    )


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
@pytest.mark.parametrize(
    ("sent", "carried"),
    [
        ("0", _SPEC_RETRY_AFTER_MIN),
        ("30", 30),
        ("99999", _SPEC_RETRY_AFTER_MAX),
    ],
)
def test_the_carried_retry_after_is_clamped_to_the_spec_bound(seam_call, sent, carried, monkeypatch, local_origin_tls):
    """What the buyer receives is clamped to [1, 3600]; what the seam waits is not.

    ``core/error.json`` @3.1.1: ``retry_after`` is ``"type": "number"``,
    ``minimum: 1``, ``maximum: 3600`` — "Clients MUST clamp values outside this
    range." So the value on the envelope is bounded whatever the origin sent.

    Splitting the two uses is what makes ``Retry-After: 0`` expressible at all.
    Clamping before the wait would floor every wait at one second, so an origin
    could not answer "come straight back" and every case in this section would
    burn a second of real time it cannot get back.
    """
    set_flags(monkeypatch, private=True)
    set_backoff_base(monkeypatch, 0.001)
    rate_limited(local_origin_tls, retry_after=sent)
    record_sleeps(monkeypatch, seam_call)
    pin_jitter(monkeypatch, 0.0)

    error = assert_delivery_failed(seam_call, f"{local_origin_tls.base_url}/webhook", max_attempts=2)

    assert error.retry_after == carried, (
        f"origin sent Retry-After: {sent}, buyer should receive {carried} "
        f"(spec bound [{_SPEC_RETRY_AFTER_MIN}, {_SPEC_RETRY_AFTER_MAX}]), got {error.retry_after!r}"
    )


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
def test_retry_after_rides_the_envelope_top_level_not_details(seam_call, monkeypatch, local_origin_tls):
    """The value reaches the buyer in the spec's own slot, on both envelope layers.

    ``core/error.json`` @3.1.1 puts ``retry_after`` at the TOP LEVEL of the error
    object, and ``error-details/rate-limited.json`` enumerates
    limit / remaining / window_seconds / scope with no ``retry_after`` member —
    so ``details["retry_after"]`` is not the modelled home, it is a slot a call
    site invented. Both layers are asserted for the same reason
    ``assert_envelope_shape`` checks ``field`` on both: pinned storyboards read
    each of them in the wild.
    """
    set_flags(monkeypatch, private=True)
    set_backoff_base(monkeypatch, 0.001)
    rate_limited(local_origin_tls, retry_after="30")
    record_sleeps(monkeypatch, seam_call)
    pin_jitter(monkeypatch, 0.0)

    error = assert_delivery_failed(seam_call, f"{local_origin_tls.base_url}/webhook", max_attempts=2)
    envelope = envelope_for(error)

    assert_envelope_shape(envelope, "SERVICE_UNAVAILABLE", recovery="transient")
    assert envelope["adcp_error"].get("retry_after") == 30, f"adcp_error carries no top-level retry_after: {envelope}"
    assert envelope["errors"][0].get("retry_after") == 30, f"errors[0] carries no top-level retry_after: {envelope}"
    assert "retry_after" not in (envelope["errors"][0].get("details") or {}), (
        f"retry_after was duplicated into details, which models no such member: {envelope}"
    )


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
def test_details_carries_exactly_the_declared_detail_keys(seam_call, monkeypatch, local_origin_tls):
    """``_DETAIL_KEYS`` is a promise about the buyer-visible payload — asserted, not commented.

    ``details`` rides to the buyer on the ``AdcpErrorResponse`` the boundary builds
    from the exception and serializes with ``to_wire``, so the seam's rule is that
    nothing derived from the origin's response or from an httpx error string may
    be added to it (spec point 6). That rule is a comment
    on a ClassVar referenced nowhere else, and the call sites migrating onto the
    seam lean on it — including this ticket's, which carries ``retry_after`` in
    ``AdCPSalesAgentError``'s own slot precisely so ``details`` does not grow.

    Graded on a 429 WITH a Retry-After, which is the answer most likely to leak a
    fourth key.
    """
    set_flags(monkeypatch, private=True)
    set_backoff_base(monkeypatch, 0.001)
    rate_limited(local_origin_tls, retry_after="30")
    record_sleeps(monkeypatch, seam_call)
    pin_jitter(monkeypatch, 0.0)

    error = assert_delivery_failed(seam_call, f"{local_origin_tls.base_url}/webhook", max_attempts=2)
    envelope = envelope_for(error)

    declared = _seam().OutboundDeliveryFailed._DETAIL_KEYS
    assert declared == ("attempts", "last_status"), (
        f"the declared buyer-visible detail keys changed to {declared} — "
        "every migrated call site's envelope shifts with them"
    )
    # Graded through ``to_wire()``, which IS the buyer-visible projection
    # ``_DETAIL_KEYS`` is a promise about. This read used to be ``tuple(error.details)``,
    # which yielded keys only while ``details`` was a bare dict; it is now a declared
    # ``OutboundDeliveryDetails``, over which ``tuple()`` yields (name, value) pairs for
    # every inherited field. The obligation is unchanged and is now graded twice — at the
    # projection here, and at the fully serialized envelope on the next line.
    assert tuple(error.details.to_wire()) == declared, (
        f"details keys {tuple(error.details.to_wire())} do not match {declared}"
    )
    assert tuple(envelope["errors"][0]["details"]) == declared, (
        f"wire details keys {tuple(envelope['errors'][0]['details'])} do not match {declared}: {envelope}"
    )


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
def test_a_transport_failure_after_a_rate_limit_carries_no_stale_retry_after(seam_call, monkeypatch, local_origin_tls):
    """A hang-up on the last attempt reports no retry_after, not the previous answer's.

    ``last_status`` is already reset to None when an attempt dies at the
    transport level — there was no response to read a status from — and the
    Retry-After tracked beside it has to be reset in the same branch. Carrying
    the previous attempt's value would tell the buyer to wait 30 seconds because
    of a 429 that is no longer the failure being reported.
    """
    set_flags(monkeypatch, private=True)
    set_backoff_base(monkeypatch, 0.001)
    local_origin_tls.respond_in_sequence(
        [
            responds(429, body=_RATE_LIMITED_BODY, headers={"Retry-After": "30"}),
            hangs_up(),
        ]
    )
    record_sleeps(monkeypatch, seam_call)
    pin_jitter(monkeypatch, 0.0)

    error = assert_delivery_failed(seam_call, f"{local_origin_tls.base_url}/webhook", max_attempts=2)

    assert error.http_status is None
    assert error.retry_after is None, (
        f"the 429's Retry-After survived into a transport-level failure: {error.retry_after!r}"
    )
    assert local_origin_tls.hits == 2


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
def test_a_rate_limit_without_the_header_carries_no_retry_after(seam_call, monkeypatch, local_origin_tls):
    """No header means no value — the seam does not invent one from its own schedule.

    The code this ticket deletes fell back to ``2 ** attempt`` when the header
    was absent, which handed the buyer a number the origin never said. An absent
    ``retry_after`` is honest: ``core/error.json`` makes it optional.
    """
    set_flags(monkeypatch, private=True)
    set_backoff_base(monkeypatch, 0.001)
    rate_limited(local_origin_tls)
    durations = record_sleeps(monkeypatch, seam_call)
    pin_jitter(monkeypatch, 0.0)

    error = assert_delivery_failed(seam_call, f"{local_origin_tls.base_url}/webhook", max_attempts=2)
    envelope = envelope_for(error)

    assert error.retry_after is None, f"a retry_after appeared with no Retry-After header: {error.retry_after!r}"
    assert "retry_after" not in envelope["errors"][0], f"errors[0] carries an invented retry_after: {envelope}"
    assert durations == pytest.approx([0.001]), (
        f"expected the plain geometric wait when no header was sent, got {durations}"
    )


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
@pytest.mark.parametrize("header", ["Wed, 21 Oct 2026 07:28:00 GMT", "soon", ""])
def test_an_unparseable_retry_after_is_treated_as_absent(seam_call, header, monkeypatch, local_origin_tls):
    """A date form, or junk, leaves the schedule alone instead of crashing the fetch.

    RFC 9110 allows delta-seconds OR an HTTP-date. The seam parses delta-seconds
    only — an HTTP-date drags clock-skew policy into the one module that must not
    grow policy — and the limitation is stated rather than half-implemented. The
    code this ticket deletes called ``int()`` on the raw header, so a spec-legal
    date form raised ``ValueError`` out of an ``except httpx`` branch and crashed the
    fetch: this is a latent bug fix, not only a migration.
    """
    set_flags(monkeypatch, private=True)
    set_backoff_base(monkeypatch, 0.001)
    rate_limited(local_origin_tls, retry_after=header)
    durations = record_sleeps(monkeypatch, seam_call)
    pin_jitter(monkeypatch, 0.0)

    error = assert_delivery_failed(seam_call, f"{local_origin_tls.base_url}/webhook", max_attempts=2)

    assert error.retry_after is None, f"{header!r} was parsed into a retry_after: {error.retry_after!r}"
    assert durations == pytest.approx([0.001]), f"expected the plain geometric wait for {header!r}, got {durations}"


# ---------------------------------------------------------------------------
# 14. The terminal-client-error predicate — WHICH 4xx the seam refused to retry
# ---------------------------------------------------------------------------
#
# ``terminal_client_error_status`` lives in this module (``outbound_http.py``),
# and the fact it asserts is a property of THIS module: a 4xx is "terminal" only
# if it is absent from ``_RETRYABLE_STATUSES``. 429 is the counter-example that
# makes the distinction load-bearing — it is a 4xx the seam retries to
# exhaustion, so a caller keying off ``400 <= status < 500`` logs "will not
# retry" about a request that was just retried three times.
#
# Graded here, against real origin responses, so the predicate and the retry set
# cannot drift apart in two files; and reusing this suite's origin, flag and
# backoff helpers rather than restating them in a second module.


def _terminal_client_error_status():
    """Import the predicate lazily, for the same reason as :func:`_seam`."""
    from src.core.security.outbound_http import terminal_client_error_status

    return terminal_client_error_status


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
def test_a_4xx_the_seam_did_not_retry_reports_its_status(seam_call, monkeypatch, local_origin_tls):
    """A 404 the seam gave up on after one attempt IS a terminal client error.

    ``attempts == 1`` is asserted alongside the predicate on purpose: it is what
    makes "will not retry" a true sentence about this failure rather than a
    guess, and it is the half a status-range test cannot see.
    """
    set_flags(monkeypatch, private=True)
    local_origin_tls.respond_with(404, body=b'{"error": "no such hook"}')

    error = assert_delivery_failed(seam_call, f"{local_origin_tls.base_url}/webhook", max_attempts=3)

    assert error.attempts == 1, f"the seam retried a 404 {error.attempts} times"
    assert _terminal_client_error_status()(error) == 404


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
def test_a_rate_limited_4xx_is_never_reported_as_terminal(seam_call, monkeypatch, local_origin_tls):
    """A 429 is a 4xx the seam RETRIES, so it is not a terminal client error.

    This is the case a hand-written ``400 <= status < 500`` at a call site gets
    wrong, and no operator can catch it from the outside: the log would read
    "returned client error 429, will not retry" about a delivery the seam had
    just attempted three times. The origin really answers 429 and the hit count
    really is 3, so the contradiction is observed, not assumed.
    """
    set_flags(monkeypatch, private=True)
    fast_backoff(monkeypatch)
    rate_limited(local_origin_tls)

    error = assert_delivery_failed(seam_call, f"{local_origin_tls.base_url}/webhook", max_attempts=3)

    assert error.http_status == 429
    assert error.attempts == 3
    assert local_origin_tls.hits == 3, "the seam did not actually retry the 429"
    assert _terminal_client_error_status()(error) is None, (
        "a 429 the seam retried three times was reported as a terminal client error"
    )


def test_every_status_the_seam_retries_is_reported_as_not_terminal():
    """The predicate is keyed on the seam's OWN retry set, not a copied range.

    The two origin-driven cases above grade one member each. This grades the
    key: every status in ``_RETRYABLE_STATUSES``, whatever that set becomes,
    must answer ``None`` — which is only true if the predicate reads the set
    rather than restating a status range that happens to agree with it today.
    """
    seam = _seam()
    predicate = _terminal_client_error_status()

    for status in sorted(seam._RETRYABLE_STATUSES):
        error = seam.OutboundDeliveryFailed(attempts=3, http_status=status)
        assert predicate(error) is None, f"status {status} is retried by the seam but was reported as terminal"


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
def test_a_refusal_is_not_a_client_error(seam_call, monkeypatch):
    """A URL refused before connecting has no status to be terminal about."""
    set_flags(monkeypatch, private=True)

    error = assert_blocked(seam_call, METADATA_URL)

    assert _terminal_client_error_status()(error) is None


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
def test_a_transport_failure_carries_no_client_error_status(seam_call, monkeypatch, local_origin_tls):
    """A failure with no response at all reports no client error, rather than crashing.

    ``http_status`` is ``None`` here, which is the input a predicate written as
    a bare comparison raises ``TypeError`` on.
    """
    set_flags(monkeypatch, private=True)
    fast_backoff(monkeypatch)
    local_origin_tls.close_without_responding()

    error = assert_delivery_failed(seam_call, f"{local_origin_tls.base_url}/webhook", max_attempts=2)

    assert error.http_status is None
    assert _terminal_client_error_status()(error) is None


# ---------------------------------------------------------------------------
# 15. ONE retry state machine, stepped by both loops (GH #1802)
#
# Every section above grades the retry policy's arithmetic: how many attempts,
# how long between them, what an origin's Retry-After may do to that. This one
# grades where that policy LIVES. ``send`` and ``asend`` each carried their own
# copy of the post-response fork — retry / succeed / give up — so the two paths
# could agree today and drift tomorrow with nothing failing: every case above is
# parametrised over both paths and would keep passing against two separate
# implementations that happen to match.
#
# ``src.core.security.egress.attempts.Attempts`` is the one machine both loops
# step. These cases grade it THROUGH the seam and never in isolation: the
# machine is pure, so a direct test of it would say nothing about which loop
# obeys it. Each case therefore records the machine's own decisions as well as
# the outcome, because the outcome alone cannot tell the two shapes apart — a
# loop that decided for itself raises exactly the same error and records no
# decisions at all.
#
# Sleeps are recorded rather than served, as section 13 does, and for a sharper
# reason here: these origins send a real ``Retry-After``, so a case that reached
# an actual sleep would wait the origin's number out for real.
# ---------------------------------------------------------------------------


def _machine():
    """Import the retry state machine lazily, mirroring :func:`_seam`.

    Lazy for ``_seam``'s reason — a module-level import would turn "the machine
    does not exist yet" into a collection error for the whole file instead of an
    independently diagnosable failure per case.

    Imported from ``egress.attempts`` rather than through the seam's re-exporting
    facade on purpose: the lane's claim is that the retry policy has a home of
    its own, and a helper that reached it as ``outbound_http.Attempts`` would go
    on passing the day it moved back into the driver.
    """
    from src.core.security.egress.attempts import Attempts

    return Attempts


def record_machine_run(monkeypatch, seam_call: str, *, base: float) -> tuple[list[float], list]:
    """Set up this section's two observation points: the waits, and the decisions.

    The waits come from the recorders section 6 and 13 already use. The
    decisions come from wrapping the machine's own response fork — a RECORDER,
    not a stub: the real method runs and its verdict is returned untouched,
    exactly as ``pin_jitter`` wraps the real ``uniform`` and ``record_sleeps``
    wraps the real sleep. What it adds is the one fact a raised
    ``OutboundDeliveryFailed`` cannot show, which is that the decision was taken
    by the shared machine at all.

    The patch is on the CLASS, not on an instance, because the loop constructs
    its own ``Attempts`` and a test never gets to see the object — which is also
    why this grades both paths from outside without either loop knowing.

    ``base`` is stated explicitly for ``fast_backoff``'s reason: an unstated base is
    decided elsewhere, and would silently change the magnitudes these cases assert.
    """
    set_backoff_base(monkeypatch, base)
    durations = record_sleeps(monkeypatch, seam_call)
    pin_jitter(monkeypatch, 0.0)

    machine = _machine()
    decide = machine.record_response
    decisions: list = []

    def _recording(self, *args, **kwargs):
        outcome = decide(self, *args, **kwargs)
        decisions.append(outcome)
        return outcome

    monkeypatch.setattr(machine, "record_response", _recording)
    return durations, decisions


# One scripted 429, both directions of the same comparison in a single run. The
# geometric wait CROSSES the origin's Retry-After between the first sleep and
# the second — 4s is below the header, 8s is above it — so one origin grades
# both halves of section 13's asymmetry against one machine: the header
# lengthens the wait it exceeds, and the wait it does not exceed is left alone.
#
# Spelled as literals rather than derived from the base: a test that recomputed
# ``max(base * 2 ** step, header)`` would restate the implementation it grades,
# and would agree with a machine that had the ordering backwards.
_CROSSING_BASE = 4.0
_CROSSING_RETRY_AFTER = "6"
_CROSSING_WAITS = [6.0, 8.0]

# The rate-limited answer that precedes every multi-branch sequence below. The
# header is the state a later attempt must not inherit: 30 seconds is a number
# the buyer would act on, and it belongs to a 429 that is not the failure being
# reported by the time these sequences end.
_STALE_RETRY_AFTER = "30"


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
def test_one_machine_lengthens_a_wait_and_leaves_the_next_one_alone(seam_call, monkeypatch, local_origin_tls):
    """A 6s Retry-After raises the 4s wait and does not cut the 8s one — same machine, both paths.

    Section 13 grades each direction of BR-RULE-029's asymmetry against its own
    origin. This grades both against one, in one run, so a machine that applied
    the ceiling and the floor in the wrong order cannot pass by getting one
    origin's case right: the crossing point is between the two sleeps.

    The decisions are asserted alongside the durations because the durations
    alone do not say who computed them. Three 429s are three retry decisions and
    two waits — the third attempt exhausts ``max_attempts`` and is never slept.
    """
    set_flags(monkeypatch, private=True)
    rate_limited(local_origin_tls, retry_after=_CROSSING_RETRY_AFTER)
    durations, decisions = record_machine_run(monkeypatch, seam_call, base=_CROSSING_BASE)

    error = assert_delivery_failed(seam_call, f"{local_origin_tls.base_url}/webhook", max_attempts=3)

    outcome = _machine().Outcome
    assert decisions == [outcome.RETRY, outcome.RETRY, outcome.RETRY], (
        f"expected every 429 to be one RETRY decision on the shared machine, got {decisions}"
    )
    assert durations == pytest.approx(_CROSSING_WAITS), (
        f"expected the {_CROSSING_RETRY_AFTER}s Retry-After to lengthen the first wait and leave the "
        f"second alone ({_CROSSING_WAITS}), got {durations}"
    )
    assert error.attempts == 3


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
def test_a_size_cap_abort_after_a_rate_limit_carries_no_stale_retry_after(seam_call, monkeypatch, local_origin_tls):
    """The abort reports the attempt it died on and NO retry_after — not the earlier 429's.

    The size cap aborts mid-read, before there is any status decision to take,
    so the state the machine is holding at that moment is the PREVIOUS attempt's:
    a 429 that asked for 30 seconds. Reporting that number here would tell the
    buyer to come back in 30s because of a rate limit that is not the failure
    being reported — the same drift
    ``test_a_transport_failure_after_a_rate_limit_carries_no_stale_retry_after``
    closes one branch over, and wire-visible for the same reason (it is clamped
    to the spec's [1, 3600] and emitted).

    ``retry_after`` is asserted for exactly that reason: ``attempts`` and
    ``http_status`` are identical whether or not the stale value leaks, so a case
    that graded only those two would let it ship.

    The empty decision record is the other half: an aborted read is not a status
    the machine may read as success or as retryable, so it must never reach the
    response fork at all — it is named as its own event.
    """
    set_flags(monkeypatch, private=True)
    local_origin_tls.respond_in_sequence(
        [
            responds(429, body=_RATE_LIMITED_BODY, headers={"Retry-After": _STALE_RETRY_AFTER}),
            sends_chunked_body(_seam()._MAX_RESPONSE_BYTES + 1),
        ]
    )
    _durations, decisions = record_machine_run(monkeypatch, seam_call, base=0.001)

    error = assert_delivery_failed(seam_call, f"{local_origin_tls.base_url}/webhook", max_attempts=3)

    assert error.attempts == 2
    assert error.http_status == 200
    assert error.retry_after is None, (
        f"the first attempt's Retry-After: {_STALE_RETRY_AFTER} survived into a size-cap abort as "
        f"{error.retry_after!r} — the buyer would be told to wait for a 429 that is not this failure"
    )
    assert local_origin_tls.hits == 2, (
        f"the oversized body was retried: a body too large now is too large on a retry too, "
        f"but the origin was reached {local_origin_tls.hits} times"
    )
    outcome = _machine().Outcome
    assert decisions == [outcome.RETRY], (
        f"expected only the 429 to reach the response fork — an aborted read has no status verdict — got {decisions}"
    )


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
def test_both_paths_walk_retry_retry_terminal_through_the_same_machine(seam_call, monkeypatch, local_origin_tls):
    """503, then 429, then 404: two retries and a terminal branch, identically on both paths.

    One origin exercising two of the machine's three branches in sequence, at
    ``max_attempts=5`` so that what stops the walk is the TERMINAL decision and
    not exhaustion — with the attempt budget spent, a machine that never routed
    to the terminal branch would look the same from outside.

    ``retry_after`` is None on the reported failure even though attempt 2 sent
    one: the value belongs to the response being reported, and the 404 sent
    none. A machine that carried its state forward instead of rewriting it per
    response reports the 429's 30s here.
    """
    set_flags(monkeypatch, private=True)
    local_origin_tls.respond_in_sequence(
        [
            responds(503, body=b'{"error": "unavailable"}'),
            responds(429, body=_RATE_LIMITED_BODY, headers={"Retry-After": _STALE_RETRY_AFTER}),
            responds(404, body=b'{"error": "no such hook"}'),
        ]
    )
    _durations, decisions = record_machine_run(monkeypatch, seam_call, base=0.001)

    error = assert_delivery_failed(seam_call, f"{local_origin_tls.base_url}/webhook", max_attempts=5)

    outcome = _machine().Outcome
    assert decisions == [outcome.RETRY, outcome.RETRY, outcome.TERMINAL], (
        f"expected the shared machine to decide retry, retry, terminal for 503/429/404, got {decisions}"
    )
    assert error.attempts == 3
    assert error.http_status == 404
    assert error.retry_after is None, (
        f"the 429's Retry-After: {_STALE_RETRY_AFTER} survived into the 404 that actually failed: {error.retry_after!r}"
    )
    assert local_origin_tls.hits == 3, f"the terminal 404 was retried: {local_origin_tls.hits} hits"


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
def test_both_paths_walk_retry_retry_success_through_the_same_machine(seam_call, monkeypatch, local_origin_tls):
    """503, then 429, then 200: the same two retries and the machine's third branch.

    The sibling of the terminal walk above, and the reason the fork has three
    branches rather than a boolean: "not retryable" splits into a delivered response
    and a failure, and a machine that returned the terminal branch where success is
    due would fail every migrated call site's happy path. Graded on the same
    scripted prefix so the two cases differ only in the final answer.
    """
    set_flags(monkeypatch, private=True)
    local_origin_tls.respond_in_sequence(
        [
            responds(503, body=b'{"error": "unavailable"}'),
            responds(429, body=_RATE_LIMITED_BODY, headers={"Retry-After": _STALE_RETRY_AFTER}),
            responds(200, body=b'{"ok": true}'),
        ]
    )
    _durations, decisions = record_machine_run(monkeypatch, seam_call, base=0.001)

    result = call_seam(seam_call, f"{local_origin_tls.base_url}/webhook", max_attempts=5)

    outcome = _machine().Outcome
    assert decisions == [outcome.RETRY, outcome.RETRY, outcome.SUCCESS], (
        f"expected the shared machine to decide retry, retry, success for 503/429/200, got {decisions}"
    )
    assert result.http_status == 200
    assert result.json() == {"ok": True}
    assert result.attempts == 3
    assert local_origin_tls.hits == 3, f"the recovered request was sent {local_origin_tls.hits} times"


# ---------------------------------------------------------------------------
# Per-attempt signing (GH #1441)
#
# The seam owns retry, so a signature computed once ABOVE the loop is replayed
# on every attempt. That is valid for a legacy HMAC (body + timestamp, replay-
# valid inside its window) and invalid for RFC 9421, whose ``nonce`` a
# conformant receiver MUST reject on replay (adcp's own conformance suite ships
# ``negative/016-replayed-nonce.json``). The hook exists so a signing caller can
# stay on this seam's pinned transport instead of bringing its own client.
# ---------------------------------------------------------------------------


def _counting_signer() -> tuple[Any, list[tuple[str, str, bytes]]]:
    """A signer that records what it was asked to sign and never repeats itself.

    The header value embeds the call index, standing in for RFC 9421's nonce:
    two attempts carrying the same value is exactly the defect this hook exists
    to prevent, and an index makes that visible on the wire.

    Keyword-only, because that is the shape the real consumer has
    (``adcp.webhook_auth.WebhookAuthStrategy.build_auth_headers``). A stand-in
    spelled differently from every actual signer would grade a calling
    convention nobody uses — which is how the mismatch stayed invisible.
    """
    seen: list[tuple[str, str, bytes]] = []

    def sign(*, method: str, url: str, body: bytes) -> dict[str, str]:
        seen.append((method, url, body))
        return {"Signature-Input": f'sig1=();nonce="n{len(seen)}"', "Signature": f"sig1=:s{len(seen)}:"}

    return sign, seen


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
def test_each_retry_carries_a_distinct_signature_on_the_wire(seam_call, monkeypatch, local_origin_tls):
    """Three attempts produce three DIFFERENT signatures at the origin.

    Asserted on what the origin received, not on how many times the callback
    ran: a signer invoked per attempt whose headers were merged once would still
    replay, and only the wire shows the difference.
    """
    set_flags(monkeypatch, private=True)
    fast_backoff(monkeypatch)
    local_origin_tls.respond_with(503, body=b'{"error": "unavailable"}')
    sign, seen = _counting_signer()

    assert_delivery_failed(seam_call, f"{local_origin_tls.base_url}/webhook", json={"a": 1}, max_attempts=3, sign=sign)

    assert local_origin_tls.hits == 3
    assert len(seen) == 3
    on_wire = [r.headers["Signature"] for r in local_origin_tls.requests]
    assert len(set(on_wire)) == 3, f"a signature was replayed across retries: {on_wire}"


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
def test_the_signer_is_given_the_exact_bytes_that_go_on_the_wire(seam_call, monkeypatch, local_origin_tls):
    """Signed bytes and transmitted bytes are one object, not two that agree.

    This is the same invariant GH #1095 established for the webhook
    body: a signer handed the pre-serialization payload would be trusting httpx
    to serialize it the same way, which is how #1441 happened.
    """
    set_flags(monkeypatch, private=True)
    local_origin_tls.respond_with(200)
    sign, seen = _counting_signer()

    call_seam(
        seam_call,
        f"{local_origin_tls.base_url}/webhook",
        json={"z": 1, "a": 2},
        max_attempts=1,
        sign=sign,
    )

    signed_method, signed_url, signed_body = seen[0]
    received = local_origin_tls.requests[0]
    assert signed_body == received.body
    assert signed_method == "POST"
    assert signed_url == f"{local_origin_tls.base_url}/webhook"


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
def test_the_signer_sees_the_target_uri_after_params_are_applied(seam_call, monkeypatch, local_origin_tls):
    """RFC 9421 covers @target-uri, so the signer must see the FINAL URL.

    Signing the pre-params URL would produce a signature over a URI the origin
    never sees, which no receiver can verify.
    """
    set_flags(monkeypatch, private=True)
    local_origin_tls.respond_with(200)
    sign, seen = _counting_signer()

    call_seam(
        seam_call,
        f"{local_origin_tls.base_url}/webhook",
        json={"a": 1},
        params={"tenant": "acme"},
        max_attempts=1,
        sign=sign,
    )

    _, signed_url, _ = seen[0]
    assert signed_url.endswith("/webhook?tenant=acme")
    assert local_origin_tls.requests[0].path.endswith("/webhook?tenant=acme")


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
def test_omitting_the_signer_changes_nothing(seam_call, monkeypatch, local_origin_tls):
    """The hook is opt-in: an unsigned send carries no signature headers."""
    set_flags(monkeypatch, private=True)
    local_origin_tls.respond_with(200)

    call_seam(seam_call, f"{local_origin_tls.base_url}/webhook", json={"a": 1}, max_attempts=1)

    headers = local_origin_tls.requests[0].headers
    assert "Signature" not in headers
    assert "Signature-Input" not in headers


# ---------------------------------------------------------------------------
# The REAL consumer shape (GH #1441)
#
# Everything above drives a hand-rolled signer, which grades the seam's
# mechanics but cannot grade the thing this hook exists for: that an ACTUAL
# ``adcp`` signer can be handed to the seam and produce a delivery a conformant
# receiver accepts. The acceptance criterion is "no caller needs to supply its
# own httpx client to sign" — a caller forced to hand-write a positional-to-
# keyword shim around ``build_auth_headers`` has not been served, so these cases
# pass ``sign=strategy.build_auth_headers`` with NO adapter of any kind.
#
# They also assert VERIFIABILITY, not presence. A ``Signature`` header that is
# on the wire but whose base no receiver can rebuild is the exact failure the
# Content-Type case below pins, and a presence check is blind to it.
# ---------------------------------------------------------------------------

# The receiver's own scheme. A receiver knows what it is listening on; it does
# not learn it from the request. The origin fixture serves real TLS, so https.
_RECEIVER_SCHEME = "https"


def _adcp_webhook_signer() -> tuple[JwkSignerStrategy, dict[str, Any]]:
    """A real RFC 9421 webhook signer from the installed SDK, and its public JWK.

    ``purpose="webhook-signing"`` is not decoration: the webhook verifier
    enforces the JWK's ``adcp_use`` claim, so a request-signing key would
    produce a signature a conformant receiver refuses for a reason that has
    nothing to do with this seam.
    """
    pem, jwk = generate_signing_keypair(alg="ed25519", kid="seam-test-key", purpose="webhook-signing")
    strategy = JwkSignerStrategy(
        private_key=load_private_key_pem(pem),
        key_id=jwk["kid"],
        alg=alg_for_jwk(jwk),
    )
    return strategy, jwk


def _rebuild_signature_base(received) -> str:
    """Rebuild the RFC 9421 signature base the way the RECEIVER does.

    From the wire headers and the effective request URI reconstructed out of
    ``Host`` and the request target — never from anything the sender kept on its
    own side. That is the whole point: a signature is only worth something if it
    can be reconstructed from what actually crossed the socket.

    Raises ``ValueError`` when ``Signature-Input`` covers a component the wire
    does not carry — which is how a missing ``Content-Type`` surfaces.
    """
    url = f"{_RECEIVER_SCHEME}://{received.headers['Host']}{received.path}"
    parsed = parse_signature_input_header(received.headers["Signature-Input"])["sig1"]
    return build_signature_base(received.method, url, received.headers, parsed)


def assert_receiver_can_verify(received, jwk: dict[str, Any]) -> None:
    """Assert a conformant receiver accepts ``received`` — base, signature, digest.

    Three separate obligations, all graded on wire bytes: the base rebuilds from
    the headers that arrived, the signature validates against the published
    public key over that base, and the covered ``Content-Digest`` matches the
    body the origin actually read off the socket.
    """
    base = _rebuild_signature_base(received)
    assert verify_signature(
        alg=alg_for_jwk(jwk),
        public_key=public_key_from_jwk(jwk),
        signature_base=base.encode(),
        signature=extract_signature_bytes(received.headers["Signature"]),
    ), f"the signature does not verify over the base rebuilt from the wire:\n{base}"
    assert content_digest_matches(received.headers["Content-Digest"], received.body), (
        "Content-Digest does not cover the body the origin received"
    )


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
def test_an_adcp_signer_is_accepted_with_no_adapter_and_the_receiver_verifies(seam_call, monkeypatch, local_origin_tls):
    """``sign=strategy.build_auth_headers``, passed straight through, verifies.

    This one case is the ticket's acceptance criterion. ``build_auth_headers``
    is keyword-only (``*, method, url, body``) in adcp==6.6.0, so the seam's
    callback type has to BE that shape rather than restate it positionally —
    otherwise every signing caller writes a shim, and a caller writing a shim is
    a caller the hook did not serve.
    """
    set_flags(monkeypatch, private=True)
    local_origin_tls.respond_with(200)
    strategy, jwk = _adcp_webhook_signer()

    call_seam(
        seam_call,
        f"{local_origin_tls.base_url}/webhook",
        json={"event": "delivery"},
        max_attempts=1,
        sign=strategy.build_auth_headers,
    )

    assert local_origin_tls.hits == 1
    assert_receiver_can_verify(local_origin_tls.requests[0], jwk)


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
def test_every_retry_of_an_adcp_signed_send_is_independently_verifiable(seam_call, monkeypatch, local_origin_tls):
    """Three attempts, three signatures, each one verifiable on its own.

    The distinct-nonce assertion is the ticket's reason for existing (adcp ships
    ``negative/016-replayed-nonce.json``; a conformant receiver MUST reject a
    replay), and verifying each attempt separately is what proves the freshness
    did not come at the cost of a signature that no longer matches its request —
    a per-attempt nonce over a stale base would still be dead on arrival.
    """
    set_flags(monkeypatch, private=True)
    fast_backoff(monkeypatch)
    local_origin_tls.respond_with(503, body=b'{"error": "unavailable"}')
    strategy, jwk = _adcp_webhook_signer()

    assert_delivery_failed(
        seam_call,
        f"{local_origin_tls.base_url}/webhook",
        json={"event": "delivery"},
        max_attempts=3,
        sign=strategy.build_auth_headers,
    )

    assert local_origin_tls.hits == 3
    for received in local_origin_tls.requests:
        assert_receiver_can_verify(received, jwk)
    nonces = [
        parse_signature_input_header(r.headers["Signature-Input"])["sig1"].params["nonce"]
        for r in local_origin_tls.requests
    ]
    assert len(set(nonces)) == 3, f"a nonce was replayed across retries: {nonces}"


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
def test_a_content_body_verifies_when_the_caller_declares_its_content_type(seam_call, monkeypatch, local_origin_tls):
    """``content=`` works — provided the caller says what the bytes are.

    ``JwkSignerStrategy`` covers ``content-type`` unconditionally in its base,
    and httpx sets that header on the ``json=`` path but NOT on ``content=``.
    So a ``content=`` caller owes the seam an explicit ``Content-Type``. That is
    a PAYLOAD-layer decision, which is exactly why the seam must not invent one
    on the caller's behalf — the same reason ``webhook_egress`` does its own
    ``setdefault("Content-Type", "application/json")`` at the payload layer.
    """
    set_flags(monkeypatch, private=True)
    local_origin_tls.respond_with(200)
    strategy, jwk = _adcp_webhook_signer()
    payload = json.dumps({"event": "delivery"}).encode()

    call_seam(
        seam_call,
        f"{local_origin_tls.base_url}/webhook",
        content=payload,
        headers={"Content-Type": "application/json"},
        max_attempts=1,
        sign=strategy.build_auth_headers,
    )

    received = local_origin_tls.requests[0]
    assert received.body == payload
    assert_receiver_can_verify(received, jwk)


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
def test_a_content_body_without_a_content_type_signs_a_header_that_never_ships(
    seam_call, monkeypatch, local_origin_tls
):
    """The trap, pinned: the signature ships, and no receiver can rebuild it.

    ``content=`` without an explicit ``Content-Type`` leaves the header off the
    wire while the signer has already covered it, so the delivery LOOKS signed
    and is undeliverable. Pinned as an executable obligation rather than left as
    prose in a docstring, because a presence check on ``Signature`` — the
    obvious assertion to reach for — passes here.
    """
    set_flags(monkeypatch, private=True)
    local_origin_tls.respond_with(200)
    strategy, _ = _adcp_webhook_signer()

    call_seam(
        seam_call,
        f"{local_origin_tls.base_url}/webhook",
        content=json.dumps({"event": "delivery"}).encode(),
        max_attempts=1,
        sign=strategy.build_auth_headers,
    )

    received = local_origin_tls.requests[0]
    assert "Signature" in received.headers
    assert "Content-Type" not in received.headers
    with pytest.raises(ValueError, match="missing header for covered component: content-type"):
        _rebuild_signature_base(received)


@pytest.mark.parametrize("seam_call", SEAM_CALLS)
def test_a_signer_returning_a_reserved_header_cannot_desync_the_body(seam_call, monkeypatch, local_origin_tls):
    """A signer's headers may not re-frame the message (GH #1441, ADJUST 4).

    ``Transfer-Encoding: chunked`` merged verbatim next to the ``Content-Length``
    httpx computed makes the origin read the chunk-size line as body: it receives
    ``b'8\\r\\n{"eve'`` instead of the bytes that were signed. That is the CL.TE
    shape, produced here by a signer rather than an attacker — and the seam is
    the only layer that sees both the framing headers and the signer's mapping.

    The framing headers are the transport's, not the signer's: adcp's own
    ``WebhookAuthStrategy`` declares ``content-length``/``content-type``/``host``
    reserved for precisely this reason. The assertion is on the OUTCOME (the
    origin reads the signed bytes) rather than on a mechanism, so filtering the
    reserved names and refusing the send both satisfy it — but silently merging
    them does not.
    """
    set_flags(monkeypatch, private=True)
    local_origin_tls.respond_with(200)
    strategy, jwk = _adcp_webhook_signer()
    payload = json.dumps({"event": "delivery"}).encode()

    def sign_and_reframe(*, method: str, url: str, body: bytes) -> dict[str, str]:
        return {**strategy.build_auth_headers(method=method, url=url, body=body), "Transfer-Encoding": "chunked"}

    call_seam(
        seam_call,
        f"{local_origin_tls.base_url}/webhook",
        content=payload,
        headers={"Content-Type": "application/json"},
        max_attempts=1,
        sign=sign_and_reframe,
    )

    received = local_origin_tls.requests[0]
    assert received.body == payload, "a signer's header re-framed the body the origin read"
    assert_receiver_can_verify(received, jwk)
