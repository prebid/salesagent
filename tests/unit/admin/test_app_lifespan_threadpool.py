"""Unit tests for L0-31 — AnyIO threadpool limiter bump in ``app_lifespan``.

Canonical spec: ``flask-to-fastapi-foundation-modules.md`` §11.14.F.

Under Decision 1 (Path B — sync adapters wrapped in ``run_in_threadpool``),
FastAPI's sync ``def`` admin handlers run in the AnyIO threadpool; the
default is 40 tokens (a 41st concurrent request blocks before the handler
even starts). Admin OAuth bursts + adapter ``run_in_threadpool`` wraps
can push concurrency past 40 comfortably. We raise to 80 by default and
expose an ``ADCP_THREADPOOL_TOKENS`` env-var override.

Obligations:
  1. After ``app_lifespan`` starts up, the default limiter reports
     ``total_tokens == 80`` (canonical default).
  2. ``ADCP_THREADPOOL_TOKENS`` env-var override is honored — setting
     it to ``"120"`` pre-startup produces ``total_tokens == 120``.
  3. The env-var name is ``ADCP_THREADPOOL_TOKENS`` (canonical per
     §11.14.F); the older draft name ``ADCP_THREADPOOL_SIZE`` is NOT
     read (no silent back-compat).
"""

from __future__ import annotations

import anyio.to_thread
import pytest

from src.app import app_lifespan


async def _enter_lifespan_and_read_tokens() -> int:
    """Drive ``app_lifespan`` through startup and capture ``total_tokens``.

    The lifespan is an async context manager keyed on the FastAPI app.
    The limiter we read is the AnyIO-managed default thread limiter
    attached to the current task group.
    """
    from src.app import app

    async with app_lifespan(app):
        return anyio.to_thread.current_default_thread_limiter().total_tokens


@pytest.mark.asyncio
async def test_default_threadpool_tokens_is_80(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default ``ADCP_THREADPOOL_TOKENS`` raises limiter to 80."""
    monkeypatch.delenv("ADCP_THREADPOOL_TOKENS", raising=False)

    tokens = await _enter_lifespan_and_read_tokens()

    assert tokens == 80


@pytest.mark.asyncio
async def test_threadpool_tokens_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """``ADCP_THREADPOOL_TOKENS=120`` raises limiter to 120."""
    monkeypatch.setenv("ADCP_THREADPOOL_TOKENS", "120")

    tokens = await _enter_lifespan_and_read_tokens()

    assert tokens == 120


@pytest.mark.asyncio
async def test_deprecated_env_var_name_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """The deprecated draft ``ADCP_THREADPOOL_SIZE`` must NOT be read.

    Canonical env name is ``ADCP_THREADPOOL_TOKENS`` (§11.14.F). Silent
    back-compat on the older name would mask ops configs that intended
    to tune tokens but spelled the var wrong.
    """
    monkeypatch.delenv("ADCP_THREADPOOL_TOKENS", raising=False)
    monkeypatch.setenv("ADCP_THREADPOOL_SIZE", "200")

    tokens = await _enter_lifespan_and_read_tokens()

    assert tokens == 80
