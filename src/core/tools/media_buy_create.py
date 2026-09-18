"""Create Media Buy tool implementation.

Handles media buy creation including:
- Product selection and validation
- Package configuration with pricing
- Creative assignment
- Ad server order provisioning
- Budget validation
"""

import logging
import secrets
import time
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal, NoReturn, TypedDict, cast

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from src.core.database.repositories.media_buy import MediaBuyRepository
    from src.core.database.repositories.uow import MediaBuyUoW as _MediaBuyUoWType

from adcp.server.helpers import valid_actions_for_status
from adcp.types import GeneratedTaskStatus as AdcpTaskStatus
from adcp.types import PackageRequest as AdcpPackageRequest
from pydantic import BaseModel, ValidationError
from rich.console import Console

from src.core.database.integrity import is_constraint_violation
from src.core.database.repositories.creative import CreativeRepository
from src.core.exceptions import (
    AdCPAdapterError,
    AdCPAuthorizationError,
    AdCPBudgetExceededError,
    AdCPBudgetTooLowError,
    AdCPCapabilityNotSupportedError,
    AdCPConfigurationError,
    AdCPCreativeNotFoundError,
    AdCPFormatNotFoundError,
    AdCPGoneError,
    AdCPIdempotencyExpiredError,
    AdCPInvalidRequestError,
    AdCPPersistedStateError,
    AdCPProductNotFoundError,
    AdCPSalesAgentError,
    AdCPServiceUnavailableError,
    AdCPValidationError,
)
from src.core.helpers import enum_value
from src.core.idempotency_canonical import canonical_request_hash
from src.core.idempotency_policy import DEFAULT_REPLAY_TTL
from src.core.idempotency_replay import raise_on_payload_conflict


class PackageAssignmentDict(TypedDict):
    """Internal dict format for passing package assignments to adapters."""

    package_id: str
    weight: int


logger = logging.getLogger(__name__)
console = Console()


# Tool-specific imports
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy.exc import SQLAlchemyError

from src.adapters.base import AdapterCreateRequest, AdapterCreateResult
from src.core.audit_logger import get_audit_logger
from src.core.context_manager import get_context_manager
from src.core.database.models import AdapterConfig, CurrencyLimit, MediaBuy, PersistedMediaBuyStatus, Tenant
from src.core.database.models import Creative as DBCreative
from src.core.database.models import MediaPackage as DBMediaPackage
from src.core.database.models import Product as ModelProduct
from src.core.database.models import Product as ProductModel
from src.core.helpers import log_tool_activity
from src.core.helpers.adapter_helpers import get_adapter
from src.core.helpers.creative_helpers import (
    extract_click_url,
    extract_impression_tracker_url,
    extract_media_url_and_dimensions,
    process_and_upload_package_creatives,
)
from src.core.helpers.pricing_helpers import pricing_info_for
from src.core.logging_config import log_safe
from src.core.resolved_identity import AccountIdentity, ResolvedIdentity, identity_of
from src.core.schemas import (
    AssetStatus,
    CreateMediaBuyRequest,
    CreateMediaBuyResult,
    CreateMediaBuySubmitted,
    CreateMediaBuySuccess,
    CreativeApprovalStatus,
    FormatId,
    FormatIdentity,
    MediaPackage,
    Package,
    PackageRequest,
    Product,
    Targeting,
    canonical_agent_url,
)
from src.core.schemas import (
    url as make_url,
)
from src.core.security.outbound_http import CounterpartyUrl, UrlProvenance
from src.core.tools._media_buy_transitions import resolve_flight_window_status
from src.core.tools.financial_validation import (
    raise_if_validation_failed,
    validate_budget_positive,
    validate_max_daily_package_spend,
    validate_min_package_budget,
)

# Import get_product_catalog from main (after refactor)
from src.core.validation_helpers import (
    PACKAGES_FIELD,
    format_validation_error,
    package_field_path,
)
from src.core.webhook_validator import (
    reject_unsafe_webhook_registration_url,
    webhook_url_for_log,
)
from src.core.webhooks.registration import accept_push_notification_config
from src.services.activity_feed import activity_feed
from src.services.gam_product_config_service import GAMProductConfigService
from src.services.targeting_capabilities import (
    collect_targeting_violations,
    property_list_unsupported_advisories,
    raise_if_property_targeting_violations,
    validate_property_targeting_allowed,
)

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


def _merge_creative_enrichment(existing_data: dict | None, status: AssetStatus) -> dict:
    """Merge an adapter's push result into a creative's ``data`` blob.

    Returns a new dict; does not mutate the input. Two enrichments are folded in,
    both fill-only-when-absent so an existing value is never overwritten:

    - ``platform_creative_id`` — the ad server's creative id, used to associate
      already-synced creatives and to skip re-pushing on re-approval.
    - ``concept_id`` / ``concept_name`` / ``concept_source`` — the seller-side
      concept enrichment (#1506). AdCP exposes concept_id/concept_name read-only on
      list_creatives but carries no concept on sync_creatives, so an adapter may
      derive a fallback (e.g. the GAM Order). ``concept_source`` records provenance;
      because we only fill when absent, a buyer-supplied concept (a future
      spec-defined field) is preserved and takes precedence over this fallback.
    """
    data = dict(existing_data or {})
    if status.creative_id and not data.get("platform_creative_id"):
        data["platform_creative_id"] = status.creative_id
    if status.concept_id and not data.get("concept_id"):
        data["concept_id"] = status.concept_id
        data["concept_name"] = status.concept_name
        # Every current producer sets concept_source (GAM → "gam_order"); the fallback
        # only fires for a future non-GAM adapter that derives a concept without tagging
        # provenance, so the marker is never silently empty.
        data["concept_source"] = status.concept_source or "adapter_enrichment"
    return data


def _apply_creative_enrichment(creative: DBCreative, status: AssetStatus) -> dict | None:
    """Merge an adapter push result into a creative's ``data`` blob, ready to persist.

    Wraps :func:`_merge_creative_enrichment` with the change detection + provenance log
    shared by all three GAM push paths (auto-approval create, manual-approval execution,
    and retroactive per-creative push — #1506). Returns the merged dict when the merge
    adds something the caller must persist, or ``None`` when it is a no-op (nothing to
    write). Callers persist the returned dict through ``CreativeRepository.update_data``
    so the enrichment has a single persistence path::

        merged = _apply_creative_enrichment(creative, status)
        if merged is not None:
            creatives_repo.update_data(creative, merged)
    """
    merged = _merge_creative_enrichment(creative.data, status)
    if merged == (creative.data or {}):
        return None
    logger.info(
        log_safe(
            f"Enriched creative {creative.creative_id} from adapter push "
            f"(platform_creative_id={merged.get('platform_creative_id')}, "
            f"concept_source={merged.get('concept_source')})"
        )
    )
    return merged


def _determine_media_buy_status(
    manual_approval_required: bool,
    has_creatives: bool,
    creatives_approved: bool,
    start_time: datetime,
    end_time: datetime,
    now: datetime | None = None,
) -> PersistedMediaBuyStatus:
    """Centralized media buy status determination logic.

    This ensures consistent status across all adapters (GAM, Mock, Kevel, etc.).

    Status Priority (highest to lowest) - ALL SPEC-COMPLIANT:
    1. completed: Past end date
    2. pending_creatives: Missing creatives or unapproved creatives
    3. pending_start: Manual approval required OR scheduled for future start
    4. active: Currently delivering (has creatives, approved, within flight dates)
    5. paused: (Reserved for future use - not currently returned)

    Internal states mapped to spec statuses:
    - "needs_creatives" → pending_creatives (missing or unapproved creatives)
    - "pending_approval" → pending_start (awaiting manual approval)
    - "ready" → pending_start (scheduled for future start)

    Args:
        manual_approval_required: Whether the media buy requires manual approval
        has_creatives: Whether creatives have been assigned
        creatives_approved: Whether all assigned creatives are approved
        start_time: Campaign start datetime
        end_time: Campaign end datetime
        now: Current time (defaults to datetime.now(UTC))

    Returns:
        The persisted-vocabulary member the flight window and creative state imply.
        Returning the member rather than its spelling is what lets the caller hand it
        straight to the repository: a string would have to be coerced back, one frame
        from the door that coerces it again.
    """
    if now is None:
        now = datetime.now(UTC)

    # Priority 1: Completed (past end date - check first to avoid false pending states)
    if now > end_time:
        return PersistedMediaBuyStatus.COMPLETED

    # Priority 2: Pending creatives (missing or unapproved creatives block delivery)
    # This is distinct from pending_start: the buy is otherwise ready, but creatives
    # need to be assigned/approved before it can go active.
    if not has_creatives or not creatives_approved:
        return PersistedMediaBuyStatus.PENDING_CREATIVES

    # Priority 3: Pending start (manual approval required or scheduled for future)
    if manual_approval_required or now < start_time:
        return PersistedMediaBuyStatus.PENDING_START

    # Priority 4: Active (currently delivering - all conditions met)
    return PersistedMediaBuyStatus.ACTIVE


def _get_format_spec_sync(agent_url: str, format_id: str, *, provenance: UrlProvenance | None = None) -> Any | None:
    """Get format specification synchronously from the async registry.

    Thin delegate kept for its patch surface (tests/harness envs patch this
    name). The behavior — typed AdCPSalesAgentError propagates (transient agent failures
    keep their recovery semantics, #1430: transient-error taxonomy fix), untyped errors log and
    become None (unknown-format) — lives in the SINGLE shared fetch path,
    format_resolver.fetch_format_spec (#1430 review).

    ``provenance`` carries BUYER provenance for a stored creative's ``agent_url``
    (it came out of the buyer's own prior sync_creatives call, not this
    deployment's configuration) — a ``CounterpartyUrl`` routes a refusal through
    the seam's counterparty-aware path (VALIDATION_ERROR/correctable) instead of
    the operator path (CONFIGURATION_ERROR/terminal), UNLESS the url happens to
    also be a real tenant-registered operator agent, which stays terminal
    regardless (salesagent-ypgd).
    """
    from src.core.format_resolver import fetch_format_spec

    return fetch_format_spec(agent_url, format_id, provenance=provenance)


def _validate_creatives_before_adapter_call(
    packages: "Sequence[MediaPackage] | Sequence[AdcpPackageRequest]",
    tenant_id: str,
    principal_id: str,
    session: "Session | None" = None,
) -> None:
    """Validate all creatives have required fields BEFORE calling adapter.

    This prevents GAM order creation when creatives are invalid, enabling
    true all-or-nothing behavior without rollback complexity.

    Fetches format specifications to determine correct asset structure per format.

    Args:
        packages: List of Package objects with creative_ids
        tenant_id: Tenant ID for database lookup
        principal_id: Owning principal — the creatives PK is composite
            (creative_id, tenant_id, principal_id), so the existence gate must
            match the full key: another principal's creative resolves to
            "not found" (uniform, no field leak) instead of passing the gate
            on their row and violating the composite FK on assignment insert.
        session: SQLAlchemy session (from UoW).

    Raises:
        AdCPCreativeRejectedError: If any creative is missing required fields (URL,
            dimensions), is in a terminal state, or has an incompatible format.
    """
    if session is None:
        raise ValueError("session is required for _validate_creatives_before_adapter_call")

    from src.core.format_resolver import is_dialled_agent_url

    # Collect all creative IDs from all packages
    all_creative_ids = set()
    for package in packages:
        pkg_creative_ids = _get_creative_ids(package)
        if pkg_creative_ids:
            all_creative_ids.update(pkg_creative_ids)

    if not all_creative_ids:
        # No creatives to validate
        return

    # Fetch all creatives in one principal-scoped query
    creatives_list = CreativeRepository(session, tenant_id).get_by_ids(list(all_creative_ids), principal_id)

    # Referenced creative_ids that don't exist are rejected up front so BOTH
    # approval paths fail identically before anything is persisted (the pending
    # path previously skipped missing ids and returned a pending success).
    found_creative_ids = {str(c.creative_id) for c in creatives_list}
    missing_ids = all_creative_ids - found_creative_ids
    if missing_ids:
        error_msg = f"Creative IDs not found: {', '.join(sorted(missing_ids))}"
        logger.error(log_safe(error_msg))
        # 3.1.1 enums/error-code.json: "Sellers MUST return this code uniformly for any
        # creative_id not owned by the calling account." The deferral that stood here
        # cited the BR-UC-003 ext-i cell as grading CREATIVE_REJECTED; that cell asks for
        # CREATIVE_NOT_FOUND, so the FIXME was resolved against a reading of the grader
        # that the grader did not support.
        raise AdCPCreativeNotFoundError(
            details=CreativeRefDetails(missing_creative_ids=sorted(missing_ids)),
            field=PACKAGES_FIELD,
        )

    # Validate each creative has required fields
    validation_errors = []
    # Terminal-state creatives are collected separately: the pinned enum codes
    # "operation is not permitted for the resource's current status" as INVALID_STATE,
    # a different condition from the field/format failures below, and update_media_buy
    # already splits them the same way (_validate_creatives_for_assignment).
    bad_state: list[Any] = []
    for creative in creatives_list:
        creative_data = creative.data or {}

        # BR-RULE-026: Reject creatives in terminal error states
        if hasattr(creative, "status") and creative.status in ("error", "rejected"):
            bad_state.append(creative)
            continue

        # Get format specification from creative agent (uses in-memory cache with 30min TTL).
        # Skip the fetch entirely for an adapter-provided pseudo-URL (e.g.
        # broadstreet://<tenant_id>): those formats are served by the adapter
        # in-process, so no dialled agent could ever resolve them, and the
        # unconditional fetch below would reject a format the seller itself
        # advertised (salesagent-ypgd). CounterpartyUrl marks BUYER provenance for
        # a genuinely-dialled url — see _get_format_spec_sync's docstring. No
        # field: create-media-buy-request.json defines no creative:{id} path, so
        # there is no canonical request-document locator to name (a fabricated
        # one is not honest — CounterpartyUrl(field=None) states that plainly).
        format_spec = None
        if creative.format and creative.agent_url and is_dialled_agent_url(creative.agent_url):
            format_spec = _get_format_spec_sync(
                creative.agent_url,
                str(creative.format),
                provenance=CounterpartyUrl(field=None),
            )

            # Fail validation if format spec not found (no skipping!) — only
            # meaningful once we know the agent SHOULD have it (a dialled url).
            if not format_spec:
                validation_errors.append(
                    f"Creative {creative.creative_id} has unknown format '{creative.format}' "
                    f"from agent {creative.agent_url}. Format must be registered with the creative agent."
                )
                continue

        # Skip validation for generative formats - they need conversion first
        # Generative formats have output_format_ids (they generate reference formats)
        if format_spec and format_spec.output_format_ids:
            logger.info(
                log_safe(
                    f"Skipping validation for generative creative {creative.creative_id} "
                    f"(format={creative.format}) - will be converted to reference format"
                )
            )
            continue

        # Only validate reference creatives (formats we can directly use)
        # Extract URL and dimensions using shared helper
        url, width, height = extract_media_url_and_dimensions(creative_data, format_spec)

        if not url:
            validation_errors.append(
                f"Reference creative {creative.creative_id} (format={creative.format}) "
                f"missing required URL field in assets"
            )
        if not width or not height:
            validation_errors.append(
                f"Reference creative {creative.creative_id} missing dimensions (width={width}, height={height})"
            )

    if bad_state:
        # The STATE per creative, not a joined sentence naming the ids: per-creative
        # outcomes are per-ENTITY problems. Same shape and same code as the
        # update_media_buy gate, so one condition reads identically on both tools.
        raise AdCPGoneError(
            details=InvalidStateDetails(
                problems=[
                    ErrorProblem(subject_type="creative", subject_id=c.creative_id, rejected_value=c.status)
                    for c in bad_state
                ]
            ),
            field=PACKAGES_FIELD,
        )

    # --- Format compatibility check: creative format vs product accepted formats ---
    # Build creative_id -> format mapping from fetched creatives
    creative_format_map: dict[str, str] = {}
    for creative in creatives_list:
        if creative.format:
            creative_format_map[creative.creative_id] = str(creative.format)

    # Collect all product_ids from packages that have creatives
    product_ids_needed: set[str] = set()
    for package in packages:
        pkg_creative_ids = _get_creative_ids(package)
        if pkg_creative_ids and package.product_id:
            product_ids_needed.add(package.product_id)

    if product_ids_needed:
        from src.core.database.repositories.product import ProductRepository

        products_list = ProductRepository(session, tenant_id).list_by_ids(list(product_ids_needed))

        # Build product_id -> set of accepted format id strings
        product_format_map: dict[str, set[str]] = {}
        for product in products_list:
            accepted_formats: set[str] = set()
            if product.format_ids:
                for fmt in product.format_ids:
                    fmt_id = fmt.get("id")
                    if fmt_id:
                        accepted_formats.add(str(fmt_id))
            product_format_map[product.product_id] = accepted_formats

        # Check each package's creatives against its product's accepted formats
        for package in packages:
            pkg_creative_ids = _get_creative_ids(package)
            if not pkg_creative_ids or not package.product_id:
                continue

            accepted = product_format_map.get(package.product_id)
            if accepted is None or not accepted:
                continue  # Product not found or has no formats — skip format check

            for cid in pkg_creative_ids:
                creative_fmt = creative_format_map.get(cid)
                if creative_fmt and creative_fmt not in accepted:
                    validation_errors.append(
                        f"Creative {cid} has format '{creative_fmt}' which is not accepted by "
                        f"product {package.product_id} (accepted formats: {sorted(accepted)})"
                    )

    if validation_errors:
        error_msg = (
            "Cannot create media buy with invalid creatives. "
            "The following creatives have validation errors:\n" + "\n".join(f"  • {err}" for err in validation_errors)
        )
        logger.error(f"[PRE-VALIDATION] {error_msg}")
        # A stored creative missing its required assets, or carrying a format the
        # product does not accept, "violates business rules beyond schema validation"
        # (3.1.1 enums/error-code.json) -- VALIDATION_ERROR. Not CREATIVE_REJECTED,
        # which that enum defines as a content-policy review failure and shapes as
        # {policy_id, policy_url, reasons}; no policy review runs on this path.
        raise AdCPValidationError(
            details=ValidationDetails(reasons=validation_errors),
            field=PACKAGES_FIELD,
        )


