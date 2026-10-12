"""BDD binding for the locally-added Google Ad Manager line item flight feature.

Grades the flight a GAM seller writes on the line item it books for a buyer's
create_media_buy: the buyer's instants in the network's own time zone, and an "asap" start
as an immediate one; and a refused zone lookup, which leaves nothing in GAM and reaches the
buyer as a classified fault. The real GoogleAdManager adapter runs, with only Google's SOAP client
stood in (``GamSellerMixin.sell_through_gam``). Graded on a2a, mcp and rest.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/local-gam-line-item-flight.feature")
