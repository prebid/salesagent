"""BDD scenarios for UC-010: Discover Seller Capabilities.

Binds BR-UC-010 so the POST-S10 scenarios actually execute — the feature was
dormant: no ``scenarios()`` call referenced this feature file, so the
supported_pricing_models contract had no wire-level grading at all.

Three scenarios are harnessed today — @T-UC-010-pricing (happy path),
@T-UC-010-pricing-degrade (empty surface → field absent) and
@T-UC-010-pricing-offenum (unrecognized values skipped); the remaining
scenarios xfail at the ``_harness_env`` fixture with an explicit reason rather
than spinning up a database per scenario for steps that do not exist yet (same
shape as UC-018).

Parametrized across [a2a, mcp, rest] (+ e2e_rest when BDD_E2E_ENABLED=true) via
``pytest_generate_tests`` — the wired scenarios carry no transport tag. The
degrade partitions inject their adapter surface in-process, so their e2e_rest
legs xfail via the env's ``E2EUnsupportedSetup`` declaration; the degrade
scenario's [mcp] leg is strict-xfailed against #1710 (MCP emits explicit null
for absent optionals) in the conftest collection hook.
"""

from __future__ import annotations

from pytest_bdd import scenarios

# Step definitions come from conftest's ``pytest_plugins``
# (tests.bdd.steps.domain.uc010_capabilities). All UC-010 step texts are
# exclusive to this feature file, so global registration shadows nothing.

scenarios("features/BR-UC-010-discover-seller-capabilities.feature")
