import pytest

from src.core.database.repositories.push_notification import PushNotificationConfigSnapshot
from src.core.schemas import Signal, SignalDeployment
from src.core.tools.signals import _cpm_pricing_option
from src.services import protocol_change_webhooks


@pytest.mark.asyncio
async def test_account_status_change_webhook_targets_registered_principal(monkeypatch) -> None:
    sent = []
    snapshots = [
        PushNotificationConfigSnapshot(
            id="pnc_1",
            tenant_id="tenant_1",
            principal_id="agent_1",
            url="https://buyer.example/webhooks",
            operation_id="sync-op-1",
            authentication_type="HMAC-SHA256",
            authentication_token="shared-secret",
            validation_token="client-validation-token",
        )
    ]

    def fake_targets(tenant_id: str, *, principal_id: str | None = None):
        assert tenant_id == "tenant_1"
        assert principal_id is None
        return snapshots

    class FakeProtocolWebhookService:
        async def send_notification(self, push_notification_config, payload, metadata):
            sent.append(
                {
                    "url": push_notification_config.url,
                    "payload": payload,
                    "metadata": metadata,
                }
            )
            return True

    monkeypatch.setattr(protocol_change_webhooks, "_list_push_notification_targets", fake_targets)
    monkeypatch.setattr(
        protocol_change_webhooks,
        "_account_visible_principal_ids",
        lambda **kwargs: ["agent_1"],
    )
    monkeypatch.setattr(protocol_change_webhooks, "ProtocolWebhookService", FakeProtocolWebhookService)

    await protocol_change_webhooks.notify_account_status_changed_async(
        tenant_id="tenant_1",
        account_id="acc_1",
        from_status="pending_approval",
        to_status="active",
        principal_id="agent_1",
    )

    assert len(sent) == 1
    assert sent[0]["url"] == "https://buyer.example/webhooks"
    assert sent[0]["payload"]["notification_type"] == "account.status_changed"
    assert sent[0]["payload"]["subscriber_id"] == "agent_1"
    assert sent[0]["payload"]["account_id"] == "acc_1"
    assert sent[0]["payload"]["wholesale_feed_version"]
    assert sent[0]["payload"]["cache_scope"] == {
        "object_type": "account",
        "object_id": "acc_1",
        "refresh_tool": None,
    }
    assert sent[0]["payload"]["event"]["type"] == "account.status_changed"
    assert sent[0]["payload"]["type"] == "account.status_changed"
    assert sent[0]["payload"]["object_type"] == "account"
    assert sent[0]["payload"]["object_id"] == "acc_1"
    assert sent[0]["payload"]["data"] == {"from_status": "pending_approval", "to_status": "active"}
    assert sent[0]["payload"]["event"]["data"] == {"from_status": "pending_approval", "to_status": "active"}
    assert sent[0]["payload"]["operation_id"] == "sync-op-1"
    assert sent[0]["payload"]["token"] == "client-validation-token"
    assert "validation_token" not in sent[0]["payload"]
    assert sent[0]["metadata"] == {
        "task_type": "account.status_changed",
        "tenant_id": "tenant_1",
        "principal_id": "agent_1",
    }


