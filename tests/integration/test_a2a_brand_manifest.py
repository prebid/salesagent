#!/usr/bin/env python3
"""
Test A2A get_products brand parameter handling (adcp 3.6.0).

Verifies that the A2A server enforces the schema: brand_manifest is no longer
accepted; clients must use brief or brand (BrandReference with domain field).
"""

import logging

import pytest
from a2a.server.routes.common import ServerCallContext
from a2a.types import SendMessageRequest, Task, TaskState

from tests.helpers import assert_envelope_shape
from tests.utils.a2a_helpers import create_a2a_message_with_skill, extract_data_from_artifact

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

logger = logging.getLogger(__name__)


@pytest.mark.asyncio
async def test_get_products_with_brief_only(seamed_a2a_handler, sample_tenant, sample_products):
    """Test get_products skill invocation with brief only (no brand)."""
    from src.core.config_loader import set_current_tenant

    set_current_tenant(sample_tenant)

    message = create_a2a_message_with_skill(
        skill_name="get_products",
        parameters={"brief": "Athletic footwear advertising"},
    )
    params = SendMessageRequest(message=message)

    context = ServerCallContext()
    result = await seamed_a2a_handler.on_message_send(params, context)

    assert isinstance(result, Task)
    assert result.artifacts is not None
    assert len(result.artifacts) > 0


@pytest.mark.asyncio
async def test_get_products_with_brand_domain(seamed_a2a_handler, sample_tenant, sample_products):
    """Test get_products skill invocation with brand.domain (adcp 3.6.0 format)."""
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
    result = await seamed_a2a_handler.on_message_send(params, context)

    assert isinstance(result, Task)
    assert result.artifacts is not None
    assert len(result.artifacts) > 0


@pytest.mark.asyncio
async def test_get_products_brand_manifest_translated_to_brand(seamed_a2a_handler, sample_tenant, sample_products):
    """Test that brand_manifest is translated to brand via request normalization.

    After the universal request normalization layer ,
    brand_manifest is translated to brand (BrandReference) before the
    handler sees it. So brand_manifest with a valid URL now succeeds.
    """
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
    result = await seamed_a2a_handler.on_message_send(params, context)

    assert isinstance(result, Task)
    assert result.artifacts is not None
    assert len(result.artifacts) > 0


@pytest.mark.asyncio
async def test_get_products_neither_brief_nor_brand_rejected(seamed_a2a_handler, sample_tenant, sample_products):
    """Test that requests with neither brief nor brand are rejected.

    The handler raises AdCPValidationError; the explicit-skill dispatcher
    catches it and surfaces a failed Task with the two-layer envelope as
    the artifact DataPart — AdCP-domain errors are async-task failures, not
    JSON-RPC transport errors.
    """
    from src.core.config_loader import set_current_tenant

    set_current_tenant(sample_tenant)

    message = create_a2a_message_with_skill(
        skill_name="get_products",
        parameters={},
    )
    params = SendMessageRequest(message=message)

    context = ServerCallContext()
    result = await seamed_a2a_handler.on_message_send(params, context)

    # Empty params → AdCPValidationError → failed Task with envelope DataPart
    assert isinstance(result, Task)
    assert result.status.state == TaskState.TASK_STATE_FAILED, f"Expected failed task, got {result.status.state}"
    assert result.artifacts, "Failed task must carry an envelope artifact"
    envelope = extract_data_from_artifact(result.artifacts[0])
    # Two-layer envelope: adcp_error mirror + errors[] payload
    assert_envelope_shape(envelope, "VALIDATION_ERROR", recovery="correctable")
