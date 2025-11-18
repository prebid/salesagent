"""Create Media Buy tool implementation.

Handles media buy creation including:
- Product selection and validation
- Package configuration with pricing
- Creative assignment
- Ad server order provisioning
- Budget validation
"""

import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlparse

from adcp.types.generated_poc.media_buy_status import MediaBuyStatus
from adcp.types.generated_poc.package_status import PackageStatus
from fastmcp.exceptions import ToolError
from fastmcp.server.context import Context
from fastmcp.tools.tool import ToolResult
from pydantic import ValidationError
from rich.console import Console

logger = logging.getLogger(__name__)
console = Console()


def validate_agent_url(url: str | None) -> bool:
    """Validate agent_url is a valid HTTP(S) URL per AdCP spec.

    Args:
        url: URL string to validate

    Returns:
        True if valid HTTP(S) URL, False otherwise
    """
    if not url or not isinstance(url, str):
        return False
    try:
        result = urlparse(url)
        return all([result.scheme in ("http", "https"), result.netloc])
    except Exception:
        return False


# Tool-specific imports
from src.core import schemas
from src.core.audit_logger import get_audit_logger
from src.core.auth import (
    get_principal_object,
)
from src.core.config_loader import get_current_tenant
from src.core.context_manager import get_context_manager
from src.core.database.models import MediaBuy
from src.core.database.models import Principal as ModelPrincipal
from src.core.database.models import Product as ModelProduct
from src.core.helpers import get_principal_id_from_context, log_tool_activity
from src.core.helpers.adapter_helpers import get_adapter
from src.core.helpers.creative_helpers import _convert_creative_to_adapter_asset, process_and_upload_package_creatives
from src.core.schema_adapters import CreateMediaBuyResponse
from src.core.schemas import (
    CreateMediaBuyError,
    CreateMediaBuyRequest,
    CreateMediaBuySuccess,
    CreativeApprovalStatus,
    Error,
    FormatId,
    MediaPackage,
    Package,
    PackageRequest,
    Principal,
    Product,
    Targeting,
)
from src.core.schemas import (
    url as make_url,
)
from src.core.testing_hooks import TestingContext, apply_testing_hooks, get_testing_context
from src.core.tool_context import ToolContext

# Import get_product_catalog from main (after refactor)
from src.core.validation_helpers import format_validation_error
from src.services.activity_feed import activity_feed

# --- Helper Functions ---


def _sanitize_package_status(status: Any) -> str | None:
    """Ensure package status matches AdCP spec.

    Per AdCP spec, Package.status must be one of: draft, active, paused, completed.
    This is DIFFERENT from TaskStatus (input-required, working, completed, etc.)
    which describes the approval workflow state.

    Common mistake: Setting Package.status to TaskStatus.INPUT_REQUIRED
    Correct: Package.status should be null until adapter creates it (then draft/active)

    Args:
        status: The status value from adapter response or database

    Returns:
        Valid AdCP PackageStatus or None
    """
    # Use library enum as source of truth for valid values
    VALID_PACKAGE_STATUSES = {s.value for s in PackageStatus}

    if status is None:
        return None

    # Convert to string for validation
    status_str = str(status)

    if status_str in VALID_PACKAGE_STATUSES:
        return status_str

    # Log error for non-spec-compliant status - this indicates a bug in our code
    logger.error(
        f"[SPEC-VIOLATION] Package.status='{status_str}' violates AdCP spec! "
        f"Valid PackageStatus values: {VALID_PACKAGE_STATUSES}. "
        f"Note: '{status_str}' may be a TaskStatus (workflow state), which should be stored in WorkflowStep.status, NOT Package.status. "
        f"Setting to None to prevent spec violation."
    )
    return None


def _determine_media_buy_status(
    manual_approval_required: bool,
    has_creatives: bool,
    creatives_approved: bool,
    start_time: datetime,
    end_time: datetime,
    now: datetime | None = None,
) -> str:
    """Centralized media buy status determination logic.

    This ensures consistent status across all adapters (GAM, Mock, Kevel, etc.).

    Status Priority (highest to lowest) - ALL SPEC-COMPLIANT:
    1. pending_activation: Manual approval required OR needs creatives OR scheduled for future
    2. active: Currently delivering (has creatives, approved, within flight dates)
    3. completed: Past end date
    4. paused: (Reserved for future use - not currently returned)

    Internal states mapped to spec statuses:
    - "pending_approval" → pending_activation (awaiting manual approval)
    - "needs_creatives" → pending_activation (missing or unapproved creatives)
    - "ready" → pending_activation (scheduled for future start)

    Args:
        manual_approval_required: Whether the media buy requires manual approval
        has_creatives: Whether creatives have been assigned
        creatives_approved: Whether all assigned creatives are approved
        start_time: Campaign start datetime
        end_time: Campaign end datetime
        now: Current time (defaults to datetime.now(UTC))

    Returns:
        Status string matching AdCP MediaBuyStatus enum (pending_activation, active, paused, completed)
    """
    if now is None:
        now = datetime.now(UTC)

    # Priority 1: Completed (past end date - check first to avoid false pending_activation)
    if now > end_time:
        return MediaBuyStatus.completed.value

    # Priority 2: Pending activation (any blocking condition or scheduled for future)
    # - Manual approval required
    # - Missing creatives or unapproved creatives
    # - Scheduled for future start
    if manual_approval_required or not has_creatives or not creatives_approved or now < start_time:
        return MediaBuyStatus.pending_activation.value

    # Priority 3: Active (currently delivering - all conditions met)
    return MediaBuyStatus.active.value


def _extract_creative_url_and_dimensions(
    creative_data: dict[str, Any], format_spec: Any | None
) -> tuple[str | None, int | None, int | None]:
    """Extract URL and dimensions from creative data.

    All production creatives now use AdCP v2.4 format with data.assets[asset_id]
    containing typed asset objects per the creative format specification.

    Extraction priority:
    1. Format spec assets_required (most specific - AdCP v2.4 compliant)
    2. First available asset with URL (fallback if no format spec)

    Args:
        creative_data: Creative data dict from database
        format_spec: Format specification with assets_required

    Returns:
        Tuple of (url, width, height). Values are None if not found.

    Note:
        - URL extracted from asset types: image, video, url
        - Dimensions extracted from asset types: image, video
        - Type validation: width/height must be int or coercible to int
    """
    # Extract URL from assets using format specification
    url = None
    if creative_data.get("assets") and format_spec and format_spec.assets_required:
        # Use format spec to find the correct asset_id for image/video/url assets
        for asset_req in format_spec.assets_required:
            asset_type = asset_req.asset_type.lower()
            if asset_type in ["image", "video", "url"]:
                asset_id = asset_req.asset_id
                if asset_id in creative_data["assets"]:
                    asset_obj = creative_data["assets"][asset_id]
                    if isinstance(asset_obj, dict) and asset_obj.get("url"):
                        url = asset_obj["url"]
                        break

    # Extract dimensions from assets using format specification
    width = None
    height = None
    if creative_data.get("assets") and format_spec and format_spec.assets_required:
        # Use format spec to find the correct asset_id for image/video assets
        for asset_req in format_spec.assets_required:
            asset_type = asset_req.asset_type.lower()
            if asset_type in ["image", "video"]:
                asset_id = asset_req.asset_id
                if asset_id in creative_data["assets"]:
                    asset_obj = creative_data["assets"][asset_id]
                    if isinstance(asset_obj, dict):
                        raw_width = asset_obj.get("width")
                        raw_height = asset_obj.get("height")
                        # Type validation: ensure int
                        if raw_width is not None:
                            try:
                                width = int(raw_width)
                            except (ValueError, TypeError):
                                logger.warning(
                                    f"Invalid width type in creative assets: {raw_width} (type={type(raw_width)})"
                                )
                        if raw_height is not None:
                            try:
                                height = int(raw_height)
                            except (ValueError, TypeError):
                                logger.warning(
                                    f"Invalid height type in creative assets: {raw_height} (type={type(raw_height)})"
                                )
                        if width and height:
                            break

    return url, width, height


def _validate_creatives_before_adapter_call(packages: list[Package], tenant_id: str) -> None:
    """Validate all creatives have required fields BEFORE calling adapter.

    This prevents GAM order creation when creatives are invalid, enabling
    true all-or-nothing behavior without rollback complexity.

    Fetches format specifications to determine correct asset structure per format.

    Args:
        packages: List of Package objects with creative_ids
        tenant_id: Tenant ID for database lookup

    Raises:
        ToolError: If any creative is missing required fields (URL, dimensions)
    """
    import asyncio

    from sqlalchemy import select

    from src.core.creative_agent_registry import get_creative_agent_registry
    from src.core.database.database_session import get_db_session
    from src.core.database.models import Creative as DBCreative

    # Collect all creative IDs from all packages
    all_creative_ids = set()
    for package in packages:
        if hasattr(package, "creative_ids") and package.creative_ids:
            all_creative_ids.update(package.creative_ids)

    if not all_creative_ids:
        # No creatives to validate
        return

    # Fetch all creatives in one query
    with get_db_session() as session:
        stmt = select(DBCreative).where(
            DBCreative.tenant_id == tenant_id, DBCreative.creative_id.in_(list(all_creative_ids))
        )
        creatives_list = list(session.scalars(stmt).all())

    # Get creative registry for format lookup
    registry = get_creative_agent_registry()

    # Validate each creative has required fields
    validation_errors = []
    for creative in creatives_list:
        creative_data = creative.data or {}

        # Get format specification from cache first, then creative agent
        format_spec = None
        if creative.format:
            from src.core.format_spec_cache import get_cached_format

            # Try cache first (fast, works offline)
            format_spec = get_cached_format(str(creative.format))

            # If not in cache, try fetching from creative agent
            if not format_spec:
                try:
                    # Use asyncio event loop to run async registry.get_format() synchronously
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        format_spec = loop.run_until_complete(
                            registry.get_format(creative.agent_url, str(creative.format))
                        )
                        # Cache the fetched format for future use
                        if format_spec:
                            from src.core.format_spec_cache import save_format_specs_to_cache

                            save_format_specs_to_cache([format_spec])
                    finally:
                        loop.close()
                except Exception as e:
                    logger.warning(f"Could not fetch format {creative.format} from {creative.agent_url}: {e}")

        # Fail validation if format spec not found (no skipping!)
        if not format_spec:
            validation_errors.append(
                f"Creative {creative.creative_id} has unknown format '{creative.format}' "
                f"(not in cache and could not fetch from agent {creative.agent_url})"
            )
            continue

        # Skip validation for generative formats - they need conversion first
        # Generative formats have output_format_ids (they generate reference formats)
        if format_spec.output_format_ids:
            logger.info(
                f"Skipping validation for generative creative {creative.creative_id} "
                f"(format={creative.format}) - will be converted to reference format"
            )
            continue

        # Only validate reference creatives (formats we can directly use)
        # Extract URL and dimensions using shared helper
        url, width, height = _extract_creative_url_and_dimensions(creative_data, format_spec)

        if not url:
            validation_errors.append(
                f"Reference creative {creative.creative_id} (format={creative.format}) "
                f"missing required URL field in assets"
            )
        if not width or not height:
            validation_errors.append(
                f"Reference creative {creative.creative_id} missing dimensions (width={width}, height={height})"
            )

    if validation_errors:
        error_msg = (
            "Cannot create media buy with invalid reference creatives. "
            "The following reference creatives are missing required fields:\n"
            + "\n".join(f"  • {err}" for err in validation_errors)
            + "\n\nReference creatives must have dimensions (width/height) and a content URL "
            "matching their format specification. "
            + "Generative creatives will be converted to reference formats during campaign creation. "
            + "Please ensure reference creatives are properly synced before creating media buys."
        )
        logger.error(f"[PRE-VALIDATION] {error_msg}")
        raise ToolError("INVALID_CREATIVES", error_msg, {"creative_errors": validation_errors})


def _execute_adapter_media_buy_creation(
    request: CreateMediaBuyRequest,
    packages: list[Package],
    start_time: datetime,
    end_time: datetime,
    package_pricing_info: dict[str, dict[str, Any]],
    principal: Principal,
    testing_ctx: TestingContext | None = None,
) -> schemas.CreateMediaBuyResponse:
    """Execute adapter's create_media_buy call.

    This function is shared between auto-approval and manual approval flows
    to ensure consistent adapter behavior across all adapters (GAM, Mock, Kevel, etc.).

    Args:
        request: The CreateMediaBuyRequest with all campaign details
        packages: List of Package objects with product/creative configuration
        start_time: Resolved campaign start datetime
        end_time: Resolved campaign end datetime
        package_pricing_info: Pricing model info per package
        principal: The Principal object (buyer/advertiser)
        testing_ctx: Optional testing context for dry-run mode

    Returns:
        CreateMediaBuyResponse from the adapter

    Raises:
        Exception: If adapter creation fails (with detailed logging)
    """
    # Get adapter using helper (loads config from DB and respects tenant context)
    dry_run = testing_ctx.dry_run if testing_ctx else False
    adapter = get_adapter(principal, dry_run=dry_run, testing_context=testing_ctx)

    # Call adapter with detailed error logging
    try:
        # Type ignore needed because adapter expects MediaPackage but we pass Package (compatible types)
        response = adapter.create_media_buy(request, packages, start_time, end_time, package_pricing_info)  # type: ignore[arg-type]

        # Log based on response type
        if isinstance(response, CreateMediaBuyError):
            error_count = len(response.errors) if response.errors else 0
            logger.error(f"[ADAPTER] create_media_buy returned error response: {error_count} error(s)")
            if response.errors:
                for err in response.errors:
                    logger.error(f"[ADAPTER]   Error: {err.code} - {err.message}")
        else:
            logger.info(
                f"[ADAPTER] create_media_buy succeeded: {response.media_buy_id} "
                f"with {len(response.packages) if response.packages else 0} packages"
            )
            if response.packages:
                for i, pkg in enumerate(response.packages):
                    # response.packages can be dicts or objects
                    pkg_id = pkg.get("package_id") if isinstance(pkg, dict) else pkg.package_id
                    logger.info(f"[ADAPTER] Response package {i}: {pkg_id}")
        return response
    except Exception as adapter_error:
        import traceback

        error_traceback = traceback.format_exc()
        logger.error(f"[ADAPTER] create_media_buy failed:\n{error_traceback}")
        raise


