"""Creative-to-package assignment processing."""

import logging
from collections.abc import Iterable
from typing import Any

from adcp.types import CreativeAssignment as LibraryCreativeAssignment

from src.core.database.models import CreativeAssignment, PersistedMediaBuyStatus
from src.core.database.repositories.creative import CreativeAssignmentRepository
from src.core.database.repositories.uow import CreativeUoW
from src.core.errors.details import CreativeRefDetails, EntityRefDetails, ValidationDetails
from src.core.exceptions import (
    AdCPCreativeNotFoundError,
    AdCPPackageNotFoundError,
    AdCPValidationError,
)
from src.core.format_satisfaction import creative_refusal, creative_satisfies, require_covered
from src.core.logging_config import log_safe
from src.core.schemas import SyncCreativeResult
from src.core.tools._media_buy_transitions import advance_past_pending_creatives

logger = logging.getLogger(__name__)


def _resolve_creative_for_assignment(assignment_repo, creative_id: str, principal_id: str):
    """Resolve the creative state this assignment must be graded against.

    The creative writes this request made are visible as flushed rows in the sync's one
    transaction. Returns the DB row, or ``None`` when the creative does not exist.
    """
    return assignment_repo.get_creative_by_id(creative_id, principal_id)


#: What one assignment entry asks for beyond naming the package: the pinned
#: sync-creatives-request.json's optional ``assignments[].weight`` and
#: ``assignments[].placement_ids``, ``None`` when the entry omits them.
AssignmentTerms = tuple[float | None, list[str] | None]

#: ``{creative_id: {package_id: (weight, placement_ids)}}`` -- the internal shape.
AssignmentMap = dict[str, dict[str, AssignmentTerms]]


def _normalise_assignments(entries: list[Any]) -> AssignmentMap:
    """The assignment ARRAY, as the internal ``{creative_id: {package_id: terms}}`` map.

    Entries arrive TYPED (adcp ``Assignment``) from every transport whose shape is derived
    from the DTO, and as raw dicts from callers that build the list by hand. Both must work.

    Reading only dicts is what made this a silent defect: typed entries failed the isinstance
    check, the map came back empty, and the caller read an empty map as "no assignments
    requested" -- so a buyer's assignments vanished with no error, no failed result and no log
    line, behind a response that looked like a clean sync. Adopting the DTO at the boundary is
    precisely what turned those dicts into models, so the failure arrived with the
    announcement work rather than with any edit here.

    The map used to be ``{creative_id: [package_ids]}``, which is where ``weight`` and
    ``placement_ids`` were dropped: the pin defines both per entry, and a map of ids has
    nowhere to carry them. Keying the terms by package keeps them with the entry they came on.
    """
    coerced: AssignmentMap = {}
    dropped = 0
    for entry in entries:
        if isinstance(entry, dict):
            creative_id, package_id = entry.get("creative_id"), entry.get("package_id")
            weight, placement_ids = entry.get("weight"), entry.get("placement_ids")
        else:
            creative_id = getattr(entry, "creative_id", None)
            package_id = getattr(entry, "package_id", None)
            weight = getattr(entry, "weight", None)
            placement_ids = getattr(entry, "placement_ids", None)
        if creative_id and package_id:
            coerced.setdefault(creative_id, {})[package_id] = (weight, placement_ids)
        else:
            dropped += 1
    if dropped:
        # Never silent: an entry we cannot read is a buyer instruction we are not carrying
        # out, so it is said out loud rather than left to be inferred from a short result.
        logger.warning(
            "sync_creatives: %d assignment entr%s lacked a readable creative_id/package_id and could not be applied",
            dropped,
            "y" if dropped == 1 else "ies",
        )
    return coerced


def require_package_covered(
    assignment_repo: CreativeAssignmentRepository,
    media_buy_id: str,
    package_id: str,
    product: Any,
    *,
    field: str,
) -> None:
    """Refuse when a package's creative set, as the session now holds it, leaves an included placement uncovered.

    Asked once a request's assignment writes for the package are in place, of the WHOLE set:
    the creatives already assigned and the ones the request added or kept, each with the
    ``placement_ids`` its assignment routes it to
    (:func:`~src.core.format_satisfaction.require_covered`). Shared by ``sync_creatives``
    assignments and ``update_media_buy``'s three replace-the-package's-creatives branches
    (``creative_ids``, ``creative_assignments`` and the inline ``creatives``), which is every
    gate that changes an existing package's creatives. The caller's unit of work rolls
    back on the raise. No product means no format restriction, as at every creative gate.
    """
    if product is None:
        return
    require_covered(
        assignment_repo.package_creative_set(media_buy_id, package_id),
        product,
        field=field,
        package_id=package_id,
        product_id=product.product_id,
    )


