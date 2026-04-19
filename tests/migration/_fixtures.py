"""Shared helpers for L0-02 AdCP-boundary protective tests.

These tests freeze baselines of the public AdCP wire contracts
(OpenAPI schema, MCP tool inventory, A2A agent card, REST responses,
JSON Schema discovery, AdCPError response shape) so L1+ refactors can
detect drift before shipping.

Known pre-existing nondeterminism (tracked here, not fixed in L0):
  * ``/a2a/`` is registered with three methods pointing at a single
    ``a2a_trailing_slash_redirect`` coroutine (``src/app.py``). FastAPI
    emits a duplicate-operation-id warning and the "winning"
    ``operationId`` alternates (``_get`` / ``_post`` / ``_options``)
    between processes. ``normalize_openapi_for_hashing`` strips the
    volatile suffix so the hash is stable.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

_A2A_REDIRECT_OP_STABLE = "a2a_trailing_slash_redirect"


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize ``value`` to JSON bytes with sorted keys and no whitespace."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    """Lowercase hex SHA-256 of ``data``."""
    return hashlib.sha256(data).hexdigest()


def normalize_openapi_for_hashing(schema: dict[str, Any]) -> dict[str, Any]:
    """Return a deep copy of ``schema`` with pre-existing nondeterminism scrubbed.

    Currently this replaces the operationId emitted for ``/a2a/`` methods
    (``a2a_trailing_slash_redirect_a2a__{get,post,options}``) with a stable
    ``a2a_trailing_slash_redirect`` so the byte hash is deterministic.

    If/when the production code assigns explicit operation IDs (or the
    function is split per-method), this normalization becomes a no-op and
    should be removed.
    """
    normalized: dict[str, Any] = copy.deepcopy(schema)
    a2a = normalized.get("paths", {}).get("/a2a/")
    if isinstance(a2a, dict):
        for method_spec in a2a.values():
            if isinstance(method_spec, dict) and method_spec.get("operationId", "").startswith(_A2A_REDIRECT_OP_STABLE):
                method_spec["operationId"] = _A2A_REDIRECT_OP_STABLE
    return normalized
