"""Get products tool implementation.

This module contains the get_products tool implementation following the MCP/A2A
shared implementation pattern from CLAUDE.md.
"""

import logging
import time
from collections.abc import Iterator
from typing import Any, cast

from adcp.types import PropertyListReference
from adcp.types.legacy import LegacyProduct as LibraryProduct

# The FIXME(#1388) import of FormatReferenceStructuredObject that stood here is gone with
# its only use: standard_formats_only's isinstance(str | dict | FormatId) chain, which was
# guarding against shapes a converted product cannot hold. One fewer library type imported
# past its local subclass, which is what that FIXME asked for.
from src.adapters import get_adapter_default_channels
from src.core.audit_logger import get_audit_logger
from src.core.errors.codes import ErrorCode
from src.core.errors.details import ErrorDetails, PolicyViolationDetails
from src.core.exceptions import (
    AdCPAuthorizationError,
    AdCPInternalError,
    AdCPPolicyViolationError,
    AdCPSalesAgentError,
    AdCPValidationError,
)
from src.core.format_resolver import (
    bundled_extension_definitions,
    declaration_platform_extensions,
    format_declaration_divergence,
    platform_extension_collisions,
    product_format_kinds,
    product_format_option_identities,
    seller_format_catalogue,
)
from src.core.format_satisfaction import accepts_legacy_reference
from src.core.helpers import enum_value
from src.core.helpers.channel_helpers import effective_channel_names
from src.core.resolved_identity import PublicIdentity
from src.core.schemas import (
    Error,
    FormatOptionIdentity,
    GetProductsRequest,  # OURS, extending the SDK's — the accepted shape
    GetProductsResponse,
    LibraryErrorSource,
    Product,  # Extends library Product
)
from src.core.validation_helpers import safe_parse_json_field
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
        # The element IS the member: Product.pricing_options holds the arms, not the
        # RootModel wrapper the pinned SDK deleted.
        inner = option
        price_guidance = getattr(inner, "price_guidance", None)
        if inner.pricing_model.upper() == "CPM" and price_guidance:
            p75 = price_guidance.p75
            if p75 is not None:
                return float(p75)
    return None


# Import conversion utilities from dedicated module to avoid circular imports
from src.core.product_conversion import convert_product_model_to_schema


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
        # No ``.root``: the pinned SDK ships PublisherPropertySelector as the union of its three
        # arms rather than a RootModel over them, so the selector IS the arm.
        if selector.selection_type == "all":
            # Product covers ALL properties for this domain
            return None
        elif selector.selection_type == "by_id":
            for pid in selector.property_ids:
                # ``PropertyId`` is a ``ScalarStr`` -- a plain ``str`` subclass -- from
                # the pinned SDK on, and it keeps ``.root`` as a compatibility accessor returning
                # the same string. Read it as the string it is.
                property_ids.add(str(pid))
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


def _products_message(count: int, *, anonymous: bool) -> str:
    """The envelope's human-readable summary of this catalogue answer.

    ``message`` is a protocol-envelope field the pinned schema describes as generated by the
    protocol layer, so the implementation that knows what happened fills it. It is not a
    ``__str__`` on the response model: that would leave each transport deciding for itself
    whether to call it, and the sentence would land in a different place on each one.

    The anonymous suffix is passed in rather than re-derived. The implementation knows the
    caller state for certain, so it is never inferred by scanning the products' pricing
    options for an absent rate.
    """
    if count == 0:
        return "No products matched your requirements."
    base = (
        "Found 1 product that matches your requirements."
        if count == 1
        else f"Found {count} products that match your requirements."
    )
    if anonymous:
        return f"{base} Please connect through an authorized buying agent for pricing data."
    return base


def _format_declaration_findings(products: list[Product]) -> Iterator[tuple[int, ErrorDetails]]:
    """Each product's ``FORMAT_DECLARATION_DIVERGENT`` details, keyed by its response index.

    A product's v1_format_ref divergence comes first, then each platform-extension collision
    on its declarations, so every advisory for the code is built in one place.
    """
    for index, product in enumerate(products):
        divergence = format_declaration_divergence(product.product_id, product.format_options)
        if divergence is not None:
            yield index, divergence
    for index, product in enumerate(products):
        for declaration in product.format_options or ():
            for collision in platform_extension_collisions(
                declaration_platform_extensions(declaration), bundled_extension_definitions()
            ):
                yield index, collision


