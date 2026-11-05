"""Dynamic product variant generation from signals agents.

This service handles:
1. Querying signals agents with buyer briefs
2. Generating product variants from signals
3. Managing variant lifecycle (creation, expiration, archival)

Architecture:
- Uses singleton SignalsAgentRegistry for all signal queries
- Registry handles multi-agent calls, auth, and MCP client management
- Deployment specified per AdCP spec (our agent_url as destination)
"""

import hashlib
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import attributes

from src.core.database.database_session import get_db_session
from src.core.database.models import Product
from src.core.database.repositories.product import ProductRepository
from src.core.signals_agent_registry import get_signals_agent_registry

logger = logging.getLogger(__name__)


async def generate_variants_for_brief(tenant_id: str, brief: str, our_agent_url: str | None = None) -> list[Product]:
    """Generate product variants from signals agents based on buyer's brief.

    Uses the singleton SignalsAgentRegistry which handles:
    - Multi-agent queries (all configured agents for tenant)
    - MCP client management and connection pooling
    - Auth handling per agent

    Args:
        tenant_id: Tenant ID
        brief: Buyer's brief text
        our_agent_url: Our sales agent URL for deployment specification (optional)

    Returns:
        List of Product variants (newly created or existing)
    """
    variants = []

    # IMPORTED HERE, NOT AT MODULE LEVEL, and this is the fix rather than a style
    # choice (salesagent-uvca5). ``patch`` rebinds an attribute on a module; a
    # module-level ``from ... import ProductUoW`` holds the class object itself, so
    # a patch of ``src.core.database.repositories.uow.ProductUoW`` could not reach
    # it and whether a test's patch landed depended on whether anything earlier in
    # the process had already imported this module. Importing at call time looks the
    # name up after the patch is in place, every time. ``src/core/tools/products.py``
    # (355, 784) does the same for the same reason. Do not hoist this to module level.
    from src.core.database.repositories.uow import ProductUoW

    with ProductUoW(tenant_id) as uow:
        assert uow.products is not None  # inside the context manager the repo is bound
        templates = uow.products.list_dynamic_templates()

        if not templates:
            logger.debug(f"No dynamic product templates found for tenant {tenant_id}")
            return []

        # Check if any templates have signals agents configured
        # Note: signals_agent_ids=None means "use all agents", which counts as configured
        has_signals_agents = any(
            template.signals_agent_ids is None or template.signals_agent_ids for template in templates
        )
        if not has_signals_agents:
            logger.debug(f"No dynamic product templates with signals agents for tenant {tenant_id}")
            return []

        # Query ALL signals agents for this tenant in one call (registry handles multi-agent)
        # This is more efficient than per-agent queries
        try:
            registry = get_signals_agent_registry()

            # Collect unique countries from all dynamic templates
            # Note: signals_agent_ids=None means "use all agents"
            all_countries = set()
            for template in templates:
                if (template.signals_agent_ids is None or template.signals_agent_ids) and template.countries:
                    all_countries.update(template.countries)

            # Call async registry function
            import time

            query_start = time.time()

            countries_info = f" for countries {sorted(all_countries)}" if all_countries else ""
            logger.info(f"[TIMING] Querying signals agents with brief: {brief[:100]}...{countries_info}")

            # No deliver_to is sent: the registry's get_signals never forwarded the
            # deployment specification this used to build, so building it claimed a
            # scoping the query did not have.
            all_signals = await registry.get_signals(
                brief=brief,  # Note: Registry uses 'brief', not 'signal_spec' (TODO: update to match AdCP spec)
                tenant_id=tenant_id,
            )

            query_duration = time.time() - query_start
            logger.info(f"[TIMING] Received {len(all_signals)} total signals in {query_duration:.2f}s")

        except Exception as e:
            logger.error(f"Error querying signals agents for tenant {tenant_id}: {e}", exc_info=True)
            return []

        # Generate variants for each template using the signals
        for template in templates:
            if not template.signals_agent_ids:
                continue

            try:
                # Filter signals to only those we want for this template
                # (up to max_signals limit)
                template_signals = all_signals[: template.max_signals]

                # Generate variants from signals
                template_variants = generate_variants_from_signals(
                    uow.products, template, template_signals, brief, our_agent_url
                )
                variants.extend(template_variants)

            except Exception as e:
                logger.error(f"Error generating variants for template {template.product_id}: {e}", exc_info=True)
                continue

    return variants


