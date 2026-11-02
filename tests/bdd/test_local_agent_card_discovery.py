"""BDD binding for the locally-added agent-card discovery feature.

Grades that the card publishes the host its tenant declares — read from the row, never
derived from the request — and that a fetch naming no tenant is refused rather than
answered with a card built from the caller's own Host.

Local rather than a BR-* storyboard on purpose: the pinned spec does not say how a
deployment maps a host to a tenant, only that a request is addressed to an agent's URL.
What the A2A specification fixes is the canonical discovery path, which these use.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/local-agent-card-discovery.feature")
