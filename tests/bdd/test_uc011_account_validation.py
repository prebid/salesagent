"""BDD scenario binding for the hand-authored UC-011 validation companion.

The companion covers the brandless-entry obligation from PR1399 R3-F1 and
the ungraded AUTH-before-VERSION cross-transport policy from PR #1546.
Step definitions are imported through ``tests.bdd.conftest``.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/BR-UC-011-account-validation.feature")
