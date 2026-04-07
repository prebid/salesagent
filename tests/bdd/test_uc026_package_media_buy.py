"""BDD scenarios for UC-026: Package Media Buy.

Integration test module — parametrized across [impl, a2a, mcp, rest].
Uses MediaBuyCreateEnv (package ops go through create_media_buy).
"""

from pytest_bdd import scenarios

scenarios("features/BR-UC-026-package-media-buy.feature")
