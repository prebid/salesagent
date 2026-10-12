"""BDD binding for the locally-added Google Ad Manager buy read-back feature.

Grades get_media_buy_delivery for a buy a Google Ad Manager seller just created: each
package under the id create_media_buy returned, with its own line item's delivery, at its
own product's rate. The real GoogleAdManager adapter runs, with only Google's SOAP client
and report download stood in. Graded on a2a, mcp and rest.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/local-gam-buy-readback.feature")
