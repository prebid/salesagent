"""BDD tests for BR-COMPAT-001: deprecated field normalization.

Scenarios verify that deprecated AdCP field names are translated to
current equivalents consistently. Transport parametrization is handled
by conftest.py (impl, a2a, mcp, rest).
"""

from pytest_bdd import scenarios

scenarios("features/BR-COMPAT-001-deprecated-field-normalization.feature")