@pytest.mark.asyncio
async def test_catalog_change_webhook_includes_refresh_tool(monkeypatch) -> None:
    sent = []
    snapshots = [
        PushNotificationConfigSnapshot(
            id="pnc_1",
            tenant_id="tenant_1",
            principal_id="agent_1",
            url="https://buyer.example/webhooks",
        )
    ]

    class FakeProtocolWebhookService:
        async def send_notification(self, push_notification_config, payload, metadata):
            sent.append({"payload": payload, "metadata": metadata})
            return True

    signal = Signal(
        signal_id={
            "source": "agent",
            "agent_url": "https://salesagent.adcontextprotocol.org/signals",
            "id": "sig_1",
        },
        signal_agent_segment_id="sig_1",
        name="Audience",
        description="Audience signal",
        signal_type="owned",
        data_provider="publisher",
        coverage_percentage=100.0,
        deployments=[SignalDeployment(platform="mock", is_live=True, type="platform")],
        pricing_options=_cpm_pricing_option(0.0),
    )

    monkeypatch.setattr(protocol_change_webhooks, "_list_push_notification_targets", lambda *args, **kwargs: snapshots)
    monkeypatch.setattr(protocol_change_webhooks, "_load_signal_for_webhook", lambda *args, **kwargs: signal)
    monkeypatch.setattr(protocol_change_webhooks, "ProtocolWebhookService", FakeProtocolWebhookService)

    await protocol_change_webhooks._notify_protocol_change_async(
        tenant_id="tenant_1",
        event_type="catalog.changed",
        object_type="signal",
        object_id="sig_1",
        action="updated",
        refresh_tool="get_signals",
        data={"name": "Audience"},
    )

    assert sent[0]["payload"]["type"] == "catalog.changed"
    assert sent[0]["payload"]["notification_type"] == "signal.updated"
    assert sent[0]["payload"]["subscriber_id"] == "agent_1"
    assert sent[0]["payload"]["cache_scope"] == "public"
    assert sent[0]["payload"]["event"]["event_type"] == "signal.updated"
    assert sent[0]["payload"]["event"]["payload"]["signal_agent_segment_id"] == "sig_1"
    assert sent[0]["payload"]["object_type"] == "signal"
    assert sent[0]["payload"]["object_id"] == "sig_1"
    assert sent[0]["payload"]["action"] == "updated"
    assert sent[0]["payload"]["refresh_tool"] == "get_signals"
    assert sent[0]["payload"]["data"] == {"name": "Audience"}


def test_signal_catalog_change_projects_legacy_signal_id_to_wire_safe_id(monkeypatch) -> None:
    captured = {}

    def fake_run(coro):
        captured["coro"] = coro
        coro.close()

    async def noop():
        return None

    def fake_notify(**kwargs):
        captured["kwargs"] = kwargs
        return noop()

    monkeypatch.setattr(protocol_change_webhooks, "_run_or_schedule", fake_run)
    monkeypatch.setattr(protocol_change_webhooks, "_notify_protocol_change_async", fake_notify)

    protocol_change_webhooks.notify_signal_catalog_changed(
        tenant_id="tenant_1",
        action="updated",
        signal_id="audience.sports_fans",
    )

    assert captured["kwargs"]["object_id"] == "audience_sports_fans"


def test_signal_catalog_changes_batches_into_one_scheduled_job(monkeypatch) -> None:
    scheduled = []

    def fake_run(coro):
        scheduled.append(coro)
        coro.close()

    monkeypatch.setattr(protocol_change_webhooks, "_run_or_schedule", fake_run)

    protocol_change_webhooks.notify_signal_catalog_changes(
        tenant_id="tenant_1",
        action="updated",
        signal_ids=["sig_1", "sig_2", "sig_3"],
    )

    assert len(scheduled) == 1


@pytest.mark.asyncio
async def test_product_catalog_change_filters_restricted_principals(monkeypatch) -> None:
    sent = []
    snapshots = [
        PushNotificationConfigSnapshot(
            id="pnc_1",
            tenant_id="tenant_1",
            principal_id="agent_1",
            url="https://buyer-1.example/webhooks",
            purpose="catalog_changes",
        ),
        PushNotificationConfigSnapshot(
            id="pnc_2",
            tenant_id="tenant_1",
            principal_id="agent_2",
            url="https://buyer-2.example/webhooks",
            purpose="catalog_changes",
        ),
    ]

    class FakeProtocolWebhookService:
        async def send_notification(self, push_notification_config, payload, metadata):
            sent.append({"url": push_notification_config.url, "payload": payload, "metadata": metadata})
            return True

    monkeypatch.setattr(protocol_change_webhooks, "_list_push_notification_targets", lambda *args, **kwargs: snapshots)
    monkeypatch.setattr(protocol_change_webhooks, "ProtocolWebhookService", FakeProtocolWebhookService)

    await protocol_change_webhooks._notify_protocol_change_async(
        tenant_id="tenant_1",
        event_type="catalog.changed",
        object_type="product",
        object_id="prod_1",
        action="updated",
        refresh_tool="get_products",
        data={"name": "Restricted Product"},
        principal_ids=["agent_2"],
    )

    assert [entry["url"] for entry in sent] == ["https://buyer-2.example/webhooks"]


