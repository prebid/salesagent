"""Create Media Buy tool implementation.

Handles media buy creation including:
- Product selection and validation
- Package configuration with pricing
- Creative assignment
- Ad server order provisioning
- Budget validation
"""

import logging
import os
import time
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, TypedDict, cast
from urllib.parse import urlparse

from sqlalchemy.exc import IntegrityError

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

from adcp.types import Account, ContextObject, MediaBuyStatus
from adcp.types import GeneratedTaskStatus as AdcpTaskStatus
from adcp.types import PackageRequest as AdcpPackageRequest
from adcp.types.aliases import Package as ResponsePackage
from pydantic import BaseModel, ValidationError
from rich.console import Console

from src.core.exceptions import (
    AdCPAdapterError,
    AdCPAuthenticationError,
    AdCPAuthorizationError,
    AdCPError,
    AdCPInvalidRequestError,
    AdCPNotFoundError,
    AdCPProductNotFoundError,
    AdCPTermsRejectedError,
    AdCPValidationError,
)
from src.core.tracing import traced

# Conservative seller policy for measurement_terms negotiation.
# Buyers propose; sellers accept, reject, or adjust. The minimum variance the
# salesagent will honor against any third-party measurement vendor — anything
# tighter than this is unworkable in practice and surfaces as TERMS_REJECTED
# (correctable). Buyer agents are expected to relax and retry. This floor is
# deliberately conservative: real publishers tolerate 5–10% variance against
# DV/MOAT/IAS depending on the metric, and 0% is mathematically impossible
# across independent counting methodologies.
_MIN_SUPPORTED_VARIANCE_PERCENT = 5.0


def _format_ref_display(format_ref: Any) -> str:
    agent_url = getattr(format_ref, "agent_url", None)
    fmt_id = getattr(format_ref, "id", None)
    if isinstance(format_ref, dict):
        agent_url = format_ref.get("agent_url")
        fmt_id = format_ref.get("id", format_ref.get("format_id"))
    if not fmt_id:
        fmt_id = str(format_ref)
    if not agent_url:
        return str(fmt_id)
    return f"{str(agent_url).rstrip('/')}/{fmt_id}"


def _matching_supported_format(requested_format: Any, supported_formats: list[Any]) -> Any | None:
    for supported_format in supported_formats:
        if canonical_format_satisfies(requested_format, supported_format):
            return supported_format
    return None


def _format_params(format_ref: Any) -> tuple[int | None, int | None, float | None]:
    fmt = upgrade_legacy_format_id(format_ref)
    return (
        fmt.width,
        fmt.height,
        fmt.duration_ms,
    )


def _uploaded_platform_creative_id(upload_result: Any, creative_id: str) -> str:
    if not upload_result:
        raise AdCPAdapterError(
            f"Failed to upload creative {creative_id} to adapter: adapter returned no upload status",
            details={"error_code": "CREATIVE_UPLOAD_FAILED"},
        )

    uploaded_status = upload_result[0]
    status = str(getattr(uploaded_status, "status", "") or "").lower()
    platform_creative_id = getattr(uploaded_status, "creative_id", None)
    if status != "approved" or not platform_creative_id:
        message = getattr(uploaded_status, "message", None)
        detail = f" status={status or 'missing'}"
        if message:
            detail += f": {message}"
        raise AdCPAdapterError(
            f"Failed to upload creative {creative_id} to adapter:{detail}",
            details={"error_code": "CREATIVE_UPLOAD_FAILED"},
        )

    return str(platform_creative_id)


def _validate_measurement_terms(req: "CreateMediaBuyRequest") -> None:
    """Reject buyer-proposed measurement_terms the seller cannot honor.

    Today's salesagent has no per-tenant measurement_terms_supported config,
    so the policy is a fixed floor: any ``max_variance_percent`` below
    :data:`_MIN_SUPPORTED_VARIANCE_PERCENT` is rejected. Sellers that want
    to honor stricter terms can subclass this check or extend tenant config
    later — the rejection branch must exist regardless so unworkable terms
    surface as TERMS_REJECTED instead of leaking through to INTERNAL_ERROR
    via an unhandled downstream exception.

    Per AdCP spec the error is ``correctable``: buyer relaxes variance and
    retries with a fresh idempotency_key.
    """
    if not req.packages:
        return
    for idx, package in enumerate(req.packages):
        terms = getattr(package, "measurement_terms", None)
        if terms is None:
            continue
        billing = getattr(terms, "billing_measurement", None)
        if billing is None:
            continue
        variance = getattr(billing, "max_variance_percent", None)
        if variance is None:
            continue
        if float(variance) < _MIN_SUPPORTED_VARIANCE_PERCENT:
            raise AdCPTermsRejectedError(
                (
                    f"max_variance_percent={variance} is tighter than this seller's "
                    f"minimum of {_MIN_SUPPORTED_VARIANCE_PERCENT}%. No third-party "
                    "measurement vendor can guarantee a variance below this floor. "
                    "Relax measurement_terms.billing_measurement.max_variance_percent "
                    f"to >= {_MIN_SUPPORTED_VARIANCE_PERCENT} and retry with a fresh "
                    "idempotency_key."
                ),
                details={
                    "field": f"packages[{idx}].measurement_terms.billing_measurement.max_variance_percent",
                    "min_supported_variance_percent": _MIN_SUPPORTED_VARIANCE_PERCENT,
                },
            )


def _effective_manual_approval_required(
    *,
    tenant_approval_required: bool,
    adapter_approval_required: bool,
    publisher_owns_campaign: bool | None = None,
) -> bool:
    """Apply the embedded campaign_approval gate to manual approval checks."""
    owns_approval = publisher_owns_campaign_approval() if publisher_owns_campaign is None else publisher_owns_campaign
    if not owns_approval:
        return False
    return owns_approval and (tenant_approval_required or adapter_approval_required)


def _suppress_adapter_manual_approval(adapter: Any, operation: str) -> None:
    """Disable an adapter manual-approval operation for this request instance."""
    operations = getattr(adapter, "manual_approval_operations", None)
    if not operations:
        return

    if hasattr(operations, "discard"):
        operations.discard(operation)
        return

    if isinstance(operations, list):
        adapter.manual_approval_operations = [op for op in operations if op != operation]
        return

    if isinstance(operations, tuple):
        adapter.manual_approval_operations = tuple(op for op in operations if op != operation)


class PackageAssignmentDict(TypedDict):
    """Internal dict format for passing package assignments to adapters."""

    package_id: str
    weight: int


class RequestedCreativeAssignment(TypedDict):
    """Normalized package creative binding from either supported request shape."""

    creative_id: str
    weight: int
    placement_ids: list[str] | None


logger = logging.getLogger(__name__)
console = Console()


