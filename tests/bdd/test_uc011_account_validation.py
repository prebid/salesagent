"""BDD scenario binding for the UC-011 account-validation companion.

Hand-authored companion to BR-UC-011 (salesagent-fbdb / PR1399 R3-F1):
encodes the brandless-entry rejection obligation that the upstream LLM
derivation cannot see (an absent required field). Step definitions are
imported via conftest.py (tests.bdd.steps.domain.uc011_accounts).
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/BR-UC-011-account-validation.feature")
