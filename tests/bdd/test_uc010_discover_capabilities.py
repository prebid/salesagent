"""BDD scenarios for UC-010: Discover Seller Capabilities.

Binds BR-UC-010 so @T-UC-010-pricing (POST-S10) actually executes — it was
dormant: no ``scenarios()`` call referenced this feature file, so the
supported_pricing_models contract had no wire-level grading at all.

Only @T-UC-010-pricing is harnessed today; the remaining scenarios xfail at the
``_harness_env`` fixture with an explicit reason rather than spinning up a
database per scenario for steps that do not exist yet (same shape as UC-018).

Parametrized across [a2a, mcp, rest] (+ e2e_rest when BDD_E2E_ENABLED=true) via
``pytest_generate_tests`` — @T-UC-010-pricing carries no transport tag.
"""

from __future__ import annotations

from pytest_bdd import scenarios

# Step definitions come from conftest's ``pytest_plugins``
# (tests.bdd.steps.domain.uc010_capabilities). All four UC-010 step texts are
# exclusive to this feature file, so global registration shadows nothing.

scenarios("features/BR-UC-010-discover-seller-capabilities.feature")
