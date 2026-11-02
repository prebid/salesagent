"""Creative input validation: schema and business rule checks."""

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from adcp.types import CreativeAsset

from src.core.errors.details import EntityRefDetails, ValidationDetails
from src.core.exceptions import (
    AdCPFormatNotFoundError,
    AdCPProvenanceDigitalSourceTypeMissingError,
    AdCPProvenanceDisclosureMissingError,
    AdCPProvenanceEmbeddedMissingError,
    AdCPProvenanceRequiredError,
    AdCPValidationError,
)
from src.core.format_resolver import is_dialled_agent_url
from src.core.schemas import Creative, CreativePolicy, CreativeStatusEnum

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
    index: int = 0,
) -> Creative:
    """Validate a CreativeAsset and return a validated Creative model.

    Builds schema_data from the creative model, validates via Creative(**schema_data),
    checks business logic (empty name, missing format), and validates the format_id
    against the creative agent registry.

    Args:
        creative: CreativeAsset model from the sync payload.
        registry: CreativeAgentRegistry instance for format validation.
        principal_id: Authenticated principal ID for ownership.
        index: This creative's position in the request's ``creatives`` array,
            used to build the JSONPath-lite ``field`` carried on a refusal of the
            buyer-supplied ``agent_url`` — a sync may hold up to 100 creatives and
            the refusal message says nothing, so an unindexed path would leave the
            buyer unable to tell WHICH one to fix.

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
        "name": creative.name,
        "format_id": creative.format_id,
        # Handed through as the model it is. The sync input types the asset map with a
        # different generated ``Assets`` class from the listing model's; the receiving
        # ``Creative.assets`` adopts the sibling instance (see the validator there).
        "assets": creative.assets,
        # Internal fields (added by sales agent)
        "principal_id": principal_id,
        "created_date": datetime.now(UTC),
        "updated_date": datetime.now(UTC),
        "status": CreativeStatusEnum.pending_review.value,
    }

    # Add optional AdCP v1 fields if provided
    # NOTE: creative.inputs is NOT included — Creative model (extra="forbid")
    # doesn't accept it. Processing reads inputs from the original CreativeAsset.
    if creative.tags:
        schema_data["tags"] = creative.tags
    approved = getattr(creative, "approved", None)
    if approved is not None:
        schema_data["approved"] = approved

    # Pass through AI provenance metadata (EU AI Act Article 50), as the model it is.
    provenance = getattr(creative, "provenance", None)
    if provenance is not None:
        schema_data["provenance"] = provenance

    # Validate by creating a Creative schema object
    # This will fail if required fields are missing or invalid (like empty name)
    validated_creative = Creative(**schema_data)

    # Additional business logic validation
    if not creative.name or str(creative.name).strip() == "":
        raise AdCPValidationError(field="name")

    if not creative.format_id:
        raise AdCPValidationError(field="format_id")

    # Use validated format (auto-upgraded from string if needed)
    format_value = validated_creative.format

    if format_value is None:
        raise AdCPValidationError(
            details=ValidationDetails(rejected_value=str(creative.format_id)),
            field="format_id",
        )

    # Validate format exists in creative agent
    agent_url = str(format_value.agent_url)
    format_id = format_value.id

    # Skip external validation for adapter-provided formats (non-HTTP URLs)
    # These formats are served by the adapter itself (e.g., broadstreet://default)
    # and validation is handled internally by the adapter
    is_adapter_format = not is_dialled_agent_url(agent_url)

    if not is_adapter_format:
        # Check if the format exists via the SINGLE shared fetch path
        # (format_resolver.fetch_format_spec): a typed AdCPSalesAgentError from the
        # registry (429 -> RATE_LIMITED, 5xx/timeout -> SERVICE_UNAVAILABLE)
        # PROPAGATES with its own recovery semantics — the old bare-except
        # rewrap into AdCPAdapterError made a rate-limited agent look like a
        # creative problem . None = the agent genuinely
        # doesn't expose the format.
        from src.core.format_resolver import fetch_format_spec
        from src.core.security.outbound_http import CounterpartyUrl

        format_spec = fetch_format_spec(
            agent_url, format_id, provenance=CounterpartyUrl(field=f"creatives[{index}].format_id.agent_url")
        )
        if not format_spec:
            # A format_id no agent serves is a REFERENCE that does not resolve, and the
            # pinned enums/error-code.json routes exactly that to REFERENCE_NOT_FOUND
            # ("Generic fallback for a referenced identifier ... that does not exist ...
            # Use when no resource-specific not-found code applies"); VALIDATION_ERROR is
            # for "invalid field values or business rules beyond schema validation", and
            # a well-formed id that simply is not in the catalog is neither. The typed
            # subclass says WHICH kind of reference failed; the wire code is the same.
            raise AdCPFormatNotFoundError(
                details=EntityRefDetails(format_id=format_id),
                field="format_id",
            )
        # TODO(#767): Call validate_creative when available in creative agent spec
        # to validate that creative manifest matches format requirements
    else:
        logger.debug(f"Skipping external validation for adapter-provided format '{format_id}' (agent_url: {agent_url})")

    return validated_creative


def _assets_carry_provenance(creative: Creative) -> bool:
    """Whether any individual asset declares its own provenance object."""
    for value in (creative.assets or {}).values():
        for asset in value if isinstance(value, list) else [value]:
            inner = getattr(asset, "root", asset)
            if isinstance(inner, dict):
                if inner.get("provenance") is not None:
                    return True
            elif getattr(inner, "provenance", None) is not None:
                return True
    return False


def check_provenance_policy(
    creative: Creative,
    creative_policy: CreativePolicy | dict | None,
) -> None:
    """Refuse a creative that does not meet the product's provenance policy.

    core/creative-policy.json: ``provenance_required`` says provenance must be attached, and
    ``provenance_requirements`` refines it -- "Sellers that publish a requirement here MUST
    enforce it on creative submission: a sync_creatives request that omits a required field
    is rejected with the corresponding PROVENANCE_* error code" -- while being ignored
    unless ``provenance_required`` is true. enums/error-code.json: PROVENANCE_REQUIRED is
    "no provenance object on the manifest, on the creative-asset, or on any individual
    asset"; each *_MISSING code is "provenance is present, just missing this specific
    field", inspected on the resolved provenance. All are correctable per-item failures,
    naming in ``field`` the path inspected.

    This used to append a warning and route the creative to review instead. BR-RULE-094's
    "warning" reading lost to the pin, which defines the codes for exactly this refusal.
    The resolved provenance inspected for the *_MISSING checks is the creative-level
    object; a creative carrying provenance only on individual assets satisfies
    ``provenance_required`` and is not inspected field by field here.
    """
    if creative_policy is None:
        return
    # Read off whichever shape arrived. The repository hands back the persisted document
    # for a stored policy and the model for a typed one, and neither is serialized here:
    # a model dumped to inspect one field is a second representation of the same value
    # (CLAUDE.md pattern 4, serialize-only-at-the-edges).
    if not _get_field(creative_policy, "provenance_required"):
        return

    details = EntityRefDetails(creative_id=creative.creative_id)
    provenance = creative.provenance
    if provenance is None:
        if _assets_carry_provenance(creative):
            return
        raise AdCPProvenanceRequiredError(field="provenance", details=details)

    requirements = _get_field(creative_policy, "provenance_requirements") or {}
    if _get_field(requirements, "require_digital_source_type") and provenance.digital_source_type is None:
        raise AdCPProvenanceDigitalSourceTypeMissingError(field="provenance.digital_source_type", details=details)
    if _get_field(requirements, "require_disclosure_metadata"):
        disclosure = provenance.disclosure
        required = getattr(disclosure, "required", None)
        if required is None or (required is True and not getattr(disclosure, "jurisdictions", None)):
            raise AdCPProvenanceDisclosureMissingError(field="provenance.disclosure", details=details)
    if _get_field(requirements, "require_embedded_provenance") and not provenance.embedded_provenance:
        raise AdCPProvenanceEmbeddedMissingError(field="provenance.embedded_provenance", details=details)
