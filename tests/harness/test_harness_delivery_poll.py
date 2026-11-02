"""Meta-tests for DeliveryPollEnv (unit variant) — verifies the harness contract.

These tests ensure the unit harness itself works correctly. They run in ``make quality``
but have no ``Covers:`` tags — they test infrastructure, not obligations.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from src.core.schemas import GetMediaBuyDeliveryResponse
from tests.factories.media_buy import pricing_options_named, request_package
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

    def test_default_env_reports_an_active_buy_as_active(self):
        """An env that says nothing about the circuit breaker runs with it CLOSED.

        Pins the default in ``_configure_mocks``. ``_is_circuit_breaker_open`` is patched,
        and an unconfigured MagicMock returns a TRUTHY Mock — so without the default every
        test in this env polls with the breaker OPEN and reads "reporting_delayed" for a
        serving buy. Nothing else grades that: a test about degraded reporting sets the
        state itself (``set_circuit_open``), which is exactly what makes it blind to what
        the unset default does.
        """
        with DeliveryPollEnv() as env:
            env.add_buy(media_buy_id="mb_default", status="active")
            env.set_adapter_response("mb_default", impressions=5000)

            response = env.call_impl(media_buy_ids=["mb_default"])

            assert response.media_buy_deliveries[0].status == "active"

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
                env.add_buy(media_buy_id=mb_id)
                env.set_adapter_response(mb_id, impressions=1000 * (i + 1))

            response = env.call_impl(media_buy_ids=["mb_000", "mb_001", "mb_002"])

            assert len(response.media_buy_deliveries) == 3

    def test_adapter_error_produces_error_response(self):
        """set_adapter_error causes _impl to return an error entry in errors list."""
        with DeliveryPollEnv() as env:
            env.add_buy(media_buy_id="mb_err")
            env.set_adapter_error(Exception("Adapter unavailable"))

            response = env.call_impl(media_buy_ids=["mb_err"])

            # When adapter fails, _impl returns a valid response with an error entry.
            # WHICH buy failed travels in details, never in the message: message is
            # derived from the code via CODE_TABLE (salesagent-3dawm), so it reads
            # "Seller service is temporarily unavailable" for every failing buy.
            assert isinstance(response, GetMediaBuyDeliveryResponse)
            assert len(response.errors) >= 1
            assert any(
                e.code == "SERVICE_UNAVAILABLE" and (e.details or {}).get("media_buy_id") == "mb_err"
                for e in response.errors
            ), f"expected a SERVICE_UNAVAILABLE entry naming mb_err in details, got {response.errors!r}"

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
            # No "principal" patch: the principal comes off the identity the env builds,
            # so there is no principal lookup for the env to stand in for.
            assert "adapter" in env.mock
            assert "pricing" in env.mock

    def test_pricing_options(self):
        """set_pricing_options makes pricing data available to _impl.

        The option states its TERMS and the key is read off the row's stored
        ``pricing_option_id`` column, so the key the lookup answers under is the one the
        package below names. An id passed by hand could be one no reader resolves while
        the fixture answered for it anyway — which is how a package naming an
        unresolvable option went green.
        """
        with DeliveryPollEnv() as env:
            env.set_pricing_options(pricing_options_named(pricing_model="cpm", rate="5.00"))

            env.add_buy(
                media_buy_id="mb_001",
                raw_request={"packages": [request_package(0)]},
            )
            env.set_adapter_response("mb_001", impressions=5000)

            response = env.call_impl(media_buy_ids=["mb_001"])
            assert isinstance(response, GetMediaBuyDeliveryResponse)
            # The configured option reached the wire, not merely the lookup: asserting only
            # that env.mock["pricing"] was CALLED passed even while a MagicMock option
            # produced a MagicMock currency and the buy was dropped into errors[].
            pkg = response.media_buy_deliveries[0].by_package[0]
            assert (pkg.rate, pkg.currency) == (5.0, "USD")

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

            # The unregistered ID should produce an error, not a delivery. The id is
            # in details, not the message — see the note above.
            #
            # SERVICE_UNAVAILABLE, not MEDIA_BUY_NOT_FOUND: add_buy() DID persist
            # this buy, so it is not missing from the DB. What is unregistered is the
            # harness's ADAPTER RESPONSE, so the mock adapter raises and production
            # takes its adapter-failure path (media_buy_delivery.py:362).
            assert len(response.errors) >= 1
            assert any(
                e.code == "SERVICE_UNAVAILABLE" and (e.details or {}).get("media_buy_id") == "mb_unregistered"
                for e in response.errors
            ), f"expected a SERVICE_UNAVAILABLE entry naming mb_unregistered in details, got {response.errors!r}"

    def test_multi_package_adapter_response(self):
        """set_adapter_response with packages= builds multi-package response."""
        with DeliveryPollEnv() as env:
            env.add_buy(
                media_buy_id="mb_multi",
                # Each package names its pricing option: the delivery report states
                # pricing_model/rate/currency per package, and refuses to guess. The
                # request factory puts one on every package, because the accepted
                # ``PackageRequest`` model requires it.
                raw_request={
                    "packages": [
                        request_package(0, package_id="pkg_A"),
                        request_package(1, package_id="pkg_B"),
                    ]
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

    def test_call_impl_accepts_identity_kwarg(self):
        """call_impl must handle identity kwarg from dispatcher without error.

        When dispatched via call_impl directly, identity is injected
        as a kwarg. call_impl must pop it before building the request (identity
        is not a GetMediaBuyDeliveryRequest field) and pass it to _impl separately.
        """
        from tests.factories.principal import PrincipalFactory

        with DeliveryPollEnv() as env:
            env.add_buy(media_buy_id="mb_001")
            env.set_adapter_response("mb_001", impressions=5000)

            # Simulate what call_via does: inject identity as kwarg
            custom_identity = PrincipalFactory.make_identity(
                principal_id="custom_agent",
                tenant_id="test_tenant",
            )
            response = env.call_impl(
                media_buy_ids=["mb_001"],
                identity=custom_identity,
            )

            assert isinstance(response, GetMediaBuyDeliveryResponse)
            assert len(response.media_buy_deliveries) >= 1

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
