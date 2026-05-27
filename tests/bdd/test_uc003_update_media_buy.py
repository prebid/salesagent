"""BDD scenarios for UC-003: Update Media Buy.

Integration test module — parametrized across [impl, a2a, mcp, rest].
Uses MediaBuyUpdateEnv with real DB.
"""

from pytest_bdd import scenarios

scenarios("features/BR-UC-003-update-media-buy.feature")
