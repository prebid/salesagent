"""Policy service for managing tenant policy settings.

This service provides a clean abstraction for all policy-related operations,
consolidating validation and business logic that was previously scattered
across multiple blueprint handlers.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from babel import numbers as babel_numbers
from sqlalchemy import select
from sqlalchemy.orm import attributes

from src.core.database.database_session import get_db_session
from src.core.database.models import CurrencyLimit, Tenant


class ValidationError(Exception):
    """Raised when policy validation fails.

    Attributes:
        errors: Dict mapping field names to error messages
    """

    def __init__(self, errors: dict[str, str] | str):
        """Initialize validation error.

        Args:
            errors: Either a dict of field->error mappings or a single error message
        """
        if isinstance(errors, str):
            errors = {"_general": errors}
        self.errors = errors
        super().__init__(self._format_errors())

    def _format_errors(self) -> str:
        """Format errors for display."""
        return "; ".join(f"{field}: {msg}" for field, msg in self.errors.items())


@dataclass
class CurrencyLimitData:
    """Currency limit data transfer object."""

    currency_code: str
    min_package_budget: Decimal | None = None
    max_daily_package_spend: Decimal | None = None
    _delete: bool = False


@dataclass
class PolicySettings:
    """Complete policy settings for a tenant.

    This represents all the settings managed in the "Policies & Workflows" section
    of the tenant settings UI.
    """

    # Budget controls
    currencies: list[CurrencyLimitData] = field(default_factory=list)

    # Measurement
    measurement_providers: dict[str, Any] = field(default_factory=dict)
    default_measurement_provider: str | None = None

    # Naming conventions
    order_name_template: str = "{campaign_name|brand_name} - {buyer_ref} - {date_range}"
    line_item_name_template: str = "{order_name} - {product_name}"

    # Approval workflow
    approval_mode: str = "auto-approve"
    creative_review_criteria: str = ""
    creative_auto_approve_threshold: float = 0.9
    creative_auto_reject_threshold: float = 0.1

    # AI policy
    ai_policy: dict[str, Any] = field(default_factory=dict)

    # Advertising policy
    advertising_policy: dict[str, Any] = field(default_factory=dict)

    # Features
    enable_axe_signals: bool = False
    brand_manifest_policy: str = "public"

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "currencies": [
                {
                    "currency_code": c.currency_code,
                    "min_package_budget": float(c.min_package_budget) if c.min_package_budget else None,
                    "max_daily_package_spend": float(c.max_daily_package_spend) if c.max_daily_package_spend else None,
                }
                for c in self.currencies
                if not c._delete
            ],
            "measurement_providers": self.measurement_providers,
            "default_measurement_provider": self.default_measurement_provider,
            "order_name_template": self.order_name_template,
            "line_item_name_template": self.line_item_name_template,
            "approval_mode": self.approval_mode,
            "creative_review_criteria": self.creative_review_criteria,
            "creative_auto_approve_threshold": self.creative_auto_approve_threshold,
            "creative_auto_reject_threshold": self.creative_auto_reject_threshold,
            "ai_policy": self.ai_policy,
            "advertising_policy": self.advertising_policy,
            "enable_axe_signals": self.enable_axe_signals,
            "brand_manifest_policy": self.brand_manifest_policy,
        }


class PolicyService:
    """Service for managing tenant policy settings."""

    @staticmethod
    def validate_currency_code(currency_code: str) -> None:
        """Validate currency code using Babel's ISO 4217 database.

        Args:
            currency_code: 3-letter ISO 4217 currency code (e.g., "USD", "EUR")

        Raises:
            ValidationError: If currency code is invalid
        """
        if not currency_code or len(currency_code) != 3:
            raise ValidationError({"currency_code": "Currency code must be exactly 3 letters"})

        currency_code = currency_code.upper()

        try:
            # Get the currency name - if Babel returns the code itself, it's not a real currency
            # Real currencies have proper names (e.g., "US Dollar" for USD)
            # Unknown currencies just return the code (e.g., "XYZ" for XYZ)
            name = babel_numbers.get_currency_name(currency_code, locale="en")
            if name == currency_code:
                raise ValidationError(
                    {
                        "currency_code": f"Invalid currency code: {currency_code}. Please use a valid ISO 4217 currency code."
                    }
                )
        except Exception as e:
            raise ValidationError({"currency_code": f"Error validating currency code: {str(e)}"}) from e

    @staticmethod
    def validate_currency_limits(currencies: list[CurrencyLimitData]) -> None:
        """Validate currency limit data.

        Args:
            currencies: List of currency limits to validate

        Raises:
            ValidationError: If any currency limit is invalid
        """
        errors = {}
        seen_codes = set()

        for currency in currencies:
            # Skip deleted currencies
            if currency._delete:
                continue

            # Check for duplicates
            if currency.currency_code in seen_codes:
                errors[currency.currency_code] = f"Duplicate currency code: {currency.currency_code}"
                continue
            seen_codes.add(currency.currency_code)

            # Validate currency code
            try:
                PolicyService.validate_currency_code(currency.currency_code)
            except ValidationError as e:
                errors[currency.currency_code] = e.errors.get("currency_code", str(e))
                continue

            # Validate numeric values
            if currency.min_package_budget is not None and currency.min_package_budget < 0:
                errors[f"{currency.currency_code}_min"] = "Minimum budget cannot be negative"

            if currency.max_daily_package_spend is not None and currency.max_daily_package_spend < 0:
                errors[f"{currency.currency_code}_max"] = "Maximum spend cannot be negative"

            if (
                currency.min_package_budget is not None
                and currency.max_daily_package_spend is not None
                and currency.min_package_budget > currency.max_daily_package_spend
            ):
                errors[f"{currency.currency_code}_range"] = "Minimum budget cannot exceed maximum spend"

        if errors:
            raise ValidationError(errors)

    @staticmethod
    def validate_measurement_providers(providers_data: dict[str, Any], is_gam_tenant: bool = False) -> None:
        """Validate measurement provider configuration.

        Args:
            providers_data: Dict with 'providers' list and optional 'default'
            is_gam_tenant: Whether this is a GAM tenant (has different requirements)

        Raises:
            ValidationError: If provider configuration is invalid
        """
        providers = providers_data.get("providers", [])
        default = providers_data.get("default")

        # Non-GAM tenants must have at least one provider
        if not is_gam_tenant and not providers:
            raise ValidationError(
                {"measurement_providers": "At least one measurement provider is required. Please add a provider name."}
            )

        # If default is specified, it must be in the provider list
        if default and default not in providers:
            raise ValidationError(
                {"measurement_providers": f"Default provider '{default}' must be in the provider list"}
            )

    @staticmethod
    def validate_naming_template(template: str, field_name: str) -> None:
        """Validate naming template syntax.

        Args:
            template: Template string to validate
            field_name: Name of the field for error messages

        Raises:
            ValidationError: If template syntax is invalid
        """
        if not template or not template.strip():
            raise ValidationError({field_name: "Template cannot be empty"})

        # Check for balanced braces
        open_count = template.count("{")
        close_count = template.count("}")
        if open_count != close_count:
            raise ValidationError({field_name: "Template has unbalanced braces"})

    @staticmethod
    def get_policies(tenant_id: str) -> PolicySettings:
        """Get all policy settings for a tenant.

        Args:
            tenant_id: ID of the tenant

        Returns:
            PolicySettings object with all current settings

        Raises:
            ValueError: If tenant not found
        """
        with get_db_session() as session:
            # Get tenant
            stmt = select(Tenant).filter_by(tenant_id=tenant_id)
            tenant = session.scalars(stmt).first()

            if not tenant:
                raise ValueError(f"Tenant {tenant_id} not found")

            # Get currency limits
            stmt_currency = select(CurrencyLimit).filter_by(tenant_id=tenant_id)
            currency_limits = session.scalars(stmt_currency).all()

            currencies = [
                CurrencyLimitData(
                    currency_code=limit.currency_code,
                    min_package_budget=limit.min_package_budget,
                    max_daily_package_spend=limit.max_daily_package_spend,
                )
                for limit in currency_limits
            ]

            # Extract measurement providers
            mp_data = tenant.measurement_providers or {}
            default_provider = mp_data.get("default") if mp_data else None

            return PolicySettings(
                currencies=currencies,
                measurement_providers=mp_data,
                default_measurement_provider=default_provider,
                order_name_template=tenant.order_name_template or PolicySettings.order_name_template,
                line_item_name_template=tenant.line_item_name_template or PolicySettings.line_item_name_template,
                approval_mode=tenant.approval_mode or "auto-approve",
                creative_review_criteria=tenant.creative_review_criteria or "",
                creative_auto_approve_threshold=tenant.creative_auto_approve_threshold or 0.9,
                creative_auto_reject_threshold=tenant.creative_auto_reject_threshold or 0.1,
                ai_policy=tenant.ai_policy or {},
                advertising_policy=tenant.advertising_policy or {},
                enable_axe_signals=tenant.enable_axe_signals or False,
                brand_manifest_policy=tenant.brand_manifest_policy or "public",
            )

    @staticmethod
    def update_policies(tenant_id: str, updates: dict[str, Any]) -> PolicySettings:
        """Update policy settings for a tenant.

        This method performs atomic updates - validates ALL changes before applying ANY.

        Args:
            tenant_id: ID of the tenant
            updates: Dict of field names to new values (partial updates supported)

        Returns:
            Updated PolicySettings object

        Raises:
            ValidationError: If any validation fails
            ValueError: If tenant not found
        """
        with get_db_session() as session:
            # Get tenant
            stmt = select(Tenant).filter_by(tenant_id=tenant_id)
            tenant = session.scalars(stmt).first()

            if not tenant:
                raise ValueError(f"Tenant {tenant_id} not found")

            # Validate ALL changes before applying ANY
            validation_errors = {}

            # Validate currencies
            if "currencies" in updates:
                try:
                    PolicyService.validate_currency_limits(updates["currencies"])
                except ValidationError as e:
                    validation_errors.update(e.errors)

            # Validate measurement providers
            if "measurement_providers" in updates:
                try:
                    PolicyService.validate_measurement_providers(
                        updates["measurement_providers"], is_gam_tenant=tenant.is_gam_tenant
                    )
                except ValidationError as e:
                    validation_errors.update(e.errors)

            # Validate naming templates
            if "order_name_template" in updates:
                try:
                    PolicyService.validate_naming_template(updates["order_name_template"], "order_name_template")
                except ValidationError as e:
                    validation_errors.update(e.errors)

            if "line_item_name_template" in updates:
                try:
                    PolicyService.validate_naming_template(
                        updates["line_item_name_template"], "line_item_name_template"
                    )
                except ValidationError as e:
                    validation_errors.update(e.errors)

            # If any validation failed, raise error with all errors
            if validation_errors:
                raise ValidationError(validation_errors)

            # All validation passed - apply changes atomically

            # Update currencies
            if "currencies" in updates:
                PolicyService._update_currencies(session, tenant_id, updates["currencies"])

            # Update measurement providers
            if "measurement_providers" in updates:
                tenant.measurement_providers = updates["measurement_providers"]
                attributes.flag_modified(tenant, "measurement_providers")

            # Update naming templates
            if "order_name_template" in updates:
                tenant.order_name_template = updates["order_name_template"]

            if "line_item_name_template" in updates:
                tenant.line_item_name_template = updates["line_item_name_template"]

            # Update approval settings
            if "approval_mode" in updates:
                tenant.approval_mode = updates["approval_mode"]

            if "creative_review_criteria" in updates:
                tenant.creative_review_criteria = updates["creative_review_criteria"]

            if "creative_auto_approve_threshold" in updates:
                tenant.creative_auto_approve_threshold = updates["creative_auto_approve_threshold"]

            if "creative_auto_reject_threshold" in updates:
                tenant.creative_auto_reject_threshold = updates["creative_auto_reject_threshold"]

            # Update AI policy
            if "ai_policy" in updates:
                tenant.ai_policy = updates["ai_policy"]
                attributes.flag_modified(tenant, "ai_policy")

            # Update advertising policy
            if "advertising_policy" in updates:
                tenant.advertising_policy = updates["advertising_policy"]
                attributes.flag_modified(tenant, "advertising_policy")

            # Update features
            if "enable_axe_signals" in updates:
                tenant.enable_axe_signals = updates["enable_axe_signals"]

            if "brand_manifest_policy" in updates:
                tenant.brand_manifest_policy = updates["brand_manifest_policy"]

            # Update product ranking prompt
            if "product_ranking_prompt" in updates:
                tenant.product_ranking_prompt = updates["product_ranking_prompt"]

            # Commit all changes
            session.commit()

            # Return updated state
            return PolicyService.get_policies(tenant_id)

    @staticmethod
    def _update_currencies(session, tenant_id: str, currencies: list[CurrencyLimitData]) -> None:
        """Update currency limits in database.

        Args:
            session: Database session
            tenant_id: ID of the tenant
            currencies: List of currency limit updates
        """
        # Get existing currency limits
        stmt = select(CurrencyLimit).filter_by(tenant_id=tenant_id)
        existing_limits = {limit.currency_code: limit for limit in session.scalars(stmt).all()}

        # Process each currency
        for currency_data in currencies:
            if currency_data._delete:
                # Delete currency
                if currency_data.currency_code in existing_limits:
                    session.delete(existing_limits[currency_data.currency_code])
            else:
                # Update or create currency
                if currency_data.currency_code in existing_limits:
                    # Update existing
                    limit = existing_limits[currency_data.currency_code]
                    limit.min_package_budget = currency_data.min_package_budget
                    limit.max_daily_package_spend = currency_data.max_daily_package_spend
                    limit.updated_at = datetime.now(UTC)
                else:
                    # Create new
                    limit = CurrencyLimit(
                        tenant_id=tenant_id,
                        currency_code=currency_data.currency_code,
                        min_package_budget=currency_data.min_package_budget,
                        max_daily_package_spend=currency_data.max_daily_package_spend,
                    )
                    session.add(limit)
