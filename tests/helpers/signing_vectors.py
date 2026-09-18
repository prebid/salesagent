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
``src.core.signing.capture.ADCP_SURFACE_PREFIXES = ("/mcp", "/a2a", "/api/v1")`` gates
the CAPTURE middleware, and the vectors address ``/adcp/<operation>``. Sent verbatim
they carry no ``HttpExchange``, so ``SignatureSubject.exchange`` is ``None``, nothing is
verifiable, and all 40 would "pass" having graded nothing. Serving ``/adcp/*`` is a
protocol-surface decision, not a test-harness one, so it belongs to B4
(``salesagent-z6nr.15``), which grades the URL space black-box.

There is no verifier MIDDLEWARE on this architecture. ``capture.py`` "DECIDES NOTHING":
it records the message bytes, ``@method`` and the raw-path ``@target-uri`` on the ASGI
scope, and the ONE verifier call is
:func:`src.core.signing.verifier.verify_inbound_signature`, reached from
``src.core.resolved_identity._resolve_identity``, which only
``src.core.tools._boundary.invoke_tool`` calls. A request therefore reaches the verifier
iff it dispatches a registry tool — see :attr:`VectorPlan.route_named`.

The transplant rule is MECHANICAL and has exactly one form — see
:func:`transplant_url`. ``wire_url`` is nevertheless spelled out per row so a typo
in either the rule or a row is a loud failure
(``test_signing_conformance_plan.py::test_wire_url_matches_the_mechanical_rule``),
not a silently different request.

The BODY is transplanted for the same reason, and only since #1721
-------------------------------------------------------------------
``src.core.tools._boundary.serve`` computes ``validated_request(...)`` BEFORE it calls
``invoke_tool(...)``, and ``invoke_tool`` is what resolves the identity and therefore
what reaches the verifier. A body the dispatched tool's DTO refuses is answered
``INVALID_REQUEST`` (400) with the checklist never running — so a vector body carried
verbatim onto ``create_media_buy`` grades nothing at all, exactly as a verbatim
``/adcp/...`` URL grades nothing. The vectors were authored against the pre-#1721
ordering, where the verifier was an ASGI middleware that ran ahead of every DTO.

The body transplant is therefore the same kind of edit as the URL one and is bounded
the same way: :func:`transplant_body` moves the body onto the transplanted operation
and preserves every part of it that the vector GRADES. What a vector grades about its
body at L2 is exactly two things, and both survive:

* its ``Content-Digest`` — recomputed only where the vector's own digest was a TRUTHFUL
  digest of its own body AND the row is re-signed anyway (:func:`recomputes_digest`,
  ``positive/002`` alone). A digest the vector deliberately FALSIFIED stays falsified,
  which is what keeps ``negative/010`` a digest mismatch, and a row that is not
  re-signed keeps every header byte, which is what keeps ``017``/``020``'s placeholder
  signature working as the step-ordering canary.
* whether the request REGISTERS webhook credentials — ``negative/027``'s whole subject,
  read by ``src.core.signing.webhook_credentials.registers_webhook_credentials`` off
  the VALIDATED request, so it only exists at all if the body validates.

A row that names no route (:attr:`VectorPlan.route_named` is False) keeps its body
VERBATIM: it 404s in the router before any DTO sees it, and ``negative/028``'s JSON-RPC
envelope is the thing that puts it in the protocol-method namespace.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from functools import cache, lru_cache
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
    cross-check — it must never reach ``get_settings().signing.provider`` wiring.
    """
    return json.loads(_read("keys.json"))


def vectors_with_expected_signature_base() -> dict[str, dict[str, Any]]:
    """The L1(a) set: every vector file shipping ``expected_signature_base``."""
    return {vid: v for vid, v in load_signing_vectors().items() if "expected_signature_base" in v}


# ---------------------------------------------------------------------------
# The transplant rule
# ---------------------------------------------------------------------------

#: The real surface the vectors are transplanted onto.
#: ``TOOLS["create_media_buy"].rest == RestBinding("POST", "/media-buys")`` under the
#: router's ``/api/v1`` prefix — the vectors' OWN operation — which is why every
#: vector's ``verifier_capability`` is fed to the posture BYTE-IDENTICAL, with no
#: ``required_for`` rewrite anywhere.
TRANSPLANT_PREFIX = "/api/v1"
TRANSPLANT_OPERATION_SEGMENT = "media-buys"
#: The registry key ``TRANSPLANT_OPERATION_SEGMENT`` dispatches, and therefore the DTO
#: :func:`transplant_body` must produce a payload for. Asserted against the LIVE route
#: table by ``test_every_row_names_the_route_the_plan_claims``.
TRANSPLANT_OPERATION = "create_media_buy"


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


def _idempotency_key(vector_id: str) -> str:
    """A key that is STABLE per vector and distinct ACROSS vectors.

    Stable because ``negative/016`` sends its request TWICE and the two submissions must
    be byte-identical — a fresh key would make the second a different request and the
    replay it grades would be a coincidence. Distinct because a key REUSED across vectors
    is served from the at-most-once cache, and a positive answered from that cache would
    still have been verified (the resolver runs first) but would no longer exercise the
    tool it dispatched.

    Spelled to the pin's ``^[A-Za-z0-9_.:-]{16,255}$``: the vector id's ``/`` is the one
    character in it the pattern refuses.

    The prefix carries NO local name. It used to be ``b3-``, this repo's own task
    reference, and these keys travel: ``tests/storyboard/corrected_vectors.py`` writes them
    into a corpus proposed to the spec, where a reader has no way to learn what ``b3``
    meant. Only the two properties above are load-bearing.
    """
    return f"vector-{vector_id.replace('/', '-')}"


@cache
def transplant_body(vector_id: str) -> bytes:
    """Move a vector BODY onto the transplanted operation, mechanically.

    The body analogue of :func:`transplant_url`, needed since #1721 for the reason the
    module docstring gives: ``serve`` validates before it resolves, so a body
    ``create_media_buy`` refuses is a 400 the checklist never sees.

    Three clauses, and nothing else:

    1. the baseline is ``CreateMediaBuyRequestFactory.payload()`` — the ONE conformant
       ``create_media_buy`` payload this repo owns, graded against the pinned schema by
       ``tests/factories/request.py``, rather than a second one typed out here;
    2. the vector's own ``plan_id`` survives, because ``create_media_buy`` declares it —
       the body's counterpart of the authority and query bytes :func:`transplant_url`
       preserves;
    3. a vector whose body registers webhook credentials keeps registering them
       (``negative/027``), through ``PushNotificationConfigRequestFactory`` and
       ``hmac_authentication``. The BLOCK is re-spelled rather than copied: the vector
       writes the pre-3.1 singular ``authentication.scheme`` and ``credentials`` shorter
       than the pin's ``minLength: 32``, neither of which the pinned DTO declares. What
       ``registers_webhook_credentials`` reads is the PRESENCE of the block, so
       re-spelling it preserves the graded property exactly and dropping it would invert
       the vector into one that must NOT be rejected.

    A row that names no route keeps its body verbatim — it never reaches a DTO.

    Cached because the result must be byte-stable for a given vector: it is hashed into
    ``Content-Digest`` and signed, and ``negative/016`` sends it twice.
    """
    vector = load_signing_vectors()[vector_id]
    if not TRANSPLANT[vector_id].route_named:
        return vector["request"].get("body", "").encode()
    return json.dumps(create_media_buy_body(vector_id), separators=(",", ":")).encode()


def create_media_buy_body(vector_id: str) -> dict[str, Any]:
    """The conformant ``create_media_buy`` payload *vector_id* transplants onto.

    The THREE CLAUSES, without the routing gate. :func:`transplant_body` asks a REST
    question first — does this vector's wire URL name a registry binding, so that a DTO
    ever sees the body — and answers the body question only if it does. The corrected
    storyboard corpus (``tests/storyboard/corrected_vectors.py``) asks the body question
    ALONE, because the storyboard runner's MCP mode routes every vector to the MCP
    endpoint and names the operation in the ``tools/call`` envelope, so the URL's shape
    decides nothing about whether the payload is parsed.

    Split out rather than copied so there is still ONE conformant payload in this repo:
    a second builder here is the drift that makes a vector pass at one layer and fail at
    the other for reasons nobody can see.
    """
    from tests.factories import CreateMediaBuyRequestFactory
    from tests.factories.webhook import PushNotificationConfigRequestFactory, hmac_authentication

    original = load_signing_vectors()[vector_id]["request"].get("body", "").encode()
    payload = CreateMediaBuyRequestFactory.payload(idempotency_key=_idempotency_key(vector_id))
    sent = json.loads(original) if original.strip().startswith(b"{") else {}
    if "plan_id" in sent:
        payload["plan_id"] = sent["plan_id"]
    registered = sent.get("push_notification_config") or {}
    if registered.get("authentication") is not None:
        payload["push_notification_config"] = PushNotificationConfigRequestFactory.payload(
            url=registered["url"], authentication=hmac_authentication()
        )
    return payload


def recomputes_digest(vector_id: str) -> bool:
    """Whether the wire ``Content-Digest`` is recomputed over :func:`transplant_body`.

    True for exactly the rows whose own digest was a TRUTHFUL digest of their own body
    AND which the plan re-signs anyway. Both halves are load-bearing:

    * untruthful (``negative/010``'s ``AAAA…``, ``negative/023``'s duplicate algorithm) is
      the mutation the vector grades, so it goes on the wire untouched;
    * not re-signed (``negative/018``) means the placeholder signature is the
      step-ordering canary, and every header byte stays as shipped.

    On the vendored 3.1.1 corpus that is ``positive/002`` alone — the one row that must
    PASS the step-11 digest check, and therefore the one row whose digest has to describe
    the body actually sent.
    """
    from adcp.signing.digest import content_digest_matches

    vector = load_signing_vectors()[vector_id]
    headers = {name.lower(): value for name, value in vector["request"]["headers"].items()}
    digest = headers.get("content-digest")
    if digest is None or TRANSPLANT[vector_id].resigned is None:
        return False
    return content_digest_matches(digest, vector["request"].get("body", "").encode())


# ---------------------------------------------------------------------------
# The per-vector plan
# ---------------------------------------------------------------------------


class Credential(Enum):
    """Which credential the wire request presents.

    ``_resolve_identity`` resolves the caller with
    :func:`src.core.auth_utils.get_principal_from_token` and hands
    :func:`~src.core.signing.verifier.verify_inbound_signature` the ``Principal``; the
    verifier reads ``principal.agent_url`` as the key-resolution INPUT (verifier.py:234).
    With no accepted credential ``agent_url`` is ``None``, no ``StaticJwksResolver`` can
    be built, and every signed vector short-circuits at step 7 with
    ``request_signature_key_unknown`` — which would make all 12 positives fail and
    ``negative/008`` pass for the wrong reason.

    ``Principal.agent_url`` being the ONLY legitimate source is a spec rule, not a
    harness convenience: security.mdx § "agent_url derivation" forbids taking it from a
    header or body field, since that would let the signer choose its own key set.
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
    #: 017: ``SigningSettings.revoked_keyids`` -> ``CounterpartyRevocationChecker``.
    REVOKED_KID = "revoked_kid"
    #: 020: ``SigningSettings.per_keyid_cap_overrides`` + case-unique claimed nonces.
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
    #: Does ``wire_url`` (with ``method``) match a ``TOOLS[...].rest`` binding, so the
    #: request dispatches a registry tool and ``invoke_tool`` runs at all?
    #:
    #: There is no URL->operation resolver and no fail-closed bucket on this architecture:
    #: ``SignatureSubject.operation`` IS the registry key ``invoke_tool`` dispatched on
    #: (``_boundary.py:339``), so a wire URL that names no route 404s in the router and
    #: NEVER reaches ``_resolve_identity``. The four ``False`` positives (006 ``/./``, 008
    #: ``%e2%98%83``, 009 ``%7E%2D%5F%2E``, 010 ``%2F``) and the verbatim ``negative/028``
    #: therefore cannot be graded end-to-end through the boundary; how those five are
    #: driven is the DRIVER's decision, not this table's. Their canonicalization
    #: obligation is still graded — at L1(a) by
    #: ``test_signing_conformance_signature_base.py`` (the shipped
    #: ``expected_signature_base``) and by ``test_signing_conformance_canonicalization.py``
    #: — and the raw-path ``@target-uri`` derivation 010 pins is graded by
    #: ``test_architecture_signed_target_uri_raw_path.py``.
    route_named: bool
    #: The registry key the request dispatches, or ``""`` when it names no route.
    #: ASSERTED against ``TOOLS``/the metric label, never assumed.
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
        notes="/./ names no registry REST binding, so invoke_tool never runs and operation is ''.",
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
        notes="THE raw_path pin: uvicorn percent-decodes scope['path'], turning %2F into a real "
        "'/' and producing a different @target-uri than the client signed. capture.py rebuilds "
        "@target-uri from scope['raw_path'] for exactly this; graded by "
        "test_architecture_signed_target_uri_raw_path.py.",
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
        notes="Runs VERBATIM — POST /mcp is inside ADCP_SURFACE_PREFIXES, so the capture runs. "
        "JSON-RPC tasks/cancel names the PROTOCOL-METHOD namespace and is not a TOOLS key, so "
        "invoke_tool never dispatches it and operation is ''. verify_inbound_signature asks "
        "bucket_for for the AdCP-operation namespace ONLY and is never handed a protocol_method "
        "(verifier.py:259-262), so protocol_methods_required_for is currently ungraded here.",
    ),
}