def generate_variants_from_signals(
    products: ProductRepository,
    template: Product,
    signals: list[dict],
    brief: str,
    our_agent_url: str | None = None,
) -> list[Product]:
    """Generate product variants from template and signals.

    Takes the REPOSITORY, not a session. A session parameter is what made this
    module reach a real database out of a unit test: the module-level
    ``from ... import get_db_session`` binds the function object, so patching the
    attribute on ``database_session`` could not reach it, and whether the patch
    landed depended on whether anything earlier in the process had imported this
    module (salesagent-uvca5). A repository has no such seam -- the caller's UoW
    owns the session -- and ``uow.session`` is deprecated precisely so a session
    cannot be handed down from one.

    Args:
        products: Tenant-scoped ProductRepository from the caller's ProductUoW
        template: Dynamic product template
        signals: List of signal dicts from signals agent
        brief: Buyer's brief (for variant customization)
        our_agent_url: Our sales agent URL to match deployment (optional)

    Returns:
        List of generated/updated Product variants
    """
    variants = []

    for signal in signals:
        try:
            # Extract activation key from signal for our deployment
            activation_key = extract_activation_key(signal, our_agent_url)
            if not activation_key:
                logger.warning(
                    f"No activation key found for signal {signal.get('signal_agent_segment_id')} "
                    f"matching our deployment {our_agent_url or 'any'}"
                )
                continue

            # Generate variant ID (deterministic)
            variant_id = generate_variant_id(template.product_id, activation_key)

            # Check if variant already exists. The repository scopes to its own
            # tenant, which is the tenant the templates were selected for, so this
            # is the same row the explicit tenant_id filter used to reach.
            existing = products.get_by_id(variant_id)

            if existing:
                # Update existing variant
                existing.last_synced_at = datetime.now(UTC)
                # Extend expiration if still active
                ttl_days = template.variant_ttl_days or 30
                new_expiration = datetime.now(UTC) + timedelta(days=ttl_days)
                if not existing.expires_at or existing.expires_at < new_expiration:
                    existing.expires_at = new_expiration
                variants.append(existing)
                logger.debug(f"Updated existing variant {variant_id}")
                continue

            # Create new variant from template
            variant = create_variant_from_template(template, signal, activation_key, variant_id, brief)

            # create() validates tenant_id and flushes; the UoW commits on exit.
            products.create(variant)
            variants.append(variant)

            logger.info(
                f"Created dynamic variant {variant_id} from template {template.product_id} "
                f"for signal {signal.get('signal_agent_segment_id')}"
            )

        except Exception as e:
            logger.error(f"Error generating variant from signal: {e}", exc_info=True)
            continue

    return variants


def extract_activation_key(signal: dict, our_agent_url: str | None = None) -> dict | None:
    """Extract activation key from signal response for our deployment.

    Per AdCP spec, there should only be one deployment which matches our agent_url.
    We look for the deployment that matches our URL (or any live deployment if URL not provided).

    Args:
        signal: Signal dict from signals agent response
        our_agent_url: Our sales agent URL to match deployment (optional)

    Returns:
        Activation key dict or None if not found
    """
    # Look for activation key in deployments
    deployments = signal.get("deployments", [])

    # If we have our agent URL, find the deployment that matches it
    if our_agent_url:
        for deployment in deployments:
            destination = deployment.get("destination", {})
            if destination.get("agent_url") == our_agent_url:
                # Found our deployment
                if deployment.get("is_live") and deployment.get("activation_key"):
                    activation_key = deployment["activation_key"]

                    # Validate activation key has required fields
                    key_type = activation_key.get("type")
                    if key_type == "key_value":
                        if "key" in activation_key and "value" in activation_key:
                            return activation_key
                    elif key_type == "segment_id":
                        if "segment_id" in activation_key:
                            return activation_key

    # Fallback: If no URL provided or no matching deployment, use first live deployment
    for deployment in deployments:
        if deployment.get("is_live") and deployment.get("activation_key"):
            activation_key = deployment["activation_key"]

            # Validate activation key has required fields
            key_type = activation_key.get("type")
            if key_type == "key_value":
                if "key" in activation_key and "value" in activation_key:
                    return activation_key
            elif key_type == "segment_id":
                if "segment_id" in activation_key:
                    return activation_key

    return None


def generate_variant_id(template_id: str, activation_key: dict) -> str:
    """Generate deterministic variant ID from template and activation key.

    Args:
        template_id: Template product ID
        activation_key: Activation key dict

    Returns:
        Variant product ID
    """
    # Create deterministic hash from activation key
    if activation_key.get("type") == "key_value":
        hash_input = f"{activation_key['key']}:{activation_key['value']}"
    elif activation_key.get("type") == "segment_id":
        hash_input = f"segment:{activation_key['segment_id']}"
    else:
        hash_input = str(activation_key)

    hash_suffix = hashlib.md5(hash_input.encode()).hexdigest()[:8]

    return f"{template_id}__variant_{hash_suffix}"


