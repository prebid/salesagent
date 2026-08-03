"""Shared pytest-bdd datatable parsing helpers.

One canonical headered-table parser for all step modules — per-module copies
drift (the uc009 copy stripped whitespace, the generic one didn't).
"""

from __future__ import annotations

from collections.abc import Sequence


def datatable_to_dicts(datatable: Sequence[Sequence[object]]) -> list[dict[str, str]]:
    """Convert a pytest-bdd raw datatable (list of lists) to one dict per row.

    The first row is treated as column headers. Remaining rows become dicts
    keyed by those headers. Header and cell values are str()-coerced and
    whitespace-stripped, so Gherkin table padding never leaks into keys or
    values.
    """
    headers = [str(cell).strip() for cell in datatable[0]]
    return [{h: str(v).strip() for h, v in zip(headers, row, strict=True)} for row in datatable[1:]]