def _pre_validate_package_creatives(
    packages: "Sequence[MediaPackage] | Sequence[AdcpPackageRequest]",
    tenant_id: str,
    principal_id: str,
    ctx_manager: Any,
    step: Any,
) -> None:
    """Run pre-adapter creative validation inside its own UoW.

    Shared by the auto-approval and manual-approval create paths so the same
    buyer input is rejected identically (CREATIVE_REJECTED) regardless of the
    tenant's approval mode, BEFORE any media-buy state is persisted (POST-F1).
    Marks the workflow step failed on rejection. Duck-typed over packages:
    only ``_get_creative_ids(package)`` and ``package.product_id`` are used.
    """
    from src.core.database.repositories import MediaBuyUoW

    try:
        with MediaBuyUoW(tenant_id) as pre_validate_uow:
            # FIXME(#1119): creative validation should use a repository
            assert pre_validate_uow.session is not None
            _validate_creatives_before_adapter_call(packages, tenant_id, principal_id, session=pre_validate_uow.session)
    except AdCPSalesAgentError:
        # Validation failed - creative validation errors already logged
        # Update workflow step as failed and re-raise
        if step:
            ctx_manager.update_workflow_step(step.step_id, status="failed", error_message="Creative validation failed")
        raise


def _execute_adapter_media_buy_creation(
    request: AdapterCreateRequest,
    packages: list[MediaPackage],
    start_time: datetime,
    end_time: datetime,
    package_pricing_info: dict[str, dict[str, Any]],
    identity: ResolvedIdentity,
) -> AdapterCreateResult:
    """Execute adapter's create_media_buy call.

    This function is shared between auto-approval and manual approval flows
    to ensure consistent adapter behavior across all adapters (GAM, Mock, Kevel, etc.).

    Args:
        request: The buy to place, as the adapters read it. The buyer's DTO does not
            come through here: the approval flow replays a row, not a request, and
            asking it to rebuild a ``CreateMediaBuyRequest`` is what made it fabricate
            an idempotency key.
        packages: List of Package objects with product/creative configuration
        start_time: Resolved campaign start datetime
        end_time: Resolved campaign end datetime
        package_pricing_info: Pricing model info per package
        identity: The caller the adapter acts for (from a request or from stored ids)

    Returns:
        AdapterCreateResult from the adapter

    Raises:
        Exception: If adapter creation fails (with detailed logging)
    """
    principal = identity.principal
    adapter = get_adapter(identity)

    # Call adapter with detailed error logging
    try:
        response = adapter.create_media_buy(request, packages, start_time, end_time, package_pricing_info)
        logger.info(
            f"[ADAPTER] create_media_buy succeeded: {response.media_buy_id} with {len(response.packages)} packages"
        )
        for i, pkg in enumerate(response.packages):
            logger.info(f"[ADAPTER] Response package {i}: {pkg.package_id}")
        return response
    except Exception as adapter_error:
        import traceback

        error_traceback = traceback.format_exc()
        logger.error(f"[ADAPTER] create_media_buy failed:\n{error_traceback}")
        raise


def _persist_adapter_package_ids(
    media_buy_repo: "MediaBuyRepository",
    *,
    media_buy_id: str,
    platform_order_id: str,
    platform_line_item_ids: dict[str, str] | None = None,
    log_label: str = "",
) -> None:
    """Write platform_order_id (and optional line-item IDs) to all packages for a buy."""
    from sqlalchemy.orm import attributes

    prefix = f"[{log_label}] " if log_label else ""
    all_pkgs = {p.package_id: p for p in media_buy_repo.get_packages(media_buy_id)}
    for pkg_record in all_pkgs.values():
        if pkg_record.package_config is None:
            pkg_record.package_config = {}
        existing = pkg_record.package_config.get("platform_order_id")
        new_id = str(platform_order_id)
        if existing and existing != new_id:
            logger.warning(
                f"{prefix}platform_order_id mismatch on {pkg_record.package_id}: "
                f"existing={existing} new={new_id} — refusing to overwrite"
            )
            continue
        pkg_record.package_config["platform_order_id"] = new_id
        attributes.flag_modified(pkg_record, "package_config")
    logger.info(f"{prefix}Persisted platform_order_id to {len(all_pkgs)} package(s) for {media_buy_id}")

    if not platform_line_item_ids:
        logger.info(f"{prefix}No platform_line_item_ids found on response object")
        return

    logger.info(f"{prefix}Found platform_line_item_ids: {platform_line_item_ids}")
    for pkg_id, line_item_id in platform_line_item_ids.items():
        li_pkg = all_pkgs.get(pkg_id)
        if li_pkg:
            if li_pkg.package_config is None:
                li_pkg.package_config = {}
            existing_li = li_pkg.package_config.get("platform_line_item_id")
            new_li = str(line_item_id)
            if existing_li and existing_li != new_li:
                logger.warning(
                    f"{prefix}platform_line_item_id mismatch on {pkg_id}: "
                    f"existing={existing_li} new={new_li} — refusing to overwrite"
                )
                continue
            li_pkg.package_config["platform_line_item_id"] = new_li
            attributes.flag_modified(li_pkg, "package_config")
            logger.info(f"{prefix}Updated package {pkg_id} with platform_line_item_id: {line_item_id}")
        else:
            logger.warning(f"{prefix}Could not find package {pkg_id} to save platform_line_item_id")
    logger.info(f"{prefix}Saved platform_line_item_ids to database")


def _build_adapter_asset_from_creative(
    creative: Any,
    package_assignments: list[PackageAssignmentDict],
    *,
    tenant_id: str,
) -> tuple[dict[str, Any] | None, str | None]:
    """Build the asset dict for adapter.creatives_manager.add_creative_assets.

    Returns (asset, None) on success, (None, error_message) when url/width/height
    cannot be extracted. Shared by execute_approved_media_buy and push_creative_to_existing_buy.
    """
    creative_data = creative.data or {}
    format_spec = None
    # Prefer cached spec (same as auto-approval path); fall back to the resolver
    # (product overrides + agent search) on an UNKNOWN-format miss (None). A
    # typed transient AdCPSalesAgentError from either fetch PROPAGATES — a rate-limited
    # agent must not be degraded into a missing-spec asset error, and the
    # fallback must not mask it by re-fetching from the same throttled agent
    # (#1430 review). AdCPFormatNotFoundError from the resolver = genuinely
    # unknown -> proceed without a spec (extraction falls back to raw data).
    #
    # Skip BOTH the fetch and the fallback for an adapter-provided pseudo-URL
    # (e.g. broadstreet://<tenant_id>) — served in-process, so no dialled agent
    # could ever resolve it (salesagent-ypgd). Extraction below already falls
    # back to the creative's raw data fields when format_spec stays None.
    from src.core.format_resolver import is_dialled_agent_url

    if creative.format and creative.agent_url and is_dialled_agent_url(creative.agent_url):
        format_spec = _get_format_spec_sync(
            creative.agent_url,
            str(creative.format),
            provenance=CounterpartyUrl(field=None),
        )
    if format_spec is None and creative.format and creative.agent_url and is_dialled_agent_url(creative.agent_url):
        from src.core.exceptions import AdCPFormatNotFoundError
        from src.core.format_resolver import get_format

        try:
            format_spec = get_format(
                str(creative.format),
                agent_url=creative.agent_url,
                tenant_id=tenant_id,
                product_id=None,
                # Same buyer-supplied URL as the first fetch above — omitting
                # provenance here would silently reclassify it as operator
                # configuration and route it off the egress seam (salesagent-6gpt.1
                # diff-review finding).
                provenance=CounterpartyUrl(field=None),
            )
        except AdCPFormatNotFoundError as e:
            # Genuinely unknown format — proceed without a spec (extraction
            # falls back to the creative's raw data fields).
            logger.warning(log_safe(f"[ASSET] Could not load format spec for {creative.creative_id}: {e}"))
        except AdCPSalesAgentError:
            # Transient/typed agent failure — propagate with its recovery
            # semantics rather than degrading to a missing-spec asset error.
            raise
        except Exception as e:
            # Resolver internals (e.g. the product-override DB read) — keep the
            # historical resilience: log and build from raw creative data.
            logger.warning(log_safe(f"[ASSET] Could not load format spec for {creative.creative_id}: {e}"))

    url, width, height = extract_media_url_and_dimensions(creative_data, format_spec)
    click_url = extract_click_url(creative_data, format_spec)
    impression_tracker_url = extract_impression_tracker_url(creative_data, format_spec)

    if not url or not width or not height:
        return None, (
            f"Creative {creative.creative_id} missing url/width/height "
            f"(width={width}, height={height}, url={'set' if url else 'missing'}, format={creative.format})"
        )

    asset: dict[str, Any] = {
        "creative_id": creative.creative_id,
        "package_assignments": package_assignments,
        "width": width,
        "height": height,
        "url": url,
        "click_url": click_url,
        "asset_type": creative_data.get("asset_type", "image"),
        "name": creative.name or f"Creative {creative.creative_id}",
    }
    if impression_tracker_url:
        asset["delivery_settings"] = {"tracking_urls": {"impression": [impression_tracker_url]}}

    return asset, None


class ApprovalOutcome(StrEnum):
    """What an approval attempt did to the media buy."""

    EXECUTED = "executed"
    HELD_PENDING_CREATIVES = "held_pending_creatives"
    FAILED = "failed"


@dataclass(frozen=True)
class ApprovalResult:
    """The outcome of an approval, and the row state it produced.

    Replaces ``tuple[bool, str | None]``. The tuple could say "it worked" but not
    what state the buy reached, so every caller re-read the row to find out — and
    three callers then wrote their own answer over the one the callee had just
    committed. Carrying the state back is what lets a route render a flash and a
    webhook without touching the row, which is the whole point: a route that never
    reads the buy after the call cannot read a detached one.

    ``confirmed_at`` and ``revision`` are carried because the webhook envelope
    publishes both.
    """

    outcome: ApprovalOutcome
    status: PersistedMediaBuyStatus | None = None
    revision: int | None = None
    confirmed_at: datetime | None = None
    error_msg: str | None = None

    @property
    def ok(self) -> bool:
        """True when the adapter ran and the buy reached its post-approval status."""
        return self.outcome is ApprovalOutcome.EXECUTED

    @classmethod
    def failed(cls, error_msg: str) -> "ApprovalResult":
        return cls(outcome=ApprovalOutcome.FAILED, error_msg=error_msg)


def _mark_approval_failed(
    tenant_id: str,
    media_buy_id: str,
    error_msg: str,
    *,
    uow: "_MediaBuyUoWType | None" = None,
) -> ApprovalResult:
    """Record that the adapter did not create the order, and report it.

    Lives beside the single writer rather than in a route: the failure branch is a
    state transition like any other, and leaving it to callers is how one route
    came to write FAILED and two did not. Because nothing is written before the
    adapter runs, ``confirmed_at`` is still NULL here — the buy failed without
    ever carrying a seller commitment.

    ``uow`` JOINS the caller's transaction when it already has one open, rather
    than opening a second: ``get_db_session()`` hands back the thread-scoped
    session with no nesting refcount, so a second unit commits the caller's
    in-flight writes and then closes the session out from under it. The
    creative-upload branch of ``execute_approved_media_buy`` calls this with unflushed
    enrichment writes pending, which is exactly that shape. Passing nothing owns a
    transaction for the duration — the live behaviour for the branches that run once
    the caller's unit has already closed.
    """
    from contextlib import ExitStack

    from src.core.database.repositories.uow import MediaBuyUoW as _MediaBuyUoW

    try:
        with ExitStack() as stack:
            if uow is None:
                uow = stack.enter_context(_MediaBuyUoW(tenant_id))
            assert uow.media_buys is not None
            uow.media_buys.update_status(media_buy_id, PersistedMediaBuyStatus.FAILED)
    except SQLAlchemyError:
        # Narrow deliberately. A broad except here swallowed a NameError once and
        # reported it as an ad-server failure, which is a lie the caller cannot see
        # through: only a database problem is worth continuing past, and anything
        # else is a defect in this function that must not be disguised as one in
        # the adapter.
        logger.exception(log_safe(f"[APPROVAL] could not record FAILED for {media_buy_id}"))
    return ApprovalResult.failed(error_msg)


