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

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel


def _pinned_error_metadata() -> dict[str, dict[str, str]]:
    """code -> {recovery, suggestion} — delegates to the PUBLIC ``pinned_error_metadata``.

    The private module-local name is retained for the in-module methods and the existing
    ``from tests.harness.transport import _pinned_error_metadata`` call sites; the loader and
    its per-field source contract now live once in ``tests.helpers.error_metadata`` (#1329).
    """
    from tests.helpers.error_metadata import pinned_error_metadata

    return pinned_error_metadata()


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


def _envelope_from_adcp_error(exc: Exception) -> dict[str, Any] | None:
    """Build a SYNTHESIZED envelope from an AdCPError instance.

    Used by ImplDispatcher (``tests/harness/dispatchers.py``) to populate the
    separate ``synthesized_error_envelope`` field — IMPL has no wire by
    definition and ``wire_error_envelope`` is reserved for real wire bytes
    captured by REST/MCP/A2A. Production code uses the same
    ``build_two_layer_error_envelope`` helper at the boundary, so the
    synthesized envelope matches what production would emit for the same
    exception. It does NOT verify that a regression in
    ``build_two_layer_error_envelope`` actually reaches the wire.

    Lives here (not in ``dispatchers.py`` or ``client.py``) because both of
    those modules need it: ``dispatchers.py`` for the in-process dispatchers,
    ``client.py`` for the generic ``AdCPTestClient`` error path. Housing it
    in either would force the other to reach back across the dispatch-core
    boundary, which is exactly the mutual-lazy-import cycle this module
    breaks.

    A2A and REST tests asserting on ``result.wire_error_envelope`` see
    REAL wire bytes:
        - A2A: the artifact DataPart, attached to the reconstructed
          ``AdCPError`` as ``_wire_error_envelope`` by
          ``tests.harness._base._envelope_to_adcp_error``.
        - REST: the HTTP response body, captured directly by RestDispatcher.
        - MCP: the JSON string in ``ToolError``, parsed by McpDispatcher.
    """
    from src.core.exceptions import AdCPError, build_two_layer_error_envelope

    if isinstance(exc, AdCPError):
        return build_two_layer_error_envelope(exc)
    return None


def _wire_envelope_from_exception(exc: Exception) -> dict[str, Any] | None:
    """Prefer the REAL wire envelope stashed by the harness; fall back to synthesized.

    When the A2A pipeline reconstructs an AdCPError from a failed Task's
    artifact DataPart, ``tests.harness._base._envelope_to_adcp_error``
    attaches the captured envelope to the exception as
    ``_wire_error_envelope``. This helper returns that real wire envelope
    if present; otherwise falls back to ``_envelope_from_adcp_error``
    (synthesized — same helper production calls).
    """
    real_wire = getattr(exc, "_wire_error_envelope", None)
    if isinstance(real_wire, dict):
        return real_wire
    return _envelope_from_adcp_error(exc)


def _envelope_from_mcp_error(exc: Exception) -> dict[str, Any] | None:
    """Extract the wire envelope from an MCP ToolError's JSON string."""
    from fastmcp.exceptions import ToolError

    if not isinstance(exc, ToolError):
        return None
    try:
        envelope = json.loads(str(exc))
        if isinstance(envelope, dict) and "errors" in envelope:
            return envelope
    except (json.JSONDecodeError, TypeError):
        pass
    return None


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


# The ONE identity-argument omission sentinel for the whole dispatch core
# (tests/harness/client.py, dispatchers.py, _base.py, _mixins.py — plus
# tests/helpers/mcp_envelope_capture.py, which carries the same distinction
# outside tests/harness/). Distinguishes "the caller did not pass identity="
# (fall back to whatever default THAT call site uses — env.identity_for(),
# self.identity, delegate-by-omission, PrincipalFactory.make_identity(), ...)
# from an EXPLICIT identity=None (deliberately unauthenticated dispatch).
# Previously reimplemented as a private object() in seven different function
# bodies plus two other module-level sentinels (client.py, mcp_envelope_
# capture.py) — this is the one shared object identity every comparison uses;
# each call site keeps its OWN fallback logic when it detects the sentinel,
# never folded into this constant. Scoped to the identity-argument omission
# disease specifically — other object()-as-sentinel uses in tests/harness/
# for unrelated fields (e.g. media_buy_create.py's OMIT_IDEMPOTENCY_KEY) are
# a different sentinel family and are not consolidated here.
NO_IDENTITY_OVERRIDE = object()


