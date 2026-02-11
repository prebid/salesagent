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
from typing import Any, Literal, TypedDict, cast
from urllib.parse import urlparse

from adcp import BrandManifest, PushNotificationConfig
from adcp.types import GeneratedTaskStatus as AdcpTaskStatus
from adcp.types import MediaBuyStatus
from adcp.types.generated_poc.core.context import ContextObject
from adcp.types.generated_poc.core.creative_asset import CreativeAsset
from adcp.types.generated_poc.core.format import Assets
from adcp.types.generated_poc.core.reporting_webhook import ReportingWebhook
from adcp.types.generated_poc.core.targeting import TargetingOverlay
from adcp.types.generated_poc.media_buy.package_request import PackageRequest as AdcpPackageRequest
from adcp.utils.format_assets import get_individual_assets, has_assets
from fastmcp.exceptions import ToolError
from fastmcp.server.context import Context
from fastmcp.tools.tool import ToolResult
from pydantic import ValidationError
from rich.console import Console


class PackageAssignmentDict(TypedDict):
    """Internal dict format for passing package assignments to adapters."""

    package_id: str
    weight: int


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
from src.core.schema_helpers import to_context_object, to_reporting_webhook
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


# NOTE: _sanitize_package_status() removed in adcp 2.12.0 migration
# Package.status enum was replaced with Package.paused boolean field
# See: adcp 2.12.0 changelog


