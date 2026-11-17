"""BDD binding for the locally-added initial-status feature.

Grades the status a buy starts in when create_media_buy assigns it creatives that are
already approved: on the create response and on get_media_buys, through one create and
one read on the same env and identity. Graded on a2a, mcp, rest and e2e_rest, each through
the real Mock adapter, so the auto-approve scenarios also grade its creative upload (#2420).
"""

from __future__ import annotations

from pytest_bdd import scenarios

# The get_media_buys poll and its Then live in the UC-019 step module, which is registered
# per test module rather than globally (test_uc019_query_media_buys.py says why). None of
# its generic-step overrides is a step this feature uses.
from tests.bdd.steps.domain.uc019_query_media_buys import *  # noqa: F401,F403,E402

scenarios("features/local-uc002-initial-status.feature")
