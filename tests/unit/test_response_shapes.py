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


#: The confirmation instant every create response built here carries, and the revision
#: beside it. Neither field has a model default -- both are columns the repository owns --
#: so every construction states where its value came from. A literal rather than
#: ``now()``: these cases assert on response SHAPE, so one deterministic value keeps them
#: from disagreeing, and a test does not speak for the repository.
_CONFIRMED_AT = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)
_REVISION = 1


# ===========================================================================
# 1. GetProductsResponse
# ===========================================================================


# GetProductsResponse serializes Product via a RootModel-union (Product1/Product2)
# nested-serializer override; the cosmetic "Expected Product2" serializer warning is
# expected here only. Scoped to this class (and the get_products serialization-
# consistency case below) so a real serializer regression in any other model still
# surfaces suite-wide. See PR #1388 review F3.
@pytest.mark.filterwarnings("ignore:Pydantic serializer warnings:UserWarning:pydantic.main")
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

    # REMOVED: test_minimal_success_response and test_success_response_with_packages.
    # Both asserted the PRESENCE and TYPE of media_buy_id and packages, which this model
    # INHERITS from the adcp parent (verified off the live MRO: neither is redeclared
    # here), so they asserted that Python inheritance works -- the comparison CLAUDE.md
    # says this repo deliberately does not make. The value assertions read back the
    # literals the test had just passed in.
    #
    # What survives below is the case that CAN fail: the exclusion is OURS. The SDK
    # parent is extra="allow", this class is extra="ignore", and only the latter keeps an
    # undeclared seller-internal key off the wire.

    def test_internal_fields_excluded(self):
        """An undeclared seller-internal key does not reach the buyer.

        ``workflow_step_id`` is no longer a field on this model at all -- it was deleted
        when adapters moved to ``AdapterCreateResult``. What this grades now is the
        ``extra="ignore"`` config that makes the deletion effective: with the SDK
        parent's ``extra="allow"``, a construction site still passing the keyword turned
        it into a stored extra that serialized to the buyer on all three dump paths.
        """
        from src.core.schemas import CreateMediaBuySuccess

        resp = CreateMediaBuySuccess.sync_success(
            media_buy_id="buy_003",
            packages=[],
            confirmed_at=_CONFIRMED_AT,
            revision=_REVISION,
            workflow_step_id="wf_123",
        )
        data = resp.model_dump(mode="json")

        assert "workflow_step_id" not in data


# ===========================================================================
# 3. SyncCreativesResponse
# ===========================================================================


