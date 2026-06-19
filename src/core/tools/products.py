"""Get products tool implementation.

This module contains the get_products tool implementation following the MCP/A2A
shared implementation pattern from CLAUDE.md.
"""

import logging
import os
import time
from typing import TYPE_CHECKING, Annotated, Any, cast

# FIXME(#1388): FormatId, ProductFilters have local subclasses; import from src.core.schemas (Pattern #7/#4).
from adcp import FormatId, ProductFilters
from adcp import GetProductsRequest as GetProductsRequestGenerated
from adcp import Product as LibraryProduct
from adcp.types import BrandReference, ContextObject, PropertyListReference
from adcp.types.generated_poc.media_buy.get_products_request import Refine
from adcp.types.generated_poc.media_buy.get_products_response import RefinementApplied
from fastmcp.server.context import Context
from fastmcp.tools.tool import ToolResult
from pydantic import Field, ValidationError
from sqlalchemy.exc import DisconnectionError, InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as SATimeoutError

from src.adapters import get_adapter_default_channels
from src.core.audit_logger import get_audit_logger
from src.core.auth import get_principal_object, require_identity, require_tenant
from src.core.exceptions import (
    AdCPAdapterError,
    AdCPAuthenticationError,
    AdCPAuthorizationError,
    AdCPError,
    AdCPPolicyViolationError,
    AdCPServiceUnavailableError,
    AdCPValidationError,
)
from src.core.helpers import enum_value
from src.core.resolved_identity import ResolvedIdentity
from src.core.schema_helpers import create_get_products_request
from src.core.schemas import (
    Error,
    GetProductsResponse,
    Product,  # Extends library Product
)
from src.core.testing_hooks import AdCPTestContext
from src.core.tool_context import ToolContext
from src.core.transport_helpers import resolve_identity_from_context
from src.core.validation_helpers import safe_parse_json_field
from src.core.version_compat import apply_version_compat
from src.services.policy_check_service import PolicyCheckService, PolicyStatus
from src.services.property_intersection import PropertyIntersection, property_list_drop_advisory

if TYPE_CHECKING:
    from src.services.property_intersection import DroppedProduct

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

# Per-item dropped-product advisories stay bounded: enumerate up to this many,
# then aggregate the tail into a single count entry.
_MAX_DROPPED_PRODUCT_ADVISORIES = 25


def _dropped_product_advisories(dropped: "tuple[DroppedProduct, ...]") -> list[Error]:
    """Buyer-visible advisories for products excluded by their property_list.

    One ``Error`` per dropped product (bounded), carrying the structured
    ``DropReason`` — the accept-with-context counterpart to the media-buy
    side's UNSUPPORTED_FEATURE reject: the request succeeds, and the envelope
    says what the filter did instead of silently shrinking the list.
    """
    # PRODUCT_UNAVAILABLE is the closest standard code at spec 3.0.1 (the
    # storyboard's INSUFFICIENT_INVENTORY/INVALID_TARGETING aren't in the
    # 3.0.1 enum); "unavailable under your filter" is a documented stretch
    # of its sold-out wording, chosen over a non-standard code so buyers
    # keep standard-table recovery resolution. The code and details
    # vocabulary live in property_list_drop_advisory.
    advisories = [
        property_list_drop_advisory(
            message=(
                f"Product {getattr(item.product, 'product_id', '<unknown>')} was excluded by your "
                f"property_list filter ({item.reason.value})"
            ),
            field="property_list",
            product_id=getattr(item.product, "product_id", None),
            reason=item.reason,
            suggestion="Broaden the property_list (or omit it) to see this seller's full catalog.",
        )
        for item in dropped[:_MAX_DROPPED_PRODUCT_ADVISORIES]
    ]
    overflow = len(dropped) - _MAX_DROPPED_PRODUCT_ADVISORIES
    if overflow > 0:
        advisories.append(
            property_list_drop_advisory(
                message=f"{overflow} more products were excluded by your property_list filter",
                field="property_list",
                additional_dropped=overflow,
            )
        )
    return advisories


_REFINE_NOT_PERSISTED_NOTES = "Proposal-state persistence is not yet implemented; refinement cannot be applied."


