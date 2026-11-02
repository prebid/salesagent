"""Raise-site coverage for the mock adapter's business-outcome taxonomy classes.

``test_typed_error_wire_codes.py`` pins each class -> wire-code mapping by
constructing the exception directly. This module drives the production
``MockAdServer`` create paths so the actual ``raise`` fires: a class-swap at
the site (e.g. AdCPMediaBuyRejectedError -> AdCPSalesAgentError) would go unnoticed by
the mapping test but fails here.

The internal ``error_code`` asserted on each class is the taxonomy code carried
as class identity (MEDIA_BUY_REJECTED / INVENTORY_UNAVAILABLE); the boundary
collapses those to POLICY_VIOLATION / PRODUCT_UNAVAILABLE on the wire, pinned
separately in test_typed_error_wire_codes.py.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from src.adapters.base import AdapterCreateRequest
from src.adapters.mock_ad_server import MockAdServer
from src.core.schemas import FormatId, MediaPackage, Principal


def _make_request() -> AdapterCreateRequest:
    """What the create path hands an adapter, passing the mock's GAM-like validation.

    The adapters take the carrier, not the buyer's ``CreateMediaBuyRequest``: the DTO
    this used to build reached the mock's budget check as a null total, because the
    summing the tool does for the carrier is not a field on a request.
    """
    return AdapterCreateRequest(brand={"domain": "example.com"}, total_budget=Decimal("5000.00"))


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
    CreateMediaBuyRequest under the pinned adcp SDK — the library ``BrandReference.domain``
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


# (Retired) TestMockBudgetExhaustedRaiseSite and TestMockInventoryUnavailableRaiseSite
# drove the mock adapter's simulation force-error block: an in-memory ``StrategyContext``
# built from a ``sim_``-prefixed Strategy row whose config carried force_budget_exceeded /
# force_inventory_unavailable. ``src/core/strategy.py`` is deleted and the adapter takes no
# strategy context (commit a1b79d22d) -- there is no force block left, and the mock adapter
# raises neither AdCPBudgetExhaustedError nor AdCPProductUnavailableError any more.
#
# The raise-site guard is satisfied without them: no adapter raises
# AdCPBudgetExhaustedError at all, and AdCPProductUnavailableError is raised by the GAM
# adapter, where tests/unit/test_gam_workflow_packages.py drives the real raise.
