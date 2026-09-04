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
from typing import Any

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

    ImplDispatcher is its ONLY caller, and deliberately so: no other transport
    may hand a rebuilt envelope to a test. It lives here rather than in
    ``dispatchers.py`` because this module is the dispatch-core both
    ``dispatchers.py`` and ``client.py`` import from; housing it in either would
    force the other to reach back across that boundary, which is exactly the
    mutual-lazy-import cycle this module breaks.

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
    """The REAL wire envelope stashed by the harness, or None. NEVER synthesized.

    When the A2A pipeline reconstructs an AdCPError from a failed Task's
    artifact DataPart, ``tests.harness._base._envelope_to_adcp_error`` attaches
    the captured envelope to the exception as ``_wire_error_envelope``. That
    stash — real bytes that actually came back — is the only thing this helper
    will hand out.

    It used to fall back to ``_envelope_from_adcp_error`` above, the same builder
    production calls, and return the result under ``wire_error_envelope`` — the
    field named for what actually crossed the wire. A scenario asserting on that
    field then graded the harness rebuilding an envelope from the exception it
    had just caught, which passes whether or not production emitted anything at
    all. Making the synthesized field private did not close
    that channel: the laundered copy arrives under the name of the thing it is
    impersonating.

    ``None`` is the honest answer when nothing crossed the wire. A transport that
    genuinely has no wire says so through ``has_wire=False`` and offers
    ``_synthesized_error_envelope`` under its OWN name, as ImplDispatcher does.
    Do not reintroduce the fallback here; pinned by
    ``tests/unit/test_harness_mcp_never_synthesizes.py``.
    """
    real_wire = getattr(exc, "_wire_error_envelope", None)
    return real_wire if isinstance(real_wire, dict) else None


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


def _enum_value(x: Any) -> Any:
    """Unwrap enum-or-str discriminators to a comparable value."""
    return x.value if hasattr(x, "value") else x


def _transport_has_no_wire(transport: Transport) -> bool:
    """True when *transport* is IMPL (no success-path wire to stash)."""
    return _enum_value(transport) == Transport.IMPL.value


def _action_value(obj: Any) -> Any:
    """Unwrap dict/enum/str action discriminators to a comparable value."""
    if isinstance(obj, dict):
        action = obj.get("action")
        if isinstance(action, dict):
            return action.get("value")
        return _enum_value(action)
    return _enum_value(getattr(obj, "action", None))


def _iter_creative_entries(container: Any) -> list[Any]:
    """Return the creatives list from a typed response or wire dict (no ``results`` key)."""
    if container is None:
        return []
    if isinstance(container, dict):
        return list(container.get("creatives") or [])
    return list(getattr(container, "creatives", None) or [])


def _response_has_failed_creative(response: Any) -> bool:
    """True when a typed sync response carries a per-creative ``action == "failed"``.

    Single home for the discriminator shared by ``first_failed_creative_advisory``
    (wire-present-but-dropped-advisory case) and the BDD ``then_error`` reader
    (wire-absent case) — decides whether a missing/advisory-free wire on a
    wire-stashing transport is a buyer-facing regression (loud) vs a genuinely
    envelope-only error path that never carries a success+advisory response
    (soft ``None``, e.g. UC003/UC019 validation failures).
    """
    for creative in _iter_creative_entries(response):
        if _action_value(creative) == "failed":
            return True
    return False


