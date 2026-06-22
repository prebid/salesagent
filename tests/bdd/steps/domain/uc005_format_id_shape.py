"""UC-005 baseline: list_creative_formats format_id object shape.

Scenario ``T-UC-005-storyboard-baseline-format-id-object-shape`` (@baseline-conformance):
every ``format_id`` returned by ``list_creative_formats`` must be an object carrying
both ``agent_url`` and ``id`` — never a bare string. The "every entry / never a bare
string" strictness is the **schema** contract (``core/format-id.json``: required
[agent_url, id]); the storyboard's discover_formats step only grades ``field_present``
on ``formats[0]``, so this scenario is intentionally stricter than the graded step.

Wired to real production across all 4 transports (auto-parametrized; UC-005 →
CreativeFormatsEnv). REST/A2A/MCP assert the actual serialized wire dict surfaced
by the harness (``ctx["wire_response"]``); IMPL has no wire, so it asserts the
production serializer output (``model_dump(mode="json")`` — exercises
``NestedModelSerializerMixin``). The wire/serializer path is the only place the
contract is falsifiable: the typed payload is a required structured type and can
never be a bare string by construction.
"""

from __future__ import annotations

from typing import Any

from pytest_bdd import then, when

from tests.bdd.steps._outcome_helpers import _require_response
from tests.harness.transport import Transport
from tests.helpers.format_assertions import assert_wire_format_id_is_object


def _serialized_formats(ctx: dict) -> list[dict[str, Any]]:
    """Return the formats array as the buyer sees it on the serialized wire.

    REST/A2A/MCP expose the real success-path wire dict via ``ctx["wire_response"]``.
    IMPL has no wire, so serialize the typed payload through the production
    serializer — the same path that produces wire bytes for the other transports.
    """
    wire = ctx.get("wire_response")
    transport = ctx.get("transport")
    # Loud guard: a real-wire transport (REST/A2A/MCP) that didn't stash
    # wire_response would otherwise fall through to the model_dump path and
    # assert nothing on the wire — the silent tautology this scenario removes.
    # A future sibling wired against a non-stashing env (e.g. media_buy_list,
    # creative_sync, media_buy_update A2A/MCP) trips this instead of passing
    # green. IMPL (and the non-parametrized None default) legitimately have no wire.
    if wire is None and transport not in (None, Transport.IMPL):
        raise AssertionError(f"{transport}: wire_response missing — env does not stash success-path wire")
    if wire is not None:
        return wire["formats"]
    # IMPL has no wire — serialize the typed payload through the production
    # serializer. _require_response preserves the diagnostic if a (reused) sibling
    # scenario hit an error path, instead of a bare ctx["response"] KeyError.
    return _require_response(ctx).model_dump(mode="json")["formats"]


@when("the response returns a non-empty formats array")
def when_response_returns_non_empty_formats(ctx: dict) -> None:
    # Precondition guard (phrased as a When by the storyboard): the Given's
    # dispatch must have returned a non-empty formats array before shape checks.
    assert len(_require_response(ctx).formats) >= 1


@then("every entry's format_id should be an object carrying both agent_url and id")
def then_every_format_id_is_object(ctx: dict) -> None:
    # Non-emptiness is asserted by the preceding When step; here we assert the
    # shape of each entry (element-level, not a count).
    for entry in _serialized_formats(ctx):
        assert_wire_format_id_is_object(entry["format_id"])


@then("no entry's format_id should be a bare string")
def then_no_format_id_is_bare_string(ctx: dict) -> None:
    for entry in _serialized_formats(ctx):
        fid = entry["format_id"]
        assert isinstance(fid, dict), f"format_id is a bare string on the wire: {fid!r}"