def _build_refinement_applied_unable(
    refine_entries: list[Refine] | None,
) -> list[RefinementApplied]:
    """Build a refinement_applied response for buying_mode='refine'.

    Until proposal-state persistence and intelligent refinement ship, every entry reports
    status='unable' with a notes field that names the gap. The response matches the
    request's refine array by position per AdCP spec, and echoes each entry's scope and
    id field (product_id / proposal_id) so orchestrators can cross-validate alignment.
    """
    if not refine_entries:
        return []

    items: list[RefinementApplied] = []
    for entry in refine_entries:
        # Entry is a discriminated-union root over Refine1 (request) / Refine2 (product) /
        # Refine3 (proposal). Pull the inner variant via .root, then read scope and the
        # scope-specific id field. Fail-fast attribute access — the request validator
        # already guarantees the id is present for product/proposal scopes.
        inner = getattr(entry, "root", entry)
        scope = inner.scope
        payload: dict[str, Any] = {
            "scope": scope,
            "status": "unable",
            "notes": _REFINE_NOT_PERSISTED_NOTES,
        }
        if scope == "product":
            payload["product_id"] = inner.product_id
        elif scope == "proposal":
            payload["proposal_id"] = inner.proposal_id
        items.append(RefinementApplied.model_validate(payload))
    return items


async def _get_products_impl(
    req: GetProductsRequestGenerated,
    identity: ResolvedIdentity | None,
    pre_v3_defaulted: bool = False,
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

    # buying_mode normalization + cross-mode invariants live in
    # _validate_buying_mode_invariants on GetProductsRequest — it runs on every model
    # construction (via create_get_products_request) and raises before _impl sees the
    # request, so trust that single layer here rather than re-checking.
    mode = enum_value(req.buying_mode)

    # No generic brief/brand/filters gate: the cross-mode validator enforces per-mode
    # requirements (brief mode needs a brief, refine needs a refine array), and wholesale
    # legitimately requests raw inventory with no criterion.

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
        raise AdCPAuthorizationError("Brand manifest required by tenant policy", recovery="correctable")
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
        # Get tenant's Gemini API key for policy checks
        tenant_gemini_key = tenant.get("gemini_api_key")
        if not tenant_gemini_key:
            # No API key - cannot run policy checks
            policy_result = None
            policy_disabled_reason = "no_gemini_api_key"
            logger.warning(f"Policy checks enabled but no Gemini API key configured for tenant {tenant['tenant_id']}")
        else:
            policy_service = PolicyCheckService(gemini_api_key=tenant_gemini_key)

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

    # Query products via repository (tenant-scoped).
    # Lazy: tests patch ProductUoW at its source module; the name must
    # resolve at call time (one import serves every use in this function).
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
    # property_list filtering result, surfaced on the response per the spec's
    # GetProductsResponse contract: ``property_list_applied`` flags that the
    # filter ran, and dropped products become per-item ``errors[]`` advisories
    # ("Task-specific errors and warnings (e.g., product filtering issues)") so
    # the buyer can refine the filter instead of guessing at a shrunk list.
    property_list_applied: bool | None = None
    property_list_errors: list[Error] = []
    _property_list_ref = getattr(req, "property_list", None)
    if isinstance(_property_list_ref, PropertyListReference):
        try:
            # Lazy: tests patch the resolver at its source module; it must
            # resolve at call time.
            from src.core.property_list_resolver import resolve_property_list_typed

            buyer_identifiers = await resolve_property_list_typed(_property_list_ref)
            with ProductUoW(tenant["tenant_id"]) as intersection_uow:
                assert intersection_uow.authorized_properties is not None
                intersection = PropertyIntersection(intersection_uow.authorized_properties)
                result = intersection.filter_products(products, buyer_identifiers)
            products = list(result.kept_products)
            property_list_applied = True
            property_list_errors = _dropped_product_advisories(result.dropped_products)
            logger.info(
                f"[GET_PRODUCTS] After property list filtering: {len(products)} products "
                f"(buyer list has {len(buyer_identifiers)} identifiers; dropped {len(result.dropped_products)})"
            )
        except AdCPError:
            raise
        except ValidationError as e:
            # The buyer's list service answered with a payload that isn't a
            # GetPropertyListResponse — correctable on the buyer's side (fix
            # the list service or the reference), not retryable as-is.
            raise AdCPValidationError(f"Property list service returned an invalid response: {e}") from e
        except (OperationalError, InterfaceError, SATimeoutError, DisconnectionError) as e:
            # Genuinely-transient infrastructure failures (connection refused/
            # dropped, pool exhausted) → SERVICE_UNAVAILABLE/transient per the
            # spec's recovery taxonomy. DB-API programming errors
            # (ProgrammingError/IntegrityError/DataError) and everything else
            # PROPAGATE to the boundary, which normalizes unexpected exceptions
            # to the internal-error envelope — bugs are not retryable.
            logger.error("Property list intersection infrastructure failure: %s", e)
            raise AdCPServiceUnavailableError(
                "Could not evaluate property_list filtering against the property catalog"
            ) from e

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
            # FIXME(salesagent-9f2): DynamicPricingService needs a repository, not raw session
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
                for format_id in product.format_ids:
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
                for format_id in product.format_ids:
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
    product_ranking_prompt = tenant.get("product_ranking_prompt")
    # AI ranking runs in brief mode only; wholesale returns raw inventory and refine
    # iterates on prior state — both bypass the ranker per the AdCP three-mode contract.
    if mode == "brief" and product_ranking_prompt and brief_text and eligible_products:
        try:
            from src.services.ai.agents.ranking_agent import (
                create_ranking_agent,
                rank_products_async,
            )
            from src.services.ai.factory import get_factory

            factory = get_factory()
            if factory.is_ai_enabled():
                model = factory.create_model()
                agent = create_ranking_agent(model)

                # Convert products to dicts for ranking
                # Run AI ranking
                ranking_result = await rank_products_async(
                    agent=agent,
                    custom_prompt=product_ranking_prompt,
                    brief=brief_text,
                    products=eligible_products,
                )

                # Build a map of product_id -> (score, reason)
                ranking_map = {r.product_id: (r.relevance_score, r.reason) for r in ranking_result.rankings}

                # Sort products by relevance score (highest first)
                # Products not in ranking_map get score 0
                eligible_products.sort(
                    key=lambda p: ranking_map.get(p.product_id, (0.0, ""))[0],
                    reverse=True,
                )

                # Filter out products with very low relevance (score < 0.1)
                eligible_products = [p for p in eligible_products if ranking_map.get(p.product_id, (0.0, ""))[0] >= 0.1]

                # Surface the ranker's reason on each surviving product as brief_relevance
                # per the AdCP 3.0.1 Product schema ("only included when brief is provided").
                # Spec-compliant since brief mode requires a brief.
                for product in eligible_products:
                    reason = ranking_map.get(product.product_id, (0.0, ""))[1]
                    if reason:
                        product.brief_relevance = reason

                # Log the ranking results
                for r in ranking_result.rankings:
                    logger.info(f"[AI_RANKING] {r.product_id}: score={r.relevance_score:.2f}, reason={r.reason}")

                logger.info(
                    f"[GET_PRODUCTS] AI ranking applied: {len(ranking_result.rankings)} products ranked, "
                    f"{len(eligible_products)} products above threshold"
                )
            else:
                logger.debug("[GET_PRODUCTS] AI ranking configured but AI not enabled (no API key)")
        except (ImportError, RuntimeError, OSError) as e:
            logger.warning(f"Failed to apply AI product ranking: {e}. Returning unranked products.")

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
                        pricing_model = enum_value(inner.pricing_model)
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
    # buying_mode='refine' returns refinement_applied (status='unable' until proposal-state
    # persistence ships); brief and wholesale omit it.
    refinement_applied = _build_refinement_applied_unable(req.refine) if mode == "refine" else None

    resp = GetProductsResponse(
        products=cast(list[LibraryProduct], eligible_products),
        errors=property_list_errors or None,
        property_list_applied=property_list_applied,
        refinement_applied=refinement_applied,
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
            "buying_mode": mode,
            "refine_count": len(req.refine) if req.refine else 0,
            "pre_v3_defaulted": pre_v3_defaulted,
            "elapsed_ms": elapsed_ms,
        },
    )

    return resp


