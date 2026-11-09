"""Canonical payload hashing for idempotency replay-conflict detection.

The one RFC 8785 seam in this repo, so it also computes the other JCS digest the pin asks a
seller to check: ``selected_output_digest`` of a sync item carrying ``representation_selection``.

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

from typing import Any, Protocol

from adcp.server.idempotency import EXCLUDED_FIELDS as _EXCLUDED_FIELDS
from adcp.server.idempotency import canonical_json_sha256 as _canonical_json_sha256
from adcp.server.idempotency import strip_excluded_fields

from src.core.exceptions import AdCPValidationError

__all__ = [
    # _EXCLUDED_FIELDS is re-exported for the test suite's literal pin of the
    # spec's closed exclusion list (engine-independent conformance anchor).
    "_EXCLUDED_FIELDS",
    "canonical_payload_hash",
    "canonical_request_hash",
    "selected_output_digest",
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
        raise AdCPValidationError() from exc


class SupportsModelDump(Protocol):
    """Anything that can render itself as a JSON-ready dict.

    Named structurally because the two kinds of value hashed here -- a plain pydantic model
    and a :class:`~src.core.schemas._base.BuyerRequest` DTO, which is a model plus a mixin --
    have no common nominal base and Python has no intersection type.
    """

    def model_dump(self, **kwargs: Any) -> dict[str, Any]: ...


def canonical_request_hash(request: SupportsModelDump) -> str:
    """Canonical hash of a Pydantic request model.

    Thin wrapper over :func:`canonical_payload_hash` that performs the
    ``model_dump(mode="json")`` here, so transport-agnostic ``_impl`` bodies never
    call ``.model_dump()`` directly (the no-model-dump-in-impl architecture guard).
    """
    return canonical_payload_hash(request.model_dump(mode="json"))


#: The top-level members a seller-bound manifest projection drops before it is digested --
#: core/representation-selection.json (v3.2.7) ``selected_output_digest``: "remove exactly
#: top-level ``$schema``, ``representation_selection``, ``creative_id``, ``revision_id``,
#: ``name``, ``tags``, ``status``, ``weight``, ``placement_refs``, ``placement_ids``, and
#: ``inputs``. Every other field, including unknown delivery fields, is included."
SELECTED_OUTPUT_EXCLUDED: frozenset[str] = frozenset(
    {
        "$schema",
        "representation_selection",
        "creative_id",
        "revision_id",
        "name",
        "tags",
        "status",
        "weight",
        "placement_refs",
        "placement_ids",
        "inputs",
    }
)


def selected_output_digest(creative: SupportsModelDump) -> str:
    """``sha256:<hex>`` of the RFC 8785 JCS of *creative*'s seller-bound manifest projection.

    The value a sync item's ``representation_selection.selected_output_digest`` must equal
    (docs/creative/representation-sets.mdx § Seller-bound resolution, v3.2.7). Over what the
    buyer SENT -- ``exclude_unset``, so no default is materialized -- as the boundary's own
    request hash is over the validated request (``tools/_boundary.py``): a member this
    seller's DTO discards cannot be part of what it compares. Reproduces the pin's worked
    example (``sha256:02735eb8...`` for the paired-redirect manifest in that section).

    The same engine as the idempotency hash. Its closed exclusion list (``idempotency_key``,
    ``context``, ``governance_context``) names no member of a creative item, so on this
    projection it strips nothing and the digest is plain JCS SHA-256.
    """
    document = creative.model_dump(mode="json", by_alias=True, exclude_unset=True)
    projection = {key: value for key, value in document.items() if key not in SELECTED_OUTPUT_EXCLUDED}
    return f"sha256:{canonical_payload_hash(projection)}"
