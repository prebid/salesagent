"""Regression guard for the e2e tenant currency baseline (PR #1420 review).

Background: ``_reset_e2e_db`` TRUNCATEs every table per e2e_rest scenario, wiping
the init_db bootstrap CurrencyLimit rows (USD/EUR/GBP). The per-scenario harness
recreates only what ``TenantFactory`` provides — and that is USD ONLY. No
e2e_rest scenario depends on the wiped EUR/GBP today (the EUR scenarios in
BR-UC-002/008/017/019/023 are xfail or unwired as e2e_rest; GBP is unused), and
the bootstrap ``ci-test-token`` is not a dependency either (the harness
self-seeds identity via PrincipalFactory).

Scope — this is a CI-visible PROXY, not a test of the reset path itself: it
asserts the ``TenantFactory`` invariant (USD-only auto-currency), which is the
*source* the harness re-seeds from after the truncate. The runtime reset path
needs the live Docker stack, so we guard its re-seed source instead. When the
EUR scenarios are wired for e2e_rest, whoever adds EUR seeding trips this
assertion, so the gap closes visibly rather than as a confusing
currency-validation failure. See PR #1420 for the seeding follow-up.
"""

from tests.factories.core import CurrencyLimitFactory, TenantFactory


def test_tenant_factory_auto_currency_is_usd_only():
    # The currency RelatedFactory TenantFactory auto-creates defaults to USD.
    assert CurrencyLimitFactory.currency_code == "USD"

    # And it is the ONLY auto-created currency — no EUR/GBP RelatedFactory.
    post_declarations = list(TenantFactory._meta.post_declarations.as_dict())
    currency_declarations = [name for name in post_declarations if "currency" in name]
    assert currency_declarations == ["currency_usd"], (
        f"TenantFactory auto-currency declarations changed: {currency_declarations}. "
        "If EUR/GBP were added to close the e2e_rest seeding gap, update this guard "
        "and the e2e seeding follow-up (PR #1420)."
    )
