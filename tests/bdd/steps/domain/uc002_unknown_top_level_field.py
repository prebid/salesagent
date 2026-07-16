"""Steps for the locally-added UC-002 top-level unknown-field scenario (GH #1442).

Pattern #7 extra-field policy at the TOP-LEVEL request body: dev/CI runs with
extra=forbid, so an unknown top-level key must be rejected on every wire
transport. REST/A2A emit the two-layer INVALID_REQUEST envelope; MCP rejects
at the FastMCP tool-signature layer before the envelope builder runs (owner
decision 2026-07-11 on salesagent-cyz0 accepts that transport-native shape —
no envelope remap). Production (extra=ignore, spec-compliant per v3.1.1
additionalProperties: true) is graded by
tests/unit/test_schema_validation_modes.py::TestProductionModeRestBodyIgnoresExtra.
"""

from __future__ import annotations

from pytest_bdd import given, parsers, then


@given(parsers.parse('the request body carries unknown top-level field "{field_name}"'))
def given_unknown_top_level_field(ctx: dict, field_name: str) -> None:
    """Inject an unknown key into the flat request body and force raw dispatch.

    Raw dispatch is required: a typed CreateMediaBuyRequest would reject the
    key in test code before it ever reached the wire — the gate under test is
    the production transport boundary, not the test-side constructor.
    """
    ctx["dispatch_mode"] = "create_raw"
    ctx.setdefault("request_kwargs", {})[field_name] = "unexpected-value"


@then(parsers.parse('the unknown top-level field "{field_name}" is rejected per the transport contract'))
def then_unknown_top_level_field_rejected(ctx: dict, field_name: str) -> None:
    """Assert the identical cross-transport rejection contract on the real wire.

    Every transport REJECTS (the Pattern #7 dev-forbid gate) and names the
    offending field in the spec-canonical place. AdCP 3.1.1 ``core/error.json``
    names the field in the ``field`` property (JSONPath-lite), NOT the free-form
    ``message`` — so the field-naming obligation is asserted on ``error.field``,
    which every transport emits identically (verified: ``field="nonsense_field"``
    on MCP, A2A, and REST). The only accepted per-transport difference is the
    boundary ``code`` (owner decision 2026-07-11, salesagent-cyz0 — no remap):
    REST rejects at the pydantic extra=forbid handler -> ``INVALID_REQUEST``;
    A2A's boundary validator and MCP's ``mcp_compat_middleware`` (#1534) both
    emit ``VALIDATION_ERROR``. Message prose is intentionally NOT asserted — the
    spec leaves ``message`` free-form, and pinning it would be non-portable.
    """
    from tests.harness.transport import Transport
    from tests.helpers import assert_envelope_shape

    result = ctx.get("result")
    error = ctx.get("error")
    assert error is not None or (result is not None and result.is_error), (
        f"expected the unknown top-level field {field_name!r} to be rejected, "
        f"but the request succeeded: {ctx.get('response')!r}"
    )

    transport = ctx["transport"]
    transport_key = transport.name.lower() if isinstance(transport, Transport) else str(transport).lower()

    # Owner-accepted boundary-code difference; field naming is identical.
    expected_code = "INVALID_REQUEST" if transport_key in ("rest", "e2e_rest") else "VALIDATION_ERROR"
    assert_envelope_shape(
        ctx.get("wire_error_envelope"),
        expected_code,
        recovery="correctable",
        field=field_name,
    )
