"""BDD scenario binding for pricing options announced through get_products.

Scenarios store real ``PricingOption`` rows and read what a buyer receives, covering
all nine AdCP pricing models plus the rows the seller cannot announce at all.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/BR-UC-GET-PRODUCTS-pricing-options.feature")
