"""Get products tool implementation.

This module contains the get_products tool implementation following the MCP/A2A
shared implementation pattern from CLAUDE.md.
"""

import logging
import os
import time
from typing import Annotated, Any, cast

# FIXME(#1388): FormatId, ProductFilters have local subclasses; import from src.core.schemas (Pattern #7/#4).
from adcp import FormatId, ProductFilters
from adcp import GetProductsRequest as GetProductsRequestGenerated
from adcp import Product as LibraryProduct
from adcp.types import BrandReference, ContextObject, Error, PropertyListReference
from fastmcp.server.context import Context
from pydantic import Field

from src.adapters import get_adapter_default_channels
from src.core.audit_logger import get_audit_logger
from src.core.auth import get_principal_object, require_identity, require_tenant
from src.core.exceptions import (
    AdCPAdapterError,
    AdCPAuthenticationError,
    AdCPAuthorizationError,
    AdCPConfigurationError,
    AdCPError,
    AdCPPolicyViolationError,
    AdCPValidationError,
)
from src.core.helpers import enum_value
from src.core.resolved_identity import ResolvedIdentity
from src.core.schema_helpers import create_get_products_request
from src.core.schemas import (
    GetProductsResponse,
    Product,  # Extends library Product
)
from src.core.testing_hooks import AdCPTestContext
from src.core.tool_context import ToolContext
from src.core.transport_helpers import resolve_identity_from_context
from src.core.validation_helpers import adcp_validation_boundary, safe_parse_json_field
from src.services.ai.config import TenantAIConfig
from src.services.policy_check_service import PolicyCheckService, PolicyStatus

logger = logging.getLogger(__name__)


def get_recommended_cpm(product: Product) -> float | None:
    """Extract recommended CPM from product's pricing_options.

    Uses p75 (75th percentile) as the recommended value per AdCP price_guidance spec.

    Args:
        product: Product schema object

    Returns:
        Recommended CPM value (p75) from price_guidance, or None if not available
    """
    for option in product.pricing_options:
        inner = option.root
        price_guidance = getattr(inner, "price_guidance", None)
        if inner.pricing_model.upper() == "CPM" and price_guidance:
            p75 = price_guidance.p75
            if p75 is not None:
                return float(p75)
    return None


# Import conversion utilities from dedicated module to avoid circular imports
from src.core.product_conversion import convert_product_model_to_schema
from src.core.tools._mcp import mcp_result


def extract_product_property_ids(
    publisher_properties: list,
) -> set[str] | None:
    """Extract property IDs from a product's publisher_properties list.

    Args:
        publisher_properties: List of PublisherPropertySelector objects.

    Returns:
        Set of property ID strings, or None if any selector is selection_type="all"
        (meaning the product covers all properties). Returns empty set for empty input.
    """
    if not publisher_properties:
        return set()

    property_ids: set[str] = set()
    for selector in publisher_properties:
        inner = selector.root
        if inner.selection_type == "all":
            # Product covers ALL properties for this domain
            return None
        elif inner.selection_type == "by_id":
            for pid in inner.property_ids:
                property_ids.add(pid.root)
        # by_tag: we don't resolve tags to IDs here; tags are excluded from matching

    return property_ids


def should_include_product_for_property_list(
    product: Any,
    allowed_properties: set[str],
) -> bool:
    """Determine if a product should be included based on property list filtering.

    Args:
        product: Product object with publisher_properties and property_targeting_allowed.
        allowed_properties: Set of allowed property ID strings from the buyer's property list.

    Returns:
        True if the product should be included, False otherwise.
    """
    product_property_ids = extract_product_property_ids(product.publisher_properties)

    # None means product has selection_type="all" — always include
    if product_property_ids is None:
        return True

    # No property IDs on the product — exclude (can't determine overlap)
    if not product_property_ids:
        return False

    intersection = product_property_ids & allowed_properties
    if not intersection:
        return False

    if not product.property_targeting_allowed:
        # Strict: ALL product properties must be in allowed set
        return product_property_ids <= allowed_properties

    # Permissive: any intersection is enough
    return True


def filter_products_by_property_list(
    products: list,
    allowed_properties: set[str],
) -> list:
    """Filter a list of products based on allowed property IDs.

    Args:
        products: List of product objects.
        allowed_properties: Set of allowed property ID strings.

    Returns:
        Filtered list of products that match the property list criteria.
    """
    return [p for p in products if should_include_product_for_property_list(p, allowed_properties)]


# The recovery classification each advisory code carries on the wire, mirroring the
# `enumMetadata` block of the pinned `enums/error-code.json`. Derived from the code
# rather than passed alongside it: recovery is a PROPERTY of the code, and a call site
# free to pair them itself is a call site free to tell a buyer to retry a missing API
# key (or to give up on a rate limit).
#
# * CONFIGURATION_ERROR -> "terminal" / "surface to a human at the seller — the buyer
#   cannot resolve a seller-side deployment misconfiguration and MUST NOT auto-retry".
# * SERVICE_UNAVAILABLE -> "transient" / "retry with exponential backoff".
#
# recovery is stated on the wire rather than left to be inferred from the enum because
# pinned `core/error.json` makes the code vocabulary OPEN ("senders MAY emit codes
# outside that set ... read `error.recovery` for the recovery classification"): a client
# that does not consult the enum still classifies these correctly.
_ADVISORY_RECOVERY: dict[str, str] = {
    "CONFIGURATION_ERROR": "terminal",
    "SERVICE_UNAVAILABLE": "transient",
}


