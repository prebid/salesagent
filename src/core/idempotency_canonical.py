"""Dormant RFC 8785 payload-hashing primitives.

The seller advertises AdCP 3.1.1 ``idempotency.supported=false``. Consequently,
no production transport or tool hashes a request for replay/conflict handling;
valid keys are operationally inert after shape validation. These helpers remain
as tested substrate for a future, separately grounded universal implementation.

The SDK's ``adcp.server.idempotency`` canonicalizer is kept behind this single
import seam. RFC 8785 vectors pin the helper's mechanics, and boundary
translation turns pathological recursion into ``AdCPValidationError``. Those
primitive guarantees do not imply that the current seller offers replay,
conflict detection, or any other idempotency behavior.
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
    "strip_excluded_fields",
]


def canonical_payload_hash(payload: dict[str, Any]) -> str:
    """Return the SHA-256 hex digest of the RFC 8785-canonicalized payload.

    Excluded fields (the spec's closed list, re-exported as
    :data:`_EXCLUDED_FIELDS`) are stripped first. The digest is stable across
    key ordering, so two requests differing only in field order (or in
    excluded fields) hash equal. Current production paths do not call it.
    """
    try:
        return _canonical_json_sha256(payload)
    except RecursionError as exc:
        # A pathologically nested payload must reject as a buyer error, not
        # crash the boundary with an unhandled RecursionError.
        raise AdCPValidationError("request payload too deeply nested to canonicalize for idempotency") from exc


def canonical_request_hash(request: BaseModel) -> str:
    """Canonical hash of a Pydantic request model.

    Thin dormant wrapper over :func:`canonical_payload_hash` that performs the
    ``model_dump(mode="json")`` in one place.
    """
    return canonical_payload_hash(request.model_dump(mode="json"))
