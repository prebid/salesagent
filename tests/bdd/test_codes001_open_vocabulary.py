"""BDD tests for BR-CODES-001: a declared error code reaches the buyer unrewritten.

Transport parametrization is handled by conftest.py (a2a, mcp, rest, e2e_rest).
"""

from pytest_bdd import scenarios

scenarios("features/BR-CODES-001-open-vocabulary-wire-codes.feature")
