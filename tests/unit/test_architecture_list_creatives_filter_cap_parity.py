"""Structural guard: every array-typed CreativeFilters property is length-capped.

``_CAPPED_FILTER_FIELDS`` (src/core/tools/creatives/listing.py) is a
hand-maintained tuple. Every array-typed property on the AdCP
``creative-filters.json`` schema expands into a SQL ``IN (...)`` predicate, so
every one of them must be in that tuple or an over-long list filter reaches the
database uncapped — the whole point of the #1505 cap.

This lives in tests/unit/ (not beside the cap's behavioral tests in
tests/integration/) because it touches no database and the invariant has to hold
at the pre-commit gate: `make quality` and the tox `unit` env run
``tests/unit/``, so a guard parked under ``tests/integration/`` never executes
there regardless of its markers.
"""

from __future__ import annotations

from src.core.tools.creatives.listing import _CAPPED_FILTER_FIELDS
from tests.helpers import pinned_schema

# The AdCP JSON schema is the authority for what CreativeFilters declares.
# tests/helpers/pinned_schema.py reads it from the installed adcp SDK's own schema
# tree: the SDK's installed version IS the pin. #1868 retired the separately
# vendored fixture tree (it had drifted a full spec-minor behind), so there is now
# exactly one upstream pin. Deriving the expected set from
# ``CreativeFilters.model_fields`` instead would grade one wheel-derived artifact
# against another, so the schema — not the model — stays the source.
_PINNED_CREATIVE_FILTERS_REF = "core/creative-filters.json"


def _pinned_array_filter_fields() -> set[str]:
    """Array-typed properties of the pinned ``creative-filters.json`` schema."""
    schema = pinned_schema.load(_PINNED_CREATIVE_FILTERS_REF)
    fields = {name for name, spec in schema["properties"].items() if spec.get("type") == "array"}
    assert fields, f"non-vacuity: {_PINNED_CREATIVE_FILTERS_REF} declared no array-typed properties"
    return fields


def test_capped_fields_match_pinned_schema_array_filters():
    """_CAPPED_FILTER_FIELDS is hand-maintained — pin it against the SDK-pinned schema.

    Every array-typed CreativeFilters property expands into an ``IN (...)``
    predicate, so every one of them must be capped. If a future pin refresh adds
    an array filter, this fails and the new field must be added to the cap (or
    explicitly excluded here with a reason) — no list filter slips through
    uncapped silently.
    """
    pinned = _pinned_array_filter_fields()
    capped = set(_CAPPED_FILTER_FIELDS)

    assert pinned == capped, (
        f"{_PINNED_CREATIVE_FILTERS_REF} array filters diverged from _CAPPED_FILTER_FIELDS — "
        f"schema-only (uncapped, would expand into IN (...)): {sorted(pinned - capped)}, "
        f"cap-only (not a schema array filter): {sorted(capped - pinned)}"
    )
