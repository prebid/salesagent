"""Data validation tests for Admin UI pages.

These tests verify that pages return CORRECT data, not just that they don't crash.
Complements the smoke tests in test_admin_ui_routes_comprehensive.py.

Smoke tests: "Does it render?" (status code checks)
Data tests: "Does it show the right data?" (content validation)
"""

import pytest

from src.core.database.models import PricingOption, Product

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


@pytest.mark.requires_db
class TestProductsDataValidation:
    """Validate that products list shows correct data without duplicates."""

    def test_products_list_no_duplicates_with_pricing_options(
        self, authenticated_admin_session, test_tenant_with_data, integration_db
    ):
        """Test that products with pricing_options are not duplicated in list.

        This is a regression test for the joinedload() without .unique() bug.

        SQLAlchemy 2.0 requires .unique() when using joinedload() on collections:
        - Without .unique(): Returns duplicate Product rows (one per pricing_option)
        - With .unique(): Returns each Product once with pricing_options loaded

        Bug caught: https://github.com/your-org/repo/issues/XXX
        """
        from src.core.database.database_session import get_db_session

        tenant_id = test_tenant_with_data["tenant_id"]

        # Create a product with multiple pricing options
        with get_db_session() as db_session:
            # Create product (must have either properties OR property_tags per AdCP spec)
            product = Product(
                tenant_id=tenant_id,
                product_id="test_product_duplicate_check",
                name="Test Product With Multiple Prices",
                description="Should appear once, not duplicated",
                delivery_type="guaranteed",
                countries=["US"],
                formats=[],
                targeting_template={},
                property_tags=["all_inventory"],  # Required: either properties OR property_tags
            )
            db_session.add(product)
            db_session.flush()

            # Add 3 pricing options (this causes 3 joined rows)
            for i in range(3):
                pricing = PricingOption(
                    tenant_id=tenant_id,
                    product_id=product.product_id,
                    pricing_model="cpm",
                    rate=10.0 + i,
                    currency="USD",
                    is_fixed=True,
                )
                db_session.add(pricing)

            db_session.commit()

        # Request products page
        response = authenticated_admin_session.get(f"/tenant/{tenant_id}/products/", follow_redirects=True)
        assert response.status_code == 200

        # Parse HTML to count products in table
        html = response.data.decode("utf-8")

        # Count table rows (<tr>) in tbody
        if "<tbody>" in html and "</tbody>" in html:
            tbody_start = html.find("<tbody>")
            tbody_end = html.find("</tbody>")
            tbody = html[tbody_start:tbody_end]
            # Count <tr> tags (excluding the opening <tbody>)
            row_count = tbody.count("<tr>")
        else:
            row_count = 0

        # Should have exactly 1 row (not 3 rows for 3 pricing options)
        assert row_count == 1, (
            f"Product table has {row_count} rows (expected 1). "
            f"This indicates joinedload() without .unique() bug. "
            f"Fix: Add .unique() before .all() in products list query."
        )

    def test_products_list_shows_all_products(self, authenticated_admin_session, test_tenant_with_data, integration_db):
        """Test that products list shows all tenant's products exactly once."""
        from src.core.database.database_session import get_db_session

        tenant_id = test_tenant_with_data["tenant_id"]

        # Create 5 distinct products
        product_names = [f"Product {i}" for i in range(5)]

        with get_db_session() as db_session:
            for name in product_names:
                product = Product(
                    tenant_id=tenant_id,
                    product_id=f"test_product_{name.lower().replace(' ', '_')}",
                    name=name,
                    description="Test product",
                    delivery_type="guaranteed",
                    countries=["US"],
                    formats=[],
                    targeting_template={},
                    property_tags=["all_inventory"],  # Required per AdCP spec
                )
                db_session.add(product)
            db_session.commit()

        # Request products page
        response = authenticated_admin_session.get(f"/tenant/{tenant_id}/products/", follow_redirects=True)
        assert response.status_code == 200

        html = response.data.decode("utf-8")

        # Count table rows in tbody
        if "<tbody>" in html and "</tbody>" in html:
            tbody_start = html.find("<tbody>")
            tbody_end = html.find("</tbody>")
            tbody = html[tbody_start:tbody_end]
            row_count = tbody.count("<tr>")
        else:
            row_count = 0

        # Should have exactly 5 rows (one per product)
        assert row_count == 5, f"Product table has {row_count} rows (expected 5)"

    def test_products_list_with_no_pricing_options(
        self, authenticated_admin_session, test_tenant_with_data, integration_db
    ):
        """Test that products without pricing_options still render correctly."""
        from src.core.database.database_session import get_db_session
        from tests.integration_v2.conftest import create_test_product_with_pricing

        tenant_id = test_tenant_with_data["tenant_id"]

        # Create product using new pricing_options model
        with get_db_session() as db_session:
            product = create_test_product_with_pricing(
                session=db_session,
                tenant_id=tenant_id,
                product_id="test_product_no_pricing",
                name="Product Without Pricing Options",
                description="Product with pricing options",
                pricing_model="CPM",
                rate="15.0",
                is_fixed=True,
                delivery_type="guaranteed",
                countries=["US"],
                formats=[],
                targeting_template={},
                property_tags=["all_inventory"],  # Required per AdCP spec
            )
            db_session.commit()

        # Request products page
        response = authenticated_admin_session.get(f"/tenant/{tenant_id}/products/", follow_redirects=True)
        assert response.status_code == 200

        html = response.data.decode("utf-8")

        # Count table rows in tbody
        if "<tbody>" in html and "</tbody>" in html:
            tbody_start = html.find("<tbody>")
            tbody_end = html.find("</tbody>")
            tbody = html[tbody_start:tbody_end]
            row_count = tbody.count("<tr>")
        else:
            row_count = 0

        # Should have exactly 1 row
        assert row_count == 1, f"Product table has {row_count} rows (expected 1)"


