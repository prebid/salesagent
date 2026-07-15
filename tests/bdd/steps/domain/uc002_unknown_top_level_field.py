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
    """Assert the rejection in the shape each transport actually produces.

    All transports must REJECT (the Pattern #7 dev-forbid gate), but each
    validates at its own boundary layer (owner decision 2026-07-11,
    salesagent-cyz0 — shape differences accepted, no remap):
    - REST (incl. e2e_rest): pydantic extra=forbid -> RequestValidationError
      handler -> two-layer INVALID_REQUEST envelope, asserted on the REAL
      wire envelope per the Error Verification Policy.
    - A2A: its boundary validator rejects before the Body model, emitting a
      two-layer VALIDATION_ERROR envelope whose message names the field.
    - MCP: FastMCP's tool-signature TypeAdapter rejects the unknown kwarg
      before with_error_logging can build an envelope, so no
      wire_error_envelope exists; the observable contract is a
      transport-native error naming the offending field.
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

    if transport_key == "mcp":
        # Transport-native rejection: the error must name the offending field
        # (FastMCP's signature-level message), proving THIS key caused it.
        assert field_name in str(error), f"MCP rejection does not name the unknown field {field_name!r}: {error!r}"
    elif transport_key == "a2a":
        assert_envelope_shape(
            ctx.get("wire_error_envelope"),
            "VALIDATION_ERROR",
            recovery="correctable",
            message_substr=field_name,
        )
    else:
        assert_envelope_shape(
            ctx.get("wire_error_envelope"),
            "INVALID_REQUEST",
            recovery="correctable",
            message_substr="Extra inputs are not permitted",
        )
