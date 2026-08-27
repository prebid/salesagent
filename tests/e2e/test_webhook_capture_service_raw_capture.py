"""Contract: the capture service records the WIRE, and answers what proves control.

The request-handling contract of ``tests/e2e/webhook_capture_service.py``, in two
layers: raw-wire capture (salesagent-mp53.9, landed) and the proof-of-control ECHO
mode (salesagent-mp53.4, the TDD red at the bottom of this module). It runs the
service IN-PROCESS on loopback (no Docker, no compose network, no tls-proxy),
because the claims here are about this module's own request handling, not about
the stack wiring (that is
``tests/unit/test_architecture_e2e_webhook_capture_wiring.py`` and
``tests/e2e/test_webhook_capture_egress_gate_e2e.py``) and not about what the
server does with the answer (``tests/e2e/test_notification_proof_challenge_e2e.py``).

**Why this module exists at all, given the sibling branch already has a service.**
The version being ported (``feat/secure-outbound-fetch``) parses each request
body with ``json.loads`` and stores the resulting dict — it DISCARDS the request
headers and the exact bytes. That is fine on that branch and fatal on this one:
this branch's in-process receiver records ``received_raw`` as
``(path, headers, body_bytes)`` (``tests/e2e/_webhook_capture.py``, #1291 C1),
and ``tests/e2e/test_webhook_signature_e2e.py`` grades RFC 9421 signatures out of
it. A signature is computed over the BYTES the sender emitted and over the
``Signature`` / ``Signature-Input`` / ``Content-Digest`` headers; a service that
returns a re-serialized dict has thrown away the entire subject of that test,
and it would do so SILENTLY — the payload assertions would keep passing while
signature verification failed as ``webhook_signature_invalid``, the failure mode
most likely to be misread as a crypto bug.

**The contract this pins**, chosen to be additive so the ported consumers keep
working unchanged: ``GET``/``DELETE /webhook/<key>`` answer with BOTH

* ``received``     — the parsed JSON payloads, exactly as the sibling branch's
  service already returns them; and
* ``received_raw`` — one entry per request, ``{"path", "headers", "body_b64"}``,
  reconstituting this branch's ``(path, headers, body_bytes)`` tuple across an
  HTTP readback hop (base64 because the bytes are not necessarily text, let
  alone JSON).
"""

from __future__ import annotations

import base64
import json
import uuid
from collections.abc import Iterator
from http.client import HTTPException, HTTPResponse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import httpx
import pytest

# ``webhook_path`` is the TDD red for salesagent-mp53.4 (the echo mode below): the
# service owns ONE spelling of its delivery/readback URL convention, and mp53.4 is
# what makes that convention carry a mode. ``run_capture_service`` was mp53.9's own
# red and exists now.
from tests.e2e.webhook_capture_service import run_capture_service, webhook_path

_TIMEOUT_SECONDS = 5.0

#: A realistic RFC 9421 header set. The values are opaque to the service — that
#: is the point: it must hand back what it was given, byte for byte, with no
#: normalization of its own.
_SIGNATURE_HEADERS = {
    "Signature": "sig1=:dGVzdC1zaWduYXR1cmUtYnl0ZXM=:",
    "Signature-Input": 'sig1=("@method" "@target-uri" "content-digest");created=1754400000;keyid="k1";alg="ed25519";tag="adcp-webhook-v1"',
    "Content-Digest": "sha-256=:X48E9qOokqqrvdts8nOJRJN3OWDUoyWxBf7kbu9DBPE=:",
}

#: Valid JSON whose exact byte sequence differs from any canonical
#: re-serialization: irregular spacing, a trailing newline, and a non-ASCII
#: character. A service that parses and re-encodes cannot reproduce this.
_UNCANONICAL_BODY = b'{"event":"delivery_report",  "media_buy_id" : "mb_1",\n "note": "caf\xc3\xa9"}\n'