@pytest.mark.asyncio
async def test_catalog_change_honors_account_subscription_scope_and_event_types(monkeypatch) -> None:
    sent = []
    snapshots = [
        PushNotificationConfigSnapshot(
            id="pnc_1",
            tenant_id="tenant_1",
            principal_id="agent_1",
            subscriber_id="sub_1",
            account_id="acc_1",
            event_types=["signal.*"],
            url="https://buyer-1.example/webhooks",
            purpose="catalog_changes",
        ),
        PushNotificationConfigSnapshot(
            id="pnc_2",
            tenant_id="tenant_1",
            principal_id="agent_1",
            subscriber_id="sub_2",
            account_id="acc_2",
            event_types=["product.updated"],
            url="https://buyer-2.example/webhooks",
            purpose="catalog_changes",
        ),
    ]

    class FakeProtocolWebhookService:
        async def send_notification(self, push_notification_config, payload, metadata):
            sent.append({"url": push_notification_config.url, "payload": payload, "metadata": metadata})
            return True

    signal = Signal(
        signal_id={
            "source": "agent",
            "agent_url": "https://salesagent.adcontextprotocol.org/signals",
            "id": "sig_1",
        },
        signal_agent_segment_id="sig_1",
        name="Audience",
        description="Audience signal",
        signal_type="owned",
        data_provider="publisher",
        coverage_percentage=100.0,
        deployments=[SignalDeployment(platform="mock", is_live=True, type="platform")],
        pricing_options=_cpm_pricing_option(0.0),
    )

    monkeypatch.setattr(protocol_change_webhooks, "_list_push_notification_targets", lambda *args, **kwargs: snapshots)
    monkeypatch.setattr(protocol_change_webhooks, "_load_signal_for_webhook", lambda *args, **kwargs: signal)
    monkeypatch.setattr(protocol_change_webhooks, "ProtocolWebhookService", FakeProtocolWebhookService)

    await protocol_change_webhooks._notify_protocol_change_async(
        tenant_id="tenant_1",
        event_type="signal.updated",
        object_type="signal",
        object_id="sig_1",
        action="updated",
        refresh_tool="get_signals",
        data={"account_ids": ["acc_1"]},
    )

    assert [entry["url"] for entry in sent] == ["https://buyer-1.example/webhooks"]
    assert sent[0]["payload"]["subscriber_id"] == "sub_1"
    assert sent[0]["payload"]["account_id"] == "acc_1"


@pytest.mark.asyncio
async def test_account_status_change_filters_to_account_access_principals(monkeypatch) -> None:
    sent = []
    snapshots = [
        PushNotificationConfigSnapshot(
            id="pnc_1",
            tenant_id="tenant_1",
            principal_id="agent_1",
            url="https://buyer-1.example/webhooks",
            purpose="catalog_changes",
        ),
        PushNotificationConfigSnapshot(
            id="pnc_2",
            tenant_id="tenant_1",
            principal_id="agent_2",
            url="https://buyer-2.example/webhooks",
            purpose="catalog_changes",
        ),
    ]

    class FakeProtocolWebhookService:
        async def send_notification(self, push_notification_config, payload, metadata):
            sent.append({"url": push_notification_config.url, "payload": payload, "metadata": metadata})
            return True

    monkeypatch.setattr(protocol_change_webhooks, "_list_push_notification_targets", lambda *args, **kwargs: snapshots)
    monkeypatch.setattr(
        protocol_change_webhooks,
        "_account_visible_principal_ids",
        lambda **kwargs: ["agent_2"],
    )
    monkeypatch.setattr(protocol_change_webhooks, "ProtocolWebhookService", FakeProtocolWebhookService)

    await protocol_change_webhooks.notify_account_status_changed_async(
        tenant_id="tenant_1",
        account_id="acc_1",
        from_status="pending_approval",
        to_status="active",
        principal_id=None,
    )

    assert [entry["url"] for entry in sent] == ["https://buyer-2.example/webhooks"]
