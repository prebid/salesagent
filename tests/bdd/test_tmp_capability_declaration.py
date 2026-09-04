"""BDD binding for the locally-added TMP capability-declaration feature.

Grades the obligation this PR creates by emitting ``experimental_features`` for
the first time — a seller declares ``trusted_match.core`` exactly when it has a
provider the advertised surfaces actually serve — once, across every transport
the harness fans out to (a2a/mcp/rest, plus e2e_rest when the live stack is
enabled).

Replaces the hand-written ``@parametrize("transport", [MCP, A2A, REST])`` that
lived in ``tests/integration/test_tmp_provider_integration.py``, which rolled its
own envelope extraction and structurally could not include e2e_rest (#1197
review).

Retire together with the local feature once the upstream storyboard (adcp-req)
grows a scenario for a seller's own experimental declaration.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/local-tmp-capability-declaration.feature")
