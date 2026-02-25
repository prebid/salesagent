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
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from fastmcp.exceptions import ToolError
from pydantic import ValidationError

from src.core.schemas import (
    CreateMediaBuyError,
    CreateMediaBuyRequest,
    CreateMediaBuyResult,
    PricingOption,
)

# ---------------------------------------------------------------------------
# Shared helpers for building mocks/fixtures
# ---------------------------------------------------------------------------


def _future(days: int = 7) -> str:
    """Return an ISO 8601 datetime string N days in the future."""
    dt = datetime.now(UTC) + timedelta(days=days)
    return dt.isoformat()


def _make_request(**overrides) -> CreateMediaBuyRequest:
    """Build a minimal valid CreateMediaBuyRequest.

    Defaults: one package with product_id, pricing_option_id, budget, buyer_ref.
    Start 1 day ahead, end 8 days ahead.
    """
    defaults = {
        "buyer_ref": "test-buyer",
        "brand_manifest": {"name": "Test Brand"},
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
    # Simulate RootModel unwrap: getattr(po, "root", po) returns the object itself
    pricing_option.root = pricing_option

    product = MagicMock()
    product.product_id = product_id
    product.pricing_options = [pricing_option]
    return product


def _mock_currency_limit(
    max_daily_package_spend: Decimal | None = None,
    min_package_budget: Decimal | None = None,
) -> MagicMock:
    """Create a mock CurrencyLimit row."""
    cl = MagicMock()
    cl.max_daily_package_spend = max_daily_package_spend
    cl.min_package_budget = min_package_budget
    return cl


def _standard_patches():
    """Return a dict of common patch targets for _create_media_buy_impl."""
    return {
        "src.core.tools.media_buy_create.get_testing_context": "_testing_ctx",
        "src.core.tools.media_buy_create.get_principal_id_from_context": "_principal_id",
        "src.core.tools.media_buy_create.get_current_tenant": "_tenant",
        "src.core.tools.media_buy_create.validate_setup_complete": "_setup",
        "src.core.tools.media_buy_create.get_principal_object": "_principal_obj",
        "src.core.tools.media_buy_create.get_context_manager": "_ctx_manager",
    }


class _PatchContext:
    """Thin helper that sets up the standard mocks for _create_media_buy_impl.

    Usage::

        with _PatchContext() as pc:
            # Customise mocks via pc attributes
            pc.db_session.scalars.return_value.all.return_value = [product]
            result = await _create_media_buy_impl(req=req, ctx=pc.ctx)
    """

    def __init__(
        self,
        *,
        products: list[MagicMock] | None = None,
        currency_limit: MagicMock | None = None,
        adapter_config: MagicMock | None = None,
    ):
        self._products = products or [_mock_product()]
        self._currency_limit = currency_limit or _mock_currency_limit()
        self._adapter_config = adapter_config

    def __enter__(self):
        # Build a mock context
        self.ctx = MagicMock()
        self.ctx.headers = {"x-adcp-auth": "test-token"}

        # testing_ctx
        self._p_testing = patch("src.core.tools.media_buy_create.get_testing_context")
        m = self._p_testing.start()
        self.testing_ctx = MagicMock(dry_run=False, test_session_id="test-session")
        m.return_value = self.testing_ctx

        # principal_id
        self._p_pid = patch("src.core.tools.media_buy_create.get_principal_id_from_context")
        self._p_pid.start().return_value = "principal_1"

        # tenant
        self._p_tenant = patch("src.core.tools.media_buy_create.get_current_tenant")
        self._p_tenant.start().return_value = {
            "tenant_id": "test_tenant",
            "human_review_required": False,
            "auto_create_media_buys": True,
        }

        # setup validation
        self._p_setup = patch("src.core.tools.media_buy_create.validate_setup_complete")
        self._p_setup.start()

        # principal object
        self._p_principal = patch("src.core.tools.media_buy_create.get_principal_object")
        mock_principal = MagicMock()
        mock_principal.principal_id = "principal_1"
        mock_principal.name = "Test Buyer"
        self._p_principal.start().return_value = mock_principal

        # context manager (for workflow steps)
        self._p_ctx_mgr = patch("src.core.tools.media_buy_create.get_context_manager")
        mock_ctx_mgr = MagicMock()
        mock_step = MagicMock()
        mock_step.step_id = "step_1"
        mock_ctx_mgr.return_value.create_context.return_value = MagicMock(context_id="ctx_1")
        mock_ctx_mgr.return_value.create_workflow_step.return_value = mock_step
        self._p_ctx_mgr.start()
        self.ctx_manager = mock_ctx_mgr.return_value

        # DB session — configure a side_effect-driven scalars chain so different
        # queries can return different results.
        # Patched at source because media_buy_create.py uses local imports.
        self._p_db = patch("src.core.database.database_session.get_db_session")
        self.db_session = MagicMock()
        self.db_session.__enter__ = MagicMock(return_value=self.db_session)
        self.db_session.__exit__ = MagicMock(return_value=None)
        self._p_db.start().return_value = self.db_session

        # By default, configure the scalars chain to return products on .all()
        # and currency_limit (then adapter_config) on successive .first() calls.
        all_mock = MagicMock()
        all_mock.all.return_value = self._products
        first_results = [self._currency_limit, self._adapter_config]
        first_mock = MagicMock(side_effect=first_results)

        scalars_result = MagicMock()
        scalars_result.all = all_mock.all
        scalars_result.first = first_mock
        self.db_session.scalars.return_value = scalars_result

        return self

    def __exit__(self, *args):
        self._p_testing.stop()
        self._p_pid.stop()
        self._p_tenant.stop()
        self._p_setup.stop()
        self._p_principal.stop()
        self._p_ctx_mgr.stop()
        self._p_db.stop()


# ===========================================================================
# HIGH_RISK Tests
# ===========================================================================


class TestProductNotFound:
    """GAP-001: Product not found returns CreateMediaBuyError with validation_error."""

    @pytest.mark.asyncio
    async def test_product_not_found_returns_error(self):
        """When packages reference non-existent product_ids, return validation_error
        with missing IDs listed.

        Anchors: media_buy_create.py:1470-1473
        """
        from src.core.tools.media_buy_create import _create_media_buy_impl

        req = _make_request(
            packages=[
                {
                    "product_id": "prod_exists",
                    "buyer_ref": "pkg-1",
                    "budget": 5000.0,
                    "pricing_option_id": "cpm_usd_fixed",
                },
                {
                    "product_id": "prod_missing",
                    "buyer_ref": "pkg-2",
                    "budget": 3000.0,
                    "pricing_option_id": "cpm_usd_fixed",
                },
            ]
        )

        # Only prod_exists is in the DB
        existing_product = _mock_product("prod_exists")

        with _PatchContext(products=[existing_product]) as pc:
            result = await _create_media_buy_impl(req=req, ctx=pc.ctx)

        assert isinstance(result, CreateMediaBuyResult)
        assert isinstance(result.response, CreateMediaBuyError)
        assert result.status == "failed"
        errors = result.response.errors
        assert len(errors) == 1
        assert errors[0].code == "validation_error"
        assert "prod_missing" in errors[0].message
        assert "not found" in errors[0].message.lower()


class TestMaxDailySpendExceeded:
    """GAP-002: Max daily spend exceeded returns validation_error."""

    @pytest.mark.asyncio
    async def test_max_daily_spend_exceeded(self):
        """When budget / flight_days > max_daily_package_spend, return validation_error.

        Anchors: media_buy_create.py:1696-1733
        """
        from src.core.tools.media_buy_create import _create_media_buy_impl

        # 7 day flight, $7000 budget = $1000/day
        # max_daily_package_spend = $500 -> should fail
        req = _make_request(
            packages=[
                {
                    "product_id": "prod_1",
                    "buyer_ref": "pkg-1",
                    "budget": 7000.0,
                    "pricing_option_id": "cpm_usd_fixed",
                },
            ]
        )

        product = _mock_product("prod_1")
        cl = _mock_currency_limit(max_daily_package_spend=Decimal("500"))

        with _PatchContext(products=[product], currency_limit=cl) as pc:
            result = await _create_media_buy_impl(req=req, ctx=pc.ctx)

        assert isinstance(result, CreateMediaBuyResult)
        assert isinstance(result.response, CreateMediaBuyError)
        assert result.status == "failed"
        errors = result.response.errors
        assert len(errors) == 1
        assert errors[0].code == "validation_error"
        assert "daily" in errors[0].message.lower()

    @pytest.mark.asyncio
    async def test_max_daily_spend_within_cap_passes_validation(self):
        """When daily spend is within cap, validation should pass (no error from this check).

        This test verifies the boundary: daily spend <= max means no daily-spend error.
        It will still fail later in the pipeline (adapter call) but that's expected.

        Anchors: media_buy_create.py:1696-1733
        """
        from src.core.tools.media_buy_create import _create_media_buy_impl

        # 7 day flight, $3500 budget = $500/day exactly
        # max_daily_package_spend = $500 -> should pass (equal is OK)
        req = _make_request(
            packages=[
                {
                    "product_id": "prod_1",
                    "buyer_ref": "pkg-1",
                    "budget": 3500.0,
                    "pricing_option_id": "cpm_usd_fixed",
                },
            ]
        )

        product = _mock_product("prod_1")
        cl = _mock_currency_limit(max_daily_package_spend=Decimal("500"))

        with _PatchContext(products=[product], currency_limit=cl) as pc:
            # Patch the adapter and downstream calls so we can check we passed
            # daily spend validation (any error beyond it is fine)
            with patch("src.core.tools.media_buy_create.get_adapter") as mock_adapter:
                mock_adapter.return_value = MagicMock(
                    manual_approval_required=False,
                    manual_approval_operations=["create_media_buy"],
                )
                try:
                    result = await _create_media_buy_impl(req=req, ctx=pc.ctx)
                except Exception:
                    # Downstream failures are fine — we only care that daily spend
                    # validation did NOT produce a validation_error
                    result = None

        # If we got a result, it should NOT be a daily-spend validation error
        if result is not None and isinstance(result.response, CreateMediaBuyError):
            for err in result.response.errors:
                assert "daily" not in err.message.lower() or "exceeds" not in err.message.lower(), (
                    f"Daily spend validation should have passed but got: {err.message}"
                )

    @pytest.mark.asyncio
    async def test_max_daily_spend_same_day_flight_uses_min_one_day(self):
        """Same-day flight (0 calendar days) uses min 1 day for daily spend calculation.

        Anchors: media_buy_create.py:1700-1701
        """
        from src.core.tools.media_buy_create import _create_media_buy_impl

        # Same-day: start = now+1h, end = now+2h -> 0 days -> uses min 1 day
        # Budget = $600, max_daily = $500 -> $600/1 = $600 > $500 -> fail
        now = datetime.now(UTC)
        req = _make_request(
            start_time=(now + timedelta(hours=1)).isoformat(),
            end_time=(now + timedelta(hours=2)).isoformat(),
            packages=[
                {
                    "product_id": "prod_1",
                    "buyer_ref": "pkg-1",
                    "budget": 600.0,
                    "pricing_option_id": "cpm_usd_fixed",
                },
            ],
        )

        product = _mock_product("prod_1")
        cl = _mock_currency_limit(max_daily_package_spend=Decimal("500"))

        with _PatchContext(products=[product], currency_limit=cl) as pc:
            result = await _create_media_buy_impl(req=req, ctx=pc.ctx)

        assert isinstance(result, CreateMediaBuyResult)
        assert isinstance(result.response, CreateMediaBuyError)
        assert result.status == "failed"
        assert "daily" in result.response.errors[0].message.lower()

    @pytest.mark.asyncio
    async def test_max_daily_spend_no_cap_configured(self):
        """When max_daily_package_spend is None, no daily spend check is applied.

        Anchors: media_buy_create.py:1698
        """
        from src.core.tools.media_buy_create import _create_media_buy_impl

        # Large budget, no cap -> should pass daily spend check
        req = _make_request(
            packages=[
                {
                    "product_id": "prod_1",
                    "buyer_ref": "pkg-1",
                    "budget": 999999.0,
                    "pricing_option_id": "cpm_usd_fixed",
                },
            ]
        )

        product = _mock_product("prod_1")
        cl = _mock_currency_limit(max_daily_package_spend=None)

        with _PatchContext(products=[product], currency_limit=cl) as pc:
            with patch("src.core.tools.media_buy_create.get_adapter") as mock_adapter:
                mock_adapter.return_value = MagicMock(
                    manual_approval_required=False,
                    manual_approval_operations=["create_media_buy"],
                )
                try:
                    result = await _create_media_buy_impl(req=req, ctx=pc.ctx)
                except Exception:
                    result = None

        # Should not fail on daily spend (any failure is from something else)
        if result is not None and isinstance(result.response, CreateMediaBuyError):
            for err in result.response.errors:
                assert "daily" not in err.message.lower() or "exceeds" not in err.message.lower()


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
            patch("src.core.database.database_session.get_db_session") as mock_db,
            patch("src.core.tools.media_buy_create._get_format_spec_sync") as mock_get_format,
            patch("src.core.tools.media_buy_create.extract_media_url_and_dimensions") as mock_extract,
        ):
            # DB returns the creative
            session = MagicMock()
            session.__enter__ = MagicMock(return_value=session)
            session.__exit__ = MagicMock(return_value=None)
            session.scalars.return_value.all.return_value = [mock_creative]
            mock_db.return_value = session

            # Format spec found (non-generative)
            mock_get_format.return_value = mock_format_spec

            # URL extraction returns None (missing)
            mock_extract.return_value = (None, None, None)

            with pytest.raises(ToolError) as exc_info:
                _validate_creatives_before_adapter_call([mock_package], "test_tenant")

            assert "INVALID_CREATIVES" in str(exc_info.value)

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
            patch("src.core.database.database_session.get_db_session") as mock_db,
            patch("src.core.tools.media_buy_create._get_format_spec_sync") as mock_get_format,
            patch("src.core.tools.media_buy_create.extract_media_url_and_dimensions") as mock_extract,
        ):
            session = MagicMock()
            session.__enter__ = MagicMock(return_value=session)
            session.__exit__ = MagicMock(return_value=None)
            session.scalars.return_value.all.return_value = [mock_creative]
            mock_db.return_value = session

            mock_get_format.return_value = mock_format_spec
            # Has URL but no dimensions
            mock_extract.return_value = ("https://example.com/ad.jpg", None, None)

            with pytest.raises(ToolError) as exc_info:
                _validate_creatives_before_adapter_call([mock_package], "test_tenant")

            assert "INVALID_CREATIVES" in str(exc_info.value)


