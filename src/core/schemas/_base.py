# SDK 5.7 type:ignore tracking (adcontextprotocol/adcp-client-python#913):
# - [misc] on lines ~339, ~415: UpdateMediaBuySuccess/Error class defs.
#   Pydantic metaclass interaction in SDK hierarchy; permanent.
# - [assignment] on lines ~1449, ~1450, ~1637, ~1638: account/idempotency_key
#   overrides (required -> optional). Architectural; permanent.

import copy
import re
import warnings
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

# --- V2.3 Pydantic Models (Bearer Auth, Restored & Complete) ---
# --- MCP Status System (AdCP PR #77) ---
from enum import StrEnum
from functools import cache
from typing import TYPE_CHECKING, Annotated, Any, ClassVar, Literal, Self, TypeAlias, cast, get_args

from src.core.enum_helpers import enum_value

if TYPE_CHECKING:
    from pydantic.fields import FieldInfo

    from src.core.schemas.creative import CreativeAssetRequest

from adcp import Error as _LibraryError
from adcp.types import AccountReference as LibraryAccountReference
from adcp.types import BrandReference as LibraryBrandReference
from adcp.types import (
    ContextObject,
    # DeliveryStatus / MediaBuyStatus are no longer referenced by a declaration in
    # this module — Snapshot and GetMediaBuysMediaBuy inherit them from their library
    # bases — but src.core.schemas re-exports this module with `import *`, so dropping
    # them here silently breaks every `from src.core.schemas import MediaBuyStatus`
    # caller. Unit tests do not catch that; a collection ImportError in the
    # integration suite is how it surfaced.
    DeliveryStatus,  # noqa: F401 — re-exported via src.core.schemas
    MediaBuyStatus,  # noqa: F401 — re-exported via src.core.schemas
    PriceGuidance,  # Replaces local PriceGuidance class
    PricingModel,  # Replaces local PricingModel enum (lowercase members: .cpm, .cpc, etc.)
    ProtocolEnvelope,
)
from adcp.types import CreateMediaBuyRequest as LibraryCreateMediaBuyRequest

# Import main request/response types from stable API
from adcp.types import Format as LibraryFormat

# Import types from stable API (per adcp 2.7.0+)
from adcp.types import FormatId as LibraryFormatId
from adcp.types import GetAdcpCapabilitiesRequest as LibraryGetAdcpCapabilitiesRequest
from adcp.types import GetAdcpCapabilitiesResponse as LibraryGetAdcpCapabilitiesResponse
from adcp.types import GetMediaBuysRequest as LibraryGetMediaBuysRequest
from adcp.types import GetMediaBuysResponse as LibraryGetMediaBuysResponse
from adcp.types import GetTaskStatusResponse as LibraryGetTaskStatusResponse
from adcp.types import ListTasksRequest as LibraryListTasksRequest
from adcp.types import ListTasksResponse as LibraryListTasksResponse
from adcp.types import PackageRequest as LibraryPackageRequest

# Import types from stable API (per adcp 2.9.0+ - all types now in stable)
# Note: AffectedPackage was removed in 2.9.0, use Package instead
from adcp.types import PackageUpdate as LibraryPackageUpdate
from adcp.types import UpdateMediaBuyRequest as LibraryUpdateMediaBuyRequest
from adcp.types.aliases import (
    CreateMediaBuyErrorResponse as AdCPCreateMediaBuyError,
)
from adcp.types.aliases import (
    CreateMediaBuySubmittedResponse as AdCPCreateMediaBuySubmitted,
)
from adcp.types.aliases import (
    CreateMediaBuySuccessResponse as AdCPCreateMediaBuySuccess,
)
from adcp.types.aliases import Package as AdCPPackage
from adcp.types.aliases import (
    UpdateMediaBuyErrorResponse as AdCPUpdateMediaBuyError,
)
from adcp.types.aliases import (
    UpdateMediaBuySubmittedResponse as AdCPUpdateMediaBuySubmitted,
)
from adcp.types.aliases import (
    UpdateMediaBuySuccessResponse as AdCPUpdateMediaBuySuccess,
)
from adcp.types.base import AdCPBaseModel as LibraryAdCPBaseModel
from adcp.types.generated_poc.core.version_envelope import AdcpVersionEnvelope
from adcp.types.generated_poc.enums.creative_approval_status import (
    # Aliased to OUR name for this concept, not to the library's. Two reasons, both
    # load-bearing: src.core.schemas already exports an unrelated
    # ``CreativeApprovalStatus`` (creative.py:252, a per-creative result model), so an
    # unaliased import would shadow it depending on star-import order; and the
    # schema-inheritance guard reads ``Library<X>`` as "the local class X must be or
    # extend this", which for ``LibraryCreativeApprovalStatus`` would point it at that
    # unrelated model. ``LibraryApprovalStatus`` states the true relationship: this is
    # the library type behind our ``ApprovalStatus``.
    CreativeApprovalStatus as LibraryApprovalStatus,
)
from adcp.types.generated_poc.enums.media_buy_valid_action import (
    MediaBuyValidAction,  # noqa: F401 — re-exported via src.core.schemas
)
from adcp.types.generated_poc.enums.snapshot_unavailable_reason import (
    SnapshotUnavailableReason as LibrarySnapshotUnavailableReason,
)
from adcp.types.generated_poc.enums.task_status import TaskStatus as LibraryTaskStatus
from adcp.types.generated_poc.media_buy.get_media_buys_response import (
    MediaBuy as LibraryGetMediaBuysMediaBuy,
)
from adcp.types.generated_poc.media_buy.get_media_buys_response import (
    Package as LibraryGetMediaBuysPackage,
)
from adcp.types.generated_poc.media_buy.get_media_buys_response import (
    Snapshot as LibraryGetMediaBuysSnapshot,
)
from adcp.types.generated_poc.protocol.get_task_status_request import (
    GetTaskStatusRequest as LibraryGetTaskStatusRequest,
)
from adcp.types.generated_poc.protocol.list_tasks_response import Task as LibraryTaskSummary

from src.core.config import get_pydantic_extra_mode
from src.core.errors.codes import CODE_TABLE, ErrorCodeT
from src.core.errors.details import ErrorDetails
from src.core.exceptions import (
    AdCPInvalidRequestError,
    AdCPNotFoundError,
    AdCPSalesAgentError,
    _details_to_wire,
)
from src.core.schemas.notification import PushNotificationConfig

# For backward compatibility, alias AdCPPackage as LibraryPackage
LibraryPackage: TypeAlias = AdCPPackage  # noqa: UP040 — runtime re-export used as base class
# Simple types that match library exactly
# V3: Structured geo targeting types
from adcp.types import ActivateSignalRequest as LibraryActivateSignalRequest

# AdCP creative types for schema definitions
from adcp.types import CreativePolicy as LibraryCreativePolicy
from adcp.types import FrequencyCap as LibraryFrequencyCap
from adcp.types import GetSignalsRequest as LibraryGetSignalsRequest
from adcp.types import GetSignalsResponse as LibraryGetSignalsResponse
from adcp.types import Measurement as LibraryMeasurement
from adcp.types import PlatformDeployment as LibraryPlatformDeployment
from adcp.types import Property as LibraryProperty
from adcp.types import Signal as LibrarySignal
from adcp.types import SignalFilters as LibrarySignalFilters
from adcp.types import TargetingOverlay

# The adcp SDK generates CollectionListReference and uses it on TargetingOverlay.collection_list /
# collection_list_exclude, but does not re-export the type from the public adcp.types namespace
# (PropertyListReference is exported, CollectionListReference is not — likely a codegen oversight).
# We import from the internal generated path so our isinstance checks and type annotations match
# the library's TargetingOverlay field types exactly. Track upstream:
# https://github.com/adcontextprotocol/adcp-client-python — when CollectionListReference is added
# to adcp.types, switch this import to the public path.
from adcp.types.generated_poc.core.collection_list_ref import (
    CollectionListReference,  # noqa: F401 — re-exported via src.core.schemas; used by callers and TargetingOverlay.collection_list type
)
from annotated_types import MaxLen, MinLen
from pydantic import (
    AnyUrl,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    Tag,
    TypeAdapter,
    model_serializer,
    model_validator,
)
from pydantic_core import InitErrorDetails, PydanticCustomError
from pydantic_core import ValidationError as PydanticCoreValidationError

# The pricing option types (nine local member subclasses, the AdCPPricingOption
# union, and the PricingOption wrapper) live in src.core.schemas.pricing; the
# package __init__ star-exports the members and the union from there.


# Helper function for creating AnyUrl instances (eliminates mypy warnings)


