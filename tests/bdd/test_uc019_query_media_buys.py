"""BDD scenarios for UC-019: Query Media Buys.

Integration test module — parametrized across [a2a, mcp, rest] (+ e2e_rest in
the full run). Wired scenarios use MediaBuyLifecycleEnv (create/update/get
composite) with real DB; the REST binding is POST /api/v1/media-buys/query.
"""

from pytest_bdd import scenarios

scenarios("features/BR-UC-019-query-media-buys.feature")
