"""BDD scenario binding for UC-002: Create Media Buy.

Uses pytest-bdd's ``scenarios()`` to auto-generate test functions
from the compiled feature file. Step definitions are imported via conftest.py.

Account resolution scenarios are the initial focus (salesagent-2rq).
Remaining scenarios are @pending until step definitions are implemented.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/BR-UC-002-create-media-buy.feature")
