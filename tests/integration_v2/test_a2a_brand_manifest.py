#!/usr/bin/env python3
"""
Test A2A get_products with brand_manifest parameter.

Verifies that the A2A server properly handles brand_manifest in get_products skill invocations,
including both dict and object formats.
"""

import logging
from unittest.mock import MagicMock, patch

import pytest
from a2a.types import MessageSendParams, Task

from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
from tests.utils.a2a_helpers import create_a2a_message_with_skill

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

logger = logging.getLogger(__name__)


@pytest.mark.asyncio
async def test_get_products_with_brand_manifest_dict(sample_tenant, sample_principal, sample_products, mock_identity):
    """Test get_products skill invocation with brand_manifest as dict.

    KNOWN ISSUE: A2A server loses brand_manifest dict data during parameter extraction.
    The A2A handler receives the parameter but it becomes empty by the time it reaches
    get_products_impl. Needs investigation of A2A parameter marshalling.
    """
    handler = AdCPRequestHandler()

    # Mock auth token
    handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

    # Mock identity resolution
    with patch("src.core.resolved_identity.resolve_identity", return_value=mock_identity):
        # Build ServerCallContext with Host header for tenant detection
        from tests.a2a_helpers import make_a2a_context

        ctx = make_a2a_context(headers={"host": f"{sample_tenant['subdomain']}.example.com"})

        # Create A2A message with brand_manifest as dict
        message = create_a2a_message_with_skill(
            skill_name="get_products",
            parameters={
                "brand_manifest": {"name": "Nike", "url": "https://nike.com"},
                "brief": "Athletic footwear advertising",
            },
        )
        params = MessageSendParams(message=message)

        # Call handler using correct A2A SDK API
        result = await handler.on_message_send(params, context=ctx)

        # Verify we got a Task with artifacts
        assert isinstance(result, Task)
        assert result.artifacts is not None
        assert len(result.artifacts) > 0

        # Extract result from artifact
        artifact = result.artifacts[0]
        assert artifact.parts, "Artifact has no parts"

        result_data = None
        for part in artifact.parts:
            # A2A SDK returns parts with .root attribute (RootModel pattern)
            if hasattr(part, "root"):
                part_content = part.root
                if hasattr(part_content, "data") and isinstance(part_content.data, dict):
                    result_data = part_content.data
                    break
            elif hasattr(part, "data") and isinstance(part.data, dict):
                result_data = part.data
                break

        assert result_data, "Could not extract result data from artifact"
        assert "products" in result_data, "Result missing 'products' field"
        assert isinstance(result_data["products"], list), "Products should be a list"
        assert len(result_data["products"]) > 0, "Expected at least one product"


@pytest.mark.asyncio
async def test_get_products_with_brand_manifest_url_only(
    sample_tenant, sample_principal, sample_products, mock_identity
):
    """Test get_products skill invocation with brand_manifest as URL string.

    KNOWN ISSUE: A2A server rejects brand_manifest as plain string (URL-only format).
    Per AdCP spec, brand_manifest can be a string URL, but A2A parameter validation
    may be too strict. Needs investigation of GetProductsRequest schema handling.
    """
    handler = AdCPRequestHandler()
    handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

    with patch("src.core.resolved_identity.resolve_identity", return_value=mock_identity):
        from tests.a2a_helpers import make_a2a_context

        ctx = make_a2a_context(headers={"host": f"{sample_tenant['subdomain']}.example.com"})

        message = create_a2a_message_with_skill(
            skill_name="get_products",
            parameters={
                "brand_manifest": "https://nike.com",
                "brief": "Athletic footwear advertising",
            },
        )
        params = MessageSendParams(message=message)

        result = await handler.on_message_send(params, context=ctx)

        assert isinstance(result, Task)
        assert result.artifacts is not None
        assert len(result.artifacts) > 0


@pytest.mark.asyncio
async def test_get_products_with_brand_manifest_name_only(
    sample_tenant, sample_principal, sample_products, mock_identity
):
    """Test get_products skill invocation with brand_manifest containing only name."""
    handler = AdCPRequestHandler()
    handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

    with patch("src.core.resolved_identity.resolve_identity", return_value=mock_identity):
        from tests.a2a_helpers import make_a2a_context

        ctx = make_a2a_context(headers={"host": f"{sample_tenant['subdomain']}.example.com"})

        message = create_a2a_message_with_skill(
            skill_name="get_products",
            parameters={
                "brand_manifest": {"name": "Nike"},
                "brief": "Athletic footwear advertising",
            },
        )
        params = MessageSendParams(message=message)

        result = await handler.on_message_send(params, context=ctx)

        assert isinstance(result, Task)
        assert result.artifacts is not None


@pytest.mark.asyncio
async def test_get_products_backward_compat_promoted_offering(
    sample_tenant, sample_principal, sample_products, mock_identity
):
    """Test get_products still works with deprecated promoted_offering parameter."""
    handler = AdCPRequestHandler()
    handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

    with patch("src.core.resolved_identity.resolve_identity", return_value=mock_identity):
        from tests.a2a_helpers import make_a2a_context

        ctx = make_a2a_context(headers={"host": f"{sample_tenant['subdomain']}.example.com"})

        message = create_a2a_message_with_skill(
            skill_name="get_products",
            parameters={
                "promoted_offering": "Nike Athletic Footwear",
                "brief": "Display advertising",
            },
        )
        params = MessageSendParams(message=message)

        result = await handler.on_message_send(params, context=ctx)

        assert isinstance(result, Task)
        assert result.artifacts is not None


@pytest.mark.asyncio
async def test_get_products_missing_brand_info_uses_brief_fallback(
    sample_tenant, sample_principal, sample_products, mock_identity
):
    """Test get_products uses brief as fallback when brand information is missing."""
    handler = AdCPRequestHandler()
    handler._get_auth_token = MagicMock(return_value=sample_principal["access_token"])

    with patch("src.core.resolved_identity.resolve_identity", return_value=mock_identity):
        from tests.a2a_helpers import make_a2a_context

        ctx = make_a2a_context(headers={"host": f"{sample_tenant['subdomain']}.example.com"})

        message = create_a2a_message_with_skill(
            skill_name="get_products",
            parameters={
                "brief": "Display advertising",
            },
        )
        params = MessageSendParams(message=message)

        result = await handler.on_message_send(params, context=ctx)

        # Should complete (uses brief as fallback for promoted_offering)
        assert isinstance(result, Task)
        assert result.artifacts is not None
