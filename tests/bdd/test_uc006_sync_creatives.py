"""BDD scenario binding for UC-006: Sync Creatives.

Uses pytest-bdd's ``scenarios()`` to auto-generate test functions
from the compiled feature file. Step definitions are imported via conftest.py.

Account resolution scenarios are the initial focus (salesagent-71q).
Remaining scenarios are @pending until step definitions are implemented.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/BR-UC-006-sync-creatives.feature")
