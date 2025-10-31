"""Integration tests for update_media_buy creative assignment functionality."""

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select

from src.core.database.models import Creative as DBCreative
from src.core.database.models import CreativeAssignment as DBAssignment
from src.core.schema_adapters import UpdateMediaBuyResponse
from src.core.tools.media_buy_update import _update_media_buy_impl


@pytest.mark.requires_db
def test_update_media_buy_assigns_creatives_to_package(integration_db):
    """Test that update_media_buy can assign creatives to a package."""
    from src.core.database.database_session import get_db_session
    from src.core.database.models import MediaBuy, Principal, Product, PropertyTag, Tenant

    with get_db_session() as session:
        # Create tenant
        tenant = Tenant(
            tenant_id="test_tenant",
            name="Test Org",
            subdomain="test",
        )
        session.add(tenant)

        # Create property tag (required for products)
        property_tag = PropertyTag(
            tenant_id="test_tenant",
            tag_id="all_inventory",
            name="All Inventory",
            description="All available inventory",
        )
        session.add(property_tag)

        # Create principal (MUST be flushed before creatives due to FK constraint)
        principal = Principal(
            principal_id="test_principal",
            tenant_id="test_tenant",
            name="Test Advertiser",
            access_token="test_token",
            platform_mappings={"mock": {"id": "test_advertiser"}},
        )
        session.add(principal)
        session.flush()  # Ensure principal exists before creating creatives

        # Create product
        product = Product(
            product_id="test_product",
            tenant_id="test_tenant",
            name="Test Product",
            description="Test product for creative assignment",
            formats=["display_300x250"],
            targeting_template={},
            delivery_type="guaranteed",
            property_tags=["all_inventory"],
        )
        session.add(product)

        # Create media buy
        media_buy = MediaBuy(
            media_buy_id="test_buy_123",
            tenant_id="test_tenant",
            principal_id="test_principal",
            buyer_ref="buyer_ref_123",
            order_name="Test Order",
            advertiser_name="Test Advertiser",
            start_date="2025-11-01",
            end_date="2025-11-30",
            start_time="2025-11-01T00:00:00Z",
            end_time="2025-11-30T23:59:59Z",
            raw_request={
                "packages": [{"package_id": "pkg_default", "impressions": 100000, "products": ["test_product"]}]
            },
        )
        session.add(media_buy)

        # Create creatives (FK to principal now satisfied)
        creative1 = DBCreative(
            creative_id="creative_1",
            tenant_id="test_tenant",
            principal_id="test_principal",
            name="Creative 1",
            agent_url="https://creative.adcontextprotocol.org",
            format="display",
            status="ready",
            data={"platform_creative_id": "gam_123"},
        )
        creative2 = DBCreative(
            creative_id="creative_2",
            tenant_id="test_tenant",
            principal_id="test_principal",
            name="Creative 2",
            agent_url="https://creative.adcontextprotocol.org",
            format="display",
            status="ready",
            data={"platform_creative_id": "gam_456"},
        )
        session.add_all([creative1, creative2])
        session.commit()

    # Mock context and tenant resolution
    mock_context = MagicMock()
    mock_context.headers = {"x-adcp-auth": "test_token"}

    with (
        patch("src.core.helpers.get_principal_id_from_context", return_value="test_principal"),
        patch("src.core.config_loader.get_current_tenant", return_value={"tenant_id": "test_tenant"}),
        patch("src.core.auth.get_principal_object", return_value=principal),
        patch("src.core.helpers.adapter_helpers.get_adapter") as mock_get_adapter,
        patch("src.core.context_manager.get_context_manager") as mock_ctx_mgr,
    ):
        # Mock adapter
        mock_adapter = MagicMock()
        mock_adapter.manual_approval_required = False
        mock_get_adapter.return_value = mock_adapter

        # Mock context manager
        mock_ctx_manager_inst = MagicMock()
        mock_ctx_manager_inst.get_or_create_context.return_value = MagicMock(context_id="ctx_123")
        mock_ctx_manager_inst.create_workflow_step.return_value = MagicMock(step_id="step_123")
        mock_ctx_mgr.return_value = mock_ctx_manager_inst

        # Call update_media_buy with creative assignment
        response = _update_media_buy_impl(
            media_buy_id="test_buy_123",
            buyer_ref="buyer_ref_123",
            packages=[
                {
                    "package_id": "pkg_default",
                    "creative_ids": ["creative_1", "creative_2"],
                }
            ],
            context=mock_context,
        )

    # Verify response
    assert isinstance(response, UpdateMediaBuyResponse)
    assert response.media_buy_id == "test_buy_123"
    assert response.buyer_ref == "buyer_ref_123"
    assert response.affected_packages is not None
    assert len(response.affected_packages) == 1

    # Check affected_packages structure
    affected = response.affected_packages[0]
    assert affected["buyer_package_ref"] == "pkg_default"
    assert "changes_applied" in affected
    assert "creative_ids" in affected["changes_applied"]

    creative_changes = affected["changes_applied"]["creative_ids"]
    assert set(creative_changes["added"]) == {"creative_1", "creative_2"}
    assert creative_changes["removed"] == []
    assert set(creative_changes["current"]) == {"creative_1", "creative_2"}

    # Verify assignments were created in database
    with get_db_session() as session:
        assignment_stmt = select(DBAssignment).where(
            DBAssignment.tenant_id == "test_tenant",
            DBAssignment.media_buy_id == "test_buy_123",
            DBAssignment.package_id == "pkg_default",
        )
        assignments = session.scalars(assignment_stmt).all()
        assert len(assignments) == 2
        assigned_creative_ids = {a.creative_id for a in assignments}
        assert assigned_creative_ids == {"creative_1", "creative_2"}