def validate_agent_url(url: str | None) -> bool:
    """Validate agent_url is a well-formed HTTP(S) URL per AdCP spec.

    This validates format/structure only (scheme + netloc). It does NOT
    perform DNS resolution or SSRF network checks because it is called
    during approval processing against URLs that are already stored in
    the database — not against live user-supplied input.

    SSRF protection for user-supplied agent URLs is enforced at the admin
    ingestion boundary in src/admin/blueprints/signals_agents.py using
    check_url_ssrf(), which includes DNS resolution.

    Args:
        url: URL string to validate

    Returns:
        True if valid HTTP(S) URL with a non-empty netloc.
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
from src.core.context_manager import get_context_manager
from src.core.database.models import MediaBuy
from src.core.database.models import Principal as ModelPrincipal
from src.core.database.models import Product as ModelProduct
from src.core.embedded_runtime import publisher_owns_campaign_approval, publisher_owns_creative_approval
from src.core.format_cache import canonical_format_satisfies, upgrade_legacy_format_id
from src.core.helpers import log_tool_activity
from src.core.helpers.adapter_helpers import get_adapter
from src.core.helpers.creative_helpers import (
    _convert_creative_to_adapter_asset,
    adapter_asset_requires_dimensions,
    build_adapter_asset_from_stored_creative,
    extract_click_url,
    extract_impression_tracker_url,
    extract_media_url_and_dimensions,
    process_and_upload_package_creatives,
)
from src.core.resolved_identity import ResolvedIdentity
from src.core.sandbox import (
    account_ref_from_request,
    mark_sandbox_trafficking_request,
    sandbox_mode_for_request,
    sandbox_mode_for_rows,
    sandbox_trafficking_packages,
    sandbox_trafficking_pricing_info,
)
from src.core.schemas import (
    CreateMediaBuyError,
    CreateMediaBuyRequest,
    CreateMediaBuyResult,
    CreateMediaBuySubmitted,
    CreateMediaBuySuccess,
    CreativeApprovalStatus,
    Error,
    FormatId,
    MediaPackage,
    Package,
    PackageRequest,
    Principal,
    Targeting,
)
from src.core.schemas import (
    url as make_url,
)
from src.core.testing_hooks import AdCPTestContext, TestingContext, apply_testing_hooks
from src.core.tools.financial_validation import validate_max_daily_package_spend, validate_min_package_budget
from src.core.tools.workflow_serialization import serialize_for_workflow_step

# Import get_product_catalog from main (after refactor)
from src.core.validation_helpers import format_validation_error
from src.services.activity_feed import activity_feed

# --- Helper Functions ---


# NOTE: _sanitize_package_status() removed in adcp 2.12.0 migration
# Package.status enum was replaced with Package.paused boolean field
# See: adcp 2.12.0 changelog


def _get_creative_ids(package: AdcpPackageRequest | PackageRequest | Package | MediaPackage) -> list[str] | None:
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


def _get_requested_creative_assignments(
    package: AdcpPackageRequest | PackageRequest | Package | MediaPackage,
) -> list[RequestedCreativeAssignment]:
    """Normalize ``creative_ids`` and spec ``creative_assignments`` into bindings.

    ``creative_ids`` is the legacy local shorthand. ``creative_assignments`` is
    the AdCP placement/weight-aware shape. Create paths validate and persist
    both through the same flow so the media-buy status and read path cannot
    disagree about whether creatives are actually attached.
    """
    assignments_by_id: dict[str, RequestedCreativeAssignment] = {}

    for creative_id in _get_creative_ids(package) or []:
        assignments_by_id[str(creative_id)] = {
            "creative_id": str(creative_id),
            "weight": 100,
            "placement_ids": None,
        }

    for assignment in getattr(package, "creative_assignments", None) or []:
        creative_id = str(assignment.creative_id)
        assignments_by_id[creative_id] = {
            "creative_id": creative_id,
            "weight": int(assignment.weight) if assignment.weight is not None else 100,
            "placement_ids": list(assignment.placement_ids) if assignment.placement_ids is not None else None,
        }

    return list(assignments_by_id.values())


def _get_requested_creative_ids(
    package: AdcpPackageRequest | PackageRequest | Package | MediaPackage,
) -> list[str] | None:
    """Return all creative ids requested on a package, regardless of input shape."""
    creative_ids = [assignment["creative_id"] for assignment in _get_requested_creative_assignments(package)]
    return creative_ids or None


def _build_creatives_not_found_error(missing_ids: set[str]) -> tuple[str, AdCPNotFoundError]:
    """Build the canonical missing-creatives error for create-time assignment paths."""
    error_msg = f"Creative IDs not found: {', '.join(sorted(missing_ids))}"
    return error_msg, AdCPNotFoundError(
        error_msg,
        details={"error_code": "CREATIVES_NOT_FOUND"},
        recovery="correctable",
    )


def _determine_media_buy_status(
    manual_approval_required: bool,
    has_creatives: bool,
    start_time: datetime,
    end_time: datetime,
    now: datetime | None = None,
) -> str:
    """Centralized media buy status determination logic.

    This ensures consistent status across all adapters (GAM, Mock, etc.).

    Status Priority (highest to lowest) - ALL SPEC-COMPLIANT:
    1. completed: Past end date (terminal — must check first)
    2. pending_creatives: No creatives assigned — buyer's next action is
       sync_creatives. Higher priority than pending_start because missing creatives
       are a concrete missing artifact, not just a wall-clock wait.
    3. pending_start: Manual approval required OR scheduled for future start
    4. active: Currently delivering (has creatives within flight dates)

    Args:
        manual_approval_required: Whether the media buy requires manual approval
        has_creatives: Whether creatives have been assigned
        start_time: Campaign start datetime
        end_time: Campaign end datetime
        now: Current time (defaults to datetime.now(UTC))

    Returns:
        Status string matching AdCP MediaBuyStatus enum
        (pending_creatives, pending_start, active, completed)
    """
    if now is None:
        now = datetime.now(UTC)

    # Priority 1: Completed (past end date - check first to avoid false pending state)
    if now > end_time:
        return MediaBuyStatus.completed.value

    # Priority 2: Pending creatives (higher than pending_start). Per AdCP, this is the
    # buyer's signal to call sync_creatives. A future-dated buy with no creatives is
    # blocked on creatives, not on the clock — pending_creatives wins.
    #
    # Creative review state is reported separately on creative_approvals[*]; once
    # creatives are attached, the buy leaves pending_creatives even if review is pending.
    if not has_creatives:
        return MediaBuyStatus.pending_creatives.value

    # Priority 3: Pending start (manual approval gate or scheduled for future)
    if manual_approval_required or now < start_time:
        return MediaBuyStatus.pending_start.value

    # Priority 4: Active (currently delivering - all conditions met)
    return MediaBuyStatus.active.value


def _confirmed_at_for_create_status(status: str, timestamp: datetime) -> datetime | None:
    """Return confirmation time for synchronous create successes.

    ``confirmed_at`` records seller commitment to the media buy, not whether
    the buy is already delivering. A future-start buy and a buy waiting for
    creatives are still confirmed once the seller has created the buy.
    """
    if status in {
        MediaBuyStatus.active.value,
        MediaBuyStatus.completed.value,
        MediaBuyStatus.pending_start.value,
        MediaBuyStatus.pending_creatives.value,
    }:
        return timestamp
    return None


def _status_after_creative_attachment(
    *,
    current_status: str,
    approved_at: datetime | None,
    start_time: datetime | None,
    end_time: datetime | None,
    now: datetime | None = None,
) -> str | None:
    """Return the new media-buy status after creatives are attached, if any.

    ``pending_creatives`` means no creatives are assigned. Once assignments exist,
    creative review state remains on ``creative_approvals[*]`` and the buy status
    falls back to manual approval / flight-date gates.
    """
    if current_status == "draft" and approved_at is None:
        return None
    if current_status not in {"draft", MediaBuyStatus.pending_creatives.value}:
        return None
    if start_time is None or end_time is None:
        return MediaBuyStatus.pending_start.value
    return _determine_media_buy_status(
        manual_approval_required=False,
        has_creatives=True,
        start_time=start_time,
        end_time=end_time,
        now=now,
    )


def _request_has_creatives(req: CreateMediaBuyRequest) -> bool:
    """True if the request carries any creative references (ids or inline objects).

    Used to pick the spec ``MediaBuyStatus`` for a synchronously-minted buy:
    when no creatives accompany the request, the buyer's next step is to call
    ``sync_creatives``, which corresponds to ``MediaBuyStatus.pending_creatives``.
    When creatives are already present, the buy is awaiting governance/start
    rather than creatives, which corresponds to ``MediaBuyStatus.pending_start``.
    """
    if not req.packages:
        return False
    return _packages_have_creatives(req.packages)


def _packages_have_creatives(packages: Any) -> bool:
    """True if package objects or serialized package dicts carry creatives."""
    if not packages:
        return False
    for pkg in packages:
        if isinstance(pkg, dict):
            if pkg.get("creative_ids") or pkg.get("creative_assignments") or pkg.get("creatives"):
                return True
            continue
        # Local ``creative_ids`` and AdCP ``creative_assignments`` both attach
        # pre-uploaded creatives to the package. Inline ``creatives`` carries
        # full creative objects.
        if _get_requested_creative_ids(pkg) or getattr(pkg, "creatives", None):
            return True
    return False


def _media_buy_status_for_create_replay(existing: Any) -> MediaBuyStatus:
    """Derive the beta-2 lifecycle status for a create-media-buy replay."""
    try:
        return MediaBuyStatus(existing.status)
    except ValueError:
        if existing.status == "pending_approval":
            raw_request = existing.raw_request or {}
            if not _packages_have_creatives(raw_request.get("packages")):
                return MediaBuyStatus.pending_creatives
        return MediaBuyStatus.pending_start


def _link_step_to_media_buy(*, tenant_id: str, step_id: str, media_buy_id: str, branch: str) -> None:
    """Persist the workflow_step ↔ media_buy linkage as an ``ObjectWorkflowMapping``.

    ``context_manager._send_push_notifications`` walks these mappings to know
    which media_buy a step refers to when firing completion webhooks. Without
    a mapping, it returns early with "No object mappings found" and the
    buyer's webhook never fires.

    Both the manual-approval and auto-approval success branches need this row,
    hence the shared helper. ``branch`` is just a log label so we can tell the
    two call sites apart in the activity feed.
    """
    # FIXME(salesagent-9f2): workflow mapping should use a repository method
    from src.core.database.models import ObjectWorkflowMapping
    from src.core.database.repositories import MediaBuyUoW

    with MediaBuyUoW(tenant_id) as wf_uow:
        assert wf_uow.session is not None
        mapping = ObjectWorkflowMapping(
            object_type="media_buy",
            object_id=media_buy_id,
            step_id=step_id,
            action="create",
        )
        wf_uow.session.add(mapping)
        # UoW auto-commits on clean exit
        logger.info(f"✅ Linked workflow step {step_id} to media buy ({branch})")


def _get_format_spec_sync(agent_url: str, format_id: str) -> Any | None:
    """Get format specification synchronously from async registry code.

    This helper function wraps the async registry.get_format() call to make it
    usable in synchronous contexts. The registry uses in-memory cache (30min TTL)
    and falls back to the creative agent if not cached.

    Args:
        agent_url: Creative agent URL
        format_id: Format ID to fetch

    Returns:
        Format specification object or None if not found
    """
    from src.core.creative_agent_registry import get_creative_agent_registry
    from src.core.validation_helpers import run_async_in_sync_context

    registry = get_creative_agent_registry()

    try:
        return run_async_in_sync_context(registry.get_format(agent_url, format_id))
    except Exception as e:
        logger.warning(f"Could not fetch format {format_id} from {agent_url}: {e}")
        return None


def _stored_creative_format_id(creative: Any) -> FormatId:
    """Build a full FormatId from a stored creative row."""
    format_ref: dict[str, Any] = {
        "agent_url": creative.agent_url,
        "id": creative.format,
    }
    format_ref.update(creative.format_parameters or {})
    return FormatId(**format_ref)


def _dict_implementation_config(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _detect_overbook_warnings(
    *,
    adapter: Any,
    products_in_buy: list[Any],
    effective_configs: dict[str, dict],
    req: "CreateMediaBuyRequest",
    total_budget: float,
) -> list[dict[str, Any]]:
    """Pre-flight check that surfaces inventory-overbook warnings to the buyer.

    For single-product GAM buys, compares the implied impression goal
    (``budget / cpm * 1000``) against GAM's availability forecast for
    the product's ad units. Returns a list of warning records with
    ``code='inventory_overbook_minor'`` when goal exceeds forecast.

    Fail-open everywhere: returns ``[]`` when the adapter is not GAM,
    when the buy has multiple products (per #152 design — multi-product
    budget allocation deferred to a follow-up), when forecast or rate
    can't be determined, or when GAM rejects the forecast call. Never
    blocks the buy; this is informational only.

    Wire surface: callers attach the returned list to ``response.ext``
    under the key ``warnings`` per AdCP convention. Once
    https://github.com/adcontextprotocol/adcp/issues/4248 lands, this
    moves to a first-class ``warnings[]`` field.
    """
    if adapter.__class__.__name__ != "GoogleAdManager":
        return []
    if len(products_in_buy) != 1:
        # Multi-product allocation is non-trivial (different products may
        # have different CPMs). Defer to a follow-up — see #152.
        return []
    if total_budget <= 0:
        return []

    product = products_in_buy[0]
    impl_config = effective_configs.get(product.product_id, {}) or {}
    ad_unit_ids = impl_config.get("targeted_ad_unit_ids") or []
    if not ad_unit_ids:
        return []

    # Pull the first pricing option as the rate. The CPM-only cap is
    # intentional for the first ship: derive impressions only when the
    # rate is meaningful for that calculation.
    pricing_options = getattr(product, "pricing_options", None) or []
    if not pricing_options:
        return []
    first_option = pricing_options[0]
    inner = getattr(first_option, "root", first_option)
    pricing_model = str(getattr(inner, "pricing_model", "") or "").lower()
    if pricing_model != "cpm":
        return []
    rate = getattr(inner, "rate", None)
    if rate is None or float(rate) <= 0:
        return []

    cpm = float(rate)
    implied_impressions = int(total_budget / cpm * 1000)
    if implied_impressions <= 0:
        return []

    # Resolve flight window from the request
    flight_start = getattr(req, "flight_start_date", None) or getattr(req, "start_time", None)
    flight_end = getattr(req, "flight_end_date", None) or getattr(req, "end_time", None)
    if flight_start is None or flight_end is None:
        return []

    if isinstance(flight_start, datetime):
        start_d = flight_start.date()
    else:
        start_d = flight_start
    if isinstance(flight_end, datetime):
        end_d = flight_end.date()
    else:
        end_d = flight_end

    # Get forecast manager from the adapter. Fail-open if absent.
    orders_manager = getattr(adapter, "orders_manager", None)
    client_manager = getattr(orders_manager, "client_manager", None)
    if client_manager is None:
        return []

    from src.adapters.gam.managers.forecast import GAMForecastManager

    advertiser_id = getattr(orders_manager, "advertiser_id", None)
    forecast_manager = GAMForecastManager(client_manager=client_manager, advertiser_id=advertiser_id)

    available = forecast_manager.get_available_units(
        ad_unit_ids=ad_unit_ids,
        start_date=start_d,
        end_date=end_d,
        line_item_type=impl_config.get("line_item_type", "STANDARD"),
        cost_type=impl_config.get("cost_type", "CPM"),
        include_descendants=bool(impl_config.get("include_descendants", True)),
    )
    if available is None:
        # Forecast call failed or returned null — fail open. The buy
        # proceeds without warning. The adapter call below will run as
        # before; if delivery actually under-paces, the buyer sees that
        # via get_media_buy_delivery.
        return []

    if implied_impressions <= available:
        return []

    overbook_pct = round((implied_impressions / available - 1) * 100, 1) if available > 0 else 100.0
    return [
        {
            "code": "inventory_overbook_minor",
            "message": (
                f"Implied impression goal ({implied_impressions:,}) exceeds GAM availability "
                f"forecast ({available:,}) by {overbook_pct}%. The line item will accept the "
                f"buy but may land in INVENTORY_RELEASED until publisher resolves the overbook "
                f"in GAM admin."
            ),
            "details": {
                "goal_impressions": implied_impressions,
                "forecast_available_impressions": available,
                "overbook_percent": overbook_pct,
                "product_id": product.product_id,
            },
        }
    ]


def _creative_ids_for_packages(packages: list[MediaPackage]) -> set[str]:
    all_creative_ids: set[str] = set()
    for package in packages:
        pkg_creative_ids = _get_requested_creative_ids(package)
        if pkg_creative_ids:
            all_creative_ids.update(pkg_creative_ids)
    return all_creative_ids


def _load_creatives_by_id(session: "Session", tenant_id: str, creative_ids: set[str]) -> dict[str, Any]:
    if not creative_ids:
        return {}
    from sqlalchemy import select

    from src.core.database.models import Creative as DBCreative

    stmt = select(DBCreative).where(DBCreative.tenant_id == tenant_id, DBCreative.creative_id.in_(list(creative_ids)))
    return {str(creative.creative_id): creative for creative in session.scalars(stmt).all()}


def _validate_creatives_before_adapter_call(
    packages: list[MediaPackage],
    tenant_id: str,
    session: "Session | None" = None,
) -> None:
    """Validate all creatives have required fields BEFORE calling adapter.

    This prevents GAM order creation when creatives are invalid, enabling
    true all-or-nothing behavior without rollback complexity.

    Fetches format specifications to determine correct asset structure per format.

    Args:
        packages: List of Package objects with creative_ids
        tenant_id: Tenant ID for database lookup
        session: SQLAlchemy session (from UoW).

    Raises:
        AdCPValidationError: If any creative is missing required fields (URL, dimensions)
    """
    from sqlalchemy import select

    if session is None:
        raise ValueError("session is required for _validate_creatives_before_adapter_call")

    all_creative_ids = _creative_ids_for_packages(packages)
    if not all_creative_ids:
        return

    creatives_by_id = _load_creatives_by_id(session, tenant_id, all_creative_ids)
    creatives_list = list(creatives_by_id.values())

    # Validate each creative has required fields
    validation_errors = []
    for creative in creatives_list:
        creative_data = creative.data or {}

        # BR-RULE-026: Reject creatives in terminal error states
        if hasattr(creative, "status") and creative.status in ("error", "rejected"):
            validation_errors.append(
                f"Creative {creative.creative_id} has status '{creative.status}' and cannot be used in a media buy"
            )
            continue

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
        url, width, height = extract_media_url_and_dimensions(creative_data, format_spec)
        format_id = str(creative.format or "")
        format_type = str(getattr(format_spec, "type", "") or "").lower()
        requires_dimensions = format_type not in {"audio"} and "vast" not in format_id.lower()

        if not url:
            validation_errors.append(
                f"Reference creative {creative.creative_id} (format={creative.format}) "
                f"missing required URL field in assets"
            )
        if requires_dimensions and (not width or not height):
            validation_errors.append(
                f"Reference creative {creative.creative_id} missing dimensions (width={width}, height={height})"
            )

    # --- Format compatibility check: creative format vs product accepted formats ---
    # Build creative_id -> namespaced format mapping from fetched creatives
    creative_format_map: dict[str, dict[str, Any]] = {}
    for creative in creatives_list:
        if creative.format:
            format_ref: dict[str, Any] = {
                "agent_url": creative.agent_url,
                "id": str(creative.format),
            }
            format_ref.update(creative.format_parameters or {})
            creative_format_map[creative.creative_id] = format_ref

    # Collect all product_ids from packages that have creatives
    product_ids_needed: set[str] = set()
    for package in packages:
        pkg_creative_ids = _get_requested_creative_ids(package)
        if pkg_creative_ids and package.product_id:
            product_ids_needed.add(package.product_id)

    if product_ids_needed:
        from src.core.database.models import Product as DBProduct

        product_stmt = select(DBProduct).where(
            DBProduct.tenant_id == tenant_id, DBProduct.product_id.in_(list(product_ids_needed))
        )
        products_list = list(session.scalars(product_stmt).all())

        # Build product_id -> accepted namespaced formats
        product_format_map: dict[str, list[Any]] = {}
        for product in products_list:
            product_format_map[product.product_id] = list(product.format_ids or [])

        # Check each package's creatives against its product's accepted formats
        for package in packages:
            pkg_creative_ids = _get_requested_creative_ids(package)
            if not pkg_creative_ids or not package.product_id:
                continue

            accepted = product_format_map.get(package.product_id)
            if accepted is None or not accepted:
                continue  # Product not found or has no formats — skip format check

            for cid in pkg_creative_ids:
                creative_format = creative_format_map.get(cid)
                if creative_format and not _matching_supported_format(creative_format, accepted):
                    accepted_display = sorted(_format_ref_display(fmt) for fmt in accepted)
                    validation_errors.append(
                        f"Creative {cid} has format '{_format_ref_display(creative_format)}' which is not accepted by "
                        f"product {package.product_id} (accepted formats: {accepted_display})"
                    )

    if validation_errors:
        error_msg = (
            "Cannot create media buy with invalid creatives. "
            "The following creatives have validation errors:\n"
            + "\n".join(f"  • {err}" for err in validation_errors)
            + "\n\nReference creatives must have dimensions (width/height) and a content URL "
            "matching their format specification. "
            + "Creative formats must match the product's accepted formats. "
            + "Generative creatives will be converted to reference formats during campaign creation. "
            + "Please ensure reference creatives are properly synced before creating media buys."
        )
        logger.error(f"[PRE-VALIDATION] {error_msg}")
        raise AdCPValidationError(
            error_msg, details={"error_code": "INVALID_CREATIVES", "creative_errors": validation_errors}
        )


def _is_springserve_tag_mode(adapter: Any) -> bool:
    return str(getattr(adapter, "adapter_name", "") or "").lower() == "springserve" and (
        getattr(adapter, "demand_class", None) == "tag"
    )


def _tenant_uses_springserve(tenant_context: Any) -> bool:
    return _tenant_adapter_name(tenant_context) == "springserve"


def _tenant_uses_google_ad_manager(tenant_context: Any) -> bool:
    return _tenant_adapter_name(tenant_context) in {"google_ad_manager", "gam"}


def _tenant_adapter_name(tenant_context: Any) -> str:
    if isinstance(tenant_context, dict):
        adapter_name = tenant_context.get("ad_server")
    else:
        adapter_name = getattr(tenant_context, "ad_server", None)
    return str(adapter_name or "").lower()


def _existing_springserve_vast_endpoint_url(package: MediaPackage) -> str | None:
    impl = getattr(package, "implementation_config", None) or {}
    springserve_config = impl.get("springserve", impl) if isinstance(impl, dict) else {}
    if not isinstance(springserve_config, dict):
        return None
    extras = springserve_config.get("extra_demand_tag_fields") or {}
    if not isinstance(extras, dict):
        return None
    value = extras.get("vast_endpoint_url")
    return str(value) if value else None


def _package_with_springserve_vast_endpoint_url(package: MediaPackage, url: str) -> MediaPackage:
    impl = dict(getattr(package, "implementation_config", None) or {})
    raw_springserve_config = impl.get("springserve")
    if isinstance(raw_springserve_config, dict):
        springserve_config = dict(raw_springserve_config)
    else:
        springserve_config = dict(impl)

    extras = dict(springserve_config.get("extra_demand_tag_fields") or {})
    extras["vast_endpoint_url"] = url
    springserve_config["extra_demand_tag_fields"] = extras
    impl["springserve"] = springserve_config
    return package.model_copy(update={"implementation_config": impl})


def _prepare_springserve_tag_mode_packages(
    adapter: Any,
    packages: list[MediaPackage],
    tenant_id: str,
    session: "Session",
) -> list[MediaPackage]:
    """Inject assigned VAST creative URLs into SpringServe tag-mode packages.

    SpringServe ``demand_class=tag`` requires ``vast_endpoint_url`` on the
    Demand Tag create call. The later creative upload/bind phase is too late,
    so resolve package creative assignments before ``adapter.create_media_buy``.
    """
    if not _is_springserve_tag_mode(adapter):
        return packages

    all_creative_ids = _creative_ids_for_packages(packages)
    creatives_by_id = _load_creatives_by_id(session, tenant_id, all_creative_ids)
    prepared: list[MediaPackage] = []
    validation_errors: list[str] = []

    for package in packages:
        creative_ids = _get_requested_creative_ids(package)
        if not creative_ids:
            if _existing_springserve_vast_endpoint_url(package):
                prepared.append(package)
                continue
            validation_errors.append(
                f"Package {package.package_id} uses SpringServe demand_class=tag but has no assigned audio_vast creative"
            )
            prepared.append(package)
            continue

        if len(creative_ids) != 1:
            validation_errors.append(
                f"Package {package.package_id} uses SpringServe demand_class=tag and requires exactly one VAST creative "
                f"(got {len(creative_ids)})"
            )
            prepared.append(package)
            continue

        creative_id = creative_ids[0]
        creative = creatives_by_id.get(creative_id)
        if creative is None:
            validation_errors.append(f"Creative {creative_id} not found for SpringServe tag-mode package")
            prepared.append(package)
            continue

        format_id = str(getattr(creative, "format", "") or "")
        if "vast" not in format_id.lower():
            validation_errors.append(
                f"Creative {creative_id} has format '{format_id}', but SpringServe demand_class=tag requires a VAST format"
            )
            prepared.append(package)
            continue

        format_spec = _get_format_spec_sync(creative.agent_url, format_id) if format_id else None
        asset = build_adapter_asset_from_stored_creative(
            creative,
            package_id=package.package_id,
            format_spec=format_spec,
        )
        vast_url = asset.get("url")
        if not vast_url:
            validation_errors.append(f"Creative {creative_id} is missing the VAST URL required by SpringServe tag mode")
            prepared.append(package)
            continue

        validate_url = getattr(adapter, "_validate_creative_remote_url", None)
        url_error = validate_url(str(vast_url)) if callable(validate_url) else None
        if url_error:
            validation_errors.append(f"Creative {creative_id} VAST URL is invalid for SpringServe: {url_error}")
            prepared.append(package)
            continue

        prepared.append(_package_with_springserve_vast_endpoint_url(package, str(vast_url)))

    if validation_errors:
        error_msg = "Cannot create SpringServe tag-mode media buy with invalid creatives:\n" + "\n".join(
            f"  • {err}" for err in validation_errors
        )
        raise AdCPValidationError(error_msg, details={"error_code": "INVALID_CREATIVES"})

    return prepared


def _execute_adapter_media_buy_creation(
    request: CreateMediaBuyRequest,
    packages: list[MediaPackage],
    start_time: datetime,
    end_time: datetime,
    package_pricing_info: dict[str, dict[str, Any]],
    principal: Principal,
    testing_ctx: TestingContext | None = None,
    tenant: Any = None,
) -> schemas.CreateMediaBuyResponse:
    """Execute adapter's create_media_buy call.

    This function is shared between auto-approval and manual approval flows
    to ensure consistent adapter behavior across all adapters (GAM, Mock, etc.).

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
    # Get adapter using helper
    dry_run = testing_ctx.dry_run if testing_ctx else False
    adapter = get_adapter(principal, dry_run=dry_run, testing_context=testing_ctx, tenant=tenant)

    # Call adapter with detailed error logging
    try:
        response = adapter.create_media_buy(request, packages, start_time, end_time, package_pricing_info)

        # Log based on response type
        if isinstance(response, CreateMediaBuyError):
            error_count = len(response.errors) if response.errors else 0
            logger.error(f"[ADAPTER] create_media_buy returned error response: {error_count} error(s)")
            if response.errors:
                for err in response.errors:
                    logger.error(f"[ADAPTER]   Error: {err.code} - {err.message}")
        elif isinstance(response, CreateMediaBuySuccess):
            logger.info(
                f"[ADAPTER] create_media_buy succeeded: {response.media_buy_id} "
                f"with {len(response.packages) if response.packages else 0} packages"
            )
            if response.packages:
                for i, pkg in enumerate(response.packages):
                    # response.packages are now always Package objects
                    logger.info(f"[ADAPTER] Response package {i}: {pkg.package_id}")
        else:
            logger.info(f"[ADAPTER] create_media_buy submitted async task: {response.task_id}")
        return response
    except Exception as adapter_error:
        import traceback

        error_traceback = traceback.format_exc()
        logger.error(f"[ADAPTER] create_media_buy failed:\n{error_traceback}")
        raise