def _unranked_products_advisory(tenant_id: str, *, code: str, cause: str) -> Error:
    """The advisory that stands in for AI ranking a tenant asked for but did not get.

    Ranking is configured (``product_ranking_prompt`` is set) but did not happen, so
    ``products[]`` comes back in catalog order. Silence here is the bug: the buyer sees
    a plausible-looking list and has no way to tell it was never ranked. One builder,
    two callers — the shape, the field pointer and the ``PRODUCT_RANKING_UNAVAILABLE``
    marker are identical; only the CODE and the ``cause`` clause differ, because the two
    conditions need opposite advice:

    * **Nothing resolved / nothing usable resolved** — CONFIGURATION_ERROR. The fault is
      entirely on the seller's side of the wire (the buyer's request was valid) and no
      retry, at any backoff, supplies a missing API key. This mirrors the ruling at
      ``src/core/tools/media_buy_list.py::_omitted_row_advisory``.
    * **The ranking call itself did not complete** — SERVICE_UNAVAILABLE. A provider
      429, a 5xx, a timeout, an unexpected model response: pydantic-ai raises
      ``ModelHTTPError`` / ``UnexpectedModelBehavior`` / ``UsageLimitExceeded`` /
      ``UserError``, all of which subclass ``RuntimeError``. Telling the buyer
      "terminal — MUST NOT auto-retry" for a rate limit is wrong advice; the pinned
      enumDescription for SERVICE_UNAVAILABLE is "Seller service is temporarily
      unavailable. Retry with exponential backoff. Recovery: transient."

    The ``cause`` clause never interpolates ``str(exc)``. This message is wire-visible to
    an anonymous buyer (``get_products`` is auth-optional discovery), and a provider
    exception's text can carry request URLs, response bodies and key fragments; the
    pinned enumDescription for CONFIGURATION_ERROR states sellers "MUST NOT include
    credentials, connection strings, or stack traces — the message is wire-visible to
    the buyer". The exception class name is enough to classify; the full text goes to
    the seller's log.

    It rides on ``payload.errors[]`` inside a SUCCESS envelope, never as an envelope
    ``adcp_error``: pinned ``core/protocol-envelope.json`` states that non-fatal warnings
    populate ONLY ``payload.errors[]`` with ``severity: warning`` and that the envelope
    MUST NOT carry ``adcp_error`` for non-failures, and pinned
    ``get-products-response.json`` requires ``errors[]`` only when ``status == "failed"``
    while describing it as "Task-specific errors and warnings", so a completed response
    may carry it. Ungraded: no phase in the pinned storyboards grades a get_products
    ranking advisory.
    """
    return Error(  # structural-guard: advisory unranked-catalog notice in GetProductsResponse.errors[]
        code=code,
        recovery=_ADVISORY_RECOVERY[code],
        # severity is not a field of the pinned core/error.json, whose
        # additionalProperties is true; it is the marker the envelope schema names for a
        # non-fatal payload error, and the envelope's own examples carry it exactly here.
        # It is also what keeps the A2A envelope reporting success=True for an advisory
        # (see AdCPRequestHandler._stamp_a2a_protocol_fields).
        severity="warning",
        message=(
            f"PRODUCT_RANKING_UNAVAILABLE: seller {tenant_id!r} has AI product ranking "
            f"configured but {cause}, so these products are returned unranked (catalog "
            f"order), not ordered by relevance to the brief."
        ),
        field="products[]",
    )


