"""Canonical payload hashing for idempotency replay-conflict detection.

The AdCP idempotency contract (the ``replay_ttl_seconds`` capability) defines
payload equivalence as RFC 8785 JSON Canonicalization Scheme over the request
with a CLOSED exclusion list: two requests carrying the same ``idempotency_key``
and the same canonical hash are replays of each other; the same key with a
different canonical hash is an ``IDEMPOTENCY_CONFLICT``.

The exclusion list mirrors the AdCP spec (``security.mdx#idempotency``) — protocol
metadata and routing/auth fields whose presence must not force a new resource on
replay. Kept salesagent-native (``rfc8785`` directly) rather than importing the
SDK's ``adcp.server.idempotency`` module, which carries the return-based replay
store this project deliberately does not adopt. Byte-parity with the SDK hasher
(and exclusion-set equality) is pinned by ``TestSdkEquivalencePin`` in
``tests/unit/test_idempotency_canonical.py``; adopting the SDK helpers outright
is deferred to the SDK v5 bump, at which point this module and the ``rfc8785``
pin go away.
"""

from __future__ import annotations

import copy
import hashlib
from typing import Any

import rfc8785

from src.core.exceptions import AdCPValidationError

# Top-level request fields excluded from the canonical hash (spec closed list):
# idempotency_key is the identifier itself; context / governance_context are
# correlation metadata that must not change the resource on replay.
_EXCLUDED_FIELDS: frozenset[str] = frozenset({"idempotency_key", "context", "governance_context"})

# Nested dotted paths excluded from the canonical hash. A rotated webhook
# credential must not be read as a different payload.
_NESTED_EXCLUSIONS: tuple[tuple[str, ...], ...] = (("push_notification_config", "authentication", "credentials"),)


def _drop_nested(obj: dict[str, Any], path: tuple[str, ...]) -> None:
    """Remove the leaf key of ``path`` from ``obj``, walking nested dicts; missing keys are a no-op."""
    cursor: Any = obj
    for key in path[:-1]:
        if not isinstance(cursor, dict) or key not in cursor:
            return
        cursor = cursor[key]
    if isinstance(cursor, dict):
        cursor.pop(path[-1], None)


def strip_excluded_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a deep copy of ``payload`` with the spec's excluded fields removed.

    The input dict is never mutated. Missing keys (top-level or nested) are a
    no-op — the payload is free to omit them.
    """
    out: dict[str, Any] = copy.deepcopy(payload)
    for key in _EXCLUDED_FIELDS:
        out.pop(key, None)
    for path in _NESTED_EXCLUSIONS:
        _drop_nested(out, path)
    return out


def canonical_payload_hash(payload: dict[str, Any]) -> str:
    """Return the SHA-256 hex digest of the RFC 8785-canonicalized payload.

    Excluded fields (:data:`_EXCLUDED_FIELDS` / :data:`_NESTED_EXCLUSIONS`) are
    stripped first. The digest is stable across key ordering and equivalent
    number/string encodings, so two requests differing only in field order (or
    in excluded fields) hash equal — the equivalence test for replay vs conflict.
    """
    try:
        stripped = strip_excluded_fields(payload)
        canonical = rfc8785.dumps(stripped)
    except RecursionError as exc:
        # A pathologically nested payload must reject as a buyer error, not
        # crash the boundary with an unhandled RecursionError.
        raise AdCPValidationError("request payload too deeply nested to canonicalize for idempotency") from exc
    return hashlib.sha256(canonical).hexdigest()


def canonical_request_hash(request: Any) -> str:
    """Canonical hash of a Pydantic request model.

    Thin wrapper over :func:`canonical_payload_hash` that performs the
    ``model_dump(mode="json")`` here, so transport-agnostic ``_impl`` bodies never
    call ``.model_dump()`` directly (the no-model-dump-in-impl architecture guard).
    """
    return canonical_payload_hash(request.model_dump(mode="json"))