class MissingToolNameError(NotImplementedError):
    """A legacy ``env.call_via(transport, **kwargs)`` E2E dispatch had no way to
    derive the tool/skill name (no ``tool_name=`` kwarg, no per-env attribute
    to introspect it from).

    The ONE exception type for this failure mode, replacing what used to be a
    per-dispatcher fork (``TypeError`` in one, ``NotImplementedError`` in the
    other). Subclasses ``NotImplementedError`` deliberately: that is the one
    exception ``AdCPTestClient.call()`` re-raises as a hard wiring failure
    instead of downgrading into an error ``TransportResult`` — a missing tool
    name is a harness bug, not a simulated AdCP rejection.
    """


@dataclass(frozen=True)
class E2EConfig:
    """Configuration for E2E transport dispatch.

    Attributes:
        base_url: Docker stack URL (e.g., ``http://localhost:8092``).
        postgres_url: Docker PostgreSQL URL for factory data writes.
    """

    base_url: str
    postgres_url: str


# Fields `_serialize_for_a2a` adds to an A2A artifact DataPart. They are
# populated by the PROTOCOL layer (the pin's Protocol Envelope arm) and are not
# declared on any Pydantic response model, so they must come off before a body
# is validated — under extra="forbid" they are a hard ValidationError. The
# captured `wire_response` keeps them: siblings assert on the full envelope.
A2A_PROTOCOL_ENVELOPE_FIELDS = ("message", "success")


def strip_a2a_protocol_fields(data: dict[str, Any]) -> dict[str, Any]:
    """A copy of *data* without the A2A protocol-envelope fields.

    One definition, three call sites (``_run_a2a_handler``, the client's
    ``_deliver_a2a``, and ``BaseTestEnv._deliver_via_client``). Each used to
    spell the same two ``pop`` calls itself, so adding a third protocol field
    would have needed finding all of them.
    """
    return {k: v for k, v in data.items() if k not in A2A_PROTOCOL_ENVELOPE_FIELDS}


# The two values TransportResult.envelope["status"] may take. A DERIVED enum,
# never a synthesized HTTP status_code: fabricating an integer for MCP/A2A would
# turn today's silent no-op into a loud tautology — the harness asserting != 500
# against a number the harness itself invented.
DERIVED_STATUS_ADCP_ERROR = "adcp_error"
DERIVED_STATUS_TRANSPORT_FAULT = "transport_fault"


def derive_error_status(wire_error_envelope: dict[str, Any] | None) -> str:
    """Did the seller answer with a structured AdCP envelope, or fault?

    Reads each transport's OWN authentic evidence, because that is exactly what
    ``wire_error_envelope`` is built from — REST's real HTTP body, A2A's failed
    Task artifact DataPart, MCP's ToolError JSON. Recovering an envelope from any
    of them means the seller produced a structured AdCP rejection; recovering
    none means the request died as a transport fault before any envelope existed.

    This is the signal the storyboard Then actually means by "not a 500 or
    non-AdCP error shape", expressed so it grades on all three transports instead
    of only the one that happens to carry an HTTP status.
    """
    return DERIVED_STATUS_ADCP_ERROR if wire_error_envelope else DERIVED_STATUS_TRANSPORT_FAULT


