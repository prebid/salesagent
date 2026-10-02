"""BDD binding for the locally-added tenant-identification feature.

Grades that both of this deployment's identification routes — the Host and the
``x-adcp-tenant`` header — resolve the same tenant, and that a host nobody serves
resolves none.

Local rather than a BR-* storyboard on purpose: the pinned spec does not say how a
buyer selects a seller, only that a request is addressed to an agent's URL. How a
deployment maps a host to a tenant is the seller's own concern, so what is graded
here is this seller's documented order (``_detect_tenant``) and its buyer-visible
consequence, not a protocol obligation.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/local-tenant-identification-routes.feature")