def assignments_by_package(rows: Iterable[CreativeAssignment]) -> dict[str, list[LibraryCreativeAssignment]]:
    """Stored assignment rows as core/package.json ``creative_assignments``, keyed by package_id.

    "Creative assets assigned to this package": every surface that reports a package's
    state reads its creative set from the rows, so ``create_media_buy``'s ``packages`` and
    ``update_media_buy``'s ``affected_packages`` report what was assigned, whichever request
    array wrote it (the inline ``creatives``, ``creative_ids`` or ``creative_assignments``),
    and report it the same way. Call inside the unit of work that loaded *rows*.
    """
    by_package: dict[str, list[LibraryCreativeAssignment]] = {}
    for row in rows:
        by_package.setdefault(row.package_id, []).append(
            LibraryCreativeAssignment(creative_id=row.creative_id, weight=row.weight, placement_ids=row.placement_ids)
        )
    return by_package


def _assignment_terms(assignments: dict | list | None) -> dict | None:
    """*assignments* as ``{creative_id: {package_id: (weight, placement_ids)}}``, or None.

    AdCP v3 spec defines assignments as list[{creative_id, package_id, weight?, placement_ids?}].
    A caller still handing over the older {creative_id: [package_ids]} map asks for the default
    terms on every entry.
    """
    if assignments and isinstance(assignments, list):
        return _normalise_assignments(assignments) or None
    if assignments and isinstance(assignments, dict):
        return {
            creative_id: dict.fromkeys(package_ids, (None, None)) for creative_id, package_ids in assignments.items()
        }
    return None


def _refuse_format_the_product_does_not_take(
    creative_row: Any, product: Any, creative_id: str, package_id: str
) -> None:
    """Refuse an assignment whose format its product does not take.

    THE ONE creative validator, which the two create gates and update_media_buy also ask -- so no
    two of the four can disagree about whether a creative satisfies what the product declares,
    its manifest included (creative-manifests.mdx § Validation: "Sellers perform final acceptance
    during sync_creatives").
    """
    verdict = creative_satisfies(creative_row, product.format_contract)
    if verdict:
        return
    if verdict.creative_coded:
        raise creative_refusal(verdict, creative_id=creative_id, package_id=package_id)
    # A legacy format the product does not take at all. Reverses #1417, which routed this to
    # CREATIVE_REJECTED: adcp 3.1.1's enum reserves that for "Creative failed content policy
    # review". Converged with the create and update paths. The verdict's accepted set, joined:
    # the pin-canonical `accepted_values` is an array, and this wire has always carried the whole
    # supported list as its single entry.
    raise AdCPValidationError(details=ValidationDetails(accepted_values=[", ".join(verdict.accepted_values)]))