@pytest.mark.requires_db
def test_update_media_buy_replaces_creatives(integration_db):
    """Test that update_media_buy can replace existing creative assignments."""
    from src.core.database.database_session import get_db_session
    from src.core.database.models import MediaBuy, Principal, Product, PropertyTag, Tenant

    with get_db_session() as session:
        # Create tenant
        tenant = Tenant(
            tenant_id="test_tenant",
            name="Test Org",
            subdomain="test",
        )
        session.add(tenant)

        # Create property tag (required for products)
        property_tag = PropertyTag(
            tenant_id="test_tenant",
            tag_id="all_inventory",
            name="All Inventory",
            description="All available inventory",
        )
        session.add(property_tag)

        # Create principal (MUST be flushed before creatives due to FK constraint)
        principal = Principal(
            principal_id="test_principal",
            tenant_id="test_tenant",
            name="Test Advertiser",
            access_token="test_token",
            platform_mappings={"mock": {"id": "test_advertiser"}},
        )
        session.add(principal)
        session.flush()  # Ensure principal exists before creating creatives

        # Create product
        product = Product(
            product_id="test_product",
            tenant_id="test_tenant",
            name="Test Product",
            description="Test product for creative assignment",
            formats=["display_300x250"],
            targeting_template={},
            delivery_type="guaranteed",
            property_tags=["all_inventory"],
        )
        session.add(product)

        # Create media buy
        media_buy = MediaBuy(
            media_buy_id="test_buy_456",
            tenant_id="test_tenant",
            principal_id="test_principal",
            buyer_ref="buyer_ref_456",
            order_name="Test Order",
            advertiser_name="Test Advertiser",
            start_date="2025-11-01",
            end_date="2025-11-30",
            start_time="2025-11-01T00:00:00Z",
            end_time="2025-11-30T23:59:59Z",
            raw_request={
                "packages": [{"package_id": "pkg_default", "impressions": 100000, "products": ["test_product"]}]
            },
        )
        session.add(media_buy)
        session.flush()  # Ensure media_buy exists before creating assignments

        # Create creatives (FK to principal now satisfied)
        creative1 = DBCreative(
            creative_id="creative_1",
            tenant_id="test_tenant",
            principal_id="test_principal",
            name="Creative 1",
            agent_url="https://creative.adcontextprotocol.org",
            format="display",
            status="ready",
            data={},
        )
        creative2 = DBCreative(
            creative_id="creative_2",
            tenant_id="test_tenant",
            principal_id="test_principal",
            name="Creative 2",
            agent_url="https://creative.adcontextprotocol.org",
            format="display",
            status="ready",
            data={},
        )
        creative3 = DBCreative(
            creative_id="creative_3",
            tenant_id="test_tenant",
            principal_id="test_principal",
            name="Creative 3",
            agent_url="https://creative.adcontextprotocol.org",
            format="display",
            status="ready",
            data={},
        )
        session.add_all([creative1, creative2, creative3])

        # Create existing assignments (creative_1 already assigned)
        assignment1 = DBAssignment(
            assignment_id="assign_existing",
            tenant_id="test_tenant",
            media_buy_id="test_buy_456",
            package_id="pkg_default",
            creative_id="creative_1",
        )
        session.add(assignment1)
        session.commit()

    # Mock context and tenant resolution
    mock_context = MagicMock()
    mock_context.headers = {"x-adcp-auth": "test_token"}

    with (
        patch("src.core.helpers.get_principal_id_from_context", return_value="test_principal"),
        patch("src.core.config_loader.get_current_tenant", return_value={"tenant_id": "test_tenant"}),
        patch("src.core.auth.get_principal_object", return_value=principal),
        patch("src.core.helpers.adapter_helpers.get_adapter") as mock_get_adapter,
        patch("src.core.context_manager.get_context_manager") as mock_ctx_mgr,
    ):
        # Mock adapter
        mock_adapter = MagicMock()
        mock_adapter.manual_approval_required = False
        mock_get_adapter.return_value = mock_adapter

        # Mock context manager
        mock_ctx_manager_inst = MagicMock()
        mock_ctx_manager_inst.get_or_create_context.return_value = MagicMock(context_id="ctx_456")
        mock_ctx_manager_inst.create_workflow_step.return_value = MagicMock(step_id="step_456")
        mock_ctx_mgr.return_value = mock_ctx_manager_inst

        # Call update_media_buy to replace creative_1 with creative_2 and creative_3
        response = _update_media_buy_impl(
            media_buy_id="test_buy_456",
            buyer_ref="buyer_ref_456",
            packages=[
                {
                    "package_id": "pkg_default",
                    "creative_ids": ["creative_2", "creative_3"],
                }
            ],
            context=mock_context,
        )

    # Verify response
    assert isinstance(response, UpdateMediaBuyResponse)
    assert response.affected_packages is not None
    assert len(response.affected_packages) == 1

    # Check changes
    affected = response.affected_packages[0]
    creative_changes = affected["changes_applied"]["creative_ids"]
    assert set(creative_changes["added"]) == {"creative_2", "creative_3"}
    assert set(creative_changes["removed"]) == {"creative_1"}
    assert set(creative_changes["current"]) == {"creative_2", "creative_3"}

    # Verify database state
    with get_db_session() as session:
        assignment_stmt = select(DBAssignment).where(
            DBAssignment.tenant_id == "test_tenant",
            DBAssignment.media_buy_id == "test_buy_456",
            DBAssignment.package_id == "pkg_default",
        )
        assignments = session.scalars(assignment_stmt).all()
        assert len(assignments) == 2
        assigned_creative_ids = {a.creative_id for a in assignments}
        assert assigned_creative_ids == {"creative_2", "creative_3"}


