"""Atomic JSONB list-append expression builders.

Whole-value read-modify-write of a JSONType collection (load the list, append
in Python, write the list back) loses concurrent appends: two writers read the
same snapshot and the later commit erases the earlier append. These builders
put the read and the write in ONE UPDATE expression, so concurrent appends
serialize on the row lock and both survive (salesagent-pgqs; same mechanism as
the v8dt authorized-list mutators).

Usage::

    stmt = (
        update(Model)
        .where(...)
        .values(col=jsonb_list_append(Model.col, elem_expr))
        .execution_options(synchronize_session=False)
    )
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import cast
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func


def jsonb_list(col: Any) -> Any:
    """``coalesce(col::jsonb, '[]')`` — treat NULL (or never-set) as an empty list."""
    return func.coalesce(cast(col, JSONB), func.jsonb_build_array())


def jsonb_list_append(col: Any, elem: Any) -> Any:
    """Expression appending ``elem`` to the JSONB list ``col`` in one statement."""
    return jsonb_list(col).op("||")(func.jsonb_build_array(elem))
