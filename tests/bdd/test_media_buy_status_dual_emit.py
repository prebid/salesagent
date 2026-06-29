"""BDD scenario binding for AdCP 3.1 media_buy_status dual-emit.

Local (hand-authored) feature — verifies that create_media_buy and
update_media_buy success responses carry both the preferred ``media_buy_status``
and the deprecated ``status`` with identical MediaBuyStatus values
(adcp==5.7.0 CreateMediaBuySuccessResponse / UpdateMediaBuySuccessResponse).

Step definitions come from the registered plugins in conftest.py (UC-002 create,
UC-003 update, and the generic given/when steps).
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/BR-UC-002-media-buy-status-dual-emit.feature")
