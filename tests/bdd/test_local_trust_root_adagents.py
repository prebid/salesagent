"""BDD binding for the locally-added adagents.json publication feature.

Grades that the adagents.json served at a tenant's own host exists only when that host
owns an authorized property, that it then claims the properties on that host and no
others, and that it validates against the pinned 3.1.1 schema.

Local rather than a BR-* storyboard: no 3.1.1 compliance yaml fetches the seller's own
adagents.json, and which host serves which document is this deployment's routing.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/local-trust-root-adagents.feature")
