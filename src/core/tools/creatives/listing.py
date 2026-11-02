"""List creatives implementation, MCP wrapper, and A2A raw function."""

import logging
import time
from datetime import UTC, datetime
from typing import Any, cast, get_args

from adcp.types import AssignedPackage, Assignments, SnapshotUnavailableReason
from adcp.types.generated_poc.core.creative_item import CreativeItem
from adcp.types.generated_poc.core.creative_item import CreativeItem1 as MediaItem
from adcp.types.generated_poc.core.creative_item import CreativeItem2 as TextItem
from adcp.types.generated_poc.core.creative_variable import CreativeVariable
from pydantic import TypeAdapter, ValidationError

from src.core.audit_logger import get_audit_logger
from src.core.database.models import CreativeAssignment as DBCreativeAssignment
from src.core.database.repositories.uow import CreativeUoW
from src.core.errors.codes import ErrorCode
from src.core.errors.details import CapabilityRefusalDetails, EntityRefDetails, ValidationDetails
from src.core.exceptions import AdCPCapabilityNotSupportedError, AdCPInvalidRequestError
from src.core.helpers import enum_value, log_tool_activity
from src.core.logging_config import log_safe
from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas import (
    Creative,
    Error,
    ListCreativesRequest,
    ListCreativesResponse,
    canonical_agent_url,
)
from src.core.tools.creatives._assets import ASSET_MAP

logger = logging.getLogger(__name__)

#: The typed variables list the pin declares, as a validator. The stored value comes from
#: the untyped data blob, so it is checked here on the row-to-model read — the same
#: treatment ``ASSET_MAP`` gives the asset map.
VARIABLE_LIST: TypeAdapter[list[CreativeVariable]] = TypeAdapter(list[CreativeVariable])

#: core/pagination-request.json's own default for ``max_results``, applied when the buyer
#: sends no pagination object at all.
_DEFAULT_MAX_RESULTS = 50

#: What this seller's opaque pagination cursor carries, and nothing else. The buyer never
#: parses a cursor — core/pagination-request.json calls it "Opaque" — so the encoding is
#: the seller's business; prefixing it means a value from somewhere else is recognisably
#: not ours instead of being read as an offset by coincidence.
_CURSOR_PREFIX = "off:"


def _cursor_for_offset(offset: int) -> str:
    """The opaque cursor naming where the next page starts."""
    return f"{_CURSOR_PREFIX}{offset}"


def _offset_from_cursor(cursor: str | None) -> int:
    """Where the requested page starts: 0 for no cursor, otherwise the cursor's offset.

    A cursor this seller did not mint is REFUSED rather than treated as the first page.
    Silently restarting would answer a paging request with page one, which a buyer walking
    pages cannot distinguish from a genuine page and would read as a duplicate of results
    it already has. INVALID_REQUEST is the pinned code for a malformed request value.
    """
    if cursor is None:
        return 0
    remainder = cursor.removeprefix(_CURSOR_PREFIX) if cursor.startswith(_CURSOR_PREFIX) else None
    if remainder is None or not remainder.isdigit():
        raise AdCPInvalidRequestError(
            field="pagination.cursor",
            details=ValidationDetails(rejected_value=cursor),
        )
    return int(remainder)


def _log_blob_drop(shape: str, field_label: str, log_context: str, *, value_type: str | None = None) -> None:
    """Emit the one drop-warning template shared by every blob coercer.

    All four blob-drop sites — the non-scalar/non-dict/non-list whole-value drops and the
    null-element drop inside a list — route through this single emitter so the message shape
    cannot drift out of lockstep: a reworded stem or a new attribution field lands in one place
    instead of four. ``value_type`` present renders the "...value of type X..." form (a corrupt
    whole value); ``value_type`` absent renders the "...element..." form (a corrupt element inside
    an otherwise-valid list). ``log_context`` is the optional operator-attribution suffix built by
    :func:`_blob_log_context` (empty for the pure-function callers).
    """
    if value_type is None:
        logger.warning("Dropping %s %s element from creative listing%s", shape, field_label, log_context)
    else:
        logger.warning(
            "Dropping %s %s value of type %s from creative listing%s",
            shape,
            field_label,
            value_type,
            log_context,
        )