async def _rank_products_with_ai(
    products: list[Product],
    tenant: Any,
    ranking_prompt: str,
    brief_text: str,
    advisories: list[Error],
) -> list[Product]:
    """Order products by AI-scored relevance to the brief, dropping the irrelevant ones.

    Returns the products unchanged when ranking cannot run, appending EXACTLY ONE
    advisory to ``advisories`` on every such path — the unusable-configuration one and
    the call-did-not-complete one alike. Owning the failure paths here, rather than in a
    ``try`` at the call site, is what makes that "every" true: the caller's handler
    logged a warning and dropped through with ``advisories`` untouched, so a provider
    401/429/5xx returned a plausible-looking catalog-order list with ``errors=None`` —
    precisely the silent failure the advisory exists to end, on the paths most likely to
    happen in production.

    Extracted from ``_get_products_impl`` so the ranking decision reads in one place —
    and so this addition does not deepen a function already over every complexity
    threshold.
    """
    from src.services.ai.agents.ranking_agent import create_ranking_agent, rank_products_async
    from src.services.ai.factory import get_factory

    tenant_id = tenant["tenant_id"]
    factory = get_factory()
    # TenantAIConfig.from_tenant owns "given a tenant, what is its AI configuration?" —
    # ai_config first, the legacy gemini_api_key column second, None when neither is set.
    # Reading only the platform environment key here meant a tenant that configured its
    # key in the Admin UI got no ranking at all.
    tenant_ai_config = TenantAIConfig.from_tenant(tenant)

    # is_ai_enabled(), not `tenant_ai_config is not None`: the platform-key fallback is
    # existing, relied-upon behaviour for ranking — a deployment-wide GEMINI_API_KEY
    # ranks for every tenant that set a ranking prompt.
    if not factory.is_ai_enabled(tenant_ai_config):
        logger.warning(
            "[GET_PRODUCTS] AI ranking is configured for tenant %s but no usable AI configuration "
            "resolved (tenant ai_config/gemini_api_key: %s; platform environment key: absent). "
            "Returning products unranked.",
            tenant_id,
            "present but unusable" if tenant_ai_config is not None else "absent",
        )
        advisories.append(
            _unranked_products_advisory(
                tenant_id,
                code="CONFIGURATION_ERROR",
                cause="no usable AI configuration resolved for it",
            )
        )
        return products

    try:
        model = factory.create_model(tenant_ai_config=tenant_ai_config)
        ranking_result = await rank_products_async(
            agent=create_ranking_agent(model),
            custom_prompt=ranking_prompt,
            brief=brief_text,
            products=products,
        )
    except (AdCPConfigurationError, ImportError, RuntimeError, OSError) as e:
        # Two conditions, opposite advice, one handler so neither can be added without
        # its advisory:
        #
        # * AdCPConfigurationError is the factory REFUSING this seller's configuration.
        #   `is_ai_enabled` can report a coherent provider/model/key and `create_model`
        #   still decline the combination (a provider with no explicit-API-key
        #   integration, where the string form would authenticate with whatever ELSE is
        #   in the environment). It extends Exception, not RuntimeError, so nothing here
        #   caught it: a seller-side misconfiguration 500'd an auth-OPTIONAL discovery
        #   call for every anonymous buyer. Same fault as the branch above, same code.
        # * Everything else is the call not completing. pydantic-ai's ModelHTTPError,
        #   UnexpectedModelBehavior, UsageLimitExceeded and UserError all subclass
        #   RuntimeError, so every provider auth failure, 429, timeout and malformed
        #   response lands here. That is transient, not a deployment misconfiguration.
        #
        # `str(e)` is logged for the seller and deliberately kept OFF the wire (see
        # `_unranked_products_advisory`).
        code, cause = (
            ("CONFIGURATION_ERROR", "its AI configuration cannot build a model")
            if isinstance(e, AdCPConfigurationError)
            else ("SERVICE_UNAVAILABLE", "the ranking call did not complete")
        )
        logger.warning(
            "[GET_PRODUCTS] AI ranking did not run for tenant %s (%s). Returning products unranked. Cause: %s",
            tenant_id,
            cause,
            e,
        )
        advisories.append(_unranked_products_advisory(tenant_id, code=code, cause=f"{cause} ({type(e).__name__})"))
        return products

    # Build a map of product_id -> (score, reason); products the agent did not score sort last.
    ranking_map = {r.product_id: (r.relevance_score, r.reason) for r in ranking_result.rankings}
    ranked = sorted(products, key=lambda p: ranking_map.get(p.product_id, (0.0, ""))[0], reverse=True)
    # Drop very low relevance. Deliberately NOT reported on errors[]: the pinned schema
    # assigns score-threshold reporting to filter_diagnostics and calls it observability,
    # not error reporting.
    ranked = [p for p in ranked if ranking_map.get(p.product_id, (0.0, ""))[0] >= 0.1]

    for r in ranking_result.rankings:
        logger.info(f"[AI_RANKING] {r.product_id}: score={r.relevance_score:.2f}, reason={r.reason}")
    logger.info(
        f"[GET_PRODUCTS] AI ranking applied: {len(ranking_result.rankings)} products ranked, "
        f"{len(ranked)} products above threshold"
    )
    return ranked


