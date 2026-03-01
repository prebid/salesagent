#!/usr/bin/env python3
"""
Test A2A get_products brand parameter handling (adcp 3.6.0).

Verifies that the A2A server enforces the schema: brand_manifest is no longer
accepted; clients must use brief or brand (BrandReference with domain field).
"""

import logging
from unittest.mock import MagicMock

import pytest
from a2a.types import MessageSendParams, Task
from a2a.utils.errors import InvalidParamsError, ServerError

from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
from src.core.resolved_identity import ResolvedIdentity
from tests.utils.a2a_helpers import create_a2a_message_with_skill

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

logger = logging.getLogger(__name__)


def _make_identity(sample_tenant, sample_principal) -> ResolvedIdentity:
    """Build a ResolvedIdentity for A2A tests."""
    return ResolvedIdentity(
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
    params = MessageSendParams(message=message)

    result = await handler.on_message_send(params)

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
    params = MessageSendParams(message=message)

    result = await handler.on_message_send(params)

    assert isinstance(result, Task)
    assert result.artifacts is not None
    assert len(result.artifacts) > 0


@pytest.mark.asyncio
async def test_get_products_brand_manifest_without_brief_rejected(sample_tenant, sample_principal, sample_products):
    """Test that brand_manifest without brief is rejected (brand_manifest is not brief or brand).

    The handler raises AdCPValidationError which is translated to
    InvalidParamsError at the A2A boundary via _adcp_to_a2a_error().
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
            # No brief, no brand
        },
    )
    params = MessageSendParams(message=message)

    # brand_manifest without brief or brand → AdCPValidationError → InvalidParamsError
    with pytest.raises(ServerError) as exc_info:
        await handler.on_message_send(params)

    assert isinstance(exc_info.value.error, InvalidParamsError)


@pytest.mark.asyncio
async def test_get_products_neither_brief_nor_brand_rejected(sample_tenant, sample_principal, sample_products):
    """Test that requests with neither brief nor brand are rejected.

    The handler raises AdCPValidationError which is translated to
    InvalidParamsError at the A2A boundary via _adcp_to_a2a_error().
    """
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
    params = MessageSendParams(message=message)

    # Empty params → AdCPValidationError → InvalidParamsError
    with pytest.raises(ServerError) as exc_info:
        await handler.on_message_send(params)

    assert isinstance(exc_info.value.error, InvalidParamsError)
