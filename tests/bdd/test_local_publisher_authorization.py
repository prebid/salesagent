"""BDD binding for the locally-added publisher-authorization feature.

Grades that a publisher's adagents.json entry naming this agent's origin, or an MCP or A2A
endpoint it serves, authorizes it in each admin action that reads the file, and that an
entry naming another scheme, port, path or host does not (url-canonicalization.mdx).

Local rather than a BR-* storyboard: no storyboard grades a seller reading a publisher's
file about itself. Graded on both admin transports, admin_integration and e2e_admin.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/local-publisher-authorization.feature")
