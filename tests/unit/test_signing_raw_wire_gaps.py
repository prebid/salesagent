"""The two RAW-WIRE production gaps the conformance run exposes, pinned by name.

#1291 B3 (``salesagent-z6nr.14``), design step 10. Both gaps are the SAME disease:
**reading a COLLAPSED or DECODED view of the wire where the signature-covered raw
bytes are required.** The conformance vectors catch each of them exactly once
(``positive/008``/``010``, ``negative/021``/``022``/``023``/``026``); these tests
pin the shapes the vectors do NOT ship, so the fix is graded on the threat rather
than on the sample.

(a) ``_verify_url`` builds ``@target-uri`` from ``scope["path"]``, which uvicorn
    percent-DECODES. ``%2F`` becomes a literal ``/`` and every percent-encoded byte
    is decoded before canonicalization, so a legitimate signed request on any
    ``/api/v1/...`` route WITH A PATH PARAMETER is rejected as
    ``request_signature_invalid``. This is reachable in production today.

    The fix is not one line, and each sub-decision below is a separate test:

    1. ``raw_path`` is BYTES and ALREADY carries ``root_path`` (uvicorn h11:202 /
       httptools:261 both build ``full_raw_path`` that way).
    2. Decode STRICT ASCII. A non-ASCII raw path is not representable in a signed
       ``@target-uri`` (percent-encoding is mandatory on the wire), so a
       ``UnicodeDecodeError`` is a REJECTION, not a fallback: ``latin-1`` would
       mojibake it into a PASSING signature base and UTF-8 would mask it.
    3. Strip from the first ``?`` — some ASGI servers and test clients put the query
       string inside ``raw_path``, and the query still comes from
       ``scope["query_string"]``.
    4. Do NOT strip ``root_path``: the client signed the URL it dialed, mount prefix
       included. This is the deliberate OPPOSITE of ``path_from_asgi_scope``, which
       strips it because it feeds a ROUTE TABLE — which is why a blanket "always use
       raw_path" rule would be wrong and why the five ALLOWLIST rows in the disease
       scan stay allowlisted.
    5. Fall back to ``scope["path"]`` only when ``raw_path`` is absent, with the
       degradation documented (that fallback voids vectors 008/009/010).

(b) ``headers_from_asgi_scope`` is a dict comprehension, so genuinely REPEATED ASGI
    header tuples silently LAST-WIN — they are not joined. The vectors express
    "multi-valued" as one comma-joined value, so a check written over the collapsed
    dict passes all four vectors while MISSING the attack both ``$comment``s
    describe: a proxy inserting a SECOND ``Content-Type`` / ``Content-Digest``
    header LINE, which collapses before the check ever runs. ``_verify_url``'s
    ``headers.get("host")`` has the same exposure.

    So the strict pre-parse gate runs over ``scope["headers"]`` (the raw
    ``list[tuple[bytes, bytes]]``), BEFORE ``verify_request_signature``, raising
    ``SignatureVerificationError(REQUEST_SIGNATURE_HEADER_MALFORMED, step=1)`` — the
    same type and constant ``_handle_unsigned`` already constructs. BOTH forms are
    tested: the vector's comma-joined one and the repeated-tuple one.

Spec grounding: ``adcontextprotocol/adcp@v3.1.1``
``docs/reference/url-canonicalization.mdx`` (the authoritative algorithm;
the "receivers do not silently re-normalize" rule grounds the non-ASCII authority
rejection) and ``.../L1/security.mdx`` §"Verifier checklist (requests)" step 1.
**Said out loud rather than implied:** 026 and the malformed-authority family are
well grounded in that page; 021 (duplicate ``Signature-Input`` dictionary key) and
022 (multi-valued covered non-list field) are grounded ONLY in the vectors' own
``$comment`` plus step 1's bare "Reject if malformed" — no prose enumerates them.
023 additionally rests on RFC 9530's duplicate-algorithm rule.
"""

from __future__ import annotations

from typing import Any

import pytest

_HOST = {"host": "seller.example.com"}


def _scope(**overrides: Any) -> dict[str, Any]:
    scope: dict[str, Any] = {
        "type": "http",
        "scheme": "https",
        "method": "POST",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"seller.example.com")],
    }
    scope.update(overrides)
    return scope


def _verify_url(scope: dict[str, Any], headers: dict[str, str]) -> str:
    from src.core.signing.request_verifier_middleware import _verify_url as production

    return production(scope, headers)


# ---------------------------------------------------------------------------
# (a) _verify_url must read the RAW path
# ---------------------------------------------------------------------------


