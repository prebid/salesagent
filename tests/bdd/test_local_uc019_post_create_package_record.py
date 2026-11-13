"""BDD binding for the locally-added UC-019 package-record feature.

Grades that get_media_buys reports the product, budget and targeting the buyer booked for a
package, after a real create_media_buy on the same env (``@post-create-poll`` routes it to
``MediaBuyCreateListEnv``).
"""

from __future__ import annotations

from pytest_bdd import scenarios

# The UC-019 steps are registered per module, not through conftest (see
# test_uc019_query_media_buys.py for why); this feature uses them, so it imports them the same way.
from tests.bdd.steps.domain.uc019_query_media_buys import *  # noqa: F401,F403,E402

scenarios("features/local-uc019-post-create-package-record.feature")
