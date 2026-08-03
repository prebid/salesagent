"""BDD scenario binding for UC-001: Discover Available Inventory (get_products).

Uses pytest-bdd's ``scenarios()`` to auto-generate test functions from the
generated feature file. Step definitions are imported via conftest.py.

Wired set (salesagent-cfrg, port-minus of salesagent-pli8 / salesagent-8wf2):
alt-empty, alt-filtered pass on all wire transports. T-UC-001-main (#1595)
and T-UC-001-alt-anonymous (#1591) are not wired on this slice. Every other
scenario stays dormant via the UC-001 fixture catch-all xfail.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/BR-UC-001-discover-available-inventory.feature")
