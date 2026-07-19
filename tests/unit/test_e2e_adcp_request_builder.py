"""Regression coverage for ordinary E2E mutating-request defaults."""

from collections.abc import Callable
from typing import Any

import pytest

from src.core.schemas._base import validate_idempotency_key_shape
from tests.e2e.adcp_request_builder import (
    build_adcp_media_buy_request,
    build_sync_creatives_request,
    build_update_media_buy_request,
)


@pytest.mark.parametrize(
    "request_factory",
    [
        pytest.param(
            lambda: build_adcp_media_buy_request(
                product_ids=["product-1"],
                total_budget=100.0,
                start_time="2030-01-01T00:00:00Z",
                end_time="2030-01-02T00:00:00Z",
            ),
            id="create-media-buy",
        ),
        pytest.param(
            lambda: build_sync_creatives_request(creatives=[]),
            id="sync-creatives",
        ),
        pytest.param(
            lambda: build_update_media_buy_request(media_buy_id="media-buy-1"),
            id="update-media-buy",
        ),
    ],
)
def test_mutating_request_builder_supplies_fresh_valid_idempotency_key(
    request_factory: Callable[[], dict[str, Any]],
) -> None:
    """Ordinary E2E mutations must never accidentally omit the required key."""
    first_key = request_factory()["idempotency_key"]
    second_key = request_factory()["idempotency_key"]

    validate_idempotency_key_shape(first_key, allow_none=False)
    assert second_key != first_key