def first_failed_creative_advisory(
    wire: dict[str, Any] | None,
    *,
    transport: Transport,
    response: Any = None,
    wire_is_proxy: bool = False,
    require_real_wire: bool = False,
) -> dict[str, Any] | None:
    """First failed ``creatives[].errors[0]`` from a success-path wire body.

    Grades the buyer-facing nested advisory inside a successful ``sync_creatives``
    artifact (no ``wire_error_envelope``). Filters to ``action == "failed"``.

    Loud guards (same contract as BDD ``wire_dict`` / ``wire_field``):
    - A wire-stashing transport (REST/A2A/MCP/…) that did not stash ``wire`` at
      all raises unconditionally instead of silently returning ``None``.
    - A wire-stashing transport that stashed a *present* wire whose failed
      creative dropped ``errors[]`` — the buyer-facing regression this
      accessor exists to catch — raises when the caller passes the typed
      ``response`` and it shows a failed creative (``_response_has_failed_creative``).
      Without ``response`` this case cannot be distinguished from "no failed
      creative on the wire" and returns ``None``.
    - When ``require_real_wire=True`` and ``wire_is_proxy=True`` (CreativeSyncEnv
      A2A ``model_dump`` proxy — not Task/Artifact DataPart; see #1919), raises
      instead of treating the proxy as real A2A framing evidence.

    IMPL transport legitimately has no wire and returns ``None``.
    """
    if require_real_wire and wire_is_proxy:
        raise AssertionError(
            f"{transport}: wire_response is a model_dump proxy — not real A2A Task/Artifact "
            "framing (see #1919); refuse require_real_wire grading"
        )
    no_wire = _transport_has_no_wire(transport)
    if wire is None:
        if no_wire:
            return None
        raise AssertionError(f"{transport}: wire_response missing — env does not stash success-path wire")
    if not isinstance(wire, dict):
        return None
    for creative in _iter_creative_entries(wire):
        if not isinstance(creative, dict):
            continue
        if _action_value(creative) != "failed":
            continue
        errs = creative.get("errors") or []
        if errs and isinstance(errs[0], dict):
            return errs[0]
    if not no_wire and _response_has_failed_creative(response):
        raise AssertionError(f"{transport}: advisory missing from wire body — failed creative dropped errors[]")
    return None


