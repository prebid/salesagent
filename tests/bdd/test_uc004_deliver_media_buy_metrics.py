"""BDD scenario binding for UC-004: Deliver Media Buy Metrics.

This file uses pytest-bdd's ``scenarios()`` to auto-generate test functions
from the compiled feature file. Step definitions are imported via conftest.py.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/BR-UC-004-deliver-media-buy-metrics.feature")
