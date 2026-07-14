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
from urllib.parse import urlparse

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
from pydantic import ValidationError

from src.core.exceptions import AdCPValidationError
from src.core.schemas.product import GetProductsRequest
from src.core.validation_helpers import adcp_validation_boundary


def to_context_object(context: dict[str, Any] | ContextObject | None) -> ContextObject | None:
    """Convert dict context to ContextObject for adcp 2.12.0+ compatibility.

    Args:
        context: Context as dict or ContextObject or None

    Returns:
        ContextObject or None
    """
    if context is None:
        return None
    if isinstance(context, ContextObject):
        return context
    if isinstance(context, dict):
        return ContextObject(**context)
    return None  # Fallback for unexpected types


def to_reporting_webhook(webhook: dict[str, Any] | ReportingWebhook | None) -> ReportingWebhook | None:
    """Convert dict to ReportingWebhook for adcp type compatibility.

    Args:
        webhook: Webhook config as dict or ReportingWebhook or None

    Returns:
        ReportingWebhook or None
    """
    if webhook is None:
        return None
    if isinstance(webhook, ReportingWebhook):
        return webhook
    if isinstance(webhook, dict):
        return ReportingWebhook(**webhook)
    return None  # Fallback for unexpected types


def to_push_notification_config(
    config: dict[str, Any] | PushNotificationConfig | None,
) -> PushNotificationConfig | None:
    """Convert dict to PushNotificationConfig for adcp type compatibility.

    Args:
        config: Push notification config as dict or PushNotificationConfig or None

    Returns:
        PushNotificationConfig or None
    """
    if config is None:
        return None
    if isinstance(config, PushNotificationConfig):
        return config
    if isinstance(config, dict):
        return PushNotificationConfig(**config)
    return None  # Fallback for unexpected types


def is_url_shorthand(value: str) -> bool:
    """Return True when a string looks like a URL (scheme or protocol-relative)."""
    return "://" in value or value.startswith("//")


def brand_shorthand_to_domain(value: str) -> str:
    """Normalize AdCP v3 brand string shorthand to a domain hostname.

    Storyboard runners may send ``https://test.example``; ``BrandReference.domain``
    expects a hostname (no scheme/path) per the adcp library pattern.

    Returns empty string when a URL-shaped value cannot be parsed into a hostname
    (malformed IPv6, etc.) so legacy ``brand_manifest`` middleware can silently
    strip the field. Callers on the explicit ``brand`` path must use
    ``to_brand_reference`` / ``_coerce_domain_or_raise`` instead — those raise
    ``AdCPValidationError(field="brand")`` rather than dropping the brand.
    """
    value = value.strip()
    if not value:
        return value
    if is_url_shorthand(value):
        try:
            parsed = urlparse(value if "://" in value else f"https:{value}")
        except ValueError:
            return ""
        if parsed.hostname:
            return parsed.hostname.lower()
        return ""
    return value.lower()


def _coerce_domain_or_raise(raw: str) -> str:
    """Normalize brand shorthand and validate against BrandReference.domain pattern.

    Used for explicit ``brand`` on tool boundaries — malformed input must surface
    as ``VALIDATION_ERROR / field="brand"``, not be coerced to missing brand
    (which would mis-route ``require_brand`` policy to an authorization error).

    Raises:
        AdCPValidationError: when the value cannot be normalized to a valid hostname
            (empty parse, path/underscore/IDN/pattern mismatch). Always tagged
            ``field="brand"`` so wire envelopes name the request field.
    """
    domain = brand_shorthand_to_domain(raw)
    if not domain:
        raise AdCPValidationError(
            f"Invalid brand: could not derive domain from brand shorthand {raw!r}",
            field="brand",
        )
    try:
        BrandReference(domain=domain)
    except ValidationError as e:
        raise AdCPValidationError(
            f"Invalid brand: domain {domain!r} is not a valid hostname",
            field="brand",
        ) from e
    return domain


def to_brand_reference(brand: dict[str, Any] | BrandReference | str | None) -> BrandReference | None:
    """Convert dict/string brand to BrandReference for adcp 3.6.0 compatibility.

    String and dict ``domain`` values share one normalize-then-validate funnel so
    ``"ACME.COM"`` / ``{"domain":"ACME.COM"}`` / URL-in-domain are equivalent.

    Args:
        brand: Brand as dict, string domain shorthand, BrandReference, or None

    Returns:
        BrandReference or None

    Raises:
        AdCPValidationError: when an explicit brand cannot be coerced to a valid
            ``BrandReference`` (tagged ``field="brand"``).
    """
    if brand is None:
        return None
    if isinstance(brand, BrandReference):
        return brand
    if isinstance(brand, str):
        return BrandReference(domain=_coerce_domain_or_raise(brand))
    if isinstance(brand, dict):
        domain_raw = brand.get("domain")
        if not isinstance(domain_raw, str):
            raise AdCPValidationError(
                "Invalid brand: domain is required",
                field="brand",
            )
        allowed = BrandReference.model_fields.keys()
        ref_data = {key: value for key, value in brand.items() if key in allowed}
        ref_data["domain"] = _coerce_domain_or_raise(domain_raw)
        # field="brand" pins the request-level path (the buyer set `brand`,
        # not a bare BrandReference), matching this function's other raises.
        with adcp_validation_boundary(context="brand", field="brand"):
            return BrandReference(**ref_data)
    raise AdCPValidationError(
        f"Invalid brand: expected dict, string, or BrandReference, got {type(brand).__name__}",
        field="brand",
    )


def to_account_reference(account: dict[str, Any] | AccountReference | None) -> AccountReference | None:
    """Convert dict to AccountReference for adcp compatibility.

    Args:
        account: Account reference as dict, AccountReference, or None

    Returns:
        AccountReference or None
    """
    if account is None:
        return None
    if isinstance(account, AccountReference):
        return account
    if isinstance(account, dict):
        # AccountReference is a RootModel, so validate the whole value instead of field-unpacking.
        return AccountReference.model_validate(account)
    return None  # Fallback for unexpected types


def to_property_list_reference(
    property_list: dict[str, Any] | PropertyListReference | None,
) -> PropertyListReference | None:
    """Convert dict to PropertyListReference for adcp compatibility.

    Args:
        property_list: Property list reference as dict or PropertyListReference or None

    Returns:
        PropertyListReference or None
    """
    if property_list is None:
        return None
    if isinstance(property_list, PropertyListReference):
        return property_list
    if isinstance(property_list, dict):
        return PropertyListReference(**property_list)
    return None  # Fallback for unexpected types


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
    "is_url_shorthand",
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
