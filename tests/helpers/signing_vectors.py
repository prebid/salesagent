"""Loader + execution plan for the AdCP request-signing conformance vectors.

#1291 B3 (``salesagent-z6nr.14``). Two things live here, and nothing else:

1. **The loader** — reads the vendored, sha256-pinned snapshot under
   ``tests/fixtures/adcp_conformance_vectors/3.1.1/request-signing/`` offline.
   Same shape as :mod:`tests.helpers.pinned_schema`: a missing file is a HARD
   ``AssertionError``, never a skip, so a re-vendor that drops a vector cannot
   quietly shrink the graded set.

2. **The per-vector execution plan** (:data:`TRANSPLANT`) — OUR data about how
   each vector is driven at L2, deliberately NOT inside the vendored tree: that
   tree is upstream-owned and byte-pinned, and mixing our plan into it would put
   our decisions behind the drift guard's hashes.

Why a transplant is needed at all (decision D-B3-1, settled — do not relitigate):
``ADCP_SURFACE_PREFIXES = ("/mcp", "/a2a", "/api/v1")`` gates the ENTIRE verifier
middleware, and the vectors address ``/adcp/<operation>``. Sent verbatim they would
sail past the middleware unverified and all 40 would "pass" having graded nothing.
Serving ``/adcp/*`` is a protocol-surface decision, not a test-harness one, so it
belongs to B4 (``salesagent-z6nr.15``), which grades the URL space black-box.

The transplant rule is MECHANICAL and has exactly one form — see
:func:`transplant_url`. ``wire_url`` is nevertheless spelled out per row so a typo
in either the rule or a row is a loud failure
(``test_signing_conformance_plan.py::test_wire_url_matches_the_mechanical_rule``),
not a silently different request.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from tests.helpers.adcp_pin import EXPECTED_SPEC_VERSION

VECTORS_DIR = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "adcp_conformance_vectors"
    / EXPECTED_SPEC_VERSION
    / "request-signing"
)

#: The one label the AdCP profile mandates a verifier process. ``positive/004``
#: ships two labels and pins ``expected_outcome.verified_label == "sig1"``, so
#: choosing "the first key" instead would make a renamed label pass silently.
VERIFIED_LABEL = "sig1"


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def _read(relpath: str) -> str:
    path = VECTORS_DIR / relpath
    if not path.exists():
        raise AssertionError(
            f"Conformance vector not vendored: {relpath} -> {path}. "
            "Re-run uv run python -m tests.fixtures.adcp_schemas_pinned._refresh to vendor it."
        )
    return path.read_text()


def load_vector_manifest() -> dict[str, Any]:
    """The committed ``MANIFEST.json``: spec version, source tag, per-file sha256."""
    return json.loads(_read("MANIFEST.json"))


@lru_cache(maxsize=1)
def load_signing_vectors() -> dict[str, dict[str, Any]]:
    """Every request vector, keyed by id (``"positive/001-basic-post"``)."""
    out: dict[str, dict[str, Any]] = {}
    for bucket in ("positive", "negative"):
        directory = VECTORS_DIR / bucket
        if not directory.is_dir():
            raise AssertionError(f"Conformance vector directory not vendored: {directory}")
        for path in sorted(directory.glob("*.json")):
            out[f"{bucket}/{path.stem}"] = json.loads(path.read_text())
    return out


@lru_cache(maxsize=1)
def load_canonicalization_cases() -> tuple[dict[str, Any], ...]:
    """The flat URL-canonicalization cases from ``canonicalization.json``.

    NOTE the file self-reports ``"version": "3.0"``. That field is NOT the spec
    pin and must never be asserted against ``adcp.get_adcp_spec_version()`` —
    only ``MANIFEST["spec_version"]`` is the tie to the repo's ``adcp`` pin.
    """
    return tuple(json.loads(_read("canonicalization.json"))["cases"])


@lru_cache(maxsize=1)
def load_signing_keys() -> dict[str, Any]:
    """``keys.json``: the runner keypairs.

    Carries ``_private_d_for_test_only`` private material. It is PUBLIC spec data
    (the vector README says so) and exists for the optional signature-regeneration
    cross-check — it must never reach ``SigningConfig.provider`` wiring.
    """
    return json.loads(_read("keys.json"))


def vectors_with_expected_signature_base() -> dict[str, dict[str, Any]]:
    """The L1(a) set: every vector file shipping ``expected_signature_base``."""
    return {vid: v for vid, v in load_signing_vectors().items() if "expected_signature_base" in v}


# ---------------------------------------------------------------------------
# The transplant rule
# ---------------------------------------------------------------------------

#: The real surface the vectors are transplanted onto. ``operation_for_rest_route
#: ("POST", "/api/v1/media-buys") == "create_media_buy"`` — the vectors' OWN
#: operation — which is why every vector's ``verifier_capability`` is fed to the
#: posture BYTE-IDENTICAL, with no ``required_for`` rewrite anywhere.
TRANSPLANT_PREFIX = "/api/v1"
TRANSPLANT_OPERATION_SEGMENT = "media-buys"


def transplant_url(url: str) -> str:
    """Move a vector URL onto the served AdCP surface, mechanically.

    Replace the leading ``/adcp`` prefix with ``/api/v1`` and the FINAL path
    segment (the operation name) with ``media-buys``. Everything else —
    authority, scheme, port, query string, and every byte BETWEEN those two
    edits — is preserved verbatim, because those bytes (``:443``, ``/./``,
    ``?b=2&a=1&c=3``, ``%e2%98%83``, ``%7E%2D%5F%2E``, ``%2F``,
    ``[2001:db8::1]``, ``bücher.example.com``) are exactly the pathology the
    canonicalization vectors exist to grade.
    """
    parts = urlsplit(url)
    segments = parts.path.split("/")
    if len(segments) < 3 or segments[1] != "adcp":
        raise ValueError(f"not an /adcp vector path: {parts.path!r}")
    segments[1] = TRANSPLANT_PREFIX.lstrip("/")
    segments[-1] = TRANSPLANT_OPERATION_SEGMENT
    return urlunsplit((parts.scheme, parts.netloc, "/".join(segments), parts.query, parts.fragment))


# ---------------------------------------------------------------------------
# The per-vector plan
# ---------------------------------------------------------------------------


class Credential(Enum):
    """Which credential the wire request presents.

    ``_resolve_request_context`` resolves the counterparty's ``agent_url`` from
    ``PrincipalRepository.get_by_token``; with no accepted credential it hands the
    verifier an EMPTY ``StaticJwksResolver`` and every signed vector short-circuits
    at step 7 with ``request_signature_key_unknown`` — which would make all 12
    positives fail and ``negative/008`` pass for the wrong reason.
    """

    #: A provisioned Principal whose ``agent_url`` is the seeded counterparty.
    PRINCIPAL_TOKEN = "principal_token"
    #: The vector's own headers verbatim, and NO principal provisioned for them.
    #: security.mdx §"Composition with fallback authenticators": "an unrecognized
    #: bearer token or API key (one the verifier does not accept) is not a valid
    #: credential — the caller is unauthenticated and falls into the first rule."
    NONE = "none"


class HarnessState(Enum):
    """Pre-state the case establishes through PRODUCTION config seams."""

    NONE = "none"
    #: 016: send the byte-identical request TWICE (the kit's ``repeat_request``).
    REPLAY_PAIR = "replay_pair"
    #: 017: ``SigningConfig.revoked_keyids`` -> ``CounterpartyRevocationChecker``.
    REVOKED_KID = "revoked_kid"
    #: 020: ``SigningConfig.per_keyid_cap_overrides`` + case-unique claimed nonces.
    CAP_OVERRIDE = "cap_override"


class Outcome(Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


@dataclass(frozen=True)
class VectorPlan:
    """How one vector is driven at L2. Every column is mandatory.

    Frozen and typed on purpose: a mistyped column is a silent vacuous pass, and
    that is this ticket's whole failure mode.
    """

    vector_id: str
    #: The absolute URL actually sent (transplanted; verbatim for ``negative/028``).
    wire_url: str
    credential: Credential
    #: Does ``operation_for_rest_route(method, decoded_path)`` NAME a route, or does
    #: the case rely on ``_fail_closed_bucket`` promoting an unresolvable request?
    route_named: bool
    #: The string the resolver MUST return — ASSERTED, never assumed.
    expected_operation: str
    #: The keyid from ``keys.json`` the harness re-signs with, or None for verbatim.
    resigned: str | None
    outcome: Outcome
    #: The exact ``WWW-Authenticate: Signature error="<code>"`` value, or None.
    expected_code: str | None
    harness_state: HarnessState = HarnessState.NONE
    notes: str = ""


_ED = "test-ed25519-2026"
_SELLER = "https://seller.example.com"


def _positive(
    vector_id: str,
    wire_url: str,
    *,
    route_named: bool,
    operation: str,
    resigned: str = _ED,
    notes: str = "",
) -> VectorPlan:
    return VectorPlan(
        vector_id=vector_id,
        wire_url=wire_url,
        credential=Credential.PRINCIPAL_TOKEN,
        route_named=route_named,
        expected_operation=operation,
        resigned=resigned,
        outcome=Outcome.ACCEPTED,
        expected_code=None,
        notes=notes,
    )


def _rejects(
    vector_id: str,
    code: str,
    *,
    wire_url: str = f"{_SELLER}/api/v1/media-buys",
    credential: Credential = Credential.PRINCIPAL_TOKEN,
    route_named: bool = True,
    operation: str = "create_media_buy",
    resigned: str | None = None,
    harness_state: HarnessState = HarnessState.NONE,
    notes: str = "",
) -> VectorPlan:
    return VectorPlan(
        vector_id=vector_id,
        wire_url=wire_url,
        credential=credential,
        route_named=route_named,
        expected_operation=operation,
        resigned=resigned,
        outcome=Outcome.REJECTED,
        expected_code=code,
        harness_state=harness_state,
        notes=notes,
    )


#: Re-signing is needed ONLY where the URL changes AND the expected outcome is at or
#: after step 10 (crypto). Everything else goes VERBATIM — most negatives ship a
#: placeholder ``sig1=:AAAA…:`` that is not meant to verify, and for 017 and 020 that
#: placeholder IS the step-ordering canary: a crypto-first verifier returns
#: ``request_signature_invalid`` and that is a graded FAIL. Re-signing them destroys it.
TRANSPLANT: dict[str, VectorPlan] = {
    # -- positives: 12 of 12, all re-signed (URL changed, outcome is step 10+) ------
    "positive/001-basic-post": _positive(
        "positive/001-basic-post", f"{_SELLER}/api/v1/media-buys", route_named=True, operation="create_media_buy"
    ),
    "positive/002-post-with-content-digest": _positive(
        "positive/002-post-with-content-digest",
        f"{_SELLER}/api/v1/media-buys",
        route_named=True,
        operation="create_media_buy",
    ),
    "positive/003-es256-post": _positive(
        "positive/003-es256-post",
        f"{_SELLER}/api/v1/media-buys",
        route_named=True,
        operation="create_media_buy",
        resigned="test-es256-2026",
        notes="ES256 (alg=ecdsa-p256-sha256); re-signed with its OWN key, not the Ed25519 one.",
    ),
    "positive/004-multiple-signature-labels": _positive(
        "positive/004-multiple-signature-labels",
        f"{_SELLER}/api/v1/media-buys",
        route_named=True,
        operation="create_media_buy",
        notes=(
            "Ships NO expected_signature_base, so its canonicalization is graded only "
            "here and transitively by positive/001 (byte-identical URL). Two labels; "
            "expected_outcome.verified_label pins sig1."
        ),
    ),
    "positive/005-default-port-stripped": _positive(
        "positive/005-default-port-stripped",
        "https://seller.example.com:443/api/v1/media-buys",
        route_named=True,
        operation="create_media_buy",
        notes="httpx/TestClient strips :443 before the app sees it — hence the raw-ASGI driver.",
    ),
    "positive/006-dot-segment-path": _positive(
        "positive/006-dot-segment-path",
        f"{_SELLER}/api/v1/./media-buys",
        route_named=False,
        operation="",
        notes="/./ names no route: verification happens by _fail_closed_bucket, operation=''.",
    ),
    "positive/007-query-byte-preserved": _positive(
        "positive/007-query-byte-preserved",
        f"{_SELLER}/api/v1/media-buys?b=2&a=1&c=3",
        route_named=True,
        operation="create_media_buy",
        notes="Vector URL names get_media_buy, not create_media_buy; the transplant moves it into "
        "the required bucket. Outcome unchanged (positive). verifier_capability NOT rewritten.",
    ),
    "positive/008-percent-encoded-path": _positive(
        "positive/008-percent-encoded-path",
        f"{_SELLER}/api/v1/resource/%e2%98%83/media-buys",
        route_named=False,
        operation="",
        notes="No operation-name final segment upstream (/adcp/resource/%e2%98%83/item); the "
        "final-segment rule yields .../resource/%e2%98%83/media-buys. Names no route.",
    ),
    "positive/009-percent-encoded-unreserved-decoded": _positive(
        "positive/009-percent-encoded-unreserved-decoded",
        f"{_SELLER}/api/v1/a%7Eb%2Dc%5Fd%2Ee/media-buys",
        route_named=False,
        operation="",
    ),
    "positive/010-percent-encoded-slash-preserved": _positive(
        "positive/010-percent-encoded-slash-preserved",
        f"{_SELLER}/api/v1/segment%2Fwith-encoded-slash/media-buys",
        route_named=False,
        operation="",
        notes="THE _verify_url pin: uvicorn percent-decodes scope['path'], turning %2F into a real "
        "'/' and producing a different @target-uri than the client signed.",
    ),
    "positive/011-ipv6-authority": _positive(
        "positive/011-ipv6-authority",
        "https://[2001:db8::1]/api/v1/media-buys",
        route_named=True,
        operation="create_media_buy",
        notes="httpx cannot send an IPv6 authority at all (ValueError) — raw-ASGI only.",
    ),
    "positive/012-ipv6-authority-default-port-stripped": _positive(
        "positive/012-ipv6-authority-default-port-stripped",
        "https://[2001:db8::1]:443/api/v1/media-buys",
        route_named=True,
        operation="create_media_buy",
    ),
    # -- negatives: 28 of 28 -------------------------------------------------------
    "negative/001-no-signature-header": _rejects(
        "negative/001-no-signature-header",
        "request_signature_required",
        credential=Credential.NONE,
        notes="Unauthenticated AND unsigned -> the composition rule's first branch.",
    ),
    "negative/002-wrong-tag": _rejects("negative/002-wrong-tag", "request_signature_tag_invalid"),
    "negative/003-expired-signature": _rejects("negative/003-expired-signature", "request_signature_window_invalid"),
    "negative/004-window-too-long": _rejects("negative/004-window-too-long", "request_signature_window_invalid"),
    "negative/005-alg-not-allowed": _rejects("negative/005-alg-not-allowed", "request_signature_alg_not_allowed"),
    "negative/006-missing-covered-component": _rejects(
        "negative/006-missing-covered-component", "request_signature_components_incomplete"
    ),
    "negative/007-missing-content-digest": _rejects(
        "negative/007-missing-content-digest", "request_signature_components_incomplete"
    ),
    "negative/008-unknown-keyid": _rejects(
        "negative/008-unknown-keyid",
        "request_signature_key_unknown",
        notes="Additionally asserts the counterparty WAS resolvable, so the rejection is earned on "
        "keyid='not-a-real-kid' at step 7 and not on a missing principal.",
    ),
    "negative/009-key-ops-missing-verify": _rejects(
        "negative/009-key-ops-missing-verify", "request_signature_key_purpose_invalid"
    ),
    "negative/010-content-digest-mismatch": _rejects(
        "negative/010-content-digest-mismatch",
        "request_signature_digest_mismatch",
        resigned=_ED,
        notes="Step 11 — past crypto, so the transplanted URL must be re-signed or it would fail "
        "at step 10 instead and grade the wrong check.",
    ),
    "negative/011-malformed-header": _rejects(
        "negative/011-malformed-header",
        "request_signature_header_malformed",
        notes="Vector URL names sync_creatives; the transplant moves it into the required bucket. "
        "Outcome unchanged (rejects at step 1). verifier_capability NOT rewritten.",
    ),
    "negative/012-missing-expires-param": _rejects(
        "negative/012-missing-expires-param", "request_signature_params_incomplete"
    ),
    "negative/013-expires-le-created": _rejects("negative/013-expires-le-created", "request_signature_window_invalid"),
    "negative/014-missing-nonce-param": _rejects(
        "negative/014-missing-nonce-param", "request_signature_params_incomplete"
    ),
    "negative/015-signature-invalid": _rejects(
        "negative/015-signature-invalid",
        "request_signature_invalid",
        notes="NEVER re-signed: its Signature is 86 'A's, a deliberate non-signature. Its shipped "
        "expected_signature_base is spec-authored data graded at L1(a).",
    ),
    "negative/016-replayed-nonce": _rejects(
        "negative/016-replayed-nonce",
        "request_signature_replayed",
        resigned=_ED,
        harness_state=HarnessState.REPLAY_PAIR,
        notes="Do NOT preload: send twice and assert submission #1 was ACCEPTED and #2 rejected. "
        "Preloading makes the acceptance unobservable — the kit's own false-green warning.",
    ),
    "negative/017-key-revoked": _rejects(
        "negative/017-key-revoked",
        "request_signature_key_revoked",
        harness_state=HarnessState.REVOKED_KID,
        notes="NEVER re-signed: the placeholder signature is the step-ordering canary — a "
        "crypto-first verifier returns request_signature_invalid and that is a graded FAIL.",
    ),
    "negative/018-digest-covered-when-forbidden": _rejects(
        "negative/018-digest-covered-when-forbidden", "request_signature_components_unexpected"
    ),
    "negative/019-signature-without-signature-input": _rejects(
        "negative/019-signature-without-signature-input", "request_signature_header_malformed"
    ),
    "negative/020-rate-abuse": _rejects(
        "negative/020-rate-abuse",
        "request_signature_rate_abuse",
        harness_state=HarnessState.CAP_OVERRIDE,
        notes="NEVER re-signed (same canary as 017). Cap set via per_keyid_cap_overrides, which is "
        "process-local and cannot reach another xdist worker.",
    ),
    "negative/021-duplicate-signature-input-label": _rejects(
        "negative/021-duplicate-signature-input-label",
        "request_signature_header_malformed",
        notes="PRODUCTION GAP: adcp==6.6.0 last-wins on the duplicate RFC 8941 dictionary key and "
        "returns request_signature_components_incomplete.",
    ),
    "negative/022-multi-valued-content-type": _rejects(
        "negative/022-multi-valued-content-type",
        "request_signature_header_malformed",
        notes="PRODUCTION GAP: adcp==6.6.0 returns request_signature_invalid (no single-valued "
        "check on a covered non-list field).",
    ),
    "negative/023-multi-valued-content-digest": _rejects(
        "negative/023-multi-valued-content-digest",
        "request_signature_header_malformed",
        notes="PRODUCTION GAP: adcp==6.6.0 returns request_signature_invalid (RFC 9530 "
        "duplicate-algorithm not rejected).",
    ),
    "negative/024-unquoted-string-param": _rejects(
        "negative/024-unquoted-string-param", "request_signature_header_malformed"
    ),
    "negative/025-jwk-alg-crv-mismatch": _rejects(
        "negative/025-jwk-alg-crv-mismatch",
        "request_signature_key_purpose_invalid",
        notes="Carries jwks_override rather than jwks_ref — the seeded JWKS is the vector's own.",
    ),
    "negative/026-non-ascii-host": _rejects(
        "negative/026-non-ascii-host",
        "request_signature_header_malformed",
        wire_url="https://bücher.example.com/api/v1/media-buys",
        notes="PRODUCTION GAP: adcp==6.6.0 returns request_signature_invalid — no A-label "
        "enforcement anywhere on the authority path. httpx punycodes the host before send, so "
        "this vector is only gradeable through the raw-ASGI driver.",
    ),
    "negative/027-webhook-registration-authentication-unsigned": _rejects(
        "negative/027-webhook-registration-authentication-unsigned",
        "request_signature_required",
        credential=Credential.NONE,
        notes="SHIPS Authorization: Bearer test-bearer-token and it goes on the wire VERBATIM. No "
        "principal is provisioned for it: an unrecognized bearer is not a valid credential. "
        "Provisioning one would invert the vector to must-not-be-rejected.",
    ),
    "negative/028-unsigned-protocol-method-required": _rejects(
        "negative/028-unsigned-protocol-method-required",
        "request_signature_required",
        wire_url=f"{_SELLER}/mcp",
        credential=Credential.NONE,
        route_named=False,
        operation="",
        notes="Runs VERBATIM — POST /mcp already targets a real surface. JSON-RPC tasks/cancel "
        "names the PROTOCOL-METHOD namespace, so operation is '' and protocol_method is "
        "'tasks/cancel'.",
    ),
}
