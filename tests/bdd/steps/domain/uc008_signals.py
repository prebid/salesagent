"""Steps for UC-008 get_signals discovery — salesagent-8wf2/d0l4.

Wired scenarios run a real get_signals through the wire transports on
SignalsEnv (zero mocks — the static signal catalog IS production code; the
wrappers registered by salesagent-2rls are the surface under test).
Assertions read the REAL serialized wire body via ``wire_field``.

Transport-tagged storyboard scenarios (@mcp / @rest @a2a) suppress
auto-parametrization and pin their transport in the When step; untagged
wired scenarios (context-echo) parametrize across a2a/mcp/rest.
"""

from __future__ import annotations

import json
from typing import Any

from pytest_bdd import given, parsers, then, when

from tests.bdd.steps._outcome_helpers import wire_field
from tests.bdd.steps.generic._dispatch import dispatch_request

_VALUE_TYPES = {"binary", "categorical", "numeric"}
_SIGNAL_TYPES = {"marketplace", "custom", "owned"}


# ── Given steps ─────────────────────────────────────────────────────


@given("at least one signals agent is registered for the tenant")
def given_signals_agent_registered(ctx: dict) -> None:
    """Background: the sales agent itself serves the signal catalog."""
    env = ctx["env"]
    tenant, principal = env.setup_default_data()
    ctx.setdefault("tenant", tenant)
    ctx.setdefault("principal", principal)


@given(parsers.parse('the Buyer provides a valid signal_spec "{spec}"'))
@given(parsers.parse('the Buyer provides signal_spec "{spec}"'))
def given_signal_spec(ctx: dict, spec: str) -> None:
    ctx.setdefault("request_kwargs", {})["signal_spec"] = spec


@given(parsers.parse('destinations includes "{destination}"'))
def given_destinations(ctx: dict, destination: str) -> None:
    # v3.1.1 core/destination.json: discriminated union on `type`; the platform
    # variant requires {type: platform, platform: <id>}.
    ctx.setdefault("request_kwargs", {}).setdefault("destinations", []).append(
        {"type": "platform", "platform": destination}
    )


@given(parsers.parse('countries includes "{country}"'))
def given_countries(ctx: dict, country: str) -> None:
    ctx.setdefault("request_kwargs", {}).setdefault("countries", []).append(country)


@given(parsers.parse("the request includes context {context_json}"))
def given_request_context(ctx: dict, context_json: str) -> None:
    ctx.setdefault("request_kwargs", {})["context"] = json.loads(context_json)
    ctx["expected_context"] = json.loads(context_json)


# ── When steps ──────────────────────────────────────────────────────


def _dispatch_get_signals(ctx: dict) -> None:
    from src.core.schemas import GetSignalsRequest

    req = GetSignalsRequest(**ctx.get("request_kwargs", {}))
    dispatch_request(ctx, req=req)


@when("the Buyer Agent calls the get_signals MCP tool")
def when_get_signals_mcp(ctx: dict) -> None:
    from tests.harness.transport import Transport

    ctx.setdefault("transport", Transport.MCP)
    _dispatch_get_signals(ctx)


@when("the Buyer Agent sends a get_signals A2A task request")
def when_get_signals_a2a(ctx: dict) -> None:
    from tests.harness.transport import Transport

    ctx.setdefault("transport", Transport.A2A)
    _dispatch_get_signals(ctx)


@when("the Buyer Agent sends a get_signals request")
def when_get_signals(ctx: dict) -> None:
    """Transport-agnostic When — parametrized transport dispatches."""
    _dispatch_get_signals(ctx)


# ── Then steps ──────────────────────────────────────────────────────


def _wire_signals(ctx: dict) -> list[dict[str, Any]]:
    assert ctx.get("error") is None, f"Request failed: {ctx.get('error')}"
    signals = wire_field(ctx, "signals")
    assert isinstance(signals, list), f"signals is not an array on the wire: {type(signals).__name__}"
    return signals