class Error(_LibraryError):
    """Advisory error whose graded fields are FUNCTIONS OF ``code``, not inputs.

    The SDK types ``code`` as ``str`` — correctly, because AdCP 3.1.1 makes the
    error-code vocabulary open. That openness is a wire contract, not a licence
    for this seller to emit a code it never defined. Narrowing the field here
    makes an out-of-vocabulary advisory unconstructible, at every site, whatever
    local name the type is imported under — which an AST guard cannot do, because
    an import alias defeats it.

    ``message``, ``suggestion`` and ``recovery`` are DERIVED from ``CODE_TABLE``
    at validation (ADR-010: a graded wire field is a function of the code, never
    authored at a raise site). Anything a caller passes for those three is
    discarded, so the advisory lane and the raised lane resolve one code to one
    sentence — they previously did not, because the normalizer this replaces
    filled ``recovery``/``suggestion`` only when the caller left them unset and
    never touched ``message`` at all.

    Every other field — ``field``, ``details``, ``retry_after``, ``issues``,
    ``source``, ``sdk_id`` — passes through untouched: they carry the specifics,
    which is exactly where specifics belong.

    The four overrides below exist because ``frozen=True`` alone does NOT close
    this. Measured against pydantic 2.12.5, five separate routes overwrite a
    derived field, and ``frozen`` stops only direct assignment:

        model_copy(update=...)   __replace__(...)   model_construct(...)
        setattr                  __dict__ poke

    ``validate_assignment`` does not help either — it re-runs FIELD validators,
    not this ``mode="before"`` MODEL validator. So each mutating entry point is
    redefined as a RE-VALIDATION: a copy re-derives from the table rather than
    poking a field, which also means ``model_copy(update={"code": X})`` correctly
    yields X's message rather than carrying the old one. This is what makes the
    defect unconstructible instead of merely detectable; the alternative
    considered was an AST guard banning ``model_copy``, which would have covered
    one of the five routes and could never have fired on ``AdCPSalesAgentError`` at all
    (it is not a pydantic model).
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode(), frozen=True)

    # The parent's length constraints are carried forward rather than dropped: the
    # narrowing is on the VOCABULARY axis only, so re-declaring must not quietly
    # relax MinLen/MaxLen that the SDK's ``code: str`` carries.
    code: Annotated[ErrorCodeT, MinLen(1), MaxLen(64)]

    @model_validator(mode="before")
    @classmethod
    def _derive_graded_fields(cls, data: Any) -> Any:
        """Resolve ``message``/``suggestion``/``recovery`` from the code table.

        Returns a NEW mapping rather than mutating ``data`` in place: pydantic
        hands this validator the caller's own dict by reference (and, for a list
        field, each item dict), so mutating it would edit the caller's object.
        """
        if not isinstance(data, dict):
            return data
        entry = CODE_TABLE.get(cast("ErrorCodeT", data.get("code")))
        if entry is None:
            # Unknown code: leave it alone and let ``code: ErrorCodeT`` produce
            # the validation error. Deriving here would mask it with a KeyError.
            return data
        return {
            **data,
            "message": entry.message,
            "suggestion": entry.suggestion,
            "recovery": entry.recovery.value,
        }

    @classmethod
    def of(
        cls,
        code: ErrorCodeT,
        *,
        field: str | None = None,
        details: ErrorDetails | None = None,
        retry_after: int | None = None,
    ) -> "Error":
        """Build an advisory. THE construction surface for one.

        There is no ``message``/``suggestion``/``recovery`` parameter, so a call site
        cannot author one -- not "should not", cannot: the name does not exist to pass.
        Those three are resolved from ``code`` by the validator below.

        This exists instead of giving ``message`` a default on this subclass. A default
        would have worked at runtime (the before-validator injects the field anyway) and
        was only needed to stop mypy demanding the argument -- but redeclaring an
        inherited field to make it optional is exactly what
        ``test_architecture_schema_inheritance`` forbids, and it is right to: "widening a
        type, making a required field optional, or substituting an unrelated model are
        all retypes that this guard must keep flagging." A narrower constructor removes
        the parameter without touching the inherited declaration.
        """
        payload: dict[str, Any] = {"code": code}
        if field is not None:
            payload["field"] = field
        if details is not None:
            payload["details"] = details.to_wire()
        if retry_after is not None:
            payload["retry_after"] = retry_after
        return cls.model_validate(payload)

    @classmethod
    def from_exception(cls, exc: AdCPSalesAgentError[Any]) -> "Error":
        """Build an advisory from the error that already knows its own code.

        The EXCEPTION class is the authority on which code an error is, so an
        advisory derived from one needs no code argument: there is nothing for a
        caller to get wrong, and no way to pair a code with a details block
        belonging to a different failure. Detail typing comes along for free,
        because constructing the exception is what mypy checked.

        This replaces the hand-rolled decomposition at the webhook rejection
        path, which spelled the same thing as three separate reads
        (``rejection.error_code``, ``rejection.field``, ``rejection.details``)
        and so could drift from the transport envelope built from the same
        exception.
        """
        payload: dict[str, Any] = {"code": exc.error_code}
        if exc.field is not None:
            payload["field"] = exc.field
        wire_details = _details_to_wire(exc.details)
        if wire_details is not None:
            payload["details"] = wire_details
        if exc.retry_after is not None:
            payload["retry_after"] = exc.retry_after
        return cls.model_validate(payload)

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> "Error":
        """A copy is a RE-VALIDATION: ``update=`` cannot outrank the table."""
        data: dict[str, Any] = {**self.__dict__, **(dict(update) if update else {})}
        return type(self).model_validate(copy.deepcopy(data) if deep else data)

    def __replace__(self, **changes: Any) -> "Error":
        """``copy.replace()`` support (3.13) — same re-validation as model_copy."""
        return self.model_copy(update=changes)

    # ``model_construct`` is pydantic's documented validation BYPASS. A graded model
    # cannot have one: it is route 3 of the five that would otherwise let a caller set
    # message/suggestion/recovery directly. Narrowed to a re-validation. mypy flags the
    # override as a redefinition of the inherited classmethod, which is exactly what it
    # is and what it must be.
    @classmethod  # type: ignore[no-redef]
    def model_construct(cls, _fields_set: set[str] | None = None, **values: Any) -> "Error":  # type: ignore[override]
        """Validation-skipping construction is not available for a graded model."""
        return cls.model_validate(values)


def url(value: str) -> AnyUrl:
    """Convert string to AnyUrl for type-safe URL construction.

    This helper eliminates mypy warnings when passing strings to AnyUrl fields.
    Pydantic's AnyUrl accepts strings at runtime and validates/converts them automatically.

    Usage:
        FormatId(agent_url=url("https://example.com"), id="test")

    Args:
        value: URL string to convert

    Returns:
        AnyUrl instance (auto-validated by Pydantic)
    """
    return AnyUrl(value)  # Pydantic handles string -> AnyUrl conversion


def canonical_agent_url(agent_url: object) -> str:
    """Canonicalize an agent_url for identity comparison (spec MUST canonicalization).

    ``core/format-id.json`` makes canonicalizing ``agent_url`` a MUST before two
    format references may be treated as the same, per the eight-step algorithm at
    ``docs/reference/url-canonicalization``: lowercased scheme and host, IDN hosts
    to Punycode, userinfo stripped, default ports dropped, dot segments removed but
    consecutive slashes PRESERVED, percent-encoding normalized, query preserved
    byte-for-byte, fragment stripped. This is the single canonical form used both to
    compare two FormatId references for federation identity (see
    ``format_id_identity``) and to key the creative-agent format cache
    (``CreativeAgentRegistry._cache_key``).

    The algorithm is not reimplemented here. It comes from ``src.vendor.adcp_canonical``
    — adcp 7.0.2's implementation, copied verbatim, which passes all 37 published
    conformance vectors — reached through ``src.core.signing.canonical``, the ONE seam
    over that package. The version this repo pins (6.6.0) fails 14 of them and is
    terminal on its line; that package's docstring has the detail, and salesagent-3xcdk
    deletes it by migrating.

    ``producer_target_uri`` and not the seam's ``canonical_target_uri``: they differ on
    url-canonicalization.mdx step 2, and this is the PRODUCER side. A raw non-ASCII host is
    converted to its A-label here, where a verifier comparing a signature base must refuse
    one rather than pick a UTS-46 outcome the signer may not have picked.

    **Spec step 5 is applied here, not there.** "If the path is empty AND an
    authority is present, substitute ``/``." 7.0.2 does this only when a query is
    present (``https://h.com?x=1`` -> ``https://h.com/?x=1``) and leaves
    ``https://h.com`` with an empty path; no conformance vector covers the no-query
    case, so it passes 37/37 while still diverging. Applying it makes
    ``https://x.org`` and ``https://x.org/`` one agent, which is the spelling
    difference a human actually produces.

    What this deliberately does NOT do is ``.rstrip("/")``, which it used to. That
    is an "additional transformation before comparison", which the algorithm's
    closing sentence forbids, and it went further than step 5 in three ways the
    spec calls DISTINCT: it equated ``/a`` with ``/a/``, equated
    ``/.well-known/adcp/sales`` with ``.../sales/``, and collapsed the consecutive
    slashes step 5 exists to preserve.

    Args:
        agent_url: A URL string or ``AnyUrl`` (FormatId.agent_url, CreativeAgent.agent_url).

    Returns:
        The canonical form, with an empty path rendered as ``/``.

    Raises:
        TargetUriMalformedError: For an authority the profile requires be rejected
            rather than canonicalized (no host, bare IPv6, unclosed bracket, IPv6
            zone id). A ``ValueError`` subclass, so existing callers that treat a
            bad URL as ``ValueError`` keep working.
    """
    from src.core.signing.canonical import producer_target_uri

    canonical = producer_target_uri(str(agent_url))
    # Step 5, the half the vendored implementation applies only when a query is present.
    scheme, _, rest = canonical.partition("://")
    if rest and "/" not in rest and "?" not in rest:
        canonical = f"{scheme}://{rest}/"
    return canonical


@dataclass(frozen=True, slots=True, order=True)
class FormatIdentity:
    """The federation identity of a ``format_id``: its canonical ``agent_url`` and its ``id``.

    A DISTINCT TYPE rather than a ``tuple[str, str]``, and that is the whole point of
    the class. The canonicalization in ``canonical_agent_url`` is a spec MUST, so a
    comparison key built any other way is wrong -- but when the key was a bare tuple
    nothing could say so, because a hand-built tuple has the identical static type and
    differs only in VALUE. ``mypy.ini`` already sets ``strict_equality = True`` and it
    does not help there: comparing a raw ``tuple[str, str]`` against a
    ``set[tuple[str, str]]`` type-checks clean.

    It does help here. Against a ``set[FormatIdentity]`` the same mistake is
    ``Non-overlapping container check ... [comparison-overlap]`` at type-check time,
    so the rule is REFUSED rather than merely documented.

    This is not hypothetical. A UC-005 BDD scenario compared
    ``(SELLER_AGENT_URL, fid.id)`` -- the raw constant, un-canonicalized -- against a
    set of identities, and that assertion was the only one separating ``(agent_url,
    id)`` matching from ``id``-alone matching. Canonicalization appends the spec's
    step-5 trailing slash, so the two could never be equal and the assertion held
    whatever the seller returned. An id-only filter regression would have passed on
    every transport.

    Frozen and slotted so it hashes, lives in a set, and cannot be mutated after the
    canonical form is computed.
    """

    agent_url: str
    id: str


def format_id_identity(format_id: LibraryFormatId) -> FormatIdentity:
    """Return the federation identity of a FormatId: its canonical ``agent_url`` and ``id``.

    AdCP v3.1 makes ``format_id`` an object whose identity is BOTH ``agent_url`` and
    ``id`` (``core/format-id.json`` requires ``[agent_url, id]``; the ``list_formats``
    storyboard step matches product/format references with ``match_keys: [agent_url,
    id]``, ``scope.equals: $agent_url``, ``on_out_of_scope: warn``). Comparing on
    ``id`` alone would mis-resolve a
    third-party reference (foreign ``agent_url``) to a local format that merely
    shares an ``id`` — fabricating a local entry for a format the seller does not
    host. Works on both the library ``FormatId`` and our subclass (duck-typed on
    ``agent_url``/``id``).

    THE ONLY way to build a comparison key. ``FormatIdentity`` is constructible
    directly, but every caller should come through here: this is where the spec's
    canonicalization is applied, and a key that skipped it is the bug the type exists
    to refuse.

    Args:
        format_id: Any FormatId-like object exposing ``agent_url`` and ``id``.

    Returns:
        The ``FormatIdentity`` comparison key for federation identity.
    """
    return FormatIdentity(canonical_agent_url(format_id.agent_url), format_id.id)


class WireSerializerMixin:
    """The single wrap ``model_serializer`` seat for wire shaping.

    Pydantic runs only the FIRST model serializer it finds in the MRO and silently
    drops the rest — two mixins that each declare one do not compose, they shadow.
    (Verified on the pinned pydantic: two wrap serializers on one model produce one
    call.) So every wire-shaping concern shares this one seat and is switched on by
    a class attribute, rather than each concern bringing its own serializer.

    Exactly two concerns live here:

    * **nested re-serialization** — opt in via :class:`NestedModelSerializerMixin`.
    * **required-nullable retention** — opt in via :class:`AlwaysIncludeFieldsMixin`.

    A class that needs both names both mixins and still gets exactly one serializer.

    There is deliberately no per-class hook and no per-class strip set. A wire model
    conforms by inheriting the pinned library parent; a field that must exist on the model
    and not on the wire is ``Field(exclude=True)`` at its declaration. Both of the
    mechanisms that used to sit here (a per-class finishing hook and a per-class strip set) existed
    only to patch back the output of a redeclaration that had weakened the library type,
    or to hide a field that belongs on the wire.
    """

    if TYPE_CHECKING:
        # This mixin is only ever composed onto a pydantic model, and it reads the host's
        # fields. Declaring that here states the requirement instead of leaving mypy to infer
        # a plain class and reject ``cls.model_fields``.
        model_fields: ClassVar[dict[str, FieldInfo]]

    _SERIALIZE_NESTED_MODELS: ClassVar[bool] = False

    @classmethod
    def _always_include_null_fields(cls) -> frozenset[str]:
        """Fields this model declares REQUIRED whose type admits ``None``.

        Read off ``model_fields``, not off a schema path. ``required`` and nullable are
        independent axes in JSON Schema, and their conjunction is exactly "the key must be
        present, its value may be null" -- which is the one case the SDK base's blanket
        ``exclude_none=True`` gets wrong. The model already carries both axes, so nothing
        needs naming.

        This used to read ``_PINNED_SCHEMA_REF``, a hand-written schema path per adopter, and
        resolve it through 150 lines of ``$ref`` / ``allOf`` / JSON-pointer walking. Measured
        across the 416 models reachable from a response, the derived rule reproduces that
        result exactly on every adopter -- and it cannot name the wrong schema, which the
        string could. Two of the four adopters named a ref that derived nothing at all.
        """
        return frozenset(
            name
            for name, field in cls.model_fields.items()
            if field.is_required() and type(None) in get_args(field.annotation)
        )

    @model_serializer(mode="wrap")
    def _serialize_wire(self, serializer, info):
        data = serializer(self)
        if self._SERIALIZE_NESTED_MODELS:
            data = self._apply_nested_models(data, info)
        return self._apply_always_include(data, info)

    def _apply_nested_models(self, data, info):
        """Re-serialize nested models through their own ``model_dump()``.

        Pydantic's default serialization does not call a nested model's custom
        ``model_dump()``, so internal fields it excludes would survive. Introspects
        fields rather than hardcoding names, so schema changes need no edit here.
        """
        for field_name in self.__class__.model_fields:
            if field_name not in data:
                continue

            field_value = getattr(self, field_name, None)
            if field_value is None:
                continue

            if isinstance(field_value, list) and field_value:
                if isinstance(field_value[0], BaseModel):
                    data[field_name] = [item.model_dump(mode=info.mode) for item in field_value]
            elif isinstance(field_value, BaseModel):
                data[field_name] = field_value.model_dump(mode=info.mode)

        return data

    def _apply_always_include(self, data, info):
        """Put back the declared required-nullable fields ``exclude_none`` dropped.

        Two rules, which between them close both hazards the previous
        ``model_dump()`` override could only document in prose:

        1. A field the caller explicitly named in ``exclude=`` (or left out of
           ``include=``) is never re-inserted. The wrap serializer receives the
           caller's selection on ``info``, which a ``model_dump()`` override could
           not see — so the exclusion is honoured for every value, not just the
           ones that happened to be non-null.
        2. Only a ``None`` value is ever put back. A non-null value absent from
           *data* was dropped deliberately, and ``None`` is the same token in every
           serialization mode, so nothing can land a live ``datetime`` in a
           ``mode="json"`` dump.
        """
        excluded = info.exclude or ()
        included = info.include
        for field in self._always_include_null_fields():
            if field in data or field in excluded:
                continue
            if included is not None and field not in included:
                continue
            if getattr(self, field, None) is None:
                data[field] = None
        return data


class NestedModelSerializerMixin(WireSerializerMixin):
    """Ensures nested Pydantic models are dumped through their custom ``model_dump()``.

    Usage:
        class MyResponse(NestedModelSerializerMixin, SalesAgentBaseModel):
            nested_field: NestedModel
            # Automatically serializes nested_field correctly

    The behaviour lives in :class:`WireSerializerMixin` — see the note there on why
    it is one shared serializer rather than one per mixin.
    """

    _SERIALIZE_NESTED_MODELS: ClassVar[bool] = True


class AlwaysIncludeFieldsMixin(WireSerializerMixin):
    """Keeps spec-required fields on the wire even when their value is null.

    The library base serializes with ``exclude_none=True``, which is right for
    OPTIONAL fields — AdCP omits them rather than sending null — and wrong for a
    field the schema lists in ``required`` while typing it nullable. Dropping one
    of those produces a response that fails item-level validation: the same class
    of silent omission as the missing envelope status (GH #1900).

    The retained set is derived from the model's own ``model_fields`` — a field it
    declares required whose type admits ``None`` — so there is nothing to name and
    nothing to keep in step with the spec. Adopters used to write a
    ``_PINNED_SCHEMA_REF`` schema path, which could name the wrong schema and, on
    two of the four, named one that derived nothing. The hook and the serializer
    live in :class:`WireSerializerMixin`; this class is the opt-in name.

    The two footguns this mixin used to document are fixed rather than described —
    see ``WireSerializerMixin._apply_always_include``: only a ``None`` value is
    ever re-inserted, so an explicit ``exclude=`` is honoured and no raw Python
    value can reach a ``mode="json"`` dump.
    """


@cache
def _announced_schema(dto: type[BaseModel]) -> dict[str, Any]:
    """The DTO's JSON Schema, computed once per model.

    Cached because ``model_json_schema()`` walks the whole model tree and this runs on every
    request. Keyed by the class, so a subclass gets its own entry.
    """
    return dto.model_json_schema()


class BuyerRequest:
    """Accessors for the envelope the boundary reads, on every request whether it declares it.

    The boundary needs these for every tool -- ``account`` to scope authorization,
    ``idempotency_key`` to decide at-most-once, the two version pins to negotiate -- but only
    10 of the 14 pinned request schemas declare an account and only 4 declare a key. Mixed in,
    these answer None for the rest, so the boundary asks the request instead of writing
    ``getattr(req, "account", None)``.

    The version pins are the case where every request DOES declare the field, and the accessor
    still earns its place: this mixin declares no fields, so ``BuyerRequest`` -- the type the
    boundary and the registry hand around -- has no ``adcp_version`` attribute to reach for.
    Asking through a method keeps that call type-checked instead of cast.

    METHODS, not properties: pydantic does not let a DTO's field shadow a property of the same
    name. The value is stored and ``model_dump`` shows it, but attribute access returns the
    property, so every tool that declares an account resolves None -- silently.
    """

    if TYPE_CHECKING:
        # DECLARED, not inherited. Every concrete BuyerRequest is a pydantic model -- the
        # mixin is only ever combined with one -- so these members always exist, and the
        # registry can type ``dto`` as ``type[BuyerRequest]`` and still validate through it.
        # Inheriting ``BaseModel`` to say the same thing would make the mixin a model in its
        # own right, which is exactly what it must not be: it declares no fields, and a bare
        # ``BaseModel`` base put an empty model in every DTO's MRO.
        @classmethod
        def model_validate(cls, obj: Any, **kwargs: Any) -> Self: ...

        def model_dump(self, **kwargs: Any) -> dict[str, Any]: ...

    @model_validator(mode="before")
    @classmethod
    def _accept_only_declared_fields(cls, data: Any) -> Any:
        """Reduce the incoming bag to the fields this request's schema declares.

        THE POLICY, in the one place every transport already passes through. A field our
        models do not declare never reaches an implementation: in development its presence is
        an error, so a spec field we have not implemented is loud; in production it is dropped,
        so a newer buyer is served instead of refused.

        A ``mode="before"`` validator on a mixin, not a call site. Every transport CONSTRUCTS
        the DTO -- A2A through the registry, REST through ``model_validate``, MCP through
        FastMCP's TypeAdapter -- so every transport gets this, and there is no call to forget.
        The previous home was ``ToolSpec.validate``, a method added to collapse ten mypy casts
        on the A2A handlers; hanging the policy off it meant only A2A ever ran it, and the same
        bytes still had three meanings.

        BEFORE, and that is what reaches the nesting. Field validation has not run yet, so the
        strip pre-empts the SDK's own nested ``extra="forbid"`` -- which in PRODUCTION was
        rejecting a buyer who sent an unknown field inside ``account``, the exact
        forward-incompatibility ``extra="ignore"`` exists to prevent. ``extra="ignore"`` only
        ever covered the top level, because everything nested is a library model whose config
        our environment override does not reach.

        ``AdCPInvalidRequestError``, not ``AdCPValidationError``. An undeclared property is an
        ``additionalProperties`` violation, and the pin puts that in the first bucket:
        ``INVALID_REQUEST`` is "malformed, missing required fields, or violates schema
        constraints" while ``VALIDATION_ERROR`` is "invalid field values or business rules
        BEYOND schema validation" (enums/error-code.json), and L1/security.mdx states "Schema
        validation runs first ... A malformed request returns INVALID_REQUEST".

        The rejection NAMES what it rejected, in the two channels core/error.json defines for
        it: one ``issues[]`` entry per removed key -- RFC 6901 ``pointer``, ``keyword``
        ``additionalProperties`` -- and ``field``, which the exception derives from
        ``issues[0].pointer`` in JSONPath-lite because the pin makes that dual-write a MUST
        for pre-3.1 consumers. Without them a buyer was told only "Invalid request
        parameters", with nothing saying which of thirty fields to remove -- and REST already
        named the field for a schema rejection through its own handler, so the same payload
        got a useful answer over REST and a useless one over MCP.
        """
        if not isinstance(data, dict):
            return data
        from src.core.config import get_pydantic_extra_mode
        from src.core.errors.issues import ErrorIssue
        from src.core.exceptions import AdCPInvalidRequestError
        from src.core.schemas._accepted_shape import deep_strip_to_schema

        rejected: list[str] = []
        # ``cls`` is the DTO class: a BuyerRequest AND the pydantic model it is mixed into.
        # Python has no intersection type, so the cast states the half this call needs.
        accepted = deep_strip_to_schema(data, _announced_schema(cast("type[BaseModel]", cls)), rejected=rejected)
        # THE SAME ANSWER the ``extra=`` on every model above is configured from, asked the
        # same way. This used to read ``is_production()`` while ``model_config`` read
        # ``get_pydantic_extra_mode()`` — one policy with two sources, which agreed only
        # because the second was a pure function of the first. It stopped agreeing the moment
        # a deployment needed the forward-compatible boundary WITHOUT claiming production (the
        # storyboard conformance agent, docker-compose.e2e.yml): the top-level ``extra``
        # relaxed and this validator did not, so the five read tools rejected the
        # ``idempotency_key`` AdCP 3.1 puts on every task request — measured as three
        # ``read_tool_idempotency`` checks regressing with the wire mode reading ``ignore``.
        if rejected and get_pydantic_extra_mode() == "forbid":
            raise AdCPInvalidRequestError(
                issues=[ErrorIssue.of(pointer=p, keyword="additionalProperties") for p in rejected]
            )
        return accepted

    def get_account(self) -> LibraryAccountReference | None:
        """The account this request names, or None when its schema declares no ``account``."""
        return self.__dict__.get("account")

    def get_context(self) -> ContextObject | None:
        """The buyer's opaque ``context``, or None when the request carried none.

        Read by the boundary alone, which echoes it onto whatever leaves. Business logic may
        call this too; nothing may set the field.
        """
        return self.__dict__.get("context")

    def get_idempotency_key(self) -> str | None:
        """The at-most-once key this request carries, or None when its schema declares none."""
        return self.__dict__.get("idempotency_key")

    def get_adcp_version(self) -> str | None:
        """The release this buyer pins, or None when it pinned none."""
        return self.__dict__.get("adcp_version")

    def get_adcp_major_version(self) -> int | None:
        """The major this buyer pins, or None when it pinned none."""
        return self.__dict__.get("adcp_major_version")


class SalesAgentBaseModel(LibraryAdCPBaseModel):
    """Base model for all internal salesagent schemas.

    Extends the adcp library's AdCPBaseModel to add environment-aware validation:
    - Production: extra="ignore" (forward compatible, accepts future schema fields)
    - Non-production: extra="forbid" (strict, catches bugs early)

    Inherits from library base:
    - model_dump(exclude_none=True) — AdCP spec compliance
    - model_dump_json(exclude_none=True) — AdCP spec compliance
    - model_summary() — human-readable protocol responses

    The validation mode is set at class definition time based on the ENVIRONMENT variable.
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())


def _mirror_media_buy_status(model: Any) -> Any:
    """Backfill the deprecated body-level ``status`` from the domain ``media_buy_status``.

    AdCP 3.1 create-/update-media-buy-response adds ``media_buy_status`` as the
    PREFERRED domain-status field on the RESPONSE BODY (a ``MediaBuyStatus`` value)
    and DEPRECATES the body-level ``status`` (removed in 3.2). This validator only
    backfills whichever of the two BODY fields is set onto the other, so the
    deprecated body ``status`` still carries the domain value during the deprecation
    window. Both body fields are typed ``MediaBuyStatus | None`` on the adcp 5.7
    library base (``CreateMediaBuySuccessResponse`` / ``UpdateMediaBuySuccessResponse``).

    This does NOT govern the WIRE top-level ``status``. On the flattened envelope,
    ``TaskResultEnvelope._serialize`` OVERWRITES the top-level ``status`` with the
    PROTOCOL ``TaskStatus`` (``submitted`` / ``completed``), so on the wire the
    top-level ``status`` and ``media_buy_status`` are DIFFERENT namespaces and are
    NOT identical. This is the model graded by the pinned 3.1.1 storyboard
    ``pending_creatives_to_start.yaml`` (status=field_value 'completed'). The
    body-``status`` backfill below serves the #4908 deprecation window only.
    See docs/adcp-spec-version.md
    "`status` vs `media_buy_status` on media-buy responses".

    Shared by ``CreateMediaBuySuccess`` and ``UpdateMediaBuySuccess`` (DRY).
    """
    status = getattr(model, "status", None)
    media_buy_status = getattr(model, "media_buy_status", None)
    if media_buy_status is None and status is not None:
        model.media_buy_status = status
    elif status is None and media_buy_status is not None:
        model.status = media_buy_status
    return model


#: ``union root -> TypeAdapter`` for the tools whose response schema is a ``oneOf``. Populated
#: beside the branches, which is the only place that knows them; :meth:`AdcpResponse.revive`
#: is the only reader. A root absent from here has a single shape and validates as itself.
_BRANCH_ADAPTERS: dict[type, TypeAdapter] = {}


class AdcpResponse(AdcpVersionEnvelope, ProtocolEnvelope):
    """What every AdCP response IS: the two envelopes its schema composes at the root.

    Declares NO fields of its own. It exists to name a TYPE, and a field added here would put
    a key on the wire that no pinned schema declares.

    Every response schema opens with the same root composition --
    ``"allOf": [{"$ref": "../core/version-envelope.json"}, {"$ref": "../core/protocol-envelope.json"}]``
    -- and a root ``allOf`` in JSON Schema draft-07 applies to the whole document, so it reaches
    every branch of a root ``oneOf`` unconditionally. The SDK's generator does not: it applies
    the bases to some branches and not others (adcontextprotocol/adcp-client-python#1136, and
    its spec-side twin adcontextprotocol/adcp#7324, closed with the ruling that the schemas are
    correct and the codegen is not). Inheriting this base RESTORES that composition; it does not
    widen anything.

    The boundary writes exactly three fields onto a response -- ``adcp_version`` and
    ``context`` in ``_boundary._served``, ``replayed`` in ``_boundary._deserializer_for`` --
    and they come from the two different bases above. Naming both bases here is what lets the
    boundary be typed rather than cast.

    ``context`` is REFUSED here on both roads in: a validator rejects a non-None value on
    construction, and ``__setattr__`` rejects assignment afterwards. Both are needed --
    pydantic's ``__init__`` populates a model without routing through ``__setattr__``, so the
    override alone never sees a constructor argument. ``_boundary._served`` writes the field
    through ``object.__setattr__``, which bypasses both; it is the one writer, and these two
    refusals are what make that true rather than customary (salesagent-3cs7o.3).
    """

    @model_validator(mode="after")
    def _context_is_the_boundarys(self) -> "AdcpResponse":
        if self.__dict__.get("context") is not None:
            raise ValueError(
                "context is stamped by the boundary (src/core/tools/_boundary._served); a response is constructed without one"
            )
        return self

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "context":
            raise AttributeError(
                "context is stamped by the boundary (src/core/tools/_boundary._served); nothing else assigns it"
            )
        super().__setattr__(name, value)

    @classmethod
    def revive(cls, data: dict[str, Any]) -> "AdcpResponse":
        """Rebuild a response of this type from a document, resolving a ``oneOf`` to its branch.

        A response whose schema is a single shape validates as itself. A union root resolves
        through the discriminated union registered for it, which returns the BRANCH the buyer
        originally received -- validating against the root would build the root and drop
        whatever the branch declares.

        THE ONE DOOR FOR A DOCUMENT THAT ALREADY CARRIES A CONTEXT. The constructor refuses
        ``context`` and so does assignment, which is what makes ``_boundary._served`` the only
        thing that can PUT one on a response. A reader rebuilding a document the boundary
        already served is the other side of that: the context is not minted here, it arrived,
        and refusing to carry it would mean no reader can ever have the response it was sent.
        The idempotency cache does not exercise this -- it stores the body BEFORE the stamp --
        but the test harness re-parses the served wire into this env's own response subclass,
        and two integration tests read ``result.payload.context`` off it
        (tests/integration/test_creative_sync_transport.py:1468). So the field comes off the
        document, the rest validates through the refusing constructor unchanged, and the value
        is re-attached through ``object.__setattr__`` -- the same door the boundary uses, in the
        one classmethod that owns reconstruction.

        Raises whatever pydantic raises; the caller decides that an unrevivable stored body is
        a cache miss.
        """
        payload = dict(data)
        context = payload.pop("context", None)
        adapter = _BRANCH_ADAPTERS.get(cls)
        revived = adapter.validate_python(payload) if adapter is not None else cls.model_validate(payload)
        if context is not None:
            object.__setattr__(revived, "context", context)
        return revived


class AdcpErrorResponse(AdcpResponse):
    """What a FAILED tool call is: the response envelope, carrying the error.

    AdCP models a failure as a response, not as a separate document.
    ``core/protocol-envelope.json`` declares ``adcp_error``, ``context`` AND ``status`` on
    every response envelope and lists ``status`` as required, so an error body is a response
    body with the error fields filled in. This class is that body, declared once.

    ``errors`` is declared HERE rather than on ``AdcpResponse`` because the pin puts it on
    each tool's own schema (``media-buy/*-response.json``) and not on the envelope. One
    subclass is the only place it can live without a per-tool copy, and a failure has no tool
    payload to carry besides the error.

    Why a class and not a dict: the hand-assembled dict this replaces could not carry
    ``status``, and did not -- so every error body this seller emitted was invalid against
    every pinned response schema, and nothing caught it, because the graded error checks
    validate the error OBJECTS against ``core/error.json`` and never the envelope around them
    (salesagent-3cs7o.4). A detached dict also has no ``context`` field, which is why the
    buyer's context had to ride the exception and be hand-threaded to every raise site to get
    there.
    """

    errors: list[_LibraryError] = Field(
        default_factory=list, description="The failure, as the tool's schema declares it"
    )

    @property
    def http_status(self) -> int:
        """The HTTP status this failure is signalled with: its code's own, read from ``CODE_TABLE``.

        The same lookup ``AdCPSalesAgentError.status_code`` performs, keyed here by the wire
        string this response carries. Subscripted, so a code outside the vocabulary raises
        rather than answering a status the table never declared.
        """
        from src.core.errors.codes import CODE_BY_VALUE, CODE_TABLE

        if self.adcp_error is None:
            raise ValueError("an error response carries its error in adcp_error")
        return CODE_TABLE[CODE_BY_VALUE[self.adcp_error.code]].status

    @classmethod
    def of(cls, exc: "AdCPSalesAgentError") -> "AdcpErrorResponse":
        """Build the failure response for one typed exception.

        Carries the SAME error object at both levels the wire expects -- ``adcp_error`` on the
        envelope and ``errors[0]`` -- because a receiver is free to read either.

        The dict is assembled HERE and validated through the SDK type, so the shape stays the
        SDK's and only the assembly is ours. ``adcp.server.helpers.adcp_error()`` used to do the
        assembling, and every value it could have contributed was already being passed to it
        explicitly: its ``message or STANDARD_ERROR_CODES[code]["message"]`` and
        ``recovery or std.get("recovery", "terminal")`` fallbacks were both dead on this path,
        because ``message`` and ``recovery`` are properties of the code resolved from
        ``CODE_TABLE``. What remained was "omit the key when the value is None", five times --
        written out below -- and one hazard: the helper's recovery default disagrees with the
        pinned ``enumMetadata`` on 56 of the 92 published codes, so deleting the
        ``recovery=exc.recovery`` keyword during any future refactor would have silently
        emitted ``terminal`` for every error. It also had no ``issues`` parameter and typed
        ``details`` flat-scalars-only, so neither fitted through it.

        What the BOUNDARY owns -- ``context`` and ``adcp_version`` -- is stamped by the
        boundary (``_boundary._served``), on a failure exactly as on a success.
        """
        from src.core.exceptions import _details_to_wire

        error = _LibraryError.model_validate(
            {
                "code": exc.error_code,
                "message": exc.message,
                "recovery": exc.recovery,
                **({"field": exc.field} if exc.field is not None else {}),
                **({"suggestion": exc.suggestion} if exc.suggestion is not None else {}),
                **({"retry_after": exc.retry_after} if exc.retry_after is not None else {}),
                **({"details": d} if (d := _details_to_wire(exc.details)) is not None else {}),
                **({"issues": [issue.to_wire() for issue in exc.issues]} if exc.issues else {}),
            }
        )
        return cls(status=LibraryTaskStatus.failed, adcp_error=error, errors=[error])


class CreateMediaBuyResult(AdcpResponse):
    """The three shapes a create_media_buy response can take, as one nameable type.

    ``media-buy/create-media-buy-response.json`` is a root ``oneOf`` over Success, Error and
    Submitted. The SDK renders that as a bare union alias, which is correct for a
    non-discriminated ``oneOf`` -- but a union alias is ``types.UnionType``, not a ``type``, so
    it cannot be a return annotation this codebase can resolve. ``_boundary._response_model_for``
    reads the implementation's annotation and requires a class; given a union it returns None
    and the idempotency replay silently disables for the tool.

    So the branches inherit a common root and the implementation returns THAT. Each branch
    stays its own SDK type and serializes itself.
    """


class UpdateMediaBuyResult(AdcpResponse):
    """The three shapes an update_media_buy response can take. See ``CreateMediaBuyResult``."""


class CreateMediaBuySuccess(AlwaysIncludeFieldsMixin, AdCPCreateMediaBuySuccess, CreateMediaBuyResult):
    """Successful create_media_buy response, extending the SDK success branch.

    Extends the official adcp CreateMediaBuySuccess type with internal workflow tracking.

    ``media-buy/create-media-buy-response.json`` @ 3.1.1 composes
    ``core/version-envelope.json`` and ``core/protocol-envelope.json`` at its ROOT and puts
    the Success/Error/Submitted branches under ``oneOf``, so the document a buyer receives
    is the envelope fields plus one branch's fields, flat. This class IS that document:
    ``ProtocolEnvelope`` is a base, and ``status``, ``task_id``, ``message``, ``context_id``
    and the rest are declared rather than attached by whichever transport happens to run.

    AdCP spec 3.0.0 ``error-handling.mdx`` allows non-fatal errors on the
    success envelope ("populate only the payload... MUST NOT populate
    ``adcp_error``"). The ``errors`` field below carries per-package
    advisories like ``UNSUPPORTED_FEATURE`` for fields the seller persists but
    cannot yet honor (e.g. ``property_list_filtering=False`` window). Mirrors
    the pattern used by ``GetProductsResponse``, ``ListCreativeFormatsResponse``,
    ``SyncAccountsResponse``.
    """

    # A RESPONSE model keeps nothing it was not declared. The SDK parent sets
    # extra="allow", which STORES an unknown key and then serializes it, so
    # ``workflow_step_id`` -- deleted from this class when adapters moved to a carrier type
    # -- came back as an EXTRA the moment a construction site still passed it, and reached
    # the buyer on all three dump paths. Deleting a seller-internal field from a wire model
    # only removes it from the wire if the class also refuses to keep what it did not
    # declare. "ignore" rather than "forbid": a response has no forward-compatibility
    # reason to retain a sender's unknown keys and no reason to refuse them either.
    model_config = ConfigDict(extra="ignore")

    # adcp 6.6 (spec 3.1.1) made these required on the success envelope. ``status`` is
    # REQUIRED and typed ``Literal["completed"]`` by the SDK parent, because this is the
    # success branch and the branch's schema makes the value a const. A local
    # ``CompletedTaskStatusMixin`` used to declare that field and supply its default; it is
    # deleted, and nothing replaced it, because the SDK parent already does both jobs — its
    # own ``_normalize_legacy_status`` before-validator fills ``"completed"`` when a caller
    # omits the field. Listing ``ProtocolEnvelope`` AFTER the parent is deliberate: the
    # parent's narrower ``Literal`` wins, so ``status="failed"`` on a success branch stays a
    # type error rather than becoming an eight-member enum.
    # ``confirmed_at`` and ``revision`` are NOT invariant: they are columns the repository
    # owns, so they carry NO default here and every construction site states where its
    # value came from. A default made this model a second producer of persisted state —
    # and an invisible one, because the write-seam guard scans assignments and a
    # ``default_factory`` is not an assignment.
    #
    # What the default asserted: ``confirmed_at=datetime.now(UTC)`` on EVERY success,
    # including a ``pending_creatives`` buy whose column is NULL precisely because the
    # seller has not committed. The docstring of ``CreateMediaBuySubmitted`` below
    # already rejects that reasoning in so many words ("would falsely assert the seller
    # confirmed a buy that is not yet committed"); the Success branch was doing it.
    #
    # Both keep the parent's REQUIRED types and lose only their local defaults, so
    # omitting either is a construction error instead of a fabricated value.
    #
    # WIDENED to ``| None``, and the whole stack already agreed except this line:
    #
    #   pin  create-media-buy-response.json @ 3.1.1 branch0 (CreateMediaBuySuccess)
    #        type ["string", "null"], and IN ``required`` -> present, may be null
    #   ORM  MediaBuy.confirmed_at  Mapped[datetime | None], nullable=True
    #   DB   media_buys.confirmed_at  is_nullable = YES
    #
    # So this annotation was the only layer narrower than the contract, and the
    # narrowing was never mandated. ``required`` + nullable is exactly "the key must
    # be present, its value may be null" -- no default, and AlwaysIncludeFieldsMixin
    # emits the explicit null rather than dropping the key.
    #
    # It previously said "deliberately NOT widened", on the premise that "this seller
    # never [sends null]: ... a create that is NOT committed (manual approval pending)
    # returns the ``CreateMediaBuySubmitted`` branch instead." That premise was false for
    # one path, and review found it: a ``pending_creatives`` create returns the
    # SUCCESS branch, not Submitted. While PENDING_CREATIVES sat in
    # ``_SELLER_COMMITTED_STATUSES`` the buy got stamped and the non-null type held --
    # but the stamp itself was the defect, a write-once buyer-visible commitment minted
    # at the moment of a HOLD, before the ad server was contacted. Removing that
    # membership is the fix; this widening is what makes the correct state expressible.
    #
    # The suppression records an SDK/SPEC DIVERGENCE, not our convenience: we extend the
    # SDK's class, and that class does not account for the null case its own pinned
    # schema permits. Suppressing here is how we keep inheriting the SDK type while
    # honouring the contract it under-specifies. Cost stated rather than buried -- the
    # type-ignore ratchet moves 56 -> 57, and the ratchet is shrink-only by default, so
    # this is an approved exception rather than a silent increase.
    confirmed_at: AwareDatetime | None  # type: ignore[assignment]

    # Names the branch this model IS, so the retained-field set is DERIVED from the pin
    # rather than listed here. branch0 is CreateMediaBuySuccess; the bare ref is
    # underivable by design because the root composes through oneOf.
    #
    # Adopting the mixin is not optional once confirmed_at can be null: the library
    # base serializes with exclude_none=True, so a null value DROPS the key -- and the
    # pin lists confirmed_at in `required`, so a document missing it fails validation.
    # That is the same silent-omission class as GH #1900, which is why this PR exists.
    # It was invisible until now only because the field could never be null.

    # Replaces a second wrap serializer that used to live on this class. Two wrap
    # serializers in one model means only one runs, so composing the always-include
    # mixin above had NO effect until the duplicate was removed -- confirmed_at was
    # still dropped. Its packages special-case (exclude platform_line_item_id) was
    # already redundant: that field carries exclude=True at both declaration sites.
    # test_architecture_one_wire_serializer_seat.py exists to forbid exactly this and
    # could not see it -- ALLOWED_FILES exempts this file whole.
    _SERIALIZE_NESTED_MODELS: ClassVar[bool] = True
    # revision is INHERITED. It was redeclared here as a bare ``int`` -- no Field, no
    # description -- against a parent typed int/required/[Ge(ge=1)], so the redeclaration
    # dropped the pinned minimum and nothing else. Deleting it restores the bound by
    # inheritance rather than hand-copying it, which is what _pinned_fields.py exists to
    # prevent: a bound written in a second place is a bound that can drift from the pin.

    @classmethod
    def sync_success(cls, **kwargs: Any) -> "CreateMediaBuySuccess":
        """Construct a synchronous create_media_buy success the BUYER will receive.

        ``confirmed_at`` and ``revision`` are required keyword arguments in practice:
        they carry no field default, so omitting them is a construction error rather
        than a silently fabricated value. Pass what the persisted row holds — the
        repository owns both columns.

        This factory exists ONLY because mypy's pydantic plugin does not treat the SDK
        parent's ``_normalize_legacy_status`` before-validator as satisfying the required
        ``status`` field (spurious ``call-arg``); callers route the untyped ``**kwargs``
        through here to dodge that. Do NOT re-default anything here.
        """
        return cls(**kwargs)

    # There is deliberately no second constructor with placeholder ``confirmed_at`` /
    # ``revision``: an adapter does not return this class. It returns
    # ``src.adapters.base.AdapterCreateResult``, a plain model the tool reads and never
    # serializes, so every construction of THIS class is the buyer's envelope and must
    # pass the row's values.

    # account/sandbox/creative_deadline/valid_actions/context: inherited from the
    # adcp 6.6 parent, which re-added all five typed (Account, AwareDatetime,
    # list[MediaBuyValidAction], ContextObject) — the SDK-5.7-era local
    # redeclarations were deleted as stale (PR #1567 round-3; same cleanup
    # Product/SyncCreativeResult got, exemplar 5a8953a46). Pinned by
    # test_adcp_contract.py::test_create_media_buy_success_inherits_parent_typed_annotations.
    # buyer_ref: the SDK-5.7 parent wrongly declared it (removed from AdCP 3.1
    # create-media-buy-response; SDK bug adcontextprotocol/adcp-client-python#950,
    # excluded here by #1417); adcp 6.6 no longer declares it, so no override needed.

    # Non-fatal advisories — see class docstring for the spec basis.
    errors: list[Error] | None = Field(
        default=None,
        description="Non-fatal advisories for the buyer (e.g. UNSUPPORTED_FEATURE when a "
        "field is persisted but won't yet affect targeting). Absent on a fully-honored buy.",
    )

    @model_validator(mode="after")
    def _dual_emit_media_buy_status(self):
        """AdCP 3.1: backfill the deprecated BODY ``status`` from the domain ``media_buy_status``.

        Deprecation-window compat only. The WIRE top-level ``status`` is a PROTOCOL
        ``TaskStatus`` (``submitted`` / ``completed``) set by ``TaskResultEnvelope._serialize``
        — a different namespace from ``media_buy_status`` (GA 3.1.0 model, divergent).
        NOTE: adcp 5.7 types this body ``status`` as ``MediaBuyStatus | None``; the wire
        top-level protocol value never lands on this body field (the envelope owns it), so no
        enum widening is needed here — the SDK type is not authoritative for the wire status.
        See ``_mirror_media_buy_status`` and docs/adcp-spec-version.md "`status` vs `media_buy_status` on media-buy responses".
        """
        return _mirror_media_buy_status(self)


class CreateMediaBuyError(AdCPCreateMediaBuyError, CreateMediaBuyResult):
    """Failed create_media_buy response, extending the SDK error branch.

    Extends the official adcp CreateMediaBuyError type.

    ``status`` and ``replayed`` arrive from ``CreateMediaBuyResult``: the SDK's error branch
    does not carry ``ProtocolEnvelope`` (adcp-client-python#1136) although the schema composes
    it at the root, so the root ``allOf`` reaches this branch through the shared base instead.
    """

    #: Required, with no default. ``core/protocol-envelope.json`` lists ``status`` in
    #: ``required`` and declares no default; the SDK model supplies ``completed``, which on an
    #: error response is the wrong value. Removing it makes an error that does not state its
    #: status unconstructible. Same annotation as the parent, so this is a strengthening and
    #: needs no inheritance allowlist row.
    status: LibraryTaskStatus


class CreateMediaBuySubmitted(AdCPCreateMediaBuySubmitted, CreateMediaBuyResult):
    """Async/pending create_media_buy response, extending the SDK submitted branch.

    Spec 3.1.1 ``create-media-buy-response.json`` models a buy that cannot be
    confirmed before the response is emitted (e.g. one pending human approval)
    as the ``CreateMediaBuySubmitted`` variant of the response ``oneOf``:
    protocol-envelope ``status="submitted"`` (const) plus a required ``task_id``
    the buyer polls for the outcome. ``media_buy_id`` and ``packages`` land on
    the task's COMPLETION artifact, not this envelope. This is distinct from
    ``CreateMediaBuySuccess``, whose ``status="completed"`` would assert a
    synchronously committed buy. (Its ``confirmed_at``/``revision`` defaults used to
    be part of that false assertion too; they are gone — every site now reads the
    persisted row.) Mirrors ``UpdateMediaBuySubmitted``.

    ``status`` defaults to ``"submitted"`` on the library base; ``task_id`` is
    required (the workflow step id the admin approval flow acts on).
    """


# Union type for the SYNCHRONOUS create_media_buy contract (adapter returns,
# replay bodies of completed buys). Deliberately excludes CreateMediaBuySubmitted:
# adapters execute synchronously and can only confirm or fail; the submitted
# task envelope is produced by the tool's approval branches and lives on
# CreateMediaBuyResult.response (Success | Error | Submitted).
CreateMediaBuyResponse = CreateMediaBuySuccess | CreateMediaBuyError


# --- Update Media Buy Response Components ---


class AffectedPackage(LibraryPackage):
    """Affected package in UpdateMediaBuySuccess response.

    Extends adcp library Package with internal tracking fields.
    Note: In AdCP 2.12.0+, affected_packages uses the full Package type.

    Library Package required fields (adcp 2.12.0):
    - package_id: Publisher's package identifier
    - paused: Boolean indicating whether package is paused (replaces old status enum)
    """

    # Internal fields for tracking what changed (not in AdCP spec)
    changes_applied: dict[str, Any] | None = Field(
        None,
        description="Internal: Detailed changes applied to package (creative_ids added/removed, etc.)",
        exclude=True,
    )
    buyer_package_ref: str | None = Field(
        None, description="Internal: Buyer's package reference (legacy compatibility)", exclude=True
    )


class UpdateMediaBuySuccess(NestedModelSerializerMixin, AdCPUpdateMediaBuySuccess, UpdateMediaBuyResult):  # type: ignore[misc]
    """Successful update_media_buy response, extending the SDK success branch.

    Extends the official adcp UpdateMediaBuySuccess type.

    ``media-buy/update-media-buy-response.json`` @ 3.1.1 composes the version and protocol
    envelopes at its ROOT, exactly as the create response does, so this class declares the
    envelope fields alongside its branch fields. See ``CreateMediaBuySuccess``.

    Carries an optional ``errors`` field for non-fatal advisories on the
    same basis as ``CreateMediaBuySuccess`` (AdCP 3.0.0 error-handling
    "non-fatal in payload" rule). Used today for per-package
    ``UNSUPPORTED_FEATURE`` notices when ``property_list`` is persisted but
    not yet compiled by the adapter.
    """

    # A RESPONSE model keeps nothing it was not declared, for the reason spelled out on
    # ``CreateMediaBuySuccess``: the SDK parent's extra="allow" turned the deleted
    # ``workflow_step_id`` into an extra that serialized to the buyer.
    model_config = ConfigDict(extra="ignore")

    # adcp 6.6 (spec 3.1.1) made status/revision required on the update success envelope.
    # status is REQUIRED and typed Literal["completed"] by the SDK parent, because this is the
    # success branch and the branch's schema makes the value a const. A local
    # CompletedTaskStatusMixin used to declare the field and supply its default; it is
    # deleted, and the SDK parent's own _normalize_legacy_status before-validator fills
    # "completed" when a caller omits it. ProtocolEnvelope is listed AFTER the parent
    # deliberately: the parent's narrower Literal wins, so status="failed" on a success
    # branch stays a type error rather than becoming an eight-member enum.
    #
    # revision is NOT invariant — it is the buy's live optimistic-concurrency token, so
    # it is a column the repository owns and carries NO default here. It previously
    # defaulted to 1, and the allowlist entry that permitted the default asserted
    # "every update path passes the persisted value". That was false for 22 of the 25
    # construction sites: no adapter passes one. The three sites that build the buyer's
    # envelope do read the row, so nothing fabricated reached a wire — but a default is
    # what lets the next consumer read a token nobody looked up, which is the state the
    # create branch was changed to make unreachable.
    #
    # Mirrors CreateMediaBuySuccess: the buyer's envelope uses sync_success() and passes
    # the row's value. An adapter never constructs this class; it returns
    # ``src.adapters.base.AdapterUpdateResult``.
    #
    # revision is INHERITED, for the same reason as CreateMediaBuySuccess: the bare
    # redeclaration dropped the parent's Ge(ge=1) and added nothing.

    @classmethod
    def sync_success(cls, **kwargs: Any) -> "UpdateMediaBuySuccess":
        """Construct a synchronous update_media_buy success the BUYER will receive.

        ``revision`` is a required keyword argument in practice: it carries no field
        default, so omitting it is a construction error rather than a silently
        fabricated value. Pass what the persisted row holds — the repository owns the
        column, and the update tool re-reads the row before building this.

        Do NOT re-default anything here.
        """
        return cls(**kwargs)

    # Override affected_packages to use our extended AffectedPackage type
    # This allows us to include internal tracking fields (changes_applied, buyer_package_ref)
    # while still being AdCP-compliant (those fields are excluded via exclude=True)
    # Pydantic allows subclass override at runtime but mypy doesn't recognize this
    affected_packages: list[AffectedPackage] | None = None

    # buyer_ref: the SDK-5.7 parent wrongly declared it (removed from AdCP 3.1
    # update-media-buy-response; SDK bug adcontextprotocol/adcp-client-python#950,
    # excluded here by #1417); adcp 6.6 no longer declares it, so no override needed
    # — the create side lost its twin in the 6.6 bump and this one was missed.

    # Non-fatal advisories — see class docstring for the spec basis.
    errors: list[Error] | None = Field(
        default=None,
        description="Non-fatal advisories for the buyer (e.g. UNSUPPORTED_FEATURE when a "
        "field is persisted but won't yet affect targeting). Absent when fully honored.",
    )

    @model_validator(mode="after")
    def _dual_emit_media_buy_status(self):
        """AdCP 3.1: backfill the deprecated BODY ``status`` from the domain ``media_buy_status``.

        Deprecation-window compat only. The WIRE top-level ``status`` is a PROTOCOL
        ``TaskStatus`` (``submitted`` / ``completed``) set by ``TaskResultEnvelope._serialize``
        — a different namespace from ``media_buy_status`` (GA 3.1.0 model, divergent).
        NOTE: adcp 5.7 types this body ``status`` as ``MediaBuyStatus | None``; the wire
        top-level protocol value never lands on this body field (the envelope owns it), so no
        enum widening is needed here — the SDK type is not authoritative for the wire status.
        See ``_mirror_media_buy_status`` and docs/adcp-spec-version.md "`status` vs `media_buy_status` on media-buy responses".
        """
        return _mirror_media_buy_status(self)

    # MRO NOTE. NestedModelSerializerMixin is listed FIRST in the bases so its wrap serializer
    # is the one pydantic runs: pydantic keeps only the first model serializer it finds in the
    # MRO and silently drops the rest (see WireSerializerMixin). A hand-written wrap serializer
    # used to live here and duplicated _apply_nested_models line for line -- a second seat,
    # kept so AffectedPackage's exclude=True fields stayed off the wire. The mixin re-dumps
    # every nested model by its instance, affected_packages included, so the copy is gone.


class UpdateMediaBuyError(AdCPUpdateMediaBuyError, UpdateMediaBuyResult):  # type: ignore[misc]
    #: Required, not defaulted -- see ``CreateMediaBuyError.status``.
    status: LibraryTaskStatus

    """Failed update_media_buy response, extending the SDK error branch.

    Extends the official adcp UpdateMediaBuyError type.
    Per AdCP PR #113, this response contains ONLY domain data.
    """


class UpdateMediaBuySubmitted(AdCPUpdateMediaBuySubmitted, UpdateMediaBuyResult):  # type: ignore[misc]
    """Async/pending update_media_buy response, extending the SDK submitted branch.

    Spec 3.1.1 ``update-media-buy-response.json`` models a not-yet-applied update
    (e.g. one pending human approval) as the ``UpdateMediaBuySubmitted`` variant of
    the response ``oneOf``: protocol-envelope ``status="submitted"`` (const) plus a
    required ``task_id`` the buyer polls for the outcome. This is distinct from
    ``UpdateMediaBuySuccess``, whose adcp-6.6 envelope ``status`` defaults to
    ``"completed"`` and would falsely assert the update was applied.

    The update transport wrappers serialize the returned model straight onto the
    wire (``ToolResult(structured_content=response.model_dump(mode="json"))`` /
    A2A / REST), so returning this type from the manual-approval branch yields the
    spec-correct submitted envelope on every transport. ``status`` defaults to
    ``"submitted"`` on the library base; ``task_id`` is required.
    """


# Union type for update_media_buy operation
UpdateMediaBuyResponse = UpdateMediaBuySuccess | UpdateMediaBuyError | UpdateMediaBuySubmitted


def _media_buy_branch(value: Any) -> str:
    """Which ``oneOf`` branch a stored create/update media-buy response is.

    Both schemas discriminate the same way, by required fields rather than by a tag: the
    submitted branch is the only one whose ``status`` is the const ``"submitted"``, the success
    branch is the only one requiring ``media_buy_id``, and the error branch requires ``errors``.

    ``media_buy_id`` is tested BEFORE ``errors`` because a SUCCESS response carries ``errors``
    too -- ``property_list_unsupported_advisories`` puts advisories there -- so keying on
    ``errors`` first would resolve a successful buy to the error branch.
    """
    data = value if isinstance(value, dict) else value.__dict__
    if data.get("status") == "submitted":
        return "submitted"
    if data.get("media_buy_id") is not None:
        return "success"
    return "error"


_BRANCH_ADAPTERS[CreateMediaBuyResult] = TypeAdapter(
    Annotated[
        Annotated[CreateMediaBuySuccess, Tag("success")]
        | Annotated[CreateMediaBuyError, Tag("error")]
        | Annotated[CreateMediaBuySubmitted, Tag("submitted")],
        Discriminator(_media_buy_branch),
    ]
)
_BRANCH_ADAPTERS[UpdateMediaBuyResult] = TypeAdapter(
    Annotated[
        Annotated[UpdateMediaBuySuccess, Tag("success")]
        | Annotated[UpdateMediaBuyError, Tag("error")]
        | Annotated[UpdateMediaBuySubmitted, Tag("submitted")],
        Discriminator(_media_buy_branch),
    ]
)


class TaskStatus(StrEnum):
    """Standardized task status enum per AdCP MCP Status specification.

    Provides crystal clear guidance on when operations need clarification,
    approval, or other human input with consistent status handling across
    MCP and A2A protocols.
    """

    SUBMITTED = "submitted"
    WORKING = "working"
    INPUT_REQUIRED = "input-required"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"
    REJECTED = "rejected"
    AUTH_REQUIRED = "auth-required"
    UNKNOWN = "unknown"

    @classmethod
    def from_operation_state(
        cls, operation_type: str, has_errors: bool = False, requires_approval: bool = False, requires_auth: bool = False
    ) -> str:
        """Convert operation state to appropriate status for decision trees.

        Args:
            operation_type: Type of operation (discovery, creation, activation, etc.)
            has_errors: Whether the operation encountered errors
            requires_approval: Whether the operation requires human approval
            requires_auth: Whether the operation requires authentication

        Returns:
            Appropriate TaskStatus value for client decision making
        """
        if requires_auth:
            return cls.AUTH_REQUIRED
        if has_errors:
            return cls.FAILED
        if requires_approval:
            return cls.INPUT_REQUIRED
        if operation_type in ["discovery", "listing"]:
            return cls.COMPLETED  # Discovery operations complete immediately
        if operation_type in ["creation", "activation", "update"]:
            return cls.WORKING  # Async operations in progress
        return cls.UNKNOWN


# --- Core Models ---


# PricingModel is now imported from adcp.types (adcp library)
# Library uses lowercase member names: .cpm, .vcpm, .cpc, .cpcv, .cpv, .cpp, .flat_rate

# PriceGuidance is now imported from adcp.types.stable (adcp 2.7.0+)
# The library version has the same fields and behavior as our previous local class


class PricingParameters(SalesAgentBaseModel):
    """Additional parameters specific to pricing models per AdCP spec."""

    # CPP parameters
    demographic: str | None = Field(None, description="Target demographic for CPP pricing (e.g., 'A18-49', 'W25-54')")
    min_points: float | None = Field(None, ge=0, description="Minimum GRPs/TRPs required for CPP pricing")

    # CPV parameters
    view_threshold: float | None = Field(
        None, ge=0, le=1, description="Percentage of video/audio that must be viewed for CPV pricing (0.0 to 1.0)"
    )

    # CPA/CPL parameters (reserved for future use)
    action_type: str | None = Field(
        None, description="Type of action for CPA pricing (e.g., 'purchase', 'sign_up', 'download')"
    )
    attribution_window_days: int | None = Field(
        None, ge=1, description="Attribution window in days for CPA/CPL pricing"
    )

    # DOOH parameters
    duration_hours: float | None = Field(None, ge=0, description="Duration in hours for time-based flat rate pricing")
    sov_percentage: float | None = Field(
        None, ge=0, le=100, description="Guaranteed share of voice as percentage (0-100)"
    )
    loop_duration_seconds: int | None = Field(None, ge=1, description="Duration of ad loop rotation in seconds")
    min_plays_per_hour: int | None = Field(
        None, ge=0, description="Minimum number of times ad plays per hour (frequency guarantee)"
    )
    venue_package: str | None = Field(
        None, description="Named venue package identifier (e.g., 'times_square_network', 'airport_terminals')"
    )
    estimated_impressions: int | None = Field(
        None, ge=0, description="Estimated impressions for this pricing option (informational)"
    )
    daypart: str | None = Field(
        None, description="Specific daypart for time-based pricing (e.g., 'morning_commute', 'evening_prime')"
    )


class PricingOption(SalesAgentBaseModel):
    """LEGACY flat pricing option — no production consumers.

    Production pricing options are the discriminated-union subclasses in
    ``src.core.schemas.pricing`` (``Product.pricing_options`` is typed with
    that module's RootModel wrapper). This flat model is retained only because
    ledgered schema-validation tests (T-UC-001-boundary-pricing-xor in
    tests/integration/test_get_products_behavioral.py and
    tests/unit/test_null_field_exclusion.py) still grade its XOR validator;
    retire it together with those tests once the scenario is reconciled.

    V3 Migration: Consolidated pricing fields:
    - rate → fixed_price (for fixed-rate pricing)
    - floor added at top level as floor_price (was in price_guidance)
    - is_fixed removed (determined by presence of fixed_price vs floor_price)
    - price_guidance now only contains percentiles (p25, p50, p75, p90)
    """

    pricing_option_id: str = Field(
        ..., description="Unique identifier for this pricing option within the product (e.g., 'cpm_usd_guaranteed')"
    )
    pricing_model: PricingModel = Field(..., description="The pricing model for this option")
    currency: str = Field(..., pattern="^[A-Z]{3}$", description="ISO 4217 currency code (e.g., USD, EUR, GBP)")

    # V3: Consolidated pricing fields - use fixed_price OR floor_price, not both
    fixed_price: float | None = Field(None, ge=0, description="Fixed rate for this pricing model (V3: replaces rate)")
    floor_price: float | None = Field(
        None, ge=0, description="Floor price for auction-based pricing (V3: was price_guidance.floor)"
    )

    # V3: price_guidance now only contains percentiles, no floor
    price_guidance: PriceGuidance | None = Field(
        None, description="Pricing guidance with percentiles (p25, p50, p75, p90) for auction-based pricing"
    )
    min_spend_per_package: float | None = Field(
        None, ge=0, description="Minimum spend requirement per package using this pricing option"
    )

    @property
    def is_fixed(self) -> bool:
        """Fixed-rate versus auction, DERIVED from which price field is present (AdCP V3)."""
        return self.fixed_price is not None

    @model_validator(mode="after")
    def validate_pricing_option(self) -> "PricingOption":
        """Validate pricing option per AdCP V3 spec constraints."""
        # V3: Must have either fixed_price or floor_price (not both, not neither)
        has_fixed = self.fixed_price is not None
        has_floor = self.floor_price is not None

        if has_fixed and has_floor:
            raise ValueError("Cannot have both fixed_price and floor_price - use one or the other")
        if not has_fixed and not has_floor:
            raise ValueError("Must have either fixed_price (for fixed-rate) or floor_price (for auction)")
        return self


class AssetRequirement(SalesAgentBaseModel):
    """Asset requirement specification per AdCP spec."""

    asset_id: str = Field(..., description="Asset identifier used as key in creative manifest assets object")
    asset_type: str = Field(..., description="Type of asset required")
    asset_role: str | None = Field(None, description="Optional descriptive label (not used for referencing)")
    required: bool = Field(True, description="Whether this asset is required")
    quantity: int = Field(default=1, ge=1, description="Number of assets of this type required")
    requirements: dict[str, Any] | None = Field(None, description="Specific requirements for this asset type")


class FormatReference(SalesAgentBaseModel):
    """Reference to a format from a specific creative agent.

    DEPRECATED: Use FormatId instead. This class is maintained for backward compatibility.
    FormatReference serializes as FormatId (with 'id' field) but accepts 'format_id' for legacy code.

    Used in Product.format_ids to store full format references with agent URL.
    This enables dynamic format resolution from the correct creative agent.

    Example:
        {
            "agent_url": "https://creative.adcontextprotocol.org",
            "format_id": "display_300x250_image"  # Serializes as "id" per AdCP spec
        }
    """

    agent_url: str = Field(
        ..., description="URL of the creative agent that provides this format (must be registered in tenant config)"
    )
    format_id: str = Field(..., serialization_alias="id", description="Format ID within that agent's format catalog")


class Format(LibraryFormat):
    """Creative format definition per AdCP spec.

    Extends the adcp library's Format class. The format_id.agent_url field identifies
    the authoritative creative agent that provides this format (e.g., the reference
    creative agent at https://creative.adcontextprotocol.org).

    Note: All spec-defined fields are inherited from adcp.types.stable.Format.
    We only add internal fields here marked with exclude=True.
    """

    # Internal fields for backward compatibility and convenience
    # These are NOT part of the AdCP spec and are excluded from serialization
    platform_config: dict[str, Any] | None = Field(
        None,
        exclude=True,
        description="Internal: Platform-specific configuration (e.g., gam, kevel) for creative mapping",
    )
    category: Literal["standard", "custom", "generative"] | None = Field(
        None, exclude=True, description="Internal: Format category (not in AdCP spec)"
    )
    is_standard: bool | None = Field(
        None, exclude=True, description="Internal: Whether this follows IAB specifications (not in AdCP spec)"
    )
    requirements: dict[str, Any] | None = Field(
        None,
        exclude=True,
        description="Internal: Legacy technical specifications (not in AdCP spec, use renders instead)",
    )
    iab_specification: str | None = Field(
        None, exclude=True, description="Internal: Name of IAB specification (not in AdCP spec)"
    )
    accepts_3p_tags: bool | None = Field(
        None, exclude=True, description="Internal: Whether format accepts third-party tags (not in AdCP spec)"
    )

    @property
    def agent_url(self) -> str | None:
        """Convenience property to access agent_url from format_id.

        Returns the agent_url from format_id.agent_url per AdCP spec.
        This property exists for backward compatibility with code that expects format.agent_url.

        Returns:
            Agent URL string, or None if not available
        """
        return str(self.format_id.agent_url) if self.format_id.agent_url else None

    def get_primary_dimensions(self) -> tuple[int, int] | None:
        """Extract primary dimensions from renders array or format_id parameters.

        Checks in order:
        1. Parameterized format_id (AdCP 2.5) - width/height on FormatId
        2. Renders array - first render's dimensions
        3. Requirements field (legacy, internal)

        Returns:
            Tuple of (width, height) in pixels, or None if not available.
        """
        # Try format_id parameters first (AdCP 2.5 parameterized formats)
        # Access width/height directly — works with both library FormatId and our subclass
        if self.format_id.width is not None and self.format_id.height is not None:
            return (self.format_id.width, self.format_id.height)

        # Try renders field (AdCP spec - renders is list of Render objects)
        if self.renders and len(self.renders) > 0:
            primary_render = self.renders[0]  # First render is typically primary
            if primary_render.dimensions:
                render_dims = primary_render.dimensions
                # dimensions is a Dimensions object with width/height attributes
                if render_dims.width is not None and render_dims.height is not None:
                    return (int(render_dims.width), int(render_dims.height))

        # Fallback to requirements field (legacy, internal field)
        if self.requirements:
            width = self.requirements.get("width")
            height = self.requirements.get("height")
            if width is not None and height is not None:
                return (int(width), int(height))

        return None

    def get_form_value(self) -> str:
        """Get the value used in HTML form submissions for this format.

        This method provides a consistent way to construct format identifiers
        for use in form checkboxes and validation. It handles both FormatId
        objects and string format_id values.

        Returns:
            String in format "agent_url|format_id" for use in forms

        Example:
            >>> fmt = Format(format_id=FormatId(agent_url="...", id="display_300x250"), ...)
            >>> fmt.get_form_value()
            'https://creative.adcontextprotocol.org/|display_300x250'
        """
        return f"{self.format_id.agent_url}|{self.format_id.id}"


# FORMAT_REGISTRY removed - now using dynamic format discovery via CreativeAgentRegistry
#
# The static FORMAT_REGISTRY has been replaced with dynamic format discovery per AdCP v2.4.
# Format lookups now go through CreativeAgentRegistry which queries creative agents via MCP:
#   - Default agent: https://creative.adcontextprotocol.org
#   - Tenant-specific agents: Configured in creative_agents database table
#
# Migration guide:
#   - Old: FORMAT_REGISTRY["display_300x250"]
#   - New: format_resolver.get_format("display_300x250", tenant_id="...")
#
# See:
#   - src/core/creative_agent_registry.py for registry implementation
#   - src/core/format_resolver.py for format resolution functions


def get_format_by_id(format_id: str, tenant_id: str | None = None) -> Format | None:
    """Get a Format object by its ID from creative agent registry.

    Args:
        format_id: Format identifier
        tenant_id: Optional tenant ID for tenant-specific agents

    Returns:
        Format object or None if not found
    """
    from src.core.format_resolver import get_format

    try:
        return get_format(format_id, tenant_id=tenant_id)
    except (ValueError, AdCPNotFoundError):
        return None


def convert_format_ids_to_formats(format_ids: list[str], tenant_id: str | None = None) -> list[Format]:
    """Convert a list of format ID strings to Format objects.

    This function is used to ensure AdCP schema compliance by converting
    internal format ID representations to full Format objects via dynamic discovery.

    Args:
        format_ids: List of format IDs to resolve
        tenant_id: Optional tenant ID for tenant-specific agents

    Returns:
        List of Format objects
    """
    formats = []
    for format_id in format_ids:
        format_obj = get_format_by_id(format_id, tenant_id=tenant_id)
        if format_obj:
            formats.append(format_obj)
        else:
            # For unknown format IDs, create a minimal Format object with FormatId
            formats.append(
                Format(
                    format_id=FormatId(agent_url=url("https://creative.adcontextprotocol.org"), id=format_id),
                    name=format_id.replace("_", " ").title(),
                )
            )
    return formats


class FrequencyCap(LibraryFrequencyCap):
    """Frequency capping extending AdCP library type with scope.

    Inherits suppress_minutes: float from library.
    Adds scope field for media buy vs package level capping.
    """

    scope: Literal["media_buy", "package"] = Field("media_buy", description="Apply at media buy or package level")


class TargetingCapability(SalesAgentBaseModel):
    """Defines targeting dimension capabilities and restrictions."""

    dimension: str  # e.g., "geo_country"
    # No "managed_only" and no "removed": the pinned targeting overlay declares no managed-only
    # dimension, and the rows that carried either value described fields nothing declares
    # (salesagent-3cs7o.22; the city-level refusal went with salesagent-3cs7o.15).
    access: Literal["overlay", "both"] = "overlay"
    description: str | None = None
    allowed_values: list[str] | None = None  # For restricted value sets


# Mapping from device_platform (OS-level, AdCP TargetingOverlay) to the form factors
# adapters target (``Targeting.device_form_factors``).
# Each platform maps to a list of form factors the device typically has.
_PLATFORM_TO_FORM_FACTORS: dict[str, list[str]] = {
    "ios": ["mobile", "tablet"],
    "android": ["mobile", "tablet"],
    "windows": ["desktop"],
    "macos": ["desktop"],
    "linux": ["desktop"],
    "chromeos": ["desktop"],
    "tvos": ["ctv"],
    "tizen": ["ctv"],
    "webos": ["ctv"],
    "fire_os": ["ctv"],
    "roku_os": ["ctv"],
    # "unknown" intentionally omitted — maps to no form factors
}


class Targeting(TargetingOverlay):
    """Targeting extending AdCP TargetingOverlay with internal dimensions.

    Inherits v3 structured geo fields from library:
    - geo_countries, geo_regions, geo_metros, geo_postal_areas
    - frequency_cap, axe_include_segment, axe_exclude_segment

    Adds exclusion extensions and internal dimensions. It reshapes nothing on the way in:
    the accepted shape is what the fields declare, and a stored document that does not fit
    is a repository or migration problem, not a validator's.
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    # --- Inherited from TargetingOverlay (7 fields): ---
    # geo_countries: list[GeoCountry] | None
    # geo_regions: list[GeoRegion] | None
    # geo_metros: list[GeoMetro] | None
    # geo_postal_areas: list[GeoPostalArea] | None
    # frequency_cap: FrequencyCap | None  (overridden below)
    # axe_include_segment: str | None
    # axe_exclude_segment: str | None

    # Override frequency_cap to use our extended FrequencyCap with scope
    frequency_cap: FrequencyCap | None = None

    # NOTE: property_list, collection_list, and collection_list_exclude are inherited from
    # TargetingOverlay (native in the adcp SDK). CollectionListReference is re-exported
    # from src.core.schemas (see import above) so callers can use a single import path.

    # --- Internal dimensions (unchanged) ---

    # Device and platform targeting
    device_type_any_of: list[str] | None = None  # ["mobile", "desktop", "tablet", "ctv", "audio", "dooh"]
    device_type_none_of: list[str] | None = None

    os_any_of: list[str] | None = None  # Operating systems: ["iOS", "Android", "Windows"]
    os_none_of: list[str] | None = None

    browser_any_of: list[str] | None = None  # Browsers: ["Chrome", "Safari", "Firefox"]
    browser_none_of: list[str] | None = None

    # Content and contextual targeting
    content_cat_any_of: list[str] | None = None  # IAB content categories
    content_cat_none_of: list[str] | None = None

    keywords_any_of: list[str] | None = None  # Keyword targeting
    keywords_none_of: list[str] | None = None

    # Audience targeting
    audiences_any_of: list[str] | None = None  # Audience segments
    audiences_none_of: list[str] | None = None

    # Signal targeting - can use signal IDs from get_signals endpoint
    signals: list[str] | None = None  # Signal IDs like ["auto_intenders_q1_2025", "sports_content"]

    # Media type targeting
    media_type_any_of: list[str] | None = None  # ["video", "audio", "display", "native"]
    media_type_none_of: list[str] | None = None

    # Connection type targeting
    connection_type_any_of: list[int] | None = None  # OpenRTB connection types
    connection_type_none_of: list[int] | None = None

    # Platform-specific custom targeting
    custom: dict[str, Any] | None = None  # Platform-specific targeting options

    # No seller-managed key/value targeting field. The pinned core/targeting.json declares no
    # such field and no managed-only concept; the one that lived here had no writer under
    # src/, and Field(exclude=True) kept it off the wire, off persistence and out of the
    # idempotency hash alike, so it could never round-trip. A seller-side value that must
    # persist belongs on a repository-owned carrier, not on a wire model (CLAUDE.md pattern
    # 4; salesagent-3cs7o.22).

    @property
    def device_form_factors(self) -> list[str] | None:
        """The form factors adapters target, DERIVED on read.

        An explicit seller-side ``device_type_any_of`` wins; otherwise the buyer's spec
        ``device_platform`` (OS-level) maps to form factors through
        ``_PLATFORM_TO_FORM_FACTORS``. A property rather than a field a validator fills in,
        so the model never rewrites its own input.
        """
        if self.device_type_any_of:
            return self.device_type_any_of
        if not self.device_platform:
            return None
        form_factors: set[str] = set()
        for platform in self.device_platform:
            form_factors.update(_PLATFORM_TO_FORM_FACTORS.get(enum_value(platform), []))
        return sorted(form_factors) or None


class Budget(SalesAgentBaseModel):
    """Budget object with multi-currency support (AdCP spec compliant)."""

    total: float = Field(..., gt=0, description="Total budget amount (AdCP spec field name)")
    currency: str = Field(..., description="ISO 4217 currency code (e.g., 'USD', 'EUR')")
    daily_cap: float | None = Field(None, description="Optional daily spending limit")
    pacing: Literal["even", "asap", "daily_budget"] = Field("even", description="Budget pacing strategy")
    auto_pause_on_budget_exhaustion: bool | None = Field(
        None, description="Whether to pause campaign when budget is exhausted"
    )


# Budget utility functions for v1.8.0 compatibility
def extract_budget_amount(budget: "Budget | float | dict | None", default_currency: str = "USD") -> tuple[float, str]:
    """Extract budget amount and currency from various budget formats (v1.8.0 compatible).

    Handles:
    - v1.8.0 format: simple float (currency should be from pricing option)
    - Legacy format: Budget object with total and currency
    - Dict format: {'total': float, 'currency': str}
    - None: returns (0.0, default_currency)

    Args:
        budget: Budget in any supported format
        default_currency: Currency to use for v1.8.0 float budgets.
                         **IMPORTANT**: This should be the currency from the selected
                         pricing option, not an arbitrary default.

    Returns:
        Tuple of (amount, currency)

    Note:
        Per AdCP v1.8.0, currency is determined by the pricing option selected for
        the package, not by the budget field. The default_currency parameter allows
        callers to pass the pricing option's currency for v1.8.0 float budgets.
        For legacy Budget objects, the currency from the object is used instead.

    Example:
        # v1.8.0: currency from package pricing option
        package_currency = request.packages[0].currency  # From pricing option
        amount, currency = extract_budget_amount(request.budget, package_currency)

        # Legacy: currency from Budget object
        amount, currency = extract_budget_amount(Budget(total=5000, currency="EUR"))
    """
    if budget is None:
        return (0.0, default_currency)
    elif isinstance(budget, dict):
        return (budget.get("total", 0.0), budget.get("currency", default_currency))
    elif isinstance(budget, int | float):
        return (float(budget), default_currency)
    else:
        # Budget object with .total and .currency attributes
        return (budget.total, budget.currency)


# AdCP Compliance Models
class Measurement(LibraryMeasurement):
    """Measurement capabilities included with a product per AdCP spec.

    Extends library type - all fields inherited from AdCP spec.
    """

    pass  # All fields inherited from library


class AIReviewPolicy(SalesAgentBaseModel):
    """Configuration for AI-powered creative review with confidence thresholds.

    This policy defines how AI confidence scores map to approval decisions:
    - High confidence approvals/rejections are automatic
    - Low confidence or sensitive categories require human review
    - Confidence thresholds are configurable per tenant
    """

    auto_approve_threshold: float = Field(
        0.90,
        ge=0.0,
        le=1.0,
        description="Confidence threshold for auto-approval (>= this value). AI must be at least this confident to auto-approve.",
    )
    auto_reject_threshold: float = Field(
        0.10,
        ge=0.0,
        le=1.0,
        description="Confidence threshold for auto-rejection (<= this value). AI must be this certain or less to auto-reject.",
    )
    always_require_human_for: list[str] = Field(
        default_factory=lambda: ["political", "healthcare", "financial"],
        description="Creative categories that always require human review regardless of AI confidence",
    )
    learn_from_overrides: bool = Field(
        True,
        description="Track when humans disagree with AI decisions for model improvement",
    )


class CreativePolicy(LibraryCreativePolicy):
    """The pinned ``core/creative-policy.json``, adding nothing.

    ``provenance_required`` was redeclared here as a "local extension ... for EU AI Act
    Article 50 compliance". It is not an extension: the pin declares it, and the parent
    carries it with the same annotation, the same default, the same metadata and the same
    optionality, so the redeclaration duplicated the field byte for byte and is now
    inherited. The docstring it replaces also undercounted the parent, which provides six
    fields, not three -- ``accepted_verifiers`` and ``provenance_requirements`` are pinned
    too and this repo has never implemented either. Implementing them is feature work
    tracked upstream, not something to start from a comment.

    The duplication was not inert, which is why deleting it matters beyond tidiness. A
    redeclaration replaces the parent's DESCRIPTION, and ours said the creative must
    include provenance metadata and cited the regulation, where the pin says whether
    creatives must include it and points the buyer at ``get_creative_features`` for the
    seller's independent verification. A description lives on the field info rather than in
    its metadata, so the inheritance guard's metadata axis cannot see the substitution: a
    reworded pinned field passes as equivalent. Inheriting restores the pinned wording.
    """


# --- Core Schemas ---


class Principal(SalesAgentBaseModel):
    """The buyer a credential resolved to, with its adapter mappings."""

    principal_id: str
    name: str
    platform_mappings: dict[str, Any]
    #: The counterparty's own AdCP agent URL, from onboarding, or None when none was
    #: recorded. It is the key-resolution input for RFC 9421 verification: security.mdx
    #: @ v3.1.1 § "agent_url derivation" makes the publication coordinate the verifier
    #: already controls the ONLY legitimate source, so a signer cannot assert its own and
    #: choose which key set it is checked against. None means no key can be resolved for
    #: this counterparty, not that it is trusted.
    agent_url: str | None = None

    @classmethod
    def from_row(cls, row: Any) -> "Principal":
        """Build from the ``Principal`` ORM row, the one shape a principal is loaded from."""
        return cls(
            principal_id=row.principal_id,
            name=row.name,
            platform_mappings=row.platform_mappings,
            agent_url=row.agent_url,
        )

    def get_adapter_id(self, adapter_name: str) -> str | None:
        """Get the adapter-specific ID for this principal."""
        from src.core.platform_mappings import resolve_adapter_id

        return resolve_adapter_id(self.platform_mappings, adapter_name)


# --- Performance Index ---
class ProductPerformance(SalesAgentBaseModel):
    product_id: str
    performance_index: float  # 1.0 = baseline, 1.2 = 20% better, 0.8 = 20% worse
    confidence_score: float | None = None  # 0.0 to 1.0


# --- Discovery ---


class FormatId(LibraryFormatId):
    """AdCP format identifier - extends library FormatId with convenience methods.

    Note: The inherited agent_url field has type AnyUrl, but Pydantic accepts strings
    at runtime and automatically validates/converts them. This causes mypy warnings
    (str vs AnyUrl) which are safe to ignore - the code works correctly at runtime.

    AdCP 2.5+ supports parameterized format IDs with width/height/duration_ms fields.
    """

    def __str__(self) -> str:
        """Return human-readable format identifier for display in UIs."""
        return self.id

    def __repr__(self) -> str:
        """Return representation for debugging."""
        return f"FormatId(id='{self.id}', agent_url='{self.agent_url}')"

    def get_dimensions(self) -> tuple[int, int] | None:
        """Get dimensions from parameterized FormatId (AdCP 2.5).

        Returns:
            Tuple of (width, height) in pixels, or None if not specified.
        """
        if self.width is not None and self.height is not None:
            return (self.width, self.height)
        return None

    def get_duration_ms(self) -> float | None:
        """Get duration from parameterized FormatId (AdCP 2.5).

        Returns:
            Duration in milliseconds, or None if not specified.
        """
        return self.duration_ms


# --- Brand Manifest Models (AdCP v1.8.0) ---


class LogoAsset(SalesAgentBaseModel):
    """Logo asset with metadata."""

    url: str = Field(..., description="URL to logo asset")
    width: int | None = Field(None, ge=1, description="Logo width in pixels")
    height: int | None = Field(None, ge=1, description="Logo height in pixels")
    tags: list[str] | None = Field(None, description="Tags for logo usage (e.g., 'primary', 'square', 'white')")


class BrandColors(SalesAgentBaseModel):
    """Brand color palette."""

    primary: str | None = Field(None, pattern="^#[0-9A-Fa-f]{6}$", description="Primary brand color (hex)")
    secondary: str | None = Field(None, pattern="^#[0-9A-Fa-f]{6}$", description="Secondary brand color (hex)")
    accent: str | None = Field(None, pattern="^#[0-9A-Fa-f]{6}$", description="Accent color (hex)")
    background: str | None = Field(None, pattern="^#[0-9A-Fa-f]{6}$", description="Background color (hex)")
    text: str | None = Field(None, pattern="^#[0-9A-Fa-f]{6}$", description="Text color (hex)")


class FontGuidance(SalesAgentBaseModel):
    """Typography guidelines."""

    primary: str | None = Field(None, description="Primary font family")
    secondary: str | None = Field(None, description="Secondary font family")
    weights: list[str] | None = Field(None, description="Recommended font weights")


class BrandAsset(SalesAgentBaseModel):
    """Multimedia brand asset."""

    url: str = Field(..., description="URL to brand asset")
    asset_type: str = Field(..., description="Asset type (image, video, audio, etc.)")
    tags: list[str] | None = Field(None, description="Asset tags for categorization")
    width: int | None = Field(None, ge=1, description="Asset width in pixels")
    height: int | None = Field(None, ge=1, description="Asset height in pixels")
    duration: float | None = Field(None, ge=0, description="Duration in seconds (for video/audio)")


# --- Package Schemas (Extend adcp library for proper request/response separation) ---


class PackageRequest(LibraryPackageRequest):
    """Package request schema (for CreateMediaBuyRequest).

    Extends adcp library PackageRequest with internal fields.
    Used when CREATING media buys - has creative_ids/creatives/format_ids but no package_id/status.

    Library PackageRequest required fields per AdCP spec:
    - budget, pricing_option_id, product_id
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    # DELETED, not moved: tenant_id, media_buy_id, platform_line_item_id, created_at,
    # updated_at, metadata and a deprecated pricing_model. media-buy/package-request.json
    # (AdCP 3.1.1, the pinned version) declares none of the seven, and the persisted
    # MediaPackage row already owns each one -- media_buy_id and package_id are its primary
    # key, platform_line_item_id is written into its package_config, and the timestamps
    # belong to the parent media buy. Every read of those names on a package in src/ reaches
    # an ORM row or the internal MediaPackage carrier, never this DTO, so there was nothing
    # to route anywhere.
    #
    # pricing_model was the one of the seven a buyer could actually send, because declaring a
    # field is what admits it (pattern 7: the DTO IS the accepted shape). It was a legacy
    # alias for pricing_option_id with no producer anywhere in src/ or tests/, and the pin
    # names pricing_option_id as the only pricing selector -- it is in
    # package-request.json /required. A package that now carries pricing_model is an
    # undeclared field and follows pattern 7: VALIDATION_ERROR in dev, silently dropped in
    # production. Its three readers in media_buy_create.py went with it, which is why the
    # currency resolution there no longer has a legacy branch.

    # RE-STATED rather than inherited. Deleting this would restore the parent's
    # Ge(ge=0.0) but would also silently UN-DEPRECATE the field: the parent describes it
    # as "Impression goal for this package" while this declaration marks it Legacy, and
    # that description is buyer-visible in the tool schema.
    impressions: float | None = Field(
        None, ge=0.0, description="Legacy: Impression goal (use budget instead)", exclude=True
    )
    # The item type the PIN declares. media-buy/package-request.json types creatives[] as
    # core/creative-asset.json, and ``CreativeAssetRequest`` is this repo's one model of that
    # schema -- the same type ``SyncCreativesRequest.creatives`` carries, because the two tools
    # accept the same item.
    #
    # This used to point at ``Creative``, which extends the list_creatives RESPONSE model, so a
    # REQUEST field was validated against a listing shape. That is strictly WIDER: the listing
    # model types ``assets`` as an untyped dict, while the pin types it as a map of
    # discriminated AssetVariant objects, so an assets value carrying no ``asset_type`` cleared
    # the boundary and round-tripped unchanged. BR-UC-002 @T-UC-002-inv-015-6 (BR-RULE-015
    # INV-6) says that payload must be refused with INVALID_REQUEST. The identical field on
    # sync_creatives was corrected first; this was the remaining door, and it is why
    # update_media_buy REJECTED such a creative through the strict AdCPPackageUpdate while
    # create_media_buy accepted it.
    #
    # ``type: ignore[assignment]`` survives the correction and is not the same silence it was.
    # ``adcp.types.CreativeAsset`` is a RootModel UNION over the two codegen branches of a
    # oneOf; ``CreativeAssetRequest`` extends one branch (see its docstring for why the union
    # cannot be extended: a failing branch puts ``CreativeAsset1`` -- a name absent from AdCP --
    # into the buyer's error pointer). A branch subclass is not a static subtype of the union,
    # so mypy reports the mismatch for any correct spelling of this field. Same ignore, same
    # reason, on SyncCreativesRequest.creatives.
    creatives: list["CreativeAssetRequest"] | None = Field(  # type: ignore[assignment]
        None,
        min_length=1,
        max_length=100,
        description="Full creative objects to upload and assign at creation time (alternative to creative_ids)",
    )
    # The one field this DTO declares beyond the pin, and it survives on a reader census
    # rather than on "backward compatibility". media-buy/package-request.json declared
    # creative_ids up to AdCP 2.5 and dropped it at 3.0 in favour of `creatives` (full
    # objects), but the create flow still routes ids, not objects, through it: the inline
    # creative upload writes the merged ids back onto the package
    # (src/core/helpers/creative_helpers.py, model_copy), and _get_creative_ids
    # (src/core/tools/media_buy_create.py) reads them at every assignment, existence-check
    # and persistence site. Deleting it would break that path, so it stays declared and
    # exclude=True keeps it off the wire.
    creative_ids: list[str] | None = Field(
        None,
        description="Internal: List of creative IDs to assign (alternative to full creatives objects)",
        exclude=True,
    )
    # Override library TargetingOverlay -> our Targeting with internal fields
    targeting_overlay: Targeting | None = None


class Package(LibraryPackage):
    """Package response schema (for CreateMediaBuySuccess and responses).

    Extends adcp library Package with internal fields.
    Used in RESPONSES - has package_id/status but no creative_ids/format_ids (those become creative_assignments/format_ids_to_provide).

    Library Package required fields:
    - package_id, status
    """

    # A RESPONSE model keeps nothing it was not declared. The library parent sets
    # extra="allow", which STORES an unknown key and then serializes it, so deleting the
    # seven internal declarations below would have turned each one from a field that could
    # never reach the wire (Field(exclude=True)) into an extra that always does. That is the
    # opposite of what deleting them was for, and it is invisible: nothing refuses the
    # construction and the value simply appears in the buyer's document. "ignore" is the
    # right setting rather than "forbid" because a response has no forward-compatibility
    # reason to retain a sender's unknown keys and no reason to refuse them either --
    # a request's tolerance is pattern 7's business, not a response's.
    model_config = ConfigDict(extra="ignore")

    # DELETED, not moved, and for the same reason as on PackageRequest above: tenant_id,
    # media_buy_id, platform_line_item_id, created_at, updated_at, metadata and a
    # deprecated pricing_model. core/package.json (AdCP 3.1.1, the pinned version) declares
    # 29 properties and requires only package_id; none of the seven is among them. On this
    # class they were deader than on the request: no site under src/ or scripts/ ever
    # CONSTRUCTED a Package with one of them, and no site read one back. The four surviving
    # reads of those names on anything package-shaped belong to the ORM MediaPackage row
    # (src/core/database/repositories/media_buy.py and the _PackageData the list tool builds
    # from it), which is where a persisted package's tenant, parent buy, line item and
    # timestamps live.
    #
    # This class inherits extra="allow" from the library parent, so a caller that still
    # passes one of the seven is not refused -- the value is accepted and dropped. That is
    # the library's forward-compatibility choice, not a place for this class to declare a
    # field.

    # Note: No need for validate_required hack - library Package already has package_id and status as required fields!


# --- Media Buy Lifecycle ---
class CreateMediaBuyRequest(BuyerRequest, LibraryCreateMediaBuyRequest):
    """Extends library CreateMediaBuyRequest from AdCP spec.

    Per AdCP spec, the required fields are:
    - brand: BrandReference (with domain and optional brand_id)
    - packages: list[PackageRequest] (array of package configurations)
    - start_time: str | datetime ('asap' or ISO 8601 datetime)
    - end_time: datetime (ISO 8601 datetime)

    Optional fields:
    - context: dict (application-level context)
    - ext: dict (extension object for custom fields)
    - po_number: str (purchase order number)
    - reporting_webhook: dict (webhook configuration)
    """

    TAGS: ClassVar[tuple[str, ...]] = (
        "campaign",
        "media",
        "buy",
        "adcp",
    )

    # The spec's type, matching the library parent. REQUIRED, as the library declares it (create-media-buy-request.json /required lists
    # brand). Narrowing the TYPE back to the parent's must not touch the requiredness --
    # redeclaring it optional would relax a spec constraint, which is the weakening the
    # inheritance guard exists to catch and which broke
    # test_brand_field_is_required_on_create_media_buy_request.
    brand: LibraryBrandReference = Field(..., description="Brand reference")

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    # Narrowed to the local class, so the authentication block a request carries is the ONE
    # class every registration site names (src/core/schemas/notification.py). Same
    # nullability and default as the parent.
    push_notification_config: PushNotificationConfig | None = None

    # account and idempotency_key are REQUIRED by AdCP 3.1.1
    # (media-buy/create-media-buy-request.json /required = [account, brand, end_time,
    # idempotency_key, start_time]) and are inherited as required from the library type.
    # ``account`` was overridden to optional here on the grounds that "our impl resolves
    # identity at the transport layer, not from the request payload" -- an argument the
    # override's own comment undercut, since it cited update-media-buy-request.json's
    # /required list too, and salesagent-prkv.28 rejected exactly that argument for
    # update_media_buy and sync_creatives. This was the last surviving instance of it: the
    # same reasoning, the opposite outcome, in one comment. Our job on a required field is
    # compliance, not judgement, so the override is gone and all three tools agree.

    # Override packages to use our PackageRequest (which overrides targeting_overlay
    # to Targeting instead of library TargetingOverlay, enabling the legacy normalizer).
    # extra='forbid' prevents arbitrary field injection at buyer boundary.
    # RE-STATED: the parent carries MinLen(1) and this redeclaration dropped it, so an
    # empty array was accepted and travelled downstream. Restored by hand because the
    # element type is the local PackageRequest, not the parent's — deleting the
    # declaration would change the element type as well as the bound.
    packages: list[PackageRequest] | None = Field(None, min_length=1)

    @model_validator(mode="after")
    def _check_idempotency_key(self):
        """Reject a malformed idempotency_key with VALIDATION_ERROR (AdCP 16-255).

        Parity with UpdateMediaBuyRequest: create now emits the same tailored
        suggestion on a malformed key (#1417).
        """
        validate_idempotency_key_shape(self.idempotency_key)
        return self

    @model_validator(mode="after")
    def validate_timezone_aware(self):
        """Validate that datetime fields are timezone-aware.

        AdCP spec requires ISO 8601 datetime strings with timezone information.
        This validator ensures all datetime fields have timezone info.
        The literal string 'asap' is also valid per AdCP spec.
        """
        if self.start_time and self.start_time != "asap":
            if isinstance(self.start_time, datetime) and self.start_time.tzinfo is None:
                raise ValueError("start_time must be timezone-aware (ISO 8601 with timezone) or 'asap'")
        if self.end_time and self.end_time.tzinfo is None:
            raise ValueError("end_time must be timezone-aware (ISO 8601 with timezone)")
        return self

    # Helper properties for common access patterns
    @property
    def flight_start_date(self) -> date | None:
        """Extract date from start_time for display purposes."""
        # start_time is StartTiming (RootModel[datetime | 'asap']); unwrap via .root
        inner = self.start_time.root if self.start_time else None
        if isinstance(inner, datetime):
            return inner.date()
        return None

    @property
    def flight_end_date(self) -> date | None:
        """Extract date from end_time for display purposes."""
        return self.end_time.date() if self.end_time else None

    def get_total_budget(self) -> Decimal:
        """Calculate total budget by summing all package budgets.

        Per AdCP spec, budget is specified at the package level, not the media buy level.
        Returns Decimal — budget is money, float is wrong for money.
        """
        total = Decimal(0)
        if self.packages:
            for package in self.packages:
                if package.budget:
                    total += Decimal(str(package.budget))
        return total

    def get_product_ids(self) -> list[str]:
        """Extract unique product IDs from packages per AdCP spec.

        Per AdCP spec, packages use product_id (singular, required) field.
        Returns list of unique product IDs (no duplicates).
        """
        if self.packages:
            product_ids = []
            for package in self.packages:
                if package.product_id:
                    product_ids.append(package.product_id)
            # Remove duplicates while preserving order
            return list(dict.fromkeys(product_ids))
        return []


class CheckMediaBuyStatusRequest(SalesAgentBaseModel):
    media_buy_id: str
    strategy_id: str | None = Field(
        None,
        description="Optional strategy ID for consistent simulation/testing context",
    )


class CheckMediaBuyStatusResponse(SalesAgentBaseModel):
    media_buy_id: str
    status: str  # pending_creative, active, paused, completed, failed
    packages: list[dict[str, Any]] | None = None
    budget_spent: Budget | None = None
    budget_remaining: Budget | None = None
    creative_count: int = 0


# --- Additional Schema Classes ---
class MediaPackage(SalesAgentBaseModel):
    package_id: str
    name: str
    delivery_type: Literal["guaranteed", "non_guaranteed"]
    cpm: float
    impressions: int
    # Accept library FormatId (not our extended FormatId) to avoid validation errors
    # when Product from library returns LibraryFormatId instances
    format_ids: list[LibraryFormatId]  # FormatId objects per AdCP spec
    targeting_overlay: Targeting | None = None
    product_id: str | None = None  # Product ID for this package
    budget: float | None = None  # Budget allocation in the currency specified by the pricing option
    creative_ids: list[str] | None = None  # Creative IDs to assign to this package


class AssetStatus(SalesAgentBaseModel):
    asset_id: str | None = None  # Asset identifier
    creative_id: str | None = None  # GAM creative ID (may be None for pending/failed)
    status: str  # Status: draft, active, submitted, failed, etc.
    message: str | None = None  # Status message
    # Seller-side concept enrichment (#1506). AdCP exposes read-only
    # concept_id/concept_name on list_creatives but carries no concept on
    # sync_creatives, so there is no protocol writer. An adapter may derive a
    # fallback concept from its native creative grouping (e.g. the GAM Order)
    # and surface it here. This is explicitly NOT the authoritative buyer-side
    # concept: concept_source records the provenance so a future
    # buyer-supplied concept can be distinguished and take precedence.
    concept_id: str | None = None  # Seller-derived concept grouping id
    concept_name: str | None = None  # Human-readable concept name
    concept_source: str | None = None  # Provenance marker, e.g. "gam_order"


# Unified update models
class PackageUpdate(SalesAgentBaseModel):
    """Updates to apply to a specific package."""

    package_id: str
    active: bool | None = None  # True to activate, False to pause
    budget: float | None = Field(None, ge=0)  # Budget allocation in the currency specified by the pricing option
    impressions: int | None = None  # Direct impression goal (overrides budget calculation)
    cpm: float | None = None  # Update CPM rate
    daily_budget: float | None = None  # Daily spend cap
    daily_impressions: int | None = None  # Daily impression cap
    pacing: Literal["even", "asap", "front_loaded"] | None = None
    creative_ids: list[str] | None = None  # Update creative assignments
    targeting_overlay: Targeting | None = None  # Package-specific targeting refinements


class UpdatePackageRequest(SalesAgentBaseModel):
    """Update one or more packages within a media buy.

    Uses PATCH semantics: Only packages mentioned are affected.
    Omitted packages remain unchanged.
    To remove a package from delivery, set active=false.
    To add new packages, use create_media_buy or add_packages (future tool).
    """

    media_buy_id: str
    packages: list[PackageUpdate]  # List of package updates
    today: date | None = None  # For testing/simulation


# AdCP-compliant supporting models for update-media-buy-request
class AdCPPackageUpdate(LibraryPackageUpdate):
    """Package-specific update extending library type.

    Inherits all fields from library (budget, paused, targeting_overlay,
    creative_assignments, creatives, bid_price, ext, impressions, pacing,
    package_id).

    Adds creative_ids — spec-mandated field missing from library codegen.
    TODO(adcp-library): Remove creative_ids once upstream codegen adds it.

    Overrides targeting_overlay to use the local Targeting subclass so
    extensions (collection_list / collection_list_exclude) are typed at the
    request boundary instead of dropping through library extra="allow" as
    raw dicts. Mirrors the PackageRequest.targeting_overlay override pattern.
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())
    # Spec field missing from library codegen (adcp#208)
    creative_ids: list[str] | None = None
    # Override library targeting_overlay so local extensions (collection_list)
    # are coerced to typed CollectionListReference at the boundary, not dicts.
    targeting_overlay: Targeting | None = None

    # Fields that are immutable once a package is created (BR-RULE-198). They are
    # not valid update fields, so the AdCP package-update schema rejects them via a
    # root `not` constraint. We surface that as INVALID_REQUEST (a known, named
    # business-rule rejection) rather than letting them fall through to the generic
    # extra="forbid" VALIDATION_ERROR — and, because this runs before extra-mode
    # handling, it also closes the production (extra="ignore") silent-drop gap.
    _IMMUTABLE_PACKAGE_FIELDS: ClassVar[frozenset[str]] = frozenset({"product_id", "format_ids", "pricing_option_id"})

    @model_validator(mode="before")
    @classmethod
    def _validate_package_update_shape(cls, data: Any) -> Any:
        """Enforce the package-update request shape with INVALID_REQUEST.

        Two structural ("which package, and is this field allowed?") rules,
        both graded INVALID_REQUEST by the spec rather than the generic
        VALIDATION_ERROR:
        - package_id identifies the package being updated; it is required
          (BR-UC-003 ext-h). Raising here (mode="before") surfaces a helpful
          INVALID_REQUEST + suggestion instead of Pydantic's bare
          "Field required" VALIDATION_ERROR.
        - immutable fields (BR-RULE-198) cannot be changed post-create; the
          AdCP package-update schema rejects them via a root ``not`` constraint.
          Running before extra-mode handling means dev (extra="forbid") and
          prod (extra="ignore") both reject them as INVALID_REQUEST rather than
          a generic VALIDATION_ERROR / silent drop.
        """
        if isinstance(data, dict):
            # Raised as a PYDANTIC error, not a typed AdCPInvalidRequestError, and the
            # loc is what makes it correct on both counts.
            #
            # WHY NOT THE TYPED ERROR: this validator runs inside pydantic, which FastMCP
            # drives through a TypeAdapter BEFORE the tool body. A typed error raised there
            # never reaches RegistryTool.run's AdcpFailure catch (the tool has not been
            # entered) and is not a pydantic ValidationError either, so nothing on the MCP
            # path converts it --
            # FastMCP masked it into a prose ToolError and the buyer received no envelope at
            # all: no code, no field, no suggestion. A pydantic error is the one shape every
            # boundary already converts.
            #
            # WHY THE loc: core/error.json defines `field` as "JSONPath-lite" and its own
            # example is request-rooted -- 'packages[0].targeting'. Naming the loc here lets
            # pydantic nest it under the enclosing collection, so the buyer gets
            # 'packages[0].package_id'. The previous entry-relative 'package_id' named a
            # field without saying which package it belonged to, which for a multi-package
            # update is not actionable.
            if not data.get("package_id"):
                raise PydanticCoreValidationError.from_exception_data(
                    "UpdateMediaBuyPackage",
                    [
                        InitErrorDetails(
                            type=PydanticCustomError("missing", "package_id is required on a package update"),
                            loc=("package_id",),
                            input=data,
                        )
                    ],
                )
            present = sorted(f for f in cls._IMMUTABLE_PACKAGE_FIELDS if f in data)
            if present:
                # One entry per immutable field, each carrying its own loc, so `issues[]`
                # names every offender and `field` derives from issues[0] as the pin's
                # "populate field from issues[0]" MUST requires.
                raise PydanticCoreValidationError.from_exception_data(
                    "UpdateMediaBuyPackage",
                    [
                        InitErrorDetails(
                            type=PydanticCustomError(
                                "readOnly", "{name} is immutable on a package update", {"name": name}
                            ),
                            loc=(name,),
                            input=data,
                        )
                        for name in present
                    ],
                )
        return data


# AdCP idempotency_key constraint (create- and update-media-buy-request.json):
# minLength 16, maxLength 255, pattern ^[A-Za-z0-9_.:-]{16,255}$.
_IDEMPOTENCY_KEY_MIN = 16
_IDEMPOTENCY_KEY_MAX = 255
_IDEMPOTENCY_KEY_CHARSET = re.compile(r"^[A-Za-z0-9_.:-]+$")


def validate_idempotency_key_shape(key: str | None) -> None:
    """Enforce the AdCP idempotency_key length/charset constraint.

    Shared by create and update so both reject a malformed key identically.

    A length or character-set violation is INVALID_REQUEST, not VALIDATION_ERROR.
    The pinned schemas declare ``idempotency_key`` with ``minLength: 16``,
    ``maxLength: 255`` and ``pattern: ^[A-Za-z0-9_.:-]{16,255}$`` (verified in
    3.1/account/sync-accounts-request.json and its siblings), and core/error.json
    puts "violates schema constraints" under INVALID_REQUEST while VALIDATION_ERROR
    covers "business rules BEYOND schema validation". A minLength/pattern violation
    is the first: a JSON-Schema validator alone rejects it.

    This said VALIDATION_ERROR "per the idempotency storyboard and the value/range
    taxonomy". Those are downstream artifacts, and the spec-grounding rule is that
    they are a cross-check, never the authority -- grounding protocol behavior in
    them instead of the schema is how a contract ends up inverted. The schema wins.

    ``None`` is allowed here — required-ness is enforced separately (and is
    deliberately relaxed on update).
    """
    if key is None:
        return
    if len(key) < _IDEMPOTENCY_KEY_MIN:
        raise AdCPInvalidRequestError(
            field="idempotency_key",
        )
    if len(key) > _IDEMPOTENCY_KEY_MAX:
        raise AdCPInvalidRequestError(
            field="idempotency_key",
        )
    if not _IDEMPOTENCY_KEY_CHARSET.match(key):
        raise AdCPInvalidRequestError(
            field="idempotency_key",
        )


class UpdateMediaBuyRequest(BuyerRequest, LibraryUpdateMediaBuyRequest):
    """Update media buy request extending library type.

    Inherits all AdCP fields from library (paused, start_time, end_time,
    packages, push_notification_config, context, reporting_webhook, ext).
    In adcp 3.9 all fields are optional (consolidated from oneOf variants).

    Overrides:
    - start_time: accept Literal["asap"] (backward compat with A2A path)
    - packages: use our AdCPPackageUpdate (adds creative_ids)
    - budget: campaign-level budget (not in library — convenience field)

    No internal field is declared here. ``today`` used to be, under ``exclude=True``,
    and ``_update_media_buy_impl`` read it as ``req.today or date.today()``. Nothing set
    it -- no transport could (the marker kept it off all three announced shapes) and no
    caller in src or tests passed it -- so that read already always yielded
    ``date.today()``, which is now what it says. See docs/development/building-tools.md,
    "Decisions this forces, and the answers".
    """

    TAGS: ClassVar[tuple[str, ...]] = (
        "campaign",
        "update",
        "management",
        "adcp",
    )

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    # Narrowed to the local class; see CreateMediaBuyRequest.
    push_notification_config: PushNotificationConfig | None = None

    # account and idempotency_key are REQUIRED by AdCP 3.1.1
    # (media-buy/update-media-buy-request.json /required = [idempotency_key, account,
    # media_buy_id]) and are inherited as required from the library type. They were
    # overridden to optional here, with the override itself noting it should go away "when
    # the update BDD contract requires keys". Relaxing a REQUIRED field is not a neutral
    # convenience: the spec makes idempotency_key CLIENT-generated so that resending after a
    # lost response is at-most-once, and a key we do not demand is a guarantee we do not
    # provide -- a retried update executes twice. Our job on a required field is compliance,
    # not judgement, so the overrides are gone.

    # Override datetime fields to accept raw strings (A2A path sends ISO strings)
    start_time: datetime | Literal["asap"] | None = None  # type: ignore[assignment]
    end_time: datetime | None = None
    # Override packages to use our extended type with creative_ids
    # RE-STATED, same reason as the create side: the local element type is
    # AdCPPackageUpdate, so the declaration must stay and the parent's MinLen(1) has to be
    # restored on it rather than inherited.
    packages: list[AdCPPackageUpdate] | None = Field(None, min_length=1)
    # Campaign-level budget (not in library spec — convenience field)
    # Bare float is accepted so transport wrappers can preserve existing DB currency
    # when the caller updates only the amount.
    # `budget` is NOT declared here on purpose. AdCP 3.1.1
    # media-buy/update-media-buy-request.json lists 16 properties and budget is not among
    # them -- budget is PACKAGE-level (media-buy/package-update.json /properties/budget), and
    # adcp.types.UpdateMediaBuyRequest declares none either. The schema and the SDK are the
    # contract; a campaign-level budget update is expressed as packages[].budget, which this
    # codebase implements (media_buy_update.py reads pkg_update.budget).
    #
    # The conformance storyboard DOES carry a top-level "Update campaign-level budget"
    # scenario (BR-UC-003 @T-UC-003-alt-budget). That does not license a field the schema does
    # not define: we do not support anything the schema rejects. The disagreement is upstream
    # and is filed for reconciliation there, not accommodated here.

    @model_validator(mode="after")
    def _check_idempotency_key(self):
        """Reject a malformed idempotency_key with VALIDATION_ERROR (AdCP 16-255)."""
        validate_idempotency_key_shape(self.idempotency_key)
        return self

    @model_validator(mode="after")
    def validate_timezone_aware(self):
        """Validate that datetime fields are timezone-aware.

        AdCP spec requires ISO 8601 datetime strings with timezone information.
        This validator ensures all datetime fields have timezone info.
        The literal string 'asap' is also valid per AdCP v1.7.0.
        """
        if self.start_time and self.start_time != "asap" and self.start_time.tzinfo is None:
            raise ValueError("start_time must be timezone-aware (ISO 8601 with timezone) or 'asap'")
        if self.end_time and self.end_time.tzinfo is None:
            raise ValueError("end_time must be timezone-aware (ISO 8601 with timezone)")
        return self

    def has_updatable_fields(self) -> bool:
        """Check whether this request includes at least one updatable field.

        Returns True if any field beyond the identifier (media_buy_id)
        is set. Used by _build_update_request to enforce BR-RULE-022.
        """
        return any(
            f is not None
            for f in (
                self.paused,
                self.start_time,
                self.end_time,
                self.packages,
                self.push_notification_config,
                self.reporting_webhook,
                self.context,
                self.ext,
            )
        )

    # Backward compatibility properties (deprecated)
    @property
    def flight_start_date(self) -> date | None:
        """DEPRECATED: Use start_time instead. Backward compatibility only."""
        if isinstance(self.start_time, datetime):
            warnings.warn("flight_start_date is deprecated. Use start_time instead.", DeprecationWarning, stacklevel=2)
            return self.start_time.date()
        return None

    @property
    def flight_end_date(self) -> date | None:
        """DEPRECATED: Use end_time instead. Backward compatibility only."""
        if self.end_time:
            warnings.warn("flight_end_date is deprecated. Use end_time instead.", DeprecationWarning, stacklevel=2)
            return self.end_time.date()
        return None


# --- Human-in-the-Loop Task Queue ---


class HumanTask(SalesAgentBaseModel):
    """Task requiring human intervention."""

    task_id: str
    task_type: (
        str  # creative_approval, permission_exception, configuration_required, compliance_review, manual_approval
    )
    principal_id: str
    adapter_name: str | None = None
    status: str = "pending"  # pending, assigned, in_progress, completed, failed, escalated
    priority: str = "medium"  # low, medium, high, urgent

    # Context
    media_buy_id: str | None = None
    creative_id: str | None = None
    operation: str | None = None
    error_detail: str | None = None
    context_data: dict[str, Any] | None = None

    # Assignment
    assigned_to: str | None = None
    assigned_at: datetime | None = None

    # Timing
    created_at: datetime
    updated_at: datetime
    due_by: datetime | None = None
    completed_at: datetime | None = None

    # Resolution
    resolution: str | None = None  # approved, rejected, completed, cannot_complete
    resolution_detail: str | None = None
    resolved_by: str | None = None


class CreateHumanTaskRequest(SalesAgentBaseModel):
    """Request to create a human task."""

    task_type: str
    priority: str = "medium"
    adapter_name: str | None = None  # Added to match HumanTask schema

    # Context
    media_buy_id: str | None = None
    creative_id: str | None = None
    operation: str | None = None
    error_detail: str | None = None
    context_data: dict[str, Any] | None = None

    # SLA
    due_in_hours: int | None = None  # Hours until due


class CreateHumanTaskResponse(SalesAgentBaseModel):
    """Response from creating a human task."""

    task_id: str
    status: str
    due_by: datetime | None = None


class GetPendingTasksRequest(SalesAgentBaseModel):
    """Request for pending human tasks."""

    principal_id: str | None = None  # Filter by principal
    task_type: str | None = None  # Filter by type
    priority: str | None = None  # Filter by minimum priority
    assigned_to: str | None = None  # Filter by assignee
    include_overdue: bool = True


class GetPendingTasksResponse(NestedModelSerializerMixin, SalesAgentBaseModel):
    """Response with pending tasks."""

    tasks: list[HumanTask]
    total_count: int
    overdue_count: int


class AssignTaskRequest(SalesAgentBaseModel):
    """Request to assign a task."""

    task_id: str
    assigned_to: str


class VerifyTaskRequest(SalesAgentBaseModel):
    """Request to verify if a task was completed correctly."""

    task_id: str
    expected_outcome: dict[str, Any] | None = None  # What the task should have accomplished


class VerifyTaskResponse(SalesAgentBaseModel):
    """Response from task verification."""

    task_id: str
    verified: bool
    actual_state: dict[str, Any]
    expected_state: dict[str, Any] | None = None
    discrepancies: list[str] = []


class MarkTaskCompleteRequest(SalesAgentBaseModel):
    """Admin request to mark a task as complete with verification."""

    task_id: str
    override_verification: bool = False  # Force complete even if verification fails
    completed_by: str


# Targeting capabilities
class GetTargetingCapabilitiesRequest(SalesAgentBaseModel):
    """Query targeting capabilities for channels."""

    channels: list[str] | None = None  # If None, return all channels
    include_aee_dimensions: bool = True


class TargetingDimensionInfo(SalesAgentBaseModel):
    """Information about a single targeting dimension."""

    key: str
    display_name: str
    description: str
    data_type: str
    required: bool = False
    values: list[str] | None = None


class ChannelTargetingCapabilities(SalesAgentBaseModel):
    """Targeting capabilities for a specific channel."""

    channel: str
    overlay_dimensions: list[TargetingDimensionInfo]
    aee_dimensions: list[TargetingDimensionInfo] | None = None


class GetTargetingCapabilitiesResponse(NestedModelSerializerMixin, SalesAgentBaseModel):
    """Response with targeting capabilities."""

    capabilities: list[ChannelTargetingCapabilities]


class CheckAXERequirementsRequest(SalesAgentBaseModel):
    """Check if required AXE dimensions are supported."""

    channel: str
    required_dimensions: list[str]


class CheckAXERequirementsResponse(SalesAgentBaseModel):
    """Response for AXE requirements check."""

    supported: bool
    missing_dimensions: list[str]
    available_dimensions: list[str]


# Creative macro is now a simple string passed via AXE axe_signals


# --- Signal Discovery ---
class SignalDeployment(LibraryPlatformDeployment):
    """Extends library PlatformDeployment with internal signal deployment fields.

    Library provides: platform, account, is_live, type, activation_key,
    deployed_at, estimated_activation_duration_minutes.

    Local additions (internal-only, excluded from responses):
    - scope: Derived from deployment type for internal routing
    - decisioning_platform_segment_id: Platform-specific segment ID after activation
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    scope: Literal["platform-wide", "account-specific"] = Field(
        default="platform-wide", description="Deployment scope (internal)", exclude=True
    )
    decisioning_platform_segment_id: str | None = Field(
        default=None, description="Platform-specific segment ID (internal)", exclude=True
    )


class Signal(LibrarySignal):
    """Extends library Signal with internal fields and local deployment/pricing types.

    Library provides: signal_agent_segment_id, name, description, signal_type,
    data_provider, coverage_percentage, deployments, pricing_options — all
    inherited from AdCP spec.

    Local overrides:
    - deployments: local SignalDeployment (has scope, decisioning_platform_segment_id)
    - Internal fields with Field(exclude=True): tenant_id, created_at, updated_at, metadata
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    # Override types that differ from library
    deployments: list[SignalDeployment] = Field(  # type: ignore[assignment]
        ..., min_length=1, description="Array of platform deployments"
    )

    # Internal fields (not in AdCP spec, excluded from serialization)
    tenant_id: str | None = Field(None, description="Internal: Tenant ID for multi-tenancy", exclude=True)
    created_at: datetime | None = Field(None, description="Internal: Creation timestamp", exclude=True)
    updated_at: datetime | None = Field(None, description="Internal: Last update timestamp", exclude=True)
    metadata: dict[str, Any] | None = Field(None, description="Internal: Additional metadata", exclude=True)

    # Backward compatibility properties (deprecated)
    # Note: signal_id is now a library field in adcp 3.6.0 (SignalId | None)
    # The old @property signal_id that mapped to signal_agent_segment_id is removed
    # to avoid conflict with the new library field.

    @property
    def pricing(self) -> Any | None:
        """Backward compat: return the inner model of the first pricing option.

        DEPRECATED: Use pricing_options instead.
        Provides .cpm, .currency etc. from the first pricing option's root model.
        Returns None if pricing_options is empty.
        """
        if self.pricing_options:
            return self.pricing_options[0].root
        return None

    @property
    def type(self) -> str:
        """Backward compatibility for type.

        DEPRECATED: Use signal_type instead.
        This property will be removed in a future version.
        """
        warnings.warn(
            "type is deprecated and will be removed in a future version. Use signal_type instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.signal_type


class SignalFilters(LibrarySignalFilters):
    """Signal filters per AdCP get-signals-request schema.

    Extends library type - all fields inherited.
    """

    pass  # All fields inherited from library


# Re-export the library type; callers use .signal_spec, .filters, .max_results directly.
GetSignalsRequest = LibraryGetSignalsRequest


class GetSignalsResponse(NestedModelSerializerMixin, LibraryGetSignalsResponse):
    """Extends library GetSignalsResponse with local Signal type.

    Library provides: signals, errors, context, ext — all inherited from AdCP spec.
    Local override: signals uses local Signal type (with exclude=True internal fields).
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    signals: list[Signal] = Field(..., description="Array of available signals")  # type: ignore[assignment]


# --- Signal Activation ---
class ActivateSignalRequest(LibraryActivateSignalRequest):
    """Extends library ActivateSignalRequest with local extension fields.

    Library provides: signal_agent_segment_id, deployments, context, ext.
    Local extensions: campaign_id, media_buy_id (unused in impl, kept for API compat).

    NOTE: ActivateSignalResponse is NOT migrated — library uses RootModel
    discriminated union (success|error) which is fundamentally incompatible
    with the local flat model pattern.
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    # Extension fields (not in library spec)
    campaign_id: str | None = Field(None, description="Optional campaign ID to activate signal for")
    media_buy_id: str | None = Field(None, description="Optional media buy ID to activate signal for")

    @property
    def signal_id(self) -> str:
        """DEPRECATED: Use signal_agent_segment_id instead."""
        warnings.warn(
            "signal_id is deprecated. Use signal_agent_segment_id instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.signal_agent_segment_id


class ActivateSignalResponse(SalesAgentBaseModel):
    """Response from signal activation.

    NOT migrated to library base (evaluated in ):
    1. Historically "library uses RootModel[SuccessVariant | ErrorVariant] — cannot
       add fields". That reason is STALE at adcp 6.6: ActivateSignalResponse is a
       union TypeAlias, not a RootModel, so the SyncAccountsResponse approach
       (subclass the success variant directly — src/core/schemas/account.py:141)
       would work. It is no longer what keeps this model local.
    2. Library has no signal_id field (no request correlation in response)
    3. Library uses structured list[Deployment] vs our generic activation_details dict
    4. Library enforces atomic success/error; we allow both simultaneously

    Reasons 2-4 still hold, and together they say something stronger than "missing
    the envelope status": this model is spec-divergent in SHAPE. So when #1353
    registers activate_signal on a transport, the fix is to rebuild it on the library
    success variant — never to patch a status field onto this shape, which would
    manufacture false conformance against an branch that requires deployments.
    """

    signal_id: str = Field(..., description="Activated signal ID")
    activation_details: dict[str, Any] | None = Field(None, description="Platform-specific activation details")
    errors: list[Error] | None = Field(None, description="Optional error reporting")
    context: ContextObject | None = Field(None, description="Application-level context echoed from the request")


# --- Simulation and Time Progression Control ---
class SimulationControlRequest(SalesAgentBaseModel):
    """Control simulation time progression and events."""

    strategy_id: str = Field(..., description="Strategy ID to control (must be simulation strategy with 'sim_' prefix)")
    action: Literal["jump_to", "reset", "set_scenario"] = Field(..., description="Action to perform on the simulation")
    parameters: dict[str, Any] = Field(default_factory=dict, description="Action-specific parameters")
    context: ContextObject | None = Field(None, description="Application-level context echoed from the request")


class SimulationControlResponse(SalesAgentBaseModel):
    """Response from simulation control operations."""

    status: Literal["ok", "error"] = "ok"
    message: str | None = None
    current_state: dict[str, Any] | None = None
    simulation_time: datetime | None = None
    context: ContextObject | None = Field(None, description="Application-level context echoed from the request")


# --- Authorized Properties Constants ---

# Valid property types per AdCP specification
PROPERTY_TYPES = ["website", "mobile_app", "ctv_app", "dooh", "podcast", "radio", "streaming_audio"]

# Valid verification statuses
VERIFICATION_STATUSES = ["pending", "verified", "failed"]

# Valid identifier types by property type (AdCP compliant mappings)
IDENTIFIER_TYPES_BY_PROPERTY_TYPE = {
    "website": ["domain", "subdomain"],
    "mobile_app": ["bundle_id", "store_id"],
    "ctv_app": ["roku_store_id", "amazon_store_id", "samsung_store_id", "lg_store_id"],
    "dooh": ["venue_id", "network_id"],
    "podcast": ["podcast_guid", "rss_feed_url"],
    "radio": ["station_call_sign", "stream_url"],
    "streaming_audio": ["platform_id", "stream_id"],
}

# Property form field requirements
PROPERTY_REQUIRED_FIELDS = ["property_type", "name", "identifiers", "publisher_domain"]

# Property form validation rules
PROPERTY_VALIDATION_RULES = {
    "name": {"min_length": 1, "max_length": 255},
    "publisher_domain": {"min_length": 1, "max_length": 255},
    "property_type": {"allowed_values": PROPERTY_TYPES},
    "verification_status": {"allowed_values": VERIFICATION_STATUSES},
    "tag_id": {"pattern": r"^[a-z0-9_]+$", "max_length": 50},
}

# Supported file types for bulk upload
SUPPORTED_UPLOAD_FILE_TYPES = [".json", ".csv"]

# Property form error messages
PROPERTY_ERROR_MESSAGES = {
    "missing_required_field": "Property type, name, and publisher domain are required",
    "invalid_property_type": "Invalid property type: {property_type}. Must be one of: {valid_types}",
    "invalid_file_type": "Only JSON and CSV files are supported",
    "no_file_selected": "No file selected",
    "at_least_one_identifier": "At least one identifier is required",
    "identifier_incomplete": "Identifier {index}: Both type and value are required",
    "invalid_json": "Invalid JSON format: {error}",
    "invalid_tag_id": "Tag ID must contain only letters, numbers, and underscores",
    "tag_already_exists": "Tag '{tag_id}' already exists",
    "all_fields_required": "All fields are required",
    "property_not_found": "Property not found",
    "tenant_not_found": "Tenant not found",
}


# --- Authorized Properties (AdCP Spec) ---
# Use library types directly - all fields inherited from AdCP spec
# V3: Property uses property-specific Identifier, not generic Identifier
from adcp.types.generated_poc.core.property import (
    Identifier as PropertySpecificIdentifier,
)  # TODO: no stable alias in adcp.types (different from adcp.types.Identifier)

PropertyIdentifier: TypeAlias = PropertySpecificIdentifier  # noqa: UP040 — runtime re-export
Property: TypeAlias = LibraryProperty  # noqa: UP040 — runtime re-export


class PropertyTagMetadata(SalesAgentBaseModel):
    """Metadata for a property tag."""

    name: str = Field(..., description="Human-readable name for this tag")
    description: str = Field(..., description="Description of what this tag represents")


# --- Get Media Buys Types ---
# DeliveryStatus: imported from adcp library at top of file (all 6 values).


# Reason why a delivery snapshot is not available. Aliased to the library enum
# rather than redeclared: the local copy carried only two of the pinned three
# members (it was missing SNAPSHOT_PERMISSION_DENIED), which is exactly the drift
# that copying an enum out of the spec produces.
SnapshotUnavailableReason = LibrarySnapshotUnavailableReason


# Approval status for a creative assignment in a get_media_buys response. Aliased to
# the library enum rather than redeclared, on the same basis as
# SnapshotUnavailableReason above: the local copy and the pinned enum have identical
# members (pending_review/approved/rejected), and a hand-kept copy only ever drifts.
# Until now producer and consumer disagreed by type — _map_creative_status
# (src/core/tools/media_buy_list.py:619) returned the LOCAL enum while CreativeApproval
# inherits the LIBRARY one — and that worked only because both are StrEnums that
# validate by value. The alias closes the split by construction.
ApprovalStatus = LibraryApprovalStatus


class Snapshot(LibraryGetMediaBuysSnapshot):
    """Near-real-time delivery snapshot for a package.

    Every field this class used to declare — as_of, impressions, spend,
    staleness_seconds, clicks, pacing_index, delivery_status, currency — is a
    verbatim match for the library type's, so they are inherited rather than
    copied (Pattern #1). Kept as a local subclass rather than a plain alias so
    the name stays stable for the ~2 adapter construction sites and so
    GetMediaBuysPackage can narrow to it.
    """


class GetMediaBuysPackage(NestedModelSerializerMixin, LibraryGetMediaBuysPackage):
    """Package details within a GetMediaBuys response.

    Grounded on the library type so the item chain above it can be too. Only the
    fields that genuinely narrow the library's are redeclared; package_id, budget,
    bid_price, product_id, paused and start/end_time are inherited verbatim.

    start_time/end_time used to be declared here as ``str``. The library (and the
    pinned schema) type them as aware datetimes, which is strictly more correct:
    Pydantic still accepts the ISO strings production passes and re-emits ISO under
    ``mode="json"``, so the wire is unchanged while the model stops accepting
    arbitrary strings.
    """

    targeting_overlay: Targeting | None = Field(
        default=None,
        description="Targeting overlay echoed from the most recent create_media_buy or update_media_buy. Includes any property_list / collection_list references the buyer attached, so callers can verify what was persisted without replaying the request.",
    )
    # snapshot, creative_approvals and snapshot_unavailable_reason are inherited.
    # Snapshot is a local subclass that adds NO fields of its own, so narrowing to it
    # would buy nothing a Pattern #4 override is meant to buy. Neither can
    # be narrowed here: list[] is invariant, so list[our CreativeApproval] is not a
    # list[library CreativeApproval] even though the element type now IS a subclass —
    # and inheriting costs nothing, since our subclass adds no fields and instances of
    # it satisfy the library annotation. targeting_overlay is a local subclass, and the
    # nested-model serializer above re-dumps it through its own serializer.


class GetMediaBuysMediaBuy(AlwaysIncludeFieldsMixin, LibraryGetMediaBuysMediaBuy):
    """Media buy details in a GetMediaBuys response.

    Grounded on the library type, which makes the pinned item contract enforceable
    at the construction site: ``confirmed_at`` and ``revision`` are REQUIRED on
    media_buys[] in AdCP 3.1.1, and every caller must now supply them or fail to
    typecheck. That is the point — they are populated from the persisted columns
    rather than defaulted (GH #1928).

    Inherited verbatim: media_buy_id, status, currency, total_budget, confirmed_at,
    revision, created_at, updated_at, valid_actions.
    """

    # Not a library field — this seller carries the buyer's campaign reference back
    # on the item so a buyer can correlate without a second lookup.
    buyer_campaign_ref: str | None = Field(default=None, description="Buyer campaign reference")
    packages: list[GetMediaBuysPackage] = Field(..., description="Packages within this media buy")

    # ``confirmed_at`` is spec-required AND nullable on the pinned item schema:
    # ``{"type": ["string", "null"]}`` and in ``required``, because it "may be null
    # until seller commitment occurs in deferred/manual approval flows". Dropping it
    # produced a response that failed item-level validation for every
    # not-yet-confirmed buy — the same class of silent omission as the missing
    # envelope status (GH #1900), caught by the wire-graded UC-019 scenario rather
    # than by review. AlwaysIncludeFieldsMixin keeps it present.
    # Derived from the pin: the item schema lists confirmed_at in `required` and
    # types it ["string","null"], so it is the one field that must stay on the wire
    # as an explicit null. This adopter was already correct; deriving means it stays
    # correct across a pin bump without anyone re-checking.

    # packages are local subclasses: the wire serializer re-dumps them through their own.
    _SERIALIZE_NESTED_MODELS: ClassVar[bool] = True


class CompleteTaskRequest(BuyerRequest, AdcpVersionEnvelope):
    """Request to complete a task.

    There used to be TWO models for this one tool: this one, and a legacy
    ``CompleteTaskRequest`` declaring resolution / resolution_detail / resolved_by. Neither
    subclassed the other and neither was SDK-grounded, so the "two vocabularies need
    reconciling" note this docstring used to carry resolved the only way it could: the
    legacy one had zero callers and is deleted.

    The base is ``AdcpVersionEnvelope`` because the pin ships no complete-task request
    schema (the only ``complet*`` file in 3.1 is ``enums/completion-source.json``) and
    ``adcp.types`` exports no ``CompleteTaskRequest``. When the SDK provides no type, the
    rule is to subclass the SDK's base envelope so the request still carries
    ``adcp_version`` / ``adcp_major_version`` -- ``SalesAgentBaseModel`` has no fields, so
    under it this request could not carry a protocol version at all. Every other request DTO
    reaches the same envelope through its pinned generated type. Not exported from
    ``adcp.types``; import the full generated path.
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    task_id: str = Field(..., description="The task to complete")
    # REQUIRED, and only the two values the tool acts on. The impl used to re-raise
    # AdCPValidationError(field="status") for anything else while this typed it as an
    # optional free string -- the accepted shape declared twice, with buyers shown the
    # loose one on all three transports and the strict one enforced at runtime.
    status: Literal["completed", "failed"] = Field(..., description="Completion status")
    response_data: dict[str, Any] | None = Field(default=None, description="Structured result payload")
    error_message: str | None = Field(default=None, description="Failure detail, when the task failed")
    context: ContextObject | None = Field(default=None, description="Application-level context")


class CompleteTaskResponse(AdcpResponse):
    """Response from completing a task.

    On the SDK's base envelopes for the same reason the request is: the pin defines no
    complete-task schema, and a response that cannot carry ``adcp_version`` is not a response
    in this protocol. The impl returned a bare ``dict`` while both its siblings returned
    response models, so the REST boundary's ``response.model_dump(mode="json")`` raised
    AttributeError on the one tool of the three that had no model.

    ``ProtocolEnvelope`` for the second half of the same argument: a response that cannot carry
    ``status`` is not one either. Every tool with a pinned schema gets the envelope through
    that schema's ``allOf``; this one has no schema to get it from, so it names the base
    directly. That is not a local invention -- it is the same class the other thirteen inherit.
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    # task_id and message REDECLARE envelope fields to make them required: this response
    # always names the task it completed and always says so. status is NOT redeclared -- the
    # envelope's TaskStatus is the pinned enum, and narrowing it to a bare str is how a value
    # outside that enum would have reached the wire.
    task_id: str = Field(..., description="The task that was completed")
    message: str = Field(..., description="Human-readable confirmation")
    completed_at: str = Field(..., description="ISO-8601 completion time")
    completed_by: str = Field(..., description="Principal that completed it")


class GetTaskStatusRequest(BuyerRequest, LibraryGetTaskStatusRequest):
    """Request to retrieve one task.

    Extends the SDK's model rather than restating it. The previous docstring recorded an
    OWNER DECISION of 2026-08-31 -- "the pinned SDK ships no GetTaskStatusRequest ... define ours
    now, and when the SDK ships one, make it the BASE CLASS rather than maintaining a
    parallel definition". That instruction was already satisfiable when it was written: the
    SDK ships the type under a DIFFERENT NAME, ``GetTaskStatusRequest`` in
    ``generated_poc.protocol``, because the spec calls the operation ``get-task-status-status``
    while the tool is ``get_task_status``. A search for the type by the TOOL's name found nothing
    and a hand-written parallel was declared instead.

    The hand-written version reproduced the SDK's field set exactly -- same fields, same
    single required one -- which is itself the answer to whether protocol/
    get-task-status-status-request.json and core/tasks-get-request.json are the same operation:
    someone already decided they were, then wrote the fields out instead of inheriting. What
    the parallel lost was the version envelope: under ``SalesAgentBaseModel`` this request
    could not carry ``adcp_version`` / ``adcp_major_version`` at all.

    It also narrowed ``include_history`` and ``include_result`` from ``bool | None`` to
    ``bool``. That narrowing is dropped with the parallel: the SDK's nullability is the
    spec's, and a DTO does not get to be stricter than the schema it claims to implement.
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())


class TaskSummary(LibraryTaskSummary):
    """One row of ``list_tasks``, extending the pinned ``tasks[]`` item.

    The pinned item and ``get-task-status-status-response.json`` share a byte-identical seven-field
    core -- task_id, task_type, status, created_at, updated_at, completed_at, has_webhook --
    and differ only in ``domain`` vs ``protocol``, which are the same axis under two names
    (``Domain`` is media-buy/signals/creative; ``AdcpProtocol`` adds four more). So the two
    responses below are built from one derivation, not two.

    The extra fields are this seller's own and the pin permits them
    (``additionalProperties: true`` on both response schemas). They are DECLARED rather than
    passed through as extras so the shape a buyer receives is written down somewhere.
    """

    context_id: str | None = Field(default=None, description="Workflow context this task belongs to")
    tool_name: str | None = Field(default=None, description="Non-spec: the seller-side tool the step invokes")
    owner: str | None = Field(default=None, description="Non-spec: principal | publisher | system")
    associated_objects: list[dict[str, Any]] = Field(
        default_factory=list, description="Non-spec: objects this task acts on"
    )
    error_message: str | None = Field(default=None, description="Non-spec: failure detail when the task failed")
    summary: dict[str, Any] | None = Field(default=None, description="Non-spec: request highlights")


class GetAdcpCapabilitiesRequest(BuyerRequest, LibraryGetAdcpCapabilitiesRequest):
    """Extends the pinned GetAdcpCapabilitiesRequest.

    Adds nothing, and that is the point. The tool registry names the class WE own, never
    the library model: a row pointing at the SDK class could never be narrowed, because
    ``@omit_declared`` would pop fields off a model shared with every other consumer of
    ``adcp.types``. An empty subclass is the correct starting shape -- the statement is
    ownership, not content.
    """

    TAGS: ClassVar[tuple[str, ...]] = (
        "capabilities",
        "discovery",
        "adcp",
    )


class GetAdcpCapabilitiesResponse(LibraryGetAdcpCapabilitiesResponse, AdcpResponse):
    """The get_adcp_capabilities response."""


class ListTasksRequest(BuyerRequest, LibraryListTasksRequest):
    """Extends the pinned ListTasksRequest.

    Adds nothing, for the same reason as :class:`GetAdcpCapabilitiesRequest` above.
    """


class ListTasksResponse(NestedModelSerializerMixin, LibraryListTasksResponse, AdcpResponse):
    """Extends the pinned ListTasksResponse.

    Was a raw dict ``{tasks, total, offset, limit, has_more}`` -- six violations of
    protocol/list-tasks-response.json (GH #2201): query_summary, pagination and status absent
    at the envelope; task_type and domain absent per item; and ``type`` emitted where the pin
    says ``task_type``, carrying ``WorkflowStep.step_type`` ("tool_call", "approval"), which
    is not a member of enums/task-type.json at all.

    Nothing graded it, because the response coverage gate enumerated MODELS and a tool
    returning a dict has none. The tool-keyed gate added alongside this is what makes the
    absence visible.
    """

    #: NOT redeclared as ``list[TaskSummary]``. The parent already types this
    #: ``list[Task]`` and ``TaskSummary`` extends ``Task``, so narrowing it would be an
    #: invariance error for no gain: what reaches the wire is decided by the OBJECTS the
    #: tool builds and by the nested-model serializer, not by the annotation:
    #: ``TaskSummary`` carries six non-spec fields the parent's ``Task`` does not
    #: declare, and serializing by the DECLARED type would drop them silently.


class GetTaskStatusResponse(LibraryGetTaskStatusResponse, AdcpResponse):
    """Extends the pinned GetTaskStatusResponse (the spec names the task ``get-task-status-status``).

    Was a raw dict (GH #2202): ``protocol`` and ``task_type`` absent, and ``type`` emitted in
    task_type's place carrying ``step_type``. Same root cause and same fix as
    :class:`ListTasksResponse` -- see that docstring; the two share their derivation.

    The extras mirror :class:`TaskSummary`'s, plus the two only a single-task read returns.
    """

    context_id: str | None = Field(default=None, description="Workflow context this task belongs to")
    tool_name: str | None = Field(default=None, description="Non-spec: the seller-side tool the step invokes")
    owner: str | None = Field(default=None, description="Non-spec: principal | publisher | system")
    associated_objects: list[dict[str, Any]] = Field(
        default_factory=list, description="Non-spec: objects this task acts on"
    )
    error_message: str | None = Field(default=None, description="Non-spec: failure detail when the task failed")
    request_data: dict[str, Any] | None = Field(default=None, description="Non-spec: the request that opened the task")


class GetMediaBuysRequest(BuyerRequest, LibraryGetMediaBuysRequest):
    """Extends the library GetMediaBuysRequest.

    Was hand-defined against SalesAgentBaseModel "because adcp 3.6.0 is not yet required" --
    a comment that outlived its pin. The SDK (adcp 6.6.0, AdCP 3.1.1) ships this type, and
    re-declaring the field set NARROWED it: ext, include_history, include_snapshot,
    include_webhook_activity, pagination and webhook_activity_limit are all in
    get-media-buys-request.json and were absent here, so they could not be advertised or
    accepted on any transport. Inheriting is what critical pattern #1 requires, and it is
    what stops the app model from silently shrinking the spec.

    status_filter is NOT overridden. The hand-written class typed it ``Any`` "to accept a
    MediaBuyStatus or a list", but the library already types it
    ``MediaBuyStatus | StatusFilter | None`` -- which is that union, precisely. Re-declaring
    it as ``Any`` only WEAKENED the constraint, which is what the schema-inheritance guard
    exists to stop.
    """

    TAGS: ClassVar[tuple[str, ...]] = (
        "media_buy",
        "status",
        "creative",
        "snapshot",
        "monitoring",
        "adcp",
    )

    # The library sets extra="allow"; this agent uses the ENVIRONMENT-based mode (critical
    # pattern #7): "ignore" in production for forward compatibility, "forbid" elsewhere so a
    # typo or a stale field fails fast in dev and CI. Inheriting the library's config
    # silently opted this model out of that -- an unknown field was accepted everywhere.
    model_config = ConfigDict(extra=get_pydantic_extra_mode())


class GetMediaBuysResponse(NestedModelSerializerMixin, LibraryGetMediaBuysResponse, AdcpResponse):
    """Extends library GetMediaBuysResponse.

    Library provides: media_buys, errors, context, pagination, sandbox, ext, and the
    protocol envelope — including ``status``, which AdCP 3.1.1
    (core/protocol-envelope.json, required: ["status"]) marks REQUIRED on every task
    response envelope and which this model carried on no wire at all while it
    extended SalesAgentBaseModel (GH #1900).

    Re-based rather than given a hand-declared status field, deliberately: the
    alignment suite's coverage gate admits a response model by its BASES containing
    an adcp.types class, so a locally-declared field would satisfy the letter of
    #1900 while leaving this model invisible to grading — and the next spec-required
    field would go missing exactly as silently. Following ListAccountsResponse
    (src/core/schemas/account.py).

    errors and context are dropped rather than redeclared: the library types them
    identically (list[Error] | None, ContextObject | None), which is what every call
    site already passes. The hand-written model_dump is gone too — it re-serialized
    media_buys exactly as NestedModelSerializerMixin already does generically.
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    # Redeclared for Pattern #4 (nested serialization with the local item subclass);
    # the library types it as a Sequence.
    media_buys: list[GetMediaBuysMediaBuy]


# Re-export product schemas for backward compatibility.
# These were extracted to src.core.schemas.product but must remain
# importable from src.core.schemas.
from src.core.schemas.product import (  # noqa: E402
    GetProductsRequest as GetProductsRequest,
)
from src.core.schemas.product import (
    GetProductsResponse as GetProductsResponse,
)
from src.core.schemas.product import (
    Placement as Placement,
)
from src.core.schemas.product import (
    Product as Product,
)
from src.core.schemas.product import (
    ProductCard as ProductCard,
)
from src.core.schemas.product import (
    ProductCardDetailed as ProductCardDetailed,
)
from src.core.schemas.product import (
    ProductCatalog as ProductCatalog,
)
from src.core.schemas.product import (
    ProductFilters as ProductFilters,
)
