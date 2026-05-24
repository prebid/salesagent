"""Protocol push notifications for long-lived account/catalog changes."""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from datetime import UTC, datetime
from typing import Any

from adcp import WholesaleFeedEvent, WholesaleFeedWebhook
from adcp.webhooks import generate_webhook_idempotency_key

from src.core.database.repositories.push_notification import PushNotificationConfigSnapshot
from src.core.database.repositories.uow import (
    AccountUoW,
    ProductUoW,
    PushNotificationUoW,
    TenantConfigUoW,
    TenantSignalUoW,
)
from src.core.product_conversion import convert_product_model_to_schema
from src.core.signal_ids import adcp_safe_signal_id
from src.core.tools.signals import _tenant_signal_to_adcp, current_signal_feed_version
from src.services.protocol_webhook_service import ProtocolWebhookService

logger = logging.getLogger(__name__)


async def notify_account_status_changed_async(
    *,
    tenant_id: str,
    account_id: str,
    from_status: str,
    to_status: str,
    principal_id: str | None = None,
    principal_ids: list[str] | None = None,
) -> None:
    """Notify registered buyers that an account status changed."""
    visible_principal_ids = _account_visible_principal_ids(
        tenant_id=tenant_id,
        account_id=account_id,
        fallback_principal_id=principal_id,
        explicit_principal_ids=principal_ids,
    )
    await _notify_protocol_change_async(
        tenant_id=tenant_id,
        event_type="account.status_changed",
        object_type="account",
        object_id=account_id,
        action="status_changed",
        data={"from_status": from_status, "to_status": to_status},
        principal_ids=visible_principal_ids,
    )


def notify_account_status_changed(
    *,
    tenant_id: str,
    account_id: str,
    from_status: str,
    to_status: str,
    principal_id: str | None = None,
    principal_ids: list[str] | None = None,
) -> None:
    """Sync wrapper for account status notifications from Flask handlers."""
    _run_or_schedule(
        notify_account_status_changed_async(
            tenant_id=tenant_id,
            account_id=account_id,
            from_status=from_status,
            to_status=to_status,
            principal_id=principal_id,
            principal_ids=principal_ids,
        )
    )


def notify_product_catalog_changed(
    *,
    tenant_id: str,
    action: str,
    product_id: str,
    data: dict[str, Any] | None = None,
    principal_ids: list[str] | None = None,
) -> None:
    """Notify registered buyers that the product catalog changed."""
    notification_type = _catalog_notification_type("product", action)
    _run_or_schedule(
        _notify_protocol_change_async(
            tenant_id=tenant_id,
            event_type=notification_type,
            object_type="product",
            object_id=product_id,
            action=action,
            refresh_tool="get_products",
            data=data or {},
            principal_ids=principal_ids,
        )
    )


def notify_signal_catalog_changed(
    *,
    tenant_id: str,
    action: str,
    signal_id: str,
    data: dict[str, Any] | None = None,
) -> None:
    """Notify registered buyers that the signals catalog changed."""
    wire_signal_id = adcp_safe_signal_id(signal_id)
    _run_or_schedule(
        _notify_protocol_change_async(
            tenant_id=tenant_id,
            event_type=_catalog_notification_type("signal", action),
            object_type="signal",
            object_id=wire_signal_id,
            action=action,
            refresh_tool="get_signals",
            data=data or {},
        )
    )


def notify_signal_catalog_changes(
    *,
    tenant_id: str,
    action: str,
    signal_ids: list[str],
    data: dict[str, Any] | None = None,
) -> None:
    """Notify registered buyers that one or more signals changed."""
    if not signal_ids:
        return
    notification_type = _catalog_notification_type("signal", action)
    _run_or_schedule(
        _notify_signal_catalog_changes_async(
            tenant_id=tenant_id,
            event_type=notification_type,
            action=action,
            signal_ids=signal_ids,
            data=data or {},
        )
    )


async def _notify_signal_catalog_changes_async(
    *,
    tenant_id: str,
    event_type: str,
    action: str,
    signal_ids: list[str],
    data: dict[str, Any],
) -> None:
    tasks = []
    for signal_id in signal_ids:
        wire_signal_id = adcp_safe_signal_id(signal_id)
        tasks.append(
            _notify_protocol_change_async(
                tenant_id=tenant_id,
                event_type=event_type,
                object_type="signal",
                object_id=wire_signal_id,
                action=action,
                refresh_tool="get_signals",
                data=data,
            )
        )
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for result in results:
        if isinstance(result, Exception):
            logger.warning("signal catalog webhook scheduling failed", exc_info=result)


