"""Creative create/update logic: DB persistence, agent validation, preview extraction."""

from __future__ import annotations

import logging
import time
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from adcp.types import BrandReference, CreativeAsset
from pydantic import BaseModel

from src.core.creative_agent_registry import GenerativeBuildResult
from src.core.exceptions import AdCPError, to_wire_error_code, wire_advisory
from src.core.format_resolver import find_format, is_agent_backed, is_generative
from src.core.helpers import _extract_format_info, _validate_creative_assets
from src.core.helpers.outbound_error_mapping import raise_mapped_outbound_error

# Format/FormatId come from src.core.schemas, not adcp.types: the local models
# subclass the library ones (Pattern #1), so annotating against the local types
# keeps the extension point in play and lets callers pass extended instances.
# Enforced by test_architecture_local_schema_imports.py.
from src.core.schemas import CreativeStatusEnum, Format, FormatId, SyncCreativeResult, canonical_agent_url
from src.core.security.outbound_http import OperatorEndpoint, OutboundError
from src.core.validation_helpers import run_async_in_sync_context

from ._assets import _build_creative_data, _extract_message_from_assets, _extract_url_from_assets

if TYPE_CHECKING:
    from src.core.database.repositories.creative import CreativeRepository

logger = logging.getLogger(__name__)


def _resolve_agent_format(all_formats: list[Format], creative_format: FormatId) -> tuple[Format, FormatId] | None:
    """Resolve a creative's format reference to ``(format_obj, agent identity)``.

    One home for the resolve step both the create and the update path run before
    dialling a creative agent (they previously carried byte-identical copies).
    The matching itself is ``format_resolver.find_format`` — the ONE answer to
    "same format?", which compares values rather than Python classes.

    Returns ``None`` when the reference does not resolve to an agent-backed
    format: the caller then skips agent validation entirely, since there is no
    agent to ask.

    The returned :class:`FormatId` is the canonical federation identity the agent
    calls are addressed with: one value, so the request cannot carry two
    spellings of the same agent_url (the registry renders every wire object from
    it — see ``creative_agent_registry._render_creative_manifest``).
    """
    format_obj = find_format(creative_format, all_formats)
    if format_obj is None or not is_agent_backed(format_obj):
        return None
    return format_obj, FormatId(agent_url=canonical_agent_url(format_obj.agent_url), id=creative_format.id)


def _generation_assets(creative: CreativeAsset) -> dict[str, Any]:
    """Validate the buyer's asset slot map and return it (``{}`` when absent).

    Buyer-input validation, deliberately called OUTSIDE the agent-dial ``try``:
    :func:`~src.core.helpers._validate_creative_assets` raises
    :class:`AdCPValidationError` (``VALIDATION_ERROR`` / ``correctable`` per
    ``enums/error-code.json``), and inside that ``try`` the failure would be
    reported as "creative agent unreachable … retry recommended" —
    ``transient`` — for an error only the buyer can fix, before any agent was
    even dialled.
    """
    if not creative.assets:
        return {}
    return _validate_creative_assets(creative.assets) or {}