def test_encoded_slash_survives_into_the_target_uri() -> None:
    """``%2F`` is a reserved byte and MUST NOT decode into a path separator.

    The vector this generalises is ``positive/010``. uvicorn hands us
    ``path = unquote(raw_path)``, so reading ``path`` yields
    ``.../segment/with-encoded-slash/...`` — a different resource, and a signature
    base the client never signed.
    """
    scope = _scope(
        raw_path=b"/api/v1/segment%2Fwith-encoded-slash/media-buys",
        path="/api/v1/segment/with-encoded-slash/media-buys",
    )
    assert _verify_url(scope, _HOST) == "https://seller.example.com/api/v1/segment%2Fwith-encoded-slash/media-buys"


def test_percent_encoded_non_ascii_path_survives_into_the_target_uri() -> None:
    """``%e2%98%83`` stays percent-encoded (``positive/008``'s shape)."""
    scope = _scope(raw_path=b"/api/v1/resource/%e2%98%83/media-buys", path="/api/v1/resource/☃/media-buys")
    assert _verify_url(scope, _HOST) == "https://seller.example.com/api/v1/resource/%e2%98%83/media-buys"


def test_query_string_inside_raw_path_is_not_duplicated() -> None:
    """Some ASGI servers put the query INSIDE ``raw_path``; the query has one source.

    ``scope["query_string"]`` is the authority for the query (raw bytes, decoded
    latin-1 — which is why ``positive/007`` passes today). Leaving the ``?...`` in the
    path would emit it twice.
    """
    scope = _scope(raw_path=b"/api/v1/media-buys?b=2&a=1&c=3", path="/api/v1/media-buys", query_string=b"b=2&a=1&c=3")
    assert _verify_url(scope, _HOST) == "https://seller.example.com/api/v1/media-buys?b=2&a=1&c=3"


def test_mount_prefix_is_kept_because_the_client_dialed_it() -> None:
    """``root_path`` is NOT stripped here — the deliberate opposite of ``path_from_asgi_scope``.

    That helper strips the mount prefix because it feeds a ROUTE TABLE. This one feeds
    ``@target-uri``, and the client signed the URL it dialed, prefix included. Under
    uvicorn ``raw_path`` already carries the prefix, so the correct behavior is to use
    it verbatim.
    """
    scope = _scope(raw_path=b"/mounted/api/v1/media-buys", path="/mounted/api/v1/media-buys", root_path="/mounted")
    assert _verify_url(scope, _HOST) == "https://seller.example.com/mounted/api/v1/media-buys"


def test_non_ascii_raw_path_is_rejected_not_transcoded() -> None:
    """A raw path with non-ASCII BYTES is unrepresentable in a signed ``@target-uri``.

    Percent-encoding is mandatory on the wire, so this is a malformed request, not an
    encoding puzzle. ``latin-1`` would mojibake it into a signature base that could
    still VERIFY; UTF-8 would mask it. Both are silent-acceptance bugs, so the decode
    is strict ASCII and the failure is a rejection at step 1.
    """
    from adcp.signing import REQUEST_SIGNATURE_HEADER_MALFORMED
    from adcp.signing.errors import SignatureVerificationError

    scope = _scope(raw_path="/api/v1/bücher/media-buys".encode(), path="/api/v1/bücher/media-buys")
    with pytest.raises(SignatureVerificationError) as excinfo:
        _verify_url(scope, _HOST)
    assert excinfo.value.code == REQUEST_SIGNATURE_HEADER_MALFORMED


def test_missing_raw_path_degrades_to_the_decoded_path() -> None:
    """An ASGI server that omits ``raw_path`` gets the documented degradation.

    Pinned rather than left implicit: on such a server vectors 008/009/010 are NOT
    gradeable, because the encoded bytes are already gone before we are called. The
    fallback must still produce a URL — silently failing every signed request would be
    worse than the known limitation.
    """
    scope = _scope(path="/api/v1/media-buys")
    assert _verify_url(scope, _HOST) == "https://seller.example.com/api/v1/media-buys"


def test_repeated_host_line_is_rejected_rather_than_last_winning() -> None:
    """``@authority`` is signature-covered, and a SECOND ``Host`` line silently wins.

    The dict view ``_verify_url`` reads today keeps only the last of two ``host``
    tuples, so a proxy-inserted second ``Host`` rewrites the authority the signature
    is checked against with no error anywhere.
    """
    from adcp.signing import REQUEST_SIGNATURE_HEADER_MALFORMED
    from adcp.signing.errors import SignatureVerificationError

    from src.core.signing.request_verifier_middleware import _strict_header_precheck

    scope = _scope(
        raw_path=b"/api/v1/media-buys",
        path="/api/v1/media-buys",
        headers=[(b"host", b"seller.example.com"), (b"host", b"attacker.example.com")],
    )
    with pytest.raises(SignatureVerificationError) as excinfo:
        _strict_header_precheck(scope)
    assert excinfo.value.code == REQUEST_SIGNATURE_HEADER_MALFORMED


