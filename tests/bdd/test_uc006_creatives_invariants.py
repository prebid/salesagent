"""BDD scenario binding for the UC-006 creatives-invariants companion.

Hand-authored companion to BR-UC-006 (salesagent-j49n / PR1399 R3-F2):
encodes the success-variant invariant that an all-failed sync_creatives still
returns the success variant carrying a creatives array (per-item action='failed'),
never the error variant. Step definitions are imported via conftest.py
(tests.bdd.steps.domain.uc006_sync_creatives).
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/BR-UC-006-creatives-invariants.feature")
