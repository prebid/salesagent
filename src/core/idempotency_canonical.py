"""Canonical payload hashing for idempotency replay-conflict detection.

The AdCP idempotency contract (the ``replay_ttl_seconds`` capability) defines
payload equivalence as RFC 8785 JSON Canonicalization Scheme over the request
as sent, with a CLOSED exclusion list: two requests carrying the same
``idempotency_key`` and the same canonical hash are replays of each other; the
same key with a different canonical hash is an ``IDEMPOTENCY_CONFLICT``.

The hashing ENGINE is the SDK's ``adcp.server.idempotency`` canonicalizer
(strip the spec exclusion list → ``rfc8785`` → SHA-256) — the same algorithm
the reference implementation ships. This module is the single production seam
in front of it, deliberately:

- production code never imports the SDK module directly, so swapping the
  engine (back to a local implementation, or forward to a future SDK's) is a
  change to THIS file only;
- the RFC 8785 vectors in ``tests/unit/test_idempotency_canonical.py`` pin the
  BEHAVIOR against RFC-published bytes independently of who implements it — an
  SDK bump that changes hashing fails OUR vectors, and we choose;
- boundary translation stays ours: a pathologically nested payload raises a
  typed ``AdCPValidationError`` instead of the engine's raw ``RecursionError``.

``canonical_request_hash`` (model-dump indirection) exists so transport-
agnostic ``_impl`` bodies never call ``.model_dump()`` directly (the
no-model-dump-in-impl guard). It is the documented fallback for impl-direct
callers ONLY — transport wrappers thread the raw wire payload to
``canonical_payload_hash``, the spec's equivalence input.
"""

from __future__ import annotations

from typing import Any

from adcp.server.idempotency import EXCLUDED_FIELDS as _EXCLUDED_FIELDS
from adcp.server.idempotency import canonical_json_sha256 as _canonical_json_sha256
from adcp.server.idempotency import strip_excluded_fields
from pydantic import BaseModel

from src.core.exceptions import AdCPValidationError

__all__ = [
    # _EXCLUDED_FIELDS is re-exported for the test suite's literal pin of the
    # spec's closed exclusion list (engine-independent conformance anchor).
    "_EXCLUDED_FIELDS",
    "canonical_payload_hash",
    "canonical_request_hash",
    "request_hash_for",
    "strip_excluded_fields",
]


def canonical_payload_hash(payload: dict[str, Any]) -> str:
    """Return the SHA-256 hex digest of the RFC 8785-canonicalized payload.

    Excluded fields (the spec's closed list, re-exported as
    :data:`_EXCLUDED_FIELDS`) are stripped first. The digest is stable across
    key ordering, so two requests differing only in field order (or in
    excluded fields) hash equal — the equivalence test for replay vs conflict.
    """
    try:
        return _canonical_json_sha256(payload)
    except RecursionError as exc:
        # A pathologically nested payload must reject as a buyer error, not
        # crash the boundary with an unhandled RecursionError.
        raise AdCPValidationError("request payload too deeply nested to canonicalize for idempotency") from exc


def canonical_request_hash(request: BaseModel) -> str:
    """Canonical hash of a Pydantic request model.

    Thin wrapper over :func:`canonical_payload_hash` that performs the
    ``model_dump(mode="json")`` here, so transport-agnostic ``_impl`` bodies never
    call ``.model_dump()`` directly (the no-model-dump-in-impl architecture guard).
    """
    return canonical_payload_hash(request.model_dump(mode="json"))


def request_hash_for(request: BaseModel, raw_wire_payload: dict[str, Any] | None) -> str:
    """Select the idempotency hash input — the single rule every mutating tool shares.

    Transport wrappers thread ``raw_wire_payload`` (the spec's equivalence input,
    hashed AS SENT); impl-direct callers pass ``None`` and fall back to the
    model-dump hash. Centralizing the choice keeps ``create_media_buy`` and
    ``sync_accounts`` from drifting on which input the hash is computed over.
    """
    if raw_wire_payload is not None:
        return canonical_payload_hash(raw_wire_payload)
    return canonical_request_hash(request)
