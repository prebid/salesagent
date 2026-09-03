"""Grades our webhook HMAC signer/verifier against AdCP's own conformance vectors.

Source: ``tests/fixtures/adcp_webhook_vectors_pinned/webhook-hmac-sha256.json``,
vendored from ``adcontextprotocol/adcp`` at the repo's pinned spec tag (v3.1.1
-- see ``docs/adcp-spec-version.md`` and that fixture dir's ``_refresh.py``).
This turns "we changed the code" into "the wire is proven against the spec's
own test data" -- independent of any local test's own (possibly wrong)
expectations (GH #1802).

Scope boundary -- excluded on purpose, not by oversight:

- The ``signer_side.rejection_vectors`` / ``signer_side.positive_vectors``
  blocks (GH #1802) grade the strict-parse PRIMITIVE
  (``webhook_strict_json.loads_rejecting_duplicate_keys``), not the signer
  (``prepare_signed_request``) -- the signer never receives text (see the
  scope note below), so it is never on the code path these vectors exercise.
  ``TestSignerSideVectorsGradeThePrimitive`` names this explicitly: a green
  result here means the primitive matches the spec's own signer-conformance
  data, not that the signer has a runtime check it structurally cannot need
  (its dict-typed input already makes the disease this class of vector probes
  unrepresentable -- see ``src/core/security/webhook_egress.py``'s module
  docstring for the full argument). The verifier half of the duplicate-object-key
  rule IS graded end-to-end here, from the ``duplicate-keys-conflicting-values``
  entry in ``vectors``; see ``TestVerifierRejectsDuplicateObjectKeys``.
- ``secret_rejection_vectors``' two entropy vectors (all-zero, all-repeated-char)
  grade a SHOULD, not a MUST (spec text: "Implementations SHOULD reject
  well-known weak values"). Implementing that check was tried and reverted:
  it would reject ``"x" * 32``, which BR-UC-004's "Webhook credentials at
  minimum length - accepted" BDD scenario deliberately uses to pin the exact
  32-char MUST-accept boundary. Only the two MUST-level length vectors
  (31-byte, empty) are graded here; the entropy SHOULD is a separate,
  deliberately unscoped follow-up (avoids breaking a pinned BDD scenario for
  a task with no design-review atom).
- The signer (``prepare_signed_request``) only ever emits its own canonical
  compact-separator, ``ensure_ascii=True`` serialization of a payload dict --
  by the Core Invariant GH #1802 established, it never signs
  externally-supplied raw bytes. So it can only be graded against the subset
  of ``vectors`` whose ``raw_body`` IS byte-reproducible from a dict via that
  exact serialization (pure-ASCII, compact-form vectors); vectors in other
  JSON forms (spaced, pretty-printed, unicode, empty-body, embedded nulls,
  trailing newline) are verifier-only -- there is nothing in the signer to
  grade them against. This is a scope boundary from the architecture, not a
  gap: the verifier (byte-transparent) is graded against every remaining
  vector regardless of form.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from adcp import sign_legacy_webhook

from src.core.security.webhook_egress import prepare_signed_request
from src.core.security.webhook_strict_json import DuplicateKeyInput, loads_rejecting_duplicate_keys
from src.services.webhook_verification import WebhookBodyMalformedError, WebhookVerificationError, WebhookVerifier

_VECTORS_PATH = Path(__file__).parent.parent / "fixtures" / "adcp_webhook_vectors_pinned" / "webhook-hmac-sha256.json"
_DATA = json.loads(_VECTORS_PATH.read_text())
_SECRET = _DATA["secret"]

# The one ``vectors`` entry the verifier must REJECT rather than accept: its
# signature is mathematically valid over its exact bytes, but the body carries a
# duplicate object key (``expected_verifier_action: "reject-malformed"``).
_DUPLICATE_KEY_VECTOR_ID = "duplicate-keys-conflicting-values"
_DUPLICATE_KEY_VECTOR = next(v for v in _DATA["vectors"] if v["id"] == _DUPLICATE_KEY_VECTOR_ID)
assert _DUPLICATE_KEY_VECTOR["expected_verifier_action"] == "reject-malformed"

_VERIFIER_ACCEPT_VECTORS = [v for v in _DATA["vectors"] if v["id"] != _DUPLICATE_KEY_VECTOR_ID]
assert len(_VERIFIER_ACCEPT_VECTORS) == 14, "expected 14 accept vectors alongside the 1 duplicate-key reject vector"

# Pure-ASCII, compact-separator vectors our signer's canonical serialization
# byte-reproduces exactly -- see module docstring for the excluded forms.
_SIGNER_VECTOR_IDS = frozenset(
    {
        "compact-js-style",
        "empty-object",
        "nested",
        "scalars-mixed",
        "timestamp-zero",
        "timestamp-2040",
        "compact-whitespace-keys",
    }
)
_SIGNER_VECTORS = [v for v in _DATA["vectors"] if v["id"] in _SIGNER_VECTOR_IDS]
assert len(_SIGNER_VECTORS) == len(_SIGNER_VECTOR_IDS), "a signer-subset vector id is missing from the fixture"

_REJECTION_VECTORS = _DATA["rejection_vectors"]

# Only the two MUST-level (length) secret vectors -- see module docstring.
_SECRET_MUST_REJECT_VECTORS = [v for v in _DATA["secret_rejection_vectors"] if len(v["secret"]) < 32]
assert len(_SECRET_MUST_REJECT_VECTORS) == 2, "expected exactly the 31-byte and empty-string secret vectors"

_SIGNER_SIDE = _DATA["signer_side"]
_SIGNER_SIDE_REJECTION_VECTORS = _SIGNER_SIDE["rejection_vectors"]
assert len(_SIGNER_SIDE_REJECTION_VECTORS) == 4, "expected top-level/nested/array-contained/three-deep"
_SIGNER_SIDE_POSITIVE_VECTOR = _SIGNER_SIDE["positive_vectors"][0]


class TestVerifierAcceptsSpecVectors:
    """Our verifier (``WebhookVerifier`` -> ``adcp.signing.webhook_hmac.verify_webhook_hmac``)
    must accept every non-duplicate-key vector's exact raw bytes + signature.

    ``time.time`` is patched to the vector's own timestamp so the replay-window
    check passes regardless of the vector's (often historical) timestamp --
    the property under test is signature validity over raw bytes, not clock
    freshness (that's graded separately by the rejection vectors below).

    GH #1802: ``verify_webhook`` returns the parsed JSON payload
    (duplicate-key-checked) instead of a bare sentinel, to eliminate a
    double-parse -- except for a body that carries no JSON object at all (e.g.
    the ``empty-body``/``null-bytes`` vectors), where duplicate-key detection
    doesn't apply and ``None`` is returned: the signature verified, there is
    just no object payload to hand back. Compute the expected value the same
    way production does (attempt a plain parse, fall back to ``None``) rather
    than hardcoding which vectors are JSON-shaped.
    """

    @pytest.mark.parametrize("vector", _VERIFIER_ACCEPT_VECTORS, ids=[v["id"] for v in _VERIFIER_ACCEPT_VECTORS])
    def test_accepts_vector(self, vector: dict) -> None:
        verifier = WebhookVerifier(webhook_secret=_SECRET, replay_window_seconds=300)
        with patch("src.services.webhook_verification.time.time", return_value=float(vector["timestamp"])):
            result = verifier.verify_webhook(
                body=vector["raw_body"].encode("utf-8"),
                headers={
                    "X-AdCP-Signature": vector["expected_signature"],
                    "X-AdCP-Timestamp": str(vector["timestamp"]),
                },
            )

        try:
            expected = json.loads(vector["raw_body"])
        except ValueError:
            expected = None

        assert result == expected


class TestVerifierReturnsNoPayloadForValidNonObjectJson:
    """A correctly signed body that IS valid JSON but is NOT an object returns ``None``.

    Not reachable from ``vectors``: every one of the 14 accept vectors is
    either a JSON object or not JSON at all (``empty-body``, ``null-bytes``),
    so the narrowing arm in ``verify_webhook`` (``return payload if
    isinstance(payload, dict) else None``) is graded by nothing in the pinned
    fixture -- reverting it to a bare ``return payload`` leaves the rest of
    this file, and the whole suite, green. This class is that arm's grader.

    The obligation: ``None`` carries exactly ONE meaning -- "the signature
    verified and the body carries no JSON-object payload" -- so a top-level
    array/scalar must not leak its parsed value back to a caller that has
    been told to expect ``dict[str, Any] | None``. The ``json-null`` case is
    included as the boundary the narrowing exists to disambiguate (it reached
    ``None`` even before the narrowing, since ``json.loads(b"null")`` is
    ``None``); the array and scalar cases are the ones that regress if the
    narrowing is dropped.

    Signing goes through the SDK's ``sign_legacy_webhook`` rather than our own
    ``prepare_signed_request`` because the latter is dict-typed by Core
    Invariant GH #1802 and so structurally cannot emit a non-object
    body -- and signing here by hand would put a second copy of the HMAC
    scheme in the tests.
    """

    @pytest.mark.parametrize(
        ("payload", "raw_body"),
        [
            ([1, 2, 3], b"[1,2,3]"),
            ([], b"[]"),
            ("just-a-string", b'"just-a-string"'),
            (17, b"17"),
            (True, b"true"),
            (None, b"null"),
        ],
        ids=["json-array", "empty-json-array", "json-string", "json-number", "json-true", "json-null"],
    )
    def test_returns_none_for_valid_non_object_json(self, payload: object, raw_body: bytes) -> None:
        timestamp = 1700000000
        headers, body = sign_legacy_webhook(_SECRET, payload, timestamp=timestamp)  # type: ignore[arg-type]
        assert body == raw_body, "the SDK signer must sign the exact non-object bytes this case is about"

        verifier = WebhookVerifier(webhook_secret=_SECRET, replay_window_seconds=300)
        with patch("src.services.webhook_verification.time.time", return_value=float(timestamp)):
            result = verifier.verify_webhook(body=body, headers=headers)

        assert result is None, (
            f"a signed body of {raw_body!r} is valid JSON but not an object, so verify_webhook must report "
            f"'no JSON-object payload' as None rather than hand back {result!r} -- its declared return is "
            "dict[str, Any] | None, and a caller reading result['...'] on that value would fail at runtime"
        )


class TestVerifierRejectsMalformedOrTamperedRequests:
    """Our verifier must reject every ``rejection_vectors`` entry for its stated reason.

    Timing vectors (``timestamp-too-old``/``-future``) use the vector's own
    ``current_time`` as the patched ``now`` -- that is the point under test.
    Non-timing vectors pin ``now`` to the vector's own (numeric) ``timestamp``
    so the replay window does not mask the defect being graded; the one
    non-numeric-timestamp vector cannot supply a numeric ``now`` at all, so it
    uses an arbitrary fixed value (the defect fires before the window check
    can run).
    """

    @pytest.mark.parametrize("vector", _REJECTION_VECTORS, ids=[v["id"] for v in _REJECTION_VECTORS])
    def test_rejects_vector(self, vector: dict) -> None:
        now = vector.get("current_time")
        if now is None:
            ts = vector["timestamp"]
            now = ts if isinstance(ts, (int, float)) else 1700000000

        headers = {"X-AdCP-Timestamp": str(vector["timestamp"])}
        if vector["signature"] is not None:
            headers["X-AdCP-Signature"] = vector["signature"]

        verifier = WebhookVerifier(webhook_secret=_SECRET, replay_window_seconds=300)
        with patch("src.services.webhook_verification.time.time", return_value=float(now)):
            with pytest.raises(WebhookVerificationError):
                verifier.verify_webhook(body=vector["raw_body"].encode("utf-8"), headers=headers)


class TestVerifierRejectsDuplicateObjectKeys:
    """Our verifier must reject a duplicate-object-key body AFTER the HMAC verifies,
    with a malformed-body error that is a DISTINCT class from a signature failure.

    Spec basis (pinned AdCP 3.1.1): ``docs/building/by-layer/L1/security.mdx``
    §Duplicate object keys -- "Verifiers MUST reject bodies containing duplicate
    object keys after HMAC verification succeeds, returning a structured
    malformed-body error, distinct from a signature-mismatch error" ("the
    signature IS valid; the body is malformed"). Verifier checklist step 14 names
    the verifier's error identifier ``webhook_body_malformed``, distinct from
    ``webhook_signature_digest_mismatch``. UNGRADED by every storyboard in
    ``dist/compliance/3.1.1/**`` -- the vendored vector file is the sole
    conformance authority, which is why this grades from it directly.

    ``time.time`` is patched to the vector's own timestamp, exactly as the accept
    class does, so the replay window cannot mask the property under test.
    """

    @staticmethod
    def _headers(signature: str) -> dict[str, str]:
        return {
            "X-AdCP-Signature": signature,
            "X-AdCP-Timestamp": str(_DUPLICATE_KEY_VECTOR["timestamp"]),
        }

    def test_rejects_duplicate_key_body_whose_signature_is_valid(self) -> None:
        verifier = WebhookVerifier(webhook_secret=_SECRET, replay_window_seconds=300)

        with patch(
            "src.services.webhook_verification.time.time",
            return_value=float(_DUPLICATE_KEY_VECTOR["timestamp"]),
        ):
            try:
                accepted = verifier.verify_webhook(
                    body=_DUPLICATE_KEY_VECTOR["raw_body"].encode("utf-8"),
                    headers=self._headers(_DUPLICATE_KEY_VECTOR["expected_signature"]),
                )
            except Exception as exc:
                raised = exc
            else:
                pytest.fail(
                    f"verify_webhook returned {accepted!r} for the {_DUPLICATE_KEY_VECTOR_ID} vector -- a body "
                    "whose HMAC is valid but which repeats an object key must be rejected as malformed"
                )

        assert type(raised) is WebhookBodyMalformedError, (
            "duplicate-key rejection must raise a malformed-body error distinct from a signature failure, "
            f"got {type(raised).__name__}: {raised}"
        )

    def test_bad_signature_over_the_same_body_stays_a_signature_failure(self) -> None:
        """Pins the ORDER: the body is only judged once the HMAC has verified.

        Same duplicate-key bytes, one flipped hex digit in the signature. A
        verifier that parsed before verifying would report this as a malformed
        body -- collapsing the very distinction step 14 requires.
        """
        signature = _DUPLICATE_KEY_VECTOR["expected_signature"]
        tampered = signature[:-1] + ("0" if signature[-1] != "0" else "1")

        verifier = WebhookVerifier(webhook_secret=_SECRET, replay_window_seconds=300)
        with patch(
            "src.services.webhook_verification.time.time",
            return_value=float(_DUPLICATE_KEY_VECTOR["timestamp"]),
        ):
            with pytest.raises(WebhookVerificationError) as excinfo:
                verifier.verify_webhook(
                    body=_DUPLICATE_KEY_VECTOR["raw_body"].encode("utf-8"),
                    headers=self._headers(tampered),
                )

        assert type(excinfo.value) is WebhookVerificationError, (
            f"a digest mismatch must stay a signature failure, got {type(excinfo.value).__name__}"
        )


class TestVerifierRejectsWeakSecretsAtMustLevel:
    """``WebhookVerifier.__init__`` must reject secrets below the 32-char MUST floor.

    Only the length-based vectors are graded -- the entropy SHOULD (reject
    all-zero / all-repeated-char secrets) is a documented, deliberate
    exclusion; see the module docstring.
    """

    @pytest.mark.parametrize(
        "vector", _SECRET_MUST_REJECT_VECTORS, ids=[v["description"] for v in _SECRET_MUST_REJECT_VECTORS]
    )
    def test_rejects_short_secret(self, vector: dict) -> None:
        with pytest.raises(ValueError):
            WebhookVerifier(webhook_secret=vector["secret"])


class TestSignerReproducesSpecVectors:
    """Our signer (``prepare_signed_request`` -> ``adcp.sign_legacy_webhook``)
    must reproduce the exact wire bytes and signature for every vector whose
    raw body is our canonical (compact-separator, ASCII-escaped) form.
    """

    @pytest.mark.parametrize("vector", _SIGNER_VECTORS, ids=[v["id"] for v in _SIGNER_VECTORS])
    def test_signs_vector(self, vector: dict) -> None:
        payload = json.loads(vector["raw_body"])

        headers, body_bytes = prepare_signed_request(payload, _SECRET, {}, timestamp=vector["timestamp"])

        assert body_bytes == vector["raw_body"].encode("utf-8")
        assert headers["X-AdCP-Signature"] == vector["expected_signature"]
        assert headers["X-AdCP-Timestamp"] == str(vector["timestamp"])


class TestSignerSideVectorsGradeThePrimitive:
    """Grades ``webhook_strict_json.loads_rejecting_duplicate_keys`` against the
    spec's own signer-conformance data.

    This is PRIMITIVE conformance, not signer conformance --
    ``prepare_signed_request`` never receives text (see the module docstring's
    scope-boundary note and ``webhook_egress.py``'s own docstring): a Python
    ``dict`` cannot hold a duplicate key, so the signer-side MUST is satisfied
    structurally, and is, per the spec itself, "unverifiable on the wire ...
    enforced by out-of-band audit/interop testing, not runtime detection". A
    green result here means the PARSER matches the spec's own signer-side
    fixture data across all four probed shapes (top-level, nested,
    array-contained, three-deep) -- it does not mean the signer has a runtime
    check it structurally cannot need.
    """

    @pytest.mark.parametrize(
        "vector", _SIGNER_SIDE_REJECTION_VECTORS, ids=[v["id"] for v in _SIGNER_SIDE_REJECTION_VECTORS]
    )
    def test_primitive_rejects_duplicate_key_input(self, vector: dict) -> None:
        with pytest.raises(DuplicateKeyInput) as excinfo:
            loads_rejecting_duplicate_keys(vector["signer_input_body"])

        assert excinfo.value.code == "duplicate_key_input"

    def test_primitive_accepts_clean_input_and_the_signer_emits_a_signed_frame(self) -> None:
        """The positive vector: a reject-everything primitive would trivially
        pass the four cases above, so this proves clean input of the same
        structural complexity (nested, array-contained) is still accepted --
        end to end through the signer, which IS on this code path since the
        input is valid JSON text turned into a dict first.
        """
        payload = loads_rejecting_duplicate_keys(_SIGNER_SIDE_POSITIVE_VECTOR["signer_input_body"])
        assert isinstance(payload, dict)

        headers, body_bytes = prepare_signed_request(payload, _SECRET, {}, timestamp=1700000000)

        assert headers["X-AdCP-Signature"].startswith("sha256=")
        assert json.loads(body_bytes) == payload


class TestKeyNameSanitization14b:
    """Proves the step-14b sanitization rules actually execute -- without
    these cases the sanitization code in ``webhook_strict_json.py`` ships
    unverified.
    """

    def test_non_printable_key_name_is_replaced_with_a_sanitized_marker(self) -> None:
        body = '{"a\\u0007b":1,"a\\u0007b":2}'  # BEL (0x07) is non-printable

        with pytest.raises(DuplicateKeyInput) as excinfo:
            loads_rejecting_duplicate_keys(body)

        (name,) = excinfo.value.keys
        assert name == "<sanitized:3>"

    def test_more_than_four_duplicate_keys_are_capped_with_a_count_marker(self) -> None:
        body = "{" + ",".join(f'"k{i}":{i}' for i in range(6)) + "," + ",".join(f'"k{i}":{i}' for i in range(6)) + "}"

        with pytest.raises(DuplicateKeyInput) as excinfo:
            loads_rejecting_duplicate_keys(body)

        assert len(excinfo.value.keys) == 5
        assert excinfo.value.keys[:4] == ("k0", "k1", "k2", "k3")
        assert excinfo.value.keys[4] == "<...2 more>"
