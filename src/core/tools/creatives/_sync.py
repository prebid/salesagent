"""Sync creatives orchestrator: main _sync_creatives_impl function."""

import logging
import time
from collections.abc import Sequence
from contextlib import ExitStack
from typing import Any

from adcp.types import CreativeAction
from adcp.types.legacy import LegacyCreativeAsset as CreativeAsset
from pydantic import BaseModel

from src.core.database.repositories.uow import CreativeUoW
from src.core.errors.details import ValidationDetails
from src.core.exceptions import AdCPSalesAgentError, adcp_error_for
from src.core.format_resolver import seller_format_catalogue
from src.core.helpers import enum_value, log_tool_activity
from src.core.resolved_identity import AccountIdentity, ResolvedIdentity
from src.core.schemas import SyncCreativeResult, SyncCreativesResponse
from src.core.schemas.creative import SyncCreativeAsset, SyncCreativesRequest
from src.core.tenant_context import TenantContext
from src.core.validation_helpers import format_validation_error
from src.core.webhook_validator import webhook_url_for_log
from src.core.webhooks.registration import accept_push_notification_config

from ._assignments import _process_assignments
from ._processing import _create_new_creative, _failed_sync_result, _update_existing_creative, incoming_identity_of
from ._validation import (
    _get_field,
    _validate_creative_input,
    check_provenance_policy,
    check_representation_selection,
    check_unassigned_manifest,
)
from ._workflow import _audit_log_sync, _create_sync_workflow_steps, _send_creative_notifications

logger = logging.getLogger(__name__)


def _with_creative(details: ValidationDetails | None, creative_id: str) -> ValidationDetails:
    """Attach the offending creative to a details block, or start one.

    Both per-creative failure paths need this, so it lives once rather than as
    two copies of a dict merge. ``model_copy`` rather than assignment because a
    details block is a value: the caller's instance is not mutated underneath it.
    """
    if details is None:
        return ValidationDetails(creative_id=creative_id)
    return details.model_copy(update={"creative_id": creative_id})


def _sync_creatives_impl(
    req: SyncCreativesRequest,
    identity: AccountIdentity,
) -> SyncCreativesResponse:
    """The sync_creatives CONTROLLER: resolve who is calling, then run the service.

    Thin by construction. Everything a transport must establish before the work can start
    lives here; the work itself is :func:`sync_creatives`, which takes an already-resolved
    caller and never asks who they are.

    That split is what lets another tool reuse creative sync. ``create_media_buy`` and
    ``update_media_buy`` upload a package's inline creatives, and they used to do it by
    calling this function -- one controller invoking another, which meant the nested call
    carried the outer request's ``idempotency_key`` into a function that had no business
    seeing it, and inherited an auth check that had already run. They call the SERVICE now.
    """
    return sync_creatives(req, identity=identity, principal_id=identity.principal.principal_id, tenant=identity.tenant)


