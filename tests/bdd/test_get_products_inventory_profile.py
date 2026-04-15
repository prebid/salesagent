"""BDD scenario binding for product discovery with inventory profiles (#1162).

Scenarios test that Product.effective_properties infers selection_type
when inventory profile publisher_properties lack the discriminator.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/BR-UC-GET-PRODUCTS-inventory-profile.feature")
