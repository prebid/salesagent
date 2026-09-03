"""BDD binding for the locally-added A2A task ownership feature (#1780).

Grades the in-memory ownership gate (#1702) at the JSON-RPC protocol altitude:
`tasks/get` and `tasks/cancel` against a task owned by another principal.
Retire together with the local feature once the upstream storyboard (adcp-req)
grows the equivalent scenarios.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/local-a2a-task-ownership.feature")
