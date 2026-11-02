"""BDD tests for BR-PROTOCOL-001: inbound AdCP version negotiation, on every tool.

Transport parametrization is handled by conftest.py (a2a, mcp, rest, e2e_rest).
The scenarios that widen the seller's advertised release set are in-process only —
``set_supported_versions`` patches process-wide constants and declares itself
``e2e_unsupported``.
"""

from pytest_bdd import scenarios

scenarios("features/BR-PROTOCOL-001-version-negotiation.feature")
