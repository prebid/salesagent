"""Transport enum and TransportResult for multi-transport behavioral tests.

Defines the seven dispatch transports (IMPL, A2A, REST, MCP + E2E variants)
and a frozen result container that separates transport-specific envelope from
shared payload.

Usage::

    result = env.call_via(Transport.REST, creatives=[...])
    assert result.is_success
    assert result.payload.creatives[0].action == CreativeAction.created
"""

from __future__ import annotations

import functools
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel

from tests.helpers import pinned_schema


@functools.lru_cache(maxsize=1)
def _pinned_error_metadata() -> dict[str, dict[str, str]]:
    """code -> {recovery, suggestion} from the installed SDK's error-code enum.

    Sourced through ``pinned_schema`` so there is exactly ONE upstream spec pin
    in play — the installed SDK's own (guarded by
    tests/unit/test_pinned_schema_single_source.py; see docs/adcp-spec-version.md
    "Pinned schema sources").

    This module reads only two things out of the returned dict: the KEY SET
    (``is_pinned_error_code`` / the canonical-code assertion below) and
    ``recovery`` (``assert_wire_error``'s default expected classification). It
    never reads ``suggestion`` — ``extract_wire_suggestion`` below reads the
    WIRE's own suggestion text, not this metadata.

    Measured, not assumed: the SDK tree's enum and the SHA-pinned vendored
    fixture (tests/fixtures/adcp_schemas_pinned/enums/error-code.json, re-vendored
    verbatim from the v3.1.1 tag and hash-guarded by
    tests/unit/test_guards_error_code_fixture_pin.py) now agree EXACTLY — same 92
    codes, 0 ``recovery`` divergences, 0 ``suggestion`` divergences — so the two
    possible sources are interchangeable here. The older, pre-re-vendor fixture
    was the one that disagreed: 64 codes to the SDK's 92 (a strict subset, still
    with 0 ``recovery`` divergences across the 64 shared codes, but 4
    ``suggestion`` divergences). That is why consumers which grade ``suggestion``
    CONTENT (test_architecture_error_suggestion_enum_conformance.py) read the
    hash-pinned fixture directly rather than this helper.
    """
    return pinned_schema.load("error-code.json")["enumMetadata"]


def is_pinned_error_code(code: str | None) -> bool:
    """Whether ``code`` is a canonical AdCP error code in the pinned enum.

    The guard an outcome-dispatch step needs BEFORE calling
    :meth:`TransportResult.assert_wire_error`: that method HARD-FAILS on any
    non-pinned code (a scenario-only code like ``DOMAIN_INVALID_FORMAT`` that
    production never emits), so a step whose scenarios can carry a non-pinned
    code must route those through a reconstructed-exception branch instead of
    the wire assertion. Same pinned source as ``assert_wire_error`` (pin-wins),
    so the two never disagree on what "canonical" means.
    """
    return code is not None and code in _pinned_error_metadata()


def extract_wire_suggestion(envelope: dict | None) -> str | None:
    """The buyer-facing ``suggestion`` from a two-layer AdCP wire error envelope.

    STRICT error.json conformance: ``suggestion`` is a top-level sibling of
    code/message/field/retry_after/recovery on the error object (in either the
    ``errors[0]`` or the envelope-level ``adcp_error`` layer). A suggestion
    buried in the free-form ``details`` dict is NOT at the protocol position
    and deliberately does not satisfy this lookup — emitters that bury it are
    conformance bugs the harness must surface, not mask (#1417).
    Single source of truth for both ``TransportResult.assert_wire_error`` and
    the BDD ``_wire_suggestion`` step (#1417). Returns ``None`` when
    there is no envelope (IMPL / no-wire).
    """
    if not envelope:
        return None
    errors = envelope.get("errors") or [{}]
    adcp_error = envelope.get("adcp_error") or {}
    return errors[0].get("suggestion") or adcp_error.get("suggestion")


class Transport(StrEnum):
    """Dispatch transports for behavioral tests."""

    IMPL = "impl"  # Direct _impl() call
    A2A = "a2a"  # _raw() A2A wrapper
    REST = "rest"  # FastAPI TestClient → route → _raw() → _impl()
    MCP = "mcp"  # Mock Context → MCP wrapper → _impl()
    E2E_REST = "e2e_rest"  # Real HTTP via httpx → nginx → server
    E2E_MCP = "e2e_mcp"  # Real MCP via httpx → nginx → server (placeholder)
    E2E_A2A = "e2e_a2a"  # Real A2A via httpx → nginx → server (placeholder)


