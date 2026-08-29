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
import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, TypedDict

from pydantic import BaseModel

from tests.helpers import pinned_schema


@functools.lru_cache(maxsize=1)
def _pinned_error_metadata() -> dict[str, dict[str, str]]:
    """code -> {recovery, suggestion} from the installed SDK's error-code enum.

    Only the ``recovery`` field is actually read by this module (assert_wire_error
    below) — verified safe to source from the SDK tree: the SDK's enum is a
    strict superset of the older vendored fixture (92 vs 64 codes, fixture-only
    set empty) and its ``recovery`` classification is IDENTICAL across every one
    of the 64 shared codes (0 divergences). ``suggestion`` DOES diverge on 4
    codes between the two sources — but this module never reads that field
    (extract_wire_suggestion below reads the WIRE's own suggestion text, not
    this metadata), so that divergence has no effect here. Consumers that DO
    grade ``suggestion`` content (test_architecture_error_suggestion_enum_conformance.py)
    stay on the vendored fixture — see docs/adcp-spec-version.md "Pinned schema sources"
    (which also cites the command to reproduce the 64-code fixture count).
    """
    return pinned_schema.load("error-code.json")["enumMetadata"]


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


def _a2a_wire_envelope_is_synthesized(exc: Exception) -> bool:
    """True when ``_wire_envelope_from_exception`` had to FALL BACK for *exc*.

    The A2A boundary has two rejection shapes. A failed Task carries the
    envelope in its artifact DataPart, and a JSON-RPC ``A2AError`` carries it in
    ``data`` — both are real wire bytes and both get stashed on the reconstructed
    exception as ``_wire_error_envelope``. But an ``A2AError`` raised with NO
    ``data`` reaches the buyer as a bare protocol error with no AdCP envelope at
    all, and the fallback above quietly synthesizes one from the reconstructed
    exception. That synthesis is indistinguishable from the real thing on
    ``wire_error_envelope``, so a test can assert a wire contract the buyer never
    actually receives — a synthesized envelope masquerading as the wire.

    Surfacing the distinction lets a test opt into proving its envelope is real
    (``assert_wire_error(..., require_real_wire=True)``). The fallback itself is
    left in place: existing A2A error tests depend on it, and narrowing it is a
    separate change.

    Lives beside ``_wire_envelope_from_exception`` (whose fallback it detects)
    rather than in ``dispatchers.py``, for the same reason that helper moved
    here: the A2A error unwrap is now ``client.py``'s ``unwrap_a2a_error``,
    shared by both dispatch paths, and it must derive the flag too.
    """
    return not isinstance(getattr(exc, "_wire_error_envelope", None), dict)


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


class InvalidAuthHint(TypedDict):
    """Shape of the transport-blind ``_invalid_auth`` hint the BDD step forwards.

    Names the two keys ONCE, so the producer (``uc002_create_media_buy``'s
    ``_dispatch_full_create``) and both REST consumers (``_run_rest_request`` in
    ``_base``; ``RestE2EDispatcher`` in ``dispatchers``, which forwards it to
    ``client._deliver_e2e_rest``) are bound to one declaration instead of
    re-spelling the string keys three times. ``tenant``
    carries the bare host-routed tenant id the whole non-disclosure contract
    turns on: a typo or a dropped key would otherwise be caught by nothing until
    the leg silently stopped reaching the redacted raise.

    Lives here, in the leaf transport module, rather than in ``_base``: both
    consumers already import down from ``transport``, and ``transport`` imports
    nothing from ``tests.harness``, so neither consumer has to reach up into the
    env layer to name the contract.
    """

    token: str
    tenant: str


def _invalid_auth_headers(hint: InvalidAuthHint) -> dict[str, str]:
    """Realize the ``_invalid_auth`` hint as REST auth headers — one recipe, both legs.

    The in-process leg (``_run_rest_request``) and the e2e leg
    (``client._deliver_e2e_rest``, fed the hint by ``RestE2EDispatcher``) send
    the identical pair. Hand-copying it per leg is how the two silently diverge,
    and a divergence here does not fail loudly — it false-floors the grade,
    because a leg that stops reaching the redacted raise still reports no
    disclosure.
    """
    return {"x-adcp-auth": hint["token"], "x-adcp-tenant": hint["tenant"]}


