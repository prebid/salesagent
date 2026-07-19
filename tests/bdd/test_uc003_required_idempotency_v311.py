"""BDD binding for the hand-authored AdCP 3.1.1 UC-003 key requirement."""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/local-uc003-required-idempotency-v311.feature")
