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

import logging
from datetime import datetime

logger = logging.getLogger(__name__)


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


def _extract_brand_name(request) -> str | None:
    """Extract brand name from request's brand (BrandReference)."""
    if not hasattr(request, "brand") or not request.brand:
        return None

    brand = request.brand
    if hasattr(brand, "domain"):
        return brand.domain
    elif isinstance(brand, dict):
        return brand.get("domain")
    return None


def _get_fallback_name(request) -> str:
    """Get fallback name when AI is unavailable."""
    brand_name = _extract_brand_name(request)
    return brand_name or "Campaign"


def generate_auto_name(
    request,
    packages: list,
    start_time: datetime,
    end_time: datetime,
    tenant_ai_config=None,
    tenant_gemini_key: str | None = None,  # Deprecated: use tenant_ai_config
    max_length: int = 150,
) -> str:
    """Generate AI-powered order name using Pydantic AI.

    Args:
        request: CreateMediaBuyRequest object
        packages: List of MediaPackage objects
        start_time: Order start datetime
        end_time: Order end datetime
        tenant_ai_config: Tenant's AI configuration (TenantAIConfig or dict)
        tenant_gemini_key: Deprecated - Tenant's Gemini API key
        max_length: Maximum length for generated name

    Returns:
        AI-generated name, or falls back to brand_name if AI unavailable

    Example output:
        "Nike Air Max Campaign - Q4 Holiday Push"
        "Acme Corp Brand Awareness - Premium Video"
    """
    from src.services.ai import AIServiceFactory

    factory = AIServiceFactory()

    # Handle backward compatibility: convert gemini_api_key to ai_config
    effective_config = tenant_ai_config
    if effective_config is None and tenant_gemini_key:
        effective_config = {
            "provider": "gemini",
            "api_key": tenant_gemini_key,
        }

    # Check if AI is enabled
    if not factory.is_ai_enabled(effective_config):
        logger.debug("No AI configuration available, falling back to brand_name")
        return _get_fallback_name(request)

    try:
        from src.services.ai.agents.naming_agent import (
            create_naming_agent,
            generate_name_async,
        )

        # Create the model and agent
        model_string = factory.create_model(effective_config)
        agent = create_naming_agent(model_string, max_length=max_length)

        # Extract context for AI
        brand_name = _extract_brand_name(request) or "N/A"

        # Build budget info
        budget_info = None
        budget_amount = request.get_total_budget()
        if budget_amount > 0:
            currency = "USD"
            if request.packages:
                for pkg in request.packages:
                    if hasattr(pkg, "currency") and pkg.currency:
                        currency = pkg.currency
                        break
            budget_info = f"${budget_amount:,.2f} {currency}"

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

    except Exception as e:
        logger.warning(f"Failed to generate auto_name with AI: {e}, falling back")
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

    # Find all {variable} or {var1|var2} patterns
    import re

    pattern = r"\{([^}]+)\}"

    for match in re.finditer(pattern, result):
        full_match = match.group(0)  # e.g., "{campaign_name|promoted_offering}"
        variables = match.group(1).split("|")  # e.g., ["campaign_name", "promoted_offering"]

        # Try each variable in order until we find a non-None, non-empty value
        value = None
        for var_name in variables:
            var_name = var_name.strip()
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


def build_order_name_context(
    request,
    packages: list,
    start_time: datetime,
    end_time: datetime,
    tenant_ai_config=None,
    tenant_gemini_key: str | None = None,  # Deprecated: use tenant_ai_config
    media_buy_id: str | None = None,
) -> dict:
    """Build context dictionary for order name template.

    Args:
        request: CreateMediaBuyRequest object
        packages: List of MediaPackage objects
        start_time: Order start datetime
        end_time: Order end datetime
        tenant_ai_config: Tenant's AI configuration (TenantAIConfig or dict)
        tenant_gemini_key: Deprecated - Tenant's Gemini API key
        media_buy_id: Media buy identifier for template substitution

    Returns:
        Dictionary of variables available for template substitution
    """
    # Generate auto_name if template uses it (lazy evaluation via dict access is fine)
    # Note: This gets called only if {auto_name} is in the template
    auto_name = generate_auto_name(
        request=request,
        packages=packages,
        start_time=start_time,
        end_time=end_time,
        tenant_ai_config=tenant_ai_config,
        tenant_gemini_key=tenant_gemini_key,
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
        "buyer_ref": media_buy_id or "",  # Backward compatibility alias for {buyer_ref} templates
        "package_count": len(packages),
        "start_date": start_time.strftime("%Y-%m-%d"),
        "end_date": end_time.strftime("%Y-%m-%d"),
    }


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
