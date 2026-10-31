"""Update Media Buy tool implementation.

Handles media buy updates including:
- Campaign-level budget and date changes
- Package-level budget adjustments
- Creative assignments per package
- Activation/pause controls
- Currency limit validation
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal

from adcp.server.helpers import MEDIA_BUY_STATE_MACHINE, is_terminal_status
from adcp.types import GeneratedTaskStatus as AdcpTaskStatus
from adcp.types import MediaBuyStatus

from src.core.tools._media_buy_status import UNSUPPORTED_ACTIONS, seller_valid_actions
from src.core.tools._media_buy_transitions import advance_past_pending_creatives
from src.core.tools.media_buy_list import _compute_status

if TYPE_CHECKING:
    from src.core.database.models import MediaBuy

from sqlalchemy import select

from src.core.exceptions import (
    AdCPAdapterError,
    AdCPAmbiguousBiddingPolicyError,
    AdCPBudgetExceededError,
    AdCPBudgetTooLowError,
    AdCPCapabilityNotSupportedError,
    AdCPConflictError,
    AdCPContextNotFoundError,
    AdCPCreativeNotFoundError,
    AdCPGoneError,
    AdCPMediaBuyNotFoundError,
    AdCPNotCancellableError,
    AdCPValidationError,
)
from src.core.format_satisfaction import FormatSatisfaction, creative_refusal, creative_satisfies
from src.core.webhook_validator import reject_unsafe_webhook_registration_url, webhook_url_for_log
from src.core.webhooks.registration import accept_push_notification_config

logger = logging.getLogger(__name__)

from adcp.types.domains.creative.sync_creatives_request import Assignment

from src.core.audit_logger import get_audit_logger
from src.core.context_manager import get_context_manager
from src.core.database.models import (
    MediaBuy,
    MediaPackage,
    ObjectWorkflowMapping,
    PersistedMediaBuyStatus,
)
from src.core.database.models import (
    Product as DBProduct,
)
from src.core.database.repositories import MediaBuyRepository, MediaBuyUoW
from src.core.database.repositories.uow import CreativeUoW
from src.core.errors.details import (
    AdapterFailureDetails,
    CapabilityRefusalDetails,
    ConflictDetails,
    CreativeRefDetails,
    EntityRefDetails,
    ErrorProblem,
    InvalidStateDetails,
    ValidationDetails,
)
from src.core.helpers.adapter_helpers import get_adapter
from src.core.resolved_identity import AccountIdentity, ResolvedIdentity
from src.core.schemas import (
    AdCPPackageUpdate,
    AffectedPackage,
    CreativeFormatIdentity,
    SyncCreativesRequest,
    TargetingInput,
    UpdateMediaBuyRequest,
    UpdateMediaBuyResult,
    UpdateMediaBuySubmitted,
    UpdateMediaBuySuccess,
)
from src.core.tools.creatives import sync_creatives
from src.core.tools.creatives._assignments import assignments_by_package, require_package_covered
from src.core.tools.financial_validation import (
    raise_if_validation_failed,
    validate_budget_positive,
    validate_max_daily_package_spend,
    validate_min_package_budget,
)
from src.core.utils import utc_flight_start
from src.core.validation_helpers import (
    package_field_path,
    refuse_duplicate_catalog_types,
    refuse_inverted_flight,
)
from src.services.targeting_capabilities import (
    property_list_unsupported_advisories,
    raise_if_property_targeting_violations,
    refuse_invalid_package_overlays,
    refuse_unsupported_package_members,
    validate_property_targeting_allowed,
)


def _adcp_status_and_actions(buy: "MediaBuy", today: date | None = None) -> tuple[MediaBuyStatus | None, list[str]]:
    """Map a media buy to ``(media_buy_status, valid_actions)``, DATE-REFINED.

    Routes through ``get_media_buys``' ``_compute_status`` (``resolve_canonical_status``
    + the delivery-only ``failed`` -> ``rejected`` mapping) so the update-response
    ``media_buy_status`` agrees with ``get_media_buys`` for the same buy and reference
    date — the two surfaces must describe one buy identically (the 8plg agreement;
    ). A past-end serving buy therefore reports ``completed`` on both,
    not the un-refined persisted ``active`` (the status scheduler that transitions the
    column may lag behind the flight window).

    ``valid_actions`` is derived from the resulting AdCP status so a persisted value
    whose name differs from its AdCP value (``scheduled``/``approved``/
    ``pending_approval``/``failed``/``draft``) never feeds a non-AdCP token to
    ``seller_valid_actions`` (which would yield ``[]`` + a null status).

    The row is REQUIRED. Every caller obtains it from
    ``MediaBuyRepository.get_by_id_or_raise``, which raises
    ``AdCPMediaBuyNotFoundError`` (with a message, a suggestion and the buyer's
    context) for a row that vanished mid-transaction — so a missing row never reaches
    an envelope and this helper does not carry a second answer for one. ``today`` defaults to the current
    UTC date (mock-time aware, matching ``get_media_buys``); callers may pass an
    explicit reference date.

    Single source of truth for the update-response status pair so the four
    ``UpdateMediaBuySuccess`` sites cannot drift — from each other or from
    ``get_media_buys``.
    """
    if today is None:
        today = datetime.now(UTC).date()
    media_buy_status: MediaBuyStatus | None = _compute_status(buy, today) if buy.status else None
    valid_actions = seller_valid_actions(media_buy_status.value) if media_buy_status else []
    return media_buy_status, valid_actions


def _applied_instant() -> datetime:
    """The instant a synchronously-applied update takes effect.

    ``implementation_date`` is "ISO 8601 timestamp when changes take effect (null if
    pending approval)" — the pinned ``media-buy/update-media-buy-response.json``,
    ``/oneOf/0/properties/implementation_date`` (optional and nullable on that branch;
    ``/oneOf/0/required`` is ``["media_buy_id", "revision"]``). So the field names THIS
    update's effective instant, and the applied branches take it here, once, after their
    writes and before the response they return.

    It is NOT ``media_buys.updated_at``. That column is the parent ROW's last-modified
    stamp, and it is this update's instant only when this update actually UPDATEd the
    parent row. Two shapes do not: a ``packages`` entry carrying only ``package_id`` and
    ``creative_ids``, and a ``creative_assignments`` entry. Both write
    ``creative_assignments`` rows and nothing else on an ACTIVE buy — the
    ``update_status(..., PENDING_CREATIVES)`` those branches can reach is gated on the
    buy being ``draft`` with ``approved_at`` set — so ``updated_at`` still held an
    EARLIER update's instant (measured against PostgreSQL: unchanged across such an
    update, with no trigger on the column) and the buyer was told the change took effect
    then. A row-derived value is only as good as the row having been written.

    Both success sites call this rather than reading a clock inline, so the field has one
    meaning and one place that states it.
    """
    return datetime.now(UTC)


@dataclass
class _PackageChanges:
    """What this update DID to one package, accumulated across its per-field branches.

    ONE of these per modified package, not one per field: the pin's obligation is
    per-PACKAGE ("the state of each directly modified package"), so a request that
    changes a package's budget AND its pacing owes one snapshot carrying both, not two
    snapshots each carrying half. The six per-branch ``affected_packages_list.append``
    calls this replaces produced the second shape, and a seventh branch would have
    produced a seventh copy of the same assembly — which is why the assembly moved to
    one place (``_affected_package``, called once per package after the loop) instead.

    ``paused`` and ``budget`` hold what the branches WROTE, for the two fields the record
    cannot be re-read for: the adapter commits its package writes in its OWN session, so a
    value this update caused is carried here rather than looked up again.

    A third field, ``changes_applied``, is gone with the ``AffectedPackage`` field it was
    copied into. Eight branches wrote per-change entries into it and nothing read it back,
    so the entries described the update to no one. The six branches whose ONLY statement
    was such a write now call ``_package_changes`` for its registration alone -- the call
    is what puts the package in ``affected_packages``, and that was always the load-bearing
    half.
    """

    paused: bool | None = None
    budget: float | None = None


def _package_changes(records: dict[str, _PackageChanges], package_id: str) -> _PackageChanges:
    """The accumulator for *package_id*, created on first touch.

    ``setdefault`` in one place rather than at each branch: the dict's insertion order IS
    the order ``affected_packages`` reports, so "first branch that modified this package"
    has to mean the same thing everywhere.
    """
    return records.setdefault(package_id, _PackageChanges())


def _affected_package(
    uow: MediaBuyUoW,
    media_buy_id: str,
    package_id: str,
    changes: _PackageChanges,
) -> AffectedPackage:
    """One package's POST-UPDATE state, for ``affected_packages``.

    The pinned ``media-buy/update-media-buy-response.json``
    (``/oneOf/0/properties/affected_packages`` @ 3.2.1) defines the field as "full Package
    objects showing complete post-update state for each modified package ... a state
    snapshot, not a sparse delta: sellers MUST NOT return ``{ package_id }``-only stubs",
    and allows the empty array only for "campaign-level updates that do not modify
    packages". The written spec at the same tag (``docs/media-buy/specification.mdx``)
    says it as an obligation on the seller: "Sales agents MUST return ``affected_packages``
    containing the state of each directly modified package after the update is applied
    ...; an empty array is valid when only campaign-level fields (e.g., ``paused``,
    ``start_time``) are updated." Four branches of this tool were hand-building the entry
    with ``paused=False`` written in as a literal — so a budget change to an already-paused
    package reported it as active — and a fifth (``paused``) reported nothing at all.

    Every field below comes from the STORED package, except where the calling branch has
    just written one and passes it on ``changes``. That split is not a convenience: the
    adapter commits its package writes in its OWN session, so a field this update changed
    is supplied rather than re-read, while the fields it did not touch are whatever the
    record holds — the same source ``get_media_buys`` serves them from, which is what keeps
    the two surfaces from describing one package differently.

    WHAT IS STILL MISSING, and why it is not here. The snapshot carries the three columns
    (``budget``, ``bid_price``, ``pacing``), the two ``package_config`` scalars this file
    already trusted (``product_id``, ``paused``) and the one ``package_config`` document
    written only from a validated request (``catalogs``, by create and by this tool). It
    does NOT carry ``format_ids``,
    ``impressions``, ``start_time`` or ``end_time``, which also sit in ``package_config``:
    those are untyped blobs written by past clients, so reading them needs the
    corrupt-row tolerance ``get_media_buys`` owns (``_resolve_blob_field``, which answers a
    bad row with a per-row advisory rather than a 500). Borrowing that is a separate
    change, tracked as its own item; a bare ``config.get(...)`` into a typed field here
    would turn one malformed historical row into a failed update.

    ``creative_assignments`` and ``pricing_option_id`` ARE carried, as the
    pending_creatives_to_start storyboard grades them (``assign_creative_to_package``,
    field_contains ``affected_packages[*]`` @ 3.2.7). The assignments are read from the
    stored rows, so the inline ``creatives``, ``creative_ids`` and ``creative_assignments``
    branches report the package's creative set the same way, whichever wrote it; the
    pricing option is the one create recorded from the validated request.

    ``targeting_overlay`` is NOT carried either, and that reason is a type boundary rather
    than an oversight: a package update's overlay is the INPUT sibling (``TargetingInput``)
    while the readback field takes ``Targeting``, and the two are siblings with neither a
    subclass of the other — crossing that gap is ``_adopt_sibling``'s job, by attribute,
    never by a dump in a caller. Rehydrating a STORED overlay is separately
    ``get_media_buys``' job, which owns the corrupt-row advisory that goes with it
    (``media_buy_list.py:274``).
    """
    assert uow.media_buys is not None
    # The tolerant accessor, deliberately: a media buy whose packages live only in
    # ``raw_request`` has no ``media_packages`` row, and the creative branches above
    # accept that today (they read the row for a product_id and carry on without one).
    # Reporting what IS known about such a package is right; refusing an update that
    # succeeded, because the projection could not find a row, would not be. The branches
    # that DO require a row keep their own ``get_package_or_raise`` pre-check.
    stored = uow.media_buys.get_package(media_buy_id, package_id)
    config = (stored.package_config or {}) if stored is not None else {}
    stored_budget = float(stored.budget) if stored is not None and stored.budget is not None else None
    stored_bid_price = float(stored.bid_price) if stored is not None and stored.bid_price is not None else None
    assert uow.assignments is not None
    assignments = assignments_by_package(uow.assignments.get_by_media_buy_and_package(media_buy_id, package_id)).get(
        package_id
    )
    return AffectedPackage(
        package_id=package_id,
        paused=bool(config.get("paused", False)) if changes.paused is None else changes.paused,
        budget=stored_budget if changes.budget is None else changes.budget,
        bid_price=stored_bid_price,
        # Re-read rather than carried: ``update_package_fields`` writes the column inside
        # THIS session, so the row already answers with what this update applied.
        pacing=stored.pacing if stored is not None else None,
        product_id=config.get("product_id"),
        pricing_option_id=config.get("pricing_option_id"),
        creative_assignments=assignments,
        # Written by create_media_buy from the validated request and by the catalogs branch
        # below, both from the pinned core/catalog.json model, so the stored document has
        # one shape -- not a blob some past client left behind.
        catalogs=config.get("catalogs"),
    )


#: The package-update fields this seller APPLIES: each one has a branch below that writes
#: a persisted effect, and the ``affected_packages`` snapshot reads that effect back.
#:
#: ``package_id`` is absent on purpose — it names WHICH package to update rather than
#: asking for a change — and so are ``context`` and ``ext``, which are the protocol's own
#: echoed context and the namespaced extension bag (``core/context.json``, ``core/ext.json``)
#: rather than update instructions.
#:
#: A name here that the DTO no longer declares is not silent: its own branch reads
#: ``pkg_update.<name>`` and raises ``AttributeError``, and the renamed field's new spelling
#: lands in the refusal set below.
_APPLIED_PACKAGE_FIELDS: frozenset[str] = frozenset(
    {
        "budget",
        "pacing",
        "catalogs",
        "paused",
        "targeting_overlay",
        "creative_ids",
        "creatives",
        "creative_assignments",
    }
)

#: Not instructions, so neither applied nor refused: the package's identity, the echoed
#: protocol context, and the extension bag.
_NON_INSTRUCTION_PACKAGE_FIELDS: frozenset[str] = frozenset({"package_id", "context", "ext"})

#: The package-update fields this seller accepts in its DTO — because the pinned schema
#: declares them — and cannot execute. DERIVED by SUBTRACTION from the DTO's own field set,
#: so a field this tool does not apply is refused whether or not anybody remembered to list
#: it: a property added to ``3.2/media-buy/package-update.json`` upstream lands here the
#: moment the SDK declares it. That is the opposite of the previous shape — a
#: ``keyword_targets_`` / ``negative_keywords_`` prefix match — which refused four fields
#: and let the other fourteen through silently, the defect this derivation closes.
#:
#: WHY A REFUSAL AND NOT A SILENT NO-OP. Two sentences in the pin, independently:
#: ``core/targeting.json`` — "A fresh create/update request MUST be applied exactly or
#: rejected" — and ``core/targeting-input.json`` — "Sellers apply all supplied dimensions
#: atomically and return only the resulting non-null effective targeting state." The written
#: spec's worked example for the analogous feature is ``docs/media-buy/specification.mdx``'s
#: frequency-cap rule: rejected "with UNSUPPORTED_FEATURE before any mutation; sellers MUST
#: NOT silently drop, soften, or clamp it." So accepting a declared field and answering plain
#: success is excluded by a MUST, and so is declaring non-support and ignoring the request.
#:
#: This is NOT the pattern-#7 strip story: these fields ARE declared in
#: ``3.2/media-buy/package-update.json``, so dropping them is not the dev/prod tolerance for
#: an undeclared field. It is also NOT the ``_FIELD_POLICY`` ``ignored_by_design`` shape used
#: for the seven 3.2 account fields — that is right there because nothing in those fields'
#: schemas carries an apply-exactly-or-reject MUST, and these carry one each:
#: ``min_spend_target`` and ``pacing`` name ``UNSUPPORTED_FEATURE`` in their own pinned
#: descriptions; ``bidding``, ``daily_budget_cap`` and ``optimization_goals`` define
#: replacement semantics a no-op cannot deliver; and ``canceled`` is the one that makes the
#: silence expensive — a buyer asking to stop a package's delivery used to be answered
#: ``completed`` while the package kept delivering.
#:
#: The DISCOVERY half of the obligation is already met, by omission.
#: ``core/targeting-overlay-support.json`` is binding — "Product.overlay_support is the
#: binding selectable-targeting contract: presence means the seller can apply protocol-valid
#: values" — and it declares ``keyword_targets`` and ``negative_keywords``. MEASURED:
#: ``git grep overlay_support -- src/`` returns nothing, so this seller advertises no overlay
#: support at all and never claimed keyword targeting. (It also understates the dimensions
#: this seller DOES support, which is its own gap and not this refusal's business.)
_UNAPPLIED_PACKAGE_FIELDS: tuple[str, ...] = tuple(
    name
    for name in AdCPPackageUpdate.model_fields
    if name not in _APPLIED_PACKAGE_FIELDS and name not in _NON_INSTRUCTION_PACKAGE_FIELDS
)


def _refuse_unapplied_package_fields(req: UpdateMediaBuyRequest) -> None:
    """Refuse a package update carrying a field this seller cannot apply.

    Raised BEFORE the UoW opens, because the spec puts the rejection "before any
    mutation" and a partially-applied update is the state it exists to prevent.

    The capability NAME travels as structured detail and the buyer-facing sentence is the
    code's own, which is how every other ``AdCPCapabilityNotSupportedError`` site in this
    tree reports a refusal (``src/adapters/gam/managers/targeting.py:680`` is the nearest
    sibling). The field is the indexed pointer to the refused member, the same shape the
    undeclared-overlay refusal reports, because both build it in one place
    (``src/services/targeting_capabilities.py``).
    """
    refuse_unsupported_package_members(req.packages, _UNAPPLIED_PACKAGE_FIELDS)


def _refuse_ambiguous_bidding(req: UpdateMediaBuyRequest) -> None:
    """Refuse a package update carrying a non-null ``bidding`` object beside ``bid_price``.

    ``enums/error-code.json`` AMBIGUOUS_BIDDING_POLICY: "Sellers MUST reject rather than
    choosing a winner." Raised ahead of :func:`_refuse_unapplied_package_fields`, which
    would otherwise answer UNSUPPORTED_FEATURE for either member alone and hide the more
    specific refusal. ``bidding: null`` beside ``bid_price`` is allowed by the pin and reads
    as ``None`` here, so it passes. The members are read by attribute with no default, so a
    pin that renames one fails loudly here instead of never matching.
    """
    for index, pkg_update in enumerate(req.packages or ()):
        if all(getattr(pkg_update, name) is not None for name in AdCPPackageUpdate.AMBIGUOUS_BIDDING_MEMBERS):
            raise AdCPAmbiguousBiddingPolicyError(
                details=EntityRefDetails(package_id=pkg_update.package_id, package_index=index),
                field=f"packages[{index}]",
            )


def _refuse_unsupported_actions(req: UpdateMediaBuyRequest) -> None:
    """Refuse an update asking for an action this seller does not perform, whatever the buy's state.

    ``media-buy/update-media-buy-request.json`` (``/properties/new_packages``): "Sellers that
    do not support this MUST reject with ACTION_NOT_ALLOWED (preferred) or
    UNSUPPORTED_FEATURE (legacy)". UNSUPPORTED_FEATURE, the code BR-UC-003 BR-RULE-217 INV-1
    grades. The action set and the field each one maps to are
    :data:`~src.core.tools._media_buy_status.UNSUPPORTED_ACTIONS`, the same declaration that
    keeps the action out of ``valid_actions[]``, so what is refused here is exactly what is
    not advertised there.

    Raised BEFORE the UoW opens, for the reason :func:`_refuse_unapplied_package_fields`
    gives: the refusal is a property of the request, and the whole update stays unapplied.
    """
    for action in _requested_actions(req):
        if (field := UNSUPPORTED_ACTIONS.get(action)) is not None:
            raise AdCPCapabilityNotSupportedError(details=CapabilityRefusalDetails(capability=action), field=field)


def _requested_actions(req: UpdateMediaBuyRequest) -> list[str]:
    """Derive the AdCP buyer-action names implied by an update request.

    Returned names align with ``MEDIA_BUY_STATE_MACHINE`` keys so they can
    be intersected against ``seller_valid_actions(current_status)``.

    A package entry's ``budget`` maps to ``update_budget`` alone: the pinned
    ``enums/media-buy-valid-action.json`` lists ``packages[].budget`` among
    ``update_budget``'s ``update_fields``, and update_media_buy.mdx's normative action
    table maps it to the budget actions only. So an entry that changes nothing but its
    budget does not also ask for ``update_packages``, which a paused buy does not offer
    while it does offer ``update_budget``.
    """
    actions: list[str] = []
    if req.new_packages:
        actions.append("add_packages")
    if req.paused is True:
        actions.append("pause")
    if req.paused is False:
        actions.append("resume")
    if req.packages and any(pkg.budget is not None for pkg in req.packages):
        actions.append("update_budget")
    if req.start_time is not None or req.end_time is not None:
        actions.append("update_dates")
    if req.packages and any(pkg.model_fields_set - {"package_id", "budget"} for pkg in req.packages):
        actions.append("update_packages")
    return actions


def _replace_package_assignments(
    uow: "MediaBuyUoW", media_buy_id: str, package_id: str, keep: set[str], *, inline: bool = False
) -> set[str]:
    """Drop each assignment of *package_id* that *keep* does not name, within the set replaced.

    Returns the creative ids the package was assigned before the drop. The three package
    fields that set a package's creatives each REPLACE a set (package-update.json):
    ``creative_ids`` and ``creative_assignments`` ("Replace creative assignments for this
    package ... Uses replacement semantics") replace every assignment; the inline
    ``creatives`` (``inline=True``) replace only the inline ones. update_media_buy.mdx's
    package field table gives the two arrays separate scopes -- ``creative_assignments``
    "Replace assigned library creatives", ``creatives`` "Replace assigned inline creative
    bodies for this package" -- and the same page says a seller "must never silently delete
    an assignment". So the inline replacement leaves a library assignment in place, and
    marks the rows it keeps as inline, the ones the sync just created among them. One drop
    rule for all three, so a fix to it cannot reach one field and miss another.
    """
    assert uow.assignments is not None
    existing = uow.assignments.get_by_media_buy_and_package(media_buy_id, package_id)
    for assignment in existing:
        if assignment.creative_id in keep:
            if inline:
                assignment.inline = True
        elif assignment.inline or not inline:
            uow.assignments.delete_row(assignment)
    return {assignment.creative_id for assignment in existing}


def _patched_overlay(package: MediaPackage | None, overlay: TargetingInput) -> TargetingInput:
    """The package's stored overlay patched by the dimensions *overlay* sends.

    update_media_buy.mdx (Package Update Object, targeting_overlay): "Omit the overlay or a
    dimension inside it to leave that dimension unchanged; provide a non-null value to
    replace it; provide ``null`` to clear it." The dimensions the buyer sent, an explicit
    null among them, are the overlay's ``model_fields_set``; every other stored dimension is
    kept. A package that does not exist has nothing stored, so the sent overlay is the result.

    The cross-dimension rules run on this result and this result is what gets stored, so the
    overlay that is validated is the overlay that is stored: a stored ``geo_regions`` plus a
    sent ``geo_regions_exclude`` of the same value is the overlap create_media_buy refuses.
    """
    stored = (package.package_config.get("targeting_overlay") or {}) if package is not None else {}
    kept = {name: value for name, value in stored.items() if value is not None}
    return TargetingInput.model_validate(kept | {name: getattr(overlay, name) for name in overlay.model_fields_set})


def _package_product(uow: "MediaBuyUoW", media_buy_id: str, package_id: str) -> "DBProduct | None":
    """The product a held package was bought on, or None when its config names none.

    One lookup for the three branches that replace a package's creatives: each grades the
    creatives against this product and then asks it whether the package is still covered.
    """
    assert uow.media_buys is not None and uow.products is not None
    package = uow.media_buys.get_package(media_buy_id, package_id)
    product_id = package.package_config.get("product_id") if package and package.package_config else None
    return uow.products.get_by_id(product_id) if product_id else None


def _validate_creatives_for_assignment(
    creative_ids: list[str],
    *,
    uow: "MediaBuyUoW",
    principal_id: str,
    product: "DBProduct | None",
    field: str,
    product_name: str | None = None,
) -> None:
    """Validate a set of creatives is assignable to a package.

    Shared by both the ``creative_ids`` and ``creative_assignments`` update
    paths so the existence / status / format-compatibility rules live in one
    place (DRY). Checks run in order and raise on the first failing category,
    each with the code the pinned SDK's ``enums/error-code.json`` defines for it --
    three different conditions, so three different codes:

    1. Existence — every ``creative_id`` exists for this principal within the
       tenant. ``CREATIVE_NOT_FOUND``, which the enum makes uniform for any
       creative_id not owned by the caller. The creatives PK is composite
       (creative_id, tenant_id, principal_id): another principal's creative
       resolves to "not found" — never passes this gate on their row, which the
       assignment insert would then violate on the composite FK.
    2. Status — none are in ``error`` or ``rejected`` state. ``INVALID_STATE``:
       "Operation is not permitted for the resource's current status".
    3. Format compatibility — each creative's ``(agent_url, format)`` is
       supported by the package's product ``format_ids``. ``VALIDATION_ERROR``:
       "violates business rules beyond schema validation". A product with no
       declared formats imposes no restriction.

    None of the three is ``CREATIVE_REJECTED``. The pin shapes that code's
    details as ``{policy_id, policy_url, reasons}``
    (``error-details/creative-rejected.json``) and describes it as "Creative
    failed content policy review" — a policy outcome none of these paths reach.
    All three emitted it before; see the commit that split them.

    Args:
        creative_ids: Creative IDs referenced by the package update.
        uow: Tenant-scoped unit of work exposing ``creatives``.
        principal_id: The requesting buyer's principal (from ResolvedIdentity).
        product: The package's product ORM record (or ``None`` if the package
            has no resolvable product, in which case the format check is skipped).
        field: JSON pointer to the array parameter the ids came from — the two
            callers pass different ones (``packages[i].creative_ids`` vs
            ``packages[i].creative_assignments``), and the spec asks the pointer
            to name the array itself when the failure is about the collection.
        product_name: Display name for error messages (falls back to the
            product's name, then the product_id).

    Raises:
        AdCPCreativeNotFoundError: If any creative_id is not owned by this principal.
        AdCPGoneError: If any creative is in a terminal (``error``/``rejected``) state.
        AdCPValidationError: If any creative's format is not one the product declares.
    """
    if not creative_ids:
        return

    requested_ids = list(dict.fromkeys(creative_ids))  # de-dup, preserve order

    # (a) Existence — principal-scoped multi-get via repository.
    assert uow.creatives is not None, "MediaBuyUoW.creatives required for creative validation"
    creatives_list = uow.creatives.get_by_ids(requested_ids, principal_id)
    found_by_id = {c.creative_id: c for c in creatives_list}
    missing_ids = [cid for cid in requested_ids if cid not in found_by_id]
    if missing_ids:
        # The ids come back. The anti-enumeration MUST is about not distinguishing
        # "exists elsewhere" from "does not exist" -- it is not a vow of silence, and
        # 3.1.1 L3/error-handling.mdx says so: "Sellers MAY enumerate specific
        # unresolvable elements in error.details -- but only when the elements were
        # supplied verbatim by the caller." These were. Every unresolvable id is listed
        # the SAME way, so a co-tenant's creative and a nonexistent one are
        # indistinguishable in the response, which is what the MUST actually protects.
        # `field` names the ARRAY parameter, per the same paragraph.
        raise AdCPCreativeNotFoundError(
            details=CreativeRefDetails(missing_creative_ids=sorted(missing_ids)),
            field=field,
        )

    # (b) Status — terminal-state creatives are not assignable.
    bad_state = [c for c in creatives_list if c.status in ("error", "rejected")]
    if bad_state:
        # INVALID_STATE: "Operation is not permitted for the resource's current status".
        # NOT CREATIVE_REJECTED -- the pin shapes that code's details as
        # {policy_id, policy_url, reasons} (error-details/creative-rejected.json), a
        # content-policy review outcome this path cannot populate. AdCPGoneError is this
        # repo's INVALID_STATE carrier (see the state-machine raises below).
        raise AdCPGoneError(
            # The STATE per creative, not a joined sentence duplicating creative_ids.
            # Per-creative outcomes are per-ENTITY problems, not fields.
            details=InvalidStateDetails(
                problems=[
                    ErrorProblem(subject_type="creative", subject_id=c.creative_id, rejected_value=c.status)
                    for c in bad_state
                ]
            ),
        )

    # (c) Format satisfaction against the package's product.
    if product is None:
        return  # No product — no format restriction.

    display_name = product_name or getattr(product, "name", None) or getattr(product, "product_id", "")

    # THE ONE satisfaction function, which this path, the two create gates and the
    # sync-creatives assignment path all ask -- so they cannot disagree about whether a
    # creative's format is one the product declares. It used to be an inline comparison
    # against ``product_format_identities`` alone, which could only see the DEPRECATED arm:
    # a product declaring ``format_options[]`` imposed no restriction here at all, and a
    # creative carrying no legacy assertion was REFUSED here while the create path skipped
    # it. ``creative_satisfies`` reads both arms off the product and decides which lane applies.
    contract = product.format_contract
    first_refusal: FormatSatisfaction | None = None
    incompatible: list[str] = []
    for creative in creatives_list:
        # The ONE creative validator: the stored identity with BOTH arms, and a canonical
        # creative's manifest graded against the declaration it selects.
        verdict = creative_satisfies(creative, contract)
        if verdict:
            continue
        if verdict.creative_coded:
            # A canonical refusal -- the closed set (UNSUPPORTED_FEATURE) or a manifest, or a
            # legacy reference's parameters, failing what it selects (a creative code) -- is
            # raised on the first one: the
            # recovery is to pick a format inside the closed set or fix the creative, and
            # collecting it beside the legacy problems would answer the buyer one code for
            # two conditions.
            #
            # ``field`` is the caller's pointer -- ``packages[i].creative_ids`` or
            # ``packages[i].creative_assignments``, whichever array the buyer actually sent.
            # The two call sites already resolve it and the legacy refusals below already
            # travel with it; a canonical refusal arriving with ``field=None`` would be the
            # only refusal on this path that did not say which array it is about.
            raise creative_refusal(verdict, field=field, creative_id=creative.creative_id, product_id=display_name)
        first_refusal = first_refusal or verdict
        incompatible.append(f"{creative.creative_id} (format '{CreativeFormatIdentity.from_row(creative).label}')")

    if incompatible and first_refusal is not None:
        # A format outside the product's declared set is a BUSINESS-RULE violation, which
        # the pinned SDK's enums/error-code.json codes as VALIDATION_ERROR ("violates business
        # rules beyond schema validation"). It is not CREATIVE_REJECTED: that enum entry
        # reads "Creative failed content policy review ... revise the creative per the
        # seller's advertising_policies", and CREATIVE_VALUE_NOT_ALLOWED's own text calls
        # it "generic content-policy failure". The creative here is fine; the ASSIGNMENT
        # is what the product does not permit. (#1417 chose CREATIVE_REJECTED as canonical
        # for a rejected creative, which is right for a content-policy outcome and wrong
        # for this one; the class is the authority on the code, so the fix is the class.)
        #
        # ``accepted_values`` stays the single joined string this wire has always carried:
        # the verdict's own tuple is the same set, one entry per format, and switching to it
        # would change the buyer's document for no obligation anyone named.
        raise AdCPValidationError(
            # FIXME(#2099): the product's DISPLAY NAME is prose, and product_id already
            # identifies it. Preserved for now because removing it changes the wire.
            details=ValidationDetails(
                accepted_values=[", ".join(first_refusal.accepted_values)], product_id=display_name
            ),
        )


def _verify_principal(
    media_buy_id: str,
    identity: "ResolvedIdentity",
    repo: MediaBuyRepository,
) -> None:
    """Verify that the principal from identity owns the media buy.

    OWNERSHIP ONLY. It does not check whether there IS a principal, and must not: the
    parameter is a ``ResolvedIdentity``, whose ``principal`` is a required field, so the
    resolver has already refused a missing or rejected credential before this can be
    called. ``identity.principal.principal_id`` is read directly for that reason.

    Uses the provided repository for database access (no own session).

    Args:
        media_buy_id: Media buy ID to verify
        identity: ResolvedIdentity with principal info
        repo: Tenant-scoped MediaBuyRepository for DB lookups

    Raises:
        AdCPMediaBuyNotFoundError: Media buy not found, or owned by another principal
    """
    principal_id = identity.principal.principal_id
    tenant = identity.tenant

    # Fetch the media buy (raises AdCPMediaBuyNotFoundError if absent)
    media_buy = repo.get_by_id_or_raise(media_buy_id)

    if media_buy.principal_id != principal_id:
        # Log security violation
        security_logger = get_audit_logger("AdCP", tenant.tenant_id)
        security_logger.log_security_violation(
            operation="access_media_buy",
            principal_id=principal_id,
            resource_id=media_buy_id,
            reason=f"Principal does not own media buy (owner: {media_buy.principal_id})",
        )
        # The buyer gets the absent-buy answer, byte for byte: L1/security.mdx (3.2.7)
        # "the body MUST NOT distinguish 'unauthorized' from 'not found'", and
        # enums/error-code.json MEDIA_BUY_NOT_FOUND covers a buy "not accessible to the
        # requesting agent". Same class and details as get_by_id_or_raise above.
        raise AdCPMediaBuyNotFoundError(details=EntityRefDetails(media_buy_id=media_buy_id))


def _refuse_stale_revision(req: UpdateMediaBuyRequest, repo: MediaBuyRepository) -> None:
    """Refuse an update whose ``revision`` is not the buy's current one, with CONFLICT.

    update-media-buy-request.json ``/properties/revision``: "When provided, sellers MUST
    reject the update with CONFLICT if the media buy's current revision does not match,
    and MUST enforce that comparison atomically with the write". update_media_buy.mdx adds
    that the seller "applies no changes", so this runs before the first write. The value
    compared is read under the row lock ``lock_revision`` takes, which this transaction
    holds through its own revision bump. An absent ``revision`` is last-write-wins: the
    field is optional "for backward compatibility".

    The details are error-details/conflict.json's: the buy, the revision the buyer
    expected, and the one it is at, so the buyer can re-read and retry.
    """
    if req.revision is None:
        return
    current = repo.lock_revision(req.media_buy_id)
    if current is not None and current != req.revision:
        raise AdCPConflictError(
            details=ConflictDetails(
                resource_id=req.media_buy_id,
                expected_version=str(req.revision),
                current_version=str(current),
            ),
            field="revision",
        )


def _update_media_buy_impl(
    req: UpdateMediaBuyRequest,
    identity: AccountIdentity,
) -> UpdateMediaBuyResult:
    """Shared implementation for update_media_buy (used by both MCP and A2A).

    Callers construct the validated UpdateMediaBuyRequest at their boundary
    (MCP wrapper from typed FastMCP params, A2A raw from dict params).

    Uses a single MediaBuyUoW for the entire operation — one session, one transaction.

    Args:
        req: Validated UpdateMediaBuyRequest with all protocol fields
        identity: ResolvedIdentity with principal/tenant info (transport-agnostic)

    Returns:
        UpdateMediaBuyResponse with updated media buy details
    """
    # One entry per package this update MODIFIES, in the order the request named them.
    # The per-field branches below record into it; the single assembly point after the
    # loop turns each entry into the post-update snapshot the pin requires. The list of
    # AffectedPackage this replaced was appended to from six branches, so a package
    # touched twice appeared twice and a field no branch read appeared not at all.
    changes_by_package: dict[str, _PackageChanges] = {}

    principal_id = identity.principal.principal_id
    tenant = identity.tenant

    # SSRF gate at registration — after auth so unauthenticated callers get AUTH
    # first, and ABOVE the UoW so no DB transaction is held and a refused URL is
    # VALIDATION_ERROR regardless of buy state: the buyer must fix the request
    # before state questions are reachable, so this outranks the terminal-state
    # refusal. The stored copy this guards is the workflow step's request_data,
    # which context_manager later reads back and dials.
    #
    # Deliberately the no-DNS registration gate (gh-#1697), NOT the outbound seam's
    # validate_url (gh-#1589): validate_url always resolves, so at registration it
    # would reject a buyer whose hostname has not yet propagated — and would answer
    # the same input differently from create_media_buy / sync_creatives. The seam
    # stays the SEND-time gate and re-checks with DNS when the URL is actually dialed.
    # Use str(url): library PushNotificationConfig/ReportingWebhook.url is pydantic
    # AnyUrl, not str.
    if req.push_notification_config:
        registration = accept_push_notification_config(
            req.push_notification_config,
            field_prefix="push_notification_config",
        )
        pnc_url = registration.url
        if pnc_url is not None and str(pnc_url).strip():
            # Log scheme+host+path only — never credentials / full auth blob.
            logger.info(
                "[update_media_buy] Push notification webhook URL: %s",
                webhook_url_for_log(str(pnc_url)),
            )
    if req.reporting_webhook:
        rw_url = getattr(req.reporting_webhook, "url", None)
        reject_unsafe_webhook_registration_url(
            str(rw_url) if rw_url is not None else None,
            field="reporting_webhook.url",
        )

    # Capability refusal, above the UoW for the same reason the webhook gates are: the spec
    # puts this rejection "before any mutation", so no transaction may be open when it fires.
    _refuse_ambiguous_bidding(req)
    _refuse_unapplied_package_fields(req)
    _refuse_unsupported_actions(req)

    # ── Workflow-step bookkeeping fence ──────────────────────────────────
    # Hoist ``ctx_manager`` and ``step`` out of the try below so the
    # AdCPSalesAgentError / Exception handlers at the function end can mark the step
    # as ``failed``. Without this, a raise from any validation site
    # (property_targeting, geo, budget, …) leaves the workflow step
    # orphaned in ``in_progress`` forever, which suppresses the
    # buyer-facing ``status="failed"`` push notification fired from
    # ``context_manager.update_workflow_step``, which fires it through
    # ``_send_push_notifications``.
    # Mirrors ``media_buy_create``'s own workflow-step construction exactly.
    ctx_manager = get_context_manager()
    step = None

    with ctx_manager.audit_workflow_step_failure_ctx(lambda: step):
        # Single UoW for entire update operation — one session, one transaction
        with MediaBuyUoW(tenant.tenant_id) as uow:
            assert uow.media_buys is not None
            # FIXME(#2128): raw session usages below should migrate to repository methods
            assert uow.session is not None
            session = uow.session

            # media_buy_id is required by library base class
            media_buy_id_to_use = req.media_buy_id

            if not media_buy_id_to_use:
                raise AdCPValidationError()

            # Verify principal owns this media buy
            _verify_principal(media_buy_id_to_use, identity, uow.media_buys)
            _refuse_stale_revision(req, uow.media_buys)

            # State-machine precondition: terminal states reject all mutations,
            # and non-terminal states only accept actions in their valid set.
            # ``AdCPGoneError`` carries the spec-mandated ``INVALID_STATE`` code
            # for both terminal states and disallowed actions — see
            # ``adcp.server.helpers.MEDIA_BUY_STATE_MACHINE`` for the source of truth.
            _current_mb = uow.media_buys.get_by_id(media_buy_id_to_use)
            _current_status = _current_mb.status if _current_mb else ""
            if is_terminal_status(_current_status):
                # The pin SPECIALIZES the code by what was asked, not by what went wrong:
                # 3.1/enums/error-code.json reserves NOT_CANCELLABLE for "cannot be canceled
                # in its current state" and leaves INVALID_STATE for "operation is not
                # permitted for the resource's current status". Both are correctable. So a
                # cancel against a terminal buy -- the re-cancel case -- answers the specific
                # code, and every other mutation keeps the generic one.
                # BR-UC-003-update-media-buy.feature states the same split.
                if req.canceled:
                    raise AdCPNotCancellableError(
                        details=InvalidStateDetails(current_status=_current_status),
                        field="media_buy_id",
                    )
                raise AdCPGoneError(
                    details=InvalidStateDetails(current_status=_current_status),
                    field="media_buy_id",
                )

            _requested = _requested_actions(req)
            _allowed = set(seller_valid_actions(_current_status))
            # Only enforce state machine for statuses defined in the spec.
            # Pre-confirmation internal states (e.g., "draft") are not in the
            # SDK state machine — allow all actions on those.
            if _allowed or _current_status in MEDIA_BUY_STATE_MACHINE:
                _disallowed = [a for a in _requested if a not in _allowed]
                if _requested and _disallowed:
                    raise AdCPGoneError(
                        details=InvalidStateDetails(disallowed_actions=_disallowed, current_status=_current_status),
                        field="media_buy_id",
                    )

            # Create or get persistent context and workflow step
            # (ctx_manager + step were hoisted before the try block so the
            # AdCPSalesAgentError / Exception handlers can mark the step as failed)
            ctx_id = None
            persistent_ctx = None

            persistent_ctx = ctx_manager.get_or_create_context(
                tenant_id=tenant.tenant_id,
                principal_id=principal_id,  # Now guaranteed to be str
                context_id=ctx_id,
                is_async=True,
            )

            # Verify persistent_ctx is not None. In the async path this is
            # only None when a buyer-supplied context_id does not resolve —
            # a not-found condition, not a transient adapter outage.
            if persistent_ctx is None:
                raise AdCPContextNotFoundError(details=EntityRefDetails(context_id=ctx_id), field="context_id")

            # Create workflow step for this tool call
            step = ctx_manager.create_workflow_step(
                context_id=persistent_ctx.context_id,  # Now safe to access
                step_type="tool_call",
                owner="principal",
                status="in_progress",
                tool_name="update_media_buy",
                request_data=req,
            )

            principal = identity.principal

            adapter = get_adapter(identity)
            today = date.today()

            # spec (core/product.json `property_targeting_allowed`): reject property_list targeting
            # on products with property_targeting_allowed=False. Runs before the dry_run
            # early return so dry_run requests are also rejected (parity with create).
            # Raise shape is shared with create via ``raise_if_property_targeting_violations``
            # so both paths emit byte-identical error envelopes (same code, same field,
            # same details). The boundary's AdCPSalesAgentError handler updates any in-flight
            # workflow step to status="failed" for the audit trail.
            if req.packages:
                assert uow.products is not None, "MediaBuyUoW.products required for product targeting validation"
                # Run the same per-package targeting validators the create path runs, so a buyer
                # can't bypass unknown-field rejection, managed-only dimension checks, or
                # same-value geo inclusion/exclusion overlap by sending changes through update.
                # INVALID_REQUEST per the generated storyboard (UC-002 @ext-f and UC-003
                # @*-targeting-overlay); the one check create runs too (#1417). The rules run on
                # the stored overlay as the update patches it (``_patched_overlay``), and the
                # write below stores exactly that patched overlay, so the overlay that is
                # validated is the overlay that is stored.
                patched_overlays: dict[str, TargetingInput] = {}
                package_overlays: list[TargetingInput | None] = []
                for pkg_update in req.packages:
                    if pkg_update.targeting_overlay is None:
                        package_overlays.append(None)
                        continue
                    stored_package = (
                        uow.media_buys.get_package(req.media_buy_id, pkg_update.package_id)
                        if pkg_update.package_id
                        else None
                    )
                    patched = _patched_overlay(stored_package, pkg_update.targeting_overlay)
                    if pkg_update.package_id:
                        patched_overlays[pkg_update.package_id] = patched
                    package_overlays.append(patched)
                refuse_invalid_package_overlays(package_overlays)

                property_targeting_violations: list[str] = []
                for pkg_update in req.packages:
                    if (
                        pkg_update.targeting_overlay is None
                        or pkg_update.targeting_overlay.property_list is None
                        or not pkg_update.package_id
                    ):
                        continue
                    media_package = uow.media_buys.get_package(req.media_buy_id, pkg_update.package_id)
                    if media_package is None:
                        continue
                    package_product_id = (media_package.package_config or {}).get("product_id")
                    if not package_product_id:
                        continue
                    product = uow.products.get_by_id(package_product_id)
                    violation = validate_property_targeting_allowed(product, pkg_update.targeting_overlay)
                    if violation:
                        property_targeting_violations.append(violation)
                raise_if_property_targeting_violations(property_targeting_violations)

            assert step is not None
            assert persistent_ctx is not None

            # Check if manual approval is required
            manual_approval_required = adapter.manual_approval_required
            manual_approval_operations = adapter.manual_approval_operations

            if manual_approval_required and "update_media_buy" in manual_approval_operations:
                # Store the original request alongside the response so the approval
                # execution path can re-execute the update after human approval.
                # This mirrors create_media_buy's raw_request pattern.
                # Spec 3.1.1 models a not-yet-applied (pending human approval) update as the
                # UpdateMediaBuySubmitted response variant: protocol-envelope status="submitted"
                # + a task_id the buyer polls for the outcome. Returning UpdateMediaBuySuccess
                # here would emit the adcp-6.6 default status="completed", falsely asserting the
                # update was applied. task_id is the workflow step the admin approval flow acts on.
                approval_response = UpdateMediaBuySubmitted(
                    task_id=step.step_id,
                    message=f"Media buy update submitted for approval (task {step.step_id}).",
                    errors=property_list_unsupported_advisories(req.packages, adapter),
                )
                ctx_manager.audit_workflow_step_result(
                    step.step_id,
                    approval_response,
                    status="requires_approval",
                    request_obj=req,
                    add_comment={
                        "user": "system",
                        "comment": "Publisher requires manual approval for all media buy updates",
                    },
                )

                # Create ObjectWorkflowMapping so the admin approval flow can
                # find this update and execute it after human approval.
                mapping = ObjectWorkflowMapping(
                    step_id=step.step_id,
                    object_type="media_buy",
                    object_id=req.media_buy_id,
                    action="update",
                )
                session.add(mapping)

                approval_response.status = AdcpTaskStatus.submitted
                return approval_response

            # Validate currency limits if flight dates or budget changes
            # This prevents workarounds where buyers extend flight to bypass daily max
            if req.start_time or req.end_time or (req.packages and any(pkg.budget for pkg in req.packages)):
                media_buy = uow.media_buys.get_by_id(req.media_buy_id)

                if media_buy:
                    # The buy's own currency: a package budget is denominated by its pricing
                    # option, and there is no campaign-level budget to carry a currency.
                    request_currency = str(media_buy.currency) if media_buy.currency else "USD"

                    assert uow.currency_limits is not None
                    currency_limit = uow.currency_limits.get_for_currency(request_currency)

                    if not currency_limit:
                        raise AdCPCapabilityNotSupportedError(
                            details=CapabilityRefusalDetails(capability="currency", rejected_value=request_currency),
                        )

                    start = req.start_time if req.start_time else media_buy.start_time
                    end = req.end_time if req.end_time else media_buy.end_time

                    from datetime import datetime as dt

                    start_dt: datetime
                    end_dt: datetime

                    if isinstance(start, str):
                        if start == "asap":
                            start_dt = dt.now(UTC)
                        else:
                            start_dt = dt.fromisoformat(start.replace("Z", "+00:00"))
                    elif isinstance(start, datetime):
                        start_dt = start
                    else:
                        start_dt = dt.now(UTC)

                    if isinstance(end, str):
                        end_dt = dt.fromisoformat(end.replace("Z", "+00:00"))
                    elif isinstance(end, datetime):
                        end_dt = end
                    else:
                        end_dt = start_dt + timedelta(days=1)

                    flight_days = (end_dt - start_dt).days
                    if flight_days <= 0:
                        flight_days = 1

                    if currency_limit.max_daily_package_spend and req.packages:
                        for pkg_update in req.packages:
                            if pkg_update.budget is not None:
                                pkg_budget_amount: float
                                if isinstance(pkg_update.budget, int | float):
                                    pkg_budget_amount = float(pkg_update.budget)
                                else:
                                    pkg_budget_amount = float(pkg_update.budget.total)

                                package_daily_spend_error: str | None = validate_max_daily_package_spend(
                                    package_budget=Decimal(str(pkg_budget_amount)),
                                    flight_days=flight_days,
                                    max_daily_spend=currency_limit.max_daily_package_spend,
                                    currency=request_currency,
                                )
                                raise_if_validation_failed(
                                    package_daily_spend_error,
                                    exc_type=AdCPBudgetExceededError,
                                    requested_budget=Decimal(str(pkg_budget_amount)),
                                    budget_limit=currency_limit.max_daily_package_spend,
                                )

            # Handle package-level updates
            if req.packages:
                # enumerate so a per-package rejection can name WHICH package failed:
                # the pointer is packages[N].package_id, never packages[].package_id,
                # which named neither the array nor an element.
                for pkg_index, pkg_update in enumerate(req.packages):
                    # Handle paused state
                    if pkg_update.paused is not None:
                        # the pinned SDK+: paused=True means pause, paused=False means resume
                        action = "pause_package" if pkg_update.paused else "resume_package"
                        # Failure is raised by the adapter, never returned.
                        adapter.update_media_buy(
                            media_buy_id=req.media_buy_id,
                            action=action,
                            package_id=pkg_update.package_id,
                            budget=None,
                            today=utc_flight_start(today),
                        )

                        # This branch modified a package, so the response says which one
                        # and in what state. It used to apply the pause through the adapter
                        # and record NOTHING, which is the one shape the pin's
                        # affected_packages description does not allow outside a
                        # campaign-level update (see _affected_package). ``paused`` is
                        # carried, not re-read: it is the value this branch just caused.
                        record = _package_changes(changes_by_package, pkg_update.package_id)
                        record.paused = pkg_update.paused

                    # Handle budget updates
                    if pkg_update.budget is not None:
                        # Validate package_id is provided (required for budget updates)
                        if not pkg_update.package_id:
                            raise AdCPValidationError(field=package_field_path("package_id", pkg_index))
                        # Extract budget amount - handle both float and Budget object
                        budget_amount: float
                        currency: str
                        if isinstance(pkg_update.budget, int | float):
                            budget_amount = float(pkg_update.budget)
                            # F-07: preserve existing DB currency rather than defaulting to USD
                            _existing_mb = uow.media_buys.get_by_id(req.media_buy_id)
                            currency = str(_existing_mb.currency) if _existing_mb and _existing_mb.currency else "USD"
                        else:
                            # Budget object with .total and .currency attributes
                            budget_amount = float(pkg_update.budget.total)
                            currency = str(pkg_update.budget.currency) if pkg_update.budget.currency else "USD"

                        # A zero or negative budget is BUDGET_TOO_LOW, not a fall-through.
                        # This check existed only on the campaign-level path, which AdCP
                        # 3.1.1 does not define -- so removing that path took the only
                        # positivity guard with it, and `if pkg_update.budget:` skipped 0.0 as
                        # falsy, leaving a zero budget to surface later as INVALID_STATE.
                        budget_positive_err = validate_budget_positive(
                            Decimal(str(budget_amount)), field=package_field_path("budget", pkg_index)
                        )
                        if budget_positive_err:
                            raise AdCPBudgetTooLowError(field=package_field_path("budget", pkg_index))

                        assert uow.currency_limits is not None
                        _cl = uow.currency_limits.get_for_currency(currency)
                        if _cl and _cl.min_package_budget:
                            # `currency` is keyword-only and REQUIRED; this call omitted it.
                            # It never raised because the enclosing guard was
                            # `if pkg_update.budget:` -- truthy-only, so a 0 budget (the one
                            # value that reliably reaches the minimum check) was skipped and
                            # the broken call was unreachable. Fixing the guard to
                            # `is not None` exposed it.
                            package_min_budget_error: str | None = validate_min_package_budget(
                                package_budget=Decimal(str(budget_amount)),
                                min_package_budget=Decimal(str(_cl.min_package_budget)),
                                currency=currency,
                            )
                            raise_if_validation_failed(
                                package_min_budget_error,
                                exc_type=AdCPBudgetTooLowError,
                                requested_budget=Decimal(str(budget_amount)),
                                budget_limit=Decimal(str(_cl.min_package_budget)),
                            )

                        # The package must exist in the media buy before we hand the
                        # budget change to the adapter — otherwise the adapter silently
                        # no-ops (quiet failure). Checked after the budget-value
                        # validation so a malformed budget still surfaces BUDGET_TOO_LOW.
                        # Raise PACKAGE_NOT_FOUND (BR-UC-003 ext-l).
                        uow.media_buys.get_package_or_raise(req.media_buy_id, pkg_update.package_id)

                        # Failure is raised by the adapter, never returned.
                        adapter.update_media_buy(
                            media_buy_id=req.media_buy_id,
                            action="update_package_budget",
                            package_id=pkg_update.package_id,
                            budget=int(budget_amount),
                            today=utc_flight_start(today),
                        )

                        # The seller's budget column is written HERE, inside this request's
                        # unit of work, once the ad server has accepted the change, and
                        # nowhere else: no adapter writes media_packages.budget. It was left
                        # to each adapter, and they disagreed: Kevel wrote nothing, GAM and
                        # Broadstreet wrote only package_config["budget"] while get_media_buys
                        # reads the column, and the mock wrote both in its own session. Pinned
                        # update_media_buy.mdx (v3.2.7) makes the request "one atomic mutation"
                        # in which no package field from a rejected update may change, and
                        # "every mutating update increments revision": update_package_fields
                        # writes the column and moves the revision, and both roll back with
                        # the UoW. The UoW owns only that: the adapter call above has already
                        # reached the ad server when a later branch refuses, and GAM and
                        # Broadstreet still commit package_config["budget"] in their own
                        # sessions.
                        uow.media_buys.update_package_fields(
                            req.media_buy_id,
                            pkg_update.package_id,
                            budget=Decimal(str(budget_amount)),
                        )

                        # Track budget update in affected_packages. ``budget`` is carried
                        # (the value just applied); ``paused`` is read off the record,
                        # because a budget change does not alter it and the literal
                        # ``False`` that stood here reported an already-paused package as
                        # active.
                        record = _package_changes(changes_by_package, pkg_update.package_id)
                        record.budget = budget_amount

                    # Handle creative_ids updates (AdCP v2.2.0+)
                    if pkg_update.creative_ids is not None:
                        # Validate package_id is provided
                        if not pkg_update.package_id:
                            raise AdCPValidationError(field=package_field_path("package_id", pkg_index))

                        # Resolve media_buy_id
                        media_buy_obj = uow.media_buys.get_by_id_or_raise(req.media_buy_id)

                        # Use the actual internal media_buy_id
                        actual_media_buy_id = media_buy_obj.media_buy_id

                        # Validate creatives (existence, status, format) via the
                        # shared helper so the rules match the creative_assignments path.
                        product = _package_product(uow, actual_media_buy_id, pkg_update.package_id)
                        _validate_creatives_for_assignment(
                            pkg_update.creative_ids,
                            uow=uow,
                            principal_id=principal_id,
                            product=product,
                            field=package_field_path("creative_ids", pkg_index),
                        )

                        # Replace: drop the assignments not requested, then add the new ones.
                        requested_ids = set(pkg_update.creative_ids)
                        existing_creative_ids = _replace_package_assignments(
                            uow, actual_media_buy_id, pkg_update.package_id, requested_ids
                        )
                        added_ids = requested_ids - existing_creative_ids

                        # Add new assignments
                        assert uow.assignments is not None
                        for creative_id in added_ids:
                            uow.assignments.create(
                                media_buy_id=actual_media_buy_id,
                                package_id=pkg_update.package_id,
                                creative_id=creative_id,
                                principal_id=principal_id,
                            )

                        # If media buy was approved (approved_at set) but is in draft status
                        # (meaning it was approved without creatives), transition to pending_creatives
                        # Check whenever creative_ids are being set (not just when new ones added)
                        if (
                            pkg_update.creative_ids
                            and media_buy_obj.status == "draft"
                            and media_buy_obj.approved_at is not None
                        ):
                            uow.media_buys.update_status(actual_media_buy_id, PersistedMediaBuyStatus.PENDING_CREATIVES)
                            logger.info(
                                f"[UPDATE] Media buy {actual_media_buy_id} transitioned from draft to pending_creatives "
                                f"(creative_ids: {pkg_update.creative_ids})"
                            )

                        # Flush to persist assignment changes within the session
                        session.flush()
                        assert uow.assignments is not None
                        require_package_covered(
                            uow.assignments,
                            actual_media_buy_id,
                            pkg_update.package_id,
                            product,
                            field=package_field_path("creative_ids", pkg_index),
                        )

                        # Registers the package as affected; the response snapshot reads the
                        # persisted effect back rather than a per-change dict.
                        _package_changes(changes_by_package, pkg_update.package_id)

                    # Handle creatives (inline upload)
                    if pkg_update.creatives:
                        # Validate package_id is provided
                        if not pkg_update.package_id:
                            raise AdCPValidationError(field=package_field_path("package_id", pkg_index))

                        # Sync creatives (upload/update)
                        # Built through the SAME builder the three transports use, rather
                        # than handed to _impl as loose fields. _sync_creatives_impl takes a
                        # request; an in-process caller that could not produce one was
                        # reaching into the tool instead of invoking it.
                        #
                        # account is the OUTER request's, not invented: these creatives
                        # belong to that account. No idempotency_key: this is a SERVICE call,
                        # and the service does not do idempotency -- the controller does, and
                        # only for the request a buyer actually sent.
                        # A buyer's malformed inline creative raises here and travels
                        # untouched to the transport boundary, which names their field --
                        # nothing between this frame and that one may reclassify it.
                        sync_req = SyncCreativesRequest(
                            creatives=pkg_update.creatives,
                            account=req.account,
                            # ITS OWN key: required by the schema, never read by the service,
                            # and never the outer request's -- see creative_helpers.
                            idempotency_key=f"internal-creative-upload-{uuid.uuid4().hex}",
                            # The typed Assignment the request model declares, not the
                            # {creative_id: [package_id]} map this used to build. That
                            # map was a second, internal-only spelling of the same
                            # relation, and it forced _sync_creatives_impl to accept a
                            # dict as well as the spec's list -- so the one internal
                            # caller widened the type for every transport.
                            assignments=[
                                Assignment(creative_id=c.creative_id, package_id=pkg_update.package_id)
                                for c in pkg_update.creatives
                                if c.creative_id
                            ],
                        )
                        # Joined, not a unit of the sync's own: update_media_buy.mdx
                        # § Compatibility transaction boundary makes this request "one
                        # atomic mutation" in which "No package, assignment, library
                        # creative, or MediaBuy field from the rejected update may change".
                        # A sync that committed on its own exit kept the uploaded creative
                        # and its assignment when a later check refused the update.
                        sync_response = sync_creatives(
                            sync_req,
                            identity=identity,
                            principal_id=principal_id,
                            tenant=tenant,
                            uow=uow.join(CreativeUoW),
                        )

                        # Check for sync errors
                        failed_creatives = [r for r in sync_response.creatives if r.action == "failed"]
                        if failed_creatives:
                            raise AdCPAdapterError(
                                # ``e.code`` rather than ``e.message``: the message is a
                                # function of the code through CODE_TABLE, so the code is
                                # the fact and the sentence is derivable from it. Sending
                                # the sentence made the buyer parse prose to learn which
                                # creative failed and why.
                                details=AdapterFailureDetails(
                                    problems=[
                                        ErrorProblem(
                                            code=err.code,
                                            subject_type="creative",
                                            subject_id=r.creative_id,
                                        )
                                        for r in failed_creatives
                                        for err in r.errors or []
                                    ]
                                ),
                            )

                        # The array REPLACES the package's inline creative bodies
                        # (package-update.json /properties/creatives: "Replace this
                        # package's inline creative assets"; update_media_buy.mdx: "The
                        # creatives array replaces the package's inline creative bodies").
                        # The sync above assigned the new bodies; the inline ones they
                        # replace are unassigned here, and library assignments -- the
                        # creative_ids / creative_assignments set -- are left as they are.
                        inline_media_buy_id = uow.media_buys.get_by_id_or_raise(req.media_buy_id).media_buy_id
                        _replace_package_assignments(
                            uow,
                            inline_media_buy_id,
                            pkg_update.package_id,
                            {c.creative_id for c in pkg_update.creatives},
                            inline=True,
                        )
                        session.flush()
                        # The sync above asked coverage of the set BEFORE this drop, when the
                        # replaced inline bodies still covered their placements. The drop can
                        # leave an included placement with no creative, which core/product.json
                        # format_options forbids: "the selected creative set MUST cover every
                        # included placement; uncovered inventory MUST be rejected or refined,
                        # never silently omitted". So the set is asked again, as it now stands.
                        assert uow.assignments is not None
                        require_package_covered(
                            uow.assignments,
                            inline_media_buy_id,
                            pkg_update.package_id,
                            _package_product(uow, inline_media_buy_id, pkg_update.package_id),
                            field=package_field_path("creatives", pkg_index),
                        )

                        # Track in affected_packages
                        _package_changes(changes_by_package, pkg_update.package_id)

                    # Handle creative_assignments (weight/placement updates) - adcp#208
                    if pkg_update.creative_assignments:
                        # Validate package_id is provided
                        if not pkg_update.package_id:
                            raise AdCPValidationError(field=package_field_path("package_id", pkg_index))

                        # Resolve media_buy_id
                        media_buy_obj = uow.media_buys.get_by_id_or_raise(req.media_buy_id)

                        actual_media_buy_id = media_buy_obj.media_buy_id

                        # Validate referenced creatives (existence, status, format) BEFORE
                        # building any assignment rows — otherwise a not-found creative_id
                        # would surface as a composite-FK IntegrityError. Same rules as the
                        # creative_ids path via the shared helper.
                        ca_product = _package_product(uow, actual_media_buy_id, pkg_update.package_id)
                        _validate_creatives_for_assignment(
                            [ca.creative_id for ca in pkg_update.creative_assignments],
                            uow=uow,
                            principal_id=principal_id,
                            product=ca_product,
                            field=package_field_path("creative_assignments", pkg_index),
                        )

                        # Validate placement_ids against product's available placements (adcp#208)
                        # Build set of placement_ids from all creative_assignments
                        all_requested_placement_ids: set[str] = set()
                        for ca in pkg_update.creative_assignments:
                            if ca.placement_ids:
                                all_requested_placement_ids.update(ca.placement_ids)

                        if all_requested_placement_ids:
                            # Get package to find product_id
                            pkg_record = uow.media_buys.get_package_or_raise(actual_media_buy_id, pkg_update.package_id)

                            product_id = (
                                pkg_record.package_config.get("product_id") if pkg_record.package_config else None
                            )

                            if product_id:
                                # Get product's placements
                                prod_stmt = select(DBProduct).where(
                                    DBProduct.tenant_id == tenant.tenant_id,
                                    DBProduct.product_id == product_id,
                                )
                                product_obj = session.scalars(prod_stmt).first()

                                if product_obj and product_obj.placements:
                                    available_placement_ids: set[str] = {
                                        str(p.get("placement_id"))
                                        for p in product_obj.placements
                                        if p.get("placement_id")
                                    }
                                    invalid_ids = all_requested_placement_ids - available_placement_ids
                                    if invalid_ids:
                                        raise AdCPValidationError(field="creative_assignments[].placement_ids")
                                elif product_obj and not product_obj.placements:
                                    # Product doesn't define placements, so placement targeting not supported
                                    raise AdCPCapabilityNotSupportedError(
                                        details=CapabilityRefusalDetails(product_id=product_id),
                                    )

                        updated_assignments = []
                        new_assignments_created = []

                        # BR-RULE-024 INV-2: creative_assignments replaces ALL existing
                        # assignments for this package. Delete existing assignments not
                        # in the new list, matching the creative_ids handler pattern.
                        _replace_package_assignments(
                            uow,
                            actual_media_buy_id,
                            pkg_update.package_id,
                            {ca.creative_id for ca in pkg_update.creative_assignments},
                        )
                        assert uow.assignments is not None

                        for ca in pkg_update.creative_assignments:
                            # Schema validates and coerces dict inputs to LibraryCreativeAssignment
                            creative_id = ca.creative_id
                            weight = ca.weight
                            placement_ids = ca.placement_ids

                            # Find or create assignment record. principal_id is part of
                            # the match key: the same creative_id can exist under two
                            # principals (composite creatives PK), and the create branch
                            # below inserts under the requester's principal.
                            db_assignment = uow.assignments.get_existing(
                                actual_media_buy_id,
                                pkg_update.package_id,
                                creative_id,
                                principal_id,
                            )

                            if db_assignment:
                                # Update existing assignment
                                if weight is not None:
                                    db_assignment.weight = int(weight)
                                # adcp#208: persist placement_ids for placement-specific targeting
                                if placement_ids is not None:
                                    db_assignment.placement_ids = placement_ids
                                updated_assignments.append(creative_id)
                            else:
                                # Create new assignment with weight and placement_ids
                                uow.assignments.create(
                                    media_buy_id=actual_media_buy_id,
                                    package_id=pkg_update.package_id,
                                    creative_id=creative_id,
                                    principal_id=principal_id,
                                    weight=int(weight) if weight is not None else 100,
                                    # adcp#208: placement-specific targeting
                                    placement_ids=placement_ids,
                                )
                                updated_assignments.append(creative_id)
                                new_assignments_created.append(creative_id)

                        # If media buy was approved (approved_at set) but is in draft status
                        # (meaning it was approved without creatives), transition to pending_creatives
                        # Check whenever creative_assignments are being set (not just when new ones created)
                        if (
                            pkg_update.creative_assignments
                            and media_buy_obj.status == "draft"
                            and media_buy_obj.approved_at is not None
                        ):
                            uow.media_buys.update_status(actual_media_buy_id, PersistedMediaBuyStatus.PENDING_CREATIVES)
                            logger.info(
                                f"[UPDATE] Media buy {actual_media_buy_id} transitioned from draft to pending_creatives "
                                f"(creative_assignments processed: {updated_assignments})"
                            )

                        # Flush to persist assignment changes within the session
                        session.flush()
                        require_package_covered(
                            uow.assignments,
                            actual_media_buy_id,
                            pkg_update.package_id,
                            ca_product,
                            field=package_field_path("creative_assignments", pkg_index),
                        )

                        # Track in affected_packages
                        _package_changes(changes_by_package, pkg_update.package_id)

                    # Handle targeting_overlay updates
                    if pkg_update.targeting_overlay is not None:
                        # Validate package_id is provided
                        if not pkg_update.package_id:
                            raise AdCPValidationError(field=package_field_path("package_id", pkg_index))

                        from sqlalchemy.orm import attributes

                        # Get the package via repository
                        media_package = uow.media_buys.get_package_or_raise(req.media_buy_id, pkg_update.package_id)

                        # property_targeting_allowed validation runs earlier (before dry_run gate);
                        # by this point the request is known-valid against that rule.

                        # The per-dimension patch the targeting rules validated before the
                        # dry_run gate (``_patched_overlay``). The model is stored directly:
                        # the engine's pydantic_core.to_json serializer handles it.
                        media_package.package_config["targeting_overlay"] = patched_overlays[pkg_update.package_id]
                        # Flag the JSON field as modified so SQLAlchemy persists it
                        attributes.flag_modified(media_package, "package_config")
                        session.flush()
                        logger.info(
                            f"[update_media_buy] Updated package {pkg_update.package_id} targeting: {pkg_update.targeting_overlay}"
                        )

                        # Track targeting update in affected_packages
                        _package_changes(changes_by_package, pkg_update.package_id)

                    # Handle pacing updates. 3.2 media-buy/package-update.json declares
                    # pacing as "Updated package pacing, subordinate to media-buy pacing",
                    # and the MediaPackage row carries a ``pacing`` COLUMN that
                    # create_media_buy already dual-writes — so applying it is a column
                    # write, and the snapshot above re-reads the column. Accepted and
                    # dropped until now: a buyer asking for even pacing alongside a budget
                    # change got ``completed`` with the budget applied and the pacing gone.
                    if pkg_update.pacing is not None:
                        if not pkg_update.package_id:
                            raise AdCPValidationError(field=package_field_path("package_id", pkg_index))
                        # The row must exist before the write, for the reason the budget
                        # branch states: ``update_package_fields`` answers a missing package
                        # with None, which is a quiet failure.
                        uow.media_buys.get_package_or_raise(req.media_buy_id, pkg_update.package_id)
                        uow.media_buys.update_package_fields(
                            req.media_buy_id,
                            pkg_update.package_id,
                            # The column is VARCHAR(20) and the DTO field is the pinned
                            # ``enums/pacing.json`` enum, so the MEMBER'S VALUE is what
                            # persists — the same string create_media_buy's dual-write puts
                            # there and the same one get_media_buys reads back.
                            pacing=pkg_update.pacing.value,
                        )
                        _package_changes(changes_by_package, pkg_update.package_id)

                    # Handle catalogs replacement. 3.2 media-buy/package-update.json:
                    # "Replace the catalogs this package promotes. Uses replacement
                    # semantics — the provided array replaces the current list. Omit to
                    # leave catalogs unchanged." So the write is a whole-document swap,
                    # never a merge, and omission is already handled by this guard.
                    if pkg_update.catalogs is not None:
                        if not pkg_update.package_id:
                            raise AdCPValidationError(field=package_field_path("package_id", pkg_index))
                        # The replacement becomes the package's catalogs, which core/package.json
                        # requires to be of distinct types -- the same refusal create makes.
                        refuse_duplicate_catalog_types(pkg_update.catalogs, pkg_index)
                        catalog_pkg = uow.media_buys.get_package_or_raise(req.media_buy_id, pkg_update.package_id)
                        # Through the repository's own writer rather than a mutate +
                        # flag_modified pair: it replaces the document, bumps the buy's
                        # revision (the buyer's optimistic-concurrency token, which an
                        # applied update owes them) and flushes, in one call.
                        uow.media_buys.update_package_config(
                            req.media_buy_id,
                            pkg_update.package_id,
                            {**(catalog_pkg.package_config or {}), "catalogs": pkg_update.catalogs},
                        )
                        _package_changes(changes_by_package, pkg_update.package_id)

            # A campaign-level budget update is not expressible in AdCP: the request
            # schema declares no top-level budget, so the block that lived here -- validating
            # and persisting req.budget, then marking every package affected -- had no way to
            # be reached by a conformant buyer. Package budgets are handled above, per
            # package, where the spec puts them (package-update.json /properties/budget).
            # Handle start_time/end_time updates
            if req.start_time is not None or req.end_time is not None:
                # TODO: Sync date changes to GAM order
                # Currently only updates database - does NOT sync to GAM API
                # This creates data inconsistency between our database and GAM
                # Need to implement: adapter.orders_manager.update_order_dates(order_id, start_time, end_time)

                update_values: dict[str, Any] = {}
                if req.start_time is not None:
                    # Parse start_time (handle 'asap' and datetime strings)
                    if isinstance(req.start_time, str):
                        if req.start_time == "asap":
                            update_values["start_time"] = datetime.now(UTC)
                        else:
                            update_values["start_time"] = datetime.fromisoformat(req.start_time.replace("Z", "+00:00"))
                    elif isinstance(req.start_time, datetime):
                        update_values["start_time"] = req.start_time

                if req.end_time is not None:
                    # Parse end_time (datetime string or datetime object)
                    if isinstance(req.end_time, str):
                        update_values["end_time"] = datetime.fromisoformat(req.end_time.replace("Z", "+00:00"))
                    elif isinstance(req.end_time, datetime):
                        update_values["end_time"] = req.end_time

                if update_values:
                    # Get existing media buy to check date range consistency
                    existing_mb = uow.media_buys.get_by_id_or_raise(req.media_buy_id)

                    # Validate date range: end_time must be after start_time
                    # Type guard: Ensure we're working with datetime objects (not SQLAlchemy DateTime)
                    start_val = update_values.get("start_time", existing_mb.start_time)
                    end_val = update_values.get("end_time", existing_mb.end_time)

                    # Convert to Python datetime if needed (handle SQLAlchemy DateTime)
                    final_start_time: datetime | None = None
                    final_end_time: datetime | None = None

                    if start_val is not None:
                        final_start_time = (
                            start_val if isinstance(start_val, datetime) else datetime.fromisoformat(str(start_val))
                        )
                    if end_val is not None:
                        final_end_time = (
                            end_val if isinstance(end_val, datetime) else datetime.fromisoformat(str(end_val))
                        )

                    if final_start_time and final_end_time:
                        refuse_inverted_flight(final_start_time, final_end_time)

                    uow.media_buys.update_fields(req.media_buy_id, **update_values)
                    logger.warning(
                        f"Updated MediaBuy {req.media_buy_id} dates in database ONLY: "
                        f"start_time={update_values.get('start_time')}, end_time={update_values.get('end_time')}"
                    )
                    logger.warning("GAM sync NOT implemented - GAM still has old dates")

            # Campaign-level pause/resume, applied LAST so every package and date change in
            # the same request is validated and applied first. update_media_buy.mdx
            # § Compatibility transaction boundary: "One update_media_buy request is one
            # atomic mutation", whose payload can carry "budget, targeting, catalog, pause,
            # and billing changes" together. This branch used to answer the pause and
            # RETURN, so every package and date change sent beside it was dropped while the
            # buyer was told the update completed.
            if req.paused is not None:
                # paused=True means pause, paused=False means resume.
                pause_result = adapter.update_media_buy(
                    media_buy_id=req.media_buy_id,
                    action="pause_media_buy" if req.paused else "resume_media_buy",
                    package_id=None,
                    budget=None,
                    today=utc_flight_start(today),
                )
                # A package the adapter paused with the buy goes through the one assembly
                # point below like any other modified package, so it is reported as a full
                # post-update snapshot rather than the adapter's package_id-and-paused stub.
                for reported in pause_result.affected_packages:
                    _package_changes(changes_by_package, reported.package_id).paused = req.paused
                # Persist the pause/resume OURSELVES: the adapter changes the ad server, not
                # our row. ``is_paused`` is what _adcp_status_and_actions derives the
                # reported status from, and update_fields bumps the revision the response
                # reports ("Revision number after this update").
                uow.media_buys.update_fields(req.media_buy_id, is_paused=bool(req.paused))

            # After every write above, so the coverage and the flight window it reads are
            # this update's result, and before the response reads the status back.
            assert uow.assignments is not None
            advance_past_pending_creatives(uow.media_buys, uow.assignments, req.media_buy_id or "")

            # Create ObjectWorkflowMapping to link media buy update to workflow step
            # This enables webhook delivery when the update completes
            mapping = ObjectWorkflowMapping(
                step_id=step.step_id,
                object_type="media_buy",
                object_id=req.media_buy_id,
                action="update",
            )
            session.add(mapping)

            # THE ONE ASSEMBLY POINT for affected_packages: one snapshot per package this
            # update modified, built after every branch has run, so a package touched on
            # two fields is reported once with both. The six per-branch constructions this
            # replaced each reported a package's state as of ONE field's branch, which is
            # the "sparse delta" the pin names and forbids.
            affected_packages_list = [
                _affected_package(uow, req.media_buy_id or "", package_id, changes)
                for package_id, changes in changes_by_package.items()
            ]
            logger.info(f"[update_media_buy] Final affected_packages before return: {affected_packages_list}")

            _final_mb = uow.media_buys.get_by_id_or_raise(req.media_buy_id or "")
            _final_revision = _final_mb.revision
            _final_mbs, _final_actions = _adcp_status_and_actions(_final_mb)
            final_response = UpdateMediaBuySuccess(
                media_buy_id=req.media_buy_id or "",
                message=f"Media buy {req.media_buy_id or ''} updated successfully.",
                revision=_final_revision,
                media_buy_status=_final_mbs,  # AdCP: mirrors `status`
                # The applied instant (see _applied_instant). This branch reaches writes
                # that never touch the parent row — a creative_ids or
                # creative_assignments entry on an active buy writes assignment rows
                # only — so the row's updated_at is not this update's instant here.
                implementation_date=_applied_instant(),
                affected_packages=affected_packages_list,
                valid_actions=_final_actions,
                errors=property_list_unsupported_advisories(req.packages, adapter),
                # sandbox.mdx: sellers "SHOULD include `sandbox: true` in success responses
                # when processing a sandbox account request", as create_media_buy does. The
                # same predicate get_adapter's sandbox selection reads (identity.in_sandbox).
                sandbox=identity.sandbox_echo,
            )

            # Log successful update_media_buy call
            audit_logger = get_audit_logger("AdCP", tenant.tenant_id)
            audit_logger.log_operation(
                operation="update_media_buy",
                principal_name=principal_id or "anonymous",
                principal_id=principal_id or "anonymous",
                adapter_id="mcp_server",
                success=True,
                details={
                    "media_buy_id": req.media_buy_id,
                    "affected_packages_count": len(affected_packages_list),
                    "has_budget_update": bool(req.packages and any(pkg.budget is not None for pkg in req.packages)),
                    "has_pause_update": req.paused is not None,
                    "has_packages_update": req.packages is not None and len(req.packages) > 0,
                },
            )

            # Persist success with response data, then return
            # Use mode="json" to ensure enums are serialized as strings for JSONB storage
            ctx_manager.audit_workflow_step_result(step.step_id, final_response)

        # No status assignment: UpdateMediaBuySuccess IS the completed branch of
        # update-media-buy-response.json's oneOf, so its ``status`` is the const
        # ``Literal["completed"]`` with that default.
        return final_response


def _normalize_pacing(pacing: str | None) -> Literal["even", "asap", "daily_budget"]:
    """Coerce the flat pacing string to the Budget literal, defaulting to even."""
    if pacing == "asap":
        return "asap"
    if pacing == "daily_budget":
        return "daily_budget"
    return "even"
