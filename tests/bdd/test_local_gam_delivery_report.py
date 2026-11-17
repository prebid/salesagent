"""BDD binding for the locally-added Google Ad Manager delivery report feature.

Grades a buyer's get_media_buy_delivery for a GAM seller's buy while GAM has not finished
reporting the period, with the real GoogleAdManager adapter and only Google's SOAP client
and report download stood in (``DeliveryPollEnv.report_through_gam``). Graded on a2a, mcp
and rest.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/local-gam-delivery-report.feature")
