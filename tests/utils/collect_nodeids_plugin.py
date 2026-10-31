"""Dump every collected node-id to ``$NODEID_DUMP``, for a guard that needs the real set.

Loaded with ``-p tests.utils.collect_nodeids_plugin`` by a subprocess ``pytest
--collect-only``. The alternative is parsing pytest's human output, and that does not
work here: measured on pytest 9.0.3 in this repo, ``--collect-only -q`` prints
``collected 11503 items`` and **no node-id list at all**, so a parser would silently
read an empty set and whatever it graded would pass vacuously. A plugin reads the
items pytest built, so the format cannot drift out from under it.

Writes nothing when ``NODEID_DUMP`` is unset, so loading this is inert for any other
run.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Write the collected node-ids, one per line, newest write wins."""
    destination = os.environ.get("NODEID_DUMP")
    if not destination:
        return
    Path(destination).write_text("\n".join(item.nodeid for item in items), encoding="utf-8")
