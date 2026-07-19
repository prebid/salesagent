"""Availability and proof-integrity tests for callback URL registration."""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import patch

import pytest

from src.core.exceptions import AdCPValidationError
from src.core.webhook_validator import (
    callback_url_validation_scope,
    require_valid_callback_config_urls,
    require_valid_callback_config_urls_async,
)

_CALLBACK_URL = "https://buyer.example/callback"
_EVENT_LOOP_RESPONSIVENESS_LIMIT_SECONDS = 0.5


@pytest.mark.asyncio
async def test_callback_validation_does_not_block_event_loop() -> None:
    """A stalled resolver cannot delay an unrelated coroutine."""
    release_resolver = threading.Event()

    def _blocked_validation(_url: str, *, allow_private: bool) -> tuple[bool, str]:
        del allow_private
        release_resolver.wait(timeout=1)
        return True, ""

    async def _release_from_unrelated_coroutine() -> None:
        await asyncio.sleep(0.02)
        release_resolver.set()

    loop = asyncio.get_running_loop()
    started_at = loop.time()
    with patch(
        "src.core.webhook_validator._validate_callback_url_with_policy",
        side_effect=_blocked_validation,
    ):
        await asyncio.gather(
            require_valid_callback_config_urls_async(
                push_notification_config={"url": _CALLBACK_URL},
                timeout_seconds=0.5,
            ),
            _release_from_unrelated_coroutine(),
        )

    assert loop.time() - started_at < _EVENT_LOOP_RESPONSIVENESS_LIMIT_SECONDS


@pytest.mark.asyncio
async def test_callback_validation_timeout_fails_closed_without_dns_details() -> None:
    """The caller deadline is bounded and its error is a generic wire-safe rejection."""
    release_resolver = threading.Event()
    resolver_finished = threading.Event()

    def _blocked_validation(_url: str, *, allow_private: bool) -> tuple[bool, str]:
        del allow_private
        release_resolver.wait(timeout=1)
        resolver_finished.set()
        return True, ""

    loop = asyncio.get_running_loop()
    started_at = loop.time()
    try:
        with patch(
            "src.core.webhook_validator._validate_callback_url_with_policy",
            side_effect=_blocked_validation,
        ):
            with pytest.raises(AdCPValidationError) as exc_info:
                await require_valid_callback_config_urls_async(
                    push_notification_config={"url": _CALLBACK_URL},
                    timeout_seconds=0.02,
                )
    finally:
        # The hard deadline cancels the await, not a libc getaddrinfo already
        # executing in the dedicated thread. Release it so no worker leaks.
        release_resolver.set()

    assert loop.time() - started_at < _EVENT_LOOP_RESPONSIVENESS_LIMIT_SECONDS
    assert exc_info.value.field == "push_notification_config.url"
    assert _CALLBACK_URL not in str(exc_info.value)
    assert "deadline" in str(exc_info.value).lower()
    assert await asyncio.to_thread(resolver_finished.wait, 0.2)


@pytest.mark.asyncio
async def test_no_callback_issues_proof_without_executor_work() -> None:
    """Ordinary requests do not consume resolver threads."""
    with patch("src.core.webhook_validator._CALLBACK_VALIDATION_EXECUTOR.submit") as submit:
        proof = await require_valid_callback_config_urls_async()

    require_valid_callback_config_urls(validation_proof=proof)
    submit.assert_not_called()


@pytest.mark.asyncio
async def test_callback_proof_is_value_bound_and_prevents_second_resolution() -> None:
    """A valid proof skips DNS once; mutation rejects instead of re-resolving."""
    callback_config = {"url": _CALLBACK_URL}
    with patch(
        "src.core.webhook_validator._validate_callback_url_with_policy",
        return_value=(True, ""),
    ) as validate:
        proof = await require_valid_callback_config_urls_async(
            push_notification_config=callback_config,
        )
        with callback_url_validation_scope(proof):
            require_valid_callback_config_urls(push_notification_config=callback_config)

        callback_config["url"] = "https://other.example/callback"
        with callback_url_validation_scope(proof):
            with pytest.raises(AdCPValidationError, match="changed after security validation"):
                require_valid_callback_config_urls(push_notification_config=callback_config)

    validate.assert_called_once_with(_CALLBACK_URL, allow_private=False)


@pytest.mark.asyncio
async def test_callback_resolver_bulkhead_never_runs_more_than_four_workers() -> None:
    """Timed-out callback floods cannot overflow the dedicated resolver bulkhead."""
    release_resolvers = threading.Event()
    state_lock = threading.Lock()
    active_workers = 0
    maximum_workers = 0
    completed_workers = 0

    def _blocked_validation(_url: str, *, allow_private: bool) -> tuple[bool, str]:
        nonlocal active_workers, completed_workers, maximum_workers
        del allow_private
        with state_lock:
            active_workers += 1
            maximum_workers = max(maximum_workers, active_workers)
        release_resolvers.wait(timeout=1)
        with state_lock:
            active_workers -= 1
            completed_workers += 1
        return True, ""

    async def _validate(index: int) -> object:
        try:
            return await require_valid_callback_config_urls_async(
                push_notification_config={"url": f"https://buyer{index}.example/callback"},
                timeout_seconds=0.05,
            )
        except AdCPValidationError as exc:
            return exc

    try:
        with patch(
            "src.core.webhook_validator._validate_callback_url_with_policy",
            side_effect=_blocked_validation,
        ):
            results = await asyncio.gather(*(_validate(index) for index in range(12)))
    finally:
        release_resolvers.set()

    assert all(isinstance(result, AdCPValidationError) for result in results)
    assert maximum_workers <= 4
    for _ in range(50):
        with state_lock:
            if completed_workers == maximum_workers:
                break
        await asyncio.sleep(0.01)
    assert completed_workers == maximum_workers