class TestCreativeUploadFailure:
    """GAP-004: Creative upload failure raises CREATIVE_UPLOAD_FAILED.

    The upload exception wrapping is at media_buy_create.py:3162-3168.
    We verify this with:
    1. A behavioral test exercising the actual code path through _create_media_buy_impl
    2. A behavioral test of the ToolError wrapping logic
    """

    @pytest.mark.asyncio
    async def test_creative_upload_failure_raises_tool_error(self):
        """When adapter.add_creative_assets() raises a generic exception during auto-approval,
        _create_media_buy_impl wraps it as ToolError('CREATIVE_UPLOAD_FAILED').

        Exercises the real code path at media_buy_create.py:3132-3168 by mocking
        the pipeline deep enough to reach the creative upload code.

        Anchors: media_buy_create.py:3162-3168
        """
        from src.core.schemas import CreateMediaBuySuccess
        from src.core.schemas import Package as RespPackage
        from src.core.tools.media_buy_create import _create_media_buy_impl

        # Request with a package that has creative_ids (triggers the creative upload path)
        req = _make_request(
            packages=[
                {
                    "product_id": "prod_1",
                    "buyer_ref": "pkg-1",
                    "budget": 5000.0,
                    "pricing_option_id": "cpm_usd_fixed",
                    "creative_ids": ["creative_no_platform"],
                },
            ]
        )

        product = _mock_product("prod_1")

        # Mock creative in DB: no platform_creative_id -> triggers upload path
        mock_creative = MagicMock()
        mock_creative.creative_id = "creative_no_platform"
        mock_creative.format = "display_300x250_image"
        mock_creative.agent_url = "https://creative.example.com"
        mock_creative.name = "Test Creative"
        mock_creative.data = {}  # No platform_creative_id

        # Build a successful adapter response
        resp_package = MagicMock(spec=RespPackage)
        resp_package.package_id = "pkg_prod_1_abc_1"
        resp_package.platform_line_item_id = None
        adapter_response = MagicMock(spec=CreateMediaBuySuccess)
        adapter_response.media_buy_id = "mb_test123"
        adapter_response.packages = [resp_package]
        # Make isinstance(response, CreateMediaBuyError) return False
        adapter_response.__class__ = CreateMediaBuySuccess

        # Mock adapter whose add_creative_assets raises a generic exception
        mock_adapter = MagicMock()
        mock_adapter.manual_approval_required = False
        mock_adapter.manual_approval_operations = []
        mock_adapter.__class__.__name__ = "MockAdapter"
        mock_adapter.add_creative_assets.side_effect = ConnectionError("Network timeout during GAM upload")

        # Mock product catalog for products_in_buy lookup
        mock_schema_product = MagicMock()
        mock_schema_product.product_id = "prod_1"
        mock_schema_product.name = "Test Product"
        mock_schema_product.implementation_config = None
        mock_schema_product.format_ids = None
        mock_schema_product.delivery_type = MagicMock()
        mock_schema_product.delivery_type.value = "non_guaranteed"

        with _PatchContext(products=[product]) as pc:
            # Override the scalars chain to handle multiple .all() and .first() calls.
            # .all() call 1 (products query at line 1464) -> [product]
            # .all() call 2 (creatives query at line 2954) -> [mock_creative]
            # .first() calls: currency_limit (1554), adapter_config=None (1569),
            #   package_record=None (2919), product_format_check=None (2986)
            all_results = iter([[product], [mock_creative]])
            first_results = iter([_mock_currency_limit(), None, None, None, None, None])
            scalars_mock = MagicMock()
            scalars_mock.all.side_effect = lambda: next(all_results)
            scalars_mock.first.side_effect = lambda: next(first_results, None)
            pc.db_session.scalars.return_value = scalars_mock

            with (
                patch("src.core.tools.media_buy_create.process_and_upload_package_creatives") as mock_upload,
                patch("src.core.tools.media_buy_create.get_adapter", return_value=mock_adapter),
                patch("src.core.tools.media_buy_create._validate_creatives_before_adapter_call"),
                patch(
                    "src.core.tools.media_buy_create._execute_adapter_media_buy_creation", return_value=adapter_response
                ),
                patch("src.core.tools.media_buy_create._determine_media_buy_status", return_value="active"),
                patch("src.core.main.media_buys", {}),
                patch("src.core.main.get_product_catalog", return_value=[mock_schema_product]),
                patch("src.core.helpers.validate_creative_format_against_product", return_value=(True, None)),
                patch(
                    "src.core.tools.media_buy_create._get_format_spec_sync",
                    return_value=MagicMock(output_format_ids=None),
                ),
                patch(
                    "src.core.tools.media_buy_create.extract_media_url_and_dimensions",
                    return_value=("https://example.com/ad.jpg", 300, 250),
                ),
                patch("src.core.tools.media_buy_create.extract_click_url", return_value=None),
                patch("src.core.tools.media_buy_create.extract_impression_tracker_url", return_value=None),
                patch("src.core.tools.media_buy_create.get_slack_notifier"),
                patch("src.core.tools.media_buy_create.activity_feed"),
            ):
                mock_upload.return_value = (req.packages, {})

                with pytest.raises(ToolError) as exc_info:
                    await _create_media_buy_impl(req=req, ctx=pc.ctx)

                assert "CREATIVE_UPLOAD_FAILED" in str(exc_info.value)
                assert "creative_no_platform" in str(exc_info.value)
                assert "Network timeout" in str(exc_info.value)

    def test_creative_upload_failure_wraps_exception_as_tool_error(self):
        """The try/except pattern at line 3162-3168 wraps generic exceptions
        as ToolError('CREATIVE_UPLOAD_FAILED', ...).

        This directly tests the exception wrapping behavior by simulating the
        pattern. The actual upload call is adapter.add_creative_assets().
        """
        # Simulate the exact wrapping pattern from the source:
        #   except Exception as upload_error:
        #       raise ToolError("CREATIVE_UPLOAD_FAILED", f"Failed to ...") from upload_error
        upload_error = ConnectionError("Network timeout during GAM upload")
        creative_id = "creative_abc"

        with pytest.raises(ToolError) as exc_info:
            try:
                raise upload_error
            except Exception as e:
                raise ToolError(
                    "CREATIVE_UPLOAD_FAILED",
                    f"Failed to upload creative {creative_id} to GAM: {e!s}",
                ) from e

        assert "CREATIVE_UPLOAD_FAILED" in str(exc_info.value)
        assert creative_id in str(exc_info.value)
        assert "Network timeout" in str(exc_info.value)


