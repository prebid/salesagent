"""BDD binding for the locally-added Google Ad Manager creative-kinds feature.

Grades the GAM creative a GAM seller creates for each creative a buyer names on
create_media_buy, and its size (``GamSellerMixin.sell_through_gam``). Graded on a2a, mcp
and rest.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/local-gam-creative-kinds.feature")
