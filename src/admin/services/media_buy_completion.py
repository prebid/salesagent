"""Shared media-buy completion / rejection webhook emission for the admin approval routes.

The operations approve and reject routes duplicated a near-identical
notification-emission block. Extracting it here (a) removes the duplication and
(b) lets the workflow and creative-unblock approval routes emit the same
completion artifact — async buyers otherwise never receive the final
``revision``/``confirmed_at`` for an approved buy. See #1544.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from adcp import create_a2a_webhook_payload, create_mcp_webhook_payload
from adcp.types import GeneratedTaskStatus as AdcpTaskStatus
from sqlalchemy.orm import Session

from src.core.database.repositories.push_notification_config import PushNotificationConfigRepository
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
    payload: Any
    if protocol == "a2a":
        payload = create_a2a_webhook_payload(
            task_id=step_data["step_id"],
            status=status,
            result=result,
            context_id=step_data["context_id"],
        )
    else:
        # tool_name is untrusted (workflow_steps DB column) — validate a COPY for the
        # SDK payload; metadata keeps the original label.
        payload = create_mcp_webhook_payload(
            task_id=step_data["step_id"],
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
