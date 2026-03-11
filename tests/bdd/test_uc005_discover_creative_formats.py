"""BDD scenario binding for UC-005: Discover Creative Formats.

This file uses pytest-bdd's ``scenarios()`` to auto-generate test functions
from the compiled feature file. Step definitions are imported via conftest.py.

Phase 0: all steps are stubs. Scenarios run and pass using in-memory ctx
state without calling production code.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/BR-UC-005-discover-creative-formats.feature")
