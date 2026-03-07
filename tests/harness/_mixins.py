"""Domain mixins — shared fluent API for integration and unit test environments.

Each mixin provides the domain-specific helper methods (set_*, call_*, get_*)
that are identical across integration and unit variants. Concrete env classes
inherit from both a base (BaseTestEnv or IntegrationEnv) and a mixin.

Mixins don't define ``__init__`` — concrete classes set up required state.
Mixins may call ``self._commit_factory_data()`` which is a no-op in unit mode.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

from adcp import GetProductsRequest as GetProductsRequestGenerated

from src.core.schemas import (
    AdapterGetMediaBuyDeliveryResponse,
    AdapterPackageDelivery,
    DeliveryTotals,
    GetMediaBuyDeliveryRequest,
    GetMediaBuyDeliveryResponse,
    GetProductsResponse,
    ReportingPeriod,
)
from src.core.tools.media_buy_delivery import _get_media_buy_delivery_impl
from src.core.tools.products import _get_products_impl
from src.core.webhook_delivery import WebhookDelivery, deliver_webhook_with_retry
from src.services.webhook_delivery_service import (
    CircuitBreaker,
    WebhookDeliveryService,
)


class DeliveryPollMixin:
    """Shared fluent API for delivery poll testing.

    Requires concrete class to set ``self._adapter_responses: dict`` in __init__.
    """

    _adapter_responses: dict[str, AdapterGetMediaBuyDeliveryResponse]

    def _configure_adapter_mock(self) -> None:
        """Wire adapter mock with side_effect lookup. Call from _configure_mocks."""
        mock_adapter = MagicMock()
        mock_adapter.get_media_buy_delivery.side_effect = self._adapter_lookup
        self.mock["adapter"].return_value = mock_adapter  # type: ignore[attr-defined]

    def _adapter_lookup(self, *args: Any, **kwargs: Any) -> AdapterGetMediaBuyDeliveryResponse:
        """Look up configured adapter response by media_buy_id.

        Raises KeyError for unregistered IDs when other IDs are registered,
        preventing tests from silently succeeding with wrong data.
        """
        mb_id = kwargs.get("media_buy_id") or (args[0] if args else None)
        if mb_id and mb_id in self._adapter_responses:
            return self._adapter_responses[mb_id]
        if self._adapter_responses:
            raise KeyError(
                f"No adapter response registered for media_buy_id={mb_id!r}. "
                f"Registered: {list(self._adapter_responses.keys())}. "
                f"Call env.set_adapter_response({mb_id!r}, ...) first."
            )
        return self._make_default_adapter_response()

    def set_adapter_response(
        self,
        media_buy_id: str = "mb_001",
        impressions: int = 5000,
        spend: float = 250.0,
        package_id: str = "pkg_001",
        clicks: int | None = None,
        packages: list[dict[str, Any]] | None = None,
    ) -> None:
        """Configure adapter to return specific delivery data for a media buy.

        For single-package responses, use the scalar parameters (backward compatible).
        For multi-package responses, pass ``packages`` — a list of dicts with
        ``package_id``, ``impressions``, and ``spend`` keys. Totals are auto-computed
        as the sum of per-package values.
        """
        if packages is not None:
            by_package = [
                AdapterPackageDelivery(
                    package_id=p["package_id"],
                    impressions=p.get("impressions", 0),
                    spend=p.get("spend", 0.0),
                )
                for p in packages
            ]
            total_impressions = float(sum(p.get("impressions", 0) for p in packages))
            total_spend = float(sum(p.get("spend", 0.0) for p in packages))
            totals = DeliveryTotals(impressions=total_impressions, spend=total_spend)
        else:
            by_package = [
                AdapterPackageDelivery(
                    package_id=package_id,
                    impressions=impressions,
                    spend=spend,
                )
            ]
            totals = DeliveryTotals(impressions=float(impressions), spend=spend)

        if clicks is not None:
            totals.clicks = float(clicks)

        self._adapter_responses[media_buy_id] = AdapterGetMediaBuyDeliveryResponse(
            media_buy_id=media_buy_id,
            reporting_period=ReportingPeriod(
                start=datetime(2025, 1, 1, tzinfo=UTC),
                end=datetime(2025, 12, 31, tzinfo=UTC),
            ),
            totals=totals,
            by_package=by_package,
            currency="USD",
        )

    def set_adapter_error(self, exception: Exception) -> None:
        """Make the adapter raise the given exception on get_media_buy_delivery."""
        self.mock["adapter"].return_value.get_media_buy_delivery.side_effect = exception  # type: ignore[attr-defined]

    def call_impl(
        self,
        media_buy_ids: list[str] | None = None,
        buyer_refs: list[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        status_filter: list[str] | None = None,
        **extra: Any,
    ) -> GetMediaBuyDeliveryResponse:
        """Call _get_media_buy_delivery_impl with the given parameters."""
        self._commit_factory_data()  # type: ignore[attr-defined]

        kwargs: dict[str, Any] = {}
        if media_buy_ids is not None:
            kwargs["media_buy_ids"] = media_buy_ids
        if buyer_refs is not None:
            kwargs["buyer_refs"] = buyer_refs
        if start_date is not None:
            kwargs["start_date"] = start_date
        if end_date is not None:
            kwargs["end_date"] = end_date
        if status_filter is not None:
            kwargs["status_filter"] = status_filter
        kwargs.update(extra)

        req = GetMediaBuyDeliveryRequest(**kwargs)
        return _get_media_buy_delivery_impl(req, self.identity)  # type: ignore[attr-defined]

    @staticmethod
    def _make_default_adapter_response() -> AdapterGetMediaBuyDeliveryResponse:
        return AdapterGetMediaBuyDeliveryResponse(
            media_buy_id="mb_001",
            reporting_period=ReportingPeriod(
                start=datetime(2025, 1, 1, tzinfo=UTC),
                end=datetime(2025, 12, 31, tzinfo=UTC),
            ),
            totals=DeliveryTotals(impressions=5000.0, spend=250.0),
            by_package=[AdapterPackageDelivery(package_id="pkg_001", impressions=5000, spend=250.0)],
            currency="USD",
        )


class WebhookMixin:
    """Shared fluent API for webhook delivery testing."""

    def set_http_status(self, code: int, text: str = "") -> None:
        """Configure requests.post to return a single response with the given status."""
        mock_response = MagicMock()
        mock_response.status_code = code
        mock_response.text = text or f"Status {code}"
        self.mock["post"].return_value = mock_response  # type: ignore[attr-defined]
        self.mock["post"].side_effect = None  # type: ignore[attr-defined]

    def set_http_sequence(self, responses: list[tuple[int, str]]) -> None:
        """Configure requests.post to return a sequence of responses.

        Args:
            responses: List of (status_code, text) tuples.
        """
        mocks = []
        for code, text in responses:
            r = MagicMock()
            r.status_code = code
            r.text = text
            mocks.append(r)
        self.mock["post"].side_effect = mocks  # type: ignore[attr-defined]

    def set_http_error(self, exception: Exception) -> None:
        """Make requests.post raise the given exception."""
        self.mock["post"].side_effect = exception  # type: ignore[attr-defined]

    def set_url_invalid(self, error_msg: str = "Invalid URL") -> None:
        """Make URL validation fail, short-circuiting delivery."""
        self.mock["validate"].return_value = (False, error_msg)  # type: ignore[attr-defined]

    def call_deliver(
        self,
        webhook_url: str = "https://example.com/webhook",
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        signing_secret: str | None = None,
        max_retries: int = 3,
        timeout: int = 10,
        event_type: str | None = None,
        tenant_id: str | None = None,
        object_id: str | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        """Call deliver_webhook_with_retry with the given parameters."""
        self._commit_factory_data()  # type: ignore[attr-defined]
        if payload is None:
            payload = {"event": "delivery.update", "media_buy_id": "mb_001"}
        if headers is None:
            headers = {"Content-Type": "application/json"}
        delivery = WebhookDelivery(
            webhook_url=webhook_url,
            payload=payload,
            headers=headers,
            signing_secret=signing_secret,
            max_retries=max_retries,
            timeout=timeout,
            event_type=event_type,
            tenant_id=tenant_id,
            object_id=object_id,
        )
        return deliver_webhook_with_retry(delivery)

    def call_impl(self, **kwargs: Any) -> Any:
        """Alias for call_deliver to satisfy BaseTestEnv interface."""
        return self.call_deliver(**kwargs)


class CircuitBreakerMixin:
    """Shared fluent API for circuit breaker / webhook delivery service testing."""

    _service: WebhookDeliveryService | None

    def get_service(self) -> WebhookDeliveryService:
        """Return a WebhookDeliveryService instance (cached per env)."""
        if self._service is None:
            self._service = WebhookDeliveryService()
        return self._service

    def get_breaker(self, **kwargs: Any) -> CircuitBreaker:
        """Return a fresh CircuitBreaker instance with the given params."""
        return CircuitBreaker(**kwargs)

    def set_http_response(self, status_code: int) -> None:
        """Configure the httpx Client mock to return the given status code."""
        mock_response = MagicMock()
        mock_response.status_code = status_code
        self.mock["client"].return_value.__enter__.return_value.post.return_value = mock_response  # type: ignore[attr-defined]

    def call_send(
        self,
        media_buy_id: str = "mb_001",
        tenant_id: str | None = None,
        principal_id: str | None = None,
        reporting_period_start: datetime | None = None,
        reporting_period_end: datetime | None = None,
        impressions: float = 1000.0,
        spend: float = 100.0,
        **extra: Any,
    ) -> bool:
        """Call service.send_delivery_webhook with sensible defaults."""
        self._commit_factory_data()  # type: ignore[attr-defined]
        service = self.get_service()
        return service.send_delivery_webhook(
            media_buy_id=media_buy_id,
            tenant_id=tenant_id or self._tenant_id,  # type: ignore[attr-defined]
            principal_id=principal_id or self._principal_id,  # type: ignore[attr-defined]
            reporting_period_start=reporting_period_start or datetime(2025, 1, 1, tzinfo=UTC),
            reporting_period_end=reporting_period_end or datetime(2025, 12, 31, tzinfo=UTC),
            impressions=impressions,
            spend=spend,
            **extra,
        )

    def call_impl(self, **kwargs: Any) -> bool:
        """Alias for call_send to satisfy BaseTestEnv interface."""
        return self.call_send(**kwargs)


class ProductMixin:
    """Shared fluent API for _get_products_impl testing.

    Requires concrete class to define EXTERNAL_PATCHES with these keys:
        "policy_service", "dynamic_variants", "ranking_factory",
        "dynamic_pricing", "resolve_property_list"

    And ASYNC_PATCHES containing at least:
        {"dynamic_variants", "resolve_property_list"}

    Fluent API:
        set_policy_approved()            -- policy check returns approved
        set_policy_blocked(reason)       -- policy check returns blocked
        set_dynamic_variants(variants)   -- configure dynamic variant generation
        set_property_list(ids)           -- configure property list resolver
        set_ranking_disabled()           -- disable AI ranking
        call_impl(brief, **kw)           -- call _get_products_impl
    """

    def set_policy_approved(self) -> None:
        """Configure PolicyCheckService to approve the brief.

        Note: Policy checks are only invoked when the tenant dict has
        ``advertising_policy.enabled = True`` AND ``gemini_api_key`` set.
        By default the harness identity has neither, so this is a no-op
        unless the test explicitly configures the tenant.
        """
        from unittest.mock import AsyncMock

        mock_result = MagicMock(status="approved", reason=None, restrictions=[])
        mock_instance = MagicMock()
        mock_instance.check_brief_compliance = AsyncMock(return_value=mock_result)
        self.mock["policy_service"].return_value = mock_instance  # type: ignore[attr-defined]

    def set_policy_blocked(self, reason: str = "Policy violation") -> None:
        """Configure PolicyCheckService to block the brief."""
        from unittest.mock import AsyncMock

        from src.services.policy_check_service import PolicyStatus

        mock_result = MagicMock(status=PolicyStatus.BLOCKED, reason=reason, restrictions=[])
        mock_instance = MagicMock()
        mock_instance.check_brief_compliance = AsyncMock(return_value=mock_result)
        self.mock["policy_service"].return_value = mock_instance  # type: ignore[attr-defined]

    def set_dynamic_variants(self, variants: list[Any] | None = None) -> None:
        """Configure generate_variants_for_brief to return specific variants.

        Args:
            variants: List of Product model instances to return. Defaults to [].
        """
        self.mock["dynamic_variants"].return_value = variants or []  # type: ignore[attr-defined]

    def set_property_list(self, property_ids: list[str] | None = None) -> None:
        """Configure resolve_property_list to return specific property IDs.

        Args:
            property_ids: List of property identifier strings. Defaults to [].
        """
        self.mock["resolve_property_list"].return_value = property_ids or []  # type: ignore[attr-defined]

    def set_ranking_disabled(self) -> None:
        """Disable AI ranking by making the factory report AI as not enabled."""
        mock_factory = MagicMock()
        mock_factory.is_ai_enabled.return_value = False
        self.mock["ranking_factory"].return_value = mock_factory  # type: ignore[attr-defined]

    def _configure_product_mocks(self) -> None:
        """Wire default happy-path mocks for product testing.

        Call from _configure_mocks() in concrete classes.

        Defaults:
        - PolicyCheckService: not invoked (no gemini_api_key in tenant dict)
        - Dynamic variants: returns [] (already AsyncMock via ASYNC_PATCHES)
        - DynamicPricingService: pass-through in unit mode, real in integration mode
        - Property list resolver: returns [] (already AsyncMock via ASYNC_PATCHES)
        - Ranking factory: AI not enabled
        """
        # Dynamic variants: returns empty list (AsyncMock from ASYNC_PATCHES)
        self.mock["dynamic_variants"].return_value = []  # type: ignore[attr-defined]

        # DynamicPricingService: configure pass-through mock in unit mode only.
        # In integration mode (ProductEnv from product.py), dynamic_pricing is NOT
        # in EXTERNAL_PATCHES, so self.mock won't have it — runs against real DB.
        if "dynamic_pricing" in self.mock:  # type: ignore[attr-defined]
            mock_pricing_instance = MagicMock()
            mock_pricing_instance.enrich_products_with_pricing.side_effect = lambda products, **kw: products
            self.mock["dynamic_pricing"].return_value = mock_pricing_instance  # type: ignore[attr-defined]

        # Ranking factory: AI not enabled
        self.set_ranking_disabled()

        # Property list resolver: returns [] (AsyncMock from ASYNC_PATCHES)
        self.mock["resolve_property_list"].return_value = []  # type: ignore[attr-defined]

    async def call_impl(  # type: ignore[override]
        self,
        brief: str = "test brief",
        brand: dict[str, Any] | None = None,
        filters: dict[str, Any] | None = None,
        property_list: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
        **extra: Any,
    ) -> GetProductsResponse:
        """Call _get_products_impl with the given parameters.

        Args:
            brief: Search brief text.
            brand: Brand reference dict (defaults to {"domain": "test.com"}).
            filters: ProductFilters dict.
            property_list: PropertyListReference dict.
            context: ContextObject dict.
            **extra: Additional kwargs forwarded to request construction.

        Returns:
            GetProductsResponse from the impl function.
        """
        self._commit_factory_data()  # type: ignore[attr-defined]

        if brand is None:
            brand = {"domain": "test.com"}

        req = GetProductsRequestGenerated(
            brief=brief,
            brand=brand,
            filters=filters,
            property_list=property_list,
            context=context,
            **extra,
        )
        return await _get_products_impl(req, self.identity)  # type: ignore[attr-defined]
