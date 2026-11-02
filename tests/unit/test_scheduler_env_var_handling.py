"""The two schedulers read their interval off the settings when they start.

DELIVERY_WEBHOOK_INTERVAL and MEDIA_BUY_STATUS_CHECK_INTERVAL are read by the settings
loader (src/core/config.py); an empty value is the default (a compose file that sets
``DELIVERY_WEBHOOK_INTERVAL=""`` once crashed the process on ``int('')``). The scheduler
snapshots nothing at import: it reads ``settings.limits`` in ``start()``, so an environment
change followed by ``load_settings()`` is seen by the next start.
"""

from __future__ import annotations

import pytest

from src.core.config import load_settings
from src.services.delivery_webhook_scheduler import DeliveryWebhookScheduler
from src.services.media_buy_status_scheduler import MediaBuyStatusScheduler


async def _noop() -> None:
    """Stands in for the scheduler loop so start() reads its settings and creates no work."""


async def _interval_read_at_start(scheduler, attribute: str, monkeypatch: pytest.MonkeyPatch) -> int:
    monkeypatch.setattr(scheduler, "_run_scheduler", _noop)
    await scheduler.start()
    try:
        return getattr(scheduler, attribute)
    finally:
        await scheduler.stop()


@pytest.mark.parametrize(
    ("make_scheduler", "attribute", "variable", "default"),
    [
        (DeliveryWebhookScheduler, "_sleep_interval_seconds", "DELIVERY_WEBHOOK_INTERVAL", 3600),
        (MediaBuyStatusScheduler, "_check_interval_seconds", "MEDIA_BUY_STATUS_CHECK_INTERVAL", 60),
    ],
    ids=["delivery_webhook", "media_buy_status"],
)
class TestSchedulerIntervalFromSettings:
    async def test_default_when_not_set(self, make_scheduler, attribute, variable, default, monkeypatch):
        monkeypatch.delenv(variable, raising=False)
        load_settings()

        assert await _interval_read_at_start(make_scheduler(), attribute, monkeypatch) == default

    async def test_default_when_empty_string(self, make_scheduler, attribute, variable, default, monkeypatch):
        """Regression: an empty value is unset, not a crash on int('')."""
        monkeypatch.setenv(variable, "")
        load_settings()

        assert await _interval_read_at_start(make_scheduler(), attribute, monkeypatch) == default

    async def test_custom_value_is_read(self, make_scheduler, attribute, variable, default, monkeypatch):
        monkeypatch.setenv(variable, str(default * 2))
        load_settings()

        assert await _interval_read_at_start(make_scheduler(), attribute, monkeypatch) == default * 2

    async def test_change_is_seen_by_the_next_start(self, make_scheduler, attribute, variable, default, monkeypatch):
        """Nothing is snapshotted at import: the same scheduler sees a reloaded value."""
        monkeypatch.setenv(variable, str(default * 2))
        load_settings()
        scheduler = make_scheduler()
        assert await _interval_read_at_start(scheduler, attribute, monkeypatch) == default * 2

        monkeypatch.setenv(variable, str(default * 3))
        load_settings()

        assert await _interval_read_at_start(scheduler, attribute, monkeypatch) == default * 3
