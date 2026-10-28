"""BDD scenario binding for get_products and the seller's own AI configuration.

Binds only the local feature below. The generated UC-001 discovery feature (no step
definitions) stays unbound and on the allowlist in
``tests/unit/test_architecture_feature_file_bound.py``; binding it here would collect
scenarios that all xfail as "step definition not found", which reads as coverage and is
not. Name it here and that guard counts it as bound.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/BR-UC-001-ai-ranking-tenant-config.feature")