def execute_approved_media_buy(
    media_buy_id: str,
    tenant_id: str,
    *,
    approved_by: str | None = None,
    approved_at: datetime | None = None,
) -> ApprovalResult:
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

    from src.core.database.models import MediaPackage as DBMediaPackage
    from src.core.database.repositories import MediaBuyUoW

    logger.info(log_safe(f"[APPROVAL] Executing adapter creation for approved media buy {media_buy_id}"))

    adapter_ran = False

    try:
        # Load tenant and set context — single UoW for all reads
        with MediaBuyUoW(tenant_id) as uow:
            # FIXME(#1119): raw session usages below should migrate to repository methods
            assert uow.session is not None
            session = uow.session
            stmt_tenant = select(Tenant).filter_by(tenant_id=tenant_id)
            tenant_obj = session.scalars(stmt_tenant).first()

            if not tenant_obj:
                error_msg = f"Tenant {tenant_id} not found"
                logger.error(f"[APPROVAL] {error_msg}")
                return ApprovalResult.failed(error_msg)

            # Load media buy
            stmt = select(MediaBuy).filter_by(tenant_id=tenant_id, media_buy_id=media_buy_id)
            media_buy = session.scalars(stmt).first()

            if not media_buy:
                error_msg = f"Media buy {media_buy_id} not found"
                logger.error(f"[APPROVAL] {error_msg}")
                return ApprovalResult.failed(error_msg)

            # THE creative gate. One gate, before the adapter runs, tenant-scoped by
            # the repository's own tenant_id rather than by whatever predicate the
            # caller remembered. Three routes previously open-coded this and the
            # three disagreed — on tenant scoping, on whether `active` counts as
            # cleared, and on what an empty assignment list means.
            from src.core.database.repositories.creative import CreativeAssignmentRepository
            from src.core.database.repositories.media_buy import MediaBuyRepository as _MediaBuyRepository

            unapproved = CreativeAssignmentRepository(session, tenant_id).unapproved_creative_ids(media_buy_id)
            if unapproved:
                logger.info(
                    log_safe(f"[APPROVAL] Media buy {media_buy_id} held: {len(unapproved)} creative(s) not approved")
                )
                held = _MediaBuyRepository(session, tenant_id).update_status(
                    media_buy_id,
                    PersistedMediaBuyStatus.PENDING_CREATIVES,
                    approved_at=approved_at,
                    approved_by=approved_by,
                )
                session.commit()
                return ApprovalResult(
                    outcome=ApprovalOutcome.HELD_PENDING_CREATIVES,
                    status=PersistedMediaBuyStatus.PENDING_CREATIVES,
                    revision=held.revision if held else None,
                    confirmed_at=held.confirmed_at if held else None,
                    error_msg=f"{len(unapproved)} creative(s) not approved: {unapproved}",
                )

            # The buy's account, read off the row while the session is open (the row
            # detaches when this block commits); the executor acts on it below.
            buy_account_id = media_buy.account_id

            # ``account`` is resolved from the PERSISTED ROW, never synthesised:
            # MediaBuy.account_id is what the boundary resolved this buy's reference to,
            # so it is the same account. A row with no account_id is a seller-side store
            # defect and is refused (No Quiet Failures), not given a stand-in; the
            # census of such rows is a ticket question, not a code path.
            if buy_account_id is None:
                # The buy's id is a fact, so it rides the declared details class;
                # nothing here was caught, so there is no cause to chain.
                raise AdCPPersistedStateError(details=ConfigurationDetails(media_buy_id=media_buy_id))

            # What the adapter reads, built from the ROW through the carrier's own
            # persisted-request constructor. This replay is not a request: it reruns a
            # buy the seller already accepted, so it builds the adapter carrier and
            # never a CreateMediaBuyRequest. Rebuilding the DTO forced two inventions
            # that are gone — a synthetic idempotency key, prefixed to look internal,
            # for rows stored before the field was required, and an account re-injected
            # into a copy of raw_request — plus a package_id strip and a ValidationError
            # branch that existed only to satisfy the DTO.
            #
            # It does not hand-build the carrier either. This site once did, and it
            # omitted push_notification_config: already_approved=True makes GAM skip its
            # manual-approval branch and create the order itself, and when approve_order
            # comes back NO_FORECAST_YET, GAM reads that config for the webhook target it
            # tells the buyer the approval on. With it dropped, a buy approved off the
            # queue was never told its order had been approved. from_persisted_request
            # derives the field list from the carrier's declared fields, so this site
            # cannot omit the next one either.
            #
            # ``total_budget`` comes off the row's own column (written from the request's
            # package sum when the buy was created), not from raw_request.
            request = AdapterCreateRequest.from_persisted_request(
                media_buy.raw_request,
                total_budget=media_buy.budget or Decimal(0),
            )

            # Load packages from media_packages table
            # FIXME(#1119): migrate to uow.media_buys.get_packages()
            stmt_packages = select(DBMediaPackage).filter_by(media_buy_id=media_buy_id)
            db_packages = session.scalars(stmt_packages).all()

            if not db_packages:
                error_msg = f"No packages found for media buy {media_buy_id}"
                logger.error(f"[APPROVAL] {error_msg}")
                return ApprovalResult.failed(error_msg)

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
                        return ApprovalResult.failed(error_msg)

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
                        return ApprovalResult.failed(error_msg)

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
                        return ApprovalResult.failed(error_msg)

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
                        # Fallback for buys stored without pricing_info: the option's terms
                        # with no bid_price, because the package that bid one is not on hand.
                        package_pricing_info[package_id] = pricing_info_for(pricing_option_inner)

                    # Get targeting_overlay from package_config if present
                    # Fallback to "targeting" key for data written before fix.
                    # Corrupt targeting in package_config (non-dict input, schema drift)
                    # would otherwise surface from the outer ``except Exception`` below as
                    # an opaque message like "'str' object is not a mapping" — admin sees
                    # something cryptic, has to grep the audit log to find which package.
                    # Narrow exception + targeted message gives the approver actionable
                    # context immediately. Abort (not skip) — execute_approved_media_buy
                    # is a mutating operation; silently dropping targeting would ship
                    # a buy without the buyer's intended targeting.
                    targeting_overlay = None
                    targeting_raw = package_config.get("targeting_overlay") or package_config.get("targeting")
                    if targeting_raw:
                        try:
                            targeting_overlay = Targeting(**targeting_raw)
                        except (TypeError, ValidationError) as exc:
                            error_msg = (
                                f"Failed to reconstruct package {package_id}: "
                                f"targeting_overlay corrupt in package_config: {exc}"
                            )
                            logger.error(f"[APPROVAL] {error_msg}", exc_info=True)
                            return ApprovalResult.failed(error_msg)

                    # Create MediaPackage object (what adapters expect)
                    # Note: Product model has 'formats' not 'format_ids'
                    if not package_id:
                        error_msg = f"Package ID missing for package in media buy {media_buy_id}"
                        logger.error(f"[APPROVAL] {error_msg}")
                        return ApprovalResult.failed(error_msg)

                    delivery_type_str = enum_value(product.delivery_type)

                    # Validate delivery_type is a valid literal
                    if delivery_type_str not in ["guaranteed", "non_guaranteed"]:
                        delivery_type_str = "non_guaranteed"  # Default fallback

                    # Convert formats to FormatId objects with comprehensive validation
                    format_ids_list: list[FormatId] = []
                    formats = product.format_ids or []

                    logger.debug(f"[APPROVAL] Converting {len(formats)} formats for package {package_id}")

                    for idx, fmt in enumerate(formats):
                        try:
                            validated = FormatId.model_validate(fmt)
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
                            return ApprovalResult.failed(error_msg)

                    # Validate non-empty format_ids (required by AdCP spec)
                    if not format_ids_list:
                        error_msg = (
                            f"Failed to reconstruct package {package_id}: "
                            f"Product {product_id} has no valid formats - cannot create media buy"
                        )
                        logger.error(f"[APPROVAL] {error_msg}")
                        return ApprovalResult.failed(error_msg)

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
                    return ApprovalResult.failed(error_msg)
                except Exception as e:
                    error_msg = f"Failed to reconstruct package {db_pkg.package_id}: {str(e)}"
                    logger.error(f"[APPROVAL] {error_msg}")
                    return ApprovalResult.failed(error_msg)

            # Use start_time/end_time from media_buy (already resolved)
            start_time = media_buy.start_time
            end_time = media_buy.end_time

            # Validate required datetime fields
            if not start_time or not end_time:
                error_msg = f"Media buy {media_buy_id} missing required start_time or end_time"
                logger.error(f"[APPROVAL] {error_msg}")
                return ApprovalResult.failed(error_msg)

            # Captured while the session is open, because media_buy detaches when this
            # block commits. The RESOLUTION happens after the block closes — see below.
            buy_principal_id = media_buy.principal_id

            logger.info(
                f"[APPROVAL] Calling adapter for {media_buy_id}: "
                f"{len(packages)} packages, start={start_time}, end={end_time}"
            )

            # PRE-VALIDATE: Check all creatives have required fields BEFORE calling adapter
            # This prevents GAM order creation when creatives are invalid (all-or-nothing approach)
            _validate_creatives_before_adapter_call(packages, tenant_id, buy_principal_id, session=session)

        # Resolution from stored ids: this job acts as the buy's owner, on the buy's
        # account (refused above if the row has none). Resolved HERE, after the unit above
        # has closed, because identity_of opens its own sessions (TenantContext.load,
        # get_principal_by_id, and the account read when one is named) and
        # get_db_session() yields the THREAD-SCOPED session with no nesting refcount — so
        # calling it inside the block left the outer unit holding a closed session, and
        # anything it wrote afterwards committed on its own transaction or not at all
        # (GH #1644). Only the two ids cross the boundary, which is why the capture above
        # is separate from this line.
        identity = identity_of(tenant_id, buy_principal_id, buy_account_id)

        # Execute adapter creation (outside session to avoid conflicts)
        # Set BEFORE the call, not after: the condition is whether the ad server was
        # ASKED, not whether it answered. A raising adapter may still have created a
        # partial order, so that buy has failed — where a buy that never reached the
        # boundary was simply never attempted.
        adapter_ran = True
        response = _execute_adapter_media_buy_creation(
            request,
            packages,
            start_time,
            end_time,
            package_pricing_info,
            identity,
        )

        logger.info(log_safe(f"[APPROVAL] Adapter creation succeeded for {media_buy_id}: {response.media_buy_id}"))

        # Persist adapter IDs to package_config.
        # platform_order_id is per-buy — always write to all packages so retroactive creative
        # push works regardless of whether the adapter also provides per-package line-item IDs.
        # platform_line_item_id is per-package and only present when the adapter maps them.
        platform_line_item_ids = response.platform_line_item_ids
        if response.media_buy_id:
            with MediaBuyUoW(tenant_id) as uow_plids:
                assert uow_plids.media_buys is not None
                _persist_adapter_package_ids(
                    uow_plids.media_buys,
                    media_buy_id=media_buy_id,
                    platform_order_id=str(response.media_buy_id),
                    platform_line_item_ids=platform_line_item_ids or None,
                    log_label="APPROVAL",
                )
        else:
            logger.info("[APPROVAL] Adapter returned no media_buy_id — skipping ID persistence")

        # Upload and associate inline creatives if any exist
        # This handles inline creatives that were uploaded during initial media buy creation
        with MediaBuyUoW(tenant_id) as uow2:
            # FIXME(#1788): creative handling should use repository methods
            assert uow2.session is not None
            session = uow2.session
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

                # Load all creatives scoped to the buy's principal: the composite PK
                # (creative_id, tenant_id, principal_id) allows the same creative_id
                # under two principals — a tenant-only load keyed by bare creative_id
                # could upload the wrong principal's creative to the ad server.
                all_creative_ids = list(packages_by_creative.keys())
                creatives = CreativeRepository(session, tenant_id).get_by_ids(all_creative_ids, buy_principal_id)

                # Create creative map
                creative_map = {c.creative_id: c for c in creatives}

                # Build assets list for adapter and collect all validation errors
                assets = []
                all_validation_errors = []
                for creative_id, package_assignment_list in packages_by_creative.items():
                    creative = creative_map.get(creative_id)
                    if not creative:
                        logger.warning(log_safe(f"[APPROVAL] Creative {creative_id} not found in database"))
                        continue

                    # Hold back pending_review creatives — pushed retroactively on approval (#1038)
                    if creative.status == "pending_review":
                        logger.info(
                            log_safe(
                                f"[APPROVAL] Holding back creative {creative_id} (pending_review) from adapter upload"
                            )
                        )
                        continue

                    asset, err = _build_adapter_asset_from_creative(
                        creative, package_assignment_list, tenant_id=tenant_id
                    )
                    if err:
                        all_validation_errors.append(err)
                        logger.error(f"[APPROVAL] {err}")
                        continue
                    assert asset is not None

                    assets.append(asset)

                # If we found validation errors, fail with complete error list
                if all_validation_errors:
                    error_msg = (
                        f"Cannot approve media buy: {len(all_validation_errors)} creatives have validation errors:\n"
                        + "\n".join(f"  • {err}" for err in all_validation_errors)
                        + "\n\nAll creatives must have dimensions (width/height) and a content URL."
                    )
                    logger.error(f"[APPROVAL] {error_msg}")
                    return _mark_approval_failed(tenant_id, media_buy_id, error_msg, uow=uow2)

                if assets:
                    logger.info(f"[APPROVAL] Uploading {len(assets)} creatives to adapter")

                    # Get adapter and upload creatives
                    adapter = get_adapter(identity)

                    # Call adapter's add_creative_assets method
                    # For GAM, the media_buy_id is the GAM order ID
                    gam_order_id: str = response.media_buy_id

                    try:
                        if hasattr(adapter, "creatives_manager") and adapter.creatives_manager and gam_order_id:
                            asset_statuses = adapter.creatives_manager.add_creative_assets(
                                gam_order_id, assets, datetime.now(UTC)
                            )
                            logger.info(f"[APPROVAL] Creative upload completed: {len(asset_statuses)} assets processed")

                            # Enrich creatives (concept + platform id) on success; log failures.
                            # This is the manual-approval push path — the third of three GAM push
                            # sites, wired here so admin-approved buys enrich like the other two
                            # (#1506). uow2 commits the writeback on block exit.
                            creatives_repo = CreativeRepository(session, tenant_id)
                            for status in asset_statuses:
                                if status.status == "failed":
                                    logger.error(
                                        log_safe(
                                            f"[APPROVAL] Failed to upload creative {status.creative_id}: {status.message}"
                                        )
                                    )
                                    continue
                                if not status.creative_id:
                                    continue
                                creative = creative_map.get(status.creative_id)
                                if creative is None:
                                    continue
                                merged = _apply_creative_enrichment(creative, status)
                                if merged is not None:
                                    creatives_repo.update_data(creative, merged)
                        else:
                            logger.warning("[APPROVAL] Adapter does not support creative upload, skipping")
                    except Exception as creative_error:
                        # Creative upload failed - this is critical for GAM orders
                        error_msg = f"Failed to upload creatives to adapter: {str(creative_error)}"
                        logger.error(f"[APPROVAL] {error_msg}", exc_info=True)
                        return _mark_approval_failed(tenant_id, media_buy_id, error_msg, uow=uow2)
            else:
                logger.info(
                    log_safe(f"[APPROVAL] No creative assignments found for {media_buy_id}, skipping creative upload")
                )

        # After creatives are uploaded (or skipped), retry order approval
        # This is necessary because:
        # 1. GAM may still be processing inventory forecasts (NO_FORECAST_YET error)
        # 2. Creatives may have been uploaded after the initial approval attempt
        logger.info(log_safe(f"[APPROVAL] Attempting to approve order {response.media_buy_id} in GAM"))
        try:
            adapter = get_adapter(identity)
            if hasattr(adapter, "orders_manager") and adapter.orders_manager:
                approval_success = adapter.orders_manager.approve_order(response.media_buy_id)
                if approval_success:
                    logger.info(log_safe(f"[APPROVAL] Successfully approved GAM order {response.media_buy_id}"))
                else:
                    # GAM approval failed - return failure so status can be updated
                    error_msg = (
                        f"Failed to approve order {response.media_buy_id}, "
                        f"it will remain in DRAFT status. This may be due to missing creatives or "
                        f"GAM still processing inventory forecasts."
                    )
                    logger.warning(f"[APPROVAL] {error_msg}")
                    return _mark_approval_failed(tenant_id, media_buy_id, error_msg)
            else:
                logger.info("[APPROVAL] Adapter does not support order approval, skipping")
        except Exception as approval_error:
            # Approval exception - return failure
            error_msg = f"Failed to approve order {response.media_buy_id}: {str(approval_error)}"
            logger.error(f"[APPROVAL] {error_msg}", exc_info=True)
            return _mark_approval_failed(tenant_id, media_buy_id, error_msg)

        # THE post-adapter write. One writer, one write, one revision bump.
        #
        # The row is re-fetched inside this UoW rather than reusing the instance
        # loaded before the adapter call: that instance was detached when a nested
        # get_db_session() closed the shared scoped session, and resolving a flight
        # window off a detached row is the defect this function exists to stop the
        # routes from committing. Reusing it here would move that defect one frame
        # down rather than remove it.
        with MediaBuyUoW(tenant_id) as uow3:
            assert uow3.media_buys is not None
            fresh = uow3.media_buys.get_by_id(media_buy_id)
            if fresh is None:
                return ApprovalResult.failed(f"media buy {media_buy_id!r} vanished during approval")
            resolved = resolve_flight_window_status(
                fresh,
                now=datetime.now(UTC),
                creatives_approved=True,
            )
            assert resolved is not None, (
                "flight_window() returned None for a persisted media buy; "
                "MediaBuy.start_date/end_date are NOT NULL, so this cannot happen"
            )
            written = uow3.media_buys.update_status(
                media_buy_id,
                resolved,
                # Reached only after the adapter created the order, so this is the
                # commitment instant for every buy that arrives through approval.
                # The creative-review HOLD returns above this line, before the
                # adapter, and so leaves the default and stamps nothing.
                seller_committed=True,
                approved_at=approved_at,
                approved_by=approved_by,
            )
            assert written is not None, f"media buy {media_buy_id!r} vanished mid-write"
            logger.info(log_safe(f"[APPROVAL] Media buy {media_buy_id} -> {resolved}"))
            return ApprovalResult(
                outcome=ApprovalOutcome.EXECUTED,
                status=resolved,
                revision=written.revision,
                confirmed_at=written.confirmed_at,
            )

    except AdCPPersistedStateError as e:
        # The persisted row refused reconstruction (a NULL account_id, above); no adapter
        # ran, so this is a store defect and not an adapter failure. The buy stays
        # pending_approval so an operator who repairs the row can retry.
        error_msg = f"Persisted media buy {media_buy_id} cannot be acted on: {e}"
        logger.error(f"[APPROVAL] {error_msg}", exc_info=True)
        return ApprovalResult.failed(error_msg)
    except Exception as e:
        import traceback

        error_traceback = traceback.format_exc()
        error_msg = f"Adapter creation failed: {str(e)}"
        logger.error(f"[APPROVAL] {error_msg}\n{error_traceback}")
        if not adapter_ran:
            # The adapter never ran, so nothing was created and the buy has not failed —
            # it was never attempted. Persisting FAILED here would be a dead end: all
            # three approval routes gate on status == "pending_approval", so an operator
            # who fixed the underlying row could never retry.
            return ApprovalResult.failed(error_msg)
        return _mark_approval_failed(tenant_id, media_buy_id, error_msg)


