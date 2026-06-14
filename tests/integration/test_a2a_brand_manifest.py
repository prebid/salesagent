#!/usr/bin/env python3
"""
Test A2A get_products brand parameter handling (adcp 3.6.0).

Verifies that the A2A server enforces the schema: brand_manifest is no longer
accepted; clients must use brief or brand (BrandReference with domain field).
"""

import logging
from unittest.mock import MagicMock

import pytest
from a2a.server.routes.common import ServerCallContext
from a2a.types import SendMessageRequest, Task

from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
from src.core.resolved_identity import ResolvedIdentity
from tests.factories.principal import PrincipalFactory
from tests.utils.a2a_helpers import create_a2a_message_with_skill

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

logger = logging.getLogger(__name__)


def _make_identity(sample_tenant, sample_principal) -> ResolvedIdentity:
    """Build a ResolvedIdentity for A2A tests."""
    return PrincipalFactory.make_identity(
        principal_id=sample_principal["principal_id"],
        tenant_id=sample_tenant["tenant_id"],
        tenant=sample_tenant,
        auth_token=sample_principal["access_token"],
        protocol="a2a",
    )


@pytest.mark.asyncio
async def test_get_products_with_brief_only(sample_tenant, sample_principal, sample_products):
    """Test get_products skill invocation with brief only (no brand)."""
    handler = AdCPRequestHandler()
    identity = _make_identity(sample_tenant, sample_principal)
    handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])
    handler._resolve_a2a_identity = MagicMock(return_value=identity)

    from src.core.config_loader import set_current_tenant

    set_current_tenant(sample_tenant)

    message = create_a2a_message_with_skill(
        skill_name="get_products",
        parameters={"brief": "Athletic footwear advertising"},
    )
    params = SendMessageRequest(message=message)

    context = ServerCallContext()
    result = await handler.on_message_send(params, context)

    assert isinstance(result, Task)
    assert result.artifacts is not None
    assert len(result.artifacts) > 0


@pytest.mark.asyncio
async def test_get_products_with_brand_domain(sample_tenant, sample_principal, sample_products):
    """Test get_products skill invocation with brand.domain (adcp 3.6.0 format)."""
    handler = AdCPRequestHandler()
    identity = _make_identity(sample_tenant, sample_principal)
    handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])
    handler._resolve_a2a_identity = MagicMock(return_value=identity)

    from src.core.config_loader import set_current_tenant

    set_current_tenant(sample_tenant)

    message = create_a2a_message_with_skill(
        skill_name="get_products",
        parameters={
            "brand": {"domain": "nike.com"},
            "brief": "Athletic footwear advertising",
        },
    )
    params = SendMessageRequest(message=message)

    context = ServerCallContext()
    result = await handler.on_message_send(params, context)

    assert isinstance(result, Task)
    assert result.artifacts is not None
    assert len(result.artifacts) > 0


@pytest.mark.asyncio
async def test_get_products_brand_manifest_translated_to_brand(sample_tenant, sample_principal, sample_products):
    """Test that brand_manifest is translated to brand via request normalization.

    After the universal request normalization layer (salesagent-3ydk),
    brand_manifest is translated to brand (BrandReference) before the
    handler sees it. So brand_manifest with a valid URL now succeeds.
    """
    handler = AdCPRequestHandler()
    identity = _make_identity(sample_tenant, sample_principal)
    handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])
    handler._resolve_a2a_identity = MagicMock(return_value=identity)

    from src.core.config_loader import set_current_tenant

    set_current_tenant(sample_tenant)

    message = create_a2a_message_with_skill(
        skill_name="get_products",
        parameters={
            "brand_manifest": {"name": "Nike", "url": "https://nike.com"},
            # No brief, no brand — but brand_manifest is translated to brand
        },
    )
    params = SendMessageRequest(message=message)

    # brand_manifest is now translated to brand: {domain: "nike.com"}
    context = ServerCallContext()
    result = await handler.on_message_send(params, context)

    assert isinstance(result, Task)
    assert result.artifacts is not None
    assert len(result.artifacts) > 0


@pytest.mark.asyncio
async def test_get_products_neither_brief_nor_brand_browses_wholesale(sample_tenant, sample_principal, sample_products):
    """Empty params (pre-v3, no brief) default to wholesale and browse all inventory.

    PART 1 ``resolve_pre_v3_buying_mode`` deliberately defaults a pre-v3 client that
    also sent no brief to 'wholesale' (not 'brief'), so v2 clients that legitimately
    discover raw inventory with no brief are not broken. The skill completes with the
    products catalog rather than failing — the old mode-agnostic "neither brief nor
    brand" rejection is gone.
    """
    from a2a.types import TaskState

    from tests.utils.a2a_helpers import extract_data_from_artifact

    handler = AdCPRequestHandler()
    identity = _make_identity(sample_tenant, sample_principal)
    handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])
    handler._resolve_a2a_identity = MagicMock(return_value=identity)

    from src.core.config_loader import set_current_tenant

    set_current_tenant(sample_tenant)

    message = create_a2a_message_with_skill(
        skill_name="get_products",
        parameters={},
    )
    params = SendMessageRequest(message=message)

    context = ServerCallContext()
    result = await handler.on_message_send(params, context)

    # Empty params (pre-v3, no brief) → defaults to 'wholesale' and browses all
    # inventory, completing with the products catalog (no mode-agnostic rejection).
    assert isinstance(result, Task)
    assert result.status.state == TaskState.TASK_STATE_COMPLETED, (
        f"Expected a completed wholesale browse, got {result.status.state}"
    )
    assert result.artifacts, "Completed browse must carry a products artifact"
    payload = extract_data_from_artifact(result.artifacts[0])
    assert "products" in payload, "wholesale browse returns the products catalog"
