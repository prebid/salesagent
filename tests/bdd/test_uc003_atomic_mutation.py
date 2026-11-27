"""BDD binding for the locally-added UC-003 atomic-mutation feature.

Grades update_media_buy.mdx's transaction boundary ("One `update_media_buy` request is one
atomic mutation") on every transport: a request that changes several things is stored as a
whole, and one the ad server refuses part-way stores none of it. Retire this file together
with the local feature once the generated BR-UC-003 flow grades both (prebid/salesagent#2647).
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/local-uc003-atomic-mutation.feature")
