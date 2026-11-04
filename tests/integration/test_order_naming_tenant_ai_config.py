"""Order naming runs on the seller's own AI configuration, wherever the seller stored it.

The adapters read the tenant row and hand its AI configuration to
``build_order_name_context``. They used to hand over ``gemini_api_key`` alone, so a seller
who configured AI in the Admin UI (``tenants.ai_config``) got no ``{auto_name}``: the
naming fell back to the brand name.

The platform AI credential is removed, so the seller's own configuration is the only one
that can make the AI run. The model call itself is the one thing stubbed: it is a vendor
API this test cannot call.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from adcp.types import FormatId

from src.adapters.base import AdapterCreateRequest
from src.adapters.mock_ad_server import MockAdServer
from src.core.schemas import MediaPackage, Principal
from tests.helpers.settings_injection import PlatformAICredential

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_AI_NAME = "Seller AI Order Name"


@pytest.mark.parametrize(
    ("ai_config", "gemini_api_key"),
    [
        ({"provider": "anthropic", "model": "claude-x", "api_key": "tenant-owned-naming-key"}, None),
        (None, "tenant-owned-legacy-key"),
    ],
    ids=["ai_config", "legacy_gemini_api_key"],
)
def test_mock_adapter_names_the_order_with_the_sellers_own_ai(bound_factory_session, ai_config, gemini_api_key):
    from tests.factories import TenantFactory

    tenant = TenantFactory(order_name_template="{auto_name}", ai_config=ai_config)
    tenant.gemini_api_key = gemini_api_key
    bound_factory_session.commit()

    adapter = MockAdServer(
        config={},
        principal=Principal(principal_id="p_naming", name="Naming Principal", platform_mappings={}),
        tenant_id=tenant.tenant_id,
    )
    package = MediaPackage(
        package_id="pkg_naming",
        name="Naming Package",
        delivery_type="non_guaranteed",
        cpm=10.0,
        impressions=1000,
        format_ids=[FormatId(agent_url="https://creative.example.com", id="display_300x250")],
        product_id="prod_naming",
    )
    request = AdapterCreateRequest(brand={"domain": "naming.example.com"}, total_budget=Decimal("1000.00"))

    with (
        PlatformAICredential(),
        patch(
            "src.services.ai.agents.naming_agent.generate_name_async", new=AsyncMock(return_value=_AI_NAME)
        ) as model_call,
    ):
        result = adapter._create_media_buy_immediate(
            request, [package], datetime(2026, 12, 1, tzinfo=UTC), datetime(2026, 12, 31, tzinfo=UTC)
        )

    assert model_call.await_count == 1, "order naming never asked the seller's AI"
    assert adapter._media_buys[result.media_buy_id]["name"] == _AI_NAME
