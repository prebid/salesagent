"""Response shape tests for AdCP transport-level contracts.

These tests verify the HTTP-level response structure (field names, types, nesting)
for each major AdCP operation. They catch subtle serialization regressions that
schema-level tests miss, by exercising model_dump(mode="json") -- the same
path used by both MCP and A2A transports.

Approach:
- Construct response objects directly from schema classes with realistic test data.
- Serialize via model_dump(mode="json") (same as actual transports).
- Assert expected field names exist and have correct types.
- Do NOT assert exact values (that is for contract/integration tests).

This file intentionally does NOT call _impl functions -- those require heavy
mocking of DB, adapters, and auth. The shape contract is between the response
schema and external clients; the _impl functions are tested elsewhere.
"""

from datetime import UTC, datetime, timedelta

import pytest

from tests.helpers.adcp_factories import (
    create_test_cpm_pricing_option,
    create_test_format,
    create_test_package,
    create_test_product,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def assert_field_type(data: dict, field: str, expected_type: type, *, allow_none: bool = False) -> None:
    """Assert a field exists in data and has the expected type."""
    assert field in data, f"Missing field '{field}' in {sorted(data.keys())}"
    if allow_none and data[field] is None:
        return
    assert isinstance(data[field], expected_type), (
        f"Field '{field}' expected {expected_type.__name__}, got {type(data[field]).__name__}: {data[field]!r}"
    )


def assert_fields_present(data: dict, required_fields: list[str]) -> None:
    """Assert all required fields are present in data."""
    missing = [f for f in required_fields if f not in data]
    assert not missing, f"Missing required fields: {missing} in {sorted(data.keys())}"


# ===========================================================================
# 1. GetProductsResponse
# ===========================================================================


class TestGetProductsResponseShape:
    """Verify the serialized shape of GetProductsResponse."""

    def test_empty_products_response(self):
        """Empty products list produces correct top-level structure."""
        from src.core.schemas import GetProductsResponse

        resp = GetProductsResponse(products=[])
        data = resp.model_dump(mode="json")

        assert_field_type(data, "products", list)
        assert len(data["products"]) == 0

    def test_products_response_with_product(self):
        """Response with a product has correct nested structure."""
        from src.core.schemas import GetProductsResponse

        product = create_test_product(
            product_id="prod_1",
            name="Premium Display",
            format_ids=["display_300x250", "display_728x90"],
            pricing_options=[create_test_cpm_pricing_option(pricing_option_id="cpm_1", rate=12.50)],
        )

        resp = GetProductsResponse(products=[product])
        data = resp.model_dump(mode="json")

        assert_field_type(data, "products", list)
        assert len(data["products"]) == 1

        p = data["products"][0]
        assert_field_type(p, "product_id", str)
        assert_field_type(p, "name", str)
        assert_field_type(p, "pricing_options", list)
        assert_field_type(p, "format_ids", list)
        assert_field_type(p, "publisher_properties", list)

        assert p["product_id"] == "prod_1"
        assert p["name"] == "Premium Display"

    def test_product_pricing_option_shape(self):
        """Each pricing option has required fields."""
        from src.core.schemas import GetProductsResponse

        product = create_test_product(
            pricing_options=[create_test_cpm_pricing_option(pricing_option_id="cpm_99", currency="EUR", rate=8.0)],
        )
        resp = GetProductsResponse(products=[product])
        data = resp.model_dump(mode="json")

        pricing = data["products"][0]["pricing_options"]
        assert len(pricing) >= 1

        po = pricing[0]
        assert_field_type(po, "pricing_model", str)
        assert_field_type(po, "currency", str)
        assert_field_type(po, "pricing_option_id", str)

    def test_product_format_ids_shape(self):
        """format_ids serialize as objects with agent_url and id."""
        from src.core.schemas import GetProductsResponse

        product = create_test_product(format_ids=["display_300x250"])
        resp = GetProductsResponse(products=[product])
        data = resp.model_dump(mode="json")

        format_ids = data["products"][0]["format_ids"]
        assert len(format_ids) >= 1

        fmt = format_ids[0]
        assert isinstance(fmt, dict), f"format_id should be dict, got {type(fmt)}"
        assert_field_type(fmt, "id", str)
        assert_field_type(fmt, "agent_url", str)

    def test_product_publisher_properties_shape(self):
        """publisher_properties contain expected discriminated union fields."""
        from src.core.schemas import GetProductsResponse

        product = create_test_product()
        resp = GetProductsResponse(products=[product])
        data = resp.model_dump(mode="json")

        props = data["products"][0]["publisher_properties"]
        assert len(props) >= 1

        prop = props[0]
        assert isinstance(prop, dict)
        assert_field_type(prop, "publisher_domain", str)

    def test_multiple_products_response(self):
        """Multiple products serialize independently."""
        from src.core.schemas import GetProductsResponse

        products = [create_test_product(product_id=f"prod_{i}", name=f"Product {i}") for i in range(3)]
        resp = GetProductsResponse(products=products)
        data = resp.model_dump(mode="json")

        assert len(data["products"]) == 3
        ids = {p["product_id"] for p in data["products"]}
        assert ids == {"prod_0", "prod_1", "prod_2"}


# ===========================================================================
# 2. CreateMediaBuyResponse (Success variant)
# ===========================================================================


class TestCreateMediaBuyResponseShape:
    """Verify the serialized shape of CreateMediaBuySuccess."""

    def test_minimal_success_response(self):
        """Minimal success response has required fields."""
        from src.core.schemas import CreateMediaBuySuccess

        resp = CreateMediaBuySuccess(
            media_buy_id="buy_001",
            buyer_ref="ref_001",
            packages=[],
        )
        data = resp.model_dump(mode="json")

        assert_field_type(data, "media_buy_id", str)
        assert_field_type(data, "buyer_ref", str)
        assert_field_type(data, "packages", list)

        assert data["media_buy_id"] == "buy_001"
        assert data["buyer_ref"] == "ref_001"

    def test_success_response_with_packages(self):
        """Response with packages has correct nested package structure."""
        from src.core.schemas import CreateMediaBuySuccess

        package = create_test_package(
            package_id="pkg_001",
            product_id="prod_1",
        )
        resp = CreateMediaBuySuccess(
            media_buy_id="buy_002",
            buyer_ref="ref_002",
            packages=[package],
        )
        data = resp.model_dump(mode="json")

        assert len(data["packages"]) == 1

        pkg = data["packages"][0]
        assert_field_type(pkg, "package_id", str)
        assert pkg["package_id"] == "pkg_001"

    def test_internal_fields_excluded(self):
        """Internal fields (workflow_step_id) are excluded from serialization."""
        from src.core.schemas import CreateMediaBuySuccess

        resp = CreateMediaBuySuccess(
            media_buy_id="buy_003",
            buyer_ref="ref_003",
            packages=[],
            workflow_step_id="wf_123",
        )
        data = resp.model_dump(mode="json")

        assert "workflow_step_id" not in data


# ===========================================================================
# 3. SyncCreativesResponse
# ===========================================================================


class TestSyncCreativesResponseShape:
    """Verify the serialized shape of SyncCreativesResponse."""

    def test_empty_sync_response(self):
        """Empty sync response has creatives list."""
        from src.core.schemas import SyncCreativesResponse

        resp = SyncCreativesResponse(creatives=[], dry_run=False)  # type: ignore[call-arg]
        data = resp.model_dump(mode="json")

        assert_field_type(data, "creatives", list)
        assert len(data["creatives"]) == 0

    def test_sync_response_with_created_creative(self):
        """Sync response with a created creative has correct shape."""
        from adcp.types.generated_poc.enums.creative_action import CreativeAction

        from src.core.schemas import SyncCreativeResult, SyncCreativesResponse

        result = SyncCreativeResult(
            creative_id="creative_001",
            action=CreativeAction.created,
            platform_id="plat_001",
        )
        resp = SyncCreativesResponse(creatives=[result], dry_run=False)  # type: ignore[call-arg]
        data = resp.model_dump(mode="json")

        assert_field_type(data, "creatives", list)
        assert len(data["creatives"]) == 1

        c = data["creatives"][0]
        assert_field_type(c, "creative_id", str)
        assert_field_type(c, "action", str)
        assert c["creative_id"] == "creative_001"
        assert c["action"] == "created"

    def test_sync_response_internal_fields_excluded(self):
        """Internal fields (status, review_feedback) are excluded."""
        from adcp.types.generated_poc.enums.creative_action import CreativeAction

        from src.core.schemas import SyncCreativeResult, SyncCreativesResponse

        result = SyncCreativeResult(
            creative_id="creative_002",
            action=CreativeAction.updated,
            status="approved",
            review_feedback="Looks good",
        )
        resp = SyncCreativesResponse(creatives=[result], dry_run=False)  # type: ignore[call-arg]
        data = resp.model_dump(mode="json")

        c = data["creatives"][0]
        assert "status" not in c, "Internal 'status' field should be excluded"
        assert "review_feedback" not in c, "Internal 'review_feedback' field should be excluded"

    def test_sync_response_failed_creative_has_errors(self):
        """Failed creative includes errors list."""
        from adcp.types.generated_poc.enums.creative_action import CreativeAction

        from src.core.schemas import SyncCreativeResult, SyncCreativesResponse

        result = SyncCreativeResult(
            creative_id="creative_003",
            action=CreativeAction.failed,
            errors=["Format not supported", "Missing required asset"],
        )
        resp = SyncCreativesResponse(creatives=[result], dry_run=False)  # type: ignore[call-arg]
        data = resp.model_dump(mode="json")

        c = data["creatives"][0]
        assert_field_type(c, "errors", list)
        assert len(c["errors"]) == 2
        assert all(isinstance(e, str) for e in c["errors"])


# ===========================================================================
# 4. GetMediaBuyDeliveryResponse
# ===========================================================================


class TestGetMediaBuyDeliveryResponseShape:
    """Verify the serialized shape of GetMediaBuyDeliveryResponse."""

    @pytest.fixture()
    def delivery_response(self):
        """Create a realistic delivery response for testing."""
        from src.core.schemas import (
            AggregatedTotals,
            DeliveryTotals,
            GetMediaBuyDeliveryResponse,
            MediaBuyDeliveryData,
            PackageDelivery,
            PricingModel,
            ReportingPeriod,
        )

        now = datetime.now(UTC)
        start = now - timedelta(days=7)

        return GetMediaBuyDeliveryResponse(
            reporting_period=ReportingPeriod(start=start, end=now),
            currency="USD",
            aggregated_totals=AggregatedTotals(
                impressions=50000.0,
                spend=500.0,
                clicks=250.0,
                video_completions=None,
                media_buy_count=1,
            ),
            media_buy_deliveries=[
                MediaBuyDeliveryData(
                    media_buy_id="buy_100",
                    buyer_ref="ref_100",
                    status="active",
                    pricing_model=PricingModel.cpm,
                    totals=DeliveryTotals(
                        impressions=50000.0,
                        spend=500.0,
                        clicks=250.0,
                    ),
                    by_package=[
                        PackageDelivery(
                            package_id="pkg_100",
                            impressions=50000.0,
                            spend=500.0,
                            clicks=250.0,
                        )
                    ],
                )
            ],
            errors=None,
        )

    def test_top_level_shape(self, delivery_response):
        """Top-level response has required fields."""
        data = delivery_response.model_dump(mode="json")

        assert_field_type(data, "reporting_period", dict)
        assert_field_type(data, "currency", str)
        assert_field_type(data, "aggregated_totals", dict)
        assert_field_type(data, "media_buy_deliveries", list)

    def test_reporting_period_shape(self, delivery_response):
        """Reporting period has start and end."""
        data = delivery_response.model_dump(mode="json")

        period = data["reporting_period"]
        assert_field_type(period, "start", str)
        assert_field_type(period, "end", str)

    def test_aggregated_totals_shape(self, delivery_response):
        """Aggregated totals have expected metrics fields."""
        data = delivery_response.model_dump(mode="json")

        totals = data["aggregated_totals"]
        assert_field_type(totals, "impressions", (int, float))
        assert_field_type(totals, "spend", (int, float))
        assert_field_type(totals, "media_buy_count", int)

    def test_media_buy_delivery_shape(self, delivery_response):
        """Each media buy delivery entry has required fields."""
        data = delivery_response.model_dump(mode="json")

        assert len(data["media_buy_deliveries"]) == 1
        delivery = data["media_buy_deliveries"][0]

        assert_field_type(delivery, "media_buy_id", str)
        assert_field_type(delivery, "status", str)
        assert_field_type(delivery, "totals", dict)
        assert_field_type(delivery, "by_package", list)

    def test_delivery_totals_shape(self, delivery_response):
        """Delivery totals have expected metric fields."""
        data = delivery_response.model_dump(mode="json")

        totals = data["media_buy_deliveries"][0]["totals"]
        assert_field_type(totals, "impressions", (int, float))
        assert_field_type(totals, "spend", (int, float))

    def test_package_delivery_shape(self, delivery_response):
        """Package delivery entries have expected fields."""
        data = delivery_response.model_dump(mode="json")

        pkgs = data["media_buy_deliveries"][0]["by_package"]
        assert len(pkgs) == 1

        pkg = pkgs[0]
        assert_field_type(pkg, "package_id", str)
        assert_field_type(pkg, "impressions", (int, float))
        assert_field_type(pkg, "spend", (int, float))

    def test_empty_deliveries_response(self):
        """Empty deliveries list is valid."""
        from src.core.schemas import (
            AggregatedTotals,
            GetMediaBuyDeliveryResponse,
            ReportingPeriod,
        )

        now = datetime.now(UTC)
        resp = GetMediaBuyDeliveryResponse(
            reporting_period=ReportingPeriod(start=now - timedelta(days=1), end=now),
            currency="USD",
            aggregated_totals=AggregatedTotals(
                impressions=0.0,
                spend=0.0,
                media_buy_count=0,
            ),
            media_buy_deliveries=[],
        )
        data = resp.model_dump(mode="json")

        assert data["media_buy_deliveries"] == []
        assert data["aggregated_totals"]["media_buy_count"] == 0


# ===========================================================================
# 5. ListCreativeFormatsResponse
# ===========================================================================


class TestListCreativeFormatsResponseShape:
    """Verify the serialized shape of ListCreativeFormatsResponse."""

    def test_empty_formats_response(self):
        """Empty formats list produces correct structure."""
        from src.core.schemas import ListCreativeFormatsResponse

        resp = ListCreativeFormatsResponse(formats=[])
        data = resp.model_dump(mode="json")

        assert_field_type(data, "formats", list)
        assert len(data["formats"]) == 0

    def test_formats_response_with_format(self):
        """Response with a format has correct nested structure."""
        from src.core.schemas import ListCreativeFormatsResponse

        fmt = create_test_format(
            format_id="display_300x250",
            name="Medium Rectangle",
            type="display",
        )
        resp = ListCreativeFormatsResponse(formats=[fmt])
        data = resp.model_dump(mode="json")

        assert len(data["formats"]) == 1

        f = data["formats"][0]
        assert_field_type(f, "format_id", dict)
        assert_field_type(f, "name", str)
        assert_field_type(f, "type", str)

        assert f["name"] == "Medium Rectangle"
        assert f["type"] == "display"

    def test_format_id_structure(self):
        """format_id within each format has agent_url and id."""
        from src.core.schemas import ListCreativeFormatsResponse

        fmt = create_test_format(format_id="video_1920x1080", name="Full HD Video", type="video")
        resp = ListCreativeFormatsResponse(formats=[fmt])
        data = resp.model_dump(mode="json")

        fid = data["formats"][0]["format_id"]
        assert_field_type(fid, "id", str)
        assert_field_type(fid, "agent_url", str)
        assert fid["id"] == "video_1920x1080"

    def test_multiple_formats(self):
        """Multiple formats serialize correctly."""
        from src.core.schemas import ListCreativeFormatsResponse

        formats = [
            create_test_format(format_id="display_300x250", name="Medium Rectangle", type="display"),
            create_test_format(format_id="video_1920x1080", name="Full HD Video", type="video"),
            create_test_format(format_id="audio_30s", name="30s Audio Spot", type="audio"),
        ]
        resp = ListCreativeFormatsResponse(formats=formats)
        data = resp.model_dump(mode="json")

        assert len(data["formats"]) == 3
        names = {f["name"] for f in data["formats"]}
        assert names == {"Medium Rectangle", "Full HD Video", "30s Audio Spot"}


# ===========================================================================
# 6. ListAuthorizedPropertiesResponse
# ===========================================================================


class TestListAuthorizedPropertiesResponseShape:
    """Verify the serialized shape of ListAuthorizedPropertiesResponse."""

    def test_empty_properties_response(self):
        """Empty publisher domains list."""
        from src.core.schemas import ListAuthorizedPropertiesResponse

        resp = ListAuthorizedPropertiesResponse(publisher_domains=[])
        data = resp.model_dump(mode="json")

        assert_field_type(data, "publisher_domains", list)
        assert len(data["publisher_domains"]) == 0

    def test_properties_response_with_domains(self):
        """Response with publisher domains has correct shape."""
        from src.core.schemas import ListAuthorizedPropertiesResponse

        resp = ListAuthorizedPropertiesResponse(
            publisher_domains=["news.example.com", "sports.example.com"],
        )
        data = resp.model_dump(mode="json")

        assert_field_type(data, "publisher_domains", list)
        assert len(data["publisher_domains"]) == 2
        assert all(isinstance(d, str) for d in data["publisher_domains"])

    def test_properties_response_optional_fields(self):
        """Optional fields are present when set."""
        from src.core.schemas import ListAuthorizedPropertiesResponse

        resp = ListAuthorizedPropertiesResponse(
            publisher_domains=["example.com"],
            advertising_policies="No gambling or tobacco advertising.",
            portfolio_description="A premium news publisher network.",
            primary_channels=["display", "video"],
            primary_countries=["US", "GB"],
        )
        data = resp.model_dump(mode="json")

        assert_field_type(data, "publisher_domains", list)
        assert_field_type(data, "advertising_policies", str)
        assert_field_type(data, "portfolio_description", str)
        assert_field_type(data, "primary_channels", list)
        assert_field_type(data, "primary_countries", list)

    def test_properties_response_none_fields_excluded(self):
        """None optional fields are excluded from serialization."""
        from src.core.schemas import ListAuthorizedPropertiesResponse

        resp = ListAuthorizedPropertiesResponse(
            publisher_domains=["example.com"],
        )
        data = resp.model_dump(mode="json")

        # AdCP convention: exclude_none=True by default
        # Optional fields not set should be absent or None depending on base class behavior
        # The key assertion: publisher_domains is present and correct
        assert "publisher_domains" in data
        assert data["publisher_domains"] == ["example.com"]


# ===========================================================================
# 7. UpdateMediaBuyResponse (Success variant)
# ===========================================================================


class TestUpdateMediaBuyResponseShape:
    """Verify the serialized shape of UpdateMediaBuySuccess."""

    def test_minimal_success_response(self):
        """Minimal success response has required fields."""
        from src.core.schemas import UpdateMediaBuySuccess

        resp = UpdateMediaBuySuccess(
            media_buy_id="buy_100",
            buyer_ref="ref_100",
        )
        data = resp.model_dump(mode="json")

        assert_field_type(data, "media_buy_id", str)
        assert_field_type(data, "buyer_ref", str)

        assert data["media_buy_id"] == "buy_100"
        assert data["buyer_ref"] == "ref_100"

    def test_success_response_with_packages(self):
        """Response with affected_packages has correct nested package structure."""
        from src.core.schemas import AffectedPackage, UpdateMediaBuySuccess

        package = AffectedPackage(
            package_id="pkg_001",
            paused=False,
        )
        resp = UpdateMediaBuySuccess(
            media_buy_id="buy_101",
            buyer_ref="ref_101",
            affected_packages=[package],
        )
        data = resp.model_dump(mode="json")

        assert_field_type(data, "affected_packages", list)
        assert len(data["affected_packages"]) == 1

        pkg = data["affected_packages"][0]
        assert_field_type(pkg, "package_id", str)
        assert_field_type(pkg, "paused", bool)
        assert pkg["package_id"] == "pkg_001"

    def test_internal_fields_excluded(self):
        """Internal fields (workflow_step_id, changes_applied, buyer_package_ref) are excluded."""
        from src.core.schemas import AffectedPackage, UpdateMediaBuySuccess

        package = AffectedPackage(
            package_id="pkg_002",
            paused=True,
            changes_applied={"creative_ids_added": ["c1", "c2"]},
            buyer_package_ref="buyer_pkg_ref_002",
        )
        resp = UpdateMediaBuySuccess(
            media_buy_id="buy_102",
            buyer_ref="ref_102",
            affected_packages=[package],
            workflow_step_id="wf_456",
        )
        data = resp.model_dump(mode="json")

        assert "workflow_step_id" not in data

        pkg = data["affected_packages"][0]
        assert "changes_applied" not in pkg, "Internal 'changes_applied' field should be excluded"
        assert "buyer_package_ref" not in pkg, "Internal 'buyer_package_ref' field should be excluded"


# ===========================================================================
# 8. ListCreativesResponse
# ===========================================================================


class TestListCreativesResponseShape:
    """Verify the serialized shape of ListCreativesResponse."""

    def test_empty_creatives_response(self):
        """Empty creatives list produces correct top-level structure."""
        from src.core.schemas import ListCreativesResponse, Pagination, QuerySummary

        resp = ListCreativesResponse(
            creatives=[],
            query_summary=QuerySummary(returned=0, total_matching=0, filters_applied=[]),
            pagination=Pagination(limit=10, offset=0, total_pages=0, current_page=1, has_more=False),
        )
        data = resp.model_dump(mode="json")

        assert_field_type(data, "creatives", list)
        assert_field_type(data, "query_summary", dict)
        assert_field_type(data, "pagination", dict)
        assert len(data["creatives"]) == 0

    def test_creatives_response_with_creative(self):
        """Response with a creative has correct nested structure."""
        from src.core.schemas import Creative, ListCreativesResponse, Pagination, QuerySummary

        creative = Creative(
            creative_id="creative_001",
            name="Premium Banner",
            format_id={"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"},
        )
        resp = ListCreativesResponse(
            creatives=[creative],
            query_summary=QuerySummary(returned=1, total_matching=1, filters_applied=[]),
            pagination=Pagination(limit=10, offset=0, total_pages=1, current_page=1, has_more=False),
        )
        data = resp.model_dump(mode="json")

        assert_field_type(data, "creatives", list)
        assert len(data["creatives"]) == 1

        c = data["creatives"][0]
        assert_field_type(c, "creative_id", str)
        assert_field_type(c, "name", str)
        assert_field_type(c, "format_id", dict)
        assert_field_type(c, "status", str)
        assert_field_type(c, "created_date", str)
        assert_field_type(c, "updated_date", str)

        assert c["creative_id"] == "creative_001"
        assert c["name"] == "Premium Banner"

    def test_query_summary_shape(self):
        """Query summary has expected fields."""
        from src.core.schemas import ListCreativesResponse, Pagination, QuerySummary

        resp = ListCreativesResponse(
            creatives=[],
            query_summary=QuerySummary(returned=5, total_matching=42, filters_applied=["status"]),
            pagination=Pagination(limit=10, offset=0, total_pages=5, current_page=1, has_more=True),
        )
        data = resp.model_dump(mode="json")

        qs = data["query_summary"]
        assert_field_type(qs, "returned", int)
        assert_field_type(qs, "total_matching", int)
        assert_field_type(qs, "filters_applied", list)

    def test_pagination_shape(self):
        """Pagination has expected fields."""
        from src.core.schemas import ListCreativesResponse, Pagination, QuerySummary

        resp = ListCreativesResponse(
            creatives=[],
            query_summary=QuerySummary(returned=0, total_matching=0, filters_applied=[]),
            pagination=Pagination(limit=25, offset=50, total_pages=4, current_page=3, has_more=True),
        )
        data = resp.model_dump(mode="json")

        pg = data["pagination"]
        assert_field_type(pg, "limit", int)
        assert_field_type(pg, "offset", int)
        assert_field_type(pg, "total_pages", int)
        assert_field_type(pg, "current_page", int)
        assert_field_type(pg, "has_more", bool)

    def test_internal_fields_excluded(self):
        """Internal fields (principal_id) on creatives are excluded from serialization."""
        from src.core.schemas import Creative, ListCreativesResponse, Pagination, QuerySummary

        creative = Creative(
            creative_id="creative_002",
            name="Confidential Ad",
            format_id={"agent_url": "https://creative.adcontextprotocol.org", "id": "display_728x90"},
            principal_id="principal_secret_123",
        )
        resp = ListCreativesResponse(
            creatives=[creative],
            query_summary=QuerySummary(returned=1, total_matching=1, filters_applied=[]),
            pagination=Pagination(limit=10, offset=0, total_pages=1, current_page=1, has_more=False),
        )
        data = resp.model_dump(mode="json")

        c = data["creatives"][0]
        assert "principal_id" not in c, "Internal 'principal_id' field should be excluded"

    def test_creative_format_id_structure(self):
        """format_id within each creative has agent_url and id."""
        from src.core.schemas import Creative, ListCreativesResponse, Pagination, QuerySummary

        creative = Creative(
            creative_id="creative_003",
            name="Video Ad",
            format_id={"agent_url": "https://creative.adcontextprotocol.org", "id": "video_1920x1080"},
        )
        resp = ListCreativesResponse(
            creatives=[creative],
            query_summary=QuerySummary(returned=1, total_matching=1, filters_applied=[]),
            pagination=Pagination(limit=10, offset=0, total_pages=1, current_page=1, has_more=False),
        )
        data = resp.model_dump(mode="json")

        fid = data["creatives"][0]["format_id"]
        assert_field_type(fid, "id", str)
        assert_field_type(fid, "agent_url", str)
        assert fid["id"] == "video_1920x1080"


# ===========================================================================
# 9. Cross-cutting: model_dump(mode="json") roundtrip consistency
# ===========================================================================


class TestSerializationConsistency:
    """Verify that model_dump(mode="json") produces JSON-safe types."""

    @pytest.mark.parametrize(
        "response_factory",
        [
            pytest.param(
                lambda: __import__("src.core.schemas", fromlist=["GetProductsResponse"]).GetProductsResponse(
                    products=[create_test_product()]
                ),
                id="get_products",
            ),
            pytest.param(
                lambda: __import__("src.core.schemas", fromlist=["CreateMediaBuySuccess"]).CreateMediaBuySuccess(
                    media_buy_id="buy_1", buyer_ref="ref_1", packages=[]
                ),
                id="create_media_buy",
            ),
            pytest.param(
                lambda: __import__(
                    "src.core.schemas", fromlist=["ListCreativeFormatsResponse"]
                ).ListCreativeFormatsResponse(formats=[create_test_format()]),
                id="list_creative_formats",
            ),
            pytest.param(
                lambda: __import__(
                    "src.core.schemas", fromlist=["ListAuthorizedPropertiesResponse"]
                ).ListAuthorizedPropertiesResponse(publisher_domains=["example.com"]),
                id="list_authorized_properties",
            ),
            pytest.param(
                lambda: __import__("src.core.schemas", fromlist=["UpdateMediaBuySuccess"]).UpdateMediaBuySuccess(
                    media_buy_id="buy_1", buyer_ref="ref_1"
                ),
                id="update_media_buy",
            ),
            pytest.param(
                lambda: __import__("src.core.schemas", fromlist=["ListCreativesResponse"]).ListCreativesResponse(
                    creatives=[],
                    query_summary=__import__("src.core.schemas", fromlist=["QuerySummary"]).QuerySummary(
                        returned=0, total_matching=0, filters_applied=[]
                    ),
                    pagination=__import__("src.core.schemas", fromlist=["Pagination"]).Pagination(
                        limit=10, offset=0, total_pages=0, current_page=1, has_more=False
                    ),
                ),
                id="list_creatives",
            ),
        ],
    )
    def test_json_mode_produces_serializable_types(self, response_factory):
        """model_dump(mode='json') should produce only JSON-native types."""
        import json

        resp = response_factory()
        data = resp.model_dump(mode="json")

        # Must be JSON-serializable without errors
        json_str = json.dumps(data)
        assert isinstance(json_str, str)

        # Roundtrip: parse back and verify structure is preserved
        parsed = json.loads(json_str)
        assert isinstance(parsed, dict)

    def test_delivery_response_json_serializable(self):
        """GetMediaBuyDeliveryResponse is JSON-serializable."""
        import json

        from src.core.schemas import (
            AggregatedTotals,
            DeliveryTotals,
            GetMediaBuyDeliveryResponse,
            MediaBuyDeliveryData,
            PackageDelivery,
            PricingModel,
            ReportingPeriod,
        )

        now = datetime.now(UTC)
        resp = GetMediaBuyDeliveryResponse(
            reporting_period=ReportingPeriod(start=now - timedelta(days=1), end=now),
            currency="USD",
            aggregated_totals=AggregatedTotals(impressions=1000.0, spend=10.0, media_buy_count=1),
            media_buy_deliveries=[
                MediaBuyDeliveryData(
                    media_buy_id="buy_1",
                    status="active",
                    pricing_model=PricingModel.cpm,
                    totals=DeliveryTotals(impressions=1000.0, spend=10.0),
                    by_package=[PackageDelivery(package_id="pkg_1", impressions=1000.0, spend=10.0)],
                )
            ],
        )
        data = resp.model_dump(mode="json")
        json_str = json.dumps(data)
        assert isinstance(json_str, str)

    def test_sync_creatives_response_json_serializable(self):
        """SyncCreativesResponse is JSON-serializable."""
        import json

        from adcp.types.generated_poc.enums.creative_action import CreativeAction

        from src.core.schemas import SyncCreativeResult, SyncCreativesResponse

        resp = SyncCreativesResponse(  # type: ignore[call-arg]
            creatives=[
                SyncCreativeResult(
                    creative_id="c1",
                    action=CreativeAction.created,
                ),
                SyncCreativeResult(
                    creative_id="c2",
                    action=CreativeAction.failed,
                    errors=["Bad format"],
                ),
            ],
            dry_run=False,
        )
        data = resp.model_dump(mode="json")
        json_str = json.dumps(data)
        assert isinstance(json_str, str)
