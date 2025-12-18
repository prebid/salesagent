"""
Factory classes for generating test objects.

These factories provide consistent, customizable test data generation.
"""

import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any


class TenantFactory:
    """Factory for creating test tenants."""

    @staticmethod
    def create(
        tenant_id: str | None = None,
        name: str | None = None,
        subdomain: str | None = None,
        is_active: bool = True,
        config: dict[str, Any] | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        """Create a test tenant."""
        tenant_id = tenant_id or f"tenant_{uuid.uuid4().hex[:8]}"
        name = name or f"Test Publisher {tenant_id[-4:]}"
        subdomain = subdomain or f"pub{tenant_id[-4:]}"

        default_config = {
            "adapters": {"mock": {"enabled": True, "manual_approval_required": False}},
            "creative_engine": {
                "auto_approve_format_ids": ["display_300x250", "display_728x90"],
                "human_review_required": False,
            },
            "features": {"max_daily_budget": 10000, "enable_axe_signals": True},
            "authorized_emails": ["test@example.com"],
            "authorized_domains": ["example.com"],
        }

        if config:
            default_config.update(config)

        return {
            "tenant_id": tenant_id,
            "name": name,
            "subdomain": subdomain,
            "is_active": is_active,
            "config": json.dumps(default_config) if isinstance(default_config, dict) else default_config,
            "billing_plan": kwargs.get("billing_plan", "standard"),
            "ad_server": kwargs.get("ad_server", "mock"),
            "created_at": kwargs.get("created_at", datetime.now(UTC).isoformat()),
            "updated_at": kwargs.get("updated_at", datetime.now(UTC).isoformat()),
            **kwargs,
        }

    @staticmethod
    def create_batch(count: int = 3, **kwargs) -> list[dict[str, Any]]:
        """Create multiple test tenants."""
        return [TenantFactory.create(**kwargs) for _ in range(count)]


class PrincipalFactory:
    """Factory for creating test principals (advertisers)."""

    @staticmethod
    def create(
        tenant_id: str | None = None,
        principal_id: str | None = None,
        name: str | None = None,
        access_token: str | None = None,
        platform_mappings: dict[str, Any] | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        """Create a test principal."""
        tenant_id = tenant_id or f"tenant_{uuid.uuid4().hex[:8]}"
        principal_id = principal_id or f"principal_{uuid.uuid4().hex[:8]}"
        name = name or f"Test Advertiser {principal_id[-4:]}"
        access_token = access_token or f"token_{secrets.token_urlsafe(32)}"

        default_mappings = {"mock": {"advertiser_id": f"mock_adv_{uuid.uuid4().hex[:8]}"}}

        if platform_mappings:
            default_mappings.update(platform_mappings)

        return {
            "tenant_id": tenant_id,
            "principal_id": principal_id,
            "name": name,
            "access_token": access_token,
            "platform_mappings": (
                json.dumps(default_mappings) if isinstance(default_mappings, dict) else default_mappings
            ),
            "is_active": kwargs.get("is_active", True),
            "created_at": kwargs.get("created_at", datetime.now(UTC).isoformat()),
            "updated_at": kwargs.get("updated_at", datetime.now(UTC).isoformat()),
            **kwargs,
        }

    @staticmethod
    def create_with_tenant() -> tuple:
        """Create a principal with its associated tenant."""
        tenant = TenantFactory.create()
        principal = PrincipalFactory.create(tenant_id=tenant["tenant_id"])
        return tenant, principal


class ProductFactory:
    """Factory for creating test products."""

    @staticmethod
    def create(
        tenant_id: str | None = None,
        product_id: str | None = None,
        name: str | None = None,
        format_ids: list[dict[str, str]] | None = None,
        targeting_template: dict[str, Any] | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        """Create a test product with AdCP-compliant format_ids."""
        tenant_id = tenant_id or f"tenant_{uuid.uuid4().hex[:8]}"
        product_id = product_id or f"prod_{uuid.uuid4().hex[:8]}"
        name = name or f"Test Product {product_id[-4:]}"

        # Default: AdCP-compliant FormatId objects
        default_format_ids = [
            {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"},
            {"agent_url": "https://creative.adcontextprotocol.org", "id": "display_728x90"},
        ]
        format_ids = format_ids or default_format_ids

        default_targeting = {"geo_country": ["US", "CA"], "device_type": ["desktop", "mobile"], "viewability": 0.7}

        if targeting_template:
            default_targeting.update(targeting_template)

        return {
            "tenant_id": tenant_id,
            "product_id": product_id,
            "name": name,
            "description": kwargs.get("description", f"Description for {name}"),
            "type": kwargs.get("type", "guaranteed"),
            "format_ids": json.dumps(format_ids) if isinstance(format_ids, list) else format_ids,
            "targeting_template": (
                json.dumps(default_targeting) if isinstance(default_targeting, dict) else default_targeting
            ),
            "delivery_type": kwargs.get("delivery_type", "guaranteed"),
            "min_spend": kwargs.get("min_spend", 1000.0),
            "currency": kwargs.get("currency", "USD"),
            "inventory_type": kwargs.get("inventory_type", "display"),
            "is_active": kwargs.get("is_active", True),
            "created_at": kwargs.get("created_at", datetime.now(UTC).isoformat()),
            **kwargs,
        }

    @staticmethod
    def create_video_product(**kwargs) -> dict[str, Any]:
        """Create a video product with video format_ids."""
        video_defaults = {
            "format_ids": [
                {"agent_url": "https://creative.adcontextprotocol.org", "id": "video_16x9"},
                {"agent_url": "https://creative.adcontextprotocol.org", "id": "video_outstream"},
            ],
            "inventory_type": "video",
        }
        video_defaults.update(kwargs)
        return ProductFactory.create(**video_defaults)

    @staticmethod
    def create_batch(count: int = 3, **kwargs) -> list[dict[str, Any]]:
        """Create multiple test products."""
        return [ProductFactory.create(**kwargs) for _ in range(count)]


class MediaBuyFactory:
    """Factory for creating test media buys."""

    @staticmethod
    def create(
        tenant_id: str | None = None,
        media_buy_id: str | None = None,
        principal_id: str | None = None,
        status: str = "draft",
        **kwargs,
    ) -> dict[str, Any]:
        """Create a test media buy."""
        tenant_id = tenant_id or f"tenant_{uuid.uuid4().hex[:8]}"
        media_buy_id = media_buy_id or f"mb_{uuid.uuid4().hex[:8]}"
        principal_id = principal_id or f"principal_{uuid.uuid4().hex[:8]}"

        flight_start = kwargs.get("flight_start_date", datetime.now(UTC).date())
        flight_end = kwargs.get("flight_end_date", (datetime.now(UTC) + timedelta(days=30)).date())

        default_config = {
            "packages": [
                {
                    "package_id": f"pkg_{uuid.uuid4().hex[:8]}",
                    "product_id": f"prod_{uuid.uuid4().hex[:8]}",
                    "budget_amount": 5000.0,
                    "cpm": 10.0,
                    "targeting_overlay": {},
                }
            ]
        }

        config = kwargs.get("config", default_config)

        return {
            "tenant_id": tenant_id,
            "media_buy_id": media_buy_id,
            "principal_id": principal_id,
            "status": status,
            "config": json.dumps(config) if isinstance(config, dict) else config,
            "total_budget": kwargs.get("total_budget", 5000.0),
            "spent_amount": kwargs.get("spent_amount", 0.0),
            "flight_start_date": flight_start.isoformat() if hasattr(flight_start, "isoformat") else flight_start,
            "flight_end_date": flight_end.isoformat() if hasattr(flight_end, "isoformat") else flight_end,
            "created_at": kwargs.get("created_at", datetime.now(UTC).isoformat()),
            "updated_at": kwargs.get("updated_at", datetime.now(UTC).isoformat()),
            **kwargs,
        }

    @staticmethod
    def create_active(**kwargs) -> dict[str, Any]:
        """Create an active media buy."""
        kwargs["status"] = "active"
        kwargs["spent_amount"] = kwargs.get("total_budget", 5000.0) * 0.3  # 30% spent
        return MediaBuyFactory.create(**kwargs)


class CreativeFactory:
    """Factory for creating test creatives."""

    @staticmethod
    def create(
        tenant_id: str | None = None,
        creative_id: str | None = None,
        principal_id: str | None = None,
        format_id: str = "display_300x250",
        status: str = "pending",
        **kwargs,
    ) -> dict[str, Any]:
        """Create a test creative."""
        tenant_id = tenant_id or f"tenant_{uuid.uuid4().hex[:8]}"
        creative_id = creative_id or f"creative_{uuid.uuid4().hex[:8]}"
        principal_id = principal_id or f"principal_{uuid.uuid4().hex[:8]}"

        default_content = {
            "headline": "Test Ad Headline",
            "body": "This is a test advertisement.",
            "image_url": "https://example.com/image.jpg",
            "click_url": "https://example.com/landing",
            "advertiser": "Test Advertiser",
        }

        content = kwargs.get("content", default_content)

        return {
            "tenant_id": tenant_id,
            "creative_id": creative_id,
            "principal_id": principal_id,
            "format_id": format_id,
            "status": status,
            "content": json.dumps(content) if isinstance(content, dict) else content,
            "name": kwargs.get("name", f"Test Creative {creative_id[-4:]}"),
            "created_at": kwargs.get("created_at", datetime.now(UTC).isoformat()),
            "updated_at": kwargs.get("updated_at", datetime.now(UTC).isoformat()),
            "approved_at": kwargs.get("approved_at"),
            "approved_by": kwargs.get("approved_by"),
            **kwargs,
        }

    @staticmethod
    def create_approved(**kwargs) -> dict[str, Any]:
        """Create an approved creative."""
        kwargs["status"] = "approved"
        kwargs["approved_at"] = datetime.now(UTC).isoformat()
        kwargs["approved_by"] = "auto_approval"
        return CreativeFactory.create(**kwargs)

    @staticmethod
    def create_video_creative(**kwargs) -> dict[str, Any]:
        """Create a video creative."""
        video_content = {
            "video_url": "https://example.com/video.mp4",
            "duration": 30,
            "click_url": "https://example.com/landing",
            "advertiser": "Test Advertiser",
        }
        kwargs["format_id"] = "video_16x9"
        kwargs["content"] = video_content
        return CreativeFactory.create(**kwargs)
