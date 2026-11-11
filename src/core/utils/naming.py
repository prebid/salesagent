"""Naming template utilities for orders and line items.

Adapter-agnostic utilities that work across all ad servers (GAM, Mock, Kevel, etc.)

Supports variable substitution with fallback syntax:
- {campaign_name} - Direct substitution
- {campaign_name|brand_name} - Use campaign_name, fall back to brand_name
- {date_range} - Formatted date range (e.g., "Oct 7-14, 2025")
- {month_year} - Month and year (e.g., "Oct 2025")
- {brand_name} - Brand from brand reference
- {media_buy_id} - Media buy identifier
- {auto_name} - AI-generated name from full context (requires AI configuration)
- {product_name} - Product name (for line items)
- {package_count} - Number of packages in order
- {package_index} - Package position number (1, 2, 3...)
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import TYPE_CHECKING

from src.core.helpers.brand_key import brand_key_parts
from src.core.logging_config import log_safe

if TYPE_CHECKING:
    # The caller is always an adapter (or an adapter's workflow manager), so `request`
    # here is the adapter carrier, never the buyer's DTO. TYPE_CHECKING-only: this
    # module is under src/core and the carrier lives with the adapters.
    from src.adapters.base import AdapterCreateRequest
    from src.services.ai.config import SellerAIConfig

logger = logging.getLogger(__name__)

#: The order name template for a seller who configured none. ``Tenant.order_name_template``'s
#: server default spells the same string.
DEFAULT_ORDER_NAME_TEMPLATE = "{campaign_name|brand_name} - {media_buy_id} - {date_range}"

#: One template placeholder, ``{variable}`` or ``{var1|var2|var3}``.
_PLACEHOLDER = re.compile(r"\{([^}]+)\}")


def _alternatives(placeholder: re.Match[str]) -> list[str]:
    """The variable names one placeholder tries, in order: ``{a|b}`` -> ``["a", "b"]``."""
    return [name.strip() for name in placeholder.group(1).split("|")]


def template_references(template: str, variable: str) -> bool:
    """True when any placeholder in ``template`` names ``variable``, alone or as an alternative."""
    return any(variable in _alternatives(placeholder) for placeholder in _PLACEHOLDER.finditer(template))


def format_date_range(start_time: datetime, end_time: datetime) -> str:
    """Format date range for display.

    Examples:
        - Same month: "Oct 7-14, 2025"
        - Different months: "Oct 15 - Nov 5, 2025"
        - Different years: "Dec 28, 2024 - Jan 5, 2025"
    """
    if start_time.year != end_time.year:
        return f"{start_time.strftime('%b %d, %Y')} - {end_time.strftime('%b %d, %Y')}"
    elif start_time.month != end_time.month:
        return f"{start_time.strftime('%b %d')} - {end_time.strftime('%b %d, %Y')}"
    else:
        return f"{start_time.strftime('%b %d')}-{end_time.strftime('%d, %Y')}"


def format_month_year(start_time: datetime) -> str:
    """Format month and year for display.

    Example: "Oct 2025"
    """
    return start_time.strftime("%b %Y")


def _extract_brand_name(request: AdapterCreateRequest) -> str | None:
    """Read the brand's domain, through the canonical accessor.

    ``brand`` is the widened union (BrandReference | dict | str | None), and
    ``brand_key_parts`` is total over it — the three-branch narrowing this replaces was
    one of the hand-rolled copies that helper exists to delete, and it read nothing off
    the bare-string branch.
    """
    return brand_key_parts(request.brand)[0]


def _get_fallback_name(request: AdapterCreateRequest) -> str:
    """Get fallback name when AI is unavailable."""
    brand_name = _extract_brand_name(request)
    return brand_name or "Campaign"


def generate_auto_name(
    request: AdapterCreateRequest,
    packages: list,
    start_time: datetime,
    end_time: datetime,
    tenant_ai_config: SellerAIConfig = None,
    max_length: int = 150,
) -> str:
    """Generate AI-powered order name using Pydantic AI.

    Args:
        request: The adapter's AdapterCreateRequest carrier
        packages: List of MediaPackage objects
        start_time: Order start datetime
        end_time: Order end datetime
        tenant_ai_config: What AI the seller configured (``TenantContext.ai_config``);
            None falls back to the platform's credential
        max_length: Maximum length for generated name

    Returns:
        AI-generated name, or falls back to brand_name if AI unavailable

    Example output:
        "Nike Air Max Campaign - Q4 Holiday Push"
        "Acme Corp Brand Awareness - Premium Video"
    """
    from src.services.ai import AIServiceFactory
    from src.services.ai.factory import AIProviderError

    try:
        model = AIServiceFactory().build(tenant_ai_config)
        if model is None:
            logger.debug("No AI configuration available, falling back to brand_name")
            return _get_fallback_name(request)

        from src.services.ai.agents.naming_agent import (
            create_naming_agent,
            generate_name_async,
        )

        agent = create_naming_agent(model, max_length=max_length)

        # Extract context for AI
        brand_name = _extract_brand_name(request) or "N/A"

        # Build budget info. The currency lookup this replaces walked the request's
        # packages for a `currency` attribute that the buyer's package shape does not
        # declare (PackageRequest has no such field), so it could only ever yield the
        # default — and the carrier deliberately does not invent one: the order currency
        # an adapter uses comes from package_pricing_info, not from here.
        budget_info = None
        budget_amount = request.total_budget
        if budget_amount > 0:
            budget_info = f"${budget_amount:,.2f} USD"

        # BrandReference has no campaign_objectives; pass None
        objectives = None

        # Run async agent — handle both sync and async calling contexts
        from src.core.validation_helpers import run_async_in_sync_context

        generated_name = run_async_in_sync_context(
            generate_name_async(
                agent=agent,
                campaign_name=None,  # Not in AdCP spec
                brand_name=brand_name if brand_name != "N/A" else None,
                budget_info=budget_info,
                date_range=format_date_range(start_time, end_time),
                products=[pkg.product_id for pkg in packages],
                objectives=objectives,
                max_length=max_length,
            )
        )

        return generated_name

    except AIProviderError as e:
        # The provider failed the call (run_agent classifies it). Any other exception is a
        # defect in this path and propagates rather than becoming a fallback name.
        logger.warning(f"Failed to generate auto_name with AI: {log_safe(e)}, falling back")
        return _get_fallback_name(request)


def apply_naming_template(
    template: str,
    context: dict,
) -> str:
    """Apply naming template with variable substitution and fallback support.

    Args:
        template: Template string with {variable} or {var1|var2|var3} syntax
        context: Dictionary of available variables

    Returns:
        Formatted string with variables substituted

    Examples:
        >>> apply_naming_template("{campaign_name} - {date_range}", {
        ...     "campaign_name": "Q1 Launch",
        ...     "date_range": "Oct 7-14, 2025"
        ... })
        "Q1 Launch - Oct 7-14, 2025"

        >>> apply_naming_template("{campaign_name|brand_name}", {
        ...     "campaign_name": None,
        ...     "brand_name": "Nike Shoes"
        ... })
        "Nike Shoes"
    """
    # Ensure template is a string (handle MagicMock in tests)
    if not isinstance(template, str):
        template = str(template)

    result = template

    for match in _PLACEHOLDER.finditer(result):
        full_match = match.group(0)  # e.g., "{campaign_name|promoted_offering}"
        variables = _alternatives(match)  # e.g., ["campaign_name", "promoted_offering"]

        # Try each variable in order until we find a non-None, non-empty value
        value = None
        for var_name in variables:
            if var_name in context:
                candidate = context[var_name]
                if candidate is not None and candidate != "":
                    value = str(candidate)
                    break

        # If no value found, use empty string (or could use first variable name as placeholder)
        if value is None:
            value = ""

        result = result.replace(full_match, value)

    return result


def _build_order_name_context(
    request: AdapterCreateRequest,
    packages: list,
    start_time: datetime,
    end_time: datetime,
    *,
    template: str,
    tenant_ai_config: SellerAIConfig = None,
    media_buy_id: str | None = None,
) -> dict:
    """Build context dictionary for order name template.

    Args:
        request: The adapter's AdapterCreateRequest carrier
        packages: List of MediaPackage objects
        start_time: Order start datetime
        end_time: Order end datetime
        template: The order name template the context is built for
        tenant_ai_config: What AI the seller configured (``TenantContext.ai_config``);
            None falls back to the platform's credential
        media_buy_id: Media buy identifier for template substitution

    Returns:
        Dictionary of variables available for template substitution
    """
    # The model is asked only when the template uses its answer. This runs on the
    # create_media_buy write path and the call blocks it, so asking on every order made a
    # seller on the default template pay for a name it then discarded.
    auto_name = (
        generate_auto_name(
            request=request,
            packages=packages,
            start_time=start_time,
            end_time=end_time,
            tenant_ai_config=tenant_ai_config,
        )
        if template_references(template, "auto_name")
        else None
    )

    # Extract brand name from brand (BrandReference)
    brand_name = _extract_brand_name(request)

    # campaign_name is no longer on CreateMediaBuyRequest per AdCP spec
    # Use brand_name as campaign name
    campaign_name = brand_name or "Campaign"

    return {
        "campaign_name": campaign_name,
        "brand_name": brand_name or "N/A",
        "promoted_offering": brand_name or "N/A",  # Backward compatibility alias
        "auto_name": auto_name,
        "date_range": format_date_range(start_time, end_time),
        "month_year": format_month_year(start_time),
        "media_buy_id": media_buy_id or "",
        "package_count": len(packages),
        "start_date": start_time.strftime("%Y-%m-%d"),
        "end_date": end_time.strftime("%Y-%m-%d"),
    }


def render_order_name(
    template: str | None,
    request: AdapterCreateRequest,
    packages: list,
    start_time: datetime,
    end_time: datetime,
    *,
    tenant_ai_config: SellerAIConfig = None,
    media_buy_id: str | None = None,
) -> str:
    """The order name *template* renders to; ``None`` is `DEFAULT_ORDER_NAME_TEMPLATE`.

    The one way an adapter names an order. The context is built for the same template it
    is then applied to, so ``{auto_name}`` is asked of the model exactly when the applied
    template uses it. Built and applied separately, a site could pass two templates and
    compute a name it throws away, or miss one it needs, with no error.

    Ad-server rules (a unique suffix, a length limit) stay with the caller, after this.
    """
    effective = template or DEFAULT_ORDER_NAME_TEMPLATE
    context = _build_order_name_context(
        request,
        packages,
        start_time,
        end_time,
        template=effective,
        tenant_ai_config=tenant_ai_config,
        media_buy_id=media_buy_id,
    )
    return apply_naming_template(effective, context)


def build_line_item_name_context(
    order_name: str,
    product_name: str,
    package_name: str | None = None,
    package_index: int | None = None,
) -> dict:
    """Build context dictionary for line item name template.

    Args:
        order_name: Name of the parent order
        product_name: Name of the product/package (from database)
        package_name: Name from the package itself (from MediaPackage.name).
            Falls back to product_name if not provided.
        package_index: Optional index of package in order (1-based)

    Returns:
        Dictionary of variables available for template substitution
    """
    context = {
        "order_name": order_name,
        "product_name": product_name,
        "package_name": package_name or product_name,  # Fallback to product_name
    }

    if package_index is not None:
        context["package_index"] = str(package_index)

    return context