async def _get_products_impl(
    req: GetProductsRequestGenerated, identity: ResolvedIdentity | None
) -> GetProductsResponse:
    """Shared implementation for get_products.

    Contains all business logic for product discovery including policy checks,
    product catalog providers, dynamic pricing, and filtering.

    Args:
        req: GetProductsRequest from generated schemas
        identity: Resolved identity from transport boundary

    Returns:
        GetProductsResponse containing matching products
    """
    start_time = time.time()

    # Require at least one search criterion (brief, brand, or filters)
    if not req.brief and not req.brand and not req.filters:
        raise AdCPValidationError("At least one of 'brief', 'brand', or 'filters' is required")

    # Extract identity fields
    identity = require_identity(identity, context=req.context)

    testing_ctx: AdCPTestContext | None = identity.testing_context or AdCPTestContext()
    principal_id: str | None = identity.principal_id
    tenant = require_tenant(identity, context=req.context)
    logger.info(f"[GET_PRODUCTS] Tenant context: {tenant['tenant_id']}")

    # Get the Principal object with ad server mappings
    principal = get_principal_object(principal_id, tenant_id=identity.tenant_id) if principal_id else None

    # Extract offering text from brand (adcp 3.6.0: brand replaces brand_manifest).
    # req.brand is BrandReference | None (Pydantic model with .domain attribute).
    offering = None
    if req.brand:
        domain = getattr(req.brand, "domain", None)
        if domain:
            offering = f"Brand at {domain}"

    # Check brand_manifest_policy from tenant settings
    brand_manifest_policy = tenant.get("brand_manifest_policy", "require_auth")

    # Enforce policy-based validation
    if brand_manifest_policy == "require_brand" and not offering:
        raise AdCPAuthorizationError("Brand manifest required by tenant policy")
    elif brand_manifest_policy == "require_auth" and not principal_id:
        raise AdCPAuthenticationError("Authentication required by tenant policy")
    # public policy allows all requests (no brand_manifest or auth required)

    # For non-public policies, we need offering for policy checks and product matching
    # Use a generic offering if not provided
    if not offering:
        offering = "Generic product inquiry"

    # Skip strict validation in test environments (allow simple test values)

    is_test_mode = (testing_ctx and testing_ctx.test_session_id is not None) or os.getenv("ADCP_TESTING") == "true"

    # Note: brand_manifest validation is handled by Pydantic schema, no need for runtime validation here

    # Check policy compliance first (if enabled)
    advertising_policy = safe_parse_json_field(
        tenant.get("advertising_policy"), field_name="advertising_policy", default={}
    )

    # Only run policy checks if enabled in tenant settings
    policy_check_enabled = advertising_policy.get("enabled", False)  # Default to False for new tenants
    policy_disabled_reason = None

    # Extract brief text early - needed for policy checks, dynamic variants, and AI ranking
    brief_text = req.brief if req.brief else ""

    if not policy_check_enabled:
        # Skip policy checks if disabled
        policy_result = None
        policy_disabled_reason = "disabled_by_tenant"
        logger.info(f"Policy checks disabled for tenant {tenant['tenant_id']}")
    else:
        # Resolve the tenant's own AI configuration. `TenantAIConfig.from_tenant` returns
        # a config only when the tenant has its OWN usable credential — ai_config with a
        # non-empty api_key first, the legacy gemini_api_key column second, None
        # otherwise — so `is not None` here reads as "this seller has a credential of its
        # own". That is the same question the pre-PR gate asked as
        # `tenant.get("gemini_api_key")`, generalised to the column the Admin UI actually
        # writes: reading only gemini_api_key meant a tenant that configured AI in the UI
        # had its policy checks silently skipped even with "enabled" ticked.
        #
        # Usability is load-bearing, not incidental. src/admin/blueprints/settings.py
        # stores {"provider": ..., "model": ...} with NO api_key when the seller leaves
        # the key blank, and tells them "AI features will be disabled" as it does. If
        # that row came back as a configuration, this gate would open and
        # check_brief_compliance would make a live LLM call on the OPERATOR's platform
        # credential — and a BLOCKED verdict raises AdCPPolicyViolationError below, a new
        # buyer-visible rejection for a tenant the UI said was switched off.
        #
        # The gate is NOT factory.is_ai_enabled(): is_ai_enabled counts the platform
        # GEMINI_API_KEY from the environment, so gating on it would silently switch
        # policy checks on for every tenant on any deployment that sets that variable.
        # Gating on the tenant's own configuration widens the gate exactly as far as the
        # bug requires and no further.
        tenant_ai_config = TenantAIConfig.from_tenant(tenant)
        if tenant_ai_config is None:
            # No AI configuration - cannot run policy checks
            policy_result = None
            policy_disabled_reason = "no_ai_configuration"
            logger.warning(f"Policy checks enabled but no AI configuration for tenant {tenant['tenant_id']}")
        else:
            # tenant_ai_config=, not the deprecated gemini_api_key=: that parameter pins
            # provider/model instead of honouring the tenant's own, and its _UNSET
            # sentinel treats an explicit None as "hard-disable AI". Leaving it unset
            # keeps the intended branch — configuration honoured, platform defaults
            # filling any field the tenant did not set.
            policy_service = PolicyCheckService(tenant_ai_config=tenant_ai_config)

            # Use advertising_policy settings for tenant-specific rules
            tenant_policies = advertising_policy if advertising_policy else {}

            try:
                policy_result = await policy_service.check_brief_compliance(
                    brief=brief_text,
                    promoted_offering=offering,  # Use extracted offering from brand
                    brand_manifest=None,  # adcp 3.6.0: brand_manifest replaced by brand; policy service still accepts None
                    tenant_policies=tenant_policies if tenant_policies else None,
                )

                # Log successful policy check
                audit_logger = get_audit_logger("AdCP", tenant["tenant_id"])
                audit_logger.log_operation(
                    operation="policy_check",
                    principal_name=principal_id or "anonymous",
                    principal_id=principal_id or "anonymous",
                    adapter_id="policy_service",
                    success=policy_result.status != PolicyStatus.BLOCKED,
                    details={
                        "brief": brief_text[:100] + "..." if len(brief_text) > 100 else brief_text,
                        "brand_name": offering[:100] + "..." if offering and len(offering) > 100 else offering,
                        "policy_status": policy_result.status,
                        "reason": policy_result.reason,
                        "restrictions": policy_result.restrictions,
                    },
                )

            except Exception as e:
                # Policy check failed - log error
                logger.error(f"Policy check failed for tenant {tenant['tenant_id']}: {e}")
                audit_logger = get_audit_logger("AdCP", tenant["tenant_id"])
                audit_logger.log_operation(
                    operation="policy_check_failure",
                    principal_name=principal_id or "anonymous",
                    principal_id=principal_id or "anonymous",
                    adapter_id="policy_service",
                    success=False,
                    details={
                        "error": str(e),
                        "error_type": type(e).__name__,
                        "brief": brief_text[:100] + "..." if len(brief_text) > 100 else brief_text,
                    },
                )

                # Fail open by default (allow campaigns) with warning in response
                policy_result = None
                policy_disabled_reason = f"service_error: {type(e).__name__}"
                logger.warning(f"Policy check failed, allowing campaign by default: {e}")

    # Handle policy result based on settings
    if policy_result and policy_result.status == PolicyStatus.BLOCKED:
        # Always block if policy says blocked
        logger.warning(f"Brief blocked by policy: {policy_result.reason}")
        # Raise ToolError to properly signal failure to client
        raise AdCPPolicyViolationError(policy_result.reason or "Blocked by policy")

    # If restricted and manual review is required, create a task
    if (
        policy_result
        and policy_result.status == PolicyStatus.RESTRICTED
        and advertising_policy.get("require_manual_review", False)
    ):
        # Log policy violation for audit trail and compliance
        audit_logger = get_audit_logger("AdCP", tenant["tenant_id"])
        principal_name = principal_id if principal_id else "anonymous"
        audit_logger.log_operation(
            operation="get_products_policy_violation",
            principal_name=principal_name,
            principal_id=principal_name,
            adapter_id="policy_engine",
            success=False,
            details={
                "brief": req.brief,
                "brand_name": offering,
                "policy_status": policy_result.status,
                "restrictions": policy_result.restrictions,
                "reason": policy_result.reason,
            },
        )

        # Raise error for policy violations - explicit failure, not silent return
        restrictions_list = policy_result.restrictions if policy_result.restrictions else []
        raise AdCPPolicyViolationError(
            f"Request violates content policy: {policy_result.reason}. Restrictions: {', '.join(restrictions_list)}"
        )

    # Resolve adapter type for delivery_measurement defaults
    ad_server_config = tenant.get("ad_server", {})
    tenant_adapter_type = (
        ad_server_config.get("adapter", "mock") if isinstance(ad_server_config, dict) else ad_server_config
    )

    # Query products via repository (tenant-scoped)
    from src.core.database.repositories.uow import ProductUoW

    with ProductUoW(tenant["tenant_id"]) as uow:
        assert uow.products is not None
        db_products = uow.products.list_all()

        # Convert database Product models to AdCP Product schema
        products = []
        for product_obj in db_products:
            try:
                validated_product = convert_product_model_to_schema(product_obj, adapter_type=tenant_adapter_type)
                products.append(validated_product)
                logger.debug(f"Successfully converted product {product_obj.product_id}")
            except AdCPError:
                raise
            except Exception as e:
                error_msg = (
                    f"Product '{product_obj.product_id}' failed to convert to AdCP schema. "
                    f"This indicates data corruption or migration issue. Error: {e}"
                )
                logger.error(error_msg)
                raise AdCPAdapterError(error_msg) from e

    logger.info(f"[GET_PRODUCTS] Got {len(products)} products from database for tenant {tenant['tenant_id']}")

    # Filter products by principal access control
    # Products with allowed_principal_ids set are only visible to those specific principals
    # Products with null/empty allowed_principal_ids are visible to all (default)
    if principal_id:
        filtered_by_access = []
        for product in products:
            # Check if product has access restrictions
            allowed_ids = getattr(product, "allowed_principal_ids", None)
            if allowed_ids is None or len(allowed_ids) == 0:
                # No restrictions - visible to all
                filtered_by_access.append(product)
            elif principal_id in allowed_ids:
                # Principal is in the allowed list
                filtered_by_access.append(product)
            else:
                # Principal not in allowed list - skip this product
                logger.debug(f"Product {product.product_id} hidden from principal {principal_id} (not in allowed list)")
        products = filtered_by_access
        logger.info(f"[GET_PRODUCTS] After principal access filtering: {len(products)} products")
    else:
        # No principal authenticated - only show unrestricted products
        # This handles anonymous/discovery requests
        filtered_by_access = []
        for product in products:
            allowed_ids = getattr(product, "allowed_principal_ids", None)
            if allowed_ids is None or len(allowed_ids) == 0:
                # No restrictions - visible to anonymous users
                filtered_by_access.append(product)
            else:
                # Product has restrictions - hide from anonymous users
                logger.debug(f"Product {product.product_id} hidden from anonymous user (has access restrictions)")
        products = filtered_by_access
        logger.info(f"[GET_PRODUCTS] After anonymous access filtering: {len(products)} products")

    # Filter products by buyer property list (if provided)
    # Use isinstance check to safely handle mock objects in tests
    _property_list_ref = getattr(req, "property_list", None)
    if isinstance(_property_list_ref, PropertyListReference):
        # No try/except here on purpose. Typed AdCPErrors already carry their own
        # buyer-facing classification, and anything else is a fault on our side —
        # the transport boundary translates it honestly (SERVICE_UNAVAILABLE,
        # transient, 500). The handler that used to sit here relabelled every
        # implementation bug as AdCPValidationError, so the buyer read "your
        # request is malformed" for our crash, and it forced recovery="transient"
        # against the pinned enum's "correctable" for that code — telling the
        # buyer their request was invalid AND that retrying it might work.
        from src.core.property_list_resolver import resolve_property_list

        allowed_property_ids = await resolve_property_list(_property_list_ref)
        allowed_set = set(allowed_property_ids)
        products = filter_products_by_property_list(products, allowed_set)
        logger.info(
            f"[GET_PRODUCTS] After property list filtering: {len(products)} products "
            f"(allowed {len(allowed_set)} properties)"
        )

    # Generate dynamic product variants from signals agents
    try:
        from src.services.dynamic_products import generate_variants_for_brief

        # Get our agent URL for deployment specification
        our_agent_url = tenant.get("virtual_host")  # Our sales agent URL (e.g., https://sales.example.com)

        dynamic_variants = await generate_variants_for_brief(tenant["tenant_id"], brief_text, our_agent_url)
        if dynamic_variants:
            # Convert Product models to Product schemas for response

            for variant_model in dynamic_variants:
                # Convert database model to schema (returns library Product)
                # Cast to our extended Product type for mypy compatibility
                variant_schema = convert_product_model_to_schema(variant_model, adapter_type=tenant_adapter_type)
                # Type: ignore - library Product is compatible with our extended Product at runtime
                products.append(variant_schema)

            logger.info(f"[GET_PRODUCTS] Added {len(dynamic_variants)} dynamic product variants")
    except (ImportError, RuntimeError, OSError) as e:
        logger.warning(f"Failed to generate dynamic product variants: {e}. Continuing with static products only.")

    logger.info(f"[GET_PRODUCTS] Total products (static + dynamic): {len(products)}")

    # Enrich products with dynamic pricing from cached performance metrics
    # Updates pricing_options with price_guidance (floor, recommended) and estimated_exposures
    try:
        from src.services.dynamic_pricing_service import DynamicPricingService

        # Extract country from request if available (future enhancement: parse from targeting)
        country_code = None  # TODO: Extract from targeting if provided

        with ProductUoW(tenant["tenant_id"]) as pricing_uow:
            # FIXME(#1119): DynamicPricingService needs a repository, not raw session
            assert pricing_uow.session is not None
            pricing_service = DynamicPricingService(pricing_uow.session)
            products = pricing_service.enrich_products_with_pricing(
                products,
                tenant_id=tenant["tenant_id"],
                country_code=country_code,
                min_exposures=getattr(req.filters, "min_exposures", None) if req.filters else None,
            )
    except (ImportError, RuntimeError, OSError) as e:
        logger.warning(f"Failed to enrich products with dynamic pricing: {e}. Using defaults.")

    # Apply AdCP filters if provided
    if req.filters:
        filtered_products = []
        for product in products:
            # Filter by delivery_type
            if req.filters.delivery_type and product.delivery_type != req.filters.delivery_type:
                continue

            # Filter by is_fixed_price (check pricing_options)
            # Spec: true = at least one option with fixed_price,
            #        false = at least one option without fixed_price.
            #        Products with both fixed and auction options match both.
            if req.filters.is_fixed_price is not None:
                # PricingOption is a Pydantic RootModel — unwrap via .root
                # to access inner variant fields (fixed_price lives on the variant)
                if req.filters.is_fixed_price:
                    has_matching_pricing = any(
                        getattr(po.root, "fixed_price", None) is not None for po in product.pricing_options
                    )
                else:
                    has_matching_pricing = any(
                        getattr(po.root, "fixed_price", None) is None for po in product.pricing_options
                    )
                if not has_matching_pricing:
                    continue

            # Filter by format_ids (format_types removed in adcp 3.12)
            if req.filters.format_ids:
                # Product.format_ids is list[str] or list[dict] (format IDs)
                product_format_ids: set[str] = set()
                for format_id in product.format_ids or []:  # adcp 6.6: Product.format_ids is now Optional (spec 3.1.1)
                    if isinstance(format_id, str):
                        product_format_ids.add(format_id)
                    elif isinstance(format_id, dict):
                        # Dict with 'id' key (from database)
                        dict_id = format_id.get("id")
                        if dict_id is not None:
                            product_format_ids.add(dict_id)
                    elif isinstance(format_id, FormatId):
                        product_format_ids.add(format_id.id)

                # req.filters.format_ids contains FormatId objects, extract .id from them
                request_format_ids: set[str] = set()
                for fmt_id in req.filters.format_ids:
                    if isinstance(fmt_id, str):
                        request_format_ids.add(fmt_id)
                    elif isinstance(fmt_id, FormatId):
                        request_format_ids.add(fmt_id.id)
                    elif isinstance(fmt_id, dict):
                        dict_id = fmt_id.get("id")
                        if dict_id is not None:
                            request_format_ids.add(dict_id)

                if not any(fmt_id in product_format_ids for fmt_id in request_format_ids):
                    continue

            # Filter by standard_formats_only
            if req.filters.standard_formats_only:
                # Check if all formats are IAB standard formats
                # IAB standard formats typically follow patterns like "display_", "video_", "audio_", "native_"
                has_only_standard = True
                for format_id in product.format_ids or []:  # adcp 6.6: Product.format_ids is now Optional (spec 3.1.1)
                    format_id_str: str | None = None
                    if isinstance(format_id, str):
                        format_id_str = format_id
                    elif isinstance(format_id, dict):
                        format_id_str = format_id.get("id")
                    elif isinstance(format_id, FormatId):
                        format_id_str = format_id.id

                    if format_id_str and not format_id_str.startswith(("display_", "video_", "audio_", "native_")):
                        has_only_standard = False
                        break

                if not has_only_standard:
                    continue

            # Filter by countries
            if req.filters.countries:
                # Get product's countries from the placements or targeting
                product_countries: set[str] = set()

                # Check if product has countries field (from database)
                # Our extended Product may have a countries field
                if product.countries:
                    product_countries.update(product.countries)

                # If no countries specified, product is considered available everywhere
                if not product_countries:
                    # Product has no country restrictions, matches any country filter
                    pass
                else:
                    # Extract country codes from filter (Country is RootModel[str])
                    request_countries: set[str] = set()
                    for country in req.filters.countries:
                        request_countries.add(country.root.upper())

                    # Check if any requested country is in the product's countries
                    if not product_countries.intersection(request_countries):
                        continue

            # Filter by channels
            if req.filters.channels:
                # Check if product has channels field
                product_channels: set[str] = set()
                if product.channels:
                    product_channels = {c.value.lower() for c in product.channels}

                # Extract channel values from filter (enum values)
                request_channels: set[str] = set()
                for channel in req.filters.channels:
                    request_channels.add(channel.value.lower())

                if product_channels:
                    # Product has explicit channels - must have at least one match
                    if not product_channels.intersection(request_channels):
                        continue
                else:
                    # Product has no channels - use adapter defaults
                    # Get adapter type from tenant config
                    ad_server_config = tenant.get("ad_server", {})
                    adapter_type = (
                        ad_server_config.get("adapter", "mock")
                        if isinstance(ad_server_config, dict)
                        else ad_server_config
                    )
                    adapter_channels = get_adapter_default_channels(adapter_type)

                    # Product matches if any of adapter's default channels is in request
                    if adapter_channels and not request_channels.intersection(set(adapter_channels)):
                        continue

            # Filter by device_types (local extension, not in AdCP spec)
            requested_device_types = getattr(req.filters, "device_types", None)
            if requested_device_types:
                product_device_types = getattr(product, "device_types", None)
                if product_device_types:
                    # Product declares supported device types — must have intersection
                    if not set(product_device_types).intersection(set(requested_device_types)):
                        continue
                # else: product has no device_types restriction — matches any filter

            # Product passed all filters
            filtered_products.append(product)

        products = filtered_products
        logger.info("Applied filters: %s. %d products remain.", req.filters, len(products))

    # Filter products based on policy compliance (if policy checks are enabled)
    eligible_products = []
    if policy_result and policy_check_enabled:
        # Policy checks are enabled - filter products based on policy compliance
        for product in products:
            is_eligible, reason = policy_service.check_product_eligibility(policy_result, product)

            if is_eligible:
                # Product passed policy checks - add to eligible products
                # Note: policy_compliance field removed in AdCP v2.4
                eligible_products.append(product)
            else:
                logger.info(f"Product {product.product_id} excluded: {reason}")
    else:
        # Policy checks disabled - all products are eligible
        eligible_products = products

    # Apply min_exposures filtering (AdCP PR #79)
    min_exposures = getattr(req.filters, "min_exposures", None) if req.filters else None
    if min_exposures is not None:
        filtered_products = []
        for product in eligible_products:
            # For guaranteed products, check estimated_exposures
            delivery_type_value = enum_value(product.delivery_type)
            if delivery_type_value == "guaranteed":
                estimated = getattr(product, "estimated_exposures", None)
                if estimated is not None and estimated >= min_exposures:
                    filtered_products.append(product)
                else:
                    logger.info(
                        f"Product {product.product_id} excluded: estimated_exposures "
                        f"({estimated}) < min_exposures ({min_exposures})"
                    )
            else:
                # For non-guaranteed, include if recommended CPM is set in price_guidance
                # (indicates it can meet min_exposures) or if no pricing data available
                # (product doesn't provide exposure estimates)
                recommended = get_recommended_cpm(product)
                if recommended is not None:
                    filtered_products.append(product)
                else:
                    # Include non-guaranteed products without price_guidance (can't filter by exposure estimates)
                    filtered_products.append(product)
        eligible_products = filtered_products

    # AI-powered product ranking (when tenant has product_ranking_prompt configured)
    advisories: list[Error] = []
    product_ranking_prompt = tenant.get("product_ranking_prompt")
    if product_ranking_prompt and brief_text and eligible_products:
        # No handler here on purpose: `_rank_products_with_ai` owns every failure path so
        # that each one emits its advisory. A `try` here could only log and drop through
        # with `advisories` empty — which is how a provider 429 returned an unranked list
        # with `errors=None`.
        eligible_products = await _rank_products_with_ai(
            products=eligible_products,
            tenant=tenant,
            ranking_prompt=product_ranking_prompt,
            brief_text=brief_text,
            advisories=advisories,
        )

    # Annotate pricing options with adapter support (AdCP PR #88)
    # Do this BEFORE serialization to avoid reconstruction issues
    if principal and eligible_products:
        try:
            # Use correct get_adapter from adapter_helpers (accepts Principal and dry_run)
            from src.core.helpers.adapter_helpers import get_adapter

            # Get adapter in dry-run mode (no actual ad server calls)
            adapter = get_adapter(principal, dry_run=True, tenant=tenant)

            supported_models = adapter.get_supported_pricing_models()

            for product in eligible_products:
                if product.pricing_options:
                    # Annotate each pricing option with "supported" flag
                    for option in product.pricing_options:
                        inner = option.root
                        # Get pricing model as string (handle both enum and literal)
                        pricing_model = getattr(inner.pricing_model, "value", inner.pricing_model)
                        # Add supported annotation (will be included in response)
                        # Dynamic attributes on discriminated union types
                        is_supported = pricing_model in supported_models
                        inner.supported = is_supported  # type: ignore[union-attr]
                        if not is_supported:
                            inner.unsupported_reason = (  # type: ignore[union-attr]
                                f"Current adapter does not support {str(pricing_model).upper()} pricing"
                            )
        except (ImportError, RuntimeError, OSError, ValueError) as e:
            logger.warning(f"Failed to annotate pricing options with adapter support: {e}")

    # Filter pricing data for anonymous users
    # Do this BEFORE serialization to avoid reconstruction issues
    if principal_id is None:  # Anonymous user
        # Remove pricing data from products for anonymous users
        # Set to empty list to hide pricing (will be excluded during serialization)
        for product in eligible_products:
            product.pricing_options = []

    # Our Product extends LibraryProduct - cast for type safety since list is invariant
    # When serialized, Pydantic automatically uses library Product fields
    # Internal-only fields (implementation_config) excluded by model_dump()
    # Note: We use eligible_products (Product objects), not response_data (dicts)
    # because Product objects have typed pricing_options (CpmFixedRatePricingOption, etc.)
    # while dicts lose this type information during serialization
    # adcp 2.16.0+ accepts subclass lists at runtime via BeforeValidator coercion,
    # but mypy still needs cast() due to list invariance in static typing
    resp = GetProductsResponse(
        products=cast(list[LibraryProduct], eligible_products),
        errors=advisories or None,
        context=req.context,
    )

    # Log successful get_products call
    elapsed_ms = int((time.time() - start_time) * 1000)
    audit_logger = get_audit_logger("AdCP", tenant["tenant_id"])
    audit_logger.log_operation(
        operation="get_products",
        principal_name=principal_id or "anonymous",
        principal_id=principal_id or "anonymous",
        adapter_id="mcp_server",
        success=True,
        details={
            "product_count": len(eligible_products),
            "brief_length": len(brief_text),
            "has_filters": req.filters is not None,
            "has_brand": req.brand is not None,
            "elapsed_ms": elapsed_ms,
        },
    )

    return resp


