"""Integration tests for v1.8.0 budget format migration.

Tests that verify the budget extraction helper is properly used throughout
the codebase (main.py, naming.py, adapters).
"""

from datetime import UTC, datetime
from decimal import Decimal

from src.core.schemas import (
    Budget,
    PackageRequest,
    extract_budget_amount,
)


class TestBudgetMigrationInMainPy:
    """Test budget handling in main.py code paths."""

    def test_package_budget_validation_with_float(self):
        """Test that package budget validation works with float budget (v1.8.0)."""
        # Simulate the logic from main.py lines 3633-3637
        package = PackageRequest(product_id="prod_1", buyer_ref="pkg1", budget=5000.0, pricing_option_id="test_pricing")
        request_currency = "USD"

        # This is what main.py now does
        package_budget_amount, _ = extract_budget_amount(package.budget, request_currency)
        package_budget = Decimal(str(package_budget_amount))

        assert package_budget == Decimal("5000.0")

    # NOTE: Tests removed - Package.budget is now float | None per AdCP v2.2.0 spec
    # Budget objects and dict budgets are no longer supported at package level

    def test_update_media_buy_budget_with_float(self):
        """Test update_media_buy budget extraction with float (v1.8.0).

        Note: In practice, UpdateMediaBuyRequest.budget should be a Budget object,
        but the extract helper handles floats for flexibility.
        """
        # Simulate the logic from main.py lines 4768-4770
        budget = 10000.0

        # UpdateMediaBuyRequest doesn't have a currency field, so we use default
        total_budget, currency = extract_budget_amount(budget, "USD")

        assert total_budget == 10000.0
        assert currency == "USD"

    def test_update_media_buy_budget_with_budget_object(self):
        """Test update_media_buy budget extraction with Budget object.

        This is the standard case for UpdateMediaBuyRequest.
        """
        budget_obj = Budget(total=15000.0, currency="EUR")

        # UpdateMediaBuyRequest doesn't have a currency field
        total_budget, currency = extract_budget_amount(budget_obj, "USD")

        assert total_budget == 15000.0
        assert currency == "EUR"  # From Budget object, not default USD

    def test_update_media_buy_budget_with_dict(self):
        """Test update_media_buy budget extraction with dict."""
        budget_dict = {"total": 8000.0, "currency": "CAD"}

        # UpdateMediaBuyRequest doesn't have a currency field
        total_budget, currency = extract_budget_amount(budget_dict, "USD")

        assert total_budget == 8000.0
        assert currency == "CAD"  # From dict, not default USD


class TestBudgetMigrationInNamingUtils:
    """Test budget handling in naming.py generate_auto_name function."""

    def test_naming_budget_extraction_with_float(self):
        """Test naming utility budget extraction with float budget (v1.8.0)."""

        # Simulate the logic from naming.py lines 98-101
        class MockRequest:
            budget = 5000.0
            currency = "USD"

        request = MockRequest()

        if request.budget:
            budget_amount, currency = extract_budget_amount(request.budget, request.currency or "USD")
            budget_str = f"Budget: ${budget_amount:,.2f} {currency}"

        assert budget_str == "Budget: $5,000.00 USD"

    def test_naming_budget_extraction_with_budget_object(self):
        """Test naming utility budget extraction with Budget object (legacy)."""

        class MockRequest:
            budget = Budget(total=12500.50, currency="EUR")
            currency = "USD"

        request = MockRequest()

        if request.budget:
            budget_amount, currency = extract_budget_amount(request.budget, request.currency or "USD")
            budget_str = f"Budget: ${budget_amount:,.2f} {currency}"

        assert budget_str == "Budget: $12,500.50 EUR"  # EUR from Budget object

    def test_naming_budget_extraction_with_dict(self):
        """Test naming utility budget extraction with dict budget."""

        class MockRequest:
            budget = {"total": 9750.25, "currency": "GBP"}
            currency = "USD"

        request = MockRequest()

        if request.budget:
            budget_amount, currency = extract_budget_amount(request.budget, request.currency or "USD")
            budget_str = f"Budget: ${budget_amount:,.2f} {currency}"

        assert budget_str == "Budget: $9,750.25 GBP"


