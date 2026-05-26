import asyncio
from unittest.mock import MagicMock, patch

import pytest

from src.core.tracing import traced


def test_traced_passes_through_sync_result():
    @traced
    def my_impl(identity=None):
        return "result"

    assert my_impl() == "result"


def test_traced_passes_through_async_result():
    @traced
    async def my_impl(identity=None):
        return "async_result"

    result = asyncio.run(my_impl())
    assert result == "async_result"


def test_traced_reraises_exception():
    @traced
    def my_impl(identity=None):
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        my_impl()


def test_traced_reraises_async_exception():
    @traced
    async def my_impl(identity=None):
        raise ValueError("async boom")

    with pytest.raises(ValueError, match="async boom"):
        asyncio.run(my_impl())


def test_traced_span_name_strips_impl_suffix():
    recorded_names = []

    mock_span = MagicMock()
    mock_span.__enter__ = lambda s: s
    mock_span.__exit__ = MagicMock(return_value=False)

    mock_tracer = MagicMock()
    mock_tracer.start_as_current_span = lambda name, **kw: (recorded_names.append(name) or mock_span)

    with patch("src.core.tracing.get_tracer", return_value=mock_tracer):
        with patch("src.core.tracing.is_tracing_enabled", return_value=True):

            @traced
            def _create_media_buy_impl(identity=None):
                return "ok"

            _create_media_buy_impl()

    assert recorded_names == ["create_media_buy"]


def test_traced_is_noop_when_tracing_disabled():
    call_count = {"n": 0}

    with patch("src.core.tracing.is_tracing_enabled", return_value=False):

        @traced
        def my_impl(identity=None):
            call_count["n"] += 1
            return "result"

        result = my_impl()

    assert result == "result"
    assert call_count["n"] == 1
