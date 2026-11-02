"""BDD tests for BR-SECURITY-001: wire error safety for untyped exceptions.

Transport parametrization is handled by conftest.py (a2a, mcp, rest, e2e_rest).
"""

from pytest_bdd import scenarios

scenarios("features/BR-SECURITY-001-wire-error-safety.feature")
