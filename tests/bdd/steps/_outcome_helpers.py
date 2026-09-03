"""Outcome-based assertion helpers for E2E transport compatibility.

These helpers verify outcomes through the harness (which uses repositories
and the correctly-bound DB session), making assertions work across all
transports including E2E.

No raw session access. No db_session(ctx). The harness owns the session,
the repository owns the query, the helper owns the assertion.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from tests.harness.transport import TransportResult


def error_envelope_or_none(ctx: dict) -> dict | None:
    """The error envelope for this dispatch, or ``None`` when there is none.

    The ctx-side adapter for :meth:`TransportResult.error_envelope_or_none` —
    the same relationship :func:`_wire_or_none` has to the success path. Steps
    hold a ctx and the reader lives on the result, so without this the four
    ctx-holding call sites each re-spell the ``ctx.get("result")`` dance, which
    is three copies of the decision this lane exists to make once.

    Returns ``None`` rather than raising, because every ctx-side caller branches
    on envelope-presence as control flow: an MCP dispatch can fail with a
    ``ToolError`` that is genuinely not an AdCP envelope.
    """
    result = ctx.get("result")
    return result.error_envelope_or_none() if result is not None else None


def _wire_or_none(ctx: dict) -> dict | None:
    """The real wire body for this dispatch, or ``None`` when there is no wire.

    Branches on the DECLARATION the dispatcher made at construction
    (``TransportResult.has_wire``), never on which transport enum is in play.
    The old spelling inferred wire-presence from transport IDENTITY — a lookup
    miss against the in-process transport member — so it would break, or
    silently reclassify every result, the day that member is removed.

    Two loud failures, both harness bugs rather than test failures:

    * no ``TransportResult`` in ctx — the When step did not dispatch through
      ``dispatch_request`` or ``when_request._call_via``, so there is no
      declaration to branch on and any answer here would be a guess;
    * ``has_wire`` with nothing stashed — the env crossed a wire and failed to
      capture it. Falling back to re-serializing the typed payload would assert
      nothing about the wire while looking green, which is the tautology these
      helpers exist to prevent.
    """
    result = ctx.get("result")
    if not isinstance(result, TransportResult):
        # Both dispatch seams end in ``except Exception as exc: ctx["error"] = exc``
        # WITHOUT stashing a result, so a dispatch that THREW arrives here too. Say
        # so, rather than misreporting it as "never dispatched" and sending the
        # reader hunting for a wiring bug that does not exist.
        failure = ctx.get("error")
        if failure is not None:
            raise AssertionError(
                f"no TransportResult in ctx because the dispatch RAISED: {failure!r} — "
                "there is no wire to read; assert on the error instead"
            )
        raise AssertionError(
            "no TransportResult in ctx — the When step did not dispatch through "
            "dispatch_request/_call_via, so wire-presence cannot be determined"
        )
    if not result.has_wire:
        return None
    # The guarded read itself lives on TransportResult (#1941): one implementation,
    # shared with the integration tests asserting the same thing, so a step
    # definition cannot drift from them. This helper decides only WHETHER a wire
    # exists — from the dispatcher's declaration — and ``require_wire`` decides
    # whether the declared wire was actually captured.
    return result.require_wire()


def wire_objects(value: Any) -> Any:
    """Recursively wrap wire dicts for attribute-style oracles.

    ``wire_field`` returns JSON-shaped dicts/lists (buyer wire). Many Then
    steps still read ``buy.media_buy_id`` / ``pkg.package_id``. Wrapping here
    keeps grading on wire values without ``getattr(dict, …)`` silently yielding
    ``None`` (e2e_rest UC-019 regression after switching ``_get_media_buys`` to
    ``wire_field``).
    """
    if isinstance(value, dict):
        return SimpleNamespace(**{key: wire_objects(item) for key, item in value.items()})
    if isinstance(value, list):
        return [wire_objects(item) for item in value]
    return value


def wire_field(ctx: dict, field: str) -> Any:
    """Return a top-level success-response field as the buyer sees it on the wire.

    A dispatch that crossed a wire exposes the real success-path body; one that
    did not (an in-process call) has none, so the typed payload is serialized
    through the production serializer — the same path that produces wire bytes
    for the other transports. Which case applies is read from the dispatcher's
    own declaration, not guessed here; see :func:`_wire_or_none`.
    """
    return wire_dict(ctx)[field]


def wire_dict(ctx: dict) -> dict:
    """Return the full success-path wire body as the buyer sees it on the wire.

    The dict analogue of :func:`wire_field` — use when an oracle must test key
    PRESENCE/ABSENCE (e.g. an optional field) rather than read one known field.
    Shares the same loud guard and the same source of truth: see
    :func:`_wire_or_none`.
    """
    wire = _wire_or_none(ctx)
    if wire is not None:
        return wire
    return require_payload(ctx).model_dump(mode="json")


def assert_wire_rejection(ctx: dict, code: str, *, recovery: str, field: str) -> None:
    """Assert the wire error envelope is *code* / *recovery* and names *field*.

    One implementation for every "the request is rejected with <CODE> naming
    field <f>" Then step. Each such step keeps its own literal Gherkin text —
    replacing them with one ``{code}``-parameterized parser would leave two
    parsers matching the same sentence, resolved by pytest-bdd's scan order, and
    the shadowed body would silently stop grading (``test_architecture_bdd_no_shadowed_steps``
    compares text ACROSS modules, so it would not catch it). Thin steps over a
    shared helper give DRY without the shadow.
    """
    from tests.helpers import assert_envelope_shape

    envelope = _require(ctx, "result", hint="no dispatch was recorded").error_envelope()
    assert_envelope_shape(envelope, code, recovery=recovery, field=field)


def _real_wire_error_envelope(ctx: dict) -> dict | None:
    """Read ``TransportResult.wire_error_envelope`` — the ONE attribute-access site.

    Every reader of this field, anywhere in ``tests/bdd/steps/``, must go
    through this module (:func:`wire_error_envelope_or_none` or
    :func:`wire_error_dict`) rather than hand-rolling
    ``getattr(result, "wire_error_envelope", None)`` — enforced by
    ``test_architecture_bdd_wire_discipline.py``'s access-pattern check.
    """
    result = ctx.get("result")
    return getattr(result, "wire_error_envelope", None) if result is not None else None


def wire_error_envelope_or_none(ctx: dict) -> dict | None:
    """Return the REAL wire error envelope (REST/A2A/MCP) captured for this dispatch, or ``None``.

    No loud guard, no IMPL-synthesized fallback — the strict counterpart to
    :func:`wire_error_dict`. Use this when a caller must distinguish "a real
    wire envelope was captured" from "only the IMPL-synthesized one exists"
    before delegating to ``TransportResult.assert_wire_error``, which reads
    ``wire_error_envelope`` specifically and raises its own (misleading)
    error if handed a synthesized-only result (``then_error_recovery``'s
    reason for using this instead of ``wire_error_dict``). Returns ``None``
    on IMPL and on any scenario where no wire envelope was captured —
    callers fall back to the reconstructed ``ctx['error']``.
    """
    return _real_wire_error_envelope(ctx)


def wire_error_dict(ctx: dict) -> dict:
    """Return the full error-path wire envelope as the buyer sees it on the wire.

    The error-path analogue of :func:`wire_dict` — the single guarded accessor
    for ``TransportResult.wire_error_envelope``, which its own docstring names
    "the canonical field for error verification" (``tests/CLAUDE.md`` § Error
    Verification Policy) and whose ``assert_wire_error`` calls "the single
    harness-provided way to verify an error on the wire — step definitions
    must not hand-roll envelope parsing." Callers that only need to read a
    field off the envelope (e.g. ``context.correlation_id`` echo checks) call
    this directly; callers verifying the error SHAPE should prefer
    ``result.assert_wire_error(...)``, the single shape authority.

    Shares the same loud guard as ``wire_dict``: a dispatch that captured no
    error envelope raises instead of silently asserting nothing — that
    combination is a test bug (the operation should have failed through the
    wire), not a legitimate no-wire case. IMPL has no wire, so it falls back to
    the synthesized envelope (what the boundary translator WOULD emit against
    the caught error), consistent with ``wire_dict``'s IMPL fallback to the
    serialized typed payload.

    Both halves of that guard live on ``TransportResult.error_envelope`` (#1941)
    — the same reader ``error_envelope_or_none`` wraps — rather than being
    re-derived here from ``ctx["transport"]``. Branching on transport IDENTITY
    was the spelling ``wire_dict`` moved off: it infers wire-presence from which
    enum member is in play instead of from the dispatcher's own ``has_wire``
    declaration, and it reached for a ``synthesized_error_envelope`` attribute
    that is now the private ``_synthesized_error_envelope``.
    """
    result = _require(ctx, "result", hint="expected an error dispatch")
    return result.error_envelope()


def _require(ctx: dict, key: str, *, hint: str | None = None) -> object:
    """Return ``ctx[key]``, failing with a diagnostic if it is absent.

    Then steps read entities and outcomes a prior step was expected to put in
    ``ctx``. Reading ``ctx[key]`` by subscript raises a bare ``KeyError`` when
    that step did not populate it — giving no hint why. This helper raises an
    ``AssertionError`` that names the missing key, includes an optional hint,
    and surfaces any recorded error instead.

    ``env`` is intentionally not routed through this helper: the harness
    guarantees it and the ``no-silent-env`` guard requires ``ctx["env"]``.
    """
    val = ctx.get(key)
    detail = f" {hint}" if hint else ""
    assert val is not None, f"Expected ctx[{key!r}] in ctx but none found.{detail} Recorded error: {ctx.get('error')!r}"
    return val


def payload_or_none(ctx: dict) -> object | None:
    """The dispatch's typed payload, or ``None`` when it produced an error.

    For steps that BRANCH on which path ran ("success response must not contain
    X; error response must not contain Y") rather than reading a value. Those
    steps used ``ctx.get("response")`` as the selector, and they need a selector
    that still works now the dispatch seams stop writing that copy.

    Returns None both when no dispatch happened and when the dispatch errored —
    a branch selector does not care which, and the error branch it falls into
    reports the difference. A step that genuinely REQUIRES a payload calls
    :func:`require_payload`, which raises instead.
    """
    result = ctx.get("result")
    if not isinstance(result, TransportResult):
        return ctx.get("self_dispatched_response")
    return result.payload


def require_payload(ctx: dict) -> object:
    """Return the typed payload of the dispatch that just ran.

    Reads the ``TransportResult`` the dispatch seams stash under
    ``ctx["result"]``, so the value arrives WITH its provenance rather than as a
    detached copy. Fails loudly when no dispatch happened, and separately when
    the dispatch recorded an error — a Then that asks for a payload after an
    error path is asking the wrong question, and a bare ``KeyError`` would not
    say so.
    """
    result = ctx.get("result")
    if not isinstance(result, TransportResult):
        # Second NAMED source: modules whose When still calls production directly
        # (uc011's _list_accounts_impl) stash under ctx["self_dispatched_response"],
        # and the GENERIC Then steps are shared with them. Both sources are explicit
        # keys, which is the point — the removed ctx["response"] was written by
        # dispatch AND by self-dispatching modules AND (in one case) held a REQUEST,
        # so a reader could not tell what it had. These two can always be told apart,
        # and when the pinned modules migrate the branch simply disappears.
        self_dispatched = ctx.get("self_dispatched_response")
        if self_dispatched is not None:
            return self_dispatched
        failure = ctx.get("error")
        if failure is not None:
            raise AssertionError(
                f"no TransportResult in ctx because the dispatch RAISED: {failure!r} — "
                "there is no payload; assert on the error instead"
            )
        raise AssertionError(
            "no TransportResult in ctx — the When step did not dispatch through "
            "dispatch_request/_call_via, so there is no payload to read"
        )
    if result.payload is None:
        raise AssertionError(f"the dispatch produced no payload — it errored instead. Recorded error: {result.error!r}")
    return result.payload


def _require_error(ctx: dict) -> object:
    """Return ctx["error"], failing with a diagnostic if no error was recorded.

    Then steps on an error path read ``ctx["error"]``. By subscript that raises
    a bare ``KeyError`` when the operation actually succeeded — giving no hint
    that the expected error never happened. This helper raises an
    ``AssertionError`` that says an error was expected and surfaces the response
    produced instead.
    """
    error = ctx.get("error")
    assert error is not None, (
        "Expected an error to be recorded in ctx but none found — the operation "
        f"may have succeeded. Response: {ctx.get('response')!r}"
    )
    return error


def _get_response_field(resp: object, field: str) -> object:
    """Extract a field from a response, handling wrapper types."""
    if hasattr(resp, field):
        return getattr(resp, field)
    inner = getattr(resp, "response", None)
    if inner is not None and hasattr(inner, field):
        return getattr(inner, field)
    if isinstance(resp, dict):
        return resp.get(field)
    return None


def is_e2e(ctx: dict) -> bool:
    """Check if the current transport is E2E (Docker-based)."""
    transport = ctx.get("transport")
    return transport is not None and hasattr(transport, "value") and str(transport.value).startswith("e2e_")


def assert_media_buy_created(ctx: dict, media_buy_id: str | None = None) -> object:
    """Verify media buy exists in DB through the harness.

    Returns the MediaBuy ORM instance for further assertions.
    """
    env = ctx["env"]

    if media_buy_id is None:
        resp = payload_or_none(ctx)
        if resp is not None:
            media_buy_id = _get_response_field(resp, "media_buy_id")

    assert media_buy_id is not None, "No media_buy_id available to verify creation"

    mb = env.get_media_buy(media_buy_id)
    return mb


def assert_adapter_executed(ctx: dict) -> object:
    """Verify adapter ran by checking DB state through the harness.

    A media buy that reaches a non-draft status proves the adapter was invoked.
    """
    mb = assert_media_buy_created(ctx)
    executed_statuses = ("active", "completed", "pending_approval", "pending_start", "submitted")
    assert mb.status in executed_statuses, (
        f"Media buy status '{mb.status}' does not confirm adapter execution. Expected one of {executed_statuses}."
    )
    return mb


def assert_audit_logged(ctx: dict, *, operation_substring: str = "create_media_buy") -> None:
    """Verify audit logging occurred — transport-aware.

    In-process: asserts on mock audit logger calls (fast, precise).
    E2E: queries audit_logs through the harness.
    """
    if is_e2e(ctx):
        env = ctx["env"]
        logs = env.get_audit_logs(operation_substring)
        assert logs, f"Expected audit_logs entry containing '{operation_substring}' for tenant {env._tenant_id}"
    else:
        _assert_audit_logged_mock(ctx, operation_substring)


def _assert_audit_logged_mock(ctx: dict, operation_substring: str) -> None:
    """Assert audit logger mock was called with the operation (in-process mode)."""
    env = ctx["env"]
    mock_audit = env.mock["audit"].return_value
    assert mock_audit.log_operation.called, (
        f"Expected audit_logger.log_operation to be called with '{operation_substring}', but it was never called"
    )
    operations = [
        call.kwargs.get("operation") or (call.args[0] if call.args else None)
        for call in mock_audit.log_operation.call_args_list
    ]
    matching = [op for op in operations if op and operation_substring in op]
    assert matching, (
        f"Expected at least one log_operation call containing '{operation_substring}', got operations: {operations}"
    )


def assert_audit_approval_logged(ctx: dict) -> None:
    """Verify approval decision was logged — transport-aware."""
    if is_e2e(ctx):
        env = ctx["env"]
        logs = env.get_audit_logs()
        found = any("pending_approval" in (log.operation or "") for log in logs) or any(
            "create_media_buy" in (log.operation or "") and log.success is True for log in logs
        )
        assert found, (
            f"Expected audit entry for approval decision, found: {[(log.operation, log.success) for log in logs]}"
        )
    else:
        _assert_audit_approval_mock(ctx)


def _assert_audit_approval_mock(ctx: dict) -> None:
    """Assert approval-specific audit log call exists (in-process mode)."""
    env = ctx["env"]
    mock_audit = env.mock["audit"].return_value
    assert mock_audit.log_operation.called, (
        "Expected audit_logger.log_operation to be called for approval decision logging"
    )
    for call in mock_audit.log_operation.call_args_list:
        op = call.kwargs.get("operation") or (call.args[0] if call.args else None)
        if op == "create_media_buy_pending_approval":
            return
        if op == "create_media_buy":
            success = call.kwargs.get("success")
            details = call.kwargs.get("details") or {}
            if success is True and "media_buy_id" in details:
                return
    raise AssertionError(
        f"Expected audit log entry with approval-specific content, "
        f"got calls: {[c.kwargs for c in mock_audit.log_operation.call_args_list]}"
    )


def assert_audit_adapter_logged(ctx: dict) -> None:
    """Verify adapter execution was logged — transport-aware.

    If the media buy went to pending_approval, the adapter was not called —
    that's correct behavior (no adapter audit log expected).
    """
    if is_e2e(ctx):
        env = ctx["env"]
        logs = env.get_audit_logs()
        for log in logs:
            op = log.operation or ""
            if "create_media_buy" in op and log.success is True and log.details is not None:
                return
            if "pending_approval" in op:
                return
        raise AssertionError(
            f"Expected audit entry for adapter execution or pending_approval, "
            f"found: {[(log.operation, log.success) for log in logs]}"
        )
    else:
        _assert_audit_adapter_mock(ctx)


def _assert_audit_adapter_mock(ctx: dict) -> None:
    """Assert adapter execution audit log call exists (in-process mode)."""
    env = ctx["env"]
    mock_audit = env.mock["audit"].return_value
    assert mock_audit.log_operation.called, (
        "Expected audit_logger.log_operation to be called for adapter execution logging"
    )
    for call in mock_audit.log_operation.call_args_list:
        op = call.kwargs.get("operation") or (call.args[0] if call.args else None)
        success = call.kwargs.get("success")
        details = call.kwargs.get("details")
        if op == "create_media_buy" and success is True and details is not None:
            return
    raise AssertionError(
        f"Expected audit log entry for adapter execution "
        f"(operation='create_media_buy', success=True, with details), "
        f"got: {[c.kwargs for c in mock_audit.log_operation.call_args_list]}"
    )
