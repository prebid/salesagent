"""Currency limit service — business logic for currency limit CRUD.

Extracted from src/admin/blueprints/settings.py Flask blueprint.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import CurrencyLimit
from src.core.exceptions import AdCPNotFoundError, AdCPValidationError

logger = logging.getLogger(__name__)

_CURRENCY_CODE_RE = re.compile(r"^[A-Z]{3}$")


class CurrencyLimitService:
    """Stateless service for currency limit operations."""

    def list_limits(self, tenant_id: str) -> list[dict[str, Any]]:
        """List all currency limits for a tenant."""
        with get_db_session() as session:
            stmt = select(CurrencyLimit).filter_by(tenant_id=tenant_id).order_by(CurrencyLimit.currency_code)
            limits = session.scalars(stmt).all()
            return [self._to_dict(limit) for limit in limits]

    def create_limit(self, tenant_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """Create a new currency limit."""
        currency_code = data.get("currency_code", "").upper()
        self._validate_currency_code(currency_code)

        min_budget = self._parse_decimal(data.get("min_package_budget"))
        max_daily = self._parse_decimal(data.get("max_daily_package_spend"))

        with get_db_session() as session:
            # Check for duplicate
            stmt = select(CurrencyLimit).filter_by(tenant_id=tenant_id, currency_code=currency_code)
            existing = session.scalars(stmt).first()
            if existing:
                raise AdCPValidationError(f"Currency limit for '{currency_code}' already exists")

            limit = CurrencyLimit(
                tenant_id=tenant_id,
                currency_code=currency_code,
                min_package_budget=min_budget,
                max_daily_package_spend=max_daily,
            )
            session.add(limit)
            session.commit()

            # Re-read to get server defaults
            session.refresh(limit)
            return self._to_dict(limit)

    def update_limit(self, tenant_id: str, currency_code: str, data: dict[str, Any]) -> dict[str, Any]:
        """Update an existing currency limit."""
        with get_db_session() as session:
            stmt = select(CurrencyLimit).filter_by(tenant_id=tenant_id, currency_code=currency_code)
            limit = session.scalars(stmt).first()
            if not limit:
                raise AdCPNotFoundError(f"Currency limit for '{currency_code}' not found")

            if "min_package_budget" in data:
                limit.min_package_budget = self._parse_decimal(data["min_package_budget"])
            if "max_daily_package_spend" in data:
                limit.max_daily_package_spend = self._parse_decimal(data["max_daily_package_spend"])

            limit.updated_at = datetime.now(UTC)
            session.commit()
            session.refresh(limit)
            return self._to_dict(limit)

    def delete_limit(self, tenant_id: str, currency_code: str) -> dict[str, Any]:
        """Delete a currency limit."""
        with get_db_session() as session:
            stmt = select(CurrencyLimit).filter_by(tenant_id=tenant_id, currency_code=currency_code)
            limit = session.scalars(stmt).first()
            if not limit:
                raise AdCPNotFoundError(f"Currency limit for '{currency_code}' not found")

            session.delete(limit)
            session.commit()
            return {"message": f"Currency limit for '{currency_code}' deleted", "currency_code": currency_code}

    def _validate_currency_code(self, code: str) -> None:
        """Validate ISO 4217 currency code format."""
        if not _CURRENCY_CODE_RE.match(code):
            raise AdCPValidationError(f"Invalid currency code '{code}': must be 3 uppercase letters (ISO 4217)")

        # Use Babel for semantic validation if available
        try:
            from babel.numbers import get_currency_name

            name = get_currency_name(code, locale="en")
            if name == code:
                raise AdCPValidationError(f"Unknown currency code '{code}'")
        except ImportError:
            pass  # Babel not installed — format check is sufficient
        except Exception:
            raise AdCPValidationError(f"Unknown currency code '{code}'")

    def _parse_decimal(self, value: Any) -> Decimal | None:
        """Parse a value to Decimal, returning None for empty/None."""
        if value is None or value == "":
            return None
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError):
            raise AdCPValidationError(f"Invalid decimal value: {value}")

    def _to_dict(self, limit: CurrencyLimit) -> dict[str, Any]:
        """Convert CurrencyLimit ORM object to dict."""
        return {
            "tenant_id": limit.tenant_id,
            "currency_code": limit.currency_code,
            "min_package_budget": float(limit.min_package_budget) if limit.min_package_budget is not None else None,
            "max_daily_package_spend": (
                float(limit.max_daily_package_spend) if limit.max_daily_package_spend is not None else None
            ),
            "created_at": limit.created_at.isoformat() if limit.created_at else None,
            "updated_at": limit.updated_at.isoformat() if limit.updated_at else None,
        }