def _apply_account_advertiser_mapping(
    identity: ResolvedIdentity,
    principal: Principal,
    *,
    dry_run: bool,
) -> str | None:
    """Resolve account advertiser routing and stamp it on the adapter principal."""
    from src.core.helpers.account_provisioning import resolve_account_advertiser

    account_advertiser_id = resolve_account_advertiser(identity, dry_run=dry_run)
    if account_advertiser_id is None:
        return None

    mappings = dict(principal.platform_mappings or {})
    gam_block = dict(mappings.get("google_ad_manager") or {})
    gam_block["advertiser_id"] = account_advertiser_id
    mappings["google_ad_manager"] = gam_block
    principal.platform_mappings = mappings
    return account_advertiser_id


def execute_approved_media_buy(media_buy_id: str, tenant_id: str) -> tuple[bool, str | None]:
    """Execute adapter creation for a manually approved media buy.

    This function is called after a media buy has been manually approved
    (either via media buy approval or creative approval). It reconstructs
    the original request from the database and calls the adapter to create
    the order/line items in the external ad server (GAM, etc.).

    This ensures the same adapter logic runs for both auto-approved and
    manually approved media buys.

    Args:
        media_buy_id: The media buy ID to execute
        tenant_id: The tenant ID for context

    Returns:
        Tuple of (success: bool, error_message: str | None)
    """
    from sqlalchemy import select

    from src.core.database.models import MediaPackage as DBMediaPackage
    from src.core.database.repositories import MediaBuyUoW

    logger.info(f"[APPROVAL] Executing adapter creation for approved media buy {media_buy_id}")

    # Set tenant context (required for adapter helpers to work)
    from src.core.config_loader import set_current_tenant
    from src.core.database.models import Tenant

    try:
        # Load tenant and set context — single UoW for all reads
        with MediaBuyUoW(tenant_id) as uow:
            # FIXME(salesagent-9f2): raw session usages below should migrate to repository methods
            assert uow.session is not None
            session = uow.session
            stmt_tenant = select(Tenant).filter_by(tenant_id=tenant_id)
            tenant_obj = session.scalars(stmt_tenant).first()

            if not tenant_obj:
                error_msg = f"Tenant {tenant_id} not found"
                logger.error(f"[APPROVAL] {error_msg}")
                return False, error_msg

            # Set tenant ContextVar via standard config_loader boundary
            from src.core.config_loader import get_tenant_by_id
            from src.core.utils.tenant_utils import serialize_tenant_to_dict

            tenant_context = get_tenant_by_id(tenant_id) or serialize_tenant_to_dict(tenant_obj)
            set_current_tenant(tenant_context)
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
            # FIXME(salesagent-rva2): migrate to uow.media_buys.get_packages()
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
            from src.core.inventory_profile_projection import project_visible_inventory_profile_product

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
                        .options(
                            selectinload(ProductModel.pricing_options),
                            selectinload(ProductModel.inventory_profile),
                        )
                    )
                    product = session.scalars(stmt_product).first()

                    if not product:
                        product = project_visible_inventory_profile_product(session, tenant_id, product_id)

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

                        targeting_overlay = Targeting.model_validate_persisted(targeting_raw)

                    # Create MediaPackage object (what adapters expect)
                    # Note: Product model has 'formats' not 'format_ids'
                    if not package_id:
                        error_msg = f"Package ID missing for package in media buy {media_buy_id}"
                        logger.error(f"[APPROVAL] {error_msg}")
                        return False, error_msg

                    delivery_type_str = (
                        product.delivery_type.value
                        if hasattr(product.delivery_type, "value")
                        else str(product.delivery_type)
                    )

                    # Validate delivery_type is a valid literal
                    if delivery_type_str not in ["guaranteed", "non_guaranteed"]:
                        delivery_type_str = "non_guaranteed"  # Default fallback

                    # Convert formats to FormatId objects with comprehensive validation
                    from src.core.schemas import FormatId as FormatIdType

                    format_ids_list: list[FormatIdType] = []
                    formats = product.effective_format_ids or []

                    logger.debug(f"[APPROVAL] Converting {len(formats)} formats for package {package_id}")

                    for idx, fmt in enumerate(formats):
                        try:
                            validated = FormatIdType.model_validate(fmt)
                            url_str = str(validated.agent_url)
                            if not url_str.startswith(("http://", "https://")):
                                raise ValueError(f"agent_url must be HTTP(S), got: {url_str}")
                            format_ids_list.append(validated)
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

                    product_implementation_config = _dict_implementation_config(product.effective_implementation_config)

                    media_package = MediaPackage(
                        package_id=package_id,
                        name=package_config.get("name") or product.name,
                        delivery_type=delivery_type_str,
                        impressions=impressions,
                        format_ids=cast(list[Any], format_ids_list),
                        targeting_overlay=targeting_overlay,
                        product_id=product_id,
                        budget=budget,
                        creative_ids=package_config.get("creative_ids"),
                        implementation_config=product_implementation_config,
                    )
                    packages.append(media_package)

                    logger.info(
                        f"[APPROVAL] Reconstructed MediaPackage {package_id}: "
                        f"name={media_package.name}, rate={cpm}, impressions={impressions}"
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

            principal = get_principal_object(media_buy.principal_id, tenant_id=tenant_id)
            if not principal:
                error_msg = f"Principal {media_buy.principal_id} not found"
                logger.error(f"[APPROVAL] {error_msg}")
                return False, error_msg

            account = None
            media_buy_account_id = getattr(media_buy, "account_id", None)
            if isinstance(media_buy_account_id, str) and media_buy_account_id:
                from src.core.database.repositories.account import AccountRepository

                account = AccountRepository(session, tenant_id).get_by_id(media_buy_account_id)
            sandbox_mode = sandbox_mode_for_rows(account=account)
            if sandbox_mode.active:
                logger.info(
                    "[SANDBOX] Approved media buy %s will traffic against sandbox advertiser with zero economics: %s",
                    media_buy_id,
                    sandbox_mode.diagnostic,
                )
                mark_sandbox_trafficking_request(request)
                packages = sandbox_trafficking_packages(packages)
                package_pricing_info = sandbox_trafficking_pricing_info(package_pricing_info)

            # Create testing context (dry_run should be False for approved buys)
            testing_ctx = TestingContext(dry_run=False, test_session_id=None)

            approval_identity = ResolvedIdentity(
                principal_id=media_buy.principal_id,
                tenant_id=tenant_id,
                tenant=tenant_context,
                protocol="mcp",
                account_id=media_buy_account_id if isinstance(media_buy_account_id, str) else None,
            )
            _apply_account_advertiser_mapping(approval_identity, principal, dry_run=False)

            logger.info(
                f"[APPROVAL] Calling adapter for {media_buy_id}: "
                f"{len(packages)} packages, start={start_time}, end={end_time}"
            )

            # PRE-VALIDATE: Check all creatives have required fields BEFORE calling adapter
            # This prevents GAM order creation when creatives are invalid (all-or-nothing approach)
            _validate_creatives_before_adapter_call(packages, tenant_id, session=session)
            if _tenant_uses_springserve(tenant_context):
                adapter_for_preparation = get_adapter(
                    principal, dry_run=False, testing_context=testing_ctx, tenant=tenant_context
                )
                packages = _prepare_springserve_tag_mode_packages(
                    adapter_for_preparation,
                    packages,
                    tenant_id,
                    session=session,
                )

        # Execute adapter creation (outside session to avoid conflicts)
        response = _execute_adapter_media_buy_creation(
            request,
            packages,
            start_time,
            end_time,
            package_pricing_info,
            principal,
            testing_ctx,
            tenant=tenant_context,
        )

        # Check if adapter returned an error response
        if isinstance(response, CreateMediaBuyError):
            # Adapter returned error response (not an exception)
            error_messages = [str(err) for err in response.errors] if response.errors else ["Unknown error"]
            error_msg = "; ".join(error_messages)
            logger.error(f"[APPROVAL] Adapter creation failed for {media_buy_id}: {error_msg}")
            return False, error_msg
        if isinstance(response, CreateMediaBuySubmitted):
            error_msg = f"Adapter submitted async task {response.task_id} during approval execution"
            logger.error(f"[APPROVAL] {error_msg}")
            return False, error_msg

        logger.info(f"[APPROVAL] Adapter creation succeeded for {media_buy_id}: {response.media_buy_id}")

        # Persist platform_line_item_ids from adapter response (same as auto-approval path)
        # Adapters (GAM, Broadstreet) attach _platform_line_item_ids to the response object
        # These are required for update_media_buy operations (budget updates, pause/resume)
        platform_line_item_ids = getattr(response, "_platform_line_item_ids", {})
        if platform_line_item_ids:
            logger.info(f"[APPROVAL] Found platform_line_item_ids mapping: {platform_line_item_ids}")
            with MediaBuyUoW(tenant_id) as uow_plids:
                # FIXME(salesagent-rva2): migrate to uow.media_buys.update_package_config()
                assert uow_plids.session is not None
                plid_session = uow_plids.session
                for pkg_id, line_item_id in platform_line_item_ids.items():
                    package_stmt = select(DBMediaPackage).filter_by(media_buy_id=media_buy_id, package_id=pkg_id)
                    pkg_record: DBMediaPackage | None = plid_session.scalars(package_stmt).first()
                    if pkg_record:
                        pkg_record.package_config["platform_line_item_id"] = str(line_item_id)
                        from sqlalchemy.orm import attributes

                        attributes.flag_modified(pkg_record, "package_config")
                        logger.info(f"[APPROVAL] Updated package {pkg_id} with platform_line_item_id: {line_item_id}")
                    else:
                        logger.warning(f"[APPROVAL] Could not find package {pkg_id} to save platform_line_item_id")
                logger.info("[APPROVAL] Saved platform_line_item_ids to database")
        else:
            logger.info("[APPROVAL] No platform_line_item_ids found on response object")

        # Upload and associate inline creatives if any exist
        # This handles inline creatives that were uploaded during initial media buy creation
        with MediaBuyUoW(tenant_id) as uow2:
            # FIXME(salesagent-9f2): creative handling should use repository methods
            assert uow2.session is not None
            session = uow2.session
            from src.core.database.models import Creative as CreativeModel
            from src.core.database.models import CreativeAssignment

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

                # Load all creatives (scoped to tenant)
                all_creative_ids = list(packages_by_creative.keys())
                stmt_creatives = select(CreativeModel).filter(
                    CreativeModel.tenant_id == tenant_id,
                    CreativeModel.creative_id.in_(all_creative_ids),
                )
                creatives = session.scalars(stmt_creatives).all()

                # Create creative map
                creative_map = {c.creative_id: c for c in creatives}

                # Resolve the per-tenant pre-approval gate once per buy approval
                # (rather than per creative) since the flag changes only on
                # operator action. (#145)
                from src.core.feature_flags import is_creative_pre_approval_gate_enabled

                gate_enabled = is_creative_pre_approval_gate_enabled(tenant_context)

                # Build assets list for adapter and collect all validation errors
                assets = []
                all_validation_errors = []
                gated_creative_ids: list[str] = []
                for creative_id, package_assignment_list in packages_by_creative.items():
                    creative = creative_map.get(creative_id)
                    if not creative:
                        logger.warning(f"[APPROVAL] Creative {creative_id} not found in database")
                        continue

                    # Pre-approval gate: hold back creatives that haven't
                    # cleared local human review. They'll be pushed to the
                    # ad server retroactively by approve_creative when the
                    # operator flips status to 'approved'. (#145)
                    if gate_enabled and creative.status == "pending_review":
                        gated_creative_ids.append(creative_id)
                        logger.info(
                            f"[APPROVAL] Pre-approval gate held back creative {creative_id} "
                            f"(status=pending_review). Adapter upload deferred to local approve."
                        )
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
                    url, width, height = extract_media_url_and_dimensions(creative_data, format_spec)

                    # Extract click-through URL separately from media URL (with macro substitution)
                    click_url = extract_click_url(creative_data, format_spec)

                    # Extract impression tracker URL
                    impression_tracker_url = extract_impression_tracker_url(creative_data, format_spec)

                    asset = {
                        "creative_id": creative.creative_id,
                        # Include full assignment info with weights for adapter creative rotation (AdCP 2.5)
                        "package_assignments": package_assignment_list,
                        "width": width,
                        "height": height,
                        "url": url,
                        "click_url": click_url,  # Separate click-through URL for GAM destinationUrl
                        "asset_type": creative_data.get("asset_type", "image"),
                        "name": creative.name or f"Creative {creative.creative_id}",
                    }

                    # Add impression tracker URL in the format expected by GAM adapter
                    if impression_tracker_url:
                        asset["delivery_settings"] = {"tracking_urls": {"impression": [impression_tracker_url]}}

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
                    adapter = get_adapter(principal, dry_run=False, testing_context=testing_ctx, tenant=tenant_context)

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

        if _tenant_uses_google_ad_manager(tenant_context):
            # After creatives are uploaded (or skipped), retry order approval.
            # GAM may still be processing inventory forecasts (NO_FORECAST_YET),
            # and creatives may have been uploaded after the initial approval attempt.
            logger.info(f"[APPROVAL] Attempting to approve order {response.media_buy_id} in GAM")
            try:
                from src.adapters.gam.managers.orders import GAMOrderApprovalPermissionDenied

                adapter = get_adapter(principal, dry_run=False, testing_context=testing_ctx, tenant=tenant_context)
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
            except GAMOrderApprovalPermissionDenied:
                # Expected: service account has no approval permission.
                # Background status polling was already started during adapter creation.
                # The media buy will be activated once a human approves the order in GAM.
                logger.info(
                    f"[APPROVAL] Service account cannot approve GAM order {response.media_buy_id} — "
                    f"order will remain in DRAFT until approved externally. "
                    f"Background status polling is running."
                )
                # Skip setting media buy to 'active' — the background poller handles that.
                return True, None
            except Exception as approval_error:
                # Approval exception - return failure
                error_msg = f"Failed to approve order {response.media_buy_id}: {str(approval_error)}"
                logger.error(f"[APPROVAL] {error_msg}", exc_info=True)
                return False, error_msg
        else:
            logger.info("[APPROVAL] Non-GAM adapter does not require order approval, skipping")

        # Update media buy status to 'active' after successful adapter execution
        # (UC-002:437 — "updates the media buy status to active")
        with MediaBuyUoW(tenant_id) as uow3:
            assert uow3.media_buys is not None
            uow3.media_buys.update_status(media_buy_id, "active")
            logger.info(f"[APPROVAL] Updated media buy {media_buy_id} status to 'active'")

        return True, None

    except Exception as e:
        import traceback

        error_traceback = traceback.format_exc()
        error_msg = f"Adapter creation failed: {str(e)}"
        logger.error(f"[APPROVAL] {error_msg}\n{error_traceback}")
        return False, error_msg


def push_creative_to_existing_buy(
    *,
    creative_id: str,
    media_buy_id: str,
    tenant_id: str,
) -> tuple[bool, str | None]:
    """Push a single approved creative to an already-active GAM line item.

    Used by ``approve_creative`` (admin blueprint) when the per-tenant
    pre-approval gate is enabled and the operator just approved a
    creative whose buy is already live in the ad server. The buy
    approval flow (#145 gate at ``execute_approved_media_buy``) skipped
    this creative because its local status was ``pending_review``;
    now that a human has approved it, push it to the live line item.

    Returns ``(success, error_message)``. ``error_message`` is non-None
    only on failure. Local approval has already succeeded by the time
    this fires — failures here log loudly but don't roll back the local
    state. The operator can re-trigger by re-clicking Approve
    (idempotent at the adapter layer for already-uploaded creatives).
    """
    from sqlalchemy import select

    from src.core.config_loader import get_tenant_by_id, set_current_tenant
    from src.core.database.models import (
        Creative as CreativeModel,
    )
    from src.core.database.models import (
        CreativeAssignment,
    )
    from src.core.database.models import (
        Tenant as ModelTenant,
    )
    from src.core.database.repositories import MediaBuyUoW

    try:
        with MediaBuyUoW(tenant_id) as uow:
            assert uow.session is not None
            session = uow.session

            tenant_obj = session.scalars(select(ModelTenant).filter_by(tenant_id=tenant_id)).first()
            if not tenant_obj:
                return False, f"Tenant {tenant_id} not found"

            # Set tenant ContextVar so adapter helpers resolve config correctly.
            from src.core.utils.tenant_utils import serialize_tenant_to_dict

            tenant_context = get_tenant_by_id(tenant_id) or serialize_tenant_to_dict(tenant_obj)
            set_current_tenant(tenant_context)

            creative = session.scalars(
                select(CreativeModel).filter_by(tenant_id=tenant_id, creative_id=creative_id)
            ).first()
            if not creative:
                return False, f"Creative {creative_id} not found"

            assignment = session.scalars(
                select(CreativeAssignment).filter_by(
                    tenant_id=tenant_id, creative_id=creative_id, media_buy_id=media_buy_id
                )
            ).first()
            if not assignment:
                return False, (f"No assignment of creative {creative_id} to media buy {media_buy_id} — nothing to push")

            # ``get_adapter`` requires the Pydantic ``Principal`` schema (with
            # decoded platform_mappings); ``get_principal_object`` returns that
            # shape from the ORM model.
            principal = get_principal_object(creative.principal_id, tenant_id=tenant_id)
            if not principal:
                return False, f"Principal {creative.principal_id} not found"

            adapter = get_adapter(principal, dry_run=False, tenant=tenant_context)
            if not (hasattr(adapter, "creatives_manager") and adapter.creatives_manager):
                return False, "Adapter does not support creative upload"

            # Build the asset dict — same shape as execute_approved_media_buy.
            from src.core.format_resolver import get_format
            from src.core.helpers.creative_helpers import (
                extract_click_url,
                extract_impression_tracker_url,
                extract_media_url_and_dimensions,
            )

            creative_data = creative.data or {}
            try:
                format_spec = get_format(
                    str(creative.format),
                    agent_url=creative.agent_url,
                    tenant_id=tenant_id,
                    product_id=None,
                )
            except Exception as e:
                logger.warning(f"[GATE-PUSH] Could not load format spec for {creative_id}: {e}")
                format_spec = None

            url, width, height = extract_media_url_and_dimensions(creative_data, format_spec)
            click_url = extract_click_url(creative_data, format_spec)
            impression_tracker_url = extract_impression_tracker_url(creative_data, format_spec)

            if not url or not width or not height:
                return False, (
                    f"Creative {creative_id} cannot be pushed: missing url/width/height "
                    f"(width={width}, height={height}, url={'set' if url else 'missing'})"
                )

            asset: dict[str, Any] = {
                "creative_id": creative.creative_id,
                "package_assignments": [{"package_id": assignment.package_id, "weight": assignment.weight or 100}],
                "width": width,
                "height": height,
                "url": url,
                "click_url": click_url,
                "asset_type": creative_data.get("asset_type", "image"),
                "name": creative.name or f"Creative {creative.creative_id}",
            }
            if impression_tracker_url:
                asset["delivery_settings"] = {"tracking_urls": {"impression": [impression_tracker_url]}}

            # Resolve the GAM order id. Native AdCP buys use the GAM
            # order id as their media_buy_id; gam_import buys carry it
            # on external_id.
            from src.core.database.models import MediaBuy as DBMediaBuy

            media_buy_row = session.scalars(
                select(DBMediaBuy).filter_by(tenant_id=tenant_id, media_buy_id=media_buy_id)
            ).first()
            if media_buy_row is None:
                return False, f"Media buy {media_buy_id} not found"
            gam_order_id = media_buy_row.external_id or media_buy_id

            try:
                statuses = adapter.creatives_manager.add_creative_assets(gam_order_id, [asset], datetime.now(UTC))
            except Exception as e:
                logger.error(
                    f"[GATE-PUSH] Adapter raised pushing creative {creative_id} to buy {media_buy_id}: {e}",
                    exc_info=True,
                )
                return False, str(e)

            # Inspect statuses: a single creative was uploaded; check it.
            for status in statuses:
                if status.creative_id == creative_id and status.status == "failed":
                    return False, status.message or "Adapter reported upload failure"

            logger.info(
                f"[GATE-PUSH] Successfully pushed creative {creative_id} to live buy "
                f"{media_buy_id} (GAM order {gam_order_id})"
            )
            return True, None
    except Exception as e:
        logger.error(
            f"[GATE-PUSH] Unexpected error pushing creative {creative_id} to buy {media_buy_id}: {e}",
            exc_info=True,
        )
        return False, str(e)


def _unwrap_pricing_option(option: Any) -> Any:
    """Unwrap adcp RootModel pricing options while preserving ORM objects."""
    return getattr(option, "root", option)


def _pricing_option_wire_id(option: Any) -> str:
    """Return the pricing_option_id shape exposed by get_products."""
    option = _unwrap_pricing_option(option)
    fixed_str = "fixed" if option.is_fixed else "auction"
    return f"{option.pricing_model.lower()}_{option.currency.lower()}_{fixed_str}"


def _uses_first_pricing_option_alias(product: Any, pricing_option_id: str | None) -> bool:
    """Return true for SDK fixture aliases that mean "first pricing option"."""
    if not pricing_option_id:
        return False
    normalized = pricing_option_id.lower()
    if normalized == "default":
        return True
    if normalized == "legacy_conversion":
        return True
    return normalized == "test-pricing" and getattr(product, "product_id", None) == "test-product"


def _validate_pricing_model_selection(
    package: Package | PackageRequest | AdcpPackageRequest,
    product: Any,  # ProductModel from database
    campaign_currency: str | None,
    *,
    sandbox_trafficking: bool = False,
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
        raise AdCPValidationError(
            f"Product {product.product_id} has no pricing_options configured. This is a data integrity error.",
            details={"error_code": "PRICING_ERROR"},
            recovery="terminal",
        )

    # Determine which pricing option to use
    # Priority: pricing_option_id (AdCP spec) > pricing_model (legacy)
    pricing_option_id = package.pricing_option_id
    pricing_model_fallback = getattr(package, "pricing_model", None)  # Legacy field
    if _uses_first_pricing_option_alias(product, pricing_option_id):
        pricing_option_id = None

    # If neither specified, use first pricing option from product. A
    # campaign-level currency hint narrows legacy conversion aliases without
    # mutating the request package into a concrete pricing_option_id too early.
    if not pricing_option_id and not pricing_model_fallback:
        candidate_options = [_unwrap_pricing_option(option) for option in product.pricing_options]
        if campaign_currency:
            currency_matches = [option for option in candidate_options if option.currency == campaign_currency]
            if currency_matches:
                candidate_options = currency_matches
        first_option = candidate_options[0]
        return {
            "pricing_model": first_option.pricing_model,
            "rate": float(first_option.rate) if first_option.rate else None,
            "currency": first_option.currency or campaign_currency or "USD",
            "is_fixed": first_option.is_fixed,
            "bid_price": float(package.bid_price) if package.bid_price else None,
        }

    # Find matching pricing option
    selected_option = None
    if pricing_option_id:
        for option in product.pricing_options:
            opt_inner = _unwrap_pricing_option(option)
            option_id = _pricing_option_wire_id(opt_inner)

            # Try matching by pricing_option_id first (AdCP spec)
            if pricing_option_id.lower() == option_id.lower():
                selected_option = opt_inner
                break
    elif pricing_model_fallback:
        matching_options = [
            _unwrap_pricing_option(option)
            for option in product.pricing_options
            if _unwrap_pricing_option(option).pricing_model == pricing_model_fallback.value
        ]
        if campaign_currency:
            matching_options = [option for option in matching_options if option.currency == campaign_currency]
        elif len({option.currency for option in matching_options if option.currency}) > 1:
            raise AdCPValidationError(
                f"Product {product.product_id} offers {pricing_model_fallback.value} pricing in multiple currencies. "
                "Specify pricing_option_id instead of legacy pricing_model.",
                details={"error_code": "PRICING_ERROR"},
            )
        selected_option = matching_options[0] if matching_options else None

    if not selected_option:
        # Show available options in same format as matching logic expects
        available_options = [
            f"{_pricing_option_wire_id(opt)} ({_unwrap_pricing_option(opt).pricing_model} - {_unwrap_pricing_option(opt).currency})"
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
        raise AdCPValidationError(error_msg, details={"error_code": "PRICING_ERROR"})

    # Validate auction pricing
    if not selected_option.is_fixed and not sandbox_trafficking:
        if not package.bid_price:
            raise AdCPValidationError(
                f"Package requires bid_price for auction-based {selected_option.pricing_model} pricing. "
                f"Floor price: {selected_option.price_guidance.get('floor') if selected_option.price_guidance else 'N/A'}",
                details={"error_code": "PRICING_ERROR"},
            )

        floor_price = (
            Decimal(str(selected_option.price_guidance.get("floor", 0)))
            if selected_option.price_guidance
            else Decimal("0")
        )
        bid_decimal = Decimal(str(package.bid_price))

        if bid_decimal < floor_price:
            raise AdCPValidationError(
                f"Bid price {package.bid_price} is below floor price {floor_price} for {selected_option.pricing_model} pricing",
                details={"error_code": "PRICING_ERROR"},
            )

    # Validate fixed pricing has rate
    if selected_option.is_fixed and not selected_option.rate and not sandbox_trafficking:
        raise AdCPValidationError(
            f"Product {product.product_id} pricing option has is_fixed=true but no rate specified",
            details={"error_code": "PRICING_ERROR"},
            recovery="terminal",
        )

    # Validate minimum spend per package
    if selected_option.min_spend_per_package and not sandbox_trafficking:
        package_budget = None
        if package.budget is not None:
            # Package.budget is now always float | None (per AdCP spec)
            package_budget = Decimal(str(package.budget))

        if package_budget and package_budget < Decimal(str(selected_option.min_spend_per_package)):
            raise AdCPValidationError(
                f"Package budget {package_budget} {selected_option.currency} is below minimum spend "
                f"{selected_option.min_spend_per_package} {selected_option.currency} for {selected_option.pricing_model}",
                details={"error_code": "PRICING_ERROR"},
            )

    # Return validated pricing information
    return {
        "pricing_model": selected_option.pricing_model,
        "rate": float(selected_option.rate) if selected_option.rate else None,
        "currency": selected_option.currency,
        "is_fixed": selected_option.is_fixed,
        "bid_price": float(package.bid_price) if package.bid_price else None,
    }


def _collect_package_pricing_info_by_index(
    packages: Sequence[PackageRequest | AdcpPackageRequest],
    product_map: dict[str, Any],
    *,
    campaign_currency: str | None = None,
    sandbox_trafficking: bool = False,
) -> dict[int, dict[str, Any]]:
    """Resolve and validate selected pricing for request packages.

    The returned dict is index-keyed because package IDs are generated later in
    the create flow. Callers remap the entries after Package/MediaPackage
    objects have their permanent package IDs.
    """
    package_pricing_info_by_index: dict[int, dict[str, Any]] = {}

    for idx, package in enumerate(packages):
        product_id = package.product_id
        if not product_id or product_id not in product_map:
            continue

        package_pricing_info_by_index[idx] = _validate_pricing_model_selection(
            package=package,
            product=product_map[product_id],
            campaign_currency=campaign_currency,
            sandbox_trafficking=sandbox_trafficking,
        )

    return package_pricing_info_by_index


def _derive_single_request_currency(package_pricing_info_by_index: dict[int, dict[str, Any]]) -> str | None:
    """Return the single selected currency for a media buy, or reject mixed currencies."""
    currencies = sorted(
        {
            str(pricing_info["currency"])
            for pricing_info in package_pricing_info_by_index.values()
            if pricing_info.get("currency")
        }
    )
    if not currencies:
        return None
    if len(currencies) > 1:
        raise ValueError(
            "All packages in a media buy must use the same currency. "
            f"Selected currencies: {', '.join(currencies)}. "
            "Create separate media buys for different currencies."
        )
    return currencies[0]


def _derive_legacy_request_currency(req: CreateMediaBuyRequest) -> str | None:
    """Best-effort currency extraction for deprecated request shapes."""
    legacy_currency = getattr(req, "currency", None)
    if legacy_currency:
        return legacy_currency

    legacy_budget = getattr(req, "budget", None)
    if legacy_budget and hasattr(legacy_budget, "currency"):
        return legacy_budget.currency

    if req.packages and req.packages[0].budget and hasattr(req.packages[0].budget, "currency"):
        return req.packages[0].budget.currency

    return None


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
            raise AdCPValidationError(
                f"Package {package_idx + 1}, format_ids[{idx}]: Plain string format IDs are not supported. "
                f"Per AdCP spec, format_ids must be FormatId objects with {{agent_url, id}}. "
                f'Example: {{"agent_url": "https://creative.adcontextprotocol.org", "id": "{fmt_id}"}}. '
                f"Use list_creative_formats to discover available formats.",
                details={"error_code": "FORMAT_VALIDATION_ERROR"},
            )

        # Coerce to FormatId via Pydantic validation (handles dicts and FormatId objects)
        try:
            validated_fmt = FormatId.model_validate(fmt_id, from_attributes=True)
        except (ValueError, ValidationError) as e:
            raise AdCPValidationError(
                f"Package {package_idx + 1}, format_ids[{idx}]: Invalid format_id structure: {e}",
                details={"error_code": "FORMAT_VALIDATION_ERROR"},
            ) from e
        agent_url = str(validated_fmt.agent_url).rstrip("/")
        format_id = validated_fmt.id

        if not agent_url or not format_id:
            raise AdCPValidationError(
                f"Package {package_idx + 1}, format_ids[{idx}]: FormatId object missing required fields. "
                f"Both agent_url and id are required. Got: agent_url={agent_url!r}, id={format_id!r}",
                details={"error_code": "FORMAT_VALIDATION_ERROR"},
            )

        # VALIDATION: Check agent is registered
        # Normalize incoming agent_url for comparison (strips /mcp, /a2a, /.well-known/*, trailing slashes)
        normalized_agent_url = normalize_agent_url(agent_url)
        if normalized_agent_url not in registered_agent_urls:
            raise AdCPAuthorizationError(
                f"Package {package_idx + 1}, format_ids[{idx}]: Creative agent not registered: {agent_url}. "
                f"Registered agents: {', '.join(sorted(registered_agent_urls))}. "
                f"Contact your administrator to register this creative agent.",
                details={"error_code": "FORMAT_VALIDATION_ERROR"},
            )

        # VALIDATION: Verify format exists on agent
        try:
            format_obj = await registry.get_format(agent_url, format_id)
            if not format_obj:
                raise AdCPNotFoundError(
                    f"Package {package_idx + 1}, format_ids[{idx}]: Format not found on agent. "
                    f"agent_url={agent_url}, format_id={format_id!r}. "
                    f"Use list_creative_formats to discover available formats.",
                    details={"error_code": "FORMAT_VALIDATION_ERROR"},
                    recovery="correctable",
                )
        except Exception as e:
            if isinstance(e, AdCPError):
                raise
            logger.exception(f"Error fetching format {format_id} from {agent_url}: {e}")
            raise AdCPAdapterError(
                f"Package {package_idx + 1}, format_ids[{idx}]: Failed to verify format on agent. "
                f"agent_url={agent_url}, format_id={format_id!r}. Error: {e}",
                details={"error_code": "FORMAT_VALIDATION_ERROR"},
            ) from e

        # Format validated - add to results
        validated_format_ids.append({"agent_url": str(agent_url), "id": format_id})

    return validated_format_ids


from src.services.setup_checklist_service import SetupIncompleteError, validate_setup_complete
from src.services.slack_notifier import get_slack_notifier


def _build_idempotency_hit_result(
    tenant_id: str,
    idempotency_key: str,
    principal_id: str,
    context: ContextObject | None,
) -> CreateMediaBuyResult:
    """Re-query the winner of an idempotency race and return its result.

    Used both for the happy-path idempotency lookup and for the TOCTOU
    race-condition recovery (IntegrityError on commit).
    """
    from src.core.database.repositories import MediaBuyUoW

    with MediaBuyUoW(tenant_id) as uow:
        assert uow.media_buys is not None
        existing = uow.media_buys.find_by_idempotency_key(idempotency_key, principal_id)
        if existing is None:
            raise AdCPValidationError(
                f"Idempotency key {idempotency_key} not found after race resolution",
                recovery="terminal",
            )

        try:
            from src.core.database.repositories.workflow import WorkflowRepository

            workflow_step = None
            if uow.session is not None:
                workflow_step = WorkflowRepository(uow.session, tenant_id).find_by_idempotency_key(
                    idempotency_key,
                    principal_id,
                    tool_name="create_media_buy",
                )
        except Exception:
            workflow_step = None
        cached_response = getattr(workflow_step, "response_data", None)
        if isinstance(cached_response, dict) and cached_response and not cached_response.get("errors"):
            cached = {k: v for k, v in cached_response.items() if k != "request_data"}
            cached["replayed"] = True
            return CreateMediaBuyResult(
                response=CreateMediaBuySuccess.model_validate(cached),
                status=AdcpTaskStatus.completed.value,
            )

        db_packages = uow.media_buys.get_packages(existing.media_buy_id)
        # Rebuild AdCP Package response from the persisted ``package_config``
        # so the replay payload is byte-stable with the original response
        # (AdCP L1/security idempotency rule: only envelope fields may differ
        # between original and replay). A bare ``Package(package_id=...)``
        # drops ``product_id``, ``pricing_option_id``, ``paused``, ``canceled``,
        # ``budget``, etc. and fails the conformance suite.
        response_packages = [_package_from_config(pkg) for pkg in db_packages]

        return CreateMediaBuyResult(
            response=CreateMediaBuySuccess(
                media_buy_id=existing.media_buy_id,
                packages=response_packages,
                status="completed",
                media_buy_status=_media_buy_status_for_create_replay(existing),
                revision=1,
                confirmed_at=getattr(existing, "confirmed_at", None)
                or getattr(existing, "created_at", None)
                or datetime.now(UTC),
                replayed=True,
            ),
            status=AdcpTaskStatus.completed.value,
        )


def _package_from_config(db_package: Any) -> Package:
    """Rebuild a wire-shaped :class:`Package` from a persisted ``MediaPackage``.

    Reads buyer-supplied fields from the JSON ``package_config`` blob (where
    create_media_buy persists ``product_id``, ``pricing_option_id``, ``paused``,
    ``canceled``, ``budget``, etc.) and merges with the dedicated columns. Used
    on the idempotency-replay path so cached replays return the same package
    shape the buyer saw on the original commit.
    """
    config = db_package.package_config or {}
    kwargs: dict[str, Any] = {"package_id": db_package.package_id}
    # Echo buyer-visible AdCP package fields from the persisted config.
    # We use ``in`` rather than ``.get(...) is not None`` so explicit ``null``
    # values round-trip on replay — matches the original wire payload.
    for field in (
        "product_id",
        "pricing_option_id",
        "name",
        "paused",
        "canceled",
        "budget",
        "creative_assignments",
        "format_ids_to_provide",
        "impressions",
    ):
        if field in config:
            kwargs[field] = config[field]
    return Package(**kwargs)


def _buyer_safe_account(tenant_id: str, account_id: str | None) -> Account | None:
    """Buyer-safe account projection for the create-media-buy success response.

    Constructed with ONLY non-sensitive identity fields (account_id, name, status) —
    seller financials (rate_card, credit_limit, payment_terms, billing, billing_proxy,
    operator, governance_agents, setup, …) are never set, so they cannot leak via the
    wire dump (redaction by construction, not by stripping). The status is projected
    through ``wire_status`` so internal lifecycle states surface identically to the
    get_accounts flow (e.g. ``pending_provision`` → ``pending_approval``, #332).

    Returns None and never raises if the account can't be resolved/projected —
    account context is response enrichment, never load-bearing for the buy.
    """
    if not account_id:
        return None
    try:
        from src.core.database.repositories import MediaBuyUoW
        from src.core.database.repositories.account import AccountRepository
        from src.core.tools.account_status import wire_status

        with MediaBuyUoW(tenant_id) as uow:
            assert uow.session is not None
            row = AccountRepository(uow.session, tenant_id).get_by_id(account_id)
            if row is None or not getattr(row, "name", None):
                return None
            # Construct inside the UoW: reading row.name/row.status after the
            # session closes would raise DetachedInstanceError.
            return Account(account_id=account_id, name=row.name, status=wire_status(row.status))
    except ValidationError:
        # Status (or other field) the AdCP Account model can't represent — skip
        # the enrichment quietly; it is never load-bearing for the buy.
        logger.debug("create_media_buy: account %r not buyer-projectable", account_id, exc_info=True)
        return None
    except Exception:
        # DB/repository failure — surface it (warning) so a systemic regression
        # is visible, but still never break the buy over response enrichment.
        logger.warning("create_media_buy: account projection failed for %r", account_id, exc_info=True)
        return None


def _success_response_extras(*, req: Any, sandbox_mode: Any, tenant_id: str, account_id: str | None) -> dict[str, Any]:
    """Fields echoed onto every CreateMediaBuySuccess in the create flow (adcp 6.3).

    - ``context``: request context echoed unchanged (BR-RULE-043-01, all response paths)
    - ``sandbox``: present only when the tenant is in sandbox mode (UC-002-UPG-09)
    - ``account``: buyer-safe {account_id, name, status} projection (UC-002-UPG-07)

    None values are dropped by ``exclude_none`` on serialization, so non-sandbox / no-
    account buys are unaffected. Splatted via ``**`` into each success construction.
    """
    return {
        "context": getattr(req, "context", None),
        "sandbox": True if getattr(sandbox_mode, "active", False) else None,
        "account": _buyer_safe_account(tenant_id, account_id),
    }


@traced
async def _create_media_buy_impl(
    req: CreateMediaBuyRequest,
    push_notification_config: dict[str, Any] | None = None,
    identity: ResolvedIdentity | None = None,
    context_id: str | None = None,
) -> CreateMediaBuyResult:
    """Create a media buy with the specified parameters.

    Args:
        req: Validated CreateMediaBuyRequest with all protocol fields
        push_notification_config: Push notification config dict (transport wrapper serializes models)
        identity: ResolvedIdentity with principal/tenant info (transport-agnostic)

    Returns:
        CreateMediaBuyResult wrapping response and status
    """
    request_start_time = time.time()

    # Warn if unsupported reporting_webhook frequency is requested
    if req.reporting_webhook:
        freq_attr = getattr(req.reporting_webhook, "reporting_frequency", None)
        # Enums (ReportingFrequency.daily) format as "ReportingFrequency.daily"
        # via str(), which is unhelpful in logs. Prefer .value for the wire form.
        if freq_attr is None:
            raw_freq = "daily"
        elif hasattr(freq_attr, "value"):
            raw_freq = str(freq_attr.value).lower()
        else:
            raw_freq = str(freq_attr).lower()
        if raw_freq != "daily":
            logger.warning(
                "CreateMediaBuy requested reporting webhook frequency '%s', "
                "but only 'daily' frequency is currently supported. "
                "Hourly and monthly reporting will be ignored until implemented.",
                raw_freq,
            )

    # Extract testing context first
    if identity is None:
        raise AdCPValidationError("Identity is required")

    testing_ctx = identity.testing_context if identity.testing_context else AdCPTestContext()

    # Authentication and tenant setup
    principal_id = identity.principal_id
    if principal_id is None:
        raise AdCPAuthenticationError("Principal ID not found in identity - authentication required")

    # Tenant is resolved at the transport boundary (resolve_identity_from_context)
    tenant = identity.tenant
    if not tenant:
        raise AdCPAuthenticationError("No tenant context available")

    # Reject buyer-proposed measurement_terms the seller cannot honor BEFORE any
    # workflow / DB side effects. Surfaces as TERMS_REJECTED (correctable) on the
    # wire so buyer agents relax terms and retry with a fresh idempotency_key.
    _validate_measurement_terms(req)

    # Validate setup completion (only in production, skip for testing/dev)
    _skip_setup_check = os.environ.get("ADCP_SKIP_SETUP_CHECK") == "true"
    if not testing_ctx.dry_run and not testing_ctx.test_session_id and not _skip_setup_check:
        try:
            validate_setup_complete(tenant["tenant_id"])
        except SetupIncompleteError as e:
            # Return helpful error with missing tasks
            task_list = "\n".join(f"  - {task['name']}: {task['description']}" for task in e.missing_tasks)
            error_msg = (
                f"Setup incomplete. Please complete the following required tasks:\n\n{task_list}\n\n"
                f"Visit the setup checklist at /tenant/{tenant['tenant_id']}/setup-checklist for details."
            )
            raise AdCPValidationError(error_msg, recovery="terminal") from e

    # Validate principal exists after setup checks.
    principal = get_principal_object(principal_id, tenant_id=identity.tenant_id)
    if not principal:
        error_msg = f"Principal {principal_id} not found"
        return CreateMediaBuyResult(
            response=CreateMediaBuyError(
                errors=[Error(code="authentication_error", message=error_msg, details=None)],
                context=req.context,
            ),
            status=AdcpTaskStatus.failed.value,
        )

    sandbox_mode = sandbox_mode_for_request(identity=identity, account_ref=account_ref_from_request(req))
    if sandbox_mode.active:
        logger.info("[SANDBOX] create_media_buy will traffic with zero economics: %s", sandbox_mode.diagnostic)

    # Fields echoed onto every CreateMediaBuySuccess in this flow — see
    # _success_response_extras (context echo / sandbox flag / buyer-safe account).
    _success_extras: dict[str, Any] = _success_response_extras(
        req=req,
        sandbox_mode=sandbox_mode,
        tenant_id=tenant["tenant_id"],
        account_id=identity.account_id if identity else None,
    )

    # Account-scoped buyers route through Account.platform_mappings (or the
    # sandbox advertiser cache) by stamping the resolved advertiser on the
    # in-memory Principal used for adapter construction.
    _account_advertiser_id = _apply_account_advertiser_mapping(identity, principal, dry_run=testing_ctx.dry_run)
    if _account_advertiser_id is not None:
        logger.info(
            f"[ACCOUNT_ADVERTISER] account_id={identity.account_id!r} resolved to "
            f"GAM advertiser {_account_advertiser_id!r}; overriding principal mapping"
        )

    # Idempotency check: if request carries an idempotency_key, look up an existing
    # media buy for the same (tenant, principal, key) triple.  Per adcp 3.12 spec,
    # retrying with the same key must return the original media_buy_id without
    # creating a duplicate ad-server booking.
    if req.idempotency_key:
        from src.core.database.repositories import MediaBuyUoW as _IdempotencyUoW

        with _IdempotencyUoW(tenant["tenant_id"]) as idem_uow:
            assert idem_uow.media_buys is not None
            existing = idem_uow.media_buys.find_by_idempotency_key(req.idempotency_key, principal_id)
            if existing is not None:
                logger.info(
                    "Idempotency hit: returning existing media buy %s for key %s",
                    existing.media_buy_id,
                    req.idempotency_key,
                )
                return _build_idempotency_hit_result(
                    tenant_id=tenant["tenant_id"],
                    idempotency_key=req.idempotency_key,
                    principal_id=principal_id,
                    context=req.context,
                )

    # Context management and workflow step creation - create workflow step FIRST
    # Skip for dry_run mode (no side effects, no database writes)
    ctx_manager = get_context_manager()
    ctx_id = context_id  # Extracted at transport boundary, passed in
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
        # Pass model directly — ContextManager serializes at the DB boundary
        workflow_metadata: dict[str, Any] = {"protocol": identity.protocol}
        if push_notification_config:
            workflow_metadata["push_notification_config"] = push_notification_config

        step = ctx_manager.create_workflow_step(
            context_id=persistent_ctx.context_id,
            step_type="media_buy_creation",
            owner="system",
            status="in_progress",
            tool_name="create_media_buy",
            request_data=req,
            request_metadata=workflow_metadata,
        )

    try:
        # Validate input parameters
        # 1. Budget validation
        total_budget = req.get_total_budget()
        if sandbox_mode.active:
            budget_invalid = total_budget < 0
            budget_requirement = "non-negative"
        else:
            budget_invalid = total_budget <= 0
            budget_requirement = "positive"
        if budget_invalid:
            error_msg = f"Invalid budget: {total_budget}. Budget must be {budget_requirement}."
            raise ValueError(error_msg)

        # 2. DateTime validation
        now = datetime.now(UTC)

        # Validate start_time
        if req.start_time is None:
            error_msg = "start_time is required"
            raise ValueError(error_msg)

        # Handle 'asap' start_time (AdCP v1.7.0)
        # start_time is StartTiming (RootModel[datetime | 'asap']); unwrap via .root
        raw_start_time = req.start_time.root
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
            f"DEBUG: Request packages: {[{'product_id': p.product_id, 'bid_price': p.bid_price, 'pricing_option_id': p.pricing_option_id} for p in (req.packages or [])]}"
        )
        if not product_ids:
            error_msg = "At least one product is required."
            raise ValueError(error_msg)

        if req.packages:
            for package in req.packages:
                # Check product_id field per AdCP spec
                if not package.product_id:
                    error_msg = "Package must specify product_id."
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

        from src.core.database.models import CurrencyLimit
        from src.core.database.models import Product as ProductModel
        from src.core.database.repositories import MediaBuyUoW
        from src.core.inventory_profile_projection import (
            is_materialized_wholesale_product,
            project_visible_inventory_profile_product,
        )

        # Get products first to determine currency from pricing options
        projected_bundle_resolved_map: dict[str, Any] = {}
        with MediaBuyUoW(tenant["tenant_id"]) as validation_uow:
            # FIXME(salesagent-9f2): raw session usages below should migrate to repository methods
            assert validation_uow.session is not None
            session = validation_uow.session
            # Get products from database
            from sqlalchemy.orm import selectinload

            products_stmt = (
                select(ProductModel)
                .where(ProductModel.tenant_id == tenant["tenant_id"], ProductModel.product_id.in_(product_ids))
                .options(selectinload(ProductModel.pricing_options), selectinload(ProductModel.inventory_profile))
            )
            products = [
                product
                for product in session.scalars(products_stmt).all()
                if not is_materialized_wholesale_product(product)
            ]

            # Build product lookup map
            product_map = {p.product_id: p for p in products}
            missing_product_ids = set(product_ids) - set(product_map.keys())
            if missing_product_ids:
                projected_bundle_products = [
                    product
                    for product_id in sorted(missing_product_ids)
                    if (
                        product := project_visible_inventory_profile_product(
                            session,
                            tenant["tenant_id"],
                            product_id,
                        )
                    )
                ]
                products.extend(projected_bundle_products)
                product_map.update({p.product_id: p for p in projected_bundle_products})
                if projected_bundle_products:
                    from src.core.product_conversion import convert_product_model_to_resolved

                    projected_bundle_resolved_map = {
                        product.product_id: convert_product_model_to_resolved(product)
                        for product in projected_bundle_products
                    }

            # Validate all requested product_ids exist. Raise the typed
            # AdCPProductNotFoundError so the boundary translator maps it to
            # spec-canonical PRODUCT_NOT_FOUND on the wire (#351); a bare
            # ValueError would otherwise be swallowed by the outer
            # ``except (ValueError, PermissionError)`` handler and surface as
            # generic VALIDATION_ERROR. ``details`` carries the offending IDs
            # so the buyer can drop them and retry with valid IDs.
            missing_product_ids = set(product_ids) - set(product_map.keys())
            if missing_product_ids:
                sorted_missing = sorted(missing_product_ids)
                raise AdCPProductNotFoundError(
                    f"Product(s) not found: {', '.join(sorted_missing)}",
                    details={"missing_product_ids": sorted_missing, "field": "packages[].product_id"},
                )

            # Resolve package pricing before currency-limit checks. Per AdCP,
            # numeric package budgets inherit currency from the selected
            # pricing_option_id, not from product ordering or a top-level field.
            package_pricing_info_by_index: dict[int, dict[str, Any]] = {}
            legacy_request_currency = _derive_legacy_request_currency(req)
            if req.packages:
                try:
                    package_pricing_info_by_index = _collect_package_pricing_info_by_index(
                        packages=req.packages,
                        product_map=product_map,
                        campaign_currency=legacy_request_currency,
                        sandbox_trafficking=sandbox_mode.active,
                    )
                    request_currency = _derive_single_request_currency(package_pricing_info_by_index)
                except AdCPError as e:
                    # Re-raise pricing validation errors through the existing
                    # buyer-fixable INVALID_REQUEST conversion below.
                    raise ValueError(str(e)) from e
            else:
                request_currency = None

            # Fallback to deprecated/legacy sources only when package pricing
            # could not establish a currency.
            request_currency = request_currency or legacy_request_currency
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

            # Validate minimum product spend (legacy + new pricing_options)
            if not sandbox_mode.active and currency_limit.min_package_budget:
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
                                min_budget_error: str | None = (
                                    validate_min_package_budget(
                                        package_budget=package_budget,
                                        min_package_budget=package_min_spend,
                                        currency=package_currency,
                                        context="for products in this package",
                                    )
                                    if package_min_spend
                                    else None
                                )
                                if min_budget_error:
                                    raise ValueError(min_budget_error)
                    else:
                        # Legacy mode: single total_budget for all products
                        applicable_min_spends = list(product_min_spends.values())
                        if applicable_min_spends:
                            required_min_spend = max(applicable_min_spends)
                            budget_decimal = Decimal(str(total_budget))

                            legacy_min_budget_error: str | None = validate_min_package_budget(
                                package_budget=budget_decimal,
                                min_package_budget=required_min_spend,
                                currency=request_currency,
                                subject="Total",
                                context="for the selected products",
                            )
                            if legacy_min_budget_error:
                                raise ValueError(legacy_min_budget_error)

            # Validate maximum daily spend per package (if set)
            # This is per-package to prevent buyers from splitting large budgets across many packages
            if not sandbox_mode.active and currency_limit.max_daily_package_spend:
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
                        daily_package_spend_error: str | None = validate_max_daily_package_spend(
                            package_budget=package_budget,
                            flight_days=flight_days,
                            max_daily_spend=Decimal(str(currency_limit.max_daily_package_spend)),
                            currency=request_currency,
                            limit_label="maximum daily spend per package",
                            context="This protects against accidental large budgets and prevents GAM line item proliferation.",
                        )
                        if daily_package_spend_error:
                            raise ValueError(daily_package_spend_error)
                else:
                    # Legacy mode: validate total budget
                    legacy_daily_spend_error: str | None = validate_max_daily_package_spend(
                        package_budget=Decimal(str(total_budget)),
                        flight_days=flight_days,
                        max_daily_spend=Decimal(str(currency_limit.max_daily_package_spend)),
                        currency=request_currency,
                        subject="Daily",
                        limit_label="maximum daily spend",
                        context="This protects against accidental large budgets.",
                    )

                    if legacy_daily_spend_error:
                        raise ValueError(legacy_daily_spend_error)

        # Validate targeting doesn't use managed-only dimensions (targeting_overlay is at package level per AdCP spec)
        if req.packages:
            for pkg in req.packages:
                if pkg.targeting_overlay is not None:
                    from src.services.targeting_capabilities import (
                        validate_geo_overlap,
                        validate_overlay_targeting,
                        validate_unknown_targeting_fields,
                    )

                    # Reject unknown targeting fields (typos, bogus names) via model_extra
                    unknown_violations = validate_unknown_targeting_fields(pkg.targeting_overlay)

                    # Validate access control (managed-only, removed dimensions)
                    access_violations = validate_overlay_targeting(pkg.targeting_overlay)

                    # Reject same-value geo inclusion/exclusion overlap (AdCP SHOULD requirement)
                    geo_overlap_violations = validate_geo_overlap(pkg.targeting_overlay)

                    violations = unknown_violations + access_violations + geo_overlap_violations
                    if violations:
                        error_msg = f"Targeting validation failed: {'; '.join(violations)}"
                        raise ValueError(error_msg)

    except (ValueError, PermissionError) as e:
        # Update workflow step as failed (only if step exists - not created in dry_run mode)
        if step:
            ctx_manager.update_workflow_step(step.step_id, status="failed", error_message=str(e))

        # ``INVALID_REQUEST`` per AdCP 3.0 standard error-code enum (core/error.json):
        # the canonical code for buyer-fixable shape / business-rule violations
        # surfaced by the pre-dispatch validation pass above (past start_time,
        # reversed dates, empty product_ids, duplicate product_ids, targeting
        # validation failure, currency/budget validation failure). Storyboard
        # ``error_compliance/nonexistent_product`` requires one of
        # {PRODUCT_NOT_FOUND, PRODUCT_UNAVAILABLE, INVALID_REQUEST};
        # ``error_compliance/reversed_dates_error`` requires {VALIDATION_ERROR,
        # INVALID_REQUEST}. ``INVALID_REQUEST`` is the only value in the
        # intersection and matches the spec preference for buyer-fixable
        # shape issues. Note: ``PermissionError`` is caught here too but
        # currently no path inside the try raises it; if a future
        # principal-ownership check moves into this try, split the except
        # so PermissionError maps to PERMISSION_DENIED (the semantically
        # correct code) rather than riding the INVALID_REQUEST jacket.
        return CreateMediaBuyResult(
            response=CreateMediaBuyError(
                errors=[Error(code="INVALID_REQUEST", message=str(e), details=None)],
                context=req.context,
            ),
            status=AdcpTaskStatus.failed.value,
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
                    f"[INLINE_CREATIVE_DEBUG] Package {idx}: product_id={pkg.product_id}, creatives_count={len(pkg.creatives) if pkg.creatives else 0}"
                )
            try:
                logger.info("[INLINE_CREATIVE_DEBUG] Calling process_and_upload_package_creatives")
                # Cast packages to local PackageRequest type (runtime compatible, mypy list invariance)
                updated_packages, uploaded_ids = process_and_upload_package_creatives(
                    packages=cast(list[PackageRequest], req.packages),
                    context=identity,
                    testing_ctx=testing_ctx,
                )
                # Replace packages with updated versions (functional approach).
                req.packages = cast(list[AdcpPackageRequest], updated_packages)  # type: ignore[assignment]  # blocked by list invariance: local PackageRequest -> adcp PackageRequest
                logger.info("[INLINE_CREATIVE_DEBUG] Updated req.packages with creative_ids")
                if uploaded_ids:
                    logger.info(f"Successfully uploaded creatives for {len(uploaded_ids)} packages: {uploaded_ids}")
            except AdCPError as e:
                # Update workflow step on failure (only if step exists)
                if step:
                    ctx_manager.update_workflow_step(step.step_id, status="failed", error_message=str(e))
                raise

        # Get the appropriate adapter with testing context
        # Use dry_run from testing context (which comes from config or testing flags)
        adapter = get_adapter(principal, dry_run=testing_ctx.dry_run, testing_context=testing_ctx, tenant=tenant)

        # Check if manual approval is required
        # Use tenant.human_review_required as the authoritative source, with adapter setting as fallback
        tenant_approval_required = tenant.get("human_review_required", True)
        adapter_approval_required = adapter.manual_approval_required
        campaign_approval_owned = publisher_owns_campaign_approval()
        creative_approval_owned = publisher_owns_creative_approval()
        manual_approval_required = _effective_manual_approval_required(
            tenant_approval_required=tenant_approval_required,
            adapter_approval_required=adapter_approval_required,
            publisher_owns_campaign=campaign_approval_owned,
        )
        if not campaign_approval_owned:
            _suppress_adapter_manual_approval(adapter, "create_media_buy")
            setattr(req, "_already_approved", True)  # noqa: B010
        if sandbox_mode.active:
            manual_approval_required = False
            _suppress_adapter_manual_approval(adapter, "create_media_buy")
            setattr(req, "_already_approved", True)  # noqa: B010
        if not creative_approval_owned:
            _suppress_adapter_manual_approval(adapter, "add_creative_assets")
        manual_approval_operations = adapter.manual_approval_operations

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
            package_id_map: dict[int, str] = {}  # 0-based index → package_id

            # req.packages validated earlier in _create_media_buy_impl
            assert req.packages is not None, "packages required - validated earlier"
            for idx, pkg in enumerate(req.packages, 1):
                # Generate permanent package ID using product_id and index
                # Format: pkg_{product_id}_{timestamp_part}_{idx}
                import secrets

                package_id = f"pkg_{pkg.product_id}_{secrets.token_hex(4)}_{idx}"

                # Use product_id for package name since Package schema doesn't have 'name'
                pkg_name = f"Package {idx}"
                if pkg.product_id:
                    pkg_name = f"{pkg.product_id} - Package {idx}"

                # Build Package object with complete package data (matching auto-approval path)
                # NOTE: Package schema does NOT have a 'status' field - workflow state is tracked in WorkflowStep
                # (Package is imported at module level)

                # Create Package object from request package, adding generated fields
                # Maps PackageRequest fields to Package fields directly:
                # - format_ids (request) → format_ids_to_provide (response)
                # - creative_ids/creatives (request) → creative_assignments (response) [handled separately]
                pending_packages.append(
                    Package(
                        package_id=package_id,
                        paused=False,  # Initial state is not paused (AdCP 2.12.0)
                        product_id=pkg.product_id,
                        budget=pkg.budget,
                        bid_price=pkg.bid_price,
                        pricing_option_id=pkg.pricing_option_id,
                        targeting_overlay=pkg.targeting_overlay,
                        pacing=pkg.pacing,
                        impressions=getattr(pkg, "impressions", None),
                        ext=pkg.ext,
                        creative_assignments=pkg.creative_assignments,
                        format_ids_to_provide=pkg.format_ids,
                    )
                )

                # Track package_id for injection into serialized raw_request (0-based index)
                package_id_map[idx - 1] = package_id

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
            # Repository handles raw_request serialization + package_id injection at the DB boundary
            try:
                with MediaBuyUoW(tenant["tenant_id"]) as pending_uow:
                    assert pending_uow.media_buys is not None
                    pending_uow.media_buys.create_from_request(
                        media_buy_id=media_buy_id,
                        req=req,
                        principal_id=principal.principal_id,
                        advertiser_name=principal.name,
                        budget=total_budget,
                        currency=request_currency or "USD",
                        start_time=start_time,
                        end_time=end_time,
                        status="pending_approval",
                        order_name=f"{media_buy_id} - {start_time.strftime('%Y-%m-%d')}",
                        package_id_map=package_id_map,
                        by_alias=True,
                        account_id=identity.account_id if identity else None,
                        created_at=datetime.now(UTC),
                    )
                    logger.info(f"✅ Created media buy {media_buy_id} with status=pending_approval")
            except IntegrityError as exc:
                if "idempotency_key" not in str(exc.orig):
                    raise
                logger.warning(
                    "Idempotency race (pending_approval): another request won the commit for key %s. "
                    "Returning the winner. An orphan adapter-side order may exist.",
                    req.idempotency_key,
                )
                return _build_idempotency_hit_result(
                    tenant_id=tenant["tenant_id"],
                    idempotency_key=req.idempotency_key,
                    principal_id=principal.principal_id,
                    context=req.context,
                )

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
            with MediaBuyUoW(tenant["tenant_id"]) as pkg_uow:
                # FIXME(salesagent-9f2): package creation should use repository methods
                assert pkg_uow.session is not None
                session = pkg_uow.session
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
                                else:
                                    # ADCP 2.3 object format or other: _pydantic_json_serializer handles it
                                    budget_value = req_pkg.budget

                            # _pydantic_json_serializer on the engine handles Pydantic models,
                            # AnyUrl, enums, and datetimes in JSONType columns automatically
                            package_config.update(
                                {
                                    "product_id": req_pkg.product_id,
                                    "pricing_option_id": getattr(req_pkg, "pricing_option_id", None),
                                    "budget": budget_value,
                                    "targeting_overlay": req_pkg.targeting_overlay,
                                    "creative_ids": _get_requested_creative_ids(req_pkg),
                                    "format_ids": req_pkg.format_ids,
                                    "canceled": getattr(pkg_obj, "canceled", False),
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
                        package_id=pkg_obj.package_id,
                        package_config=package_config,
                        # Dual-write: populate dedicated columns
                        budget=Decimal(str(budget_total)) if budget_total is not None else None,
                        bid_price=Decimal(str(bid_price_value)) if bid_price_value is not None else None,
                        pacing=pacing_value,
                    )
                    session.add(db_package)

                # UoW auto-commits on clean exit
                logger.info(f"✅ Created {len(pending_packages)} MediaPackage records")

            # Link the workflow step to the media buy so the approval button shows in UI
            _link_step_to_media_buy(
                tenant_id=tenant["tenant_id"],
                step_id=step.step_id,
                media_buy_id=media_buy_id,
                branch="manual-approval",
            )

            # Create creative assignments for manual approval flow
            # This must happen AFTER media packages are created so we have package_ids
            if req.packages:
                with MediaBuyUoW(tenant["tenant_id"]) as assign_uow:
                    # FIXME(salesagent-9f2): assignment creation should use repository methods
                    assert assign_uow.session is not None
                    session = assign_uow.session
                    from src.core.database.models import Creative as DBCreative
                    from src.core.database.models import CreativeAssignment as DBAssignment

                    # Batch load all creatives upfront
                    all_creative_ids = []
                    for package in req.packages:
                        pkg_cids = _get_requested_creative_ids(package)
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

                        missing_ids = set(all_creative_ids) - set(creatives_map.keys())
                        if missing_ids:
                            error_msg, error = _build_creatives_not_found_error(missing_ids)
                            logger.error(error_msg)
                            ctx_manager.update_workflow_step(step.step_id, status="failed", error_message=error_msg)
                            raise error

                        # Validate creative formats against product formats BEFORE creating assignments
                        # This ensures creatives match the product's supported formats
                        # Validation happens at assignment time (not sync time) because:
                        # - Creatives may be synced before being assigned to products
                        # - A creative may be valid for product A but not product B
                        # - Same creative can be reused across packages if formats align
                        from src.core.helpers import validate_creative_format_against_product

                        for package in req.packages:
                            pkg_cids = _get_requested_creative_ids(package)
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
                                            creative_format_id = _stored_creative_format_id(creative)
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
                                                raise AdCPValidationError(format_error or "Format validation failed")

                                            logger.info(
                                                f"[CREATIVE_ASSIGN_DEBUG] Creative {creative_id} format "
                                                f"validated against product {package.product_id}"
                                            )

                    # Create assignments for each package
                    for i, package in enumerate(req.packages):
                        requested_assignments = _get_requested_creative_assignments(package)
                        if requested_assignments:
                            # Get package_id from pending_packages (already generated)
                            pkg_id: str | None = pending_packages[i].package_id if i < len(pending_packages) else None
                            if not pkg_id:
                                logger.error(f"Cannot assign creatives: No package_id for package {i}")
                                continue

                            logger.info(
                                f"[CREATIVE_ASSIGN_DEBUG] Creating assignments for package {pkg_id}, creative_ids: "
                                f"{[a['creative_id'] for a in requested_assignments]}"
                            )

                            for requested_assignment in requested_assignments:
                                creative_id = requested_assignment["creative_id"]
                                creative = creatives_map.get(creative_id)
                                if not creative:
                                    logger.warning(f"Creative {creative_id} not found in database, skipping assignment")
                                    continue

                                # Create database assignment
                                assignment_id = f"assign_{uuid.uuid4().hex[:12]}"
                                assignment = DBAssignment(
                                    assignment_id=assignment_id,
                                    tenant_id=tenant["tenant_id"],
                                    principal_id=principal_id,
                                    media_buy_id=media_buy_id,
                                    package_id=pkg_id,
                                    creative_id=creative_id,
                                    weight=requested_assignment["weight"],
                                    placement_ids=requested_assignment["placement_ids"],
                                )
                                session.add(assignment)
                                logger.info(
                                    f"[CREATIVE_ASSIGN_DEBUG] Created assignment {assignment_id} for creative {creative_id}"
                                )

                            # UoW auto-commits on clean exit
                            logger.info(f"✅ Created creative assignments for package {pkg_id}")

            # Manual-approval workflow with a synchronously-minted buy — emit
            # variant-1 of ``create_media_buy_response`` (the sync-success
            # shape) carrying ``media_buy_id``, ``packages``, and the spec
            # ``MediaBuyStatus`` that reflects what's blocking activation:
            #   - no creatives on the request → ``pending_creatives`` (buyer's
            #     next call is ``sync_creatives``);
            #   - creatives present → ``pending_start`` (awaiting human
            #     governance and/or start time).
            # Variant-3 (``status='submitted'`` + ``task_id``, no
            # ``media_buy_id``) is reserved for genuinely async cases where
            # the seller hasn't minted a buy yet. Here the buy is already in
            # the DB with a permanent id, so withholding it from the buyer
            # forces them through a polling round-trip and breaks downstream
            # tools that key off ``media_buy_id``.
            buy_status = (
                MediaBuyStatus.pending_start if _request_has_creatives(req) else MediaBuyStatus.pending_creatives
            )
            approval_response = CreateMediaBuySuccess(
                media_buy_id=media_buy_id,
                packages=pending_packages,
                status="completed",
                media_buy_status=buy_status,
                revision=1,
                confirmed_at=datetime.now(UTC),
                workflow_step_id=step.step_id,
                **_success_extras,
            )
            ctx_manager.update_workflow_step(
                step.step_id,
                status="requires_approval",
                response_data=serialize_for_workflow_step(approval_response),
            )
            return CreateMediaBuyResult(response=approval_response, status=AdcpTaskStatus.completed.value)

        # Get products for the media buy to check product-level auto-creation settings
        # Lazy import to avoid circular dependency with main.py
        from src.core.tools.products import get_product_catalog

        catalog = get_product_catalog(tenant_id=identity.tenant_id)
        product_ids = req.get_product_ids()
        products_in_buy = [p for p in catalog if p.product_id in product_ids]
        catalog_product_ids = {p.product_id for p in products_in_buy}
        missing_catalog_product_ids = set(product_ids) - catalog_product_ids
        if missing_catalog_product_ids:
            products_in_buy.extend(
                projected_bundle_resolved_map[product_id]
                for product_id in sorted(missing_catalog_product_ids)
                if product_id in projected_bundle_resolved_map
            )

        # Validate and auto-generate GAM implementation_config for each product if needed.
        # ``effective_configs`` lets us thread the auto-generated value through the
        # rest of the function without mutating the wire-shape Product schema.
        effective_configs: dict[str, dict] = {
            p.product_id: _dict_implementation_config(getattr(p, "implementation_config", None))
            for p in products_in_buy
        }
        if adapter.__class__.__name__ == "GoogleAdManager":
            from src.services.gam_product_config_service import GAMProductConfigService

            gam_validator = GAMProductConfigService()
            config_errors = []

            for schema_product in products_in_buy:
                existing_effective_config = dict(effective_configs.get(schema_product.product_id) or {})
                needs_default_config = not existing_effective_config or any(
                    field not in existing_effective_config for field in ("priority", "creative_placeholders")
                )

                # Auto-generate default line-item config if missing or incomplete.
                # Profile-backed products may already have inventory targeting
                # from the profile but no product-level GAM line-item defaults.
                if needs_default_config:
                    logger.info(
                        f"Product '{schema_product.name}' ({schema_product.product_id}) is missing GAM configuration defaults. "
                        f"Auto-generating defaults based on product type."
                    )
                    # Generate defaults based on product delivery type and formats
                    delivery_type_str = (
                        str(schema_product.delivery_type) if schema_product.delivery_type else "non_guaranteed"
                    )
                    # Extract format IDs as strings for config generation
                    formats_list: list[str] | None = None
                    if schema_product.format_ids:
                        formats_list = [fmt.id for fmt in schema_product.format_ids]
                    auto_config = gam_validator.generate_default_config(
                        delivery_type=delivery_type_str, formats=formats_list
                    )
                    effective_configs[schema_product.product_id] = {**auto_config, **existing_effective_config}

                    # Persist the auto-generated config to database
                    with MediaBuyUoW(tenant["tenant_id"]) as gam_uow:
                        # FIXME(salesagent-9f2): product update should use ProductRepository
                        assert gam_uow.session is not None
                        product_stmt = select(ModelProduct).filter_by(product_id=schema_product.product_id)
                        db_product = gam_uow.session.scalars(product_stmt).first()
                        if db_product:
                            db_product.implementation_config = {
                                **auto_config,
                                **(db_product.implementation_config or {}),
                            }
                            # UoW auto-commits on clean exit
                            logger.info(f"Saved auto-generated GAM config for product {schema_product.product_id}")

                # Validate the config (whether existing or auto-generated)
                impl_config = effective_configs[schema_product.product_id]
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
                return CreateMediaBuyResult(
                    response=CreateMediaBuyError(
                        errors=[
                            Error(code="invalid_configuration", message=err, details=None) for err in config_errors
                        ],
                        context=req.context,
                    ),
                    status=AdcpTaskStatus.failed.value,
                )

        product_auto_create = all(
            effective_configs[p.product_id].get("auto_create_enabled", True) for p in products_in_buy
        )

        # Pre-flight overbook detection (#152). Single-product GAM CPM
        # buys only on this iteration; fail-open everywhere. Warnings
        # surface on the success response via response.ext.warnings
        # pending https://github.com/adcontextprotocol/adcp/issues/4248.
        overbook_warnings: list[dict[str, Any]] = []
        try:
            overbook_warnings = _detect_overbook_warnings(
                adapter=adapter,
                products_in_buy=products_in_buy,
                effective_configs=effective_configs,
                req=req,
                total_budget=total_budget,
            )
            if overbook_warnings:
                logger.warning(
                    f"[OVERBOOK] {len(overbook_warnings)} warning(s) attached to response: "
                    f"{[w['details'] for w in overbook_warnings]}"
                )
        except Exception as e:
            # Defence-in-depth — the detector is already fail-open, but
            # any unexpected raise here must NOT block the buy.
            logger.warning(f"[OVERBOOK] detector raised, fail-open: {e}", exc_info=True)
            overbook_warnings = []

        # Check if either tenant or product disables auto-creation
        # Skip in dry_run mode - we're only validating, not creating workflow
        if not testing_ctx.dry_run and campaign_approval_owned and (not auto_create_enabled or not product_auto_create):
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
                # - package_id (required): Publisher's unique identifier
                response_packages.append(
                    Package(
                        package_id=package_id,
                    )
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

            # Tenant- or product-config-driven approval requirement with a
            # synchronously-minted buy — emit variant-1 (sync-success) carrying
            # ``media_buy_id``, ``packages``, and the spec ``MediaBuyStatus``
            # that reflects what's blocking activation. See the manual-approval
            # branch above for the rationale on why this is variant-1, not
            # variant-3.
            buy_status = (
                MediaBuyStatus.pending_start if _request_has_creatives(req) else MediaBuyStatus.pending_creatives
            )
            approval_response = CreateMediaBuySuccess(
                media_buy_id=media_buy_id,
                packages=response_packages,
                status="completed",
                media_buy_status=buy_status,
                revision=1,
                confirmed_at=datetime.now(UTC),
                workflow_step_id=step.step_id,
                **_success_extras,
            )
            ctx_manager.update_workflow_step(
                step.step_id,
                status="requires_approval",
                response_data=serialize_for_workflow_step(approval_response),
            )
            return CreateMediaBuyResult(response=approval_response, status=AdcpTaskStatus.completed.value)

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
        packages: list[MediaPackage] = []
        assert req.packages is not None, "packages required - validated earlier"
        for idx, pkg in enumerate(req.packages, 1):  # Iterate over request packages
            # Find the product for this package (from schema catalog, not database model)
            # Package has product_id field per AdCP spec
            pkg_product_id = pkg.product_id

            if not pkg_product_id:
                error_msg = f"Package {idx} has no product_id field set"
                raise ValueError(error_msg)

            pkg_product: Any | None = None
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
            if matching_package and matching_package.format_ids:
                product_formats = list(pkg_product.format_ids or [])
                unsupported_formats = [
                    _format_ref_display(fmt)
                    for fmt in matching_package.format_ids
                    if _matching_supported_format(fmt, product_formats) is None
                ]

                if unsupported_formats:
                    if not product_formats:
                        # Product has no format_ids configured - this is a configuration error
                        error_msg = (
                            f"Product '{pkg_product.name}' ({pkg_product.product_id}) has no format_ids configured. "
                            f"This product is not properly set up for media buys. "
                            f"Please configure format_ids on the product or contact the publisher."
                        )
                    else:
                        supported_formats_str = ", ".join(_format_ref_display(fmt) for fmt in product_formats)
                        error_msg = (
                            f"Product '{pkg_product.name}' ({pkg_product.product_id}) does not support requested format(s): "
                            f"{', '.join(unsupported_formats)}. Supported formats: {supported_formats_str}"
                        )
                    raise ValueError(error_msg)

                # Merge dimensions from product's format_ids if request format_ids don't have them
                # This handles the case where buyer specifies format_id but not dimensions
                # Process request format_ids, merging dimensions from product if missing
                for req_fmt in matching_package.format_ids:
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
                        matching_product_format = _matching_supported_format(req_fmt, product_formats)
                        product_dims = _format_params(matching_product_format) if matching_product_format else None
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

            # Get budget from matching request package if available
            package_budget_value: float | None = None
            if matching_package:
                if matching_package.budget is not None:
                    raw_budget = matching_package.budget
                    # Normalize budget: MediaPackage expects float | None (ADCP 2.5.0)
                    if raw_budget is not None:
                        if isinstance(raw_budget, (int, float)):
                            # ADCP 2.5.0 flat format: use as-is (float)
                            package_budget_value = float(raw_budget)
                        elif isinstance(raw_budget, dict):
                            # Legacy dict format: extract total
                            package_budget_value = float(raw_budget.get("total", 0.0))
                        elif isinstance(raw_budget, BaseModel):
                            # Budget object: extract total
                            package_budget_value = float(raw_budget.total)
                        else:
                            package_budget_value = None

            delivery_type_str = str(pkg_product.delivery_type.value)

            delivery_type_value: Literal["guaranteed", "non_guaranteed"] = cast(
                Literal["guaranteed", "non_guaranteed"], delivery_type_str
            )

            packages.append(
                MediaPackage(
                    package_id=package_id,
                    name=pkg_product.name,
                    delivery_type=delivery_type_value,
                    impressions=int(total_budget / cpm * 1000),
                    format_ids=cast(list[Any], format_ids_to_use),
                    targeting_overlay=cast(
                        "Targeting | None",
                        matching_package.targeting_overlay if matching_package else None,
                    ),
                    product_id=pkg_product.product_id,  # Include product_id
                    budget=package_budget_value,  # Include budget from request (now normalized)
                    creative_ids=(
                        _get_requested_creative_ids(matching_package) if matching_package else None
                    ),  # Include creative_ids from uploaded creatives
                    implementation_config=effective_configs.get(pkg_product.product_id),
                )
            )

        # Remap package_pricing_info from index-based keys to actual package IDs
        # Note: packages loop used enumerate(products_in_buy, 1) but pricing used enumerate(req.packages) starting at 0
        remapped_package_pricing_info: dict[str, dict[str, Any]] = {}
        # Map pricing info from index to package_id
        for pkg_idx, media_pkg in enumerate(packages):
            if pkg_idx in package_pricing_info_by_index:
                # Only add to dict if package_id is not None
                if media_pkg.package_id is not None:
                    remapped_package_pricing_info[media_pkg.package_id] = package_pricing_info_by_index[pkg_idx]
            else:
                logger.warning(f"No pricing info found for package index {pkg_idx}")
        logger.debug(f"[PRICING] Mapped {len(remapped_package_pricing_info)} package pricing info")
        # Reassign to package_pricing_info for use later
        package_pricing_info = remapped_package_pricing_info
        if sandbox_mode.active:
            mark_sandbox_trafficking_request(req)
            packages = sandbox_trafficking_packages(packages)
            package_pricing_info = sandbox_trafficking_pricing_info(
                package_pricing_info,
                default_currency=request_currency or "USD",
            )

        # Create the media buy using the adapter (SYNCHRONOUS operation)
        # Defensive null check: ensure start_time and end_time are set
        if not req.start_time or not req.end_time:
            error_msg = "start_time and end_time are required but were not properly set"
            if step:
                ctx_manager.update_workflow_step(step.step_id, status="failed", error_message=error_msg)
            return CreateMediaBuyResult(
                response=CreateMediaBuyError(
                    errors=[Error(code="invalid_datetime", message=error_msg, details=None)],
                    context=req.context,
                ),
                status=AdcpTaskStatus.failed.value,
            )

        # PRE-VALIDATE: Check all creatives have required fields BEFORE calling adapter
        # This prevents GAM order creation when creatives are invalid (all-or-nothing approach)
        try:
            with MediaBuyUoW(tenant["tenant_id"]) as pre_validate_uow:
                # FIXME(salesagent-9f2): creative validation should use a repository
                assert pre_validate_uow.session is not None
                _validate_creatives_before_adapter_call(packages, tenant["tenant_id"], session=pre_validate_uow.session)
                packages = _prepare_springserve_tag_mode_packages(
                    adapter,
                    packages,
                    tenant["tenant_id"],
                    session=pre_validate_uow.session,
                )
        except AdCPError:
            # Validation failed - creative validation errors already logged
            # Update workflow step as failed and re-raise (only if step exists - not created in dry_run mode)
            if step:
                ctx_manager.update_workflow_step(
                    step.step_id, status="failed", error_message="Creative validation failed"
                )
            raise

        # Pre-validate adapter-specific constraints (pricing models, budget limits)
        # This runs regardless of dry_run so adapter restrictions are always enforced.
        pre_creation_errors: list[str] = adapter.validate_media_buy_request(
            req, packages, start_time, end_time, package_pricing_info
        )
        if isinstance(pre_creation_errors, list) and pre_creation_errors:
            logger.error(f"[PRE-VALIDATE] Adapter validation failed: {pre_creation_errors}")
            if step:
                ctx_manager.update_workflow_step(
                    step.step_id, status="failed", error_message="Adapter validation failed"
                )
            raise AdCPInvalidRequestError(
                "; ".join(pre_creation_errors),
                details={"error_code": "ADAPTER_VALIDATION_FAILED"},
            )

        # Dry-run mode: skip adapter call entirely, return simulated response
        # All validation (products, pricing, budgets, creatives) has passed above.
        if testing_ctx.dry_run:
            if sandbox_mode.active:
                logger.info("[SANDBOX] Validation passed, returning no-spend simulated response without adapter call")
            else:
                logger.info("[DRY_RUN] Validation passed, returning simulated response without adapter call")
            simulated_packages = [
                ResponsePackage(
                    package_id=pkg.package_id,
                    product_id=pkg.product_id,
                    budget=pkg.budget,
                )
                for pkg in packages
            ]
            simulated_response = CreateMediaBuySuccess(
                media_buy_id=f"{'sandbox' if sandbox_mode.active else 'dry_run'}_{uuid.uuid4().hex[:12]}",
                packages=simulated_packages,
                status="completed",
                media_buy_status=MediaBuyStatus.pending_start
                if _request_has_creatives(req)
                else MediaBuyStatus.pending_creatives,
                revision=1,
                confirmed_at=datetime.now(UTC),
                **_success_extras,
            )
            return CreateMediaBuyResult(response=simulated_response, status=AdcpTaskStatus.completed.value)

        # Call adapter using shared creation logic
        # Note: start_time variable already resolved from 'asap' to actual datetime if needed
        # This uses the same function as manual approval to ensure consistency across adapters
        try:
            response = _execute_adapter_media_buy_creation(
                req, packages, start_time, end_time, package_pricing_info, principal, testing_ctx, tenant=tenant
            )
        except Exception as adapter_error:
            raise

        # Check if adapter returned an error response FIRST (before accessing any fields)
        # With oneOf pattern, response can be CreateMediaBuySuccess or CreateMediaBuyError
        if isinstance(response, CreateMediaBuyError):
            error_msg = response.errors[0].message if response.errors else "Unknown error"
            error_code = response.errors[0].code if response.errors else "UNKNOWN"
            logger.error(f"[ADAPTER] Adapter returned error response: {error_code} - {error_msg}")
            return CreateMediaBuyResult(response=response, status=AdcpTaskStatus.failed.value)

        if isinstance(response, CreateMediaBuySubmitted):
            return CreateMediaBuyResult(response=response, status=AdcpTaskStatus.submitted.value)

        # At this point, response is CreateMediaBuySuccess - safe to access success-specific fields
        # Type narrowing: media_buy_id must be present in successful response
        assert response.media_buy_id is not None, "Adapter returned response without media_buy_id"

        # Log response packages for debugging
        if response.packages:
            for i, pkg_item in enumerate(response.packages):
                # pkg_item is dict[str, Any] here (response.packages), different scope from earlier Package usage
                logger.info(f"[DEBUG] create_media_buy: Response package {i} = {pkg_item}")

        # Type narrowing: after dry_run return, step and persistent_ctx are guaranteed to exist
        # This is needed for mypy to understand these won't be None in the code below
        assert step is not None, "step should be created when not in dry_run mode"
        assert persistent_ctx is not None, "persistent_ctx should be created when not in dry_run mode"

        # Determine initial status using centralized logic.
        has_creatives = _request_has_creatives(req)

        # Use centralized status determination
        now = datetime.now(UTC)
        media_buy_status = _determine_media_buy_status(
            manual_approval_required=False,  # This path only runs when approval NOT required
            has_creatives=has_creatives,
            start_time=start_time,
            end_time=end_time,
            now=now,
        )
        logger.info(
            f"[STATUS] Media buy {response.media_buy_id}: manual_approval=False, "
            f"has_creatives={has_creatives} → status={media_buy_status}"
        )
        confirmed_at = _confirmed_at_for_create_status(media_buy_status, now)

        # Store the media buy in database (context_id is NULL for synchronous operations)
        # Repository handles raw_request serialization at the DB boundary.
        # Build package_id_map from the adapter's response so the serialised
        # raw_request.packages entries carry the same package_id the
        # MediaPackage rows are stored under. Without this injection, the
        # delivery read path (media_buy_delivery.py:388) falls back to
        # ``f"pkg_{product_id}_{idx}"`` which doesn't match the DB row, and
        # the pricing_info lookup fails — yielding a delivery response with
        # ``pricing_model=None`` that the AdCP wire validator rejects on
        # the package-level ``ByPackageItem`` schema.
        #
        # Adapter contract: ``response.packages`` is positionally aligned
        # with ``req.packages``. We assert the lengths match to fail loud
        # if a future adapter ever reorders / drops / dedupes — silently
        # mapping by adapter-response index when the request has a
        # different shape would corrupt every downstream pricing lookup.
        auto_package_id_map: dict[int, str] = {}
        if response.packages:
            req_pkg_count = len(req.packages) if req.packages else 0
            assert len(response.packages) == req_pkg_count, (
                f"Adapter contract violation: response.packages has "
                f"{len(response.packages)} items, request had {req_pkg_count}. "
                "package_id_map injection assumes 1:1 positional alignment."
            )
            for idx, resp_pkg in enumerate(response.packages):
                pkg_id = getattr(resp_pkg, "package_id", None)
                if pkg_id:
                    auto_package_id_map[idx] = pkg_id

        try:
            with MediaBuyUoW(tenant["tenant_id"]) as create_uow:
                assert create_uow.media_buys is not None
                create_uow.media_buys.create_from_request(
                    media_buy_id=response.media_buy_id,
                    req=req,
                    principal_id=principal_id,
                    advertiser_name=principal.name,
                    budget=0.0 if sandbox_mode.active else total_budget,
                    currency=request_currency,
                    start_time=start_time,
                    end_time=end_time,
                    status=media_buy_status,
                    campaign_objective=getattr(req, "campaign_objective", "") or "",
                    kpi_goal=getattr(req, "kpi_goal", "") or "",
                    package_id_map=auto_package_id_map or None,
                    approved_at=confirmed_at,
                    confirmed_at=confirmed_at,
                    approved_by="system" if confirmed_at is not None else None,
                    account_id=identity.account_id if identity else None,
                )
                # UoW auto-commits on clean exit
        except IntegrityError as exc:
            if "idempotency_key" not in str(exc.orig):
                raise
            logger.warning(
                "Idempotency race (auto-approved): another request won the commit for key %s. "
                "Returning the winner. An orphan adapter-side order may exist for %s.",
                req.idempotency_key,
                response.media_buy_id,
            )
            return _build_idempotency_hit_result(
                tenant_id=tenant["tenant_id"],
                idempotency_key=req.idempotency_key,
                principal_id=principal_id,
                context=req.context,
            )

        # Populate media_packages table for structured querying
        # This enables creative_assignments to work properly
        if req.packages or (response.packages and len(response.packages) > 0):
            with MediaBuyUoW(tenant["tenant_id"]) as auto_pkg_uow:
                # FIXME(salesagent-9f2): package creation should use repository methods
                assert auto_pkg_uow.session is not None
                session = auto_pkg_uow.session
                from src.core.database.models import MediaPackage as DBMediaPackage

                # Use response packages if available (has package_ids), otherwise generate from request
                packages_to_save = response.packages if response.packages else []
                logger.info(f"[DEBUG] Saving {len(packages_to_save)} packages to media_packages table")

                for i, resp_package in enumerate(packages_to_save):
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

                    # targeting_overlay is buyer-supplied input — pull from the request, not the
                    # adapter response. Adapter responses are AdCP-spec ResponsePackage objects
                    # which intentionally do not echo targeting back. Persisting the request value
                    # is what lets get_media_buys later round-trip property_list / collection_list
                    # references per the AdCP spec for sellers claiming list-targeting specialisms.
                    request_targeting_overlay = request_pkg.targeting_overlay if request_pkg is not None else None

                    # Persist buyer-supplied pricing_option_id from the original
                    # request package so the idempotency-replay rebuild path
                    # (_build_idempotency_hit_result) can echo it on cached
                    # replays — AdCP L1/security rule: the replay payload MUST
                    # be byte-stable across replays, only envelope fields differ.
                    request_pricing_option_id = getattr(request_pkg, "pricing_option_id", None) if request_pkg else None
                    package_config = {
                        "package_id": resp_package_id,
                        "name": getattr(resp_package, "name", None),  # Include package name from adapter response
                        "product_id": getattr(resp_package, "product_id", None),
                        "pricing_option_id": getattr(resp_package, "pricing_option_id", None)
                        or request_pricing_option_id,
                        "budget": getattr(resp_package, "budget", None),
                        "targeting_overlay": request_targeting_overlay,
                        "creative_ids": getattr(resp_package, "creative_ids", None),
                        "creative_assignments": getattr(resp_package, "creative_assignments", None),
                        "format_ids_to_provide": getattr(resp_package, "format_ids_to_provide", None),
                        "paused": paused,  # Store paused state (adcp 2.12.0)
                        "canceled": getattr(resp_package, "canceled", False),
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

                session.flush()  # Flush so packages are visible for line_item_id queries below
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

                        # FIXME(salesagent-rva2): migrate to uow.media_buys.get_package()
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

                    # UoW auto-commits on clean exit
                    logger.info("✓ Saved platform_line_item_ids to database")
                else:
                    logger.info("[DEBUG] No platform_line_item_ids found on response object")

        # Handle creative_ids in packages if provided (immediate association)
        if req.packages:
            with MediaBuyUoW(tenant["tenant_id"]) as creative_uow:
                # FIXME(salesagent-9f2): creative assignment should use repository methods
                assert creative_uow.session is not None
                session = creative_uow.session
                from src.core.database.models import Creative as DBCreative
                from src.core.database.models import CreativeAssignment as DBAssignment

                # Batch load all creatives upfront to avoid N+1 queries
                all_creative_ids = []
                for package in req.packages:
                    pkg_cids = _get_requested_creative_ids(package)
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
                        error_msg, error = _build_creatives_not_found_error(missing_ids)
                        logger.error(error_msg)
                        ctx_manager.update_workflow_step(step.step_id, status="failed", error_message=error_msg)
                        raise error

                    # Validate creative formats against product formats BEFORE creating assignments
                    # This ensures creatives match the product's supported formats
                    # Validation happens at assignment time (not sync time) because:
                    # - Creatives may be synced before being assigned to products
                    # - A creative may be valid for product A but not product B
                    # - Same creative can be reused across packages if formats align
                    from src.core.helpers import validate_creative_format_against_product

                    for package in req.packages:
                        pkg_cids = _get_requested_creative_ids(package)
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
                                        creative_format_id = _stored_creative_format_id(creative)
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
                                            raise AdCPValidationError(
                                                format_error or "Creative format mismatch",
                                                details={"error_code": "CREATIVE_FORMAT_MISMATCH"},
                                            )

                                        logger.info(
                                            f"Creative {creative_id} format validated against product {package.product_id}"
                                        )

                for i, package in enumerate(req.packages):
                    requested_assignments = _get_requested_creative_assignments(package)
                    if requested_assignments:
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

                        for requested_assignment in requested_assignments:
                            creative_id = requested_assignment["creative_id"]
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
                                # Creative not uploaded to the adapter yet - upload it now.
                                logger.info(
                                    f"Creative {creative_id} has no platform_creative_id - uploading to adapter now"
                                )
                                try:
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

                                    asset = build_adapter_asset_from_stored_creative(
                                        creative,
                                        package_id=response_package_id,
                                        format_spec=format_spec,
                                    )

                                    # Validate required fields - FAIL FAST, do not skip
                                    validation_errors = []
                                    adapter_name = str(getattr(adapter, "adapter_name", "") or "").lower()
                                    if adapter_asset_requires_dimensions(adapter_name, asset) and (
                                        not asset["width"] or not asset["height"]
                                    ):
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
                                            + "\n\nDisplay creatives must have dimensions (width/height); all creatives need a content URL. "
                                            "Please ensure creatives are properly synced before creating media buys."
                                        )
                                        logger.error(f"[AUTO-APPROVAL] {error_msg}")
                                        # Raise exception for MCP - this will be caught and returned as error response
                                        raise AdCPValidationError(
                                            error_msg,
                                            details={
                                                "error_code": "INVALID_CREATIVES",
                                                "creative_errors": validation_errors,
                                            },
                                        )

                                    # Upload through the selected adapter. The helper above preserves
                                    # canonical format metadata so SpringServe audio and FreeWheel VAST
                                    # creatives can use their adapter-specific wire shapes.
                                    upload_result = adapter.add_creative_assets(
                                        response.media_buy_id if response.media_buy_id else "",
                                        [asset],
                                        datetime.now(UTC),
                                    )
                                    logger.info(
                                        f"Successfully uploaded creative {creative_id} to adapter: {upload_result}"
                                    )

                                    uploaded_platform_creative_id = _uploaded_platform_creative_id(
                                        upload_result, creative_id
                                    )
                                    if not creative.data.get("platform_creative_id"):
                                        creative.data["platform_creative_id"] = uploaded_platform_creative_id
                                        session.add(creative)
                                        platform_creative_ids.append(uploaded_platform_creative_id)
                                        logger.info(
                                            f"Updated creative {creative_id} with platform_creative_id={uploaded_platform_creative_id}"
                                        )
                                    else:
                                        logger.info(
                                            f"Preserving existing platform_creative_id={creative.data.get('platform_creative_id')} "
                                            f"for creative {creative_id}, not overwriting with upload result"
                                        )
                                        platform_creative_ids.append(creative.data["platform_creative_id"])
                                except AdCPError:
                                    # Re-raise AdCPError - validation failures should fail the entire operation
                                    raise
                                except Exception as upload_error:
                                    # Other exceptions (network errors, etc.) - log and fail
                                    logger.error(f"Failed to upload creative {creative_id} to adapter: {upload_error}")
                                    raise AdCPAdapterError(
                                        f"Failed to upload creative {creative_id} to adapter: {str(upload_error)}",
                                        details={"error_code": "CREATIVE_UPLOAD_FAILED"},
                                    ) from upload_error

                            # Create database assignment
                            assignment_id = f"assign_{uuid.uuid4().hex[:12]}"
                            assignment = DBAssignment(
                                assignment_id=assignment_id,
                                tenant_id=tenant["tenant_id"],
                                principal_id=principal_id,
                                media_buy_id=response.media_buy_id,
                                package_id=response_package_id,
                                creative_id=creative_id,
                                weight=requested_assignment["weight"],
                                placement_ids=requested_assignment["placement_ids"],
                            )
                            session.add(assignment)

                        session.flush()  # Flush assignments before adapter call

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
                    product_ids_list = req.get_product_ids()
                    asset = _convert_creative_to_adapter_asset(creative, product_ids_list)
                    assets.append(asset)
                except Exception as e:
                    logger.error(f"Error converting creative {creative.creative_id}: {e}")
                    # Add a failed status for this creative
                    creative_statuses[creative.creative_id] = CreativeApprovalStatus(
                        creative_id=creative.creative_id, status="rejected", detail=f"Conversion error: {str(e)}"
                    )
                    continue
            statuses = adapter.add_creative_assets(response.media_buy_id, assets, datetime.now(UTC))

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
        # - package_id (required): Publisher's unique identifier
        response_packages = []

        # Get adapter response packages (have package_ids)
        adapter_packages = response.packages if response.packages else []

        assert req.packages is not None, "packages required - validated earlier"
        for i, package in enumerate(req.packages):
            # Get package_id and paused from adapter response
            if i < len(adapter_packages):
                # adapter_packages may be Package Pydantic objects (adcp v1.2.1) or dicts
                response_package = adapter_packages[i]
                if isinstance(response_package, BaseModel):
                    adapter_package_id = response_package.package_id
                    adapter_paused = getattr(response_package, "paused", False)
                elif isinstance(response_package, dict):
                    adapter_package_id = response_package.get("package_id")
                    adapter_paused = response_package.get("paused", False)
                else:
                    adapter_package_id = None
                    adapter_paused = False
            else:
                # Fallback if adapter didn't return enough packages
                logger.warning(f"Adapter returned fewer packages than request. Using request package {i}")
                adapter_package_id = None
                adapter_paused = False

            # Validate that adapter returned package_id
            if not adapter_package_id:
                error_msg = f"Adapter did not return package_id for package {i}. Cannot build response."
                logger.error(error_msg)
                raise ValueError(error_msg)

            # Ensure paused is a boolean
            if not isinstance(adapter_paused, bool):
                if isinstance(adapter_paused, str):
                    adapter_paused = adapter_paused.lower() in ("true", "1", "yes", "active")
                else:
                    adapter_paused = bool(adapter_paused)

            # Build Package response directly from request fields + adapter fields
            response_packages.append(
                Package(
                    package_id=adapter_package_id,
                    paused=adapter_paused,
                    product_id=package.product_id,
                    budget=package.budget,
                    bid_price=package.bid_price,
                    pricing_option_id=package.pricing_option_id,
                    pacing=package.pacing,
                    targeting_overlay=package.targeting_overlay,
                    impressions=getattr(package, "impressions", None),
                    creative_assignments=package.creative_assignments,
                    format_ids_to_provide=getattr(package, "format_ids", None),
                )
            )

        # Create AdCP response with typed Package objects.
        # Surface the DB-derived MediaBuyStatus on the wire so buyers see the correct
        # blocker (pending_creatives vs pending_start) on the create_media_buy reply
        # itself, matching what /get_media_buy will report later.
        if overbook_warnings:
            logger.info("[OVERBOOK] Warnings detected but CreateMediaBuySuccess has no extension field in AdCP 5.7")
        adcp_response = CreateMediaBuySuccess(
            media_buy_id=response.media_buy_id,
            packages=response_packages,
            status="completed",
            media_buy_status=MediaBuyStatus(media_buy_status),
            creative_deadline=response.creative_deadline,
            revision=1,
            confirmed_at=confirmed_at,
            **_success_extras,
        )

        # Log activity
        # Activity logging imported at module level

        log_tool_activity(identity, "create_media_buy", request_start_time)

        # Also log specific media buy activity
        try:
            principal_name = "Unknown"
            with MediaBuyUoW(tenant["tenant_id"]) as log_uow:
                # FIXME(salesagent-9f2): principal lookup should use a repository method
                assert log_uow.session is not None
                principal_stmt = select(ModelPrincipal).filter_by(
                    principal_id=principal_id, tenant_id=tenant["tenant_id"]
                )
                principal_db = log_uow.session.scalars(principal_stmt).first()
                if principal_db:
                    principal_name = principal_db.name

            # Calculate duration using new datetime fields (resolved from 'asap' if needed)
            duration_days = (end_time_val - start_time_val).days + 1

            activity_feed.log_media_buy(
                tenant_id=tenant["tenant_id"],
                principal_name=principal_name,
                media_buy_id=response.media_buy_id,
                budget=0.0 if sandbox_mode.active else total_budget,
                duration_days=duration_days,
                action="created",
            )
        except Exception as e:
            # Activity feed logging is non-critical, but we should log the failure
            logger.warning(f"Failed to log media buy creation to activity feed: {e}")

        # Apply testing hooks to response with campaign information (resolved from 'asap' if needed)
        campaign_info = {
            "start_date": start_time,
            "end_date": end_time,
            "total_budget": 0.0 if sandbox_mode.active else total_budget,
        }

        hooks_result = apply_testing_hooks(
            testing_ctx,
            "create_media_buy",
            campaign_info,
            media_buy_id=adcp_response.media_buy_id,
            spend_amount=0.0 if sandbox_mode.active else total_budget,
        )

        # Only mutation that survives: test_ prefix on media_buy_id in dry-run mode
        modified_response = adcp_response
        if hooks_result.media_buy_id_override:
            modified_response = adcp_response.model_copy(update={"media_buy_id": hooks_result.media_buy_id_override})

        # Link the workflow step to the media buy so push-notification webhooks
        # fire on completion. ``_send_push_notifications`` reads
        # ``ObjectWorkflowMapping`` rows by step_id; without one, it returns
        # early with "No object mappings found" and the buyer's webhook never
        # fires. The manual-approval branch (~line 2362) already does this for
        # its own flow; the auto-approval success branch needs the same. See
        # issue #64.
        if step is not None and not testing_ctx.dry_run:
            _link_step_to_media_buy(
                tenant_id=tenant["tenant_id"],
                step_id=step.step_id,
                media_buy_id=modified_response.media_buy_id,
                branch="auto-approval",
            )

        # Mark workflow step as completed on success
        ctx_manager.update_workflow_step(
            step.step_id,
            status="completed",
            response_data=serialize_for_workflow_step(modified_response),
        )

        # Send Slack notification for successful media buy creation
        try:
            # Get principal name for notification (reuse from activity logging above)
            principal_name = "Unknown"
            with MediaBuyUoW(tenant["tenant_id"]) as slack_uow:
                # FIXME(salesagent-9f2): principal lookup should use a repository method
                assert slack_uow.session is not None
                principal_stmt2 = select(ModelPrincipal).filter_by(
                    principal_id=principal_id, tenant_id=tenant["tenant_id"]
                )
                principal_db = slack_uow.session.scalars(principal_stmt2).first()
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
                "total_budget": 0.0 if sandbox_mode.active else total_budget,
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
                "total_budget": 0.0 if sandbox_mode.active else total_budget,
                "po_number": req.po_number,
                "duration_days": (end_time_val - start_time_val).days + 1,  # Resolved from 'asap' if needed
                "product_count": len(req.get_product_ids()),
                "packages_count": len(response_packages) if response_packages else 0,
            },
        )

        return CreateMediaBuyResult(response=modified_response, status=AdcpTaskStatus.completed.value)

    except AdCPError as adcp_err:
        # Re-raise transport-agnostic errors (CREATIVE_UPLOAD_FAILED, etc.) without wrapping
        if step:
            ctx_manager.update_workflow_step(step.step_id, status="failed", error_message=str(adcp_err))
        raise

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

        raise AdCPAdapterError(
            f"Failed to create media buy: {str(e)}", details={"error_code": "MEDIA_BUY_CREATION_ERROR"}
        ) from e


# Unified update tools
