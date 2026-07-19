"""Availability guarantees for outbound webhook DNS and delivery workers."""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
import requests

from src.core.security.webhook_http import _resolve_delivery_target, post_webhook_status_async

_URL = "https://buyer.example/callback"


@pytest.mark.asyncio
async def test_async_delivery_timeout_retains_worker_capacity_until_work_finishes() -> None:
    """Timed-out deliveries cannot refill the worker pool while old work runs."""
    release_workers = threading.Event()
    state_lock = threading.Lock()
    started = 0
    active = 0
    maximum_active = 0

    def _blocked_post(*args: object, **kwargs: object) -> int:
        nonlocal active, maximum_active, started
        del args, kwargs
        with state_lock:
            started += 1
            active += 1
            maximum_active = max(maximum_active, active)
        release_workers.wait(timeout=1)
        with state_lock:
            active -= 1
        return 204

    async def _deliver() -> int | requests.Timeout:
        try:
            return await post_webhook_status_async(
                requests.Session(),
                _URL,
                body=b"{}",
                headers={},
                timeout=0.5,
                deadline_seconds=0.05,
            )
        except requests.Timeout as exc:
            return exc

    try:
        with patch("src.core.security.webhook_http.post_webhook_status", side_effect=_blocked_post):
            results = await asyncio.gather(*(_deliver() for _ in range(12)))
            assert all(isinstance(result, requests.Timeout) for result in results)

            # All four underlying calls are still blocked. A new caller times
            # out at admission instead of placing a fifth item in a worker queue.
            probe = await _deliver()
            assert isinstance(probe, requests.Timeout)
            with state_lock:
                assert started == 4
                assert maximum_active == 4

            release_workers.set()
            recovered = await post_webhook_status_async(
                requests.Session(),
                _URL,
                body=b"{}",
                headers={},
                timeout=0.5,
                deadline_seconds=0.5,
            )
    finally:
        release_workers.set()

    assert recovered == 204
    with state_lock:
        assert started == 5
        assert maximum_active == 4


def test_delivery_dns_timeout_retains_capacity_until_resolver_finishes() -> None:
    """Slow DNS has a deadline and cannot grow a hidden resolver queue."""
    release_resolvers = threading.Event()
    four_started = threading.Event()
    state_lock = threading.Lock()
    started = 0

    def _blocked_resolver(
        url: str,
        *,
        require_https: bool,
        allow_private: bool,
    ) -> tuple[str | None, str]:
        nonlocal started
        del url, require_https, allow_private
        with state_lock:
            started += 1
            if started == 4:
                four_started.set()
        release_resolvers.wait(timeout=1)
        return "93.184.216.34", ""

    def _resolve() -> tuple[str | None, str] | requests.Timeout:
        try:
            return _resolve_delivery_target(_URL, require_https=True, allow_private=False)
        except requests.Timeout as exc:
            return exc

    try:
        with (
            patch("src.core.security.webhook_http.WEBHOOK_DNS_TIMEOUT_SECONDS", 0.1),
            patch(
                "src.core.security.webhook_http.resolve_and_validate_target",
                side_effect=_blocked_resolver,
            ),
            ThreadPoolExecutor(max_workers=4) as callers,
        ):
            initial = [callers.submit(_resolve) for _ in range(4)]
            assert four_started.wait(timeout=0.5)
            assert all(isinstance(future.result(timeout=0.5), requests.Timeout) for future in initial)

            # Completion callbacks, not caller timeouts, own the four permits.
            assert isinstance(_resolve(), requests.Timeout)
            with state_lock:
                assert started == 4

            release_resolvers.set()
            assert _resolve() == ("93.184.216.34", "")
    finally:
        release_resolvers.set()

    with state_lock:
        assert started == 5
