"""Integration tests for update_media_buy creative assignment functionality."""

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select

from src.core.database.models import Creative as DBCreative
from src.core.database.models import CreativeAssignment as DBAssignment
from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas import UpdateMediaBuyRequest, UpdateMediaBuyResponse
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
            format_ids=["display_300x250"],
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

    # Create identity for the new _update_media_buy_impl signature
    identity = ResolvedIdentity(
        principal_id="test_principal",
        tenant_id="test_tenant",
        tenant={"tenant_id": "test_tenant"},
        auth_token="test_token",
        protocol="mcp",
    )

    with (
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
        req = UpdateMediaBuyRequest(
            media_buy_id="test_buy_123",
            packages=[
                {
                    "package_id": "pkg_default",
                    "creative_ids": ["creative_1", "creative_2"],
                }
            ],
        )
        response = _update_media_buy_impl(req=req, identity=identity)

    # Verify response
    assert isinstance(response, UpdateMediaBuyResponse)
    assert response.media_buy_id == "test_buy_123"
    # buyer_ref is empty because the request uses media_buy_id (oneOf constraint)
    # and the response reflects req.buyer_ref which is None
    assert response.affected_packages is not None
    assert len(response.affected_packages) == 1

    # Check affected_packages structure
    affected = response.affected_packages[0]
    assert affected.buyer_package_ref == "pkg_default"  # Internal field
    assert affected.changes_applied is not None  # Internal field
    assert "creative_ids" in affected.changes_applied

    creative_changes = affected.changes_applied["creative_ids"]
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
            format_ids=["display_300x250"],
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
            principal_id="test_principal",
            media_buy_id="test_buy_456",
            package_id="pkg_default",
            creative_id="creative_1",
        )
        session.add(assignment1)
        session.commit()

    # Create identity for the new _update_media_buy_impl signature
    identity = ResolvedIdentity(
        principal_id="test_principal",
        tenant_id="test_tenant",
        tenant={"tenant_id": "test_tenant"},
        auth_token="test_token",
        protocol="mcp",
    )

    with (
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
        req = UpdateMediaBuyRequest(
            media_buy_id="test_buy_456",
            packages=[
                {
                    "package_id": "pkg_default",
                    "creative_ids": ["creative_2", "creative_3"],
                }
            ],
        )
        response = _update_media_buy_impl(req=req, identity=identity)

    # Verify response
    assert isinstance(response, UpdateMediaBuyResponse)
    assert response.affected_packages is not None
    assert len(response.affected_packages) == 1

    # Check changes
    affected = response.affected_packages[0]
    creative_changes = affected.changes_applied["creative_ids"]  # Access internal field via attribute
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
            format_ids=["display_300x250"],
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

    # Create identity for the new _update_media_buy_impl signature
    identity = ResolvedIdentity(
        principal_id="test_principal",
        tenant_id="test_tenant",
        tenant={"tenant_id": "test_tenant"},
        auth_token="test_token",
        protocol="mcp",
    )

    with (
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
        req = UpdateMediaBuyRequest(
            media_buy_id="test_buy_789",
            packages=[
                {
                    "package_id": "pkg_default",
                    "creative_ids": ["nonexistent_creative"],
                }
            ],
        )
        response = _update_media_buy_impl(req=req, identity=identity)

    # Verify error response
    assert isinstance(response, UpdateMediaBuyResponse)
    assert response.errors is not None
    assert len(response.errors) > 0
    assert response.errors[0].code == "creatives_not_found"
    assert "nonexistent_creative" in response.errors[0].message


@pytest.mark.requires_db
def test_creative_assignments_with_weights(integration_db):
    """UC-003-CA01: creative_assignments replaces all with specified weights.

    Tests that update_media_buy handles the creative_assignments field
    (as opposed to creative_ids) with weight values per AdCP spec.
    """
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
        session.flush()

        # Create product
        product = Product(
            product_id="test_product",
            tenant_id="test_tenant",
            name="Test Product",
            description="Test product for creative assignment weights",
            format_ids=["display_300x250"],
            targeting_template={},
            delivery_type="guaranteed",
            property_tags=["all_inventory"],
        )
        session.add(product)

        # Create media buy
        media_buy = MediaBuy(
            media_buy_id="test_buy_weights",
            tenant_id="test_tenant",
            principal_id="test_principal",
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
            creative_id="c1",
            tenant_id="test_tenant",
            principal_id="test_principal",
            name="Creative 1",
            agent_url="https://creative.adcontextprotocol.org",
            format="display",
            status="ready",
            data={},
        )
        creative2 = DBCreative(
            creative_id="c2",
            tenant_id="test_tenant",
            principal_id="test_principal",
            name="Creative 2",
            agent_url="https://creative.adcontextprotocol.org",
            format="display",
            status="ready",
            data={},
        )
        session.add_all([creative1, creative2])
        session.commit()

    # Create ResolvedIdentity for transport-agnostic _impl call
    identity = ResolvedIdentity(
        principal_id="test_principal",
        tenant_id="test_tenant",
        tenant={"tenant_id": "test_tenant"},
        protocol="mcp",
    )

    with (
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
        mock_ctx_manager_inst.get_or_create_context.return_value = MagicMock(context_id="ctx_weights")
        mock_ctx_manager_inst.create_workflow_step.return_value = MagicMock(step_id="step_weights")
        mock_ctx_mgr.return_value = mock_ctx_manager_inst

        # Call update_media_buy with creative_assignments (not creative_ids)
        req = UpdateMediaBuyRequest(
            media_buy_id="test_buy_weights",
            packages=[
                {
                    "package_id": "pkg_default",
                    "creative_assignments": [
                        {"creative_id": "c1", "weight": 70},
                        {"creative_id": "c2", "weight": 30},
                    ],
                }
            ],
        )
        response = _update_media_buy_impl(req=req, identity=identity)

    # Verify response is successful (not an error)
    assert isinstance(response, UpdateMediaBuyResponse)
    assert not hasattr(response, "errors") or not response.errors

    # Verify assignments were created in database with correct weights
    with get_db_session() as session:
        assignment_stmt = select(DBAssignment).where(
            DBAssignment.tenant_id == "test_tenant",
            DBAssignment.media_buy_id == "test_buy_weights",
            DBAssignment.package_id == "pkg_default",
        )
        assignments = session.scalars(assignment_stmt).all()
        assert len(assignments) == 2

        # Build a map of creative_id -> weight
        weight_map = {a.creative_id: a.weight for a in assignments}
        assert weight_map["c1"] == 70
        assert weight_map["c2"] == 30


@pytest.mark.requires_db
def test_creative_assignments_replaces_all(integration_db):
    """UC-003-CA01 / BR-RULE-024 INV-2: creative_assignments replaces ALL existing.

    When creative_assignments is sent, it should replace the entire set of
    assignments for that package, not just add/update. If c1 and c2 are assigned
    and we send creative_assignments=[c2, c3], the result should be only c2 and c3.
    """
    from src.core.database.database_session import get_db_session
    from src.core.database.models import MediaBuy, Principal, Product, PropertyTag, Tenant

    with get_db_session() as session:
        tenant = Tenant(tenant_id="test_tenant", name="Test Org", subdomain="test")
        session.add(tenant)

        property_tag = PropertyTag(
            tenant_id="test_tenant",
            tag_id="all_inventory",
            name="All Inventory",
            description="All available inventory",
        )
        session.add(property_tag)

        principal = Principal(
            principal_id="test_principal",
            tenant_id="test_tenant",
            name="Test Advertiser",
            access_token="test_token",
            platform_mappings={"mock": {"id": "test_advertiser"}},
        )
        session.add(principal)
        session.flush()

        product = Product(
            product_id="test_product",
            tenant_id="test_tenant",
            name="Test Product",
            description="Test product",
            format_ids=["display_300x250"],
            targeting_template={},
            delivery_type="guaranteed",
            property_tags=["all_inventory"],
        )
        session.add(product)

        media_buy = MediaBuy(
            media_buy_id="test_buy_replace",
            tenant_id="test_tenant",
            principal_id="test_principal",
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

        # Create three creatives
        for cid in ["c1", "c2", "c3"]:
            session.add(
                DBCreative(
                    creative_id=cid,
                    tenant_id="test_tenant",
                    principal_id="test_principal",
                    name=f"Creative {cid}",
                    agent_url="https://creative.adcontextprotocol.org",
                    format="display",
                    status="ready",
                    data={},
                )
            )
        session.flush()

        # Pre-existing assignments: c1 (weight 50) and c2 (weight 50)
        session.add(
            DBAssignment(
                assignment_id="assign_c1",
                tenant_id="test_tenant",
                principal_id="test_principal",
                media_buy_id="test_buy_replace",
                package_id="pkg_default",
                creative_id="c1",
                weight=50,
            )
        )
        session.add(
            DBAssignment(
                assignment_id="assign_c2",
                tenant_id="test_tenant",
                principal_id="test_principal",
                media_buy_id="test_buy_replace",
                package_id="pkg_default",
                creative_id="c2",
                weight=50,
            )
        )
        session.commit()

    # Create ResolvedIdentity for transport-agnostic _impl call
    identity = ResolvedIdentity(
        principal_id="test_principal",
        tenant_id="test_tenant",
        tenant={"tenant_id": "test_tenant"},
        protocol="mcp",
    )

    with (
        patch("src.core.auth.get_principal_object", return_value=principal),
        patch("src.core.helpers.adapter_helpers.get_adapter") as mock_get_adapter,
        patch("src.core.context_manager.get_context_manager") as mock_ctx_mgr,
    ):
        mock_adapter = MagicMock()
        mock_adapter.manual_approval_required = False
        mock_get_adapter.return_value = mock_adapter

        mock_ctx_manager_inst = MagicMock()
        mock_ctx_manager_inst.get_or_create_context.return_value = MagicMock(context_id="ctx_replace")
        mock_ctx_manager_inst.create_workflow_step.return_value = MagicMock(step_id="step_replace")
        mock_ctx_mgr.return_value = mock_ctx_manager_inst

        # Send creative_assignments with ONLY c2 and c3 — c1 should be REMOVED
        req = UpdateMediaBuyRequest(
            media_buy_id="test_buy_replace",
            packages=[
                {
                    "package_id": "pkg_default",
                    "creative_assignments": [
                        {"creative_id": "c2", "weight": 70},
                        {"creative_id": "c3", "weight": 30},
                    ],
                }
            ],
        )
        response = _update_media_buy_impl(req=req, identity=identity)

    # Verify response is successful
    assert isinstance(response, UpdateMediaBuyResponse)
    assert not hasattr(response, "errors") or not response.errors

    # Verify database: ONLY c2 and c3 remain (c1 was replaced/removed)
    with get_db_session() as session:
        assignment_stmt = select(DBAssignment).where(
            DBAssignment.tenant_id == "test_tenant",
            DBAssignment.media_buy_id == "test_buy_replace",
            DBAssignment.package_id == "pkg_default",
        )
        assignments = session.scalars(assignment_stmt).all()
        assigned_ids = {a.creative_id for a in assignments}

        # BR-RULE-024 INV-2: creative_assignments REPLACES ALL
        assert assigned_ids == {"c2", "c3"}, f"Expected only c2, c3 but got {assigned_ids} — c1 was not removed"

        # Verify weights are correct
        weight_map = {a.creative_id: a.weight for a in assignments}
        assert weight_map["c2"] == 70
        assert weight_map["c3"] == 30
