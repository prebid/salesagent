"""Creative domain schemas.

All Creative-related Pydantic models extracted from the monolithic schemas module.
These classes handle creative lifecycle management, sync operations, listing,
assignments, and admin approval workflows.
"""

from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal

from adcp.types import (
    AiTool,
    CreativeAction,
    CreativeStatus,
    Error,
    SchemaVariant,
)
from adcp.types import CreativeApproval as LibraryCreativeApproval
from adcp.types import CreativeAsset as LibraryCreativeAsset
from adcp.types import FormatId as LibraryFormatId
from adcp.types import (
    ListCreativeFormatsRequest as LibraryListCreativeFormatsRequest,
)
from adcp.types import (
    ListCreativeFormatsResponse as LibraryListCreativeFormatsResponse,
)
from adcp.types import (
    ListCreativesRequest as LibraryListCreativesRequest,
)
from adcp.types import (
    ListCreativesResponse as LibraryListCreativesResponse,
)
from adcp.types import PaginationResponse as LibraryResponsePagination
from adcp.types import (
    QuerySummary as LibraryQuerySummary,
)
from adcp.types import (
    SyncCreativeResult as LibrarySyncCreativeResult,
)
from adcp.types import (
    SyncCreativesRequest as LibrarySyncCreativesRequest,
)