@then("the response contains a non-empty signals array")
def then_signals_non_empty(ctx: dict) -> None:
    signals = _wire_signals(ctx)
    assert signals, "signals array is empty"


@then("each signal includes signal_id")
def then_signals_have_signal_id(ctx: dict) -> None:
    for signal in _wire_signals(ctx):
        assert signal.get("signal_id"), f"signal missing signal_id: {signal.get('name')!r}"


@then("each signal includes signal_agent_segment_id, name, description")
def then_signals_have_identity_fields(ctx: dict) -> None:
    for signal in _wire_signals(ctx):
        for field in ("signal_agent_segment_id", "name", "description"):
            assert signal.get(field), f"signal missing {field}: {signal!r}"


@then("each signal includes signal_type from [marketplace, custom, owned]")
def then_signals_have_signal_type(ctx: dict) -> None:
    for signal in _wire_signals(ctx):
        assert signal.get("signal_type") in _SIGNAL_TYPES, f"bad signal_type: {signal.get('signal_type')!r}"


@then("each signal includes data_provider name")
def then_signals_have_data_provider(ctx: dict) -> None:
    for signal in _wire_signals(ctx):
        assert signal.get("data_provider"), f"signal missing data_provider: {signal.get('name')!r}"


@then("each signal includes coverage_percentage between 0 and 100")
def then_signals_have_coverage(ctx: dict) -> None:
    for signal in _wire_signals(ctx):
        coverage = signal.get("coverage_percentage")
        assert coverage is not None and 0 <= coverage <= 100, f"bad coverage_percentage: {coverage!r}"


@then("each signal includes deployments array")
def then_signals_have_deployments(ctx: dict) -> None:
    for signal in _wire_signals(ctx):
        deployments = signal.get("deployments")
        assert isinstance(deployments, list) and deployments, f"signal missing deployments: {signal.get('name')!r}"


@then("each signal includes pricing_options array with at least 1 entry")
def then_signals_have_pricing_options(ctx: dict) -> None:
    for signal in _wire_signals(ctx):
        pricing = signal.get("pricing_options")
        assert isinstance(pricing, list) and len(pricing) >= 1, (
            f"signal missing pricing_options: {signal.get('name')!r}"
        )


@then("each pricing_option includes pricing_option_id and a pricing model")
def then_pricing_options_shape(ctx: dict) -> None:
    for signal in _wire_signals(ctx):
        for option in signal.get("pricing_options", []):
            assert option.get("pricing_option_id"), f"pricing_option missing id: {option!r}"
            assert option.get("model"), f"pricing_option missing model: {option!r}"


@then("each signal includes value_type from [binary, categorical, numeric]")
def then_signals_have_value_type(ctx: dict) -> None:
    """Production gap: the catalog sets no value_type (schema default None).

    Hard assert keeps T-UC-008-main-mcp strict-xfail until the catalog
    carries value_type (see conftest _UC008_XFAIL_TAGS).
    """
    for signal in _wire_signals(ctx):
        assert signal.get("value_type") in _VALUE_TYPES, (
            f"signal {signal.get('name')!r} value_type={signal.get('value_type')!r} not in {_VALUE_TYPES}"
        )


@then("the response is wrapped in MCP ToolResult content")
def then_wrapped_in_tool_result(ctx: dict) -> None:
    """The MCP dispatcher stashes ToolResult.structured_content as the wire body."""
    assert wire_field(ctx, "signals") is not None, "no structured_content captured from MCP ToolResult"


@then("the response is returned directly (no ToolResult wrapper)")
def then_returned_directly(ctx: dict) -> None:
    """A2A returns the response artifact directly — signals at the top level."""
    assert wire_field(ctx, "signals") is not None, "signals not at the top level of the A2A artifact"


@then(parsers.parse("the response context equals {context_json}"))
def then_context_echoed(ctx: dict, context_json: str) -> None:
    expected = json.loads(context_json)
    assert ctx.get("error") is None, f"Request failed: {ctx.get('error')}"
    actual = wire_field(ctx, "context")
    assert actual == expected, f"context not echoed: {actual!r} != {expected!r}"
