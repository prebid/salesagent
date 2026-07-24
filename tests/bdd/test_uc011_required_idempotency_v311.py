"""BDD binding for the hand-authored AdCP 3.1.1 UC-011 key requirement."""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/local-uc011-required-idempotency-v311.feature")
