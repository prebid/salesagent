"""BDD scenario binding for UC-002 NFR enforcement (restructured).

Restructured NFR scenarios that test enforcement via negative-path Given
steps instead of dispatch-inside-Then. See the feature file header for
the rationale.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/BR-UC-002-nfr-enforcement.feature")
