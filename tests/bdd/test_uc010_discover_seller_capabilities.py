"""BDD scenario binding for UC-010: Discover Seller Capabilities.

This file uses pytest-bdd's ``scenarios()`` to auto-generate test functions
from the derived feature file. Step definitions are imported via conftest.py
(tests/bdd/steps/domain/uc010_version_negotiation.py + generic steps).

Wired today: the four VERSION_UNSUPPORTED version-negotiation scenarios
(#1546). The remaining UC-010 scenarios xfail fast at the harness fixture
until their steps + harness support land (mirrors UC-002/UC-018).
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/BR-UC-010-discover-seller-capabilities.feature")
