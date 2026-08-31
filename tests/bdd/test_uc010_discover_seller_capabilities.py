"""BDD scenario binding for UC-010: the get_adcp_capabilities honesty graders.

Binds the compiled BR-UC-010 feature via pytest-bdd's ``scenarios()`` (the whole-feature
binding the CI shard manifest requires — ``scripts/ci/shard_split.py`` counts scenarios off
this call). The WIRED graders (account-sandbox, specialisms, idempotency-required, no-tenant
minimal-degradation) execute against CapabilitiesEnv across a2a/mcp/rest; every OTHER BR-UC-010
scenario has no step definitions and auto-xfails on the undefined-step path — there is NO
conftest complement/routing gate for UC-010 (it was removed), so binding the whole feature does
not un-dormant it. Mirrors the UC-030 bind-all pattern (``test_uc030_manage_governance.py``).

Step definitions come from ``tests.bdd.steps.domain.uc010_capabilities`` (+ the shared generic
Givens). #1329 (UC-010).
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/BR-UC-010-discover-seller-capabilities.feature")
