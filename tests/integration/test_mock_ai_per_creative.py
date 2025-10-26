#!/usr/bin/env python3
"""Integration test demonstrating per-creative AI orchestration.

This test shows how each creative's name field controls its own test behavior.
"""

from datetime import UTC, datetime

import pytest

from src.adapters.mock_ad_server import MockAdServer
from src.core.database.database_session import get_db_session
from src.core.database.models import Principal, Tenant

pytestmark = [
    pytest.mark.integration,
    pytest.mark.requires_db,
    pytest.mark.skip(reason="TODO: Fix mock_adapter fixture - Principal missing get_adapter_id method"),
]


@pytest.fixture
def mock_adapter(integration_db):
    """Create a mock adapter with test principal."""
    with get_db_session() as session:
        # Create test tenant
        tenant = Tenant(
            tenant_id="test_tenant_ai_creative",
            name="Test Tenant - AI Creative",
            subdomain="test-ai-creative",
            ad_server="mock",  # Use ad_server field instead of config
        )
        session.add(tenant)

        # Create test principal
        principal = Principal(
            tenant_id=tenant.tenant_id,
            principal_id="test_principal_ai",
            name="Test Principal AI",
            access_token="test_token_ai",
            platform_mappings={"mock": {"account_id": "test_mock_account"}},
        )
        session.add(principal)
        session.commit()

        # Create adapter
        config = {"adapters": {"mock": {"enabled": True}}}
        adapter = MockAdServer(
            principal=principal,
            config=config,
            dry_run=False,
        )
        adapter.tenant_id = tenant.tenant_id

        yield adapter

        # Cleanup
        session.delete(principal)
        session.delete(tenant)
        session.commit()


def test_per_creative_ai_orchestration(mock_adapter, mock_gemini_test_scenarios):
    """Test that each creative's name controls its own behavior."""
    # Mock Gemini is already patched via fixture, no need for API key check

    # Create a test media buy first
    media_buy_id = "test_buy_ai_creative"
    mock_adapter._media_buys[media_buy_id] = {
        "media_buy_id": media_buy_id,
        "creatives": [],
        "start_time": datetime.now(UTC),
        "end_time": datetime.now(UTC),
    }

    # Sync creatives with different AI instructions in each name
    assets = [
        {
            "id": "creative_1",
            "name": "approve this banner",
            "format": "display_300x250",
            "media_url": "https://example.com/banner.jpg",
            "click_url": "https://example.com/click",
        },
        {
            "id": "creative_2",
            "name": "reject this for missing URL",
            "format": "display_300x250",
            "media_url": "https://example.com/banner2.jpg",
            "click_url": "https://example.com/click2",
        },
        {
            "id": "creative_3",
            "name": "ask for brand logo",
            "format": "display_300x250",
            "media_url": "https://example.com/banner3.jpg",
            "click_url": "https://example.com/click3",
        },
    ]

    results = mock_adapter._add_creative_assets_immediate(media_buy_id, assets, datetime.now(UTC))

    # Verify results
    assert len(results) == 3

    # First creative should be approved
    assert results[0].creative_id == "creative_1"
    assert results[0].status == "approved"

    # Second creative should be rejected
    assert results[1].creative_id == "creative_2"
    assert results[1].status == "rejected"

    # Third creative should be pending (asking for something)
    assert results[2].creative_id == "creative_3"
    assert results[2].status == "pending"


def test_mixed_creative_behaviors(mock_adapter, mock_gemini_test_scenarios):
    """Test mixing approved and rejected creatives."""
    # Mock Gemini is already patched via fixture, no need for API key check

    # Create a test media buy first
    media_buy_id = "test_buy_mixed"
    mock_adapter._media_buys[media_buy_id] = {
        "media_buy_id": media_buy_id,
        "creatives": [],
        "start_time": datetime.now(UTC),
        "end_time": datetime.now(UTC),
    }

    # Multiple creatives with different outcomes
    assets = [
        {
            "id": "c1",
            "name": "Banner Ad 300x250",
            "format": "display_300x250",
            "media_url": "https://ex.com/1.jpg",
            "click_url": "https://ex.com/c1",
        },
        {
            "id": "c2",
            "name": "reject - missing captions",
            "format": "video",
            "media_url": "https://ex.com/2.mp4",
            "click_url": "https://ex.com/c2",
        },
        {
            "id": "c3",
            "name": "Native Ad Story",
            "format": "native",
            "media_url": "https://ex.com/3.jpg",
            "click_url": "https://ex.com/c3",
        },
    ]

    results = mock_adapter._add_creative_assets_immediate(media_buy_id, assets, datetime.now(UTC))

    # First creative: normal name, should approve
    assert results[0].status == "approved"

    # Second creative: explicit reject instruction, should reject
    assert results[1].status == "rejected"

    # Third creative: normal name, should approve
    assert results[2].status == "approved"


def test_creative_without_test_instructions(mock_adapter, mock_gemini_test_scenarios):
    """Test that creatives without test instructions are auto-approved."""
    # Mock Gemini is already patched via fixture, no need for API key check

    # Create a test media buy first
    media_buy_id = "test_buy_normal"
    mock_adapter._media_buys[media_buy_id] = {
        "media_buy_id": media_buy_id,
        "creatives": [],
        "start_time": datetime.now(UTC),
        "end_time": datetime.now(UTC),
    }

    # Normal creatives with descriptive names (no test instructions)
    assets = [
        {
            "id": "c1",
            "name": "Spring Sale Banner",
            "format": "display_300x250",
            "media_url": "https://ex.com/1.jpg",
            "click_url": "https://ex.com/c1",
        },
        {
            "id": "c2",
            "name": "Product Showcase Video",
            "format": "video",
            "media_url": "https://ex.com/2.mp4",
            "click_url": "https://ex.com/c2",
        },
    ]

    results = mock_adapter._add_creative_assets_immediate(media_buy_id, assets, datetime.now(UTC))

    # Both should be auto-approved
    assert all(r.status == "approved" for r in results)