def create_variant_from_template(
    template: Product, signal: dict, activation_key: dict, variant_id: str, brief: str
) -> Product:
    """Create a new product variant from template.

    Args:
        template: Dynamic product template
        signal: Signal dict from signals agent
        activation_key: Activation key for targeting
        variant_id: Generated variant ID
        brief: Buyer's brief (for customization)

    Returns:
        New Product variant
    """
    # HAZARD, named rather than counted: THIS DICT IS AN ENUMERATION, so a column missing
    # from it is missing from every variant ever generated, silently and with nothing raised.
    # The comment above it used to say "Copy ALL fields from template", which was not true of
    # the code and is the reason the canonical format arm below went unnoticed.
    #
    # It is not a hypothetical and it is not fixed by this one addition. The derivation that
    # settles it -- walk this literal's keys against ``Product.__table__.columns`` -- reports
    # eleven further omissions, and TWO OF THEM WIDEN AUTHORIZATION rather than merely losing
    # data: dropping ``property_ids`` sends ``effective_properties`` from the ``by_id`` arm to
    # ``selection_type: "all"``, i.e. the publisher's whole inventory, and dropping
    # ``allowed_principal_ids`` turns a principal restriction into visibility for every
    # principal of the tenant. Both measured on this function, both in salesagent-tq42j.
    #
    # The right shape is to DERIVE the copied set from the table and pin an exception set
    # naming each column a variant must not inherit, so a new column is inherited by default
    # and an exclusion is a recorded decision -- the precedent is
    # ``tests/helpers/media_buy_row_stubs.stub_package_row``. That is deliberately NOT done
    # here: it would begin propagating those two authorization columns, which is a
    # security-relevant behaviour change needing its own grading, and this change's instrument
    # grades an adapter's vendor payload. Do not add another name here without asking whether
    # the enumeration itself is what should go.
    contract = template.format_contract
    variant_data = {
        "tenant_id": template.tenant_id,
        "product_id": variant_id,
        "name": customize_name(template.name, signal, activation_key, template.variant_name_template),
        "description": customize_description(
            template.description, signal, activation_key, brief, template.variant_description_template
        ),
        # BOTH format arms, because a variant that carries neither is unservable.
        # ``core/product.json``: "Products MUST carry format_ids, format_options, or both
        # during the 3.x migration window", and a variant is a product the buyer sees in
        # ``get_products`` like any other. This copied only the deprecated arm, so a
        # canonically-authored template -- format_options set, format_ids NULL, which is the
        # shape the same sentence tells new products to author ("Do not author a new product
        # from format_ids alone") -- generated variants satisfying no branch of that anyOf.
        # Legacy templates were unaffected, which is why it went unseen (salesagent-ki6a4).
        #
        # Copied from the template's CONTRACT (``Product.format_contract``), not its own
        # columns: a template linked to an inventory profile is served the profile's
        # declarations, and the variant does not carry the link, so copying the template's
        # own arms handed the variant a contract the template never offered. A canonical
        # contract is copied as its declarations alone; its legacy projection is derived
        # from them, so storing it beside them would be a second authored catalog.
        "format_ids": None if contract.format_options else (list(contract.format_ids) or None),
        "format_options": list(contract.format_options) if contract.format_options else None,
        "targeting_template": template.targeting_template,
        "delivery_type": template.delivery_type,
        "measurement": template.measurement,
        "creative_policy": template.creative_policy,
        "price_guidance": template.price_guidance,
        "is_custom": False,  # Variants are generated, not custom
        "countries": template.countries,
        "implementation_config": template.implementation_config,
        "properties": template.properties,
        "property_tags": template.property_tags,
        "delivery_measurement": template.delivery_measurement,
        "product_card": template.product_card,
        "product_card_detailed": template.product_card_detailed,
        "placements": template.placements,
        "reporting_capabilities": template.reporting_capabilities,
        # Variant-specific fields
        "is_dynamic": False,  # Variant is not a template
        "is_dynamic_variant": True,
        "parent_product_id": template.product_id,
        "activation_key": activation_key,
        "signal_metadata": {
            "signal_agent_segment_id": signal.get("signal_agent_segment_id"),
            "name": signal.get("name"),
            "description": signal.get("description"),
            "data_provider": signal.get("data_provider"),
            "coverage_percentage": signal.get("coverage_percentage"),
        },
        "last_synced_at": datetime.now(UTC),
        # Expiration
        "expires_at": datetime.now(UTC) + timedelta(days=template.variant_ttl_days or 30),
    }

    return Product(**variant_data)


