"""BDD binding for the locally-added Google Ad Manager geo targeting feature.

Grades the geo targeting a GAM seller books on the line item for a package's
targeting_overlay, and the geo it refuses before it creates anything in GAM, with the
real GoogleAdManager adapter and only Google's SOAP client stood in
(``GamSellerMixin.sell_through_gam``). Graded on a2a, mcp and rest.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/local-gam-geo-targeting.feature")
