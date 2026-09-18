"""Shared dispatch helper for BDD domain step definitions.

Provides a single implementation of the transport-aware dispatch pattern
used across UC-004, UC-011, and future domain step files.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypedDict, cast

from tests.bdd.payload_capture import record_dispatched_request
from tests.factories.malformed import assert_declared_malformations
from tests.harness.transport import NO_IDENTITY_OVERRIDE, Transport

if TYPE_CHECKING:
    from tests.harness._base import WireError
    from tests.harness.transport import TransportResult

# NOTE: NO_IDENTITY_OVERRIDE is IMPORTED, never re-declared. A local
# `_SENTINEL = object()` renamed into this name would be a SECOND distinct
# object wearing the canonical name, and `credential is not NO_IDENTITY_OVERRIDE`
# would then compare against the local one — so a caller passing the harness's
# real sentinel (meaning "no override") would be misread as HAVING overridden
# the credential. One object, one name.

#: Opt-in: a Given sets this when the scenario's claim is about WEBHOOK CREDENTIALS
#: rather than about one request. The seller must then answer the same way at every
#: place the transport lets a buyer hand it credentials — see
#: ``BaseTestEnv.credential_registrations``. Opt-in rather than inferred from a
#: ``push_notification_config`` being present, because most scenarios that carry one
#: are about the WEBHOOK (its URL, its echo), not about the credential-registration
#: surface, and they must keep dispatching exactly one request.
GRADE_EVERY_CREDENTIAL_LOCATION = "grade_every_credential_location"

#: Where the per-location results land: ``((location, TransportResult), ...)``.
CREDENTIAL_REGISTRATIONS = "credential_registrations"


class WireCtx(TypedDict, total=False):
    """What a WIRE dispatch may publish into ``ctx``, and with what types.

    The type that made the defect possible was ``ctx`` itself: an unowned dict slot
    with no invariant, written from two step modules and read hundreds of times.
    Naming the shape closes the WRITE at typecheck, which no runtime trap can -- a
    sentinel object only fires when something READS it, and readers that merely test
    ``is not None`` or format ``type(x).__name__`` slip past silently
    (salesagent-3dawm.15).

    ``total=False`` because a dispatch publishes the success keys or the error key,
    never both.

    There is deliberately NO ``response`` key. A provenance-stripped copy of the
    payload cannot tell a Then whether it is reading a wire fact or an in-process
    reconstruction — which is how a self-grading transport stayed green — and the
    key had three writers with three meanings. Steps read the dispatch's own
    ``TransportResult`` through ``tests/bdd/steps/_outcome_helpers.py``'s
    ``require_payload`` / ``payload_or_none``; modules whose When calls production
    directly stash under the explicitly-named ``ctx["self_dispatched_response"]``,
    which those accessors know by name. Enforced by
    ``test_architecture_bdd_wire_discipline``'s Check D (empty allowlist).
    """

    result: TransportResult
    wire_response: dict[str, Any]
    wire_error_envelope: dict[str, Any]
    #: The CARRIER, never a production error class. On a wire transport a failed
    #: dispatch raises ``WireError``, which holds the envelope verbatim; typing the
    #: slot as that carrier is what makes
    #:
    #:     ctx["error"] = AdCPValidationError(field="x")
    #:
    #: a mypy error rather than a convention someone must remember not to break.
    #: That is exactly the subject test_architecture_bdd_wire_discipline's Check A
    #: detects today, closed here at the type instead of watched for.
    #:
    #: The key is still PUBLISHED because ~40 step sites reach the envelope through
    #: it (``_wire_of(error)`` reads ``error.envelope``). Dropping it outright is a
    #: READER migration, not a recorder change -- measured: doing it without
    #: migrating them first costs 41 UC-006 failures.
    error: WireError


def _populate_ctx_from_result(ctx: WireCtx, result: TransportResult) -> None:
    """Project a ``TransportResult`` into ``ctx``. The SINGLE writer.

    Every dispatch path routes through here — :func:`dispatch_request` via
    ``env.call_via``, :func:`dispatch_via_client` via ``AdCPTestClient.call``,
    and ``when_request._call_via`` — so no two step modules can disagree about
    what a scenario may read after a dispatch. They did disagree: ``when_request``
    published neither ``ctx["result"]`` nor ``ctx["wire_error_envelope"]``, so
    every scenario routed through it had no object to call
    ``result.assert_wire_error(...)`` on, and the Error Verification Policy's
    prescribed assertion was literally unreachable there (salesagent-3dawm.18).
    ``ctx['result']`` is the key with exactly one producer; leaving it unset
    silently downgrades the wire-first Then steps (``then_error.py``'s
    ``_wire_code`` / ``_wire_suggestion`` / ``_wire_error_object``) to the lossy
    reconstructed ``ctx['error']`` fallback.

    Both branches are projected here, not just the error branch. Unifying only
    the error branch would leave the success branch with two writers — the same
    divergence one level down.
    """
    # Expose the normalized TransportResult so Then-steps can use the
    # harness-provided, transport-independent assertions (result.assert_wire_error)
    # instead of hand-rolling envelope parsing.
    ctx["result"] = result
    if result.is_error:
        # BDD dispatches on a WIRE transport only (IMPL was dropped from the default
        # parametrization, #1417), so a failed dispatch here is always the WireError
        # carrier -- never a production class rebuilt from wire bytes, which is what
        # this slot used to hold.
        ctx["error"] = cast("WireError", result.error)
        # Capture the REAL wire envelope only (A2A/REST/MCP) so Then steps can
        # assert the two-layer AdCP shape per the Error Verification Policy. This
        # is a passthrough copy of the field, not a second implementation of the
        # accessor's guard/fallback logic — which is why
        # ``test_architecture_bdd_wire_discipline``'s Check E names this module as
        # one of the two sanctioned direct readers of the field. Steps that need
        # the guarded reading still go through ``_outcome_helpers``'
        # ``wire_error_dict`` / ``wire_error_envelope_or_none``, whose source of
        # truth is ``ctx["result"]``.
        #
        # The synthesized envelope is deliberately NOT published: copying it into
        # ctx let a step write `ctx.get("wire_error_envelope") or
        # ctx.get("synthesized_error_envelope")` and thereby reinstate exactly
        # the fallback McpDispatcher refuses (tests/harness/dispatchers.py —
        # "a dead MCP wire path must yield None here"). The dataclass field
        # TransportResult.synthesized_error_envelope still exists for the
        # integration suites that legitimately grade the envelope BUILDER.
        # None-safe; an absent key means "no envelope".
        if result.wire_error_envelope is not None:
            ctx["wire_error_envelope"] = result.wire_error_envelope
        return

    # Success path. Written as an early return above rather than an ``else``:
    # each branch publishes its own key CONDITIONALLY, so an absent key means
    # "this dispatch captured no such wire" on both sides of the split.
    #
    # Propagate the real serialized success-path wire body so Then steps
    # can assert on what the buyer actually receives (ctx["wire_response"]),
    # not the reconstructed typed payload (REST HTTP body; A2A/MCP artifact
    # only when the env routes through _run_a2a_handler/_run_mcp_client).
    # None on IMPL / non-stashing envs; the wire_field() helper guards
    # against silent tautologies (#1417). See tests/CLAUDE.md
    # "TransportResult.wire_response".
    if result.wire_response is not None:
        ctx["wire_response"] = result.wire_response


def _as_transport(ctx: dict, caller: str) -> Transport:
    """Read ``ctx['transport']`` as a :class:`Transport`. The SINGLE normalizer.

    ``ctx["transport"]`` is *usually* already the enum — the ``ctx`` fixture
    injects it that way (``tests/bdd/conftest.py``, "gets a fresh dict with
    ``ctx['transport']`` set to the Transport enum"). But three step modules
    overwrite it with a STRING mid-scenario (``given_auth.py`` with ``"mcp"``,
    ``uc010_capabilities.py`` with ``"MCP"``/``"A2A"``, ``uc011_accounts.py``
    with ``"A2A"``), and the two spellings differ in case.

    ``dispatch_request`` grew an inline map for that and ``dispatch_via_client``
    did not, so the client seam raised ``KeyError``/``NoAddressForTransport`` on
    exactly the scenarios that re-assign the key — a latent trap that only fires
    once a string-assigning module is migrated onto the client. Normalizing in
    ONE place means the two dispatch entry points cannot disagree about what the
    key may hold (CLAUDE.md DRY invariant).

    A missing transport is a WIRING BUG, not an IMPL fallback: BDD dispatches on
    a wire transport only (IMPL was dropped from the default parametrization,
    #1417), so fail loudly rather than silently bypassing the wire.
    """
    transport = ctx.get("transport")
    if transport is None:
        raise RuntimeError(
            f"{caller}: ctx['transport'] is unset. BDD scenarios must dispatch "
            "through a wire transport (a2a/mcp/rest); the IMPL call_impl fallback was removed."
        )
    if isinstance(transport, Transport):
        return transport
    if isinstance(transport, str):
        try:
            # Transport values are lowercase ("a2a"/"mcp"/"rest"/"e2e_rest"), so
            # casefolding covers both the "mcp" and "MCP" spellings in use without
            # a hand-maintained alias map that a new member would silently miss.
            return Transport(transport.lower())
        except ValueError as exc:
            raise RuntimeError(f"{caller}: unrecognized wire transport {transport!r}") from exc
    raise RuntimeError(f"{caller}: ctx['transport'] is neither a Transport nor a str: {transport!r}")


def gate_and_record(payload: dict[str, Any]) -> None:
    """The two obligations every dispatch owes BEFORE its payload reaches a transport.

    ONE function rather than two calls at each seam, because the seams are what
    multiplied last time. ``assert_declared_malformations`` shipped with a single
    call site while three step-level entries reached a transport, so a malformed
    payload on either of the other two was never checked and a declared malformation
    silently repaired on them was never reported (salesagent-99w2t). A pair of
    obligations that must always travel together is one function, not a convention.

    ORDER IS LOAD-BEARING, in both directions:

    * the gate runs before ``json_safe`` (which ``env.call_via`` and the capture both
      apply), because ``json_safe`` rebuilds every dict and so drops the ``_Malformed``
      subclass the declaration is carried by;
    * the capture runs before the transport, because the subject is the request AS
      DISPATCHED, and any per-transport shaping downstream would make the same
      scenario's payload unlike itself across a2a/mcp/rest and break the
      transport-twin tolerance ``compare_payloads.py`` depends on.
    """
    assert_declared_malformations(payload)
    record_dispatched_request(payload)


def dispatch_request(ctx: dict, *, credential: Any = NO_IDENTITY_OVERRIDE, **kwargs: Any) -> None:
    """Dispatch a request through ctx['transport'] via ``env.call_via``.

    Stores the TransportResult in ctx["result"]; ctx["error"] on failure.
    If ctx["transport"] is a Transport enum, uses call_via directly.
    If it's a string, maps to Transport enum first.

    The ``credential`` kwarg overrides the env's own credential for multi-agent
    and no-auth scenarios: a headers dict, built by ``env.credential(...)`` or
    ``credential_headers(...)``. When provided, it flows through to call_via
    (which uses kwargs.setdefault, so an explicit credential won't be clobbered).
    ``env.credential(token=None)`` presents nothing on the env's tenant; ``{}``
    sends no headers at all.

    ``ctx["credential"]`` IS THAT OVERRIDE WHEN THE CALLER PASSES NONE. Every writer of
    that key -- the generic no-auth, no-tenant and unresolvable-credential Givens, and
    their per-use-case copies -- writes it to mean "present this instead of the env's
    own", and five When steps each re-implemented the same three-line read (uc002,
    uc006, uc010, uc019, local_context_echo). A When that did not know to make that
    read presented the env's OWN valid credential, so the scenario dispatched an
    authenticated request while its Given said there was none, and the auth row it was
    grading passed on a success document. That is a wiring defect a step author cannot
    see, so the read belongs in the one place every transport already goes through.
    An explicit ``credential=`` still wins, which is what multi-agent steps pass.
    """
    if credential is NO_IDENTITY_OVERRIDE:
        credential = ctx.get("credential", NO_IDENTITY_OVERRIDE)
    if credential is not NO_IDENTITY_OVERRIDE:
        kwargs["credential"] = credential

    # Grade the ITEMS against the pinned model before anything else touches them. This
    # is the half that FINDS unmarked malformations, and it looks for validity, never
    # for markers (tests/factories/malformed.py states the invariant).
    #
    # Placed BEFORE ``env.call_via`` -> ``json_safe``, which rebuilds every dict and so
    # drops the ``_Malformed`` subclass the declaration is carried by.
    #
    # It used to also be placed before a ``try`` whose ``except Exception`` would have
    # swallowed this assertion into ``ctx["error"]`` and let the scenario grade the
    # gate's own failure as the server's answer. That except is gone (see below), so the
    # gate is no longer one reordering away from being silenced — but the json_safe
    # ordering above is still load-carrying and this call must stay above it.
    gate_and_record(kwargs)

    env = ctx["env"]
    transport = _as_transport(ctx, "dispatch_request")
    # NO `except Exception: ctx["error"] = exc` HERE, and the third outcome it was
    # defending is not lost — it never travelled as an exception in the first place.
    #
    # The old comment argued a real distinction: "env.call_via itself blew up, so
    # nothing reached a transport and there is no envelope to publish" is genuinely
    # different from "the buyer received an error" (salesagent-3dawm.15). True — but
    # `except Exception` cannot tell that case from a CLIENT-SIDE request-construction
    # error, where production never ran and the scenario then grades the model instead
    # of the server. That is the defect the two sibling entries removed and documented
    # (`when_request._call_via`, `dispatch_via_client`'s docstring); prkv.33 measured it
    # at all 86 UC-005 instances recording dispatched=False.
    #
    # The distinction survives WITHOUT the blanket, because it is already carried on the
    # return value. All three in-process dispatchers (tests/harness/dispatchers.py) wrap
    # their own delivery in a catch-all and hand back an ERROR TransportResult declaring
    # `has_wire=False` and `envelope["status"] == "transport_fault"`
    # (`derive_error_status(None)`, tests/harness/transport.py) — a first-class "nothing
    # crossed a wire" verdict a Then can read, rather than an exception a step had to
    # hand-stash. Whatever still escapes `call_via` is by construction OUTSIDE every
    # dispatcher's catch-all — identity resolution, a missing address, a missing
    # tool_name, absent e2e_config — i.e. harness WIRING, which must fail loudly for the
    # same reason `dispatch_via_client` lets NotImplementedError / NoAddressForTransport
    # propagate.
    #
    # MEASURED before removal, not assumed (salesagent-ryzil.3): a full bdd_inprocess run
    # with the blanket deleted, diffed per nodeid against the run before it, produced ZERO
    # outcome changes and ZERO longrepr changes across all 8164 common nodeids — over a
    # seam carrying 2509 dispatches / 2374 nodeids (salesagent-ryzil.2). The blanket
    # caught nothing; it was dead weight standing where a real swallow could grow.
    result = env.call_via(transport, **kwargs)
    _populate_ctx_from_result(cast("WireCtx", ctx), result)
    # Runs UNCONDITIONALLY after the dispatch, never in an ``else:`` hung off a
    # ``try``. The blanket ``except Exception`` that ``else`` belonged to is gone
    # (above), so there is no longer a branch on which an opted-in scenario could
    # silently skip the second credential location and still be graded — which is
    # the vacuity :func:`_record_credential_registrations` exists to prevent, and
    # the same one :func:`dispatch_via_client` raises over.
    _record_credential_registrations(ctx, transport, result, kwargs)


def _record_credential_registrations(
    ctx: dict, transport: Transport, result: TransportResult, kwargs: dict[str, Any]
) -> None:
    """Ask the env for the seller's answer at EVERY credential location, not just this one.

    A DEPARTURE FROM "one request, one outcome", and it is deliberate: for the
    opted-in scenarios this one When puts more than one request on the wire,
    because on at least one transport a buyer has more than one way to hand the
    seller webhook credentials, and a claim about "registrations" that exercised
    only one of them is a claim about a surface nothing looked at. WHICH transport
    that is, and how many places it has, is not stated here and must not be — see
    the env.

    The step layer stays transport-blind through it: WHICH locations exist, and
    what each is called, is the env's answer (``credential_registrations``), and
    this function neither knows nor asks how many there will be. If a THIRD
    location ever appears it appears there, silently, and every opted-in scenario
    grades it.

    Off by default (``GRADE_EVERY_CREDENTIAL_LOCATION``), so no existing scenario
    changes its dispatch count.
    """
    if not ctx.get(GRADE_EVERY_CREDENTIAL_LOCATION):
        return
    # The REALIZATION, verbatim — never ``bool(...)``. ``signed`` is not a flag: it is
    # one of False / True / "malformed" / "tampered"
    # (tests.helpers.signing.SIGNATURE_REALIZATIONS), and ``bool("malformed")`` is
    # True, so collapsing it here would hand this frame a WELL-FORMED signature while
    # the operation dispatch above carried the malformed one. The scenario would then
    # grade an acceptance at the credential location it exists to grade a refusal at,
    # and pass. A second credential location is the epic's headline bypass surface, so
    # these are the frames that can least afford the collapse. WHICH transport has one
    # stays the env's business, here too: this comment names the hazard, not the leg.
    ctx[CREDENTIAL_REGISTRATIONS] = ctx["env"].credential_registrations(
        transport,
        kwargs.get("push_notification_config"),
        result,
        signed=kwargs.get("signed", False),
    )


def _refuse_ungraded_credential_locations(ctx: dict, caller: str) -> None:
    """Refuse to dispatch from an entry that CANNOT grade every credential location.

    :func:`dispatch_request` is the only entry whose call shape (a keyword bag carrying
    ``push_notification_config`` and the ``signed`` realization) lets it ask the env for
    the seller's answer at each location. The other two entries take a single opaque
    body and have no ``signed``, so they can only ever produce ONE outcome. Letting an
    opted-in scenario through them would grade one location and pass — the exact vacuity
    :func:`_record_credential_registrations` exists to prevent, and worse than a missing
    test because it reads as coverage.

    ONE function rather than the check written out at each entry: the guard shipped with
    two entries in view and the merge added a third (:func:`dispatch_raw_document`), which
    is precisely how a seam-level obligation goes missing on the newest seam. A rule that
    must hold at every non-grading entry is a function, not a convention (CLAUDE.md DRY
    invariant).
    """
    if ctx.get(GRADE_EVERY_CREDENTIAL_LOCATION):
        raise RuntimeError(
            f"{caller}: scenario opted into {GRADE_EVERY_CREDENTIAL_LOCATION!r}, which only "
            "dispatch_request grades. Dispatch through dispatch_request, or teach this entry "
            "the credential-registration contract first."
        )


def dispatch_raw_document(ctx: dict, document: Any, *, credential: Any = NO_IDENTITY_OVERRIDE) -> None:
    """Dispatch a document the client cannot shape, through ``tests/harness/raw_wire.py``.

    The same ctx contract as :func:`dispatch_request`, published through the same single
    writer. The gate that grades a keyword bag against the pinned model does not run here:
    there is no model for a document whose whole point is that no model accepts it, and the
    seller's answer to it is what the scenario grades.
    """
    from tests.harness.raw_wire import dispatch_raw

    # A raw document is one opaque body; there is no keyword bag to read a
    # push_notification_config or a ``signed`` realization out of, so this entry cannot
    # answer a credentials claim at more than one location.
    _refuse_ungraded_credential_locations(ctx, "dispatch_raw_document")
    env = ctx["env"]
    transport = _as_transport(ctx, "dispatch_raw_document")
    _populate_ctx_from_result(cast("WireCtx", ctx), dispatch_raw(env, transport, document, credential))


def dispatch_via_client(
    ctx: dict, tool: str, payload: dict[str, Any], *, credential: Any = NO_IDENTITY_OVERRIDE
) -> None:
    """Dispatch through ``AdCPTestClient.call`` instead of ``env.call_via``.

    The same single dispatch seam reached through the transport-generic client
    (``tests/harness/client.py``, whose module docstring states the design), for
    scenarios wired onto it — one scenario, one request model, four transports.
    Publishes the ctx contract through the same
    :func:`_populate_ctx_from_result`, so every wire-first Then step works
    unmodified regardless of which entry point the When used.

    Deliberately does NOT wrap ``client.call()`` in a blanket
    ``except Exception`` — and neither does :func:`dispatch_request` any more, so all
    three step-level dispatch entries now agree on this. ``AdCPTestClient.call``
    already converts ordinary
    transport errors into an error ``TransportResult`` internally, and
    re-raises ``NotImplementedError`` / lets ``NoAddressForTransport``
    propagate on purpose — a harness wiring gap must surface as a hard
    failure, not get silently downgraded into ``ctx["error"]`` where
    ``then_operation_fails`` would mistake it for a real AdCP rejection
    (client.py's own anti-vacuity comment on ``call()``).
    """
    # The dispatch paths converge on the same ctx contract but NOT on the same
    # credential-location grading: ``AdCPTestClient.call`` takes a single ``payload``
    # and has no ``signed`` parameter, so this path cannot ask the env for the seller's
    # answer at every credential location. Fail loudly instead; no scenario wires both
    # today.
    _refuse_ungraded_credential_locations(ctx, "dispatch_via_client")
    client = ctx["client"]
    transport = _as_transport(ctx, "dispatch_via_client")
    # This entry CANNOT be folded into :func:`dispatch_request`, so it is gated in
    # place. ``AccountListDispatchMixin.is_list_request`` discriminates on
    # ``isinstance(kwargs["req"], ListAccountsRequest)`` (uc011_accounts.py:112-121), so
    # a raw list payload sent through ``env.call_via`` is MISROUTED to
    # ``sync_accounts``. Two entries in one module is the achievable end state; both of
    # them gate.
    #
    # ``payload`` only: ``credential`` travels beside the bag here rather than inside it,
    # and the capture's identity token exists for the bag. Measured: every recorded
    # ``dispatch_via_client`` dispatch took the no-credential branch (0 of 55 —
    # salesagent-ryzil.2), so nothing is lost today; the day one does not, the
    # difference to record is still whether auth was carried, not what it held.
    gate_and_record(payload)
    if credential is not NO_IDENTITY_OVERRIDE:
        result = client.call(tool, payload, transport, credential=credential)
    else:
        result = client.call(tool, payload, transport)
    _populate_ctx_from_result(cast("WireCtx", ctx), result)
