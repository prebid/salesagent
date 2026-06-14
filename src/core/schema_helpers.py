"""Helper functions for working with generated schemas.

This module provides convenience functions for constructing complex generated schemas
without losing type safety. Unlike adapters (which wrap schemas in dict[str, Any]),
these helpers work directly with the generated Pydantic models.

Philosophy:
- Generated schemas are the source of truth (always in sync with AdCP spec)
- Helpers make construction easier without sacrificing type safety
- Custom logic (validators, conversions) lives here, not in wrapper classes
"""

from typing import Any, NamedTuple

# FIXME(#1388): GetProductsResponse, Product have local subclasses; import from src.core.schemas.
from adcp import GetProductsResponse, Product

# FIXME(#1388): ProductFilters has a local subclass; import from src.core.schemas.
from adcp.types import (
    AccountReference,
    BrandReference,
    ContextObject,
    ProductFilters,
    PropertyListReference,
    ReportingWebhook,
)
from pydantic import ValidationError

from src.core.exceptions import AdCPInvalidRequestError
from src.core.product_conversion import resolve_pre_v3_buying_mode
from src.core.schemas.product import GetProductsRequest
from src.core.validation_helpers import (
    _BUYING_MODE_SUGGESTIONS,
    extract_buying_mode_suggestion,
    format_validation_error,
)


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


class GetProductsRequestBuild(NamedTuple):
    """Result of building a GetProductsRequest with the pre-v3 buying_mode shim.

    Named (not a bare tuple) so the audit flag can't be confused with a future second
    bool. Transport wrappers unpack ``(request, pre_v3_defaulted)``.
    """

    request: GetProductsRequest
    pre_v3_defaulted: bool


def create_get_products_request(
    brief: str = "",
    brand: dict[str, Any] | BrandReference | str | None = None,
    filters: dict[str, Any] | ProductFilters | None = None,
    property_list: dict[str, Any] | PropertyListReference | None = None,
    context: dict[str, Any] | ContextObject | None = None,
    buying_mode: str | None = None,
    refine: list[dict[str, Any]] | None = None,
    adcp_version: str | None = None,
) -> GetProductsRequestBuild:
    """Create GetProductsRequest aligned with the AdCP 3.0.1 three-mode contract.

    Single source of truth for the pre-v3 buying_mode shim and the schema-validation
    translation — transport wrappers (MCP, A2A, REST) only forward client params, so all
    three observe identical behavior (transport parity).

    Args:
        brief: Campaign brief (required when buying_mode='brief'; forbidden for 'wholesale'/'refine').
        brand: Brand reference (BrandReference or dict with domain field).
        filters: Structured filters for product discovery (dict or ProductFilters).
        property_list: Property list reference for filtering by buyer's property list.
        context: Application-level context (dict or ContextObject).
        buying_mode: 'brief' / 'wholesale' / 'refine'. May be None for pre-v3 clients; the
            pre-v3 default shim applies internally.
        refine: Change requests for refining a previous response; only valid for 'refine'.
        adcp_version: Client-declared AdCP version; decides whether the pre-v3 shim applies.

    Returns:
        ``GetProductsRequestBuild(request, pre_v3_defaulted)``. The flag is True iff the
        shim defaulted buying_mode; callers thread it into ``_get_products_impl`` for audit.

    Raises:
        AdCPInvalidRequestError: when schema validation rejects the request (cross-mode
            violation, missing field, malformed value) — wire INVALID_REQUEST per AdCP 3.0.1
            get_products prose, translated from ValidationError/ValueError so transport
            wrappers see one exception type.
    """
    resolved_mode, pre_v3_defaulted = resolve_pre_v3_buying_mode(buying_mode, adcp_version, brief)

    # Spec 3.0.1: "v3 clients MUST include buying_mode; pre-v3 clients without it
    # SHOULD default to 'brief'." The shim above defaults pre-v3 clients, so a None
    # mode that survives it is a v3 client that omitted the field. This required-ness
    # is version-keyed, so it lives in this version-aware wrapper, not in the
    # version-agnostic GetProductsRequest validator (which has no adcp_version).
    if resolved_mode is None:
        raise AdCPInvalidRequestError(
            "Invalid get_products request: buying_mode is required — v3 clients must declare "
            "'brief', 'wholesale', or 'refine'.",
            suggestion=_BUYING_MODE_SUGGESTIONS[0][1],
        )

    # Handle filters - can be dict, ProductFilters, or None
    filters_obj: ProductFilters | None = None
    if filters is not None:
        if isinstance(filters, ProductFilters):
            filters_obj = filters
        elif isinstance(filters, dict):
            filters_obj = ProductFilters(**filters)

    try:
        req = GetProductsRequest(  # type: ignore[call-arg]
            brand=to_brand_reference(brand),
            brief=brief or None,
            filters=filters_obj,
            property_list=to_property_list_reference(property_list),
            context=to_context_object(context),
            buying_mode=resolved_mode,
            refine=refine,
        )
    except ValidationError as e:
        raise AdCPInvalidRequestError(
            format_validation_error(e, context="get_products request"),
            suggestion=extract_buying_mode_suggestion(e),
        ) from e
    except ValueError as e:
        raise AdCPInvalidRequestError(f"Invalid get_products request: {e}") from e

    return GetProductsRequestBuild(req, pre_v3_defaulted)


# Re-export commonly used generated types for convenience
__all__ = [
    "to_account_reference",
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
