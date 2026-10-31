"""Creative domain schemas.

All Creative-related Pydantic models extracted from the monolithic schemas module.
These classes handle creative lifecycle management, sync operations, listing,
assignments, and admin approval workflows.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from enum import Enum
from typing import Annotated, Any, ClassVar, Literal

from adcp.types import CreativeStatus
from adcp.types import PaginationResponse as LibraryResponsePagination
from adcp.types import (
    QuerySummary as LibraryQuerySummary,
)
from adcp.types import (
    SyncCreativeResult as LibrarySyncCreativeResult,
)

# The BRANCH, named at its generated path. the pinned SDK retired the numeric arm name: the branch
# IS ``CreativeAsset`` in the generated module now, and neither it nor the public
# ``adcp.types.CreativeAsset`` is a RootModel, so the static/runtime disagreement this
# import was written around is gone. The import stays on the GENERATED path for one
# reason: the public name binds to ``adcp.types.canonical_creative.CreativeAsset``, which
# STRIPS ``format_id``. Repointing it is part of the creative migration (salesagent-3xcdk),
# not of the SDK bump.
from adcp.types.domains.core.creative_asset import CreativeAsset as LibraryCreativeAsset
from adcp.types.domains.core.creative_filters import CreativeFilters as LibraryCreativeFilters
from adcp.types.domains.core.creative_revision_id import CreativeRevisionId
from adcp.types.domains.core.format_id import FormatReferenceStructuredObject as LibraryFormatId
from adcp.types.domains.core.provenance import Provenance as LibraryProvenance
from adcp.types.domains.creative.list_creatives_response import (
    Creative as LibraryCreative,
)
from adcp.types.domains.creative.sync_creatives_request import Creative as LibrarySyncCreative
from adcp.types.domains.creative.sync_creatives_response import (
    SyncCreativesResponse1 as LibrarySyncCreativesSuccess,
)
from adcp.types.domains.enums.digital_source_type import DigitalSourceType as LibraryDigitalSourceType
from adcp.types.domains.media_buy.get_media_buys_response import (
    CreativeApproval as LibraryGetMediaBuysCreativeApproval,
)

# The legacy arm from the module that contains only it — see the note in ``_base.py``.
from adcp.types.legacy import (
    LegacyListCreativeFormatsRequest as LibraryListCreativeFormatsRequest,
)
from adcp.types.legacy import (
    LegacyListCreativeFormatsResponse as LibraryListCreativeFormatsResponse,
)
from adcp.types.legacy import (
    LegacyListCreativesRequest as LibraryListCreativesRequest,
)
from adcp.types.legacy import (
    LegacyListCreativesResponse as LibraryListCreativesResponse,
)
from adcp.types.legacy import (
    LegacySyncCreativesRequest as LibrarySyncCreativesRequest,
)
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

from src.core.config import get_pydantic_extra_mode
from src.core.schemas._base import (
    BuyerRequest,
    FormatId,
    FormatIdSlot,
    NestedModelSerializerMixin,
    PeerFormat,
    SalesAgentAdcpResponse,
    SalesAgentBaseModel,
    Targeting,
)
from src.core.schemas._dependencies import as_local, build_as_local, narrowed_from
from src.core.schemas.notification import PushNotificationConfig

#: IPTC Digital Source Type, for AI provenance under EU AI Act Article 50.
#:
#: THE PINNED ENUM ITSELF, not a local copy of it. This was a hand-written ``StrEnum``
#: duplicating the vocabulary, and it had DRIFTED: it still carried
#: ``composite_with_trained_model``, ``trained_algorithmic_model`` and
#: ``minor_human_edits``, while the pin renamed the first two to
#: ``composite_with_trained_algorithmic_media`` / ``trained_algorithmic_media``, dropped
#: the third, and added ``data_driven_media``.
#:
#: The drift was not cosmetic. ``Provenance.digital_source_type`` is typed with the
#: LIBRARY enum, so every renamed member of the local copy was REFUSED by the very model
#: it existed to populate — constructing a Provenance with one raised ValidationError.
#: Nothing in ``src/`` used it (measured: one definition, zero other references), so the
#: breakage lived only in tests, which is why it survived.
#:
#: An alias rather than a subclass because Python forbids extending an enum that has
#: members. CLAUDE.md Pattern #1: use the
#: library type, never duplicate it.
DigitalSourceType = LibraryDigitalSourceType


#: AI provenance metadata for creative assets: the pinned ``core/provenance.json``, used
#: directly. Tracks the origin, AI involvement and disclosure status of creative content
#: per EU AI Act Article 50 (enforcement Aug 2026). The sales agent is pass-through -- it
#: stores and forwards what buyers and creative agents declare, it does not generate it.
#:
#: An ALIAS rather than a subclass, for the same reason as ``DigitalSourceType`` above:
#: there is nothing local to add. The subclass this replaces had a docstring and no body --
#: identical field set, identical annotations, identical config, zero validators of its
#: own -- so it conformed by inheritance and then existed only to be a DIFFERENT CLASS.
#:
#: That difference had a cost. Pydantic validates a model-typed slot by instance, and the
#: one real arrival path (``src/core/tools/creatives/_validation.py``, which reads
#: ``provenance`` off a ``CreativeAssetRequest`` and hands it to ``Creative``) delivers the
#: LIBRARY class, so the subclass REFUSED the very object it was there to carry. A
#: ``mode="before"`` validator existed purely to rebuild one from the other's attributes.
#: Deleting the subclass deletes the cause, and the validator went with it.
#:
#: What the earlier hand-written version got wrong is recorded here because the pin is the
#: only place that shape is now stated: it declared eight of the pin's twelve fields --
#: missing ``declared_at``, ``embedded_provenance``, ``watermarks`` and ``ext`` entirely --
#: and typed four of the eight as scalars where the pin declares objects (``c2pa``,
#: ``disclosure``, ``declared_by``, ``human_oversight``), with ``verification`` a list of
#: them rather than a free dict. It also wrapped a plain string ``ai_tool`` as
#: ``AiTool(name=...)``, a tolerance the pin does not grant. Using the pinned class is what
#: makes all of that unrepeatable.
Provenance = LibraryProvenance


class CreativeStatusEnum(Enum):
    """Creative status enum (not in adcp library, local definition)."""

    processing = "processing"
    approved = "approved"
    rejected = "rejected"
    pending_review = "pending_review"


#: The two members core/creative-asset.json's root oneOf discriminates on, one per arm.
_FORMAT_IDENTIFIERS: tuple[str, str] = ("format_id", "format_kind")


class CreativeAssetRequest(LibraryCreativeAsset):
    """The item type sync_creatives ACCEPTS, per core/creative-asset.json.

    Deliberately NOT ``Creative`` below, which extends the list_creatives RESPONSE model.
    This field used to point there, so the request shape was a response shape. Once MCP and
    REST both derived their accepted shape from this DTO, that became enforced on both, and
    it cost real capability:

      * seven SPEC-LEGAL request fields could not be sent -- inputs, format_kind,
        format_option_ref, weight, placement_refs, placement_ids, industry_identifiers.
        ``inputs`` is IMPLEMENTED (tools/creatives/_assets.py reads inputs[0].
        context_description as the generative prompt fallback), so a shipped feature was
        unreachable on two of three transports.
      * ``assets`` is spec-REQUIRED and the response model made it optional, so a payload
        omitting it cleared the boundary and failed late and differently per transport:
        mid-pipeline VALIDATION_ERROR on mcp/rest, silently defaulted to {} on a2a.

    The old docstring called the response model "richer". It is -- in RESPONSE fields, while
    missing REQUEST ones, which is the whole defect in one word.

    THE ONEOF, FLATTENED. core/creative-asset.json identifies a creative by ``format_id`` OR
    by ``format_kind``. The SDK models that: datamodel-codegen renders two classes with
    identical field sets, differing only in which identifier each requires (CreativeAsset1,
    CreativeAsset2), wrapped in a RootModel union. Pydantic's union validation of that type
    IS the oneOf, so re-implementing it would normally be the mistake.

    One thing rules it out, and it is a wire-contract reason rather than a taste one. A
    failing union branch carries its own class name in the pydantic ``loc``, so the buyer
    receives the pointer ``/creatives/0/CreativeAsset1/name``. ``core/error.json`` requires
    ``issues[].pointer`` to address the offending field IN THE REQUEST PAYLOAD, and
    ``CreativeAsset1`` names nothing the buyer sent: it is a codegen artifact that appears
    nowhere in AdCP. The SDK validates correctly and reports the failure unconformantly.
    NOT yet reported upstream -- searched adcontextprotocol/adcp and found no issue for it.

    Flattening onto one branch -- which already carries every field of both -- keeps the
    validation and fixes the pointer. ``format_id`` relaxes to optional, the constraint
    states itself as the ``oneOf`` keyword, and the pointer stays ``/creatives/0``. Extend
    the union type instead on the day its locs address the payload.
    """

    # from_attributes: a subclass of an SDK type must accept INSTANCES of that SDK type.
    # Without it pydantic's model_type check rejects the parent -- so adcp's own
    # CreativeAsset could not be handed to a request model that extends it, and every
    # caller holding a typed SDK object would have to know about our subclass and
    # round-trip through a dict to get past it.
    model_config = ConfigDict(extra=get_pydantic_extra_mode(), from_attributes=True)

    # ``format_id`` is BUILT as the local :class:`FormatId`, which carries
    # core/format-id.json's ``dependencies``, and otherwise is the parent's declaration
    # (``narrowed_from``). An earlier redeclaration here, written to weaken the field to
    # optional, dropped the parent's description and its ``deprecated=True`` marker; keeping
    # the parent's declaration keeps both: ``format_id`` is DEPRECATED in 3.2, the published
    # schema says so, and reading the attribute emits the SDK's DeprecationWarning, which is
    # the correct signal while format_kind is the canonical identifier.
    format_id: FormatIdSlot | None = narrowed_from(LibraryCreativeAsset, "format_id")

    @model_validator(mode="before")
    @classmethod
    def _exactly_one_format_identifier(cls, data: Any) -> Any:
        """core/creative-asset.json is a oneOf: format_id XOR format_kind.

        Raised at the ITEM, which is where a oneOf failure belongs. core/error.json requires
        each entry in ``issues[]`` to carry an RFC 6901 ``pointer`` to the offending field
        and a ``keyword`` "drawn from the JSON Schema vocabulary" -- and for a oneOf, no
        single field is at fault, so the pointer is the object (``/creatives/0``) and the
        keyword is ``oneOf``. The schema even provides ``issues[].discriminator`` for naming
        the variant.

        So this deliberately does NOT attach a loc pointing at ``format_id``. That would
        report a field the buyer never had to send, because the format_kind branch is
        equally legal.

        The error TYPE is the keyword. A plain ``ValueError`` becomes pydantic's
        ``value_error``, which ``PYDANTIC_KEYWORD_MAP`` classifies as ``None`` -- "no honest
        JSON Schema keyword" -- so the issue was dropped from ``issues[]`` and the buyer got
        an envelope with no structured reason. This rejection CAN be substantiated as
        ``oneOf``, so it says so (see ``SELLER_RAISED_KEYWORDS``).

        ``mode="before"``, and that is the whole reason this runs at all. The generated
        parent carries ``_require_schema_required_group``, the "at least one" half of the
        same oneOf, as an AFTER validator raising a plain ``ValueError``:

            CreativeAsset requires at least one of these field groups: format_id | format_kind

        Pydantic runs the parent's after-validators before the subclass's, so for the case
        that matters most -- a creative carrying NEITHER identity -- the parent failed first
        and the buyer got ``value_error`` with an empty ``issues[]``. Measured: four
        integration rows asserted ``oneOf`` and read ``value_error`` or nothing. A before
        validator precedes every after validator in the hierarchy, so the keyword-bearing
        refusal is the one the buyer sees, and the parent's is unreachable rather than
        suppressed.
        """
        # The raw payload on the wire path, an instance on the model-to-model path
        # (``from_attributes=True``). Both answer "which identity did the caller supply".
        #
        # SUPPLIED MEANS PRESENT, NULL INCLUDED. Each arm of the pinned oneOf is a ``required``
        # plus a ``not: {required: [...]}``, and JSON Schema's ``required`` is satisfied by the
        # key being there whatever its value -- so ``{format_kind: "image", format_id: null}``
        # names the legacy identifier and matches neither arm. Counting ``is not None`` let it
        # through. A model instance answers the same question with ``model_fields_set``,
        # which records an explicit null and omits a defaulted field. The one identifier
        # present must also carry a value: a lone ``format_id: null`` satisfies the v1 arm's
        # ``required`` and fails its type (core/format-id.json is an object), so it matches
        # no arm either.
        if isinstance(data, Mapping):
            present = [name for name in _FORMAT_IDENTIFIERS if name in data]
            valued = [name for name in present if data[name] is not None]
        elif isinstance(data, BaseModel):
            present = [name for name in _FORMAT_IDENTIFIERS if name in data.model_fields_set]
            valued = [name for name in present if getattr(data, name) is not None]
        else:
            present = valued = [name for name in _FORMAT_IDENTIFIERS if getattr(data, name, None) is not None]
        if len(present) != 1 or present != valued:
            raise PydanticCustomError("oneOf", "provide exactly one of format_id or format_kind")
        return data

    @model_validator(mode="after")
    def _trackers_bind_tracking_events(self) -> "CreativeAssetRequest":
        """The two tracker-asset rules the pin states beyond what the SDK renders.

        core/assets/vast-tracker-asset.json and daast-tracker-asset.json bind ``vast_event`` /
        ``daast_event`` with ``not: {enum: [impression, clickTracking, ...]}`` -- an Impression
        URL "MUST be modeled as a url asset with url_type tracker_pixel", the others belong to
        VideoClicks, Error and ViewableImpression -- and require ``offset`` when the event is
        ``progress`` (an ``allOf if/then``). datamodel-codegen renders the event as the whole
        tracking-event enum and cannot express either, so the accepted shape states them here,
        as the schema violations they are (INVALID_REQUEST). ``target`` needs nothing: the SDK
        types it as the pin's two-member enum.

        Raised as pydantic errors, like the oneOf above, so the boundary's one mapping
        turns them into the INVALID_REQUEST envelope with ``issues[]``: the refused event is
        an ``enum`` claim (the value is outside the set this field accepts), the missing
        offset a ``required`` one (pydantic's ``missing``).
        """
        for slot, value in (self.assets or {}).items():
            for asset in value if isinstance(value, list) else [value]:
                inner = getattr(asset, "root", asset)
                asset_type = getattr(inner, "asset_type", None)
                if asset_type not in ("vast_tracker", "daast_tracker"):
                    continue
                event_field = "vast_event" if asset_type == "vast_tracker" else "daast_event"
                event = getattr(inner, event_field, None)
                event = getattr(event, "value", event)
                if event in _NON_TRACKING_EVENTS:
                    raise PydanticCustomError(
                        "enum",
                        "assets.{slot}.{field}: {event} is not a TrackingEvents event",
                        {"slot": slot, "field": event_field, "event": event, "expected": "a TrackingEvents event"},
                    )
                if event == "progress" and getattr(inner, "offset", None) is None:
                    raise PydanticCustomError(
                        "missing",
                        "assets.{slot}.offset is required when {field} is progress",
                        {"slot": slot, "field": event_field},
                    )
        return self


#: Events the pinned tracker assets refuse (vast-tracker-asset.json / daast-tracker-asset.json
#: ``not: {enum: [...]}``): each belongs to another VAST/DAAST element, not TrackingEvents.
_NON_TRACKING_EVENTS: frozenset[str] = frozenset(
    {
        "impression",
        "clickTracking",
        "customClick",
        "error",
        "viewable",
        "notViewable",
        "viewUndetermined",
        "measurableImpression",
        "viewableImpression",
    }
)


class SyncCreativeAsset(CreativeAssetRequest):
    """The item ``sync_creatives`` accepts: core/creative-asset.json plus the sync request's ``revision_id``.

    creative/sync-creatives-request.json (v3.2.7) declares ``creatives[]`` as creative-asset.json
    AND an object adding ``revision_id``, and says of it: "Buyers may send it to any 3.2 peer;
    creative.supports_revisions gates reliance on immutability, echo, readback, and delivery
    correlation, not whether the open request shape may carry the field." So the field is
    accepted here, as the pin declares it (``narrowed_from`` keeps the pin's own declaration),
    and only on the sync item: a package's inline creative is creative-asset.json alone.

    This seller does not advertise ``creative.supports_revisions``, so the id is neither
    echoed nor read back. Its one reader is the representation-selection identity check
    (``check_representation_selection``): "When an item carries ``representation_selection``,
    its ``creative_id`` and ``revision_id`` must equal the selection lineage"
    (docs/creative/task-reference/sync_creatives.mdx).

    ``localization``, the item's other member, stays undeclared: this seller does not advertise
    ``creative.localization``, which the pin makes a precondition of accepting it.
    """

    revision_id: CreativeRevisionId | None = narrowed_from(LibrarySyncCreative, "revision_id")


# --- Creative Lifecycle ---
class Creative(LibraryCreative):
    """Individual creative asset - extends listing Creative with internal workflow fields.

    the pinned SDK listing Creative fields (public):
    - Required: creative_id, format_id, name, status, created_date, updated_date
    - Optional: assets, assignments, catalogs, tags, performance, account, sub_assets

    Internal fields (excluded from AdCP responses, used for workflow/DB):
    - principal_id: associates creative with advertiser principal
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    # === Overrides of listing Creative fields ===
    # ``name`` is INHERITED: the pinned listing Creative declares the same required
    # ``str``. The redeclaration's only remaining effect was to publish "Creative name"
    # instead of the pin's "Human-readable creative name".
    status: CreativeStatus = Field(
        default=CreativeStatus.pending_review,
        description="Workflow approval status",
    )
    # AwareDatetime, matching the pin: a naive value is schema-invalid here.
    created_date: AwareDatetime = Field(default_factory=lambda: datetime.now(tz=UTC), description="Creation timestamp")
    updated_date: AwareDatetime = Field(default_factory=lambda: datetime.now(tz=UTC), description="Update timestamp")
    # assets is INHERITED as the library's typed asset map. It used to be redeclared as
    # dict[str, Any] because the JSON column stores what the buyer sent; the row-to-model
    # read (listing.py) now validates the stored value into the typed map instead.

    @field_validator("assets", mode="before")
    @classmethod
    def _adopt_sibling_assets(cls, v: Any) -> Any:
        """A sync request carries the SIBLING generated ``Assets`` list; this field is the listing's.

        The pinned ``core/creative-asset.json`` list shape is generated twice -- under the
        sync input as ``core.creative_asset.Assets`` and under the listing response as
        ``list_creatives_response.Assets`` -- as two ``RootModel`` classes over one list.
        Pydantic validates a model-typed slot by instance, so the sync input's instance
        would be refused here. Its ``root`` is the list the two share, and validating that
        list builds this field's own class: a model-to-model step, never a dump.

        This is NOT the shape ``provenance`` had below. There the two classes were the
        library's and a local subclass of it with nothing added, so the fix was to delete
        the subclass and type the field with the pinned class. Here both classes are the
        library's own, generated twice from one schema, so there is no local class to
        delete and the adoption is irreducible.
        """
        if isinstance(v, dict):
            return {key: item.root if isinstance(item, RootModel) else item for key, item in v.items()}
        return v

    # === AI Provenance (EU AI Act Article 50) ===
    # NOT DECLARED HERE, and the pin is why. ``provenance`` belongs to the REQUEST item:
    # ``core/creative-asset.json`` declares it, and the listing item this class extends
    # (``creatives[]`` of list-creatives-response.json, 28 properties, measured on that node)
    # does not. The field appeared on the pinned bundle's nodes 81 times and on none of them
    # was it this one -- which is exactly why a substring search over the bundle cannot
    # settle a question about a model.
    #
    # It existed here to carry a value two lines: ``_validate_creative_input`` copied it off
    # the ``CreativeAssetRequest`` so ``check_provenance_policy`` could read it back. That
    # check now reads the request item directly, which is both where the pin puts the field
    # and where the value came from. Persistence never went through here either -- the stored
    # document is built from the request item in ``_assets.py``.
    #
    # The cost of the declaration was not the copy. A field on this class is a field in the
    # ADVERTISED list_creatives shape (``schema_validation.create_schema_registry`` serves
    # it), so a buyer was shown a response property the spec does not define and could not
    # validate against the pin.

    # === Internal Fields (excluded from AdCP responses) ===
    principal_id: str | None = Field(
        default=None, exclude=True, description="Associates creative with advertiser (workflow tracking)"
    )

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


class SyncCreativesRequest(BuyerRequest, LibrarySyncCreativesRequest):
    """Extends library SyncCreativesRequest with local Creative type.

    Library provides: account_id, assignments, context, creative_ids, creatives,
    delete_missing, dry_run, ext, push_notification_config, validation_mode — all
    inherited from AdCP spec.

    Local overrides:
    - creatives: list[SyncCreativeAsset] -- the SPEC's request item type (creative-asset.json
      plus the sync item's revision_id). This used to be list[Creative], the response model,
      on the reasoning that it was "richer"; it is richer in response fields while missing
      seven request ones. See CreativeAssetRequest.
    """

    TAGS: ClassVar[tuple[str, ...]] = (
        "creative",
        "sync",
        "library",
        "adcp",
        "spec",
    )

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    # Narrowed to the local class; see CreateMediaBuyRequest in _base.py.
    push_notification_config: PushNotificationConfig | None = None

    # account and idempotency_key are REQUIRED by AdCP.1
    # (creative/sync-creatives-request.json /required = [idempotency_key, account,
    # creatives]) and are inherited as required. The removed override claimed
    # idempotency_key is "generated at the transport boundary when not supplied" -- which the
    # code did not do for this tool, and which cannot work in principle: the spec makes the
    # key CLIENT-generated precisely so a retry after a lost response carries the SAME key.
    # A server-generated key differs on every retry, so it provides no at-most-once guarantee
    # at all, and a sync retried after a timeout creates the creatives twice.

    creatives: list[SyncCreativeAsset] = Field(
        ..., min_length=1, max_length=100, description="Array of creative assets to sync (create or update)"
    )  # type: ignore[assignment]

    @field_validator("creatives", mode="before")
    @classmethod
    def _creatives_as_sync_items(cls, value: Any) -> Any:
        """Build an in-process caller's ``CreativeAssetRequest`` as the sync item, by its SUPPLIED fields.

        ``create_media_buy`` and ``update_media_buy`` hand a package's inline creatives over as
        ``CreativeAssetRequest`` instances. ``from_attributes`` alone would read every attribute,
        marking each default as sent; ``build_as_local`` reads ``model_fields_set``, so a creative
        means here what it meant on the instance (``selected_output_digest`` digests exactly what
        was sent).
        """
        if not isinstance(value, list):
            return value
        return [
            build_as_local(SyncCreativeAsset, item) if isinstance(item, CreativeAssetRequest) else item
            for item in value
        ]

    @model_validator(mode="after")
    def _delete_missing_needs_the_whole_library(self):
        """Refuse delete_missing together with creative_ids, as the pin says to.

        creative/sync-creatives-request.json @ ``delete_missing``: "Invalid
        when creative_ids is provided -- delete_missing applies to the entire library
        scope, not a filtered subset." A request that says both is malformed as a whole,
        which is INVALID_REQUEST; nothing about it is a per-creative outcome.
        """
        from src.core.exceptions import AdCPInvalidRequestError

        if self.delete_missing and self.creative_ids:
            raise AdCPInvalidRequestError(field="delete_missing")
        return self


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

    the pinned SDK re-added assigned_to, assignment_errors, platform_id, status,
    changes and warnings to the library parent, so all six are now INHERITED (they were
    locally redeclared under SDK 5.7 which had dropped them). See PR #1567.

    Internal-only (not in AdCP spec, excluded from responses):
    - internal_status: review-routing state (renamed off the inherited spec `status`)

    ``review_feedback`` IS GONE. ``sync-creatives-response.json``'s result item does not
    declare it -- measured on the node -- and it was write-only: all five construction
    sites in ``src/core/tools/creatives/`` passed the literal ``None``, and no site in
    ``src/``, ``scripts/`` or ``tests/`` ever read it. ``Field(exclude=True)`` meant the
    attribute was its only observable, so a field that was never set and never read looked
    identical to one doing its job. The pin's channel for per-result feedback is the
    inherited ``errors``/``warnings`` arrays.
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    # action is inherited from the the pinned SDK parent as the StrEnum CreativeAction. Every value
    # production emits (created/updated/unchanged/failed/deleted — as strings or enum members) is
    # a valid CreativeAction, and StrEnum coerces the strings, so no override or normalizer is needed.

    # the pinned SDK re-added assigned_to/assignment_errors/platform_id/status/changes/
    # warnings to the library parent (PR #1567, shrinking the schema-inheritance
    # allowlist). Our former local `status` held internal review-routing state, not the spec's
    # advisory CreativeStatus, so it is renamed to `internal_status` (excluded from the wire)
    # rather than shadowing the inherited spec field. The spec `status` is DERIVED from it in
    # `_advisory_status_from_review_state` below: the row's review state is exactly the
    # "advisory review-lifecycle state of the creative after this sync" the pin describes.
    # An unset status is omitted on every transport: the library base dumps with
    # exclude_none, and MCP serializes through the same model_dump.
    # platform_id/assigned_to/assignment_errors/changes/warnings/errors are inherited as-is,
    # with the parent's None defaults: an optional array the tool did not populate is
    # OMITTED by exclude_none on every path (the wire serializer runs on model_dump,
    # model_dump_json and structured_content alike), which is the spec-valid absence.
    # Writers materialize the list before appending (_append_warning in _sync.py).
    internal_status: str | None = Field(
        None, exclude=True, description="Internal review-routing status (INTERNAL - excluded from AdCP responses)"
    )

    @model_validator(mode="after")
    def _advisory_status_from_review_state(self) -> "SyncCreativeResult":
        """The spec ``status`` is the row's review state, on the actions that have one.

        sync-creatives-response.json: ``status`` is the "advisory review-lifecycle state of
        the creative after this sync", drawn from CreativeStatus, and "MUST be omitted when
        action is failed or deleted (the creative has no meaningful review state -- failure
        details belong in the errors array; deleted creatives are gone from the library)".
        ``internal_status`` IS that state -- every value the creatives row holds is a
        CreativeStatus member, pinned by test_architecture_creative_status_vocabulary -- so
        the wire field is derived here once rather than at the three sites that build a
        result. A failed or deleted result carries no row state and its status stays unset,
        which the exclude_none serialization then omits.
        """
        if self.action in ("failed", "deleted"):
            self.status = None
        elif self.status is None and self.internal_status is not None:
            self.status = CreativeStatus(self.internal_status)
        return self


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


class SyncCreativesResponse(NestedModelSerializerMixin, LibrarySyncCreativesSuccess, SalesAgentAdcpResponse):
    """Extends library SyncCreativesResponse success variant.

    the pinned SDK: SyncCreativesResponse is now a union TypeAlias (not RootModel).
    Since the error variant is never constructed (ToolError handles failures),
    we subclass the success variant directly.

    ``ProtocolEnvelope`` IS INHERITED HERE AS A LOCAL WORKAROUND, and it should not have to
    be. ``creative/sync-creatives-response.json`` composes the envelope into the whole
    response with ``allOf``, so every branch carries its eleven fields -- but the SDK's generated
    ``SyncCreativesResponse1`` (this class's parent) inherits ``AdcpVersionEnvelope`` alone.
    At the pinned SDK that is true of 19 of the 24 ``*SuccessResponse`` aliases: the five that DO
    inherit are the ones whose response schema has no ``oneOf``, so the alias resolves to the
    root class and picks the base up from the root ``allOf``. Filed upstream as
    adcontextprotocol/adcp-client-python#1136.

    Without this base, nine of the eleven envelope fields are untyped here and reach the wire
    only as pydantic extras -- ``replayed`` among them, which is why a replayed sync used to
    go out with no marker at all. Delete this base the day the SDK's success branches compose the
    envelope; ``ProtocolEnvelope`` is exported and correct, only the branches fail to inherit it.

    the pinned SDK restored the fields SDK 5.7 had collapsed off the success envelope:
    dry_run, context (ContextObject|None) and ext (ExtensionObject|None) are all
    INHERITED from the parent again (identical definitions; production may assign
    raw dicts to context/ext, which Pydantic coerces on validation). Only
    ``creatives`` remains overridden — see below.

    Design decision : error variant never constructed.
    """

    # Protocol-envelope `status` comes from ProtocolEnvelope (composed above):
    # REQUIRED on every task response envelope, a sibling field at the MCP/REST wire
    # root (not nested under a "payload" key). This class only ever represents a
    # synchronously-completed sync (the error/submitted branches are never constructed
    # here — see class docstring), so "completed" is invariant. It is carried on the
    # response rather than by a wrapper that never actually ran (GH #1710), and this
    # is a TEMPORARY adoption: the library parent has no status field at all, so the
    # mixin deletes as a no-op once adcp ships it.

    # Override creatives to use our SyncCreativeResult (Pattern #4: nested serialization).
    # Library parent uses its Creative type which lacks assigned_to, assignment_errors, etc.
    # Required (no default): pinned 3.1 SyncCreativesSuccess.required=['creatives'] — a
    # synchronously-processed sync always carries a creatives array, even all-failed
    # (#1399 R3-F2).
    creatives: list[SyncCreativeResult]  # type: ignore[assignment]


class ListCreativeFormatsRequest(BuyerRequest, LibraryListCreativeFormatsRequest):
    """Extends library ListCreativeFormatsRequest from AdCP spec.

    Inherits all AdCP-compliant fields from adcp library,
    ensuring we stay in sync with spec updates.
    """

    TAGS: ClassVar[tuple[str, ...]] = (
        "creative",
        "formats",
        "specs",
        "discovery",
        "adcp",
    )

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    # Narrowed to the local class, which carries core/format-id.json's ``dependencies``.
    format_ids: list[FormatIdSlot] | None = narrowed_from(LibraryListCreativeFormatsRequest, "format_ids")
    input_format_ids: list[FormatIdSlot] | None = narrowed_from(LibraryListCreativeFormatsRequest, "input_format_ids")
    output_format_ids: list[FormatIdSlot] | None = narrowed_from(LibraryListCreativeFormatsRequest, "output_format_ids")

    @field_validator("disclosure_positions", "disclosure_persistence", mode="before")
    @classmethod
    def _refuse_duplicate_disclosure_members(cls, v: Any) -> Any:
        """Both disclosure filters declare ``uniqueItems: true`` — the generated model dedupes.

        3.2.1 list-creative-formats-request.json puts ``uniqueItems: true`` on
        ``disclosure_positions`` AND ``disclosure_persistence``, in both role variants
        (``media-buy/`` and ``creative/``). The generated model does not express it: the pinned SDK's
        ``types._ergonomic._patch_unique_enum_list`` attaches a ``WrapValidator`` whose
        ``coerce_to_unique_enum_list`` SILENTLY DEDUPES (``list(dict.fromkeys(...))``), so
        ``["prominent", "prominent"]`` arrived as ``["prominent"]`` and a buyer's schema
        violation became a quiet rewrite. A constraint the pin states and the generated model
        drops is asserted on the DTO — the one shape every transport validates into — the same
        way :meth:`ListCreativesRequest._account_required_when_pricing_requested` asserts an
        if/then the generator cannot carry.

        ``mode="before"`` so the raw array is seen ahead of that dedupe. Members are
        ``StrEnum`` values, so a mixed spelling (``["prominent", DisclosurePosition.prominent]``)
        hashes equal and is caught too; an unhashable member is left for the enum validator to
        refuse on type rather than being reported as a duplicate.
        """
        if isinstance(v, list):
            try:
                distinct = len(dict.fromkeys(v))
            except TypeError:
                return v
            if distinct != len(v):
                raise PydanticCustomError("unique_list", "members must be unique")
        return v


class ListCreativeFormatsResponse(
    NestedModelSerializerMixin, LibraryListCreativeFormatsResponse, SalesAgentAdcpResponse
):
    """Extends library ListCreativeFormatsResponse from AdCP spec.

    Inherits all AdCP-compliant fields from adcp library,
    ensuring we stay in sync with spec updates.

    Adds NestedModelSerializerMixin for proper nested model serialization
    and custom __str__ for human-readable protocol messages.

    Per AdCP PR #113, this response contains ONLY domain data.
    Protocol fields (status, task_id, message, context_id) are added by the
    protocol layer (MCP, A2A, REST) via ProtocolEnvelope wrapper.
    """

    @field_validator("formats", mode="before")
    @classmethod
    def _parse_as_local_format(cls, v: Any) -> Any:
        """Parse each format as the local Format, not the inherited library Format.

        The library Format loses canonical_parameters.format_kind and .params (see
        ProductFormatDeclaration), so a response re-parsed from its own wire dropped both.
        The field is not redeclared as ``list[Format]`` because list is invariant and mypy
        refuses the narrowing; a local Format is a library Format, so the inherited field
        keeps the instance and NestedModelSerializerMixin serializes it by instance.

        A dict is read as :class:`PeerFormat`: this response carries a peer creative agent's
        formats, and those may hold closed-enum members the pin does not model, which the
        wire re-emits. Reading that wire back strictly would refuse a document this seller
        itself produced.
        """
        if isinstance(v, list):
            return [PeerFormat.model_validate(f) if isinstance(f, dict) else f for f in v]
        return v


class CreativeFilters(LibraryCreativeFilters):
    """``core/creative-filters.json``, with ``format_ids`` narrowed to the local
    :class:`FormatId`, which carries core/format-id.json's ``dependencies``."""

    format_ids: list[FormatIdSlot] | None = narrowed_from(LibraryCreativeFilters, "format_ids")


class ListCreativesRequest(BuyerRequest, LibraryListCreativesRequest):
    """Extends library ListCreativesRequest from AdCP spec.

    Every spec field is inherited from the library parent and none is redeclared;
    3.1.1's list-creatives-request.json carries filters, sort, pagination, fields,
    account, context, ext and the ``include_*`` projection flags. The list this
    docstring used to enumerate named ``include_performance`` and
    ``include_sub_assets`` among them, which stopped being true at the pinned SDK when
    both were removed from the spec — a reader checking "is this field spec'd?"
    against the docstring got the wrong answer for two years' worth of SDK bumps.
    Enumerate nothing: the parent is the list.

    No internal field is declared here. ``format`` and ``page`` were, under
    ``exclude=True``; they live on :class:`ListCreativesRequest` below. See
    docs/development/building-tools.md.
    """

    TAGS: ClassVar[tuple[str, ...]] = (
        "creative",
        "library",
        "search",
        "adcp",
        "spec",
    )

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    filters: Annotated[LibraryCreativeFilters, as_local(CreativeFilters)] | None = narrowed_from(
        LibraryListCreativesRequest, "filters"
    )

    @model_validator(mode="after")
    def _account_required_when_pricing_requested(self) -> "ListCreativesRequest":
        """``include_pricing: true`` requires ``account`` — the request schema's own condition.

        3.1.1 list-creatives-request.json carries it as an allOf branch (``if``
        include_pricing is ``const: true``, ``then required: [account]``), and
        include_pricing's own description repeats it: "Requires account to be provided."
        A generated model cannot express a JSON-Schema if/then, so the condition is
        asserted on the DTO — the one shape every transport validates into — rather than
        in a tool body, where the other two transports would each need their own copy.
        """
        if self.include_pricing and self.account is None:
            raise ValueError("account is required when include_pricing is true (pricing comes from its rate card)")
        return self


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


class ListCreativesResponse(NestedModelSerializerMixin, LibraryListCreativesResponse, SalesAgentAdcpResponse):
    """Extends library ListCreativesResponse with local subtypes.

    Library provides: context, creatives, ext, format_summary, pagination,
    query_summary, status_summary — all inherited from AdCP spec.

    Local overrides nested types to ensure correct dict-to-model parsing
    (Pydantic needs the local type annotations for local subtypes).
    Other fields (format_summary, status_summary, context, ext) inherited.
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    # Override with local subtypes (each extends its library counterpart)
    query_summary: QuerySummary = Field(..., description="Summary of the query that was executed")  # type: ignore[assignment]
    pagination: Pagination = Field(..., description="Pagination information for navigating results")
    creatives: list[Creative] = Field(..., description="Array of creative assets")


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


class CreativeApproval(LibraryGetMediaBuysCreativeApproval):
    """Creative approval record for a package.

    All three fields — creative_id, approval_status, rejection_reason — match the
    library type's exactly, so they are inherited rather than copied (Pattern #1).
    The local ApprovalStatus enum this class used to reference has the same three
    members as the library's CreativeApprovalStatus and both are StrEnums, so
    existing call sites keep working: their members validate by value.
    """
