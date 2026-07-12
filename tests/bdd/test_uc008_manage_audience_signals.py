"""BDD scenario binding for UC-008: Manage Audience Signals (get_signals).

Uses pytest-bdd's ``scenarios()`` to auto-generate test functions from the
generated feature file. Step definitions are imported via conftest.py.

Wired set (salesagent-d0l4 / salesagent-8wf2): main-rest and
main-context-echo pass against the exposed get_signals surface
(salesagent-2rls); main-mcp is wired but strict-xfailed (the signal catalog
carries no value_type — see conftest _UC008_XFAIL_TAGS). activate_signal
scenarios stay dormant: the tool is deliberately unregistered until
v3.1.1-conformant (salesagent-42ap).
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/BR-UC-008-manage-audience-signals.feature")