def _discard_invalid_auth_hint(kwargs: dict[str, Any]) -> None:
    """Drop the transport-blind ``_invalid_auth`` hint on the A2A and MCP legs.

    The BDD invalid-token scenario forwards the bad token uniformly as
    ``_invalid_auth`` (``_dispatch_full_create``) so no step carries
    transport-specific knowledge. A2A and MCP need no special realization: the
    bad token already rides the dispatched identity's ``auth_token`` and the real
    auth chain (header → token → DB lookup → ``ResolvedIdentity``) runs against
    it, so both simply discard the hint. REST is the only transport that realizes
    it specially — the in-process leg routes the bad token through the real
    auth dep as headers (``_run_rest_request``) because its dep override would
    otherwise inject a resolved identity and skip the raise, and the e2e leg
    CONSUMES the hint into real headers (``RestE2EDispatcher``, not this discard)
    because the identity's tenant dict carries only the derived ``pub-<uuid>``
    subdomain, not the bare host-routed tenant id under test.
    """
    kwargs.pop("_invalid_auth", None)


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
        wire_error_envelope_is_synthesized: ``True`` when the A2A dispatcher could
            not find real wire bytes and SYNTHESIZED ``wire_error_envelope`` from
            the reconstructed exception. That happens for an ``A2AError`` raised
            with no ``data``: the buyer receives a bare protocol error carrying no
            AdCP envelope, yet ``wire_error_envelope`` is populated anyway, so a
            wire assertion can pass against an envelope nobody was ever sent.
            Always ``False`` on REST/MCP (they read real bytes) and on success.
            Pass ``require_real_wire=True`` to ``assert_wire_error`` to refuse
            the synthesized envelope. Distinct from ``synthesized_error_envelope``
            above: this flag marks the A2A wire FALLBACK, while that field is the
            IMPL-only envelope no wire was ever involved in.
    """

    payload: BaseModel | None = None
    envelope: dict[str, Any] = field(default_factory=dict)
    error: Exception | None = None
    raw_response: Any = None
    wire_response: dict[str, Any] | None = None
    wire_error_envelope: dict[str, Any] | None = None
    synthesized_error_envelope: dict[str, Any] | None = None
    wire_error_envelope_is_synthesized: bool = False

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
        require_real_wire: bool = False,
    ) -> None:
        """Assert this result carries the AdCP two-layer wire error ``code``.

        Transport-independent: reads the normalized ``wire_error_envelope`` the
        dispatcher captured for whatever transport produced this result, so the
        same call holds on a2a/mcp/rest. Recovery defaults to the PINNED AdCP
        enum's classification for ``code`` (pin-wins), making the assertion
        non-vacuous without per-scenario duplication. This is the single
        harness-provided way to verify an error on the wire — step definitions
        must not hand-roll envelope parsing.

        Args:
            require_real_wire: Refuse an envelope the A2A dispatcher SYNTHESIZED
                from the reconstructed exception (see
                ``wire_error_envelope_is_synthesized``). Use it when the point of
                the test is what the buyer actually receives — a security or
                disclosure contract — rather than the envelope shape alone.
                Without it, an ``A2AError`` raised with no ``data`` still
                produces a passing wire assertion even though the buyer got a
                bare protocol error with no AdCP envelope.
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
        assert not (require_real_wire and self.wire_error_envelope_is_synthesized), (
            f"Expected {code} on the REAL wire, but the envelope was synthesized from the reconstructed "
            f"exception: the transport raised with no envelope attached, so the buyer received a bare "
            f"protocol error carrying no AdCP envelope at all. Synthesized envelope: {envelope}"
        )
        assert_envelope_shape(envelope, code, recovery=expected_recovery, message_substr=message_substr)
        if require_suggestion:
            suggestion = extract_wire_suggestion(envelope)
            assert suggestion, f"Expected a non-empty suggestion in the {code} wire envelope: {envelope}"