def _apply_build_result(
    data: dict[str, Any],
    build_result: GenerativeBuildResult,
    *,
    user_provided_assets: Any,
    changes: list[str] | None = None,
) -> None:
    """Persist a generative build onto the creative's ``data`` dict.

    Shared by the create and update paths, which previously each carried their
    own copy of this block. ``changes`` is the update path's field-change log
    (``None`` on create, which reports every field as changed anyway).

    Buyer-provided assets and URLs always win: a generative build fills gaps, it
    does not overwrite what the buyer sent.
    """

    def _changed(field: str) -> None:
        if changes is not None:
            changes.append(field)

    # The full response is persisted as a dict — the one place a dict is
    # genuinely needed (a JSONType column), so the model_dump lives here rather
    # than at the adapter boundary where it would untype every caller.
    data["generative_build_result"] = build_result.model_dump(mode="json")
    data["generative_status"] = build_result.status
    data["generative_context_id"] = build_result.context_id
    _changed("generative_build_result")

    output = build_result.creative_output
    if output is None:
        return

    if output.assets and not user_provided_assets:
        data["assets"] = output.assets
        _changed("assets")
        logger.info("[sync_creatives] Using assets from generative output")
    elif user_provided_assets:
        logger.info("[sync_creatives] Preserving user-provided assets, not overwriting with generative output")

    if output.output_format is not None:
        data["output_format"] = output.output_format.model_dump(mode="json")
        _changed("output_format")

        if output.output_format.url:
            if not data.get("url"):
                data["url"] = output.output_format.url
                _changed("url")
                logger.info(f"[sync_creatives] Got URL from generative output: {data['url']}")
            else:
                logger.info("[sync_creatives] Preserving user-provided URL, not overwriting with generative output")

    logger.info(
        f"[sync_creatives] Generative creative built: "
        f"status={data.get('generative_status')}, "
        f"context_id={data.get('generative_context_id')}"
    )


def _apply_preview_result(
    data: dict[str, Any], preview_result: dict[str, Any], *, changes: list[str] | None = None
) -> None:
    """Persist a creative agent's preview response onto the creative's ``data``.

    Shared by the create and update paths (previously duplicated). Stores the
    full response for the UI (per AdCP PR #119) and back-fills only the fields
    the buyer did not provide — a preview never overwrites buyer input.
    """

    def _changed(field: str) -> None:
        if changes is not None:
            changes.append(field)

    data["preview_response"] = preview_result
    _changed("preview_response")

    renders = (preview_result["previews"][0] or {}).get("renders") or []
    if renders:
        first_render = renders[0]
        if first_render.get("preview_url") and not data.get("url"):
            data["url"] = first_render["preview_url"]
            _changed("url")
            logger.info(f"[sync_creatives] Got preview URL from creative agent: {data['url']}")
        elif data.get("url"):
            logger.info("[sync_creatives] Preserving user-provided URL from assets, not overwriting with preview URL")

        dimensions = first_render.get("dimensions", {})
        for dimension in ("width", "height", "duration"):
            if dimensions.get(dimension) and not data.get(dimension):
                data[dimension] = dimensions[dimension]
                _changed(dimension)

    logger.info(
        f"[sync_creatives] Preview data populated: "
        f"url={bool(data.get('url'))}, "
        f"width={data.get('width')}, "
        f"height={data.get('height')}, "
        f"variants={len(preview_result.get('previews', []))}"
    )


def _build_via_agent(
    registry: Any,
    agent_format: FormatId,
    message: str,
    *,
    assets: dict[str, Any],
    media_buy_brand: BrandReference | None,
    creative_id: str,
    action_label: str,
) -> GenerativeBuildResult | None:
    """Dial the creative agent's generative build for *agent_format*.

    One home for the call both paths make (``action_label`` names the operation
    in the log). The wire objects — the manifest, the request identity — are
    rendered by the registry from ``agent_format``; this layer passes domain
    values only.
    """
    logger.info(
        "[sync_creatives] Calling build_creative for %s of %s: format %s from agent %s, message_length=%d",
        action_label,
        creative_id,
        agent_format.id,
        agent_format.agent_url,
        len(message),
    )
    return cast(
        GenerativeBuildResult | None,
        run_async_in_sync_context(
            registry.build_creative(
                format_id=agent_format,
                message=message,
                brand=media_buy_brand,
                assets=assets,
            )
        ),
    )


def _preview_via_agent(
    registry: Any,
    agent_format: FormatId,
    *,
    assets: dict[str, Any],
    url: str | None,
    creative_id: str,
    action_label: str,
) -> dict[str, Any]:
    """Dial the creative agent's static preview for *agent_format*.

    Counterpart to :func:`_build_via_agent`: one home for the call both paths
    make, so a change to the agent contract is made once instead of in two
    byte-identical copies that must be edited in lockstep.
    """
    logger.info(
        "[sync_creatives] Calling preview_creative for validation (%s): %s format %s "
        "from agent %s, has_assets=%s, has_url=%s",
        action_label,
        creative_id,
        agent_format.id,
        agent_format.agent_url,
        bool(assets),
        bool(url),
    )
    return cast(
        dict[str, Any],
        run_async_in_sync_context(registry.preview_creative(format_id=agent_format, assets=assets, url=url)),
    )


