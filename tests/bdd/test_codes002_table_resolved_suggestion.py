"""BDD tests for BR-CODES-002: a suggestion is resolved from the code table.

Transport parametrization is handled by conftest.py (a2a, mcp, rest, e2e_rest).
"""

from pytest_bdd import scenarios

scenarios("features/BR-CODES-002-table-resolved-suggestion.feature")