def execute_approved_media_buy(media_buy_id: str, tenant_id: str) -> tuple[bool, str | None]:
    """Execute adapter creation for a manually approved media buy.

    This function is called after a media buy has been manually approved
    (either via media buy approval or creative approval). It reconstructs
    the original request from the database and calls the adapter to create
    the order/line items in the external ad server (GAM, Kevel, etc.).

    This ensures the same adapter logic runs for both auto-approved and
    manually approved media buys.

    Args:
        media_buy_id: The media buy ID to execute
        tenant_id: The tenant ID for context

    Returns:
        Tuple of (success: bool, error_message: str | None)
    """
    from sqlalchemy import select

    from src.core.database.database_session import get_db_session
    from src.core.database.models import MediaBuy
    from src.core.database.models import MediaPackage as DBMediaPackage

    logger.info(f"[APPROVAL] Executing adapter creation for approved media buy {media_buy_id}")

    # Set tenant context (required for adapter helpers to work)
    from src.core.config_loader import set_current_tenant
    from src.core.database.models import Tenant

    try:
        # Load tenant and set context
        with get_db_session() as session:
            stmt_tenant = select(Tenant).filter_by(tenant_id=tenant_id)
            tenant_obj = session.scalars(stmt_tenant).first()

            if not tenant_obj:
                error_msg = f"Tenant {tenant_id} not found"
                logger.error(f"[APPROVAL] {error_msg}")
                return False, error_msg

            # Set tenant context (converts ORM object to dict)
            tenant_dict = {
                "tenant_id": tenant_obj.tenant_id,
                "name": tenant_obj.name,
                "subdomain": tenant_obj.subdomain,
                "ad_server": tenant_obj.ad_server,
                "virtual_host": tenant_obj.virtual_host,
            }
            set_current_tenant(tenant_dict)
            logger.info(f"[APPROVAL] Set tenant context: {tenant_id}")

            # Load media buy
            stmt = select(MediaBuy).filter_by(tenant_id=tenant_id, media_buy_id=media_buy_id)
            media_buy = session.scalars(stmt).first()

            if not media_buy:
                error_msg = f"Media buy {media_buy_id} not found"
                logger.error(f"[APPROVAL] {error_msg}")
                return False, error_msg

            # Reconstruct CreateMediaBuyRequest from raw_request
            try:
                request = CreateMediaBuyRequest(**media_buy.raw_request)
                # Mark this request as already approved to skip adapter's approval workflow
                request._already_approved = True  # type: ignore[attr-defined]
            except ValidationError as ve:
                error_msg = f"Failed to reconstruct request: {format_validation_error(ve)}"
                logger.error(f"[APPROVAL] {error_msg}")
                return False, error_msg

            # Load packages from media_packages table
            stmt_packages = select(DBMediaPackage).filter_by(media_buy_id=media_buy_id)
            db_packages = session.scalars(stmt_packages).all()

            if not db_packages:
                error_msg = f"No packages found for media buy {media_buy_id}"
                logger.error(f"[APPROVAL] {error_msg}")
                return False, error_msg

            # Reconstruct MediaPackage objects (what adapters expect) from database
            # We need to load Products to get name, delivery_type, format_ids, etc.
            from sqlalchemy.orm import selectinload

            from src.core.database.models import Product as ProductModel

            packages: list[MediaPackage] = []
            package_pricing_info: dict[str, dict[str, Any]] = {}

            for db_pkg in db_packages:
                try:
                    package_config = dict(db_pkg.package_config)
                    package_id = package_config.get("package_id")
                    product_id = package_config.get("product_id")

                    if not product_id:
                        error_msg = f"Package {package_id} missing product_id"
                        logger.error(f"[APPROVAL] {error_msg}")
                        return False, error_msg

                    # Load product to get name, delivery_type, format_ids, pricing
                    stmt_product = (
                        select(ProductModel)
                        .filter_by(tenant_id=tenant_id, product_id=product_id)
                        .options(selectinload(ProductModel.pricing_options))
                    )
                    product = session.scalars(stmt_product).first()

                    if not product:
                        error_msg = f"Product {product_id} not found for package {package_id}"
                        logger.error(f"[APPROVAL] {error_msg}")
                        return False, error_msg

                    # Get budget from package_config (AdCP 2.5.0: budget is always float | None)
                    budget_data = package_config.get("budget")
                    if isinstance(budget_data, dict):
                        # Legacy dict format (Budget object) - extract total
                        total_budget = float(budget_data.get("total", 0.0))
                        budget = total_budget  # MediaPackage expects float
                    elif isinstance(budget_data, (int, float)):
                        # AdCP 2.5.0 format - flat number
                        total_budget = float(budget_data)
                        budget = total_budget  # MediaPackage expects float
                    else:
                        total_budget = 0.0
                        budget = None

                    # Get pricing option from product
                    pricing_option = None
                    if product.pricing_options:
                        # Try to match pricing_model from package_config if present
                        pkg_pricing_model = package_config.get("pricing_model")
                        if pkg_pricing_model:
                            pricing_option = next(
                                (po for po in product.pricing_options if po.pricing_model == pkg_pricing_model),
                                None,
                            )
                        if not pricing_option:
                            # Fall back to first pricing option
                            pricing_option = product.pricing_options[0]

                    if not pricing_option:
                        error_msg = f"Product {product_id} has no pricing options"
                        logger.error(f"[APPROVAL] {error_msg}")
                        return False, error_msg

                    # Calculate CPM and impressions (convert Decimal to float for math operations)
                    cpm = float(pricing_option.rate) if pricing_option.rate else 0.0
                    impressions = int(total_budget / cpm * 1000) if cpm > 0 else 0

                    # Reconstruct package_pricing_info from package_config if available
                    # This includes the bid_price for auction pricing
                    pricing_info_from_config = package_config.get("pricing_info")
                    if pricing_info_from_config and package_id:
                        # Use the stored pricing_info which has the correct bid_price
                        package_pricing_info[package_id] = pricing_info_from_config
                    elif package_id:
                        # Fallback for old media buys without pricing_info
                        package_pricing_info[package_id] = {
                            "pricing_model": pricing_option.pricing_model,
                            "currency": pricing_option.currency,
                            "is_fixed": pricing_option.is_fixed,
                            "rate": float(pricing_option.rate) if pricing_option.rate else None,
                            "bid_price": None,
                        }

                    # Get targeting_overlay from package_config if present
                    targeting_overlay = None
                    if "targeting_overlay" in package_config and package_config["targeting_overlay"]:
                        from src.core.schemas import Targeting

                        targeting_overlay = Targeting(**package_config["targeting_overlay"])

                    # Create MediaPackage object (what adapters expect)
                    # Note: Product model has 'formats' not 'format_ids'
                    if not package_id:
                        error_msg = f"Package ID missing for package in media buy {media_buy_id}"
                        logger.error(f"[APPROVAL] {error_msg}")
                        return False, error_msg

                    # Extract delivery_type string value from enum (if it's an enum)
                    if hasattr(product.delivery_type, "value"):
                        # It's an enum - extract the string value
                        delivery_type_str = product.delivery_type.value
                    else:
                        # Already a string
                        delivery_type_str = str(product.delivery_type)

                    # Validate delivery_type is a valid literal
                    if delivery_type_str not in ["guaranteed", "non_guaranteed"]:
                        delivery_type_str = "non_guaranteed"  # Default fallback

                    # Convert formats to FormatId objects with comprehensive validation
                    from src.core.schemas import FormatId as FormatIdType
                    from src.core.schemas import FormatReference

                    format_ids_list: list[FormatIdType] = []
                    formats = product.format_ids or []

                    logger.debug(f"[APPROVAL] Converting {len(formats)} formats for package {package_id}")

                    for idx, fmt in enumerate(formats):
                        try:
                            # Most common case: dict from database (JSONB field returns dicts)
                            if isinstance(fmt, dict):
                                agent_url = fmt.get("agent_url")
                                format_id = fmt.get("format_id") or fmt.get("id")

                                # Validate required fields exist and are non-empty strings
                                if not agent_url or not isinstance(agent_url, str):
                                    raise ValueError(f"Format missing or invalid agent_url: agent_url={agent_url!r}")
                                if not validate_agent_url(agent_url):
                                    raise ValueError(f"agent_url must be valid HTTP(S) URL: {agent_url!r}")
                                if not format_id or not isinstance(format_id, str):
                                    raise ValueError(f"Format missing or invalid id: id={format_id!r}")

                                # Pydantic automatically converts string to AnyUrl per FormatId schema
                                format_ids_list.append(FormatIdType(agent_url=make_url(agent_url), id=format_id))

                            # Already correct type (no conversion needed)
                            elif isinstance(fmt, FormatIdType):
                                # Defensive validation even for FormatId objects
                                if not fmt.agent_url or not validate_agent_url(fmt.agent_url):
                                    raise ValueError(f"FormatId has invalid agent_url: {fmt.agent_url!r}")
                                if not fmt.id:
                                    raise ValueError(f"FormatId has empty id: {fmt.id!r}")
                                format_ids_list.append(fmt)

                            # Legacy FormatReference object (convert format_id -> id)
                            elif isinstance(fmt, FormatReference):
                                if not fmt.agent_url or not validate_agent_url(fmt.agent_url):
                                    raise ValueError(f"FormatReference has invalid agent_url: {fmt.agent_url!r}")
                                if not fmt.format_id:
                                    raise ValueError(f"FormatReference has empty format_id: {fmt.format_id!r}")
                                format_ids_list.append(FormatIdType(agent_url=fmt.agent_url, id=fmt.format_id))

                            else:
                                raise ValueError(f"Unknown format type: {type(fmt).__name__}")

                        except (ValueError, ValidationError) as e:
                            error_msg = (
                                f"Failed to reconstruct package {package_id}: "
                                f"Format validation failed at index {idx}: {e}"
                            )
                            logger.error(f"[APPROVAL] {error_msg}")
                            return False, error_msg

                    # Validate non-empty format_ids (required by AdCP spec)
                    if not format_ids_list:
                        error_msg = (
                            f"Failed to reconstruct package {package_id}: "
                            f"Product {product_id} has no valid formats - cannot create media buy"
                        )
                        logger.error(f"[APPROVAL] {error_msg}")
                        return False, error_msg

                    # Log conversion results
                    logger.info(
                        f"[APPROVAL] Package {package_id}: "
                        f"Successfully converted all {len(format_ids_list)} formats"
                    )

                    media_package = MediaPackage(
                        package_id=package_id,
                        name=package_config.get("name") or product.name,
                        delivery_type=delivery_type_str,  # type: ignore[arg-type]
                        cpm=cpm,
                        impressions=impressions,
                        format_ids=format_ids_list,  # type: ignore[arg-type]
                        targeting_overlay=targeting_overlay,
                        buyer_ref=package_config.get("buyer_ref"),
                        product_id=product_id,
                        budget=budget,
                    )
                    packages.append(media_package)

                    logger.info(
                        f"[APPROVAL] Reconstructed MediaPackage {package_id}: "
                        f"name={media_package.name}, cpm={cpm}, impressions={impressions}"
                    )

                except ValidationError as ve:
                    error_msg = f"Failed to reconstruct package {db_pkg.package_id}: {format_validation_error(ve)}"
                    logger.error(f"[APPROVAL] {error_msg}")
                    return False, error_msg
                except Exception as e:
                    error_msg = f"Failed to reconstruct package {db_pkg.package_id}: {str(e)}"
                    logger.error(f"[APPROVAL] {error_msg}")
                    return False, error_msg

            # Use start_time/end_time from media_buy (already resolved)
            start_time = media_buy.start_time
            end_time = media_buy.end_time

            # Get the Principal object (needed for adapter)
            from src.core.auth import get_principal_object

            principal = get_principal_object(media_buy.principal_id)
            if not principal:
                error_msg = f"Principal {media_buy.principal_id} not found"
                logger.error(f"[APPROVAL] {error_msg}")
                return False, error_msg

            # Create testing context (dry_run should be False for approved buys)
            testing_ctx = TestingContext(dry_run=False, test_session_id=None)

            logger.info(
                f"[APPROVAL] Calling adapter for {media_buy_id}: "
                f"{len(packages)} packages, start={start_time}, end={end_time}"
            )

        # PRE-VALIDATE: Check all creatives have required fields BEFORE calling adapter
        # This prevents GAM order creation when creatives are invalid (all-or-nothing approach)
        _validate_creatives_before_adapter_call(packages, tenant_id)  # type: ignore[arg-type]

        # Execute adapter creation (outside session to avoid conflicts)
        # Type ignore needed because we're passing MediaPackage list as Package list (adapter compatibility)
        response = _execute_adapter_media_buy_creation(
            request, packages, start_time, end_time, package_pricing_info, principal, testing_ctx  # type: ignore[arg-type]
        )

        # Check if adapter returned an error response
        if isinstance(response, CreateMediaBuyError):
            # Adapter returned error response (not an exception)
            error_messages = [str(err) for err in response.errors] if response.errors else ["Unknown error"]
            error_msg = "; ".join(error_messages)
            logger.error(f"[APPROVAL] Adapter creation failed for {media_buy_id}: {error_msg}")
            return False, error_msg

        logger.info(f"[APPROVAL] Adapter creation succeeded for {media_buy_id}: {response.media_buy_id}")

        # Upload and associate inline creatives if any exist
        # This handles inline creatives that were uploaded during initial media buy creation
        with get_db_session() as session:
            from src.core.database.models import Creative as CreativeModel
            from src.core.database.models import CreativeAssignment

            # Import adapter helper here (used for both creative upload and order approval)
            from src.core.helpers.adapter_helpers import get_adapter

            # Get all creative assignments for this media buy
            stmt_assignments = select(CreativeAssignment).filter_by(media_buy_id=media_buy_id)
            assignments = session.scalars(stmt_assignments).all()

            if assignments:
                logger.info(f"[APPROVAL] Found {len(assignments)} creative assignments, uploading to adapter")

                # Group packages by creative (REVERSED - key by creative_id, value is list of package_ids)
                # This ensures each creative is uploaded ONCE with ALL its package assignments
                packages_by_creative: dict[str, list[str]] = {}
                for assignment in assignments:
                    if assignment.creative_id not in packages_by_creative:
                        packages_by_creative[assignment.creative_id] = []
                    packages_by_creative[assignment.creative_id].append(assignment.package_id)

                # Load all creatives
                all_creative_ids = list(packages_by_creative.keys())
                stmt_creatives = select(CreativeModel).filter(CreativeModel.creative_id.in_(all_creative_ids))
                creatives = session.scalars(stmt_creatives).all()

                # Create creative map
                creative_map = {c.creative_id: c for c in creatives}

                # Build assets list for adapter and collect all validation errors
                assets = []
                all_validation_errors = []
                for creative_id, package_ids in packages_by_creative.items():
                    creative = creative_map.get(creative_id)
                    if not creative:
                        logger.warning(f"[APPROVAL] Creative {creative_id} not found in database")
                        continue

                    # Convert Creative model to asset dict format expected by adapter
                    # The Creative model stores all content in the 'data' JSON field
                    creative_data = creative.data or {}

                    # Get format spec for proper extraction
                    from src.core.format_resolver import get_format

                    format_spec = None
                    try:
                        format_spec = get_format(
                            str(creative.format), agent_url=creative.agent_url, tenant_id=tenant_id, product_id=None
                        )
                    except (ValueError, Exception) as e:
                        logger.warning(
                            f"[APPROVAL] Could not load format spec for creative {creative_id} "
                            f"(format={creative.format}): {e}"
                        )

                    # Extract URL and dimensions using shared helper
                    url, width, height = _extract_creative_url_and_dimensions(creative_data, format_spec)

                    asset = {
                        "creative_id": creative.creative_id,
                        "package_assignments": package_ids,  # Array of ALL package IDs this creative is assigned to
                        "width": width,
                        "height": height,
                        "url": url,
                        "asset_type": creative_data.get("asset_type", "image"),
                        "name": creative.name or f"Creative {creative.creative_id}",
                    }

                    # GAM requires width, height, and url for creative upload
                    # Validate required fields and accumulate all errors
                    if not asset["width"] or not asset["height"]:
                        error = f"Creative {creative_id} missing dimensions (width={asset['width']}, height={asset['height']}, format={creative.format})"
                        all_validation_errors.append(error)
                        logger.error(f"[APPROVAL] {error}")
                    if not asset["url"]:
                        error = f"Creative {creative_id} missing required URL field"
                        all_validation_errors.append(error)
                        logger.error(f"[APPROVAL] {error}")

                    # Skip invalid creatives but continue checking others
                    if any(err.startswith(f"Creative {creative_id}") for err in all_validation_errors):
                        continue

                    assets.append(asset)

                # If we found validation errors, fail with complete error list
                if all_validation_errors:
                    error_msg = (
                        f"Cannot approve media buy: {len(all_validation_errors)} creatives have validation errors:\n"
                        + "\n".join(f"  • {err}" for err in all_validation_errors)
                        + "\n\nAll creatives must have dimensions (width/height) and a content URL."
                    )
                    logger.error(f"[APPROVAL] {error_msg}")
                    return False, error_msg

                if assets:
                    logger.info(f"[APPROVAL] Uploading {len(assets)} creatives to adapter")

                    # Get adapter and upload creatives
                    adapter = get_adapter(principal, dry_run=False, testing_context=testing_ctx)

                    # Call adapter's add_creative_assets method
                    # For GAM, the media_buy_id is the GAM order ID
                    # At this point, we know response is CreateMediaBuySuccess (checked above)
                    gam_order_id: str = response.media_buy_id if response.media_buy_id else ""

                    try:
                        if hasattr(adapter, "creatives_manager") and adapter.creatives_manager and gam_order_id:
                            asset_statuses = adapter.creatives_manager.add_creative_assets(
                                gam_order_id, assets, datetime.now(UTC)
                            )
                            logger.info(f"[APPROVAL] Creative upload completed: {len(asset_statuses)} assets processed")

                            # Log any failures
                            for status in asset_statuses:
                                if status.status == "failed":
                                    logger.error(
                                        f"[APPROVAL] Failed to upload creative {status.creative_id}: {status.message}"
                                    )
                        else:
                            logger.warning("[APPROVAL] Adapter does not support creative upload, skipping")
                    except Exception as creative_error:
                        # Creative upload failed - this is critical for GAM orders
                        error_msg = f"Failed to upload creatives to adapter: {str(creative_error)}"
                        logger.error(f"[APPROVAL] {error_msg}", exc_info=True)
                        return False, error_msg
            else:
                logger.info(f"[APPROVAL] No creative assignments found for {media_buy_id}, skipping creative upload")

        # After creatives are uploaded (or skipped), retry order approval
        # This is necessary because:
        # 1. GAM may still be processing inventory forecasts (NO_FORECAST_YET error)
        # 2. Creatives may have been uploaded after the initial approval attempt
        logger.info(f"[APPROVAL] Attempting to approve order {response.media_buy_id} in GAM")
        try:
            adapter = get_adapter(principal, dry_run=False, testing_context=testing_ctx)
            if hasattr(adapter, "orders_manager") and adapter.orders_manager:
                approval_success = adapter.orders_manager.approve_order(response.media_buy_id)
                if approval_success:
                    logger.info(f"[APPROVAL] Successfully approved GAM order {response.media_buy_id}")
                else:
                    # GAM approval failed - return failure so status can be updated
                    error_msg = (
                        f"Failed to approve order {response.media_buy_id}, "
                        f"it will remain in DRAFT status. This may be due to missing creatives or "
                        f"GAM still processing inventory forecasts."
                    )
                    logger.warning(f"[APPROVAL] {error_msg}")
                    return False, error_msg
            else:
                logger.info("[APPROVAL] Adapter does not support order approval, skipping")
        except Exception as approval_error:
            # Approval exception - return failure
            error_msg = f"Failed to approve order {response.media_buy_id}: {str(approval_error)}"
            logger.error(f"[APPROVAL] {error_msg}", exc_info=True)
            return False, error_msg

        return True, None

    except Exception as e:
        import traceback

        error_traceback = traceback.format_exc()
        error_msg = f"Adapter creation failed: {str(e)}"
        logger.error(f"[APPROVAL] {error_msg}\n{error_traceback}")
        return False, error_msg