def _failed_sync_result(
    creative_id: str,
    error_msg: str,
    *,
    code: str = "SERVICE_UNAVAILABLE",
    field: str | None = None,
) -> SyncCreativeResult:
    """Build a SyncCreativeResult for a failed creative sync operation.

    The CODE is the choice; the recovery follows from it. ``wire_advisory``
    derives the buyer-facing retry classification from the pinned enumMetadata,
    so a call site says what happened and the retry signal follows. Pass the
    condition-specific code: ``CONFIGURATION_ERROR`` for a seller-side
    misconfiguration (pinned terminal — the buyer must not retry),
    ``CREATIVE_NOT_FOUND`` for an assignment referencing an unknown creative_id
    (matching the strict-mode ``AdCPCreativeNotFoundError`` raise since
    287c93099), ``VALIDATION_ERROR`` for other buyer-correctable causes. The
    default ``SERVICE_UNAVAILABLE`` (pinned transient) covers a creative agent
    that is simply down.
    """
    return SyncCreativeResult(
        creative_id=creative_id,
        action="failed",
        errors=[wire_advisory(code, error_msg, field=field)],
        review_feedback=None,
        assigned_to=None,
        assignment_errors=None,
    )


def _failed_from_agent_error(
    creative_id: str, error: BaseException, *, action_label: str
) -> tuple[SyncCreativeResult, bool]:
    """Classify a creative-agent failure into a failed ``(result, needs_approval)``.

    Single home for the ladder shared by the update and create paths
    (``_update_existing_creative`` / ``_create_new_creative``), which previously
    each carried their own byte-identical copy of it:

    - :class:`OutboundError` → DELEGATED to ``raise_mapped_outbound_error``. The
      egress seam already classified the refusal (and knows the field it came
      from); re-describing it here would launder a correctable, buyer-fixable
      address error into the generic "retry recommended" message below. The
      mapper always raises, and the mapped ``AdCPError`` propagates to
      ``_sync.py``'s per-creative handler, which forwards its own code onto the
      per-item result.
    - any other :class:`AdCPError` → the error's OWN ``error_code``. The
      exception class owns its classification (``exceptions.py``: each typed
      subclass declares ``_default_error_code``), so this function must not
      restate it — e.g. ``AdCPConfigurationError`` already carries
      ``CONFIGURATION_ERROR``, and a hardcoded arm here would discard a raise
      site's override. The code goes through :func:`to_wire_error_code` so an
      internal-only code cannot leak into an advisory.
    - anything else → ``SERVICE_UNAVAILABLE``: a genuinely unknown failure
      (network error, agent down) is the spec-endorsed fallback.

    In every arm the recovery is DERIVED from the code by ``wire_advisory`` —
    the pair can no longer disagree, which is what let a ``correctable`` hint
    ride out beside a ``transient`` code.

    Args:
        creative_id: Creative the failure applies to (reported on the result).
        error: The caught exception.
        action_label: Names the operation in the ERROR log ("update", "create").

    Returns:
        ``(failed SyncCreativeResult, needs_approval=False)`` — the tuple shape
        both callers return.
    """
    if isinstance(error, OutboundError):
        raise_mapped_outbound_error(error, provenance=OperatorEndpoint("the creative agent"), logger=logger)

    if isinstance(error, AdCPError):
        code = to_wire_error_code(error.error_code)
        error_msg = str(error)
    else:
        code = "SERVICE_UNAVAILABLE"
        error_msg = (
            f"Creative agent unreachable or validation error: {str(error)}. "
            f"Retry recommended - creative agent may be temporarily unavailable."
        )
    logger.error(
        "[sync_creatives] %s - rejecting %s of creative %s", error_msg, action_label, creative_id, exc_info=True
    )
    return (_failed_sync_result(creative_id, error_msg, code=code), False)


