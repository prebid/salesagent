"""Unit tests for the GAM adapter's three create-path outcomes.

Each of manual approval, activation workflow and the plain success path must hand the
tool back the packages it was asked to place — the same package ids, none dropped or
duplicated — plus, where line items were created, the GAM line-item id per package.

This file used to say it proved the adapter "returns packages with package_id, fixing
the 'Adapter did not return package_id' error". That bug is no longer gradable: the
carrier split made ``AdapterCreateResult.packages`` a required ``list[ResponsePackage]``
with a required ``package_id``, so pydantic refuses a result that lacks them.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, Mock, patch

import pytest

from src.adapters.base import AdapterCreateRequest
from src.adapters.google_ad_manager import GoogleAdManager
from src.core.schemas import CreateMediaBuyRequest, FormatId, MediaPackage, PackageRequest


@pytest.fixture
def mock_principal():
    """Mock principal for testing."""
    principal = Mock()
    principal.name = "test_principal"
    principal.principal_id = "principal_123"
    return principal


@pytest.fixture
def mock_gam_config():
    """Mock GAM configuration."""
    return {
        "network_code": "123456",
        "advertiser_id": "789",
        "trafficker_id": "456",
        "refresh_token": "test_token",
        "manual_approval_operations": ["create_media_buy"],  # Enable manual approval
    }


@pytest.fixture
def sample_request():
    """What the create path hands the adapter.

    An adapter takes the CARRIER, not the buyer's ``CreateMediaBuyRequest`` — so this
    goes through the production projection rather than building one by hand, which is
    also how ``total_budget`` gets summed the way the tool sums it.
    """
    start_time = datetime.now(UTC)
    end_time = start_time + timedelta(days=30)
    # adcp 3.6.0: brand_manifest → brand (BrandReference with domain field)
    return AdapterCreateRequest.from_buyer_request(
        CreateMediaBuyRequest(
            account={"account_id": "acct_test"},
            brand={"domain": "testbrand.com"},
            idempotency_key="unit-test-key-gamwf-0001",
            packages=[
                PackageRequest(product_id="prod_123", budget=5000.0, pricing_option_id="test_pricing"),
                PackageRequest(product_id="prod_456", budget=5000.0, pricing_option_id="test_pricing"),
            ],
            start_time=start_time,
            end_time=end_time,
        )
    )


@pytest.fixture
def sample_packages():
    """Sample packages list."""
    return [
        MediaPackage(
            package_id="pkg_001",
            name="Package 1",
            delivery_type="guaranteed",
            impressions=10000,
            cpm=5.0,
            format_ids=[FormatId(agent_url="https://test.com", id="display_300x250")],
        ),
        MediaPackage(
            package_id="pkg_002",
            name="Package 2",
            delivery_type="guaranteed",
            impressions=20000,
            cpm=7.5,
            format_ids=[FormatId(agent_url="https://test.com", id="display_728x90")],
        ),
    ]


def _build_gam_adapter(mock_principal):
    """Construct a real GoogleAdManager with the OAuth client manager patched out.

    Shared by the create-path raise-site test classes below so the adapter wiring
    lives in one place.
    """
    config = {
        "network_code": "123456",
        "advertiser_id": "789",
        "trafficker_id": "456",
        "refresh_token": "test_token",
    }
    with patch("src.adapters.google_ad_manager.GAMClientManager") as mock_client_manager:
        mock_client_manager.return_value.get_client.return_value = Mock()
        return GoogleAdManager(
            config=config,
            principal=mock_principal,
            network_code="123456",
            advertiser_id="789",
            trafficker_id="456",
            tenant_id="tenant_123",
        )


_DEFAULT_PRODUCT = object()  # sentinel: build a product configured with inventory targeting


def _make_configured_product():
    """A product whose implementation_config carries inventory targeting (passes validation)."""
    product = Mock()
    product.product_id = "prod_test"
    product.implementation_config = {"targeted_ad_unit_ids": ["123456"]}
    product.gemini_api_key = None
    product.order_name_template = None
    return product


def _make_product_without_inventory():
    """A product that exists but carries no inventory targeting (no ad units, no placements)."""
    product = _make_configured_product()
    product.implementation_config = {}
    return product


def _stub_product_session(mock_db_session, *, product=_DEFAULT_PRODUCT):
    """Wire the product-config DB lookup the create path performs.

    ``product`` defaults to a fully-configured product (``.first()`` returns it,
    passing the inventory-targeting validation). Pass ``product=None`` to simulate
    a lookup miss, or a product without inventory to drive the no-targeting branch.
    ``.all()`` returns no inventory mappings so the GAMInventory branch is skipped.
    """
    if product is _DEFAULT_PRODUCT:
        product = _make_configured_product()
    mock_session = MagicMock()
    mock_db_session.return_value.__enter__.return_value = mock_session
    mock_result = Mock()
    mock_result.first.return_value = product
    mock_result.all.return_value = []
    mock_session.scalars.return_value = mock_result


class TestGAMManualApprovalPath:
    """Test GAM adapter manual approval path returns packages correctly."""

    def test_manual_approval_returns_packages_with_package_ids(
        self, mock_principal, mock_gam_config, sample_request, sample_packages
    ):
        """Manual approval path must return packages with package_id for each package."""
        # Arrange - Mock the client manager to avoid OAuth initialization
        with patch("src.adapters.google_ad_manager.GAMClientManager") as mock_client_manager:
            mock_client_manager.return_value.get_client.return_value = Mock()

            adapter = GoogleAdManager(
                config=mock_gam_config,
                principal=mock_principal,
                network_code="123456",
                advertiser_id="789",
                trafficker_id="456",
                tenant_id="tenant_123",
            )

            # Mock _requires_manual_approval to return True
            with (
                patch.object(adapter, "_requires_manual_approval", return_value=True),
                patch.object(adapter.workflow_manager, "create_manual_order_workflow_step") as mock_workflow,
            ):
                mock_workflow.return_value = "workflow_step_123"

                # Act
                start_time = datetime.now()
                end_time = start_time + timedelta(days=30)
                response = adapter.create_media_buy(
                    request=sample_request, packages=sample_packages, start_time=start_time, end_time=end_time
                )

                # The returned packages ARE the requested ones: same ids, none dropped,
                # none duplicated.
                #
                # Removed with them: `packages is not None`, `isinstance(..., list)` and
                # a per-package `hasattr(pkg, "package_id") and pkg.package_id is not
                # None`. Those cannot fail. `AdapterCreateResult.packages` is a required
                # `list[ResponsePackage]` and `ResponsePackage.package_id` is a required
                # `str`, so pydantic refuses the construction before any assertion runs —
                # they graded the type system, not the adapter.
                assert len(response.packages) == len(sample_packages)
                assert {pkg.package_id for pkg in response.packages} == {pkg.package_id for pkg in sample_packages}

                # Assert - the workflow step was opened. It is NOT on the result:
                # AdapterCreateResult carries what the tool reads, and a step an
                # adapter opens is tracked by the workflow tables.
                mock_workflow.assert_called_once_with(
                    sample_request,
                    sample_packages,
                    start_time,
                    end_time,
                    response.media_buy_id,
                    order_name_template=None,
                )

    def test_manual_approval_failure_still_returns_packages(
        self, mock_principal, mock_gam_config, sample_request, sample_packages
    ):
        """Manual approval path must return packages even when workflow creation fails."""
        # Arrange
        with patch("src.adapters.google_ad_manager.GAMClientManager") as mock_client_manager:
            mock_client_manager.return_value.get_client.return_value = Mock()

            adapter = GoogleAdManager(
                config=mock_gam_config,
                principal=mock_principal,
                network_code="123456",
                advertiser_id="789",
                trafficker_id="456",
                tenant_id="tenant_123",
            )

            # Mock workflow manager to fail
            with (
                patch.object(adapter, "_requires_manual_approval", return_value=True),
                patch.object(adapter.workflow_manager, "create_manual_order_workflow_step") as mock_workflow,
            ):
                mock_workflow.return_value = None  # Simulate failure

                # Act / Assert - workflow failure raises the typed AdCPWorkflowError,
                # whose class identity carries the WORKFLOW_CREATION_FAILED taxonomy.
                import pytest

                from src.core.exceptions import AdCPWorkflowError

                start_time = datetime.now()
                end_time = start_time + timedelta(days=30)
                with pytest.raises(AdCPWorkflowError) as exc_info:
                    adapter.create_media_buy(
                        request=sample_request,
                        packages=sample_packages,
                        start_time=start_time,
                        end_time=end_time,
                    )
                assert exc_info.value.error_code == "WORKFLOW_CREATION_FAILED"


class TestGAMActivationWorkflowPath:
    """Test GAM adapter activation workflow path returns packages correctly."""

    def test_activation_workflow_returns_packages_with_line_item_ids(
        self, mock_principal, sample_request, sample_packages
    ):
        """Activation workflow path must return packages with package_id AND platform_line_item_id."""
        # Arrange - No manual approval, guaranteed line items trigger activation workflow
        config = {
            "network_code": "123456",
            "advertiser_id": "789",
            "trafficker_id": "456",
            "refresh_token": "test_token",
            # manual_approval_operations not set - automatic mode
        }

        with patch("src.adapters.google_ad_manager.GAMClientManager") as mock_client_manager:
            mock_client_manager.return_value.get_client.return_value = Mock()

            adapter = GoogleAdManager(
                config=config,
                principal=mock_principal,
                network_code="123456",
                advertiser_id="789",
                trafficker_id="456",
                tenant_id="tenant_123",
            )

            # Mock the order creation
            mock_order_id = "order_123"
            # production's create_line_items returns list[str] (orders.py str()-casts the
            # GAM id), so the stand-in returns what the real manager returns
            mock_line_item_ids = ["111", "222"]

            with (
                patch.object(adapter.orders_manager, "create_order") as mock_create_order,
                patch.object(adapter.orders_manager, "create_line_items") as mock_create_line_items,
                patch.object(adapter, "_check_order_has_guaranteed_items") as mock_check_guaranteed,
                patch.object(adapter.workflow_manager, "create_activation_workflow_step") as mock_activation_workflow,
                patch("src.core.database.database_session.get_db_session") as mock_db_session,
            ):
                # Setup mocks
                mock_create_order.return_value = mock_order_id
                mock_create_line_items.return_value = mock_line_item_ids
                mock_check_guaranteed.return_value = (True, ["STANDARD"])  # Guaranteed line items
                mock_activation_workflow.return_value = "activation_workflow_123"

                # Mock database session - need to return products with inventory config
                mock_session = MagicMock()
                mock_db_session.return_value.__enter__.return_value = mock_session

                # Create mock products with inventory targeting (required by validation)
                mock_product = Mock()
                mock_product.product_id = "prod_test"
                mock_product.implementation_config = {"targeted_ad_unit_ids": ["123456"]}
                # Prevent MagicMock auto-generation for tenant attributes
                mock_product.gemini_api_key = None
                mock_product.order_name_template = None

                # Simpler approach: Always return mock_product for .first(), empty for .all()
                mock_result = Mock()
                mock_result.first.return_value = mock_product
                mock_result.all.return_value = []
                mock_session.scalars.return_value = mock_result

                # Act
                start_time = datetime.now()
                end_time = start_time + timedelta(days=30)
                response = adapter.create_media_buy(
                    request=sample_request, packages=sample_packages, start_time=start_time, end_time=end_time
                )

            # Same ids in, same ids out (see the note on the manual-approval test for
            # the presence/type assertions removed here and why they could not fail).
            assert len(response.packages) == len(sample_packages)
            assert {pkg.package_id for pkg in response.packages} == {pkg.package_id for pkg in sample_packages}

            # Assert - the activation step was opened for the guaranteed order. It is
            # not on the result: the workflow tables track a step an adapter opens.
            mock_activation_workflow.assert_called_once_with(mock_order_id, sample_packages)
            assert response.media_buy_id == mock_order_id, "media_buy_id must match order ID"

            # Assert - each package carries the line item GAM created for it
            assert response.platform_line_item_ids == {"pkg_001": "111", "pkg_002": "222"}


class TestGAMSuccessPath:
    """Test GAM adapter success path (no workflow) returns packages correctly."""

    def test_success_path_returns_packages_with_line_item_ids(self, mock_principal, sample_request, sample_packages):
        """Success path (no workflow) must return packages with package_id AND platform_line_item_id."""
        # Arrange - No manual approval, non-guaranteed line items (no activation workflow)
        config = {
            "network_code": "123456",
            "advertiser_id": "789",
            "trafficker_id": "456",
            "refresh_token": "test_token",
        }

        with patch("src.adapters.google_ad_manager.GAMClientManager") as mock_client_manager:
            mock_client_manager.return_value.get_client.return_value = Mock()

            adapter = GoogleAdManager(
                config=config,
                principal=mock_principal,
                network_code="123456",
                advertiser_id="789",
                trafficker_id="456",
                tenant_id="tenant_123",
            )

            # Mock the order creation
            mock_order_id = "order_456"
            mock_line_item_ids = ["333", "444"]

            with (
                patch.object(adapter.orders_manager, "create_order") as mock_create_order,
                patch.object(adapter.orders_manager, "create_line_items") as mock_create_line_items,
                patch.object(adapter, "_check_order_has_guaranteed_items") as mock_check_guaranteed,
                patch.object(adapter.workflow_manager, "create_activation_workflow_step") as mock_activation_workflow,
                patch("src.core.database.database_session.get_db_session") as mock_db_session,
            ):
                # Setup mocks
                mock_create_order.return_value = mock_order_id
                mock_create_line_items.return_value = mock_line_item_ids
                mock_check_guaranteed.return_value = (False, ["PRICE_PRIORITY"])  # Non-guaranteed

                # Mock database session - need to return products with inventory config
                mock_session = MagicMock()
                mock_db_session.return_value.__enter__.return_value = mock_session

                # Create mock products with inventory targeting (required by validation)
                mock_product = Mock()
                mock_product.product_id = "prod_test"
                mock_product.implementation_config = {"targeted_ad_unit_ids": ["123456"]}
                # Prevent MagicMock auto-generation for tenant attributes
                mock_product.gemini_api_key = None
                mock_product.order_name_template = None

                # Simpler approach: Always return mock_product for .first(), empty for .all()
                mock_result = Mock()
                mock_result.first.return_value = mock_product
                mock_result.all.return_value = []
                mock_session.scalars.return_value = mock_result

                # Act
                start_time = datetime.now()
                end_time = start_time + timedelta(days=30)
                response = adapter.create_media_buy(
                    request=sample_request, packages=sample_packages, start_time=start_time, end_time=end_time
                )

            # Same ids in, same ids out (see the note on the manual-approval test for
            # the presence/type assertions removed here and why they could not fail).
            assert len(response.packages) == len(sample_packages)
            assert {pkg.package_id for pkg in response.packages} == {pkg.package_id for pkg in sample_packages}

            # Assert - no activation step on the success path, and the line items GAM
            # created reach the tool under their package ids
            mock_activation_workflow.assert_not_called()
            assert response.platform_line_item_ids == {"pkg_001": "333", "pkg_002": "444"}


class TestGAMAdapterErrorTaxonomy:
    """Exercise a GAM create-path taxonomy raise site so the distinct internal
    code is pinned at the site, not just on the class.

    The wire collapses the adapter taxonomy to SERVICE_UNAVAILABLE (pinned for
    all six taxonomy classes in test_typed_error_wire_codes.py); here the
    line-item-failure raise site is driven so a class-swap at the site is caught
    too. AdCPWorkflowError's create-path site is exercised above; the
    update-path sites (activation / GAM-update / bulk-update) are covered by the
    wire-mapping test and the GAM lifecycle integration suite.
    """

    def test_line_item_creation_failure_raises_typed_line_item_error(
        self, mock_principal, sample_request, sample_packages
    ):
        """A line-item creation failure raises AdCPLineItemError (LINE_ITEM_CREATION_FAILED)."""
        from src.core.exceptions import AdCPLineItemError

        adapter = _build_gam_adapter(mock_principal)

        with (
            patch.object(adapter.orders_manager, "create_order", return_value="order_123"),
            patch.object(adapter.orders_manager, "create_line_items", side_effect=Exception("GAM API error")),
            patch("src.core.database.database_session.get_db_session") as mock_db_session,
        ):
            _stub_product_session(mock_db_session)

            start_time = datetime.now()
            end_time = start_time + timedelta(days=30)
            with pytest.raises(AdCPLineItemError) as exc_info:
                adapter.create_media_buy(
                    request=sample_request, packages=sample_packages, start_time=start_time, end_time=end_time
                )

        assert exc_info.value.error_code == "AD_SERVER_CREATE_FAILED"


class TestGAMAdapterSeamPreservesClassification:
    """The classification survives ``GoogleAdManager.create_media_buy``.

    ``orders_manager.create_order`` classifies an ad-server refusal into the AdCP
    error the buyer should read. The tool layer then re-raises a typed
    ``AdCPSalesAgentError`` untouched. Between those two is this seam
    (google_ad_manager.py:672), and nothing pinned it: an ``except Exception``
    introduced around that call would re-collapse every refusal to one code while
    the manager-level and wire-level tests both stayed green.

    Driving the real adapter, because the seam IS adapter code -- a mocked adapter
    replaces exactly what is under test. Reuses ``_build_gam_adapter`` rather than
    re-wiring the adapter here (salesagent-rys3u.8).
    """

    def test_rate_limit_from_the_order_call_propagates_unchanged(self, mock_principal, sample_request, sample_packages):
        """A quota refusal beneath the seam reaches the caller still RATE_LIMITED."""
        from src.core.exceptions import AdCPRateLimitError

        adapter = _build_gam_adapter(mock_principal)

        with (
            patch.object(
                adapter.orders_manager,
                "create_order",
                side_effect=AdCPRateLimitError(retry_after=30),
            ),
            patch("src.core.database.database_session.get_db_session") as mock_db_session,
        ):
            _stub_product_session(mock_db_session)

            start_time = datetime.now()
            end_time = start_time + timedelta(days=30)
            with pytest.raises(AdCPRateLimitError) as exc_info:
                adapter.create_media_buy(
                    request=sample_request,
                    packages=sample_packages,
                    start_time=start_time,
                    end_time=end_time,
                )

        # Not AdCPAdapterError, not AD_SERVER_CREATE_FAILED: the seam must not
        # relabel a classification the layer beneath it already made.
        assert exc_info.value.error_code == "RATE_LIMITED"


class TestGAMProductUnavailableRaiseSites:
    """Drive both AdCPProductUnavailableError raise sites in GAM create_media_buy.

    Two distinct pre-order validation conditions raise the same class:
      1. Product config missing — the package's product is absent from the DB,
         so ``products_map`` never gets an entry (google_ad_manager.py:540).
      2. No inventory targeting — the product exists but its implementation_config
         carries neither ad units nor placements (google_ad_manager.py:561).

    test_typed_error_wire_codes.py pins the class -> wire-code mapping by
    constructing the exception directly; here the production validation loop is
    driven so a class-swap at either site (e.g. AdCPProductUnavailableError ->
    AdCPSalesAgentError or AdCPCapabilityNotSupportedError) is caught. The wire collapses
    PRODUCT_UNAVAILABLE as its own declared code, pinned separately.
    """

    @pytest.mark.parametrize(
        ("condition", "make_product"),
        [
            ("product_config_missing", lambda: None),
            ("no_inventory_targeting", _make_product_without_inventory),
        ],
    )
    def test_product_unavailable_raise_sites(
        self, mock_principal, sample_request, sample_packages, condition, make_product
    ):
        """Both pre-order validation conditions raise AdCPProductUnavailableError
        (PRODUCT_UNAVAILABLE) at the actual production site."""
        from src.core.exceptions import AdCPProductUnavailableError

        adapter = _build_gam_adapter(mock_principal)

        with patch("src.core.database.database_session.get_db_session") as mock_db_session:
            _stub_product_session(mock_db_session, product=make_product())

            start_time = datetime.now()
            end_time = start_time + timedelta(days=30)
            with pytest.raises(AdCPProductUnavailableError) as exc_info:
                adapter.create_media_buy(
                    request=sample_request, packages=sample_packages, start_time=start_time, end_time=end_time
                )

        assert exc_info.value.error_code == "PRODUCT_UNAVAILABLE", condition