class TestSyncCreativesResponseShape:
    """Verify the serialized shape of SyncCreativesResponse."""

    def test_creatives_is_required(self):
        """SyncCreativesResponse() with no creatives must raise (#1399 R3-F2).

        Pinned 3.1 SyncCreativesSuccess.required=['creatives'] (the only required
        field). The success-variant model must not permit an under-specified shape
        — a synchronously-processed sync always carries a creatives array, even
        when every item failed.
        """
        from pydantic import ValidationError

        from src.core.schemas import SyncCreativesResponse

        with pytest.raises(ValidationError):
            SyncCreativesResponse()  # type: ignore[call-arg]

    def test_empty_sync_response(self):
        """Empty sync response has creatives list."""
        from src.core.schemas import SyncCreativesResponse

        resp = SyncCreativesResponse(creatives=[], dry_run=False)  # type: ignore[call-arg]
        data = resp.model_dump(mode="json")

        assert_field_type(data, "creatives", list)
        assert len(data["creatives"]) == 0

    def test_sync_response_with_created_creative(self):
        """Sync response with a created creative has correct shape."""
        from adcp.types import CreativeAction

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
        """Internal fields (internal_status, review_feedback) are excluded; spec status is derived."""
        from adcp.types import CreativeAction

        from src.core.schemas import SyncCreativeResult, SyncCreativesResponse

        result = SyncCreativeResult(
            creative_id="creative_002",
            action=CreativeAction.updated,
            internal_status="approved",
            review_feedback="Looks good",
        )
        resp = SyncCreativesResponse(creatives=[result], dry_run=False)  # type: ignore[call-arg]
        data = resp.model_dump(mode="json")

        c = data["creatives"][0]
        assert c["status"] == "approved", "The spec per-creative status is the row's review state"
        assert "internal_status" not in c, "Internal 'internal_status' field should be excluded"
        assert "review_feedback" not in c, "Internal 'review_feedback' field should be excluded"

    def test_sync_response_failed_creative_has_errors(self):
        """Failed creative includes errors list."""
        from adcp.types import CreativeAction

        from src.core.schemas import Error as AdCPErrorDetail
        from src.core.schemas import SyncCreativeResult, SyncCreativesResponse

        result = SyncCreativeResult(
            creative_id="creative_003",
            action=CreativeAction.failed,
            errors=[
                AdCPErrorDetail(code="REFERENCE_NOT_FOUND", message="Format not supported"),
                AdCPErrorDetail(code="REFERENCE_NOT_FOUND", message="Missing required asset"),
            ],
        )
        resp = SyncCreativesResponse(creatives=[result], dry_run=False)  # type: ignore[call-arg]
        data = resp.model_dump(mode="json")

        c = data["creatives"][0]
        assert_field_type(c, "errors", list)
        assert len(c["errors"]) == 2
        assert all(isinstance(e, dict) for e in c["errors"])


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
        )

        now = datetime.now(UTC)
        start = now - timedelta(days=7)

        # adcp 3.6.0: GetMediaBuyDeliveryResponse uses a media-buy specific ReportingPeriod
        # that differs from the creative delivery ReportingPeriod. Pass as dict for Pydantic coercion.
        return GetMediaBuyDeliveryResponse(
            reporting_period={"start": start, "end": now},
            currency="USD",
            aggregated_totals=AggregatedTotals(
                impressions=50000.0,
                spend=500.0,
                clicks=250.0,
                completed_views=None,
                media_buy_count=1,
            ),
            media_buy_deliveries=[
                MediaBuyDeliveryData(
                    media_buy_id="buy_100",
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
                            pricing_model=PricingModel.cpm,
                            rate=10.0,
                            currency="USD",
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
        )

        now = datetime.now(UTC)
        # adcp 3.6.0: use dict for reporting_period (media-buy specific type differs from schemas.ReportingPeriod)
        resp = GetMediaBuyDeliveryResponse(
            reporting_period={"start": now - timedelta(days=1), "end": now},
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
# ===========================================================================


# ===========================================================================
# 7. UpdateMediaBuyResponse (Success variant)
# ===========================================================================


class TestUpdateMediaBuyResponseShape:
    """Verify the serialized shape of UpdateMediaBuySuccess."""

    # REMOVED: test_minimal_success_response and test_success_response_with_packages, for
    # the reason given in TestCreateMediaBuyResponseShape above -- media_buy_id,
    # affected_packages, and the nested package_id/paused are all inherited, so presence
    # and type assertions on them cannot fail. Neither case asserted a LOCAL field of our
    # AffectedPackage subclass, which is the part the nested serializer actually decides.
    #
    # test_internal_fields_excluded stays: changes_applied and buyer_package_ref are ours,
    # declared with Field(exclude=True), and their absence from the dump is our behavior.

    def test_internal_fields_excluded(self):
        """Internal fields (changes_applied, buyer_package_ref) are excluded.

        ``workflow_step_id`` is no longer declared on this model -- see the create-side
        case of the same name: passing it grades ``extra="ignore"``, which is what keeps
        the deleted field off the wire rather than storing it as an extra.
        """
        from src.core.schemas import AffectedPackage, UpdateMediaBuySuccess

        package = AffectedPackage(
            package_id="pkg_002",
            paused=True,
            changes_applied={"creative_ids_added": ["c1", "c2"]},
            buyer_package_ref="buyer_pkg_ref_002",
        )
        resp = UpdateMediaBuySuccess.sync_success(
            media_buy_id="buy_102",
            affected_packages=[package],
            revision=_REVISION,
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
            pagination=Pagination(has_more=False),
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
            pagination=Pagination(has_more=False),
        )
        data = resp.model_dump(mode="json")

        assert_field_type(data, "creatives", list)
        assert len(data["creatives"]) == 1

        c = data["creatives"][0]
        assert_field_type(c, "creative_id", str)
        assert_field_type(c, "format_id", dict)

        assert c["creative_id"] == "creative_001"

    def test_query_summary_shape(self):
        """Query summary has expected fields."""
        from src.core.schemas import ListCreativesResponse, Pagination, QuerySummary

        resp = ListCreativesResponse(
            creatives=[],
            query_summary=QuerySummary(returned=5, total_matching=42, filters_applied=["status"]),
            pagination=Pagination(has_more=True),
        )
        data = resp.model_dump(mode="json")

        qs = data["query_summary"]
        assert_field_type(qs, "returned", int)
        assert_field_type(qs, "total_matching", int)
        assert_field_type(qs, "filters_applied", list)

    def test_pagination_shape(self):
        """Pagination has expected fields."""
        from src.core.schemas import ListCreativesResponse, Pagination, QuerySummary

        # In adcp 3.6.0, Pagination only has: has_more (required), cursor (optional), total_count (optional)
        resp = ListCreativesResponse(
            creatives=[],
            query_summary=QuerySummary(returned=0, total_matching=0, filters_applied=[]),
            pagination=Pagination(has_more=True, total_count=100),
        )
        data = resp.model_dump(mode="json")

        pg = data["pagination"]
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
            pagination=Pagination(has_more=False),
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
            pagination=Pagination(has_more=False),
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
                    media_buy_id="mb_test",
                    packages=[],
                    # No model defaults: both are columns the repository owns.
                    confirmed_at=_CONFIRMED_AT,
                    revision=_REVISION,
                ),
                id="create_media_buy",
            ),
            pytest.param(
                lambda: __import__(
                    "src.core.schemas", fromlist=["ListCreativeFormatsResponse"]
                ).ListCreativeFormatsResponse(formats=[create_test_format()]),
                id="list_creative_formats",
            ),
            # REMOVED: the update_media_buy param. An update_media_buy success response is
            # serialized on a REAL wire and schema-validated by a scenario that passes on
            # all four transports -- @T-UC-003-ext-scheduled-status ("update_media_buy on
            # a scheduled buy normalizes status and reports valid_actions") PASSED on a2a,
            # mcp and rest (bdd_inprocess) and on e2e_rest (bdd_e2e) in run
            # innet_150926_1232. A wire round-trip proves JSON-native types by
            # construction; this param proved it once through a constructor.
            pytest.param(
                lambda: __import__("src.core.schemas", fromlist=["ListCreativesResponse"]).ListCreativesResponse(
                    creatives=[],
                    query_summary=__import__("src.core.schemas", fromlist=["QuerySummary"]).QuerySummary(
                        returned=0, total_matching=0, filters_applied=[]
                    ),
                    pagination=__import__("src.core.schemas", fromlist=["Pagination"]).Pagination(has_more=False),
                ),
                id="list_creatives",
            ),
        ],
    )
    # Only the get_products param exercises the Product RootModel-union serializer
    # (cosmetic warning); harmless for the other params. See PR #1388 review F3.
    @pytest.mark.filterwarnings("ignore:Pydantic serializer warnings:UserWarning:pydantic.main")
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
        )

        now = datetime.now(UTC)
        # adcp 3.6.0: use dict for reporting_period (media-buy specific type differs from schemas.ReportingPeriod)
        resp = GetMediaBuyDeliveryResponse(
            reporting_period={"start": now - timedelta(days=1), "end": now},
            currency="USD",
            aggregated_totals=AggregatedTotals(impressions=1000.0, spend=10.0, media_buy_count=1),
            media_buy_deliveries=[
                MediaBuyDeliveryData(
                    media_buy_id="buy_1",
                    status="active",
                    pricing_model=PricingModel.cpm,
                    totals=DeliveryTotals(impressions=1000.0, spend=10.0),
                    by_package=[
                        # pricing_model / rate / currency are `required` on the pinned
                        # by-package item, so a package built without them is not a
                        # response this seller could serialize.
                        PackageDelivery(
                            package_id="pkg_1",
                            impressions=1000.0,
                            spend=10.0,
                            pricing_model=PricingModel.cpm,
                            rate=10.0,
                            currency="USD",
                        )
                    ],
                )
            ],
        )
        data = resp.model_dump(mode="json")
        json_str = json.dumps(data)
        assert isinstance(json_str, str)

    def test_sync_creatives_response_json_serializable(self):
        """SyncCreativesResponse is JSON-serializable."""
        import json

        from adcp.types import CreativeAction

        from src.core.schemas import Error as AdCPErrorDetail
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
                    errors=[AdCPErrorDetail(code="REFERENCE_NOT_FOUND", message="Bad format")],
                ),
            ],
            dry_run=False,
        )
        data = resp.model_dump(mode="json")
        json_str = json.dumps(data)
        assert isinstance(json_str, str)
