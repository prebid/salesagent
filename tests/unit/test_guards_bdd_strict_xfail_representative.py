"""Guard: the BDD strict-xfail xpass tripwire must not go dead for scenarios
outside the T-UC-010 opt-in.

``pytest_collection_modifyitems`` (tests/bdd/conftest.py) deselects the
mcp/rest variants of any strict-xfail scenario by default (BDD_ALL_TRANSPORTS
unset), keeping only the a2a variant as the "representative". For a scenario
whose strict-xfail marker is applied ONLY to mcp/rest (a2a already validates
and carries no marker -- the real pattern for T-UC-004-daterange-invalid,
T-UC-004-partition-reporting-dims, etc., confirmed 2026-07-25: running the
whole UC-004 file with BDD_ALL_TRANSPORTS=1 surfaces 13 genuine
XPASS(strict) failures, ALL on mcp/rest, NONE on a2a), the default run
deselects the only items that carry the tripwire and keeps the one item
(a2a) that never had it. The scenario silently stops proving its production
gap: no item in the default collection can ever XPASS(strict), so a
production fix in that gap goes undetected forever.

Drives the REAL pytest_collection_modifyitems via minimal stand-in Items
(same technique as test_bdd_e2e_enabled_xdist_guard.py's stub config).
"""

from __future__ import annotations

import pytest

from tests.bdd.conftest import pytest_collection_modifyitems


class _FakeConfig:
    def __init__(self):
        self.deselected: list[_FakeItem] = []
        self.hook = self

    def pytest_deselected(self, items):
        self.deselected.extend(items)


class _FakeItem:
    def __init__(self, nodeid, marks=()):
        self.nodeid = nodeid
        self.own_markers = list(marks)
        self.config = None  # set once all items exist, see _make_items()

    def iter_markers(self, name=None):
        for m in self.own_markers:
            if name is None or m.name == name:
                yield m

    def add_marker(self, marker):
        mark = marker.mark if hasattr(marker, "mark") else marker
        self.own_markers.append(mark)


def _make_items(tag: str):
    """One scenario, 3 transport variants -- strict xfail on mcp/rest only.

    Mirrors the real-world shape found in tests/bdd/conftest.py (e.g. the
    T-UC-004-boundary-date-range ``_dr_invalid_fail`` predicate explicitly
    excludes a2a because "a2a now validates ... mcp/rest still don't").
    """
    tag_mark = pytest.Mark(tag, (), {})
    strict_xfail = pytest.mark.xfail(reason="mcp/rest validation gap", strict=True).mark
    items = [
        _FakeItem("tests/bdd/test_fake.py::test_thing[a2a-row]", marks=[tag_mark]),
        _FakeItem("tests/bdd/test_fake.py::test_thing[mcp-row]", marks=[tag_mark, strict_xfail]),
        _FakeItem("tests/bdd/test_fake.py::test_thing[rest-row]", marks=[tag_mark, strict_xfail]),
    ]
    config = _FakeConfig()
    for item in items:
        item.config = config
    return items, config


def test_default_run_deselects_the_only_items_carrying_the_strict_xfail_tripwire(monkeypatch):
    """Non-UC-010 scenario: default collection must not strip every strict-xfail
    item, or a production fix in that gap can never surface as an XPASS."""
    monkeypatch.delenv("BDD_ALL_TRANSPORTS", raising=False)
    items, config = _make_items("T-UC-999-fake")

    pytest_collection_modifyitems(items)

    remaining_nodeids = {i.nodeid for i in items}
    remaining_strict_xfail = [
        i for i in items if any(m.name == "xfail" and m.kwargs.get("strict") for m in i.iter_markers())
    ]

    assert remaining_strict_xfail, (
        "the single-transport optimization deselected every item carrying the strict-xfail "
        f"tripwire for a non-T-UC-010 scenario; remaining items: {sorted(remaining_nodeids)} -- "
        "a production fix in this gap can never XPASS and surface in CI"
    )


def test_bdd_all_transports_preserves_the_tripwire(monkeypatch):
    """Sanity check: the opt-out env var restores full coverage (control case)."""
    monkeypatch.setenv("BDD_ALL_TRANSPORTS", "1")
    items, config = _make_items("T-UC-999-fake")

    pytest_collection_modifyitems(items)

    remaining_strict_xfail = [
        i for i in items if any(m.name == "xfail" and m.kwargs.get("strict") for m in i.iter_markers())
    ]
    assert len(remaining_strict_xfail) == 2  # mcp + rest, untouched
