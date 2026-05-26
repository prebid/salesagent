"""Creative-to-package assignment processing."""

import logging
from typing import Any

from src.core.database.repositories.uow import CreativeUoW
from src.core.exceptions import AdCPNotFoundError, AdCPValidationError
from src.core.format_cache import canonical_format_identity
from src.core.schemas import SyncCreativeResult
from src.core.tools.media_buy_create import _status_after_creative_attachment

logger = logging.getLogger(__name__)


def _process_assignments(
    assignments: dict | list | None,
    results: list[SyncCreativeResult],
    tenant: dict[str, Any],
    validation_mode: str,
    principal_id: str | None = None,
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

                for package_id in package_ids:
                    # Find which media buy this package belongs to
                    pkg_result = assignment_repo.find_package_with_media_buy(package_id, principal_id=principal_id)

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
                            raise AdCPNotFoundError(error_msg, recovery="correctable")
                        else:
                            logger.warning(f"Package not found during assignment: {package_id}, skipping")
                            continue

                    # Validate creative format against package product formats
                    db_creative_result = assignment_repo.get_creative_by_id(creative_id)

                    # Get product_id from package_config
                    product_id = db_package.package_config.get("product_id") if db_package.package_config else None

                    if db_creative_result and product_id:
                        # Get product formats
                        product = assignment_repo.get_product_by_id(product_id)

                        if product and product.format_ids:
                            # Build set of supported canonical format identities.
                            # Older persisted products may still carry legacy
                            # reference-agent IDs like display_300x250; compare
                            # them as display_image + width/height so canonical
                            # migration does not collapse all display sizes.
                            supported_formats: set[tuple[str, str, int | None, int | None, int | None]] = set()
                            for fmt in product.format_ids:
                                if isinstance(fmt, dict):
                                    agent_url_val = fmt.get("agent_url")
                                    format_id_val = fmt.get("id") or fmt.get("format_id")
                                    if agent_url_val and format_id_val:
                                        supported_formats.add(canonical_format_identity(fmt))

                            # Check creative format against supported formats
                            creative_agent_url = db_creative_result.agent_url
                            creative_format_id = db_creative_result.format
                            format_parameters = getattr(db_creative_result, "format_parameters", None)
                            if not isinstance(format_parameters, dict):
                                format_parameters = {}
                            creative_identity = canonical_format_identity(
                                {
                                    "agent_url": creative_agent_url,
                                    "id": creative_format_id,
                                    **format_parameters,
                                }
                            )
                            is_supported = creative_identity in supported_formats

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
                                    [
                                        f"{url}/{fmt_id}" + (f" {width}x{height}" if width and height else "")
                                        for url, fmt_id, width, height, _duration_ms in supported_formats
                                    ]
                                )
                                error_msg = (
                                    f"Creative {creative_id} format '{creative_format_display}' "
                                    f"is not supported by product '{product.name}' (package {package_id}). "
                                    f"Supported formats: {supported_formats_display}"
                                )
                                assignment_errors_by_creative[creative_id][package_id] = error_msg

                                if validation_mode == "strict":
                                    raise AdCPValidationError(error_msg)
                                else:
                                    logger.warning(f"Creative format mismatch during assignment, skipping: {error_msg}")
                                    continue

                    # Check if assignment already exists (idempotent operation)
                    # actual_package_id is always set when media_buy_id is set (guard above)
                    assert actual_package_id is not None
                    existing_assignment = assignment_repo.get_existing(
                        media_buy_id=media_buy_id,
                        package_id=actual_package_id,
                        creative_id=creative_id,
                    )

                    if existing_assignment:
                        # Assignment already exists - update weight if needed
                        if existing_assignment.weight != 100:
                            existing_assignment.weight = 100
                            logger.info(
                                f"Updated existing assignment: creative={creative_id}, "
                                f"package={actual_package_id}, media_buy={media_buy_id}"
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
                            f"Created new assignment: creative={creative_id}, "
                            f"package={actual_package_id}, media_buy={media_buy_id}"
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

            # Update media buy status if needed. ``pending_creatives`` means no
            # creatives are assigned, so attaching creatives clears that blocker
            # independently of creative review state.
            for mb_id, mb_obj in media_buys_with_new_assignments.items():
                previous_status = mb_obj.status
                next_status = _status_after_creative_attachment(
                    current_status=previous_status,
                    approved_at=mb_obj.approved_at,
                    start_time=mb_obj.start_time,
                    end_time=mb_obj.end_time,
                )
                if next_status is not None:
                    mb_obj.status = next_status
                    logger.info(
                        f"[SYNC_CREATIVES] Media buy {mb_id} transitioned from {previous_status} to {mb_obj.status}"
                    )

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

    return assignment_list