def _validate_pricing_model_selection(
    package: Package | PackageRequest,
    product: Any,  # ProductModel from database
    campaign_currency: str | None,
) -> dict[str, Any]:
    """Validate pricing model selection for a package against product's pricing options.

    Args:
        package: Package with optional pricing_model and bid_price
        product: Product database model with pricing_options relationship
        campaign_currency: Optional campaign-level currency

    Returns:
        Dict with validated pricing information:
        {
            "pricing_model": str,
            "rate": float | None,
            "currency": str,
            "is_fixed": bool,
            "bid_price": float | None,
        }

    Raises:
        ToolError: If pricing_model validation fails
    """
    from decimal import Decimal

    # Log pricing validation details at debug level
    logger.debug(
        f"[PRICING] Package {package.product_id}: pricing_option={package.pricing_option_id}, "
        f"model={package.pricing_model}, bid_price={package.bid_price}, budget={package.budget}"
    )

    # All products must have pricing_options
    if not product.pricing_options or len(product.pricing_options) == 0:
        raise ToolError(
            "PRICING_ERROR",
            f"Product {product.product_id} has no pricing_options configured. This is a data integrity error.",
        )

    # Determine which pricing option to use
    # Priority: pricing_option_id (AdCP spec) > pricing_model (legacy)
    pricing_option_id = package.pricing_option_id
    pricing_model_fallback = package.pricing_model  # Legacy field

    # If neither specified, use first pricing option from product
    if not pricing_option_id and not pricing_model_fallback:
        first_option = product.pricing_options[0]
        return {
            "pricing_model": first_option.pricing_model,
            "rate": float(first_option.rate) if first_option.rate else None,
            "currency": first_option.currency or campaign_currency or "USD",
            "is_fixed": first_option.is_fixed,
            "bid_price": float(package.bid_price) if package.bid_price else None,
        }

    # Find matching pricing option
    selected_option = None
    for option in product.pricing_options:
        # Construct pricing_option_id in same format as get_products returns
        # Format: {pricing_model}_{currency}_{fixed|auction}
        fixed_str = "fixed" if option.is_fixed else "auction"
        option_id = f"{option.pricing_model}_{option.currency.lower()}_{fixed_str}"

        # Try matching by pricing_option_id first (AdCP spec)
        if pricing_option_id and pricing_option_id.lower() == option_id.lower():
            selected_option = option
            break

        # Fallback: match by pricing_model (legacy)
        if pricing_model_fallback and option.pricing_model == pricing_model_fallback.value:
            # If campaign currency specified, must match
            if campaign_currency and option.currency != campaign_currency:
                continue
            selected_option = option
            break

    if not selected_option:
        # Show available options in same format as matching logic expects
        available_options = [
            f"{opt.pricing_model}_{opt.currency.lower()}_{'fixed' if opt.is_fixed else 'auction'} ({opt.pricing_model} - {opt.currency})"
            for opt in product.pricing_options
        ]
        error_msg = f"Product {product.product_id} does not offer "
        if pricing_option_id:
            error_msg += f"pricing_option_id '{pricing_option_id}'"
        elif pricing_model_fallback:
            error_msg += f"pricing model '{pricing_model_fallback}'"
            if campaign_currency:
                error_msg += f" in currency {campaign_currency}"
        error_msg += f". Available options: {', '.join(available_options)}"
        raise ToolError("PRICING_ERROR", error_msg)

    # Validate auction pricing
    if not selected_option.is_fixed:
        if not package.bid_price:
            raise ToolError(
                "PRICING_ERROR",
                f"Package requires bid_price for auction-based {package.pricing_model} pricing. "
                f"Floor price: {selected_option.price_guidance.get('floor') if selected_option.price_guidance else 'N/A'}",
            )

        floor_price = (
            Decimal(str(selected_option.price_guidance.get("floor", 0)))
            if selected_option.price_guidance
            else Decimal("0")
        )
        bid_decimal = Decimal(str(package.bid_price))

        if bid_decimal < floor_price:
            raise ToolError(
                "PRICING_ERROR",
                f"Bid price {package.bid_price} is below floor price {floor_price} for {package.pricing_model} pricing",
            )

    # Validate fixed pricing has rate
    if selected_option.is_fixed and not selected_option.rate:
        raise ToolError(
            "PRICING_ERROR",
            f"Product {product.product_id} pricing option has is_fixed=true but no rate specified",
        )

    # Validate minimum spend per package
    if selected_option.min_spend_per_package:
        package_budget = None
        if package.budget is not None:
            # Package.budget is now always float | None (per AdCP spec)
            package_budget = Decimal(str(package.budget))

        if package_budget and package_budget < Decimal(str(selected_option.min_spend_per_package)):
            raise ToolError(
                "PRICING_ERROR",
                f"Package budget {package_budget} {selected_option.currency} is below minimum spend "
                f"{selected_option.min_spend_per_package} {selected_option.currency} for {package.pricing_model}",
            )

    # Return validated pricing information
    return {
        "pricing_model": selected_option.pricing_model,
        "rate": float(selected_option.rate) if selected_option.rate else None,
        "currency": selected_option.currency,
        "is_fixed": selected_option.is_fixed,
        "bid_price": float(package.bid_price) if package.bid_price else None,
    }


async def _validate_and_convert_format_ids(
    format_ids: list[Any], tenant_id: str, package_idx: int
) -> list[dict[str, str]]:
    """Validate and convert format_ids to FormatId objects with strict enforcement.

    Per AdCP spec, format_ids must be FormatId objects with {agent_url, id}.
    This function enforces:
    1. Only FormatId objects are accepted (no plain strings)
    2. agent_url must be a registered creative agent (default or tenant-specific)
    3. format_id must exist on the specified agent
    4. Format must pass validation (dimensions, asset requirements, etc.)

    Args:
        format_ids: List of format ID objects from request
        tenant_id: Tenant ID for looking up registered agents
        package_idx: Package index for error messages (0-based)

    Returns:
        List of validated FormatId dicts with {agent_url, id}

    Raises:
        ToolError: If any format_id is invalid, unregistered, or doesn't exist
    """
    from src.core.creative_agent_registry import CreativeAgentRegistry

    if not format_ids:
        return []

    registry = CreativeAgentRegistry()
    validated_format_ids = []

    # Get registered agents for this tenant
    registered_agents = registry._get_tenant_agents(tenant_id)
    # Normalize agent URLs for consistent comparison (strips /mcp, /a2a, /.well-known/*, trailing slashes)
    # This ensures all URL variations match: "https://example.com/mcp/" -> "https://example.com"
    from src.core.validation import normalize_agent_url

    registered_agent_urls = {normalize_agent_url(agent.agent_url) for agent in registered_agents}

    for idx, fmt_id in enumerate(format_ids):
        # STRICT ENFORCEMENT: Reject plain strings
        if isinstance(fmt_id, str):
            raise ToolError(
                "FORMAT_VALIDATION_ERROR",
                f"Package {package_idx + 1}, format_ids[{idx}]: Plain string format IDs are not supported. "
                f"Per AdCP spec, format_ids must be FormatId objects with {{agent_url, id}}. "
                f'Example: {{"agent_url": "https://creative.adcontextprotocol.org", "id": "{fmt_id}"}}. '
                f"Use list_creative_formats to discover available formats.",
            )

        # Extract agent_url and id from dict/object
        if isinstance(fmt_id, dict):
            agent_url = fmt_id.get("agent_url")
            format_id = fmt_id.get("id")
        elif hasattr(fmt_id, "agent_url") and hasattr(fmt_id, "id"):
            agent_url = fmt_id.agent_url
            format_id = fmt_id.id
        else:
            raise ToolError(
                "FORMAT_VALIDATION_ERROR",
                f"Package {package_idx + 1}, format_ids[{idx}]: Invalid format_id structure. "
                f"Expected FormatId object with {{agent_url, id}}, got: {type(fmt_id).__name__}",
            )

        if not agent_url or not format_id:
            raise ToolError(
                "FORMAT_VALIDATION_ERROR",
                f"Package {package_idx + 1}, format_ids[{idx}]: FormatId object missing required fields. "
                f"Both agent_url and id are required. Got: agent_url={agent_url!r}, id={format_id!r}",
            )

        # VALIDATION: Check agent is registered
        # Normalize incoming agent_url for comparison (strips /mcp, /a2a, /.well-known/*, trailing slashes)
        normalized_agent_url = normalize_agent_url(agent_url)
        if normalized_agent_url not in registered_agent_urls:
            raise ToolError(
                "FORMAT_VALIDATION_ERROR",
                f"Package {package_idx + 1}, format_ids[{idx}]: Creative agent not registered: {agent_url}. "
                f"Registered agents: {', '.join(sorted(registered_agent_urls))}. "
                f"Contact your administrator to register this creative agent.",
            )

        # VALIDATION: Verify format exists on agent
        try:
            format_obj = await registry.get_format(agent_url, format_id)
            if not format_obj:
                raise ToolError(
                    "FORMAT_VALIDATION_ERROR",
                    f"Package {package_idx + 1}, format_ids[{idx}]: Format not found on agent. "
                    f"agent_url={agent_url}, format_id={format_id!r}. "
                    f"Use list_creative_formats to discover available formats.",
                )
        except Exception as e:
            if isinstance(e, ToolError):
                raise
            logger.exception(f"Error fetching format {format_id} from {agent_url}: {e}")
            raise ToolError(
                "FORMAT_VALIDATION_ERROR",
                f"Package {package_idx + 1}, format_ids[{idx}]: Failed to verify format on agent. "
                f"agent_url={agent_url}, format_id={format_id!r}. Error: {e}",
            )

        # Format validated - add to results
        validated_format_ids.append({"agent_url": agent_url, "id": format_id})

    return validated_format_ids


from src.services.setup_checklist_service import SetupIncompleteError, validate_setup_complete
from src.services.slack_notifier import get_slack_notifier