def push_creative_to_existing_buy(
    *,
    creative_id: str,
    media_buy_id: str,
    tenant_id: str,
) -> tuple[bool, str | None]:
    """Push a single approved creative to an already-active ad server line item.

    Called from approve_creative when the buy is already live but this creative
    was in pending_review at buy-approval time and was held back (#1038).
    Local approval has already committed — failures here are non-fatal and logged.
    Re-approval is safe: skips the adapter when platform_creative_id is already set.

    Returns (success, error_message). error_message is non-None only on failure.
    """
    from src.core.database.repositories.uow import AdminCreativeUoW

    try:
        # TWO PHASES, SEQUENTIAL, NEVER NESTED — and the ordering is load-bearing.
        #
        # identity_of opens its own sessions (TenantContext.load, get_principal_by_id, and
        # the account read when one is named), and get_db_session() yields the
        # THREAD-SCOPED session with no nesting refcount — so its exit runs
        # session.close(); scoped.remove() on the SAME session object an enclosing unit
        # holds. Called from inside the work block, as it was, it left that unit holding a
        # closed session, and the reads and the update_data WRITE below ran against it:
        # the push returned success while the enrichment was silently never persisted
        # (GH #1644, graded by tests/integration/test_push_creative_to_existing_buy.py).
        #
        # The owner is a fact of the stored row, so phase one reads it and CLOSES, phase
        # two resolves with nothing open, and the work block opens afterwards. Inline
        # rather than extracted: the admin lookup carries this function's sanctioned
        # allowlist entry in test_architecture_creative_lookup_principal_scoped, and a
        # helper would split one sanctioned violation into two.
        with AdminCreativeUoW(tenant_id) as owner_uow:
            assert owner_uow.creatives is not None
            owner_row = owner_uow.creatives.admin_get_by_id(creative_id)
            if owner_row is None:
                return False, f"Creative {creative_id} not found"
            owner_principal_id = owner_row.principal_id

        # Nothing is open here. This is the whole point of the split.
        identity = identity_of(tenant_id, owner_principal_id)

        with AdminCreativeUoW(tenant_id) as uow:
            assert uow.creatives is not None
            assert uow.assignments is not None
            assert uow.media_buys is not None
            assert uow.tenant_config is not None

            tenant_obj = uow.tenant_config.get_tenant()
            if not tenant_obj:
                return False, f"Tenant {tenant_id} not found"

            creative = uow.creatives.admin_get_by_id(creative_id)
            if not creative:
                return False, f"Creative {creative_id} not found"
            if creative.status not in {"approved", "active"}:
                return False, f"Creative {creative_id} is not approved (status={creative.status})"

            if (creative.data or {}).get("platform_creative_id"):
                logger.info(
                    log_safe(
                        f"[GATE-PUSH] Creative {creative_id} already has platform_creative_id — "
                        f"skipping adapter call (already uploaded)"
                    )
                )
                return True, None

            all_assignments = uow.assignments.get_by_creative(creative_id)
            matching = [a for a in all_assignments if a.media_buy_id == media_buy_id]
            if not matching:
                return False, f"No assignment of creative {creative_id} to media buy {media_buy_id}"

            # Resolved above, outside every unit. See the comment at the top of the try.
            adapter = get_adapter(identity)
            if not (hasattr(adapter, "creatives_manager") and adapter.creatives_manager):
                return False, "Adapter does not support creative upload"

            # platform_order_id is per-buy — any package on this buy has the same value
            media_package = uow.media_buys.get_package(media_buy_id, matching[0].package_id)
            if not media_package:
                return False, f"No package found for media buy {media_buy_id}"

            platform_order_id = (media_package.package_config or {}).get("platform_order_id")
            if not platform_order_id:
                return False, f"Media buy {media_buy_id} has no platform_order_id — buy may not be live yet"

            package_assignments: list[PackageAssignmentDict] = [
                {"package_id": a.package_id, "weight": a.weight or 100} for a in matching
            ]
            asset, build_err = _build_adapter_asset_from_creative(creative, package_assignments, tenant_id=tenant_id)
            if build_err:
                return False, build_err
            assert asset is not None

            try:
                asset_statuses = adapter.creatives_manager.add_creative_assets(
                    platform_order_id,
                    [asset],
                    datetime.now(UTC),
                )
            except Exception as e:
                logger.error(
                    log_safe(f"[GATE-PUSH] Adapter raised pushing creative {creative_id} to buy {media_buy_id}: {e}"),
                    exc_info=True,
                )
                return False, str(e)

            for status in asset_statuses:
                if status.creative_id == creative_id:
                    if status.status == "failed":
                        return False, status.message or "Adapter reported upload failure"
                    merged_data = _apply_creative_enrichment(creative, status)
                    if merged_data is not None:
                        uow.creatives.update_data(creative, merged_data)
                    logger.info(
                        log_safe(
                            f"[GATE-PUSH] Pushed creative {creative_id} to live buy "
                            f"{media_buy_id} (platform order {platform_order_id})"
                        )
                    )
                    return True, None
            return False, f"Adapter did not report status for creative {creative_id}"

    except Exception as e:
        logger.error(
            log_safe(f"[GATE-PUSH] Unexpected error pushing creative {creative_id} to buy {media_buy_id}: {e}"),
            exc_info=True,
        )
        return False, str(e)


