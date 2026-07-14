"""BDD scenarios for UC-003: Update Media Buy.

Integration test module — parametrized across [impl, a2a, mcp, rest].
Extension/error and targeting-overlay scenarios route through MediaBuyDualEnv
with real DB (conftest harness dispatch); the rest xfail until wired.
"""

from pytest_bdd import scenarios

scenarios("features/BR-UC-003-update-media-buy.feature")
