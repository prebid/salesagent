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

from adcp import GetProductsResponse, Product
from adcp.types import PropertyListReference
from adcp.types.generated_poc.core.brand_ref import BrandReference
from adcp.types.generated_poc.core.context import ContextObject
from adcp.types.generated_poc.core.product_filters import ProductFilters
from adcp.types.generated_poc.core.reporting_webhook import ReportingWebhook
from pydantic import ValidationError

from src.core.exceptions import AdCPValidationError
from src.core.product_conversion import resolve_pre_v3_buying_mode
from src.core.schemas.product import GetProductsRequest
from src.core.validation_helpers import format_validation_error


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


def to_brand_reference(brand: dict[str, Any] | BrandReference | str | None) -> BrandReference | None:
    """Convert dict/string brand to BrandReference for adcp 3.6.0 compatibility.

    Args:
        brand: Brand as dict, string domain shorthand, BrandReference, or None

    Returns:
        BrandReference or None
    """
    if brand is None:
        return None
    if isinstance(brand, BrandReference):
        return brand
    if isinstance(brand, str):
        return BrandReference(domain=brand)
    if isinstance(brand, dict):
        return BrandReference(**brand)
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


def create_get_products_request(
    brief: str = "",
    brand: dict[str, Any] | BrandReference | str | None = None,
    filters: dict[str, Any] | ProductFilters | None = None,
    property_list: dict[str, Any] | PropertyListReference | None = None,
    context: dict[str, Any] | ContextObject | None = None,
    buying_mode: str | None = None,
    refine: list[dict[str, Any]] | None = None,
    adcp_version: str | None = None,
) -> tuple[GetProductsRequest, bool]:
    """Create GetProductsRequest aligned with the AdCP 3.0 three-mode contract.

    Single source of truth for the pre-v3 buying_mode shim — transport
    wrappers (MCP, A2A, REST) only forward client params; the shim and the
    schema-validation translation live here so all three transports observe
    identical behavior (transport parity).

    Args:
        brief: Natural language description of campaign requirements (required when
            buying_mode='brief'; forbidden when buying_mode='wholesale' or 'refine')
        brand: Brand reference per adcp 3.6.0 (BrandReference or dict with domain field).
               Example: BrandReference(domain="acme.com") or {"domain": "acme.com"}
        filters: Structured filters for product discovery (dict or ProductFilters)
        property_list: Property list reference for filtering by buyer's property list
        context: Application-level context (dict or ContextObject)
        buying_mode: Buyer intent — 'brief' / 'wholesale' / 'refine'. May be None for
            pre-v3 clients; the helper applies the pre-v3 default shim internally.
        refine: Array of change requests for refining a previous get_products response;
            only valid when buying_mode='refine'.
        adcp_version: Client-declared AdCP version string. Used to decide whether the
            pre-v3 default shim should apply (pre-v3 + no buying_mode → defaulted).

    Returns:
        Tuple of ``(GetProductsRequest, pre_v3_defaulted)``. The flag is True iff the
        pre-v3 shim defaulted ``buying_mode``; transport callers thread it into
        ``_get_products_impl`` for the audit log.

    Raises:
        AdCPValidationError: when Pydantic schema validation rejects the request, or
            when a helper-level invariant fails (translated from ValidationError /
            ValueError so transport wrappers see one exception type).

    Examples:
        >>> req, defaulted = create_get_products_request(
        ...     buying_mode="brief",
        ...     brand=BrandReference(domain="acme.com"),
        ...     brief="Display ads",
        ... )
    """
    resolved_mode, pre_v3_defaulted = resolve_pre_v3_buying_mode(buying_mode, adcp_version, brief)

    # Handle filters - can be dict, ProductFilters, or None
    filters_obj: ProductFilters | None = None
    if filters is not None:
        if isinstance(filters, ProductFilters):
            filters_obj = filters
        elif isinstance(filters, dict):
            filters_obj = ProductFilters(**filters)

    try:
        req = GetProductsRequest(
            brand=to_brand_reference(brand),
            brief=brief or None,
            filters=filters_obj,
            property_list=to_property_list_reference(property_list),
            context=to_context_object(context),
            buying_mode=resolved_mode,
            refine=refine,
        )
    except ValidationError as e:
        raise AdCPValidationError(format_validation_error(e, context="get_products request")) from e
    except ValueError as e:
        raise AdCPValidationError(f"Invalid get_products request: {e}") from e

    return req, pre_v3_defaulted


# Re-export commonly used generated types for convenience
__all__ = [
    "to_brand_reference",
    "to_context_object",
    "to_reporting_webhook",
    "create_get_products_request",
    # Re-export types for type hints
    "BrandReference",
    "GetProductsRequest",
    "GetProductsResponse",
    "Product",
    "ContextObject",
    "ReportingWebhook",
]
