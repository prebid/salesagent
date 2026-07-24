"""Tests for shared idempotency-key request defaults."""

from tests.harness._idempotency import (
    OMIT_IDEMPOTENCY_KEY,
    ensure_idempotency_key,
    fresh_idempotency_key,
)


def test_fresh_idempotency_key_uses_default_and_custom_prefixes() -> None:
    default_key = fresh_idempotency_key()
    diagnostic_key = fresh_idempotency_key("integration-key")

    assert default_key.startswith("test-key-")
    assert diagnostic_key.startswith("integration-key-")
    assert default_key != fresh_idempotency_key()


def test_ensure_idempotency_key_preserves_explicit_control() -> None:
    explicit = {"idempotency_key": "stable-replay-key"}
    omitted = {"idempotency_key": OMIT_IDEMPOTENCY_KEY}

    assert ensure_idempotency_key(explicit) == {"idempotency_key": "stable-replay-key"}
    assert ensure_idempotency_key(omitted) == {}
    assert ensure_idempotency_key({})["idempotency_key"].startswith("test-key-")
