"""Tests for policy service."""

from decimal import Decimal

import pytest

from src.services.policy_service import (
    CurrencyLimitData,
    PolicyService,
    PolicySettings,
    ValidationError,
)


class TestCurrencyValidation:
    """Tests for currency code validation."""

    def test_validate_valid_currency_codes(self):
        """Valid ISO 4217 currency codes should pass validation."""
        valid_codes = ["USD", "EUR", "GBP", "JPY", "CAD", "AUD"]
        for code in valid_codes:
            # Should not raise
            PolicyService.validate_currency_code(code)

    def test_validate_invalid_currency_codes(self):
        """Invalid currency codes should raise ValidationError."""
        invalid_codes = ["XYZ", "ABC", "ZZZ", "FOO"]
        for code in invalid_codes:
            with pytest.raises(ValidationError) as exc_info:
                PolicyService.validate_currency_code(code)
            assert "Invalid currency code" in str(exc_info.value)

    def test_validate_empty_currency_code(self):
        """Empty currency code should raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            PolicyService.validate_currency_code("")
        assert "must be exactly 3 letters" in str(exc_info.value)

    def test_validate_wrong_length_currency_code(self):
        """Currency codes with wrong length should raise ValidationError."""
        with pytest.raises(ValidationError):
            PolicyService.validate_currency_code("US")  # Too short

        with pytest.raises(ValidationError):
            PolicyService.validate_currency_code("USDD")  # Too long


class TestCurrencyLimitsValidation:
    """Tests for currency limits validation."""

    def test_validate_single_valid_currency(self):
        """Single valid currency should pass validation."""
        currencies = [
            CurrencyLimitData(
                currency_code="USD", min_package_budget=Decimal("100"), max_daily_package_spend=Decimal("1000")
            )
        ]
        # Should not raise
        PolicyService.validate_currency_limits(currencies)

    def test_validate_multiple_valid_currencies(self):
        """Multiple valid currencies should pass validation."""
        currencies = [
            CurrencyLimitData(currency_code="USD", min_package_budget=Decimal("100")),
            CurrencyLimitData(currency_code="EUR", max_daily_package_spend=Decimal("1000")),
            CurrencyLimitData(currency_code="GBP"),
        ]
        # Should not raise
        PolicyService.validate_currency_limits(currencies)

    def test_validate_duplicate_currency_codes(self):
        """Duplicate currency codes should raise ValidationError."""
        currencies = [
            CurrencyLimitData(currency_code="USD", min_package_budget=Decimal("100")),
            CurrencyLimitData(currency_code="USD", max_daily_package_spend=Decimal("1000")),
        ]
        with pytest.raises(ValidationError) as exc_info:
            PolicyService.validate_currency_limits(currencies)
        assert "Duplicate currency code" in str(exc_info.value)

    def test_validate_invalid_currency_in_list(self):
        """Invalid currency code in list should raise ValidationError."""
        currencies = [
            CurrencyLimitData(currency_code="USD", min_package_budget=Decimal("100")),
            CurrencyLimitData(currency_code="XYZ", max_daily_package_spend=Decimal("1000")),
        ]
        with pytest.raises(ValidationError) as exc_info:
            PolicyService.validate_currency_limits(currencies)
        assert "XYZ" in str(exc_info.value)
        assert "Invalid currency code" in str(exc_info.value)

    def test_validate_negative_min_budget(self):
        """Negative minimum budget should raise ValidationError."""
        currencies = [CurrencyLimitData(currency_code="USD", min_package_budget=Decimal("-100"))]
        with pytest.raises(ValidationError) as exc_info:
            PolicyService.validate_currency_limits(currencies)
        assert "cannot be negative" in str(exc_info.value)

    def test_validate_negative_max_spend(self):
        """Negative maximum spend should raise ValidationError."""
        currencies = [CurrencyLimitData(currency_code="USD", max_daily_package_spend=Decimal("-1000"))]
        with pytest.raises(ValidationError) as exc_info:
            PolicyService.validate_currency_limits(currencies)
        assert "cannot be negative" in str(exc_info.value)

    def test_validate_min_exceeds_max(self):
        """Min budget exceeding max spend should raise ValidationError."""
        currencies = [
            CurrencyLimitData(
                currency_code="USD", min_package_budget=Decimal("1000"), max_daily_package_spend=Decimal("100")
            )
        ]
        with pytest.raises(ValidationError) as exc_info:
            PolicyService.validate_currency_limits(currencies)
        assert "cannot exceed" in str(exc_info.value)

    def test_validate_skips_deleted_currencies(self):
        """Currencies marked for deletion should be skipped in validation."""
        currencies = [
            CurrencyLimitData(currency_code="USD", min_package_budget=Decimal("100")),
            CurrencyLimitData(currency_code="XYZ", _delete=True),  # Invalid but marked for deletion
        ]
        # Should not raise because XYZ is marked for deletion
        PolicyService.validate_currency_limits(currencies)


class TestMeasurementProviderValidation:
    """Tests for measurement provider validation."""

    def test_validate_valid_providers_non_gam(self):
        """Valid provider configuration for non-GAM tenant should pass."""
        providers_data = {"providers": ["Publisher Ad Server"], "default": "Publisher Ad Server"}
        # Should not raise
        PolicyService.validate_measurement_providers(providers_data, is_gam_tenant=False)

    def test_validate_empty_providers_gam_tenant(self):
        """Empty providers for GAM tenant should pass (GAM is default)."""
        providers_data = {"providers": []}
        # Should not raise for GAM tenant
        PolicyService.validate_measurement_providers(providers_data, is_gam_tenant=True)

    def test_validate_empty_providers_non_gam_tenant(self):
        """Empty providers for non-GAM tenant should raise ValidationError."""
        providers_data = {"providers": []}
        with pytest.raises(ValidationError) as exc_info:
            PolicyService.validate_measurement_providers(providers_data, is_gam_tenant=False)
        assert "At least one measurement provider is required" in str(exc_info.value)

    def test_validate_default_not_in_list(self):
        """Default provider not in list should raise ValidationError."""
        providers_data = {"providers": ["Publisher Ad Server"], "default": "Google Ad Manager"}  # Not in list
        with pytest.raises(ValidationError) as exc_info:
            PolicyService.validate_measurement_providers(providers_data, is_gam_tenant=False)
        assert "must be in the provider list" in str(exc_info.value)


class TestNamingTemplateValidation:
    """Tests for naming template validation."""

    def test_validate_valid_template(self):
        """Valid template should pass validation."""
        template = "{campaign_name} - {media_buy_id} - {date_range}"
        # Should not raise
        PolicyService.validate_naming_template(template, "order_name_template")

    def test_validate_empty_template(self):
        """Empty template should raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            PolicyService.validate_naming_template("", "order_name_template")
        assert "cannot be empty" in str(exc_info.value)

    def test_validate_unbalanced_braces(self):
        """Template with unbalanced braces should raise ValidationError."""
        template = "{campaign_name - {media_buy_id}"  # Missing closing brace
        with pytest.raises(ValidationError) as exc_info:
            PolicyService.validate_naming_template(template, "order_name_template")
        assert "unbalanced braces" in str(exc_info.value)