def _request_raw(
    base_url: str,
    method: str,
    path: str,
    *,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, bytes]:
    """Issue one request against the running service; return ``(status, response_bytes)``.

    A dropped connection is reported as status ``0`` rather than raised, so an
    unhandled exception inside the service reads as a failed assertion about the
    contract instead of an opaque transport error in the test.

    The BYTES are what the echo cases below need: production hands the response body
    to the SDK's ``validate_webhook_challenge_response``, so a test that re-serialized
    a parsed dict would grade its own re-encoding rather than what the socket carried.
    """
    req = Request(f"{base_url}{path}", data=body, method=method)
    for name, value in (headers or {}).items():
        req.add_header(name, value)
    try:
        resp: HTTPResponse = urlopen(req, timeout=_TIMEOUT_SECONDS)  # noqa: S310 - test-only, loopback origin
        return resp.status, resp.read()
    except HTTPError as exc:
        return exc.code, exc.read()
    except (URLError, HTTPException):
        return 0, b""


def _request(
    base_url: str,
    method: str,
    path: str,
    *,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict]:
    """:func:`_request_raw` with the response decoded as JSON."""
    status, raw = _request_raw(base_url, method, path, body=body, headers=headers)
    return status, (json.loads(raw) if raw else {})


def _post(
    base_url: str,
    key: str,
    body: bytes,
    headers: dict[str, str] | None = None,
    *,
    echo: str | None = None,
) -> tuple[int, dict]:
    return _request(base_url, "POST", webhook_path(key, echo=echo), body=body, headers=headers)


def _get(base_url: str, key: str) -> tuple[int, dict]:
    return _request(base_url, "GET", webhook_path(key))


def _delete(base_url: str, key: str) -> tuple[int, dict]:
    return _request(base_url, "DELETE", webhook_path(key))


def _header(entry: dict, name: str) -> str | None:
    """Case-insensitive header lookup, the way every HTTP client treats header names."""
    lowered = name.lower()
    for key, value in entry["headers"].items():
        if key.lower() == lowered:
            return value
    return None


def _body_bytes(entry: dict) -> bytes:
    return base64.b64decode(entry["body_b64"])


@pytest.fixture
def capture_service(monkeypatch) -> Iterator[str]:
    """Run the capture service on an ephemeral loopback port for one test."""
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "mp539-raw-capture-test")
    with run_capture_service(host="127.0.0.1", port=0) as base_url:
        yield base_url


class TestRawBytesSurviveTheCaptureHop:
    """The exact bytes the sender emitted are readable back, unmodified.

    RFC 9421's ``content-digest`` covers the body bytes, so anything that
    re-serializes the payload changes the digest and every signature over it.
    """

    def test_readback_returns_the_posted_bytes_verbatim(self, capture_service):
        key = uuid.uuid4().hex

        post_status, _ = _post(capture_service, key, _UNCANONICAL_BODY, _SIGNATURE_HEADERS)
        get_status, body = _get(capture_service, key)

        assert post_status == 200
        assert get_status == 200
        assert [_body_bytes(entry) for entry in body["received_raw"]] == [_UNCANONICAL_BODY]

    def test_the_parsed_payload_is_returned_alongside_the_raw_bytes(self, capture_service):
        """``received`` keeps its existing shape — raw capture is additive, not a replacement."""
        key = uuid.uuid4().hex

        _post(capture_service, key, _UNCANONICAL_BODY, _SIGNATURE_HEADERS)
        _, body = _get(capture_service, key)

        assert body["received"] == [json.loads(_UNCANONICAL_BODY)]

    def test_a_body_that_is_not_json_is_still_captured_raw(self, capture_service):
        """Raw capture happens BEFORE parsing, so an unparseable body still explains itself.

        The in-process receiver this replaces states the ordering explicitly:
        ``record_raw`` "runs BEFORE :meth:`record`, so a handler whose ``record``
        rejects a payload still leaves the raw request available to explain why".
        Losing that turns a malformed-payload delivery into "no webhook arrived".
        """
        key = uuid.uuid4().hex
        garbage = b"\x00\x01 not json at all \xff"

        post_status, _ = _post(capture_service, key, garbage, {"Content-Type": "application/octet-stream"})
        get_status, body = _get(capture_service, key)

        assert post_status >= 400, "an unparseable body must be answered with an error, never a quiet 200"
        assert get_status == 200
        assert [_body_bytes(entry) for entry in body["received_raw"]] == [garbage]