async def get_products(
    brand: Annotated[
        BrandReference | str | None,
        Field(description="Brand reference with domain field, or domain string shorthand (e.g. 'acme.com')"),
    ] = None,
    brief: Annotated[str, Field(description="Natural language description of campaign goals and requirements")] = "",
    filters: ProductFilters | None = None,
    property_list: PropertyListReference | None = None,
    context: ContextObject | None = None,  # payload-level context
    buying_mode: Annotated[
        str | None,
        Field(
            description=(
                "Buyer intent: 'brief' (publisher curates from the brief), 'wholesale' "
                "(buyer requests raw inventory; brief and refine forbidden), or 'refine' "
                "(iterate on a previous response via the refine array). v3 clients MUST "
                "include this; pre-v3 clients are defaulted at the wrapper boundary."
            ),
        ),
    ] = None,
    refine: list[dict[str, Any]] | None = None,
    adcp_version: Annotated[
        str | None,
        Field(
            description=(
                "Client's AdCP version (e.g. '1.0.0', '3.0.0'). Drives the inbound pre-v3 "
                "default shim and the outbound v2-compat transform for pre-v3 buyers."
            ),
        ),
    ] = None,
    ctx: Context | ToolContext | None = None,
):
    """Get available products matching the brief.

    MCP tool wrapper aligned with the AdCP 3.0.1 three-mode contract.

    Args:
        brand: Brand reference per adcp 3.6.0. Example: BrandReference(domain="acme.com")
        brief: Brief (required for buying_mode='brief'; forbidden for 'wholesale'/'refine')
        filters: Structured filters for product discovery (optional)
        property_list: Property list reference for filtering by buyer's property list (optional)
        context: Application level context per adcp spec
        buying_mode: Buyer intent — 'brief' / 'wholesale' / 'refine' (v3 clients MUST include it)
        refine: Change requests for buying_mode='refine'
        adcp_version: Client's AdCP version (drives the pre-v3 shim + v2-compat)
        ctx: FastMCP context (automatically provided)

    Returns:
        ToolResult with human-readable text and structured data
    """
    # Coerce string brand shorthand to BrandReference (AdCP v3 allows "acme.com")
    if isinstance(brand, str):
        brand = BrandReference(domain=brand)

    # The helper owns the pre-v3 shim + the ValidationError -> AdCPInvalidRequestError
    # translation, so all three transports (MCP, A2A, REST) observe identical behavior.
    req, pre_v3_defaulted = create_get_products_request(
        brief=brief,
        brand=brand,
        filters=filters,
        property_list=property_list,
        context=context,
        buying_mode=buying_mode,
        refine=refine,
        adcp_version=adcp_version,
    )

    # Read identity pre-resolved by MCPAuthMiddleware
    identity = (await ctx.get_state("identity")) if isinstance(ctx, Context) else None

    # Call shared implementation
    response = await _get_products_impl(req, identity, pre_v3_defaulted=pre_v3_defaulted)

    # Outbound v2-compat enrichment for pre-v3 buyers. Pre-v3 clients reach the seller
    # over MCP, so the transform runs here on the response model; A2A/REST serve v3+
    # agents (their boundaries pass already-serialized dicts, a no-op for v2-compat).
    structured = apply_version_compat("get_products", response, adcp_version)
    return ToolResult(content=str(response), structured_content=structured)


