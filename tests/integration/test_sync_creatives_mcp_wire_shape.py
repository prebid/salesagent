"""MCP wire shape for sync_creatives: changes/warnings must be arrays, never null.

Regression for salesagent-274u (PR #1567, adcp 5.7->6.6 bump, round-2 review
blocker 3). adcp 6.6 re-added ``changes``/``warnings`` to the library
SyncCreativeResult parent with ``None`` defaults, and the bump switched our
subclass from local declarations (which emitted ``[]`` under 5.7) to
inheritance. On A2A/REST the custom ``model_dump()`` override strips the empty
values, but the MCP transport serializes ``structured_content`` via pydantic's
``to_jsonable_python``, which BYPASSES ``model_dump`` overrides — so the MCP
wire emits ``"changes": null`` / ``"warnings": null``.

Spec grounding (pinned 3.1.1,
tests/fixtures/adcp_schemas_pinned/creative/sync-creatives-response.json): the
per-creative ``changes`` and ``warnings`` properties are typed ``array`` —
``null`` is not a valid value; the field must be a list or absent.

This file is also the MCP-wire jsonschema oracle the round-2 review flagged as
missing: existing sync_creatives tests validate the typed model/payload, not
the actual MCP wire bytes. ``result.wire_response`` here IS the real
``ToolResult.structured_content`` captured by the harness MCP client.
"""

from __future__ import annotations

import pytest

from tests.factories.creative_asset import CreativeAssetFactory
from tests.harness import CreativeSyncEnv, Transport
from tests.helpers.pinned_schema import validate_against_pinned_schema

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def _sync_one_creative_via_mcp():
    with CreativeSyncEnv() as env:
        env.setup_default_data()
        creative = CreativeAssetFactory(
            creative_id="c_mcp_wire_shape",
            name="MCP Wire Shape Creative",
        )
        result = env.call_via(Transport.MCP, creatives=[creative])
    assert result.is_success, f"Expected success but got error: {result.error}"
    wire = result.wire_response
    assert wire is not None, "MCP dispatch must stash the real structured_content wire"
    return wire


def test_mcp_wire_changes_and_warnings_are_never_null(integration_db):
    """Per-creative changes/warnings on the MCP wire are lists or absent, never null."""
    wire = _sync_one_creative_via_mcp()
    creatives = wire.get("creatives")
    assert isinstance(creatives, list) and creatives, f"MCP wire must carry the creatives array, got {creatives!r}"
    for i, item in enumerate(creatives):
        for field in ("changes", "warnings"):
            if field in item:
                assert isinstance(item[field], list), (
                    f"creatives[{i}].{field} must be an array on the MCP wire (spec 3.1.1 "
                    f"sync-creatives-response.json types it array), got {item[field]!r}"
                )


def _nulls_as_absent(obj):
    """Recursively drop null-valued keys (the model_dump exclude_none wire equivalence)."""
    if isinstance(obj, dict):
        return {k: _nulls_as_absent(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [_nulls_as_absent(v) for v in obj]
    return obj


def test_mcp_wire_validates_against_pinned_response_schema(integration_db):
    """The MCP structured_content validates against the pinned 3.1.1 response schema.

    Nulls are treated as ABSENT before validation: the PRE-EXISTING MCP
    None-serialization question (inherited spec ``status``/version-envelope
    fields serialize as null via the same structured_content bypass) was
    explicitly out-of-scoped from salesagent-274u by the reviewer and is
    tracked separately. Null-stripping hides only that known issue — a
    wrong-TYPE value (e.g. ``changes`` as a string or object) still fails the
    schema, and present-as-null for the array-typed fields is pinned by
    ``test_mcp_wire_changes_and_warnings_are_never_null`` above.
    """
    wire = _sync_one_creative_via_mcp()
    validate_against_pinned_schema("sync-creatives-response.json", _nulls_as_absent(wire))