class TestSigningHeadersSurviveTheCaptureHop:
    """The RFC 9421 headers arrive intact — they are half of what a signature covers."""

    @pytest.mark.parametrize("header_name", sorted(_SIGNATURE_HEADERS))
    def test_each_signing_header_is_returned_with_its_exact_value(self, capture_service, header_name):
        key = uuid.uuid4().hex

        _post(capture_service, key, _UNCANONICAL_BODY, _SIGNATURE_HEADERS)
        _, body = _get(capture_service, key)

        entry = body["received_raw"][0]
        assert _header(entry, header_name) == _SIGNATURE_HEADERS[header_name]

    def test_the_request_path_is_recorded(self, capture_service):
        """``@target-uri`` / ``@path`` are covered components; the receiver must know what was dialled."""
        key = uuid.uuid4().hex

        _post(capture_service, key, _UNCANONICAL_BODY, _SIGNATURE_HEADERS)
        _, body = _get(capture_service, key)

        assert body["received_raw"][0]["path"] == f"/webhook/{key}"


class TestRawCapturesAreKeyedAndDrainedLikePayloads:
    """Raw capture inherits the isolation and drain semantics the payload list already has."""

    def test_two_keys_never_see_each_others_raw_captures(self, capture_service):
        key_a, key_b = uuid.uuid4().hex, uuid.uuid4().hex

        _post(capture_service, key_a, b'{"for":"a"}', _SIGNATURE_HEADERS)
        _post(capture_service, key_b, b'{"for":"b"}', _SIGNATURE_HEADERS)

        _, body_a = _get(capture_service, key_a)
        _, body_b = _get(capture_service, key_b)

        assert [_body_bytes(e) for e in body_a["received_raw"]] == [b'{"for":"a"}']
        assert [_body_bytes(e) for e in body_b["received_raw"]] == [b'{"for":"b"}']

    def test_delete_drains_the_raw_captures_too(self, capture_service):
        """A drained key must not leave raw entries behind for the next test to trip over."""
        key = uuid.uuid4().hex
        _post(capture_service, key, _UNCANONICAL_BODY, _SIGNATURE_HEADERS)

        delete_status, drained = _delete(capture_service, key)
        _, after = _get(capture_service, key)

        assert delete_status == 200
        assert [_body_bytes(e) for e in drained["received_raw"]] == [_UNCANONICAL_BODY]
        assert after["received_raw"] == []


# ─────────────────────────────────────────────────────────────────────────────
# salesagent-mp53.4 — the ECHO mode.
#
# The proof-of-control challenge is proven by the receiver ECHOING the single-use
# value, not by a 2xx (``sync_accounts.mdx`` @ v3.1.1 :223-235: "The receiver
# proves control by returning HTTP 2xx with a JSON body containing exactly one
# echo field"). A 2xx alone is produced by every endpoint that accepts POSTs,
# including one an attacker pointed at us — so the e2e module's SUCCESS leg needs
# a receiver that echoes, and its non-echo CONTROL needs the same receiver NOT to.
#
# Both behaviours live on ONE service, selected per delivery URL, because both
# legs must reach the same origin: production applies its reserved-TLD and SSRF
# gates before it applies the refusal under test, so a control leg pointed at a
# different (unreachable, or plain-HTTP) receiver would grade the SSRF gate and
# report "zero captures" for a reason that has nothing to do with the echo.
#
# Graded in-process here — no Docker, no compose network, no tls-proxy — for the
# same reason as the classes above: these are claims about this module's own
# request handling. The claim that the SERVER reaches it over real HTTPS is
# ``tests/e2e/test_notification_proof_challenge_e2e.py``.
# ─────────────────────────────────────────────────────────────────────────────

#: Two distinct values, both legal under ``webhook-challenge.json``'s
#: ``^[A-Za-z0-9_.:-]{32,255}$``. Two rather than one on purpose: an echo that is
#: read out of the POSTed body and an echo that is hard-coded are indistinguishable
#: from a single sample.
_CHALLENGE_VALUES = (
    "mp534-challenge-value-aaaaaaaaaaaaaaaa",
    "mp534.challenge:value_bbbbbbbbbbbbbbbb",
)