def _update_existing_creative(
    creative: CreativeAsset,
    existing_creative: Any,
    creative_repo: CreativeRepository,
    format_value: Any,
    approval_mode: str,
    tenant: dict[str, Any],
    webhook_url: str | None,
    context: dict[str, Any] | BaseModel | None,
    all_formats: list[Any],
    registry: Any,
    principal_id: str,
    media_buy_brand: BrandReference | None = None,
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
        context: Application-level context per AdCP spec.
        all_formats: Pre-fetched creative formats from registry.
        registry: CreativeAgentRegistry instance.
        principal_id: Authenticated principal ID for AI review callbacks.

    Returns:
        Tuple of (SyncCreativeResult, needs_approval).
    """

    from typing import Literal

    # Update updated_at timestamp
    now = datetime.now(UTC)
    existing_creative.updated_at = now

    # Track changes for result
    changes: list[str] = []

    # Upsert mode: update provided fields
    if creative.name != existing_creative.name:
        name_value = creative.name
        if name_value is not None:
            existing_creative.name = str(name_value)
        changes.append("name")
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
        changes.append("format")

    # Determine creative status based on approval mode
    creative_format = creative.format_id
    needs_approval = False
    if creative_format:  # Only update approval status if format is provided
        if approval_mode == "auto-approve":
            existing_creative.status = CreativeStatusEnum.approved.value
            needs_approval = False
        elif approval_mode == "ai-powered":
            # Submit to background AI review (async)

            from src.admin.blueprints.creatives import (
                _ai_review_executor,
                _ai_review_lock,
                _ai_review_tasks,
            )

            # Set status to pending_review for AI review
            existing_creative.status = CreativeStatusEnum.pending_review.value
            needs_approval = True

            # Submit background task
            task_id = f"ai_review_{existing_creative.creative_id}_{uuid.uuid4().hex[:8]}"

            # Need to flush to ensure creative_id is available
            creative_repo.flush()

            # Import the async function
            from src.admin.blueprints.creatives import _ai_review_creative_async

            future = _ai_review_executor.submit(
                _ai_review_creative_async,
                creative_id=existing_creative.creative_id,
                tenant_id=tenant["tenant_id"],
                webhook_url=webhook_url,
                slack_webhook_url=tenant.get("slack_webhook_url"),
                principal_name=principal_id,
            )

            # Track the task
            with _ai_review_lock:
                _ai_review_tasks[task_id] = {
                    "future": future,
                    "creative_id": existing_creative.creative_id,
                    "created_at": time.time(),
                }

            logger.info(f"[sync_creatives] Submitted AI review for {existing_creative.creative_id} (task: {task_id})")
        else:  # require-human
            existing_creative.status = CreativeStatusEnum.pending_review.value
            needs_approval = True

    # Store creative properties in data field
    # AdCP 2.5: Full upsert semantics (replace all data, not merge)
    url = _extract_url_from_assets(creative)
    data = _build_creative_data(creative, url, context, media_buy_brand=media_buy_brand)

    # Carry forward stored brand when no new brand is provided (Change 5 preservation).
    # _build_creative_data only sets data["brand"] when media_buy_brand is not None.
    # Without this, update_data() replaces the entire stored dict and erases the brand.
    if media_buy_brand is None and existing_creative.data and existing_creative.data.get("brand"):
        data["brand"] = existing_creative.data["brand"]

    # ALWAYS validate updates with creative agent
    if creative_format:
        # Buyer-input validation runs BEFORE the agent-dial try (see _generation_assets):
        # a bad asset slot key is the buyer's to fix, not a transient agent failure.
        validated_assets = _generation_assets(creative)
        try:
            # Use pre-fetched formats (fetched outside transaction at function start).
            # This avoids async HTTP calls inside savepoint.
            resolved = _resolve_agent_format(all_formats, creative_format)

            if resolved is not None:
                format_obj, agent_format = resolved
                # A generative format is BUILT by the agent rather than previewed
                # from what the buyer supplied. ``is_generative`` is
                # format_resolver's declared-field read — not a getattr probe
                # against a field the model actually declares.
                generative = is_generative(format_obj)

                if generative:
                    # Refinement: only rebuild when the buyer sent new instructions.
                    message = _extract_message_from_assets(creative)
                    if message:
                        build_result = _build_via_agent(
                            registry,
                            agent_format,
                            message,
                            assets=validated_assets,
                            media_buy_brand=media_buy_brand,
                            creative_id=existing_creative.creative_id,
                            action_label="update",
                        )
                        if build_result:
                            _apply_build_result(
                                data,
                                build_result,
                                user_provided_assets=creative.assets,
                                changes=changes,
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
                    preview_result = _preview_via_agent(
                        registry,
                        agent_format,
                        assets=validated_assets,
                        url=data.get("url"),
                        creative_id=existing_creative.creative_id,
                        action_label="update",
                    )

                if preview_result and preview_result.get("previews"):
                    _apply_preview_result(data, preview_result, changes=changes)
            else:
                # The format reference did not resolve to a known agent format, so no
                # agent was asked. Acceptable only when the creative carries its own
                # media_url (a static creative needs no preview).
                has_media_url = bool(getattr(creative, "url", None) or data.get("url"))

                if has_media_url:
                    warning_msg = (
                        f"Preview generation skipped for {existing_creative.creative_id}: "
                        f"format {creative_format.id} did not resolve to a creative agent "
                        f"(static creative with media_url)"
                    )
                    logger.warning(f"[sync_creatives] {warning_msg}")
                    # Continue with update - preview is optional for static creatives
                else:
                    error_msg = (
                        f"Preview generation failed for {existing_creative.creative_id}: "
                        f"format {creative_format.id} did not resolve to a creative agent "
                        f"and no media_url was provided"
                    )
                    logger.error(f"[sync_creatives] {error_msg}")
                    return (_failed_sync_result(existing_creative.creative_id, error_msg), False)

        except Exception as agent_error:
            # Code + recovery classification (AdCPError→its own /
            # unknown→SERVICE_UNAVAILABLE+transient) lives in _failed_from_agent_error.
            return _failed_from_agent_error(existing_creative.creative_id, agent_error, action_label="update")

    # In full upsert, consider all fields as changed
    changes.extend(["url", "click_url", "width", "height", "duration"])

    creative_repo.update_data(existing_creative, data)

    # Record result for updated creative
    action: Literal["updated", "unchanged"] = "updated" if changes else "unchanged"

    return (
        SyncCreativeResult(
            creative_id=existing_creative.creative_id,
            action=action,
            internal_status=existing_creative.status,
            changes=changes,
            review_feedback=None,
        ),
        needs_approval,
    )


def _create_new_creative(
    creative: CreativeAsset,
    creative_repo: CreativeRepository,
    format_value: Any,
    approval_mode: str,
    tenant: dict[str, Any],
    webhook_url: str | None,
    context: dict[str, Any] | BaseModel | None,
    all_formats: list[Any],
    registry: Any,
    principal_id: str,
    media_buy_brand: BrandReference | None = None,
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
    data = _build_creative_data(creative, url, context, media_buy_brand=media_buy_brand)

    # Store user-provided assets for preservation check
    user_provided_assets = creative.assets

    # ALWAYS validate creatives with the creative agent (validation + preview generation)
    creative_format = creative.format_id
    if creative_format:
        # Buyer-input validation runs BEFORE the agent-dial try (see _generation_assets):
        # a bad asset slot key is the buyer's to fix, not a transient agent failure.
        validated_assets = _generation_assets(creative)
        try:
            # Use pre-fetched formats (fetched outside transaction at function start).
            # This avoids async HTTP calls inside savepoint.
            resolved = _resolve_agent_format(all_formats, creative_format)

            if resolved is not None:
                format_obj, agent_format = resolved
                # A generative format is BUILT by the agent rather than previewed
                # from what the buyer supplied. ``is_generative`` is
                # format_resolver's declared-field read — not a getattr probe
                # against a field the model actually declares.
                generative = is_generative(format_obj)

                if generative:
                    message = _extract_message_from_assets(creative)
                    if not message:
                        message = f"Create a creative for: {creative.name}"
                        logger.warning(
                            "[sync_creatives] No message found in assets/inputs, using creative name as fallback"
                        )

                    build_result = _build_via_agent(
                        registry,
                        agent_format,
                        message,
                        assets=validated_assets,
                        media_buy_brand=media_buy_brand,
                        creative_id=creative_id,
                        action_label="create",
                    )
                    if build_result:
                        _apply_build_result(data, build_result, user_provided_assets=user_provided_assets)

                    # Skip preview_creative call since we already have the output
                    preview_result = None
                else:
                    preview_result = _preview_via_agent(
                        registry,
                        agent_format,
                        assets=validated_assets,
                        url=data.get("url"),
                        creative_id=creative_id,
                        action_label="create",
                    )

                if preview_result and preview_result.get("previews"):
                    _apply_preview_result(data, preview_result)
                else:
                    # No renderable output: either the agent was asked for a preview
                    # and returned none, or a generative build produced no url.
                    # Acceptable only when the creative carries its own media_url.
                    has_media_url = bool(getattr(creative, "url", None) or data.get("url"))

                    if has_media_url:
                        warning_msg = (
                            f"Preview generation returned no previews for {creative_id} "
                            f"(static creative with media_url)"
                        )
                        logger.warning(f"[sync_creatives] {warning_msg}")
                        # Continue with creative creation - preview is optional for static creatives
                    else:
                        error_msg = (
                            f"Preview generation failed for {creative_id}: "
                            f"no previews returned and no media_url provided"
                        )
                        logger.error(f"[sync_creatives] {error_msg}")
                        return (_failed_sync_result(creative_id, error_msg), False)

        except Exception as agent_error:
            # Code + recovery classification (AdCPError→its own /
            # unknown→SERVICE_UNAVAILABLE+transient) lives in _failed_from_agent_error.
            return _failed_from_agent_error(creative_id, agent_error, action_label="create")

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

    # Update creative_id if it was generated (model attribute assignment).
    # adcp.types.CreativeAsset hides creative_id from mypy's static analysis
    # (RootModel proxy via __getattr__/__setattr__ — invisible to the type checker).
    if not creative.creative_id:
        creative.creative_id = db_creative.creative_id  # type: ignore[attr-defined]

    # Now apply approval mode logic
    if approval_mode == "auto-approve":
        db_creative.status = CreativeStatusEnum.approved.value
        needs_approval = False
    elif approval_mode == "ai-powered":
        # Submit to background AI review (async)

        from src.admin.blueprints.creatives import (
            _ai_review_executor,
            _ai_review_lock,
            _ai_review_tasks,
        )

        # Set status to pending_review for AI review
        db_creative.status = CreativeStatusEnum.pending_review.value
        needs_approval = True

        # Submit background task
        task_id = f"ai_review_{db_creative.creative_id}_{uuid.uuid4().hex[:8]}"

        # Import the async function
        from src.admin.blueprints.creatives import _ai_review_creative_async

        future = _ai_review_executor.submit(
            _ai_review_creative_async,
            creative_id=db_creative.creative_id,
            tenant_id=tenant["tenant_id"],
            webhook_url=webhook_url,
            slack_webhook_url=tenant.get("slack_webhook_url"),
            principal_name=principal_id,
        )

        # Track the task
        with _ai_review_lock:
            _ai_review_tasks[task_id] = {
                "future": future,
                "creative_id": db_creative.creative_id,
                "created_at": time.time(),
            }

        logger.info(
            f"[sync_creatives] Submitted AI review for new creative {db_creative.creative_id} (task: {task_id})"
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
