"""BDD scenarios for UC-019: Query Media Buys.

Integration test module — parametrized across [impl, a2a, mcp].
Uses MediaBuyListEnv with real DB. No REST endpoint for this UC.
"""

from pytest_bdd import scenarios

scenarios("features/BR-UC-019-query-media-buys.feature")