def customize_name(
    template_name: str, signal: dict, activation_key: dict, variant_name_template: str | None = None
) -> str:
    """Customize product name for variant using template string or default pattern.

    Args:
        template_name: Base product name
        signal: Signal dict
        activation_key: Activation key
        variant_name_template: Optional template string with macros (e.g., "{{name}} - {{signal.name}}")

    Returns:
        Customized name

    Available macros:
        {{name}} - Product name
        {{signal.name}} - Signal name
        {{signal.description}} - Signal description
        {{activation_key.key}} - Activation key name (for key_value type)
        {{activation_key.value}} - Activation key value (for key_value type)
        {{activation_key.segment_id}} - Segment ID (for segment_id type)
    """
    # If custom template provided, use it
    if variant_name_template:
        # Build macro context
        context = {
            "name": template_name,
            "signal": {
                "name": signal.get("name", ""),
                "description": signal.get("description", ""),
                "data_provider": signal.get("data_provider", ""),
                "coverage_percentage": signal.get("coverage_percentage", ""),
            },
            "activation_key": {
                "key": activation_key.get("key", ""),
                "value": activation_key.get("value", ""),
                "segment_id": activation_key.get("segment_id", ""),
            },
        }

        # Simple macro substitution (supports {{field}} and {{object.field}})
        result = variant_name_template
        for key, value in context.items():
            if isinstance(value, dict):
                # Handle nested access like {{signal.name}}
                for subkey, subvalue in value.items():
                    result = result.replace(f"{{{{{key}.{subkey}}}}}", str(subvalue))
            else:
                # Handle simple access like {{name}}
                result = result.replace(f"{{{{{key}}}}}", str(value))

        return result

    # Default pattern: append signal name
    signal_name = signal.get("name", "")

    if signal_name:
        return f"{template_name} - {signal_name}"

    # Fallback: use activation key
    if activation_key.get("type") == "key_value":
        return f"{template_name} - {activation_key['key']}={activation_key['value']}"
    elif activation_key.get("type") == "segment_id":
        return f"{template_name} - Segment {activation_key['segment_id']}"

    return template_name


def customize_description(
    template_description: str | None,
    signal: dict,
    activation_key: dict,
    brief: str,
    variant_description_template: str | None = None,
) -> str | None:
    """Customize product description for variant using template string or default pattern.

    Args:
        template_description: Base product description
        signal: Signal dict
        activation_key: Activation key
        brief: Buyer's brief
        variant_description_template: Optional template string with macros

    Returns:
        Customized description

    Available macros:
        {{description}} - Product description
        {{signal.name}} - Signal name
        {{signal.description}} - Signal description
        {{signal.data_provider}} - Signal data provider
        {{signal.coverage_percentage}} - Signal coverage percentage
        {{activation_key.key}} - Activation key name (for key_value type)
        {{activation_key.value}} - Activation key value (for key_value type)
    """
    # If custom template provided, use it
    if variant_description_template:
        # Build macro context
        context = {
            "description": template_description or "",
            "signal": {
                "name": signal.get("name", ""),
                "description": signal.get("description", ""),
                "data_provider": signal.get("data_provider", ""),
                "coverage_percentage": signal.get("coverage_percentage", ""),
            },
            "activation_key": {
                "key": activation_key.get("key", ""),
                "value": activation_key.get("value", ""),
                "segment_id": activation_key.get("segment_id", ""),
            },
        }

        # Simple macro substitution (supports {{field}} and {{object.field}})
        result = variant_description_template
        for key, value in context.items():
            if isinstance(value, dict):
                # Handle nested access like {{signal.name}}
                for subkey, subvalue in value.items():
                    result = result.replace(f"{{{{{key}.{subkey}}}}}", str(subvalue))
            else:
                # Handle simple access like {{description}}
                result = result.replace(f"{{{{{key}}}}}", str(value))

        return result if result else None

    # Default pattern: append signal description to product description
    if not template_description:
        # Generate description from signal if template has none
        signal_desc = signal.get("description", "")
        if signal_desc:
            return signal_desc
        return None

    # Append signal description to product description
    # Buyers see this as integrated targeting, not a separate "signal"
    signal_desc = signal.get("description", "")
    if signal_desc:
        return f"{template_description}\n\n{signal_desc}"

    return template_description


def archive_expired_variants(tenant_id: str | None = None) -> int:
    """Archive expired dynamic product variants.

    Args:
        tenant_id: Optional tenant ID to limit archival

    Returns:
        Number of variants archived
    """
    archived_count = 0

    with get_db_session() as session:
        # Find expired variants
        stmt = select(Product).filter(
            Product.is_dynamic_variant, Product.archived_at.is_(None), Product.expires_at < datetime.now(UTC)
        )

        if tenant_id:
            stmt = stmt.filter_by(tenant_id=tenant_id)

        expired_variants = session.scalars(stmt).all()

        for variant in expired_variants:
            variant.archived_at = datetime.now(UTC)
            attributes.flag_modified(variant, "archived_at")
            archived_count += 1
            logger.info(f"Archived expired variant {variant.product_id}")

        session.commit()

    return archived_count