# Maps Transport → ResolvedIdentity.protocol value
TRANSPORT_PROTOCOL: dict[Transport, str] = {
    Transport.IMPL: "mcp",  # _impl doesn't inspect protocol; keep default
    Transport.A2A: "a2a",
    Transport.REST: "rest",
    Transport.MCP: "mcp",
    Transport.E2E_REST: "rest",
    Transport.E2E_MCP: "mcp",
    Transport.E2E_A2A: "a2a",
}


@dataclass(frozen=True)
class E2EConfig:
    """Configuration for E2E transport dispatch.

    Attributes:
        base_url: Docker stack URL (e.g., ``http://localhost:8092``). Stays
            PLAINTEXT — 487 bdd_e2e and 95 e2e tests target it and do not move.
        postgres_url: Docker PostgreSQL URL for factory data writes.
        tls_base_url: The SECOND origin the same stack serves, over real TLS at a
            dotted host (e.g. ``https://proxy.adcp.test:8443``). ``None`` when the
            stack publishes no TLS listener. Additive: only scenarios that need a
            real handshake read it (salesagent-tgzb).
        ca_bundle: ABSOLUTE path to the CA that signed the stack's leaf. Absolute
            because pytest does not always run from the repo root. ``None`` when
            there is no TLS listener to verify.
    """

    base_url: str
    postgres_url: str
    tls_base_url: str | None = None
    ca_bundle: str | None = None


