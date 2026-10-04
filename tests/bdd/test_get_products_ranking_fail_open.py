"""BDD scenario binding for AI product ranking failing open (#2334).

The scenario configures a tenant for AI ranking against a provider that cannot be
reached and reads the products a buyer receives.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/BR-UC-GET-PRODUCTS-ranking-fail-open.feature")
