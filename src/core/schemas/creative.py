"""Creative domain schemas.

All Creative-related Pydantic models extracted from the monolithic schemas module.
These classes handle creative lifecycle management, sync operations, listing,
assignments, and admin approval workflows.
"""

from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal

from adcp.types import (
    CreativeStatus,
)
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
from adcp.types import (
    QuerySummary as LibraryQuerySummary,
)
from adcp.types import (
    SyncCreativeResult as LibrarySyncCreativeResult,
)
from adcp.types import (
    SyncCreativesRequest as LibrarySyncCreativesRequest,
)
from adcp.types import (
    SyncCreativesResponse as LibrarySyncCreativesResponse,
)
from adcp.types.generated_poc.core.pagination_response import PaginationResponse as LibraryResponsePagination
from adcp.types.generated_poc.enums.creative_action import CreativeAction
from adcp.types.generated_poc.media_buy.list_creatives_response import (
    Creative as LibraryCreative,
)
from adcp.types.generated_poc.media_buy.sync_creatives_response import (
    SyncCreativesResponse1 as LibrarySyncCreativesSuccess,
)
from adcp.types.generated_poc.media_buy.sync_creatives_response import (
    SyncCreativesResponse2 as LibrarySyncCreativesError,
)
from pydantic import (
    ConfigDict,
    Field,
    model_validator,
)

