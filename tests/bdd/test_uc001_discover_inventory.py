"""BDD scenario binding for UC-001 product discovery (brief / wholesale / refine modes).

Loads scenarios from BR-UC-001-discover-available-inventory.feature. Step definitions
live in tests/bdd/steps/domain/uc_get_products_buying_mode.py and
tests/bdd/steps/domain/uc_get_products_inventory.py.

Behavioral obligations:
    Covers: BR-UC-001-MAIN-BRIEF-MODE-01
    Covers: BR-UC-001-ALT-WHOLESALE-MODE-01
    Covers: BR-UC-001-ALT-REFINE-MODE-01
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/BR-UC-001-discover-available-inventory.feature")
