"""BDD scenarios for UC-003: Update Media Buy.

Integration test module — parametrized across [a2a, mcp, rest] (+ e2e_rest in
the full run). Wired scenarios use MediaBuyDualEnv (real create + update) with
real DB.
"""

from pytest_bdd import scenarios

scenarios("features/BR-UC-003-update-media-buy.feature")