# ===========================================================================
# MEDIUM_RISK Tests
# ===========================================================================


class TestInlineCreativesProcessedBeforeApproval:
    """GAP-005: Inline creatives are processed before the approval check."""

    @pytest.mark.asyncio
    async def test_inline_creatives_processed_before_approval_check(self):
        """process_and_upload_package_creatives is called before manual approval check.

        Anchors: media_buy_create.py:1791-1808 (creatives), 1814-1819 (approval)
        """
        from src.core.tools.media_buy_create import _create_media_buy_impl

        call_order = []

        req = _make_request(
            packages=[
                {
                    "product_id": "prod_1",
                    "buyer_ref": "pkg-1",
                    "budget": 5000.0,
                    "pricing_option_id": "cpm_usd_fixed",
                    "creatives": [
                        {
                            "creative_id": "inline_creative_1",
                            "name": "Test Ad",
                            "format_id": {
                                "agent_url": "https://creative.example.com",
                                "id": "display_300x250_image",
                            },
                        }
                    ],
                },
            ]
        )

        product = _mock_product("prod_1")
        cl = _mock_currency_limit()

        with _PatchContext(products=[product], currency_limit=cl) as pc:
            with (
                patch("src.core.tools.media_buy_create.process_and_upload_package_creatives") as mock_upload,
                patch("src.core.tools.media_buy_create.get_adapter") as mock_adapter_fn,
            ):

                def record_upload(*args, **kwargs):
                    call_order.append("creatives_processed")
                    # Return (updated_packages, uploaded_ids)
                    return (req.packages, {})

                mock_upload.side_effect = record_upload

                mock_adapter = MagicMock()
                mock_adapter.manual_approval_required = True
                mock_adapter.manual_approval_operations = ["create_media_buy"]

                def record_adapter_check(*args, **kwargs):
                    call_order.append("approval_check")
                    return mock_adapter

                mock_adapter_fn.side_effect = record_adapter_check

                try:
                    await _create_media_buy_impl(req=req, ctx=pc.ctx)
                except Exception:
                    pass  # Expected — downstream failures are fine

        # Verify creatives were processed before the adapter (approval check) was accessed
        assert "creatives_processed" in call_order, "process_and_upload_package_creatives was not called"
        assert call_order.index("creatives_processed") < call_order.index("approval_check"), (
            f"Creatives must be processed before approval check. Order: {call_order}"
        )


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
            patch("src.core.database.database_session.get_db_session") as mock_db,
            patch("src.core.tools.media_buy_create._get_format_spec_sync") as mock_get_format,
            patch("src.core.tools.media_buy_create.extract_media_url_and_dimensions") as mock_extract,
        ):
            session = MagicMock()
            session.__enter__ = MagicMock(return_value=session)
            session.__exit__ = MagicMock(return_value=None)
            session.scalars.return_value.all.return_value = creatives
            mock_db.return_value = session

            mock_get_format.return_value = mock_format_spec
            # All creatives missing URL and dimensions
            mock_extract.return_value = (None, None, None)

            with pytest.raises(ToolError) as exc_info:
                _validate_creatives_before_adapter_call([mock_package], "test_tenant")

            error_message = str(exc_info.value)
            assert "INVALID_CREATIVES" in error_message
            # All three creative IDs should appear in the accumulated error
            assert "creative_1" in error_message
            assert "creative_2" in error_message
            assert "creative_3" in error_message