class TestPolicySettingsDataClass:
    """Tests for PolicySettings data class."""

    def test_to_dict_excludes_deleted_currencies(self):
        """to_dict should exclude currencies marked for deletion."""
        settings = PolicySettings(
            currencies=[
                CurrencyLimitData(currency_code="USD", min_package_budget=Decimal("100")),
                CurrencyLimitData(currency_code="EUR", _delete=True),
            ]
        )
        result = settings.to_dict()

        assert len(result["currencies"]) == 1
        assert result["currencies"][0]["currency_code"] == "USD"

    def test_to_dict_converts_decimals_to_floats(self):
        """to_dict should convert Decimal values to floats for JSON serialization."""
        settings = PolicySettings(
            currencies=[
                CurrencyLimitData(
                    currency_code="USD",
                    min_package_budget=Decimal("100.50"),
                    max_daily_package_spend=Decimal("1000.75"),
                ),
            ]
        )
        result = settings.to_dict()

        assert result["currencies"][0]["min_package_budget"] == 100.50
        assert result["currencies"][0]["max_daily_package_spend"] == 1000.75

    def test_to_dict_handles_none_values(self):
        """to_dict should handle None values correctly."""
        settings = PolicySettings(
            currencies=[
                CurrencyLimitData(currency_code="USD"),  # No min/max set
            ]
        )
        result = settings.to_dict()

        assert result["currencies"][0]["min_package_budget"] is None
        assert result["currencies"][0]["max_daily_package_spend"] is None


class TestValidationErrorClass:
    """Tests for ValidationError class."""

    def test_validation_error_with_dict(self):
        """ValidationError with dict should store errors correctly."""
        errors = {"field1": "Error 1", "field2": "Error 2"}
        exc = ValidationError(errors)

        assert exc.errors == errors
        assert "field1" in str(exc)
        assert "field2" in str(exc)

    def test_validation_error_with_string(self):
        """ValidationError with string should convert to dict."""
        error_msg = "General error"
        exc = ValidationError(error_msg)

        assert exc.errors == {"_general": error_msg}
        assert error_msg in str(exc)


# Integration-style tests with mocked database would go here
# These test the full get_policies() and update_policies() methods
# but require database mocking which we'll add in a separate test file
