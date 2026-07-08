"""Helper functions for working with generated schemas.

This module provides convenience functions for constructing complex generated schemas
without losing type safety. Unlike adapters (which wrap schemas in dict[str, Any]),
these helpers work directly with the generated Pydantic models.

Philosophy:
- Generated schemas are the source of truth (always in sync with AdCP spec)
- Helpers make construction easier without sacrificing type safety
- Custom logic (validators, conversions) lives here, not in wrapper classes
"""

from typing import Any

# FIXME(#1388): GetProductsResponse, Product have local subclasses; import from src.core.schemas.
from adcp import CreativeFilters, GetProductsResponse, Product

# FIXME(#1388): ProductFilters has a local subclass; import from src.core.schemas.
from adcp.types import (
    AccountReference,
    BrandReference,
    ContextObject,
    ProductFilters,
    PropertyListReference,
    PushNotificationConfig,
    ReportingWebhook,
)
from pydantic import BaseModel

from src.core.schemas.product import GetProductsRequest
from src.core.validation_helpers import adcp_validation_boundary


def _coerce_wire_object[ModelT: BaseModel](value: Any, model_cls: type[ModelT], context: str) -> ModelT | None:
    """Shared dict → typed-model coercion with the boundary BUILT IN.

    Single home for the ``to_*`` helpers' isinstance ladder. The internal
    ``adcp_validation_boundary`` means a malformed wire dict rejects as a
    typed ``AdCPValidationError`` (message + field + top-level suggestion)
    from EVERY call site — callers cannot forget the boundary
    (salesagent-oygh; mirrors ``coerce_creative_filters``).

    Returns ``None`` for non-dict unexpected types, preserving the helpers'
    long-standing fallback behavior.
    """
    if value is None or isinstance(value, model_cls):
        return value
    if isinstance(value, dict):
        with adcp_validation_boundary(context=context):
            # model_validate handles plain models and RootModels alike
            # (AccountReference is a RootModel — field-unpacking would break it).
            return model_cls.model_validate(value)
    return None  # Fallback for unexpected types


def to_context_object(context: dict[str, Any] | ContextObject | None) -> ContextObject | None:
    """Convert dict context to ContextObject for adcp 2.12.0+ compatibility."""
    return _coerce_wire_object(context, ContextObject, "context value")


def to_reporting_webhook(webhook: dict[str, Any] | ReportingWebhook | None) -> ReportingWebhook | None:
    """Convert dict to ReportingWebhook for adcp type compatibility."""
    return _coerce_wire_object(webhook, ReportingWebhook, "reporting_webhook value")


def to_push_notification_config(
    config: dict[str, Any] | PushNotificationConfig | None,
) -> PushNotificationConfig | None:
    """Convert dict to PushNotificationConfig for adcp type compatibility."""
    return _coerce_wire_object(config, PushNotificationConfig, "push_notification_config value")


def to_brand_reference(brand: dict[str, Any] | BrandReference | str | None) -> BrandReference | None:
    """Convert dict/string brand to BrandReference for adcp 3.6.0 compatibility.

    Accepts the AdCP v3 string domain shorthand (``"acme.com"``) in addition
    to the dict / typed forms the other coercions take.
    """
    if isinstance(brand, str):
        return BrandReference(domain=brand)
    return _coerce_wire_object(brand, BrandReference, "brand value")


def to_account_reference(account: dict[str, Any] | AccountReference | None) -> AccountReference | None:
    """Convert dict to AccountReference for adcp compatibility."""
    return _coerce_wire_object(account, AccountReference, "account value")


def to_property_list_reference(
    property_list: dict[str, Any] | PropertyListReference | None,
) -> PropertyListReference | None:
    """Convert dict to PropertyListReference for adcp compatibility."""
    return _coerce_wire_object(property_list, PropertyListReference, "property_list value")


def coerce_creative_filters(filters: dict[str, Any] | CreativeFilters | None) -> CreativeFilters | None:
    """Coerce a raw list_creatives filters value into a typed CreativeFilters.

    Single source of truth for the dict -> CreativeFilters boundary so REST and
    A2A coerce identically (the MCP transport coerces via FastMCP's TypeAdapter on
    the tool signature).

    A malformed filter (e.g. ``concept_ids`` with an empty array, violating the
    schema's ``minItems: 1``) is raised as a *typed* ``AdCPValidationError`` carrying
    a recovery suggestion, so every transport surfaces the spec's two-layer
    ``VALIDATION_ERROR`` envelope (with a suggestion, per POST-F3). Constructing the
    model directly instead (as the ``to_*`` converters above do, via ``Model(**dict)``)
    surfaces a raw pydantic ``ValidationError`` that ``normalize_to_adcp_error``
    flattens into a suggestion-less envelope.

    Args:
        filters: Filters as a wire dict, an already-typed CreativeFilters, or None.

    Returns:
        CreativeFilters or None (when no filter was supplied).

    Raises:
        AdCPValidationError: when ``filters`` is a dict that fails CreativeFilters validation.
    """
    if filters is None or isinstance(filters, CreativeFilters):
        return filters
    with adcp_validation_boundary(context="list_creatives filters"):
        return CreativeFilters.model_validate(filters)


def create_get_products_request(
    brief: str = "",
    brand: dict[str, Any] | BrandReference | str | None = None,
    filters: dict[str, Any] | ProductFilters | None = None,
    property_list: dict[str, Any] | PropertyListReference | None = None,
    context: dict[str, Any] | ContextObject | None = None,
) -> GetProductsRequest:
    """Create GetProductsRequest aligned with adcp v3.6.0 spec.

    Args:
        brief: Natural language description of campaign requirements
        brand: Brand reference per adcp 3.6.0 (BrandReference or dict with domain field).
               Example: BrandReference(domain="acme.com") or {"domain": "acme.com"}
        filters: Structured filters for product discovery (dict or ProductFilters)
        property_list: Property list reference for filtering by buyer's property list
        context: Application-level context (dict or ContextObject)

    Returns:
        GetProductsRequest

    Examples:
        >>> req = create_get_products_request(
        ...     brand=BrandReference(domain="acme.com"),
        ...     brief="Display ads"
        ... )
    """
    # Handle filters - can be dict, ProductFilters, or None
    filters_obj: ProductFilters | None = None
    if filters is not None:
        if isinstance(filters, ProductFilters):
            filters_obj = filters
        elif isinstance(filters, dict):
            filters_obj = ProductFilters(**filters)

    return GetProductsRequest(  # type: ignore[call-arg]
        brand=to_brand_reference(brand),
        brief=brief or None,
        filters=filters_obj,
        property_list=to_property_list_reference(property_list),
        context=to_context_object(context),
    )


# Re-export commonly used generated types for convenience
__all__ = [
    "to_account_reference",
    "to_brand_reference",
    "to_context_object",
    "to_reporting_webhook",
    "coerce_creative_filters",
    "create_get_products_request",
    # Re-export types for type hints
    "BrandReference",
    "CreativeFilters",
    "GetProductsRequest",
    "GetProductsResponse",
    "Product",
    "ContextObject",
    "ReportingWebhook",
]
