"""Native A2A receipts are purged; durable operations use AdCP polling."""

from pytest_bdd import scenarios

scenarios("features/local-a2a-task-ownership.feature")
