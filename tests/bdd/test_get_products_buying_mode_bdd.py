"""BDD scenario binding for the get_products buying_mode three-mode contract.

Loads the dedicated buying_mode feature (brief / wholesale / refine + cross-mode
validation). Step definitions live in
tests/bdd/steps/domain/uc_get_products_buying_mode.py; the @buying_mode tag selects
ProductEnv via conftest's _harness_env.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/BR-UC-GET-PRODUCTS-buying-mode.feature")