async def _create_media_buy_impl(
    buyer_ref: str,
    brand_manifest: Any,  # BrandManifest | str - REQUIRED per AdCP v2.2.0 spec
    packages: list[Any],  # REQUIRED per AdCP spec
    start_time: Any,  # datetime | Literal["asap"] | str - REQUIRED per AdCP spec
    end_time: Any,  # datetime | str - REQUIRED per AdCP spec
    budget: Any | None = None,  # DEPRECATED: Budget is package-level only per AdCP v2.2.0 (ignored)
    po_number: str | None = None,
    product_ids: list[str] | None = None,  # Legacy format conversion
    start_date: Any | None = None,  # Legacy format conversion
    end_date: Any | None = None,  # Legacy format conversion
    total_budget: float | None = None,  # Legacy format conversion
    targeting_overlay: Targeting | None = None,
    pacing: Literal["even", "asap", "daily_budget"] = "even",
    daily_budget: float | None = None,
    creatives: list[Any] | None = None,
    reporting_webhook: dict[str, Any] | None = None,
    required_axe_signals: list[str] | None = None,
    enable_creative_macro: bool = False,
    strategy_id: str | None = None,
    push_notification_config: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,  # Optional application level context per adcp spec
    ctx: Context | ToolContext | None = None,
) -> CreateMediaBuyResponse:
    """Create a media buy with the specified parameters.

    Args:
        buyer_ref: Buyer reference for tracking (REQUIRED per AdCP spec)
        brand_manifest: Brand information manifest - inline object or URL string (REQUIRED per AdCP v2.2.0 spec)
        packages: Array of packages with products and budgets (REQUIRED)
        start_time: Campaign start time ISO 8601 or 'asap' (REQUIRED)
        end_time: Campaign end time ISO 8601 (REQUIRED)
        budget: DEPRECATED - Budget is at package level only per AdCP v2.2.0 (ignored if provided)
        po_number: Purchase order number (optional)
        product_ids: Legacy: Product IDs (converted to packages)
        start_date: Legacy: Start date (converted to start_time)
        end_date: Legacy: End date (converted to end_time)
        total_budget: Legacy: Total budget (converted to Budget object)
        targeting_overlay: Targeting overlay configuration
        pacing: Pacing strategy (even, asap, daily_budget)
        daily_budget: Daily budget limit
        creatives: Creative assets for the campaign
        reporting_webhook: Webhook configuration for automated reporting delivery
        required_axe_signals: Required targeting signals
        enable_creative_macro: Enable AXE to provide creative_macro signal
        strategy_id: Optional strategy ID for linking operations
        push_notification_config: Push notification config for status updates (MCP/A2A)
        context: Application level context per adcp spec
        ctx:  FastMCP context (automatically provided) (automatically provided)

    Returns:
        CreateMediaBuyResponse with media buy details
    """
    request_start_time = time.time()

    # Create request object from individual parameters (MCP-compliant)
    # Validate early with helpful error messages

    try:
        req = CreateMediaBuyRequest(
            buyer_ref=buyer_ref,
            brand_manifest=brand_manifest,
            campaign_name=None,  # Optional display name
            po_number=po_number,
            packages=packages,
            start_time=start_time,
            end_time=end_time,
            budget=None,  # Budget is package-level, but field exists in schema
            currency=None,  # Derived from product pricing_options
            product_ids=product_ids,
            start_date=start_date,
            end_date=end_date,
            total_budget=total_budget,
            targeting_overlay=targeting_overlay,
            pacing=pacing,
            daily_budget=daily_budget,
            creatives=creatives,
            reporting_webhook=reporting_webhook,
            required_axe_signals=required_axe_signals,
            enable_creative_macro=enable_creative_macro,
            strategy_id=strategy_id,
            webhook_url=None,  # Internal field, not in AdCP spec
            webhook_auth_token=None,  # Internal field, not in AdCP spec
            push_notification_config=push_notification_config,
            context=context,
        )
    except ValidationError as e:
        # Format validation errors with helpful context using shared helper
        raise ToolError(format_validation_error(e, context="request")) from e

    # Extract testing context first
    if ctx is None:
        raise ToolError("Context is required")

    testing_ctx = get_testing_context(ctx)

    # Authentication and tenant setup
    principal_id = get_principal_id_from_context(ctx)
    if principal_id is None:
        raise ToolError("Principal ID not found in context - authentication required")

    tenant = get_current_tenant()

    # Validate setup completion (only in production, skip for testing)
    if not testing_ctx.dry_run and not testing_ctx.test_session_id:
        try:
            validate_setup_complete(tenant["tenant_id"])
        except SetupIncompleteError as e:
            # Return helpful error with missing tasks
            task_list = "\n".join(f"  - {task['name']}: {task['description']}" for task in e.missing_tasks)
            error_msg = (
                f"Setup incomplete. Please complete the following required tasks:\n\n{task_list}\n\n"
                f"Visit the setup checklist at /tenant/{tenant['tenant_id']}/setup-checklist for details."
            )
            raise ToolError(error_msg)

    # Validate principal exists BEFORE creating context (foreign key constraint)
    principal = get_principal_object(principal_id)
    if not principal:
        error_msg = f"Principal {principal_id} not found"
        # Cannot create context or workflow step without valid principal
        return CreateMediaBuyError(  # type: ignore[return-value]
            errors=[Error(code="authentication_error", message=error_msg, details=None)],
            context=req.context,
        )

    # Context management and workflow step creation - create workflow step FIRST
    ctx_manager = get_context_manager()
    ctx_id = ctx.headers.get("x-context-id") if ctx and hasattr(ctx, "headers") else None
    persistent_ctx = None
    step = None

    # Create workflow step immediately for tracking all operations
    if not persistent_ctx:
        # Check if we have an existing context ID
        if ctx_id:
            persistent_ctx = ctx_manager.get_context(ctx_id)

        # Create new context if needed (principal already validated above)
        if not persistent_ctx:
            persistent_ctx = ctx_manager.create_context(tenant_id=tenant["tenant_id"], principal_id=principal_id)

    # Create workflow step for tracking this operation
    step = ctx_manager.create_workflow_step(
        context_id=persistent_ctx.context_id,
        step_type="media_buy_creation",
        owner="system",
        status="in_progress",
        tool_name="create_media_buy",
        request_data=req.model_dump(mode="json"),
    )

    # Register push notification config if provided (MCP/A2A protocol support)
    if push_notification_config:
        from src.core.database.database_session import get_db_session
        from src.core.database.models import PushNotificationConfig as DBPushNotificationConfig

        logger.info(f"[MCP/A2A] Registering push notification config from request: {push_notification_config}")

        # Extract config details
        url = push_notification_config.get("url")
        authentication = push_notification_config.get("authentication", {})

        if url:
            # Extract authentication details (A2A format: schemes + credentials)
            schemes = authentication.get("schemes", []) if authentication else []
            auth_type = schemes[0] if schemes else None
            credentials = authentication.get("credentials") if authentication else None

            # Generate config ID
            config_id = push_notification_config.get("id") or f"pnc_{uuid.uuid4().hex[:16]}"

            # Save to database
            with get_db_session() as db:
                # Check if config already exists
                from sqlalchemy import select

                stmt = select(DBPushNotificationConfig).filter_by(
                    id=config_id, tenant_id=tenant["tenant_id"], principal_id=principal_id
                )
                existing_config = db.scalars(stmt).first()

                if existing_config:
                    # Update existing
                    existing_config.url = url
                    existing_config.authentication_type = auth_type
                    existing_config.authentication_token = credentials
                    # updated_at automatically updated via onupdate=func.now()
                    existing_config.is_active = True
                else:
                    # Create new
                    new_config = DBPushNotificationConfig(
                        id=config_id,
                        tenant_id=tenant["tenant_id"],
                        principal_id=principal_id,
                        url=url,
                        authentication_type=auth_type,
                        authentication_token=credentials,
                        is_active=True,
                    )
                    db.add(new_config)

                db.commit()
                logger.info(
                    f"[MCP/A2A] Push notification config {'updated' if existing_config else 'created'}: {config_id}"
                )

    try:
        # Validate input parameters
        # 1. Budget validation
        total_budget = req.get_total_budget()
        if total_budget <= 0:
            error_msg = f"Invalid budget: {total_budget}. Budget must be positive."
            raise ValueError(error_msg)

        # 2. DateTime validation
        now = datetime.now(UTC)  # noqa: F823

        # Validate start_time
        if req.start_time is None:
            error_msg = "start_time is required"
            raise ValueError(error_msg)

        # Handle 'asap' start_time (AdCP v1.7.0)
        if req.start_time == "asap":
            computed_start_time: datetime = now
        else:
            # Ensure start_time is timezone-aware for comparison
            # At this point, req.start_time is guaranteed to be datetime (not str)
            assert isinstance(req.start_time, datetime), "start_time must be datetime when not 'asap'"
            computed_start_time = req.start_time
            if computed_start_time.tzinfo is None:
                computed_start_time = computed_start_time.replace(tzinfo=UTC)

            if computed_start_time < now:
                error_msg = f"Invalid start time: {req.start_time}. Start time cannot be in the past."
                raise ValueError(error_msg)

        # Validate end_time
        if req.end_time is None:
            error_msg = "end_time is required"
            raise ValueError(error_msg)

        # Ensure end_time is timezone-aware for comparison
        computed_end_time: datetime = req.end_time
        if computed_end_time.tzinfo is None:
            computed_end_time = computed_end_time.replace(tzinfo=UTC)

        if computed_end_time <= computed_start_time:
            error_msg = f"Invalid time range: end time ({req.end_time}) must be after start time ({req.start_time})."
            raise ValueError(error_msg)

        # Assign computed times to local variables for use throughout the function
        start_time_val = computed_start_time
        end_time_val = computed_end_time

        # Update function parameters to use validated datetime objects
        # This ensures adapters receive datetime objects, not strings
        start_time = start_time_val
        end_time = end_time_val

        # 3. Package/Product validation
        product_ids = req.get_product_ids()
        logger.info(f"DEBUG: Extracted product_ids: {product_ids}")
        logger.info(
            f"DEBUG: Request packages: {[{'product_id': p.product_id, 'buyer_ref': p.buyer_ref, 'bid_price': p.bid_price, 'pricing_option_id': p.pricing_option_id} for p in (req.packages or [])]}"
        )
        if not product_ids:
            error_msg = "At least one product is required."
            raise ValueError(error_msg)

        if req.packages:
            for package in req.packages:
                # Check product_id field per AdCP spec
                if not package.product_id:
                    error_msg = f"Package {package.buyer_ref} must specify product_id."
                    raise ValueError(error_msg)

            # Check for duplicate product_ids across packages
            product_id_counts: dict[str, int] = {}
            for package in req.packages:
                if package.product_id:
                    product_id_counts[package.product_id] = product_id_counts.get(package.product_id, 0) + 1

            duplicate_products = [pid for pid, count in product_id_counts.items() if count > 1]
            if duplicate_products:
                error_msg = f"Duplicate product_id(s) found in packages: {', '.join(duplicate_products)}. Each product can only be used once per media buy."
                raise ValueError(error_msg)

        # 4. Currency-specific budget validation
        from decimal import Decimal

        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import CurrencyLimit
        from src.core.database.models import Product as ProductModel

        # Get products first to determine currency from pricing options
        with get_db_session() as session:
            # Get products from database
            from sqlalchemy.orm import selectinload

            products_stmt = (
                select(ProductModel)
                .where(ProductModel.tenant_id == tenant["tenant_id"], ProductModel.product_id.in_(product_ids))
                .options(selectinload(ProductModel.pricing_options))
            )
            products = session.scalars(products_stmt).all()

            # Build product lookup map
            product_map = {p.product_id: p for p in products}

            # Resolve legacy pricing_option_id values to actual product pricing_option_ids
            # This happens when using the legacy product_ids parameter (auto-converted to packages)
            for package in req.packages:
                if package.pricing_option_id == "legacy_conversion" and package.product_id in product_map:
                    product = product_map[package.product_id]
                    # Use the first pricing option from the product
                    if product.pricing_options and len(product.pricing_options) > 0:
                        # Use the generated pricing_option_id format from the product's first option
                        first_option = product.pricing_options[0]
                        pricing_model = first_option.pricing_model.lower()
                        currency = first_option.currency.lower()
                        is_fixed = "fixed" if first_option.is_fixed else "auction"
                        package.pricing_option_id = f"{pricing_model}_{currency}_{is_fixed}"
                        logger.info(
                            f"Resolved legacy pricing_option_id for product {package.product_id}: {package.pricing_option_id}"
                        )

            # Get currency from product pricing options (per AdCP spec)
            request_currency = None

            # First, try to get currency from first package's pricing option
            if req.packages and len(req.packages) > 0:
                first_package = req.packages[0]
                package_product_ids = [first_package.product_id] if first_package.product_id else []

                if package_product_ids and package_product_ids[0] in product_map:
                    product = product_map[package_product_ids[0]]
                    pricing_options = product.pricing_options or []

                    # Find the pricing option matching the package's pricing_model
                    if first_package.pricing_model and pricing_options:
                        matching_option = next(
                            (po for po in pricing_options if po.pricing_model == first_package.pricing_model), None
                        )
                        if matching_option:
                            request_currency = matching_option.currency

                    # If no pricing_model specified, use first pricing option's currency
                    if not request_currency and pricing_options:
                        request_currency = pricing_options[0].currency

            # Fallback to deprecated/legacy sources
            if not request_currency and req.currency:
                # Deprecated field, but still supported for backward compatibility
                request_currency = req.currency
            elif not request_currency and req.budget:
                # Legacy: Extract currency from Budget object (if it's an object)
                if hasattr(req.budget, "currency"):
                    request_currency = req.budget.currency
            elif not request_currency and req.packages and req.packages[0].budget:
                # Legacy: Extract currency from package budget object (if it's an object)
                if hasattr(req.packages[0].budget, "currency"):
                    request_currency = req.packages[0].budget.currency

            # Final fallback
            if not request_currency:
                request_currency = "USD"

            # Get currency limits for this tenant and currency
            currency_stmt = select(CurrencyLimit).where(
                CurrencyLimit.tenant_id == tenant["tenant_id"], CurrencyLimit.currency_code == request_currency
            )
            currency_limit = session.scalars(currency_stmt).first()

            # Check if tenant supports this currency
            if not currency_limit:
                error_msg = (
                    f"Currency {request_currency} is not supported by this publisher. "
                    f"Contact the publisher to add support for this currency."
                )
                raise ValueError(error_msg)

            # NEW: Validate pricing_model selections (AdCP PR #88)
            # Store validated pricing info for later use in adapter
            # Use index-based keys since package IDs aren't generated yet
            package_pricing_info_by_index = {}
            if req.packages:
                for idx, package in enumerate(req.packages):
                    # Get product ID for this package (AdCP spec: single product per package)
                    # Get product_id from package (AdCP spec has product_id field)
                    package_product_ids = [package.product_id] if package.product_id else []

                    # Validate pricing for the product
                    if package_product_ids:
                        product_id = package_product_ids[0]
                        if product_id in product_map:
                            try:
                                pricing_info = _validate_pricing_model_selection(
                                    package=package,
                                    product=product_map[product_id],
                                    campaign_currency=request_currency,
                                )
                                # Store by index (package IDs aren't generated yet)
                                package_pricing_info_by_index[idx] = pricing_info
                            except ToolError as e:
                                # Re-raise pricing validation errors
                                raise ValueError(str(e))

            # Validate minimum product spend (legacy + new pricing_options)
            if currency_limit.min_package_budget:
                # Build map of product_id -> minimum spend
                product_min_spends = {}
                for product in products:
                    # Use product pricing_options min_spend if set, otherwise use currency limit minimum
                    min_spend = currency_limit.min_package_budget
                    if product.pricing_options and len(product.pricing_options) > 0:
                        # Find pricing option matching the request currency (not just first option)
                        matching_option = next(
                            (po for po in product.pricing_options if po.currency == request_currency), None
                        )
                        if matching_option and matching_option.min_spend_per_package is not None:
                            min_spend = matching_option.min_spend_per_package
                    if min_spend is not None:
                        product_min_spends[product.product_id] = Decimal(str(min_spend))

                # Validate budget against minimum spend requirements
                if product_min_spends:
                    # Check if we're in legacy mode (packages without budgets)
                    is_legacy_mode = req.packages and all(not pkg.budget for pkg in req.packages)

                    # For packages with budgets, validate each package's budget
                    if req.packages and not is_legacy_mode:
                        for package in req.packages:
                            # Skip packages without budgets (shouldn't happen in v2.4 format)
                            if not package.budget:
                                continue

                            # Package.budget is now always float | None (per AdCP spec)
                            package_budget = Decimal(str(package.budget))

                            # Package currency is always request_currency (single currency per media buy)
                            package_currency = request_currency

                            # Get the product for this package
                            package_product_ids = [package.product_id] if package.product_id else []

                            if not package_product_ids:
                                continue

                            # Look up minimum spend for this package's currency
                            for product_id in package_product_ids:
                                if product_id not in product_map:
                                    continue

                                product = product_map[product_id]

                                # Find minimum spend for this package's currency
                                package_min_spend: Decimal | None = None

                                # First check if product has pricing option for this currency
                                if product.pricing_options:
                                    matching_option = next(
                                        (po for po in product.pricing_options if po.currency == package_currency), None
                                    )
                                    if matching_option and matching_option.min_spend_per_package is not None:
                                        package_min_spend = Decimal(str(matching_option.min_spend_per_package))

                                # If no product override, check currency limit
                                if package_min_spend is None:
                                    # Use the already-fetched currency_limit
                                    if currency_limit.min_package_budget:
                                        package_min_spend = Decimal(str(currency_limit.min_package_budget))

                                # Validate if minimum spend is set
                                if package_min_spend and package_budget < package_min_spend:
                                    error_msg = (
                                        f"Package budget ({package_budget} {package_currency}) does not meet minimum spend requirement "
                                        f"({package_min_spend} {package_currency}) for products in this package"
                                    )
                                    raise ValueError(error_msg)
                    else:
                        # Legacy mode: single total_budget for all products
                        applicable_min_spends = list(product_min_spends.values())
                        if applicable_min_spends:
                            required_min_spend = max(applicable_min_spends)
                            budget_decimal = Decimal(str(total_budget))

                            if budget_decimal < required_min_spend:
                                error_msg = (
                                    f"Total budget ({total_budget} {request_currency}) does not meet minimum spend requirement "
                                    f"({required_min_spend} {request_currency}) for the selected products"
                                )
                                raise ValueError(error_msg)

            # Validate maximum daily spend per package (if set)
            # This is per-package to prevent buyers from splitting large budgets across many packages
            if currency_limit.max_daily_package_spend:
                flight_days = (end_time_val - start_time_val).days
                if flight_days <= 0:
                    flight_days = 1

                # Check if we're in legacy mode (packages without budgets)
                is_legacy_mode = req.packages and all(not pkg.budget for pkg in req.packages)

                # For packages with budgets, validate each package's daily budget
                if req.packages and not is_legacy_mode:
                    for package in req.packages:
                        if not package.budget:
                            continue
                        # Package.budget is now always float | None (per AdCP spec)
                        package_budget = Decimal(str(package.budget))
                        package_daily_budget = package_budget / Decimal(str(flight_days))

                        if package_daily_budget > currency_limit.max_daily_package_spend:
                            error_msg = (
                                f"Package daily budget ({package_daily_budget} {request_currency}) exceeds "
                                f"maximum daily spend per package ({currency_limit.max_daily_package_spend} {request_currency}). "
                                f"This protects against accidental large budgets and prevents GAM line item proliferation."
                            )
                            raise ValueError(error_msg)
                else:
                    # Legacy mode: validate total budget
                    legacy_daily_budget: Decimal = Decimal(str(total_budget)) / Decimal(str(flight_days))
                    max_daily_spend: Decimal = Decimal(str(currency_limit.max_daily_package_spend))

                    if legacy_daily_budget > max_daily_spend:
                        error_msg = (
                            f"Daily budget ({legacy_daily_budget} {request_currency}) exceeds maximum daily spend "
                            f"({currency_limit.max_daily_package_spend} {request_currency}). "
                            f"This protects against accidental large budgets."
                        )
                        raise ValueError(error_msg)

        # Validate targeting doesn't use managed-only dimensions
        if req.targeting_overlay:
            from src.services.targeting_capabilities import validate_overlay_targeting

            violations = validate_overlay_targeting(req.targeting_overlay.model_dump(exclude_none=True))
            if violations:
                error_msg = f"Targeting validation failed: {'; '.join(violations)}"
                raise ValueError(error_msg)

    except (ValueError, PermissionError) as e:
        # Update workflow step as failed
        ctx_manager.update_workflow_step(step.step_id, status="failed", error_message=str(e))

        # Return error response (protocol layer will add status="failed")
        return CreateMediaBuyError(  # type: ignore[return-value]
            errors=[Error(code="validation_error", message=str(e), details=None)],
            context=req.context,
        )

    # Principal already validated earlier (before context creation) to avoid foreign key errors

    try:
        # Handle incoming creatives array in packages (upload to library and get IDs)
        # This MUST happen BEFORE checking manual approval so creatives are uploaded for BOTH manual and auto flows
        logger.info(f"[INLINE_CREATIVE_DEBUG] req.packages exists: {req.packages is not None}")
        if req.packages:
            logger.info(f"[INLINE_CREATIVE_DEBUG] req.packages count: {len(req.packages)}")
            for idx, pkg in enumerate(req.packages):
                logger.info(
                    f"[INLINE_CREATIVE_DEBUG] Package {idx}: product_id={pkg.product_id}, has_creatives={hasattr(pkg, 'creatives')}, creatives_count={len(pkg.creatives) if hasattr(pkg, 'creatives') and pkg.creatives else 0}"
                )
            try:
                logger.info("[INLINE_CREATIVE_DEBUG] Calling process_and_upload_package_creatives")
                updated_packages, uploaded_ids = process_and_upload_package_creatives(
                    packages=req.packages,
                    context=ctx,
                    testing_ctx=testing_ctx,
                )
                # Replace packages with updated versions (functional approach)
                req.packages = updated_packages
                logger.info("[INLINE_CREATIVE_DEBUG] Updated req.packages with creative_ids")
                if uploaded_ids:
                    logger.info(f"Successfully uploaded creatives for {len(uploaded_ids)} packages: {uploaded_ids}")
            except ToolError as e:
                # Update workflow step on failure
                ctx_manager.update_workflow_step(step.step_id, status="failed", error_message=str(e))
                raise

        # Get the appropriate adapter with testing context
        # Use dry_run from testing context (which comes from config or testing flags)
        adapter = get_adapter(principal, dry_run=testing_ctx.dry_run, testing_context=testing_ctx)

        # Check if manual approval is required
        manual_approval_required = (
            adapter.manual_approval_required if hasattr(adapter, "manual_approval_required") else False
        )
        manual_approval_operations = (
            adapter.manual_approval_operations if hasattr(adapter, "manual_approval_operations") else []
        )

        # DEBUG: Log manual approval settings
        logger.info(
            f"[DEBUG] Manual approval check - required: {manual_approval_required}, "
            f"operations: {manual_approval_operations}, "
            f"adapter type: {adapter.__class__.__name__}"
        )

        # Check if auto-creation is disabled in tenant config
        auto_create_enabled = tenant.get("auto_create_media_buys", True)
        product_auto_create = True  # Will be set correctly when we get products later

        if manual_approval_required and "create_media_buy" in manual_approval_operations:
            # Update existing workflow step to require approval
            ctx_manager.update_workflow_step(
                step.step_id,
                status="requires_approval",
                add_comment={"user": "system", "comment": "Manual approval required for media buy creation"},
            )

            # Workflow step already created above - no need for separate task
            # Generate permanent media buy ID (not "pending_xxx")
            # This ID will be used whether pending or approved - only status changes
            media_buy_id = f"mb_{uuid.uuid4().hex[:12]}"

            response_msg = (
                f"Manual approval required. Workflow Step ID: {step.step_id}. Context ID: {persistent_ctx.context_id}"
            )
            ctx_manager.add_message(persistent_ctx.context_id, "assistant", response_msg)

            # Send Slack notification for manual approval requirement
            try:
                # Get principal name for notification
                principal_name = principal.name if principal else principal_id

                # Build notifier config from tenant fields
                notifier_config = {
                    "features": {
                        "slack_webhook_url": tenant.get("slack_webhook_url"),
                        "slack_audit_webhook_url": tenant.get("slack_audit_webhook_url"),
                    }
                }
                slack_notifier = get_slack_notifier(notifier_config)

                # Create notification details
                notification_details = {
                    "total_budget": total_budget,
                    "po_number": req.po_number,
                    "start_time": start_time.isoformat(),  # Resolved from 'asap' if needed
                    "end_time": end_time.isoformat(),
                    "product_ids": req.get_product_ids(),
                    "workflow_step_id": step.step_id,
                    "context_id": persistent_ctx.context_id,
                }

                slack_notifier.notify_media_buy_event(
                    event_type="approval_required",
                    media_buy_id=media_buy_id,
                    principal_name=principal_name,
                    details=notification_details,
                    tenant_name=tenant.get("name", "Unknown"),
                    tenant_id=tenant.get("tenant_id"),
                    success=True,
                )
                logger.info("📧 Sent manual approval notification to Slack")
            except Exception as e:
                logger.warning(f"⚠️ Failed to send manual approval Slack notification: {e}")

            # Generate permanent package IDs (not dependent on media buy ID)
            # These IDs will be used whether the media buy is pending or approved
            pending_packages = []
            raw_request_dict = req.model_dump(mode="json", by_alias=True)  # Serialize with aliases (e.g., content_uri)

            for idx, pkg in enumerate(req.packages, 1):
                # Generate permanent package ID using product_id and index
                # Format: pkg_{product_id}_{timestamp_part}_{idx}
                import secrets

                package_id = f"pkg_{pkg.product_id}_{secrets.token_hex(4)}_{idx}"

                # Use product_id or buyer_ref for package name since Package schema doesn't have 'name'
                pkg_name = f"Package {idx}"
                if pkg.product_id:
                    pkg_name = f"{pkg.product_id} - Package {idx}"
                elif pkg.buyer_ref:
                    pkg_name = f"{pkg.buyer_ref} - Package {idx}"

                # Build Package object with complete package data (matching auto-approval path)
                # NOTE: Do NOT set package status here - Package.status is PackageStatus (draft/active/paused/completed)
                # The workflow approval state is tracked separately in WorkflowStep.status (TaskStatus)
                from src.core.schemas import Package

                # Create Package object from request package, adding generated fields
                pkg_data = pkg.model_dump(exclude_none=True)
                pkg_data.update(
                    {
                        "package_id": package_id,
                        "buyer_ref": pkg.buyer_ref,  # Include buyer_ref from request package
                        "status": "draft",  # Set initial status (required by AdCP spec)
                        # Status will be updated to "active" after approval + adapter creation
                    }
                )
                pending_packages.append(Package(**pkg_data))

                # Update the package in raw_request with the generated package_id so UI can find it
                raw_request_dict["packages"][idx - 1]["package_id"] = package_id

            # Remap package_pricing_info from index-based keys to actual package IDs
            # Note: pending_packages loop used enumerate(req.packages, 1) but pricing used enumerate(req.packages) starting at 0
            package_pricing_info: dict[str, dict[str, Any]] = {}
            # Map pricing info from package index to package_id
            for pkg_idx, pkg_obj in enumerate(pending_packages):
                if pkg_idx in package_pricing_info_by_index:
                    # Only add to dict if package_id is not None
                    if pkg_obj.package_id is not None:
                        package_pricing_info[pkg_obj.package_id] = package_pricing_info_by_index[pkg_idx]
                else:
                    logger.warning(f"No pricing info found for package index {pkg_idx}")
            logger.debug(f"[PRICING] Mapped {len(package_pricing_info)} package pricing info")

            # Create media buy record in the database with permanent ID
            # Status is "pending_approval" but the ID is final
            with get_db_session() as session:
                pending_buy = MediaBuy(
                    media_buy_id=media_buy_id,
                    buyer_ref=req.buyer_ref,
                    principal_id=principal.principal_id,
                    tenant_id=tenant["tenant_id"],
                    status="pending_approval",
                    order_name=f"{req.buyer_ref} - {start_time.strftime('%Y-%m-%d')}",
                    advertiser_name=principal.name,
                    budget=total_budget,
                    currency=request_currency or "USD",  # Use request_currency from validation above
                    start_date=start_time.date(),
                    end_date=end_time.date(),
                    start_time=start_time,
                    end_time=end_time,
                    raw_request=raw_request_dict,  # Now includes package_id in each package
                    created_at=datetime.now(UTC),
                )
                session.add(pending_buy)
                session.commit()
                logger.info(f"✅ Created media buy {media_buy_id} with status=pending_approval")

            # Log to activity feed for manual approval case
            try:
                principal_name = principal.name if principal else principal_id
                duration_days = (end_time - start_time).days + 1
                activity_feed.log_media_buy(
                    tenant_id=tenant["tenant_id"],
                    principal_name=principal_name,
                    media_buy_id=media_buy_id,
                    budget=total_budget,
                    duration_days=duration_days,
                    action="pending_approval",  # Different action to indicate awaiting approval
                )
            except Exception as e:
                logger.warning(f"Failed to log media buy pending approval to activity feed: {e}")

            # Log to audit log for manual approval case
            try:
                audit_logger = get_audit_logger("AdCP", tenant["tenant_id"])
                audit_logger.log_operation(
                    operation="create_media_buy_pending_approval",
                    principal_name=principal_name,
                    principal_id=principal_id or "anonymous",
                    adapter_id="mcp_server",
                    success=True,
                    details={
                        "media_buy_id": media_buy_id,
                        "buyer_ref": req.buyer_ref,
                        "budget": total_budget,
                        "currency": request_currency or "USD",
                        "workflow_step_id": step.step_id,
                        "context_id": persistent_ctx.context_id,
                    },
                )
            except Exception as e:
                logger.warning(f"Failed to log media buy pending approval to audit log: {e}")

            # Create MediaPackage records for structured querying
            # This enables the UI to display packages and creative assignments to work properly
            with get_db_session() as session:
                from src.core.database.models import MediaPackage as DBMediaPackage

                for pkg_obj in pending_packages:
                    # Sanitize status to ensure AdCP spec compliance
                    raw_status = pkg_obj.status
                    sanitized_status = _sanitize_package_status(raw_status)

                    package_config = {
                        "package_id": pkg_obj.package_id,  # type: ignore[index]
                        "name": getattr(pkg_obj, "name", None),
                        "status": sanitized_status,  # Only store AdCP-compliant status values
                    }
                    # Add full package data from raw_request
                    for idx, req_pkg in enumerate(req.packages):
                        if idx == pending_packages.index(pkg_obj):
                            # Get pricing info for this package if available
                            pricing_info_for_package = package_pricing_info.get(pkg_obj.package_id) if pkg_obj.package_id else None  # type: ignore[index]

                            # Serialize budget: normalize to object format for database storage
                            # ADCP 2.5.0 sends flat numbers, but we normalize to object with currency for DB
                            budget_value: dict[str, Any] | None = None
                            if req_pkg.budget is not None:
                                if isinstance(req_pkg.budget, (int, float)):
                                    # ADCP 2.5.0 flat format: normalize to object with currency from pricing
                                    package_currency = request_currency  # Use request-level currency
                                    if pricing_info_for_package:
                                        package_currency = pricing_info_for_package.get("currency", request_currency)
                                    budget_value = {
                                        "total": float(req_pkg.budget),
                                        "currency": package_currency,
                                    }
                                elif hasattr(req_pkg.budget, "model_dump"):
                                    # ADCP 2.3 object format: store as-is
                                    budget_value = req_pkg.budget.model_dump()
                                else:
                                    # Fallback: treat as dict or convert to dict
                                    budget_value = (
                                        dict(req_pkg.budget)
                                        if isinstance(req_pkg.budget, dict)
                                        else {"total": float(req_pkg.budget), "currency": request_currency}
                                    )

                            package_config.update(
                                {
                                    "product_id": req_pkg.product_id,
                                    "budget": budget_value,
                                    "targeting_overlay": (
                                        req_pkg.targeting_overlay.model_dump() if req_pkg.targeting_overlay else None
                                    ),
                                    "creative_ids": req_pkg.creative_ids,
                                    "format_ids": req_pkg.format_ids if hasattr(req_pkg, "format_ids") else None,
                                    "pricing_info": pricing_info_for_package,  # Store pricing info for UI display
                                    "impressions": req_pkg.impressions,  # Store impressions for display
                                }
                            )
                            break

                    # Extract pricing fields for dual-write
                    from decimal import Decimal

                    budget_total = None
                    if budget_value:
                        if isinstance(budget_value, dict):
                            budget_total = budget_value.get("total")
                        elif isinstance(budget_value, (int, float)):
                            budget_total = float(budget_value)

                    bid_price_value = None
                    pacing_value = None
                    if pricing_info_for_package:
                        bid_price_value = pricing_info_for_package.get("bid_price")
                    if budget_value and isinstance(budget_value, dict):
                        pacing_value = budget_value.get("pacing")

                    # Create MediaPackage with dual-write: dedicated columns + JSON
                    db_package = DBMediaPackage(
                        media_buy_id=media_buy_id,
                        package_id=pkg_data["package_id"],
                        package_config=package_config,
                        # Dual-write: populate dedicated columns
                        budget=Decimal(str(budget_total)) if budget_total is not None else None,
                        bid_price=Decimal(str(bid_price_value)) if bid_price_value is not None else None,
                        pacing=pacing_value,
                    )
                    session.add(db_package)

                session.commit()
                logger.info(f"✅ Created {len(pending_packages)} MediaPackage records")

            # Link the workflow step to the media buy so the approval button shows in UI
            with get_db_session() as session:
                from src.core.database.models import ObjectWorkflowMapping

                mapping = ObjectWorkflowMapping(
                    object_type="media_buy", object_id=media_buy_id, step_id=step.step_id, action="create"
                )
                session.add(mapping)
                session.commit()
                logger.info(f"✅ Linked workflow step {step.step_id} to media buy")

            # Create creative assignments for manual approval flow
            # This must happen AFTER media packages are created so we have package_ids
            if req.packages:
                with get_db_session() as session:
                    from src.core.database.models import Creative as DBCreative
                    from src.core.database.models import CreativeAssignment as DBAssignment

                    # Batch load all creatives upfront
                    all_creative_ids = []
                    for package in req.packages:
                        if package.creative_ids:
                            all_creative_ids.extend(package.creative_ids)

                    creatives_map: dict[str, Any] = {}
                    if all_creative_ids:
                        creative_stmt = select(DBCreative).where(
                            DBCreative.tenant_id == tenant["tenant_id"],
                            DBCreative.creative_id.in_(all_creative_ids),
                        )
                        creatives_list = session.scalars(creative_stmt).all()
                        creatives_map = {str(c.creative_id): c for c in creatives_list}
                        logger.info(f"[CREATIVE_ASSIGN_DEBUG] Loaded {len(creatives_map)} creatives from database")

                    # Create assignments for each package
                    for i, package in enumerate(req.packages):
                        if package.creative_ids:
                            # Get package_id from pending_packages (already generated)
                            pkg_id: str | None = (
                                pending_packages[i].package_id if i < len(pending_packages) else None  # type: ignore[union-attr]
                            )
                            if not pkg_id:
                                logger.error(f"Cannot assign creatives: No package_id for package {i}")
                                continue

                            logger.info(
                                f"[CREATIVE_ASSIGN_DEBUG] Creating assignments for package {pkg_id}, creative_ids: {package.creative_ids}"
                            )

                            for creative_id in package.creative_ids:
                                creative = creatives_map.get(creative_id)
                                if not creative:
                                    logger.warning(f"Creative {creative_id} not found in database, skipping assignment")
                                    continue

                                # Create database assignment
                                assignment_id = f"assign_{uuid.uuid4().hex[:12]}"
                                assignment = DBAssignment(
                                    assignment_id=assignment_id,
                                    tenant_id=tenant["tenant_id"],
                                    media_buy_id=media_buy_id,
                                    package_id=pkg_id,
                                    creative_id=creative_id,
                                )
                                session.add(assignment)
                                logger.info(
                                    f"[CREATIVE_ASSIGN_DEBUG] Created assignment {assignment_id} for creative {creative_id}"
                                )

                            session.commit()
                            logger.info(f"✅ Created creative assignments for package {pkg_id}")

            # Return success response with packages awaiting approval
            # The workflow_step_id in packages indicates approval is required
            # buyer_ref is required by schema, but mypy needs explicit check
            response_buyer_ref = req.buyer_ref if req.buyer_ref else "unknown"
            return CreateMediaBuySuccess(  # type: ignore[return-value]
                buyer_ref=response_buyer_ref,
                media_buy_id=media_buy_id,
                creative_deadline=None,
                packages=pending_packages,  # type: ignore[arg-type]
                workflow_step_id=step.step_id,  # Client can track approval via this ID
                context=req.context,
            )

        # Get products for the media buy to check product-level auto-creation settings
        # Lazy import to avoid circular dependency with main.py
        from src.core.main import get_product_catalog

        catalog = get_product_catalog()
        product_ids = req.get_product_ids()
        products_in_buy = [p for p in catalog if p.product_id in product_ids]

        # Validate and auto-generate GAM implementation_config for each product if needed
        if adapter.__class__.__name__ == "GoogleAdManager":
            from src.services.gam_product_config_service import GAMProductConfigService

            gam_validator = GAMProductConfigService()
            config_errors = []

            for product in products_in_buy:  # type: ignore[assignment]
                # Auto-generate default config if missing
                if not product.implementation_config:
                    logger.info(
                        f"Product '{product.name}' ({product.product_id}) is missing GAM configuration. "
                        f"Auto-generating defaults based on product type."
                    )
                    # Generate defaults based on product delivery type and formats
                    delivery_type = product.delivery_type if hasattr(product, "delivery_type") else "non_guaranteed"
                    # Extract format IDs as strings for config generation
                    formats_list: list[str] | None = None
                    if hasattr(product, "formats") and product.format_ids:
                        formats_list = []
                        for fmt in product.format_ids:
                            if isinstance(fmt, str):
                                formats_list.append(fmt)
                            elif isinstance(fmt, dict):
                                formats_list.append(fmt.get("id") or fmt.get("format_id", ""))
                            elif hasattr(fmt, "format_id"):
                                formats_list.append(fmt.format_id)
                    product.implementation_config = gam_validator.generate_default_config(
                        delivery_type=delivery_type, formats=formats_list
                    )

                    # Persist the auto-generated config to database
                    with get_db_session() as db_session:
                        product_stmt = select(ModelProduct).filter_by(product_id=product.product_id)
                        db_product = db_session.scalars(product_stmt).first()
                        if db_product:
                            db_product.implementation_config = product.implementation_config  # type: ignore[assignment]
                            db_session.commit()
                            logger.info(f"Saved auto-generated GAM config for product {product.product_id}")

                # Validate the config (whether existing or auto-generated)
                impl_config = product.implementation_config if product.implementation_config else {}
                is_valid, error_msg_temp = gam_validator.validate_config(impl_config)  # type: ignore[arg-type]
                error_msg = error_msg_temp if error_msg_temp else "Unknown error"
                if not is_valid:
                    config_errors.append(
                        f"Product '{product.name}' ({product.product_id}) has invalid GAM configuration: {error_msg}"
                    )

            if config_errors:
                error_detail = "GAM configuration validation failed:\n" + "\n".join(
                    f"  • {err}" for err in config_errors
                )
                ctx_manager.update_workflow_step(step.step_id, status="failed", error_message=error_detail)
                return CreateMediaBuyError(  # type: ignore[return-value]
                    errors=[Error(code="invalid_configuration", message=err, details=None) for err in config_errors],
                    context=req.context,
                )

        product_auto_create = all(
            p.implementation_config.get("auto_create_enabled", True) if p.implementation_config else True
            for p in products_in_buy
        )

        # Check if either tenant or product disables auto-creation
        if not auto_create_enabled or not product_auto_create:
            reason = "Tenant configuration" if not auto_create_enabled else "Product configuration"

            # Update existing workflow step to require approval
            ctx_manager.update_workflow_step(step.step_id, status="requires_approval")

            # Workflow step already created above - no need for separate task
            # Generate permanent media buy ID (not "pending_xxx")
            media_buy_id = f"mb_{uuid.uuid4().hex[:12]}"

            response_msg = f"Media buy requires approval due to {reason.lower()}. Workflow Step ID: {step.step_id}. Context ID: {persistent_ctx.context_id}"
            ctx_manager.add_message(persistent_ctx.context_id, "assistant", response_msg)

            # Generate permanent package IDs and prepare response packages
            response_packages = []
            for idx, pkg in enumerate(req.packages, 1):
                # Generate permanent package ID
                import secrets

                package_id = f"pkg_{pkg.product_id}_{secrets.token_hex(4)}_{idx}"

                # Per AdCP spec, create-media-buy-response Package only includes:
                # - buyer_ref (required): Buyer's reference identifier
                # - package_id (required): Publisher's unique identifier
                response_packages.append(
                    {
                        "buyer_ref": pkg.buyer_ref,
                        "package_id": package_id,
                    }
                )

            # Send Slack notification for configuration-based approval requirement
            try:
                # Get principal name for notification
                principal_name = principal.name if principal else principal_id

                # Build notifier config from tenant fields
                notifier_config = {
                    "features": {
                        "slack_webhook_url": tenant.get("slack_webhook_url"),
                        "slack_audit_webhook_url": tenant.get("slack_audit_webhook_url"),
                    }
                }
                slack_notifier = get_slack_notifier(notifier_config)

                # Create notification details including configuration reason
                notification_details = {
                    "total_budget": total_budget,
                    "po_number": req.po_number,
                    "start_time": start_time.isoformat(),  # Resolved from 'asap' if needed
                    "end_time": end_time.isoformat(),
                    "product_ids": req.get_product_ids(),
                    "approval_reason": reason,
                    "workflow_step_id": step.step_id,
                    "context_id": persistent_ctx.context_id,
                    "auto_create_enabled": auto_create_enabled,
                    "product_auto_create": product_auto_create,
                }

                slack_notifier.notify_media_buy_event(
                    event_type="config_approval_required",
                    media_buy_id=media_buy_id,
                    principal_name=principal_name,
                    details=notification_details,
                    tenant_name=tenant.get("name", "Unknown"),
                    tenant_id=tenant.get("tenant_id"),
                    success=True,
                )
                logger.info(f"📧 Sent {reason.lower()} approval notification to Slack")
            except Exception as e:
                logger.warning(f"⚠️ Failed to send configuration approval Slack notification: {e}")

            return CreateMediaBuySuccess(  # type: ignore[return-value]
                buyer_ref=req.buyer_ref if req.buyer_ref else "unknown",
                media_buy_id=media_buy_id,
                packages=response_packages,  # type: ignore[arg-type]
                workflow_step_id=step.step_id,
                context=req.context,
            )

        # Continue with synchronized media buy creation

        # Note: products_in_buy was already calculated above for product_auto_create check
        # No need to recalculate

        # Note: Key-value pairs are NOT aggregated here anymore.
        # Each product maintains its own custom_targeting_keys in implementation_config
        # which will be applied separately to its corresponding line item in GAM.
        # The adapter (google_ad_manager.py) handles this per-product targeting at line 491-494

        # Convert products to MediaPackages
        # CRITICAL: Iterate over req.packages, not products_in_buy, to handle multiple packages with same product_id
        # Example: 2 packages with same product_id but different targeting (US vs CA) must create 2 MediaPackages
        packages = []
        for idx, pkg in enumerate(req.packages, 1):  # Iterate over request packages
            # Find the product for this package (from schema catalog, not database model)
            # Package has product_id field per AdCP spec
            pkg_product_id = pkg.product_id

            if not pkg_product_id:
                error_msg = f"Package {idx} has no product_id field set"
                raise ValueError(error_msg)

            pkg_product: Product | None = None
            for p in products_in_buy:
                if p.product_id == pkg_product_id:
                    pkg_product = p
                    break

            if not pkg_product:
                error_msg = f"Package {idx} references unknown product_id: {pkg_product_id}"
                raise ValueError(error_msg)

            # Determine format_ids to use
            format_ids_to_use: list[FormatId] = []

            # Use format_ids from request package if provided
            matching_package = pkg  # The package we're iterating over

            # If found and has format_ids, validate and use those
            if matching_package and hasattr(matching_package, "format_ids") and matching_package.format_ids:
                # Validate that requested formats are supported by product
                # Format is composite key: (agent_url, format_id) per AdCP spec
                # Note: AdCP JSON uses "id" field, but Pydantic object uses "format_id" attribute
                # Build set of (agent_url, format_id) tuples for comparison
                product_format_keys = set()
                if pkg_product.format_ids:
                    for fmt in pkg_product.format_ids:  # type: ignore[assignment]
                        agent_url: str | None = None
                        format_id: str | None = None

                        if isinstance(fmt, dict):  # type: ignore[misc]
                            # Database JSONB: uses "id" per AdCP spec
                            agent_url = fmt.get("agent_url")
                            format_id = fmt.get("id") or fmt.get(
                                "format_id"
                            )  # "id" is AdCP spec, "format_id" is legacy
                        elif hasattr(fmt, "agent_url") and (hasattr(fmt, "format_id") or hasattr(fmt, "id")):
                            # Pydantic object: uses "format_id" attribute (serializes to "id" in JSON)
                            agent_url = fmt.agent_url
                            format_id = getattr(fmt, "format_id", None) or getattr(fmt, "id", None)
                        elif isinstance(fmt, str):
                            # Legacy: plain string format ID (no agent_url)
                            format_id = fmt

                        if format_id:
                            # Normalize agent_url by removing trailing slash for consistent comparison
                            normalized_url = agent_url.rstrip("/") if agent_url else None
                            product_format_keys.add((normalized_url, format_id))

                # Build set of requested format keys for comparison
                requested_format_keys = set()  # type: ignore[var-annotated]
                for fmt in matching_package.format_ids:  # type: ignore[assignment]
                    agent_url = None
                    format_id = None

                    if isinstance(fmt, dict):
                        # JSON from request: uses "id" per AdCP spec
                        agent_url = fmt.get("agent_url")
                        format_id = fmt.get("id") or fmt.get("format_id")  # "id" is AdCP spec, "format_id" is legacy
                    elif hasattr(fmt, "agent_url") and (hasattr(fmt, "format_id") or hasattr(fmt, "id")):
                        # Pydantic object: uses "format_id" attribute
                        agent_url = fmt.agent_url
                        format_id = getattr(fmt, "format_id", None) or getattr(fmt, "id", None)
                    elif isinstance(fmt, str):
                        # Legacy: plain string
                        format_id = fmt

                    if format_id:
                        # Normalize agent_url by removing trailing slash for consistent comparison
                        normalized_url = agent_url.rstrip("/") if agent_url else None
                        requested_format_keys.add((normalized_url, format_id))

                def format_display(url: str | None, fid: str) -> str:
                    """Format a (url, id) pair for display, handling trailing slashes."""
                    if not url:
                        return fid
                    # Remove trailing slash from URL to avoid double slashes
                    clean_url = url.rstrip("/")
                    return f"{clean_url}/{fid}"

                def _has_supported_key(url: str | None, fid: str, keys: set = product_format_keys) -> bool:
                    """Check if (url, fid) is supported, allowing an '/mcp' URL variant.

                    This does not mutate any of the underlying key sets; it only checks
                    for the presence of either the exact key or an alternative where
                    '/mcp' is appended to the end of the URL path.

                    Args:
                        url: The format URL to check
                        fid: The format ID to check
                        keys: The set of supported (url, fid) tuples (bound at function definition)
                    """
                    # Exact match first
                    if (url, fid) in keys:
                        return True

                    # If URL provided, also try with '/mcp' appended (idempotent if already present)
                    if url:
                        base = url.rstrip("/")
                        mcp_url = base if base.endswith("/mcp") else f"{base}/mcp"
                        if (mcp_url, fid) in keys:
                            return True

                    return False

                unsupported_formats = [
                    format_display(url, fid) for url, fid in requested_format_keys if not _has_supported_key(url, fid)
                ]

                if unsupported_formats:
                    supported_formats_str = ", ".join([format_display(url, fid) for url, fid in product_format_keys])
                    error_msg = (
                        f"Product '{pkg_product.name}' ({pkg_product.product_id}) does not support requested format(s): "
                        f"{', '.join(unsupported_formats)}. Supported formats: {supported_formats_str}"
                    )
                    raise ValueError(error_msg)

                # Preserve original format objects for format_ids_to_use
                format_ids_to_use = list(matching_package.format_ids)  # type: ignore[arg-type]

            # Fallback to product's formats if no request format_ids
            if not format_ids_to_use:
                if pkg_product.format_ids:
                    # Convert product.format_ids to FormatId objects if they're strings
                    # Get default creative agent URL from tenant config (tenant is dict[str, Any])
                    default_agent_url = tenant.get("creative_agent_url") or "https://creative.adcontextprotocol.org"
                    for fmt in pkg_product.format_ids:  # type: ignore[assignment]
                        if isinstance(fmt, str):
                            # Convert legacy string format to FormatId object
                            format_ids_to_use.append(FormatId(agent_url=make_url(default_agent_url), id=fmt))
                        else:
                            # Already a FormatId or FormatReference object
                            format_ids_to_use.append(fmt)  # type: ignore[arg-type]
                else:
                    format_ids_to_use = []

            # Get CPM from pricing_options
            cpm = 10.0  # Default
            if pkg_product.pricing_options and len(pkg_product.pricing_options) > 0:
                first_option = pkg_product.pricing_options[0]
                # Use getattr for discriminated union to avoid union-attr errors
                rate = getattr(first_option, "rate", None)
                if rate:
                    cpm = float(rate)

            # Generate permanent package ID (not product_id)
            import secrets

            package_id = f"pkg_{pkg_product.product_id}_{secrets.token_hex(4)}_{idx}"

            # Get buyer_ref and budget from matching request package if available
            package_buyer_ref: str | None = None
            package_budget_value: float | None = None
            if matching_package:
                if hasattr(matching_package, "buyer_ref"):
                    package_buyer_ref = matching_package.buyer_ref
                if hasattr(matching_package, "budget"):
                    raw_budget = matching_package.budget
                    # Normalize budget: MediaPackage expects float | None (ADCP 2.5.0)
                    if raw_budget is not None:
                        if isinstance(raw_budget, (int, float)):
                            # ADCP 2.5.0 flat format: use as-is (float)
                            package_budget_value = float(raw_budget)
                        elif isinstance(raw_budget, dict):
                            # Legacy dict format: extract total
                            package_budget_value = float(raw_budget.get("total", 0.0))
                        elif hasattr(raw_budget, "total"):
                            # Budget object: extract total
                            package_budget_value = float(raw_budget.total)
                        else:
                            package_budget_value = None

            # Extract delivery_type string value from enum (if it's an enum)
            from typing import cast

            if hasattr(pkg_product.delivery_type, "value"):
                # It's an enum - extract the string value
                delivery_type_str = pkg_product.delivery_type.value
            else:
                # Already a string - cast to satisfy mypy
                delivery_type_str = str(pkg_product.delivery_type)

            delivery_type_value: Literal["guaranteed", "non_guaranteed"] = cast(
                Literal["guaranteed", "non_guaranteed"], delivery_type_str
            )

            packages.append(
                MediaPackage(
                    package_id=package_id,
                    name=pkg_product.name,
                    delivery_type=delivery_type_value,
                    cpm=cpm,
                    impressions=int(total_budget / cpm * 1000),
                    format_ids=format_ids_to_use,  # type: ignore[arg-type]
                    targeting_overlay=(
                        matching_package.targeting_overlay  # type: ignore[arg-type]
                        if matching_package and hasattr(matching_package, "targeting_overlay")
                        else None
                    ),
                    buyer_ref=package_buyer_ref,
                    product_id=pkg_product.product_id,  # Include product_id
                    budget=package_budget_value,  # Include budget from request (now normalized)
                    creative_ids=(
                        matching_package.creative_ids if matching_package else None
                    ),  # Include creative_ids from uploaded creatives
                )
            )

        # Remap package_pricing_info from index-based keys to actual package IDs
        # Note: packages loop used enumerate(products_in_buy, 1) but pricing used enumerate(req.packages) starting at 0
        remapped_package_pricing_info: dict[str, dict[str, Any]] = {}
        # Map pricing info from index to package_id
        for pkg_idx, pkg in enumerate(packages):
            if pkg_idx in package_pricing_info_by_index:
                # Only add to dict if package_id is not None
                if pkg.package_id is not None:
                    remapped_package_pricing_info[pkg.package_id] = package_pricing_info_by_index[pkg_idx]
            else:
                logger.warning(f"No pricing info found for package index {pkg_idx}")
        logger.debug(f"[PRICING] Mapped {len(remapped_package_pricing_info)} package pricing info")
        # Reassign to package_pricing_info for use later
        package_pricing_info = remapped_package_pricing_info

        # Create the media buy using the adapter (SYNCHRONOUS operation)
        # Defensive null check: ensure start_time and end_time are set
        if not req.start_time or not req.end_time:
            error_msg = "start_time and end_time are required but were not properly set"
            ctx_manager.update_workflow_step(step.step_id, status="failed", error_message=error_msg)
            return CreateMediaBuyError(  # type: ignore[return-value]
                errors=[Error(code="invalid_datetime", message=error_msg, details=None)],
                context=req.context,
            )

        # PRE-VALIDATE: Check all creatives have required fields BEFORE calling adapter
        # This prevents GAM order creation when creatives are invalid (all-or-nothing approach)
        try:
            _validate_creatives_before_adapter_call(packages, tenant["tenant_id"])
        except ToolError:
            # Validation failed - creative validation errors already logged
            # Update workflow step as failed and re-raise
            ctx_manager.update_workflow_step(step.step_id, status="failed", error_message="Creative validation failed")
            raise

        # Call adapter using shared creation logic
        # Note: start_time variable already resolved from 'asap' to actual datetime if needed
        # This uses the same function as manual approval to ensure consistency across adapters
        try:
            response = _execute_adapter_media_buy_creation(
                req, packages, start_time, end_time, package_pricing_info, principal, testing_ctx
            )
        except Exception as adapter_error:
            raise

        # Check if adapter returned an error response FIRST (before accessing any fields)
        # With oneOf pattern, response can be CreateMediaBuySuccess or CreateMediaBuyError
        if isinstance(response, CreateMediaBuyError):
            error_msg = response.errors[0].message if response.errors else "Unknown error"
            error_code = response.errors[0].code if response.errors else "UNKNOWN"
            logger.error(f"[ADAPTER] Adapter returned error response: {error_code} - {error_msg}")
            return response  # type: ignore[return-value]

        # At this point, response is CreateMediaBuySuccess - safe to access success-specific fields
        # Type narrowing: media_buy_id must be present in successful response
        assert response.media_buy_id is not None, "Adapter returned response without media_buy_id"

        # Log response packages for debugging
        if response.packages:
            for i, pkg in enumerate(response.packages):  # type: ignore[assignment,no-redef]
                # pkg is dict[str, Any] here (response.packages), different scope from earlier Package usage
                logger.info(f"[DEBUG] create_media_buy: Response package {i} = {pkg}")

        # Store the media buy in memory (for backward compatibility)
        # Lazy import to avoid circular dependency
        from src.core.main import media_buys

        media_buys[response.media_buy_id] = (req, principal_id)

        # Determine initial status using centralized logic
        # Check if creatives are assigned and approved
        has_creatives = False
        creatives_approved = True  # Assume approved if any exist

        # Check packages for creative_ids
        if req.packages:
            for pkg in req.packages:
                if pkg.creative_ids:
                    has_creatives = True
                    # For now, assume creatives in request are not yet approved
                    # They need to go through sync_creatives approval flow
                    creatives_approved = False
                    break

        # Use centralized status determination
        now = datetime.now(UTC)
        media_buy_status = _determine_media_buy_status(
            manual_approval_required=False,  # This path only runs when approval NOT required
            has_creatives=has_creatives,
            creatives_approved=creatives_approved,
            start_time=start_time,
            end_time=end_time,
            now=now,
        )
        logger.info(
            f"[STATUS] Media buy {response.media_buy_id}: manual_approval=False, "
            f"has_creatives={has_creatives}, creatives_approved={creatives_approved} → status={media_buy_status}"
        )

        # Store the media buy in database (context_id is NULL for synchronous operations)
        tenant = get_current_tenant()
        with get_db_session() as session:
            new_media_buy = MediaBuy(
                media_buy_id=response.media_buy_id,
                tenant_id=tenant["tenant_id"],
                principal_id=principal_id,
                buyer_ref=req.buyer_ref,  # AdCP v2.4 buyer reference
                order_name=req.po_number or f"Order-{response.media_buy_id}",
                advertiser_name=principal.name,
                campaign_objective=getattr(req, "campaign_objective", ""),  # Optional field
                kpi_goal=getattr(req, "kpi_goal", ""),  # Optional field
                budget=total_budget,  # Extract total budget
                currency=request_currency,  # AdCP v2.4 currency field (resolved above)
                start_date=start_time.date(),  # Legacy field for compatibility
                end_date=end_time.date(),  # Legacy field for compatibility
                start_time=start_time,  # AdCP v2.4 datetime scheduling (resolved from 'asap' if needed)
                end_time=end_time,  # AdCP v2.4 datetime scheduling
                status=media_buy_status,
                raw_request=req.model_dump(mode="json"),
            )
            session.add(new_media_buy)
            session.commit()

        # Populate media_packages table for structured querying
        # This enables creative_assignments to work properly
        if req.packages or (response.packages and len(response.packages) > 0):
            with get_db_session() as session:
                from src.core.database.models import MediaPackage as DBMediaPackage

                # Use response packages if available (has package_ids), otherwise generate from request
                packages_to_save = response.packages if response.packages else []
                logger.info(f"[DEBUG] Saving {len(packages_to_save)} packages to media_packages table")

                for i, resp_package in enumerate(packages_to_save):
                    # Handle both dict and Pydantic Package objects
                    # resp_package can be dict (from some adapters) or Package (from adcp v1.2.1)
                    def get_package_field(pkg, field: str, default=None):
                        """Get field from Package object or dict."""
                        if isinstance(pkg, dict):
                            return pkg.get(field, default)
                        else:
                            return getattr(pkg, field, default)

                    def serialize_for_json(value):
                        """Serialize Pydantic models to dicts for JSON storage."""
                        from pydantic import BaseModel

                        if value is None:
                            return None
                        if isinstance(value, BaseModel):
                            return value.model_dump(exclude_none=True)
                        if isinstance(value, list):
                            return [serialize_for_json(item) for item in value]
                        if isinstance(value, dict):
                            return {k: serialize_for_json(v) for k, v in value.items()}
                        return value

                    # Extract package_id from response - MUST be present, no fallback allowed
                    resp_package_id: str | None = get_package_field(resp_package, "package_id")
                    logger.info(f"[DEBUG] Package {i}: package_id = {resp_package_id}")

                    if not resp_package_id:
                        error_msg = (
                            f"Adapter did not return package_id for package {i}. This is a critical bug in the adapter."
                        )
                        logger.error(error_msg)
                        raise ValueError(error_msg)

                    logger.info(f"[DEBUG] Package {i}: Using package_id = {resp_package_id}")

                    # Store full package config as JSON
                    # Sanitize status to ensure AdCP spec compliance
                    raw_status = get_package_field(resp_package, "status")
                    sanitized_status = _sanitize_package_status(raw_status)

                    # Get pricing info for this package if available
                    pricing_info_for_package = package_pricing_info.get(package_id)

                    # Get impressions from request package if available
                    request_pkg: PackageRequest | None = req.packages[i] if i < len(req.packages) else None
                    impressions = request_pkg.impressions if request_pkg else None

                    package_config = {
                        "package_id": resp_package_id,
                        "name": get_package_field(resp_package, "name"),  # Include package name from adapter response
                        "product_id": get_package_field(resp_package, "product_id"),
                        "budget": serialize_for_json(get_package_field(resp_package, "budget")),
                        "targeting_overlay": serialize_for_json(get_package_field(resp_package, "targeting_overlay")),
                        "creative_ids": get_package_field(resp_package, "creative_ids"),
                        "creative_assignments": serialize_for_json(
                            get_package_field(resp_package, "creative_assignments")
                        ),
                        "format_ids_to_provide": get_package_field(resp_package, "format_ids_to_provide"),
                        "status": sanitized_status,  # Only store AdCP-compliant status values
                        "pricing_info": pricing_info_for_package,  # Store pricing info for UI display
                        "impressions": impressions,  # Store impressions for display
                    }

                    # Extract pricing fields for dual-write from adapter response
                    from decimal import Decimal

                    budget_total = None
                    budget_data = get_package_field(resp_package, "budget")
                    if budget_data:
                        if isinstance(budget_data, dict):
                            budget_total = budget_data.get("total")
                        elif isinstance(budget_data, (int, float)):
                            budget_total = float(budget_data)

                    bid_price_value = None
                    pacing_value = None
                    if pricing_info_for_package:
                        bid_price_value = pricing_info_for_package.get("bid_price")
                    if budget_data and isinstance(budget_data, dict):
                        pacing_value = budget_data.get("pacing")

                    # Create MediaPackage with dual-write: dedicated columns + JSON
                    db_package = DBMediaPackage(
                        media_buy_id=response.media_buy_id,
                        package_id=resp_package_id,
                        package_config=package_config,
                        # Dual-write: populate dedicated columns
                        budget=Decimal(str(budget_total)) if budget_total is not None else None,
                        bid_price=Decimal(str(bid_price_value)) if bid_price_value is not None else None,
                        pacing=pacing_value,
                    )
                    session.add(db_package)

                session.commit()
                logger.info(
                    f"Saved {len(packages_to_save)} packages to media_packages table for media_buy {response.media_buy_id}"
                )

                # Update packages with platform_line_item_id from adapter response
                # This is required for update_media_buy operations (budget updates, pause/resume)
                # Adapters (like GAM) attach a _platform_line_item_ids mapping to the response object
                platform_line_item_ids = getattr(response, "_platform_line_item_ids", {})

                if platform_line_item_ids:
                    logger.info(f"[DEBUG] Found platform_line_item_ids mapping: {platform_line_item_ids}")

                    for pkg_id, line_item_id in platform_line_item_ids.items():
                        # Update the package_config with platform_line_item_id
                        from sqlalchemy import select

                        from src.core.database.models import MediaPackage as DBMediaPackage

                        package_stmt = select(DBMediaPackage).filter_by(
                            media_buy_id=response.media_buy_id, package_id=pkg_id
                        )
                        pkg_record: DBMediaPackage | None = session.scalars(package_stmt).first()
                        if pkg_record:
                            # Update package_config JSON with platform_line_item_id
                            pkg_record.package_config["platform_line_item_id"] = str(line_item_id)
                            from sqlalchemy.orm import attributes

                            attributes.flag_modified(pkg_record, "package_config")
                            logger.info(f"✓ Updated package {pkg_id} with platform_line_item_id: {line_item_id}")
                        else:
                            logger.warning(f"⚠️  Could not find DB package {pkg_id} to save platform_line_item_id")

                    session.commit()
                    logger.info("✓ Saved platform_line_item_ids to database")
                else:
                    logger.info("[DEBUG] No platform_line_item_ids found on response object")

        # Handle creative_ids in packages if provided (immediate association)
        if req.packages:
            with get_db_session() as session:
                from src.core.database.models import Creative as DBCreative
                from src.core.database.models import CreativeAssignment as DBAssignment

                # Batch load all creatives upfront to avoid N+1 queries
                all_creative_ids = []
                for package in req.packages:
                    if package.creative_ids:
                        all_creative_ids.extend(package.creative_ids)

                creatives_by_id: dict[str, Any] = {}
                if all_creative_ids:
                    creative_stmt = select(DBCreative).where(
                        DBCreative.tenant_id == tenant["tenant_id"],
                        DBCreative.creative_id.in_(all_creative_ids),
                    )
                    creatives_list = session.scalars(creative_stmt).all()
                    creatives_by_id = {str(c.creative_id): c for c in creatives_list}

                    # Validate all creative IDs exist (match update_media_buy behavior)
                    found_creative_ids = set(creatives_by_id.keys())
                    requested_creative_ids = set(all_creative_ids)
                    missing_ids = requested_creative_ids - found_creative_ids

                    if missing_ids:
                        error_msg = f"Creative IDs not found: {', '.join(sorted(missing_ids))}"
                        logger.error(error_msg)
                        ctx_manager.update_workflow_step(step.step_id, status="failed", error_message=error_msg)
                        raise ToolError("CREATIVES_NOT_FOUND", error_msg)

                for i, package in enumerate(req.packages):
                    if package.creative_ids:
                        # Use package_id from response (matches what's in media_packages table)
                        # NO FALLBACK - if adapter doesn't return package_id, fail loudly
                        response_package_id = None
                        if response.packages and i < len(response.packages):
                            # Package is a Pydantic model, use attribute access
                            response_package_id = getattr(response.packages[i], "package_id", None)
                            logger.info(f"[DEBUG] Package {i}: response.packages[i] = {response.packages[i]}")
                            logger.info(f"[DEBUG] Package {i}: extracted package_id = {response_package_id}")

                        if not response_package_id:
                            error_msg = f"Cannot assign creatives: Adapter did not return package_id for package {i}"
                            logger.error(error_msg)
                            raise ValueError(error_msg)

                        # Get platform_line_item_id from response if available
                        platform_line_item_id = None
                        if response.packages and i < len(response.packages):
                            platform_line_item_id = getattr(response.packages[i], "platform_line_item_id", None)

                        # Collect platform creative IDs for association
                        platform_creative_ids = []

                        for creative_id in package.creative_ids:
                            # Get creative from batch-loaded map
                            creative = creatives_by_id.get(creative_id)

                            # This should never happen now due to validation above
                            if not creative:
                                logger.error(f"Creative {creative_id} not in map despite validation - this is a bug")
                                continue

                            # Create database assignment (always create, even if not yet uploaded to GAM)
                            # Get platform_creative_id from creative.data JSON
                            platform_creative_id = creative.data.get("platform_creative_id") if creative.data else None
                            if platform_creative_id:
                                # Add to association list for immediate GAM association
                                platform_creative_ids.append(platform_creative_id)
                            else:
                                # Creative not uploaded to GAM yet - upload it now
                                # Use the same simple asset dict approach as manual approval (execute_approved_media_buy)
                                logger.info(
                                    f"Creative {creative_id} has no platform_creative_id - uploading to GAM now"
                                )
                                try:
                                    creative_data = creative.data or {}

                                    # Get format spec for proper extraction
                                    # Use cache-based approach (same as validation section) to avoid asyncio event loop conflicts
                                    from src.core.format_spec_cache import get_cached_format

                                    format_spec = None
                                    try:
                                        # Try cache first (works in any context, no asyncio conflicts)
                                        format_spec = get_cached_format(str(creative.format))
                                    except (ValueError, Exception) as e:
                                        logger.warning(
                                            f"[AUTO-APPROVAL] Could not load format spec for creative {creative_id} "
                                            f"(format={creative.format}): {e}"
                                        )

                                    # Extract URL and dimensions using shared helper
                                    url, width, height = _extract_creative_url_and_dimensions(
                                        creative_data, format_spec
                                    )

                                    # Build simple asset dict (same as manual approval flow)
                                    asset = {
                                        "creative_id": creative.creative_id,
                                        "package_assignments": [package_id],  # This specific package
                                        "width": width,
                                        "height": height,
                                        "url": url,
                                        "asset_type": creative_data.get("asset_type", "image"),
                                        "name": creative.name or f"Creative {creative.creative_id}",
                                    }

                                    # Validate required fields - FAIL FAST, do not skip
                                    validation_errors = []
                                    if not asset["width"] or not asset["height"]:
                                        validation_errors.append(
                                            f"Creative {creative_id} missing dimensions (width={asset['width']}, height={asset['height']})"
                                        )
                                    if not asset["url"]:
                                        validation_errors.append(f"Creative {creative_id} missing required URL field")

                                    if validation_errors:
                                        error_msg = (
                                            "Cannot create media buy with invalid creatives. "
                                            "The following creatives are missing required fields:\n"
                                            + "\n".join(f"  • {err}" for err in validation_errors)
                                            + "\n\nAll creatives must have dimensions (width/height) and a content URL. "
                                            "Please ensure creatives are properly synced before creating media buys."
                                        )
                                        logger.error(f"[AUTO-APPROVAL] {error_msg}")
                                        # Raise exception for MCP - this will be caught and returned as error response
                                        raise ToolError(
                                            "INVALID_CREATIVES", error_msg, {"creative_errors": validation_errors}
                                        )

                                    # Upload to GAM using adapter's add_creative_assets method
                                    upload_result = adapter.add_creative_assets(
                                        response.media_buy_id if response.media_buy_id else "",
                                        [asset],
                                        datetime.now(UTC),
                                    )
                                    logger.info(f"Successfully uploaded creative {creative_id} to GAM: {upload_result}")

                                    # Update creative in database with platform_creative_id
                                    if upload_result and len(upload_result) > 0:
                                        uploaded_status = upload_result[0]
                                        # Only set platform_creative_id if not already set
                                        if uploaded_status.creative_id and not creative.data.get(
                                            "platform_creative_id"
                                        ):
                                            creative.data["platform_creative_id"] = uploaded_status.creative_id
                                            session.add(creative)
                                            platform_creative_ids.append(uploaded_status.creative_id)
                                            logger.info(
                                                f"Updated creative {creative_id} with platform_creative_id={uploaded_status.creative_id}"
                                            )
                                        elif creative.data.get("platform_creative_id"):
                                            logger.info(
                                                f"Preserving existing platform_creative_id={creative.data.get('platform_creative_id')} "
                                                f"for creative {creative_id}, not overwriting with upload result"
                                            )
                                            platform_creative_ids.append(creative.data["platform_creative_id"])
                                except ToolError:
                                    # Re-raise ToolError - validation failures should fail the entire operation
                                    raise
                                except Exception as upload_error:
                                    # Other exceptions (network errors, etc.) - log and fail
                                    logger.error(f"Failed to upload creative {creative_id} to GAM: {upload_error}")
                                    raise ToolError(
                                        "CREATIVE_UPLOAD_FAILED",
                                        f"Failed to upload creative {creative_id} to GAM: {str(upload_error)}",
                                    ) from upload_error

                            # Create database assignment
                            assignment_id = f"assign_{uuid.uuid4().hex[:12]}"
                            assignment = DBAssignment(
                                assignment_id=assignment_id,
                                tenant_id=tenant["tenant_id"],
                                media_buy_id=response.media_buy_id,
                                package_id=response_package_id,
                                creative_id=creative_id,
                            )
                            session.add(assignment)

                        session.commit()

                        # Associate creatives with line items in ad server immediately
                        if platform_line_item_id and platform_creative_ids:
                            try:
                                logger.info(
                                    f"[cyan]Associating {len(platform_creative_ids)} pre-synced creatives with line item {platform_line_item_id}[/cyan]"
                                )
                                association_results = adapter.associate_creatives(
                                    [platform_line_item_id], platform_creative_ids
                                )

                                # Log results
                                for result in association_results:
                                    if result.get("status") == "success":
                                        logger.info(
                                            f"  ✓ Associated creative {result['creative_id']} with line item {result['line_item_id']}"
                                        )
                                    else:
                                        logger.info(
                                            f"  ✗ Failed to associate creative {result['creative_id']}: {result.get('error', 'Unknown error')}"
                                        )
                            except Exception as e:
                                logger.error(
                                    f"Failed to associate creatives with line item {platform_line_item_id}: {e}"
                                )
                        elif platform_creative_ids:
                            logger.warning(
                                f"Package {response_package_id} has {len(platform_creative_ids)} creatives but no platform_line_item_id from adapter. "
                                f"Creatives will need to be associated via sync_creatives."
                            )

        # Handle creatives if provided
        creative_statuses: dict[str, CreativeApprovalStatus] = {}
        if req.creatives:
            # Convert Creative objects to format expected by adapter
            assets = []
            for creative in req.creatives:
                try:
                    # Ensure product_ids is a list, not None
                    product_ids_list = req.product_ids if req.product_ids else []
                    asset = _convert_creative_to_adapter_asset(creative, product_ids_list)
                    assets.append(asset)
                except Exception as e:
                    logger.error(f"Error converting creative {creative.creative_id}: {e}")
                    # Add a failed status for this creative
                    creative_statuses[creative.creative_id] = CreativeApprovalStatus(
                        creative_id=creative.creative_id, status="rejected", detail=f"Conversion error: {str(e)}"
                    )
                    continue
            statuses = adapter.add_creative_assets(response.media_buy_id, assets, datetime.now())

            # Check if manual approval is required for creatives
            require_creative_approval = manual_approval_required and "add_creative_assets" in manual_approval_operations

            for status in statuses:
                # Skip statuses without creative_id (shouldn't happen but defensive)
                if status.creative_id:
                    # Override status to pending_review if manual approval is required for creatives
                    if require_creative_approval:
                        final_status: str = "pending_review"
                        detail = "Creative requires manual approval"
                    else:
                        final_status = "approved" if status.status == "approved" else "pending_review"
                        detail = "Creative submitted to ad server"

                    creative_statuses[status.creative_id] = CreativeApprovalStatus(
                        creative_id=status.creative_id,
                        status=final_status,  # type: ignore[arg-type]
                        detail=detail,
                    )

        # Build packages list for response (AdCP v2.4 format)
        # Per AdCP spec, create-media-buy-response Package only includes:
        # - buyer_ref (required): Buyer's reference identifier
        # - package_id (required): Publisher's unique identifier
        response_packages = []

        # Get adapter response packages (have package_ids)
        adapter_packages = response.packages if response.packages else []

        for i, package in enumerate(req.packages):
            # Get package_id from adapter response
            if i < len(adapter_packages):
                # adapter_packages may be Package Pydantic objects (adcp v1.2.1) or dicts
                response_package = adapter_packages[i]
                if hasattr(response_package, "model_dump"):
                    response_package_dict = response_package.model_dump(exclude_none=True, mode="python")
                else:
                    response_package_dict = response_package if isinstance(response_package, dict) else {}

                adapter_package_id = response_package_dict.get("package_id")
            else:
                # Fallback if adapter didn't return enough packages
                logger.warning(f"Adapter returned fewer packages than request. Using request package {i}")
                adapter_package_id = None

            # Validate that adapter returned package_id
            if not adapter_package_id:
                error_msg = f"Adapter did not return package_id for package {i}. Cannot build response."
                logger.error(error_msg)
                raise ValueError(error_msg)

            # Build minimal response package per AdCP spec
            response_packages.append(
                {
                    "buyer_ref": package.buyer_ref,
                    "package_id": adapter_package_id,
                }
            )

        # Ensure buyer_ref is set (defensive check)
        buyer_ref_value = req.buyer_ref if req.buyer_ref else buyer_ref
        if not buyer_ref_value:
            logger.error(f"🚨 buyer_ref is missing! req.buyer_ref={req.buyer_ref}, buyer_ref={buyer_ref}")
            buyer_ref_value = f"missing-{response.media_buy_id}"

        # Create AdCP response (protocol fields like status are added by ProtocolEnvelope wrapper)
        # NOTE: response_packages is a list of dicts, not Pydantic objects
        # The AdCP library will validate and convert these dicts to Package objects internally
        # When the response is serialized (via .model_dump()), they'll be converted back to dicts
        adcp_response = CreateMediaBuySuccess(
            buyer_ref=buyer_ref_value,
            media_buy_id=response.media_buy_id,
            packages=response_packages,  # type: ignore[arg-type]
            creative_deadline=response.creative_deadline,
            context=req.context,
        )

        # Log activity
        # Activity logging imported at module level

        log_tool_activity(ctx, "create_media_buy", request_start_time)

        # Also log specific media buy activity
        try:
            principal_name = "Unknown"
            with get_db_session() as session:
                principal_stmt = select(ModelPrincipal).filter_by(
                    principal_id=principal_id, tenant_id=tenant["tenant_id"]
                )
                principal_db = session.scalars(principal_stmt).first()
                if principal_db:
                    principal_name = principal_db.name

            # Calculate duration using new datetime fields (resolved from 'asap' if needed)
            duration_days = (end_time_val - start_time_val).days + 1

            activity_feed.log_media_buy(
                tenant_id=tenant["tenant_id"],
                principal_name=principal_name,
                media_buy_id=response.media_buy_id,
                budget=total_budget,  # Extract total budget
                duration_days=duration_days,
                action="created",
            )
        except Exception as e:
            # Activity feed logging is non-critical, but we should log the failure
            logger.warning(f"Failed to log media buy creation to activity feed: {e}")

        # Apply testing hooks to response with campaign information (resolved from 'asap' if needed)
        campaign_info = {"start_date": start_time, "end_date": end_time, "total_budget": total_budget}

        response_data = (
            adcp_response.model_dump_internal()
            if hasattr(adcp_response, "model_dump_internal")
            else adcp_response.model_dump()
        )

        response_data = apply_testing_hooks(response_data, testing_ctx, "create_media_buy", campaign_info)

        # Reconstruct response from modified data
        # Filter out testing hook fields that aren't part of CreateMediaBuyResponse schema
        # Domain fields only (status/adcp_version are protocol fields, added by ProtocolEnvelope)
        # Success branch fields only (no errors field in success path)
        valid_fields = {
            "buyer_ref",
            "media_buy_id",
            "creative_deadline",
            "packages",
            "workflow_step_id",
        }
        filtered_data = {k: v for k, v in response_data.items() if k in valid_fields}

        # Ensure required fields are present (validator compliance)
        if "buyer_ref" not in filtered_data:
            filtered_data["buyer_ref"] = buyer_ref_value

        # Use explicit fields for validator (instead of **kwargs)
        # This is the success path - use Success model
        modified_response = CreateMediaBuySuccess(
            buyer_ref=filtered_data["buyer_ref"],
            media_buy_id=filtered_data["media_buy_id"],
            packages=filtered_data["packages"],
            creative_deadline=filtered_data.get("creative_deadline"),
            context=req.context,
        )

        # Mark workflow step as completed on success
        ctx_manager.update_workflow_step(step.step_id, status="completed")

        # Send Slack notification for successful media buy creation
        try:
            # Get principal name for notification (reuse from activity logging above)
            principal_name = "Unknown"
            with get_db_session() as session:
                principal_stmt2 = select(ModelPrincipal).filter_by(
                    principal_id=principal_id, tenant_id=tenant["tenant_id"]
                )
                principal_db = session.scalars(principal_stmt2).first()
                if principal_db:
                    principal_name = principal_db.name

            # Build notifier config from tenant fields
            notifier_config = {
                "features": {
                    "slack_webhook_url": tenant.get("slack_webhook_url"),
                    "slack_audit_webhook_url": tenant.get("slack_audit_webhook_url"),
                }
            }
            slack_notifier = get_slack_notifier(notifier_config)

            # Create success notification details
            success_details = {
                "total_budget": total_budget,
                "po_number": req.po_number,
                "start_time": start_time.isoformat(),  # Resolved from 'asap' if needed
                "end_time": end_time.isoformat(),
                "product_ids": req.get_product_ids(),
                "duration_days": (end_time_val - start_time_val).days + 1,
                "packages_count": len(response_packages) if response_packages else 0,
                "creatives_count": len(req.creatives) if req.creatives else 0,
                "workflow_step_id": step.step_id,
            }

            slack_notifier.notify_media_buy_event(
                event_type="created",
                media_buy_id=response.media_buy_id,
                principal_name=principal_name,
                details=success_details,
                tenant_name=tenant.get("name", "Unknown"),
                tenant_id=tenant.get("tenant_id"),
                success=True,
            )

            logger.info(f"🎉 Sent success notification to Slack for media buy {response.media_buy_id}")
        except Exception as e:
            logger.warning(f"⚠️ Failed to send success Slack notification: {e}")

        # Log to audit logs for business activity feed
        audit_logger = get_audit_logger("AdCP", tenant["tenant_id"])
        audit_logger.log_operation(
            operation="create_media_buy",
            principal_name=principal_name,
            principal_id=principal_id or "anonymous",
            adapter_id="mcp_server",
            success=True,
            details={
                "media_buy_id": response.media_buy_id,
                "total_budget": total_budget,
                "po_number": req.po_number,
                "duration_days": (end_time_val - start_time_val).days + 1,  # Resolved from 'asap' if needed
                "product_count": len(req.get_product_ids()),
                "packages_count": len(response_packages) if response_packages else 0,
            },
        )

        return modified_response  # type: ignore[return-value]

    except Exception as e:
        # Update workflow step as failed on any error during execution
        if step:
            ctx_manager.update_workflow_step(step.step_id, status="failed", error_message=str(e))

        # Send Slack notification for failed media buy creation
        try:
            # Get principal name for notification
            principal_name = "Unknown"
            if principal:
                principal_name = principal.name

            # Build notifier config from tenant fields
            notifier_config = {
                "features": {
                    "slack_webhook_url": tenant.get("slack_webhook_url"),
                    "slack_audit_webhook_url": tenant.get("slack_audit_webhook_url"),
                }
            }
            slack_notifier = get_slack_notifier(notifier_config)

            # Create failure notification details
            failure_details = {
                "total_budget": total_budget if "total_budget" in locals() else 0,
                "po_number": req.po_number,
                "start_time": (
                    start_time.isoformat() if "start_time" in locals() else None
                ),  # Resolved from 'asap' if needed
                "end_time": end_time.isoformat() if "end_time" in locals() else None,
                "product_ids": req.get_product_ids(),
                "error_message": str(e),
                "workflow_step_id": step.step_id if step else "unknown",
            }

            slack_notifier.notify_media_buy_event(
                event_type="failed",
                media_buy_id=None,
                principal_name=principal_name,
                details=failure_details,
                tenant_name=tenant.get("name", "Unknown"),
                tenant_id=tenant.get("tenant_id"),
                success=False,
                error_message=str(e),
            )

            logger.error(f"❌ Sent failure notification to Slack: {str(e)}")
        except Exception as notify_error:
            logger.warning(f"⚠️ Failed to send failure Slack notification: {notify_error}")

        # Log to audit logs for failed operation
        try:
            audit_logger = get_audit_logger("AdCP", tenant["tenant_id"])
            audit_logger.log_operation(
                operation="create_media_buy",
                principal_name=principal.name if principal else "unknown",
                principal_id=principal_id or "anonymous",
                adapter_id="mcp_server",
                success=False,
                error=str(e),
                details={
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                    "po_number": req.po_number if req else None,
                    "total_budget": total_budget if "total_budget" in locals() else 0,
                },
            )
        except Exception as audit_error:
            # Audit logging failure is non-critical, but we should log it
            logger.warning(f"Failed to log failed media buy creation to audit: {audit_error}")

        raise ToolError("MEDIA_BUY_CREATION_ERROR", f"Failed to create media buy: {str(e)}")


