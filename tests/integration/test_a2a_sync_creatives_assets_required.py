#!/usr/bin/env python3
"""A2A sync_creatives: the explicit-skill boundary refuses a creative with no ``assets`` map.

``assets`` is a REQUIRED field on ``CreativeAsset`` in AdCP 3.1.1, and the
creative's url lives INSIDE that map — the schema has no top-level ``url``. So a
url-shaped creative that omits ``assets`` is malformed, and the A2A boundary must
refuse it with a structured two-layer ``VALIDATION_ERROR`` rather than silently
injecting the missing field.

Scope: this pins the A2A *explicit skill-invocation* path only —
``on_message_send`` → ``_handle_sync_creatives_skill`` — which constructs
``CreativeAsset`` strictly. It is deliberately NOT a cross-transport claim. No
``BR-UC-006`` BDD scenario covers this path: ``CreativeSyncEnv.call_a2a``
(tests/harness/creative_sync.py) routes a2a sync_creatives through
``sync_creatives_raw``, which forwards raw dicts to the impl and defaults
``assets`` — bypassing this boundary — so this integration test is the only
coverage of the skill-boundary refusal. MCP rejects the same shape via its typed
``list[CreativeAsset]`` signature (type-enforced, not wire-pinned); REST still
defaults ``assets={}`` via ``_creative_asset_from_wire_dict`` and accepts. That
REST/A2A-raw-vs-MCP/A2A-skill divergence, and whether a schema-invalid item should
ride back per-item instead, is a separate cross-transport reconciliation — the
transport-blind ``BR-UC-006`` assets-required scenario tracked in issue #1731 (which
also fixes ``CreativeSyncEnv.call_a2a`` to drive the real skill handler) — not this
guard's subject. This guard exists so the lenient ``assets`` default is never
reintroduced at the A2A skill boundary, where it would silently move that path off
both MCP and the schema; it is removed in the same change that lands #1731.
"""

import logging

import pytest
from a2a.server.routes.common import ServerCallContext
from a2a.types import SendMessageRequest, Task, TaskState

from tests.helpers import assert_envelope_shape
from tests.utils.a2a_helpers import (
    create_a2a_message_with_skill,
    extract_data_from_artifact,
)

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

logger = logging.getLogger(__name__)


@pytest.mark.asyncio
async def test_sync_creatives_rejects_creative_without_assets_over_a2a(seamed_a2a_handler, sample_tenant):
    """A url-shaped creative (no ``assets``) draws a structured VALIDATION_ERROR.

    Deletion oracle: default ``assets`` at the boundary (e.g. route the dict through
    the impl's lenient ``_creative_asset_from_wire_dict``) and the request stops
    failing — the ``TASK_STATE_FAILED`` and envelope assertions below both go red.
    """
    from src.core.config_loader import set_current_tenant

    set_current_tenant(sample_tenant)

    # Legacy url-shaped creative: a string format_id (upgraded at the boundary) and a
    # top-level `url` instead of the required structured `assets` map.
    creative = {
        "creative_id": "c_url_shaped",
        "name": "URL Display Creative",
        "format_id": "display_300x250",
        "url": "https://example.com/banner.jpg",
    }
    message = create_a2a_message_with_skill(skill_name="sync_creatives", parameters={"creatives": [creative]})
    result = await seamed_a2a_handler.on_message_send(SendMessageRequest(message=message), ServerCallContext())

    assert isinstance(result, Task)
    assert result.status.state == TaskState.TASK_STATE_FAILED, (
        f"a creative missing the required `assets` map must be refused, got {result.status.state}"
    )
    assert result.artifacts, "failed task must carry an envelope artifact"
    envelope = extract_data_from_artifact(result.artifacts[0])
    # message_substr pins that the refusal names the offending field so the buyer can
    # correct it; the helper grades both envelope layers (code + recovery) at once.
    assert_envelope_shape(envelope, "VALIDATION_ERROR", recovery="correctable", message_substr="assets")