def _validate_pricing_model_selection(
    package: Package | PackageRequest | AdcpPackageRequest,
    product: Any,  # ProductModel from database
    campaign_currency: str | None,
) -> dict[str, Any]:
    """Validate pricing model selection for a package against product's pricing options.

    Args:
        package: Package naming a pricing_option_id, with an optional bid_price
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
        f"bid_price={package.bid_price}, budget={package.budget}"
    )

    # All products must have pricing_options
    if not product.pricing_options or len(product.pricing_options) == 0:
        raise AdCPConfigurationError(details=ConfigurationDetails(product_id=product.product_id))

    # Which pricing option to use. pricing_option_id is the pin's only selector
    # (media-buy/package-request.json /required, AdCP 3.1.1); the legacy pricing_model
    # alias that used to be consulted as a fallback is gone with the field.
    pricing_option_id = package.pricing_option_id

    # Helper to unwrap RootModel - adcp 2.14.0+ uses RootModel wrapper
    def unwrap_option(opt: Any) -> Any:
        return getattr(opt, "root", opt)

    # Not specified: use the product's first pricing option
    if not pricing_option_id:
        first_option = unwrap_option(product.pricing_options[0])
        # The option's own terms, plus the one field it cannot supply: a currency-less
        # option falls back to the campaign's, which is a property of this request.
        return pricing_info_for(first_option, bid_price=float(package.bid_price) if package.bid_price else None) | {
            "currency": first_option.currency or campaign_currency or "USD"
        }

    # Find matching pricing option
    selected_option = None
    for option in product.pricing_options:
        opt_inner = unwrap_option(option)
        # The stored id -- the same one get_products announced and the buyer sent back.
        option_id = opt_inner.pricing_option_id

        # Match by pricing_option_id, the id get_products announced
        if pricing_option_id.lower() == option_id.lower():
            selected_option = opt_inner
            break

    if not selected_option:
        # Show available options in same format as matching logic expects
        available_options = [
            f"{unwrap_option(opt).pricing_option_id} "
            f"({unwrap_option(opt).pricing_model} - {unwrap_option(opt).currency})"
            for opt in product.pricing_options
        ]
        # The four accumulated branches used to build a sentence; each branch's VALUE is
        # what the buyer needs, so they travel as structured detail instead. The sibling
        # below (bid_price/floor_price) already relocates the same way.
        raise AdCPValidationError(
            # Reached only with a pricing_option_id in hand: an absent one returned the
            # product's first option above, so the pointer names that field unconditionally.
            field="pricing_option_id",
            # The three conditional spreads named one fact three ways -- which
            # requested value was not found. `rejected_value` is that fact, and
            # to_wire() drops it when unset, so the conditionals are unnecessary.
            details=PricingValidationDetails(
                product_id=product.product_id,
                available_pricing_options=available_options,
                rejected_value=pricing_option_id,
            ),
        )

    # Validate auction pricing
    if not selected_option.is_fixed:
        if not package.bid_price:
            raise AdCPValidationError(
                field="bid_price",
                details=PricingValidationDetails(
                    product_id=product.product_id,
                    pricing_model=str(selected_option.pricing_model),
                    floor_price=(
                        str(selected_option.price_guidance.get("floor")) if selected_option.price_guidance else None
                    ),
                ),
            )

        floor_price = (
            Decimal(str(selected_option.price_guidance.get("floor", 0)))
            if selected_option.price_guidance
            else Decimal("0")
        )
        bid_decimal = Decimal(str(package.bid_price))

        if bid_decimal < floor_price:
            raise AdCPValidationError(
                details=PricingValidationDetails(
                    bid_price=str(package.bid_price),
                    floor_price=str(floor_price),
                    pricing_model=str(selected_option.pricing_model),
                ),
            )

    # Validate fixed pricing has rate
    if selected_option.is_fixed and not selected_option.rate:
        raise AdCPConfigurationError(details=ConfigurationDetails(product_id=product.product_id))

    # Validate minimum spend per package
    if selected_option.min_spend_per_package:
        package_budget = None
        if package.budget is not None:
            # Package.budget is now always float | None (per AdCP spec)
            package_budget = Decimal(str(package.budget))

        if package_budget and package_budget < Decimal(str(selected_option.min_spend_per_package)):
            raise AdCPValidationError(
                details=PricingValidationDetails(
                    package_budget=str(package_budget),
                    currency=selected_option.currency,
                    min_spend_per_package=str(selected_option.min_spend_per_package),
                    pricing_model=str(selected_option.pricing_model),
                ),
            )

    # Return validated pricing information
    return pricing_info_for(selected_option, bid_price=float(package.bid_price) if package.bid_price else None)


def _build_registered_agent_urls(registered_agents: list[Any], tenant_id: str) -> set[str]:
    """Normalize each registered agent's URL for registration-matching comparison.

    A malformed PRE-EXISTING registered agent_url (admin-ingested data the buyer
    never touched) is skipped with a warning rather than raised: it can never
    legitimately equal a well-formed incoming agent_url, so excluding it changes
    nothing for a correct match, but hard-failing here would let ONE unrelated
    bad registration break every format_id validation in the request
    (#1291).
    """
    from src.core.signing.canonical import TargetUriMalformedError

    registered_agent_urls = set()
    for agent in registered_agents:
        try:
            registered_agent_urls.add(canonical_agent_url(agent.agent_url))
        except TargetUriMalformedError as e:
            logger.warning(
                f"Tenant {tenant_id}: registered creative agent_url {agent.agent_url!r} is malformed "
                f"per RFC 9421 canonicalization ({e.reason}); excluded from registration matching."
            )
    return registered_agent_urls


def _normalize_incoming_agent_url(agent_url: str, package_idx: int, idx: int) -> str:
    """Normalize a buyer-supplied format_id.agent_url, or raise with the same
    'Package N, format_ids[idx]' context every sibling validation in
    ``_validate_and_convert_format_ids`` carries (#1291).
    """
    from src.core.signing.canonical import TargetUriMalformedError

    try:
        return canonical_agent_url(agent_url)
    except TargetUriMalformedError as e:
        # The refusal REASON is the signing layer's diagnostic, not buyer-facing text:
        # post-ADR-010 the sentence is CODE_TABLE's property and the cause rides
        # ``internal_detail``. What the buyer gets is the position and the offending
        # value, in the same ``ValidationDetails`` shape every sibling rejection here uses.
        raise AdCPValidationError(
            field=f"packages[{package_idx}].format_ids[{idx}]",
            details=ValidationDetails(package_index=package_idx, format_index=idx, rejected_value=str(agent_url)),
            internal_detail=e,
        ) from e


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
    from src.core.creative_agent_registry import get_creative_agent_registry

    if not format_ids:
        return []

    # The registry the DEPLOYMENT selected, never a hand-constructed one: which class
    # answers an operator agent (live dial vs the checked-in reference catalog) is a
    # composition decision `get_creative_agent_registry()` owns, and constructing
    # `CreativeAgentRegistry()` here quietly opted this path out of it — the one call
    # site in src/ still doing so after the settings refactor.
    registry = get_creative_agent_registry()
    validated_format_ids = []

    # Get registered agents for this tenant.
    #
    # Both sides of the registration check go through `canonical_agent_url` — the AdCP
    # canonical form, which PRESERVES the path. This check used to run on
    # `validation.normalize_agent_url`, which additionally stripped `/mcp`, `/a2a` and
    # `/.well-known/adcp/sales`. Nothing in the pin asks for that, and it decided an
    # AUTHORIZATION outcome: an agent registered at `https://x.com` also authorized
    # `https://x.com/mcp`, and one host serving MCP at /mcp and A2A at /a2a read as a
    # single agent.
    #
    # The set is built through `_build_registered_agent_urls` rather than a bare
    # comprehension so that ONE malformed pre-existing registration (admin-ingested data
    # the buyer never touched) is skipped with a warning instead of failing every
    # format_id in the request (#1291).
    registered_agents = registry._get_tenant_agents(tenant_id)
    registered_agent_urls = _build_registered_agent_urls(registered_agents, tenant_id)

    for idx, fmt_id in enumerate(format_ids):
        # Every rejection here is per-package AND per-format, so the position and the
        # offending identifiers travel as structured detail. Without them the buyer gets
        # a bare VALIDATION_ERROR and cannot tell WHICH format in WHICH package failed.
        where = {"package_index": package_idx, "format_index": idx}
        field = f"packages[{package_idx}].format_ids[{idx}]"

        # STRICT ENFORCEMENT: Reject plain strings
        if isinstance(fmt_id, str):
            raise AdCPValidationError(
                field=field,
                details=ValidationDetails(**where, rejected_value=str(fmt_id), received_type="FormatId object"),
            )

        # Coerce to FormatId via Pydantic validation (handles dicts and FormatId objects)
        try:
            validated_fmt = FormatId.model_validate(fmt_id, from_attributes=True)
        except (ValueError, ValidationError) as e:
            raise AdCPValidationError(field=field, details=ValidationDetails(**where), internal_detail=e) from e
        # Canonicalized ONCE, here, and every later use (the registration check and the
        # registry fetch) reads this value. A URL the canonicalization profile refuses
        # outright — no host, bare IPv6, unclosed bracket — comes back as a positioned
        # VALIDATION_ERROR rather than an uncaught ValueError (#1291).
        agent_url = _normalize_incoming_agent_url(str(validated_fmt.agent_url), package_idx, idx)
        format_id = validated_fmt.id

        if not agent_url or not format_id:
            raise AdCPValidationError(
                field=field, details=ValidationDetails(**where, agent_url=agent_url, format_id=format_id)
            )

        # VALIDATION: Check agent is registered. `agent_url` is already canonical (above),
        # and so is every member of `registered_agent_urls` — one form, both sides.
        if agent_url not in registered_agent_urls:
            raise AdCPAuthorizationError(field=field, details=EntityRefDetails(**where, agent_url=agent_url))

        # VALIDATION: Verify format exists on agent
        try:
            format_obj = await registry.get_format(agent_url, format_id)
            if not format_obj:
                raise AdCPFormatNotFoundError(
                    field=field,
                    details=EntityRefDetails(**where, agent_url=agent_url, format_id=format_id),
                )
        except AdCPSalesAgentError:
            raise
        except Exception as e:
            logger.exception(f"Error fetching format {format_id} from {agent_url}: {e}")
            raise AdCPAdapterError(
                field=field,
                details=AdapterFailureDetails(**where, agent_url=agent_url, format_id=format_id),
                internal_detail=e,
            ) from e

        # Format validated - add to results
        validated_format_ids.append({"agent_url": str(agent_url), "id": format_id})

    return validated_format_ids


from src.core.errors.details import (
    AdapterFailureDetails,
    CapabilityRefusalDetails,
    ConfigurationDetails,
    CreativeRefDetails,
    EntityRefDetails,
    ErrorProblem,
    InvalidStateDetails,
    PricingValidationDetails,
    ProductRefDetails,
    TimeWindowDetails,
    ValidationDetails,
)
from src.services.setup_checklist_service import validate_setup_complete
from src.services.slack_notifier import get_slack_notifier


def _raise_degraded_replay_outcome(
    tenant_id: str,
    idempotency_key: str,
    principal_id: str,
    *,
    account_id: str | None = None,
    req: CreateMediaBuyRequest | None = None,
) -> NoReturn:
    """Fail closed when the dup-booking backstop fired.

    Reached only when this create lost the commit race for a key another request already
    booked (the ``MediaBuy.idempotency_key`` unique index fired). Replay itself is not this
    function's job -- :func:`src.core.tools._boundary._invoke` probes the verbatim cache
    before any transport reaches this implementation, so a retry replays there. What is left
    is telling the buyer WHICH kind of loss this was. The lookup is account-scoped (the spec
    idempotency scope is agent + account + key).

    Per the spec, verbatim replay is byte-for-byte or nothing: a reconstructed body the buyer
    cannot distinguish from a faithful replay is the named failure mode, so this path never
    fabricates a response. Outcomes, in order:

    - no same-key buy: terminal ``CONFIGURATION_ERROR`` (impossible-state guard),
    - buy outlived the replay TTL: ``IDEMPOTENCY_EXPIRED`` (rule 6 fail-closed) -- the
      boundary's probe filters expired rows, so without this the buyer would silently
      re-derive a booking the seller can no longer replay,
    - canonical payload differs from the buy's stored hash: ``IDEMPOTENCY_CONFLICT``
      (rule 5). The boundary answers this for every request inside the replay window; the
      buy's ``payload_hash`` column is the DURABLE signal that outlives the cache row, so
      the answer survives eviction. Legacy rows without a stored hash carry no signal.
    - otherwise: transient ``SERVICE_UNAVAILABLE`` with a short ``retry_after`` -- the
      winner's cache write is in flight; the buyer's retry replays the verbatim envelope at
      the boundary once it lands.
    """
    # Lazy: tests patch src.core.database.repositories.MediaBuyUoW; the call-time import binds the patched object.
    from src.core.database.repositories import MediaBuyUoW

    with MediaBuyUoW(tenant_id) as uow:
        assert uow.media_buys is not None
        assert uow.idempotency_attempts is not None
        existing = uow.media_buys.find_by_idempotency_key(idempotency_key, principal_id, account_id=account_id)
        if existing is None:
            # Impossible-state guard: the race resolved, yet no buy carries the key.
            # TERMINAL is load-bearing here and the docstring above depends on it —
            # the very next outcome is a TRANSIENT SERVICE_UNAVAILABLE for "the
            # winner's cache write is in flight, retry". Grading this one transient
            # too would collapse the two into one wire answer and invite a retry of
            # a state that cannot resolve itself. CONFIGURATION_ERROR is the pinned
            # TERMINAL code and its meaning fits: a server-side inconsistency only a
            # human at the seller can act on. INTERNAL_ERROR does NOT fit: its pinned
            # recovery is transient.
            raise AdCPConfigurationError(details=ConfigurationDetails(idempotency_key=idempotency_key))

        # Rule 6 (security.mdx#idempotency): a key the seller has seen whose
        # replay window has expired rejects rather than silently re-deriving —
        # the buyer cannot tell a faithful replay from a reconstruction this old.
        # Anchor the boundary on the cache row's STORED expires_at — the single
        # replay-window authority the probe path filters on. Fall back to the
        # MediaBuy creation time only when no cache row survives (evicted after
        # expiry, or the race winner's cache write still in flight).
        cached = uow.idempotency_attempts.find_including_expired(
            principal_id=principal_id, idempotency_key=idempotency_key, account_id=account_id
        )
        now = datetime.now(UTC)
        window_expired = (
            cached.expires_at <= now if cached is not None else now - existing.created_at > DEFAULT_REPLAY_TTL
        )
        if window_expired:
            raise AdCPIdempotencyExpiredError()

        # Rule 5, from the durable signal. Canonicalised HERE rather than passed in: this is
        # not an ``_impl``, so it may dump the request it was handed, and computing it at the
        # one site that compares it is what keeps the hash from becoming plumbing again.
        if req is not None:
            raise_on_payload_conflict(existing.payload_hash, canonical_request_hash(req))

    raise AdCPServiceUnavailableError(
        retry_after=1,
    )


def _submitted_approval_result(step, req: CreateMediaBuyRequest, adapter) -> CreateMediaBuyResult:
    """The submitted task envelope for a create that awaits human approval.

    Spec 3.1.1 create-media-buy-response.json: a buy awaiting a human decision is
    the CreateMediaBuySubmitted variant — status="submitted" + task_id only.
    media_buy_id/packages land on the task's completion artifact; confirmed_at/
    revision would falsely assert seller commitment (PR #1567 round-2 item 2;
    mirrors the update-path fix b8b7e751b). Single construction site shared by the
    manual-approval and config-approval branches (DRY, PR #1567 round-3).
    """
    return CreateMediaBuySubmitted(
        task_id=step.step_id,  # Client tracks approval via this ID
        errors=property_list_unsupported_advisories(req.packages, adapter),
        message=f"Media buy submitted for approval (task {step.step_id}).",
        # No explicit status: the branch's own field is a const "submitted" in the pin, and
        # stating it again here is a second place for it to be wrong.
    )


# The media_buys dup-booking backstop: a unique index over the idempotency scope
# (tenant, principal, account, key). A concurrent same-key create that loses the
# commit race violates THIS index — the signal we resolve to the winner's reply.
_IDEMPOTENCY_BACKSTOP_INDEX = "idx_media_buys_idempotency_key"


def _is_idempotency_backstop_violation(exc: IntegrityError) -> bool:
    """True iff ``exc`` is the media_buys idempotency-key unique-index collision.

    The single home for the "is this the idempotency race?" decision. The
    prefix-match-then-message-fallback mechanism is shared with every other
    constraint-narrowed recovery (``is_constraint_violation``); only the index and
    the fallback token are specific to this one. The token stays the bare column
    name rather than the index name so the fallback keeps matching drivers whose
    message names the column.
    """
    return is_constraint_violation(exc, _IDEMPOTENCY_BACKSTOP_INDEX, message_token="idempotency_key")


def _resolve_idempotency_race_or_raise(
    exc: IntegrityError,
    tenant_id: str,
    *,
    idempotency_key: str | None,
    principal_id: str,
    account_id: str | None,
    req: CreateMediaBuyRequest | None = None,
    media_buy_id: str | None = None,
) -> NoReturn:
    """Shared handler for the unique-index ``IntegrityError`` on both booking paths.

    Decides ONCE (via :func:`_is_idempotency_backstop_violation`) whether the failure is the
    idempotency-backstop collision; an unrelated integrity error re-raises unchanged. A
    backstop collision means another request won the commit for this key, so this one has no
    booking to report and :func:`_raise_degraded_replay_outcome` says which kind of loss it
    was. An orphan adapter-side order may exist.
    """
    if not _is_idempotency_backstop_violation(exc):
        raise exc
    logger.warning(
        "Idempotency race: another request won the commit for key %s%s. "
        "Failing closed; the buyer's retry replays the winner's response at the boundary. "
        "An orphan adapter-side order may exist.",
        idempotency_key,
        f" ({media_buy_id})" if media_buy_id else "",
    )
    _raise_degraded_replay_outcome(
        tenant_id,
        # Non-null whenever the backstop index fired; `or ""` only narrows the type.
        idempotency_key or "",
        principal_id,
        account_id=account_id,
        req=req,
    )


async def _create_media_buy_impl(
    req: CreateMediaBuyRequest,
    identity: AccountIdentity,
) -> CreateMediaBuyResult:
    """Create a media buy with the specified parameters.

    ``req.idempotency_key`` arrives here but this function neither probes nor caches by it:
    :func:`src.core.tools._boundary._invoke` replays a stored success and caches a fresh one
    around this call. What stays is the dup-booking backstop -- the ``media_buys`` unique
    index over (tenant, principal, account, key), whose ``IntegrityError`` only this
    function's transaction can see.

    Args:
        req: Validated CreateMediaBuyRequest with all protocol fields
        (push_notification_config is a REQUEST field, read off ``req``)
        identity: ResolvedIdentity with principal/tenant info (transport-agnostic)

    Returns:
        CreateMediaBuyResult wrapping response and status
    """
    request_start_time = time.time()

    # Warn if unsupported reporting_webhook frequency is requested
    if req.reporting_webhook:
        raw_freq = str(getattr(req.reporting_webhook, "reporting_frequency", None) or "daily").lower()
        if raw_freq != "daily":
            logger.warning(
                "CreateMediaBuy requested reporting webhook frequency '%s', "
                "but only 'daily' frequency is currently supported. "
                "Hourly and monthly reporting will be ignored until implemented.",
                raw_freq,
            )

    # The one principal this tool holds: the row the resolver loaded, non-optional on a
    # ResolvedIdentity. Its name is read off it below; nothing here loads a principal by id.
    principal = identity.principal
    principal_id = principal.principal_id
    tenant = identity.tenant

    # SSRF gate at registration — after auth so unauthenticated callers get AUTH
    # first. Must run before workflow metadata / DB writes.
    # Use str(url): library ReportingWebhook.url is pydantic AnyUrl, not str.
    if req.reporting_webhook:
        rw_url = getattr(req.reporting_webhook, "url", None)
        reject_unsafe_webhook_registration_url(
            str(rw_url) if rw_url is not None else None,
            field="reporting_webhook.url",
        )
    if req.push_notification_config:
        registration = accept_push_notification_config(
            req.push_notification_config,
            field_prefix="push_notification_config",
        )

    validate_setup_complete(tenant.tenant_id)

    # No second webhook-URL verdict here: the stored-then-fetched URLs already
    # got their correctable refusal at the registration gate above, before any
    # DB write. The egress seam's DNS-resolving verdict is deliberately NOT
    # repeated at ingest — registration must accept a public hostname whose DNS
    # has not propagated yet, and the seam re-resolves on every send anyway
    # (a resolution cached across that gap is the rebinding window it closes),
    # so the address policy is enforced where the socket is opened.

    # Context management and workflow step creation - create workflow step FIRST
    ctx_manager = get_context_manager()
    persistent_ctx = ctx_manager.create_context(tenant_id=tenant.tenant_id, principal_id=principal_id)

    # Create workflow step for tracking this operation
    # Pass model directly — ContextManager serializes at the DB boundary
    workflow_metadata: dict[str, Any] = {}
    if req.push_notification_config:
        # The VALUE's canonical dump, not the buyer's raw dict: what
        # context_manager reads back at delivery time is then gate-receipted
        # data, rehydrated through the same gate via from_stash.
        workflow_metadata["push_notification_config"] = registration.to_stash()

    step = ctx_manager.create_workflow_step(
        context_id=persistent_ctx.context_id,
        step_type="media_buy_creation",
        owner="system",
        status="in_progress",
        tool_name="create_media_buy",
        request_data=req,
        request_metadata=workflow_metadata,
    )

    # Register push notification config if provided (MCP/A2A protocol support).
    # URL was SSRF-checked above; persist via repository upsert (registration gate
    # + defense in depth).
    if req.push_notification_config:
        # Lazy: call-time import so tests that patch the UoW on the repositories package see their patched object (hoisting would bind the unpatched one at module load).
        from src.core.database.repositories import PushNotificationConfigUoW

        # No blank-URL guard here any more, and that is the TYPE doing the work
        # rather than an omission. Lane C2 needed one: _impl took a dict, the
        # registration gate is a documented no-op on blank/None URLs, so a value
        # existed for url="   " and an unguarded write persisted it. Now _impl
        # takes PushNotificationConfig, whose url is a pydantic AnyUrl —
        # PushNotificationConfig(url="   ") raises url_parsing, so a blank-url
        # config cannot be constructed, let alone arrive here. A whitespace URL
        # is refused at the wrapper, correctably, naming push_notification_config.url.
        # Log scheme+host+path only — never credentials / full auth blob.
        logger.info(
            "[MCP/A2A] Registering push notification config url=%s",
            webhook_url_for_log(registration.url),
        )
        # THE ROW IS IDENTIFIED BY ITS URL, not by anything the buyer names.
        # core/push-notification-config.json declares no ``id`` property, so a buyer
        # cannot name a row -- an earlier version dug one out of the raw wire payload,
        # which is processing a field the schema does not define.
        #
        # But identity still has to come from somewhere, or every re-registration
        # inserts and a buyer re-registering one webhook accumulates rows forever.
        # (tenant, principal, url) is the natural key and it is made of SPEC fields:
        # re-registering the same URL updates the same row, which is the behaviour
        # A2A re-registration needs, without honouring a non-spec id to get it.
        with PushNotificationConfigUoW(tenant.tenant_id) as pnc_uow:
            assert pnc_uow.push_notification_configs is not None
            _existing = pnc_uow.push_notification_configs.find_by_url(
                principal_id, str(registration.url), active_only=False
            )
            row_id = _existing.id if _existing is not None else f"pnc_{uuid.uuid4().hex[:16]}"
            _config, created = pnc_uow.push_notification_configs.upsert(
                registration,
                config_id=row_id,
                principal_id=principal_id,
            )
            logger.info(
                "[MCP/A2A] Push notification config %s: %s",
                "created" if created else "updated",
                row_id,
            )

    try:
        # Validate input parameters
        # 1. Budget validation (shared validator)
        total_budget = req.get_total_budget()
        # req.get_total_budget() sums EVERY package, so no single element is at
        # fault -- the pointer names the array (salesagent-rfxfu).
        budget_err = validate_budget_positive(total_budget, field=PACKAGES_FIELD)
        if budget_err:
            raise AdCPBudgetTooLowError(field=PACKAGES_FIELD)

        # 2. DateTime validation
        now = datetime.now(UTC)

        # Validate start_time
        if req.start_time is None:
            raise AdCPValidationError(field="start_time")

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
                raise AdCPValidationError(
                    details=ValidationDetails(received_type=type(raw_start_time).__name__),
                    field="start_time",
                )
            if computed_start_time.tzinfo is None:
                computed_start_time = computed_start_time.replace(tzinfo=UTC)

            if computed_start_time < now:
                # computed_start_time is the UNWRAPPED, tz-normalized value. Never put
                # req.start_time here: it is an adcp StartTiming RootModel, and str() of it
                # renders "root=datetime.datetime(...)" — the rendering defect the wire-safety
                # marker check grades. The unwrapped value renders as "2020-01-01 00:00:00+00:00".
                raise AdCPInvalidRequestError(
                    # BR-UC-002 grades `start_time` in details by name, so the key stays.
                    details=TimeWindowDetails(start_time=str(computed_start_time)),
                    field="start_time",
                )

        # Validate end_time
        if req.end_time is None:
            raise AdCPValidationError(field="end_time")

        # Ensure end_time is timezone-aware for comparison
        computed_end_time: datetime = req.end_time
        if computed_end_time.tzinfo is None:
            computed_end_time = computed_end_time.replace(tzinfo=UTC)

        if computed_end_time <= computed_start_time:
            # computed_* are the UNWRAPPED, tz-normalized values — see the start_time branch
            # above for why req.start_time must never be stringified into details.
            raise AdCPInvalidRequestError(
                details=TimeWindowDetails(
                    start_time=str(computed_start_time),
                    end_time=str(computed_end_time),
                ),
                field="end_time",
            )

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
            raise AdCPValidationError(field="packages")

        if req.packages:
            for pkg_index, package in enumerate(req.packages):
                # Check product_id field per AdCP spec
                if not package.product_id:
                    raise AdCPValidationError(field=package_field_path("product_id", pkg_index))

            # Check for duplicate product_ids across packages
            product_id_counts: dict[str, int] = {}
            for package in req.packages:
                if package.product_id:
                    product_id_counts[package.product_id] = product_id_counts.get(package.product_id, 0) + 1

            duplicate_products = [pid for pid, count in product_id_counts.items() if count > 1]
            if duplicate_products:
                raise AdCPValidationError(
                    details=ValidationDetails(duplicate_product_ids=sorted(duplicate_products)),
                )

        # 4. Currency-specific budget validation
        # Lazy: tests patch src.core.database.repositories.MediaBuyUoW; the call-time import binds the patched object (hoisting would bind the unpatched one at module load).
        from src.core.database.repositories import MediaBuyUoW

        # Get products first to determine currency from pricing options
        with MediaBuyUoW(tenant.tenant_id) as validation_uow:
            # FIXME(#1119): raw session usages below should migrate to repository methods
            assert validation_uow.session is not None
            session = validation_uow.session
            # Get products from database
            products_stmt = (
                select(ProductModel)
                .where(ProductModel.tenant_id == tenant.tenant_id, ProductModel.product_id.in_(product_ids))
                .options(selectinload(ProductModel.pricing_options))
            )
            products = session.scalars(products_stmt).all()

            # Build product lookup map
            product_map = {p.product_id: p for p in products}

            # Validate all requested product_ids exist
            missing_product_ids = set(product_ids) - set(product_map.keys())
            if missing_product_ids:
                # Gathered ACROSS every package (a set difference over all
                # product_ids), so the pointer names the array and details enumerate
                # which ids were missing (salesagent-rfxfu).
                raise AdCPProductNotFoundError(
                    details=ProductRefDetails(missing_product_ids=sorted(missing_product_ids)),
                    field=PACKAGES_FIELD,
                )

            # AdCP spec (core/targeting.json): "Sellers SHOULD return a validation
            # error if the product has property_targeting_allowed: false."
            # Lives here because product_map is in scope; the rest of targeting
            # validation runs further down outside this UoW block.
            if req.packages:
                property_targeting_violations = [
                    v
                    for package in req.packages
                    if package.product_id in product_map
                    and (
                        v := validate_property_targeting_allowed(
                            product_map[package.product_id], package.targeting_overlay
                        )
                    )
                ]
                raise_if_property_targeting_violations(property_targeting_violations)

            # Resolve legacy pricing_option_id values to actual product pricing_option_ids
            # This happens when using the legacy product_ids parameter (auto-converted to packages)
            if req.packages:
                for package in req.packages:
                    if package.pricing_option_id == "legacy_conversion" and package.product_id in product_map:
                        product = product_map[package.product_id]
                        # Use the first pricing option from the product
                        if product.pricing_options and len(product.pricing_options) > 0:
                            # Unwrap RootModel wrapper if present (adcp 2.14.0+ uses RootModel)
                            first_option = product.pricing_options[0]
                            first_option = getattr(first_option, "root", first_option)
                            package.pricing_option_id = first_option.pricing_option_id
                            logger.info(
                                f"Resolved legacy pricing_option_id for product {package.product_id}: {package.pricing_option_id}"
                            )

            # Get currency from product pricing options (per AdCP spec)
            request_currency: str | None = None

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

                    # The product's first pricing option supplies the currency. The branch
                    # that used to precede this one looked the option up by the package's
                    # legacy pricing_model, a field the pin does not declare and nothing
                    # produced; it went with the field.
                    if pricing_options:
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
                CurrencyLimit.tenant_id == tenant.tenant_id, CurrencyLimit.currency_code == request_currency
            )
            currency_limit = session.scalars(currency_stmt).first()

            # Check if tenant supports this currency
            if not currency_limit:
                # Currency support is a seller capability, not a malformed request:
                # emit UNSUPPORTED_FEATURE (matches the update path and UC-002 ext-d).
                raise AdCPCapabilityNotSupportedError(
                    details=CapabilityRefusalDetails(capability="currency", rejected_value=request_currency),
                )

            # Check if currency is supported by GAM network (if GAM is configured)
            # GAM only accepts: primary currency OR enabled secondary currencies
            adapter_config_stmt = select(AdapterConfig).where(AdapterConfig.tenant_id == tenant.tenant_id)
            adapter_config = session.scalars(adapter_config_stmt).first()
            if adapter_config and adapter_config.gam_network_currency:
                # Build list of supported currencies: primary + any secondary
                supported_currencies = {adapter_config.gam_network_currency}
                if adapter_config.gam_secondary_currencies:
                    supported_currencies.update(adapter_config.gam_secondary_currencies)

                if request_currency not in supported_currencies:
                    # Same seller-capability gap as above, scoped to the GAM network.
                    raise AdCPCapabilityNotSupportedError()

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
                            except AdCPSalesAgentError:
                                # Re-raise pricing validation errors as-is, preserving
                                # the typed AdCPSalesAgentError code/details/recovery hints rather
                                # than stripping to a string-only ValueError.
                                raise

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
                                if package_min_spend:
                                    raise_if_validation_failed(
                                        validate_min_package_budget(
                                            package_budget=package_budget,
                                            min_package_budget=package_min_spend,
                                            currency=package_currency,
                                            trailer="for products in this package",
                                        ),
                                        exc_type=AdCPBudgetTooLowError,
                                        requested_budget=package_budget,
                                        budget_limit=package_min_spend,
                                    )
                    else:
                        # Legacy mode: single total_budget for all products
                        applicable_min_spends = list(product_min_spends.values())
                        if applicable_min_spends:
                            required_min_spend = max(applicable_min_spends)
                            budget_decimal = Decimal(str(total_budget))

                            raise_if_validation_failed(
                                validate_min_package_budget(
                                    package_budget=budget_decimal,
                                    min_package_budget=required_min_spend,
                                    currency=request_currency,
                                    subject="Total",
                                    trailer="for the selected products",
                                ),
                                exc_type=AdCPBudgetTooLowError,
                                requested_budget=budget_decimal,
                                budget_limit=required_min_spend,
                            )

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
                        raise_if_validation_failed(
                            validate_max_daily_package_spend(
                                package_budget=package_budget,
                                flight_days=flight_days,
                                max_daily_spend=Decimal(str(currency_limit.max_daily_package_spend)),
                                currency=request_currency,
                                limit_label="maximum daily spend per package",
                                trailer=(
                                    "This protects against accidental large budgets "
                                    "and prevents GAM line item proliferation."
                                ),
                            ),
                            exc_type=AdCPBudgetExceededError,
                            requested_budget=package_budget,
                            budget_limit=Decimal(str(currency_limit.max_daily_package_spend)),
                        )
                else:
                    # Legacy mode: validate total budget
                    raise_if_validation_failed(
                        validate_max_daily_package_spend(
                            package_budget=Decimal(str(total_budget)),
                            flight_days=flight_days,
                            max_daily_spend=Decimal(str(currency_limit.max_daily_package_spend)),
                            currency=request_currency,
                            subject="Daily",
                            limit_label="maximum daily spend",
                            trailer="This protects against accidental large budgets.",
                        ),
                        exc_type=AdCPBudgetExceededError,
                        requested_budget=Decimal(str(total_budget)),
                        budget_limit=Decimal(str(currency_limit.max_daily_package_spend)),
                    )

        # Validate targeting doesn't use managed-only dimensions (targeting_overlay is at package level per AdCP spec)
        if req.packages:
            for pkg in req.packages:
                if pkg.targeting_overlay is not None:
                    violations = collect_targeting_violations(pkg.targeting_overlay)
                    if violations:
                        raise AdCPInvalidRequestError(
                            details=ValidationDetails(**violations),
                            field="targeting_overlay",
                        )

    except (AdCPSalesAgentError, ValueError, PermissionError) as e:
        # Audit-update then re-raise via the shared helper so this early-validation
        # exit threads the two-layer envelope into workflow_step.response_data the
        # same way the post-adapter failure exits do — push-notification subscribers
        # see the same wire shape the synchronous caller receives, and the audit
        # write is try/except-wrapped so a DB hiccup can't shadow the original error.
        # Typed AdCPSalesAgentError propagates to the transport boundary which translates to
        # the spec two-layer wire envelope; ValueError/PermissionError propagate so
        # the boundary wrappers translate them to AdCPValidationError /
        # AdCPAuthorizationError with correct wire codes (the prior
        # "return CreateMediaBuyResult(VALIDATION_ERROR)" path silently mis-tagged
        # PermissionError as VALIDATION_ERROR).
        ctx_manager.audit_workflow_step_failure_if_present(step, e)
        raise

    assert step is not None
    assert persistent_ctx is not None

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
                    identity=identity,
                    # The nested creative sync is built as a real SyncCreativesRequest, so
                    # it carries THIS request's account and context rather than a set of
                    # loose fields with no request behind them. Not the client key: it calls
                    # the creative-sync SERVICE, which does no idempotency.
                    account=req.account,
                    principal_id=principal_id,
                    tenant=tenant,
                )
                # Replace packages with updated versions (functional approach)
                req.packages = cast(list[AdcpPackageRequest], updated_packages)  # type: ignore[assignment]
                logger.info("[INLINE_CREATIVE_DEBUG] Updated req.packages with creative_ids")
                if uploaded_ids:
                    logger.info(f"Successfully uploaded creatives for {len(uploaded_ids)} packages: {uploaded_ids}")
            except AdCPSalesAgentError as e:
                # Update workflow step on failure (only if step exists)
                if step:
                    ctx_manager.update_workflow_step(step.step_id, status="failed", error_message=str(e))
                raise

        adapter = get_adapter(identity)

        # Check if manual approval is required
        # Use tenant.human_review_required as the authoritative source, with adapter setting as fallback.
        # NOTE: capabilities.py's resolve_manual_approval_signal() (adapter_helpers.py,
        # salesagent-y9ld) reads a SIMILAR but not identical signal (no default-True
        # fallback, honest-absence semantics for reporting) -- deliberately NOT reused
        # here: this is the live enforcement path (a pure dict read with zero DB calls
        # today), and resolve_manual_approval_signal()'s DB fallback would add an
        # unconditional query to this hot path for a stylistic DRY win. Tracked as a
        # follow-up (salesagent-3rhn) alongside the sibling media_buy_update.py gap.
        tenant_approval_required = tenant.human_review_required
        adapter_approval_required = adapter.manual_approval_required
        # Tenant setting takes precedence - if tenant requires approval, it's required
        manual_approval_required = tenant_approval_required or adapter_approval_required
        manual_approval_operations = adapter.manual_approval_operations

        # DEBUG: Log manual approval settings
        logger.info(
            f"[DEBUG] Manual approval check - tenant_approval_required: {tenant_approval_required}, "
            f"adapter_approval_required: {adapter_approval_required}, "
            f"final_required: {manual_approval_required}, "
            f"operations: {manual_approval_operations}, "
            f"adapter type: {adapter.__class__.__name__}"
        )

        # No tenant field declares auto-creation; the dict shim this replaced always
        # answered its default, so the tenant never disables it.
        auto_create_enabled = True
        product_auto_create = True  # Will be set correctly when we get products later

        if manual_approval_required and "create_media_buy" in manual_approval_operations:
            # PRE-VALIDATE: same creative validation the auto path runs, BEFORE any
            # media-buy state is persisted. Missing creative_ids, format-vs-product
            # mismatches, terminal states, and malformed reference creatives fail
            # CREATIVE_REJECTED here identically to auto-approval — the pending
            # path previously skipped missing ids (pending success) and emitted
            # VALIDATION_ERROR for format mismatch.
            if req.packages:
                _pre_validate_package_creatives(req.packages, tenant.tenant_id, principal_id, ctx_manager, step)
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
                        "slack_webhook_url": tenant.slack_webhook_url,
                        "slack_audit_webhook_url": tenant.slack_audit_webhook_url,
                    }
                }
                slack_notifier = get_slack_notifier(notifier_config)

                # Create notification details
                notification_details: dict[str, Any] = {
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
                    tenant_name=tenant.name,
                    tenant_id=tenant.tenant_id,
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
                with MediaBuyUoW(tenant.tenant_id) as pending_uow:
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
                        status=PersistedMediaBuyStatus.PENDING_APPROVAL,
                        order_name=f"{media_buy_id} - {start_time.strftime('%Y-%m-%d')}",
                        package_id_map=package_id_map,
                        by_alias=True,
                        account_id=identity.account.account_id,
                        created_at=datetime.now(UTC),
                    )
                    logger.info(f"✅ Created media buy {media_buy_id} with status=pending_approval")
            except IntegrityError as exc:  # structural-guard: integrity-narrowing - _resolve_idempotency_race_or_raise decides, and re-raises anything else
                return _resolve_idempotency_race_or_raise(
                    exc,
                    tenant.tenant_id,
                    idempotency_key=req.idempotency_key,
                    principal_id=principal.principal_id,
                    account_id=identity.account.account_id,
                    req=req,
                    media_buy_id=media_buy_id,
                )

            # Log to activity feed for manual approval case
            try:
                principal_name = principal.name if principal else principal_id
                duration_days = (end_time - start_time).days + 1
                activity_feed.log_media_buy(
                    tenant_id=tenant.tenant_id,
                    principal_name=principal_name,
                    media_buy_id=media_buy_id,
                    budget=float(total_budget),
                    duration_days=duration_days,
                    action="pending_approval",  # Different action to indicate awaiting approval
                )
            except Exception as e:
                logger.warning(f"Failed to log media buy pending approval to activity feed: {e}")

            # Log to audit log for manual approval case
            try:
                audit_logger = get_audit_logger("AdCP", tenant.tenant_id)
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
            with MediaBuyUoW(tenant.tenant_id) as pkg_uow:
                # FIXME(#1788): package creation should use repository methods
                assert pkg_uow.session is not None
                session = pkg_uow.session
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
                                    "budget": budget_value,
                                    "targeting_overlay": req_pkg.targeting_overlay,
                                    "creative_ids": _get_creative_ids(req_pkg),
                                    "format_ids": req_pkg.format_ids,
                                    "pricing_info": pricing_info_for_package,  # Store pricing info for UI display
                                    "impressions": getattr(
                                        req_pkg, "impressions", None
                                    ),  # Store impressions for display (legacy field)
                                }
                            )
                            break

                    # Extract pricing fields for dual-write
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
            ctx_manager.link_workflow_to_object(
                step_id=step.step_id,
                object_type="media_buy",
                object_id=media_buy_id,
                action="create",
                tenant_id=tenant.tenant_id,
            )
            logger.info(f"✅ Linked workflow step {step.step_id} to media buy")

            # Create creative assignments for manual approval flow
            # This must happen AFTER media packages are created so we have package_ids
            if req.packages:
                with MediaBuyUoW(tenant.tenant_id) as assign_uow:
                    assert assign_uow.session is not None
                    assert assign_uow.creatives is not None
                    assert assign_uow.assignments is not None
                    session = assign_uow.session
                    # Batch load all creatives upfront
                    all_creative_ids = []
                    for package in req.packages:
                        pkg_cids = _get_creative_ids(package)
                        if pkg_cids:
                            all_creative_ids.extend(pkg_cids)

                    creatives_map: dict[str, Any] = {}
                    if all_creative_ids:
                        # Principal-scoped: the assignment rows below are inserted under
                        # the requester's principal_id, so a cross-principal creative in
                        # the map would violate the composite FK (creative_id, tenant_id,
                        # principal_id) — it must resolve to "not found" here instead.
                        creatives_list = assign_uow.creatives.get_by_ids(all_creative_ids, principal_id)
                        creatives_map = {str(c.creative_id): c for c in creatives_list}
                        logger.info(f"[CREATIVE_ASSIGN_DEBUG] Loaded {len(creatives_map)} creatives from database")

                    # Creative existence and format-vs-product validation already ran
                    # via _pre_validate_package_creatives at the top of this branch
                    # (shared with the auto path) — before any state was persisted.

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
                                log_safe(
                                    f"[CREATIVE_ASSIGN_DEBUG] Creating assignments for package {pkg_id}, "
                                    f"creative_ids: {pkg_cids}"
                                )
                            )

                            for creative_id in pkg_cids:
                                creative = creatives_map.get(creative_id)
                                if not creative:
                                    logger.warning(
                                        log_safe(f"Creative {creative_id} not found in database, skipping assignment")
                                    )
                                    continue

                                # Create database assignment
                                assignment = assign_uow.assignments.create(
                                    media_buy_id=media_buy_id,
                                    package_id=pkg_id,
                                    creative_id=creative_id,
                                    principal_id=principal_id,
                                )
                                logger.info(
                                    log_safe(
                                        f"[CREATIVE_ASSIGN_DEBUG] Created assignment {assignment.assignment_id} "
                                        f"for creative {creative_id}"
                                    )
                                )

                            # UoW auto-commits on clean exit
                            logger.info(f"✅ Created creative assignments for package {pkg_id}")

            # Submitted task envelope (spec 3.1.1): media_buy_status/packages land on
            # the task's completion artifact, not this response — main's media_buy_status
            # addition to the old Success envelope is subsumed by the Submitted variant.
            return _submitted_approval_result(step, req, adapter)

        # Get products for the media buy to check product-level auto-creation settings
        # Lazy: tests patch src.core.tools.products.get_product_catalog; the call-time import binds the patched object.
        from src.core.tools.products import get_product_catalog

        catalog = get_product_catalog(tenant_id=tenant.tenant_id)
        product_ids = req.get_product_ids()
        products_in_buy = [p for p in catalog if p.product_id in product_ids]

        # Validate and auto-generate GAM implementation_config for each product if needed
        if adapter.__class__.__name__ == "GoogleAdManager":
            gam_validator = GAMProductConfigService()
            config_errors = []

            for schema_product in products_in_buy:
                # Auto-generate default config if missing
                if not schema_product.implementation_config:
                    logger.info(
                        f"Product '{schema_product.name}' ({schema_product.product_id}) is missing GAM configuration. "
                        f"Auto-generating defaults based on product type."
                    )
                    # Generate defaults based on product delivery type and formats.
                    # delivery_type is a plain DeliveryType enum; normalize to its
                    # value ('guaranteed'/'non_guaranteed') so generate_default_config's
                    # equality check selects the right config branch (str(enum) would
                    # yield 'DeliveryType.guaranteed' and silently mis-route — PR1399).
                    delivery_type_str = enum_value(schema_product.delivery_type) or "non_guaranteed"
                    # Extract format IDs as strings for config generation
                    formats_list: list[str] | None = None
                    if schema_product.format_ids:
                        formats_list = [fmt.id for fmt in schema_product.format_ids]
                    schema_product.implementation_config = gam_validator.generate_default_config(
                        delivery_type=delivery_type_str, formats=formats_list
                    )

                    # Persist the auto-generated config to database
                    with MediaBuyUoW(tenant.tenant_id) as gam_uow:
                        # FIXME(#1119): product update should use ProductRepository
                        assert gam_uow.session is not None
                        product_stmt = select(ModelProduct).filter_by(product_id=schema_product.product_id)
                        db_product = gam_uow.session.scalars(product_stmt).first()
                        if db_product:
                            db_product.implementation_config = schema_product.implementation_config
                            # UoW auto-commits on clean exit
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
                raise AdCPValidationError(details=ValidationDetails(reasons=config_errors))

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

            # Send Slack notification for configuration-based approval requirement
            try:
                # Get principal name for notification
                principal_name = principal.name if principal else principal_id

                # Build notifier config from tenant fields
                notifier_config = {
                    "features": {
                        "slack_webhook_url": tenant.slack_webhook_url,
                        "slack_audit_webhook_url": tenant.slack_audit_webhook_url,
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
                    tenant_name=tenant.name,
                    tenant_id=tenant.tenant_id,
                    success=True,
                )
                logger.info(f"📧 Sent {reason.lower()} approval notification to Slack")
            except Exception as e:
                logger.warning(f"⚠️ Failed to send configuration approval Slack notification: {e}")

            # Submitted task envelope (spec 3.1.1) — see note on the manual-approval
            # branch above; main's media_buy_status addition is likewise subsumed.
            return _submitted_approval_result(step, req, adapter)

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
        # 0-based for the JSON pointer; details keep the human 1-based position.
        for pkg_index, pkg in enumerate(req.packages):  # Iterate over request packages
            idx = pkg_index + 1
            # Find the product for this package (from schema catalog, not database model)
            # Package has product_id field per AdCP spec
            pkg_product_id = pkg.product_id

            if not pkg_product_id:
                raise AdCPValidationError(
                    details=ValidationDetails(package_index=idx),
                    field=package_field_path("product_id", pkg_index),
                )

            pkg_product: Product | None = None
            for p in products_in_buy:
                if p.product_id == pkg_product_id:
                    pkg_product = p
                    break

            if not pkg_product:
                # Defensive: the primary product-existence check above
                # (AdCPProductNotFoundError) catches missing products first, so this
                # per-package branch is suite-invisible — typed for guard parity.
                raise AdCPProductNotFoundError(
                    details=ProductRefDetails(package_index=idx, product_id=pkg_product_id),
                    field=package_field_path("product_id", pkg_index),
                )

            # Determine format_ids to use
            format_ids_to_use: list[FormatId] = []

            # Use format_ids from request package if provided
            matching_package = pkg  # The package we're iterating over

            # If found and has format_ids, validate and use those
            if matching_package and matching_package.format_ids:
                from src.core.format_resolver import (
                    format_display,
                    format_identity_or_none,
                    product_format_identities,
                )

                # Validate that requested formats are supported by product.
                # Identity is (canonical agent_url, id) per the pinned core/format-id.json,
                # asked of format_resolver. This branch used to additionally accept a
                # supported key with "/mcp" APPENDED to the requested URL — an unmandated
                # widening that made one host's MCP endpoint and its bare origin the same
                # agent; the path is part of the canonical form and stays part of it.
                product_format_keys = product_format_identities(pkg_product.format_ids)
                requested_format_keys = product_format_identities(matching_package.format_ids)

                unsupported_formats = [
                    format_display(key) for key in sorted(requested_format_keys - product_format_keys)
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
                        supported_formats_str = ", ".join(format_display(key) for key in sorted(product_format_keys))
                        error_msg = (
                            f"Product '{pkg_product.name}' ({pkg_product.product_id}) does not support requested format(s): "
                            f"{', '.join(unsupported_formats)}. Supported formats: {supported_formats_str}"
                        )
                    raise AdCPValidationError()

                # Merge dimensions from product's format_ids if request format_ids don't have them.
                # This handles the case where buyer specifies a format but not dimensions.
                # Keyed on the same federation identity the support check above compares on,
                # so a format that PASSED that check cannot then miss its own dimensions.
                product_format_dimensions: dict[FormatIdentity, tuple[int | None, int | None, float | None]]
                product_format_dimensions = {}
                for fmt in pkg_product.format_ids or []:
                    fmt_identity = format_identity_or_none(fmt)
                    if fmt_identity:
                        product_format_dimensions[fmt_identity] = (fmt.width, fmt.height, fmt.duration_ms)

                # Process request format_ids, merging dimensions from product if missing
                for req_fmt in matching_package.format_ids:
                    req_fmt_identity = format_identity_or_none(req_fmt)
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
                        product_dims = product_format_dimensions.get(req_fmt_identity) if req_fmt_identity else None
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
                    # No tenant field declares a creative agent URL; the dict shim this
                    # replaced always answered None, so the reference agent is the one.
                    default_agent_url = "https://creative.adcontextprotocol.org"
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

            delivery_type_str = enum_value(pkg_product.delivery_type)

            delivery_type_value: Literal["guaranteed", "non_guaranteed"] = cast(
                Literal["guaranteed", "non_guaranteed"], delivery_type_str
            )

            packages.append(
                MediaPackage(
                    package_id=package_id,
                    name=pkg_product.name,
                    delivery_type=delivery_type_value,
                    cpm=cpm,
                    impressions=int(float(total_budget) / cpm * 1000),
                    format_ids=cast(list[Any], format_ids_to_use),
                    targeting_overlay=cast(
                        "Targeting | None",
                        matching_package.targeting_overlay if matching_package else None,
                    ),
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

        # Create the media buy using the adapter (SYNCHRONOUS operation)
        # Defensive null check: ensure start_time and end_time are set
        if not req.start_time or not req.end_time:
            raise AdCPValidationError()

        # PRE-VALIDATE: Check all creatives have required fields BEFORE calling adapter
        # This prevents GAM order creation when creatives are invalid (all-or-nothing approach)
        _pre_validate_package_creatives(packages, tenant.tenant_id, principal_id, ctx_manager, step)

        # What the adapters read, projected off the buyer's request ONCE and handed to
        # both the pre-validation and the creation call — the same type the approval
        # replay builds from the row, so the two paths cannot give an adapter different
        # material.
        adapter_request = AdapterCreateRequest.from_buyer_request(req)

        # Pre-validate adapter-specific constraints (pricing models, budget limits)
        # This runs regardless of dry_run so adapter restrictions are always enforced.
        pre_creation_problems: list[ErrorProblem] = adapter.validate_media_buy_request(
            adapter_request, packages, start_time, end_time, package_pricing_info
        )
        if pre_creation_problems:
            logger.error(f"[PRE-VALIDATE] Adapter validation failed: {pre_creation_problems}")
            if step:
                ctx_manager.update_workflow_step(
                    step.step_id, status="failed", error_message="Adapter validation failed"
                )
            # Forwarded unchanged into `problems`, not `reasons` (salesagent-rys3u.4).
            # The adapter now returns classified facts rather than sentences, and
            # ValidationDetails.problems is already typed list[ErrorProblem] -- so the
            # type system, not a review, is what stops a string arriving here.
            raise AdCPValidationError(details=ValidationDetails(problems=pre_creation_problems))

        # Call adapter using shared creation logic
        # Note: start_time variable already resolved from 'asap' to actual datetime if needed
        # This uses the same function as manual approval to ensure consistency across adapters
        try:
            response = _execute_adapter_media_buy_creation(
                adapter_request, packages, start_time, end_time, package_pricing_info, identity
            )
        except Exception:
            raise

        # An adapter reports failure by RAISING an AdCPSalesAgentError, never by returning
        # one: a raise never reaches the save, so AdCP's "an error is never cached" holds
        # because the code cannot express caching one. The transports translate the raise
        # into the same two-layer envelope they build for every other tool's failures.
        for i, pkg_item in enumerate(response.packages):
            logger.info(f"[DEBUG] create_media_buy: Response package {i} = {pkg_item}")

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
        # Repository handles raw_request serialization at the DB boundary
        try:
            with MediaBuyUoW(tenant.tenant_id) as create_uow:
                assert create_uow.media_buys is not None
                created_row = create_uow.media_buys.create_from_request(
                    # The adapter has already returned by this point (`response` is
                    # its reply), so the seller HAS committed -- including when the
                    # resolved status is pending_creatives because the buyer has not
                    # supplied creatives yet. That is the auto-approval branch the v3.1
                    # sync-success scenario grades.
                    seller_committed=True,
                    media_buy_id=response.media_buy_id,
                    req=req,
                    principal_id=principal_id,
                    advertiser_name=principal.name,
                    budget=total_budget,
                    currency=request_currency,
                    start_time=start_time,
                    end_time=end_time,
                    status=media_buy_status,
                    campaign_objective=getattr(req, "campaign_objective", "") or "",
                    kpi_goal=getattr(req, "kpi_goal", "") or "",
                    account_id=identity.account.account_id,
                )
                # Read the two columns the REPOSITORY owns, inside the UoW while the
                # row is still attached. The response reports what was persisted; it
                # does not mint its own (see CreateMediaBuySuccess: both fields lost
                # their defaults precisely so this read is not optional).
                persisted_confirmed_at = created_row.confirmed_at
                persisted_revision = created_row.revision
                # UoW auto-commits on clean exit
        except IntegrityError as exc:  # structural-guard: integrity-narrowing - _resolve_idempotency_race_or_raise decides, and re-raises anything else
            return _resolve_idempotency_race_or_raise(
                exc,
                tenant.tenant_id,
                idempotency_key=req.idempotency_key,
                principal_id=principal_id,
                account_id=identity.account.account_id,
                req=req,
                media_buy_id=response.media_buy_id,
            )

        # Populate media_packages table for structured querying
        # This enables creative_assignments to work properly
        if req.packages or (response.packages and len(response.packages) > 0):
            with MediaBuyUoW(tenant.tenant_id) as auto_pkg_uow:
                # FIXME(#1788): package creation should use repository methods
                assert auto_pkg_uow.session is not None
                session = auto_pkg_uow.session
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
                        raise AdCPAdapterError()

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
                        "budget": getattr(resp_package, "budget", None),
                        "targeting_overlay": getattr(resp_package, "targeting_overlay", None),
                        "creative_ids": getattr(resp_package, "creative_ids", None),
                        "creative_assignments": getattr(resp_package, "creative_assignments", None),
                        "format_ids_to_provide": getattr(resp_package, "format_ids_to_provide", None),
                        "paused": paused,  # Store paused state (adcp 2.12.0)
                        "pricing_info": pricing_info_for_package,  # Store pricing info for UI display
                        "impressions": impressions,  # Store impressions for display
                    }

                    # Extract pricing fields for dual-write from adapter response
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

                # Persist adapter IDs to package_config.
                # platform_order_id is per-buy — always write to all packages; platform_line_item_id
                # is per-package and conditional on the adapter providing the mapping.
                platform_line_item_ids = response.platform_line_item_ids

                if response.media_buy_id:
                    assert auto_pkg_uow.media_buys is not None
                    _persist_adapter_package_ids(
                        auto_pkg_uow.media_buys,
                        media_buy_id=response.media_buy_id,
                        platform_order_id=str(response.media_buy_id),
                        platform_line_item_ids=platform_line_item_ids or None,
                        log_label="DEBUG",
                    )
                else:
                    logger.info("[DEBUG] Adapter returned no media_buy_id — skipping ID persistence")

        # Handle creative_ids in packages if provided (immediate association)
        if req.packages:
            with MediaBuyUoW(tenant.tenant_id) as creative_uow:
                assert creative_uow.session is not None
                assert creative_uow.creatives is not None
                assert creative_uow.assignments is not None
                session = creative_uow.session
                # Batch load all creatives upfront to avoid N+1 queries
                all_creative_ids = []
                for package in req.packages:
                    pkg_cids = _get_creative_ids(package)
                    if pkg_cids:
                        all_creative_ids.extend(pkg_cids)

                creatives_by_id: dict[str, Any] = {}
                if all_creative_ids:
                    # Principal-scoped: assignment rows below are inserted under the
                    # requester's principal_id (composite FK) — a cross-principal
                    # creative must resolve to "not found" here, never load.
                    creatives_list = creative_uow.creatives.get_by_ids(all_creative_ids, principal_id)
                    creatives_by_id = {str(c.creative_id): c for c in creatives_list}

                    # Validate all creative IDs exist (match update_media_buy behavior)
                    found_creative_ids = set(creatives_by_id.keys())
                    requested_creative_ids = set(all_creative_ids)
                    missing_ids = requested_creative_ids - found_creative_ids

                    if missing_ids:
                        error_msg = f"Creative IDs not found: {', '.join(sorted(missing_ids))}"
                        logger.error(error_msg)
                        ctx_manager.update_workflow_step(step.step_id, status="failed", error_message=error_msg)
                        # Same MUST as the pre-adapter gate above: uniform
                        # CREATIVE_NOT_FOUND for any creative_id not owned by the caller.
                        raise AdCPCreativeNotFoundError(
                            details=CreativeRefDetails(missing_creative_ids=sorted(missing_ids)),
                            field=PACKAGES_FIELD,
                        )

                    # Validate creative formats against product formats BEFORE creating assignments
                    # This ensures creatives match the product's supported formats
                    # Validation happens at assignment time (not sync time) because:
                    # - Creatives may be synced before being assigned to products
                    # - A creative may be valid for product A but not product B
                    # - Same creative can be reused across packages if formats align
                    # Lazy: tests patch src.core.helpers.validate_creative_format_against_product; the call-time import binds the patched object.
                    from src.core.helpers import validate_creative_format_against_product

                    for package in req.packages:
                        pkg_cids = _get_creative_ids(package)
                        if pkg_cids and package.product_id:
                            # Load product to check supported formats
                            product_format_check_stmt = select(ModelProduct).where(
                                ModelProduct.tenant_id == tenant.tenant_id,
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
                                            # A format outside the product's declared set is a
                                            # BUSINESS-RULE violation: adcp 3.1.1's error-code enum
                                            # codes that VALIDATION_ERROR, and reserves
                                            # CREATIVE_REJECTED for "Creative failed content policy
                                            # review". The creative is fine; the ASSIGNMENT is what
                                            # this product does not permit. Converged with the
                                            # sync_creatives and update paths, which raise the same
                                            # class for the identical condition.
                                            raise AdCPValidationError(
                                                details=ValidationDetails(
                                                    creative_id=creative_id, product_id=package.product_id
                                                ),
                                            )

                                        logger.info(
                                            log_safe(
                                                f"Creative {creative_id} format validated "
                                                f"against product {package.product_id}"
                                            )
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
                            raise AdCPAdapterError()

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
                                logger.error(
                                    log_safe(f"Creative {creative_id} not in map despite validation - this is a bug")
                                )
                                continue

                            # Get platform_creative_id from creative.data JSON
                            platform_creative_id = creative.data.get("platform_creative_id") if creative.data else None
                            if platform_creative_id:
                                # Already in GAM — add to association list
                                platform_creative_ids.append(platform_creative_id)
                            elif creative.status == "pending_review":
                                # Hold back until the creative is approved; the DB assignment below
                                # ensures approve_creative can find it for retroactive push
                                logger.info(
                                    log_safe(f"[AUTO-APPROVAL] Holding back creative {creative_id} (pending_review)")
                                )
                            else:
                                # Upload to GAM via shared asset helper
                                try:
                                    pkg_assignments: list[PackageAssignmentDict] = [
                                        {"package_id": response_package_id, "weight": 100}
                                    ]
                                    asset, build_err = _build_adapter_asset_from_creative(
                                        creative, pkg_assignments, tenant_id=tenant.tenant_id
                                    )
                                    if build_err:
                                        raise AdCPValidationError(
                                            details=ValidationDetails(reasons=[build_err]),
                                        )
                                    assert asset is not None

                                    upload_result = adapter.add_creative_assets(
                                        response.media_buy_id if response.media_buy_id else "",
                                        [asset],
                                        datetime.now(UTC),
                                    )
                                    logger.info(
                                        log_safe(
                                            f"Successfully uploaded creative {creative_id} to GAM: {upload_result}"
                                        )
                                    )

                                    if upload_result and len(upload_result) > 0:
                                        uploaded_status = upload_result[0]
                                        merged_data = _apply_creative_enrichment(creative, uploaded_status)
                                        if merged_data is not None:
                                            CreativeRepository(session, tenant.tenant_id).update_data(
                                                creative, merged_data
                                            )
                                        pcid = (creative.data or {}).get("platform_creative_id")
                                        if pcid:
                                            platform_creative_ids.append(pcid)
                                except AdCPSalesAgentError:
                                    raise
                                except Exception as upload_error:
                                    logger.error(
                                        log_safe(f"Failed to upload creative {creative_id} to GAM: {upload_error}")
                                    )
                                    raise AdCPAdapterError() from upload_error

                            # Create database assignment
                            creative_uow.assignments.create(
                                media_buy_id=response.media_buy_id,
                                package_id=response_package_id,
                                creative_id=creative_id,
                                principal_id=principal_id,
                            )

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
                                # FIXME(#1566): silent per-item failure — association failure
                                # is logged only; the response carries no signal that creatives
                                # were not attached. Allowlisted in
                                # test_architecture_no_silent_loop_failures.py.
                                logger.error(
                                    f"Failed to associate creatives with line item {platform_line_item_id}: {e}"
                                )
                        elif platform_creative_ids:
                            logger.warning(
                                f"Package {response_package_id} has {len(platform_creative_ids)} creatives but no platform_line_item_id from adapter. "
                                f"Creatives will need to be associated via sync_creatives."
                            )

        # Per AdCP 3.1.1 (media-buy/package-request.json) creatives live on each PackageRequest, not at
        # request level. Inline creative submission to the adapter happens via the
        # package-level inline creative flow (process_and_upload_package_creatives
        # earlier in this function and req.packages[].creatives).
        creative_statuses: dict[str, CreativeApprovalStatus] = {}

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
                raise AdCPAdapterError()

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

        # Create AdCP response with typed Package objects
        adcp_response = CreateMediaBuySuccess.sync_success(
            media_buy_id=response.media_buy_id,
            message=f"Media buy {response.media_buy_id} created successfully.",
            packages=response_packages,
            # Read from the row the repository just wrote, not minted here. A buy that
            # is not yet committed carries a NULL confirmed_at, and saying so is the
            # whole point: the previous default reported "now" for it.
            confirmed_at=persisted_confirmed_at,
            revision=persisted_revision,
            # AdCP 3.1 preferred status; mirrors deprecated `status`. Lifecycle on
            # the wire, from the same single source that drives valid_actions
            # (spec 3.1.1 create-media-buy-response.json;
            # pending_creatives_to_start.yaml step create_buy_no_creatives
            # grades media_buy_status == "pending_creatives" alongside
            # envelope status == "completed"). Partial GH #1326.
            media_buy_status=media_buy_status,
            valid_actions=valid_actions_for_status(media_buy_status),
            creative_deadline=response.creative_deadline,
            errors=property_list_unsupported_advisories(req.packages, adapter),
        )

        # Log activity
        # Activity logging imported at module level

        log_tool_activity(identity, "create_media_buy", request_start_time)

        # Also log specific media buy activity
        try:
            principal_name = principal.name

            # Calculate duration using new datetime fields (resolved from 'asap' if needed)
            duration_days = (end_time_val - start_time_val).days + 1

            activity_feed.log_media_buy(
                tenant_id=tenant.tenant_id,
                principal_name=principal_name,
                media_buy_id=response.media_buy_id,
                budget=float(total_budget),
                duration_days=duration_days,
                action="created",
            )
        except Exception as e:
            # Activity feed logging is non-critical, but we should log the failure
            logger.warning(f"Failed to log media buy creation to activity feed: {e}")

        modified_response = adcp_response

        # Link workflow step to media buy so _send_push_notifications can find the webhook URL.
        # This MUST happen before update_workflow_step() which triggers _send_push_notifications.
        ctx_manager.link_workflow_to_object(
            step_id=step.step_id,
            object_type="media_buy",
            object_id=response.media_buy_id,
            action="create",
            tenant_id=tenant.tenant_id,
        )

        # Mark workflow step as completed on success (triggers _send_push_notifications)
        ctx_manager.update_workflow_step(step.step_id, status="completed")

        # Send Slack notification for successful media buy creation
        try:
            principal_name = principal.name

            # Build notifier config from tenant fields
            notifier_config = {
                "features": {
                    "slack_webhook_url": tenant.slack_webhook_url,
                    "slack_audit_webhook_url": tenant.slack_audit_webhook_url,
                }
            }
            slack_notifier = get_slack_notifier(notifier_config)

            # Per AdCP 3.1.1, creatives live on each PackageRequest. Count creatives across
            # all request packages for the notification.
            notification_creatives_count = sum(len(pkg.creatives) for pkg in (req.packages or []) if pkg.creatives)
            success_details = {
                "total_budget": total_budget,
                "po_number": req.po_number,
                "start_time": start_time.isoformat(),  # Resolved from 'asap' if needed
                "end_time": end_time.isoformat(),
                "product_ids": req.get_product_ids(),
                "duration_days": (end_time_val - start_time_val).days + 1,
                "packages_count": len(response_packages) if response_packages else 0,
                "creatives_count": notification_creatives_count,
                "workflow_step_id": step.step_id,
            }

            slack_notifier.notify_media_buy_event(
                event_type="created",
                media_buy_id=response.media_buy_id,
                principal_name=principal_name,
                details=success_details,
                tenant_name=tenant.name,
                tenant_id=tenant.tenant_id,
                success=True,
            )

            logger.info(f"🎉 Sent success notification to Slack for media buy {response.media_buy_id}")
        except Exception as e:
            logger.warning(f"⚠️ Failed to send success Slack notification: {e}")

        # Log to audit logs for business activity feed
        audit_logger = get_audit_logger("AdCP", tenant.tenant_id)
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

        modified_response.status = AdcpTaskStatus.completed.value
        return modified_response

    except AdCPSalesAgentError as adcp_err:
        # Re-raise transport-agnostic errors (CREATIVE_UPLOAD_FAILED, etc.) without wrapping.
        # audit_workflow_step_failure_if_present threads the two-layer envelope into
        # response_data so push notification subscribers see the same wire shape
        # the synchronous caller receives, AND wraps in try/except so a DB hiccup
        # during audit can't shadow the original AdCPSalesAgentError on re-raise.
        ctx_manager.audit_workflow_step_failure_if_present(step, adcp_err)
        raise

    except Exception as e:
        # Untyped exception — same workflow audit treatment, plus Slack
        # notification + adapter audit log below before re-raising.
        ctx_manager.audit_workflow_step_failure_if_present(step, e)

        # Send Slack notification for failed media buy creation
        try:
            # Get principal name for notification
            principal_name = "Unknown"
            if principal:
                principal_name = principal.name

            # Build notifier config from tenant fields
            notifier_config = {
                "features": {
                    "slack_webhook_url": tenant.slack_webhook_url,
                    "slack_audit_webhook_url": tenant.slack_audit_webhook_url,
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
                tenant_name=tenant.name,
                tenant_id=tenant.tenant_id,
                success=False,
                error_message=str(e),
            )

            logger.error(f"❌ Sent failure notification to Slack: {str(e)}")
        except Exception as notify_error:
            logger.warning(f"⚠️ Failed to send failure Slack notification: {notify_error}")

        # Log to audit logs for failed operation
        try:
            audit_logger = get_audit_logger("AdCP", tenant.tenant_id)
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

        raise AdCPAdapterError()


# Unified update tools