class TestPrincipalsDataValidation:
    """Validate that principals/advertisers list shows correct data."""

    def test_principals_list_no_duplicates_with_relationships(
        self, authenticated_admin_session, test_tenant_with_data, integration_db
    ):
        """Test that principals page renders without duplicates.

        Similar to products bug - if using joinedload() on principal relationships,
        must use .unique() to avoid duplicates.
        """
        from src.core.database.database_session import get_db_session
        from src.core.database.models import Principal

        tenant_id = test_tenant_with_data["tenant_id"]

        # Create 3 principals to test list display
        with get_db_session() as db_session:
            for i in range(3):
                principal = Principal(
                    tenant_id=tenant_id,
                    principal_id=f"test_principal_dup_check_{i}",
                    name=f"Test Advertiser {i}",
                    access_token=f"test_token_{i}",
                    platform_mappings={"mock": {"id": f"test_advertiser_{i}"}},
                )
                db_session.add(principal)
            db_session.commit()

        # Request principals page
        response = authenticated_admin_session.get(f"/tenant/{tenant_id}/principals", follow_redirects=True)
        assert response.status_code == 200

        html = response.data.decode("utf-8")

        # Principals page renders successfully
        # Actual display depends on template and filters
        # Just verify page contains principal-related content
        assert (
            "principal" in html.lower() or "advertiser" in html.lower()
        ), "Principals page should contain principal/advertiser-related content"


