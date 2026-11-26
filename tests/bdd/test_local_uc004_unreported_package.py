"""BDD binding for the locally-added package-terms delivery feature.

Grades the terms get_media_buy_delivery prices a package at: the terms stored with it, and
a refusal when it has none. Graded on a2a, mcp, rest and e2e_rest.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/local-uc004-unreported-package.feature")
