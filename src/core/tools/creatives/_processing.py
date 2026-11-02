"""Creative create/update logic: DB persistence, agent validation, preview extraction.

SDK 5.7 type:ignore tracking (adcontextprotocol/adcp-client-python#913):
- [attr-defined] on line ~769: CreativeAsset is a RootModel proxy; .creative_id
  assignment exists at runtime but mypy cannot see through __setattr__. Fixable
  when the SDK ships typed accessors or a shared unwrapper helper.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, cast

from adcp.types import CreativeAsset

from src.core.errors.details import AdapterFailureDetails, ConfigurationDetails, CreativeRejectionDetails
from src.core.exceptions import (
    AdCPConfigurationError,
    AdCPCreativeRejectedError,
    AdCPSalesAgentError,
    AdCPServiceUnavailableError,
)
from src.core.format_resolver import find_format, is_agent_backed, is_generative
from src.core.helpers import _extract_format_info, _validate_creative_assets
from src.core.helpers.outbound_error_mapping import raise_mapped_outbound_error
from src.core.schemas import CreativeStatusEnum, SyncCreativeResult
from src.core.schemas import Error as AdCPErrorDetail
from src.core.security.outbound_http import OperatorEndpoint, OutboundError
from src.core.tenant_context import TenantContext
from src.core.validation_helpers import run_async_in_sync_context

from ._assets import _build_creative_data, _extract_message_from_assets, _extract_url_from_assets, _generative_assets

if TYPE_CHECKING:
    from src.core.database.repositories.creative import CreativeRepository

logger = logging.getLogger(__name__)

#: Fields a full upsert rewrites on every update. They used to be reported as changed
#: unconditionally, which made ``action: unchanged`` unreachable: a resync of identical
#: data always listed all five. They are now compared against the row's prior values.
_UPSERT_FIELDS = ("url", "click_url", "width", "height", "duration")


@dataclass(frozen=True)
class PriorCreativeState:
    """The values an update is compared AGAINST: the persisted row, pre-update.

    Snapshotted before the row is mutated, so ``comparison_changes`` can report
    what an update actually changed. There is one source and one branch --
    ``dry_run`` no longer builds a parallel "previewed" state (#1721: it is a
    transaction-disposal decision at the UoW boundary), so the second
    constructor this class used to carry is gone with it.
    """

    name: str | None
    agent_url: str | None
    format: str | None
    format_parameters: dict | None
    #: The upsert-rewritten data fields as the row held them, so an update can report
    #: which of them it actually changed instead of listing all five every time.
    upsert_fields: dict[str, Any]

    @classmethod
    def from_row(cls, existing_creative) -> PriorCreativeState:
        row_data = existing_creative.data or {}
        return cls(
            name=existing_creative.name,
            agent_url=existing_creative.agent_url,
            format=existing_creative.format,
            format_parameters=existing_creative.format_parameters,
            upsert_fields={key: row_data.get(key) for key in _UPSERT_FIELDS},
        )


def comparison_changes(creative: CreativeAsset, prior: PriorCreativeState, format_value) -> list[str]:
    """The part of ``changes`` derived purely by comparison -- no mutation, no agent.

    Deliberately decoupled from the field assignments it used to be interleaved
    with, which is what makes an update's reported ``changes`` derivable from a
    before/after pair rather than from the mutation code. The ``name`` quirk is preserved exactly as the live path had it: a
    ``None`` incoming name that differs from the prior one still reports ``name``
    as changed even though the live branch assigns nothing (the assignment keeps its
    ``is not None`` guard at the call site).
    """
    changes: list[str] = []
    if creative.name != prior.name:
        changes.append("name")

    format_info = _extract_format_info(format_value)
    if (
        format_info["agent_url"] != prior.agent_url
        or format_info["format_id"] != prior.format
        or format_info["parameters"] != prior.format_parameters
    ):
        changes.append("format")
    return changes


def build_update_sync_result(
    creative_id: str,
    *,
    creative: CreativeAsset,
    prior: PriorCreativeState,
    format_value,
    data: Mapping[str, Any],
    agent_derived_changes: Sequence[str] = (),
    internal_status: str | None = None,
) -> SyncCreativeResult:
    """The ONE place an ``updated`` or ``unchanged`` sync result is built, for both branches.

    ``changes`` is ``[name?, format?] + agent-derived + the upsert fields whose value
    differs from the row's``, each field once. The last group used to be all five
    unconditionally, which made ``unchanged`` -- a member of the pinned
    enums/creative-action.json -- unreachable: a resync of identical data always
    reported five changed fields. Comparing against the row's prior values is what
    lets identical data answer ``unchanged`` and a real change name the field.

    ``internal_status`` is absent for an in-request duplicate, which has no row to
    read a status from. The field is ``exclude=True``, so it never reaches the
    wire and its absence cannot make a preview diverge from a live run.
    """
    changed_upsert_fields = [key for key in _UPSERT_FIELDS if data.get(key) != prior.upsert_fields.get(key)]
    # dict.fromkeys: each field once, first occurrence's position kept.
    changes = list(
        dict.fromkeys(
            [*comparison_changes(creative, prior, format_value), *agent_derived_changes, *changed_upsert_fields]
        )
    )

    action: Literal["updated", "unchanged"] = "updated" if changes else "unchanged"

    return SyncCreativeResult(
        creative_id=creative_id,
        action=action,
        internal_status=internal_status,
        changes=changes,
        review_feedback=None,
    )


def _failed_sync_result(creative_id: str, source: AdCPSalesAgentError) -> SyncCreativeResult:
    """Build a SyncCreativeResult for a failed creative sync operation.

    Takes the TYPED error and nothing else. The advisory carries the exception's CODE
    plus its structured specifics; the sentence, recovery and suggestion are resolved
    from ``CODE_TABLE`` by ``Error.of`` — the same single derivation the transport
    envelope uses — so a per-creative advisory and the request-level envelope cannot
    disagree about the same failure.

    Provenance-bearing text belongs on ``internal_detail`` at the RAISE site (server log
    only), never in ``details``: a diagnostic in ``details`` is on the buyer's wire, which
    is the forward ``adcp_error_for`` exists to prevent.
    """
    return SyncCreativeResult(
        creative_id=creative_id,
        action="failed",
        # structural-guard: advisory per-creative result in SyncCreativeResult.errors[]
        # from_exception, NOT a hand-rolled decomposition: reading error_code/field/details
        # off the exception separately re-implemented the conversion and skipped the one
        # place that renders a details CLASS to its wire dict, so a typed detail shape
        # reached Error(details=...) as a model and failed validation.
        errors=[AdCPErrorDetail.from_exception(source)],
        review_feedback=None,
        assigned_to=None,
        assignment_errors=None,
    )


def _defer_ai_review(
    creative_repo: CreativeRepository,
    *,
    creative_id: str,
    tenant: TenantContext,
    webhook_url: str | None,
    principal_id: str,
) -> None:
    """Hand the creative to the background AI reviewer, AFTER this transaction commits.

    The job opens its OWN AdminCreativeUoW, commits a review verdict, and then
    sends Slack and the push webhook -- none of it inside this transaction, so a
    rollback cannot reach any of it. Registering it on the unit of work instead
    of calling it here means the preview branch needs no gate: a preview rolls back,
    the queue is discarded, and nothing was submitted.

    It also fixes an ordering bug that had nothing to do with previews. This used
    to flush and submit inline, but flush() is not commit() and the job reads
    through its own session -- so on the create branch the row might not exist yet,
    and on the update branch the job read PRE-update state and committed a verdict
    over it. Deferring past the commit removes the race by construction.

    Captures scalars only. Holding the ORM row here would hand a detached
    instance to a thread after this session is gone.
    """
    from src.admin.blueprints.creatives import _ai_review_executor, _ai_review_lock, _ai_review_tasks

    def _submit() -> None:
        from src.admin.blueprints.creatives import _ai_review_creative

        task_id = f"ai_review_{creative_id}_{uuid.uuid4().hex[:8]}"
        future = _ai_review_executor.submit(
            _ai_review_creative,
            creative_id=creative_id,
            tenant_id=tenant.tenant_id,
            webhook_url=webhook_url,
            slack_webhook_url=tenant.slack_webhook_url,
            principal_name=principal_id,
        )
        with _ai_review_lock:
            _ai_review_tasks[task_id] = {
                "future": future,
                "creative_id": creative_id,
                "created_at": time.time(),
            }
        logger.info(f"[sync_creatives] Submitted AI review for {creative_id} (task: {task_id})")

    creative_repo.after_commit(_submit, label=f"ai_review:{creative_id}")


def _update_existing_creative(
    creative: CreativeAsset,
    existing_creative: Any,
    creative_repo: CreativeRepository,
    format_value: Any,
    approval_mode: str,
    tenant: TenantContext,
    webhook_url: str | None,
    all_formats: list[Any],
    registry: Any,
    principal_id: str,
) -> tuple[SyncCreativeResult, bool]:
    """Update an existing creative with upsert semantics (AdCP 2.5).

    Handles the full update path: field updates, approval mode logic,
    creative agent validation (generative and static), preview extraction,
    and data persistence.

    Args:
        creative: CreativeAsset model from the sync payload.
        existing_creative: Existing DBCreative model to update (mutated in-place).
        creative_repo: CreativeRepository for DB operations (flush, update_data).
        format_value: Validated FormatId from Creative schema.
        approval_mode: Tenant approval mode (auto-approve, ai-powered, require-human).
        tenant: Tenant dict with tenant_id, slack_webhook_url, etc.
        webhook_url: Push notification webhook URL for AI review callbacks.
        all_formats: Pre-fetched creative formats from registry.
        registry: CreativeAgentRegistry instance.
        principal_id: Authenticated principal ID for AI review callbacks.

    Returns:
        Tuple of (SyncCreativeResult, needs_approval).
    """
    # Update updated_at timestamp
    now = datetime.now(UTC)
    existing_creative.updated_at = now

    # Snapshot what this update is compared against BEFORE mutating the row, and
    # derive the comparison-based part of `changes` from it. The dry_run branch runs
    # the identical comparison against the state an earlier entry previewed, which
    # is the only way the two branches can agree — see build_update_sync_result.
    prior = PriorCreativeState.from_row(existing_creative)

    # `changes` here accumulates ONLY the agent-derived entries (generative build,
    # preview render). The comparison-based and always-changed parts are added by
    # the builder at the end, in the order the live path has always produced.
    changes: list[str] = []

    # Upsert mode: update provided fields. Assignment stays guarded on `is not
    # None`; the comparison that decides whether `name` counts as changed lives in
    # comparison_changes and deliberately does NOT share that guard.
    if creative.name != existing_creative.name:
        name_value = creative.name
        if name_value is not None:
            existing_creative.name = str(name_value)
    # Extract complete format info including parameters (AdCP 2.5)
    format_info = _extract_format_info(format_value)
    new_agent_url = format_info["agent_url"]
    new_format = format_info["format_id"]
    new_params = format_info["parameters"]
    if (
        new_agent_url != existing_creative.agent_url
        or new_format != existing_creative.format
        or new_params != existing_creative.format_parameters
    ):
        existing_creative.agent_url = new_agent_url
        existing_creative.format = new_format
        # Cast TypedDict to dict for SQLAlchemy column type
        existing_creative.format_parameters = cast(dict | None, new_params)

    # Determine creative status based on approval mode
    creative_format = creative.format_id
    needs_approval = False
    if creative_format:  # Only update approval status if format is provided
        if approval_mode == "auto-approve":
            existing_creative.status = CreativeStatusEnum.approved.value
            needs_approval = False
        elif approval_mode == "ai-powered":
            # Submit to background AI review (async)

            # Set status to pending_review for AI review
            existing_creative.status = CreativeStatusEnum.pending_review.value
            needs_approval = True

            _defer_ai_review(
                creative_repo,
                creative_id=existing_creative.creative_id,
                tenant=tenant,
                webhook_url=webhook_url,
                principal_id=principal_id,
            )
        else:  # require-human
            existing_creative.status = CreativeStatusEnum.pending_review.value
            needs_approval = True

    # Store creative properties in data field
    # AdCP 2.5: Full upsert semantics (replace all data, not merge)
    url = _extract_url_from_assets(creative)
    data = _build_creative_data(creative, url)

    # ALWAYS validate updates with creative agent
    if creative_format:
        try:
            # Use pre-fetched formats (fetched outside transaction at function start)
            # This avoids async HTTP calls inside savepoint.
            #
            # find_format, not a `==` loop: format references reach this point in
            # either of two classes and pydantic v2 equality is class-sensitive,
            # so a raw comparison silently matched NOTHING on A2A and demoted
            # every generative creative to a static one (salesagent-kyc89).
            format_obj = find_format(all_formats, creative_format)

            if format_obj and is_agent_backed(format_obj):
                if is_generative(format_obj):
                    # Generative creative update - rebuild using AI
                    logger.info(
                        f"[sync_creatives] Detected generative format update: {creative_format}, "
                        f"checking for Gemini API key"
                    )

                    # Get Gemini API key from config
                    from src.core.config import get_settings

                    gemini_api_key = get_settings().integrations.gemini_api_key

                    if not gemini_api_key:
                        error_msg = (
                            f"Cannot update generative creative {creative_format}: GEMINI_API_KEY not configured"
                        )
                        logger.error(f"[sync_creatives] {error_msg}")
                        raise AdCPConfigurationError()

                    # Extract message/brief from assets or inputs
                    message = _extract_message_from_assets(creative)

                    # Extract promoted_offerings from assets if available
                    promoted_offerings = None
                    if creative.assets:
                        for role, asset in creative.assets.items():
                            if role == "promoted_offerings":
                                promoted_offerings = asset
                                break

                    # Get existing context_id for refinement
                    existing_context_id = None
                    if existing_creative.data:
                        existing_context_id = existing_creative.data.get("generative_context_id")

                    # Use provided context_id or existing one
                    context_id = getattr(creative, "context_id", None) or existing_context_id

                    # Only call build_creative if we have a message (refinement)
                    if message:
                        logger.info(
                            f"[sync_creatives] Calling build_creative for update: "
                            f"{existing_creative.creative_id} format {creative_format} "
                            f"from agent {format_obj.agent_url}, "
                            f"message_length={len(message) if message else 0}, "
                            f"context_id={context_id}"
                        )

                        # OUT-OF-TRANSACTION EFFECT: a preview must not fire a request at a
                        # creative agent's endpoint (same rule accounts.py states for
                        # activation proofs). None here is already the no-result case the
                        # consumer below guards for, so no second result path appears.
                        build_result = creative_repo.outbound(
                            lambda: run_async_in_sync_context(
                                registry.build_creative(
                                    agent_url=format_obj.agent_url,
                                    format_id=creative_format,
                                    message=message,
                                    gemini_api_key=gemini_api_key,
                                    promoted_offerings=promoted_offerings,
                                    context_id=context_id,
                                    finalize=getattr(creative, "approved", False),
                                )
                            )
                        )

                        # Store build result in data
                        if build_result:
                            data["generative_build_result"] = build_result
                            data["generative_status"] = build_result.get("status", "draft")
                            data["generative_context_id"] = build_result.get("context_id")
                            changes.append("generative_build_result")

                            # Extract creative output if available
                            if build_result.get("creative_output"):
                                creative_output = build_result["creative_output"]

                                # Only use generative assets if user didn't provide their own
                                user_provided_assets = creative.assets
                                if creative_output.get("assets") and not user_provided_assets:
                                    data["assets"] = _generative_assets(creative_output["assets"])
                                    changes.append("assets")
                                    logger.info("[sync_creatives] Using assets from generative output (update)")
                                elif user_provided_assets:
                                    logger.info(
                                        "[sync_creatives] Preserving user-provided assets in update, "
                                        "not overwriting with generative output"
                                    )

                                if creative_output.get("output_format"):
                                    output_format = creative_output["output_format"]
                                    data["output_format"] = output_format
                                    changes.append("output_format")

                                    # Only use generative URL if user didn't provide one
                                    if isinstance(output_format, dict) and output_format.get("url"):
                                        if not data.get("url"):
                                            data["url"] = output_format["url"]
                                            changes.append("url")
                                            logger.info(
                                                f"[sync_creatives] Got URL from generative output (update): "
                                                f"{data['url']}"
                                            )
                                        else:
                                            logger.info(
                                                "[sync_creatives] Preserving user-provided URL in update, "
                                                "not overwriting with generative output"
                                            )

                            logger.info(
                                f"[sync_creatives] Generative creative updated: "
                                f"status={data.get('generative_status')}, "
                                f"context_id={data.get('generative_context_id')}"
                            )
                    else:
                        # No prompt → skip build, but preserve generative fields
                        # from existing data (data was rebuilt from scratch above)
                        if existing_creative.data:
                            for key in (
                                "generative_build_result",
                                "generative_status",
                                "generative_context_id",
                                "output_format",
                            ):
                                if key in existing_creative.data:
                                    data[key] = existing_creative.data[key]
                        logger.info("[sync_creatives] No message for generative update, keeping existing creative data")

                    # Skip preview_creative call since we already have the output
                    preview_result = None
                else:
                    # Static creative - use preview_creative
                    # Build creative manifest from available data
                    # Extract string ID from FormatId object if needed
                    format_id_str = creative_format.id
                    creative_manifest: dict[str, Any] = {
                        "creative_id": existing_creative.creative_id,
                        "name": creative.name or existing_creative.name,
                        "format_id": format_id_str,
                    }

                    # Add any provided asset data for validation
                    # Validate assets are in dict format (AdCP v2.4+)
                    if creative.assets:
                        validated_assets = _validate_creative_assets(creative.assets)
                        if validated_assets:
                            creative_manifest["assets"] = validated_assets
                    if data.get("url"):
                        creative_manifest["url"] = data.get("url")

                    # Call creative agent's preview_creative for validation + preview
                    # Extract string ID from FormatId object if needed
                    format_id_str = creative_format.id
                    logger.info(
                        f"[sync_creatives] Calling preview_creative for validation (update): "
                        f"{existing_creative.creative_id} format {format_id_str} "
                        f"from agent {format_obj.agent_url}, has_assets={bool(creative.assets)}, "
                        f"has_url={bool(data.get('url'))}"
                    )

                    # OUT-OF-TRANSACTION EFFECT: a preview must not fire a request at a
                    # creative agent's endpoint (same rule accounts.py states for
                    # activation proofs). None here is already the no-result case the
                    # consumer below guards for, so no second result path appears.
                    preview_result = creative_repo.outbound(
                        lambda: run_async_in_sync_context(
                            registry.preview_creative(
                                agent_url=format_obj.agent_url,
                                format_id=format_id_str,
                                creative_manifest=creative_manifest,
                            )
                        )
                    )

                # Extract preview data and store in data field
                if preview_result and preview_result.get("previews"):
                    # Store full preview response for UI (per AdCP PR #119)
                    # This preserves all variants and renders for UI display
                    data["preview_response"] = preview_result
                    changes.append("preview_response")

                    # Also extract primary preview URL for backward compatibility
                    first_preview = preview_result["previews"][0]
                    renders = first_preview.get("renders", [])
                    if renders:
                        first_render = renders[0]

                        # Store preview URL from render ONLY if we don't already have a URL from assets
                        # This preserves user-provided URLs in assets instead of overwriting with preview URLs
                        if first_render.get("preview_url") and not data.get("url"):
                            data["url"] = first_render["preview_url"]
                            changes.append("url")
                            logger.info(f"[sync_creatives] Got preview URL from creative agent: {data['url']}")
                        elif data.get("url"):
                            logger.info(
                                "[sync_creatives] Preserving user-provided URL from assets, "
                                "not overwriting with preview URL"
                            )

                        # Extract dimensions from dimensions object
                        # Only use preview dimensions if not already provided by user
                        dimensions = first_render.get("dimensions", {})
                        if dimensions.get("width") and not data.get("width"):
                            data["width"] = dimensions["width"]
                            changes.append("width")
                        if dimensions.get("height") and not data.get("height"):
                            data["height"] = dimensions["height"]
                            changes.append("height")
                        if dimensions.get("duration") and not data.get("duration"):
                            data["duration"] = dimensions["duration"]
                            changes.append("duration")

                logger.info(
                    f"[sync_creatives] Preview data populated for update: "
                    f"url={bool(data.get('url'))}, "
                    f"width={data.get('width')}, "
                    f"height={data.get('height')}, "
                    f"variants={len(preview_result.get('previews', []) if preview_result else [])}"
                )
            else:
                # Preview generation returned no previews
                # Only acceptable if creative has a media_url (direct URL to creative asset)
                has_media_url = bool(getattr(creative, "url", None) or data.get("url"))

                if has_media_url:
                    # Static creatives with media_url don't need previews
                    warning_msg = f"Preview generation returned no previews for {existing_creative.creative_id} (static creative with media_url)"
                    logger.warning(f"[sync_creatives] {warning_msg}")
                    # Continue with update - preview is optional for static creatives
                else:
                    # Creative agent should have generated previews but didn't
                    logger.error(
                        "[sync_creatives] Preview generation returned nothing for %s and no media_url",
                        existing_creative.creative_id,
                    )
                    return (
                        _failed_sync_result(
                            existing_creative.creative_id,
                            AdCPCreativeRejectedError(
                                field="media_url",
                                details=CreativeRejectionDetails(
                                    creative_id=existing_creative.creative_id, reasons=["no_previews"]
                                ),
                            ),
                        ),
                        False,
                    )

        except AdCPConfigurationError as config_error:
            # Server-side misconfiguration (e.g. GEMINI_API_KEY missing) is terminal
            # and admin-fixable — not a transient creative-agent outage. Surface it
            # honestly so the buyer does not retry a misconfiguration.
            logger.error(
                "[sync_creatives] configuration error for update of %s",
                existing_creative.creative_id,
                exc_info=True,
            )
            return (
                _failed_sync_result(
                    existing_creative.creative_id,
                    AdCPConfigurationError(
                        details=ConfigurationDetails(creative_id=existing_creative.creative_id),
                        internal_detail=config_error,
                    ),
                ),
                False,
            )
        except OutboundError as outbound_error:
            # A refused/undeliverable egress request is already correctly classified
            # by the seam — delegate rather than laundering it into the generic
            # transient branch below. This branch MUST precede the typed branch: both
            # OutboundError subclasses are also AdCPSalesAgentError subclasses
            # (OutboundRequestBlocked/AdCPBlockedUrlError,
            # OutboundDeliveryFailed/AdCPServiceUnavailableError), so a typed catch
            # first would swallow the provenance classification.
            # raise_mapped_outbound_error always raises; the mapped error propagates
            # out of this function to _sync.py's per-creative typed handler, which
            # builds the per-item result from the typed error itself.
            raise_mapped_outbound_error(
                outbound_error,
                provenance=OperatorEndpoint("the creative agent"),
                logger=logger,
            )
        except AdCPSalesAgentError as typed_error:
            # GENERALIZES the AdCPConfigurationError branch above. That branch was added so a
            # missing GEMINI_API_KEY would not read as a transient creative-agent outage;
            # the same is true of every other typed error the registry raises. A
            # rate limit degraded to SERVICE_UNAVAILABLE tells the buyer to retry
            # immediately, which is the one thing a 429 says not to do.
            logger.error(
                "[sync_creatives] typed %s for update of %s",
                typed_error.error_code,
                existing_creative.creative_id,
                exc_info=True,
            )
            return (_failed_sync_result(existing_creative.creative_id, typed_error), False)
        except Exception as validation_error:
            # Creative agent validation failed for update (network error, agent down, etc.)
            # Do NOT update the creative - it needs validation before acceptance
            logger.error(
                "[sync_creatives] creative agent unreachable or validation error for update of %s",
                existing_creative.creative_id,
                exc_info=True,
            )
            return (
                _failed_sync_result(
                    existing_creative.creative_id,
                    AdCPServiceUnavailableError(
                        details=AdapterFailureDetails(creative_id=existing_creative.creative_id),
                        internal_detail=validation_error,
                    ),
                ),
                False,
            )

    creative_repo.update_data(existing_creative, data)

    # Record result for updated creative. The builder adds the comparison-based
    # entries and the always-changed five around the agent-derived ones collected
    # above, preserving the order this function has always emitted.
    return (
        build_update_sync_result(
            existing_creative.creative_id,
            creative=creative,
            prior=prior,
            format_value=format_value,
            data=data,
            agent_derived_changes=changes,
            internal_status=existing_creative.status,
        ),
        needs_approval,
    )


def _create_new_creative(
    creative: CreativeAsset,
    creative_repo: CreativeRepository,
    format_value: Any,
    approval_mode: str,
    tenant: TenantContext,
    webhook_url: str | None,
    all_formats: list[Any],
    registry: Any,
    principal_id: str,
) -> tuple[SyncCreativeResult, bool]:
    """Create a new creative and persist it to the database (AdCP 2.5).

    Handles the full create path: URL extraction, data dict construction,
    creative agent validation (generative build or static preview),
    DB insertion, approval mode logic, and AI review submission.

    Mutates ``creative.creative_id`` in-place when the ID is server-generated.

    Returns:
        Tuple of (SyncCreativeResult, needs_approval).
    """

    # Extract creative_id for error reporting (must be defined before any validation)
    creative_id = creative.creative_id or "unknown"

    # Prepare data field with all creative properties
    url = _extract_url_from_assets(creative)
    data = _build_creative_data(creative, url)

    # Store user-provided assets for preservation check
    user_provided_assets = creative.assets

    # ALWAYS validate creatives with the creative agent (validation + preview generation)
    creative_format = creative.format_id
    if creative_format:
        try:
            # Use pre-fetched formats (fetched outside transaction at function start)
            # This avoids async HTTP calls inside savepoint.
            #
            # find_format, not a `==` loop: format references reach this point in
            # either of two classes and pydantic v2 equality is class-sensitive,
            # so a raw comparison silently matched NOTHING on A2A and demoted
            # every generative creative to a static one (salesagent-kyc89).
            format_obj = find_format(all_formats, creative_format)

            if format_obj and is_agent_backed(format_obj):
                if is_generative(format_obj):
                    # Generative creative - call build_creative
                    logger.info(
                        f"[sync_creatives] Detected generative format: {creative_format}, checking for Gemini API key"
                    )

                    # Get Gemini API key from config
                    from src.core.config import get_settings

                    gemini_api_key = get_settings().integrations.gemini_api_key

                    if not gemini_api_key:
                        error_msg = f"Cannot build generative creative {creative_format}: GEMINI_API_KEY not configured"
                        logger.error(f"[sync_creatives] {error_msg}")
                        raise AdCPConfigurationError()

                    # Extract message/brief from assets or inputs
                    message = _extract_message_from_assets(creative)

                    if not message:
                        message = f"Create a creative for: {creative.name}"
                        logger.warning(
                            "[sync_creatives] No message found in assets/inputs, using creative name as fallback"
                        )

                    # Extract promoted_offerings from assets if available
                    promoted_offerings = None
                    if creative.assets:
                        for role, asset in creative.assets.items():
                            if role == "promoted_offerings":
                                promoted_offerings = asset
                                break

                    # Call build_creative
                    # Extract string ID from FormatId object if needed
                    format_id_str = creative_format.id
                    logger.info(
                        f"[sync_creatives] Calling build_creative for generative format: "
                        f"{format_id_str} from agent {format_obj.agent_url}, "
                        f"message_length={len(message) if message else 0}"
                    )

                    # OUT-OF-TRANSACTION EFFECT: a preview must not fire a request at a
                    # creative agent's endpoint (same rule accounts.py states for
                    # activation proofs). None here is already the no-result case the
                    # consumer below guards for, so no second result path appears.
                    build_result = creative_repo.outbound(
                        lambda: run_async_in_sync_context(
                            registry.build_creative(
                                agent_url=format_obj.agent_url,
                                format_id=format_id_str,
                                message=message,
                                gemini_api_key=gemini_api_key,
                                promoted_offerings=promoted_offerings,
                                context_id=getattr(creative, "context_id", None),
                                finalize=getattr(creative, "approved", False),
                            )
                        )
                    )

                    # Store build result
                    if build_result:
                        data["generative_build_result"] = build_result
                        data["generative_status"] = build_result.get("status", "draft")
                        data["generative_context_id"] = build_result.get("context_id")

                        # Extract creative output
                        if build_result.get("creative_output"):
                            creative_output = build_result["creative_output"]

                            # Only use generative assets if user didn't provide their own
                            if creative_output.get("assets") and not user_provided_assets:
                                data["assets"] = _generative_assets(creative_output["assets"])
                                logger.info("[sync_creatives] Using assets from generative output")
                            elif user_provided_assets:
                                logger.info(
                                    "[sync_creatives] Preserving user-provided assets, "
                                    "not overwriting with generative output"
                                )

                            if creative_output.get("output_format"):
                                output_format = creative_output["output_format"]
                                data["output_format"] = output_format

                                # Only use generative URL if user didn't provide one
                                if isinstance(output_format, dict) and output_format.get("url"):
                                    if not data.get("url"):
                                        data["url"] = output_format["url"]
                                        logger.info(f"[sync_creatives] Got URL from generative output: {data['url']}")
                                    else:
                                        logger.info(
                                            "[sync_creatives] Preserving user-provided URL, "
                                            "not overwriting with generative output"
                                        )

                        logger.info(
                            f"[sync_creatives] Generative creative built: "
                            f"status={data.get('generative_status')}, "
                            f"context_id={data.get('generative_context_id')}"
                        )

                    # Skip preview_creative call since we already have the output
                    preview_result = None
                else:
                    # Static creative - use preview_creative
                    # Build creative manifest from available data
                    # Extract string ID from FormatId object if needed
                    format_id_str = creative_format.id
                    creative_manifest: dict[str, Any] = {
                        "creative_id": creative.creative_id or str(uuid.uuid4()),
                        "name": creative.name,
                        "format_id": format_id_str,
                    }

                    # Add any provided asset data for validation
                    # Validate assets are in dict format (AdCP v2.4+)
                    if creative.assets:
                        validated_assets = _validate_creative_assets(creative.assets)
                        if validated_assets:
                            creative_manifest["assets"] = validated_assets
                    if data.get("url"):
                        creative_manifest["url"] = data.get("url")

                    # Call creative agent's preview_creative for validation + preview
                    # Extract string ID from FormatId object if needed
                    format_id_str = creative_format.id
                    logger.info(
                        f"[sync_creatives] Calling preview_creative for validation: {format_id_str} "
                        f"from agent {format_obj.agent_url}, has_assets={bool(creative.assets)}, "
                        f"has_url={bool(data.get('url'))}"
                    )

                    # OUT-OF-TRANSACTION EFFECT: a preview must not fire a request at a
                    # creative agent's endpoint (same rule accounts.py states for
                    # activation proofs). None here is already the no-result case the
                    # consumer below guards for, so no second result path appears.
                    preview_result = creative_repo.outbound(
                        lambda: run_async_in_sync_context(
                            registry.preview_creative(
                                agent_url=format_obj.agent_url,
                                format_id=format_id_str,
                                creative_manifest=creative_manifest,
                            )
                        )
                    )

                # Extract preview data and store in data field
                if preview_result and preview_result.get("previews"):
                    # Store full preview response for UI (per AdCP PR #119)
                    # This preserves all variants and renders for UI display
                    data["preview_response"] = preview_result

                    # Also extract primary preview URL for backward compatibility
                    first_preview = preview_result["previews"][0]
                    renders = first_preview.get("renders", [])
                    if renders:
                        first_render = renders[0]

                        # Only use preview URL if user didn't provide one
                        if first_render.get("preview_url") and not data.get("url"):
                            data["url"] = first_render["preview_url"]
                            logger.info(f"[sync_creatives] Got preview URL from creative agent: {data['url']}")
                        elif data.get("url"):
                            logger.info(
                                "[sync_creatives] Preserving user-provided URL from assets, "
                                "not overwriting with preview URL"
                            )

                        # Only use preview dimensions if user didn't provide them
                        dimensions = first_render.get("dimensions", {})
                        if dimensions.get("width") and not data.get("width"):
                            data["width"] = dimensions["width"]
                        if dimensions.get("height") and not data.get("height"):
                            data["height"] = dimensions["height"]
                        if dimensions.get("duration") and not data.get("duration"):
                            data["duration"] = dimensions["duration"]

                    logger.info(
                        f"[sync_creatives] Preview data populated: "
                        f"url={bool(data.get('url'))}, "
                        f"width={data.get('width')}, "
                        f"height={data.get('height')}, "
                        f"variants={len(preview_result.get('previews', []))}"
                    )
                elif creative_repo.is_preview:
                    # The boundary SUPPRESSED the call (an outbound request is not
                    # undoable by the transaction's rollback), so a falsy result
                    # here means "we did not ask", NOT "the agent had none".
                    # Asked of the TRANSACTION, deliberately: testing
                    # `preview_result is None` would conflate those two, which is
                    # the confusion this branch exists to prevent.
                    # Failing the entry would report a fault that does not exist
                    # and would break the pinned dry_run contract: a preview
                    # "returns what would be created/updated/deleted"
                    # (sync-creatives-request.json#/properties/dry_run), not a
                    # SERVICE_UNAVAILABLE blaming the creative agent. The
                    # agent-derived fields are simply absent from the preview --
                    # the known residual divergence build_update_sync_result's
                    # docstring already records, not a failure.
                    logger.info(
                        f"[sync_creatives] dry_run: skipped preview generation for "
                        f"{creative_id}; the preview reports the create it would make."
                    )
                else:
                    # Preview generation returned no previews
                    # Only acceptable if creative has a media_url (direct URL to creative asset)
                    has_media_url = bool(getattr(creative, "url", None) or data.get("url"))

                    if has_media_url:
                        # Static creatives with media_url don't need previews
                        warning_msg = f"Preview generation returned no previews for {creative_id} (static creative with media_url)"
                        logger.warning(f"[sync_creatives] {warning_msg}")
                        # Continue with creative creation - preview is optional for static creatives
                    else:
                        # Creative agent should have generated previews but didn't
                        logger.error(
                            "[sync_creatives] Preview generation returned nothing for %s and no media_url",
                            creative_id,
                        )
                        return (
                            _failed_sync_result(
                                creative_id,
                                AdCPCreativeRejectedError(
                                    field="media_url",
                                    details=CreativeRejectionDetails(creative_id=creative_id, reasons=["no_previews"]),
                                ),
                            ),
                            False,
                        )

        except AdCPConfigurationError as config_error:
            # Server-side misconfiguration (e.g. GEMINI_API_KEY missing) is terminal
            # and admin-fixable — not a transient creative-agent outage. Surface it
            # honestly so the buyer does not retry a misconfiguration.
            logger.error("[sync_creatives] configuration error - rejecting creative %s", creative_id, exc_info=True)
            return (
                _failed_sync_result(
                    creative_id,
                    AdCPConfigurationError(
                        details=ConfigurationDetails(creative_id=creative_id),
                        internal_detail=config_error,
                    ),
                ),
                False,
            )
        except OutboundError as outbound_error:
            # A refused/undeliverable egress request is already correctly classified
            # by the seam — delegate rather than laundering it into the generic
            # transient branch below. This branch MUST precede the typed branch: both
            # OutboundError subclasses are also AdCPSalesAgentError subclasses
            # (OutboundRequestBlocked/AdCPBlockedUrlError,
            # OutboundDeliveryFailed/AdCPServiceUnavailableError), so a typed catch
            # first would swallow the provenance classification.
            # raise_mapped_outbound_error always raises; the mapped error propagates
            # out of this function to _sync.py's per-creative typed handler, which
            # builds the per-item result from the typed error itself.
            raise_mapped_outbound_error(
                outbound_error,
                provenance=OperatorEndpoint("the creative agent"),
                logger=logger,
            )
        except AdCPSalesAgentError as typed_error:
            # Same generalization as the update path above.
            logger.error(
                "[sync_creatives] typed %s - rejecting creative %s",
                typed_error.error_code,
                creative_id,
                exc_info=True,
            )
            return (_failed_sync_result(creative_id, typed_error), False)
        except Exception as validation_error:
            # Creative agent validation failed (network error, agent down, etc.)
            # Do NOT store the creative - it needs validation before acceptance
            logger.error(
                "[sync_creatives] creative agent unreachable or validation error - rejecting creative %s",
                creative_id,
                exc_info=True,
            )
            return (
                _failed_sync_result(
                    creative_id,
                    AdCPServiceUnavailableError(
                        details=AdapterFailureDetails(creative_id=creative_id),
                        internal_detail=validation_error,
                    ),
                ),
                False,
            )

    # Determine creative status based on approval mode

    # Create initial creative with pending_review status (will be updated based on approval mode)
    creative_status = CreativeStatusEnum.pending_review.value
    needs_approval = False

    # Extract complete format info including parameters (AdCP 2.5)
    # Use validated format_value (already auto-upgraded from string)
    format_info = _extract_format_info(format_value)

    db_creative = creative_repo.create(
        creative_id=creative.creative_id or None,
        name=creative.name,
        agent_url=format_info["agent_url"],
        format=format_info["format_id"],
        format_parameters=cast(dict | None, format_info["parameters"]),
        principal_id=principal_id,
        status=creative_status,
        data=data,
    )

    # Update creative_id if it was generated (i6k: model attribute assignment)
    # SDK 5.7: CreativeAsset is now a RootModel; __getattr__ proxies to .root
    if not creative.creative_id:
        creative.creative_id = db_creative.creative_id  # type: ignore[attr-defined]

    # Now apply approval mode logic
    if approval_mode == "auto-approve":
        db_creative.status = CreativeStatusEnum.approved.value
        needs_approval = False
    elif approval_mode == "ai-powered":
        # Submit to background AI review (async)

        # Set status to pending_review for AI review
        db_creative.status = CreativeStatusEnum.pending_review.value
        needs_approval = True

        _defer_ai_review(
            creative_repo,
            creative_id=db_creative.creative_id,
            tenant=tenant,
            webhook_url=webhook_url,
            principal_id=principal_id,
        )
    else:  # require-human
        db_creative.status = CreativeStatusEnum.pending_review.value
        needs_approval = True

    return (
        SyncCreativeResult(
            creative_id=db_creative.creative_id,
            action="created",
            internal_status=db_creative.status,
            review_feedback=None,
        ),
        needs_approval,
    )
