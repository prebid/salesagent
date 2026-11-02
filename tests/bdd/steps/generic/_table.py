"""Gherkin data tables, parsed in one place.

pytest-bdd hands a ``datatable`` in as a list of rows where row 0 is the header.
Every step that reads one therefore needs the same two lines, and 15 sites across
five modules carried their own copy::

    headers = datatable[0]
    rows = [dict(zip(headers, row, strict=True)) for row in datatable[1:]]

Two of them lower-cased the header names and the rest did not, 100 lines apart in
``uc011_accounts.py`` -- so ``| Sandbox |`` was a different column from
``| sandbox |`` depending on which step read the table.

Booleans had two dialects. ``value.lower() == "true"`` appears at ten sites and
bare ``== "true"`` at three, and the bare form reads ``| True |`` -- the spelling
a person writing a feature file is most likely to use -- as FALSE, silently. The
scenario passes with the flag off and grades the wrong arm.
"""

from __future__ import annotations

from typing import Any

#: Values that mean true in a Gherkin cell. Anything else is false, EXCEPT the
#: spellings in :data:`_FALSE`, which are the only accepted way to say false --
#: a cell that is neither raises, because a typo silently reading as false is how
#: a scenario grades the arm it did not mean to.
_TRUE = frozenset({"true", "yes", "1"})
_FALSE = frozenset({"false", "no", "0", ""})


def rows(datatable: Any, *, lower_headers: bool = False) -> list[dict[str, str]]:
    """The table's data rows as dicts keyed by its header row.

    Args:
        datatable: the raw table pytest-bdd passes in, row 0 being the header.
        lower_headers: normalise header names to lowercase. Off by default, which
            is what 13 of the 15 previous call sites did; the two that lowered
            pass it explicitly so the divergence is visible rather than
            positional.

    ``strict=True`` on the zip is deliberate: a row with more or fewer cells than
    the header is a malformed table, and pairing it silently drops or invents a
    column.
    """
    header = [h.lower() for h in datatable[0]] if lower_headers else list(datatable[0])
    return [dict(zip(header, row, strict=True)) for row in datatable[1:]]


def as_bool(value: str, *, field: str = "value") -> bool:
    """A Gherkin cell as a bool, with one dialect and no silent default.

    Accepts true/yes/1 and false/no/0 in any case, plus the empty cell as false.
    Anything else RAISES rather than defaulting: the bare ``== "true"`` form this
    replaces read ``True``, ``TRUE`` and ``y`` as false, and a scenario that
    grades the wrong arm because of a capital letter fails for a reason nobody
    can see in the feature file.
    """
    normalised = value.strip().lower()
    if normalised in _TRUE:
        return True
    if normalised in _FALSE:
        return False
    raise ValueError(
        f"{field}: {value!r} is not a boolean this table understands. Use one of {sorted(_TRUE)} or {sorted(_FALSE)}."
    )


def drop_header_if(datatable: Any, first_cell: str) -> list[Any]:
    """The table's rows, minus a header row IF one is present.

    Some tables in this suite are written with the header optional -- the step
    accepts both ``| field | value |`` followed by data, and bare data rows. Two
    sites in ``uc003_update_media_buy.py`` did this 100 lines apart, one
    comparing ``== "field"`` and the other ``.lower() == "field"``, so
    ``| Field |`` was a header to one step and a data row to the other.

    Case-insensitive, since that is the strictly safer of the two: reading a
    header as data produces a garbage row that fails loudly, while the reverse
    silently drops a real row.
    """
    if datatable and str(datatable[0][0]).strip().lower() == first_cell.lower():
        return list(datatable[1:])
    return list(datatable)