from src.core.config import get_pydantic_extra_mode
from src.core.schemas._base import (
    ApprovalStatus,
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
    ai_tool: str | None = Field(
        default=None, description="Name/identifier of the AI tool used to create or modify the content"
    )
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
    """Creative status enum (not in adcp library, local definition)."""

    processing = "processing"
    approved = "approved"
    rejected = "rejected"
    pending_review = "pending_review"


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
    name: str = Field(description="Creative name")
    status: CreativeStatus = Field(
        default=CreativeStatus.pending_review,
        description="Workflow approval status",
    )
    created_date: datetime = Field(default_factory=lambda: datetime.now(tz=UTC), description="Creation timestamp")
    updated_date: datetime = Field(default_factory=lambda: datetime.now(tz=UTC), description="Update timestamp")
    # Override assets to untyped dict (our DB stores arbitrary asset dicts, not typed models)
    assets: dict[str, Any] | None = Field(default=None, description="Creative assets")

    # === AI Provenance (EU AI Act Article 50) ===
    provenance: Provenance | None = Field(default=None, description="AI provenance metadata per EU AI Act Article 50")

    # === Internal Fields (excluded from AdCP responses) ===
    principal_id: str | None = Field(
        default=None, exclude=True, description="Associates creative with advertiser (workflow tracking)"
    )

    @model_validator(mode="before")
    @classmethod
    def validate_format_id(cls, values):
        """Validate and upgrade format_id to AdCP namespaced format."""
        from src.core.format_cache import upgrade_legacy_format_id

        # Handle both 'format' and 'format_id' keys
        format_val = values.get("format_id") or values.get("format")
        if format_val is not None:
            try:
                upgraded = upgrade_legacy_format_id(format_val)
                values["format_id"] = upgraded
                # Remove 'format' alias to avoid extra field rejection
                values.pop("format", None)
            except ValueError as e:
                raise ValueError(f"Invalid format_id: {e}")

        # Strip delivery-only fields that callers may still pass from old code.
        # These fields existed on the delivery Creative base but not on the listing base.
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

    media_buy_id: str | None = None
    buyer_ref: str | None = None
    assets: list[Creative]  # Renamed from 'creatives' to match spec

    def model_validate(cls, values):
        # Ensure at least one of media_buy_id or buyer_ref is provided
        if not values.get("media_buy_id") and not values.get("buyer_ref"):
            raise ValueError("Either media_buy_id or buyer_ref must be provided")
        return values

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
    """Extends library SyncCreativesRequest with local Creative type.

    Library provides: account_id, assignments, context, creative_ids, creatives,
    delete_missing, dry_run, ext, push_notification_config, validation_mode — all
    inherited from AdCP spec.

    Local overrides:
    - creatives: list[Creative] instead of list[CreativeAsset] (our Creative extends
      LibraryCreative, which has a richer schema than CreativeAsset)
    - push_notification_config: kept as dict[str, Any] | None because the library's
      PushNotificationConfig requires 'authentication' and 'url' fields that aren't
      enforced in our current implementation
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    creatives: list[Creative] = Field(
        ..., min_length=1, max_length=100, description="Array of creative assets to sync (create or update)"
    )  # type: ignore[assignment]
    push_notification_config: dict[str, Any] | None = Field(  # type: ignore[assignment]
        None,
        description="Application-level webhook config (NOTE: Protocol-level push notifications via A2A/MCP transport take precedence)",
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
    - status, review_feedback: Internal fields excluded from responses
    - changes, errors, warnings: Override to default=[] (library defaults to None)
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    # Internal-only fields (not in AdCP spec)
    status: str | None = Field(
        None, exclude=True, description="Current approval status of the creative (INTERNAL - excluded from responses)"
    )
    review_feedback: str | None = Field(
        None, exclude=True, description="Feedback from platform review process (INTERNAL - excluded from responses)"
    )

    # Override library defaults: library uses None, we use [] for backward compatibility
    changes: list[str] = Field(
        default_factory=list, description="List of field names that were modified (for 'updated' action)"
    )
    errors: list[str] = Field(default_factory=list, description="Validation or processing errors (for 'failed' action)")
    warnings: list[str] = Field(default_factory=list, description="Non-fatal warnings about this creative")

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


class SyncCreativesResponse(LibrarySyncCreativesResponse):
    """Extends library SyncCreativesResponse RootModel with flat attribute access.

    Library uses RootModel[SuccessVariant | ErrorVariant] discriminated union.
    This proxy subclass provides @property accessors for backward-compatible
    flat attribute access (self.creatives, self.dry_run, self.errors, self.context)
    while preserving isinstance compatibility with the library type.

    Construction auto-wraps: SyncCreativesResponse(creatives=[...]) works without
    explicitly creating the variant. Field(exclude=True) on nested SyncCreativeResult
    is handled natively by Pydantic serialization.

    Design decision (salesagent-g3c): KEEP proxy pattern over union type alias.
    The error variant is never constructed (ToolError handles operation failures),
    so the union pattern would add ~30 consumer-site changes for no functional
    benefit. The only cost is one type:ignore[call-arg] at construction.
    """

    @property
    def creatives(self) -> list:
        """Access creatives from success variant (empty list for error variant)."""
        if isinstance(self.root, LibrarySyncCreativesSuccess):
            return self.root.creatives
        return []

    @property
    def dry_run(self) -> bool | None:
        """Access dry_run from success variant."""
        if isinstance(self.root, LibrarySyncCreativesSuccess):
            return self.root.dry_run
        return None

    @property
    def errors(self) -> list | None:
        """Access errors from error variant (None for success variant)."""
        if isinstance(self.root, LibrarySyncCreativesError):
            return self.root.errors
        return None

    @property
    def context(self):
        """Access context from whichever variant is active."""
        return self.root.context

    def model_dump(self, **kwargs) -> dict[str, Any]:
        """Override to default exclude_none=True per AdCP convention."""
        kwargs.setdefault("exclude_none", True)
        return super().model_dump(**kwargs)

    def __str__(self) -> str:
        """Return human-readable summary message for protocol envelope."""
        if isinstance(self.root, LibrarySyncCreativesError):
            return f"Creative sync failed with {len(self.root.errors)} error(s)."

        # Count actions from creatives list
        created = sum(1 for c in self.creatives if c.action == CreativeAction.created)
        updated = sum(1 for c in self.creatives if c.action == CreativeAction.updated)
        deleted = sum(1 for c in self.creatives if c.action == CreativeAction.deleted)
        failed = sum(1 for c in self.creatives if c.action == CreativeAction.failed)

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
    query_summary: QuerySummary = Field(..., description="Summary of the query that was executed")
    pagination: Pagination = Field(..., description="Pagination information for navigating results")
    creatives: list[Creative] = Field(..., description="Array of creative assets")  # type: ignore[assignment]

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


class CreativeApproval(SalesAgentBaseModel):
    """Creative approval record for a package."""

    creative_id: str = Field(..., description="Creative identifier")
    approval_status: ApprovalStatus = Field(..., description="Current approval status")
    rejection_reason: str | None = Field(default=None, description="Reason for rejection (when rejected)")
