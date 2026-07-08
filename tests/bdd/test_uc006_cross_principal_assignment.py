"""BDD binding for the locally-added UC-006 cross-principal assignment feature.

Covers the ungraded surface behind the cross-principal FK-500/leak bug
(PR #1430 review): `assignments` referencing another principal's creative_id.
Retire together with the local feature once the upstream storyboard
(adcp-req) grows the equivalent scenario (salesagent-t4or).
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/local-uc006-cross-principal-assignment.feature")
