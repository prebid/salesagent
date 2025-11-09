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
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import attributes

from src.core.database.database_session import get_db_session
from src.core.database.models import Product
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

    with get_db_session() as session:
        # Get all dynamic product templates for this tenant
        stmt = select(Product).filter_by(tenant_id=tenant_id, is_dynamic=True, archived_at=None)
        templates = session.scalars(stmt).all()

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

            # Build context with deployment specification per AdCP spec
            context = {}
            if our_agent_url:
                deliver_to: dict[str, list] = {
                    "destinations": [{"agent_url": our_agent_url}],
                }
                # Add countries if we have any from products
                if all_countries:
                    deliver_to["countries"] = sorted(all_countries)  # Sort for consistency
                context["deliver_to"] = deliver_to

            # Call async registry function
            import time

            query_start = time.time()

            countries_info = f" for countries {sorted(all_countries)}" if all_countries else ""
            logger.info(f"[TIMING] Querying signals agents with brief: {brief[:100]}...{countries_info}")

            all_signals = await registry.get_signals(
                brief=brief,  # Note: Registry uses 'brief', not 'signal_spec' (TODO: update to match AdCP spec)
                tenant_id=tenant_id,
                context=context,
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
                    session, template, template_signals, brief, our_agent_url
                )
                variants.extend(template_variants)

            except Exception as e:
                logger.error(f"Error generating variants for template {template.product_id}: {e}", exc_info=True)
                continue

        session.commit()

    return variants


def generate_variants_from_signals(
    session, template: Product, signals: list[dict], brief: str, our_agent_url: str | None = None
) -> list[Product]:
    """Generate product variants from template and signals.

    Args:
        session: Database session
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

            # Check if variant already exists
            stmt = select(Product).filter_by(tenant_id=template.tenant_id, product_id=variant_id)
            existing = session.scalars(stmt).first()

            if existing:
                # Update existing variant
                existing.last_synced_at = datetime.now()  # type: ignore[assignment]
                # Extend expiration if still active
                ttl_days = template.variant_ttl_days or 30
                new_expiration = datetime.now() + timedelta(days=ttl_days)
                if not existing.expires_at or existing.expires_at < new_expiration:
                    existing.expires_at = new_expiration  # type: ignore[assignment]
                variants.append(existing)
                logger.debug(f"Updated existing variant {variant_id}")
                continue

            # Create new variant from template
            variant = create_variant_from_template(template, signal, activation_key, variant_id, brief)

            session.add(variant)
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
    # Copy ALL fields from template
    variant_data = {
        "tenant_id": template.tenant_id,
        "product_id": variant_id,
        "name": customize_name(template.name, signal, activation_key, template.variant_name_template),
        "description": customize_description(
            template.description, signal, activation_key, brief, template.variant_description_template
        ),
        "formats": template.formats,
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
        "last_synced_at": datetime.now(),
        # Expiration
        "expires_at": datetime.now() + timedelta(days=template.variant_ttl_days or 30),
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
            Product.is_dynamic_variant, Product.archived_at.is_(None), Product.expires_at < datetime.now()
        )

        if tenant_id:
            stmt = stmt.filter_by(tenant_id=tenant_id)

        expired_variants = session.scalars(stmt).all()

        for variant in expired_variants:
            variant.archived_at = datetime.now()  # type: ignore[assignment]
            attributes.flag_modified(variant, "archived_at")
            archived_count += 1
            logger.info(f"Archived expired variant {variant.product_id}")

        session.commit()

    return archived_count
