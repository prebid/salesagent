"""Meta-tests for DeliveryPollEnv (unit variant) — verifies the harness contract.

These tests ensure the unit harness itself works correctly. They run in ``make quality``
but have no ``Covers:`` tags — they test infrastructure, not obligations.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from src.core.schemas import GetMediaBuyDeliveryResponse
from tests.harness.delivery_poll_unit import DeliveryPollEnv


class TestDeliveryPollEnvContract:
    """Contract tests for DeliveryPollEnv (unit variant)."""

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

    def test_unregistered_media_buy_id_produces_error(self):
        """Adapter mock must fail for unregistered media_buy_ids, not silently succeed.

        Previously the mock would return the first registered response for any ID,
        masking bugs. Now it raises KeyError which production code catches and
        reports as an error entry.
        """
        with DeliveryPollEnv() as env:
            env.add_buy(media_buy_id="mb_registered")
            env.set_adapter_response("mb_registered", impressions=5000)

            # Query for a DIFFERENT ID that was never registered
            env.add_buy(media_buy_id="mb_unregistered")
            response = env.call_impl(media_buy_ids=["mb_unregistered"])

            # The unregistered ID should produce an error, not a delivery
            assert len(response.errors) >= 1
            assert any("mb_unregistered" in e.message for e in response.errors)

    def test_multi_package_adapter_response(self):
        """set_adapter_response with packages= builds multi-package response."""
        with DeliveryPollEnv() as env:
            env.add_buy(
                media_buy_id="mb_multi",
                raw_request={
                    "buyer_ref": "ref_multi",
                    "packages": [
                        {"package_id": "pkg_A", "product_id": "prod_001"},
                        {"package_id": "pkg_B", "product_id": "prod_002"},
                    ],
                },
            )
            env.set_adapter_response(
                "mb_multi",
                packages=[
                    {"package_id": "pkg_A", "impressions": 10000, "spend": 500.0},
                    {"package_id": "pkg_B", "impressions": 5000, "spend": 250.0},
                ],
            )

            response = env.call_impl(media_buy_ids=["mb_multi"])

            assert len(response.media_buy_deliveries) == 1
            delivery = response.media_buy_deliveries[0]
            # Totals should be sum of packages
            assert delivery.totals.impressions == 15000.0
            assert delivery.totals.spend == 750.0
            # by_package should have 2 entries
            assert len(delivery.by_package) == 2
            pkg_ids = {p.package_id for p in delivery.by_package}
            assert pkg_ids == {"pkg_A", "pkg_B"}

    def test_set_adapter_response_rejects_negative_impressions(self):
        """set_adapter_response should reject negative impressions via Pydantic."""
        from pydantic import ValidationError

        with DeliveryPollEnv() as env:
            import pytest

            with pytest.raises(ValidationError):
                env.set_adapter_response("mb_001", impressions=-100)

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
