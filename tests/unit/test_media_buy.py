"""Entity test suite: media-buy

Spec verification: 2026-02-26
adcp spec commit: 8f26baf3
adcp-client-python commit: a08805d
Verified: 74/130 CONFIRMED, 52 UNSPECIFIED, 0 CONTRADICTS, 4 SPEC_AMBIGUOUS

Canonical test module for media-buy domain behavior.
Maps to test-obligations files:
  - UC-002-create-media-buy.md
  - UC-003-update-media-buy.md
  - UC-004-deliver-media-buy-metrics.md (main flow / status filter / date range only)
  - business-rules.md (BR-RULE-006, 008, 009, 011, 012, 013, 017, 018, 020, 021, 022, 024, 026, 028, 030)
  - constraints.md (media-buy, create-media-buy-request, update-media-buy-request)

Coverage: 47/130 obligations implemented, 83 stubs remaining.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from src.core.exceptions import AdCPValidationError
from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas import (
    AdapterGetMediaBuyDeliveryResponse,
    AdapterPackageDelivery,
    AffectedPackage,
    CreateMediaBuyError,
    CreateMediaBuyRequest,
    CreateMediaBuyResult,
    CreateMediaBuySuccess,
    DeliveryTotals,
    GetMediaBuyDeliveryRequest,
    GetMediaBuyDeliveryResponse,
    GetMediaBuysMediaBuy,
    GetMediaBuysPackage,
    GetMediaBuysResponse,
    PricingOption,
    ReportingPeriod,
    UpdateMediaBuyError,
    UpdateMediaBuyRequest,
    UpdateMediaBuySuccess,
)
from src.core.testing_hooks import AdCPTestContext
from src.core.tools.media_buy_delivery import _get_media_buy_delivery_impl

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _future(days: int = 7) -> str:
    """Return an ISO 8601 datetime string N days in the future."""
    dt = datetime.now(UTC) + timedelta(days=days)
    return dt.isoformat()


def _make_request(**overrides) -> CreateMediaBuyRequest:
    """Build a minimal valid CreateMediaBuyRequest."""
    defaults = {
        "buyer_ref": "test-buyer",
        "brand": {"domain": "testbrand.com"},
        "start_time": _future(1),
        "end_time": _future(8),
        "packages": [
            {
                "product_id": "prod_1",
                "buyer_ref": "pkg-1",
                "budget": 5000.0,
                "pricing_option_id": "cpm_usd_fixed",
            }
        ],
    }
    defaults.update(overrides)
    return CreateMediaBuyRequest(**defaults)


def _make_success(**overrides) -> CreateMediaBuySuccess:
    """Build a minimal valid CreateMediaBuySuccess response."""
    defaults = {
        "media_buy_id": "mb_1",
        "buyer_ref": "test",
        "packages": [],
    }
    defaults.update(overrides)
    return CreateMediaBuySuccess(**defaults)


def _make_identity(
    principal_id: str = "test_principal",
    tenant_id: str = "test_tenant",
    testing_context: AdCPTestContext | None = None,
) -> ResolvedIdentity:
    """Build a ResolvedIdentity with default test values."""
    return ResolvedIdentity(
        principal_id=principal_id,
        tenant_id=tenant_id,
        tenant={"tenant_id": tenant_id},
        protocol="mcp",
        testing_context=testing_context
        or AdCPTestContext(
            dry_run=False,
            mock_time=None,
            jump_to_event=None,
            test_session_id=None,
        ),
    )


def _mock_product(product_id: str = "prod_1", currency: str = "USD") -> MagicMock:
    """Create a mock DB Product with pricing_options."""
    pricing_option = MagicMock(
        spec=["pricing_model", "currency", "is_fixed", "rate", "min_spend_per_package", "root"],
    )
    pricing_option.pricing_model = "cpm"
    pricing_option.currency = currency
    pricing_option.is_fixed = True
    pricing_option.rate = Decimal("5.00")
    pricing_option.min_spend_per_package = None
    pricing_option.root = pricing_option

    product = MagicMock()
    product.product_id = product_id
    product.name = "Test Product"
    product.pricing_options = [pricing_option]
    product.delivery_type = "non_guaranteed"
    product.format_ids = [{"agent_url": "http://agent.test", "id": "fmt_1"}]
    return product


def _mock_media_buy(
    media_buy_id: str = "mb_1",
    buyer_ref: str = "test-buyer",
    start_date: date | None = None,
    end_date: date | None = None,
    budget: Decimal = Decimal("5000.00"),
    currency: str = "USD",
) -> MagicMock:
    """Create a mock MediaBuy ORM object."""
    buy = MagicMock()
    buy.media_buy_id = media_buy_id
    buy.buyer_ref = buyer_ref
    buy.tenant_id = "test_tenant"
    buy.principal_id = "test_principal"
    buy.budget = budget
    buy.currency = currency
    buy.start_date = start_date or date.today()
    buy.end_date = end_date or (date.today() + timedelta(days=30))
    buy.start_time = None
    buy.end_time = None
    buy.created_at = datetime.now(UTC)
    buy.updated_at = datetime.now(UTC)
    buy.raw_request = {"buyer_ref": buyer_ref, "packages": [{"product_id": "prod_1", "package_id": "pkg_1"}]}
    buy.status = "active"
    return buy


# ===========================================================================
# UC-002: CREATE MEDIA BUY
# ===========================================================================


class TestCreateMediaBuySchemaCompliance:
    """UC-002 schema validation: request parsing and field requirements."""

    def test_create_request_requires_brand(self):
        """UC-002-S01: brand is required per AdCP spec.

        Spec: CONFIRMED -- create-media-buy-request.json requires brand_manifest (mapped to brand in library)
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/create-media-buy-request.json
        https://github.com/adcontextprotocol/adcp-client-python/blob/a08805d6345c96d43ba9369bb0afe0597182871f/src/adcp/types/generated_poc/media_buy/create_media_buy_request.py
        """
        with pytest.raises(ValidationError):
            CreateMediaBuyRequest(
                buyer_ref="test",
                start_time=_future(1),
                end_time=_future(8),
                packages=[{"product_id": "p1", "budget": 1000.0}],
                # brand omitted
            )

    def test_create_request_requires_buyer_ref(self):
        """UC-002-S02: buyer_ref is required per AdCP spec.

        Spec: CONFIRMED -- create-media-buy-request.json required: ["buyer_ref", ...]
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/create-media-buy-request.json
        """
        with pytest.raises(ValidationError):
            CreateMediaBuyRequest(
                brand={"domain": "test.com"},
                start_time=_future(1),
                end_time=_future(8),
                packages=[{"product_id": "p1", "budget": 1000.0}],
                # buyer_ref omitted
            )

    def test_create_request_accepts_valid_minimal(self):
        """UC-002-S03: minimal valid request parses without error.

        Spec: CONFIRMED -- validates required fields from create-media-buy-request.json
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/create-media-buy-request.json
        """
        req = _make_request()
        assert req.buyer_ref == "test-buyer"
        assert req.packages is not None
        assert len(req.packages) == 1

    def test_create_request_start_time_must_be_tz_aware(self):
        """UC-002-S04: non-tz-aware start_time rejected.

        Spec: CONFIRMED -- start-timing.json requires "format": "date-time" (tz-aware)
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/core/start-timing.json
        """
        with pytest.raises(ValidationError):
            CreateMediaBuyRequest(
                buyer_ref="test",
                brand={"domain": "test.com"},
                start_time="2026-03-01T00:00:00",  # no tz
                end_time=_future(8),
                packages=[{"product_id": "p1", "budget": 1000.0}],
            )

    def test_create_request_accepts_asap_start_time(self):
        """UC-002-S05: start_time='asap' is valid per AdCP spec.

        Spec: CONFIRMED -- start-timing.json oneOf includes const "asap"
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/core/start-timing.json
        """
        req = _make_request(start_time="asap")
        assert req.start_time is not None

    def test_create_request_get_total_budget(self):
        """UC-002-S06: get_total_budget sums all package budgets.

        Spec: UNSPECIFIED (implementation-defined helper; spec defines budget at package level)
        """
        req = _make_request(
            packages=[
                {"product_id": "p1", "budget": 3000.0, "buyer_ref": "a", "pricing_option_id": "cpm_usd_fixed"},
                {"product_id": "p2", "budget": 2000.0, "buyer_ref": "b", "pricing_option_id": "cpm_usd_fixed"},
            ]
        )
        assert req.get_total_budget() == 5000.0

    def test_create_request_get_product_ids_deduplicates(self):
        """UC-002-S07: get_product_ids returns unique IDs preserving order.

        Spec: UNSPECIFIED (implementation-defined helper; spec defines product_id per package)
        """
        req = _make_request(
            packages=[
                {"product_id": "p1", "budget": 1000.0, "buyer_ref": "a", "pricing_option_id": "cpm_usd_fixed"},
                {"product_id": "p1", "budget": 2000.0, "buyer_ref": "b", "pricing_option_id": "cpm_usd_fixed"},
                {"product_id": "p2", "budget": 3000.0, "buyer_ref": "c", "pricing_option_id": "cpm_usd_fixed"},
            ]
        )
        assert req.get_product_ids() == ["p1", "p2"]


class TestCreateMediaBuyResponseShapes:
    """UC-002 response shape: success/error serialization."""

    def test_success_response_has_media_buy_id(self):
        """UC-002-R01: CreateMediaBuySuccess has media_buy_id.

        Spec: CONFIRMED -- create-media-buy-response.json success required: ["media_buy_id", ...]
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/create-media-buy-response.json
        Ported from test_approval_error_handling_core.py::test_success_response_has_media_buy_id
        """
        resp = _make_success(media_buy_id="mb_123")
        assert resp.media_buy_id == "mb_123"

    def test_error_response_has_errors_not_media_buy_id(self):
        """UC-002-R02: CreateMediaBuyError has errors field, no media_buy_id.

        Spec: CONFIRMED -- create-media-buy-response.json error: not anyOf [media_buy_id]
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/create-media-buy-response.json
        Ported from test_approval_error_handling_core.py::test_error_response_has_errors_not_media_buy_id
        """
        from adcp.types import Error

        resp = CreateMediaBuyError(errors=[Error(code="test", message="msg")])
        assert resp.errors is not None
        assert len(resp.errors) == 1

    def test_success_response_excludes_internal_fields(self):
        """UC-002-R03: workflow_step_id excluded from serialized output.

        Spec: UNSPECIFIED (implementation-defined internal field exclusion)
        Ported from test_response_shapes.py::test_internal_fields_excluded
        """
        resp = _make_success(
            media_buy_id="mb_123",
            workflow_step_id="ws_abc",
        )
        dumped = resp.model_dump()
        assert "workflow_step_id" not in dumped

    def test_result_wrapper_supports_tuple_unpacking(self):
        """UC-002-R04: CreateMediaBuyResult supports (response, status) unpacking.

        Spec: UNSPECIFIED (implementation-defined result wrapper pattern)
        """
        success = _make_success(media_buy_id="mb_1")
        result = CreateMediaBuyResult(status="completed", response=success)
        response, status = result
        assert status == "completed"
        assert response.media_buy_id == "mb_1"

    def test_result_serializes_with_status_field(self):
        """UC-002-R05: CreateMediaBuyResult.model_dump includes status at top level.

        Spec: UNSPECIFIED (implementation-defined result wrapper serialization)
        """
        success = _make_success(media_buy_id="mb_1")
        result = CreateMediaBuyResult(status="completed", response=success)
        dumped = result.model_dump()
        assert dumped["status"] == "completed"
        assert dumped["media_buy_id"] == "mb_1"

    def test_error_str_includes_error_count(self):
        """UC-002-R06: CreateMediaBuyError.__str__ mentions error count.

        Spec: UNSPECIFIED (implementation-defined string representation)
        """
        from adcp.types import Error

        resp = CreateMediaBuyError(errors=[Error(code="a", message="a"), Error(code="b", message="b")])
        assert "2 error" in str(resp)

    def test_success_str_includes_media_buy_id(self):
        """UC-002-R07: CreateMediaBuySuccess.__str__ mentions media_buy_id.

        Spec: UNSPECIFIED (implementation-defined string representation)
        """
        resp = _make_success(media_buy_id="mb_123")
        assert "mb_123" in str(resp)


class TestCreateMediaBuyValidation:
    """UC-002 business rule validation: budget, products, pricing, dates."""

    @pytest.mark.asyncio
    async def test_product_not_found_returns_error(self):
        """UC-002-V01: product not in catalog returns error in result.

        Spec: CONFIRMED -- package-request.json requires product_id; seller validates product existence
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/package-request.json
        Ported from test_create_media_buy_behavioral.py::test_product_not_found_returns_error
        """
        from src.core.tools.media_buy_create import _create_media_buy_impl

        # Request references prod_missing but DB has no products
        req = _make_request(
            packages=[
                {
                    "product_id": "prod_missing",
                    "buyer_ref": "pkg-1",
                    "budget": 5000.0,
                    "pricing_option_id": "cpm_usd_fixed",
                },
            ]
        )

        identity = ResolvedIdentity(
            principal_id="principal_1",
            tenant_id="test_tenant",
            tenant={"tenant_id": "test_tenant", "human_review_required": False, "auto_create_media_buys": True},
            auth_token="test-token",
            protocol="mcp",
            testing_context=AdCPTestContext(dry_run=False, test_session_id="test-session"),
        )

        with (
            patch("src.core.helpers.context_helpers.ensure_tenant_context"),
            patch("src.core.tools.media_buy_create.validate_setup_complete"),
            patch("src.core.tools.media_buy_create.get_principal_object") as mock_principal,
            patch("src.core.tools.media_buy_create.get_context_manager") as mock_ctx_mgr,
            patch("src.core.database.database_session.get_db_session") as mock_db,
        ):
            mock_princ = MagicMock()
            mock_princ.principal_id = "principal_1"
            mock_princ.name = "Test Buyer"
            mock_principal.return_value = mock_princ

            ctx_mgr = MagicMock()
            ctx_mgr.create_context.return_value = MagicMock(context_id="ctx_1")
            ctx_mgr.create_workflow_step.return_value = MagicMock(step_id="step_1")
            mock_ctx_mgr.return_value = ctx_mgr

            session = MagicMock()
            session.__enter__ = MagicMock(return_value=session)
            session.__exit__ = MagicMock(return_value=None)
            mock_db.return_value = session

            # Return empty product list so product is "not found"
            scalars_result = MagicMock()
            scalars_result.all.return_value = []
            scalars_result.first.return_value = None
            session.scalars.return_value = scalars_result

            result = await _create_media_buy_impl(req, identity=identity)

        assert isinstance(result, CreateMediaBuyResult)
        assert isinstance(result.response, CreateMediaBuyError)
        assert result.status == "failed"
        assert any("not found" in e.message.lower() for e in result.response.errors)

    @pytest.mark.asyncio
    async def test_max_daily_spend_exceeded(self):
        """UC-002-V02 / BR-RULE-012: daily spend > max rejected.

        Spec: UNSPECIFIED (implementation-defined spend cap enforcement; spec has no daily cap concept)
        Ported from test_create_media_buy_behavioral.py::test_max_daily_spend_exceeded
        """
        from src.core.tools.media_buy_create import _create_media_buy_impl

        # 7 day flight, $7000 budget = $1000/day; cap = $500 -> should fail
        req = _make_request(
            packages=[
                {"product_id": "prod_1", "buyer_ref": "pkg-1", "budget": 7000.0, "pricing_option_id": "cpm_usd_fixed"},
            ]
        )
        product = _mock_product("prod_1")

        # Currency limit with tight daily cap
        cl = MagicMock()
        cl.max_daily_package_spend = Decimal("500")
        cl.min_package_budget = None

        identity = ResolvedIdentity(
            principal_id="principal_1",
            tenant_id="test_tenant",
            tenant={"tenant_id": "test_tenant", "human_review_required": False, "auto_create_media_buys": True},
            auth_token="test-token",
            protocol="mcp",
            testing_context=AdCPTestContext(dry_run=False, test_session_id="test-session"),
        )

        with (
            patch("src.core.helpers.context_helpers.ensure_tenant_context"),
            patch("src.core.tools.media_buy_create.validate_setup_complete"),
            patch("src.core.tools.media_buy_create.get_principal_object") as mock_principal,
            patch("src.core.tools.media_buy_create.get_context_manager") as mock_ctx_mgr,
            patch("src.core.database.database_session.get_db_session") as mock_db,
        ):
            mock_princ = MagicMock()
            mock_princ.principal_id = "principal_1"
            mock_princ.name = "Test Buyer"
            mock_principal.return_value = mock_princ

            ctx_mgr = MagicMock()
            ctx_mgr.create_context.return_value = MagicMock(context_id="ctx_1")
            ctx_mgr.create_workflow_step.return_value = MagicMock(step_id="step_1")
            mock_ctx_mgr.return_value = ctx_mgr

            session = MagicMock()
            session.__enter__ = MagicMock(return_value=session)
            session.__exit__ = MagicMock(return_value=None)
            mock_db.return_value = session

            # .all() returns products; .first() returns currency_limit then None
            all_mock = MagicMock()
            all_mock.all.return_value = [product]
            first_mock = MagicMock(side_effect=[cl, None])
            scalars_result = MagicMock()
            scalars_result.all = all_mock.all
            scalars_result.first = first_mock
            session.scalars.return_value = scalars_result

            result = await _create_media_buy_impl(req=req, identity=identity)

        assert isinstance(result, CreateMediaBuyResult)
        assert isinstance(result.response, CreateMediaBuyError)
        assert result.status == "failed"
        assert any("daily" in e.message.lower() for e in result.response.errors)

    def test_pricing_option_xor_both_rejected(self):
        """UC-002-V03 / BR-RULE-006: both fixed_price and floor_price rejected.

        Spec: SPEC_AMBIGUOUS -- cpm-option.json has both as optional; XOR implied by description
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/pricing-options/cpm-option.json
        Ported from test_create_media_buy_behavioral.py::test_both_fixed_price_and_floor_price_rejected
        """
        with pytest.raises(ValidationError):
            PricingOption(
                pricing_model="cpm",
                currency="USD",
                fixed_price=5.0,
                floor_price=2.0,
            )

    def test_pricing_option_xor_neither_rejected(self):
        """UC-002-V04 / BR-RULE-006: neither fixed_price nor floor_price rejected.

        Spec: SPEC_AMBIGUOUS -- cpm-option.json has both as optional; XOR implied by description
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/pricing-options/cpm-option.json
        Ported from test_create_media_buy_behavioral.py::test_neither_fixed_price_nor_floor_price_rejected
        """
        with pytest.raises(ValidationError):
            PricingOption(
                pricing_model="cpm",
                currency="USD",
            )

    @pytest.mark.skip(reason="STUB: UC-002-V05 -- buyer_campaign_ref roundtrip in request/response [3.6 UPGRADE]")
    def test_buyer_campaign_ref_roundtrip(self):
        """UC-002-V05: buyer_campaign_ref preserved in create response.

        Spec: CONFIRMED -- create-media-buy-request.json has buyer_campaign_ref; response echoes it
        https://github.com/adcontextprotocol/adcp-client-python/blob/a08805d6345c96d43ba9369bb0afe0597182871f/src/adcp/types/generated_poc/media_buy/create_media_buy_request.py
        Priority: P0
        Type: unit
        Source: UC-002, salesagent-7gnv
        """

    @pytest.mark.skip(reason="STUB: UC-002-V06 -- ext fields roundtrip in create request/response [3.6 UPGRADE]")
    def test_ext_fields_roundtrip(self):
        """UC-002-V06: ext fields preserved through create flow.

        Spec: CONFIRMED -- create-media-buy-request.json and response both have ext field
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/create-media-buy-request.json
        Priority: P0
        Type: unit
        Source: UC-002, salesagent-7gnv
        """

    @pytest.mark.skip(reason="STUB: UC-002-V07 -- account_id accepted but not stored")
    def test_account_id_accepted_at_boundary(self):
        """UC-002-V07: account_id field accepted by schema but ignored in validation.

        Spec: CONFIRMED -- create-media-buy-request.json has account_id as optional property
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/create-media-buy-request.json
        Priority: P1
        Type: unit
        Source: UC-002, salesagent-7gnv
        """

    @pytest.mark.skip(reason="STUB: UC-002-V08 -- budget must be positive (BR-RULE-008)")
    def test_zero_budget_rejected(self):
        """UC-002-V08: total budget <= 0 rejected.

        Spec: CONFIRMED -- package-request.json budget has "minimum": 0 (zero technically valid)
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/package-request.json
        Priority: P1
        Type: unit
        Source: UC-002 main flow, BR-RULE-008
        """

    @pytest.mark.skip(reason="STUB: UC-002-V09 -- duplicate buyer_ref rejected (BR-RULE-009)")
    def test_duplicate_buyer_ref_rejected(self):
        """UC-002-V09: duplicate buyer_ref for same principal rejected.

        Spec: UNSPECIFIED (implementation-defined uniqueness enforcement)
        Priority: P1
        Type: unit
        Source: UC-002, BR-RULE-009
        """

    @pytest.mark.skip(reason="STUB: UC-002-V10 -- start_time required")
    def test_missing_start_time_rejected(self):
        """UC-002-V10: missing start_time rejected.

        Spec: CONFIRMED -- create-media-buy-request.json required: [..., "start_time", "end_time"]
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/create-media-buy-request.json
        Priority: P1
        Type: unit
        Source: UC-002 main flow
        """

    @pytest.mark.skip(reason="STUB: UC-002-V11 -- end_time must be after start_time (BR-RULE-013)")
    def test_end_before_start_rejected(self):
        """UC-002-V11: end_time <= start_time rejected.

        Spec: UNSPECIFIED (spec has no explicit date ordering constraint; implementation-defined)
        Priority: P1
        Type: unit
        Source: UC-002, BR-RULE-013
        """

    @pytest.mark.skip(reason="STUB: UC-002-V12 -- currency not supported by tenant")
    def test_unsupported_currency_rejected(self):
        """UC-002-V12: package currency not in tenant limits rejected.

        Spec: UNSPECIFIED (implementation-defined tenant currency configuration)
        Priority: P2
        Type: unit
        Source: UC-002
        """

    @pytest.mark.skip(reason="STUB: UC-002-V13 -- pricing_model not offered by product")
    def test_pricing_model_not_offered_rejected(self):
        """UC-002-V13: pricing_model not in product's options rejected.

        Spec: CONFIRMED -- package-request.json requires pricing_option_id referencing product's options
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/package-request.json
        Priority: P1
        Type: unit
        Source: UC-002, BR-RULE-006
        """

    @pytest.mark.skip(reason="STUB: UC-002-V14 -- bid_price below floor rejected")
    def test_bid_price_below_floor_rejected(self):
        """UC-002-V14: auction bid_price below floor_price rejected.

        Spec: CONFIRMED -- cpm-option.json floor_price description: "Bids below this value will be rejected"
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/pricing-options/cpm-option.json
        Priority: P1
        Type: unit
        Source: UC-002, BR-RULE-006
        """

    @pytest.mark.skip(reason="STUB: UC-002-V15 -- budget below minimum_spend_per_package rejected (BR-RULE-011)")
    def test_budget_below_minimum_spend_rejected(self):
        """UC-002-V15: package budget below min_spend_per_package rejected.

        Spec: CONFIRMED -- cpm-option.json has min_spend_per_package field
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/pricing-options/cpm-option.json
        Priority: P1
        Type: unit
        Source: UC-002, BR-RULE-011
        """


class TestCreateMediaBuyCreativeValidation:
    """UC-002 creative validation: pre-adapter creative checks."""

    def test_creative_missing_url_rejected(self):
        """UC-002-C01: reference creative missing URL raises INVALID_CREATIVES.

        Spec: UNSPECIFIED (implementation-defined creative pre-validation)
        Ported from test_create_media_buy_behavioral.py::test_creative_missing_url_raises_invalid_creatives
        """
        from src.core.tools.media_buy_create import _validate_creatives_before_adapter_call

        # Build a creative in DB that has no URL in its data
        mock_creative = MagicMock()
        mock_creative.creative_id = "c_1"
        mock_creative.format = "display_300x250"
        mock_creative.agent_url = "http://agent.test"
        mock_creative.data = {}  # no media_url

        # Build a mock format spec (reference format, no output_format_ids)
        mock_format_spec = MagicMock()
        mock_format_spec.output_format_ids = None

        package = MagicMock()
        package.creative_ids = ["c_1"]
        package.package_id = "pkg_1"

        with (
            patch("src.core.database.database_session.get_db_session") as mock_db,
            patch("src.core.tools.media_buy_create._get_format_spec_sync", return_value=mock_format_spec),
            patch("src.core.tools.media_buy_create.extract_media_url_and_dimensions", return_value=(None, None, None)),
        ):
            session = MagicMock()
            session.__enter__ = MagicMock(return_value=session)
            session.__exit__ = MagicMock(return_value=None)
            session.scalars.return_value.all.return_value = [mock_creative]
            mock_db.return_value = session

            with pytest.raises(AdCPValidationError) as exc_info:
                _validate_creatives_before_adapter_call([package], "test_tenant")

            assert exc_info.value.details.get("error_code") == "INVALID_CREATIVES"

    @pytest.mark.skip(reason="STUB: UC-002-C02 -- creative in error state rejected (BR-RULE-026)")
    def test_creative_error_state_rejected(self):
        """UC-002-C02: creative with status=error rejected.

        Spec: UNSPECIFIED (implementation-defined creative state validation)
        Priority: P1
        Type: unit
        Source: UC-002, BR-RULE-026
        """

    @pytest.mark.skip(reason="STUB: UC-002-C03 -- creative in rejected state rejected (BR-RULE-026)")
    def test_creative_rejected_state_rejected(self):
        """UC-002-C03: creative with status=rejected rejected.

        Spec: UNSPECIFIED (implementation-defined creative state validation)
        Priority: P1
        Type: unit
        Source: UC-002, BR-RULE-026
        """

    @pytest.mark.skip(reason="STUB: UC-002-C04 -- creative format mismatch rejected (BR-RULE-026)")
    def test_creative_format_mismatch_rejected(self):
        """UC-002-C04: creative format not matching product format rejected.

        Spec: UNSPECIFIED (implementation-defined creative format compatibility check)
        Priority: P1
        Type: unit
        Source: UC-002, BR-RULE-026
        """

    @pytest.mark.skip(reason="STUB: UC-002-C05 -- generative creatives skip pre-validation")
    def test_generative_creatives_skip_validation(self):
        """UC-002-C05: generative formats (with output_format_ids) not pre-validated.

        Spec: UNSPECIFIED (implementation-defined creative validation bypass)
        Priority: P2
        Type: unit
        Source: UC-002
        """

    @pytest.mark.skip(reason="STUB: UC-002-C06 -- multiple invalid creatives accumulated in single error")
    def test_multiple_creative_errors_accumulated(self):
        """UC-002-C06: all creative validation errors collected before raising.

        Spec: UNSPECIFIED (implementation-defined error accumulation pattern)
        Priority: P2
        Type: unit
        Source: UC-002
        """


class TestCreateMediaBuyStatusDetermination:
    """UC-002 status determination: _determine_media_buy_status logic."""

    def test_completed_when_past_end(self):
        """UC-002-ST01: past end_time -> completed.

        Spec: CONFIRMED -- media-buy-status.json: completed = "Media buy has finished running"
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/enums/media-buy-status.json
        """
        from src.core.tools.media_buy_create import _determine_media_buy_status

        now = datetime(2026, 4, 1, tzinfo=UTC)
        start = datetime(2026, 3, 1, tzinfo=UTC)
        end = datetime(2026, 3, 31, tzinfo=UTC)
        assert _determine_media_buy_status(False, True, True, start, end, now) == "completed"

    def test_active_when_in_flight_with_creatives(self):
        """UC-002-ST02: in-flight with approved creatives -> active.

        Spec: CONFIRMED -- media-buy-status.json: active = "Media buy is currently running"
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/enums/media-buy-status.json
        """
        from src.core.tools.media_buy_create import _determine_media_buy_status

        now = datetime(2026, 3, 15, tzinfo=UTC)
        start = datetime(2026, 3, 1, tzinfo=UTC)
        end = datetime(2026, 3, 31, tzinfo=UTC)
        assert _determine_media_buy_status(False, True, True, start, end, now) == "active"

    def test_pending_when_manual_approval_required(self):
        """UC-002-ST03: manual approval required -> pending_activation.

        Spec: CONFIRMED -- media-buy-status.json: pending_activation = "Media buy created but not yet activated"
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/enums/media-buy-status.json
        """
        from src.core.tools.media_buy_create import _determine_media_buy_status

        now = datetime(2026, 3, 15, tzinfo=UTC)
        start = datetime(2026, 3, 1, tzinfo=UTC)
        end = datetime(2026, 3, 31, tzinfo=UTC)
        assert _determine_media_buy_status(True, True, True, start, end, now) == "pending_activation"

    def test_pending_when_missing_creatives(self):
        """UC-002-ST04: no creatives -> pending_activation.

        Spec: CONFIRMED -- media-buy-status.json: pending_activation = "Media buy created but not yet activated"
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/enums/media-buy-status.json
        """
        from src.core.tools.media_buy_create import _determine_media_buy_status

        now = datetime(2026, 3, 15, tzinfo=UTC)
        start = datetime(2026, 3, 1, tzinfo=UTC)
        end = datetime(2026, 3, 31, tzinfo=UTC)
        assert _determine_media_buy_status(False, False, False, start, end, now) == "pending_activation"

    def test_pending_when_before_start(self):
        """UC-002-ST05: before start_time -> pending_activation.

        Spec: CONFIRMED -- media-buy-status.json: pending_activation = "Media buy created but not yet activated"
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/enums/media-buy-status.json
        """
        from src.core.tools.media_buy_create import _determine_media_buy_status

        now = datetime(2026, 2, 15, tzinfo=UTC)
        start = datetime(2026, 3, 1, tzinfo=UTC)
        end = datetime(2026, 3, 31, tzinfo=UTC)
        assert _determine_media_buy_status(False, True, True, start, end, now) == "pending_activation"


class TestCreateMediaBuyImplAuth:
    """UC-002 auth extension: identity and principal validation."""

    @pytest.mark.asyncio
    async def test_missing_identity_raises_validation_error(self):
        """UC-002-A01: None identity raises error.

        Spec: UNSPECIFIED (implementation-defined authentication boundary)
        """
        from src.core.tools.media_buy_create import _create_media_buy_impl

        req = _make_request()
        with pytest.raises(AdCPValidationError, match="[Ii]dentity"):
            await _create_media_buy_impl(req, identity=None)

    @pytest.mark.asyncio
    async def test_missing_principal_returns_error_response(self):
        """UC-002-A02: principal not found returns error (not exception).

        Spec: UNSPECIFIED (implementation-defined principal resolution)
        Ported from test_create_media_buy_behavioral.py pattern.
        """
        from src.core.tools.media_buy_create import _create_media_buy_impl

        identity = _make_identity()
        req = _make_request()

        with (
            patch("src.core.tools.media_buy_create.validate_setup_complete"),
            patch("src.core.tools.media_buy_create.get_principal_object", return_value=None),
        ):
            result = await _create_media_buy_impl(req, identity=identity)
            response, status = result
            assert isinstance(response, CreateMediaBuyError)
            assert status == "failed"

    @pytest.mark.skip(reason="STUB: UC-002-A03 -- missing tenant context raises auth error")
    def test_missing_tenant_raises_auth_error(self):
        """UC-002-A03: identity without tenant raises AdCPAuthenticationError.

        Spec: UNSPECIFIED (implementation-defined authentication boundary)
        Priority: P0
        Type: unit
        Source: UC-002 ext-a
        """

    @pytest.mark.skip(reason="STUB: UC-002-A04 -- setup incomplete raises validation error")
    def test_setup_incomplete_raises_error(self):
        """UC-002-A04: incomplete tenant setup raises validation error.

        Spec: UNSPECIFIED (implementation-defined tenant setup validation)
        Priority: P1
        Type: unit
        Source: UC-002 main flow
        """


class TestCreateMediaBuyManualApproval:
    """UC-002 alt-manual: manual approval / HITL flow."""

    @pytest.mark.skip(reason="STUB: UC-002-MA01 -- manual approval creates workflow step with pending status")
    def test_manual_approval_creates_pending_workflow_step(self):
        """UC-002-MA01: when human_review_required, status is 'submitted'.

        Spec: UNSPECIFIED (implementation-defined HITL workflow)
        Priority: P1
        Type: unit
        Source: UC-002 alt-manual, BR-RULE-017
        """

    @pytest.mark.skip(reason="STUB: UC-002-MA02 -- manual approval stores raw_request for later execution")
    def test_manual_approval_stores_raw_request(self):
        """UC-002-MA02: raw_request preserved in DB for deferred adapter call.

        Spec: UNSPECIFIED (implementation-defined deferred execution pattern)
        Priority: P1
        Type: unit
        Source: UC-002 alt-manual, BR-RULE-020
        """

    @pytest.mark.skip(reason="STUB: UC-002-MA03 -- execute_approved_media_buy calls adapter for approved buy")
    def test_execute_approved_calls_adapter(self):
        """UC-002-MA03: approved buy triggers adapter creation.

        Spec: UNSPECIFIED (implementation-defined approval execution)
        Priority: P1
        Type: unit
        Source: UC-002 alt-manual
        """


class TestCreateMediaBuyAdapterInteraction:
    """UC-002 adapter call: _execute_adapter_media_buy_creation behavior."""

    @pytest.mark.skip(reason="STUB: UC-002-AD01 -- adapter error response logged with error count")
    def test_adapter_error_logged(self):
        """UC-002-AD01: adapter returning CreateMediaBuyError logs each error.

        Spec: UNSPECIFIED (implementation-defined adapter error logging)
        Priority: P1
        Type: unit
        Source: UC-002, BR-RULE-020
        """

    @pytest.mark.skip(reason="STUB: UC-002-AD02 -- adapter exception propagates")
    def test_adapter_exception_propagates(self):
        """UC-002-AD02: adapter raising exception is re-raised.

        Spec: UNSPECIFIED (implementation-defined adapter error handling)
        Priority: P1
        Type: unit
        Source: UC-002
        """

    @pytest.mark.skip(reason="STUB: UC-002-AD03 -- dry_run skips adapter call entirely")
    def test_dry_run_skips_adapter(self):
        """UC-002-AD03: testing context dry_run=True never calls adapter.

        Spec: UNSPECIFIED (implementation-defined testing/sandbox behavior)
        Priority: P1
        Type: unit
        Source: UC-002
        """


# ===========================================================================
# UC-003: UPDATE MEDIA BUY
# ===========================================================================


class TestUpdateMediaBuySchemaCompliance:
    """UC-003 schema: request parsing and field requirements."""

    def test_update_request_accepts_media_buy_id(self):
        """UC-003-S01: media_buy_id accepted as optional field.

        Spec: CONFIRMED -- update-media-buy-request.json oneOf requires media_buy_id OR buyer_ref
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/update-media-buy-request.json
        """
        req = UpdateMediaBuyRequest(media_buy_id="mb_1", packages=[])
        assert req.media_buy_id == "mb_1"

    def test_update_request_parses_iso_datetime_strings(self):
        """UC-003-S02: ISO datetime strings parsed in pre-validator.

        Spec: CONFIRMED -- update-media-buy-request.json start_time refs start-timing.json, end_time is date-time
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/update-media-buy-request.json
        """
        req = UpdateMediaBuyRequest(
            media_buy_id="mb_1",
            start_time="2026-03-01T00:00:00+00:00",
            end_time="2026-03-31T00:00:00+00:00",
        )
        assert isinstance(req.start_time, datetime)
        assert isinstance(req.end_time, datetime)

    def test_update_request_accepts_asap_start_time(self):
        """UC-003-S03: start_time='asap' valid per AdCP spec.

        Spec: CONFIRMED -- update-media-buy-request.json start_time refs start-timing.json (oneOf: "asap" | datetime)
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/core/start-timing.json
        """
        req = UpdateMediaBuyRequest(media_buy_id="mb_1", start_time="asap")
        assert req.start_time == "asap"

    @pytest.mark.skip(reason="STUB: UC-003-S04 -- buyer_campaign_ref preserved in response [3.6 UPGRADE]")
    def test_update_buyer_campaign_ref_roundtrip(self):
        """UC-003-S04: buyer_campaign_ref preserved in update response.

        Spec: SPEC_AMBIGUOUS -- update-media-buy-request.json has no buyer_campaign_ref; response has none
        Priority: P0
        Type: unit
        Source: UC-003, salesagent-7gnv
        """

    @pytest.mark.skip(reason="STUB: UC-003-S05 -- ext fields preserved in update request/response [3.6 UPGRADE]")
    def test_update_ext_fields_roundtrip(self):
        """UC-003-S05: ext fields preserved through update flow.

        Spec: CONFIRMED -- update-media-buy-request.json and response both have ext field
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/update-media-buy-request.json
        Priority: P0
        Type: unit
        Source: UC-003, salesagent-7gnv
        """


class TestUpdateMediaBuyResponseShapes:
    """UC-003 response shape: UpdateMediaBuySuccess/Error serialization."""

    def test_success_response_includes_affected_packages(self):
        """UC-003-R01: affected_packages populated on success.

        Spec: CONFIRMED -- update-media-buy-response.json success has affected_packages property
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/update-media-buy-response.json
        Ported from test_update_media_buy_affected_packages.py::test_response_serialization_includes_affected_packages
        """
        resp = UpdateMediaBuySuccess(
            media_buy_id="mb_1",
            buyer_ref="test",
            affected_packages=[
                AffectedPackage(package_id="pkg_1", paused=False),
            ],
        )
        dumped = resp.model_dump()
        assert "affected_packages" in dumped
        assert len(dumped["affected_packages"]) == 1
        assert dumped["affected_packages"][0]["package_id"] == "pkg_1"

    def test_error_response_atomic(self):
        """UC-003-R02 / BR-RULE-018: error has no success fields.

        Spec: CONFIRMED -- update-media-buy-response.json error: not anyOf [media_buy_id, buyer_ref, affected_packages]
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/update-media-buy-response.json
        """
        from adcp.types import Error

        resp = UpdateMediaBuyError(errors=[Error(code="test", message="fail")])
        dumped = resp.model_dump()
        assert "errors" in dumped
        # success fields should not be present or should be None
        assert dumped.get("affected_packages") is None

    def test_affected_packages_excludes_internal_fields(self):
        """UC-003-R03: changes_applied and buyer_package_ref excluded.

        Spec: UNSPECIFIED (implementation-defined internal field exclusion)
        Ported from test_update_media_buy_affected_packages.py pattern.
        """
        pkg = AffectedPackage(
            package_id="pkg_1",
            paused=False,
            changes_applied={"creative_ids": ["c1"]},
            buyer_package_ref="bpr_1",
        )
        dumped = pkg.model_dump()
        assert "changes_applied" not in dumped
        assert "buyer_package_ref" not in dumped


class TestUpdateMediaBuyMainFlow:
    """UC-003 main flow: package budget update (auto-applied)."""

    @pytest.mark.skip(reason="STUB: UC-003-MF01 -- happy path package budget via media_buy_id")
    def test_package_budget_update_via_media_buy_id(self):
        """UC-003-MF01: update package budget returns success with affected_packages.

        Spec: CONFIRMED -- update-media-buy-request.json packages[].budget + response affected_packages
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/update-media-buy-request.json
        Priority: P0
        Type: unit
        Source: UC-003 main flow
        """

    @pytest.mark.skip(reason="STUB: UC-003-MF02 -- happy path package budget via buyer_ref")
    def test_package_budget_update_via_buyer_ref(self):
        """UC-003-MF02: buyer_ref resolves to media buy, update succeeds.

        Spec: CONFIRMED -- update-media-buy-request.json oneOf allows buyer_ref identification
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/update-media-buy-request.json
        Priority: P0
        Type: unit
        Source: UC-003, BR-RULE-021
        """

    @pytest.mark.skip(reason="STUB: UC-003-MF03 -- partial update: omitted fields unchanged (BR-RULE-022)")
    def test_partial_update_omitted_fields_unchanged(self):
        """UC-003-MF03: only specified fields update, rest preserved.

        Spec: CONFIRMED -- package-update.json: "Fields not present are left unchanged"
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/package-update.json
        Priority: P0
        Type: unit
        Source: UC-003, BR-RULE-022
        """

    @pytest.mark.skip(reason="STUB: UC-003-MF04 -- empty update rejected (BR-RULE-022)")
    def test_empty_update_rejected(self):
        """UC-003-MF04: update with no updatable fields returns error.

        Spec: UNSPECIFIED (implementation-defined empty update rejection)
        Priority: P1
        Type: unit
        Source: UC-003, BR-RULE-022
        """


class TestUpdateMediaBuyPauseResume:
    """UC-003 alt-pause: pause/resume campaign."""

    @pytest.mark.skip(reason="STUB: UC-003-PR01 -- pause active media buy")
    def test_pause_active_media_buy(self):
        """UC-003-PR01: paused=true on active buy calls adapter with pause action.

        Spec: CONFIRMED -- update-media-buy-request.json has paused: boolean property
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/update-media-buy-request.json
        Priority: P0
        Type: unit
        Source: UC-003 alt-pause
        """

    @pytest.mark.skip(reason="STUB: UC-003-PR02 -- resume paused media buy")
    def test_resume_paused_media_buy(self):
        """UC-003-PR02: paused=false on paused buy calls adapter with resume action.

        Spec: CONFIRMED -- update-media-buy-request.json paused: false = active
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/update-media-buy-request.json
        Priority: P0
        Type: unit
        Source: UC-003 alt-pause
        """

    @pytest.mark.skip(reason="STUB: UC-003-PR03 -- pause skips budget validation")
    def test_pause_skips_budget_validation(self):
        """UC-003-PR03: pause does not trigger currency/budget validation.

        Spec: UNSPECIFIED (implementation-defined validation bypass for pause)
        Priority: P2
        Type: unit
        Source: UC-003 alt-pause
        """


class TestUpdateMediaBuyTiming:
    """UC-003 alt-timing: update start_time/end_time."""

    def test_valid_date_range_accepted(self):
        """UC-003-T01: valid end > start persists.

        Spec: CONFIRMED -- update-media-buy-request.json has start_time and end_time properties
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/update-media-buy-request.json
        Ported from test_update_media_buy_behavioral.py::test_valid_date_range_persists_to_db
        """
        # Schema accepts valid range
        req = UpdateMediaBuyRequest(
            media_buy_id="mb_1",
            start_time="2026-03-01T00:00:00+00:00",
            end_time="2026-03-31T00:00:00+00:00",
        )
        assert req.start_time is not None
        assert req.end_time is not None

    @pytest.mark.skip(reason="STUB: UC-003-T02 -- end_time before start_time returns error (BR-RULE-013)")
    def test_end_before_start_returns_error(self):
        """UC-003-T02: end_time <= start_time rejected.

        Spec: UNSPECIFIED (no explicit date ordering in spec; implementation-defined)
        Priority: P1
        Type: unit
        Source: UC-003 ext-e, BR-RULE-013
        """

    @pytest.mark.skip(reason="STUB: UC-003-T03 -- shortened flight recalculates daily spend (BR-RULE-012)")
    def test_shortened_flight_recalculates_daily_spend(self):
        """UC-003-T03: shorter flight with same budget may exceed daily cap.

        Spec: UNSPECIFIED (implementation-defined spend cap recalculation)
        Priority: P1
        Type: unit
        Source: UC-003 alt-timing, BR-RULE-012
        """


class TestUpdateMediaBuyCampaignBudget:
    """UC-003 alt-budget: campaign-level budget update."""

    @pytest.mark.skip(reason="STUB: UC-003-B01 -- positive campaign budget accepted")
    def test_positive_campaign_budget_accepted(self):
        """UC-003-B01: campaign budget > 0 accepted.

        Spec: CONFIRMED -- package-update.json budget has "minimum": 0
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/package-update.json
        Priority: P1
        Type: unit
        Source: UC-003 alt-budget, BR-RULE-008
        """

    @pytest.mark.skip(reason="STUB: UC-003-B02 -- zero campaign budget rejected (BR-RULE-008)")
    def test_zero_campaign_budget_rejected(self):
        """UC-003-B02: budget=0 rejected.

        Spec: SPEC_AMBIGUOUS -- package-update.json budget "minimum": 0 allows zero; rejection is implementation-defined
        Priority: P1
        Type: unit
        Source: UC-003 ext-d, BR-RULE-008
        """

    @pytest.mark.skip(reason="STUB: UC-003-B03 -- negative campaign budget rejected (BR-RULE-008)")
    def test_negative_campaign_budget_rejected(self):
        """UC-003-B03: budget=-500 rejected.

        Spec: CONFIRMED -- package-update.json budget "minimum": 0 rejects negative
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/package-update.json
        Priority: P2
        Type: unit
        Source: UC-003 ext-d, BR-RULE-008
        """


class TestUpdateMediaBuyCreativeIds:
    """UC-003 alt-creative-ids: replace package creatives via creative_ids."""

    @pytest.mark.skip(reason="STUB: UC-003-CI01 -- creative_ids replaces all existing assignments (BR-RULE-024)")
    def test_creative_ids_replaces_all(self):
        """UC-003-CI01: creative_ids = replacement, not additive.

        Spec: CONFIRMED -- package-update.json creative_assignments: "Uses replacement semantics"
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/package-update.json
        Priority: P0
        Type: unit
        Source: UC-003 alt-creative-ids, BR-RULE-024
        """

    @pytest.mark.skip(reason="STUB: UC-003-CI02 -- creative_ids not found returns error")
    def test_creative_ids_not_found(self):
        """UC-003-CI02: nonexistent creative_ids returns creatives_not_found.

        Spec: CONFIRMED -- error.json structure for creative validation errors
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/core/error.json
        Priority: P1
        Type: unit
        Source: UC-003 ext-i
        """

    @pytest.mark.skip(reason="STUB: UC-003-CI03 -- creative in error state rejected (BR-RULE-026)")
    def test_creative_error_state_rejected(self):
        """UC-003-CI03: creative with status=error rejected.

        Spec: UNSPECIFIED (implementation-defined creative state validation)
        Priority: P1
        Type: unit
        Source: UC-003 ext-j, BR-RULE-026
        """

    @pytest.mark.skip(reason="STUB: UC-003-CI04 -- creative format mismatch rejected (BR-RULE-026)")
    def test_creative_format_mismatch_rejected(self):
        """UC-003-CI04: creative format incompatible with product.

        Spec: UNSPECIFIED (implementation-defined creative format compatibility)
        Priority: P1
        Type: unit
        Source: UC-003 ext-j, BR-RULE-026
        """

    @pytest.mark.skip(reason="STUB: UC-003-CI05 -- change set computation: added, removed, unchanged")
    def test_change_set_computation(self):
        """UC-003-CI05: [C1,C2,C3] -> [C2,C4] means add C4, remove C1,C3.

        Spec: CONFIRMED -- package-update.json creative_assignments: replacement semantics
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/package-update.json
        Priority: P1
        Type: unit
        Source: UC-003 alt-creative-ids, BR-RULE-024
        """


class TestUpdateMediaBuyCreativeAssignments:
    """UC-003 alt-creative-assignments: weighted/placement-targeted assignments."""

    @pytest.mark.skip(reason="STUB: UC-003-CA01 -- creative_assignments with weights")
    def test_creative_assignments_with_weights(self):
        """UC-003-CA01: creative_assignments replaces all with specified weights.

        Spec: CONFIRMED -- package-update.json has creative_assignments with replacement semantics
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/package-update.json
        Priority: P1
        Type: unit
        Source: UC-003 alt-creative-assignments, BR-RULE-024
        """

    @pytest.mark.skip(reason="STUB: UC-003-CA02 -- invalid placement_ids rejected (BR-RULE-028)")
    def test_invalid_placement_ids_rejected(self):
        """UC-003-CA02: placement_ids not in product rejected.

        Spec: UNSPECIFIED (implementation-defined placement validation)
        Priority: P1
        Type: unit
        Source: UC-003 ext-m, BR-RULE-028
        """


class TestUpdateMediaBuyIdentification:
    """UC-003 ext-b: media buy resolution (XOR identification)."""

    @pytest.mark.skip(reason="STUB: UC-003-ID01 -- both media_buy_id and buyer_ref rejected (BR-RULE-021)")
    def test_both_ids_rejected(self):
        """UC-003-ID01: providing both identifiers rejected.

        Spec: CONFIRMED -- update-media-buy-request.json oneOf [media_buy_id] or [buyer_ref] = XOR
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/update-media-buy-request.json
        Priority: P1
        Type: unit
        Source: UC-003 ext-b, BR-RULE-021
        """

    @pytest.mark.skip(reason="STUB: UC-003-ID02 -- neither media_buy_id nor buyer_ref rejected (BR-RULE-021)")
    def test_neither_id_rejected(self):
        """UC-003-ID02: providing neither identifier rejected.

        Spec: CONFIRMED -- update-media-buy-request.json oneOf requires one of the two
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/update-media-buy-request.json
        Priority: P1
        Type: unit
        Source: UC-003 ext-b, BR-RULE-021
        """

    @pytest.mark.skip(reason="STUB: UC-003-ID03 -- media_buy_id not found returns error")
    def test_media_buy_id_not_found(self):
        """UC-003-ID03: nonexistent media_buy_id returns media_buy_not_found.

        Spec: CONFIRMED -- error.json provides error structure for not_found responses
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/core/error.json
        Priority: P1
        Type: unit
        Source: UC-003 ext-b
        """

    @pytest.mark.skip(reason="STUB: UC-003-ID04 -- buyer_ref not found returns error")
    def test_buyer_ref_not_found(self):
        """UC-003-ID04: nonexistent buyer_ref returns media_buy_not_found.

        Spec: CONFIRMED -- error.json provides error structure for not_found responses
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/core/error.json
        Priority: P1
        Type: unit
        Source: UC-003 ext-b
        """


class TestUpdateMediaBuyOwnership:
    """UC-003 ext-c: ownership verification."""

    @pytest.mark.skip(reason="STUB: UC-003-OW01 -- principal does not own media buy (P0 security)")
    def test_ownership_mismatch_rejected(self):
        """UC-003-OW01: non-owner gets permission error.

        Spec: UNSPECIFIED (implementation-defined security boundary)
        Priority: P0
        Type: unit
        Source: UC-003 ext-c
        """


class TestUpdateMediaBuyManualApproval:
    """UC-003 alt-manual: manual approval for updates."""

    @pytest.mark.skip(reason="STUB: UC-003-MA01 -- update enters pending state when manual approval required")
    def test_manual_approval_pending_state(self):
        """UC-003-MA01: manual approval returns status 'submitted'.

        Spec: UNSPECIFIED (implementation-defined HITL workflow)
        Priority: P1
        Type: unit
        Source: UC-003 alt-manual, BR-RULE-017
        """

    @pytest.mark.skip(reason="STUB: UC-003-MA02 -- implementation_date null when pending approval")
    def test_implementation_date_null_when_pending(self):
        """UC-003-MA02: implementation_date is null until approved.

        Spec: CONFIRMED -- update-media-buy-response.json implementation_date: "null if pending approval"
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/update-media-buy-response.json
        Priority: P1
        Type: unit
        Source: UC-003 alt-manual
        """


class TestUpdateMediaBuyAdapterFailure:
    """UC-003 ext-o: adapter/workflow failure."""

    @pytest.mark.skip(reason="STUB: UC-003-AF01 -- adapter network error returns activation_workflow_failed")
    def test_adapter_network_error(self):
        """UC-003-AF01: adapter failure returns activation_workflow_failed.

        Spec: UNSPECIFIED (implementation-defined adapter error handling)
        Priority: P1
        Type: unit
        Source: UC-003 ext-o, BR-RULE-020
        """

    @pytest.mark.skip(reason="STUB: UC-003-AF02 -- all-or-nothing: no DB changes on adapter failure (P0)")
    def test_no_db_changes_on_adapter_failure(self):
        """UC-003-AF02: adapter failure means no DB records updated.

        Spec: CONFIRMED -- update-media-buy-response.json: "updates are either fully applied or not applied at all"
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/update-media-buy-response.json
        Priority: P0
        Type: unit
        Source: UC-003 ext-o, BR-RULE-020
        """


# ===========================================================================
# UC-004: DELIVERY METRICS (main flow, status filter, date range)
# ===========================================================================


class TestDeliveryImplSingleBuy:
    """UC-004 main flow: single media buy delivery orchestration."""

    def test_single_buy_returns_complete_response(self):
        """UC-004-D01: single buy returns all top-level fields.

        Spec: CONFIRMED -- get-media-buy-delivery-response.json: reporting_period, currency, media_buy_deliveries required
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/get-media-buy-delivery-response.json
        Ported from test_delivery_behavioral.py::test_single_buy_returns_complete_response
        """
        buy = _mock_media_buy(start_date=date.today() - timedelta(days=5))
        buy.raw_request = {"packages": [{"package_id": "pkg_1", "product_id": "prod_1"}], "buyer_ref": "test-buyer"}

        adapter_response = AdapterGetMediaBuyDeliveryResponse(
            media_buy_id="mb_1",
            reporting_period=ReportingPeriod(start=datetime.now(UTC) - timedelta(days=5), end=datetime.now(UTC)),
            totals=DeliveryTotals(impressions=1000, spend=50.0),
            by_package=[AdapterPackageDelivery(package_id="pkg_1", impressions=1000, spend=50.0)],
            currency="USD",
        )

        identity = _make_identity()
        adapter_mock = MagicMock()
        adapter_mock.get_media_buy_delivery.return_value = adapter_response

        _PATCH = "src.core.tools.media_buy_delivery"
        with (
            patch(f"{_PATCH}.get_principal_object") as mock_principal,
            patch(f"{_PATCH}.get_adapter", return_value=adapter_mock),
            patch(f"{_PATCH}._get_target_media_buys", return_value=[("mb_1", buy)]),
            patch(f"{_PATCH}._get_pricing_options", return_value={}),
            patch(f"{_PATCH}.get_db_session") as mock_db,
        ):
            mock_principal.return_value = MagicMock(principal_id="test_principal")
            # Inner session for MediaPackage query
            mock_inner_session = MagicMock()
            mock_inner_session.scalars.return_value.all.return_value = []
            mock_db.return_value.__enter__.return_value = mock_inner_session

            req = GetMediaBuyDeliveryRequest(
                media_buy_ids=["mb_1"],
                start_date="2025-01-01",
                end_date="2025-06-30",
            )
            resp = _get_media_buy_delivery_impl(req, identity)

            assert isinstance(resp, GetMediaBuyDeliveryResponse)
            assert resp.reporting_period is not None
            assert resp.currency == "USD"
            assert len(resp.media_buy_deliveries) == 1
            assert resp.aggregated_totals.impressions >= 0

    @pytest.mark.skip(reason="STUB: UC-004-D02 -- fetch by buyer_refs")
    def test_fetch_by_buyer_refs(self):
        """UC-004-D02: buyer_refs resolution returns delivery data.

        Spec: CONFIRMED -- get-media-buy-delivery-request.json has buyer_refs property
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/get-media-buy-delivery-request.json
        Priority: P0
        Type: unit
        Source: UC-004 main flow, BR-RULE-030
        """

    @pytest.mark.skip(reason="STUB: UC-004-D03 -- multiple media buys aggregate totals")
    def test_multiple_buys_aggregate_totals(self):
        """UC-004-D03: aggregated_totals sums across multiple buys.

        Spec: CONFIRMED -- get-media-buy-delivery-response.json has aggregated_totals property
        https://github.com/adcontextprotocol/adcp-client-python/blob/a08805d6345c96d43ba9369bb0afe0597182871f/src/adcp/types/generated_poc/media_buy/get_media_buy_delivery_response.py
        Priority: P1
        Type: unit
        Source: UC-004 main flow
        """

    @pytest.mark.skip(reason="STUB: UC-004-D04 -- neither ids nor refs fetches all for principal")
    def test_no_ids_fetches_all(self):
        """UC-004-D04: no identifiers = all buys for principal.

        Spec: CONFIRMED -- get-media-buy-delivery-request.json: media_buy_ids and buyer_refs both optional
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/get-media-buy-delivery-request.json
        Priority: P1
        Type: unit
        Source: UC-004 main flow, BR-RULE-030
        """

    @pytest.mark.skip(reason="STUB: UC-004-D05 -- media_buy_ids takes precedence over buyer_refs")
    def test_media_buy_ids_wins_over_buyer_refs(self):
        """UC-004-D05: when both provided, media_buy_ids used.

        Spec: UNSPECIFIED (implementation-defined precedence when both identifiers provided)
        Priority: P1
        Type: unit
        Source: UC-004, BR-RULE-030
        """


class TestDeliveryImplStatusFilter:
    """UC-004 alt-filtered: status-based delivery filtering."""

    @pytest.mark.skip(reason="STUB: UC-004-SF01 -- status_filter='active' returns only active buys")
    def test_filter_active(self):
        """UC-004-SF01: active filter returns only active buys.

        Spec: CONFIRMED -- get-media-buy-delivery-request.json has status_filter property
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/get-media-buy-delivery-request.json
        Priority: P1
        Type: unit
        Source: UC-004 alt-filtered
        """

    @pytest.mark.skip(reason="STUB: UC-004-SF02 -- status_filter='all' returns all statuses")
    def test_filter_all(self):
        """UC-004-SF02: 'all' returns all statuses.

        Spec: CONFIRMED -- get-media-buy-delivery-request.json status_filter uses media-buy-status enum
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/get-media-buy-delivery-request.json
        Priority: P2
        Type: unit
        Source: UC-004 alt-filtered
        """

    @pytest.mark.skip(reason="STUB: UC-004-SF03 -- default status_filter is 'active'")
    def test_default_filter_is_active(self):
        """UC-004-SF03: no status_filter defaults to active only.

        Spec: UNSPECIFIED (implementation-defined default filter; spec has no default)
        Priority: P2
        Type: unit
        Source: UC-004 alt-filtered
        """

    @pytest.mark.skip(reason="STUB: UC-004-SF04 -- no buys match filter returns empty array (not error)")
    def test_no_match_returns_empty(self):
        """UC-004-SF04: empty result is success, not error.

        Spec: CONFIRMED -- get-media-buy-delivery-response.json media_buy_deliveries is array (can be empty)
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/get-media-buy-delivery-response.json
        Priority: P1
        Type: unit
        Source: UC-004 alt-filtered
        """


class TestDeliveryImplDateRange:
    """UC-004 alt-date-range: custom date range queries."""

    @pytest.mark.skip(reason="STUB: UC-004-DR01 -- custom date range reflected in reporting_period")
    def test_custom_date_range_in_reporting_period(self):
        """UC-004-DR01: provided start/end_date appear in response.

        Spec: CONFIRMED -- get-media-buy-delivery-request.json has start_date, end_date; response has reporting_period
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/get-media-buy-delivery-request.json
        Priority: P1
        Type: unit
        Source: UC-004 alt-date-range
        """

    @pytest.mark.skip(reason="STUB: UC-004-DR02 -- no date range defaults to last 30 days")
    def test_default_date_range_30_days(self):
        """UC-004-DR02: omitted dates default to last 30 days.

        Spec: UNSPECIFIED (implementation-defined default date range)
        Priority: P1
        Type: unit
        Source: UC-004 main flow
        """

    def test_start_after_end_returns_error(self):
        """UC-004-DR03: start >= end returns invalid_date_range error.

        Spec: UNSPECIFIED (implementation-defined date range validation)
        """
        identity = _make_identity()

        with (
            patch("src.core.tools.media_buy_delivery.get_principal_object") as mock_principal,
            patch("src.core.tools.media_buy_delivery.get_adapter") as mock_adapter,
        ):
            mock_principal.return_value = MagicMock(principal_id="test_principal")
            mock_adapter.return_value = MagicMock()

            req = GetMediaBuyDeliveryRequest(
                media_buy_ids=["mb_1"],
                start_date="2026-03-20",
                end_date="2026-03-10",
            )
            resp = _get_media_buy_delivery_impl(req, identity)

            assert isinstance(resp, GetMediaBuyDeliveryResponse)
            assert resp.errors is not None
            assert any(e.code == "invalid_date_range" for e in resp.errors)


class TestDeliveryImplErrors:
    """UC-004 extensions: auth, principal, adapter errors."""

    def test_missing_identity_raises_error(self):
        """UC-004-E01: None identity raises AdCPValidationError.

        Spec: UNSPECIFIED (implementation-defined authentication boundary)
        """
        req = GetMediaBuyDeliveryRequest(media_buy_ids=["mb_1"])
        with pytest.raises(AdCPValidationError):
            _get_media_buy_delivery_impl(req, identity=None)

    def test_principal_not_found_returns_error_response(self):
        """UC-004-E02: principal not in DB returns error in response.

        Spec: UNSPECIFIED (implementation-defined principal resolution)
        Ported from test_delivery_behavioral.py::test_principal_not_found_returns_error
        """
        identity = _make_identity()

        with patch("src.core.tools.media_buy_delivery.get_principal_object", return_value=None):
            req = GetMediaBuyDeliveryRequest(media_buy_ids=["mb_1"])
            resp = _get_media_buy_delivery_impl(req, identity)

            assert isinstance(resp, GetMediaBuyDeliveryResponse)
            assert resp.errors is not None
            assert any(e.code == "principal_not_found" for e in resp.errors)

    @pytest.mark.skip(reason="STUB: UC-004-E03 -- adapter error returns adapter_error code")
    def test_adapter_error_returns_error_code(self):
        """UC-004-E03: adapter failure returns adapter_error.

        Spec: CONFIRMED -- get-media-buy-delivery-response.json has errors array
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/get-media-buy-delivery-response.json
        Priority: P1
        Type: unit
        Source: UC-004 ext-f
        """

    @pytest.mark.skip(reason="STUB: UC-004-E04 -- ownership mismatch returns media_buy_not_found (security)")
    def test_ownership_mismatch_returns_not_found(self):
        """UC-004-E04: non-owner sees not_found, not ownership_mismatch.

        Spec: UNSPECIFIED (implementation-defined security boundary)
        Priority: P0
        Type: unit
        Source: UC-004 ext-d
        """


class TestDeliveryImplPricingLookup:
    """UC-004 pricing: salesagent-mq3n string-to-integer PK regression."""

    @pytest.mark.skip(reason="STUB: UC-004-PL01 -- pricing_option_id lookup uses string not integer PK [3.6 CRITICAL]")
    def test_pricing_option_lookup_uses_string_field(self):
        """UC-004-PL01: lookup via string pricing_option_id, not integer PK.

        Spec: CONFIRMED -- cpm-option.json pricing_option_id is type: string
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/pricing-options/cpm-option.json
        Priority: P0
        Type: unit
        Source: UC-004, salesagent-mq3n
        """

    @pytest.mark.skip(reason="STUB: UC-004-PL02 -- delivery spend correct when pricing lookup succeeds")
    def test_delivery_spend_with_correct_pricing(self):
        """UC-004-PL02: spend computed from rate and impressions.

        Spec: UNSPECIFIED (implementation-defined spend calculation)
        Priority: P0
        Type: unit
        Source: UC-004, salesagent-mq3n
        """


class TestDeliveryResponseSerialization:
    """UC-004 response serialization: nested model dump."""

    def test_response_is_serializable(self):
        """UC-004-RS01: GetMediaBuyDeliveryResponse.model_dump() succeeds.

        Spec: CONFIRMED -- get-media-buy-delivery-response.json defines the response structure
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/get-media-buy-delivery-response.json
        """
        resp = GetMediaBuyDeliveryResponse(
            reporting_period={"start": datetime.now(UTC), "end": datetime.now(UTC)},
            currency="USD",
            aggregated_totals={"impressions": 0, "spend": 0, "media_buy_count": 0},
            media_buy_deliveries=[],
        )
        dumped = resp.model_dump()
        assert "reporting_period" in dumped
        assert "media_buy_deliveries" in dumped

    @pytest.mark.skip(reason="STUB: UC-004-RS02 -- nested media_buy_deliveries model_dump works")
    def test_nested_delivery_data_serialized(self):
        """UC-004-RS02: nested MediaBuyDeliveryData serialized correctly.

        Spec: CONFIRMED -- get-media-buy-delivery-response.json has nested media_buy_deliveries
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/get-media-buy-delivery-response.json
        Priority: P1
        Type: unit
        Source: UC-004, critical pattern #4
        """


# ===========================================================================
# GET MEDIA BUYS (get_media_buys tool)
# ===========================================================================


class TestGetMediaBuysStatusComputation:
    """get_media_buys: _compute_status logic."""

    def test_pending_activation_before_start(self):
        """GMB-ST01: before start_date -> pending_activation.

        Spec: CONFIRMED -- media-buy-status.json: pending_activation
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/enums/media-buy-status.json
        Ported from test_get_media_buys.py::test_pending_activation_when_before_start
        """
        from adcp.types.generated_poc.enums.media_buy_status import MediaBuyStatus

        from src.core.tools.media_buy_list import _compute_status, _MediaBuyData

        buy = _MediaBuyData(
            media_buy_id="mb_1",
            buyer_ref=None,
            currency="USD",
            budget=Decimal("1000"),
            start_date=date.today() + timedelta(days=10),
            end_date=date.today() + timedelta(days=40),
            start_time=None,
            end_time=None,
            raw_request={},
            created_at=None,
            updated_at=None,
        )
        assert _compute_status(buy, date.today()) == MediaBuyStatus.pending_activation

    def test_active_when_in_flight(self):
        """GMB-ST02: within flight dates -> active.

        Spec: CONFIRMED -- media-buy-status.json: active
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/enums/media-buy-status.json
        Ported from test_get_media_buys.py::test_active_when_in_flight
        """
        from adcp.types.generated_poc.enums.media_buy_status import MediaBuyStatus

        from src.core.tools.media_buy_list import _compute_status, _MediaBuyData

        buy = _MediaBuyData(
            media_buy_id="mb_1",
            buyer_ref=None,
            currency="USD",
            budget=Decimal("1000"),
            start_date=date.today() - timedelta(days=5),
            end_date=date.today() + timedelta(days=25),
            start_time=None,
            end_time=None,
            raw_request={},
            created_at=None,
            updated_at=None,
        )
        assert _compute_status(buy, date.today()) == MediaBuyStatus.active

    def test_completed_when_past_end(self):
        """GMB-ST03: past end_date -> completed.

        Spec: CONFIRMED -- media-buy-status.json: completed
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/enums/media-buy-status.json
        Ported from test_get_media_buys.py::test_completed_when_past_end
        """
        from adcp.types.generated_poc.enums.media_buy_status import MediaBuyStatus

        from src.core.tools.media_buy_list import _compute_status, _MediaBuyData

        buy = _MediaBuyData(
            media_buy_id="mb_1",
            buyer_ref=None,
            currency="USD",
            budget=Decimal("1000"),
            start_date=date.today() - timedelta(days=40),
            end_date=date.today() - timedelta(days=10),
            start_time=None,
            end_time=None,
            raw_request={},
            created_at=None,
            updated_at=None,
        )
        assert _compute_status(buy, date.today()) == MediaBuyStatus.completed

    def test_prefers_start_time_over_start_date(self):
        """GMB-ST04: start_time takes precedence over start_date.

        Spec: UNSPECIFIED (implementation-defined start_time vs start_date precedence)
        Ported from test_get_media_buys.py::test_prefers_start_time_over_start_date
        """
        from adcp.types.generated_poc.enums.media_buy_status import MediaBuyStatus

        from src.core.tools.media_buy_list import _compute_status, _MediaBuyData

        # start_date is in the past, but start_time is in the future
        buy = _MediaBuyData(
            media_buy_id="mb_1",
            buyer_ref=None,
            currency="USD",
            budget=Decimal("1000"),
            start_date=date.today() - timedelta(days=5),
            end_date=date.today() + timedelta(days=25),
            start_time=datetime.now(UTC) + timedelta(days=10),
            end_time=None,
            raw_request={},
            created_at=None,
            updated_at=None,
        )
        assert _compute_status(buy, date.today()) == MediaBuyStatus.pending_activation


class TestGetMediaBuysStatusFilter:
    """get_media_buys: _resolve_status_filter logic."""

    def test_none_returns_active_only(self):
        """GMB-SF01: no filter defaults to {active}.

        Spec: UNSPECIFIED (implementation-defined default status filter)
        Ported from test_get_media_buys.py::test_none_returns_active_only
        """
        from adcp.types.generated_poc.enums.media_buy_status import MediaBuyStatus

        from src.core.tools.media_buy_list import _resolve_status_filter

        assert _resolve_status_filter(None) == {MediaBuyStatus.active}

    def test_single_status(self):
        """GMB-SF02: single status returns set of one.

        Spec: CONFIRMED -- media-buy-status.json enum values used as filter
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/enums/media-buy-status.json
        Ported from test_get_media_buys.py::test_single_status
        """
        from adcp.types.generated_poc.enums.media_buy_status import MediaBuyStatus

        from src.core.tools.media_buy_list import _resolve_status_filter

        assert _resolve_status_filter(MediaBuyStatus.completed) == {MediaBuyStatus.completed}

    def test_list_of_statuses(self):
        """GMB-SF03: list of statuses returns set of all.

        Spec: CONFIRMED -- media-buy-status.json enum values as filter list
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/enums/media-buy-status.json
        Ported from test_get_media_buys.py::test_list_of_statuses
        """
        from adcp.types.generated_poc.enums.media_buy_status import MediaBuyStatus

        from src.core.tools.media_buy_list import _resolve_status_filter

        result = _resolve_status_filter([MediaBuyStatus.active, MediaBuyStatus.completed])
        assert result == {MediaBuyStatus.active, MediaBuyStatus.completed}


class TestGetMediaBuysResponseShape:
    """get_media_buys: response serialization."""

    def test_response_is_serializable(self):
        """GMB-RS01: GetMediaBuysResponse.model_dump() succeeds.

        Spec: CONFIRMED -- media-buy.json defines media buy entity shape
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/core/media-buy.json
        Ported from test_get_media_buys.py::test_response_is_serializable
        """
        from adcp.types.generated_poc.enums.media_buy_status import MediaBuyStatus

        resp = GetMediaBuysResponse(
            media_buys=[
                GetMediaBuysMediaBuy(
                    media_buy_id="mb_1",
                    status=MediaBuyStatus.active,
                    currency="USD",
                    total_budget=5000.0,
                    packages=[
                        GetMediaBuysPackage(package_id="pkg_1"),
                    ],
                )
            ],
        )
        dumped = resp.model_dump()
        assert len(dumped["media_buys"]) == 1
        assert dumped["media_buys"][0]["media_buy_id"] == "mb_1"

    def test_nested_packages_serialized(self):
        """GMB-RS02: packages within media_buys correctly serialized.

        Spec: CONFIRMED -- media-buy.json has packages array of package.json
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/core/media-buy.json
        Ported from test_get_media_buys.py::test_nested_serialization_roundtrip
        """
        from adcp.types.generated_poc.enums.media_buy_status import MediaBuyStatus

        resp = GetMediaBuysResponse(
            media_buys=[
                GetMediaBuysMediaBuy(
                    media_buy_id="mb_1",
                    status=MediaBuyStatus.active,
                    currency="USD",
                    total_budget=5000.0,
                    packages=[
                        GetMediaBuysPackage(package_id="pkg_1", budget=2500.0, product_id="p1"),
                        GetMediaBuysPackage(package_id="pkg_2", budget=2500.0, product_id="p2"),
                    ],
                )
            ],
        )
        dumped = resp.model_dump(exclude_none=True)
        pkgs = dumped["media_buys"][0]["packages"]
        assert len(pkgs) == 2
        assert pkgs[0]["package_id"] == "pkg_1"
        assert pkgs[1]["package_id"] == "pkg_2"

    @pytest.mark.skip(reason="STUB: GMB-RS03 -- snapshot field populated when include_snapshot=true")
    def test_snapshot_populated_when_requested(self):
        """GMB-RS03: include_snapshot=true populates snapshot per package.

        Spec: UNSPECIFIED (implementation-defined snapshot feature)
        Priority: P1
        Type: unit
        Source: get_media_buys, adcp 3.6.0
        """

    @pytest.mark.skip(reason="STUB: GMB-RS04 -- creative_approvals populated in packages")
    def test_creative_approvals_populated(self):
        """GMB-RS04: creative approval status per package.

        Spec: UNSPECIFIED (implementation-defined creative approval reporting)
        Priority: P1
        Type: unit
        Source: get_media_buys
        """


class TestGetMediaBuysImplAuth:
    """get_media_buys: authentication and principal checks."""

    @pytest.mark.skip(reason="STUB: GMB-A01 -- missing context raises ToolError")
    def test_missing_context_raises_error(self):
        """GMB-A01: None ctx raises ToolError.

        Spec: UNSPECIFIED (implementation-defined authentication boundary)
        Priority: P0
        Type: unit
        Source: get_media_buys
        """

    @pytest.mark.skip(reason="STUB: GMB-A02 -- missing principal returns empty response")
    def test_missing_principal_returns_empty(self):
        """GMB-A02: no principal_id returns empty media_buys with error.

        Spec: UNSPECIFIED (implementation-defined principal resolution)
        Priority: P0
        Type: unit
        Source: get_media_buys
        """

    @pytest.mark.skip(reason="STUB: GMB-A03 -- account_id filtering not yet supported raises error")
    def test_account_id_not_supported(self):
        """GMB-A03: account_id parameter raises 'not yet supported' error.

        Spec: CONFIRMED -- account_id exists in spec (media-buy.json has account field)
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/core/media-buy.json
        Priority: P1
        Type: unit
        Source: get_media_buys
        """


# ===========================================================================
# CROSS-CUTTING: Business Rules
# ===========================================================================


class TestBRRule018AtomicResponse:
    """BR-RULE-018: success XOR error -- never both."""

    def test_create_success_has_no_errors(self):
        """BR-018-01: CreateMediaBuySuccess has no errors field.

        Spec: CONFIRMED -- create-media-buy-response.json success: not required ["errors"]
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/create-media-buy-response.json
        """
        resp = _make_success()
        dumped = resp.model_dump()
        assert dumped.get("errors") is None

    def test_create_error_has_no_media_buy_id(self):
        """BR-018-02: CreateMediaBuyError has no media_buy_id field.

        Spec: CONFIRMED -- create-media-buy-response.json error: not anyOf [media_buy_id]
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/create-media-buy-response.json
        """
        from adcp.types import Error

        resp = CreateMediaBuyError(errors=[Error(code="test", message="fail")])
        dumped = resp.model_dump()
        # media_buy_id should not be set or should be None
        assert dumped.get("media_buy_id") is None

    def test_update_success_has_no_errors(self):
        """BR-018-03: UpdateMediaBuySuccess has no errors field.

        Spec: CONFIRMED -- update-media-buy-response.json success: not required ["errors"]
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/update-media-buy-response.json
        """
        resp = UpdateMediaBuySuccess(media_buy_id="mb_1", buyer_ref="test")
        dumped = resp.model_dump()
        assert dumped.get("errors") is None

    def test_update_error_has_no_affected_packages(self):
        """BR-018-04: UpdateMediaBuyError has no affected_packages.

        Spec: CONFIRMED -- update-media-buy-response.json error: not anyOf [affected_packages]
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/update-media-buy-response.json
        """
        from adcp.types import Error

        resp = UpdateMediaBuyError(errors=[Error(code="test", message="fail")])
        dumped = resp.model_dump()
        assert dumped.get("affected_packages") is None


class TestBRRule020AdapterAtomicity:
    """BR-RULE-020: adapter success = records persisted, adapter error = no records."""

    @pytest.mark.skip(reason="STUB: BR-020-01 -- adapter success persists media buy and packages to DB")
    def test_adapter_success_persists_records(self):
        """BR-020-01: successful adapter call creates DB records.

        Spec: CONFIRMED -- create-media-buy-response.json: "media buy is either fully created or not created at all"
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/create-media-buy-response.json
        Priority: P1
        Type: integration
        Source: BR-RULE-020
        """

    @pytest.mark.skip(reason="STUB: BR-020-02 -- adapter failure leaves DB unchanged")
    def test_adapter_failure_no_db_changes(self):
        """BR-020-02: failed adapter call creates no DB records.

        Spec: CONFIRMED -- create-media-buy-response.json: "media buy is either fully created or not created at all"
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/media-buy/create-media-buy-response.json
        Priority: P0
        Type: integration
        Source: BR-RULE-020
        """


class TestBRRule043ContextEcho:
    """BR-RULE-043: context object echoed back in responses."""

    @pytest.mark.skip(reason="STUB: BR-043-01 -- create_media_buy echoes context object")
    def test_create_echoes_context(self):
        """BR-043-01: context from request appears in response.

        Spec: CONFIRMED -- context.json: "echoed unchanged in responses"; present in request and response schemas
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/core/context.json
        Priority: P1
        Type: unit
        Source: BR-RULE-043
        """

    @pytest.mark.skip(reason="STUB: BR-043-02 -- get_media_buy_delivery echoes context object")
    def test_delivery_echoes_context(self):
        """BR-043-02: context from request appears in delivery response.

        Spec: CONFIRMED -- context.json: "echoed unchanged in responses"; present in delivery response
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/core/context.json
        Priority: P1
        Type: unit
        Source: BR-RULE-043
        """

    @pytest.mark.skip(reason="STUB: BR-043-03 -- get_media_buys echoes context object")
    def test_get_media_buys_echoes_context(self):
        """BR-043-03: context from request appears in get_media_buys response.

        Spec: CONFIRMED -- context.json: "echoed unchanged in responses"
        https://github.com/adcontextprotocol/adcp/blob/8f26baf3549c00d2638341fed1d80abacb5d894a/schemas/core/context.json
        Priority: P1
        Type: unit
        Source: BR-RULE-043
        """
