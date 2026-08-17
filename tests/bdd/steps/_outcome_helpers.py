"""Outcome-based assertion helpers for E2E transport compatibility.

These helpers verify outcomes through the harness (which uses repositories
and the correctly-bound DB session), making assertions work across all
transports including E2E.

No raw session access. No db_session(ctx). The harness owns the session,
the repository owns the query, the helper owns the assertion.

Accessor helpers here return ``Any``, not ``object``. They read out of the untyped
heterogeneous ``ctx`` dict or off ORM/Pydantic instances, and every caller
immediately indexes or attribute-accesses the result — ``object`` turned each of
those into a mypy ``attr-defined`` error while adding no real type safety. Note
that nothing type-checks ``tests/`` today: the Makefile runs ``mypy src/``, the
untyped-defs ratchet hard-codes ``SRC_DIR``, and pre-commit excludes ``tests/``.
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
    wire = ctx.get("wire_response")
    transport = ctx.get("transport")
    if wire is None and transport not in (None, Transport.IMPL):
        raise AssertionError(f"{transport}: wire_response missing — env does not stash success-path wire")
    if wire is not None:
        return wire[field]
    # IMPL has no wire — serialize the typed payload through the production
    # serializer. _require_response preserves the diagnostic if a (reused) sibling
    # scenario hit an error path, instead of a bare ctx["response"] KeyError.
    return _require_response(ctx).model_dump(mode="json")[field]


def wire_dict(ctx: dict) -> dict:
    """Return the full success-path wire body as the buyer sees it on the wire.

    The dict analogue of :func:`wire_field` — use when an oracle must test key
    PRESENCE/ABSENCE (e.g. an optional field) rather than read one known field.
    Shares the same loud guard: a real-wire transport (REST/A2A/MCP) that did not
    stash ``wire_response`` raises instead of silently asserting nothing. IMPL (and
    the non-parametrized ``None`` default) serialize the typed payload through the
    production serializer.
    """
    wire = ctx.get("wire_response")
    transport = ctx.get("transport")
    if wire is None and transport not in (None, Transport.IMPL):
        raise AssertionError(f"{transport}: wire_response missing — env does not stash success-path wire")
    if wire is not None:
        return wire
    return _require_response(ctx).model_dump(mode="json")


def _require(ctx: dict, key: str, *, hint: str | None = None) -> Any:
    """Return ``ctx[key]``, failing with a diagnostic if it is absent.

    Then steps read entities and outcomes a prior step was expected to put in
    ``ctx``. Reading ``ctx[key]`` by subscript raises a bare ``KeyError`` when
    that step did not populate it — giving no hint why. This helper raises an
    ``AssertionError`` that names the missing key, includes an optional hint,
    and surfaces any recorded error instead.

    ``env`` is intentionally not routed through this helper: the harness
    guarantees it and the ``no-silent-env`` guard requires ``ctx["env"]``.

    Returns ``Any`` rather than ``object`` deliberately. ``ctx`` is an untyped
    heterogeneous dict, so every caller immediately indexes or attribute-accesses
    the result; ``object`` made each of those a mypy ``attr-defined`` error while
    adding no real type safety.
    """
    val = ctx.get(key)
    detail = f" {hint}" if hint else ""
    assert val is not None, f"Expected ctx[{key!r}] in ctx but none found.{detail} Recorded error: {ctx.get('error')!r}"
    return val


def dispatched_kwargs(ctx: dict) -> dict:
    """Return the kwargs the scenario's When step actually dispatched.

    The single reader for the ``ctx["dispatched_kwargs"]`` channel that
    ``dispatch_request`` records. Derive expected values from THIS, never from a
    literal default: a ``.get(key, 7)`` style fallback silently turns an
    assertion into a constant, which is the defect class GH #1749 tracks.

    Recorded before dispatch, so it is available on the error path too.

    The dict is HETEROGENEOUS — it may hold plain kwargs or a whole Pydantic
    request object under ``req``. Use :func:`dispatched_field` to read one
    request field across both shapes.
    """
    return _require(ctx, "dispatched_kwargs", hint="the When step must call dispatch_request()")


def dispatched_field(ctx: dict, name: str, *, default: Any = None) -> Any:
    """Return request field ``name`` as dispatched, across both dispatch shapes.

    Handles the flat-kwargs form (``dispatch_request(ctx, media_buy_ids=[...])``)
    and the request-object form (``dispatch_request(ctx, req=SomeRequest(...))``),
    which 38 of the dispatch sites use.

    ``default`` is for fields the buyer legitimately did not send. Never pass a
    value the assertion will then compare against — that reintroduces the
    constant-oracle defect this channel exists to prevent.
    """
    kwargs = dispatched_kwargs(ctx)
    if name in kwargs:
        return kwargs[name]
    req = kwargs.get("req")
    if req is not None:
        return getattr(req, name, default)
    return default


def _require_response(ctx: dict) -> Any:
    """Return ctx["response"], failing with a diagnostic if it is absent.

    Then steps assert on the response produced by a prior When step. Reading
    ``ctx["response"]`` by subscript raises a bare ``KeyError`` when the
    operation errored (only ``ctx["error"]`` was set) — giving no hint why.
    This helper raises an ``AssertionError`` that names the missing response
    and surfaces any recorded error instead.
    """
    return _require(ctx, "response", hint="The operation may have errored instead of returning.")


def _require_error(ctx: dict) -> Any:
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


def _get_response_field(resp: object, field: str) -> Any:
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


def assert_media_buy_created(ctx: dict, media_buy_id: str | None = None) -> Any:
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


def assert_adapter_executed(ctx: dict) -> Any:
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
