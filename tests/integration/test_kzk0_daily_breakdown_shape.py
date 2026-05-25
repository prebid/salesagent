"""Regression test for include_package_daily_breakdown shape differential (salesagent-kzk0, gates xzky).

The owner-flagged invariant: when the buyer requests
``include_package_daily_breakdown=True``, the response's per-package
``daily_breakdown`` field MUST be populated. Today
``src/core/tools/media_buy_delivery.py:479`` hard-codes
``daily_breakdown=None`` regardless of the flag (debt-doc C5), so all 6
valid T-UC-004-{partition,boundary}-daily-breakdown rows pass *vacuously*
— the Then-step never sees the shape differential. No existing test
demonstrates this gap.

Pattern: this is the failing-test gate for the production fix
(`salesagent-kzk0`). Wrapped in ``xfail(strict=True)``:
- ``--runxfail`` shows the test fail today (proving the gap)
- normal run xfails clean (no suite redness)
- when ``kzk0`` lands and packages carry real ``daily_breakdown`` arrays,
  the test xpasses -> strict-fail -> forces marker removal.
"""

import pytest


@pytest.mark.requires_db
@pytest.mark.xfail(
    strict=True,
    reason=(
        "salesagent-kzk0: include_package_daily_breakdown is a no-op — "
        "src/core/tools/media_buy_delivery.py:479 hard-codes daily_breakdown=None "
        "regardless of the flag. Lands green when kzk0 populates per-package buckets."
    ),
)
def test_include_package_daily_breakdown_must_populate_daily_breakdown_field(integration_db):
    """With the flag True, at least one returned package must carry a non-None ``daily_breakdown``.

    Today every PackageDelivery.daily_breakdown is hard-coded None, so the
    assertion below fails for the genuine kzk0 reason — not a vacuous
    truthiness check.
    """
    from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory
    from tests.harness import DeliveryPollEnv

    with DeliveryPollEnv(tenant_id="t1", principal_id="p1") as env:
        tenant = TenantFactory(tenant_id="t1")
        principal = PrincipalFactory(tenant=tenant, principal_id="p1")
        buy = MediaBuyFactory(tenant=tenant, principal=principal, status="active")
        env.set_adapter_response(buy.media_buy_id, impressions=5000)

        response = env.call_impl(
            media_buy_ids=[buy.media_buy_id],
            include_package_daily_breakdown=True,
        )

    deliveries = getattr(response, "media_buy_deliveries", None) or []
    assert deliveries, "expected at least one delivery in the response"

    packages = [pkg for d in deliveries for pkg in (getattr(d, "packages", None) or [])]
    assert packages, "expected at least one package in the delivery"

    populated = [p for p in packages if getattr(p, "daily_breakdown", None) is not None]
    assert populated, (
        "kzk0 invariant: with include_package_daily_breakdown=True, daily_breakdown must be "
        "populated on every package — got None on all "
        f"{len(packages)} packages (hard-coded at media_buy_delivery.py:479)."
    )