async def get_products(
    brand: Annotated[
        BrandReference | dict[str, Any] | str | None,
        Field(
            description=(
                "Brand reference (object with domain), domain/URL string shorthand "
                "(e.g. 'acme.com' / 'https://acme.com'), or equivalent dict"
            )
        ),
    ] = None,
    brief: Annotated[str, Field(description="Natural language description of campaign goals and requirements")] = "",
    filters: ProductFilters | None = None,
    property_list: PropertyListReference | None = None,
    context: ContextObject | None = None,  # payload-level context
    ctx: Context | ToolContext | None = None,
):
    """Get available products matching the brief.

    MCP tool wrapper aligned with adcp v3.6.0 spec.

    Args:
        brand: Brand reference per adcp 3.6.0. Example: BrandReference(domain="acme.com")
        brief: Brief description of the advertising campaign or requirements (optional)
        filters: Structured filters for product discovery (optional)
        property_list: Property list reference for filtering by buyer's property list (optional)
        context: Application level context per adcp spec
        ctx: FastMCP context (automatically provided)

    Returns:
        ToolResult with human-readable text and structured data
    """
    # create_get_products_request coerces string/dict brand via to_brand_reference
    try:
        with adcp_validation_boundary(context="get_products request"):
            req = create_get_products_request(
                brief=brief,
                brand=brand,
                filters=filters,
                property_list=property_list,
                context=context,
            )
    except ValueError as e:
        # Helper raises ValueError for semantic (non-Pydantic) input problems.
        raise AdCPValidationError(
            f"Invalid get_products request: {e}",
            suggestion="Correct the get_products request per the AdCP specification and resend.",
        ) from e

    # Read identity pre-resolved by MCPAuthMiddleware
    identity = (await ctx.get_state("identity")) if isinstance(ctx, Context) else None

    # Call shared implementation
    # Note: GetProductsRequest is now a flat class (not RootModel), so pass req directly
    response = await _get_products_impl(req, identity)

    return mcp_result(response)