# Pin to the listing-side ``Creative`` (list_creatives_response). The
# top-level ``adcp.types.Creative`` resolves to the delivery-side type
# (get_creative_delivery_response) since adcp 4.4 — that variant has only
# ``creative_id, media_buy_id, format_id, totals, variant_count, variants``
# and rejects ``tags`` / ``status`` / ``assets`` etc. Our Creative is
# explicitly a listing-side schema, so we extend the listing variant.
from adcp.types.generated_poc.creative.list_creatives_response import (
    Creative as LibraryCreative,
)
from adcp.types.generated_poc.creative.sync_creatives_response import (
    SyncCreativesResponse1 as LibrarySyncCreativesSuccess,
)
from pydantic import (
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from src.core._deprecations import LEGACY_FORMAT_ID_SUNSET, warn_deprecated
from src.core.config import get_pydantic_extra_mode
from src.core.schemas._base import (
    FormatId,
    NestedModelSerializerMixin,
    SalesAgentBaseModel,
    Targeting,
    _upgrade_legacy_format_ids,
)


class DigitalSourceType(str, Enum):
    """IPTC Digital Source Type enumeration for AI provenance tracking.

    Values from IPTC NewsCodes vocabulary for Digital Source Type,
    relevant to EU AI Act Article 50 disclosure requirements.
    """

    digital_capture = "digital_capture"
    digital_creation = "digital_creation"
    composite_capture = "composite_capture"
    composite_synthetic = "composite_synthetic"
    composite_with_trained_model = "composite_with_trained_model"
    trained_algorithmic_model = "trained_algorithmic_model"
    algorithmic_media = "algorithmic_media"
    human_edits = "human_edits"
    minor_human_edits = "minor_human_edits"


class Provenance(SalesAgentBaseModel):
    """AI provenance metadata for creative assets.

    Tracks the origin, AI involvement, and disclosure status of creative content
    per EU AI Act Article 50 requirements (enforcement Aug 2026).

    The sales agent is pass-through: it stores and forwards provenance metadata
    from buyers/creative agents, it does not generate it.
    """

    digital_source_type: DigitalSourceType = Field(
        ..., description="IPTC Digital Source Type indicating how the content was created"
    )
    ai_tool: AiTool | None = Field(
        default=None, description="AI tool used to create or modify the content (adcp 3.9 AiTool model)"
    )

    @field_validator("ai_tool", mode="before")
    @classmethod
    def _coerce_ai_tool(cls, v: Any) -> Any:
        """Accept a plain string for backward compatibility, wrapping it as AiTool(name=v)."""
        if isinstance(v, str):
            return AiTool(name=v)
        return v

    human_oversight: bool | None = Field(
        default=None, description="Whether a human reviewed/approved the AI-generated content"
    )
    declared_by: str | None = Field(
        default=None, description="Entity that declared the provenance metadata (e.g., advertiser, agency)"
    )
    created_time: datetime | None = Field(default=None, description="When the provenance declaration was created")
    c2pa: str | None = Field(
        default=None, description="URL to C2PA (Coalition for Content Provenance and Authenticity) manifest store"
    )
    disclosure: str | None = Field(default=None, description="Human-readable disclosure statement about AI involvement")
    verification: dict[str, Any] | None = Field(
        default=None, description="Verification metadata (e.g., C2PA validation results, signature info)"
    )


class CreativeStatusEnum(Enum):
    """Local creative status enum.

    The library exposes ``adcp.types.CreativeStatus`` with the same values plus
    ``archived``. Switch to the library enum when the archived state is wired
    into the creative workflow.
    """

    processing = "processing"
    approved = "approved"
    rejected = "rejected"
    pending_review = "pending_review"


def _upgrade_format_id_in_values(values: Any) -> Any:
    """Upgrade ``format_id`` (or legacy ``format`` alias) to the AdCP namespaced FormatId.

    Used by both ``Creative`` (listing shape) and ``CreativeAsset`` (sync shape) so
    string format_ids, the legacy ``format`` key, and library-typed ``FormatReferenceStructuredObject``
    instances are all normalized to the local ``FormatId`` subclass.

    Buyer-facing legacy shapes emit ``DeprecationWarning`` (sunset target
    ``LEGACY_FORMAT_ID_SUNSET``):
    - String ``format_id`` (warns inside ``upgrade_legacy_format_id``)
    - Legacy ``format`` key in place of ``format_id`` (warned here)
    Library-typed ``FormatReferenceStructuredObject`` conversion is silent because
    it is internal plumbing, not buyer-facing.
    """
    from pydantic import BaseModel

    from src.core.format_cache import upgrade_legacy_format_id

    # ``model_validate(other_model_instance, from_attributes=True)`` passes the
    # source BaseModel here — dump it so the rest of the validator can mutate.
    if isinstance(values, BaseModel):
        values = values.model_dump()

    if not isinstance(values, dict):
        return values

    if "format" in values and "format_id" not in values:
        warn_deprecated(
            f"Legacy creative key 'format' is deprecated; use 'format_id'. "
            f"Will be removed in {LEGACY_FORMAT_ID_SUNSET}."
        )

    format_val = values.get("format_id") or values.get("format")
    if format_val is not None:
        try:
            values["format_id"] = upgrade_legacy_format_id(format_val)
            values.pop("format", None)
        except ValueError as e:
            raise ValueError(f"Invalid format_id: {e}") from e

    assets = values.get("assets")
    if isinstance(assets, dict):
        from src.core.schemas._asset_type_compat import infer_asset_types

        values["assets"] = infer_asset_types(assets)
    return values


# --- Creative sync wire shape ---
class CreativeAsset(LibraryCreativeAsset):
    """Sync/create wire shape — extends library CreativeAsset.

    AdCP's ``sync_creatives`` and ``create`` flows take ``CreativeAsset`` (provenance,
    weight, placement_ids, industry_identifiers, inputs); the listing flow returns
    the richer ``Creative`` (status, dates, account, assignments, etc.). Splitting
    keeps each wire honest about which fields it actually carries.

    Local extension: ``format_id`` accepts a plain string or legacy ``format`` alias
    and upgrades to the structured FormatId for backward compatibility with pre-v2.4
    callers.

    Inherits ``extra="allow"`` from the library type — sync payloads commonly carry
    forward-compat fields that should pass through silently.
    """

    @model_validator(mode="before")
    @classmethod
    def upgrade_format_id(cls, values: Any) -> Any:
        return _upgrade_format_id_in_values(values)


# --- Creative Lifecycle ---
class Creative(LibraryCreative):
    """Individual creative asset - extends listing Creative with internal workflow fields.

    adcp 3.6.0 listing Creative fields (public):
    - Required: creative_id, format_id, name, status, created_date, updated_date
    - Optional: assets, assignments, catalogs, tags, performance, account, sub_assets

    Internal fields (excluded from AdCP responses, used for workflow/DB):
    - principal_id: associates creative with advertiser principal
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    # === Overrides of listing Creative fields ===
    status: CreativeStatus = Field(
        default=CreativeStatus.pending_review,
        description="Workflow approval status",
    )
    created_date: datetime = Field(default_factory=lambda: datetime.now(tz=UTC), description="Creation timestamp")
    updated_date: datetime = Field(default_factory=lambda: datetime.now(tz=UTC), description="Update timestamp")
    # Override assets to untyped dict (our DB stores arbitrary asset dicts, not typed models)
    assets: dict[str, Any] | None = Field(default=None, description="Creative assets")

    # === AI Provenance (EU AI Act Article 50) ===
    # Library 4.5.0 ships provenance on CreativeAsset (sync shape) but not on
    # the listing Creative we extend. Salesagent surfaces it on listings too
    # because sellers need to see disclosure metadata when reviewing — track
    # spec inclusion in adcp RFC #4282.
    provenance: Provenance | None = Field(
        default=None,
        description=(
            "AI provenance metadata per EU AI Act Article 50. "
            "On the listing wire (list_creatives) this is a salesagent extension "
            "surfacing the sync-side provenance back to sellers for review; the "
            "canonical write path is sync_creatives. Becomes spec-native once "
            "AdCP RFC #4282 lands upstream — if the upstream shape is incompatible "
            "with the local Provenance, this extension is removed (not migrated)."
        ),
    )

    # === Internal Fields (excluded from AdCP responses) ===
    principal_id: str | None = Field(
        default=None, exclude=True, description="Associates creative with advertiser (workflow tracking)"
    )

    @model_validator(mode="before")
    @classmethod
    def validate_format_id(cls, values):
        """Validate and upgrade format_id to AdCP namespaced format."""
        values = _upgrade_format_id_in_values(values)

        # Strip delivery-only fields that callers may still pass from old code.
        # These fields existed on the delivery Creative base but not on the listing base.
        if isinstance(values, dict):
            for field in ("variants", "variant_count", "totals", "media_buy_id"):
                values.pop(field, None)

        return values

    # Helper properties for format_id (still present in 3.6.0)
    @property
    def format(self) -> LibraryFormatId | None:
        """Alias for format_id."""
        return self.format_id

    @property
    def format_id_str(self) -> str | None:
        """Get format ID string from FormatId object."""
        return self.format_id.id if self.format_id else None

    @property
    def format_agent_url(self) -> str | None:
        """Get agent URL string from FormatId object."""
        return str(self.format_id.agent_url) if self.format_id else None

    def model_dump_internal(self, **kwargs):
        """Dump including internal fields for database storage.

        Pydantic v2's Field(exclude=True) cannot be overridden via model_dump parameters.
        We manually include the principal_id field which is excluded from public responses.
        """
        data = super().model_dump(exclude=set(), **kwargs)
        if self.principal_id is not None:
            data["principal_id"] = self.principal_id
        # Ensure status is always present as string value for DB storage
        data["status"] = self.status.value if isinstance(self.status, CreativeStatus) else self.status
        return data


class CreativeAdaptation(SalesAgentBaseModel):
    """Suggested adaptation or variant of a creative."""

    adaptation_id: str
    format_id: FormatId
    name: str
    description: str
    preview_url: str | None = None
    changes_summary: list[str] = Field(default_factory=list)
    rationale: str | None = None
    estimated_performance_lift: float | None = None  # Percentage improvement expected


class CreativeApprovalStatus(SalesAgentBaseModel):
    """Creative approval status result (different from CreativeStatus enum)."""

    creative_id: str
    status: Literal["pending_review", "approved", "rejected", "adaptation_required"]
    detail: str
    estimated_approval_time: datetime | None = None
    suggested_adaptations: list[CreativeAdaptation] = Field(default_factory=list)


class CreativeAssignment(SalesAgentBaseModel):
    """Maps creatives to packages with distribution control.

    NOTE: Does not extend adcp.types.CreativeAssignment intentionally.
    Library type has 3 fields (creative_id, placement_ids, weight) for AdCP spec.
    This local type is an internal tracking entity with 12 fields (assignment_id,
    media_buy_id, package_id, overrides, targeting, etc.) — different semantics.
    """

    assignment_id: str
    media_buy_id: str
    package_id: str
    creative_id: str

    # Distribution control
    weight: int | None = 100  # Relative weight for rotation
    percentage_goal: float | None = None  # Percentage of impressions
    rotation_type: Literal["weighted", "sequential", "even"] | None = "weighted"

    # Override settings (platform-specific)
    override_click_url: str | None = None
    override_start_date: datetime | None = None
    override_end_date: datetime | None = None

    # Targeting override (creative-specific targeting)
    targeting_overlay: Targeting | None = None

    is_active: bool = True

    @model_validator(mode="after")
    def validate_timezone_aware(self):
        """Validate that datetime override fields are timezone-aware.

        AdCP spec requires ISO 8601 datetime strings with timezone information.
        """
        if self.override_start_date and self.override_start_date.tzinfo is None:
            raise ValueError("override_start_date must be timezone-aware (ISO 8601 with timezone)")
        if self.override_end_date and self.override_end_date.tzinfo is None:
            raise ValueError("override_end_date must be timezone-aware (ISO 8601 with timezone)")
        return self


class AddCreativeAssetsRequest(SalesAgentBaseModel):
    """Request to add creative assets to a media buy (AdCP spec compliant)."""

    media_buy_id: str
    assets: list[Creative]  # Renamed from 'creatives' to match spec

    # Backward compatibility
    @property
    def creatives(self) -> list[Creative]:
        """Backward compatibility for existing code."""
        return self.assets


class AddCreativeAssetsResponse(NestedModelSerializerMixin, SalesAgentBaseModel):
    """Response from adding creative assets (AdCP spec compliant)."""

    statuses: list[CreativeApprovalStatus]


# Legacy aliases for backward compatibility (to be removed)
SubmitCreativesRequest = AddCreativeAssetsRequest
SubmitCreativesResponse = AddCreativeAssetsResponse


class SyncCreativesRequest(LibrarySyncCreativesRequest):
    """Extends library SyncCreativesRequest.

    Library provides: account_id, assignments, context, creative_ids, creatives,
    delete_missing, dry_run, ext, push_notification_config, validation_mode — all
    inherited from AdCP spec.

    Local overrides:
    - creatives: list[CreativeAsset] re-typed against the local CreativeAsset
      subclass so the format_id upgrade validator runs on the wire.
    - push_notification_config inherited from library; buyers send the typed
      PushNotificationConfig shape (authentication + url required per spec).
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    creatives: SchemaVariant[list[CreativeAsset]] = Field(
        ..., min_length=1, max_length=100, description="Array of creative assets to sync (create or update)"
    )


class SyncSummary(SalesAgentBaseModel):
    """Summary of sync operation results."""

    total_processed: int = Field(..., ge=0, description="Total number of creatives processed")
    created: int = Field(..., ge=0, description="Number of new creatives created")
    updated: int = Field(..., ge=0, description="Number of existing creatives updated")
    unchanged: int = Field(..., ge=0, description="Number of creatives that were already up-to-date")
    failed: int = Field(..., ge=0, description="Number of creatives that failed validation or processing")
    deleted: int = Field(0, ge=0, description="Number of creatives deleted/archived (when delete_missing=true)")


class SyncCreativeResult(LibrarySyncCreativeResult):
    """Extends library SyncCreativeResult with internal-only fields.

    Library provides: creative_id, action (CreativeAction enum), platform_id,
    changes, errors, warnings, assigned_to, assignment_errors, account,
    expires_at, preview_url.

    Local overrides:
    - internal_status, review_feedback: Internal fields excluded from responses.
      ``internal_status`` is named distinctly from the library's ``status`` field
      (which is the spec's review-lifecycle CreativeStatus) to avoid shadowing.
    - changes, errors, warnings: Override to default=[] (library defaults to None)
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    # Internal-only fields — excluded from API responses. Named distinctly
    # from the library ``status: CreativeStatus | None`` field (spec
    # review-lifecycle indicator) to avoid spec collision.
    internal_status: str | None = Field(
        None, exclude=True, description="Current approval status of the creative (INTERNAL - excluded from responses)"
    )
    status: Any | None = Field(
        None,
        exclude=True,
        description="Legacy internal approval status accepted for compatibility with existing result construction.",
    )
    # platform_id is inherited from the adcp 6.3 library parent as a wire-visible
    # field (we no longer override it with exclude=True). Exposing the ad-server's
    # creative ID lets buyers reconcile creatives that exist on the platform —
    # including ones created before this account onboarded to AdCP.
    review_feedback: str | None = Field(
        None, exclude=True, description="Feedback from platform review process (INTERNAL - excluded from responses)"
    )

    # Override library defaults: library uses None, we use [] for backward compatibility.
    # ``errors`` is ``list[Error]`` per AdCP spec (each entry needs a ``code`` for
    # programmatic handling); the previous ``list[str]`` shape was off-spec and
    # tripped FastMCP's response validator on the [mcp] transport.
    changes: list[str] = Field(
        default_factory=list, description="List of field names that were modified (for 'updated' action)"
    )
    errors: list[Error] = Field(
        default_factory=list, description="Validation or processing errors (for 'failed' action)"
    )
    warnings: list[str] = Field(default_factory=list, description="Non-fatal warnings about this creative")
    # assigned_to / assignment_errors are inherited from the adcp 6.3 library parent
    # (promoted onto SyncCreativeResult). Our prior redeclarations only widened the
    # lib types; production assigns values that satisfy the stricter parent. NOTE: the
    # parent constrains assignment_errors keys to ^[a-zA-Z0-9_-]+$ — fine for current
    # paths (set to None or via attribute assignment, neither validated), but a future
    # validating construct with buyer-supplied package_id keys would now be checked.

    @field_validator("action", mode="before")
    @classmethod
    def normalize_known_action(cls, value: Any) -> Any:
        """Store known AdCP creative actions as enums while accepting extension strings."""
        if isinstance(value, str):
            try:
                return CreativeAction(value)
            except ValueError:
                return value
        return value

    def model_dump(self, **kwargs):
        """Override to exclude non-AdCP fields for spec compliance.

        The AdCP spec (sync-creatives-response.json) only allows specific fields
        with "additionalProperties": false. We exclude internal fields:
        - status: Internal approval status tracking
        - review_feedback: Internal review process feedback

        Also excludes None values and empty lists to match AdCP spec where optional
        fields should be omitted rather than set to null/empty.
        """
        exclude = kwargs.get("exclude", set())
        if isinstance(exclude, set):
            # Exclude internal fields that aren't in AdCP spec
            exclude.update({"status", "review_feedback"})
            kwargs["exclude"] = exclude

        # Exclude None values by default for AdCP compliance
        if "exclude_none" not in kwargs:
            kwargs["exclude_none"] = True

        # Call parent model_dump
        result = super().model_dump(**kwargs)

        # Also exclude empty lists for cleaner responses (only include fields with data)
        # Per AdCP spec: changes, errors, warnings are optional, so omit if empty
        if "changes" in result and not result["changes"]:
            result.pop("changes", None)
        if "errors" in result and not result["errors"]:
            result.pop("errors", None)
        if "warnings" in result and not result["warnings"]:
            result.pop("warnings", None)

        return result

    def model_dump_internal(self, **kwargs):
        """Dump including all fields for database storage and internal processing."""
        kwargs.pop("exclude", None)  # Remove any exclude parameter
        return super().model_dump(**kwargs)


class AssignmentsSummary(SalesAgentBaseModel):
    """Summary of assignment operations."""

    total_assignments_processed: int = Field(
        ..., ge=0, description="Total number of creative-package assignment operations processed"
    )
    assigned: int = Field(..., ge=0, description="Number of successful creative-package assignments")
    unassigned: int = Field(..., ge=0, description="Number of creative-package unassignments")
    failed: int = Field(..., ge=0, description="Number of assignment operations that failed")


class AssignmentResult(SalesAgentBaseModel):
    """Detailed result for creative-package assignments."""

    creative_id: str = Field(..., description="Creative that was assigned/unassigned")
    assigned_packages: list[str] = Field(
        default_factory=list, description="Packages successfully assigned to this creative"
    )
    unassigned_packages: list[str] = Field(
        default_factory=list, description="Packages successfully unassigned from this creative"
    )
    failed_packages: list[dict[str, str]] = Field(
        default_factory=list, description="Packages that failed to assign/unassign (package_id + error)"
    )


class SyncCreativesResponse(LibrarySyncCreativesSuccess):
    """Extends library SyncCreativesResponse success variant.

    adcp 3.9: SyncCreativesResponse is now a union TypeAlias (not RootModel).
    Since the error variant is never constructed (ToolError handles failures),
    we subclass the success variant directly. Fields (creatives, dry_run,
    context, ext, sandbox) are inherited.

    Design decision (salesagent-g3c): error variant never constructed.
    """

    dry_run: bool | None = None

    @field_validator("creatives", mode="after")
    @classmethod
    def _coerce_creatives(cls, values: list[Any]) -> list[SyncCreativeResult]:
        """Hydrate nested creatives into the local extension model.

        adcp 5.7's generated response field points at the library row model.
        The local row model extends that type with compatibility defaults and
        enum coercion, so MCP round-trips need to rehydrate nested rows here.
        """
        coerced: list[SyncCreativeResult] = []
        for value in values:
            if isinstance(value, SyncCreativeResult):
                coerced.append(value)
                continue
            if hasattr(value, "model_dump"):
                coerced.append(SyncCreativeResult.model_validate(value.model_dump(mode="json", exclude_none=True)))
            else:
                coerced.append(SyncCreativeResult.model_validate(value))
        return coerced

    def model_dump(self, **kwargs):
        """Pattern #4 nested serialization — re-serialize each ``SyncCreativeResult``
        through its own ``model_dump()`` so the local ``status`` /
        ``review_feedback`` fields with ``exclude=True`` are dropped.
        """
        result = super().model_dump(**kwargs)
        if "creatives" in result and self.creatives:
            result["creatives"] = [c.model_dump(**kwargs) for c in self.creatives]
        return result

    def __str__(self) -> str:
        """Return human-readable summary message for protocol envelope."""
        # Count actions from creatives list
        actions = []
        for creative in self.creatives:
            action = creative.action
            actions.append(action.value if hasattr(action, "value") else str(action))
        created = actions.count(CreativeAction.created.value)
        updated = actions.count(CreativeAction.updated.value)
        deleted = actions.count(CreativeAction.deleted.value)
        failed = actions.count(CreativeAction.failed.value)

        parts = []
        if created:
            parts.append(f"{created} created")
        if updated:
            parts.append(f"{updated} updated")
        if deleted:
            parts.append(f"{deleted} deleted")
        if failed:
            parts.append(f"{failed} failed")

        if parts:
            msg = f"Creative sync completed: {', '.join(parts)}"
        else:
            msg = "Creative sync completed: no changes"

        if self.dry_run:
            msg += " (dry run)"

        return msg


class ListCreativeFormatsRequest(LibraryListCreativeFormatsRequest):
    """Extends library ListCreativeFormatsRequest from AdCP spec.

    Inherits all AdCP-compliant fields from adcp library,
    ensuring we stay in sync with spec updates.
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    @model_validator(mode="before")
    @classmethod
    def upgrade_legacy_format_ids(cls, values: dict) -> dict:
        """Convert dict format_ids to FormatId objects (AdCP v2.4 compliance)."""
        return _upgrade_legacy_format_ids(values)


class ListCreativeFormatsResponse(NestedModelSerializerMixin, LibraryListCreativeFormatsResponse):
    """Extends library ListCreativeFormatsResponse from AdCP spec.

    Inherits all AdCP-compliant fields from adcp library,
    ensuring we stay in sync with spec updates.

    Adds NestedModelSerializerMixin for proper nested model serialization
    and custom __str__ for human-readable protocol messages.

    Per AdCP PR #113, this response contains ONLY domain data.
    Protocol fields (status, task_id, message, context_id) are added by the
    protocol layer (MCP, A2A, REST) via ProtocolEnvelope wrapper.
    """

    def __str__(self) -> str:
        """Return human-readable message for protocol layer.

        Used by both MCP (for display) and A2A (for task messages).
        Provides conversational text without adding non-spec fields to the schema.
        """
        count = len(self.formats)
        if count == 0:
            return "No creative formats are currently supported."
        elif count == 1:
            return "Found 1 creative format."
        else:
            return f"Found {count} creative formats."


class ListCreativesRequest(LibraryListCreativesRequest):
    """Extends library ListCreativesRequest from AdCP spec.

    Per AdCP spec, all fields are optional:
    - context: dict (application-level context)
    - ext: dict (extension object for custom fields)
    - fields: list[FieldModel] (specific fields to return)
    - filters: CreativeFilters (structured filter object)
    - include_assignments: bool (include package assignments, default True)
    - include_performance: bool (include performance metrics, default False)
    - include_sub_assets: bool (include sub-assets, default False)
    - pagination: Pagination (structured pagination object)
    - sort: Sort (structured sort object)
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())


class QuerySummary(LibraryQuerySummary):
    """Extends library QuerySummary with non-None defaults.

    Library defaults filters_applied to None; we keep list default for backward compat.
    sort_applied inherits SortApplied | None from library (Pydantic handles dict coercion).
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())
    # Override to keep non-None default (construction sites rely on this)
    filters_applied: list[str] = Field(default_factory=list)


