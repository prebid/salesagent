"""Meta-tests for DeliveryPollEnv — verifies the harness contract.

These tests ensure the harness itself works correctly. They run in ``make quality``
but have no ``Covers:`` tags — they test infrastructure, not obligations.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from src.core.schemas import GetMediaBuyDeliveryResponse
from tests.harness.delivery_poll import DeliveryPollEnv


class TestDeliveryPollEnvContract:
    """Contract tests for DeliveryPollEnv."""

    def test_default_env_returns_valid_response(self):
        """call_impl with one buy returns a GetMediaBuyDeliveryResponse."""
        with DeliveryPollEnv() as env:
            env.add_buy(media_buy_id="mb_001")
            env.set_adapter_response("mb_001", impressions=5000)
            response = env.call_impl(media_buy_ids=["mb_001"])

            assert isinstance(response, GetMediaBuyDeliveryResponse)

    def test_add_buy_visible_to_impl(self):
        """A buy added via add_buy appears in media_buy_deliveries."""
        with DeliveryPollEnv() as env:
            env.add_buy(media_buy_id="mb_visible")
            env.set_adapter_response("mb_visible", impressions=1000)
            response = env.call_impl(media_buy_ids=["mb_visible"])

            assert len(response.media_buy_deliveries) >= 1
            mb_ids = [d.media_buy_id for d in response.media_buy_deliveries]
            assert "mb_visible" in mb_ids

    def test_multiple_buys(self):
        """Adding 3 buys + 3 adapter responses returns 3 deliveries."""
        with DeliveryPollEnv() as env:
            for i in range(3):
                mb_id = f"mb_{i:03d}"
                env.add_buy(media_buy_id=mb_id, buyer_ref=f"ref_{i}")
                env.set_adapter_response(mb_id, impressions=1000 * (i + 1))

            response = env.call_impl(media_buy_ids=["mb_000", "mb_001", "mb_002"])

            assert len(response.media_buy_deliveries) == 3

    def test_adapter_error_produces_error_response(self):
        """set_adapter_error causes _impl to return an error entry in errors list."""
        with DeliveryPollEnv() as env:
            env.add_buy(media_buy_id="mb_err")
            env.set_adapter_error(Exception("Adapter unavailable"))

            response = env.call_impl(media_buy_ids=["mb_err"])

            # When adapter fails, _impl returns a valid response with an error entry
            assert isinstance(response, GetMediaBuyDeliveryResponse)
            assert len(response.errors) >= 1
            assert any("mb_err" in e.message for e in response.errors)

    def test_custom_identity_flows_through(self):
        """principal_id override reaches the mock principal lookup."""
        with DeliveryPollEnv(principal_id="custom_p1") as env:
            env.add_buy(media_buy_id="mb_001")
            env.set_adapter_response("mb_001")

            # The identity should use our custom principal_id
            assert env.identity.principal_id == "custom_p1"

            response = env.call_impl(media_buy_ids=["mb_001"])
            assert isinstance(response, GetMediaBuyDeliveryResponse)

    def test_mock_access(self):
        """env.mock[name] provides access to all patch targets."""
        with DeliveryPollEnv() as env:
            assert "uow" in env.mock
            assert "principal" in env.mock
            assert "adapter" in env.mock
            assert "pricing" in env.mock

    def test_pricing_options(self):
        """set_pricing_options makes pricing data available to _impl."""
        with DeliveryPollEnv() as env:
            from unittest.mock import MagicMock

            mock_pricing = MagicMock()
            mock_pricing.pricing_model = "cpm"
            mock_pricing.rate = 5.0
            env.set_pricing_options({"1": mock_pricing})

            env.add_buy(
                media_buy_id="mb_001",
                raw_request={
                    "buyer_ref": "ref_001",
                    "packages": [{"package_id": "pkg_001", "product_id": "prod_001", "pricing_option_id": "1"}],
                },
            )
            env.set_adapter_response("mb_001", impressions=5000)

            response = env.call_impl(media_buy_ids=["mb_001"])
            assert isinstance(response, GetMediaBuyDeliveryResponse)
            # Pricing mock was called
            env.mock["pricing"].assert_called()

    def test_custom_date_range(self):
        """start_date/end_date parameters flow through to the request."""
        with DeliveryPollEnv() as env:
            env.add_buy(
                media_buy_id="mb_001",
                start_date=date(2026, 3, 1),
                end_date=date(2026, 3, 7),
            )
            env.set_adapter_response("mb_001")

            response = env.call_impl(
                media_buy_ids=["mb_001"],
                start_date="2026-03-01",
                end_date="2026-03-07",
            )

            assert response.reporting_period.start == datetime(2026, 3, 1, tzinfo=UTC)
            assert response.reporting_period.end == datetime(2026, 3, 7, tzinfo=UTC)