async def _get_products_impl(req: GetProductsRequest, identity: PublicIdentity) -> GetProductsResponse:
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
        raise AdCPValidationError()

    principal_id: str | None = identity.principal_id
    # Always a tenant: a request naming no seller is refused CONFIGURATION_ERROR by the
    # resolver, so there is no empty-catalog answer for one.
    tenant = identity.tenant

    # Extract offering text from brand (the pinned SDK: brand replaces brand_manifest).
    # req.brand is BrandReference | None (Pydantic model with .domain attribute).
    offering = None
    if req.brand:
        domain = getattr(req.brand, "domain", None)
        if domain:
            offering = f"Brand at {domain}"

    # Check brand_manifest_policy from tenant settings
    brand_manifest_policy = tenant.brand_manifest_policy

    # Enforce policy-based validation
    if brand_manifest_policy == "require_brand" and not offering:
        # Message is NOT authored at the raise site: AdCPSalesAgentError.__init__
        # is keyword-only and has no ``message`` parameter — buyer-facing text
        # comes from CODE_TABLE[PERMISSION_DENIED].
        raise AdCPAuthorizationError()
    # "require_auth" is enforced by the RESOLVER, not here: ToolSpec.requires_credential(tenant)
    # answers True for this tool on such a seller, so the boundary refused an anonymous caller
    # (AUTH_MISSING, BR-UC-001 INV-1) and ``identity`` is a ResolvedIdentity by the time this
    # runs. "public" allows all requests (no brand_manifest or auth required).

    # For non-public policies, we need offering for policy checks and product matching
    # Use a generic offering if not provided
    if not offering:
        offering = "Generic product inquiry"

    # Note: brand_manifest validation is handled by Pydantic schema, no need for runtime validation here

    # Check policy compliance first (if enabled)
    advertising_policy = safe_parse_json_field(tenant.advertising_policy, field_name="advertising_policy", default={})

    # Only run policy checks if enabled in tenant settings
    policy_check_enabled = advertising_policy.get("enabled", False)  # Default to False for new tenants

    # Extract brief text early - needed for policy checks, dynamic variants, and AI ranking
    brief_text = req.brief if req.brief else ""

    if not policy_check_enabled:
        # Skip policy checks if disabled
        policy_result = None
        logger.info(f"Policy checks disabled for tenant {tenant.tenant_id}")
    else:
        # Get tenant's Gemini API key for policy checks
        tenant_gemini_key = tenant.gemini_api_key
        if not tenant_gemini_key:
            # No API key - cannot run policy checks
            policy_result = None
            logger.warning(f"Policy checks enabled but no Gemini API key configured for tenant {tenant.tenant_id}")
        else:
            policy_service = PolicyCheckService(gemini_api_key=tenant_gemini_key)

            # Use advertising_policy settings for tenant-specific rules
            tenant_policies = advertising_policy if advertising_policy else {}

            try:
                policy_result = await policy_service.check_brief_compliance(
                    brief=brief_text,
                    promoted_offering=offering,  # Use extracted offering from brand
                    brand_manifest=None,  # the pinned SDK: brand_manifest replaced by brand; policy service still accepts None
                    tenant_policies=tenant_policies if tenant_policies else None,
                )

                # Log successful policy check
                audit_logger = get_audit_logger("AdCP", tenant.tenant_id)
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
                # structural-guard: fail-open is conformant at AdCP.
                # `errors` is required on get-products-response.json only in its
                # `if status == "failed"` branch; this path completes and returns
                # products, so no emission condition applies. There is also no
                # `incomplete[]` scope for policy screening — the product set is not
                # partial, it is unscreened — and the event IS durably recorded by the
                # policy_check_failure audit entry written just below.
                # No reason for the screening gap reaches the buyer. The states that
                # produce one are seller-side CONFIGURATION (policy disabled by tenant, no
                # Gemini key), and disclosing them would leak the seller's operational
                # posture to the buyer for no spec reason.
                logger.error(f"Policy check failed for tenant {tenant.tenant_id}: {e}")
                audit_logger = get_audit_logger("AdCP", tenant.tenant_id)
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

                # Fail open by default: allow the campaign, record the reason in the
                # audit log above. Nothing is added to the response — see the
                # structural-guard note on this handler for why that is conformant.
                policy_result = None
                logger.warning(f"Policy check failed, allowing campaign by default: {e}")

    # Handle policy result based on settings
    if policy_result and policy_result.status == PolicyStatus.BLOCKED:
        # Always block if policy says blocked
        logger.warning(f"Brief blocked by policy: {policy_result.reason}")
        # Raise ToolError to properly signal failure to client
        raise AdCPPolicyViolationError()

    # If restricted and manual review is required, create a task
    if (
        policy_result
        and policy_result.status == PolicyStatus.RESTRICTED
        and advertising_policy.get("require_manual_review", False)
    ):
        # Log policy violation for audit trail and compliance
        audit_logger = get_audit_logger("AdCP", tenant.tenant_id)
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
        raise AdCPPolicyViolationError(
            # `violated_rules` is policy-violation.json's own name for what this
            # called `restrictions` -- same fact, so the pinned spelling wins.
            details=PolicyViolationDetails(violated_rules=policy_result.restrictions or [])
        )

    # Resolve adapter type for delivery_measurement defaults
    tenant_adapter_type = tenant.ad_server

    # Query products via repository (tenant-scoped)
    from src.core.database.repositories.uow import ProductUoW

    with ProductUoW(tenant.tenant_id) as uow:
        assert uow.products is not None
        db_products = uow.products.list_all()

        # Convert database Product models to AdCP Product schema
        products = []
        for product_obj in db_products:
            try:
                validated_product = convert_product_model_to_schema(product_obj, adapter_type=tenant_adapter_type)
                products.append(validated_product)
                logger.debug(f"Successfully converted product {product_obj.product_id}")
            except AdCPSalesAgentError:
                raise
            except Exception as e:
                error_msg = (
                    f"Product '{product_obj.product_id}' failed to convert to AdCP schema. "
                    f"This indicates data corruption or migration issue. Error: {e}"
                )
                logger.error(error_msg)
                raise AdCPInternalError() from e

    logger.info(f"[GET_PRODUCTS] Got {len(products)} products from database for tenant {tenant.tenant_id}")

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
        # transient, 500). A handler here could only relabel an implementation bug
        # as AdCPValidationError, which tells the buyer "your request is malformed"
        # for our crash — and to carry recovery="transient" it would have to
        # contradict the pinned enum's "correctable" for that code, telling the
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

        # A signals agent records its deployments against a ``destination.agent_url``, which
        # declares "format": "uri" (core/destination.json), and
        # ``extract_activation_key`` picks ours by comparing that string to this one. So it
        # is the URL the agent card publishes — the one accessor, so the two cannot name
        # different agents — never the bare host, which carries no scheme and may carry a
        # port and therefore matches no URI at all.
        #
        # Ungraded through this tool today: the conversion further down raises
        # DetachedInstanceError on the rows the generator hands back, so no variant reaches a
        # response at all (#2291).
        our_agent_url = tenant.agent_url

        dynamic_variants = await generate_variants_for_brief(tenant.tenant_id, brief_text, our_agent_url)
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
        # structural-guard: fail-open is conformant at AdCP.
        # get-products-response.json requires `errors` ONLY in its `if status ==
        # "failed"` branch; on a completed response the field carries no emission
        # condition. The nearest grading obligation is
        # dist/compliance/<pin>/universal/stale-response-advisory.yaml (required_tools:
        # [get_products]), which puts STALE_RESPONSE in errors[] on a populated success
        # — but it requires details.served_from_cache + cache_age_seconds and scopes
        # itself to serving CACHE past its freshness target, which this path does not
        # do (nothing cached is served; enrichment is simply absent).
        # If this is ever surfaced, the correct field is `incomplete[]` with
        # scope="products" ("not all inventory sources were searched"), NOT errors[].
        # That is a product decision needing its own grounding, not a defect fix.
        logger.warning(f"Failed to generate dynamic product variants: {e}. Continuing with static products only.")

    logger.info(f"[GET_PRODUCTS] Total products (static + dynamic): {len(products)}")

    # Enrich products with dynamic pricing from cached performance metrics
    # Updates pricing_options with price_guidance (floor, recommended) and estimated_exposures
    try:
        from src.services.dynamic_pricing_service import DynamicPricingService

        # Extract country from request if available (future enhancement: parse from targeting)
        country_code = None  # TODO: Extract from targeting if provided

        with ProductUoW(tenant.tenant_id) as pricing_uow:
            # FIXME(#1119): DynamicPricingService needs a repository, not raw session
            assert pricing_uow.session is not None
            pricing_service = DynamicPricingService(pricing_uow.session)
            products = pricing_service.enrich_products_with_pricing(
                products,
                tenant_id=tenant.tenant_id,
                country_code=country_code,
                min_exposures=getattr(req.filters, "min_exposures", None) if req.filters else None,
            )
    except (ImportError, RuntimeError, OSError) as e:
        # structural-guard: fail-open is conformant at —
        # same reasoning as the dynamic-variants handler above (errors[] is required
        # only on status=="failed"; stale-response-advisory.yaml does not reach a path
        # that serves no cache). If surfaced later, the correct field is `incomplete[]`
        # with scope="pricing" ("products returned but pricing is absent or
        # unconfirmed"), NOT errors[].
        logger.warning(f"Failed to enrich products with dynamic pricing: {e}. Using defaults.")

    # Apply AdCP filters if provided
    if req.filters:
        # The served catalogue a legacy reference is projected against, read once and only
        # when the deprecated format_ids filter asks: the projection's step 2 reads a served
        # format's own canonical annotation (registries/v1-canonical-mapping.json).
        format_catalogue = seller_format_catalogue(tenant.tenant_id) if req.filters.format_ids else []
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
                # ``fixed_price`` is read off the element directly: Product.pricing_options
                # holds the union MEMBERS and all nine declare the field. The comment here
                # used to say the element was a RootModel to unwrap via ``.root``, which was
                # never true of this list.
                if req.filters.is_fixed_price:
                    has_matching_pricing = any(po.fixed_price is not None for po in product.pricing_options)
                else:
                    has_matching_pricing = any(po.fixed_price is None for po in product.pricing_options)
                if not has_matching_pricing:
                    continue

            # Filter by format_ids (format_types removed in the pinned SDK)
            if req.filters.format_ids:
                # The deprecated filter names legacy references, and a legacy reference is
                # NORMALIZED before it is compared: docs/creative/template-format-ids.mdx
                # § Compatibility (v3.2.7), "Normalize them to canonical declarations
                # immediately and do all matching on the canonical shape", and
                # core/product-format-declaration.json, "do not exact-match raw
                # (agent_url, id) pairs once a format_id has been projected". So a product
                # matches when it would accept a package naming the reference
                # (`accepts_legacy_reference`), which is the create path's own answer: a
                # filter naming display_300x250 selects a product declaring
                # display_300x250_image, or declaring only an image fixed at 300x250. The raw
                # pair intersection this replaced answered both with "no match".
                #
                # The agent still takes part in the identity, canonicalized
                # (core/format-id.json makes that a MUST): a reference to ANOTHER agent's
                # format never selects a product declaring the same slug under this one.
                if not any(
                    accepts_legacy_reference(
                        reference, product.format_options, product.format_ids, catalogue=format_catalogue
                    )
                    for reference in req.filters.format_ids
                ):
                    continue

            # Filter by format_kinds -- the CANONICAL half of the question the format_ids
            # filter above asks of the legacy half. core/product-filters.json: "Filter to
            # products accepting any of these canonical format kinds", with items a $ref to
            # canonical-format-kind.json. So membership is OR over the kind, and the
            # comparison is between closed-vocabulary values rather than the human-chosen
            # slugs the legacy filter has to canonicalize first.
            #
            # The field was DECLARED on the request and read by nothing: measured before
            # this, `git grep format_kinds -- src/` hit one docstring. An ignored filter is
            # not a capability gap the way an unsupported operation is -- the root of
            # product-filters.json says filters "decide which products MAY BE RETURNED", so
            # ignoring one returns products the buyer EXCLUDED and the buyer cannot tell.
            if req.filters.format_kinds:
                requested_kinds = {str(kind) for kind in req.filters.format_kinds}
                if not (product_format_kinds(product.format_options) & requested_kinds):
                    continue

            # Filter by format_option_refs -- the same question at the OPTION level, and the
            # finest-grained of the three. core/product-filters.json: "Filter to products
            # accepting any of these EXACT publisher- or product-scoped canonical format
            # options". EXACT, so whole identities are compared and never their parts: the
            # two arms of core/format-option-ref.json make publisher_domain required on one
            # and forbidden on the other, so a publisher-catalog option and a product-local
            # option sharing one format_option_id are DIFFERENT options. Matching on the id
            # alone would answer a reference to a publisher's catalogue with a product-local
            # option -- the same false positive, in the same direction, that the legacy
            # format_ids filter's bare-id comparison used to produce.
            #
            # Also declared on the request and read by nothing before this.
            # FormatOptionIdentity.of_reference is THE reader of a reference's scope, so the
            # request side goes through it rather than reading the two members directly.
            if req.filters.format_option_refs:
                requested_options = {FormatOptionIdentity.of_reference(ref) for ref in req.filters.format_option_refs}
                if not (product_format_option_identities(product.format_options) & requested_options):
                    continue

            # Filter by standard_formats_only: "Only return products accepting IAB standard
            # formats" (core/product-filters.json).
            #
            # RE-KEYED, because the previous reading had a hole that salesagent-8qela.3.2
            # made reachable. It looped `product.format_ids or []` and prefix-guessed
            # "standard" from the legacy slug -- startswith(("display_", "video_", "audio_",
            # "native_")) -- so a product with NO legacy arm ran an EMPTY loop, left
            # `has_only_standard` True, and passed this filter UNCONDITIONALLY whatever its
            # canonical declarations said. Canonical-only products were unrepresentable when
            # that was written, so the hole was latent; .3.2 dropped the NOT NULL and made
            # it a live false positive, in the direction that matters (the buyer is handed a
            # product it asked to exclude).
            #
            # A product must now DECLARE something for this filter to select it, through
            # either arm, and a product declaring nothing is not "standard by default".
            if req.filters.standard_formats_only:
                # ``entry.id`` directly: on a converted product this list is
                # list[FormatReferenceStructuredObject], so every entry is a reference
                # OBJECT. The three-way isinstance(str | dict | FormatId) this replaced was
                # guarding against shapes the model cannot produce.
                legacy_slugs = {entry.id for entry in product.format_ids or []}
                canonical_kinds = product_format_kinds(product.format_options)
                if not legacy_slugs and not canonical_kinds:
                    # Declares no format at all: it accepts no IAB standard format either.
                    continue
                # The legacy half keeps the prefix heuristic, which is all a human-chosen
                # slug supports -- it is explicitly "typically follow patterns like", not a
                # registry lookup. The canonical half needs no heuristic: the sixteen kinds
                # ARE the standard vocabulary, and `custom` is the one member that names a
                # format outside it.
                if any(not slug.startswith(("display_", "video_", "audio_", "native_")) for slug in legacy_slugs):
                    continue
                if any(kind == "custom" for kind in canonical_kinds):
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
                    # Extract country codes from filter. ``Country`` is NOT a RootModel --
                    # at the pinned SDK it is ``ScalarStr``, a ``str`` subclass, so the value IS the
                    # string and ``.upper()`` can be called on it directly. ``.root`` still
                    # answers behind a deprecation shim ("Country.root is deprecated; the
                    # value IS a str"), which is what the read below is still using; the
                    # per-type cleanup for that and its eleven siblings is
                    # https://github.com/prebid/salesagent/issues/2339.
                    request_countries: set[str] = set()
                    for country in req.filters.countries:
                        request_countries.add(country.root.upper())

                    # Check if any requested country is in the product's countries
                    if not product_countries.intersection(request_countries):
                        continue

            # Filter by channels
            if req.filters.channels:
                # The one rule (channel_helpers), shared with the portfolio summary
                # get_adcp_capabilities builds: declared channels if the product has
                # any, the tenant adapter's defaults otherwise.
                product_channels = effective_channel_names(
                    product.channels, adapter_defaults=get_adapter_default_channels(tenant.ad_server)
                )

                # Extract channel values from filter (enum values)
                request_channels: set[str] = set()
                for channel in req.filters.channels:
                    request_channels.add(channel.value.lower())

                # An EMPTY effective set is "no basis to answer", not "no channels", so it
                # does not narrow -- the product stays. A tenant whose adapter reports no
                # channels therefore excludes nothing.
                if product_channels and not product_channels.intersection(request_channels):
                    continue

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
    product_ranking_prompt = tenant.product_ranking_prompt
    if product_ranking_prompt and brief_text and eligible_products:
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
            # structural-guard: no spec surface covers ranking.
            # This degrades ORDER only — and the set returned is a SUPERSET, because
            # the <0.1 relevance filter stops running too, so the buyer loses nothing
            # they were entitled to. The spec defines no ordering guarantee for
            # get_products and no `incomplete[]` scope for ranking.
            logger.warning(f"Failed to apply AI product ranking: {e}. Returning unranked products.")

    # Filter pricing data for anonymous users
    # Do this BEFORE serialization to avoid reconstruction issues
    if principal_id is None:  # Anonymous user
        # Remove pricing data from products for anonymous users
        # Set to empty list to hide pricing (will be excluded during serialization)
        for product in eligible_products:
            product.pricing_options = []

    # FORMAT_DECLARATION_DIVERGENT, on a POPULATED SUCCESS. The pinned enum calls the code a
    # "Non-fatal advisory", says "Hard-failing the entire get_products response is
    # discouraged", and makes errors[] the single surface; `source` is producer because the
    # SELLER self-detects on emit, and `field` is built from the RESPONSE order so it names
    # the product the buyer sees.
    #
    # Two divergences, one advisory each. A declaration asserting a v1_format_ref its params
    # or kind do not narrow (core/product-format-declaration.json: "The v2 declaration's
    # `params` MUST narrow ... each referenced v1 format's `requirements`"): the reference is
    # already left out of the derived format_ids (`CanonicalFormats`), as the producer rule
    # "MUST omit the legacy compatibility projection" requires, and the declaration still
    # names it on the wire, so the advisory says why the two shapes differ.
    #
    # `formats/canonical/_base.json` asks for the same code when "a producer-self-emitted
    # overlap on a single declaration" happens, with
    # `details: { collision_kind, target, overlapping_fields, winning_extension_uri }` --
    # PlatformExtensionCollisionDetails is exactly those four.
    #
    # QUIET TODAY, AND NOT DEAD: the overlap is undecidable from a declaration, because
    # `core/platform-extension-ref.json` carries `uri` + `digest` only while the target and
    # field names are the DEFINITION's. The definitions arrive in the response's bundled
    # `extensions` map, which nothing populates yet -- so every reference resolves to no
    # definition and is left alone rather than guessed at. `bundled_extension_definitions` is
    # the one seam the deferred half of salesagent-hff54 fills; feeding it is what makes this
    # emit, and nothing else about this call site changes then.
    format_declaration_advisories = [
        Error.of(  # structural-guard: advisory per-product divergence in GetProductsResponse.errors[]
            ErrorCode.FORMAT_DECLARATION_DIVERGENT,
            field=f"products[{index}]",
            details=details,
            source=LibraryErrorSource.producer,
        )
        for index, details in _format_declaration_findings(eligible_products)
    ]

    # Our Product extends LibraryProduct - cast for type safety since list is invariant
    # When serialized, Pydantic automatically uses library Product fields
    # Internal-only fields (implementation_config) excluded by model_dump()
    # Note: We use eligible_products (Product objects), not response_data (dicts)
    # because Product objects have typed pricing_options (CpmFixedRatePricingOption, etc.)
    # while dicts lose this type information during serialization
    # the pinned SDK+ accepts subclass lists at runtime via BeforeValidator coercion,
    # but mypy still needs cast() due to list invariance in static typing
    resp = GetProductsResponse(
        products=cast(list[LibraryProduct], eligible_products),
        errors=format_declaration_advisories or None,
        message=_products_message(len(eligible_products), anonymous=principal_id is None),
    )

    # Log successful get_products call
    elapsed_ms = int((time.time() - start_time) * 1000)
    audit_logger = get_audit_logger("AdCP", tenant.tenant_id)
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


def get_product_catalog(tenant_id: str) -> list[Product]:
    """Get products for a tenant.

    Returns:
        List of Product objects with full pricing options
    """
    from src.core.database.repositories.uow import ProductUoW

    with ProductUoW(tenant_id) as uow:
        assert uow.products is not None
        products = uow.products.list_all_with_inventory()

        # Use convert_product_model_to_schema for consistency
        loaded_products = []
        for product in products:
            loaded_products.append(convert_product_model_to_schema(product))

    return loaded_products
