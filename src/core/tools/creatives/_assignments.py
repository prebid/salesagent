"""Creative-to-package assignment processing."""

import logging
from contextlib import ExitStack
from typing import Any

from src.core.database.models import PersistedMediaBuyStatus
from src.core.database.repositories.uow import CreativeUoW
from src.core.errors.details import CreativeRefDetails, EntityRefDetails, ValidationDetails
from src.core.exceptions import (
    AdCPCreativeNotFoundError,
    AdCPPackageNotFoundError,
    AdCPSalesAgentError,
    AdCPValidationError,
)
from src.core.format_resolver import format_display, format_identity_or_none, product_format_identities
from src.core.logging_config import log_safe
from src.core.schemas import SyncCreativeResult
from src.core.tenant_context import TenantContext
from src.core.tools.creatives._processing import _failed_sync_result

logger = logging.getLogger(__name__)


def _resolve_creative_for_assignment(assignment_repo, creative_id: str, principal_id: str):
    """Resolve the creative state this assignment must be graded against.

    One lookup for both branches. The creative writes this request made are visible
    either way: live reads them as committed rows (the sync transaction closed
    before this stage), preview reads them as flushed rows in the shared,
    to-be-rolled-back transaction. Returns the DB row, or ``None`` when the
    creative does not exist.
    """
    return assignment_repo.get_creative_by_id(creative_id, principal_id)


#: What one assignment entry asks for beyond naming the package: the pinned
#: sync-creatives-request.json's optional ``assignments[].weight`` and
#: ``assignments[].placement_ids``, ``None`` when the entry omits them.
AssignmentTerms = tuple[float | None, list[str] | None]

#: ``{creative_id: {package_id: (weight, placement_ids)}}`` -- the internal shape.
AssignmentMap = dict[str, dict[str, AssignmentTerms]]


