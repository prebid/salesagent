"""Integration test for targeting data API endpoint.

This test validates that the /api/tenant/{id}/targeting/all endpoint
correctly returns targeting data, including the 'type' field for audience segments.
"""

import pytest

from src.core.database.database_session import get_db_session
from src.core.database.models import GAMInventory, Tenant

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def test_get_targeting_data_returns_audience_type(authenticated_admin_session, integration_db):
    """Test getting targeting data includes audience segment type."""
    with get_db_session() as db_session:
        # Create test tenant
        tenant = Tenant(
            tenant_id="test_tenant_targeting",
            name="Test Tenant Targeting",
            subdomain="test-targeting",
            ad_server="google_ad_manager",
        )
        db_session.add(tenant)
        db_session.flush()

        # Create audience segment with type
        audience = GAMInventory(
            tenant_id="test_tenant_targeting",
            inventory_type="audience_segment",
            inventory_id="aud_123",
            name="Test Audience",
            status="ACTIVE",
            inventory_metadata={
                "description": "First party data",
                "type": "FIRST_PARTY",
                "size": 1000,
                "segment_type": "RULE_BASED",
            },
        )
        db_session.add(audience)

        # Create third party audience
        audience_3p = GAMInventory(
            tenant_id="test_tenant_targeting",
            inventory_type="audience_segment",
            inventory_id="aud_456",
            name="Test Audience 3P",
            status="ACTIVE",
            inventory_metadata={
                "description": "Third party data",
                "type": "THIRD_PARTY",
                "size": 5000,
            },
        )
        db_session.add(audience_3p)

        db_session.commit()

    # Call API
    response = authenticated_admin_session.get("/api/tenant/test_tenant_targeting/targeting/all")
    assert response.status_code == 200
    data = response.json

    audiences = data["audiences"]
    assert len(audiences) == 2

    # Verify types are present
    aud_1 = next(a for a in audiences if a["id"] == "aud_123")
    assert aud_1["type"] == "FIRST_PARTY"
    assert aud_1["name"] == "Test Audience"

    aud_3 = next(a for a in audiences if a["id"] == "aud_456")
    assert aud_3["type"] == "THIRD_PARTY"
    assert aud_3["name"] == "Test Audience 3P"
