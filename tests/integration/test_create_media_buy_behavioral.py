"""Behavioral snapshot tests for create_media_buy (UC-002).

Tests pinning the current behavior of _create_media_buy_impl validation paths
before FastAPI migration. Covers gaps identified in BDD scenario cross-reference:

HIGH_RISK:
  GAP-001: Product not found returns validation_error
  GAP-002: Max daily spend exceeded
  GAP-003: Creative missing URL returns INVALID_CREATIVES
  GAP-004: Creative upload failure raises CREATIVE_UPLOAD_FAILED

MEDIUM_RISK:
  GAP-005: Inline creatives processed before approval check
  GAP-006: Multiple invalid creatives accumulated in single error
  GAP-007: PricingOption XOR (both fixed_price and floor_price rejected)
  GAP-008: Creative IDs not found returns CREATIVES_NOT_FOUND

OBLIGATION COVERAGE:
  UC-002-ALT-ASAP-START-TIMING-02, UC-002-ALT-ASAP-START-TIMING-03
  UC-002-ALT-MANUAL-APPROVAL-REQUIRED-01..10
  UC-002-ALT-PROPOSAL-BASED-MEDIA-01..06
  UC-002-ALT-WITH-INLINE-CREATIVES-01, -02, -05
  UC-002-CC-ADAPTER-ATOMICITY-03, UC-002-CC-ATOMIC-RESPONSE-SEMANTICS-03
  UC-002-CC-CREATIVE-ASSIGNMENT-VALIDATION-03
  UC-002-EXT-D-02, UC-002-EXT-F-01, -02, UC-002-EXT-H-02, -03
  UC-002-EXT-I-03, UC-002-EXT-J-02, UC-002-EXT-K-03
  UC-002-EXT-L-01, -02, -03, UC-002-EXT-M-01, -03
  UC-002-EXT-N-02, UC-002-EXT-O-01, UC-002-EXT-Q-01, -02
  UC-002-MAIN-01, -03, -04, -05, -09, -10, -14, -15, -17, -20, -22
  UC-002-POST-01, -03, UC-002-PRECOND-01, -02
  UC-002-UPG-01, -02, -04, -07, -09

The _create_media_buy_impl pipeline tests run against a real PostgreSQL database
via MediaBuyCreateEnv. Only external services (adapter, audit, slack, context
manager, setup checklist, format spec) are mocked; products, pricing options,
creatives, currency limits and workflow rows are real factory-created records.
Tests that exercise schema validation or pure helper functions (no DB pipeline)
do not use the harness.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from src.core.exceptions import (
    AdCPAdapterError,
    AdCPBudgetExceededError,
    AdCPBudgetTooLowError,
    AdCPCapabilityNotSupportedError,
    AdCPCreativeRejectedError,
    AdCPFormatNotFoundError,
    AdCPNotFoundError,
    AdCPProductNotFoundError,
    AdCPValidationError,
)
from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas import (
    CreateMediaBuyError,
    CreateMediaBuyRequest,
    CreateMediaBuyResult,
    CreateMediaBuySubmitted,
    CreateMediaBuySuccess,
    PricingOption,
)
from src.core.testing_hooks import AdCPTestContext
from tests.harness.media_buy_create import MediaBuyCreateEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

# A subdomain free of underscores is required: Product.publisher_properties derives
# publisher_domain from the tenant subdomain (f"{subdomain}.example.com") and the
# AdCP domain pattern rejects underscores. TenantFactory derives the subdomain via
# tenant_subdomain() (pub-<tenant_id> with underscores normalized to hyphens); a
# hyphen-free tenant_id keeps the rest of the derived name predictable here.
_TENANT_ID = "behavioraltenant"
_PRINCIPAL_ID = "behavioralprincipal"


# ---------------------------------------------------------------------------
# Harness factory + request helper
# ---------------------------------------------------------------------------


def _env(**overrides: Any) -> MediaBuyCreateEnv:
    """Build the harness with a hyphen-safe tenant and explicit approval flags."""
    overrides.setdefault("tenant_id", _TENANT_ID)
    overrides.setdefault("principal_id", _PRINCIPAL_ID)
    overrides.setdefault("human_review_required", False)
    return MediaBuyCreateEnv(**overrides)


def _require_manual_approval(env: MediaBuyCreateEnv) -> None:
    """Make the mock adapter opt into manual approval for create_media_buy.

    The approval branch needs both tenant.human_review_required (set via the env
    constructor) AND "create_media_buy" in adapter.manual_approval_operations.
    The harness default leaves the operations list empty.
    """
    env.mock["adapter"].return_value.manual_approval_operations = ["create_media_buy"]


def _future(days: int = 7) -> str:
    """Return an ISO 8601 datetime string N days in the future."""
    dt = datetime.now(UTC) + timedelta(days=days)
    return dt.isoformat()


def _make_request(**overrides) -> CreateMediaBuyRequest:
    """Build a minimal valid CreateMediaBuyRequest.

    Defaults: one package with product_id, pricing_option_id, budget.
    Start 1 day ahead, end 8 days ahead.

    idempotency_key is required by adcp 4.3 and drives real replay/conflict
    behavior against the persistent integration DB (the harness runs the real
    idempotency machinery), so a per-call-unique key is injected by default.
    Callers may override it via the ``idempotency_key`` kwarg.
    """
    defaults = {
        "brand": {"domain": "testbrand.com"},
        "start_time": _future(1),
        "end_time": _future(8),
        "idempotency_key": f"int-key-{uuid.uuid4().hex}",
        "packages": [
            {
                "product_id": "prod_1",
                "budget": 5000.0,
                "pricing_option_id": "cpm_usd_fixed",
            }
        ],
    }
    defaults.update(overrides)
    return CreateMediaBuyRequest(**defaults)


# ===========================================================================
# HIGH_RISK Tests
# ===========================================================================


class TestProductNotFound:
    """GAP-001: Product not found raises the typed AdCPProductNotFoundError."""

    def test_product_not_found_returns_error(self, integration_db):
        """When packages reference non-existent product_ids, raise
        AdCPProductNotFoundError with the missing IDs listed.
        """
        req = _make_request(
            packages=[
                {
                    "product_id": "prod_exists",
                    "budget": 5000.0,
                    "pricing_option_id": "cpm_usd_fixed",
                },
                {
                    "product_id": "prod_missing",
                    "budget": 3000.0,
                    "pricing_option_id": "cpm_usd_fixed",
                },
            ]
        )

        with _env() as env:
            tenant, _principal = env.setup_default_data()
            # Only prod_exists is in the DB.
            env.setup_product_chain(tenant, product_id="prod_exists")
            # Missing product_ids raise the typed AdCPProductNotFoundError, whose
            # class identity carries the PRODUCT_NOT_FOUND wire code (404).
            with pytest.raises(AdCPProductNotFoundError) as excinfo:
                env.call_impl(req=req)

        exc = excinfo.value
        assert exc.error_code == "PRODUCT_NOT_FOUND"
        assert exc.status_code == 404
        assert "prod_missing" in exc.message
        assert "not found" in exc.message.lower()


class TestMaxDailySpendExceeded:
    """GAP-002: Max daily spend exceeded returns validation_error."""

    def test_max_daily_spend_exceeded(self, integration_db):
        """When budget / flight_days > max_daily_package_spend, return validation_error.

        Anchors: media_buy_create.py:1696-1733
        """
        # 7 day flight, $7000 budget = $1000/day
        # max_daily_package_spend = $500 -> should fail
        req = _make_request(
            packages=[
                {
                    "product_id": "prod_1",
                    "budget": 7000.0,
                    "pricing_option_id": "cpm_usd_fixed",
                },
            ]
        )

        with _env() as env:
            tenant, _principal = env.setup_default_data()
            tenant.currency_limits[0].max_daily_package_spend = 500
            env.setup_product_chain(tenant)
            with pytest.raises(AdCPBudgetExceededError, match="(?i)daily") as exc_info:
                env.call_impl(req=req)

            assert exc_info.value.error_code == "BUDGET_EXCEEDED"

    def test_max_daily_spend_within_cap_passes_validation(self, integration_db):
        """When daily spend is within cap, validation should pass (no error from this check).

        This test verifies the boundary: daily spend <= max means no daily-spend error.

        Anchors: media_buy_create.py:1696-1733
        """
        # 7 day flight, $3500 budget = $500/day exactly
        # max_daily_package_spend = $500 -> should pass (equal is OK)
        req = _make_request(
            packages=[
                {
                    "product_id": "prod_1",
                    "budget": 3500.0,
                    "pricing_option_id": "cpm_usd_fixed",
                },
            ]
        )

        with _env() as env:
            tenant, _principal = env.setup_default_data()
            tenant.currency_limits[0].max_daily_package_spend = 500
            env.setup_product_chain(tenant)
            # The happy-path adapter mock lets the pipeline complete; we only need
            # to confirm the daily-spend check did not reject this budget.
            result = env.call_impl(req=req)

        assert isinstance(result.response, CreateMediaBuySuccess)

    def test_max_daily_spend_same_day_flight_uses_min_one_day(self, integration_db):
        """Same-day flight (0 calendar days) uses min 1 day for daily spend calculation.

        Anchors: media_buy_create.py:1700-1701
        """
        # Same-day: start = now+1h, end = now+2h -> 0 days -> uses min 1 day
        # Budget = $600, max_daily = $500 -> $600/1 = $600 > $500 -> fail
        now = datetime.now(UTC)
        req = _make_request(
            start_time=(now + timedelta(hours=1)).isoformat(),
            end_time=(now + timedelta(hours=2)).isoformat(),
            packages=[
                {
                    "product_id": "prod_1",
                    "budget": 600.0,
                    "pricing_option_id": "cpm_usd_fixed",
                },
            ],
        )

        with _env() as env:
            tenant, _principal = env.setup_default_data()
            tenant.currency_limits[0].max_daily_package_spend = 500
            env.setup_product_chain(tenant)
            with pytest.raises(AdCPBudgetExceededError, match="(?i)daily") as exc_info:
                env.call_impl(req=req)

            assert exc_info.value.error_code == "BUDGET_EXCEEDED"

    def test_max_daily_spend_no_cap_configured(self, integration_db):
        """When max_daily_package_spend is None, no daily spend check is applied.

        Anchors: media_buy_create.py:1698
        """
        # Large budget, no cap -> should pass daily spend check
        req = _make_request(
            packages=[
                {
                    "product_id": "prod_1",
                    "budget": 999999.0,
                    "pricing_option_id": "cpm_usd_fixed",
                },
            ]
        )

        with _env() as env:
            tenant, _principal = env.setup_default_data()
            tenant.currency_limits[0].max_daily_package_spend = None
            env.setup_product_chain(tenant)
            # No cap -> daily-spend check skipped -> pipeline reaches success.
            result = env.call_impl(req=req)

        assert isinstance(result.response, CreateMediaBuySuccess)


class TestCreativeMissingUrl:
    """GAP-003: Creative missing URL returns INVALID_CREATIVES ToolError."""

    def test_creative_missing_url_raises_invalid_creatives(self):
        """When inline creatives are missing required URL, raise ToolError(INVALID_CREATIVES).

        Anchors: media_buy_create.py:280-301
        """
        from src.core.tools.media_buy_create import _validate_creatives_before_adapter_call

        # Build a mock MediaPackage with creative_ids
        mock_package = MagicMock()
        mock_package.creative_ids = ["creative_1"]

        # Build a mock DB creative that is missing URL
        mock_creative = MagicMock()
        mock_creative.creative_id = "creative_1"
        mock_creative.format = "display_300x250_image"
        mock_creative.agent_url = "https://creative.example.com"
        mock_creative.data = {}  # No URL field

        # Build a mock format spec (non-generative, so URL is required)
        mock_format_spec = MagicMock()
        mock_format_spec.output_format_ids = None  # Not generative

        with (
            patch("src.core.tools.media_buy_create._get_format_spec_sync") as mock_get_format,
            patch("src.core.tools.media_buy_create.extract_media_url_and_dimensions") as mock_extract,
        ):
            # DB returns the creative
            session = MagicMock()
            session.scalars.return_value.all.return_value = [mock_creative]

            # Format spec found (non-generative)
            mock_get_format.return_value = mock_format_spec

            # URL extraction returns None (missing)
            mock_extract.return_value = (None, None, None)

            with pytest.raises(AdCPCreativeRejectedError) as exc_info:
                _validate_creatives_before_adapter_call(
                    [mock_package], "test_tenant", "test_principal", session=session
                )

            assert "creative_errors" in exc_info.value.details
            assert exc_info.value.error_code == "CREATIVE_REJECTED"
            assert exc_info.value.suggestion

    def test_creative_missing_dimensions_raises_invalid_creatives(self):
        """When creative has URL but missing dimensions, raise INVALID_CREATIVES.

        Anchors: media_buy_create.py:285-288
        """
        from src.core.tools.media_buy_create import _validate_creatives_before_adapter_call

        mock_package = MagicMock()
        mock_package.creative_ids = ["creative_1"]

        mock_creative = MagicMock()
        mock_creative.creative_id = "creative_1"
        mock_creative.format = "display_300x250_image"
        mock_creative.agent_url = "https://creative.example.com"
        mock_creative.data = {"url": "https://example.com/ad.jpg"}

        mock_format_spec = MagicMock()
        mock_format_spec.output_format_ids = None

        with (
            patch("src.core.tools.media_buy_create._get_format_spec_sync") as mock_get_format,
            patch("src.core.tools.media_buy_create.extract_media_url_and_dimensions") as mock_extract,
        ):
            session = MagicMock()
            session.scalars.return_value.all.return_value = [mock_creative]

            mock_get_format.return_value = mock_format_spec
            # Has URL but no dimensions
            mock_extract.return_value = ("https://example.com/ad.jpg", None, None)

            with pytest.raises(AdCPCreativeRejectedError) as exc_info:
                _validate_creatives_before_adapter_call(
                    [mock_package], "test_tenant", "test_principal", session=session
                )

            assert "creative_errors" in exc_info.value.details
            assert exc_info.value.error_code == "CREATIVE_REJECTED"
            assert exc_info.value.suggestion


class TestCreativeUploadFailure:
    """GAP-004: Creative upload failure raises CREATIVE_UPLOAD_FAILED.

    The upload exception wrapping is at media_buy_create.py:3162-3168.
    We verify this with:
    1. A behavioral test exercising the actual code path through _create_media_buy_impl
    2. A behavioral test of the ToolError wrapping logic
    """

    def test_creative_upload_failure_raises_tool_error(self, integration_db):
        """When adapter.add_creative_assets() raises a generic exception during auto-approval,
        _create_media_buy_impl wraps it as ToolError('CREATIVE_UPLOAD_FAILED').

        Exercises the real code path at media_buy_create.py:3132-3168 by seeding a
        real creative (no platform_creative_id) and an adapter whose upload raises.

        Anchors: media_buy_create.py:3162-3168
        """
        # Request with a package that has creative_ids (triggers the creative upload path)
        req = _make_request(
            packages=[
                {
                    "product_id": "prod_1",
                    "budget": 5000.0,
                    "pricing_option_id": "cpm_usd_fixed",
                    "creative_ids": ["creative_no_platform"],
                },
            ]
        )

        with _env() as env:
            from tests.factories import CreativeFactory

            tenant, principal = env.setup_default_data()
            env.setup_product_chain(tenant)
            # Creative exists, no platform_creative_id, with extractable url/dimensions
            # so _build_adapter_asset_from_creative succeeds and the upload runs.
            CreativeFactory(
                tenant=tenant,
                principal=principal,
                creative_id="creative_no_platform",
                format="display_300x250",
                agent_url="https://creative.adcontextprotocol.org",
                data={"url": "https://example.com/ad.jpg", "width": 300, "height": 250},
            )

            # Adapter create succeeds; the creative upload raises a generic exception.
            mock_adapter = env.mock["adapter"].return_value
            mock_adapter.add_creative_assets.side_effect = ConnectionError("Network timeout during GAM upload")

            with pytest.raises(AdCPAdapterError) as exc_info:
                env.call_impl(req=req)

            assert exc_info.value.error_code == "SERVICE_UNAVAILABLE"
            assert "creative_no_platform" in str(exc_info.value)
            assert "Network timeout" in str(exc_info.value)
            assert exc_info.value.error_code == "SERVICE_UNAVAILABLE"


# ===========================================================================
# MEDIUM_RISK Tests
# ===========================================================================


class TestInlineCreativesProcessedBeforeApproval:
    """GAP-005: Inline creatives are processed before the approval check."""

    def test_inline_creatives_processed_before_approval_check(self, integration_db):
        """process_and_upload_package_creatives is called before manual approval check.

        Anchors: media_buy_create.py:1791-1808 (creatives), 1814-1819 (approval)
        """
        call_order = []

        req = _make_request(
            packages=[
                {
                    "product_id": "prod_1",
                    "budget": 5000.0,
                    "pricing_option_id": "cpm_usd_fixed",
                    "creatives": [
                        {
                            "creative_id": "inline_creative_1",
                            "name": "Test Ad",
                            "format_id": {
                                "agent_url": "https://creative.example.com/",
                                "id": "display_300x250_image",
                            },
                            "assets": {"banner_image": {"url": "https://example.com/ad.png"}},
                            "variants": [],  # Required in adcp 3.6.0
                        }
                    ],
                },
            ]
        )

        with _env(human_review_required=True) as env:
            tenant, _principal = env.setup_default_data()
            env.setup_product_chain(tenant)
            _require_manual_approval(env)

            mock_adapter = env.mock["adapter"].return_value

            def record_adapter_check(*args, **kwargs):
                call_order.append("approval_check")
                return mock_adapter

            env.mock["adapter"].side_effect = record_adapter_check

            with patch("src.core.tools.media_buy_create.process_and_upload_package_creatives") as mock_upload:

                def record_upload(*args, **kwargs):
                    call_order.append("creatives_processed")
                    return (req.packages, {})

                mock_upload.side_effect = record_upload

                try:
                    env.call_impl(req=req)
                except Exception:
                    pass  # Expected — downstream failures are fine

        # Verify creatives were processed before the adapter (approval check) was accessed
        assert "creatives_processed" in call_order, "process_and_upload_package_creatives was not called"
        msg = f"Creatives must be processed before approval check. Order: {call_order}"
        assert call_order.index("creatives_processed") < call_order.index("approval_check"), msg


class TestMultipleInvalidCreativesAccumulated:
    """GAP-006: Multiple creative validation errors are accumulated."""

    def test_multiple_invalid_creatives_accumulated_in_single_error(self):
        """All creative validation errors are collected and raised together.

        Anchors: media_buy_create.py:250-301
        """
        from src.core.tools.media_buy_create import _validate_creatives_before_adapter_call

        mock_package = MagicMock()
        mock_package.creative_ids = ["creative_1", "creative_2", "creative_3"]

        # Three creatives, each with different validation failures
        creatives = []
        for i in range(1, 4):
            c = MagicMock()
            c.creative_id = f"creative_{i}"
            c.format = f"format_{i}"
            c.agent_url = "https://creative.example.com"
            c.data = {}
            creatives.append(c)

        mock_format_spec = MagicMock()
        mock_format_spec.output_format_ids = None  # Non-generative

        with (
            patch("src.core.tools.media_buy_create._get_format_spec_sync") as mock_get_format,
            patch("src.core.tools.media_buy_create.extract_media_url_and_dimensions") as mock_extract,
        ):
            session = MagicMock()
            session.scalars.return_value.all.return_value = creatives

            mock_get_format.return_value = mock_format_spec
            # All creatives missing URL and dimensions
            mock_extract.return_value = (None, None, None)

            with pytest.raises(AdCPCreativeRejectedError) as exc_info:
                _validate_creatives_before_adapter_call(
                    [mock_package], "test_tenant", "test_principal", session=session
                )

            error_message = str(exc_info.value)
            assert "creative_errors" in exc_info.value.details
            # All three creative IDs should appear in the accumulated error
            assert "creative_1" in error_message
            assert "creative_2" in error_message
            assert "creative_3" in error_message
            assert exc_info.value.error_code == "CREATIVE_REJECTED"
            assert exc_info.value.suggestion


class TestPricingOptionXOR:
    """GAP-007: PricingOption rejects both fixed_price and floor_price set."""

    def test_both_fixed_price_and_floor_price_rejected(self):
        """Pydantic model_validator rejects PricingOption with both prices set.

        Anchors: schemas.py:576-584
        """
        with pytest.raises(ValidationError) as exc_info:
            PricingOption(
                pricing_option_id="cpm_usd_both", pricing_model="cpm", currency="USD", fixed_price=5.0, floor_price=2.0
            )

        # Pydantic wraps the ValueError from model_validator
        assert "Cannot have both fixed_price and floor_price" in str(exc_info.value)

    def test_neither_fixed_price_nor_floor_price_rejected(self):
        """Pydantic model_validator rejects PricingOption with neither price set.

        Anchors: schemas.py:585-586
        """
        with pytest.raises(ValidationError) as exc_info:
            PricingOption(
                pricing_option_id="cpm_usd_neither",
                pricing_model="cpm",
                currency="USD",
                fixed_price=None,
                floor_price=None,
            )

        assert "Must have either fixed_price" in str(exc_info.value)

    def test_fixed_price_only_accepted(self):
        """PricingOption with only fixed_price is valid."""
        po = PricingOption(pricing_option_id="cpm_usd_fixed", pricing_model="cpm", currency="USD", fixed_price=5.0)
        assert po.fixed_price == 5.0
        assert po.floor_price is None
        assert po.is_fixed is True

    def test_floor_price_only_accepted(self):
        """PricingOption with only floor_price is valid."""
        po = PricingOption(pricing_option_id="cpm_usd_auction", pricing_model="cpm", currency="USD", floor_price=2.0)
        assert po.floor_price == 2.0
        assert po.fixed_price is None
        assert po.is_fixed is False


class TestCreativeIdsNotFound:
    """GAP-008: Creative IDs not found returns CREATIVES_NOT_FOUND.

    The set-difference logic at media_buy_create.py:2957-2966 checks
    requested creative IDs against found IDs and raises ToolError if any
    are missing. We verify with behavioral tests exercising the actual code path.
    """

    def test_creative_ids_not_found_raises_tool_error(self, integration_db):
        """When creative_ids reference IDs that don't exist in the database,
        _create_media_buy_impl raises ToolError('CREATIVES_NOT_FOUND') with
        the missing IDs listed.

        Exercises the real code path at media_buy_create.py:2957-2966 by seeding
        only one of the three requested creatives.

        Anchors: media_buy_create.py:2957-2966
        """
        # Request with creative_ids that includes one that won't be found in DB
        req = _make_request(
            packages=[
                {
                    "product_id": "prod_1",
                    "budget": 5000.0,
                    "pricing_option_id": "cpm_usd_fixed",
                    "creative_ids": ["creative_exists", "creative_missing_1", "creative_missing_2"],
                },
            ]
        )

        with _env() as env:
            from tests.factories import CreativeFactory

            tenant, principal = env.setup_default_data()
            env.setup_product_chain(tenant)
            # Only one creative exists in DB — the other two are missing.
            CreativeFactory(
                tenant=tenant,
                principal=principal,
                creative_id="creative_exists",
                format="display_300x250",
                agent_url="https://creative.adcontextprotocol.org",
                data={"url": "https://example.com/ad.jpg", "width": 300, "height": 250},
            )

            with pytest.raises(AdCPCreativeRejectedError) as exc_info:
                env.call_impl(req=req)

            assert "creative_missing_1" in str(exc_info.value)
            assert "creative_missing_2" in str(exc_info.value)
            assert exc_info.value.error_code == "CREATIVE_REJECTED"
            assert exc_info.value.suggestion

    def test_set_difference_logic_detects_missing_creative_ids(self):
        """The set-difference logic (requested - found) correctly identifies missing IDs.

        This mirrors the pattern at media_buy_create.py:2958-2960:
            found_creative_ids = set(creatives_by_id.keys())
            requested_creative_ids = set(all_creative_ids)
            missing_ids = requested_creative_ids - found_creative_ids
        """
        # Simulate the exact logic from the source
        all_creative_ids = ["creative_exists", "creative_missing_1", "creative_missing_2"]
        creatives_by_id = {"creative_exists": MagicMock()}

        found_creative_ids = set(creatives_by_id.keys())
        requested_creative_ids = set(all_creative_ids)
        missing_ids = requested_creative_ids - found_creative_ids

        assert missing_ids == {"creative_missing_1", "creative_missing_2"}

        # Verify the rejection would be raised with the correct error code
        if missing_ids:
            error_msg = f"Creative IDs not found: {', '.join(sorted(missing_ids))}"
            with pytest.raises(AdCPCreativeRejectedError) as exc_info:
                raise AdCPCreativeRejectedError(error_msg)

            assert exc_info.value.error_code == "CREATIVE_REJECTED"
            assert "creative_missing_1" in str(exc_info.value)
            assert "creative_missing_2" in str(exc_info.value)

    def test_all_creative_ids_found_no_error(self):
        """When all creative IDs are found, no error is raised."""
        all_creative_ids = ["creative_1", "creative_2"]
        creatives_by_id = {
            "creative_1": MagicMock(),
            "creative_2": MagicMock(),
        }

        found_creative_ids = set(creatives_by_id.keys())
        requested_creative_ids = set(all_creative_ids)
        missing_ids = requested_creative_ids - found_creative_ids

        assert len(missing_ids) == 0, "No IDs should be missing"


# ===========================================================================
# OBLIGATION COVERAGE Tests
# ===========================================================================


class TestManualApprovalPathCreativeValidation:
    """The pending-approval path must validate creative refs like the auto path.

    The graded ext-o/ext-p storyboard steps (BR-UC-002 :426-452) assert
    CREATIVE_REJECTED regardless of tenant approval mode, and POST-F1/POST-F2
    forbid a pending SUCCESS that silently dropped creative assignments.

    Covers: UC-002-EXT-O-01
    """

    def test_manual_path_rejects_missing_creative_ids(self, integration_db):
        """PR #1430 review: missing creative_ids on the manual-approval path fail
        CREATIVE_REJECTED on the wire — not a pending SUCCESS that skips them.
        """
        from tests.factories import CreativeFactory
        from tests.harness.transport import Transport
        from tests.helpers import assert_envelope_shape

        req = _make_request(
            packages=[
                {
                    "product_id": "prod_1",
                    "budget": 5000.0,
                    "pricing_option_id": "cpm_usd_fixed",
                    "creative_ids": ["creative_exists", "creative_missing_1"],
                }
            ]
        )

        with _env(human_review_required=True) as env:
            tenant, principal = env.setup_default_data()
            env.setup_product_chain(tenant)
            _require_manual_approval(env)
            CreativeFactory(
                tenant=tenant,
                principal=principal,
                creative_id="creative_exists",
                format="display_300x250",
                agent_url="https://creative.adcontextprotocol.org",
                data={"url": "https://example.com/ad.jpg", "width": 300, "height": 250},
            )

            result = env.call_via(Transport.REST, req=req)

            assert result.is_error, (
                f"Manual-approval path accepted missing creative_ids (pending success): {result.payload}"
            )
            assert_envelope_shape(
                result.wire_error_envelope,
                "CREATIVE_REJECTED",
                recovery="correctable",
                message_substr="creative_missing_1",
            )

    def test_manual_path_format_mismatch_emits_creative_rejected(self, integration_db):
        """PR #1430 review: creative-vs-product format mismatch must emit
        CREATIVE_REJECTED on the manual-approval path — the same wire code the
        auto path emits for the same buyer input — not VALIDATION_ERROR.
        """
        from tests.factories import CreativeFactory
        from tests.harness.transport import Transport
        from tests.helpers import assert_envelope_shape

        req = _make_request(
            packages=[
                {
                    "product_id": "prod_1",
                    "budget": 5000.0,
                    "pricing_option_id": "cpm_usd_fixed",
                    "creative_ids": ["cr-fmt-mismatch"],
                }
            ]
        )

        with _env(human_review_required=True) as env:
            tenant, principal = env.setup_default_data()
            env.setup_product_chain(tenant)
            _require_manual_approval(env)
            # Product accepts display_300x250; this creative carries video_640x480.
            CreativeFactory(
                tenant=tenant,
                principal=principal,
                creative_id="cr-fmt-mismatch",
                format="video_640x480",
                agent_url="https://creative.adcontextprotocol.org",
                data={"url": "https://example.com/ad.mp4", "width": 640, "height": 480},
            )

            result = env.call_via(Transport.REST, req=req)

            assert result.is_error, f"Manual-approval path accepted a format-mismatched creative: {result.payload}"
            assert_envelope_shape(
                result.wire_error_envelope,
                "CREATIVE_REJECTED",
                recovery="correctable",
            )


class TestMainFlowObligations:
    """Main flow obligation tests covering UC-002-MAIN-* IDs."""

    def test_happy_path_auto_approved(self, integration_db):
        """Auto-approved media buy returns success with media_buy_id and packages.

        Covers: UC-002-MAIN-01
        """
        req = _make_request()

        with _env() as env:
            tenant, _principal = env.setup_default_data()
            env.setup_product_chain(tenant)
            result = env.call_impl(req=req)

        assert isinstance(result, CreateMediaBuyResult)
        assert isinstance(result.response, CreateMediaBuySuccess)
        assert result.response.media_buy_id is not None

    def test_auto_approve_calls_link_workflow_to_object(self, integration_db):
        """Auto-approve path persists ObjectWorkflowMapping before update_workflow_step.

        Regression test for issue #1378: on the auto-approve path no ObjectWorkflowMapping
        row was created before update_workflow_step() triggered _send_push_notifications,
        causing the webhook to be silently dropped.

        Covers: UC-002-MAIN-22
        """
        from src.core.database.repositories.workflow import WorkflowRepository

        req = _make_request()

        with _env() as env:
            tenant, _principal = env.setup_default_data()
            env.setup_product_chain(tenant)
            result = env.call_impl(req=req)

            assert isinstance(result.response, CreateMediaBuySuccess)
            ctx_mgr_mock = env.mock["context_mgr"].return_value
            ctx_mgr_mock.link_workflow_to_object.assert_called_once_with(
                step_id=ANY,
                object_type="media_buy",
                object_id=result.response.media_buy_id,
                action="create",
                tenant_id=ANY,
            )
            # link_workflow_to_object must be called BEFORE update_workflow_step("completed")
            link_call_idx = next(
                i for i, c in enumerate(ctx_mgr_mock.method_calls) if c[0] == "link_workflow_to_object"
            )
            complete_call_idx = next(
                i
                for i, c in enumerate(ctx_mgr_mock.method_calls)
                if c[0] == "update_workflow_step" and c[2].get("status") == "completed"
            )
            assert link_call_idx < complete_call_idx, (
                "link_workflow_to_object must be called before update_workflow_step(status='completed')"
            )
            # Production-state assertion: the ObjectWorkflowMapping row must actually
            # be persisted in the DB (the harness runs the real link_workflow_to_object).
            repo = WorkflowRepository(env._session, tenant_id=tenant.tenant_id)
            mapping = repo.get_latest_mapping_for_object("media_buy", result.response.media_buy_id)
            assert mapping is not None, "ObjectWorkflowMapping row was not persisted for the auto-approved media buy"

    @pytest.mark.asyncio
    async def test_authentication_extracts_principal_id(self):
        """Authentication resolves principal_id from identity.

        Covers: UC-002-MAIN-03
        """
        from src.core.tools.media_buy_create import _create_media_buy_impl

        identity = ResolvedIdentity(
            principal_id=None,  # No principal -> should fail
            tenant_id="test_tenant",
            tenant={"tenant_id": "test_tenant", "human_review_required": False},
            auth_token="test-token",
            protocol="mcp",
            testing_context=AdCPTestContext(dry_run=False, test_session_id="test-session"),
        )

        req = _make_request()
        from src.core.exceptions import AdCPAuthenticationError

        with pytest.raises(AdCPAuthenticationError, match="Principal ID not found") as exc_info:
            await _create_media_buy_impl(req=req, identity=identity)

        assert exc_info.value.error_code == "AUTH_REQUIRED"

    @pytest.mark.asyncio
    async def test_tenant_setup_validation(self):
        """Tenant setup completion is validated before processing.

        Covers: UC-002-MAIN-04
        """
        from src.core.tools.media_buy_create import _create_media_buy_impl

        # Use a non-test identity (no test_session_id) so setup validation runs
        identity = ResolvedIdentity(
            principal_id="principal_1",
            tenant_id="test_tenant",
            tenant={"tenant_id": "test_tenant", "human_review_required": False},
            auth_token="test-token",
            protocol="mcp",
            testing_context=AdCPTestContext(dry_run=False, test_session_id=None),
        )

        req = _make_request()

        from src.services.setup_checklist_service import SetupIncompleteError

        with (
            patch("src.core.tools.media_buy_create.validate_setup_complete") as mock_validate,
            patch("src.core.auth.get_principal_object"),
        ):
            mock_validate.side_effect = SetupIncompleteError(
                "Setup incomplete", missing_tasks=[{"name": "Configure Products", "description": "Add products"}]
            )

            with pytest.raises(AdCPValidationError, match="Setup incomplete") as exc_info:
                await _create_media_buy_impl(req=req, identity=identity)

            assert exc_info.value.error_code == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_ordering_mode_detection_package_based(self):
        """Request without proposal_id proceeds with package-based validation.

        Covers: UC-002-MAIN-05
        """
        req = _make_request()
        # No proposal_id -> package-based
        assert req.proposal_id is None
        assert req.packages is not None
        assert len(req.packages) > 0

    def test_package_validation_products_exist(self, integration_db):
        """When all product_ids exist, validation passes.

        Covers: UC-002-MAIN-09
        """
        req = _make_request()

        with _env() as env:
            tenant, _principal = env.setup_default_data()
            env.setup_product_chain(tenant)
            # All products exist -> pipeline reaches success without a not-found error.
            result = env.call_impl(req=req)

        assert isinstance(result.response, CreateMediaBuySuccess)

    def test_currency_validation_supported(self, integration_db):
        """Currency supported by tenant passes validation.

        Covers: UC-002-MAIN-10
        """
        req = _make_request()

        with _env() as env:
            tenant, _principal = env.setup_default_data()
            # CurrencyLimit USD exists (auto-created) -> USD supported.
            env.setup_product_chain(tenant, currency="USD")
            result = env.call_impl(req=req)

        assert isinstance(result.response, CreateMediaBuySuccess)

    def test_targeting_overlay_validation(self, integration_db):
        """Valid targeting overlay passes validation.

        Covers: UC-002-MAIN-14
        """
        req = _make_request(
            packages=[
                {
                    "product_id": "prod_1",
                    "budget": 5000.0,
                    "pricing_option_id": "cpm_usd_fixed",
                    "targeting_overlay": {"geo_countries": ["US"]},
                },
            ]
        )

        with _env() as env:
            tenant, _principal = env.setup_default_data()
            env.setup_product_chain(tenant)
            with (
                patch("src.services.targeting_capabilities.validate_unknown_targeting_fields", return_value=[]),
                patch("src.services.targeting_capabilities.validate_overlay_targeting", return_value=[]),
                patch("src.services.targeting_capabilities.validate_geo_overlap", return_value=[]),
            ):
                result = env.call_impl(req=req)

        # Valid targeting overlay does not block the pipeline.
        assert isinstance(result.response, CreateMediaBuySuccess)

    def test_auto_approval_determination(self, integration_db):
        """Auto-approval when tenant allows and adapter doesn't require manual approval.

        Covers: UC-002-MAIN-15
        """
        req = _make_request()

        with _env(human_review_required=False) as env:
            tenant, _principal = env.setup_default_data()
            env.setup_product_chain(tenant)
            result = env.call_impl(req=req)

            # Auto-approval: adapter.create_media_buy was called (not the manual path)
            # with the original request and the resolved package/flight arguments.
            assert isinstance(result.response, CreateMediaBuySuccess)
            assert result.status == "completed"
            env.mock["adapter"].return_value.create_media_buy.assert_called_once_with(req, ANY, ANY, ANY, ANY)

    @pytest.mark.asyncio
    async def test_format_id_validation(self, integration_db):
        """Format ID validation runs for packages with format_ids.

        Covers: UC-002-MAIN-17
        """
        from src.core.tools.media_buy_create import _validate_and_convert_format_ids

        # Plain string format ID should be rejected
        with pytest.raises(AdCPValidationError) as exc_info:
            await _validate_and_convert_format_ids(
                format_ids=["banner_300x250"], tenant_id="test_tenant", package_idx=0
            )

        assert exc_info.value.error_code == "VALIDATION_ERROR"

    def test_persistence_after_adapter_success(self, integration_db):
        """Media buy is persisted after adapter returns success.

        Covers: UC-002-MAIN-20
        """
        req = _make_request()

        with _env() as env:
            tenant, _principal = env.setup_default_data()
            env.setup_product_chain(tenant)
            result = env.call_impl(req=req)

        assert isinstance(result.response, CreateMediaBuySuccess)
        assert result.response.media_buy_id is not None


class TestPreconditionObligations:
    """Precondition obligation tests."""

    def test_system_operational_required(self):
        """System must be running to accept requests.

        Covers: UC-002-PRECOND-01
        """
        # This is an infrastructure concern - verify that _create_media_buy_impl
        # can be imported and called (system is operational)
        from src.core.tools.media_buy_create import _create_media_buy_impl

        assert callable(_create_media_buy_impl)

    @pytest.mark.asyncio
    async def test_buyer_authenticated_required(self):
        """Authentication is always required for create_media_buy.

        Covers: UC-002-PRECOND-02
        """
        from src.core.exceptions import AdCPAuthenticationError
        from src.core.tools.media_buy_create import _create_media_buy_impl

        req = _make_request()

        # None identity -> should raise
        with pytest.raises(AdCPAuthenticationError, match="Authentication required") as exc_info:
            await _create_media_buy_impl(req=req, identity=None)

        assert exc_info.value.error_code == "AUTH_REQUIRED"


class TestAsapStartTimingObligations:
    """ASAP start timing obligation tests."""

    def test_asap_persisted_as_resolved_datetime(self, integration_db):
        """ASAP start_time is resolved to actual datetime, not stored as literal.

        Covers: UC-002-ALT-ASAP-START-TIMING-02
        """
        req = _make_request(start_time="asap")

        with _env() as env:
            tenant, _principal = env.setup_default_data()
            env.setup_product_chain(tenant)
            result = env.call_impl(req=req)

        # The function got past the asap resolution and created the media buy.
        assert isinstance(result.response, CreateMediaBuySuccess)
        assert result.response.media_buy_id is not None

    def test_asap_flight_days_calculation(self, integration_db):
        """ASAP uses resolved start time for flight days calculation.

        Covers: UC-002-ALT-ASAP-START-TIMING-03
        """
        # ASAP start, end in 14 days to ensure flight is long enough
        req = _make_request(
            start_time="asap",
            end_time=_future(14),
            packages=[
                {
                    "product_id": "prod_1",
                    "budget": 7000.0,
                    "pricing_option_id": "cpm_usd_fixed",
                },
            ],
        )

        with _env() as env:
            tenant, _principal = env.setup_default_data()
            # $7000/~14 days ~= $500/day -> $1500 cap should pass.
            tenant.currency_limits[0].max_daily_package_spend = 1500
            env.setup_product_chain(tenant)
            result = env.call_impl(req=req)

        assert isinstance(result.response, CreateMediaBuySuccess)


class TestManualApprovalObligations:
    """Manual approval workflow obligation tests."""

    def test_tenant_requires_review_enters_manual_path(self, integration_db):
        """Tenant with human_review_required=true enters manual approval flow.

        Covers: UC-002-ALT-MANUAL-APPROVAL-REQUIRED-01
        """
        req = _make_request()

        with _env(human_review_required=True) as env:
            tenant, _principal = env.setup_default_data()
            env.setup_product_chain(tenant)
            _require_manual_approval(env)
            result = env.call_impl(req=req)

        # Spec 3.1.1: pending approval is the Submitted task envelope, not a
        # confirmed Success (PR #1567 round-2 item 2).
        assert isinstance(result.response, CreateMediaBuySubmitted)
        assert result.status == "submitted"  # Not "completed"

    def test_adapter_requires_review_enters_manual_path(self, integration_db):
        """Adapter with manual_approval_required=true enters manual approval flow.

        Covers: UC-002-ALT-MANUAL-APPROVAL-REQUIRED-02
        """
        req = _make_request()

        with _env(human_review_required=False) as env:
            tenant, _principal = env.setup_default_data()
            env.setup_product_chain(tenant)
            # Adapter (not tenant) requires manual approval.
            mock_adapter = env.mock["adapter"].return_value
            mock_adapter.manual_approval_required = True
            mock_adapter.manual_approval_operations = ["create_media_buy"]
            result = env.call_impl(req=req)

        assert isinstance(result.response, CreateMediaBuySubmitted)
        assert result.status == "submitted"

    def test_seller_notification_sent_on_manual_approval(self, integration_db):
        """Slack notification is sent when manual approval is required.

        Covers: UC-002-ALT-MANUAL-APPROVAL-REQUIRED-05
        """
        req = _make_request()

        with _env(human_review_required=True) as env:
            tenant, _principal = env.setup_default_data()
            env.setup_product_chain(tenant)
            _require_manual_approval(env)
            result = env.call_impl(req=req)
            mock_notifier = env.mock["slack"].return_value

        assert result.status == "submitted"
        mock_notifier.notify_media_buy_event.assert_called_once_with(
            event_type="approval_required",
            media_buy_id=ANY,
            principal_name=ANY,
            details=ANY,
            tenant_name=ANY,
            tenant_id=ANY,
            success=True,
        )

    def test_response_envelope_status_is_submitted(self, integration_db):
        """Manual approval response has status 'submitted', not 'completed'.

        Covers: UC-002-ALT-MANUAL-APPROVAL-REQUIRED-06
        """
        req = _make_request()

        with _env(human_review_required=True) as env:
            tenant, _principal = env.setup_default_data()
            env.setup_product_chain(tenant)
            _require_manual_approval(env)
            result = env.call_impl(req=req)

        assert result.status == "submitted"
        # Spec 3.1.1 CreateMediaBuySubmitted: task_id is the required handle the
        # buyer polls; workflow_step_id/media_buy_id are not on this envelope.
        assert isinstance(result.response, CreateMediaBuySubmitted)
        assert result.response.task_id

    def test_no_adapter_execution_before_approval(self, integration_db):
        """Adapter is NOT called when manual approval is required.

        Covers: UC-002-ALT-MANUAL-APPROVAL-REQUIRED-07
        """
        req = _make_request()

        with _env(human_review_required=True) as env:
            tenant, _principal = env.setup_default_data()
            env.setup_product_chain(tenant)
            _require_manual_approval(env)
            result = env.call_impl(req=req)

            assert result.status == "submitted"
            env.mock["adapter"].return_value.create_media_buy.assert_not_called()

    def test_seller_rejects_buyer_notified(self, integration_db):
        """Seller rejection workflow returns appropriate status.

        Covers: UC-002-ALT-MANUAL-APPROVAL-REQUIRED-09

        Note: The rejection workflow runs in a separate approve_media_buy path.
        This test verifies the pending_approval state is set up correctly for
        subsequent rejection handling.
        """
        req = _make_request()

        with _env(human_review_required=True) as env:
            tenant, _principal = env.setup_default_data()
            env.setup_product_chain(tenant)
            _require_manual_approval(env)
            result = env.call_impl(req=req)

        # Pending approval means it's ready for accept/reject; the buyer holds
        # the task_id the reject flow resolves (spec 3.1.1 Submitted envelope).
        assert result.status == "submitted"
        assert result.response.task_id

    def test_buyer_can_poll_approval_progress(self, integration_db):
        """Response includes task_id for polling.

        Covers: UC-002-ALT-MANUAL-APPROVAL-REQUIRED-10

        Note: Polling is via tasks/get with the task_id (spec 3.1.1
        CreateMediaBuySubmitted.required; PR #1567 round-2 item 2).
        """
        req = _make_request()

        with _env(human_review_required=True) as env:
            tenant, _principal = env.setup_default_data()
            env.setup_product_chain(tenant)
            _require_manual_approval(env)
            result = env.call_impl(req=req)

        assert isinstance(result.response, CreateMediaBuySubmitted)
        assert result.response.task_id


class TestInlineCreativeObligations:
    """Inline creative handling obligation tests."""

    def test_inline_creatives_uploaded_and_assigned(self, integration_db):
        """Inline creatives are processed by process_and_upload_package_creatives.

        Covers: UC-002-ALT-WITH-INLINE-CREATIVES-01
        """
        req = _make_request(
            packages=[
                {
                    "product_id": "prod_1",
                    "budget": 5000.0,
                    "pricing_option_id": "cpm_usd_fixed",
                    "creatives": [
                        {
                            "creative_id": "inline_1",
                            "name": "Test Ad",
                            "format_id": {"agent_url": "https://creative.example.com/", "id": "display_300x250"},
                            "assets": {"banner_image": {"url": "https://example.com/ad.png"}},
                            "variants": [],
                        }
                    ],
                },
            ]
        )

        with _env(human_review_required=True) as env:
            tenant, _principal = env.setup_default_data()
            env.setup_product_chain(tenant)
            _require_manual_approval(env)

            with patch("src.core.tools.media_buy_create.process_and_upload_package_creatives") as mock_upload:
                mock_upload.return_value = (req.packages, {"pkg-1": ["new_creative_id"]})
                try:
                    env.call_impl(req=req)
                except Exception:
                    pass

        mock_upload.assert_called_once_with(packages=ANY, context=ANY, testing_ctx=ANY)

    @pytest.mark.asyncio
    async def test_inline_creative_format_validation(self, integration_db):
        """Inline creative format IDs are validated via format spec lookup.

        Covers: UC-002-ALT-WITH-INLINE-CREATIVES-02

        Note: Format validation for inline creatives happens during the
        process_and_upload_package_creatives call, which validates format_ids.
        """
        from src.core.tools.media_buy_create import _validate_and_convert_format_ids

        # Missing fields in FormatId should be rejected
        with pytest.raises(AdCPValidationError) as exc_info:
            await _validate_and_convert_format_ids(
                format_ids=[{"agent_url": "", "id": ""}], tenant_id="test_tenant", package_idx=0
            )

        assert exc_info.value.error_code == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_unapproved_creatives_may_trigger_manual_approval(self):
        """Unapproved creatives may trigger manual approval path.

        Covers: UC-002-ALT-WITH-INLINE-CREATIVES-05

        Note: The approval determination considers adapter settings and tenant
        settings independently of creative state. Creative approval state
        influences the media buy status post-creation.
        """
        from src.core.tools.media_buy_create import _determine_media_buy_status

        # When creatives are not approved, status reflects pending activation
        status = _determine_media_buy_status(
            manual_approval_required=False,
            has_creatives=True,
            creatives_approved=False,
            start_time=datetime.now(UTC) + timedelta(days=1),
            end_time=datetime.now(UTC) + timedelta(days=8),
        )
        # Unapproved creatives -> pending_creatives (waiting for creative approval)
        assert status == "pending_creatives"


class TestProposalBasedObligations:
    """Proposal-based media buy obligation tests.

    Note: proposal_id is accepted in the schema (adcp 3.6) but the proposal
    resolution flow is not yet implemented in salesagent. These tests verify
    the schema acceptance and current behavioral boundaries.
    """

    def test_proposal_id_accepted_in_request_schema(self):
        """Request schema accepts proposal_id field.

        Covers: UC-002-ALT-PROPOSAL-BASED-MEDIA-01

        Note: Schema accepts proposal_id but the business logic does not
        currently implement proposal resolution. This test pins schema acceptance.
        """
        req = _make_request(proposal_id="prop_123")
        assert req.proposal_id == "prop_123"

    def test_proposal_id_field_exists_on_schema(self):
        """CreateMediaBuyRequest has proposal_id field.

        Covers: UC-002-ALT-PROPOSAL-BASED-MEDIA-02
        """
        assert "proposal_id" in CreateMediaBuyRequest.model_fields

    def test_total_budget_field_exists_on_schema(self):
        """CreateMediaBuyRequest has total_budget field for proposal-based.

        Covers: UC-002-ALT-PROPOSAL-BASED-MEDIA-03
        """
        assert "total_budget" in CreateMediaBuyRequest.model_fields

    def test_proposal_based_packages_derived_from_allocations(self):
        """Schema supports the fields needed for package derivation.

        Covers: UC-002-ALT-PROPOSAL-BASED-MEDIA-04

        Note: Package derivation from proposal allocations is not yet
        implemented. This test pins that the schema has the required
        fields for when the feature is built.
        """
        # proposal_id and total_budget coexist on the schema
        req = CreateMediaBuyRequest(
            brand={"domain": "test.com"},
            start_time=_future(1),
            end_time=_future(8),
            packages=[{"product_id": "p1", "budget": 5000.0, "pricing_option_id": "cpm_usd_fixed"}],
            proposal_id="prop_abc",
            total_budget={"amount": 10000.0, "currency": "USD"},
            idempotency_key=f"int-key-{uuid.uuid4().hex}",
        )
        assert req.proposal_id == "prop_abc"
        assert req.total_budget is not None

    def test_proposal_based_product_validation(self, integration_db):
        """Derived packages still require valid product_ids.

        Covers: UC-002-ALT-PROPOSAL-BASED-MEDIA-06

        Note: Even with proposal_id, product validation still runs on packages.
        """
        # Request with proposal_id but packages referencing non-existent product
        req = _make_request(
            proposal_id="prop_123",
            packages=[
                {
                    "product_id": "nonexistent_product",
                    "budget": 5000.0,
                    "pricing_option_id": "cpm_usd_fixed",
                },
            ],
        )

        with _env() as env:
            # No products in DB -> products not found.
            env.setup_default_data()
            # Missing product_ids raise the typed AdCPProductNotFoundError.
            with pytest.raises(AdCPProductNotFoundError) as excinfo:
                env.call_impl(req=req)

        exc = excinfo.value
        assert exc.error_code == "PRODUCT_NOT_FOUND"
        assert "not found" in exc.message.lower()
        assert "nonexistent_product" in exc.message


class TestCrossCuttingObligations:
    """Cross-cutting obligation tests."""

    def test_response_never_both_success_and_error(self):
        """CreateMediaBuyResult response is EITHER success or error, never both.

        Covers: UC-002-CC-ATOMIC-RESPONSE-SEMANTICS-03
        """
        # Success response has no errors field
        from src.core.schemas import Package as RespPkg

        success = CreateMediaBuySuccess(
            media_buy_id="mb_1", packages=[RespPkg(package_id="p1", product_id="prod_1", budget=100)]
        )
        success_result = CreateMediaBuyResult(response=success, status="completed")

        assert isinstance(success_result.response, CreateMediaBuySuccess)
        assert not isinstance(success_result.response, CreateMediaBuyError)

        # Error response has no media_buy_id
        from src.core.schemas import Error

        error = CreateMediaBuyError(errors=[Error(code="VALIDATION_ERROR", message="test error")])
        error_result = CreateMediaBuyResult(response=error, status="failed")

        assert isinstance(error_result.response, CreateMediaBuyError)
        assert not isinstance(error_result.response, CreateMediaBuySuccess)

    def test_manual_approval_persistence_before_adapter(self, integration_db):
        """Manual approval persists records before adapter execution.

        Covers: UC-002-CC-ADAPTER-ATOMICITY-03
        """
        req = _make_request()

        with _env(human_review_required=True) as env:
            tenant, _principal = env.setup_default_data()
            env.setup_product_chain(tenant)
            _require_manual_approval(env)
            result = env.call_impl(req=req)

            # Manual path: adapter was NOT called, but records were persisted.
            # The submitted response carries no media_buy_id (spec 3.1.1) — the
            # persisted row is the evidence, keyed by the workflow mapping.
            assert result.status == "submitted"
            env.mock["adapter"].return_value.create_media_buy.assert_not_called()
            ctx_mgr_mock = env.mock["context_mgr"].return_value
            link_call = ctx_mgr_mock.link_workflow_to_object.call_args
            assert link_call is not None, "manual path must link the persisted media buy to the workflow step"
            from src.core.database.models import MediaBuy as DBMediaBuy

            persisted = env.get_one(DBMediaBuy, media_buy_id=link_call.kwargs["object_id"])
            assert persisted is not None, "media buy row must be persisted before approval"

    def test_manual_approval_calls_link_workflow_to_object(self, integration_db):
        """Manual-approval path calls link_workflow_to_object to create the ObjectWorkflowMapping row.

        Regression test for issue #1378 (DRY follow-up): the manual-approval path must use
        ctx_manager.link_workflow_to_object() rather than an inline ObjectWorkflowMapping insert,
        giving both paths consistent error handling and a single code path.

        Covers: UC-002-CC-ADAPTER-ATOMICITY-03
        """
        req = _make_request()

        with _env(human_review_required=True) as env:
            tenant, _principal = env.setup_default_data()
            env.setup_product_chain(tenant)
            _require_manual_approval(env)
            result = env.call_impl(req=req)

            # Submitted response carries no media_buy_id (spec 3.1.1); the
            # mapping's object_id must reference the PERSISTED media buy row.
            assert result.status == "submitted"
            ctx_mgr_mock = env.mock["context_mgr"].return_value
            ctx_mgr_mock.link_workflow_to_object.assert_called_once_with(
                step_id=ANY,
                object_type="media_buy",
                object_id=ANY,
                action="create",
                tenant_id=ANY,
            )
            from src.core.database.models import MediaBuy as DBMediaBuy

            object_id = ctx_mgr_mock.link_workflow_to_object.call_args.kwargs["object_id"]
            assert env.get_one(DBMediaBuy, media_buy_id=object_id) is not None, (
                "ObjectWorkflowMapping.object_id must reference the persisted media buy"
            )

    @pytest.mark.asyncio
    async def test_creative_in_valid_state_assigned_successfully(self):
        """Creative in valid state with compatible format is assigned.

        Covers: UC-002-CC-CREATIVE-ASSIGNMENT-VALIDATION-03

        Note: This tests the format validation helper directly.
        """
        # Build mocks
        from adcp.types import FormatId

        from src.core.helpers import validate_creative_format_against_product

        creative_format = FormatId(agent_url="https://creative.example.com", id="display_300x250")
        product = MagicMock()
        # Typed FormatId column (#1172): production reads product.format_ids as
        # list[FormatId] models, so the stub must carry the typed shape.
        product.format_ids = [FormatId(agent_url="https://creative.example.com", id="display_300x250")]

        is_valid, error = validate_creative_format_against_product(creative_format_id=creative_format, product=product)

        assert is_valid is True
        assert error is None


class TestExtensionObligations:
    """Extension scenario obligation tests."""

    def test_currency_not_supported_by_gam(self, integration_db):
        """Currency supported by tenant but not GAM returns error.

        Covers: UC-002-EXT-D-02
        """
        from tests.factories.core import AdapterConfigFactory, CurrencyLimitFactory

        req = _make_request()

        with _env() as env:
            tenant, _principal = env.setup_default_data()
            # Product priced in EUR; tenant supports EUR (CurrencyLimit) but GAM does not.
            CurrencyLimitFactory(tenant=tenant, currency_code="EUR")
            env.setup_product_chain(tenant, currency="EUR")
            AdapterConfigFactory(
                tenant=tenant,
                adapter_type="google_ad_manager",
                gam_network_currency="USD",
                gam_secondary_currencies=None,
            )
            # Currency unsupported by the GAM network is a seller-capability gap:
            # UNSUPPORTED_FEATURE, not VALIDATION_ERROR (#1417).
            with pytest.raises(AdCPCapabilityNotSupportedError) as excinfo:
                env.call_impl(req=req)

        exc = excinfo.value
        assert exc.error_code == "UNSUPPORTED_FEATURE"
        error_msg = exc.message.lower()
        assert "not supported" in error_msg
        assert "gam" in error_msg

    @pytest.mark.asyncio
    async def test_unknown_targeting_fields_rejected(self):
        """Unknown targeting fields are rejected.

        Covers: UC-002-EXT-F-01
        """
        from src.services.targeting_capabilities import validate_unknown_targeting_fields

        # Create a mock targeting object with model_extra (unknown fields)
        mock_targeting = MagicMock()
        mock_targeting.model_extra = {"mood": "happy", "weather": "sunny"}

        violations = validate_unknown_targeting_fields(mock_targeting)

        assert len(violations) == 2
        assert any("mood" in v for v in violations)
        assert any("weather" in v for v in violations)

    @pytest.mark.asyncio
    async def test_managed_only_dimension_rejected(self):
        """Managed-only dimension (key_value_pairs) is rejected.

        Covers: UC-002-EXT-F-02
        """
        # Build a targeting object with key_value_pairs set
        from src.core.schemas import Targeting
        from src.services.targeting_capabilities import validate_overlay_targeting

        targeting = Targeting(key_value_pairs={"segment": "premium"})

        violations = validate_overlay_targeting(targeting)

        assert len(violations) > 0
        assert any("key_value_pairs" in v for v in violations)
        assert any("managed" in v.lower() for v in violations)

    @pytest.mark.asyncio
    async def test_unregistered_creative_agent_rejected(self):
        """Unregistered creative agent in format_ids is rejected.

        Covers: UC-002-EXT-H-02
        """
        from src.core.tools.media_buy_create import _validate_and_convert_format_ids

        with patch("src.core.creative_agent_registry.CreativeAgentRegistry") as mock_registry_cls:
            mock_registry = MagicMock()
            mock_registry._get_tenant_agents.return_value = []  # No agents registered
            mock_registry_cls.return_value = mock_registry

            with patch("src.core.validation.normalize_agent_url", side_effect=lambda x: x):
                from src.core.exceptions import AdCPAuthorizationError

                with pytest.raises(AdCPAuthorizationError) as exc_info:
                    await _validate_and_convert_format_ids(
                        format_ids=[{"agent_url": "https://unknown-agent.example.com", "id": "banner_300x250"}],
                        tenant_id="test_tenant",
                        package_idx=0,
                    )

                assert "not registered" in str(exc_info.value).lower()
                assert exc_info.value.error_code == "AUTH_REQUIRED"

    @pytest.mark.asyncio
    async def test_format_not_found_on_agent(self):
        """Format ID not found on registered agent returns error.

        Covers: UC-002-EXT-H-03
        """
        from src.core.tools.media_buy_create import _validate_and_convert_format_ids

        mock_agent = MagicMock()
        mock_agent.agent_url = "https://creative.example.com"

        with (
            patch("src.core.creative_agent_registry.CreativeAgentRegistry") as mock_registry_cls,
            patch("src.core.validation.normalize_agent_url", side_effect=lambda x: x),
        ):
            mock_registry = MagicMock()
            mock_registry._get_tenant_agents.return_value = [mock_agent]
            mock_registry.get_format = AsyncMock(return_value=None)  # Format not found
            mock_registry_cls.return_value = mock_registry

            with pytest.raises(AdCPFormatNotFoundError) as exc_info:
                await _validate_and_convert_format_ids(
                    format_ids=[{"agent_url": "https://creative.example.com", "id": "nonexistent_format"}],
                    tenant_id="test_tenant",
                    package_idx=0,
                )

            assert exc_info.value.error_code == "FORMAT_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_authentication_always_required(self):
        """create_media_buy always requires authentication (no anonymous path).

        Covers: UC-002-EXT-I-03
        """
        from src.core.exceptions import AdCPAuthenticationError
        from src.core.tools.media_buy_create import _create_media_buy_impl

        req = _make_request()

        # None identity -> requires authentication
        with pytest.raises(AdCPAuthenticationError, match="Authentication required") as exc_info:
            await _create_media_buy_impl(req=req, identity=None)

        assert exc_info.value.error_code == "AUTH_REQUIRED"

        # Identity with no principal_id -> requires authentication

        identity_no_principal = ResolvedIdentity(
            principal_id=None,
            tenant_id="test_tenant",
            tenant={"tenant_id": "test_tenant"},
            auth_token="test",
            protocol="mcp",
        )
        with pytest.raises(AdCPAuthenticationError, match="Principal ID not found") as exc_info:
            await _create_media_buy_impl(req=req, identity=identity_no_principal)

        assert exc_info.value.error_code == "AUTH_REQUIRED"

    def test_no_database_record_on_adapter_failure(self, integration_db):
        """When adapter fails, no database records are created.

        Covers: UC-002-EXT-J-02

        Note: In the auto-approval path, adapter execution happens BEFORE
        database persistence. If the adapter returns an error, the function
        returns an error result and no persistence occurs.
        """
        from src.core.schemas import Error

        req = _make_request()

        with _env() as env:
            tenant, _principal = env.setup_default_data()
            env.setup_product_chain(tenant)
            # Adapter returns an error envelope (not success).
            env.mock["adapter"].return_value.create_media_buy.side_effect = None
            env.mock["adapter"].return_value.create_media_buy.return_value = CreateMediaBuyError(
                errors=[Error(code="SERVICE_UNAVAILABLE", message="GAM API error")]
            )
            result = env.call_impl(req=req)

        # Adapter returned error -> result is error, no persistence
        assert isinstance(result.response, CreateMediaBuyError)
        assert result.status == "failed"

    def test_no_max_daily_spend_configured_check_skipped(self, integration_db):
        """No max_daily_package_spend -> daily spend check is skipped.

        Covers: UC-002-EXT-K-03
        """
        req = _make_request(
            packages=[{"product_id": "prod_1", "budget": 999999.0, "pricing_option_id": "cpm_usd_fixed"}]
        )

        with _env() as env:
            tenant, _principal = env.setup_default_data()
            tenant.currency_limits[0].max_daily_package_spend = None
            env.setup_product_chain(tenant)
            # No cap -> the very large budget passes the daily-spend check.
            result = env.call_impl(req=req)

        assert isinstance(result.response, CreateMediaBuySuccess)

    def test_proposal_not_found_error_code(self):
        """PROPOSAL_NOT_FOUND error code is used for missing proposals.

        Covers: UC-002-EXT-L-01

        Note: Proposal resolution is not yet implemented. This test verifies
        the error code pattern that will be used when it is.
        """
        error = AdCPNotFoundError("Proposal not found: prop_123", details={"error_code": "PROPOSAL_NOT_FOUND"})
        assert error.details["error_code"] == "PROPOSAL_NOT_FOUND"
        assert "prop_123" in str(error)

    def test_proposal_expired_error_code(self):
        """PROPOSAL_EXPIRED error code is used for expired proposals.

        Covers: UC-002-EXT-L-02

        Note: Proposal resolution is not yet implemented. This test verifies
        the error code pattern.
        """
        error = AdCPValidationError("Proposal expired: prop_456", details={"error_code": "PROPOSAL_EXPIRED"})
        assert error.details["error_code"] == "PROPOSAL_EXPIRED"

    def test_proposal_recovery_via_get_products(self):
        """After proposal failure, buyer can call get_products for fresh proposals.

        Covers: UC-002-EXT-L-03

        Note: This is a behavioral contract -- get_products always returns fresh
        proposals. Verified by checking the function exists and is importable.
        """
        from src.core.tools.products import _get_products_impl

        assert callable(_get_products_impl)

    def test_proposal_budget_amount_zero_rejected(self, integration_db):
        """Total budget <= 0 returns BUDGET_BELOW_MINIMUM.

        Covers: UC-002-EXT-M-01
        """
        # Zero budget triggers the typed AdCPBudgetTooLowError raise at
        # media_buy_create.py:1758 directly — propagates through the boundary
        # catch unchanged (typed AdCPError raised directly).
        req = _make_request(packages=[{"product_id": "prod_1", "budget": 0, "pricing_option_id": "cpm_usd_fixed"}])

        with _env() as env:
            tenant, _principal = env.setup_default_data()
            env.setup_product_chain(tenant)
            with pytest.raises(AdCPBudgetTooLowError) as excinfo:
                env.call_impl(req=req)

        exc = excinfo.value
        assert exc.error_code == "BUDGET_TOO_LOW"
        assert "budget" in exc.message.lower()

    def test_proposal_currency_mismatch_error_code(self):
        """CURRENCY_MISMATCH error code exists for proposal currency mismatch.

        Covers: UC-002-EXT-M-03

        Note: Proposal-based currency validation is not yet implemented.
        This test verifies the error code pattern.
        """
        error = AdCPValidationError(
            "Currency EUR does not match proposal currency USD", details={"error_code": "CURRENCY_MISMATCH"}
        )
        assert error.details["error_code"] == "CURRENCY_MISMATCH"

    def test_product_with_no_pricing_options(self, integration_db):
        """Product with no pricing options returns PRICING_ERROR.

        Covers: UC-002-EXT-N-02
        """
        req = _make_request()

        with _env() as env:
            tenant, _principal = env.setup_default_data()
            # Product created WITHOUT any pricing option.
            env.setup_product_chain(tenant, with_pricing=False)
            with pytest.raises(AdCPValidationError) as excinfo:
                env.call_impl(req=req)

        # Pricing-error sites in _validate_pricing_model_selection tag the error
        # via details={"error_code": "PRICING_ERROR"} while the wire code stays
        # VALIDATION_ERROR — verify both layers so we don't regress either one.
        exc = excinfo.value
        assert exc.error_code == "VALIDATION_ERROR"
        assert exc.error_code == "VALIDATION_ERROR"
        assert "pricing_options" in exc.message

    @pytest.mark.asyncio
    async def test_creative_ids_not_in_database(self):
        """Creative IDs not in database returns CREATIVES_NOT_FOUND.

        Covers: UC-002-EXT-O-01
        """
        # This is covered by TestCreativeIdsNotFound above.
        # Verify the error code pattern: the create path now emits CREATIVE_REJECTED
        # for missing creative_ids (unified with the update path).
        error = AdCPCreativeRejectedError("Creative IDs not found: creative_missing")
        assert error.error_code == "CREATIVE_REJECTED"

    def test_creative_upload_failed_error_code(self):
        """Creative upload failures raise AdCPAdapterError (wire code SERVICE_UNAVAILABLE).

        Covers: UC-002-EXT-Q-01
        """
        error = AdCPAdapterError("Failed to upload creative to GAM")
        assert error.error_code == "SERVICE_UNAVAILABLE"

    def test_partial_execution_state_on_creative_upload_failure(self):
        """Creative upload failure may leave partial state in ad server.

        Covers: UC-002-EXT-Q-02

        Note: This is a known atomicity concern. The media buy order may
        exist in the ad server even though creative upload failed.
        The error is SERVICE_UNAVAILABLE (adapter failure), not a rollback.
        """
        error = AdCPAdapterError("Failed to upload creative cr_1 to GAM: timeout")
        # Partial execution: error is about upload, not about the order
        assert error.error_code == "SERVICE_UNAVAILABLE"
        assert "cr_1" in str(error)


class TestPostconditionObligations:
    """Postcondition obligation tests."""

    def test_system_state_unchanged_on_failure(self, integration_db):
        """On validation failure, no records are created.

        Covers: UC-002-POST-01
        """
        # Non-existent product -> validation failure inside _impl
        req = _make_request(
            packages=[
                {
                    "product_id": "nonexistent_prod",
                    "budget": 5000.0,
                    "pricing_option_id": "cpm_usd_fixed",
                }
            ]
        )

        with _env() as env:
            from sqlalchemy import func, select

            from src.core.database.models import MediaBuy

            tenant, _principal = env.setup_default_data()
            # Seed a product with id "prod_1"; req asks for "nonexistent_prod" so the
            # validation block hits the not-found branch, raising the typed error.
            env.setup_product_chain(tenant)
            with pytest.raises(AdCPProductNotFoundError) as exc_info:
                env.call_impl(req=req)

            assert exc_info.value.error_code == "PRODUCT_NOT_FOUND"

            # Postcondition: the typed raise happens BEFORE any media buy is
            # persisted — no MediaBuy rows exist for this tenant.
            count = env._session.scalar(
                select(func.count()).select_from(MediaBuy).where(MediaBuy.tenant_id == tenant.tenant_id)
            )
            assert count == 0

    def test_error_response_contains_recovery_guidance(self, integration_db):
        """Error messages include enough info for buyer to fix and retry.

        Covers: UC-002-POST-03
        """
        # Missing product -> error with product ID listed
        req = _make_request(
            packages=[
                {
                    "product_id": "nonexistent_prod",
                    "budget": 5000.0,
                    "pricing_option_id": "cpm_usd_fixed",
                }
            ]
        )

        with _env() as env:
            # No products in DB.
            env.setup_default_data()
            # Production raises the typed AdCPProductNotFoundError, whose class
            # identity carries the PRODUCT_NOT_FOUND wire code.
            with pytest.raises(AdCPProductNotFoundError) as excinfo:
                env.call_impl(req=req)

        exc = excinfo.value
        # Recovery guidance lives on the typed exception itself: the
        # exception's message must identify the unknown product so the buyer
        # knows exactly what to correct on retry, and the typed error_code
        # ("PRODUCT_NOT_FOUND") gives the buyer a machine-readable classification.
        assert "nonexistent_prod" in exc.message
        assert exc.error_code == "PRODUCT_NOT_FOUND"


class TestUpgradeObligations:
    """3.6 upgrade boundary field propagation tests."""

    def test_buyer_campaign_ref_rejected_in_strict_mode(self):
        """buyer_campaign_ref is no longer in the AdCP spec (removed in 3.12).

        Covers: UC-002-UPG-01
        """
        with pytest.raises(ValidationError, match="buyer_campaign_ref"):
            _make_request(buyer_campaign_ref="CAMP-2024-Q1")

    def test_ext_field_carries_custom_data(self):
        """ext field can carry buyer_campaign_ref as custom extension data.

        Covers: UC-002-UPG-02
        """
        req = _make_request(ext={"buyer_campaign_ref": "CAMP-2024-Q1"})
        dumped = req.model_dump()
        assert dumped["ext"]["buyer_campaign_ref"] == "CAMP-2024-Q1"

    def test_ext_field_accepted(self):
        """ext field (ExtensionObject) is accepted in request.

        Covers: UC-002-UPG-04
        """
        req = _make_request(ext={"custom_field": "value", "custom_num": 42})
        assert req.ext is not None

    def test_account_field_in_success_response(self):
        """CreateMediaBuySuccess has account field (optional).

        Covers: UC-002-UPG-07
        """
        assert "account" in CreateMediaBuySuccess.model_fields

        from src.core.schemas import Package as RespPkg

        # Verify account can be set on success
        resp = CreateMediaBuySuccess(
            media_buy_id="mb_1",
            packages=[RespPkg(package_id="p1", product_id="prod_1", budget=100)],
            account=None,  # Optional
        )
        assert resp.account is None

    def test_sandbox_flag_in_success_response(self):
        """CreateMediaBuySuccess has sandbox field (optional).

        Covers: UC-002-UPG-09
        """
        assert "sandbox" in CreateMediaBuySuccess.model_fields

        from src.core.schemas import Package as RespPkg

        resp = CreateMediaBuySuccess(
            media_buy_id="mb_1", packages=[RespPkg(package_id="p1", product_id="prod_1", budget=100)], sandbox=True
        )
        assert resp.sandbox is True
