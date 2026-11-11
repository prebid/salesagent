"""BDD scenario binding for the publishers a product names in publisher_properties (#1845).

Scenarios store real authorized properties and products and read what a buyer receives:
one publisher_properties entry per publisher, never the seller's own agent host.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/BR-UC-GET-PRODUCTS-publisher-domain.feature")