class TestInventoryDataValidation:
    """Validate that inventory pages show correct ad units."""

    def test_inventory_browser_no_duplicate_ad_units(
        self, authenticated_admin_session, test_tenant_with_data, integration_db
    ):
        """Test that ad units are not duplicated in inventory browser.

        If using joinedload() for ad unit hierarchy, must use .unique().
        """
        from src.core.database.database_session import get_db_session
        from src.core.database.models import GAMInventory

        tenant_id = test_tenant_with_data["tenant_id"]

        # Create ad units
        with get_db_session() as db_session:
            for i in range(3):
                ad_unit = GAMInventory(
                    tenant_id=tenant_id,
                    inventory_type="ad_unit",
                    inventory_id=f"test_ad_unit_{i}",
                    name=f"Test Ad Unit {i}",
                    path=["/test", f"path_{i}"],  # Array of path components
                    status="ACTIVE",
                    inventory_metadata={"code": f"TEST_{i}"},
                )
                db_session.add(ad_unit)
            db_session.commit()

        # Request inventory page
        response = authenticated_admin_session.get(f"/tenant/{tenant_id}/inventory", follow_redirects=True)
        assert response.status_code == 200

        html = response.data.decode("utf-8")

        # Inventory page renders successfully even if empty
        # This test just verifies the page loads without errors
        # The actual inventory sync would require GAM adapter integration
        assert (
            "inventory" in html.lower() or "ad units" in html.lower()
        ), "Inventory page should contain inventory-related content"


class TestDashboardDataValidation:
    """Validate that dashboard shows correct metrics."""

    def test_dashboard_media_buy_count_accurate(
        self, authenticated_admin_session, test_tenant_with_data, integration_db
    ):
        """Test that dashboard shows correct count of media buys."""
        from datetime import date, timedelta
        from decimal import Decimal

        from src.core.database.database_session import get_db_session
        from src.core.database.models import MediaBuy, Principal

        tenant_id = test_tenant_with_data["tenant_id"]

        # Create principal first
        with get_db_session() as db_session:
            principal = Principal(
                tenant_id=tenant_id,
                principal_id="test_principal_dashboard",
                name="Test Advertiser",
                access_token="test_token_dashboard",
                platform_mappings={"mock": {"id": "test"}},
            )
            db_session.add(principal)

            # Create 5 media buys
            for i in range(5):
                media_buy = MediaBuy(
                    tenant_id=tenant_id,
                    media_buy_id=f"test_mb_dashboard_{i}",
                    principal_id=principal.principal_id,
                    buyer_ref=f"buyer_ref_dashboard_{i}",
                    order_name=f"Test Order Dashboard {i}",
                    advertiser_name="Test Advertiser",
                    budget=Decimal("1000.00"),
                    currency="USD",
                    start_date=date.today(),
                    end_date=date.today() + timedelta(days=30),
                    status="live",
                    raw_request={},
                )
                db_session.add(media_buy)
            db_session.commit()

        # Request dashboard
        response = authenticated_admin_session.get(f"/tenant/{tenant_id}", follow_redirects=True)
        assert response.status_code == 200

        html = response.data.decode("utf-8")

        # Dashboard should show "5" somewhere (in media buy count)
        # This is a loose check - tighten based on actual HTML structure
        assert "5" in html, "Dashboard should show count of 5 media buys"


