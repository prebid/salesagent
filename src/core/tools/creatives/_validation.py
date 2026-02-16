"""Creative input validation: schema and business rule checks."""

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from adcp.types.generated_poc.core.creative_asset import CreativeAsset

from src.core.schemas import Creative, CreativeStatusEnum
from src.core.validation_helpers import run_async_in_sync_context

logger = logging.getLogger(__name__)


def _get_field(obj: Any, field: str, default: Any = None) -> Any:
    """Get a field from a model or dict (transitional helper for Phase 1a).

    Removed in Phase 1b when all callers pass typed models.
    """
    if isinstance(obj, dict):
        return obj.get(field, default)
    return getattr(obj, field, default)


def _validate_creative_input(
    creative: CreativeAsset,
    registry: Any,
    principal_id: str,
) -> Creative:
    """Validate a CreativeAsset and return a validated Creative model.

    Builds schema_data from the creative model, validates via Creative(**schema_data),
    checks business logic (empty name, missing format), and validates the format_id
    against the creative agent registry.

    Args:
        creative: CreativeAsset model from the sync payload.
        registry: CreativeAgentRegistry instance for format validation.
        principal_id: Authenticated principal ID for ownership.

    Returns:
        Validated Creative schema object.

    Raises:
        ValidationError: If the creative fails Pydantic schema validation.
        ValueError: If business logic checks fail (empty name, missing format,
            unknown format, unreachable agent).
    """
    # Create temporary schema object for validation (AdCP v1 spec compliant)
    # Only include AdCP spec fields + internal fields
    schema_data: dict[str, Any] = {
        "creative_id": creative.creative_id or str(uuid.uuid4()),
        "name": creative.name or "",  # Ensure name is never None
        "format_id": creative.format_id,
        "assets": creative.assets or {},  # Required by AdCP v1 spec
        # Internal fields (added by sales agent)
        "principal_id": principal_id,
        "created_date": datetime.now(UTC),
        "updated_date": datetime.now(UTC),
        "status": CreativeStatusEnum.pending_review.value,
    }

    # Add optional AdCP v1 fields if provided
    if creative.inputs:
        schema_data["inputs"] = creative.inputs
    if creative.tags:
        schema_data["tags"] = creative.tags
    approved = getattr(creative, "approved", None)
    if approved is not None:
        schema_data["approved"] = approved

    # Validate by creating a Creative schema object
    # This will fail if required fields are missing or invalid (like empty name)
    # Also auto-upgrades string format_ids to FormatId objects via validator
    validated_creative = Creative(**schema_data)

    # Additional business logic validation
    if not creative.name or str(creative.name).strip() == "":
        raise ValueError("Creative name cannot be empty")

    if not creative.format_id:
        raise ValueError("Creative format is required")

    # Use validated format (auto-upgraded from string if needed)
    format_value = validated_creative.format

    # Validate format exists in creative agent
    # Extract agent_url and format_id from FormatId
    if hasattr(format_value, "agent_url") and hasattr(format_value, "id"):
        agent_url = str(format_value.agent_url)
        format_id = format_value.id

        # Skip external validation for adapter-provided formats (non-HTTP URLs)
        # These formats are served by the adapter itself (e.g., broadstreet://default)
        # and validation is handled internally by the adapter
        is_adapter_format = not agent_url.startswith(("http://", "https://"))

        if not is_adapter_format:
            # Check if format exists (uses in-memory cache with 1-hour TTL)
            # Use run_async_in_sync_context to handle both sync and async contexts
            format_spec = None
            validation_error = None

            try:
                format_spec = run_async_in_sync_context(registry.get_format(agent_url, format_id))
            except Exception as e:
                # Network error, agent unreachable, etc.
                validation_error = e
                logger.warning(
                    f"Failed to fetch format '{format_id}' from agent {agent_url}: {e}",
                    exc_info=True,
                )

            if validation_error:
                # Agent unreachable or network error
                raise ValueError(
                    f"Cannot validate format '{format_id}': Creative agent at {agent_url} "
                    f"is unreachable or returned an error. Please verify the agent URL is correct "
                    f"and the agent is running. Error: {str(validation_error)}"
                )
            elif not format_spec:
                # Format not found (agent is reachable but format doesn't exist)
                raise ValueError(
                    f"Unknown format '{format_id}' from agent {agent_url}. "
                    f"Format must be registered with the creative agent. "
                    f"Use list_creative_formats to see available formats."
                )
            # TODO(#767): Call validate_creative when available in creative agent spec
            # to validate that creative manifest matches format requirements
        else:
            logger.debug(
                f"Skipping external validation for adapter-provided format '{format_id}' (agent_url: {agent_url})"
            )

    return validated_creative
