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
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from functools import partial

from sqlalchemy import inspect, select
from sqlalchemy.orm import attributes

from src.core.database.database_session import get_db_session
from src.core.database.models import Product
from src.core.database.repositories.product import ProductRepository
from src.core.signals_agent_registry import get_signals_agent_registry

logger = logging.getLogger(__name__)


async def generate_variants_for_brief(tenant_id: str, brief: str, our_agent_url: str | None = None) -> None:
    """Persist variants before the caller reads and converts the complete catalogue."""
    from src.core.database.repositories.uow import ProductUoW

    try:
        with ProductUoW(tenant_id) as uow:
            assert uow.products is not None
            template_ids = {
                template.product_id
                for template in uow.products.list_dynamic_templates()
                if template.signals_agent_ids is None or template.signals_agent_ids
            }
        if not template_ids:
            return

        # No database transaction spans the external MCP request.
        signals = await get_signals_agent_registry().get_signals(brief=brief, tenant_id=tenant_id)

        with ProductUoW(tenant_id) as uow:
            assert uow.products is not None
            for template in uow.products.list_dynamic_templates():
                if template.product_id not in template_ids or template.signals_agent_ids == []:
                    continue
                generate_variants_from_signals(
                    uow.products, template, signals[: template.max_signals], brief, our_agent_url
                )
    except Exception:
        logger.exception("Dynamic generation failed for tenant %s; serving the persisted catalogue", tenant_id)


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

            variant = products.get_or_create_variant(
                template,
                variant_id,
                partial(create_variant_from_template, template, signal, activation_key, variant_id, brief),
            )
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


_VARIANT_OWNED_COLUMNS = frozenset(
    {
        "tenant_id",
        "product_id",
        "name",
        "description",
        "format_ids",
        "format_options",
        "allowed_principal_ids",
        "is_custom",
        "is_dynamic",
        "is_dynamic_variant",
        "parent_product_id",
        "activation_key",
        "signal_metadata",
        "last_synced_at",
        "expires_at",
        "archived_at",
        "created_at",
        "updated_at",
        "signals_agent_ids",
        "max_signals",
        "variant_ttl_days",
        "variant_name_template",
        "variant_description_template",
    }
)


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
    # Inherit mapped data by default. Variant identity, lifecycle and access have explicit owners.
    contract = template.format_contract
    variant_data = {
        column.key: deepcopy(getattr(template, column.key))
        for column in inspect(Product).column_attrs
        if column.key not in _VARIANT_OWNED_COLUMNS
    }
    variant_data.update(
        tenant_id=template.tenant_id,
        product_id=variant_id,
        name=customize_name(template.name, signal, activation_key, template.variant_name_template),
        description=customize_description(
            template.description, signal, activation_key, brief, template.variant_description_template
        ),
        format_ids=None if contract.format_options else deepcopy(list(contract.format_ids) or None),
        format_options=deepcopy(list(contract.format_options)) if contract.format_options else None,
        allowed_principal_ids=None,
        is_custom=False,
        is_dynamic=False,
        is_dynamic_variant=True,
        parent_product_id=template.product_id,
        activation_key=deepcopy(activation_key),
        signal_metadata={
            key: deepcopy(signal.get(key))
            for key in ("signal_agent_segment_id", "name", "description", "data_provider", "coverage_percentage")
        },
        last_synced_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(days=template.variant_ttl_days or 30),
    )

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
