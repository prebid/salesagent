"""BDD scenario binding for BR-ADMIN-INVENTORY-PROFILE-publishers (#1845).

The operator's inventory-profile form and products page, driven through
``AdminInventoryProfileEnv`` on both admin transports. Step definitions are in
tests/bdd/steps/domain/admin_inventory_profiles.py.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/BR-ADMIN-INVENTORY-PROFILE-publishers.feature")
