"""Regression tests for the e2e_rest xfail-marker collapse policy.

Guards PR #1420 review finding #1: the e2e_rest collapse in
``tests/bdd/conftest.py`` must NOT downgrade authored ``strict=True`` xfail
markers. The #1270 validation-gap tripwires (sampling_method / date-range)
assert "production is wrong, tell me when it's fixed". If a future production
fix closes the gap the scenario xpasses — under ``strict=False`` that xpass is
silently swallowed and the FIXME lingers forever. Ledger ("mock-incompatible")
entries, by contrast, stay non-strict because an environment-dependent xpass
must not fail CI.

These drive the REAL ``pytest_collection_modifyitems`` hook through a minimal
faithful stub item (the hook touches only ``nodeid``/``own_markers``/
``add_marker``/``iter_markers``) so no collection logic is duplicated here.
"""

import pytest

from tests.bdd.conftest import _E2E_REST_KNOWN_FAILURES, pytest_collection_modifyitems

# Ledger entries whose [e2e_rest-…] parametrize fragment substring-matches a bare
# "rest-…" row in _UC004_GENUINE_XFAIL_ROWS. Without the `if not is_e2e_rest` guard,
# the in-process C4 loop stamps each with a strict=True "impl passes" reason that the
# collapse then preserves — turning a ledger-governed non-strict entry into a strict
# tripwire and overwriting the ledger reason. Each tuple is (ledger-substring, tag).
_LEAKED_C4_LEDGER_ROWS = [
    ("interval=0 (below minimum)", "T-UC-004-boundary-attribution"),
    ("unit=weeks (not in enum)", "T-UC-004-boundary-attribution"),
    ("model=last_click (not in enum)", "T-UC-004-boundary-attribution"),
    ("geo without geo_level", "T-UC-004-boundary-reporting-dims"),
    ("limit negative", "T-UC-004-boundary-reporting-dims"),
    ("limit=0 (below minimum)", "T-UC-004-boundary-reporting-dims"),
]


def _ledger_nodeid(substr: str) -> str:
    matches = [n for n in _E2E_REST_KNOWN_FAILURES if substr in n and "[e2e_rest-" in n]
    assert len(matches) == 1, f"expected exactly one ledger nodeid containing {substr!r}, got {matches}"
    return matches[0]


class _Mark:
    """Stand-in for a pytest Mark: the hook reads only ``.name`` and ``.kwargs``."""

    def __init__(self, name: str, kwargs: dict | None = None):
        self.name = name
        self.kwargs = kwargs or {}


class _StubItem:
    """Minimal faithful stand-in for ``pytest.Item``.

    ``pytest_collection_modifyitems`` touches only these four members, so we
    mirror exactly that surface and exercise the production hook unchanged.
    """

    def __init__(self, nodeid: str, markers: list):
        self.nodeid = nodeid
        self.own_markers = list(markers)

    def add_marker(self, marker):
        # Real pytest stores the unpacked Mark, not the MarkDecorator.
        self.own_markers.append(getattr(marker, "mark", marker))

    def iter_markers(self, name: str | None = None):
        return [m for m in self.own_markers if name is None or m.name == name]


def _single_xfail(item: _StubItem):
    xfails = [m for m in item.own_markers if m.name == "xfail"]
    assert len(xfails) == 1, f"expected exactly one collapsed xfail marker, got {len(xfails)}"
    return xfails[0]


def test_sampling_tripwire_stays_strict_on_e2e_rest():
    """#1270 sampling_method tripwire: authored strict=True survives the collapse."""
    item = _StubItem(
        nodeid="tests/bdd/test_uc004.py::test_x[Unknown string not in enum][e2e_rest]",
        markers=[_Mark("T-UC-004-boundary-sampling")],
    )
    pytest_collection_modifyitems([item])
    assert _single_xfail(item).kwargs.get("strict") is True


def test_date_range_tripwire_stays_strict_on_e2e_rest():
    """#1270 date-range tripwire: authored strict=True survives the collapse."""
    item = _StubItem(
        nodeid="tests/bdd/test_uc004.py::test_x[start_date equals end_date][e2e_rest]",
        markers=[_Mark("T-UC-004-boundary-date-range")],
    )
    pytest_collection_modifyitems([item])
    assert _single_xfail(item).kwargs.get("strict") is True


def test_ledger_entry_stays_non_strict_on_e2e_rest():
    """Ledger entries remain non-strict: an environment-dependent xpass must not fail CI."""
    # Deterministic, and a plain [e2e_rest] suffix so no parametrize fragment can
    # accidentally match a #1270 tripwire substring.
    nodeid = next(n for n in sorted(_E2E_REST_KNOWN_FAILURES) if n.endswith("[e2e_rest]"))
    item = _StubItem(nodeid=nodeid, markers=[])
    pytest_collection_modifyitems([item])
    assert _single_xfail(item).kwargs.get("strict") is False


@pytest.mark.parametrize(("substr", "tag"), _LEAKED_C4_LEDGER_ROWS)
def test_leaked_c4_ledger_rows_stay_non_strict_with_ledger_reason(substr, tag):
    """The substring-collision ledger entries stay ledger-governed (non-strict).

    These carry a boundary tag whose [e2e_rest-…] fragment substring-matches a bare
    "rest-…" C4 row. The in-process C4 loop must NOT touch them: they keep strict=False
    with the ledger's "mock-incompatible" reason, never a strict "(impl passes)" marker.
    Drives the REAL collection hook, so removing the `if not is_e2e_rest` guard fails this.
    """
    item = _StubItem(nodeid=_ledger_nodeid(substr), markers=[_Mark(tag)])
    pytest_collection_modifyitems([item])
    marker = _single_xfail(item)
    assert marker.kwargs.get("strict") is False, f"{substr!r} leaked to a strict C4 marker"
    reason = marker.kwargs.get("reason", "")
    assert "mock-incompatible" in reason, f"{substr!r} lost its ledger reason: {reason!r}"
    assert "impl passes" not in reason, f"C4 reason leaked onto ledger entry {substr!r}: {reason!r}"
