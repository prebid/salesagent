"""Factory_boy factory for FormatPerformanceMetrics model.

Used by dynamic pricing integration tests that need real metrics data.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import factory
from factory import LazyAttribute, SubFactory

from src.core.database.models import FormatPerformanceMetrics


class FormatPerformanceMetricsFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = FormatPerformanceMetrics
        sqlalchemy_session = None  # Bound dynamically by IntegrationEnv
        sqlalchemy_session_persistence = "commit"

    tenant = SubFactory("tests.factories.core.TenantFactory")
    tenant_id = LazyAttribute(lambda o: o.tenant.tenant_id)
    creative_size = "300x250"
    country_code = None

    period_start = factory.LazyFunction(lambda: (datetime.now(UTC) - timedelta(days=14)).date())
    period_end = factory.LazyFunction(lambda: datetime.now(UTC).date())

    total_impressions = 500_000
    total_clicks = 2_500
    total_revenue_micros = 3_500_000_000  # $3,500

    average_cpm = Decimal("7.00")
    median_cpm = Decimal("5.50")
    p75_cpm = Decimal("8.25")
    p90_cpm = Decimal("12.00")

    line_item_count = 15