def _normalise_assignments(entries: list[Any]) -> AssignmentMap:
    """The AdCP 3.1 assignment ARRAY, as the internal ``{creative_id: {package_id: terms}}`` map.

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


def _process_assignments(
    assignments: dict | list | None,
    results: list[SyncCreativeResult],
    tenant: TenantContext,
    validation_mode: str,
    principal_id: str,
    uow: CreativeUoW | None = None,
) -> list:
    """Process creative-to-package assignments and update results in-place.

    Handles the full assignment flow: package lookup, format validation,
    idempotent upsert of creative_assignments rows, and media-buy status
    transitions.  Mutates *results* in-place to populate ``assigned_to``
    and ``assignment_errors`` on matching ``SyncCreativeResult`` entries.

    ONE write path. There is no preview branch here: the same resolution,
    validation, strict-raise, upsert, weight normalization and media-buy status
    transition run for every caller.

    ``uow`` lets the caller supply an ALREADY-OPEN unit of work to join instead
    of opening one. sync_creatives passes its own under ``dry_run`` so the
    creative writes and these assignment writes share a single transaction that
    is rolled back as a unit — which is also what lets this stage read those
    creatives as flushed rows. Passing nothing opens (and commits) a transaction
    of its own, the live behavior.

    Returns:
        List of ``CreativeAssignment`` schema objects created or updated.
    """
    from src.core.schemas import CreativeAssignment

    assignment_list: list[CreativeAssignment] = []
    # Track assignments per creative for response population
    assignments_by_creative: dict[str, list[str]] = {}  # creative_id -> [package_ids]
    assignment_errors_by_creative: dict[str, dict[str, str]] = {}  # creative_id -> {package_id: error}
    not_found_creative_ids: set[str] = set()  # creative_ids whose library lookup returned None
    packages_not_found_by_creative: dict[str, set[str]] = {}  # creative_id -> package_ids that do not exist
    media_buys_with_new_assignments: dict[str, Any] = {}  # media_buy_id -> MediaBuy object

    # AdCP v3 spec defines assignments as list[{creative_id, package_id, weight?,
    # placement_ids?}]; normalise to {creative_id: {package_id: (weight, placement_ids)}}
    # for internal processing. A caller still handing over the older {creative_id:
    # [package_ids]} map asks for the default terms on every entry.
    if assignments and isinstance(assignments, list):
        coerced = _normalise_assignments(assignments)
        assignments = coerced if coerced else None
    elif assignments and isinstance(assignments, dict):
        assignments = {
            creative_id: dict.fromkeys(package_ids, (None, None)) for creative_id, package_ids in assignments.items()
        }

    # Creatives whose sync failed were never persisted; we must not attempt to
    # assign them (the creative_assignments FK would crash the request). Their
    # failure is already recorded in ``results`` (action="failed"); the caller
    # surfaces it. Collect those ids so the assignment loop skips them quietly.
    failed_creative_ids = {r.creative_id for r in results if getattr(r, "action", None) == "failed"}

    if assignments and isinstance(assignments, dict):
        with ExitStack() as stack:
            # Join the caller's transaction when given one; otherwise own a
            # transaction for the duration, exactly as before.
            if uow is None:
                uow = stack.enter_context(CreativeUoW(tenant.tenant_id))
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
                        error_msg = (
                            f"Creative {creative_id} was not synced; skipping assignment to package {package_id}"
                        )
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
                    error_msg = f"Creative not found: {creative_id}"
                    not_found_creative_ids.add(creative_id)
                    for package_id in package_ids:
                        assignment_errors_by_creative[creative_id][package_id] = error_msg
                    if validation_mode == "strict":
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
                    logger.warning(log_safe(f"Skipping assignments for unknown creative {creative_id}: {error_msg}"))
                    continue

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
                        # Package not found - record error
                        error_msg = f"Package not found: {package_id}"
                        assignment_errors_by_creative[creative_id][package_id] = error_msg
                        packages_not_found_by_creative.setdefault(creative_id, set()).add(package_id)

                        # Skip if in lenient mode, error if strict
                        if validation_mode == "strict":
                            # Use the specific subclass so the wire code is PACKAGE_NOT_FOUND
                            # (STANDARD); the base AdCPNotFoundError would emit INVALID_REQUEST
                            # via the wire-safe translation and lose buyer-facing specificity.
                            # WHICH package travels in details: a sync may name many, and the
                            # message is a function of the code, so without this the buyer
                            # learns that a package was not found and not which one.
                            raise AdCPPackageNotFoundError(
                                details=EntityRefDetails(creative_id=creative_id, package_id=package_id)
                            )
                        else:
                            logger.warning(log_safe(f"Package not found during assignment: {package_id}, skipping"))
                            continue

                    # Validate creative format against package product formats.
                    # creative_row was fetched once above (guaranteed non-None here).
                    db_creative_result = creative_row

                    # Get product_id from package_config
                    product_id = db_package.package_config.get("product_id") if db_package.package_config else None

                    if db_creative_result and product_id:
                        # Get product formats
                        product = assignment_repo.get_product_by_id(product_id)

                        if product and product.format_ids:
                            # Identity is (canonical agent_url, id) per the pinned
                            # core/format-id.json, asked of format_resolver so this path and
                            # the media_buy_update assignment path cannot disagree about
                            # whether a creative's format is one the product declares.
                            supported_formats = product_format_identities(product.format_ids)
                            creative_identity = format_identity_or_none(
                                {"agent_url": db_creative_result.agent_url, "id": db_creative_result.format}
                            )

                            # A product with no usable format entries imposes no restriction.
                            is_supported = not supported_formats or creative_identity in supported_formats

                            if not is_supported:
                                # Creative format not supported by product
                                creative_format_display = (
                                    format_display(creative_identity)
                                    if creative_identity
                                    else str(db_creative_result.format)
                                )
                                supported_formats_display = ", ".join(
                                    format_display(identity) for identity in sorted(supported_formats)
                                )
                                error_msg = (
                                    f"Creative {creative_id} format '{creative_format_display}' "
                                    f"is not supported by product '{product.name}' (package {package_id}). "
                                    f"Supported formats: {supported_formats_display}"
                                )
                                assignment_errors_by_creative[creative_id][package_id] = error_msg

                                if validation_mode == "strict":
                                    # Reverses #1417, which routed this to CREATIVE_REJECTED: adcp
                                    # 3.1.1's enum reserves that for "Creative failed content policy
                                    # review". Converged with the create and update paths.
                                    raise AdCPValidationError(
                                        # `supported_formats` is the pin-canonical `accepted_values`.
                                        # supported_formats_display is a JOINED string, not a list -- wrap it so the
                                        # canonical accepted_values stays an array as the pin declares.
                                        details=ValidationDetails(accepted_values=[supported_formats_display]),
                                    )
                                else:
                                    logger.warning(
                                        log_safe(f"Creative format mismatch during assignment, skipping: {error_msg}")
                                    )
                                    continue

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

            # Update media buy status if needed (draft -> pending_creatives).
            assert uow.media_buys is not None
            for mb_id, mb_obj in media_buys_with_new_assignments.items():
                if mb_obj.status == "draft" and mb_obj.approved_at is not None:
                    uow.media_buys.update_status(mb_id, PersistedMediaBuyStatus.PENDING_CREATIVES)
                    logger.info(f"[SYNC_CREATIVES] Media buy {mb_id} transitioned from draft to pending_creatives")

            # An owned UoW commits on clean exit; a caller-supplied one is the
            # caller's to dispose of (rolled back under dry_run).

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

    # Referenced-but-unsynced creatives (assignment-only references to existing
    # library creatives, or ids that don't exist at all) have NO entry in
    # `results` — synthesize one so their recorded outcome reaches the buyer.
    # The spec's success branch FORBIDS a response-level errors array; per-item
    # failures ride creatives[] with action='failed' (errors[] required, status
    # omitted), and BR-RULE-033 INV-4 pins that assignment errors are always
    # recorded in the response. Without this, creatives=[] + assignments
    # returned bare success and the buyer never learned the assignment was
    # skipped (#1430: orphan-assignment visibility fix).
    present_ids = {r.creative_id for r in results}
    for creative_id in sorted(assignments_by_creative.keys() | assignment_errors_by_creative.keys()):
        if creative_id in present_ids:
            continue
        assigned = assignments_by_creative.get(creative_id) or []
        errors = assignment_errors_by_creative.get(creative_id) or {}
        if not assigned and not errors:
            continue
        if assigned:
            # Existing library creative referenced only via assignments: the
            # sync didn't modify it — 'unchanged', with its assignment outcome
            # (and any partial failures) attached.
            entry = SyncCreativeResult(
                creative_id=creative_id,
                action="unchanged",
                status=None,
                platform_id=None,
                review_feedback=None,
                assigned_to=assigned,
                assignment_errors=errors or None,
            )
        else:
            # Nothing assigned: every referenced package failed. Buyer-correctable.
            #
            # A creative-not-found entry carries CREATIVE_NOT_FOUND, the same code the
            # strict-mode raise above emits, so the two validation modes agree on this
            # condition. The ``continue`` in the not-found branch means such an entry can
            # never also carry package causes, which is what keeps the two exclusive.
            #
            # A package-not-found entry carries PACKAGE_NOT_FOUND for the same reason: the
            # strict path raises AdCPPackageNotFoundError for this condition, and the
            # pinned enum defines the code, so the lenient advisory names the same thing.
            # Only when EVERY package the entry names was not found -- a mixed entry (one
            # package missing, another refusing the format) is a validation failure, and
            # ``assignment_errors`` spells out each package's cause.
            #
            # ``assignment_errors`` is NOT duplicated into details: the line below sets
            # it on the result entry, which is the buyer's path to it. Two copies of one
            # fact is what this migration removes. Each code carries its own details
            # shape — the exception class declares which one it accepts.
            cause: AdCPSalesAgentError
            missing_packages = packages_not_found_by_creative.get(creative_id, set())
            if creative_id in not_found_creative_ids:
                cause = AdCPCreativeNotFoundError(details=CreativeRefDetails(creative_id=creative_id))
            elif missing_packages and set(errors) == missing_packages:
                cause = AdCPPackageNotFoundError(
                    details=EntityRefDetails(
                        creative_id=creative_id,
                        package_id=next(iter(missing_packages)) if len(missing_packages) == 1 else None,
                    )
                )
            else:
                cause = AdCPValidationError(details=ValidationDetails(creative_id=creative_id))
            entry = _failed_sync_result(creative_id, cause)
            entry.assignment_errors = errors
        results.append(entry)

    return assignment_list
