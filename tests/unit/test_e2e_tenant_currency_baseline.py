"""Regression guard for the e2e tenant currency baseline.

PR #1420 review finding (salesagent-2ad1): ``_reset_e2e_db`` TRUNCATEs every
table per e2e_rest scenario, wiping the init_db bootstrap CurrencyLimit rows
(USD/EUR/GBP). The per-scenario harness recreates only what ``TenantFactory``
provides — and that is USD ONLY. No e2e_rest scenario depends on the wiped
EUR/GBP today (the EUR scenarios in BR-UC-002/008/017/019/023 are xfail or
unwired as e2e_rest; GBP is unused), and the bootstrap ``ci-test-token`` is not
a dependency either (the harness self-seeds identity via PrincipalFactory).

This pins the USD-only baseline: when those EUR scenarios are wired for e2e_rest
(salesagent-jdy1), whoever adds EUR seeding to the e2e tenant setup will trip
this assertion and update the tracking tickets, so the gap closes visibly
rather than surfacing as a confusing currency-validation failure.
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
        "If EUR/GBP were added to close the e2e_rest seeding gap, update "
        "salesagent-2ad1 / salesagent-jdy1 and this guard."
    )
