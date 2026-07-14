"""Shared media-buy completion / rejection webhook emission for the admin approval routes.

The operations approve and reject routes duplicated a near-identical
notification-emission block. Extracting it here (a) removes the duplication and
(b) lets the workflow and creative-unblock approval routes emit the same
completion artifact — async buyers otherwise never receive the final
``revision``/``confirmed_at`` for an approved buy. See #1544.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from adcp import create_a2a_webhook_payload, create_mcp_webhook_payload
from adcp.types import GeneratedTaskStatus as AdcpTaskStatus
from sqlalchemy.orm import Session

from src.core.database.repositories import MediaBuyRepository
from src.core.database.repositories.push_notification_config import PushNotificationConfigRepository
from src.core.database.repositories.workflow import WorkflowRepository
from src.core.schemas import CreateMediaBuySuccess, Package
from src.core.webhook_validator import validate_webhook_task_type
from src.services.protocol_webhook_service import get_protocol_webhook_service

if TYPE_CHECKING:
    from src.core.database.models import MediaBuy, MediaPackage

logger = logging.getLogger(__name__)


def build_media_buy_result(
    media_buy: MediaBuy,
    packages: list[MediaPackage],
    *,
    rejection_reason: str | None = None,
) -> CreateMediaBuySuccess:
    """Build the internal ``CreateMediaBuySuccess`` the completion/rejection webhook carries.

    Echoes the persisted ``confirmed_at``/``revision``. ``rejection_reason`` is set
    only on the reject path (a spec MUST on the seller rejection notification, pinned
    beta.3); ``None`` otherwise, so it is omitted from the wire.
    """
    return CreateMediaBuySuccess(
        media_buy_id=media_buy.media_buy_id,
        packages=[Package(package_id=p.package_id) for p in packages],
        context={},
        confirmed_at=media_buy.confirmed_at,
        revision=media_buy.revision,
        rejection_reason=rejection_reason,
    )


def emit_media_buy_webhook(
    step_data: dict[str, Any],
    webhook_config: Any,
    result: CreateMediaBuySuccess,
    status: AdcpTaskStatus,
) -> None:
    """Send the media-buy completion/rejection notification for the buyer's protocol.

    Protocol (``mcp``/``a2a``) is read from the workflow step's ``request_data``.
    Best-effort: a webhook failure is logged, never raised — the DB transition has
    already committed and must not be rolled back by a delivery error (mirrors the
    approval routes). #1544.
    """
    metadata = {"task_type": step_data["tool_name"]}
    # Default to MCP for backward compatibility with steps recorded before the
    # protocol was persisted on request_data.
    protocol = step_data["request_data"].get("protocol", "mcp")
    # Correlate to the id the BUYER holds. A2A returned an outer transport task id
    # (persisted on the step's request_data as ``external_task_id`` at create time);
    # the buyer polls / receives the webhook against THAT id, not the internal
    # step_id. MCP/REST have no outer id, so they fall back to step_id. #1544 B6.
    correlation_task_id = step_data["request_data"].get("external_task_id") or step_data["step_id"]
    payload: Any
    if protocol == "a2a":
        payload = create_a2a_webhook_payload(
            task_id=correlation_task_id,
            status=status,
            result=result,
            context_id=step_data["context_id"],
        )
    else:
        # tool_name is untrusted (workflow_steps DB column) — validate a COPY for the
        # SDK payload; metadata keeps the original label.
        payload = create_mcp_webhook_payload(
            task_id=correlation_task_id,
            task_type=validate_webhook_task_type(step_data.get("tool_name", "create_media_buy")),
            result=result,
            status=status,
        )
    try:
        service = get_protocol_webhook_service()
        asyncio.run(
            service.send_notification(
                push_notification_config=webhook_config,
                payload=payload,
                metadata=metadata,
            )
        )
        logger.info(f"Sent {status} webhook notification for media buy {result.media_buy_id}")
    except Exception as webhook_err:
        logger.warning(f"Failed to send webhook notification: {webhook_err}")


def emit_media_buy_completion(
    session: Session,
    tenant_id: str,
    media_buy: MediaBuy | None,
    packages: list[MediaPackage],
    step_data: dict[str, Any],
    status: AdcpTaskStatus,
    *,
    rejection_reason: str | None = None,
) -> None:
    """Look up the buyer's active push config from the workflow step and emit the
    completion/rejection artifact if one is configured. No-op when the buy has no
    push_notification_config — so every approval route (operations, workflow,
    creative-unblock) delivers the final revision/confirmed_at the same way. #1544.
    """
    if media_buy is None:
        return
    push_config = (step_data.get("request_data") or {}).get("push_notification_config") or {}
    url = push_config.get("url")
    if not url:
        return
    # Repository lookup (no raw select): the buyer's active configs for this
    # principal, matched to the step's push URL — newest wins if more than one.
    configs = PushNotificationConfigRepository(session, tenant_id).list_active_by_principal(media_buy.principal_id)
    matches = sorted((c for c in configs if c.url == url), key=lambda c: c.created_at, reverse=True)
    if not matches:
        return
    webhook_config = matches[0]
    emit_media_buy_webhook(
        step_data,
        webhook_config,
        build_media_buy_result(media_buy, packages, rejection_reason=rejection_reason),
        status,
    )


def _terminalize_step_and_emit(
    session: Session,
    tenant_id: str,
    *,
    media_buy: MediaBuy,
    packages: list[MediaPackage],
    step_id: str,
    step_data: dict[str, Any],
    step_status: str,
    task_status: AdcpTaskStatus,
    rejection_reason: str | None = None,
) -> None:
    """Persist the terminal decision artifact on the workflow step, commit, then emit.

    The single place a media-buy approve/reject decision becomes durable AND
    observable: stores the built ``CreateMediaBuySuccess`` on
    ``WorkflowStep.response_data`` under ``step_status``
    (``completed``/``rejected``/``failed``) so ``tasks/get`` can return the final
    artifact, commits, then emits the buyer's completion/rejection webhook AFTER
    commit (best-effort — a delivery failure never rolls back the committed
    decision). The workflow + creative-unblock routes previously left the step
    non-terminal with no artifact; centralising it here fixes both. #1544.
    """
    result = build_media_buy_result(media_buy, packages, rejection_reason=rejection_reason)
    WorkflowRepository(session, tenant_id).update_status(
        step_id,
        status=step_status,
        response_data=result.model_dump(mode="json"),
        error_message=rejection_reason,
    )
    session.commit()
    emit_media_buy_completion(
        session, tenant_id, media_buy, packages, step_data, task_status, rejection_reason=rejection_reason
    )


def finalize_media_buy_approval(
    session: Session,
    tenant_id: str,
    *,
    media_buy_id: str,
    step_id: str,
    step_data: dict[str, Any],
    compute_target: Callable[[MediaBuy], str | None],
    run_adapter: Callable[[], tuple[bool, str | None]],
    approved_by: str | None = None,
    approved_at: datetime.datetime | None = None,
) -> tuple[bool, str | None]:
    """Atomic approve finalizer shared by the operations / workflow / creative-unblock routes.

    Sequence (see #1544 review B4/B5):

      1. Stamp the approval instant BEFORE external work — transition the buy to the
         status COMPUTED UNDER THE ROW LOCK (``update_status_computed_or_raise``, so a
         concurrent flight-window change can't be clobbered), stamping
         ``approved_at``/``approved_by`` when supplied. That write-once approval
         instant is what ``confirmed_at`` records — NOT adapter-completion time (the
         prior workflow route stamped ``approved_at`` only after the adapter
         returned). commit.
      2. Run the adapter (``run_adapter`` callback). On failure: mark the buy
         ``failed`` and the step ``failed``, commit, return ``(False, err)`` — no
         completion artifact.
      3. On success: terminalize the step (``completed``) with the response artifact,
         commit, and emit the completion webhook after commit.

    ``approved_at``/``approved_by`` are omitted when finalizing a buy already stamped
    at an earlier ``pending_creatives`` hold (the creative-unblock path):
    ``confirmed_at`` is write-once and ``approved_at`` must keep the original
    admin-approval instant. Returns ``(success, error_message)``.
    """
    repo = MediaBuyRepository(session, tenant_id)
    repo.update_status_computed_or_raise(media_buy_id, compute_target, approved_at=approved_at, approved_by=approved_by)
    session.commit()

    success, error_msg = run_adapter()
    if not success:
        # The buy was just transitioned above, so it exists — *_or_raise (never a
        # discarded None) also satisfies the no-silent-skip guard.
        repo.update_status_or_raise(media_buy_id, "failed")
        WorkflowRepository(session, tenant_id).update_status(step_id, status="failed", error_message=error_msg)
        session.commit()
        return False, error_msg

    _terminalize_step_and_emit(
        session,
        tenant_id,
        media_buy=repo.get_by_id_or_raise(media_buy_id),
        packages=repo.get_packages(media_buy_id),
        step_id=step_id,
        step_data=step_data,
        step_status="completed",
        task_status=AdcpTaskStatus.completed,
    )
    return True, None


def finalize_unblocked_media_buy(tenant_id: str, media_buy_id: str) -> tuple[bool, str | None]:
    """Finalize ONE media buy whose last blocking creative was just approved.

    Called from the creative-approval route once all of a buy's creatives are
    approved. Owns its OWN DB session (the caller's creative UoW has already
    committed, and — unlike a plain route blueprint — ``creatives.py`` is a scanned
    business-logic module that must not open ``get_db_session`` itself), then routes
    through the shared approve finalizer: look up the buy's create/approval workflow
    step, run the adapter, transition to the flight-derived status UNDER THE ROW LOCK,
    terminalize the step with its artifact, and emit the completion webhook.
    ``approved_at``/``approved_by`` are NOT re-stamped — ``confirmed_at`` was recorded
    at the earlier ``pending_creatives`` hold (write-once). Falls back to an adapter +
    status-only transition when the buy has no workflow step (no async buyer task to
    notify). Returns ``(success, error_message)``. #1544.
    """
    from src.core.database.database_session import get_db_session
    from src.core.media_buy_flight import lifecycle_status_for_window, resolve_flight_window_utc
    from src.core.tools.media_buy_create import execute_approved_media_buy

    def _flight_status(media_buy: MediaBuy) -> str:
        return lifecycle_status_for_window(datetime.datetime.now(datetime.UTC), *resolve_flight_window_utc(media_buy))

    with get_db_session() as session:
        wf_repo = WorkflowRepository(session, tenant_id)
        mapping = wf_repo.get_latest_mapping_for_object("media_buy", media_buy_id)
        step = wf_repo.get_by_step_id(mapping.step_id) if mapping else None
        if step is None:
            # No workflow step (no async buyer task to notify) — adapter + status only.
            success, error_msg = execute_approved_media_buy(media_buy_id, tenant_id)
            if success:
                MediaBuyRepository(session, tenant_id).update_status_computed_or_raise(media_buy_id, _flight_status)
                session.commit()
            return success, error_msg

        step_data = {
            "step_id": step.step_id,
            "context_id": step.context_id,
            "tool_name": step.tool_name,
            "request_data": step.request_data or {},
        }
        return finalize_media_buy_approval(
            session,
            tenant_id,
            media_buy_id=media_buy_id,
            step_id=step.step_id,
            step_data=step_data,
            compute_target=_flight_status,
            run_adapter=lambda: execute_approved_media_buy(media_buy_id, tenant_id),
        )


def finalize_media_buy_rejection(
    session: Session,
    tenant_id: str,
    *,
    media_buy_id: str,
    step_id: str,
    step_data: dict[str, Any],
    reason: str,
) -> None:
    """Atomic reject finalizer shared by the operations + workflow reject routes.

    Transitions the buy to ``rejected`` (revision bump via the repo seam), stores the
    rejection artifact on the workflow step, commits, and emits the rejection webhook
    carrying ``rejection_reason`` (a pinned-beta.3 MUST on the seller rejection
    notification). Ensures a rejected buy never lingers at ``pending_approval``
    without an artifact — the workflow reject route previously left the mapped buy
    untouched. #1544.
    """
    repo = MediaBuyRepository(session, tenant_id)
    buy = repo.update_status_or_raise(media_buy_id, "rejected")
    _terminalize_step_and_emit(
        session,
        tenant_id,
        media_buy=buy,
        packages=repo.get_packages(media_buy_id),
        step_id=step_id,
        step_data=step_data,
        step_status="rejected",
        task_status=AdcpTaskStatus.rejected,
        rejection_reason=reason,
    )
