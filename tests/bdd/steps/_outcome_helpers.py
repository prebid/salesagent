"""Outcome-based assertion helpers for E2E transport compatibility.

These helpers verify outcomes through the harness (which uses repositories
and the correctly-bound DB session), making assertions work across all
transports including E2E.

No raw session access. No db_session(ctx). The harness owns the session,
the repository owns the query, the helper owns the assertion.

``_require``/``_require_response``/``_require_error``/``_get_response_field`` return
``Any``, not ``object``: they read out of the untyped heterogeneous ``ctx`` dict or
off ORM/Pydantic instances of unknown shape at that call site, and every caller
immediately indexes or attribute-accesses the result — ``object`` turned each of
those into a mypy ``attr-defined`` error while adding no real type safety. Every
other accessor here carries the real return type it knows statically (``MediaBuy``,
``dict[str, Any]``) so mypy can catch a typo'd attribute or an int-key subscript at
the call site — that is exactly what ``tests/bdd/steps/_outcome_helpers.py``'s
Makefile-pinned mypy gate exists to check (this file feeds every BDD step module).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tests.harness.transport import Transport

if TYPE_CHECKING:
    from src.core.database.models import MediaBuy


def wire_field(ctx: dict[str, Any], field: str) -> Any:
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


def wire_dict(ctx: dict[str, Any]) -> dict[str, Any]:
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


def wire_list(ctx: dict[str, Any], key: str) -> list[dict[str, Any]]:
    """Return a top-level list field from the success-path wire body.

    The list analogue of :func:`wire_field` — use when an oracle must iterate the
    wire-serialized items of a list field (e.g. ``accounts``) rather than read one
    scalar field. Reads through :func:`wire_dict`, inheriting its loud guard: a
    real-wire transport that stashed no body raises instead of silently falling back
    to the typed payload.
    """
    return wire_dict(ctx).get(key) or []


def wire_packages(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    """Collect every package across every delivery as the buyer sees it on the WIRE.

    The wire-reading twin of a typed ``resp.media_buy_deliveries[].by_package`` walk.
    Reads through :func:`wire_list`, inheriting its loud guard: a real-wire transport that
    stashed no body raises instead of silently degrading to the typed payload. A boolean
    truncation flag serialized as the string "true" is the concrete case a typed read misses:
    it reconstructs to ``True`` and a typed oracle passes on a non-conformant wire. (A field
    that is DROPPED is still caught by a typed reader, since it reconstructs to ``None`` — the
    blind spot is coercion, not absence.)
    """
    return [pkg for d in wire_list(ctx, "media_buy_deliveries") for pkg in d.get("by_package") or []]


def _require(ctx: dict[str, Any], key: str, *, hint: str | None = None) -> Any:
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


def dispatched_request(ctx: dict[str, Any]) -> Any:
    """Return the validated AdCP request model the scenario's When step dispatched.

    The single reader for the ``ctx["dispatched_request"]`` channel that
    :func:`tests.bdd.steps.generic._dispatch.dispatch_request` records — single-typed
    BY CONSTRUCTION: unlike the retired ``dispatched_kwargs`` /
    ``dispatched_field`` pair, there is no flat-kwargs shape to also handle, because
    ``dispatch_request`` no longer accepts one.

    Raises loudly, naming the malformed channel, when
    ``ctx["dispatched_malformed"]`` is set instead — reaching into a malformed
    dispatch's fields for an expected value is a test-authoring bug, not a
    fallback to paper over: there IS no validated request to read attributes from.

    Derive expected values from the returned model's typed attributes, never from
    a literal default — a hardcoded fallback silently turns an assertion into a
    constant, the defect class GH #1749 tracks.
    """
    if "dispatched_malformed" in ctx:
        raise AssertionError(
            "dispatched_request(ctx) called on a malformed dispatch — "
            f"ctx['dispatched_malformed'] = {ctx['dispatched_malformed']!r}. "
            "There is no validated request model to read; this scenario dispatched "
            "through dispatch_malformed_request(), not dispatch_request()."
        )
    return _require(ctx, "dispatched_request", hint="the When step must call dispatch_request(req=...)")


def _require_response(ctx: dict[str, Any]) -> Any:
    """Return ctx["response"], failing with a diagnostic if it is absent.

    Then steps assert on the response produced by a prior When step. Reading
    ``ctx["response"]`` by subscript raises a bare ``KeyError`` when the
    operation errored (only ``ctx["error"]`` was set) — giving no hint why.
    This helper raises an ``AssertionError`` that names the missing response
    and surfaces any recorded error instead.
    """
    return _require(ctx, "response", hint="The operation may have errored instead of returning.")


def _require_error(ctx: dict[str, Any]) -> Any:
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


def is_e2e(ctx: dict[str, Any]) -> bool:
    """Check if the current transport is E2E (Docker-based)."""
    transport = ctx.get("transport")
    return transport is not None and hasattr(transport, "value") and str(transport.value).startswith("e2e_")


def assert_media_buy_created(ctx: dict[str, Any], media_buy_id: str | None = None) -> MediaBuy:
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


def assert_adapter_executed(ctx: dict[str, Any]) -> MediaBuy:
    """Verify adapter ran by checking DB state through the harness.

    A media buy that reaches a non-draft status proves the adapter was invoked.
    """
    mb = assert_media_buy_created(ctx)
    executed_statuses = ("active", "completed", "pending_approval", "pending_start", "submitted")
    assert mb.status in executed_statuses, (
        f"Media buy status '{mb.status}' does not confirm adapter execution. Expected one of {executed_statuses}."
    )
    return mb


def assert_audit_logged(ctx: dict[str, Any], *, operation_substring: str = "create_media_buy") -> None:
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


def _assert_audit_logged_mock(ctx: dict[str, Any], operation_substring: str) -> None:
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


def assert_audit_approval_logged(ctx: dict[str, Any]) -> None:
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


def _assert_audit_approval_mock(ctx: dict[str, Any]) -> None:
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


def assert_audit_adapter_logged(ctx: dict[str, Any]) -> None:
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


def _assert_audit_adapter_mock(ctx: dict[str, Any]) -> None:
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
