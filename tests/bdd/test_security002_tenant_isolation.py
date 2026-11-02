"""BDD tests for BR-SECURITY-002: a credential reaches exactly one tenant's data.

Transport parametrization is handled by conftest.py (a2a, mcp, rest, e2e_rest), which is
the point: tenant and principal resolution is one decision made in one place, so every
transport must reach the same answer or the collapse this feature grades did not happen.
"""

from pytest_bdd import scenarios

scenarios("features/BR-SECURITY-002-tenant-isolation.feature")