async def _notify_protocol_change_async(
    *,
    tenant_id: str,
    event_type: str,
    object_type: str,
    object_id: str,
    action: str,
    data: dict[str, Any],
    principal_id: str | None = None,
    principal_ids: list[str] | None = None,
    refresh_tool: str | None = None,
) -> None:
    snapshots = _list_push_notification_targets(tenant_id, principal_id=principal_id)
    if principal_ids is not None:
        allowed_principals = set(principal_ids)
        snapshots = [snapshot for snapshot in snapshots if snapshot.principal_id in allowed_principals]
    snapshots = [
        snapshot
        for snapshot in snapshots
        if _snapshot_matches_change(
            snapshot,
            event_type=event_type,
            object_type=object_type,
            object_id=object_id,
            data=data,
        )
    ]
    if not snapshots:
        return

    timestamp = datetime.now(UTC).isoformat()
    tasks = [
        ProtocolWebhookService().send_notification(
            snapshot.to_delivery_config(),
            _build_change_payload(
                snapshot,
                tenant_id=tenant_id,
                event_type=event_type,
                object_type=object_type,
                object_id=object_id,
                action=action,
                refresh_tool=refresh_tool,
                data=data,
                timestamp=timestamp,
            ),
            {
                "task_type": event_type,
                "tenant_id": tenant_id,
                "principal_id": snapshot.principal_id,
            },
        )
        for snapshot in snapshots
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for result in results:
        if isinstance(result, Exception):
            logger.warning("protocol change webhook delivery failed", exc_info=result)


def _build_change_payload(
    snapshot: PushNotificationConfigSnapshot,
    *,
    tenant_id: str,
    event_type: str,
    object_type: str,
    object_id: str,
    action: str,
    refresh_tool: str | None,
    data: dict[str, Any],
    timestamp: str,
) -> dict[str, Any]:
    if object_type in {"product", "signal"}:
        return _build_catalog_change_payload(
            snapshot,
            tenant_id=tenant_id,
            event_type=event_type,
            object_type=object_type,
            object_id=object_id,
            action=action,
            refresh_tool=refresh_tool,
            data=data,
            timestamp=timestamp,
        )

    event = {
        "type": event_type,
        "object_type": object_type,
        "object_id": object_id,
        "action": action,
        "data": data,
        "timestamp": timestamp,
    }
    if refresh_tool is not None:
        event["refresh_tool"] = refresh_tool

    payload = {
        "notification_id": f"notif_{uuid.uuid4().hex}",
        "notification_type": event_type,
        "subscriber_id": snapshot.subscriber_id or snapshot.principal_id,
        "account_id": _payload_account_id(snapshot, object_type=object_type, object_id=object_id, data=data),
        "wholesale_feed_version": timestamp,
        "cache_scope": {
            "object_type": object_type,
            "object_id": object_id,
            "refresh_tool": refresh_tool,
        },
        "event": event,
        # Backward-compatible local shape retained for existing subscribers.
        "type": event_type,
        "tenant_id": tenant_id,
        "principal_id": snapshot.principal_id,
        "object_type": object_type,
        "object_id": object_id,
        "action": action,
        "data": data,
        "timestamp": timestamp,
    }
    if refresh_tool is not None:
        payload["refresh_tool"] = refresh_tool
    if snapshot.operation_id is not None:
        payload["operation_id"] = snapshot.operation_id
    if snapshot.validation_token is not None:
        payload["token"] = snapshot.validation_token
    return payload


def _build_catalog_change_payload(
    snapshot: PushNotificationConfigSnapshot,
    *,
    tenant_id: str,
    event_type: str,
    object_type: str,
    object_id: str,
    action: str,
    refresh_tool: str | None,
    data: dict[str, Any],
    timestamp: str,
) -> dict[str, Any]:
    notification_type = (
        event_type if event_type != "catalog.changed" else _catalog_notification_type(object_type, action)
    )
    event_uuid = uuid.uuid4()
    fired_at = datetime.now(UTC)
    cache_scope = data.get("cache_scope") or "public"
    account_id = _payload_account_id(snapshot, object_type=object_type, object_id=object_id, data=data)
    wholesale_feed_version = _catalog_feed_version(
        tenant_id=tenant_id,
        object_type=object_type,
        fallback=timestamp,
    )
    event = _build_wholesale_feed_event(
        tenant_id=tenant_id,
        event_id=event_uuid,
        event_type=notification_type,
        object_type=object_type,
        object_id=object_id,
        action=action,
        data=data,
        created_at=fired_at,
        cache_scope=cache_scope,
    )
    webhook = WholesaleFeedWebhook.model_validate(
        {
            "idempotency_key": generate_webhook_idempotency_key(),
            "notification_id": event_uuid,
            "notification_type": notification_type,
            "fired_at": fired_at,
            "subscriber_id": snapshot.subscriber_id or snapshot.principal_id,
            "account_id": str(account_id),
            "wholesale_feed_version": wholesale_feed_version,
            "cache_scope": cache_scope,
            "event": event,
        }
    )
    payload = webhook.model_dump(mode="json", exclude_none=True)
    payload.update(
        {
            # Backward-compatible local shape retained for existing subscribers.
            "type": "catalog.changed",
            "tenant_id": tenant_id,
            "principal_id": snapshot.principal_id,
            "object_type": object_type,
            "object_id": object_id,
            "action": action,
            "data": data,
            "timestamp": timestamp,
        }
    )
    if refresh_tool is not None:
        payload["refresh_tool"] = refresh_tool
    if snapshot.operation_id is not None:
        payload["operation_id"] = snapshot.operation_id
    if snapshot.validation_token is not None:
        payload["token"] = snapshot.validation_token
    return payload


def _build_wholesale_feed_event(
    *,
    tenant_id: str,
    event_id: uuid.UUID,
    event_type: str,
    object_type: str,
    object_id: str,
    action: str,
    data: dict[str, Any],
    created_at: datetime,
    cache_scope: str,
) -> dict[str, Any]:
    applies_to = {"scope": cache_scope}
    if cache_scope == "account" and data.get("account_ids"):
        applies_to["account_ids"] = data["account_ids"]

    if object_type == "signal":
        payload: dict[str, Any] = {
            "signal_agent_segment_id": object_id,
            "signal_id": data.get("signal_id"),
            "applies_to": applies_to,
        }
        if event_type in {"signal.created", "signal.updated"}:
            signal = _load_signal_for_webhook(tenant_id, object_id)
            if signal is not None:
                payload["signal"] = signal.model_dump(mode="json", exclude_none=True)
            else:
                return _build_bulk_change_event(
                    event_id=event_id,
                    created_at=created_at,
                    object_type=object_type,
                    object_id=f"{object_type}_{action}_{object_id}",
                    action=action,
                    applies_to=applies_to,
                )
        if event_type == "signal.updated" and data.get("changed_fields"):
            payload["changed_fields"] = data["changed_fields"]
        if event_type == "signal.priced":
            payload["pricing_options"] = data.get("pricing_options") or _load_signal_pricing_options(
                tenant_id, object_id
            )
        if event_type == "signal.removed" and data.get("removal_reason"):
            payload["removal_reason"] = data["removal_reason"]
    elif object_type == "product":
        payload = {"product_id": object_id, "applies_to": applies_to}
        if event_type in {"product.created", "product.updated"}:
            product = _load_product_for_webhook(tenant_id, object_id)
            if product is not None:
                payload["product"] = product.model_dump(mode="json", exclude_none=True)
            else:
                return _build_bulk_change_event(
                    event_id=event_id,
                    created_at=created_at,
                    object_type=object_type,
                    object_id=f"{object_type}_{action}_{object_id}",
                    action=action,
                    applies_to=applies_to,
                )
        if event_type == "product.updated" and data.get("changed_fields"):
            payload["changed_fields"] = data["changed_fields"]
        if event_type == "product.priced" and data.get("pricing_options"):
            payload["pricing_options"] = data["pricing_options"]
        if event_type == "product.removed" and data.get("removal_reason"):
            payload["removal_reason"] = data["removal_reason"]
    else:
        return _build_bulk_change_event(
            event_id=event_id,
            created_at=created_at,
            object_type=object_type,
            object_id=f"{object_type}_{action}_{object_id}",
            action=action,
            applies_to=applies_to,
        )

    return WholesaleFeedEvent.model_validate(
        {
            "event_id": event_id,
            "event_type": event_type,
            "entity_type": object_type,
            "entity_id": object_id,
            "created_at": created_at,
            "payload": payload,
        }
    ).model_dump(mode="json", exclude_none=True)


def _build_bulk_change_event(
    *,
    event_id: uuid.UUID,
    created_at: datetime,
    object_type: str,
    object_id: str,
    action: str,
    applies_to: dict[str, Any],
) -> dict[str, Any]:
    affected_entity_type = "signal" if object_type == "signal" else "product"
    return WholesaleFeedEvent.model_validate(
        {
            "event_id": event_id,
            "event_type": "wholesale_feed.bulk_change",
            "entity_type": "feed",
            "entity_id": object_id,
            "created_at": created_at,
            "payload": {
                "summary": f"{affected_entity_type} catalog {action}",
                "affected_count": 1,
                "recommendation": "wholesale_resync",
                "affected_entity_type": affected_entity_type,
                "applies_to": applies_to,
            },
        }
    ).model_dump(mode="json", exclude_none=True)


def _catalog_notification_type(object_type: str, action: str) -> str:
    action_suffix = {
        "created": "created",
        "updated": "updated",
        "priced": "priced",
        "deleted": "removed",
        "removed": "removed",
    }.get(
        action,
        "updated",
    )
    return f"{object_type}.{action_suffix}"


def _catalog_feed_version(*, tenant_id: str, object_type: str, fallback: str) -> str:
    if object_type == "signal":
        try:
            ad_server, agent_url = _tenant_signal_projection_inputs(tenant_id)
            return current_signal_feed_version(tenant_id, ad_server=ad_server, agent_url=agent_url)
        except Exception:
            logger.debug("failed to compute signal feed version for webhook", exc_info=True)
    return fallback


def _tenant_signal_projection_inputs(tenant_id: str) -> tuple[str | None, str | None]:
    with TenantConfigUoW(tenant_id) as uow:
        assert uow.tenant_config is not None
        tenant = uow.tenant_config.get_tenant()
        if tenant is None:
            return None, None
        return tenant.ad_server, tenant.public_agent_url


def _payload_account_id(
    snapshot: PushNotificationConfigSnapshot,
    *,
    object_type: str,
    object_id: str,
    data: dict[str, Any],
) -> str:
    if object_type == "account":
        return object_id
    if snapshot.account_id is not None:
        return snapshot.account_id
    return str(data.get("account_id") or snapshot.principal_id)


def _snapshot_matches_change(
    snapshot: PushNotificationConfigSnapshot,
    *,
    event_type: str,
    object_type: str,
    object_id: str,
    data: dict[str, Any],
) -> bool:
    if snapshot.event_types and not _event_type_matches(event_type, snapshot.event_types):
        return False
    if object_type == "account" and snapshot.account_id is not None:
        return snapshot.account_id == object_id
    account_ids = data.get("account_ids")
    if account_ids and snapshot.account_id is not None:
        return snapshot.account_id in set(account_ids)
    return True


def _event_type_matches(event_type: str, subscriptions: list[str]) -> bool:
    for subscription in subscriptions:
        if subscription == event_type:
            return True
        if subscription.endswith(".*") and event_type.startswith(subscription[:-1]):
            return True
        if subscription == "catalog.changed" and event_type.split(".", 1)[0] in {"product", "signal"}:
            return True
    return False


def _load_signal_for_webhook(tenant_id: str, signal_agent_segment_id: str):
    try:
        with TenantSignalUoW(tenant_id) as uow:
            assert uow.tenant_signals is not None
            signal = uow.tenant_signals.get_by_id(signal_agent_segment_id)
            if signal is None:
                return None
            return _tenant_signal_to_adcp(signal, ad_server=None, agent_url=None)
    except Exception:
        logger.debug("failed to load signal %s for catalog webhook", signal_agent_segment_id, exc_info=True)
        return None


def _load_signal_pricing_options(tenant_id: str, signal_agent_segment_id: str) -> list[dict[str, Any]]:
    signal = _load_signal_for_webhook(tenant_id, signal_agent_segment_id)
    if signal is None:
        return []
    return [option.model_dump(mode="json", exclude_none=True) for option in signal.pricing_options]


def _load_product_for_webhook(tenant_id: str, product_id: str):
    try:
        with ProductUoW(tenant_id) as uow:
            assert uow.products is not None
            product = uow.products.get_by_id_with_pricing(product_id)
            if product is None:
                return None
            return convert_product_model_to_schema(product)
    except Exception:
        logger.debug("failed to load product %s for catalog webhook", product_id, exc_info=True)
        return None


def _account_visible_principal_ids(
    *,
    tenant_id: str,
    account_id: str,
    fallback_principal_id: str | None = None,
    explicit_principal_ids: list[str] | None = None,
) -> list[str]:
    if explicit_principal_ids is not None:
        return sorted(set(explicit_principal_ids))

    with AccountUoW(tenant_id) as uow:
        assert uow.accounts is not None
        principal_ids = uow.accounts.list_principal_ids_for_account(account_id)

    if not principal_ids and fallback_principal_id is not None:
        principal_ids = [fallback_principal_id]
    return sorted(set(principal_ids))


def _list_push_notification_targets(
    tenant_id: str, *, principal_id: str | None = None
) -> list[PushNotificationConfigSnapshot]:
    with PushNotificationUoW(tenant_id) as uow:
        assert uow.push_notifications is not None
        return uow.push_notifications.list_active_snapshots(principal_id=principal_id, purpose="catalog_changes")


def _run_or_schedule(coro) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        threading.Thread(target=lambda: asyncio.run(coro), daemon=True).start()
        return

    loop.create_task(coro)