def assert_wire_advisory(
    wire: dict[str, Any] | None,
    code: str,
    *,
    recovery: str | None = None,
    suggestion: str | None = None,
    transport: Transport,
    response: Any = None,
    wire_is_proxy: bool = False,
    require_real_wire: bool = False,
) -> dict[str, Any] | None:
    """Assert the first failed creative's nested advisory on the success-path wire.

    On wire transports the advisory **must** be present (dropped ``errors[]`` is
    a buyer-facing regression) — this call is unconditionally loud regardless of
    ``response`` (see ``first_failed_creative_advisory``'s unconditional
    missing-wire raise; callers here already know they expect an advisory). On
    IMPL, returns ``None`` without asserting. When an advisory is present,
    grades ``code`` and optional ``recovery`` and ``suggestion`` — the single
    wire oracle for BDD Then-steps and integration tests alike.

    Pass ``require_real_wire=True`` to refuse CreativeSyncEnv's A2A
    ``model_dump`` proxy (``wire_is_proxy=True`` / envelope flag) — that path
    is not Task/Artifact framing (#1919).
    """
    advisory = first_failed_creative_advisory(
        wire,
        transport=transport,
        response=response,
        wire_is_proxy=wire_is_proxy,
        require_real_wire=require_real_wire,
    )
    if _transport_has_no_wire(transport):
        return advisory
    assert advisory, "advisory missing from wire body"
    assert advisory.get("code") == code, f"unexpected wire advisory code: {advisory.get('code')!r}"
    if recovery is not None:
        actual = advisory.get("recovery")
        actual_str = _enum_value(actual)
        assert actual_str == recovery, f"unexpected wire advisory recovery: {actual_str!r}"
    if suggestion is not None:
        actual_suggestion = advisory.get("suggestion")
        assert actual_suggestion == suggestion, f"unexpected wire advisory suggestion: {actual_suggestion!r}"
    return advisory


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
        base_url: Docker stack URL (e.g., ``http://localhost:8092``). Stays
            PLAINTEXT — 487 bdd_e2e and 95 e2e tests target it and do not move.
        postgres_url: Docker PostgreSQL URL for factory data writes.
        tls_base_url: The SECOND origin the same stack serves, over real TLS at a
            dotted host (e.g. ``https://proxy.adcp.test:8443``). ``None`` when the
            stack publishes no TLS listener. Additive: only scenarios that need a
            real handshake read it (#1291).
        ca_bundle: ABSOLUTE path to the CA that signed the stack's leaf. Absolute
            because pytest does not always run from the repo root. ``None`` when
            there is no TLS listener to verify.
    """

    base_url: str
    postgres_url: str
    tls_base_url: str | None = None
    ca_bundle: str | None = None


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
            artifact DataPart) for most envs. Some envs blocked on a documented
            real-dispatch bug (e.g. ``CreativeSyncEnv``'s A2A path) instead stash
            a re-serialized ``model_dump()`` proxy and flag
            ``envelope["wire_response_is_proxy"] = True`` — that field is NOT the
            real Task/Artifact framing for those envs. ``None`` on error and on
            IMPL (no wire — serialize the typed ``payload`` instead). Lets
            success-path tests assert the actual serialized shape (e.g. the v3.1
            format_id federation contract).
        wire_error_envelope: Raw two-layer error envelope dict captured from
            the actual wire bytes (REST HTTP body, MCP ToolError content text,
            A2A failed-Task artifact DataPart). ``None`` on success or on the
            IMPL transport, which has no wire. This is the canonical field
            for error verification — see ``tests/CLAUDE.md`` § Error
            Verification Policy.
        has_wire: Whether these bytes crossed a REAL wire, declared by the
            dispatcher AT CONSTRUCTION. Positive and required — never inferred
            at a read site from which transport enum happens to be in play,
            because that inference breaks (or, worse, silently reclassifies)
            the day ``Transport.IMPL`` is removed.

            REQUIRED and keyword-only, deliberately: a default would make
            omission mean "no wire", so a forgetful new dispatcher would send
            readers down the re-serialize path and a wire-shape assertion would
            pass green against a ``model_dump`` — the silent tautology the wire
            readers exist to raise on. Omitting it is a ``TypeError`` instead.

            Declared PER SITE, not per transport class: it is True only where
            the construction is downstream of an actual send/receive. A wire
            dispatcher's "missing config" guard constructs a result for a request
            that never left. Its catch-all ``except`` arm is a STRADDLE — it may
            fire before OR after bytes moved, and cannot tell which — so it
            declares False, because claiming a wire that may not exist is the
            failure mode that matters here: it would send a reader looking for a
            capture nothing produced.

            ``has_wire=True`` with ``wire_response is None`` on a success path
            means the env failed to STASH the wire. That is a harness bug to
            raise on loudly; it must never fall back to serializing the typed
            payload, which would assert nothing about the wire.

            SCOPE — this predicate governs the SUCCESS path only, and
            deliberately does NOT feed ``assert_wire_error``'s no-envelope
            diagnostic (which the lane in #1802 originally specified).
            The reason is concrete: a dispatcher's catch-all arm declares
            ``has_wire=False`` because it may fire before anything was sent, yet
            it can still derive a ``wire_error_envelope`` from the exception —
            ``A2ADispatcher``'s does exactly that. Wiring ``has_wire`` into that
            diagnostic would therefore report a genuine wire rejection as "no
            wire", which is worse than the message it replaces. Error-path
            wire-presence needs its own per-site declaration; that is not this
            lane's, and inventing one here would be the same identity-inference
            mistake in a new spelling.
        _synthesized_error_envelope: Two-layer envelope produced by
            ``build_two_layer_error_envelope`` against the IMPL-caught
            ``AdCPError`` — what production WOULD emit at the boundary.
            ``None`` on success and on REST/MCP/A2A (those expose the real
            wire envelope above instead). PRIVATE: read it through
            :meth:`error_envelope`, which is the only place allowed to decide
            that this value may stand in for a wire. A test that reads it
            directly verifies the envelope-builder contract against itself —
            production and the harness compute it from the same in-memory
            exception — so a regression in the boundary translator cannot be
            caught that way. Use REST/MCP/A2A for wire-shape regressions.
    """

    payload: BaseModel | None = None
    envelope: dict[str, Any] = field(default_factory=dict)
    error: Exception | None = None
    raw_response: Any = None
    wire_response: dict[str, Any] | None = None
    wire_error_envelope: dict[str, Any] | None = None
    _synthesized_error_envelope: dict[str, Any] | None = None
    has_wire: bool = field(kw_only=True)

    @property
    def is_success(self) -> bool:
        return self.error is None and self.payload is not None

    @property
    def is_error(self) -> bool:
        return self.error is not None

    def error_envelope(self) -> dict[str, Any]:
        """The two-layer error envelope this dispatch produced. Raises if there is none.

        Three branches, spelled out because getting them wrong is this lane's
        whole subject:

        1. a captured wire envelope is present -> return it;
        2. no wire was captured AND the dispatcher declared no wire AND a
           synthesized envelope exists -> return the synthesized one;
        3. otherwise -> RAISE.

        Branch 2 is reachable only on IMPL, and NOT because ``has_wire`` says
        so. ``has_wire`` is ``False`` on every A2A, MCP and IMPL error — a
        catch-all may fire before anything was sent — so keying on it alone
        would hand back a rebuilt envelope on transports that HAVE a wire, and
        on A2A and MCP it would discard a real captured one. What actually
        isolates IMPL is that IMPL is the only dispatcher that populates the
        synthesized field at all. That single-producer invariant is the load
        bearing one, and it is pinned by
        ``tests/unit/test_harness_mcp_never_synthesizes.py``.

        Branch 3 covers the case every operand is ``None`` — an A2A catch-all
        that derived nothing. Falling back to re-serializing the typed payload
        there would assert nothing about the wire while looking green, which is
        the tautology this reader exists to prevent.
        """
        envelope = self.error_envelope_or_none()
        assert envelope is not None, (
            "Expected an error envelope, but none was captured "
            f"(is_error={self.is_error}, payload={self.payload!r}). The operation either "
            "succeeded or errored before reaching a transport."
        )
        return envelope

    def error_envelope_or_none(self) -> dict[str, Any] | None:
        """:meth:`error_envelope`, returning ``None`` instead of raising.

        For the callers that branch on envelope-presence as CONTROL FLOW rather
        than reading it — an MCP dispatch can fail with a ``ToolError`` that is
        genuinely not an AdCP envelope, and collapsing that branch would turn a
        correct assertion into an error. The success path already ships this
        same pair: ``_wire_or_none`` returns ``None`` for a declared no-wire
        while ``wire_field``/``wire_dict`` raise. Prefer the raising one.
        """
        if isinstance(self.wire_error_envelope, dict):
            return self.wire_error_envelope
        if not self.has_wire and isinstance(self._synthesized_error_envelope, dict):
            return self._synthesized_error_envelope
        return None

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
            "wire_response is None on a successful call — no wire body was stashed, so the "
            "dispatch bypassed the real pipeline and any assertion on it would grade a "
            "harness reconstruction rather than what the buyer received"
        )
        return self.wire_response

    def assert_wire_error(
        self,
        code: str,
        *,
        recovery: str | None = None,
        require_suggestion: bool = False,
        message_substr: str | None = None,
    ) -> None:
        """Assert this result carries the AdCP two-layer wire error ``code``.

        Transport-independent: reads the normalized ``wire_error_envelope`` the
        dispatcher captured for whatever transport produced this result, so the
        same call holds on a2a/mcp/rest. Recovery defaults to the PINNED AdCP
        enum's classification for ``code`` (pin-wins), making the assertion
        non-vacuous without per-scenario duplication. This is the single
        harness-provided way to verify an error on the wire — step definitions
        must not hand-roll envelope parsing.
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
        assert_envelope_shape(envelope, code, recovery=expected_recovery, message_substr=message_substr)
        if require_suggestion:
            suggestion = extract_wire_suggestion(envelope)
            assert suggestion, f"Expected a non-empty suggestion in the {code} wire envelope: {envelope}"
