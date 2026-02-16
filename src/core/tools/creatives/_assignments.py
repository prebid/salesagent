"""Creative-to-package assignment processing."""

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from fastmcp.exceptions import ToolError
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.schemas import SyncCreativeResult

logger = logging.getLogger(__name__)


def _process_assignments(
    assignments: dict | None,
    results: list[SyncCreativeResult],
    tenant: dict[str, Any],
    validation_mode: str,
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

    # Note: assignments should be a dict, but handle both dict and None
    if assignments and isinstance(assignments, dict):
        with get_db_session() as session:
            from src.core.database.models import CreativeAssignment as DBAssignment
            from src.core.database.models import MediaBuy, MediaPackage

            for creative_id, package_ids in assignments.items():
                # Initialize tracking for this creative
                if creative_id not in assignments_by_creative:
                    assignments_by_creative[creative_id] = []
                if creative_id not in assignment_errors_by_creative:
                    assignment_errors_by_creative[creative_id] = {}

                for package_id in package_ids:
                    # Find which media buy this package belongs to by querying MediaPackage table
                    # Note: We need to join with MediaBuy to verify tenant_id
                    from sqlalchemy import join

                    package_stmt = (
                        select(MediaPackage, MediaBuy)
                        .select_from(join(MediaPackage, MediaBuy, MediaPackage.media_buy_id == MediaBuy.media_buy_id))
                        .where(MediaPackage.package_id == package_id)
                        .where(MediaBuy.tenant_id == tenant["tenant_id"])
                    )
                    result = session.execute(package_stmt).first()

                    media_buy_id = None
                    actual_package_id = None
                    if result:
                        db_package, db_media_buy = result
                        media_buy_id = db_package.media_buy_id
                        actual_package_id = db_package.package_id

                    if not media_buy_id:
                        # Package not found - record error
                        error_msg = f"Package not found: {package_id}"
                        assignment_errors_by_creative[creative_id][package_id] = error_msg

                        # Skip if in lenient mode, error if strict
                        if validation_mode == "strict":
                            raise ToolError(error_msg)
                        else:
                            logger.warning(f"Package not found during assignment: {package_id}, skipping")
                            continue

                    # Validate creative format against package product formats
                    # Get creative format
                    from src.core.database.models import Creative as DBCreative
                    from src.core.database.models import Product

                    creative_stmt = select(DBCreative).where(
                        DBCreative.tenant_id == tenant["tenant_id"], DBCreative.creative_id == creative_id
                    )
                    db_creative_result = session.scalars(creative_stmt).first()

                    # Get product_id from package_config
                    product_id = db_package.package_config.get("product_id") if db_package.package_config else None

                    if db_creative_result and product_id:
                        # Get product formats
                        product_stmt = select(Product).where(
                            Product.tenant_id == tenant["tenant_id"], Product.product_id == product_id
                        )
                        product = session.scalars(product_stmt).first()

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
                                    raise ToolError(error_msg)
                                else:
                                    logger.warning(f"Creative format mismatch during assignment, skipping: {error_msg}")
                                    continue

                    # Check if assignment already exists (idempotent operation)
                    stmt_existing = select(DBAssignment).filter_by(
                        tenant_id=tenant["tenant_id"],
                        media_buy_id=media_buy_id,
                        package_id=actual_package_id,
                        creative_id=creative_id,
                    )
                    existing_assignment = session.scalars(stmt_existing).first()

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
                        # Create new assignment in creative_assignments table
                        assignment = DBAssignment(
                            tenant_id=tenant["tenant_id"],
                            assignment_id=str(uuid.uuid4()),
                            media_buy_id=media_buy_id,
                            package_id=actual_package_id,  # Use resolved package_id
                            creative_id=creative_id,
                            weight=100,
                            created_at=datetime.now(UTC),
                        )
                        session.add(assignment)
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

            # Update media buy status if needed (draft -> pending_creatives)
            for mb_id, mb_obj in media_buys_with_new_assignments.items():
                if mb_obj.status == "draft" and mb_obj.approved_at is not None:
                    mb_obj.status = "pending_creatives"
                    logger.info(f"[SYNC_CREATIVES] Media buy {mb_id} transitioned from draft to pending_creatives")

            session.commit()

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
