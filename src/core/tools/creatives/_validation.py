"""Creative input validation: schema and business rule checks."""

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from adcp.types import ErrorCode
from adcp.types.canonical_creative import CanonicalFormatKindStr
from adcp.types.domains.core.format_id import FormatReferenceStructuredObject as LibraryFormatId
from adcp.types.legacy import LegacyCreativeAsset as CreativeAsset
from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from src.core.errors.details import CreativeManifestDetails, EntityRefDetails, ErrorProblem, ValidationDetails
from src.core.exceptions import (
    AdCPCreativeValidationFailedError,
    AdCPFormatNotFoundError,
    AdCPInvalidRequestError,
    AdCPProvenanceDigitalSourceTypeMissingError,
    AdCPProvenanceDisclosureMissingError,
    AdCPProvenanceEmbeddedMissingError,
    AdCPProvenanceRequiredError,
    AdCPValidationError,
)
from src.core.format_resolver import is_dialled_agent_url
from src.core.format_satisfaction import creative_refusal, kind_manifest_verdict, named_format_manifest_verdict
from src.core.idempotency_canonical import selected_output_digest
from src.core.schemas import (
    Creative,
    CreativePolicy,
    CreativeStatusEnum,
    canonical_agent_url,
)

#: The pinned canonical-kind vocabulary as a validator. Built once: a TypeAdapter compiles
#: its core schema on construction, and this is the only vocabulary check on the sync path.
_CANONICAL_KIND: TypeAdapter[CanonicalFormatKindStr] = TypeAdapter(CanonicalFormatKindStr)

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
    # WHICHEVER ARM the buyer used, carried through unchanged. The identity is the one of
    # format_id / format_kind the creative names, and the state mirrors the input -- a
    # creative synced by one arm is listed by that arm -- so neither is translated here.
    # Carrying only format_id made a canonical creative unrepresentable: the receiving
    # model's oneOf refuses a creative with neither identity.
    # ``format_option_ref`` rides with EITHER arm: core/creative-asset.json declares it
    # beside the root oneOf, and neither arm forbids it.
    identity_members: dict[str, Any] = {
        **(
            {"format_id": creative.format_id}
            if creative.format_id is not None
            else {"format_kind": creative.format_kind}
        ),
        "format_option_ref": creative.format_option_ref,
    }
    schema_data: dict[str, Any] = {
        "creative_id": creative.creative_id or str(uuid.uuid4()),
        "name": creative.name,
        **identity_members,
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

    # No provenance copy. The listing model this builds declares no ``provenance`` field --
    # the pin declares it on the REQUEST item (core/creative-asset.json) and not on
    # list-creatives-response.json's ``creatives[]`` -- and the only consumer,
    # :func:`check_provenance_policy`, now reads the request item it came from. The persisted
    # document is written from that same request item (``_assets.py``), so nothing that
    # stores or enforces provenance went through here.

    # Validate by creating a Creative schema object
    # This will fail if required fields are missing or invalid (like empty name)
    validated_creative = Creative(**schema_data)

    # Additional business logic validation
    if not creative.name or str(creative.name).strip() == "":
        raise AdCPValidationError(field="name")

    # THE VOCABULARY CHECK, asked of the pinned type directly. This used to build a
    # ``CreativeFormatIdentity`` purely to borrow that type's construction validator -- but
    # this function holds no projection, and the identity's ``kind`` is mandatory, so it was
    # constructing an identity it had no business owning and could no longer supply. The
    # user's ruling on exactly this shape: a function other than the creation path that
    # needs to derive a kind is reading the wrong field.
    #
    # It is still a real check and not redundant, measured rather than assumed:
    # ``Creative.format_kind`` is annotated ``str | None``, so the DTO accepts
    # ``format_kind="not_a_kind"`` and this is the only place on the sync path that refuses
    # it. What the DTO DOES already enforce is the arm exclusivity -- both arms and neither
    # arm are both refused on construction above -- which is the other half the identity
    # construction used to provide here.
    #
    # INVALID_REQUEST, not VALIDATION_ERROR. core/creative-asset.json types ``format_kind`` as
    # core/canonical-format-kind.json, an enum the pin calls "closed for producer validation"
    # whose tolerant in-memory parsing "does not change versioned JSON Schema validation". A
    # value outside it is a schema violation, which enums/error-code.json codes INVALID_REQUEST
    # ("violates schema constraints"); VALIDATION_ERROR is for violations "beyond schema
    # validation". The DTO is tolerant only because the SDK generates the field as ``str``.
    if creative.format_kind is not None:
        try:
            _CANONICAL_KIND.validate_python(creative.format_kind)
        except PydanticValidationError as exc:
            raise AdCPInvalidRequestError(
                details=ValidationDetails(rejected_value=str(creative.format_kind)),
                field="format_kind",
            ) from exc

    # The retained assertion, read off the arm the creative carries rather than off an
    # identity. ``format_id`` IS the assertion verbatim; the identity's ``assertion`` member
    # was only ever a copy of it.
    assertion = creative.format_id
    if assertion is None:
        # No retained assertion, so the creative named a KIND from a closed vocabulary rather
        # than a reference to a format some agent hosts. There is no agent to ask: the
        # registry walk below does not apply and the vocabulary check above is the whole of
        # its validation. Asked of the identity rather than by branching on its type —
        # the identity is one shape now, and what varies is whether it retains an assertion.
        #
        # ``assertion`` and not ``legacy``: the two are written together by ``of_legacy`` and
        # are exactly co-null, but this branch needs the REFERENCE's two halves below, and
        # ``legacy`` is one indexable key that is compared whole and never taken apart.
        return validated_creative

    _check_served_format(creative, assertion, index=index, creative_id=validated_creative.creative_id)

    return validated_creative


def _check_served_format(creative: CreativeAsset, assertion: LibraryFormatId, *, index: int, creative_id: str) -> None:
    """Hold a creative's retained ``format_id`` to the format its creative agent serves.

    A format no agent serves and the canonical mapping registry does not project is refused
    with REFERENCE_NOT_FOUND; a served format grades the manifest against its required assets.
    An adapter-provided format (non-HTTP ``agent_url``) is validated by the adapter itself.
    """
    # The retained assertion names a format a creative agent must actually serve. Canonical
    # agent_url, because a comparison requires it and because this is the value that gets
    # dialled — the same form the identity's ``legacy`` key is built from.
    agent_url = canonical_agent_url(assertion.agent_url)
    format_id = assertion.id

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
        from src.core.format_resolver import fetch_format_spec, registry_projects
        from src.core.security.outbound_http import CounterpartyUrl

        format_spec = fetch_format_spec(
            agent_url, format_id, provenance=CounterpartyUrl(field=f"creatives[{index}].format_id.agent_url")
        )
        # The agent's deprecated catalogue is not the authority on whether a legacy id names
        # a real format: docs/creative/template-format-ids.mdx § Compatibility (v3.2.7), "Do
        # not ... treat a deprecated list_creative_formats response as a source of truth". A
        # reference the canonical mapping registry projects is a format, listed or not, and
        # the write gate (``creative_identity_of``) projects it on the way in.
        if not format_spec and not registry_projects(assertion):
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
        # creative-manifests.mdx § Validation (v3.2.7): "Sellers perform final acceptance
        # during sync_creatives". A legacy manifest's declaration is the format it names, as
        # the agent serves it, and it is held to that format's assets the way a canonical
        # manifest is held to its slots. A reference only the mapping registry projects has
        # no served format to read, so there is nothing to grade it against here.
        if format_spec is not None:
            verdict = named_format_manifest_verdict(format_spec, creative.assets)
            if not verdict:
                raise creative_refusal(verdict, field=f"creatives[{index}]", creative_id=creative_id)
    else:
        logger.debug(f"Skipping external validation for adapter-provided format '{format_id}' (agent_url: {agent_url})")


def check_unassigned_manifest(creative: CreativeAsset, *, index: int, creative_id: str) -> None:
    """Refuse a canonical manifest that does not fill its kind, when nothing selects a declaration.

    docs/creative/creative-manifests.mdx § Validation: "Sellers perform final acceptance
    during sync_creatives". A sync that ASSIGNS the creative grades it against the product
    declaration the assignment selects (``creative_satisfies``, at the assignment gate). A
    sync that assigns it to nothing selects no declaration, so the manifest is held to what
    its ``format_kind`` names: the canonical format's slots, required and typed
    (``kind_manifest_verdict``). Only the canonical arm: a legacy item keys its assets by its
    named format's own asset ids, and is held to that format whether or not it is assigned
    (``named_format_manifest_verdict``, in :func:`_check_served_format`).

    ``field`` names the item, which the pin's creative codes ask a seller to identify; the
    manifest paths that failed are in ``details.problems[]``.
    """
    if creative.format_kind is None:
        return
    verdict = kind_manifest_verdict(str(creative.format_kind), creative.assets)
    if not verdict:
        raise creative_refusal(verdict, field=f"creatives[{index}]", creative_id=creative_id)


def check_representation_selection(
    creative: CreativeAsset, *, revision_id: str | None, index: int, creative_id: str
) -> None:
    """Refuse a sync item whose ``representation_selection`` does not describe it.

    docs/creative/task-reference/sync_creatives.mdx (v3.2.7): "When an item carries
    ``representation_selection``, its ``creative_id`` and ``revision_id`` must equal the
    selection lineage", and "The seller separately verifies ``selected_output_digest``"
    over the shared manifest projection (``selected_output_digest``). Each is a creative
    validation the buyer corrects and the pin has no specific code for, so it is
    CREATIVE_VALIDATION_FAILED_GENERIC, with ``field`` naming the lineage member that
    disagrees.

    ``revision_id`` is compared when the item states one. A package's inline creative is
    creative-asset.json alone and cannot, and this seller does not advertise
    ``creative.supports_revisions``, under which "receiver_may_ignore_revision_id"
    (sync-creatives-request.json ``revision_identity``). An item that does state one is
    making a claim about the lineage, and a claim that disagrees is refused.

    Not checked, because this seller receives only the selected output: the
    ``revision_content_digest`` against the complete set ("the actor that emitted the
    selection must first verify the digest against the complete set"). Revision reuse across
    syncs (CREATIVE_REVISION_CONTENT_MISMATCH) belongs to ``creative.supports_revisions``,
    which this seller does not advertise.
    """
    selection = creative.representation_selection
    if selection is None:
        return
    disagreements = (
        ("creative_id", selection.creative_id, creative.creative_id),
        ("revision_id", str(selection.revision_id), revision_id),
        ("selected_output_digest", selection.selected_output_digest, selected_output_digest(creative)),
    )
    for member, asserted, actual in disagreements:
        if actual is None or asserted == actual:
            continue
        field = f"creatives[{index}].representation_selection.{member}"
        raise AdCPCreativeValidationFailedError(
            field=field,
            details=CreativeManifestDetails(
                creative_id=creative_id,
                problems=[
                    ErrorProblem(
                        code=ErrorCode.CREATIVE_VALIDATION_FAILED_GENERIC,
                        subject_type="creative",
                        subject_id=creative_id,
                        field=field,
                        rejected_value=asserted,
                    )
                ],
            ),
        )


def _assets_carry_provenance(creative: CreativeAsset) -> bool:
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
    creative: CreativeAsset,
    creative_policy: CreativePolicy | dict | None,
    *,
    creative_id: str,
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

    INSPECTS THE REQUEST ITEM, which is where the pin puts the field.
    ``core/creative-asset.json`` -- the sync request's ``creatives[]`` -- declares
    ``provenance``; the listing model (``creatives[]`` of list-creatives-response.json, 28
    properties) does not. This used to take the listing model, so ``_validate_creative_input``
    had to COPY the value from the request item onto a local ``Creative`` subclass that
    declared a non-spec ``provenance`` field purely to carry it two lines -- and the field
    then appeared in the advertised list_creatives shape, where a buyer could not validate
    it. Reading the request item directly deletes the copy, the field and the hand-over.

    ``creative_id`` is passed rather than read off *creative*: the pin makes it OPTIONAL on a
    sync item (the seller assigns one when the buyer omits it), so the refusal would
    otherwise name no creative in exactly the case where a buyer most needs to know which of
    up to 100 items to fix.
    """
    if creative_policy is None:
        return
    # Read off whichever shape arrived. The repository hands back the persisted document
    # for a stored policy and the model for a typed one, and neither is serialized here:
    # a model dumped to inspect one field is a second representation of the same value
    # (CLAUDE.md pattern 4, serialize-only-at-the-edges).
    if not _get_field(creative_policy, "provenance_required"):
        return

    details = EntityRefDetails(creative_id=creative_id)
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