class Pagination(LibraryResponsePagination):
    """Pagination information for list response results.

    Uses cursor-based pagination (cursor, has_more, total_count).
    This is the appropriate type for list endpoints like list_creatives.
    """

    pass  # Inherits all fields from library: cursor, has_more, total_count


class ListCreativesResponse(NestedModelSerializerMixin, LibraryListCreativesResponse):
    """Extends library ListCreativesResponse with local subtypes.

    Library provides: context, creatives, ext, format_summary, pagination,
    query_summary, status_summary — all inherited from AdCP spec.

    Local overrides nested types to ensure correct dict-to-model parsing
    (Pydantic needs the local type annotations for local subtypes).
    Other fields (format_summary, status_summary, context, ext) inherited.
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    # Override with local subtypes (each extends its library counterpart)
    query_summary: SchemaVariant[QuerySummary] = Field(..., description="Summary of the query that was executed")
    pagination: Pagination = Field(..., description="Pagination information for navigating results")
    creatives: list[Creative] = Field(..., description="Array of creative assets")

    def __str__(self) -> str:
        """Return human-readable summary message for protocol envelope."""
        count = self.query_summary.returned
        total = self.query_summary.total_matching
        if count == total:
            return f"Found {count} creative{'s' if count != 1 else ''}."
        else:
            return f"Showing {count} of {total} creatives."


class CheckCreativeStatusRequest(SalesAgentBaseModel):
    creative_ids: list[str]


class CheckCreativeStatusResponse(NestedModelSerializerMixin, SalesAgentBaseModel):
    statuses: list[CreativeApprovalStatus]


class CreateCreativeRequest(SalesAgentBaseModel):
    """Create a creative in the library (not tied to a media buy)."""

    group_id: str | None = None
    format_id: str
    content_uri: str
    name: str
    click_through_url: str | None = None
    metadata: dict[str, Any] | None = {}


class CreateCreativeResponse(NestedModelSerializerMixin, SalesAgentBaseModel):
    creative: Creative
    status: CreativeApprovalStatus
    suggested_adaptations: list[CreativeAdaptation] = Field(default_factory=list)

    def __str__(self) -> str:
        """Return human-readable text for MCP content field."""
        return f"Creative {self.creative.creative_id} created with status: {self.status.status}"


class AssignCreativeRequest(SalesAgentBaseModel):
    """Assign a creative from the library to a package."""

    media_buy_id: str
    package_id: str
    creative_id: str
    weight: int | None = 100
    percentage_goal: float | None = None
    rotation_type: Literal["weighted", "sequential", "even"] | None = "weighted"
    override_click_url: str | None = None
    override_start_date: datetime | None = None
    override_end_date: datetime | None = None
    targeting_overlay: Targeting | None = None

    @model_validator(mode="after")
    def validate_timezone_aware(self):
        """Validate that datetime override fields are timezone-aware.

        AdCP spec requires ISO 8601 datetime strings with timezone information.
        """
        if self.override_start_date and self.override_start_date.tzinfo is None:
            raise ValueError("override_start_date must be timezone-aware (ISO 8601 with timezone)")
        if self.override_end_date and self.override_end_date.tzinfo is None:
            raise ValueError("override_end_date must be timezone-aware (ISO 8601 with timezone)")
        return self


class AssignCreativeResponse(NestedModelSerializerMixin, SalesAgentBaseModel):
    assignment: CreativeAssignment


class GetCreativesRequest(SalesAgentBaseModel):
    """Get creatives with optional filtering."""

    group_id: str | None = None
    media_buy_id: str | None = None
    status: str | None = None
    tags: list[str] | None = None
    include_assignments: bool = False


class GetCreativesResponse(NestedModelSerializerMixin, SalesAgentBaseModel):
    creatives: list[Creative]
    assignments: list[CreativeAssignment] | None = None


# Admin tools
class GetPendingCreativesRequest(SalesAgentBaseModel):
    """Admin-only: Get all pending creatives across all principals."""

    principal_id: str | None = None  # Filter by principal if specified
    limit: int | None = 100


class GetPendingCreativesResponse(SalesAgentBaseModel):
    pending_creatives: list[dict[str, Any]]  # Includes creative + principal info


class ApproveCreativeRequest(SalesAgentBaseModel):
    """Admin-only: Approve or reject a creative."""

    creative_id: str
    action: Literal["approve", "reject"]
    reason: str | None = None


class ApproveCreativeResponse(SalesAgentBaseModel):
    creative_id: str
    new_status: str
    detail: str


class CreativeApproval(LibraryCreativeApproval):
    """Creative approval record — extends library type per Pattern #1."""