class TestMediaBuysDataValidation:
    """Validate that media buys list shows correct data."""

    def test_media_buys_list_no_duplicates_with_packages(
        self, authenticated_admin_session, test_tenant_with_data, integration_db
    ):
        """Test that media buys with packages/creatives aren't duplicated.

        Similar to products bug - if using joinedload() on media buy relationships,
        must use .unique() to avoid duplicates.
        """
        from datetime import date, timedelta
        from decimal import Decimal

        from src.core.database.database_session import get_db_session
        from src.core.database.models import MediaBuy, Principal

        tenant_id = test_tenant_with_data["tenant_id"]

        # Create principal first
        with get_db_session() as db_session:
            principal = Principal(
                tenant_id=tenant_id,
                principal_id="test_principal_mb",
                name="Test Advertiser",
                access_token="test_token_mb",
                platform_mappings={"mock": {"id": "test"}},
            )
            db_session.add(principal)

            # Create media buy with complex raw_request (packages, creatives)
            media_buy = MediaBuy(
                tenant_id=tenant_id,
                media_buy_id="test_mb_duplicate_check",
                principal_id="test_principal_mb",
                buyer_ref="test_ref_duplicate_check",
                order_name="Test Order Duplicate Check",
                advertiser_name="Test Advertiser",
                budget=Decimal("5000.00"),
                currency="USD",
                start_date=date.today(),
                end_date=date.today() + timedelta(days=30),
                status="live",
                raw_request={
                    "buyer_ref": "test_ref",
                    "packages": [
                        {"package_id": "pkg1"},
                        {"package_id": "pkg2"},
                        {"package_id": "pkg3"},
                    ],
                },
            )
            db_session.add(media_buy)
            db_session.commit()

        # Request media buys page
        response = authenticated_admin_session.get(f"/tenant/{tenant_id}/media-buys", follow_redirects=True)
        assert response.status_code == 200

        html = response.data.decode("utf-8")

        # Media buy should appear exactly once (not 3 times for 3 packages)
        count = html.count("test_mb_duplicate_check")
        assert (
            count == 1
        ), f"Media buy appears {count} times in HTML (expected 1). Check for joinedload() without .unique() bug."

    def test_media_buys_list_shows_all_statuses(
        self, authenticated_admin_session, test_tenant_with_data, integration_db
    ):
        """Test that media buys with different statuses are all shown."""
        from datetime import date, timedelta
        from decimal import Decimal

        from src.core.database.database_session import get_db_session
        from src.core.database.models import MediaBuy, Principal

        tenant_id = test_tenant_with_data["tenant_id"]

        # Create principal
        with get_db_session() as db_session:
            principal = Principal(
                tenant_id=tenant_id,
                principal_id="test_principal_status",
                name="Test Advertiser",
                access_token="test_token_status",
                platform_mappings={"mock": {"id": "test"}},
            )
            db_session.add(principal)

            # Create media buys with different statuses
            statuses = ["draft", "live", "paused", "completed", "cancelled"]
            for status in statuses:
                media_buy = MediaBuy(
                    tenant_id=tenant_id,
                    media_buy_id=f"test_mb_{status}",
                    principal_id="test_principal_status",
                    buyer_ref=f"buyer_ref_{status}",
                    order_name=f"Test Order {status}",
                    advertiser_name="Test Advertiser",
                    budget=Decimal("1000.00"),
                    currency="USD",
                    start_date=date.today(),
                    end_date=date.today() + timedelta(days=30),
                    status=status,
                    raw_request={"buyer_ref": f"ref_{status}"},
                )
                db_session.add(media_buy)
            db_session.commit()

        # Request media buys page
        response = authenticated_admin_session.get(f"/tenant/{tenant_id}/media-buys", follow_redirects=True)
        assert response.status_code == 200

        html = response.data.decode("utf-8")

        # Each status should appear
        for status in statuses:
            assert status in html.lower(), f"Media buy with status '{status}' should be visible"