async def get_products_raw(
    brief: str = "",
    brand: BrandReference | str | None = None,
    filters: ProductFilters | None = None,
    property_list: PropertyListReference | None = None,
    context: ContextObject | None = None,  # Application level context per adcp spec
    ctx: Context | ToolContext | None = None,
    identity: ResolvedIdentity | None = None,
) -> GetProductsResponse:
    """Get available products matching the brief.

    Raw function without @mcp.tool decorator for A2A server use.
    Returns a clean GetProductsResponse model — v2 compat is applied
    at the caller's boundary (A2A handler), not here.

    Args:
        brief: Brief description of the advertising campaign or requirements
        brand: Brand reference per adcp 3.6.0 (BrandReference or string domain shorthand)
        filters: Structured filters for product discovery (optional)
        property_list: Property list reference for filtering by buyer's property list (optional)
        context: Application level context per adcp spec
        ctx: FastMCP context (automatically provided)
        identity: Resolved identity from transport boundary (preferred over ctx)

    Returns:
        GetProductsResponse containing matching products
    """
    # Resolve identity from transport context if not provided
    if identity is None:
        identity = resolve_identity_from_context(ctx, require_valid_token=False)

    # Create request object - adcp library validates schema
    req = create_get_products_request(
        brief=brief or "",
        brand=brand,
        filters=filters,
        property_list=property_list,
        context=context,
    )

    # Call shared implementation
    return await _get_products_impl(req, identity)


def get_product_catalog(tenant_id: str | None = None) -> list[Product]:
    """Get products for a tenant.

    Args:
        tenant_id: Tenant ID to load products for. Falls back to ContextVar if not provided.

    Returns:
        List of Product objects with full pricing options
    """
    from src.core.database.repositories.uow import ProductUoW

    if tenant_id is None:
        from src.core.config_loader import get_current_tenant

        tenant_id = get_current_tenant()["tenant_id"]

    with ProductUoW(tenant_id) as uow:
        assert uow.products is not None
        products = uow.products.list_all_with_inventory()

        # Use convert_product_model_to_schema for consistency
        loaded_products = []
        for product in products:
            loaded_products.append(convert_product_model_to_schema(product))

    return loaded_products