@dataclass(frozen=True)
class DeliverResult:
    """What one transport delivery produced: the parsed payload AND its wire bytes.

    The harness used to carry these on two different channels — the payload came
    back as the return value of ``env.call_mcp``/``call_a2a``, while the wire was
    stashed on ``env._last_wire_response`` and read back ACROSS the object
    boundary by the dispatchers. Two channels for one delivery is what let a
    second writer appear (six sites on BaseTestEnv, three more in client.py) and
    what let the wire silently go stale, since nothing tied a stash to the call
    that produced it.

    One return value closes that structurally: there is no attribute for a second
    writer to write. ``wire_response`` is None where no wire exists (IMPL) or
    where the dispatch path does not observe one (the legacy
    ``_run_mcp_wrapper``).

    The #1858 round-2 remediation;
    pinned by ``test_architecture_harness_single_dispatch``.
    """

    payload: Any
    wire_response: dict[str, Any] | None = None


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

    def require_wire(self) -> dict[str, Any]:
        """The success-path body the buyer actually received, or a loud failure.

        The success-side counterpart of :meth:`assert_wire_error`, and for the same
        reason: the guarded read belongs on the object that HOLDS the wire, so every
        caller gets the same guard instead of re-deriving it. Three copies of this
        check had grown across the suite, and a fourth partial one — each free to
        drift, and each a place where a missing ``wire_response`` could fall through
        to a harness-side reconstruction and assert nothing.

        Two failures are distinguished because they mean different things: an error
        result was never going to have a success body, while a success result with no
        stashed body means the dispatch bypassed the real pipeline — the silent
        tautology this guard exists to make loud.
        """
        assert self.is_success, f"expected a success wire body, got error {self.error!r}"
        assert self.wire_response is not None, (
            "no wire body was stashed for a successful call — the dispatch bypassed the "
            "real pipeline, so any assertion on it would grade a harness reconstruction "
            "rather than what the buyer received"
        )
        return self.wire_response

    def assert_wire_error(
        self,
        code: str,
        *,
        recovery: str | None = None,
        require_suggestion: bool = False,
        message_substr: str | None = None,
        field: str | None = None,
        field_substr: str | None = None,
        suggestion_substr: str | None = None,
    ) -> None:
        """Assert this result carries the AdCP two-layer wire error ``code``.

        Transport-independent: reads the normalized ``wire_error_envelope`` the
        dispatcher captured for whatever transport produced this result, so the
        same call holds on a2a/mcp/rest. Recovery defaults to the PINNED AdCP
        enum's classification for ``code`` (pin-wins), making the assertion
        non-vacuous without per-scenario duplication. This is the single
        harness-provided way to verify an error on the wire — step definitions
        must not hand-roll envelope parsing.

        ``message_substr`` / ``suggestion_substr`` pin the buyer-facing message and
        suggestion CONTENT (not merely their presence), so a transport-blind scenario
        can assert the SAME strings on every transport — a per-transport message/
        suggestion fork (e.g. MCP surfacing a different text than A2A/REST) then reddens
        instead of passing because ``field`` alone matched (#1329).
        """
        from tests.helpers import assert_envelope_shape

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
        assert_envelope_shape(
            envelope,
            code,
            recovery=expected_recovery,
            message_substr=message_substr,
            field=field,
            field_substr=field_substr,
        )
        if require_suggestion or suggestion_substr is not None:
            suggestion = extract_wire_suggestion(envelope)
            assert suggestion, f"Expected a non-empty suggestion in the {code} wire envelope: {envelope}"
            if suggestion_substr is not None:
                assert suggestion_substr in suggestion, (
                    f"Expected suggestion to contain {suggestion_substr!r} in the {code} wire envelope, "
                    f"got {suggestion!r}"
                )

    def assert_wire_error_shape(self) -> None:
        """Assert a well-formed two-layer AdCP error envelope WITHOUT pinning the code.

        Code-agnostic structural grade: both layers present, their codes non-empty AND
        agreeing, and a recovery hint set. The SPECIFIC code is pinned separately (by
        ``assert_wire_error`` or a following ``the error code is "X"`` step), so this is the
        single home for "the envelope is a real two-layer error" that step definitions must
        not re-hand-roll by digging ``adcp_error.code``/``errors[0].code``/``recovery`` out
        of the dict themselves. A single-layer or code-less envelope ("flip the code to
        garbage and this stays green" no longer holds) fails here (#1329).
        """
        envelope = self.wire_error_envelope
        assert envelope is not None, (
            f"expected a two-layer wire error envelope, but none was captured "
            f"(is_error={self.is_error}, payload={self.payload!r})"
        )
        top = (envelope.get("adcp_error") or {}).get("code")
        leaf = (envelope.get("errors") or [{}])[0].get("code")
        assert top and leaf and top == leaf, f"malformed/disagreeing two-layer error codes: {envelope}"
        assert (envelope.get("errors") or [{}])[0].get("recovery"), f"error missing recovery hint: {envelope}"

    def assert_secret_absent(self, secret: str) -> None:
        """Assert ``secret`` reaches NEITHER the success wire body NOR the error envelope.

        Scans BOTH ``wire_response`` (success-path body) and ``wire_error_envelope`` (error
        envelope) — a credential must never be echoed on either the accept OR the reject
        path. Raises LOUDLY if NEITHER is populated: nothing was captured to scan, so a
        green here would be vacuous (the dispatch neither succeeded with a body nor errored
        with an envelope). Single home for the "credential absent on the wire" invariant so
        the BDD leak steps + integration redaction tests stop each re-implementing a
        ``secret not in str(envelope)`` scan (#1329).
        """
        haystacks: list[tuple[str, dict[str, Any]]] = []
        if self.wire_response is not None:
            haystacks.append(("wire_response", self.wire_response))
        if self.wire_error_envelope is not None:
            haystacks.append(("wire_error_envelope", self.wire_error_envelope))
        assert haystacks, (
            "assert_secret_absent captured no wire (neither wire_response nor wire_error_envelope "
            f"populated) — nothing to scan (is_error={self.is_error}, payload={self.payload!r})"
        )
        for name, body in haystacks:
            assert secret not in str(body), f"leaked secret reached the {name}: {body!r}"

    def assert_account_error(self, account_id: str, code: str, *, recovery: str | None = None) -> None:
        """Assert the per-account entry for ``account_id`` (SUCCESS envelope) carries ``code``.

        A per-account failure lives under ``accounts[]`` (``status=failed`` + a per-account
        ``errors[]``) of the partial-failure SUCCESS variant, NOT the top-level error
        envelope (spec oneOf: accounts XOR adcp_error). Finds the entry by its echoed ref
        (the ref-echo grade — raises if the requested id was not echoed), asserts it failed,
        and pins ``code`` + recovery on its ``errors[]``. Recovery defaults to the PINNED
        AdCP enum's classification for ``code`` (pin-wins) exactly as ``assert_wire_error``
        does (reuses ``_pinned_error_metadata``), so a per-account recovery drift reddens
        without a per-scenario literal (#1329). Single home for per-account wire
        reads so the ``then_per_account_*`` steps stop hand-rolling the accounts[] scan.
        """
        meta = _pinned_error_metadata()
        spec = meta.get(code)
        assert spec is not None, (
            f"{code!r} is not a canonical AdCP error code (pinned error-code.json). "
            "Reconcile the feature to a canonical code."
        )
        expected_recovery = recovery if recovery is not None else spec["recovery"]

        body = self.wire_response
        assert body is not None, (
            "assert_account_error needs the success-path wire (wire_response); none captured "
            f"(is_error={self.is_error}). A per-account failure is the SUCCESS variant, not a "
            "top-level error."
        )
        accounts = body.get("accounts") or []
        matched = [a for a in accounts if (a.get("account") or {}).get("account_id") == account_id]
        available = [(a.get("account") or {}).get("account_id") for a in accounts]
        assert matched, f"no wire account {account_id!r}; available: {available}"
        acct = matched[0]
        assert acct.get("status") == "failed", (
            f"account {account_id} expected per-account status 'failed', got {acct.get('status')!r}: {acct}"
        )
        errs = acct.get("errors") or []
        codes = {e.get("code") for e in errs}
        assert code in codes, f"account {account_id} per-account errors {codes} do not include {code!r}"
        recoveries = {e.get("recovery") for e in errs if e.get("code") == code}
        assert recoveries == {expected_recovery}, (
            f"account {account_id} {code} recovery {recoveries} must equal the pinned enum {expected_recovery!r}"
        )