async def create_media_buy(
    buyer_ref: str,
    brand_manifest: Any,  # BrandManifest | str - REQUIRED per AdCP v2.2.0 spec
    packages: list[dict[str, Any]],  # REQUIRED per AdCP spec - Package objects as dicts with all fields
    start_time: Any,  # datetime | Literal["asap"] | str - REQUIRED per AdCP spec
    end_time: Any,  # datetime | str - REQUIRED per AdCP spec
    budget: Any | None = None,  # DEPRECATED: Budget is package-level only per AdCP v2.2.0
    po_number: str | None = None,
    product_ids: list[str] | None = None,  # Legacy format conversion
    start_date: Any | None = None,  # Legacy format conversion
    end_date: Any | None = None,  # Legacy format conversion
    total_budget: float | None = None,  # Legacy format conversion
    targeting_overlay: dict[str, Any] | None = None,
    pacing: str = "even",
    daily_budget: float | None = None,
    creatives: list[Any] | None = None,
    reporting_webhook: dict[str, Any] | None = None,
    required_axe_signals: list[str] | None = None,
    enable_creative_macro: bool = False,
    strategy_id: str | None = None,
    push_notification_config: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,  # payload-level context
    webhook_url: str | None = None,
    ctx: Context | ToolContext | None = None,
):
    """Create a media buy with the specified parameters.

    MCP tool wrapper that delegates to the shared implementation.

    Args:
        buyer_ref: Buyer reference for tracking (REQUIRED per AdCP spec)
        brand_manifest: Brand information manifest - inline object or URL string (REQUIRED per AdCP v2.2.0 spec)
        packages: Array of packages with products and budgets (REQUIRED - budgets are at package level per AdCP v2.2.0)
        start_time: Campaign start time ISO 8601 or 'asap' (REQUIRED)
        end_time: Campaign end time ISO 8601 (REQUIRED)
        budget: DEPRECATED - Budget is package-level only per AdCP v2.2.0 (ignored if provided)
        po_number: Purchase order number (optional)
        product_ids: Legacy: Product IDs (converted to packages)
        start_date: Legacy: Start date (converted to start_time)
        end_date: Legacy: End date (converted to end_time)
        total_budget: Legacy: Total budget (converted to package budgets)
        targeting_overlay: Targeting overlay configuration
        pacing: Pacing strategy (even, asap, daily_budget)
        daily_budget: Daily_budget limit
        creatives: Creative assets for the campaign
        reporting_webhook: Webhook configuration for automated reporting delivery
        required_axe_signals: Required targeting signals
        enable_creative_macro: Enable AXE to provide creative_macro signal
        strategy_id: Optional strategy ID for linking operations
        push_notification_config: Push notification config dict with url, authentication (AdCP spec)
        context: Application level context per adcp spec
        ctx:  FastMCP context (automatically provided) (automatically provided)

    Returns:
        ToolResult with CreateMediaBuyResponse data
    """
    response = await _create_media_buy_impl(
        buyer_ref=buyer_ref,
        brand_manifest=brand_manifest,
        po_number=po_number,
        packages=packages,
        start_time=start_time,
        end_time=end_time,
        budget=budget,
        product_ids=product_ids,
        start_date=start_date,
        end_date=end_date,
        total_budget=total_budget,
        targeting_overlay=targeting_overlay,  # type: ignore[arg-type]
        pacing=pacing,  # type: ignore[arg-type]
        daily_budget=daily_budget,
        creatives=creatives,
        reporting_webhook=reporting_webhook,
        required_axe_signals=required_axe_signals,
        enable_creative_macro=enable_creative_macro,
        strategy_id=strategy_id,
        push_notification_config=push_notification_config,
        context=context,
        ctx=ctx,
    )
    return ToolResult(content=str(response), structured_content=response.model_dump())