def _challenge_delivery(value: str) -> bytes:
    """A challenge POST body carrying *value*.

    Only ``challenge`` is load-bearing for the receiver — that is the field the
    spec says it echoes. ``type`` rides along because it is what a real challenge
    carries and a receiver that keyed off it would be wrong in a way this test
    should catch.
    """
    return json.dumps({"type": "webhook.challenge", "challenge": value}).encode()


def _post_challenge(base_url: str, key: str, value: str, *, echo: str | None) -> tuple[int, bytes]:
    """POST one challenge and return ``(status, response_bytes)`` exactly as sent back."""
    return _request_raw(
        base_url,
        "POST",
        webhook_path(key, echo=echo),
        body=_challenge_delivery(value),
        headers={"Content-Type": "application/json"},
    )


class TestTheDeliveryPathCarriesTheMode:
    """``webhook_path`` is the ONE spelling of the URL convention.

    The e2e module builds a subscriber URL from it, this module builds its
    requests from it, and the service routes on it. A second spelling anywhere is
    how a receiver ends up capturing under a key nobody reads back.
    """

    def test_the_default_path_is_unchanged(self):
        key = uuid.uuid4().hex

        assert webhook_path(key) == f"/webhook/{key}"

    @pytest.mark.parametrize("field", ["challenge", "token"])
    def test_the_echo_mode_is_named_in_the_path(self, field):
        """The mode travels in the URL because the URL is all production is given.

        A seller POSTs to whatever ``notification_configs[].url`` it was handed;
        there is no side channel by which a test could put the receiver into echo
        mode for one delivery and not another.
        """
        key = uuid.uuid4().hex

        assert webhook_path(key, echo=field) == f"/webhook/{key}?echo={field}"


