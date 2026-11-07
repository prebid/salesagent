"""BDD binding for the locally-added GAM assignment trafficking feature.

Grades the creatives a Google Ad Manager seller places on its booked line items through
sync_creatives and the seller's creative approval (``GamSellerMixin``). Graded on a2a, mcp
and rest.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/local-uc006-gam-assignment-trafficking.feature")