@pytest.mark.requires_db
def test_update_media_buy_rejects_missing_creatives(integration_db):
    """Test that update_media_buy rejects requests with non-existent creative IDs."""
    from src.core.database.database_session import get_db_session
    from src.core.database.models import MediaBuy, Principal, Product, PropertyTag, Tenant

    with get_db_session() as session:
        # Create tenant
        tenant = Tenant(
            tenant_id="test_tenant",
            name="Test Org",
            subdomain="test",
        )
        session.add(tenant)

        # Create property tag (required for products)
        property_tag = PropertyTag(
            tenant_id="test_tenant",
            tag_id="all_inventory",
            name="All Inventory",
            description="All available inventory",
        )
        session.add(property_tag)

        # Create principal (MUST be flushed before creatives due to FK constraint)
        principal = Principal(
            principal_id="test_principal",
            tenant_id="test_tenant",
            name="Test Advertiser",
            access_token="test_token",
            platform_mappings={"mock": {"id": "test_advertiser"}},
        )
        session.add(principal)
        session.flush()  # Ensure principal exists before creating creatives

        # Create product
        product = Product(
            product_id="test_product",
            tenant_id="test_tenant",
            name="Test Product",
            description="Test product for creative assignment",
            formats=["display_300x250"],
            targeting_template={},
            delivery_type="guaranteed",
            property_tags=["all_inventory"],
        )
        session.add(product)

        # Create media buy
        media_buy = MediaBuy(
            media_buy_id="test_buy_789",
            tenant_id="test_tenant",
            principal_id="test_principal",
            buyer_ref="buyer_ref_789",
            order_name="Test Order",
            advertiser_name="Test Advertiser",
            start_date="2025-11-01",
            end_date="2025-11-30",
            start_time="2025-11-01T00:00:00Z",
            end_time="2025-11-30T23:59:59Z",
            raw_request={
                "packages": [{"package_id": "pkg_default", "impressions": 100000, "products": ["test_product"]}]
            },
        )
        session.add(media_buy)
        session.commit()

    # Mock context and tenant resolution
    mock_context = MagicMock()
    mock_context.headers = {"x-adcp-auth": "test_token"}

    with (
        patch("src.core.helpers.get_principal_id_from_context", return_value="test_principal"),
        patch("src.core.config_loader.get_current_tenant", return_value={"tenant_id": "test_tenant"}),
        patch("src.core.auth.get_principal_object", return_value=principal),
        patch("src.core.helpers.adapter_helpers.get_adapter") as mock_get_adapter,
        patch("src.core.context_manager.get_context_manager") as mock_ctx_mgr,
    ):
        # Mock adapter
        mock_adapter = MagicMock()
        mock_adapter.manual_approval_required = False
        mock_get_adapter.return_value = mock_adapter

        # Mock context manager
        mock_ctx_manager_inst = MagicMock()
        mock_ctx_manager_inst.get_or_create_context.return_value = MagicMock(context_id="ctx_789")
        mock_ctx_manager_inst.create_workflow_step.return_value = MagicMock(step_id="step_789")
        mock_ctx_mgr.return_value = mock_ctx_manager_inst

        # Call update_media_buy with non-existent creative IDs
        response = _update_media_buy_impl(
            media_buy_id="test_buy_789",
            buyer_ref="buyer_ref_789",
            packages=[
                {
                    "package_id": "pkg_default",
                    "creative_ids": ["nonexistent_creative"],
                }
            ],
            context=mock_context,
        )

    # Verify error response
    assert isinstance(response, UpdateMediaBuyResponse)
    assert response.errors is not None
    assert len(response.errors) > 0
    assert response.errors[0]["code"] == "creatives_not_found"
    assert "nonexistent_creative" in response.errors[0]["message"]
