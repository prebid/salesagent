"""Raise-site coverage for the mock adapter's business-outcome taxonomy classes.

``test_typed_error_wire_codes.py`` pins each class -> wire-code mapping by
constructing the exception directly. This module drives the production
``MockAdServer`` create paths so the actual ``raise`` fires: a class-swap at
the site (e.g. AdCPMediaBuyRejectedError -> AdCPError) would go unnoticed by
the mapping test but fails here.

The internal ``error_code`` asserted on each class is the taxonomy code carried
as class identity (MEDIA_BUY_REJECTED / INVENTORY_UNAVAILABLE); the boundary
collapses those to POLICY_VIOLATION / PRODUCT_UNAVAILABLE on the wire, pinned
separately in test_typed_error_wire_codes.py.
"""

from datetime import UTC, datetime, timedelta

import pytest

from src.adapters.mock_ad_server import MockAdServer
from src.core.schemas import CreateMediaBuyRequest, FormatId, MediaPackage, PackageRequest, Principal


def _make_request() -> CreateMediaBuyRequest:
    """A valid create request that passes the mock's GAM-like validation."""
    start_time = datetime.now(UTC)
    end_time = start_time + timedelta(days=30)
    return CreateMediaBuyRequest(
        brand={"domain": "example.com"},
        idempotency_key="unit-test-key-mockraise-0001",
        start_time=start_time,
        end_time=end_time,
        packages=[
            PackageRequest(
                product_id="prod_test",
                budget=5000.0,
                pricing_option_id="test_pricing",
                format_ids=[FormatId(agent_url="https://creative.test", id="display_300x250")],
            )
        ],
    )


def _make_packages() -> list[MediaPackage]:
    return [
        MediaPackage(
            package_id="pkg_test",
            name="Test Package",
            delivery_type="guaranteed",
            cpm=5.0,
            impressions=10000,
            format_ids=[FormatId(agent_url="https://creative.test", id="display_300x250")],
            product_id="prod_test",
        )
    ]


class TestMockMediaBuyRejectedRaiseSites:
    """Production raise site for AdCPMediaBuyRejectedError (MEDIA_BUY_REJECTED).

    Two raise sites exist in mock_ad_server.py. The keyword-scenario site
    (``[REJECT:...]`` in brand.domain) is unreachable through a schema-validated
    CreateMediaBuyRequest under adcp 4.3.0 — the library ``BrandReference.domain``
    enforces a strict domain pattern that rejects bracket characters, so a valid
    request can never carry the keyword. The sync-mode approval-rejection site
    below is the reachable one and is what guards the class identity.
    """

    def test_sync_mode_simulated_rejection_raises_rejected_error(self):
        """When sync-mode approval simulation rejects, the sync create path raises."""
        from src.core.exceptions import AdCPMediaBuyRejectedError

        # HITL config: sync mode, zero delay, approval simulation forced to reject.
        principal = Principal(
            principal_id="principal_test",
            name="Test Principal",
            platform_mappings={
                "mock": {
                    "hitl_config": {
                        "enabled": True,
                        "mode": "sync",
                        "sync_settings": {"delay_ms": 0, "streaming_updates": False},
                        "approval_simulation": {
                            "enabled": True,
                            "approval_probability": 0.0,  # always reject
                            "rejection_reasons": ["Budget exceeds limits"],
                        },
                    }
                }
            },
        )
        adapter = MockAdServer(config={}, principal=principal, tenant_id="tenant_test")

        request = _make_request()
        start_time = datetime.now(UTC)
        end_time = start_time + timedelta(days=30)

        with pytest.raises(AdCPMediaBuyRejectedError) as exc_info:
            adapter.create_media_buy(
                request=request,
                packages=_make_packages(),
                start_time=start_time,
                end_time=end_time,
            )

        assert exc_info.value.error_code == "MEDIA_BUY_REJECTED"


class TestMockBudgetExhaustedRaiseSite:
    """Production raise site for AdCPBudgetExhaustedError (BUDGET_EXHAUSTED)."""

    def test_simulation_force_budget_exceeded_raises_budget_exhausted_error(self):
        """A simulation strategy with ``force_budget_exceeded`` drives the
        immediate-create raise site (mock_ad_server.py:758).

        Mirrors the inventory-unavailable sibling below: both raises live in the
        same simulation force-error block, with the budget check evaluated first.
        """
        from src.core.database.models import Strategy as StrategyModel
        from src.core.exceptions import AdCPBudgetExhaustedError
        from src.core.strategy import StrategyContext

        # In-memory simulation strategy: is_simulation + sim_ prefix + force flag.
        # should_force_error("budget_exceeded") reads config["force_budget_exceeded"].
        strategy_model = StrategyModel(
            strategy_id="sim_test_budget",
            tenant_id="tenant_test",
            name="sim budget",
            config={"force_budget_exceeded": True},
            is_simulation=True,
        )
        strategy_context = StrategyContext(strategy_model)

        principal = Principal(principal_id="principal_test", name="Test Principal", platform_mappings={})
        adapter = MockAdServer(
            config={},
            principal=principal,
            tenant_id="tenant_test",
            strategy_context=strategy_context,
        )

        request = _make_request()
        start_time = datetime.now(UTC)
        end_time = start_time + timedelta(days=30)

        with pytest.raises(AdCPBudgetExhaustedError) as exc_info:
            adapter.create_media_buy(
                request=request,
                packages=_make_packages(),
                start_time=start_time,
                end_time=end_time,
            )

        assert exc_info.value.error_code == "BUDGET_EXHAUSTED"


class TestMockInventoryUnavailableRaiseSite:
    """Production raise site for AdCPInventoryUnavailableError (INVENTORY_UNAVAILABLE)."""

    def test_simulation_force_inventory_unavailable_raises_inventory_error(self):
        """A simulation strategy with ``force_inventory_unavailable`` drives the
        immediate-create raise site."""
        from src.core.database.models import Strategy as StrategyModel
        from src.core.exceptions import AdCPInventoryUnavailableError
        from src.core.strategy import StrategyContext

        # In-memory simulation strategy: is_simulation + sim_ prefix + force flag.
        strategy_model = StrategyModel(
            strategy_id="sim_test_inventory",
            tenant_id="tenant_test",
            name="sim inventory",
            config={"force_inventory_unavailable": True},
            is_simulation=True,
        )
        strategy_context = StrategyContext(strategy_model)

        principal = Principal(principal_id="principal_test", name="Test Principal", platform_mappings={})
        adapter = MockAdServer(
            config={},
            principal=principal,
            tenant_id="tenant_test",
            strategy_context=strategy_context,
        )

        request = _make_request()
        start_time = datetime.now(UTC)
        end_time = start_time + timedelta(days=30)

        with pytest.raises(AdCPInventoryUnavailableError) as exc_info:
            adapter.create_media_buy(
                request=request,
                packages=_make_packages(),
                start_time=start_time,
                end_time=end_time,
            )

        assert exc_info.value.error_code == "INVENTORY_UNAVAILABLE"