def sync_creatives(
    req: SyncCreativesRequest,
    *,
    identity: ResolvedIdentity,
    principal_id: str,
    tenant: TenantContext,
    assigned_by_caller: bool = False,
    uow: CreativeUoW | None = None,
) -> SyncCreativesResponse:
    """Sync creative assets to the centralized creative library.

    THE SERVICE. Takes a resolved caller and does the work: no auth, no transport concerns,
    no idempotency. Callable from any controller, and from any other service that needs
    creatives uploaded -- which is the point.

    ``assigned_by_caller`` is True when the calling service assigns every creative in *req*
    to a package once the sync returns -- ``create_media_buy``'s inline creatives, which are
    synced before the packages they belong to exist, so ``req.assignments`` cannot name them.
    Such a creative is graded against the declaration its package's product selects at that
    service's own gate, and is not held to its kind here: a product may replace the kind's
    slot list (core/product-format-declaration.json), and "assets is keyed by asset_group_id
    from the matched declaration's params.slots[]" (creative-manifests.mdx § Basic structure).

    ``uow`` is the caller's open transaction, joined (``BaseUoW.join``) rather than a unit
    of this service's own: the creatives, their assignments and the approval steps are
    written into it and commit or roll back with the caller's other writes, and the audit
    records wait for that commit. ``update_media_buy``'s inline creatives pass it, because
    update_media_buy.mdx § Compatibility transaction boundary makes the update "one atomic
    mutation" and says "No package, assignment, library creative, or MediaBuy field from
    the rejected update may change". A joined sync is never a preview: the caller's
    transaction decides whether anything persists.
    """

    # ``creatives`` is aliased because it is NARROWED below by the creative_ids filter.
    # Everything else is read as ``req.<field>`` at its use site -- no alias, so the request
    # stays the one carrier.
    creatives: Sequence[CreativeAsset | BaseModel | dict[str, Any]] = req.creatives
    # bool() here narrows the ANNOTATION, it does not supply the default. The model declares
    # ``bool | None`` -- wider than the pinned {"type": "boolean"} -- so an explicit null is
    # still representable and two callees below want a real bool.
    dry_run = bool(req.dry_run)
    joined = uow is not None
    assert not (joined and dry_run), "a joined sync commits with its caller and cannot be a preview"
    validation_mode = enum_value(req.validation_mode)

    from pydantic import ValidationError

    # Filter creatives by creative_ids if provided -- scoped updates to specific
    # creatives without affecting others.
    if req.creative_ids:
        creative_ids_set = set(req.creative_ids)
        creatives = [c for c in creatives if _get_field(c, "creative_id") in creative_ids_set]
        logger.info(f"[sync_creatives] Filtered to {len(creatives)} creatives by creative_ids filter")

    start_time = time.time()

    # Registration SSRF gate on the buyer-supplied webhook URL, taken HERE: before
    # any DB / workflow write stashes the URL, and before the per-creative loop,
    # whose per-item `try` would turn this correctable VALIDATION_ERROR into a
    # per-item transient failure and tell the buyer to retry a URL that will never
    # be allowed. The AI-review callback fires from a background worker, so ingest
    # is the only gate with a request left to refuse into.
    #
    # Deliberately the no-DNS registration gate (gh-#1697), NOT the outbound seam's
    # validate_url (gh-#1589): validate_url always resolves, so at registration it
    # would reject a buyer whose hostname has not yet propagated. The seam stays the
    # SEND-time gate and re-checks with DNS when the callback is actually dialed —
    # so no second address check belongs on this path.
    webhook_url = None
    if req.push_notification_config:
        registration = accept_push_notification_config(
            req.push_notification_config,
            field_prefix="push_notification_config",
        )
        webhook_url = registration.url
        if webhook_url is not None and str(webhook_url).strip():
            # Log scheme+host+path only — never credentials / full auth blob.
            logger.info(
                "[sync_creatives] Push notification webhook URL: %s",
                webhook_url_for_log(str(webhook_url)),
            )

    # Track actions per creative for AdCP-compliant response

    results: list[SyncCreativeResult] = []
    created_count = 0
    updated_count = 0
    unchanged_count = 0
    failed_count = 0
    deleted_count = 0

    # Legacy tracking (still used internally)
    synced_creatives = []
    failed_creatives: list[dict[str, Any]] = []

    # Track creatives requiring approval for workflow creation
    creatives_needing_approval = []

    # The creatives this request assigns: each of those is graded against the declaration its
    # package's product selects, and every other canonical one against its kind.
    assigned_creative_ids = {assignment.creative_id for assignment in req.assignments or []}

    # Get tenant creative approval settings
    # approval_mode: "auto-approve", "require-human", "ai-powered"
    approval_mode = tenant.approval_mode
    logger.info(f"[sync_creatives] Approval mode: {approval_mode} (from tenant: {tenant.tenant_id})")

    # Fetch creative formats ONCE before processing loop (outside any transaction)
    # This avoids async HTTP calls inside database savepoints which cause transaction errors.
    # The seller's WHOLE catalogue -- agents and ad-server adapter alike (seller_format_catalogue).
    from src.core.creative_agent_registry import get_creative_agent_registry

    registry = get_creative_agent_registry()
    all_formats = seller_format_catalogue(tenant.tenant_id)

    # ONE write path for both branches: dry_run rolls this transaction back on clean
    # exit instead of committing it (BaseUoW), so preview and live run identical
    # resolve/validate/write code and a preview's reads see its own flushed rows
    # (sync-creatives-request.json#/properties/dry_run).
    #
    # The stack, rather than a plain `with`, is what keeps the assignment stage a
    # SINGLE call site below: live closes this transaction before that call (as
    # the implicit block exit used to), dry keeps it open and hands it over so
    # both stages share the one rolled-back transaction. A second invocation
    # under an `if dry_run:` would re-fork the very seam this collapses.
    with ExitStack() as stack:
        if uow is None:
            uow = stack.enter_context(CreativeUoW(tenant.tenant_id, dry_run=dry_run))
        assert uow.creatives is not None
        creative_repo = uow.creatives

        # sync-creatives-response.json (SyncCreativesSuccess.sandbox): "When true, this
        # response contains simulated data from sandbox mode"; core/account.json: a sandbox
        # account is one with "no real platform calls, no real spend". The account the
        # request names (required by the pin) is what says so, and the resolver already
        # loaded it onto the identity, so it is read off ``identity.account`` like the
        # tenant and principal -- no second select. Set only when true and omitted
        # otherwise: the error shape forbids the field, and a production account's
        # response simply has none.
        sandbox = identity.sandbox_echo

        # Check if any product in this tenant requires AI provenance metadata
        provenance_policies = creative_repo.get_provenance_policies()
        tenant_requires_provenance = len(provenance_policies) > 0
        if tenant_requires_provenance:
            logger.info(
                f"[sync_creatives] Tenant {tenant.tenant_id} has "
                f"{len(provenance_policies)} product(s) requiring AI provenance"
            )

        # Process each creative with proper transaction isolation
        for creative_index, raw_creative in enumerate(creatives):
            try:
                # Normalize to CreativeAsset model (handles dicts from A2A raw, BaseModel subclasses)
                if isinstance(raw_creative, CreativeAsset):
                    creative = raw_creative
                elif isinstance(raw_creative, dict):
                    # Default required fields for raw dicts missing them
                    creative_data = raw_creative.copy()
                    creative_data.setdefault("assets", {})
                    creative = CreativeAsset(**creative_data)
                else:
                    creative = CreativeAsset.model_validate(raw_creative, from_attributes=True)

                # Validate the creative against schema and business rules
                try:
                    validated_creative = _validate_creative_input(creative, registry, principal_id, creative_index)
                    format_value = validated_creative.format

                except (ValidationError, ValueError) as validation_error:
                    # Creative failed validation - add to failed list
                    creative_id = creative.creative_id or "unknown"
                    # Format ValidationError nicely for clients, pass through ValueError as-is
                    if isinstance(validation_error, ValidationError):
                        error_msg = format_validation_error(validation_error, label=f"creative {creative_id}")
                    else:
                        error_msg = str(validation_error)
                    failed_creatives.append({"creative_id": creative_id, "error": error_msg})
                    failed_count += 1
                    # adcp_error_for is the ONE type->code mapping: reusing it here
                    # keeps the per-creative advisory identical to what the request-level
                    # boundary would have produced, and it already derives field + details
                    # from the pydantic error. The raw text rides internal_detail (server
                    # log only) instead of details, so no arbitrary exception text reaches
                    # the buyer.
                    typed = adcp_error_for(validation_error, document=raw_creative)
                    typed.internal_detail = validation_error
                    # A DECLARED field, not a dict poked onto a built error. The
                    # subject is what `creative_id` names, and ValidationDetails
                    # carries it (inherited from EntityRefDetails). The pydantic
                    # field-level detail now travels in issues[], which
                    # adcp_error_for already populated.
                    typed.details = _with_creative(typed.details, creative_id)
                    results.append(_failed_sync_result(creative_id, typed))
                    continue  # Skip to next creative

                # A canonical manifest no assignment selects a declaration for is held to its
                # kind; an assigned one -- by this request, or by the calling service right
                # after it -- is graded against the product's declaration at that assignment's
                # gate instead (creative_satisfies).
                if not assigned_by_caller and validated_creative.creative_id not in assigned_creative_ids:
                    check_unassigned_manifest(
                        creative, index=creative_index, creative_id=validated_creative.creative_id
                    )

                # The product's provenance policy (core/creative-policy.json, EU AI Act
                # Article 50): a creative that does not meet it is a per-item PROVENANCE_*
                # failure, caught below with every other correctable typed error. The first
                # policy is the tenant-wide one.
                if tenant_requires_provenance:
                    # The REQUEST item is inspected -- that is where the pin declares
                    # ``provenance`` -- with the resolved id for the refusal to name.
                    check_provenance_policy(
                        creative,
                        provenance_policies[0],
                        creative_id=validated_creative.creative_id,
                    )

                # A selected output must be the output its lineage describes, before anything
                # is written (sync-creatives-request.json ``representation_set_projection``).
                check_representation_selection(
                    creative,
                    revision_id=raw_creative.revision_id if isinstance(raw_creative, SyncCreativeAsset) else None,
                    index=creative_index,
                    creative_id=validated_creative.creative_id,
                )

                # Savepoint per creative: isolates this row's writes AND the effects
                # queued while processing it, so a creative that fails takes its
                # queued AI-review submit down with it (#1970).
                with creative_repo.savepoint():
                    # Check if creative already exists (always check for upsert/patch behavior)
                    # SECURITY: Must filter by principal_id to prevent cross-principal modification
                    existing_creative = None
                    if creative.creative_id:
                        existing_creative = creative_repo.get_by_id(creative.creative_id, principal_id)

                    # The identity this item names, read ONCE: it is what the row stores and
                    # what an operator's review record names (``label``), whichever arm the
                    # buyer used. The record used to name ``creative.format_id``, so a
                    # creative identified by format_kind reached review as "format: None".
                    incoming_identity = incoming_identity_of(creative, all_formats)

                    if existing_creative:
                        update_result, needs_approval = _update_existing_creative(
                            creative=creative,
                            existing_creative=existing_creative,
                            creative_repo=creative_repo,
                            format_value=format_value,
                            tenant=tenant,
                            webhook_url=webhook_url,
                            all_formats=all_formats,
                            registry=registry,
                            principal_id=principal_id,
                            incoming_identity=incoming_identity,
                        )

                        # Handle failed updates
                        if update_result.action == "failed":
                            failed_creatives.append(
                                {
                                    "creative_id": existing_creative.creative_id,
                                    "error": update_result.errors[0] if update_result.errors else "Unknown error",
                                    "format": incoming_identity.label,
                                }
                            )
                            failed_count += 1
                            results.append(update_result)
                            continue

                        # Track counts
                        if update_result.action == "updated":
                            updated_count += 1
                        else:
                            unchanged_count += 1

                        # Track creatives needing approval for workflow creation
                        if needs_approval:
                            creative_info: dict[str, Any] = {
                                "creative_id": existing_creative.creative_id,
                                "format": incoming_identity.label,
                                "name": creative.name,
                                "status": existing_creative.status,
                            }
                            # Include AI review reason if available
                            if (
                                approval_mode == "ai-powered"
                                and existing_creative.data
                                and existing_creative.data.get("ai_review")
                            ):
                                creative_info["ai_review_reason"] = existing_creative.data["ai_review"].get("reason")
                            creatives_needing_approval.append(creative_info)

                        results.append(update_result)

                    else:
                        # Create new creative
                        create_result, needs_approval = _create_new_creative(
                            creative=creative,
                            creative_repo=creative_repo,
                            format_value=format_value,
                            tenant=tenant,
                            webhook_url=webhook_url,
                            all_formats=all_formats,
                            registry=registry,
                            principal_id=principal_id,
                            incoming_identity=incoming_identity,
                        )

                        # Handle failed creates
                        if create_result.action == "failed":
                            creative_id = creative.creative_id or "unknown"
                            failed_creatives.append(
                                {
                                    "creative_id": creative_id,
                                    "error": create_result.errors[0] if create_result.errors else "Unknown error",
                                    "format": incoming_identity.label,
                                }
                            )
                            failed_count += 1
                            results.append(create_result)
                            continue

                        # Track counts
                        created_count += 1

                        # Track creatives needing approval for workflow creation
                        if needs_approval:
                            creative_info = {
                                "creative_id": create_result.creative_id,
                                "format": incoming_identity.label,
                                "name": creative.name,
                                "status": create_result.internal_status,
                            }
                            # AI review reason will be added asynchronously when review completes
                            # No ai_result available yet in async mode
                            creatives_needing_approval.append(creative_info)

                        results.append(create_result)

                    # If we reach here, creative processing succeeded
                    synced_creatives.append(creative)

            except AdCPSalesAgentError as e:
                # Typed errors keyed on their recovery semantics: TRANSIENT ones
                # (agent rate-limited/unavailable during the format fetch) are
                # request-level infra failures — propagate so the buyer sees
                # RATE_LIMITED/SERVICE_UNAVAILABLE on the wire and retries the
                # request, matching create_media_buy .
                # Correctable/terminal typed errors (e.g. unknown-format
                # AdCPValidationError) remain PER-ITEM failures: the request is
                # fine, that creative is not.
                if e.recovery == "transient":
                    raise
                creative_id = _get_field(raw_creative, "creative_id", "unknown")
                error_msg = str(e)
                failed_creatives.append(
                    {"creative_id": creative_id, "name": _get_field(raw_creative, "name"), "error": error_msg}
                )
                failed_count += 1
                # Carry the typed error's OWN classification onto the per-item
                # result, by handing over the EXCEPTION rather than a message plus
                # hand-plucked kwargs. Falling back to the SERVICE_UNAVAILABLE
                # default reported the SELLER as unavailable for a problem in the
                # buyer's own document and dropped the `field` that says which input
                # to fix — worst for an egress refusal, whose message deliberately
                # says nothing.
                #
                # Passing the exception is what makes that ONE conversion:
                # AdCPErrorDetail.from_exception reads code, field and the typed
                # details class off `e` and resolves sentence/recovery/suggestion
                # from CODE_TABLE — the same derivation the transport envelope uses,
                # so the per-creative advisory and the request-level envelope cannot
                # disagree. Nothing here forwards a recovery: it follows from the code.
                results.append(_failed_sync_result(creative_id, e))
            except Exception as e:
                # Savepoint automatically rolls back this creative only
                creative_id = _get_field(raw_creative, "creative_id", "unknown")
                error_msg = str(e)
                failed_creatives.append(
                    {"creative_id": creative_id, "name": _get_field(raw_creative, "name"), "error": error_msg}
                )
                failed_count += 1
                # Same single mapping as the request-level boundary: a pydantic
                # ValidationError becomes VALIDATION_ERROR with its field and details,
                # anything else becomes INTERNAL_ERROR. Synthesizing a bare INTERNAL_ERROR
                # here instead threw away the field the buyer needs.
                typed = adcp_error_for(e, document=raw_creative)
                typed.internal_detail = e
                typed.details = _with_creative(typed.details, creative_id)
                results.append(_failed_sync_result(creative_id, typed))

        # Archive creatives not in the sync payload when delete_missing=True
        if req.delete_missing:
            # Collect all creative IDs from the payload (regardless of success/failure)
            payload_creative_ids = {_get_field(c, "creative_id") for c in creatives}
            payload_creative_ids.discard(None)

            # Query for existing creatives belonging to this tenant+principal
            # that are NOT in the payload and NOT already archived
            existing_creatives = creative_repo.list_by_principal(principal_id)

            for db_creative in existing_creatives:
                if db_creative.creative_id not in payload_creative_ids and db_creative.status != "archived":
                    db_creative.status = "archived"
                    deleted_count += 1
                    results.append(
                        SyncCreativeResult(
                            creative_id=db_creative.creative_id,
                            action=CreativeAction.deleted,
                        )
                    )

        # Approval workflow steps join THIS transaction.
        # No dry_run condition: the identical write path runs on both branches and
        # a preview's rollback discards the steps with the creatives, so a
        # preview now exercises the step/mapping write instead of skipping it.
        # Ordering: BaseUoW.__exit__ commits and only THEN drains after_commit,
        # so the notification below cannot name a step the commit has not yet
        # released — it holds by construction, not by careful sequencing.
        if creatives_needing_approval:
            _create_sync_workflow_steps(
                creatives_needing_approval=creatives_needing_approval,
                principal_id=principal_id,
                tenant=tenant,
                approval_mode=approval_mode,
                push_notification_config=req.push_notification_config,
                uow=uow,
            )

            def _notify() -> None:
                _send_creative_notifications(
                    creatives_needing_approval=creatives_needing_approval,
                    tenant=tenant,
                    approval_mode=approval_mode,
                    principal_id=principal_id,
                )

            creative_repo.after_commit(_notify, label="creative_approval_slack")

        # LIVE: close (and so commit) the creatives transaction here, exactly as
        # the implicit block exit did before — the assignment stage then opens
        # its own, and reads these creatives as committed rows.
        # DRY: leave it open. The assignment stage joins THIS transaction and
        # reads the same creatives as flushed rows, so it grades the post-sync
        # state without a shadow carrier, and the whole thing rolls back together.
        # NOTE: this conditional is the transaction seam itself (the commit-vs-
        # rollback decision), not a hand-placed effect gate — it is deliberately
        # retained, so this file is not literally dry_run-free after prkv.16.
        # JOINED: the caller owns the transaction, so it stays open like a preview's and
        # the assignment stage joins it; the caller's commit or rollback decides both.
        keep_open = dry_run or joined
        if not keep_open:
            stack.close()

        # Process assignments (spec-compliant: creative_id → package_ids mapping).
        # ONE mechanism, one call site, both branches: the same resolution,
        # validation, strict-raise, upsert, weight normalization and media-buy
        # status transition run either way — dry_run differs only in which
        # transaction they run in and that it is discarded.
        assignment_list = _process_assignments(
            assignments=req.assignments,
            results=results,
            tenant=tenant,
            validation_mode=validation_mode,
            principal_id=principal_id,
            uow=uow if keep_open else None,
        )

    # The audit writer opens a unit and a session of its own, so it runs only once no unit of
    # this sync is open: after the caller's commit when the sync joined one, and after this
    # service's own unit closed otherwise. Run inside a joined transaction it would record a
    # sync the caller can still roll back, and its own commit could release the caller's
    # pending writes. The nested-unit-of-work guard reads every call in a function handed a
    # ``uow`` as made inside it, so the two sites below carry its marker.
    def _record() -> None:
        # structural-guard: nested-unit-of-work - runs after commit or close, never in a unit
        _audit_log_sync(
            tenant=tenant,
            principal_id=principal_id,
            synced_creatives=synced_creatives,
            failed_creatives=failed_creatives,
            assignment_list=assignment_list,
            creative_ids=req.creative_ids,
            dry_run=dry_run,
            created_count=created_count,
            updated_count=updated_count,
            unchanged_count=unchanged_count,
            failed_count=failed_count,
            creatives_needing_approval=creatives_needing_approval,
        )
        log_tool_activity(identity, "sync_creatives", start_time)

    if joined:
        creative_repo.after_commit(_record, label="sync_creatives_audit")
    else:
        # structural-guard: nested-unit-of-work - this service's own unit closed above
        _record()

    # Build message
    message = f"Synced {created_count + updated_count} creatives"
    if created_count:
        message += f" ({created_count} created"
        if updated_count:
            message += f", {updated_count} updated"
        message += ")"
    elif updated_count:
        message += f" ({updated_count} updated)"
    if unchanged_count:
        message += f", {unchanged_count} unchanged"
    if deleted_count:
        message += f", {deleted_count} archived"
    if failed_count:
        message += f", {failed_count} failed"
    if assignment_list:
        message += f", {len(assignment_list)} assignments created"
    if creatives_needing_approval:
        message += f", {len(creatives_needing_approval)} require approval"

    # Build AdCP-compliant response (per official spec)
    response = SyncCreativesResponse(
        creatives=results,
        dry_run=dry_run,
        sandbox=sandbox,
        message=message,
    )

    return response
