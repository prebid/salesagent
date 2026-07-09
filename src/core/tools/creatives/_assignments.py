"""Creative-to-package assignment processing."""

import logging
from typing import Any

from src.core.database.repositories.uow import CreativeUoW
from src.core.exceptions import AdCPCreativeRejectedError, AdCPPackageNotFoundError, AdCPValidationError
from src.core.schemas import SyncCreativeResult
from src.core.tools.creatives._processing import _failed_sync_result

logger = logging.getLogger(__name__)


def _log_safe(value: object) -> str:
    """Neutralize CR/LF in request-provided values before logging.

    Buyer-supplied ids (creative_id, package_id) flow into log lines; a
    newline embedded in one would forge log entries (CodeQL py/log-injection).
    Response payloads are NOT sanitized — buyers correlate on exact ids.
    """
    return str(value).replace("\r", "").replace("\n", "")


def _process_assignments(
    assignments: dict | list | None,
    results: list[SyncCreativeResult],
    tenant: dict[str, Any],
    validation_mode: str,
    principal_id: str,
) -> list:
    """Process creative-to-package assignments and update results in-place.

    Handles the full assignment flow: package lookup, format validation,
    idempotent upsert of creative_assignments rows, and media-buy status
    transitions.  Mutates *results* in-place to populate ``assigned_to``
    and ``assignment_errors`` on matching ``SyncCreativeResult`` entries.

    Returns:
        List of ``CreativeAssignment`` schema objects created or updated.
    """
    from src.core.schemas import CreativeAssignment

    assignment_list: list[CreativeAssignment] = []
    # Track assignments per creative for response population
    assignments_by_creative: dict[str, list[str]] = {}  # creative_id -> [package_ids]
    assignment_errors_by_creative: dict[str, dict[str, str]] = {}  # creative_id -> {package_id: error}
    media_buys_with_new_assignments: dict[str, Any] = {}  # media_buy_id -> MediaBuy object

    # AdCP v3 spec defines assignments as list[{creative_id, package_id, ...}];
    # normalise to dict form {creative_id: [package_ids]} for internal processing.
    if assignments and isinstance(assignments, list):
        coerced: dict[str, list[str]] = {}
        for entry in assignments:
            if isinstance(entry, dict) and "creative_id" in entry and "package_id" in entry:
                coerced.setdefault(entry["creative_id"], []).append(entry["package_id"])
        assignments = coerced if coerced else None

    # Creatives whose sync failed were never persisted; we must not attempt to
    # assign them (the creative_assignments FK would crash the request). Their
    # failure is already recorded in ``results`` (action="failed"); the caller
    # surfaces it. Collect those ids so the assignment loop skips them quietly.
    failed_creative_ids = {r.creative_id for r in results if getattr(r, "action", None) == "failed"}

    if assignments and isinstance(assignments, dict):
        with CreativeUoW(tenant["tenant_id"]) as uow:
            assert uow.assignments is not None
            assignment_repo = uow.assignments

            for creative_id, package_ids in assignments.items():
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
                        logger.warning(_log_safe(error_msg))
                    continue

                # A creative_id absent from the creative library never existed —
                # inserting its assignment would also violate the creatives FK (#1418).
                # Principal-scoped: the composite PK is (creative_id, tenant_id,
                # principal_id), so another principal's creative must resolve to
                # "not found" here — never pass the gate on their row (which the
                # FK insert below would then violate) or read their fields.
                # Resolve the creative once up front and report the skipped packages
                # via assignment_errors (same convention as package-not-found below).
                creative_row = assignment_repo.get_creative_by_id(creative_id, principal_id)
                if creative_row is None:
                    error_msg = f"Creative not found: {creative_id}"
                    for package_id in package_ids:
                        assignment_errors_by_creative[creative_id][package_id] = error_msg
                    if validation_mode == "strict":
                        raise AdCPValidationError(
                            error_msg,
                            suggestion=(
                                "Sync the creative via sync_creatives (or include it in this "
                                "request's creatives array) before assigning it to a package."
                            ),
                        )
                    logger.warning(_log_safe(f"Skipping assignments for unknown creative {creative_id}: {error_msg}"))
                    continue

                for package_id in package_ids:
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

                        # Skip if in lenient mode, error if strict
                        if validation_mode == "strict":
                            # Use the specific subclass so the wire code is PACKAGE_NOT_FOUND
                            # (STANDARD); the base AdCPNotFoundError would emit INVALID_REQUEST
                            # via the wire-safe translation and lose buyer-facing specificity.
                            raise AdCPPackageNotFoundError(error_msg)
                        else:
                            logger.warning(_log_safe(f"Package not found during assignment: {package_id}, skipping"))
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
                            # Build set of supported formats (agent_url, format_id) tuples
                            supported_formats: set[tuple[str, str]] = set()
                            for fmt in product.format_ids:
                                if isinstance(fmt, dict):
                                    agent_url_val = fmt.get("agent_url")
                                    format_id_val = fmt.get("id") or fmt.get("format_id")
                                    if agent_url_val and format_id_val:
                                        supported_formats.add((str(agent_url_val), str(format_id_val)))

                            # Check creative format against supported formats
                            creative_agent_url = db_creative_result.agent_url
                            creative_format_id = db_creative_result.format

                            # Allow /mcp URL variant (creative agent may return format with /mcp suffix)
                            def normalize_url(url: str | None) -> str | None:
                                if not url:
                                    return None
                                return url.rstrip("/").removesuffix("/mcp")

                            normalized_creative_url = normalize_url(creative_agent_url)
                            is_supported = False

                            for supported_url, supported_format_id in supported_formats:
                                normalized_supported_url = normalize_url(supported_url)
                                if (
                                    normalized_creative_url == normalized_supported_url
                                    and creative_format_id == supported_format_id
                                ):
                                    is_supported = True
                                    break

                            if not supported_formats:
                                # Product has no format restrictions - allow all
                                is_supported = True

                            if not is_supported:
                                # Creative format not supported by product
                                creative_format_display = (
                                    f"{creative_agent_url}/{creative_format_id}"
                                    if creative_agent_url
                                    else creative_format_id
                                )
                                supported_formats_display = ", ".join(
                                    [f"{url}/{fmt_id}" if url else fmt_id for url, fmt_id in supported_formats]
                                )
                                error_msg = (
                                    f"Creative {creative_id} format '{creative_format_display}' "
                                    f"is not supported by product '{product.name}' (package {package_id}). "
                                    f"Supported formats: {supported_formats_display}"
                                )
                                assignment_errors_by_creative[creative_id][package_id] = error_msg

                                if validation_mode == "strict":
                                    # Converge with the update path (media_buy_update.py:233):
                                    # creative-format-incompatible-with-product is CREATIVE_REJECTED,
                                    # the canonical code for a rejected creative (salesagent-8j5r).
                                    raise AdCPCreativeRejectedError(
                                        error_msg,
                                        suggestion=(
                                            "Assign a creative whose format matches one of the product's "
                                            f"supported formats ({supported_formats_display}), or call "
                                            "list_creative_formats to discover supported formats."
                                        ),
                                        details={"supported_formats": supported_formats_display},
                                    )
                                else:
                                    logger.warning(
                                        _log_safe(f"Creative format mismatch during assignment, skipping: {error_msg}")
                                    )
                                    continue

                    # Check if assignment already exists (idempotent operation)
                    # actual_package_id is always set when media_buy_id is set (guard above)
                    assert actual_package_id is not None
                    existing_assignment = assignment_repo.get_existing(
                        media_buy_id=media_buy_id,
                        package_id=actual_package_id,
                        creative_id=creative_id,
                        principal_id=principal_id,
                    )

                    if existing_assignment:
                        # Assignment already exists - update weight if needed
                        if existing_assignment.weight != 100:
                            existing_assignment.weight = 100
                            logger.info(
                                _log_safe(
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
                        )
                        logger.info(
                            _log_safe(
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

            # Update media buy status if needed (draft -> pending_creatives)
            for mb_id, mb_obj in media_buys_with_new_assignments.items():
                if mb_obj.status == "draft" and mb_obj.approved_at is not None:
                    mb_obj.status = "pending_creatives"
                    logger.info(f"[SYNC_CREATIVES] Media buy {mb_id} transitioned from draft to pending_creatives")

            # UoW auto-commits on clean exit

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
    # skipped (salesagent-9qpj).
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
            # Nothing assigned: every referenced package failed (creative not
            # found, package not found, ...). Buyer-correctable — same
            # VALIDATION_ERROR the strict-mode raise carries.
            message = "; ".join(sorted(set(errors.values())))
            entry = _failed_sync_result(creative_id, message, recovery="correctable", code="VALIDATION_ERROR")
            entry.assignment_errors = errors
        results.append(entry)

    return assignment_list