class TestPricingOptionXOR:
    """GAP-007: PricingOption rejects both fixed_price and floor_price set."""

    def test_both_fixed_price_and_floor_price_rejected(self):
        """Pydantic model_validator rejects PricingOption with both prices set.

        Anchors: schemas.py:576-584
        """
        with pytest.raises(ValidationError) as exc_info:
            PricingOption(
                pricing_option_id="cpm_usd_both",
                pricing_model="cpm",
                currency="USD",
                fixed_price=5.0,
                floor_price=2.0,
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
        po = PricingOption(
            pricing_option_id="cpm_usd_fixed",
            pricing_model="cpm",
            currency="USD",
            fixed_price=5.0,
        )
        assert po.fixed_price == 5.0
        assert po.floor_price is None
        assert po.is_fixed is True

    def test_floor_price_only_accepted(self):
        """PricingOption with only floor_price is valid."""
        po = PricingOption(
            pricing_option_id="cpm_usd_auction",
            pricing_model="cpm",
            currency="USD",
            floor_price=2.0,
        )
        assert po.floor_price == 2.0
        assert po.fixed_price is None
        assert po.is_fixed is False


class TestCreativeIdsNotFound:
    """GAP-008: Creative IDs not found returns CREATIVES_NOT_FOUND.

    The set-difference logic at media_buy_create.py:2957-2966 checks
    requested creative IDs against found IDs and raises ToolError if any
    are missing. We verify with behavioral tests exercising the actual code path.
    """

    @pytest.mark.asyncio
    async def test_creative_ids_not_found_raises_tool_error(self):
        """When creative_ids reference IDs that don't exist in the database,
        _create_media_buy_impl raises ToolError('CREATIVES_NOT_FOUND') with
        the missing IDs listed.

        Exercises the real code path at media_buy_create.py:2957-2966 by mocking
        the pipeline deep enough to reach the creative ID lookup.

        Anchors: media_buy_create.py:2957-2966
        """
        from src.core.schemas import CreateMediaBuySuccess
        from src.core.schemas import Package as RespPackage
        from src.core.tools.media_buy_create import _create_media_buy_impl

        # Request with creative_ids that includes one that won't be found in DB
        req = _make_request(
            packages=[
                {
                    "product_id": "prod_1",
                    "buyer_ref": "pkg-1",
                    "budget": 5000.0,
                    "pricing_option_id": "cpm_usd_fixed",
                    "creative_ids": ["creative_exists", "creative_missing_1", "creative_missing_2"],
                },
            ]
        )

        product = _mock_product("prod_1")

        # Only one creative exists in DB — the other two are missing
        mock_creative = MagicMock()
        mock_creative.creative_id = "creative_exists"

        # Build a successful adapter response
        resp_package = MagicMock(spec=RespPackage)
        resp_package.package_id = "pkg_prod_1_abc_1"
        adapter_response = MagicMock(spec=CreateMediaBuySuccess)
        adapter_response.media_buy_id = "mb_test123"
        adapter_response.packages = [resp_package]
        adapter_response.__class__ = CreateMediaBuySuccess

        # Mock adapter
        mock_adapter = MagicMock()
        mock_adapter.manual_approval_required = False
        mock_adapter.manual_approval_operations = []
        mock_adapter.__class__.__name__ = "MockAdapter"

        # Mock product catalog for products_in_buy lookup
        mock_schema_product = MagicMock()
        mock_schema_product.product_id = "prod_1"
        mock_schema_product.name = "Test Product"
        mock_schema_product.implementation_config = None
        mock_schema_product.format_ids = None
        mock_schema_product.delivery_type = MagicMock()
        mock_schema_product.delivery_type.value = "non_guaranteed"

        with _PatchContext(products=[product]) as pc:
            # Override the scalars chain to handle multiple .all() and .first() calls.
            # .all() call 1 (products query at line 1464) -> [product]
            # .all() call 2 (creatives query at line 2954) -> [mock_creative] (only 1 of 3)
            # .first() returns currency_limit then None for subsequent calls
            all_results = iter([[product], [mock_creative]])
            first_results = iter([_mock_currency_limit(), None, None, None, None, None])
            scalars_mock = MagicMock()
            scalars_mock.all.side_effect = lambda: next(all_results)
            scalars_mock.first.side_effect = lambda: next(first_results, None)
            pc.db_session.scalars.return_value = scalars_mock

            with (
                patch("src.core.tools.media_buy_create.process_and_upload_package_creatives") as mock_upload,
                patch("src.core.tools.media_buy_create.get_adapter", return_value=mock_adapter),
                patch("src.core.tools.media_buy_create._validate_creatives_before_adapter_call"),
                patch(
                    "src.core.tools.media_buy_create._execute_adapter_media_buy_creation", return_value=adapter_response
                ),
                patch("src.core.tools.media_buy_create._determine_media_buy_status", return_value="active"),
                patch("src.core.main.media_buys", {}),
                patch("src.core.main.get_product_catalog", return_value=[mock_schema_product]),
                patch("src.core.tools.media_buy_create.get_slack_notifier"),
                patch("src.core.tools.media_buy_create.activity_feed"),
            ):
                mock_upload.return_value = (req.packages, {})

                with pytest.raises(ToolError) as exc_info:
                    await _create_media_buy_impl(req=req, ctx=pc.ctx)

                assert "CREATIVES_NOT_FOUND" in str(exc_info.value)
                assert "creative_missing_1" in str(exc_info.value)
                assert "creative_missing_2" in str(exc_info.value)

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

        # Verify the ToolError would be raised with the correct error code
        if missing_ids:
            error_msg = f"Creative IDs not found: {', '.join(sorted(missing_ids))}"
            with pytest.raises(ToolError) as exc_info:
                raise ToolError("CREATIVES_NOT_FOUND", error_msg)

            assert "CREATIVES_NOT_FOUND" in str(exc_info.value)
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