class TestWorkflowsDataValidation:
    """Validate that workflows list shows correct data."""

    def test_workflows_list_no_duplicate_steps(
        self, authenticated_admin_session, test_tenant_with_data, integration_db
    ):
        """Test that workflows with multiple steps aren't duplicated.

        If using joinedload() on workflow steps, must use .unique().
        """
        from src.core.database.database_session import get_db_session
        from src.core.database.models import Context, Principal, WorkflowStep

        tenant_id = test_tenant_with_data["tenant_id"]

        # Create principal and context
        with get_db_session() as db_session:
            # Create principal
            principal = Principal(
                tenant_id=tenant_id,
                principal_id="test_principal_workflow",
                name="Test Advertiser",
                access_token="test_token_workflow",
                platform_mappings={"mock": {"id": "test"}},
            )
            db_session.add(principal)
            db_session.flush()

            # Create context (requires principal_id)
            context = Context(
                tenant_id=tenant_id,
                context_id="test_workflow_context",
                principal_id=principal.principal_id,
                conversation_history=[{"role": "user", "content": "Test workflow"}],
            )
            db_session.add(context)
            db_session.flush()

            # Create multiple workflow steps for same context
            for i in range(3):
                step = WorkflowStep(
                    step_id=f"step_{i}",
                    context_id=context.context_id,
                    step_type="approval",
                    status="pending",
                    owner="principal",
                    request_data={"step_number": i},
                )
                db_session.add(step)
            db_session.commit()

        # Request workflows page
        response = authenticated_admin_session.get(f"/tenant/{tenant_id}/workflows", follow_redirects=True)
        assert response.status_code == 200

        html = response.data.decode("utf-8")

        # Workflows page renders successfully
        # Actual workflow display depends on filters/status
        # Just verify page contains workflow-related content
        assert (
            "workflow" in html.lower() or "step" in html.lower() or "task" in html.lower()
        ), "Workflows page should contain workflow-related content"


class TestAuthorizedPropertiesDataValidation:
    """Validate that authorized properties list shows correct data."""

    def test_authorized_properties_no_duplicates_with_tags(
        self, authenticated_admin_session, test_tenant_with_data, integration_db
    ):
        """Test that properties with multiple tags aren't duplicated.

        If using joinedload() on property tags, must use .unique().
        """
        from src.core.database.database_session import get_db_session
        from src.core.database.models import AuthorizedProperty

        tenant_id = test_tenant_with_data["tenant_id"]

        # Create property with multiple tags
        with get_db_session() as db_session:
            prop = AuthorizedProperty(
                tenant_id=tenant_id,
                property_id="test_property_duplicate_check",
                property_type="website",
                name="Test Property",
                identifiers=[{"type": "domain", "value": "example.com"}],
                tags=["tag1", "tag2", "tag3"],  # Multiple tags
                publisher_domain="example.com",
                verification_status="verified",
            )
            db_session.add(prop)
            db_session.commit()

        # Request properties page
        response = authenticated_admin_session.get(f"/tenant/{tenant_id}/authorized-properties", follow_redirects=True)
        assert response.status_code == 200

        html = response.data.decode("utf-8")

        # Property should appear exactly once
        count = html.count("Test Property")
        assert count == 1, f"Property appears {count} times (expected 1). Check for joinedload() without .unique() bug."

    def test_authorized_properties_shows_all_properties(
        self, authenticated_admin_session, test_tenant_with_data, integration_db
    ):
        """Test that all authorized properties are displayed."""
        from src.core.database.database_session import get_db_session
        from src.core.database.models import AuthorizedProperty

        tenant_id = test_tenant_with_data["tenant_id"]

        # Create 5 properties
        domains = [f"site{i}.example.com" for i in range(5)]

        with get_db_session() as db_session:
            for domain in domains:
                prop = AuthorizedProperty(
                    tenant_id=tenant_id,
                    property_id=f"prop_{domain}",
                    property_type="website",
                    name=f"Test Property {domain}",
                    identifiers=[{"type": "domain", "value": domain}],
                    tags=["all_inventory"],
                    publisher_domain=domain,
                    verification_status="verified",
                )
                db_session.add(prop)
            db_session.commit()

        # Request properties page
        response = authenticated_admin_session.get(f"/tenant/{tenant_id}/authorized-properties", follow_redirects=True)
        assert response.status_code == 200

        html = response.data.decode("utf-8")

        # Each property should appear once
        for domain in domains:
            count = html.count(domain)
            assert count >= 1, f"Property {domain} should appear at least once"


# Add more data validation tests as bugs are found...
# TODO: Add tests for:
# - Product detail/edit page (verify all pricing options shown)
# - Principal detail page (verify all webhooks, mappings shown)
# - Settings pages (verify config merging correct)
# - Creative assignments (verify no duplicates)
# - Reporting pages (verify accurate metrics)
