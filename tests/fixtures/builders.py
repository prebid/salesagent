"""
Builder classes for constructing complex test objects.

These builders provide fluent interfaces for creating test data.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any


class RequestBuilder:
    """Builder for constructing API request objects."""

    def __init__(self):
        """Initialize request builder."""
        self.data = {}
        self.headers = {}

    def with_auth(self, token: str):
        """Add authentication."""
        self.headers["x-adcp-auth"] = token
        return self

    def with_tenant(self, tenant_id: str):
        """Add tenant context."""
        self.headers["x-tenant-id"] = tenant_id
        return self

    def with_data(self, **kwargs):
        """Add request data."""
        self.data.update(kwargs)
        return self

    def with_media_buy(
        self,
        product_ids: list[str] = None,
        total_budget: float = 5000.0,
        flight_start_date: str = None,
        flight_end_date: str = None,
        **kwargs,
    ):
        """Add media buy data."""
        self.data.update(
            {
                "product_ids": product_ids or [f"prod_{uuid.uuid4().hex[:8]}"],
                "total_budget": total_budget,
                "flight_start_date": flight_start_date or datetime.now(UTC).date().isoformat(),
                "flight_end_date": flight_end_date or (datetime.now(UTC) + timedelta(days=30)).date().isoformat(),
                **kwargs,
            }
        )
        return self

    def with_creative(self, format_id: str = "display_300x250", content: dict = None, **kwargs):
        """Add creative data."""
        default_content = {
            "headline": "Test Ad",
            "body": "Test ad content",
            "image_url": "https://example.com/image.jpg",
            "click_url": "https://example.com/landing",
        }

        self.data.update({"format_id": format_id, "content": content or default_content, **kwargs})
        return self

    def with_targeting(self, targeting: dict):
        """Add targeting overlay."""
        self.data["targeting_overlay"] = targeting
        return self

    def build(self) -> dict:
        """Build the request object."""
        return {"headers": self.headers, "data": self.data}

    def build_json(self) -> str:
        """Build as JSON string."""
        return json.dumps(self.build())


class ResponseBuilder:
    """Builder for constructing API response objects."""

    def __init__(self):
        """Initialize response builder."""
        self.data = {"success": True, "timestamp": datetime.now(UTC).isoformat()}
        self.status_code = 200

    def with_success(self, success: bool = True):
        """Set success status."""
        self.data["success"] = success
        if not success:
            self.status_code = 400
        return self

    def with_error(self, error: str, code: int = 400):
        """Add error response."""
        self.data["success"] = False
        self.data["error"] = error
        self.status_code = code
        return self

    def with_data(self, **kwargs):
        """Add response data."""
        self.data.update(kwargs)
        return self

    def with_media_buy(self, media_buy_id: str = None, **kwargs):
        """Add media buy response data."""
        self.data.update(
            {
                "media_buy_id": media_buy_id or f"mb_{uuid.uuid4().hex[:8]}",
                "status": "created",
                "created_at": datetime.now(UTC).isoformat(),
                **kwargs,
            }
        )
        return self

    def with_creative(self, creative_id: str = None, **kwargs):
        """Add creative response data."""
        self.data.update(
            {
                "creative_id": creative_id or f"creative_{uuid.uuid4().hex[:8]}",
                "status": "pending",
                "created_at": datetime.now(UTC).isoformat(),
                **kwargs,
            }
        )
        return self

    def with_products(self, products: list[dict] = None):
        """Add products list."""
        if products is None:
            products = [
                {"product_id": f"prod_{i}", "name": f"Product {i}", "formats": ["display_300x250"], "min_cpm": 5.0}
                for i in range(3)
            ]
        self.data["products"] = products
        return self

    def with_status_code(self, code: int):
        """Set HTTP status code."""
        self.status_code = code
        return self

    def build(self) -> dict:
        """Build the response object."""
        return {"data": self.data, "status_code": self.status_code}

    def build_json(self) -> str:
        """Build as JSON string."""
        return json.dumps(self.data)


class TargetingBuilder:
    """Builder for constructing targeting specifications."""

    def __init__(self):
        """Initialize targeting builder."""
        self.targeting = {}

    def with_geo(
        self,
        countries: list[str] = None,
        regions: list[str] = None,
        cities: list[str] = None,
        zip_codes: list[str] = None,
    ):
        """Add geographic targeting."""
        if countries:
            self.targeting["geo_countries"] = countries
        if regions:
            self.targeting["geo_regions"] = regions
        # cities parameter ignored: city targeting removed in v3
        if zip_codes:
            self.targeting["geo_postal_areas"] = [{"system": "us_zip", "values": zip_codes}]
        return self

    def with_demographics(
        self, age_ranges: list[str] = None, genders: list[str] = None, income_ranges: list[str] = None
    ):
        """Add demographic targeting."""
        if age_ranges:
            self.targeting["demo_age_range_any_of"] = age_ranges
        if genders:
            self.targeting["demo_gender_any_of"] = genders
        if income_ranges:
            self.targeting["demo_income_range_any_of"] = income_ranges
        return self

    def with_devices(self, types: list[str] = None, os: list[str] = None, browsers: list[str] = None):
        """Add device targeting."""
        if types:
            self.targeting["device_type_any_of"] = types
        if os:
            self.targeting["device_os_any_of"] = os
        if browsers:
            self.targeting["browser_any_of"] = browsers
        return self

    def with_content(self, categories: list[str] = None, keywords: list[str] = None, topics: list[str] = None):
        """Add content targeting."""
        if categories:
            self.targeting["content_category_any_of"] = categories
        if keywords:
            self.targeting["content_keyword_any_of"] = keywords
        if topics:
            self.targeting["content_topic_any_of"] = topics
        return self

    def with_signals(self, signals: list[str]):
        """Add AEE signals."""
        self.targeting["signals"] = signals
        return self

    def with_custom(self, key: str, value: Any):
        """Add custom targeting dimension."""
        self.targeting[key] = value
        return self

    def build(self) -> dict:
        """Build the targeting object."""
        return self.targeting

    def build_minimal(self) -> dict:
        """Build minimal targeting for testing."""
        return {"geo_countries": ["US"], "device_type_any_of": ["desktop", "mobile"]}

    def build_comprehensive(self) -> dict:
        """Build comprehensive targeting for testing."""
        return {
            "geo_countries": ["US", "CA"],
            "geo_regions": ["US-CA", "US-NY", "US-TX"],
            "demo_age_range_any_of": ["25-34", "35-44"],
            "demo_gender_any_of": ["all"],
            "device_type_any_of": ["desktop", "mobile", "tablet"],
            "device_os_any_of": ["ios", "android", "windows"],
            "browser_any_of": ["chrome", "safari", "firefox"],
            "content_category_any_of": ["news", "sports", "entertainment"],
            "daypart_timezone": "America/New_York",
            "daypart_days_any_of": ["mon", "tue", "wed", "thu", "fri"],
            "daypart_hours_any_of": list(range(9, 18)),  # 9am-5pm
            "frequency_cap_impressions": 3,
            "frequency_cap_period": "day",
            "viewability": 0.7,
            "brand_safety_sensitive_categories_none_of": ["adult", "violence"],
            "signals": ["auto_intenders_q1_2025", "sports_enthusiasts"],
        }


class TestDataBuilder:
    """Builder for complete test scenarios."""

    def __init__(self):
        """Initialize test data builder."""
        self.tenant = None
        self.principal = None
        self.products = []
        self.media_buys = []
        self.creatives = []

    def with_tenant(self, **kwargs):
        """Add tenant."""
        from .factories import TenantFactory

        self.tenant = TenantFactory.create(**kwargs)
        return self

    def with_principal(self, **kwargs):
        """Add principal."""
        from .factories import PrincipalFactory

        if self.tenant:
            kwargs["tenant_id"] = self.tenant["tenant_id"]
        self.principal = PrincipalFactory.create(**kwargs)
        return self

    def with_products(self, count: int = 3, **kwargs):
        """Add products."""
        from .factories import ProductFactory

        if self.tenant:
            kwargs["tenant_id"] = self.tenant["tenant_id"]
        self.products = [ProductFactory.create(**kwargs) for _ in range(count)]
        return self

    def with_media_buy(self, **kwargs):
        """Add media buy."""
        from .factories import MediaBuyFactory

        if self.tenant:
            kwargs["tenant_id"] = self.tenant["tenant_id"]
        if self.principal:
            kwargs["principal_id"] = self.principal["principal_id"]
        self.media_buys.append(MediaBuyFactory.create(**kwargs))
        return self

    def with_creatives(self, count: int = 2, **kwargs):
        """Add creatives."""
        from .factories import CreativeFactory

        if self.tenant:
            kwargs["tenant_id"] = self.tenant["tenant_id"]
        if self.principal:
            kwargs["principal_id"] = self.principal["principal_id"]
        self.creatives = [CreativeFactory.create(**kwargs) for _ in range(count)]
        return self

    def build(self) -> dict:
        """Build complete test scenario."""
        return {
            "tenant": self.tenant,
            "principal": self.principal,
            "products": self.products,
            "media_buys": self.media_buys,
            "creatives": self.creatives,
        }

    def build_complete_scenario(self) -> dict:
        """Build a complete test scenario with all entities."""
        return (
            self.with_tenant(name="Test Publisher")
            .with_principal(name="Test Advertiser")
            .with_products(count=3)
            .with_media_buy(total_budget=10000.0)
            .with_creatives(count=2)
            .build()
        )


async def create_test_tenant_with_principal(**kwargs) -> dict:
    """Create a test tenant with an associated principal for integration tests."""
    from src.core.database.database_session import get_db_session
    from src.core.database.models import Principal as ModelPrincipal
    from src.core.database.models import Tenant
    from src.core.schemas import Principal

    from .factories import PrincipalFactory

    # Create tenant and principal data
    tenant, principal = PrincipalFactory.create_with_tenant()

    # Apply any overrides from kwargs
    if "tenant_name" in kwargs:
        tenant["name"] = kwargs["tenant_name"]
    if "principal_name" in kwargs:
        principal["name"] = kwargs["principal_name"]

    # Save tenant and principal to database
    with get_db_session() as db_session:
        # Create tenant with correct fields
        from datetime import UTC, datetime

        db_tenant = Tenant(
            tenant_id=tenant["tenant_id"],
            name=tenant["name"],
            subdomain=tenant["subdomain"],
            is_active=tenant["is_active"],
            billing_plan=tenant["billing_plan"],
            ad_server=tenant.get("ad_server", "mock"),
            enable_axe_signals=True,
            authorized_emails=["test@example.com"],
            authorized_domains=["example.com"],
            auto_approve_format_ids=["display_300x250", "display_728x90"],
            human_review_required=False,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        db_session.add(db_tenant)

        # Create principal in database
        platform_mappings = (
            json.loads(principal["platform_mappings"])
            if isinstance(principal["platform_mappings"], str)
            else principal["platform_mappings"]
        )
        db_principal = ModelPrincipal(
            tenant_id=principal["tenant_id"],
            principal_id=principal["principal_id"],
            name=principal["name"],
            access_token=principal["access_token"],
            platform_mappings=platform_mappings,
            created_at=datetime.now(UTC),
        )
        db_session.add(db_principal)
        db_session.commit()

    # Convert principal dict to Principal schema object for compatibility
    principal_obj = Principal(
        tenant_id=principal["tenant_id"],
        principal_id=principal["principal_id"],
        name=principal["name"],
        access_token=principal["access_token"],
        platform_mappings=platform_mappings,
    )

    return {
        "tenant": tenant,
        "principal": principal_obj,  # Return as Principal schema object
    }
