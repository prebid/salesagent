"""BDD binding for the locally-added UC-002 top-level unknown-field feature.

Grades the Pattern #7 extra-field policy (GH #1442) at the TOP-LEVEL request
body across wire transports — the generated BR-UC-002 unknown-field partitions
cover nested objects only. See the feature file header for the spec citation
(v3.1.1 additionalProperties: true) and the MCP owner decision.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/local-uc002-unknown-top-level-field.feature")
