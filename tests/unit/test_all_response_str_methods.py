"""Test that all MCP response classes have human-readable __str__() methods."""

from datetime import UTC, datetime

from src.core.schema_adapters import (
    ActivateSignalResponse,
    GetProductsResponse,
    ListCreativeFormatsResponse,
    ListCreativesResponse,
)
from src.core.schemas import (
    CreateHumanTaskResponse,
    CreateMediaBuyResponse,
    Creative,
    Format,
    Pagination,
    Product,
    QuerySummary,
    SimulationControlResponse,
    SyncCreativesResponse,
    UpdateMediaBuyResponse,
    UpdatePerformanceIndexResponse,
)


class TestResponseStrMethods:
    """Test __str__() methods return human-readable content for MCP."""

    def test_get_products_response_with_pricing(self):
        """GetProductsResponse with pricing returns standard message."""
        product = Product(
            product_id="test",
            name="Test",
            description="Test",
            formats=["banner"],
            delivery_type="guaranteed",
            is_fixed_price=True,
            is_custom=False,
            currency="USD",
            property_tags=["all_inventory"],  # Required per AdCP spec
            cpm=10.0,  # Has pricing
            min_spend=100.0,
        )
        resp = GetProductsResponse(products=[product])
        assert str(resp) == "Found 1 product that matches your requirements."

    def test_get_products_response_with_multiple_products(self):
        """GetProductsResponse with multiple products generates count-based message."""
        products = [
            Product(
                product_id=f"p{i}",
                name=f"Product {i}",
                description="Test",
                formats=["banner"],
                property_tags=["all_inventory"],  # Required per AdCP spec
                delivery_type="guaranteed",
                is_fixed_price=True,
                is_custom=False,
                currency="USD",
                cpm=10.0,  # Has pricing
                min_spend=100.0,
            )
            for i in range(3)
        ]
        resp = GetProductsResponse(products=products)
        assert str(resp) == "Found 3 products that match your requirements."

    def test_get_products_response_anonymous_user(self):
        """GetProductsResponse without pricing (anonymous user) adds auth message."""
        products = [
            Product(
                product_id=f"p{i}",
                name=f"Product {i}",
                description="Test",
                formats=["banner"],
                property_tags=["all_inventory"],
                delivery_type="guaranteed",
                is_fixed_price=True,
                is_custom=False,
                currency="USD",
                # No cpm or min_spend - anonymous user
            )
            for i in range(2)
        ]
        resp = GetProductsResponse(products=products)
        assert (
            str(resp)
            == "Found 2 products that match your requirements. Please connect through an authorized buying agent for pricing data."
        )

    def test_list_creative_formats_response_single_format(self):
        """ListCreativeFormatsResponse with single format generates appropriate message."""
        fmt = Format(format_id="banner_300x250", name="Banner", type="display")
        resp = ListCreativeFormatsResponse(formats=[fmt])
        assert str(resp) == "Found 1 creative format."

    def test_list_creative_formats_response_multiple_formats(self):
        """ListCreativeFormatsResponse with multiple formats generates count."""
        formats = [Format(format_id=f"fmt{i}", name=f"Format {i}", type="display") for i in range(5)]
        resp = ListCreativeFormatsResponse(formats=formats)
        assert str(resp) == "Found 5 creative formats."

    def test_list_creative_formats_response_empty(self):
        """ListCreativeFormatsResponse with no formats generates appropriate message."""
        resp = ListCreativeFormatsResponse(formats=[])
        assert str(resp) == "No creative formats are currently supported."

    def test_sync_creatives_response(self):
        """SyncCreativesResponse returns the message field."""
        resp = SyncCreativesResponse(message="Successfully synced 3 creatives", status="completed")
        assert str(resp) == "Successfully synced 3 creatives"

    def test_list_creatives_response(self):
        """ListCreativesResponse returns the message field."""
        creative = Creative(
            creative_id="cr1",
            name="Test Creative",
            format_id="display_300x250",
            content_uri="https://example.com/creative.jpg",
            principal_id="prin_123",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        resp = ListCreativesResponse(
            message="Found 1 creative",
            query_summary=QuerySummary(total_matching=1, returned=1, has_more=False),
            pagination=Pagination(limit=10, offset=0, has_more=False),
            creatives=[creative],
        )
        assert str(resp) == "Found 1 creative"

    def test_activate_signal_response_deployed(self):
        """ActivateSignalResponse with deployed status shows platform ID."""
        resp = ActivateSignalResponse(
            task_id="task_123",
            status="deployed",
            decisioning_platform_segment_id="seg_456",
        )
        assert str(resp) == "Signal activated successfully (platform ID: seg_456)."

    def test_activate_signal_response_processing(self):
        """ActivateSignalResponse with processing status shows ETA."""
        resp = ActivateSignalResponse(
            task_id="task_123",
            status="processing",
            estimated_activation_duration_minutes=5.0,
        )
        assert str(resp) == "Signal activation in progress (ETA: 5.0 min)."

    def test_activate_signal_response_pending(self):
        """ActivateSignalResponse with pending status shows task ID."""
        resp = ActivateSignalResponse(task_id="task_123", status="pending")
        assert str(resp) == "Signal activation pending (task ID: task_123)."

    def test_activate_signal_response_failed(self):
        """ActivateSignalResponse with failed status shows task ID."""
        resp = ActivateSignalResponse(task_id="task_123", status="failed")
        assert str(resp) == "Signal activation failed (task ID: task_123)."

    def test_simulation_control_response_with_message(self):
        """SimulationControlResponse with message returns the message."""
        resp = SimulationControlResponse(status="ok", message="Simulation advanced to 2025-01-15")
        assert str(resp) == "Simulation advanced to 2025-01-15"

    def test_simulation_control_response_without_message(self):
        """SimulationControlResponse without message shows status."""
        resp = SimulationControlResponse(status="ok")
        assert str(resp) == "Simulation control: ok"

    def test_create_media_buy_response_completed(self):
        """CreateMediaBuyResponse shows status-specific message."""
        resp = CreateMediaBuyResponse(status="completed", buyer_ref="ref_123", media_buy_id="mb_456", packages=[])
        assert str(resp) == "Media buy mb_456 created successfully."

    def test_create_media_buy_response_working(self):
        """CreateMediaBuyResponse working status."""
        resp = CreateMediaBuyResponse(status="working", buyer_ref="ref_123", packages=[])
        assert str(resp) == "Media buy ref_123 is being created..."

    def test_update_media_buy_response_completed(self):
        """UpdateMediaBuyResponse shows status-specific message."""
        resp = UpdateMediaBuyResponse(
            status="completed", media_buy_id="mb_123", buyer_ref="ref_456", affected_packages=[]
        )
        assert str(resp) == "Media buy mb_123 updated successfully."

    # Note: GetMediaBuyDeliveryResponse, CreateCreativeResponse, GetSignalsResponse
    # have complex nested models. Their __str__() methods are implemented and work,
    # but creating test instances requires many nested fields. Tested via integration tests.

    def test_update_performance_index_response(self):
        """UpdatePerformanceIndexResponse returns detail field."""
        resp = UpdatePerformanceIndexResponse(status="success", detail="Performance index updated for 5 products")
        assert str(resp) == "Performance index updated for 5 products"

    def test_create_human_task_response(self):
        """CreateHumanTaskResponse shows task ID and status."""
        resp = CreateHumanTaskResponse(task_id="task_123", status="pending")
        assert str(resp) == "Task task_123 created with status: pending"

    def test_all_responses_avoid_json_in_content(self):
        """Verify no response __str__ contains JSON-like content."""
        # Test a few responses to ensure they don't leak JSON
        responses = [
            GetProductsResponse(products=[]),
            ListCreativeFormatsResponse(formats=[]),
            SyncCreativesResponse(message="Test", status="completed"),
            CreateMediaBuyResponse(status="completed", buyer_ref="ref", packages=[]),
        ]

        for resp in responses:
            content = str(resp)
            # Should not contain obvious JSON markers
            assert "{" not in content or "}" not in content
            assert "adcp_version=" not in content
            assert "product_id=" not in content