def _coerce_blob_scalar(value: Any, field_label: str, *, log_context: str = "") -> str | None:
    """Coerce an untyped JSON-blob value to a spec string field.

    Fields like ``concept_id``/``concept_name`` are strings per the AdCP response
    schema but live in the untyped JSON ``data`` blob, where an out-of-band producer
    may write a non-string scalar (e.g. a numeric CM360 group id). Scalars are
    stringified. A non-scalar (list/dict) is corrupt for a string field, so it is
    dropped with a warning — surfaced in logs (No Quiet Failures) rather than projected
    as a Python repr — instead of crashing the whole listing on one bad row.

    ``field_label`` is required (never defaulted) so a dropped value names its own field
    in the log rather than borrowing a sibling's attribution. ``log_context`` is an optional
    operator-attribution suffix (the corrupt row's creative/tenant/principal ids, built by
    :func:`_blob_log_context`) appended to the drop warning so the bad row can be traced and
    repaired; it defaults to empty for the pure-function callers (unit tests). Surfacing the
    drop to the buyer as a response advisory (``ListCreativesResponse.errors[]``) instead of
    log-only is a deliberate deferral, tracked in #1779.
    """
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, (int, float)):  # bool is an int subclass; str(True)="True" is acceptable
        return str(value)
    _log_blob_drop("non-scalar", field_label, log_context, value_type=type(value).__name__)
    return None


def _coerce_blob_assets(value: Any, field_label: str, *, log_context: str = "") -> Any:
    """Validate an untyped JSON-blob value into the typed ``Creative.assets`` map.

    ``assets`` is read from the untyped ``data`` blob, where an out-of-band producer may
    have written anything. The stored value is validated against the library's asset
    union (``core/creative-asset.json``) here, in the one place a row becomes a model; a
    value that does not validate is corrupt and dropped to ``None`` with a warning (No
    Quiet Failures) instead of failing Creative validation and crashing the whole listing
    on one bad row -- the object-field sibling of :func:`_coerce_blob_scalar` /
    :func:`_coerce_blob_str_list`. An empty ``{}`` is preserved: ``assets`` is required on
    the sync input, so ``{}`` is the presence-preserving projection.
    """
    if value is None:
        return None
    try:
        return ASSET_MAP.validate_python(value)
    except ValidationError:
        _log_blob_drop("invalid-assets", field_label, log_context, value_type=type(value).__name__)
        return None


def _coerce_blob_str_list(value: Any, field_label: str, *, log_context: str = "") -> list[str] | None:
    """Coerce an untyped JSON-blob value to a spec ``list[str]`` field.

    ``Creative.tags`` is typed ``list[str] | None`` but is read from the untyped
    ``data`` blob, where a malformed value (a bare string, or a list with numeric /
    object / null elements) would fail Creative validation and crash the whole listing
    on one bad row — the same untyped-blob hazard :func:`_coerce_blob_scalar` handles for
    the scalar concept fields. A non-list value is corrupt for a list field and dropped
    to ``None`` with a warning. Within a list a ``null`` element is corrupt for an
    ``items: {type: string}`` list (a JSON ``null`` is not an absent value here) and is
    dropped with a warning; every other element is coerced via :func:`_coerce_blob_scalar`
    (scalars stringified, non-scalars dropped+logged), so ``[1, 2] -> ["1", "2"]`` and
    ``[{...}]`` drops the bad element. ``log_context`` is the same optional
    operator-attribution suffix documented on :func:`_coerce_blob_scalar`.

    Finally an empty (or fully-emptied) list collapses to ``None`` so ``exclude_none``
    omits the key: the pinned 3.1.1 ``creative/list-creatives-response`` schema permits
    both ``[]`` and omission, and this list field standardizes on omission (the object-field
    sibling :func:`_coerce_blob_assets` instead *preserves* an empty ``{}`` — see its docstring).
    That collapse is a valid-input serialization choice, not a corruption drop, so — unlike
    the drops above — it is logged at ``debug`` (traceability), never ``warning``.
    """
    if value is None:
        return None
    if not isinstance(value, list):
        _log_blob_drop("non-list", field_label, log_context, value_type=type(value).__name__)
        return None
    coerced: list[str] = []
    for element in value:
        if element is None:
            _log_blob_drop("null", field_label, log_context)
            continue
        scalar = _coerce_blob_scalar(element, field_label, log_context=log_context)
        if scalar is not None:
            coerced.append(scalar)
    if not coerced:
        logger.debug("Collapsing empty %s list to absent in creative listing", field_label)
        return None
    return coerced


