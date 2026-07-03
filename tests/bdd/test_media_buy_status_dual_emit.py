"""BDD scenario binding for AdCP 3.1 media_buy_status dual-emit.

Local (hand-authored) feature — verifies that create_media_buy and
update_media_buy success responses carry the preferred DOMAIN ``media_buy_status``
(a MediaBuyStatus enum value) alongside a top-level protocol ``status`` that is a
PROTOCOL TaskStatus — DIFFERENT namespaces, NOT identical. This is the target GA
model graded by the 3.1.0-rc.12 storyboard; it diverges from the pinned SDK's
beta.3 storyboard, which graded the two identical during the deprecation window
(#4908). See docs/adcp-spec-version.md "Behavior target vs SDK pin".

Step definitions come from the registered plugins in conftest.py (UC-002 create,
UC-003 update, and the generic given/when steps).
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/BR-UC-002-media-buy-status-dual-emit.feature")