@dataclass(frozen=True)
class TransportResult:
    """Normalized result from any transport dispatch.

    Attributes:
        payload: Pydantic response model (shared assertions target this).
        envelope: Transport-specific metadata (HTTP status, ToolResult, etc.).
        error: Exception raised during dispatch, if any.
        raw_response: Unprocessed transport response (httpx.Response, ToolResult, etc.).
        wire_response: Serialized success-path response body as a dict, captured
            from the real wire (REST HTTP JSON body, MCP structured_content, A2A
            artifact DataPart). ``None`` on error and on IMPL (no wire — serialize
            the typed ``payload`` instead). Lets success-path tests assert the
            actual serialized shape (e.g. the v3.1 format_id federation contract).
        wire_error_envelope: Raw two-layer error envelope dict captured from
            the actual wire bytes (REST HTTP body, MCP ToolError content text,
            A2A failed-Task artifact DataPart). ``None`` on success or on the
            IMPL transport, which has no wire. This is the canonical field
            for error verification — see ``tests/CLAUDE.md`` § Error
            Verification Policy.
        synthesized_error_envelope: Two-layer envelope produced by
            ``build_two_layer_error_envelope`` against the IMPL-caught
            ``AdCPError`` — what production WOULD emit at the boundary.
            ``None`` on success and on REST/MCP/A2A (those expose the real
            wire envelope above instead). Tests asserting on this field
            verify the envelope-builder contract, NOT the wire shape — a
            regression in the production boundary translator would not be
            caught here. Use REST/MCP/A2A for wire-shape regressions.
    """

    payload: BaseModel | None = None
    envelope: dict[str, Any] = field(default_factory=dict)
    error: Exception | None = None
    raw_response: Any = None
    wire_response: dict[str, Any] | None = None
    wire_error_envelope: dict[str, Any] | None = None
    synthesized_error_envelope: dict[str, Any] | None = None

    @property
    def is_success(self) -> bool:
        return self.error is None and self.payload is not None

    @property
    def is_error(self) -> bool:
        return self.error is not None

    def assert_wire_error(
        self,
        code: str,
        *,
        recovery: str | None = None,
        require_suggestion: bool = False,
        message_substr: str | Sequence[str] | None = None,
        field: str | None = None,
    ) -> None:
        """Assert this result carries the AdCP two-layer wire error ``code``.

        Transport-independent: reads the normalized ``wire_error_envelope`` the
        dispatcher captured for whatever transport produced this result, so the
        same call holds on a2a/mcp/rest. Recovery defaults to the PINNED AdCP
        enum's classification for ``code`` (pin-wins), making the assertion
        non-vacuous without per-scenario duplication. This is the SANCTIONED way
        to verify an error on the wire, and new step definitions must not
        hand-roll envelope parsing. It is not yet the ONLY way — call sites that
        still read the envelope by hand are pre-existing debt being routed here
        as they are touched (``uc010_capabilities`` was one, S1.2), so read this
        as the target state plus a migration, not as a claim about today's tree.

        ``field`` pins ``errors[0].field``, the error.json pointer naming WHICH
        request field was rejected. It is a kwarg here rather than a separate
        wire_error_field() helper on purpose: one sanctioned error surface means a
        step never has to decide which mechanism to reach for.

        ``message_substr`` takes ONE substring or a SEQUENCE of them, every one of
        which must appear in ``errors[0].message``. The sequence form is here, on
        the one sanctioned surface, rather than in a step-local loop: a step that
        pins two substrings by hand also stops pinning the CODE, so the same
        message text satisfies it under any error code (measured: the
        two-substring step in ``uc010_capabilities.py`` graded nothing but text).
        An EMPTY sequence pins nothing and is refused rather than passing.

        MATCHING IS CASE-SENSITIVE for every positive substring assertion that
        routes THROUGH HERE (``assert_envelope_shape`` is the matcher and always
        has been case-sensitive; the BDD steps that lowercased both sides now
        route their positive form here). That is not yet a suite-wide invariant —
        it holds for this surface and for the steps migrated onto it, and becomes
        suite-wide only as the remaining hand-rolled readers are routed here. The buyer-facing message is graded as the buyer
        receives it: a casing change is a rendering change, and the whole reason
        these substrings exist is that a rendering defect — the pydantic RootModel
        interpolated as ``root=...`` — is observable only in the exact wire text.
        The one deliberate exception is the NEGATIVE step ("should not contain"),
        which stays case-INSENSITIVE: for an absence claim, ignoring case is the
        STRICTER reading (it also rejects ``ROOT=``), so both rules are the strict
        form of their own direction rather than two spellings of one rule.
        """
        from tests.helpers import assert_envelope_shape

        if message_substr is None:
            substrings: tuple[str, ...] = ()
        elif isinstance(message_substr, str):
            substrings = (message_substr,)
        else:
            substrings = tuple(message_substr)
            assert substrings, (
                "message_substr=[] pins no message content at all — pass the substrings the "
                "scenario names, or omit the argument if the message is not being graded."
            )
        # An EMPTY substring is the same vacuity as an empty sequence wearing a
        # different shape: `"" in anything` is True, so it grades nothing while
        # LOOKING like a message assertion — worse than omitting the argument,
        # which at least reads as "not graded". Refusing both closes the hole in
        # one place rather than trusting every caller to notice.
        for substring in substrings:
            assert substring, (
                f"message_substr={message_substr!r} contains an empty substring, which every "
                "message trivially satisfies. Pass the text the scenario names, or omit the "
                "argument if the message is not being graded."
            )

        meta = _pinned_error_metadata()
        spec = meta.get(code)
        assert spec is not None, (
            f"{code!r} is not a canonical AdCP error code (pinned error-code.json). "
            "Reconcile the feature to a canonical code."
        )
        expected_recovery = recovery if recovery is not None else spec["recovery"]

        envelope = self.wire_error_envelope
        assert envelope is not None, (
            f"Expected a wire rejection with {code}, but no wire_error_envelope was captured "
            f"(is_error={self.is_error}, payload={self.payload!r}). The operation either "
            "succeeded or errored before reaching a transport."
        )
        # One matcher, applied once per pinned substring — the shape/code/recovery
        # checks are idempotent, so this stays a single implementation of "does the
        # buyer-facing message contain X" instead of a second one that would be free
        # to disagree about case.
        for expected_substring in substrings or (None,):
            assert_envelope_shape(
                envelope, code, recovery=expected_recovery, message_substr=expected_substring, field=field
            )
        if require_suggestion:
            suggestion = extract_wire_suggestion(envelope)
            assert suggestion, f"Expected a non-empty suggestion in the {code} wire envelope: {envelope}"

    def assert_signature_challenge(self, code: str) -> None:
        """Assert the VERIFIER refused this dispatch with ``WWW-Authenticate: Signature error="<code>"``.

        The signing counterpart of :meth:`assert_wire_error`, and the single
        harness-provided way to grade a request-signature refusal — a step or test
        must not read the challenge header itself, for the same reason it must not
        hand-roll an error envelope.

        WHAT IS GRADED, and what deliberately is NOT. The claim is the challenge
        header BYTE-EXACTLY, read with :func:`tests.helpers.signing.rejection_code`
        (reused, never re-parsed here: a reader that mishandles the label escaping
        reports "no rejection", which looks exactly like the mechanism not running).
        ``status_code == 401`` is NOT the assertion and never can be — a bare 401 is
        equally produced by the auth middleware rejecting first, by a 404 wearing a
        401, and by the malformed-header precheck
        (``tests/e2e/test_request_signature_required_e2e.py``). The evidence that
        this distinction is load-bearing is first-hand: in salesagent-n78j0.1.1 an
        e2e leg was forced to dispatch UNSIGNED and ``is_success`` still passed, so
        every status-shaped oracle on this path is vacuous by construction.

        NON-VACUITY, the same contract ``assert_wire_error`` carries:

        * an unknown ``code`` is refused up front against the request-family
          vocabulary production itself reads
          (:func:`tests.helpers.signing.request_signature_codes`, derived from
          ``REQUEST_TO_WEBHOOK_CODE`` exactly as ``src/core/metrics.py`` derives
          ``SIGNATURE_ERROR_CODES``), so a typo or an invented code fails loudly
          instead of comparing equal to a ``None`` that never arrives. That
          vocabulary is WIDER than a prefix scan of ``adcp.signing.errors``: the
          verifier also emits ``request_target_uri_malformed``, which carries no
          ``REQUEST_SIGNATURE_`` prefix, and a scan-derived veto made that refusal
          ungradeable;
        * a result with NO raw HTTP response FAILS, naming the two things that
          produce one — the env never called ``enable_request_signing()`` (so the
          leg dispatched in-process, where there is no wire and no verifier), or a
          dispatcher dropped the response. It never passes for want of evidence.
        """
        from tests.helpers.signing import rejection_code, request_signature_codes

        canonical = request_signature_codes()
        assert code in canonical, (
            f"{code!r} is not a request-signature rejection code the verifier can emit "
            f"(the request-family vocabulary, src.core.signing_contract.REQUEST_TO_WEBHOOK_CODE). "
            f"Did you mean one of: "
            f"{', '.join(sorted(c for c in canonical if code.split('_')[-1] in c)) or 'see tests.helpers.signing.request_signature_codes'}?"
        )

        response = self.raw_response

        # Lead with the ACCEPTED case. A result that succeeded is not a missing wire —
        # it is the finding: the seller waved through a request this scenario says it
        # must refuse. Diagnosing that as "the env has no signing capability" sends the
        # reader hunting a harness bug and past the defect, which is the failure mode
        # this whole surface exists to end (SF-4 survived green CI for exactly that
        # reason). Order matters: is_error is checkable on every transport, raw_response
        # is not.
        assert self.is_error, (
            f"Expected the {code!r} signature challenge, but the request was ACCEPTED "
            f"(is_error=False, payload={self.payload!r}). The seller did not refuse a request this "
            "scenario requires it to refuse. If sibling transports DO refuse the same request, that "
            "asymmetry is the finding — the operation never reached a graded posture bucket on this "
            "one, so nothing forced a signature. Read it as a production defect until proven otherwise."
        )

        assert response is not None and hasattr(response, "status_code") and hasattr(response, "headers"), (
            f"Expected the {code!r} signature challenge, and this result IS an error "
            f"(error={self.error!r}) — but it carries no raw HTTP response to read WWW-Authenticate "
            f"from (raw_response={response!r}). Either the env has no signing capability — call "
            "env.enable_request_signing() so the leg dispatches over real HTTP instead of in-process — "
            "or the dispatcher dropped the response. Refusing to grade the refusal on anything else."
        )

        actual = rejection_code(response)
        assert actual == code, (
            f"expected WWW-Authenticate: Signature error={code!r}, got {actual!r} "
            f"(HTTP {response.status_code}, WWW-Authenticate="
            f"{response.headers.get('WWW-Authenticate')!r}). None means the verifier did not refuse this "
            "request at all: a non-401, or a 401 from somewhere else in the stack (auth middleware, a 404 "
            "wearing a 401). A 2xx here usually means the operation never landed in a graded posture "
            "bucket, so the request was waved through unverified."
        )
