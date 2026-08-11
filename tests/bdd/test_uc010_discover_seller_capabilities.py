"""BDD scenario binding for UC-010: Discover Seller Capabilities.

Uses pytest-bdd's ``scenarios()`` to auto-generate test functions
from the compiled feature file. Step definitions are imported via conftest.py.

Wiring is gated per tag (#1592):
- Wired tags run for real via the wired-tags gate in conftest
  ``_harness_env``; scenarios failing on production gaps carry explicit
  ``_XFAIL_TAGS`` / ``_SELECTIVE_XFAIL`` entries.
- Tags not yet wired xfail fast at the gate; unbound steps auto-xfail via
  the ``StepDefinitionNotFoundError`` hook.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/BR-UC-010-discover-seller-capabilities.feature")