async def create_media_buy_raw(
    buyer_ref: str,
    brand_manifest: Any,  # BrandManifest | str - REQUIRED per AdCP v2.2.0 spec
    packages: list[Any],  # REQUIRED per AdCP spec
    start_time: Any,  # datetime | Literal["asap"] | str - REQUIRED per AdCP spec
    end_time: Any,  # datetime | str - REQUIRED per AdCP spec
    budget: Any | None = None,  # DEPRECATED: Budget is package-level only per AdCP v2.2.0
    po_number: str | None = None,
    product_ids: list[str] | None = None,  # Legacy format conversion
    total_budget: float | None = None,  # Legacy format conversion
    start_date: Any | None = None,  # Legacy format conversion
    end_date: Any | None = None,  # Legacy format conversion
    targeting_overlay: dict[str, Any] | None = None,
    pacing: str = "even",
    daily_budget: float | None = None,
    creatives: list[Any] | None = None,
    reporting_webhook: dict[str, Any] | None = None,
    required_axe_signals: list[str] | None = None,
    enable_creative_macro: bool = False,
    strategy_id: str | None = None,
    push_notification_config: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,  # Application level context per adcp spec
    ctx: Context | ToolContext | None = None,
):
    """Create a new media buy with specified parameters (raw function for A2A server use).

    Delegates to the shared implementation.

    Args:
        buyer_ref: Buyer reference identifier (REQUIRED per AdCP spec)
        brand_manifest: Brand information manifest - inline object or URL string (REQUIRED per AdCP v2.2.0 spec)
        packages: List of media packages (REQUIRED)
        start_time: Campaign start time ISO 8601 or 'asap' (REQUIRED)
        end_time: Campaign end time ISO 8601 (REQUIRED)
        budget: DEPRECATED - Budget is at package level only per AdCP v2.2.0 (ignored if provided)
        po_number: Purchase order number (optional)
        product_ids: Legacy: Product IDs (converted to packages)
        total_budget: Legacy: Total budget (converted to Budget object)
        start_date: Legacy: Start date (converted to start_time)
        end_date: Legacy: End date (converted to end_time)
        targeting_overlay: Additional targeting parameters
        pacing: Pacing strategy
        daily_budget: Daily budget limit
        creatives: Creative assets
        reporting_webhook: Webhook configuration for automated reporting delivery
        required_axe_signals: Required signals
        enable_creative_macro: Enable creative macro
        strategy_id: Strategy ID
        push_notification_config: Push notification config for status updates
        ctx:  FastMCP context (automatically provided) (automatically provided)

    Returns:
        CreateMediaBuyResponse with media buy details
    """
    return await _create_media_buy_impl(
        buyer_ref=buyer_ref,
        brand_manifest=brand_manifest,
        po_number=po_number,
        packages=packages,
        start_time=start_time,
        end_time=end_time,
        budget=budget,
        product_ids=product_ids,
        start_date=start_date,
        end_date=end_date,
        total_budget=total_budget,
        targeting_overlay=targeting_overlay,  # type: ignore[arg-type]
        pacing=pacing,  # type: ignore[arg-type]
        daily_budget=daily_budget,
        creatives=creatives,
        reporting_webhook=reporting_webhook,
        required_axe_signals=required_axe_signals,
        enable_creative_macro=enable_creative_macro,
        strategy_id=strategy_id,
        push_notification_config=push_notification_config,
        context=context,
        ctx=ctx,
    )


# Unified update tools