class TestTheEchoModeProvesControl:
    """With the mode on, the answer is the one that proves control."""

    @pytest.mark.parametrize("value", _CHALLENGE_VALUES)
    def test_the_answer_echoes_the_value_from_the_posted_body(self, capture_service, value):
        key = uuid.uuid4().hex

        status, raw = _post_challenge(capture_service, key, value, echo="challenge")

        assert status == 200
        assert json.loads(raw) == {"challenge": value}, (
            f"the echo must be read out of the POSTed body; the receiver answered {raw!r} for a challenge "
            f"value of {value!r}"
        )

    @pytest.mark.parametrize("value", _CHALLENGE_VALUES)
    def test_the_token_alias_is_answerable(self, capture_service, value):
        """``webhook-challenge-response.json`` permits ``challenge`` OR ``token``.

        Sellers MUST accept the backward-compatible alias (``sync_accounts.mdx`` @
        v3.1.1 :229-233), so a receiver has to be able to produce it or that clause
        is ungraded on a real socket.
        """
        key = uuid.uuid4().hex

        status, raw = _post_challenge(capture_service, key, value, echo="token")

        assert status == 200
        assert json.loads(raw) == {"token": value}

    @pytest.mark.parametrize("field", ["challenge", "token"])
    def test_the_answer_satisfies_the_check_production_actually_runs(self, capture_service, field):
        """Graded through the SDK call ``_response_proves_control`` makes, not a look-alike.

        ``notification_proof_service._response_proves_control`` hands the response
        BYTES to ``validate_webhook_challenge_response``. Asserting on that exact
        function is what makes this module's contract and production's proof rule
        the same rule rather than two that agree today.
        """
        from adcp.webhooks import validate_webhook_challenge_response

        key = uuid.uuid4().hex
        value = _CHALLENGE_VALUES[0]

        status, raw = _post_challenge(capture_service, key, value, echo=field)

        assert status == 200
        assert validate_webhook_challenge_response(raw, challenge=value) == field

    @pytest.mark.parametrize("field", ["challenge", "token"])
    def test_the_answer_carries_exactly_one_echo_field(self, capture_service, field):
        """``webhook-challenge-response.json`` has ``not: {required: [challenge, token]}``.

        Pinned here rather than trusted to the SDK: ``validate_webhook_challenge_response``
        accepts a body carrying BOTH (a recorded divergence — see
        ``notification_proof_service._response_proves_control``), so a receiver that
        emitted both would pass every SDK-mediated assertion while sending a document
        the schema forbids.
        """
        key = uuid.uuid4().hex

        _status, raw = _post_challenge(capture_service, key, _CHALLENGE_VALUES[0], echo=field)

        assert sorted(json.loads(raw)) == [field]

    def test_the_wire_is_still_recorded_in_echo_mode(self, capture_service):
        """Echoing does not cost the capture — the bytes are the signature's subject.

        The e2e success leg reads the RFC 9421 signature off this same delivery, so
        a receiver that answered the echo and dropped the wire would make the
        signature assertions vacuous rather than failing.
        """
        key = uuid.uuid4().hex
        value = _CHALLENGE_VALUES[0]

        _post_challenge(capture_service, key, value, echo="challenge")
        _, body = _get(capture_service, key)

        assert [_body_bytes(entry) for entry in body["received_raw"]] == [_challenge_delivery(value)]
        assert _header(body["received_raw"][0], "Content-Type") == "application/json"

    def test_the_echo_rule_is_the_one_the_in_process_receivers_use(self, capture_service):
        """One rule, two sockets: this service and ``webhook_wire`` must answer alike.

        ``tests.helpers.webhook_wire.echoing_challenge_response`` is the in-process
        receivers' echo (it grades the C2 integration module). This service cannot
        import it — it runs on a bare ``python:3.12-slim`` where ``tests.helpers``
        pulls in factory-boy — so the rule necessarily exists twice, and a comment
        stating the correspondence is not a guard. This assertion is the guard.
        """
        from tests.helpers.webhook_wire import CapturedWebhook, echoing_challenge_response

        key = uuid.uuid4().hex
        value = _CHALLENGE_VALUES[1]
        sent = _challenge_delivery(value)

        _status, raw = _post_challenge(capture_service, key, value, echo="challenge")

        in_process_status, in_process_body = echoing_challenge_response()(
            CapturedWebhook(url=f"http://receiver.invalid{webhook_path(key)}", headers=httpx.Headers({}), content=sent)
        )
        assert (200, raw) == (in_process_status, in_process_body)


class TestWithoutTheModeTheAnswerProvesNothing:
    """The control leg's mechanism, pinned in-process.

    The e2e module's non-echo leg asserts that a 2xx WITHOUT an echo does not
    activate the subscriber. That leg is only evidence if the receiver really does
    answer 2xx-without-echo — if it quietly echoed anyway, or answered non-2xx,
    the leg would pass for the wrong reason.
    """

    def test_the_default_answer_is_still_a_success_status(self, capture_service):
        key = uuid.uuid4().hex

        status, _raw = _post_challenge(capture_service, key, _CHALLENGE_VALUES[0], echo=None)

        assert status == 200, (
            "the control leg needs a 2xx with no echo; a non-2xx here would make the e2e leg pass on the "
            "status check instead of on the echo check"
        )

    def test_the_default_answer_does_not_echo_the_value(self, capture_service):
        """``validate_webhook_challenge_response`` must REJECT it — reason ``missing_echo``.

        Asserted on the reason and not merely on "it raised": the capture readback
        this endpoint answers with contains the challenge value NESTED inside the
        recorded payload, so "no echo" here means "no TOP-LEVEL echo field", which
        is the only thing the spec's response document allows to count.
        """
        from adcp.webhooks import WebhookChallengeError, validate_webhook_challenge_response

        key = uuid.uuid4().hex
        value = _CHALLENGE_VALUES[0]

        _status, raw = _post_challenge(capture_service, key, value, echo=None)

        with pytest.raises(WebhookChallengeError) as rejected:
            validate_webhook_challenge_response(raw, challenge=value)
        assert rejected.value.reason == "missing_echo", (
            f"expected the default answer to carry no top-level echo field; it was rejected as "
            f"{rejected.value.reason!r} instead. Body: {raw[:200]!r}"
        )
