"""Global admission and deadline guarantees for legacy reporting webhooks."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from src.core.bounded_executor import SyncThreadPoolBulkhead
from src.core.database.repositories.push_notification_config import PushNotificationTarget
from src.services import webhook_delivery_service as delivery_module
from src.services.webhook_delivery_service import WebhookDeliveryService


def _target(index: int) -> PushNotificationTarget:
    return PushNotificationTarget(
        url=f"https://buyer-{index}.example/callback",
        authentication_type=None,
        authentication_token=None,
        webhook_secret=None,
        auth_blocked_at=None,
    )


def test_legacy_delivery_deadline_retains_global_capacity_until_work_finishes(monkeypatch) -> None:
    """Timed-out simulator callers cannot replace four blocked deliveries."""
    capacity = delivery_module.WEBHOOK_DELIVERY_MAX_WORKERS
    caller_count = capacity * 3
    bulkhead = SyncThreadPoolBulkhead(
        max_workers=capacity,
        thread_name_prefix="legacy-webhook-oracle",
    )
    release_workers = threading.Event()
    four_started = threading.Event()
    all_finished = threading.Event()
    state_lock = threading.Lock()
    started = 0
    active = 0
    maximum_active = 0

    def _blocked_delivery(
        self: WebhookDeliveryService,
        endpoint_key: str,
        circuit_breaker: object,
        queue: object,
    ) -> bool:
        nonlocal active, maximum_active, started
        del self, endpoint_key, circuit_breaker, queue
        with state_lock:
            started += 1
            active += 1
            maximum_active = max(maximum_active, active)
            if started == capacity:
                four_started.set()
        release_workers.wait(timeout=2)
        with state_lock:
            active -= 1
            if active == 0:
                all_finished.set()
        return True

    monkeypatch.setattr(delivery_module, "_LEGACY_WEBHOOK_DELIVERY_BULKHEAD", bulkhead)
    monkeypatch.setattr(delivery_module, "WEBHOOK_DELIVERY_DEADLINE_SECONDS", 0.05)
    monkeypatch.setattr(WebhookDeliveryService, "_deliver_with_backoff", _blocked_delivery)

    services = [WebhookDeliveryService() for _ in range(caller_count + 1)]
    try:
        with ThreadPoolExecutor(max_workers=caller_count) as callers:
            calls = [
                callers.submit(service._queue_and_deliver_target, f"tenant-{index}", _target(index), {"n": index})
                for index, service in enumerate(services[:caller_count])
            ]
            assert four_started.wait(timeout=0.5)

            # All caller deadlines expire while the four underlying workers
            # remain blocked. Eight excess calls time out at admission.
            assert [call.result(timeout=0.5) for call in calls] == [False] * caller_count

        # A later service instance shares the same exhausted process-wide cap;
        # it cannot submit a replacement worker after earlier callers returned.
        probe_service = services[caller_count]
        assert (
            probe_service._queue_and_deliver_target(
                "tenant-probe",
                _target(caller_count),
                {"n": caller_count},
            )
            is False
        )
        with state_lock:
            assert started == capacity
            assert active == capacity
            assert maximum_active == capacity

        release_workers.set()
        assert all_finished.wait(timeout=0.5)

        # Completion callbacks return all permits, so the same global bulkhead
        # accepts new work after the abandoned underlying operations finish.
        assert (
            probe_service._queue_and_deliver_target(
                "tenant-recovered",
                _target(caller_count + 1),
                {"n": caller_count + 1},
            )
            is True
        )
        with state_lock:
            assert started == capacity + 1
            assert active == 0
            assert maximum_active == capacity
    finally:
        release_workers.set()
        bulkhead._executor.shutdown(wait=True)
