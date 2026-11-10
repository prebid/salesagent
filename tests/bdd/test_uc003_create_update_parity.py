"""BDD binding for the locally-added UC-003 create/update parity feature.

Each scenario states, on update_media_buy, work create_media_buy already does, and is
xfailed against prebid/salesagent#2483 until the two tools share one implementation.
Retire this file together with the local feature once every scenario has graduated and
the generated BR-UC-003 flow grades the same behaviour.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/local-uc003-create-update-parity.feature")
