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


class _WireMissing:
    """Type of :data:`WIRE_MISSING` — exists only to give it a readable repr."""

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return "<absent from wire>"


#: Sentinel distinguishing "the path is not on the wire at all" from "the path is
#: present and carries a JSON null". The two are different contract violations and
#: must never collapse: an unset optional object is ABSENT on a conformant wire, so
#: a serialized ``null`` is a schema-invalid serialization, not an absence.
WIRE_MISSING = _WireMissing()


def _wire_body(ctx: dict) -> dict:
    """The serialized success-path wire body, behind the loud guard.

    REST/A2A/MCP expose the real success-path wire dict via ``ctx["wire_response"]``.
    IMPL has no wire, so serialize the typed payload through the production
    serializer — the same path that produces wire bytes for the other transports.

    Loud guard: a real-wire transport (REST/A2A/MCP) that didn't stash
    ``wire_response`` would otherwise fall through to the ``model_dump`` path and
    assert nothing on the wire — a silent tautology. A sibling wired against a
    non-stashing env trips this instead of passing green. Only an EXPLICIT
    ``Transport.IMPL`` legitimately has no wire; an unset transport (``None`` —
    any non-parametrized caller, e.g. a @rest/@mcp/@a2a-tagged scenario's empty
    ctx) raises too, naming the fix, because silently serializing there turns
    the wire assertion into a serializer round-trip (GH #1744).

    Sole guard implementation for :func:`wire_field`, :func:`wire_dict` and
    :func:`wire_absent` — three copies of it would be exactly the duplication the
    canonical-helper rule exists to prevent.

    A success-path helper reached on a scenario that actually ERRORED says so, and
    names the error. Without this the transport guard below would fire first and
    report "env does not stash success-path wire" — blaming the harness for what is
    really a failed request, which is the single most misleading diagnostic these
    helpers can emit.
    """
    wire = ctx.get("wire_response")
    error = ctx.get("error")
    if wire is None and error is not None and ctx.get("response") is None:
        raise AssertionError(f"expected a success response, got error: {error!r}")
    transport = ctx.get("transport")
    if wire is None and transport is None:
        raise AssertionError(
            "wire assertion with transport unset — a non-parametrized caller reached the "
            "wire helpers without a stashed wire. Set ctx['transport'] (or pass "
            "Transport.IMPL explicitly) to declare whether a real wire must exist."
        )
    if wire is None and transport is not Transport.IMPL:
        raise AssertionError(f"{transport}: wire_response missing — env does not stash success-path wire")
    if wire is not None:
        return wire
    # Explicit IMPL has no wire — serialize the typed payload through the production
    # serializer. _require_response preserves the diagnostic if a (reused) sibling
    # scenario hit an error path, instead of a bare ctx["response"] KeyError.
    return _require_response(ctx).model_dump(mode="json")


def wire_lookup(ctx: dict, path: str) -> Any:
    """Resolve a dotted path on the success-path wire, or :data:`WIRE_MISSING` if absent.

    ``"a"`` reads a top-level key; ``"a.b.c"`` walks nested objects. A hop through a
    non-dict (e.g. a list or a scalar) counts as an absence rather than raising.

    This is the shared resolver behind :func:`wire_field` / :func:`wire_dict` /
    :func:`wire_absent`, exposed for the genuinely TRI-STATE oracle — one whose
    contract is "absent, or present with this value" (an outline column grading
    ``absent or false``; a conditional Then that only grades a block when the seller
    emits it). It ASSERTS NOTHING, so it is a primitive, not a competing assertion
    surface: whenever the oracle is binary, reach for wire_field/wire_absent instead,
    and never rebuild a private dotted-path resolver on top of this one.
    """
    cur: Any = _wire_body(ctx)
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return WIRE_MISSING
        cur = cur[part]
    return cur


def wire_field(ctx: dict, field: str) -> Any:
    """Return a success-response field, by dotted path, as the buyer sees it on the wire.

    ``field`` is a dotted path (``"media_buy.features.sandbox"``), of which a bare
    top-level key is the one-segment case.

    DUAL assert — the path must be both present AND non-null:

    - **absent** fails, naming the top-level keys actually on the wire;
    - **present but JSON null** fails too. These are optional object/array fields
      whose schemas do not admit ``null``, so a null on the wire is a serialization
      defect, not a populated section (observed on MCP ``structured_content``, which
      serializes unset ``None`` fields; #1592). Downgrading this to a presence-only
      check would silently reintroduce the vacuous-pass class this helper exists to
      catch — see :func:`wire_absent` for the symmetric rule.
    """
    value = wire_lookup(ctx, field)
    assert value is not WIRE_MISSING, f"{field!r} absent from wire response (top-level keys: {sorted(_wire_body(ctx))})"
    assert value is not None, f"{field!r} is JSON null on the wire — schema-invalid serialization of an unset field"
    return value


def wire_dict(ctx: dict, path: str | None = None) -> dict:
    """Return the success-path wire body — the whole envelope, or the object at *path*.

    The dict analogue of :func:`wire_field` — use when an oracle must test key
    PRESENCE/ABSENCE (e.g. an optional field) rather than read one known field.
    With ``path``, resolves the same dotted path :func:`wire_field` does (same
    absent/null dual assert) and additionally pins that the resolved value IS a
    JSON object, so a caller that goes on to index it cannot fail with a confusing
    ``TypeError`` on a scalar. Shares the same loud guard on a non-stashing env.
    """
    doc = _wire_body(ctx)
    if path is None:
        return doc
    value = wire_field(ctx, path)
    assert isinstance(value, dict), f"{path!r} is not a JSON object on the wire: {value!r}"
    return value


def wire_absent(ctx: dict, path: str) -> None:
    """Assert the dotted *path* is not present on the success-path wire at all.

    The strict complement of :func:`wire_field`: only the missing key counts as
    absent. A path that resolves to a JSON ``null`` is PRESENT and therefore FAILS
    here — an unset optional section must not appear on the wire at all, and a
    serialized ``null`` is the schema-invalid emission this asserts against.
    """
    value = wire_lookup(ctx, path)
    assert value is WIRE_MISSING, f"{path!r} unexpectedly present on the wire: {value!r}"


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
    """Check if the current transport is E2E (Docker-based).

    FIXME(#1757): silent default — an unset transport reads as "not e2e" here, so a
    caller that branches on this skips its e2e assertions instead of reporting the
    missing setup. Same class as the twin site ``then_payload._is_e2e``, which was
    converted to the raising form in salesagent-n78j0.1.5; this one has ~25 callers
    across UC-002/003/006 including Given steps, so it needs its own measured BDD
    slice rather than a drive-by change. Tracked as its own task, not suppressed by
    any allowlist.
    """
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