async def get_products_raw(
    brief: str = "",
    brand: BrandReference | str | None = None,
    filters: ProductFilters | None = None,
    property_list: PropertyListReference | None = None,
    context: ContextObject | None = None,  # Application level context per adcp spec
    buying_mode: str | None = None,
    refine: list[dict[str, Any]] | None = None,
    adcp_version: str | None = None,
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
        buying_mode: Buyer intent — 'brief' / 'wholesale' / 'refine' (v3 clients MUST include it)
        refine: Change requests for buying_mode='refine'
        adcp_version: Client's AdCP version (drives the pre-v3 default shim)
        ctx: FastMCP context (automatically provided)
        identity: Resolved identity from transport boundary (preferred over ctx)

    Returns:
        GetProductsResponse containing matching products
    """
    # Resolve identity from transport context if not provided
    if identity is None:
        identity = resolve_identity_from_context(ctx, require_valid_token=False)

    # The helper owns the pre-v3 shim + ValidationError translation (transport parity).
    req, pre_v3_defaulted = create_get_products_request(
        brief=brief or "",
        brand=brand,
        filters=filters,
        property_list=property_list,
        context=context,
        buying_mode=buying_mode,
        refine=refine,
        adcp_version=adcp_version,
    )

    # Call shared implementation
    return await _get_products_impl(req, identity, pre_v3_defaulted=pre_v3_defaulted)


def get_product_catalog(tenant_id: str | None = None) -> list[Product]:
    """Get products for a tenant.

    Args:
        tenant_id: Tenant ID to load products for. Falls back to ContextVar if not provided.

    Returns:
        List of Product objects with full pricing options
    """

    if tenant_id is None:
        from src.core.config_loader import get_current_tenant

        tenant_id = get_current_tenant()["tenant_id"]

    from src.core.database.repositories.uow import ProductUoW

    with ProductUoW(tenant_id) as uow:
        assert uow.products is not None
        products = uow.products.list_all_with_inventory()

        # Use convert_product_model_to_schema for consistency
        loaded_products = []
        for product in products:
            loaded_products.append(convert_product_model_to_schema(product))

    return loaded_products