def _get_creative_ids(package: AdcpPackageRequest | PackageRequest | Package) -> list[str] | None:
    """Safely get creative_ids from a package (backward compatibility).

    The creative_ids field is a local extension added to PackageRequest for
    backward compatibility. It may not exist on library types, so we use
    getattr to safely access it.

    Args:
        package: Package or PackageRequest object

    Returns:
        List of creative IDs if present, None otherwise
    """
    return getattr(package, "creative_ids", None)


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

    All production creatives now use AdCP v2.4+ format with data.assets[asset_id]
    containing typed asset objects per the creative format specification.

    Extraction priority:
    1. Format spec assets (using get_format_assets utility for backward compat - AdCP 2.6 format spec)
    2. First available asset with URL (fallback if no format spec)

    Args:
        creative_data: Creative data dict from database
        format_spec: Format specification with assets (or deprecated assets_required)

    Returns:
        Tuple of (url, width, height). Values are None if not found.

    Note:
        - URL extracted from asset types: image, video, url
        - Dimensions extracted from asset types: image, video
        - Type validation: width/height must be int or coercible to int
        - Uses adcp.utils.get_individual_assets() for backward compatibility with assets_required
    """
    # Extract URL from assets using format specification
    url = None
    if creative_data.get("assets") and format_spec and has_assets(format_spec):
        # Use format spec to find the correct asset_id for image/video/url assets
        # Note: We only support individual assets here (not repeatable groups).
        # This matches previous behavior - repeatable groups were never supported in this function.
        for asset_spec in get_individual_assets(format_spec):
            # Type guard: get_individual_assets only returns Assets, not Assets5 (repeatable groups)
            if not isinstance(asset_spec, Assets):
                continue
            asset_type = str(asset_spec.asset_type).lower()
            if asset_type in ["image", "video", "url"]:
                asset_id = asset_spec.asset_id
                if asset_id in creative_data["assets"]:
                    asset_obj = creative_data["assets"][asset_id]
                    if isinstance(asset_obj, dict) and asset_obj.get("url"):
                        url = asset_obj["url"]
                        break

    # Extract dimensions from assets using format specification
    width = None
    height = None
    if creative_data.get("assets") and format_spec and has_assets(format_spec):
        # Use format spec to find the correct asset_id for image/video assets
        # Note: We only support individual assets here (not repeatable groups).
        # This matches previous behavior - repeatable groups were never supported in this function.
        for asset_spec in get_individual_assets(format_spec):
            # Type guard: get_individual_assets only returns Assets, not Assets5 (repeatable groups)
            if not isinstance(asset_spec, Assets):
                continue
            asset_type = str(asset_spec.asset_type).lower()
            if asset_type in ["image", "video"]:
                asset_id = asset_spec.asset_id
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

    # Fallback: If format spec didn't work, iterate through all assets
    if not url or not width or not height:
        if creative_data.get("assets"):
            for _asset_id, asset_obj in creative_data["assets"].items():
                if isinstance(asset_obj, dict):
                    # Extract URL if not found yet
                    if not url and asset_obj.get("url"):
                        url = asset_obj["url"]

                    # Extract dimensions if not found yet
                    if not width or not height:
                        raw_width = asset_obj.get("width")
                        raw_height = asset_obj.get("height")
                        if raw_width is not None and not width:
                            try:
                                width = int(raw_width)
                            except (ValueError, TypeError):
                                pass
                        if raw_height is not None and not height:
                            try:
                                height = int(raw_height)
                            except (ValueError, TypeError):
                                pass

                    # Stop if we found everything
                    if url and width and height:
                        break

    # Last resort: Check top-level fields
    if not url:
        url = creative_data.get("url")
    if not width:
        try:
            width = int(creative_data["width"]) if creative_data.get("width") else None
        except (ValueError, TypeError):
            pass
    if not height:
        try:
            height = int(creative_data["height"]) if creative_data.get("height") else None
        except (ValueError, TypeError):
            pass

    return url, width, height


def _get_format_spec_sync(agent_url: str, format_id: str) -> Any | None:
    """Get format specification synchronously using asyncio.run().

    This helper function wraps the async registry.get_format() call to make it
    usable in synchronous contexts. The registry uses in-memory cache (30min TTL)
    and falls back to the creative agent if not cached.

    Args:
        agent_url: Creative agent URL
        format_id: Format ID to fetch

    Returns:
        Format specification object or None if not found
    """
    import asyncio

    from src.core.creative_agent_registry import get_creative_agent_registry

    registry = get_creative_agent_registry()

    try:
        return asyncio.run(registry.get_format(agent_url, format_id))
    except Exception as e:
        logger.warning(f"Could not fetch format {format_id} from {agent_url}: {e}")
        return None


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
    from sqlalchemy import select

    from src.core.database.database_session import get_db_session
    from src.core.database.models import Creative as DBCreative

    # Collect all creative IDs from all packages
    all_creative_ids = set()
    for package in packages:
        pkg_creative_ids = _get_creative_ids(package)
        if pkg_creative_ids:
            all_creative_ids.update(pkg_creative_ids)

    if not all_creative_ids:
        # No creatives to validate
        return

    # Fetch all creatives in one query
    with get_db_session() as session:
        stmt = select(DBCreative).where(
            DBCreative.tenant_id == tenant_id, DBCreative.creative_id.in_(list(all_creative_ids))
        )
        creatives_list = list(session.scalars(stmt).all())

    # Validate each creative has required fields
    validation_errors = []
    for creative in creatives_list:
        creative_data = creative.data or {}

        # Get format specification from creative agent (uses in-memory cache with 30min TTL)
        format_spec = None
        if creative.format:
            format_spec = _get_format_spec_sync(creative.agent_url, str(creative.format))

        # Fail validation if format spec not found (no skipping!)
        if not format_spec:
            validation_errors.append(
                f"Creative {creative.creative_id} has unknown format '{creative.format}' "
                f"from agent {creative.agent_url}. Format must be registered with the creative agent."
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
        response = adapter.create_media_buy(
            request, cast(list[MediaPackage], packages), start_time, end_time, package_pricing_info
        )

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
                    # response.packages are now always Package objects
                    logger.info(f"[ADAPTER] Response package {i}: {pkg.package_id}")
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
                # Strip package_id from packages - it was added for UI tracking but isn't
                # part of the AdCP CreateMediaBuyRequest schema (package_id is assigned by system)
                raw_request_data = dict(media_buy.raw_request)
                if "packages" in raw_request_data:
                    for pkg in raw_request_data["packages"]:
                        pkg.pop("package_id", None)

                request = CreateMediaBuyRequest(**raw_request_data)
                # Mark this request as already approved to skip adapter's approval workflow
                setattr(request, "_already_approved", True)  # noqa: B010
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
                    # package_id is stored on the MediaPackage model, not in package_config (AdCP spec)
                    package_id = db_pkg.package_id
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
                    # adcp 2.14.0+ uses RootModel wrapper - access via .root
                    pricing_option_inner = None
                    if product.pricing_options:
                        # Try to match pricing_model from package_config if present
                        pkg_pricing_model = package_config.get("pricing_model")
                        if pkg_pricing_model:
                            for po in product.pricing_options:
                                po_inner = getattr(po, "root", po)
                                if po_inner.pricing_model == pkg_pricing_model:
                                    pricing_option_inner = po_inner
                                    break
                        if not pricing_option_inner:
                            # Fall back to first pricing option
                            first_po = product.pricing_options[0]
                            pricing_option_inner = getattr(first_po, "root", first_po)

                    if not pricing_option_inner:
                        error_msg = f"Product {product_id} has no pricing options"
                        logger.error(f"[APPROVAL] {error_msg}")
                        return False, error_msg

                    # Calculate CPM and impressions (convert Decimal to float for math operations)
                    cpm = float(pricing_option_inner.rate) if pricing_option_inner.rate else 0.0
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
                            "pricing_model": pricing_option_inner.pricing_model,
                            "currency": pricing_option_inner.currency,
                            "is_fixed": pricing_option_inner.is_fixed,
                            "rate": float(pricing_option_inner.rate) if pricing_option_inner.rate else None,
                            "bid_price": None,
                        }

                    # Get targeting_overlay from package_config if present
                    # Fallback to "targeting" key for data written before salesagent-dzr fix
                    targeting_overlay = None
                    targeting_raw = package_config.get("targeting_overlay") or package_config.get("targeting")
                    if targeting_raw:
                        from src.core.schemas import Targeting

                        targeting_overlay = Targeting(**targeting_raw)

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
                                # IMPORTANT: Include width, height, duration_ms from database (parameterized formats)
                                # Convert to correct types (database may return int or need conversion)
                                fmt_width = fmt.get("width")
                                fmt_height = fmt.get("height")
                                fmt_duration_ms = fmt.get("duration_ms")

                                format_ids_list.append(
                                    FormatIdType(
                                        agent_url=make_url(agent_url),
                                        id=format_id,
                                        width=int(fmt_width) if fmt_width is not None else None,
                                        height=int(fmt_height) if fmt_height is not None else None,
                                        duration_ms=float(fmt_duration_ms) if fmt_duration_ms is not None else None,
                                    )
                                )

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
                        f"[APPROVAL] Package {package_id}: Successfully converted all {len(format_ids_list)} formats"
                    )

                    media_package = MediaPackage(
                        package_id=package_id,
                        name=package_config.get("name") or product.name,
                        delivery_type=delivery_type_str,
                        cpm=cpm,
                        impressions=impressions,
                        format_ids=cast(list[Any], format_ids_list),
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

            # Validate required datetime fields
            if not start_time or not end_time:
                error_msg = f"Media buy {media_buy_id} missing required start_time or end_time"
                logger.error(f"[APPROVAL] {error_msg}")
                return False, error_msg

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
        _validate_creatives_before_adapter_call(cast(list[Package], packages), tenant_id)

        # Execute adapter creation (outside session to avoid conflicts)
        # Type ignore needed because we're passing MediaPackage list as Package list (adapter compatibility)
        response = _execute_adapter_media_buy_creation(
            request,
            cast(list[Package], packages),
            start_time,
            end_time,
            package_pricing_info,
            principal,
            testing_ctx,
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

                # Group packages by creative (REVERSED - key by creative_id, value is list of package assignments)
                # This ensures each creative is uploaded ONCE with ALL its package assignments
                # Each assignment includes weight for proper rotation in adapters (AdCP 2.5)
                packages_by_creative: dict[str, list[PackageAssignmentDict]] = {}
                for assignment in assignments:
                    if assignment.creative_id not in packages_by_creative:
                        packages_by_creative[assignment.creative_id] = []
                    packages_by_creative[assignment.creative_id].append(
                        {
                            "package_id": assignment.package_id,
                            "weight": assignment.weight,  # From CreativeAssignment.weight (default 100)
                        }
                    )

                # Load all creatives
                all_creative_ids = list(packages_by_creative.keys())
                stmt_creatives = select(CreativeModel).filter(CreativeModel.creative_id.in_(all_creative_ids))
                creatives = session.scalars(stmt_creatives).all()

                # Create creative map
                creative_map = {c.creative_id: c for c in creatives}

                # Build assets list for adapter and collect all validation errors
                assets = []
                all_validation_errors = []
                for creative_id, package_assignment_list in packages_by_creative.items():
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
                        # Include full assignment info with weights for adapter creative rotation (AdCP 2.5)
                        "package_assignments": package_assignment_list,
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
    package: Package | PackageRequest | AdcpPackageRequest,
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
    # Use getattr for legacy pricing_model field (deprecated - use pricing_option_id instead)
    legacy_pricing_model = getattr(package, "pricing_model", None)
    logger.debug(
        f"[PRICING] Package {package.product_id}: pricing_option={package.pricing_option_id}, "
        f"model={legacy_pricing_model}, bid_price={package.bid_price}, budget={package.budget}"
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
    pricing_model_fallback = getattr(package, "pricing_model", None)  # Legacy field

    # Helper to unwrap RootModel - adcp 2.14.0+ uses RootModel wrapper
    def unwrap_option(opt: Any) -> Any:
        return getattr(opt, "root", opt)

    # If neither specified, use first pricing option from product
    if not pricing_option_id and not pricing_model_fallback:
        first_option = unwrap_option(product.pricing_options[0])
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
        opt_inner = unwrap_option(option)
        # Construct pricing_option_id in same format as get_products returns
        # Format: {pricing_model}_{currency}_{fixed|auction}
        fixed_str = "fixed" if opt_inner.is_fixed else "auction"
        option_id = f"{opt_inner.pricing_model}_{opt_inner.currency.lower()}_{fixed_str}"

        # Try matching by pricing_option_id first (AdCP spec)
        if pricing_option_id and pricing_option_id.lower() == option_id.lower():
            selected_option = opt_inner
            break

        # Fallback: match by pricing_model (legacy)
        if pricing_model_fallback and opt_inner.pricing_model == pricing_model_fallback.value:
            # If campaign currency specified, must match
            if campaign_currency and opt_inner.currency != campaign_currency:
                continue
            selected_option = opt_inner
            break

    if not selected_option:
        # Show available options in same format as matching logic expects
        available_options = [
            f"{unwrap_option(opt).pricing_model}_{unwrap_option(opt).currency.lower()}_{'fixed' if unwrap_option(opt).is_fixed else 'auction'} ({unwrap_option(opt).pricing_model} - {unwrap_option(opt).currency})"
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
                f"Package requires bid_price for auction-based {selected_option.pricing_model} pricing. "
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
                f"Bid price {package.bid_price} is below floor price {floor_price} for {selected_option.pricing_model} pricing",
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
                f"{selected_option.min_spend_per_package} {selected_option.currency} for {selected_option.pricing_model}",
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
    packages: list[Any],  # REQUIRED per AdCP spec (each has targeting_overlay)
    start_time: Any,  # datetime | Literal["asap"] | str - REQUIRED per AdCP spec
    end_time: Any,  # datetime | str - REQUIRED per AdCP spec
    po_number: str | None = None,
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
) -> tuple[CreateMediaBuySuccess | CreateMediaBuyError, AdcpTaskStatus]:
    """Create a media buy with the specified parameters.

    Args:
        buyer_ref: Buyer reference for tracking (REQUIRED per AdCP spec)
        brand_manifest: Brand information manifest - inline object or URL string (REQUIRED per AdCP v2.2.0 spec)
        packages: Array of packages with products, budgets, and targeting_overlay (REQUIRED)
        start_time: Campaign start time ISO 8601 or 'asap' (REQUIRED)
        end_time: Campaign end time ISO 8601 (REQUIRED)
        po_number: Purchase order number (optional)
        pacing: Pacing strategy (even, asap, daily_budget)
        daily_budget: Daily budget limit
        creatives: Creative assets for the campaign
        reporting_webhook: Webhook configuration for automated reporting delivery
        required_axe_signals: Required targeting signals
        enable_creative_macro: Enable AXE to provide creative_macro signal
        strategy_id: Optional strategy ID for linking operations
        push_notification_config: Push notification config for status updates (MCP/A2A)
        context: Application level context per adcp spec
        ctx: FastMCP context (automatically provided)

    Returns:
        CreateMediaBuyResponse with media buy details
    """
    request_start_time = time.time()

    # Warn if unsupported reporting_webhook frequency is requested
    if reporting_webhook and isinstance(reporting_webhook, dict):
        raw_freq = str(reporting_webhook.get("frequency") or "daily").lower()
        if raw_freq != "daily":
            logger.warning(
                "CreateMediaBuy requested reporting webhook frequency '%s' for buyer_ref '%s', "
                "but only 'daily' frequency is currently supported. "
                "Hourly and monthly reporting will be ignored until implemented.",
                raw_freq,
                buyer_ref,
            )

    # Parse ISO 8601 strings to datetime if needed
    effective_start_time = start_time
    effective_end_time = end_time
    if isinstance(effective_start_time, str) and effective_start_time != "asap":
        effective_start_time = datetime.fromisoformat(effective_start_time)
    if isinstance(effective_end_time, str):
        effective_end_time = datetime.fromisoformat(effective_end_time)

    # Create spec-compliant request object
    # Non-spec fields (pacing, daily_budget, creatives,
    # required_axe_signals, enable_creative_macro, strategy_id, push_notification_config)
    # are used directly below, not passed to the request
    try:
        req = CreateMediaBuyRequest(
            buyer_ref=buyer_ref,
            brand_manifest=brand_manifest,
            packages=packages,
            start_time=effective_start_time,
            end_time=effective_end_time,
            po_number=po_number,
            reporting_webhook=to_reporting_webhook(reporting_webhook),
            context=to_context_object(context),
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
        return (
            CreateMediaBuyError(
                errors=[Error(code="authentication_error", message=error_msg, details=None)],
                context=to_context_object(req.context),
            ),
            AdcpTaskStatus.failed,
        )

    # Context management and workflow step creation - create workflow step FIRST
    # Skip for dry_run mode (no side effects, no database writes)
    ctx_manager = get_context_manager()
    ctx_id = ctx.headers.get("x-context-id") if ctx and hasattr(ctx, "headers") else None
    persistent_ctx = None
    step = None

    if not testing_ctx.dry_run:
        # Create workflow step immediately for tracking all operations
        if not persistent_ctx:
            # Check if we have an existing context ID
            if ctx_id:
                persistent_ctx = ctx_manager.get_context(ctx_id)

            # Create new context if needed (principal already validated above)
            if not persistent_ctx:
                persistent_ctx = ctx_manager.create_context(tenant_id=tenant["tenant_id"], principal_id=principal_id)

        # Create workflow step for tracking this operation
        # Prepare request data with protocol detection
        request_data_for_workflow = req.model_dump(mode="json")

        # Store protocol type for webhook payload creation
        # ToolContext = A2A, Context (FastMCP) = MCP
        request_data_for_workflow["protocol"] = "a2a" if isinstance(ctx, ToolContext) else "mcp"

        # Store push_notification_config in workflow step request_data (same pattern as sync_creatives)
        if push_notification_config:
            request_data_for_workflow["push_notification_config"] = push_notification_config

        step = ctx_manager.create_workflow_step(
            context_id=persistent_ctx.context_id,
            step_type="media_buy_creation",
            owner="system",
            status="in_progress",
            tool_name="create_media_buy",
            request_data=request_data_for_workflow,
        )

        # Register push notification config if provided (MCP/A2A protocol support)
        # Skip for dry_run mode (no database writes)
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
        # Library may wrap start_time in StartTiming - unwrap if needed
        raw_start_time = req.start_time.root if hasattr(req.start_time, "root") else req.start_time
        if raw_start_time == "asap":
            computed_start_time: datetime = now
        else:
            # Ensure start_time is timezone-aware for comparison
            # Handle case where StartTiming.root is an ISO string (adcp 2.16.0+)
            if isinstance(raw_start_time, str):
                computed_start_time = datetime.fromisoformat(raw_start_time)
            elif isinstance(raw_start_time, datetime):
                computed_start_time = raw_start_time
            else:
                # StartTiming that wasn't unwrapped - this shouldn't happen but handle gracefully
                error_msg = f"Unexpected start_time type: {type(raw_start_time)}"
                raise ValueError(error_msg)
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

            # Validate all requested product_ids exist
            missing_product_ids = set(product_ids) - set(product_map.keys())
            if missing_product_ids:
                error_msg = f"Product(s) not found: {', '.join(sorted(missing_product_ids))}"
                raise ValueError(error_msg)

            # Resolve legacy pricing_option_id values to actual product pricing_option_ids
            # This happens when using the legacy product_ids parameter (auto-converted to packages)
            if req.packages:
                for package in req.packages:
                    if package.pricing_option_id == "legacy_conversion" and package.product_id in product_map:
                        product = product_map[package.product_id]
                        # Use the first pricing option from the product
                        if product.pricing_options and len(product.pricing_options) > 0:
                            # Use the generated pricing_option_id format from the product's first option
                            # Unwrap RootModel wrapper if present (adcp 2.14.0+ uses RootModel)
                            first_option = product.pricing_options[0]
                            first_option = getattr(first_option, "root", first_option)
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

                    # Helper to unwrap RootModel wrapper (adcp 2.14.0+ uses RootModel)
                    def unwrap_po(po: Any) -> Any:
                        return getattr(po, "root", po)

                    # Find the pricing option matching the package's pricing_model (legacy field)
                    first_package_pricing_model = getattr(first_package, "pricing_model", None)
                    if first_package_pricing_model and pricing_options:
                        matching_option = next(
                            (
                                unwrap_po(po)
                                for po in pricing_options
                                if unwrap_po(po).pricing_model == first_package_pricing_model
                            ),
                            None,
                        )
                        if matching_option:
                            request_currency = matching_option.currency

                    # If no pricing_model specified, use first pricing option's currency
                    if not request_currency and pricing_options:
                        request_currency = unwrap_po(pricing_options[0]).currency

            # Fallback to deprecated/legacy sources
            # Note: currency and budget fields no longer exist on CreateMediaBuyRequest per AdCP spec
            # They have been moved to package level. Check with hasattr/getattr for backward compat.
            legacy_currency = getattr(req, "currency", None)
            legacy_budget = getattr(req, "budget", None)
            if not request_currency and legacy_currency:
                # Deprecated field, but still supported for backward compatibility
                request_currency = legacy_currency
            elif not request_currency and legacy_budget:
                # Legacy: Extract currency from Budget object (if it's an object)
                if hasattr(legacy_budget, "currency"):
                    request_currency = legacy_budget.currency
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

            # Check if currency is supported by GAM network (if GAM is configured)
            # GAM only accepts: primary currency OR enabled secondary currencies
            from src.core.database.models import AdapterConfig

            adapter_config_stmt = select(AdapterConfig).where(AdapterConfig.tenant_id == tenant["tenant_id"])
            adapter_config = session.scalars(adapter_config_stmt).first()
            if adapter_config and adapter_config.gam_network_currency:
                # Build list of supported currencies: primary + any secondary
                supported_currencies = {adapter_config.gam_network_currency}
                if adapter_config.gam_secondary_currencies:
                    supported_currencies.update(adapter_config.gam_secondary_currencies)

                if request_currency not in supported_currencies:
                    error_msg = (
                        f"Currency {request_currency} is not supported by the GAM network. "
                        f"Supported currencies: {', '.join(sorted(supported_currencies))}. "
                        f"Contact the publisher to enable this currency in GAM."
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

        # Validate targeting doesn't use managed-only dimensions (targeting_overlay is at package level per AdCP spec)
        if req.packages:
            for pkg in req.packages:
                if hasattr(pkg, "targeting_overlay") and pkg.targeting_overlay:
                    from src.services.targeting_capabilities import (
                        validate_geo_overlap,
                        validate_overlay_targeting,
                        validate_unknown_targeting_fields,
                    )

                    # Reject unknown targeting fields (typos, bogus names) via model_extra
                    unknown_violations = validate_unknown_targeting_fields(pkg.targeting_overlay)

                    # Convert to dict for dimension-access validation
                    targeting_data: dict[str, Any] = (
                        pkg.targeting_overlay.model_dump(exclude_none=True)
                        if hasattr(pkg.targeting_overlay, "model_dump")
                        else dict(pkg.targeting_overlay)  # Fallback for dict-like objects
                    )
                    access_violations = validate_overlay_targeting(targeting_data)

                    # Reject same-value geo inclusion/exclusion overlap (AdCP SHOULD requirement)
                    geo_overlap_violations = validate_geo_overlap(targeting_data)

                    violations = unknown_violations + access_violations + geo_overlap_violations
                    if violations:
                        error_msg = f"Targeting validation failed: {'; '.join(violations)}"
                        raise ValueError(error_msg)

    except (ValueError, PermissionError) as e:
        # Update workflow step as failed (only if step exists - not created in dry_run mode)
        if step:
            ctx_manager.update_workflow_step(step.step_id, status="failed", error_message=str(e))

        # Return error response with failed status
        return (
            CreateMediaBuyError(
                errors=[Error(code="validation_error", message=str(e), details=None)],
                context=to_context_object(req.context),
            ),
            AdcpTaskStatus.failed,
        )

    # Type narrowing: in non-dry_run mode, step and persistent_ctx are guaranteed to exist
    # In dry_run mode, they may be None (database operations are skipped)
    if not testing_ctx.dry_run:
        assert step is not None, "step should be created when not in dry_run mode"
        assert persistent_ctx is not None, "persistent_ctx should be created when not in dry_run mode"

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
                # Cast packages to local PackageRequest type (runtime compatible, mypy list invariance)
                updated_packages, uploaded_ids = process_and_upload_package_creatives(
                    packages=cast(list[PackageRequest], req.packages),
                    context=ctx,
                    testing_ctx=testing_ctx,
                )
                # Replace packages with updated versions (functional approach)
                req.packages = cast(list[AdcpPackageRequest], updated_packages)
                logger.info("[INLINE_CREATIVE_DEBUG] Updated req.packages with creative_ids")
                if uploaded_ids:
                    logger.info(f"Successfully uploaded creatives for {len(uploaded_ids)} packages: {uploaded_ids}")
            except ToolError as e:
                # Update workflow step on failure (only if step exists)
                if step:
                    ctx_manager.update_workflow_step(step.step_id, status="failed", error_message=str(e))
                raise

        # Get the appropriate adapter with testing context
        # Use dry_run from testing context (which comes from config or testing flags)
        adapter = get_adapter(principal, dry_run=testing_ctx.dry_run, testing_context=testing_ctx)

        # Check if manual approval is required
        # Use tenant.human_review_required as the authoritative source, with adapter setting as fallback
        tenant_approval_required = tenant.get("human_review_required", True)
        adapter_approval_required = (
            adapter.manual_approval_required if hasattr(adapter, "manual_approval_required") else False
        )
        # Tenant setting takes precedence - if tenant requires approval, it's required
        manual_approval_required = tenant_approval_required or adapter_approval_required
        manual_approval_operations = (
            adapter.manual_approval_operations if hasattr(adapter, "manual_approval_operations") else []
        )

        # DEBUG: Log manual approval settings
        logger.info(
            f"[DEBUG] Manual approval check - tenant_approval_required: {tenant_approval_required}, "
            f"adapter_approval_required: {adapter_approval_required}, "
            f"final_required: {manual_approval_required}, "
            f"operations: {manual_approval_operations}, "
            f"adapter type: {adapter.__class__.__name__}"
        )

        # Check if auto-creation is disabled in tenant config
        auto_create_enabled = tenant.get("auto_create_media_buys", True)
        product_auto_create = True  # Will be set correctly when we get products later

        # Skip manual approval path in dry_run mode - we're only validating, not creating workflow
        if not testing_ctx.dry_run and manual_approval_required and "create_media_buy" in manual_approval_operations:
            # Type narrowing: step and persistent_ctx exist in non-dry_run mode
            assert step is not None and persistent_ctx is not None
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

            # req.packages validated earlier in _create_media_buy_impl
            assert req.packages is not None, "packages required - validated earlier"
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
                # NOTE: Package schema does NOT have a 'status' field - workflow state is tracked in WorkflowStep
                from src.core.schemas import Package

                # Create Package object from request package, adding generated fields
                # Must map PackageRequest fields to Package fields:
                # - format_ids (request) -> format_ids_to_provide (response)
                # - creative_ids/creatives (request) -> creative_assignments (response) [handled separately]
                pkg_data = pkg.model_dump(exclude_none=True)

                # Remove PackageRequest-only fields that aren't in Package schema
                pkg_data.pop("format_ids", None)  # Use format_ids_to_provide instead
                pkg_data.pop("creative_ids", None)  # These become creative_assignments
                pkg_data.pop("creatives", None)  # These become creative_assignments

                # Map format_ids to format_ids_to_provide if present
                if pkg.format_ids:
                    pkg_data["format_ids_to_provide"] = pkg.format_ids

                # Add Package-specific fields
                pkg_data.update(
                    {
                        "package_id": package_id,
                        "buyer_ref": pkg.buyer_ref,  # Include buyer_ref from request package
                        "paused": False,  # Initial state is not paused (AdCP 2.12.0)
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
                    # Get paused state from package (adcp 2.12.0: replaced status enum with paused bool)
                    paused = getattr(pkg_obj, "paused", False)  # Default to False (not paused) if not present

                    package_config = {
                        "package_id": pkg_obj.package_id,
                        "name": getattr(pkg_obj, "name", None),
                        "paused": paused,  # Store paused state (adcp 2.12.0)
                    }
                    # Add full package data from raw_request
                    assert req.packages is not None, "packages required - validated earlier"
                    for idx, req_pkg in enumerate(req.packages):
                        if idx == pending_packages.index(pkg_obj):
                            # Get pricing info for this package if available
                            pricing_info_for_package = (
                                package_pricing_info.get(pkg_obj.package_id) if pkg_obj.package_id else None
                            )

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

                            # Serialize format_ids to dicts for JSON storage
                            # Use mode='json' to convert AnyUrl to string
                            format_ids_serialized = None
                            if hasattr(req_pkg, "format_ids") and req_pkg.format_ids:
                                format_ids_serialized = [
                                    fmt.model_dump(mode="json") if hasattr(fmt, "model_dump") else fmt
                                    for fmt in req_pkg.format_ids
                                ]

                            package_config.update(
                                {
                                    "product_id": req_pkg.product_id,
                                    "budget": budget_value,
                                    "targeting_overlay": (
                                        req_pkg.targeting_overlay.model_dump() if req_pkg.targeting_overlay else None
                                    ),
                                    "creative_ids": _get_creative_ids(req_pkg),
                                    "format_ids": format_ids_serialized,
                                    "pricing_info": pricing_info_for_package,  # Store pricing info for UI display
                                    "impressions": getattr(
                                        req_pkg, "impressions", None
                                    ),  # Store impressions for display (legacy field)
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
                        pkg_cids = _get_creative_ids(package)
                        if pkg_cids:
                            all_creative_ids.extend(pkg_cids)

                    creatives_map: dict[str, Any] = {}
                    if all_creative_ids:
                        creative_stmt = select(DBCreative).where(
                            DBCreative.tenant_id == tenant["tenant_id"],
                            DBCreative.creative_id.in_(all_creative_ids),
                        )
                        creatives_list = session.scalars(creative_stmt).all()
                        creatives_map = {str(c.creative_id): c for c in creatives_list}
                        logger.info(f"[CREATIVE_ASSIGN_DEBUG] Loaded {len(creatives_map)} creatives from database")

                        # Validate creative formats against product formats BEFORE creating assignments
                        # This ensures creatives match the product's supported formats
                        # Validation happens at assignment time (not sync time) because:
                        # - Creatives may be synced before being assigned to products
                        # - A creative may be valid for product A but not product B
                        # - Same creative can be reused across packages if formats align
                        from src.core.helpers import validate_creative_format_against_product

                        for package in req.packages:
                            pkg_cids = _get_creative_ids(package)
                            if pkg_cids and package.product_id:
                                # Load product to check supported formats
                                product_for_format_validation_stmt = select(ModelProduct).where(
                                    ModelProduct.tenant_id == tenant["tenant_id"],
                                    ModelProduct.product_id == package.product_id,
                                )
                                product_for_format_validation: ModelProduct | None = session.scalars(
                                    product_for_format_validation_stmt
                                ).first()

                                if product_for_format_validation:
                                    # Validate each creative against this product
                                    for creative_id in pkg_cids:
                                        creative = creatives_map.get(creative_id)
                                        if creative:
                                            # Simple binary check: does creative's format_id match product?
                                            # Construct FormatId from database creative's agent_url and format columns
                                            # (DBCreative stores these as separate string columns, not a FormatId object)
                                            creative_format_id = FormatId(
                                                agent_url=creative.agent_url, id=creative.format
                                            )
                                            format_is_valid, format_error = validate_creative_format_against_product(
                                                creative_format_id=creative_format_id,
                                                product=product_for_format_validation,
                                            )

                                            if not format_is_valid:
                                                logger.error(f"[CREATIVE_ASSIGN_DEBUG] {format_error}")
                                                logger.warning(
                                                    "Creative format validation failure",
                                                    extra={
                                                        "creative_id": creative_id,
                                                        "product_id": package.product_id,
                                                        "creative_format": creative.format,
                                                        "validation_error": format_error,
                                                    },
                                                )
                                                raise ToolError(format_error)

                                            logger.info(
                                                f"[CREATIVE_ASSIGN_DEBUG] Creative {creative_id} format "
                                                f"validated against product {package.product_id}"
                                            )

                    # Create assignments for each package
                    for i, package in enumerate(req.packages):
                        pkg_cids = _get_creative_ids(package)
                        if pkg_cids:
                            # Get package_id from pending_packages (already generated)
                            pkg_id: str | None = pending_packages[i].package_id if i < len(pending_packages) else None
                            if not pkg_id:
                                logger.error(f"Cannot assign creatives: No package_id for package {i}")
                                continue

                            logger.info(
                                f"[CREATIVE_ASSIGN_DEBUG] Creating assignments for package {pkg_id}, creative_ids: {pkg_cids}"
                            )

                            for creative_id in pkg_cids:
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
            return (
                CreateMediaBuySuccess(
                    buyer_ref=response_buyer_ref,
                    media_buy_id=media_buy_id,
                    creative_deadline=None,
                    packages=cast(list[Any], pending_packages),
                    workflow_step_id=step.step_id,  # Client can track approval via this ID
                    context=to_context_object(req.context),
                ),
                AdcpTaskStatus.submitted,
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

            for schema_product in products_in_buy:
                # Auto-generate default config if missing
                if not schema_product.implementation_config:
                    logger.info(
                        f"Product '{schema_product.name}' ({schema_product.product_id}) is missing GAM configuration. "
                        f"Auto-generating defaults based on product type."
                    )
                    # Generate defaults based on product delivery type and formats
                    delivery_type_str = (
                        str(schema_product.delivery_type)
                        if hasattr(schema_product, "delivery_type") and schema_product.delivery_type
                        else "non_guaranteed"
                    )
                    # Extract format IDs as strings for config generation
                    formats_list: list[str] | None = None
                    if schema_product.format_ids:
                        formats_list = [fmt.id for fmt in schema_product.format_ids]
                    schema_product.implementation_config = gam_validator.generate_default_config(
                        delivery_type=delivery_type_str, formats=formats_list
                    )

                    # Persist the auto-generated config to database
                    with get_db_session() as db_session:
                        product_stmt = select(ModelProduct).filter_by(product_id=schema_product.product_id)
                        db_product = db_session.scalars(product_stmt).first()
                        if db_product:
                            db_product.implementation_config = schema_product.implementation_config
                            db_session.commit()
                            logger.info(f"Saved auto-generated GAM config for product {schema_product.product_id}")

                # Validate the config (whether existing or auto-generated)
                impl_config = schema_product.implementation_config if schema_product.implementation_config else {}
                is_valid, error_msg_temp = gam_validator.validate_config(impl_config)
                error_msg = error_msg_temp if error_msg_temp else "Unknown error"
                if not is_valid:
                    config_errors.append(
                        f"Product '{schema_product.name}' ({schema_product.product_id}) has invalid GAM configuration: {error_msg}"
                    )

            if config_errors:
                error_detail = "GAM configuration validation failed:\n" + "\n".join(
                    f"  • {err}" for err in config_errors
                )
                if step:
                    ctx_manager.update_workflow_step(step.step_id, status="failed", error_message=error_detail)
                return (
                    CreateMediaBuyError(
                        errors=[
                            Error(code="invalid_configuration", message=err, details=None) for err in config_errors
                        ],
                        context=to_context_object(req.context),
                    ),
                    AdcpTaskStatus.failed,
                )

        product_auto_create = all(
            p.implementation_config.get("auto_create_enabled", True) if p.implementation_config else True
            for p in products_in_buy
        )

        # Check if either tenant or product disables auto-creation
        # Skip in dry_run mode - we're only validating, not creating workflow
        if not testing_ctx.dry_run and (not auto_create_enabled or not product_auto_create):
            reason = "Tenant configuration" if not auto_create_enabled else "Product configuration"
            # Type narrowing: step and persistent_ctx exist in non-dry_run mode
            assert step is not None and persistent_ctx is not None
            # Update existing workflow step to require approval
            ctx_manager.update_workflow_step(step.step_id, status="requires_approval")

            # Workflow step already created above - no need for separate task
            # Generate permanent media buy ID (not "pending_xxx")
            media_buy_id = f"mb_{uuid.uuid4().hex[:12]}"

            response_msg = f"Media buy requires approval due to {reason.lower()}. Workflow Step ID: {step.step_id}. Context ID: {persistent_ctx.context_id}"
            ctx_manager.add_message(persistent_ctx.context_id, "assistant", response_msg)

            # Generate permanent package IDs and prepare response packages
            response_packages = []
            assert req.packages is not None, "packages required - validated earlier"
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

            return (
                CreateMediaBuySuccess(
                    buyer_ref=req.buyer_ref if req.buyer_ref else "unknown",
                    media_buy_id=media_buy_id,
                    packages=cast(list[Any], response_packages),
                    workflow_step_id=step.step_id,
                    context=to_context_object(req.context),
                ),
                AdcpTaskStatus.submitted,
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
        assert req.packages is not None, "packages required - validated earlier"
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
                # Format is composite key: (agent_url, id) per AdCP spec
                product_format_keys: set[tuple[str | None, str]] = set()
                if pkg_product.format_ids:
                    for fmt in pkg_product.format_ids:
                        # pkg_product.format_ids are dicts from database JSONB (type annotation says FormatId but runtime is dict)
                        agent_url = fmt["agent_url"]  # type: ignore[index]
                        normalized_url = str(agent_url).rstrip("/") if agent_url else None
                        product_format_keys.add((normalized_url, fmt["id"]))  # type: ignore[index]

                # Build set of requested format keys for comparison
                requested_format_keys: set[tuple[str | None, str]] = set()
                for fmt in matching_package.format_ids:
                    normalized_url = str(fmt.agent_url).rstrip("/") if fmt.agent_url else None
                    requested_format_keys.add((normalized_url, fmt.id))

                def format_display(url: str | None, fid: str) -> str:
                    """Format a (url, id) pair for display, handling trailing slashes."""
                    if not url:
                        return fid
                    # Remove trailing slash from URL to avoid double slashes
                    # Convert to string in case it's an AnyUrl object
                    clean_url = str(url).rstrip("/")
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
                        # Convert to string in case it's an AnyUrl object
                        base = str(url).rstrip("/")
                        mcp_url = base if base.endswith("/mcp") else f"{base}/mcp"
                        if (mcp_url, fid) in keys:
                            return True

                    return False

                unsupported_formats = [
                    format_display(url, fid) for url, fid in requested_format_keys if not _has_supported_key(url, fid)
                ]

                if unsupported_formats:
                    if not product_format_keys:
                        # Product has no format_ids configured - this is a configuration error
                        error_msg = (
                            f"Product '{pkg_product.name}' ({pkg_product.product_id}) has no format_ids configured. "
                            f"This product is not properly set up for media buys. "
                            f"Please configure format_ids on the product or contact the publisher."
                        )
                    else:
                        supported_formats_str = ", ".join(
                            [format_display(url, fid) for url, fid in product_format_keys]
                        )
                        error_msg = (
                            f"Product '{pkg_product.name}' ({pkg_product.product_id}) does not support requested format(s): "
                            f"{', '.join(unsupported_formats)}. Supported formats: {supported_formats_str}"
                        )
                    raise ValueError(error_msg)

                # Merge dimensions from product's format_ids if request format_ids don't have them
                # This handles the case where buyer specifies format_id but not dimensions
                # Build lookup of product format dimensions by (normalized_url, id)
                product_format_dimensions: dict[
                    tuple[str | None, str], tuple[int | None, int | None, float | None]
                ] = {}
                if pkg_product.format_ids:
                    for fmt in pkg_product.format_ids:
                        # pkg_product.format_ids are dicts from database JSONB
                        if isinstance(fmt, dict):
                            agent_url = fmt.get("agent_url")
                            fmt_id = fmt.get("id")
                        else:
                            agent_url = getattr(fmt, "agent_url", None)
                            fmt_id = getattr(fmt, "id", None)
                        normalized_url = str(agent_url).rstrip("/") if agent_url else None
                        if fmt_id:
                            if isinstance(fmt, dict):
                                width = fmt.get("width")
                                height = fmt.get("height")
                                duration_ms = fmt.get("duration_ms")
                            else:
                                width = getattr(fmt, "width", None)
                                height = getattr(fmt, "height", None)
                                duration_ms = getattr(fmt, "duration_ms", None)
                            product_format_dimensions[(normalized_url, fmt_id)] = (width, height, duration_ms)

                # Process request format_ids, merging dimensions from product if missing
                for req_fmt in matching_package.format_ids:
                    normalized_url = str(req_fmt.agent_url).rstrip("/") if req_fmt.agent_url else None
                    # Check if request format has dimensions
                    if req_fmt.width is not None and req_fmt.height is not None:
                        # Request has dimensions, convert to our FormatId type
                        format_ids_to_use.append(
                            FormatId(
                                agent_url=req_fmt.agent_url,
                                id=req_fmt.id,
                                width=req_fmt.width,
                                height=req_fmt.height,
                                duration_ms=req_fmt.duration_ms,
                            )
                        )
                    else:
                        # Try to get dimensions from product's format_ids
                        product_dims = product_format_dimensions.get((normalized_url, req_fmt.id))
                        if product_dims and (product_dims[0] is not None or product_dims[1] is not None):
                            # Merge dimensions from product
                            format_ids_to_use.append(
                                FormatId(
                                    agent_url=req_fmt.agent_url,
                                    id=req_fmt.id,
                                    width=product_dims[0],
                                    height=product_dims[1],
                                    duration_ms=product_dims[2] if product_dims[2] is not None else req_fmt.duration_ms,
                                )
                            )
                        else:
                            # No dimensions in product either, convert to our FormatId type
                            # GAM adapter will try regex extraction from format_id string
                            format_ids_to_use.append(
                                FormatId(
                                    agent_url=req_fmt.agent_url,
                                    id=req_fmt.id,
                                    width=req_fmt.width,
                                    height=req_fmt.height,
                                    duration_ms=req_fmt.duration_ms,
                                )
                            )

            # Fallback to product's formats if no request format_ids
            if not format_ids_to_use:
                if pkg_product.format_ids:
                    # Convert product.format_ids to FormatId objects if they're strings or dicts
                    # Get default creative agent URL from tenant config (tenant is dict[str, Any])
                    default_agent_url = tenant.get("creative_agent_url") or "https://creative.adcontextprotocol.org"
                    for fmt_item in pkg_product.format_ids:
                        if isinstance(fmt_item, str):
                            # Convert legacy string format to FormatId object
                            format_ids_to_use.append(FormatId(agent_url=make_url(default_agent_url), id=fmt_item))
                        elif isinstance(fmt_item, dict):
                            # Convert dict to FormatId object (preserves width/height/duration_ms)
                            # Ensure agent_url is set
                            if "agent_url" not in fmt_item or not fmt_item["agent_url"]:
                                fmt_item = {**fmt_item, "agent_url": default_agent_url}
                            format_ids_to_use.append(FormatId(**fmt_item))
                        elif isinstance(fmt_item, FormatId):
                            # Already a FormatId object
                            format_ids_to_use.append(fmt_item)
                        else:
                            # Unknown type - try to cast (backward compatibility)
                            format_ids_to_use.append(cast(FormatId, fmt_item))
                else:
                    format_ids_to_use = []

            # Get CPM from pricing_options
            cpm = 10.0  # Default
            if pkg_product.pricing_options and len(pkg_product.pricing_options) > 0:
                first_option = pkg_product.pricing_options[0]
                # adcp 2.14.0+ uses RootModel wrapper - access via .root
                inner_option = getattr(first_option, "root", first_option)
                rate = getattr(inner_option, "rate", None)
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
                    format_ids=cast(list[Any], format_ids_to_use),
                    targeting_overlay=cast(
                        "Targeting | None",
                        matching_package.targeting_overlay
                        if matching_package and hasattr(matching_package, "targeting_overlay")
                        else None,
                    ),
                    buyer_ref=package_buyer_ref,
                    product_id=pkg_product.product_id,  # Include product_id
                    budget=package_budget_value,  # Include budget from request (now normalized)
                    creative_ids=(
                        _get_creative_ids(matching_package) if matching_package else None
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
            if step:
                ctx_manager.update_workflow_step(step.step_id, status="failed", error_message=error_msg)
            return (
                CreateMediaBuyError(
                    errors=[Error(code="invalid_datetime", message=error_msg, details=None)],
                    context=to_context_object(req.context),
                ),
                AdcpTaskStatus.failed,
            )

        # PRE-VALIDATE: Check all creatives have required fields BEFORE calling adapter
        # This prevents GAM order creation when creatives are invalid (all-or-nothing approach)
        try:
            _validate_creatives_before_adapter_call(packages, tenant["tenant_id"])
        except ToolError:
            # Validation failed - creative validation errors already logged
            # Update workflow step as failed and re-raise (only if step exists - not created in dry_run mode)
            if step:
                ctx_manager.update_workflow_step(
                    step.step_id, status="failed", error_message="Creative validation failed"
                )
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
            return (response, AdcpTaskStatus.failed)

        # At this point, response is CreateMediaBuySuccess - safe to access success-specific fields
        # Type narrowing: media_buy_id must be present in successful response
        assert response.media_buy_id is not None, "Adapter returned response without media_buy_id"

        # Log response packages for debugging
        if response.packages:
            for i, pkg_item in enumerate(response.packages):
                # pkg_item is dict[str, Any] here (response.packages), different scope from earlier Package usage
                logger.info(f"[DEBUG] create_media_buy: Response package {i} = {pkg_item}")

        # Dry-run mode: Return adapter response without database writes
        # Adapter validation has run, so errors (like unsupported pricing) are already caught above
        if testing_ctx.dry_run:
            logger.info("[DRY_RUN] Adapter validation passed, returning response without database writes")
            return (response, AdcpTaskStatus.completed)

        # Type narrowing: after dry_run return, step and persistent_ctx are guaranteed to exist
        # This is needed for mypy to understand these won't be None in the code below
        assert step is not None, "step should be created when not in dry_run mode"
        assert persistent_ctx is not None, "persistent_ctx should be created when not in dry_run mode"

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
                if _get_creative_ids(pkg):
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
                    # resp_package is always a Package object (adapters no longer return dicts)
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
                    resp_package_id: str | None = resp_package.package_id

                    if not resp_package_id:
                        error_msg = (
                            f"Adapter did not return package_id for package {i}. This is a critical bug in the adapter."
                        )
                        logger.error(error_msg)
                        raise ValueError(error_msg)

                    # Store full package config as JSON
                    # Get paused state from adapter response (adcp 2.12.0: replaced status enum with paused bool)
                    paused = getattr(resp_package, "paused", False)  # Default to False (not paused) if not present

                    # Get pricing info for this package if available
                    pricing_info_for_package = package_pricing_info.get(resp_package_id)

                    # Get impressions from request package if available (legacy field)
                    request_pkg = req.packages[i] if req.packages and i < len(req.packages) else None
                    impressions = getattr(request_pkg, "impressions", None) if request_pkg else None

                    package_config = {
                        "package_id": resp_package_id,
                        "name": getattr(resp_package, "name", None),  # Include package name from adapter response
                        "product_id": getattr(resp_package, "product_id", None),
                        "budget": serialize_for_json(getattr(resp_package, "budget", None)),
                        "targeting_overlay": serialize_for_json(getattr(resp_package, "targeting_overlay", None)),
                        "creative_ids": getattr(resp_package, "creative_ids", None),
                        "creative_assignments": serialize_for_json(getattr(resp_package, "creative_assignments", None)),
                        "format_ids_to_provide": getattr(resp_package, "format_ids_to_provide", None),
                        "paused": paused,  # Store paused state (adcp 2.12.0)
                        "pricing_info": pricing_info_for_package,  # Store pricing info for UI display
                        "impressions": impressions,  # Store impressions for display
                    }

                    # Extract pricing fields for dual-write from adapter response
                    from decimal import Decimal

                    budget_total = None
                    budget_data = getattr(resp_package, "budget", None)
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
                    pkg_cids = _get_creative_ids(package)
                    if pkg_cids:
                        all_creative_ids.extend(pkg_cids)

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

                    # Validate creative formats against product formats BEFORE creating assignments
                    # This ensures creatives match the product's supported formats
                    # Validation happens at assignment time (not sync time) because:
                    # - Creatives may be synced before being assigned to products
                    # - A creative may be valid for product A but not product B
                    # - Same creative can be reused across packages if formats align
                    from src.core.helpers import validate_creative_format_against_product

                    for package in req.packages:
                        pkg_cids = _get_creative_ids(package)
                        if pkg_cids and package.product_id:
                            # Load product to check supported formats
                            product_format_check_stmt = select(ModelProduct).where(
                                ModelProduct.tenant_id == tenant["tenant_id"],
                                ModelProduct.product_id == package.product_id,
                            )
                            product_format_check: ModelProduct | None = session.scalars(
                                product_format_check_stmt
                            ).first()

                            if product_format_check:
                                # Validate each creative against this product
                                for creative_id in pkg_cids:
                                    creative = creatives_by_id.get(creative_id)
                                    if creative:
                                        # Simple binary check: does creative's format_id match product?
                                        # Construct FormatId from database creative's agent_url and format columns
                                        # (DBCreative stores these as separate string columns, not a FormatId object)
                                        creative_format_id = FormatId(agent_url=creative.agent_url, id=creative.format)
                                        format_is_valid, format_error = validate_creative_format_against_product(
                                            creative_format_id=creative_format_id,
                                            product=product_format_check,
                                        )

                                        if not format_is_valid:
                                            logger.error(format_error)
                                            logger.warning(
                                                "Creative format validation failure",
                                                extra={
                                                    "creative_id": creative_id,
                                                    "product_id": package.product_id,
                                                    "creative_format": creative.format,
                                                    "validation_error": format_error,
                                                },
                                            )
                                            ctx_manager.update_workflow_step(
                                                step.step_id, status="failed", error_message=format_error
                                            )
                                            raise ToolError("CREATIVE_FORMAT_MISMATCH", format_error)

                                        logger.info(
                                            f"Creative {creative_id} format validated against product {package.product_id}"
                                        )

                for i, package in enumerate(req.packages):
                    pkg_cids = _get_creative_ids(package)
                    if pkg_cids:
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

                        for creative_id in pkg_cids:
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
                                    # Uses shared helper with in-memory cache (30min TTL)
                                    format_spec = None
                                    if creative.format:
                                        format_spec = _get_format_spec_sync(creative.agent_url, str(creative.format))
                                        if not format_spec:
                                            logger.warning(
                                                f"[AUTO-APPROVAL] Could not fetch format {creative.format} "
                                                f"from {creative.agent_url}"
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
        # Note: creatives field no longer exists on CreateMediaBuyRequest per AdCP spec
        # Creative IDs are now at package level (package.creative_ids). Check with getattr for backward compat.
        creative_statuses: dict[str, CreativeApprovalStatus] = {}
        legacy_creatives = getattr(req, "creatives", None)
        if legacy_creatives:
            # Convert Creative objects to format expected by adapter
            assets = []
            for creative in legacy_creatives:
                try:
                    # Ensure product_ids is a list, not None
                    # Note: product_ids no longer exists on CreateMediaBuyRequest - use get_product_ids() method
                    product_ids_list = req.get_product_ids() if hasattr(req, "get_product_ids") else []
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
                        status=cast(Any, final_status),
                        detail=detail,
                    )

        # Build packages list for response (AdCP v2.4 format)
        # Per AdCP spec, create-media-buy-response Package only includes:
        # - buyer_ref (required): Buyer's reference identifier
        # - package_id (required): Publisher's unique identifier
        response_packages = []

        # Get adapter response packages (have package_ids)
        adapter_packages = response.packages if response.packages else []

        assert req.packages is not None, "packages required - validated earlier"
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

            # Build full Package response per AdCP 2.9.0 spec
            # Return all package fields from request, plus package_id and status from adapter

            # Extract status from adapter response, defaulting to "active" if missing
            # Get paused field from adapter response (adcp 2.12.0: replaced status enum with paused bool)
            adapter_paused = response_package_dict.get("paused", False)

            # Ensure paused is a boolean
            if not isinstance(adapter_paused, bool):
                # Convert common values to boolean
                if isinstance(adapter_paused, str):
                    adapter_paused = adapter_paused.lower() in ("true", "1", "yes", "active" == False)
                else:
                    adapter_paused = bool(adapter_paused)

            # Build full package dict with all fields from request
            full_package_dict = {
                "package_id": adapter_package_id,  # From adapter (required)
                "paused": adapter_paused,  # From adapter (adcp 2.12.0)
                "buyer_ref": package.buyer_ref,  # From request
                "product_id": package.product_id,  # From request
            }

            # Add optional fields from request if present
            if package.budget is not None:
                full_package_dict["budget"] = (
                    float(package.budget) if isinstance(package.budget, (int, float)) else package.budget
                )

            # Legacy impressions field (use getattr for backward compatibility)
            package_impressions = getattr(package, "impressions", None)
            if package_impressions is not None:
                full_package_dict["impressions"] = float(package_impressions)

            if package.bid_price is not None:
                full_package_dict["bid_price"] = float(package.bid_price)

            if package.pricing_option_id is not None:
                full_package_dict["pricing_option_id"] = package.pricing_option_id

            if package.pacing is not None:
                full_package_dict["pacing"] = (
                    package.pacing.model_dump() if hasattr(package.pacing, "model_dump") else package.pacing
                )

            if package.targeting_overlay is not None:
                full_package_dict["targeting_overlay"] = (
                    package.targeting_overlay.model_dump()
                    if hasattr(package.targeting_overlay, "model_dump")
                    else package.targeting_overlay
                )

            if hasattr(package, "creative_assignments") and package.creative_assignments:
                full_package_dict["creative_assignments"] = [
                    ca.model_dump() if hasattr(ca, "model_dump") else ca for ca in package.creative_assignments
                ]

            if hasattr(package, "format_ids_to_provide") and package.format_ids_to_provide:
                full_package_dict["format_ids_to_provide"] = package.format_ids_to_provide

            response_packages.append(full_package_dict)

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
            packages=cast(list[Any], response_packages),
            creative_deadline=response.creative_deadline,
            context=to_context_object(req.context),
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
            context=to_context_object(req.context),
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
            # Note: creatives field no longer exists on CreateMediaBuyRequest per AdCP spec
            notification_creatives = getattr(req, "creatives", None)
            success_details = {
                "total_budget": total_budget,
                "po_number": req.po_number,
                "start_time": start_time.isoformat(),  # Resolved from 'asap' if needed
                "end_time": end_time.isoformat(),
                "product_ids": req.get_product_ids(),
                "duration_days": (end_time_val - start_time_val).days + 1,
                "packages_count": len(response_packages) if response_packages else 0,
                "creatives_count": len(notification_creatives) if notification_creatives else 0,
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

        return (modified_response, AdcpTaskStatus.completed)

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
    brand_manifest: BrandManifest | str,  # BrandManifest or URL string - REQUIRED per AdCP v2.2.0 spec
    packages: list[AdcpPackageRequest],  # REQUIRED per AdCP spec - Package objects with all fields
    start_time: str,  # datetime ISO 8601 or 'asap' - REQUIRED per AdCP spec
    end_time: str,  # datetime ISO 8601 - REQUIRED per AdCP spec
    budget: Any | None = None,  # DEPRECATED: Budget is package-level only per AdCP v2.2.0
    po_number: str | None = None,
    product_ids: list[str] | None = None,  # Legacy format conversion
    start_date: Any | None = None,  # Legacy format conversion
    end_date: Any | None = None,  # Legacy format conversion
    total_budget: float | None = None,  # Legacy format conversion
    targeting_overlay: TargetingOverlay | None = None,
    pacing: str = "even",
    daily_budget: float | None = None,
    creatives: list[CreativeAsset] | None = None,
    reporting_webhook: ReportingWebhook | None = None,
    required_axe_signals: list[str] | None = None,
    enable_creative_macro: bool = False,
    strategy_id: str | None = None,
    push_notification_config: PushNotificationConfig | None = None,
    context: ContextObject | None = None,  # payload-level context
    webhook_url: str | None = None,
    ctx: Context | ToolContext | None = None,
):
    """Create a media buy with the specified parameters.

    MCP tool wrapper that delegates to the shared implementation.
    FastMCP automatically validates and coerces JSON inputs to Pydantic models.

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
    # Convert typed Pydantic models to dicts for the impl
    # FastMCP already coerced JSON inputs to these types
    brand_manifest_val = (
        brand_manifest.model_dump(mode="json") if isinstance(brand_manifest, BrandManifest) else brand_manifest
    )
    packages_dicts = [p.model_dump(mode="json") for p in packages]
    reporting_webhook_dict = reporting_webhook.model_dump(mode="json") if reporting_webhook else None
    context_dict = context.model_dump(mode="json") if context else None

    response, status = await _create_media_buy_impl(
        buyer_ref=buyer_ref,
        brand_manifest=brand_manifest_val,
        po_number=po_number,
        packages=packages_dicts,
        start_time=start_time,
        end_time=end_time,
        reporting_webhook=reporting_webhook_dict,
        context=context_dict,
        ctx=ctx,
    )
    return ToolResult(
        content=str(response),
        structured_content={"status": status.value, **response.model_dump()},
    )


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
        Dict with status and CreateMediaBuyResponse data
    """
    response, status = await _create_media_buy_impl(
        buyer_ref=buyer_ref,
        brand_manifest=brand_manifest,
        po_number=po_number,
        packages=packages,
        start_time=start_time,
        end_time=end_time,
        reporting_webhook=reporting_webhook,
        push_notification_config=push_notification_config,
        context=context,
        ctx=ctx,
    )
    return {"status": status.value, **response.model_dump()}


# Unified update tools