# ---------------------------------------------------------------------------
# (b) the strict pre-parse gate, over the RAW header list
# ---------------------------------------------------------------------------

_SIG_INPUT = (
    b'sig1=("@method" "@target-uri" "@authority" "content-type");created=1776520800;'
    b'expires=1776521100;nonce="KXYnfEfJ0PBRZXQyVXfVQA";keyid="test-ed25519-2026";'
    b'alg="ed25519";tag="adcp/request-signing/v1"'
)

#: ``(id, header list)`` — each pair is one malformation in BOTH of its forms: the
#: comma-joined one the vectors ship, and the repeated-LINE one they do not.
_MALFORMED_HEADER_SHAPES = [
    (
        "duplicate-signature-input-dictionary-key",  # negative/021's own form
        [(b"content-type", b"application/json"), (b"signature-input", _SIG_INPUT + b", " + _SIG_INPUT)],
    ),
    (
        "repeated-signature-input-line",
        [(b"content-type", b"application/json"), (b"signature-input", _SIG_INPUT), (b"signature-input", _SIG_INPUT)],
    ),
    (
        "multi-valued-content-type",  # negative/022's own form
        [(b"content-type", b"application/json, text/plain"), (b"signature-input", _SIG_INPUT)],
    ),
    (
        "repeated-content-type-line",
        [(b"content-type", b"application/json"), (b"content-type", b"text/plain"), (b"signature-input", _SIG_INPUT)],
    ),
    (
        "duplicate-content-digest-algorithm",  # negative/023's own form
        [
            (b"content-type", b"application/json"),
            (b"content-digest", b"sha-256=:AAAA:, sha-256=:BBBB:"),
            (b"signature-input", _SIG_INPUT),
        ],
    ),
    (
        "repeated-content-digest-line",
        [
            (b"content-type", b"application/json"),
            (b"content-digest", b"sha-256=:AAAA:"),
            (b"content-digest", b"sha-256=:BBBB:"),
            (b"signature-input", _SIG_INPUT),
        ],
    ),
    (
        "non-ascii-authority",  # negative/026's own form
        [(b"host", "bücher.example.com".encode("latin-1")), (b"signature-input", _SIG_INPUT)],
    ),
]


@pytest.mark.parametrize(
    "shape,headers", _MALFORMED_HEADER_SHAPES, ids=[shape for shape, _ in _MALFORMED_HEADER_SHAPES]
)
def test_strict_precheck_rejects_malformed_wire_headers(shape: str, headers: list[tuple[bytes, bytes]]) -> None:
    """Each malformation is refused at step 1 with the checklist's own code.

    ``request_signature_header_malformed`` — NOT ``request_target_uri_malformed``,
    which is the canonicalization set's code (``canonicalization.json``'s six
    ``reject: true`` cases). The two are deliberately kept apart.

    Measured against ``adcp==6.6.0``, the SDK returns
    ``request_signature_components_incomplete`` for the 021 shape and
    ``request_signature_invalid`` for 022/023/026 — which is why the gate is OURS and
    runs BEFORE ``verify_request_signature`` rather than being waited on upstream.
    """
    from adcp.signing import REQUEST_SIGNATURE_HEADER_MALFORMED
    from adcp.signing.errors import SignatureVerificationError

    from src.core.signing.request_verifier_middleware import _strict_header_precheck

    scope = _scope(raw_path=b"/api/v1/media-buys", path="/api/v1/media-buys", headers=headers)
    with pytest.raises(SignatureVerificationError) as excinfo:
        _strict_header_precheck(scope)
    assert excinfo.value.code == REQUEST_SIGNATURE_HEADER_MALFORMED, shape
    assert excinfo.value.step == 1, f"{shape}: the gate must fail at checklist step 1"


def test_strict_precheck_accepts_a_well_formed_request() -> None:
    """The gate refuses malformations ONLY — it must not reject a legitimate request.

    Without this, "raise on everything" would satisfy every assertion above, and all
    12 positive conformance vectors would fail for a reason no negative test names.
    """
    from src.core.signing.request_verifier_middleware import _strict_header_precheck

    scope = _scope(
        raw_path=b"/api/v1/media-buys",
        path="/api/v1/media-buys",
        headers=[
            (b"host", b"seller.example.com"),
            (b"content-type", b"application/json"),
            (b"content-digest", b"sha-256=:AAAA:"),
            (b"signature-input", _SIG_INPUT),
            (b"signature", b"sig1=:AAAA:"),
        ],
    )
    assert _strict_header_precheck(scope) is None
