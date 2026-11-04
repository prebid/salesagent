"""Native A2A task access is scoped to the creator's tenant and principal."""

from pytest_bdd import scenarios

scenarios("features/local-a2a-task-ownership.feature")
