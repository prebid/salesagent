#!/usr/bin/env python3
"""A2A sync_creatives: a creative with no ``assets`` map is refused at the boundary.

``assets`` is a REQUIRED field on ``CreativeAsset`` in AdCP 3.1.1, and the
creative's url lives INSIDE that map — the schema has no top-level ``url``. So a
url-shaped creative that omits ``assets`` is malformed, and the A2A boundary must
refuse it with a structured two-layer ``VALIDATION_ERROR`` rather than silently
injecting the missing field.

That matches MCP, whose ``sync_creatives`` is typed ``creatives:
list[CreativeAsset]`` and so rejects the same shape via FastMCP's coercion. This
guard exists so the lenient ``assets`` default — which still lives on the impl's
REST path — is never reintroduced at the A2A boundary, where it would silently
move A2A off both MCP and the schema.
"""

import logging
from unittest.mock import MagicMock

import pytest
from a2a.server.routes.common import ServerCallContext
from a2a.types import SendMessageRequest, Task, TaskState

from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
from src.core.resolved_identity import ResolvedIdentity
from tests.factories.principal import PrincipalFactory
from tests.helpers import assert_envelope_shape
from tests.utils.a2a_helpers import create_a2a_message_with_skill, extract_data_from_artifact

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

logger = logging.getLogger(__name__)


def _make_identity(sample_tenant, sample_principal) -> ResolvedIdentity:
    return PrincipalFactory.make_identity(
        principal_id=sample_principal["principal_id"],
        tenant_id=sample_tenant["tenant_id"],
        tenant=sample_tenant,
        auth_token=sample_principal["access_token"],
        protocol="a2a",
    )


@pytest.mark.asyncio
async def test_sync_creatives_rejects_creative_without_assets_over_a2a(sample_tenant, sample_principal):
    """A url-shaped creative (no ``assets``) draws a structured VALIDATION_ERROR.

    Deletion oracle: default ``assets`` at the boundary (e.g. route the dict through
    the impl's lenient ``creative_asset_from_wire_dict``) and the request stops
    failing — the ``TASK_STATE_FAILED`` and envelope assertions below both go red.
    """
    handler = AdCPRequestHandler()
    identity = _make_identity(sample_tenant, sample_principal)
    handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])
    handler._resolve_a2a_identity = MagicMock(return_value=identity)

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
    result = await handler.on_message_send(SendMessageRequest(message=message), ServerCallContext())

    assert isinstance(result, Task)
    assert result.status.state == TaskState.TASK_STATE_FAILED, (
        f"a creative missing the required `assets` map must be refused, got {result.status.state}"
    )
    assert result.artifacts, "failed task must carry an envelope artifact"
    envelope = extract_data_from_artifact(result.artifacts[0])
    # message_substr pins that the refusal names the offending field so the buyer can
    # correct it; the helper grades both envelope layers (code + recovery) at once.
    assert_envelope_shape(envelope, "VALIDATION_ERROR", recovery="correctable", message_substr="assets")