def _coerce_blob_variables(value: Any, log_context: str = "") -> list[CreativeVariable] | None:
    """Validate the stored dynamic-content variables into the typed list the pin declares.

    ``variables`` is "Dynamic content variables (DCO slots) for this creative", and
    core/creative-variable.json sources them from the creative platform — ``variable_id`` is
    "Variable identifier on the creative platform". Like ``concept_id``, AdCP standardizes
    no variables INPUT on sync_creatives, so the value is populated out-of-band into the
    data blob and read back here. The blob is untyped, so a value that does not validate is
    corrupt and dropped with a warning rather than failing the whole listing — the same
    treatment tags, assets and the concept fields get.
    """
    if value is None:
        return None
    try:
        return VARIABLE_LIST.validate_python(value)
    except ValidationError:
        _log_blob_drop("invalid-variables", "variables", log_context, value_type=type(value).__name__)
        return None


def _items_from_assets(assets: Any) -> list[CreativeItem] | None:
    """Project the creative's assets onto the pin's multi-asset ``items`` array.

    core/creative-item.json is an "Item within a multi-asset creative format ... for
    carousel products, native ad components, and other formats composed of multiple
    distinct elements", discriminated by ``asset_kind``: a ``media`` item carries
    ``content_uri``, a ``text`` item carries ``content``. Those elements are exactly what
    this seller already holds — the assets the buyer synced, keyed by asset_id — so items
    are DERIVED from them rather than stored a second time. An asset that fits neither
    branch (no url and no text content) contributes no item.
    """
    if not assets:
        return None
    items: list[CreativeItem] = []
    for asset_id, asset in assets.items():
        # A slot declaring min/max > 1 stores a LIST, and each member is its own item.
        candidates = asset if isinstance(asset, list) else [asset]
        for candidate in candidates:
            asset_type = str(getattr(candidate, "asset_type", None) or "media")
            url_value = getattr(candidate, "url", None)
            content = getattr(candidate, "content", None)
            if url_value is not None:
                items.append(
                    CreativeItem(
                        root=MediaItem(
                            asset_kind="media",
                            asset_type=asset_type,
                            asset_id=asset_id,
                            content_uri=str(url_value),
                        )
                    )
                )
            elif content is not None:
                items.append(
                    CreativeItem(
                        root=TextItem(
                            asset_kind="text",
                            asset_type=asset_type,
                            asset_id=asset_id,
                            content=content,
                        )
                    )
                )
    return items or None


def _assignments_block(assignments_by_creative: dict[str, list[DBCreativeAssignment]], creative_id: str) -> Assignments:
    """The creative's package assignments in the shape the response schema declares.

    ``assignment_count`` is REQUIRED on the block (list-creatives-response.json), so a
    creative with no assignments carries a zero rather than being silently omitted — the
    buyer asked whether this creative is assigned anywhere, and "nowhere" is an answer.
    ``assigned_date`` comes from the assignment row's own created_at, the only timestamp
    that records when the assignment was made.
    """
    rows = assignments_by_creative.get(creative_id, [])
    return Assignments(
        assignment_count=len(rows),
        assigned_packages=[AssignedPackage(package_id=row.package_id, assigned_date=row.created_at) for row in rows],
    )


#: The six members list-creatives-response.json marks REQUIRED on every creative item.
#: They survive every projection: a response that dropped one would not validate, whatever
#: the buyer selected.
_ALWAYS_ON_THE_WIRE = frozenset({"creative_id", "name", "format_id", "status", "created_date", "updated_date"})

#: The two `fields` members that do not name one response field. list-creatives-request.json
#: says so for the first: "The 'concept' value returns both concept_id and concept_name";
#: and a snapshot is either the object or the machine-readable reason it is absent, so
#: selecting it selects both halves of that answer.
_FIELD_SELECTS = {
    "concept": ("concept_id", "concept_name"),
    "snapshot": ("snapshot", "snapshot_unavailable_reason"),
}

#: Every response member the `fields` vocabulary can NAME, derived from the enum on
#: ListCreativesRequest.fields. A projection may only drop these: a member the buyer has no
#: way to ask for is one they had no way to decline either, so `assets` -- which the pinned
#: enum does not list, though the response schema declares it -- survives a projection
#: rather than being silently dropped from a creative whose content it is.
_SELECTABLE_WIRE_FIELDS = frozenset(
    wire_field
    # list[Field1] | None -> list[Field1] -> Field1, the generated enum of the pinned
    # `fields` members; iterating the enum is what keeps this list from being hand-copied.
    for member in get_args(get_args(ListCreativesRequest.model_fields["fields"].annotation)[0])[0]
    for wire_field in _FIELD_SELECTS.get(member.value, (member.value,))
)


