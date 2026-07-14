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

from tests.bdd.steps._outcome_helpers import _require_response, wire_field
from tests.helpers.format_assertions import assert_wire_format_id_is_object


def _serialized_formats(ctx: dict) -> list[dict[str, Any]]:
    """Return the formats array as the buyer sees it on the serialized wire.

    Thin alias over the shared :func:`wire_field` reader (single source of truth)
    — REST/A2A/MCP read the real success-path wire dict; IMPL serializes the typed
    payload through the production serializer.
    """
    return wire_field(ctx, "formats")


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
