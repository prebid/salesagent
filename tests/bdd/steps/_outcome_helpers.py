"""Outcome-based assertion helpers for E2E transport compatibility.

These helpers verify outcomes through the harness (which uses repositories
and the correctly-bound DB session), making assertions work across all
transports including E2E.

No raw session access. No db_session(ctx). The harness owns the session,
the repository owns the query, the helper owns the assertion.
"""

from __future__ import annotations

from typing import Any

from tests.harness.transport import Transport


def wire_field(ctx: dict, field: str) -> Any:
    """Return a top-level success-response field as the buyer sees it on the wire.

    REST/A2A/MCP expose the real success-path wire dict via ``ctx["wire_response"]``.
    IMPL has no wire, so serialize the typed payload through the production
    serializer — the same path that produces wire bytes for the other transports.

    Loud guard: a real-wire transport (REST/A2A/MCP) that didn't stash
    ``wire_response`` would otherwise fall through to the ``model_dump`` path and
    assert nothing on the wire — a silent tautology. A sibling wired against a
    non-stashing env trips this instead of passing green. IMPL (and the
    non-parametrized ``None`` default) legitimately have no wire.
    """
    return wire_dict(ctx)[field]


def wire_dict(ctx: dict) -> dict:
    """Return the full success-path wire body as the buyer sees it on the wire.

    The dict analogue of :func:`wire_field` — use when an oracle must test key
    PRESENCE/ABSENCE (e.g. an optional field) rather than read one known field.
    Shares the same loud guard: a real-wire transport (REST/A2A/MCP) that did not
    stash ``wire_response`` raises instead of silently asserting nothing. IMPL (and
    the non-parametrized ``None`` default) serialize the typed payload through the
    production serializer.
    """
    result = ctx.get("result")
    transport = ctx.get("transport")
    if transport not in (None, Transport.IMPL):
        # A real-wire transport: the guarded read lives on TransportResult, which is
        # the object that holds the wire. One implementation, so a step definition
        # cannot drift from an integration test asserting the same thing.
        if result is not None:
            return result.require_wire()
        wire = ctx.get("wire_response")
        if wire is None:
            raise AssertionError(f"{transport}: wire_response missing — env does not stash success-path wire")
        return wire
    # IMPL has no wire — serialize the typed payload through the production
    # serializer. _require_response preserves the diagnostic if a (reused) sibling
    # scenario hit an error path, instead of a bare ctx["response"] KeyError.
    wire = ctx.get("wire_response")
    return wire if wire is not None else _require_response(ctx).model_dump(mode="json")


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

    Shares the same loud guard as ``wire_dict``: a real-wire transport
    (REST/A2A/MCP) that captured no error envelope raises instead of silently
    asserting nothing — that combination is a test bug (the operation should
    have failed through the wire), not a legitimate no-wire case. IMPL has no
    wire — falls back to ``synthesized_error_envelope`` (what the boundary
    translator WOULD emit against the caught error), consistent with
    ``wire_dict``'s IMPL fallback to the serialized typed payload.
    """
    result = ctx.get("result")
    envelope = _real_wire_error_envelope(ctx)
    transport = ctx.get("transport")
    if envelope is None and transport not in (None, Transport.IMPL):
        raise AssertionError(f"{transport}: wire_error_envelope missing — env does not stash the wire error envelope")
    if envelope is not None:
        return envelope
    synthesized = getattr(result, "synthesized_error_envelope", None) if result is not None else None
    assert synthesized is not None, (
        f"No wire_error_envelope or synthesized_error_envelope available (result={result!r}) — "
        "expected an error dispatch"
    )
    return synthesized


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


def _require_response(ctx: dict) -> object:
    """Return ctx["response"], failing with a diagnostic if it is absent.

    Then steps assert on the response produced by a prior When step. Reading
    ``ctx["response"]`` by subscript raises a bare ``KeyError`` when the
    operation errored (only ``ctx["error"]`` was set) — giving no hint why.
    This helper raises an ``AssertionError`` that names the missing response
    and surfaces any recorded error instead.
    """
    return _require(ctx, "response", hint="The operation may have errored instead of returning.")


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


def _unwrap_response(resp: object) -> object:
    """Return the domain response inside a protocol-status wrapper, else *resp*.

    ``TaskResultEnvelope`` subclasses (CreateMediaBuyResult and friends) carry
    the domain response on ``.response``; bare domain responses do not. The one
    place that distinction is decided — callers that need the inner OBJECT (to
    register it, or to read several fields off it) use this; callers that need a
    single FIELD use :func:`_get_response_field`, which routes through here.
    """
    inner = getattr(resp, "response", None)
    return resp if inner is None else inner


def _get_response_field(resp: object, field: str) -> object:
    """Extract a field from a response, handling wrapper types."""
    if hasattr(resp, field):
        return getattr(resp, field)
    inner = _unwrap_response(resp)
    if hasattr(inner, field):
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
        resp = ctx.get("response")
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