def _project_to_fields(creative: Creative, fields: list[Any] | None) -> Creative:
    """Narrow a creative to the ``fields`` the buyer selected, per list-creatives-request.json.

    ``fields`` is "Specific fields to include in response (omit for all fields)" over a
    closed 13-member enum, so an absent list means the whole creative and a present one
    means those members ONLY.

    Only the OPTIONAL members are narrowed. The same pinned response schema marks six
    members required on every item, so a projection that dropped them would answer the
    buyer's selection with a document that violates the contract it was selected from —
    and `fields` cannot express keeping them, since all six are enum members a buyer may
    omit. Keeping them is the reading that satisfies both halves of the pin.

    A member is dropped by being set to None, which ``exclude_none`` then omits — the same
    mechanism that omits an absent tag list, rather than a second way of shaping the wire.
    """
    if not fields:
        return creative
    keep = set(_ALWAYS_ON_THE_WIRE)
    for field in fields:
        name = enum_value(field)
        keep.update(_FIELD_SELECTS.get(name, (name,)))
    dropped = dict.fromkeys(_SELECTABLE_WIRE_FIELDS - keep)
    return creative.model_copy(update=dropped)


def _blob_log_context(creative_id: str, tenant_id: str, principal_id: str) -> str:
    """Operator-attribution suffix appended to a blob-coercion drop warning.

    Names the corrupt row (creative/tenant/principal) so an operator can trace and repair the
    out-of-band data defect — the drop warnings are otherwise per-field but per-*row* anonymous.
    Passed by the ``_list_creatives_impl`` row loop; the coercers default the suffix to empty so
    their pure-function unit tests stay attribution-free.

    Each id is passed through :func:`log_safe` before interpolation: ``creative_id`` (buyer-supplied)
    and the tenant/principal ids reach this log line, so an embedded CR/LF would forge log entries
    (CodeQL ``py/log-injection``). Neutralizing CR/LF here — the single choke point every drop
    warning routes through — closes the taint on all four drop sites at once.
    """
    return (
        f" (creative_id={log_safe(creative_id)} tenant_id={log_safe(tenant_id)} principal_id={log_safe(principal_id)})"
    )