class TestBudgetMigrationInAdapters:
    """Test budget handling in ad server adapters."""

    def test_mock_adapter_budget_validation_with_float(self):
        """Test Mock adapter budget validation with float budget (v1.8.0)."""

        # Simulate the logic from mock_ad_server.py lines 321-328
        class MockRequest:
            budget = 5000.0
            currency = "USD"

        request = MockRequest()

        if request.budget:
            budget_amount, _ = extract_budget_amount(request.budget, request.currency or "USD")
            # Mock adapter checks for invalid budget
            is_invalid = budget_amount <= 0
            is_too_large = budget_amount > 1000000

        assert not is_invalid
        assert not is_too_large

    def test_mock_adapter_budget_validation_with_budget_object(self):
        """Test Mock adapter budget validation with Budget object (legacy)."""

        class MockRequest:
            budget = Budget(total=250000.0, currency="EUR")
            currency = "USD"

        request = MockRequest()

        if request.budget:
            budget_amount, _ = extract_budget_amount(request.budget, request.currency or "USD")
            is_invalid = budget_amount <= 0
            is_too_large = budget_amount > 1000000

        assert not is_invalid
        assert not is_too_large

    def test_gam_adapter_order_creation_with_float(self):
        """Test GAM adapter order creation with float budget (v1.8.0)."""

        # Simulate the logic from google_ad_manager.py lines 419-422
        class MockRequest:
            budget = 15000.0
            currency = "USD"

        request = MockRequest()

        total_budget_amount, _ = extract_budget_amount(request.budget, request.currency or "USD")

        # This would be passed to orders_manager.create_order
        assert total_budget_amount == 15000.0

    def test_gam_adapter_order_creation_with_budget_object(self):
        """Test GAM adapter order creation with Budget object (legacy)."""

        class MockRequest:
            budget = Budget(total=20000.0, currency="EUR")
            currency = None

        request = MockRequest()

        total_budget_amount, _ = extract_budget_amount(request.budget, request.currency or "USD")

        assert total_budget_amount == 20000.0

    def test_gam_workflow_manual_creation_with_float(self):
        """Test GAM workflow manager manual creation with float budget (v1.8.0)."""

        # Simulate the logic from gam/managers/workflow.py lines 170-187
        class MockRequest:
            budget = 50000.0
            currency = "USD"

        request = MockRequest()

        total_budget_amount, _ = extract_budget_amount(request.budget, request.currency or "USD")

        # This would be used in action_details
        instruction = f"Set total budget to: ${total_budget_amount:,.2f}"

        assert instruction == "Set total budget to: $50,000.00"

    def test_xandr_adapter_io_creation_with_float(self):
        """Test Xandr adapter insertion order creation with float budget (v1.8.0)."""

        # Simulate the logic from xandr.py lines 282-288
        class MockRequest:
            budget = 30000.0
            currency = "USD"
            flight_start_date = datetime(2025, 1, 1, tzinfo=UTC)
            flight_end_date = datetime(2025, 1, 31, tzinfo=UTC)

        request = MockRequest()

        budget_amount = extract_budget_amount(request.budget, request.currency or "USD")[0]
        flight_days = (request.flight_end_date - request.flight_start_date).days

        daily_budget = float(budget_amount / flight_days)
        lifetime_budget = float(budget_amount)

        assert daily_budget == 1000.0  # 30000 / 30 days
        assert lifetime_budget == 30000.0


class TestBudgetFormatMixedScenarios:
    """Test scenarios with mixed budget formats in a single request."""

    # NOTE: Tests removed - Package.budget is now float | None per AdCP v2.2.0 spec
    # Mixed budget formats (Budget objects and dicts) are no longer supported at package level
