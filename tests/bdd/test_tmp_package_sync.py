"""BDD binding for the locally-added TMP package-sync feature.

Grades the one buyer-triggered obligation the feature owns — a registered
provider holds current package data after a create and after an update — once,
across every transport the harness fans out to (a2a/mcp/rest, plus e2e_rest when
the live stack is enabled). Replaces the per-tier observables described in
``tests/harness/_mixins.TMPSyncMixin``.

Retire together with the local feature once the upstream storyboard (adcp-req)
grows the equivalent scenario.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/local-tmp-package-sync.feature")