def _list_creatives_impl(
    req: "ListCreativesRequest",
    identity: ResolvedIdentity,
) -> ListCreativesResponse:
    """List and search creative library (AdCP v2.5 spec endpoint).

    Advanced filtering and search endpoint for the centralized creative library.
    Supports pagination, sorting, and multiple filter criteria.

    Args:
        req: Typed list-creatives request — EVERY request value, including the two
            internal ``format`` / ``page`` fields, which are why this is typed to
            ListCreativesRequest and not to the buyer-facing ListCreativesRequest
        identity: ResolvedIdentity with principal/tenant info (transport-agnostic)

    Returns:
        ListCreativesResponse with filtered creative assets and pagination info
    """
    from typing import Literal

    # Derive flat DB-query params from the structured request.
    req_filters = req.filters
    # Internal fields, read off the request like every other value it carries. They were
    # ``_impl`` PARAMETERS until this was fixed, which meant a caller could hand the reader
    # a page or a format the request it was answering did not describe.
    # Where this page starts, read from the buyer's cursor. core/pagination-request.json
    # types ``cursor`` as an "Opaque cursor from a previous response to fetch the next
    # page", and core/pagination-response.json returns one "Only present when has_more is
    # true" -- so a cursor is the seller's own bookmark handed back, and this seller's
    # bookmark is the offset the next page starts at. Opaque to the buyer, who never parses
    # it; a cursor that is not one this seller minted is refused rather than guessed at.
    offset = _offset_from_cursor(req.pagination.cursor if req.pagination else None)
    # This status string is matched against the RAW persisted `creatives.status` column
    # (CreativeRepository.get_by_principal), while the value rendered on the wire is
    # derived from it below — and for a row whose stored status is not a CreativeStatus
    # member, the reader substitutes a placeholder. The two therefore disagree on
    # purpose: an unreadable row appears in an UNFILTERED read (rendered `processing`,
    # plus an errors[] advisory) and is ABSENT from `list_creatives(status="processing")`.
    # That asymmetry is deliberate, and it is the opposite of the choice made for the
    # unfiltered read on purpose: a filtered read is scoped to a status the buyer NAMED,
    # and a row we could not parse is not KNOWN to be that status, so excluding it answers
    # the buyer's actual question. Omitting it from the unfiltered read would instead be a
    # second lie ("this creative does not exist") and would desynchronise
    # query_summary.returned from total_matching. Do NOT "fix" this by mapping the
    # placeholder back onto the filter — that would report the row as confirmed
    # `processing`. Graded by
    # tests/integration/test_list_creatives_unrecognized_status.py::TestFilteredReadExcludesTheUnreadableRow.
    statuses = [enum_value(s) for s in req_filters.statuses] if req_filters and req_filters.statuses else None
    tags = req_filters.tags if req_filters else None
    tags_any = req_filters.tags_any if req_filters else None
    has_variables = req_filters.has_variables if req_filters else None
    # has_served asks which creatives have served at least one impression. This seller
    # holds no per-creative delivery: no column in the schema records a creative's
    # impressions or last-served date. Answering the filter would mean guessing, and
    # IGNORING it would answer a different question than the buyer asked -- so it is
    # refused. core/creative-filters.json grants an explicit "SHOULD ignore" licence to
    # three sales-agent-specific filters and NOT to this one, and the pinned error table
    # gives UNSUPPORTED_FEATURE for "Requested feature not supported by this seller", with
    # recovery=correctable: the buyer drops the filter and retries.
    if req_filters is not None and req_filters.has_served is not None:
        raise AdCPCapabilityNotSupportedError(
            details=CapabilityRefusalDetails(
                capability="filters.has_served",
                rejected_value=str(req_filters.has_served).lower(),
            ),
            field="filters.has_served",
        )
    creative_ids = req_filters.creative_ids if req_filters else None
    # format_id is an object (core/format-id.json); the repository matches both members, and
    # the agent_url half goes through the ONE canonicalization the spec makes a MUST before
    # two references may be treated as the same format.
    format_ids = (
        [(canonical_agent_url(f.agent_url), f.id) for f in req_filters.format_ids]
        if req_filters and req_filters.format_ids
        else None
    )
    created_after_dt = req_filters.created_after if req_filters else None
    created_before_dt = req_filters.created_before if req_filters else None
    search = req_filters.name_contains if req_filters else None
    # Deduplicated, order preserved: a buyer may name the same media buy twice and the
    # filter that gets APPLIED names it once — which is what query_summary.filters_applied
    # then reports (POST-S7), and what keeps the assignment join from multiplying rows.
    effective_media_buy_ids = (
        list(dict.fromkeys(req_filters.media_buy_ids)) if req_filters and req_filters.media_buy_ids else []
    )
    # v3.1 concept_ids filter has no flat equivalent — it arrives only via the structured
    # filters object and must be threaded into the DB query (not merely reported in
    # filters_applied), or it would be silently dropped. (#1493)
    effective_concept_ids = req_filters.concept_ids if req_filters else None

    sort_by = enum_value(req.sort.field) if req.sort and req.sort.field else "created_date"
    valid_sort_order: Literal["asc", "desc"] = cast(
        Literal["asc", "desc"],
        enum_value(req.sort.direction) if req.sort and req.sort.direction else "desc",
    )

    # core/pagination-request.json defaults max_results to 50 and caps it at 100, and the
    # DTO refuses anything outside 1..100 before this runs, so the value needs no clamping
    # here -- only the default when the buyer sent no pagination object at all.
    limit = req.pagination.max_results if req.pagination and req.pagination.max_results else _DEFAULT_MAX_RESULTS

    start_time = time.time()

    # Authentication is REQUIRED (creatives contain sensitive data): unlike a discovery
    # tool, this returns creative assets that are principal-specific. The boundary refused
    # an anonymous caller; the ResolvedIdentity carries the principal by type.
    principal_id = identity.principal.principal_id
    tenant = identity.tenant

    creatives = []
    total_count = 0
    # Advisories for rows whose stored status this reader cannot parse. Bound here (not a
    # parameter) so the loop handler below has a container to surface into; emitted on the
    # response's errors[] at the bottom of this function.
    unreadable_status_advisories: list[Error] = []

    with CreativeUoW(tenant.tenant_id) as uow:
        assert uow.creatives is not None
        result = uow.creatives.get_by_principal(
            principal_id,
            statuses=statuses,
            format=None,
            tags=tags,
            tags_any=tags_any,
            has_variables=has_variables,
            creative_ids=creative_ids,
            format_ids=format_ids,
            created_after=created_after_dt,
            created_before=created_before_dt,
            search=search,
            media_buy_ids=effective_media_buy_ids or None,
            concept_ids=effective_concept_ids,
            sort_by=sort_by,
            sort_order=valid_sort_order,
            offset=offset,
            limit=limit,
        )
        db_creatives = result.creatives
        total_count = result.total_count

        # include_assignments DEFAULTS TO TRUE (list-creatives-request.json), so the block
        # is built unless the buyer switched it off, and one query answers the whole page.
        assignments_by_creative = (
            uow.creatives.assignments_by_creative([row.creative_id for row in db_creatives], principal_id)
            if req.include_assignments is not False
            else {}
        )

        # Convert to schema objects
        for db_creative in db_creatives:
            # Handle content_uri - required field even for snippet creatives
            # For snippet creatives, provide an HTML-looking URL to pass validation
            snippet = db_creative.data.get("snippet") if db_creative.data else None
            if snippet:
                content_uri = (
                    db_creative.data.get("url") or "<script>/* Snippet-based creative */</script>"
                    if db_creative.data
                    else "<script>/* Snippet-based creative */</script>"
                )
            else:
                content_uri = (
                    db_creative.data.get("url") or "https://placeholder.example.com/missing.jpg"
                    if db_creative.data
                    else "https://placeholder.example.com/missing.jpg"
                )

            # Build Creative directly with explicit types to satisfy mypy
            from src.core.schemas import FormatId, url

            # Build FormatId with optional parameters (AdCP 2.5 format templates)
            format_kwargs: dict[str, Any] = {
                "agent_url": url(db_creative.agent_url),
                "id": db_creative.format or "",
            }
            # Add format parameters if present
            if db_creative.format_parameters:
                params = db_creative.format_parameters
                if "width" in params:
                    format_kwargs["width"] = params["width"]
                if "height" in params:
                    format_kwargs["height"] = params["height"]
                if "duration_ms" in params:
                    format_kwargs["duration_ms"] = params["duration_ms"]

            format_obj = FormatId(**format_kwargs)

            # Ensure datetime fields are timezone-aware (database may store naive datetimes)
            if isinstance(db_creative.created_at, datetime):
                created_at_dt = (
                    db_creative.created_at.replace(tzinfo=UTC)
                    if db_creative.created_at.tzinfo is None
                    else db_creative.created_at
                )
            else:
                created_at_dt = datetime.now(UTC)

            if isinstance(db_creative.updated_at, datetime):
                updated_at_dt = (
                    db_creative.updated_at.replace(tzinfo=UTC)
                    if db_creative.updated_at.tzinfo is None
                    else db_creative.updated_at
                )
            else:
                updated_at_dt = datetime.now(UTC)

            # AdCP v1 spec compliant - only spec fields
            # Get assets dict from database (all production data uses AdCP v2.4 format)
            assets_dict = db_creative.data.get("assets", {}) if db_creative.data else {}

            # Convert string status to CreativeStatus enum
            from src.core.schemas import CreativeStatus

            try:
                status_enum = CreativeStatus(db_creative.status)
            except ValueError:
                # A stored status that is not a CreativeStatus member. AdCP 3.1.1
                # list-creatives-response.json makes `status` REQUIRED on every item and
                # $refs a CLOSED 6-member enum with no `unknown`, so some value must be
                # emitted — every choice makes some claim, which is why the errors[]
                # advisory below, not the placeholder, is the honest part of this handler.
                # `processing` is the placeholder because it is the only member asserting
                # no completed evaluation, no deliverability and no seller MUST;
                # `pending_review` is the worst choice (it asserts processing already
                # succeeded AND that the seller owes a decision, i.e. "wait for us").
                #
                # The advisory code is PINNED to CONFIGURATION_ERROR: normalize_advisory_errors
                # used to collapse any unclassified code to SERVICE_UNAVAILABLE /
                # recovery=transient — which would tell the buyer to retry a permanently
                # bad row forever. Honest caveat: CONFIGURATION_ERROR's pinned prose says
                # "prevents handling the request" and here the request IS handled; it is
                # nonetheless the only wire-standard code whose recovery (`terminal`) and
                # remediation ("surface to a human at the seller — the buyer cannot resolve
                # a seller-side deployment misconfiguration and MUST NOT auto-retry") both
                # match. INVALID_STATE is `correctable` (implies the buyer can fix the
                # request — false); SERVICE_UNAVAILABLE is `transient` (implies retry).
                logger.warning(
                    "Creative %s (tenant %s) has unreadable stored status %r; reporting it as "
                    "'processing' and surfacing an advisory",
                    db_creative.creative_id,
                    tenant.tenant_id,
                    db_creative.status,
                )
                unreadable_status_advisories.append(
                    # The stored status is INTERNAL state -- by definition not in the
                    # AdCP creative vocabulary, which is why this branch fired -- so it
                    # does not go on the buyer's wire. The logger above already records
                    # it for the operator. The buyer gets the code (whose sentence and
                    # recovery come from CODE_TABLE) plus the one specific they can act
                    # on: which creative is affected.
                    Error.of(  # structural-guard: advisory per-creative result in ListCreativesResponse.errors[]
                        ErrorCode.CONFIGURATION_ERROR,
                        details=EntityRefDetails(creative_id=db_creative.creative_id),
                    )
                )
                status_enum = CreativeStatus.processing

            # v3.1 concept grouping. AdCP exposes concept_id/concept_name on the
            # list_creatives RESPONSE (a creative's concept membership, sourced from
            # the buyer's creative-management platform — Flashtalking/Celtra/CM360),
            # but standardizes no concept INPUT on sync_creatives, so the field is
            # populated out-of-band into the data blob. (A seller-side mapping of GAM
            # creative groups -> these fields is a separate enrichment/fallback
            # follow-up (#1506), not the authoritative buyer-side concept.) The blob is
            # untyped and an external producer may write numeric group ids, so coerce
            # each field to the spec's string type via _coerce_blob_scalar (with the
            # field's own label) rather than letting a non-string value fail Creative
            # validation and crash the whole listing.
            data_blob = db_creative.data or {}
            # Operator-attribution for any coercion drop below: names this row so a corrupt
            # blob value can be traced and repaired (the drops are otherwise per-row anonymous).
            # Keyword args so the id→label mapping is explicit at the call site (a transposition
            # would be a visible mislabel, and the wiring test pins it either way).
            row_log_context = _blob_log_context(
                creative_id=db_creative.creative_id,
                tenant_id=tenant.tenant_id,
                principal_id=db_creative.principal_id,
            )

            # assets is read from the same untyped blob and validated into the typed map
            # here; a stored value that does not validate is dropped with a log rather than
            # crashing the whole listing (#1508). Bound before the model because ``items``
            # is projected from the SAME validated map -- validating it twice would let the
            # two answers disagree about one stored value.
            coerced_assets = _coerce_blob_assets(assets_dict, "assets", log_context=row_log_context)

            creative = Creative(
                creative_id=db_creative.creative_id,
                name=db_creative.name,
                format_id=format_obj,
                assets=coerced_assets,
                # tags is typed list[str] but read from the untyped blob, where an
                # external producer may write a malformed value (a bare string, or
                # [1, 2]) that would fail Creative validation and crash the whole
                # listing — coerce it (stringify scalars, drop+log corrupt data), the
                # same hazard _coerce_blob_scalar handles for the concept fields (#1508).
                tags=_coerce_blob_str_list(data_blob.get("tags"), "tags", log_context=row_log_context),
                # AdCP spec fields (listing Creative)
                status=status_enum,
                created_date=created_at_dt,
                updated_date=updated_at_dt,
                concept_id=_coerce_blob_scalar(data_blob.get("concept_id"), "concept_id", log_context=row_log_context),
                concept_name=_coerce_blob_scalar(
                    data_blob.get("concept_name"), "concept_name", log_context=row_log_context
                ),
                assignments=_assignments_block(assignments_by_creative, db_creative.creative_id)
                if req.include_assignments is not False
                else None,
                # A requested snapshot is DECLINED, with the machine-readable reason the pin
                # provides for exactly this seller: enums/snapshot-unavailable-reason.json
                # describes SNAPSHOT_UNSUPPORTED as "The seller platform does not support
                # delivery snapshots for this entity". Nothing in this schema stores
                # per-creative lifetime impressions or a last-served date -- the only
                # impression columns are GAM line-item stats and format-level metrics,
                # neither of which is per creative -- so there is no snapshot to compute and
                # none to cache. Declining is what the pin asks of such a seller; inventing
                # an impression count would be worse than saying nothing, and saying nothing
                # at all would leave the buyer unable to tell a zero from an absence.
                snapshot_unavailable_reason=(
                    SnapshotUnavailableReason.SNAPSHOT_UNSUPPORTED if req.include_snapshot else None
                ),
                # Both default to FALSE on the request (list-creatives-request.json), so
                # each member is built only when the buyer asked for it.
                variables=(
                    _coerce_blob_variables(data_blob.get("variables"), row_log_context)
                    if req.include_variables
                    else None
                ),
                items=_items_from_assets(coerced_assets) if req.include_items else None,
                # Internal field (our extension)
                principal_id=db_creative.principal_id,
            )
            creatives.append(_project_to_fields(creative, req.fields))

    # Where the NEXT page would start, and therefore whether there is one. Computed from
    # the offset this page began at, not from a page index: with a cursor there is no page
    # number, and the old `page * limit` arithmetic answered "is there more after the FIRST
    # page" whatever cursor the buyer sent.
    next_offset = offset + len(creatives)
    has_more = next_offset < total_count

    # Build filters_applied list from structured filters (typed CreativeFilters model)
    filters_applied: list[str] = []
    if req.filters:
        if effective_media_buy_ids:
            filters_applied.append(f"media_buy_ids={','.join(effective_media_buy_ids)}")
        if req.filters.statuses:
            filters_applied.append(f"statuses={','.join(str(s) for s in req.filters.statuses)}")
        if req.filters.format_ids:
            filters_applied.append(f"format_ids={','.join(str(f) for f in req.filters.format_ids)}")
        if req.filters.tags:
            filters_applied.append(f"tags={','.join(req.filters.tags)}")
        if req.filters.tags_any:
            filters_applied.append(f"tags_any={','.join(req.filters.tags_any)}")
        if req.filters.creative_ids:
            filters_applied.append(f"creative_ids={','.join(req.filters.creative_ids)}")
        if req.filters.concept_ids:
            filters_applied.append(f"concept_ids={','.join(req.filters.concept_ids)}")
        if req.filters.created_after:
            filters_applied.append(f"created_after={req.filters.created_after.isoformat()}")
        if req.filters.created_before:
            filters_applied.append(f"created_before={req.filters.created_before.isoformat()}")
        if req.filters.name_contains:
            filters_applied.append(f"search={req.filters.name_contains}")

    # The sort that was APPLIED, which is never nothing: list-creatives-request.json gives
    # sort.field the default created_date and sort.direction the default desc, so a request
    # carrying no sort object is still answered in a definite order. This used to report
    # sort_applied only when the buyer named BOTH members, which left the buyer unable to
    # tell the default ordering from an unspecified one (query_summary.sort_applied is
    # "Sort order that was applied", and POST-S7 is the buyer knowing it).
    sort_applied = {"field": sort_by, "direction": valid_sort_order}

    # Audit logging
    audit_logger = get_audit_logger("AdCP", tenant.tenant_id)
    audit_logger.log_operation(
        operation="list_creatives",
        principal_name=principal_id,
        principal_id=principal_id,
        adapter_id="N/A",
        success=True,
        details={
            "result_count": len(creatives),
            "total_count": total_count,
            "offset": offset,
            "filters_applied": filters_applied if filters_applied else None,
        },
    )

    log_tool_activity(identity, "list_creatives", start_time)

    # Import required schema classes
    from src.core.schemas import Pagination as SchemaPagination
    from src.core.schemas import QuerySummary

    return ListCreativesResponse(
        query_summary=QuerySummary(
            total_matching=total_count,
            returned=len(creatives),
            filters_applied=filters_applied,
            sort_applied=sort_applied,
        ),
        pagination=SchemaPagination(
            has_more=has_more,
            # "Only present when has_more is true" (core/pagination-response.json): a
            # cursor to nowhere would invite a request for a page that does not exist.
            cursor=_cursor_for_offset(next_offset) if has_more else None,
            total_count=total_count,
        ),
        creatives=creatives,
        format_summary=None,
        status_summary=None,
        # BR-RULE-209 INV-4/INV-5. The field's pinned meaning is "this response contains
        # simulated data from sandbox mode", so it is emitted only when the account the
        # request named IS a sandbox account, and is absent for every other account — a
        # response that is not simulated makes no claim either way. list_creatives calls no
        # ad platform at all, so sandbox mode has nothing to suppress here (INV-2); what the
        # flag reports is the mode of the account whose library the buyer is reading.
        sandbox=True if identity.account is not None and identity.account.sandbox else None,
        errors=unreadable_status_advisories or None,
        message=(
            f"Found {len(creatives)} creative{'s' if len(creatives) != 1 else ''}."
            if len(creatives) == total_count
            else f"Showing {len(creatives)} of {total_count} creatives."
        ),
    )
