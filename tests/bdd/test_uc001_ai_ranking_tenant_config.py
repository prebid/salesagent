"""BDD scenario binding for BR-RULE-005 AI ranking and the seller's own AI config.

Binds only the local ranking feature. The generated
``features/BR-UC-001-discover-available-inventory.feature`` (121 scenarios, no step
definitions) stays unbound — binding it here would collect 121 scenarios that all
xfail as "step definition not found", which reads as coverage and is not.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/BR-UC-001-ai-ranking-tenant-config.feature")
