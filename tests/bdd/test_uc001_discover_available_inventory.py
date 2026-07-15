"""BDD scenario binding for UC-001: Discover Available Inventory (get_products).

Uses pytest-bdd's ``scenarios()`` to auto-generate test functions from the
generated feature file. Step definitions are imported via conftest.py.

Wired set (salesagent-pli8 / salesagent-8wf2): alt-anonymous, alt-empty,
alt-filtered pass on all wire transports; T-UC-001-main is wired but
strict-xfailed (production returns no relevance_score ordering or
brief_relevance — see conftest _UC001_XFAIL_TAGS). Every other scenario
stays dormant via the UC-001 fixture catch-all xfail.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/BR-UC-001-discover-available-inventory.feature")
