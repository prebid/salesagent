"""BDD scenarios for UC-019: Query Media Buys.

Integration test module — parametrized across [a2a, mcp] (no REST endpoint for
this UC). Uses MediaBuyListEnv with real DB.
"""

from pytest_bdd import scenarios

# Register UC-019 step definitions LOCALLY (module scope) rather than globally via
# conftest's pytest_plugins. The uc019 module intentionally redefines 8 generic
# step texts (sandbox / validation-error assertions); a global registration would
# override the generic versions for every other UC. Importing here keeps those
# overrides scoped to UC-019 scenarios only — other UCs keep the generic steps.
from tests.bdd.steps.domain.uc019_query_media_buys import *  # noqa: F401,F403,E402

scenarios("features/BR-UC-019-query-media-buys.feature")