def _process_assignments(
    assignments: dict | list | None,
    results: list[SyncCreativeResult],
    principal_id: str,
    uow: CreativeUoW,
) -> list:
    """Process creative-to-package assignments and update results in-place.

    Handles the full assignment flow: package lookup, format validation,
    idempotent upsert of creative_assignments rows, and media-buy status
    transitions.  Mutates *results* in-place to populate ``assigned_to``
    and ``assignment_errors`` on matching ``SyncCreativeResult`` entries.

    ONE write path. There is no preview branch here: the same resolution,
    validation, raise, upsert, weight normalization and media-buy status
    transition run for every caller.

    ``uow`` is the sync's own open transaction, or its caller's when it joined one. Validate
    first, save second: the creatives are flushed and not committed when this runs, so a refusal
    here takes them with it (sync-creatives-request.json: "'strict' fails entire sync on any
    validation error"), whichever disposes of the unit: its commit, a preview's rollback, or a
    joined caller's decision.

    Returns:
        List of ``CreativeAssignment`` schema objects created or updated.
    """
    from src.core.schemas import CreativeAssignment

    assignment_list: list[CreativeAssignment] = []
    # Track assignments per creative for response population
    assignments_by_creative: dict[str, list[str]] = {}  # creative_id -> [package_ids]
    assignment_errors_by_creative: dict[str, dict[str, str]] = {}  # creative_id -> {package_id: error}
    media_buys_with_new_assignments: dict[str, Any] = {}  # media_buy_id -> MediaBuy object
    # (media_buy_id, package_id) -> the package's product, for every package this request
    # assigned to: its whole creative set is graded for placement coverage once the
    # request's assignments are in place.
    packages_to_cover: dict[tuple[str, str], Any] = {}

    assignments = _assignment_terms(assignments)

    # Creatives whose sync failed were never persisted; we must not attempt to
    # assign them (the creative_assignments FK would crash the request). Their
    # failure is already recorded in ``results`` (action="failed"); the caller
    # surfaces it. Collect those ids so the assignment loop skips them quietly.
    failed_creative_ids = {r.creative_id for r in results if getattr(r, "action", None) == "failed"}

    if assignments:
        assert uow.assignments is not None
        assignment_repo = uow.assignments

        for creative_id, terms_by_package in assignments.items():
            package_ids = list(terms_by_package)
            # Initialize tracking for this creative
            if creative_id not in assignments_by_creative:
                assignments_by_creative[creative_id] = []
            if creative_id not in assignment_errors_by_creative:
                assignment_errors_by_creative[creative_id] = {}

            # A creative whose sync failed THIS request must not be assigned: if it
            # was never persisted the creative_assignments FK would crash the whole
            # request with a raw IntegrityError (#1418), and even a previously
            # persisted (stale) version must not be silently assigned when the
            # requested sync failed. The failure is already recorded on the
            # per-creative result (action="failed") and the caller (e.g.
            # update_media_buy) raises the buyer-facing, retryable AdCPAdapterError
            # for it — skip quietly in every validation mode instead of masking it
            # with a validation error (#1417).
            if creative_id in failed_creative_ids:
                for package_id in package_ids:
                    error_msg = f"Creative {creative_id} was not synced; skipping assignment to package {package_id}"
                    assignment_errors_by_creative[creative_id][package_id] = error_msg
                    logger.warning(log_safe(error_msg))
                continue

            # A creative_id absent from the creative library never existed —
            # inserting its assignment would also violate the creatives FK (#1418).
            # Principal-scoped: the composite PK is (creative_id, tenant_id,
            # principal_id), so another principal's creative must resolve to
            # "not found" here — never pass the gate on their row (which the
            # FK insert below would then violate) or read their fields.
            # Resolve the creative once up front and report the skipped packages
            # via assignment_errors (same convention as package-not-found below).
            creative_row = _resolve_creative_for_assignment(assignment_repo, creative_id, principal_id)
            if creative_row is None:
                # Entity-specific spec code (pinned enum: CREATIVE_NOT_FOUND,
                # correctable, MANDATED uniformly for unowned creative_ids) —
                # parity with the PACKAGE_NOT_FOUND branch below (#1430 review).
                # The id is echoed back because the caller supplied it verbatim,
                # which 3.1.1 L3/error-handling.mdx permits; every unresolvable
                # id reads identically, so the uniformity the enum mandates holds.
                # No index is available here (the list is normalised to a dict
                # above), so `field` names the array parameter itself.
                raise AdCPCreativeNotFoundError(
                    details=CreativeRefDetails(creative_id=creative_id),
                    field="assignments",
                )

            for package_id, (requested_weight, placement_ids) in terms_by_package.items():
                # Find which media buy this package belongs to
                pkg_result = assignment_repo.find_package_with_media_buy(package_id)

                media_buy_id = None
                actual_package_id = None
                if pkg_result:
                    db_package, db_media_buy = pkg_result
                    media_buy_id = db_package.media_buy_id
                    actual_package_id = db_package.package_id

                if not media_buy_id:
                    # Use the specific subclass so the wire code is PACKAGE_NOT_FOUND
                    # (STANDARD); the base AdCPNotFoundError would emit INVALID_REQUEST
                    # via the wire-safe translation and lose buyer-facing specificity.
                    # WHICH package travels in details: a sync may name many, and the
                    # message is a function of the code, so without this the buyer
                    # learns that a package was not found and not which one.
                    raise AdCPPackageNotFoundError(
                        details=EntityRefDetails(creative_id=creative_id, package_id=package_id)
                    )

                # Get product_id from package_config
                product_id = db_package.package_config.get("product_id") if db_package.package_config else None
                product = assignment_repo.get_product_by_id(product_id) if product_id else None
                if product is not None:
                    _refuse_format_the_product_does_not_take(creative_row, product, creative_id, package_id)

                # actual_package_id is always set when media_buy_id is set (guard above)
                assert actual_package_id is not None

                # Persisted unconditionally: one write path. Under a
                # caller-supplied dry_run UoW these writes land in that
                # transaction and are rolled back with it, so the preview
                # reports exactly what a live run would have written.
                existing_assignment = assignment_repo.get_existing(
                    media_buy_id=media_buy_id,
                    package_id=actual_package_id,
                    creative_id=creative_id,
                    principal_id=principal_id,
                )

                # The pinned sync-creatives-request.json: weight is "Relative delivery
                # weight (0-100) ... When omitted, the creative receives equal rotation
                # with other unweighted creatives. A weight of 0 means the creative is
                # assigned but paused"; placement_ids "Restrict this creative to specific
                # placements within the package. When omitted, the creative is eligible
                # for all placements." Omitted weight is the column default, 100 -- the
                # same value every unweighted creative gets, which IS equal rotation.
                # The pin types weight as a number; the column is an integer, as it is
                # for the update_media_buy writer of the same column.
                weight = 100 if requested_weight is None else int(requested_weight)
                if existing_assignment:
                    # Assignment already exists - carry this request's terms onto it
                    if existing_assignment.weight != weight or existing_assignment.placement_ids != placement_ids:
                        existing_assignment.weight = weight
                        existing_assignment.placement_ids = placement_ids
                        logger.info(
                            log_safe(
                                f"Updated existing assignment: creative={creative_id}, "
                                f"package={actual_package_id}, media_buy={media_buy_id}"
                            )
                        )
                    assignment = existing_assignment
                else:
                    # Create new assignment
                    assignment = assignment_repo.create(
                        media_buy_id=media_buy_id,
                        package_id=actual_package_id,
                        creative_id=creative_id,
                        principal_id=principal_id,
                        weight=weight,
                        placement_ids=placement_ids,
                    )
                    logger.info(
                        log_safe(
                            f"Created new assignment: creative={creative_id}, "
                            f"package={actual_package_id}, media_buy={media_buy_id}"
                        )
                    )

                if product is not None:
                    packages_to_cover[(media_buy_id, actual_package_id)] = product

                # Track media buy for potential status update (for any assignment, new or existing)
                if media_buy_id and db_media_buy and media_buy_id not in media_buys_with_new_assignments:
                    media_buys_with_new_assignments[media_buy_id] = db_media_buy

                assignment_list.append(
                    CreativeAssignment(
                        assignment_id=assignment.assignment_id,
                        media_buy_id=assignment.media_buy_id,
                        package_id=assignment.package_id,
                        creative_id=assignment.creative_id,
                        weight=assignment.weight,
                    )
                )

                # Track successful assignment
                if actual_package_id is not None:
                    assignments_by_creative[creative_id].append(actual_package_id)

        # core/product.json format_options: "the selected creative set MUST cover every
        # included placement; uncovered inventory MUST be rejected or refined, never
        # silently omitted". Asked of each package's WHOLE set -- the creatives already
        # assigned and the ones this request added, each with the placement_ids that
        # routes it -- because coverage is a property of the set, not of one creative.
        for (mb_id, pkg_id), pkg_product in packages_to_cover.items():
            require_package_covered(assignment_repo, mb_id, pkg_id, pkg_product, field="assignments")

        # Update media buy status if needed (draft -> pending_creatives).
        assert uow.media_buys is not None
        for mb_id, mb_obj in media_buys_with_new_assignments.items():
            if mb_obj.status == "draft" and mb_obj.approved_at is not None:
                uow.media_buys.update_status(mb_id, PersistedMediaBuyStatus.PENDING_CREATIVES)
                logger.info(f"[SYNC_CREATIVES] Media buy {mb_id} transitioned from draft to pending_creatives")
            advance_past_pending_creatives(uow.media_buys, assignment_repo, mb_id)

    # Update creative results with assignment information (per AdCP spec)
    for sync_result in results:
        if sync_result.creative_id in assignments_by_creative:
            assigned_packages = assignments_by_creative[sync_result.creative_id]
            if assigned_packages:
                sync_result.assigned_to = assigned_packages

        if sync_result.creative_id in assignment_errors_by_creative:
            errors = assignment_errors_by_creative[sync_result.creative_id]
            if errors:
                sync_result.assignment_errors = errors

    # Referenced-but-unsynced creatives (assignment-only references to existing library
    # creatives) have NO entry in `results` — synthesize one so their outcome reaches the
    # buyer. 'unchanged': the sync didn't modify them.
    present_ids = {r.creative_id for r in results}
    for creative_id in sorted(assignments_by_creative.keys()):
        assigned = assignments_by_creative[creative_id]
        if creative_id in present_ids or not assigned:
            continue
        results.append(
            SyncCreativeResult(
                creative_id=creative_id,
                action="unchanged",
                status=None,
                platform_id=None,
                assigned_to=assigned,
            )
        )

    return assignment_list
