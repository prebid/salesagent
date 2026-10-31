"""Stand-in ORM rows for the update_media_buy unit tests.

These tests mock the Unit of Work, so every repository read answers with whatever the
mock was told to answer with — and a bare ``MagicMock()`` answers EVERY attribute with
another ``MagicMock``. That is fine until production reads a column the test did not
think to set, at which point the response model refuses the MagicMock and the test fails
somewhere unrelated to what it was grading. It has now happened twice: once when the
post-update ``affected_packages`` projection started reading ``package_config``
(``_stub_package_reads``' docstring in ``test_media_buy.py`` records that round), and
again when it started reading the ``pacing`` and ``bid_price`` COLUMNS.

:func:`stub_package_row` closes that loop by DERIVING the attribute set from
``MediaPackage.__table__.columns``: every column starts as a real ``None``, so a column
added to the model later reads as absent rather than as a MagicMock, and a typo in an
override is refused instead of silently creating an attribute production will never read.

This is a stub for the MOCKED level only. A test that exercises the repository, the
session or the SQL itself belongs in ``tests/integration`` against real PostgreSQL with
``MediaPackageFactory`` (CLAUDE.md § "Database: repository pattern + ORM-first").
"""

from typing import Any
from unittest.mock import MagicMock

from src.core.database.models import MediaPackage

#: The MediaPackage columns, from the model rather than transcribed from it.
_PACKAGE_COLUMNS: tuple[str, ...] = tuple(column.name for column in MediaPackage.__table__.columns)


def stub_package_row(**overrides: Any) -> MagicMock:
    """A stand-in for one ``MediaPackage`` row, with every column a real value.

    ``package_config`` defaults to ``{}`` rather than ``None`` because production reads
    keys off it; everything else defaults to ``None``. Pass overrides by column name — a
    name that is not a column raises, which is the point: a stub that quietly accepts
    ``packge_config`` would hand production a MagicMock again.
    """
    unknown = set(overrides) - set(_PACKAGE_COLUMNS)
    if unknown:
        raise AttributeError(f"MediaPackage has no column(s): {', '.join(sorted(unknown))}")
    row = MagicMock()
    for column in _PACKAGE_COLUMNS:
        setattr(row, column, None)
    row.package_config = {}
    for name, value in overrides.items():
        setattr(row, name, value)
    return row
