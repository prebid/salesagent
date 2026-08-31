"""Public accessor for the pinned AdCP error-code enum metadata (#1329).

``pinned_error_metadata`` is the single, PUBLIC home for the pinned
``error-code.json`` ``enumMetadata`` — the map ``code -> {recovery, suggestion}`` derived
from the installed SDK's error-code enum. It replaces the ad-hoc
``from tests.harness.transport import _pinned_error_metadata`` imports scattered across the
governance/capabilities suites with one exported helper (``tests.harness.transport``'s
private wrapper now delegates here, so there is one loader, not two).

Per-field source contract (what each field is authoritative for):

* ``recovery`` — AUTHORITATIVE. The SDK enum's recovery classification is a strict superset
  of the older vendored fixture (92 vs 64 codes) and IDENTICAL across every shared code (0
  divergences), so ``recovery`` may be sourced from here and pinned on the wire. This is the
  field ``assert_wire_error`` / ``assert_account_error`` default from.
* ``suggestion`` — NOT authoritative for CONTENT grading. ``suggestion`` DIVERGES on 4 codes
  between this SDK source and the vendored fixture. Wire tests read the WIRE's own
  ``suggestion`` text (``extract_wire_suggestion``), never this field, so the divergence has
  no effect on them. Consumers that grade ``suggestion`` CONTENT
  (``test_architecture_error_suggestion_enum_conformance``) stay on the vendored fixture —
  see ``docs/adcp-spec-version.md`` "Pinned schema sources". Do NOT assert wire suggestion
  content against this map.
"""

from __future__ import annotations

import functools


@functools.lru_cache(maxsize=1)
def pinned_error_metadata() -> dict[str, dict[str, str]]:
    """``code -> {recovery, suggestion}`` from the pinned ``error-code.json`` (see module docstring)."""
    from tests.helpers.pinned_schema import load

    return load("error-code.json")["enumMetadata"]
