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

from tests.bdd.conftest import _E2E_REST_KNOWN_FAILURES, pytest_collection_modifyitems


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
