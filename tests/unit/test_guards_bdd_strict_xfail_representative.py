"""Guard: no strict-xfail transport leg may be deselected, ever.

``pytest_collection_modifyitems`` (tests/bdd/conftest.py) used to keep ONE
mcp/rest "representative" per strict-xfail scenario and deselect the sibling, to
save the runtime of running the same failure path on every transport. That
optimization was deleted on 2026-07-30 (GH #1291 work) for two reasons:

* **it was order-dependent.** The representative was whichever variant appeared
  first in ``items``, and pytest-randomly is active for the bdd env (only
  ``integration`` passes ``-p no:randomly``, tox.ini:82), so the surviving
  transport was a per-run coin flip -- measured as 22 UC-010 nodeids trading
  mcp<->rest between full runs with the per-suite totals conserved, which is why
  pass counts hid it completely;
* **one representative cannot see a transport-specific fix.** Transports diverge
  one at a time here (``_MCP_SELECTIVE_XFAIL`` existed for mcp-only gaps, the
  retired ``_REST_XFAIL_TAGS`` for rest-only ones; running the whole UC-004 file
  with every leg enabled surfaced 13 genuine XPASS(strict), ALL on mcp/rest and
  NONE on a2a). Making the choice deterministic would have converted an
  intermittent blind spot into a permanent one.

So the invariant these tests pin is completeness: for a strict-xfail scenario,
every wire transport leg is collected, each carrying its own strict marker, so an
xpass surfaces on whichever transport production actually fixed.

Drives the REAL pytest_collection_modifyitems via minimal stand-in Items (same
technique as test_bdd_e2e_enabled_xdist_guard.py's stub config), plus one
subprocess collection of a real module so the invariant is graded against real
items, real markers and the real conftest.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests.bdd.conftest import pytest_collection_modifyitems

_REPO_ROOT = Path(__file__).resolve().parents[2]


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


def _make_items(tag: str, strict_on: tuple[str, ...] = ("mcp", "rest"), transports=("a2a", "mcp", "rest")):
    """One scenario, one item per transport, strict xfail on *strict_on* only.

    ``strict_on`` is a parameter because the asymmetric shape is the dangerous
    one: several UC-004 markers are deliberately applied to mcp/rest ONLY because
    a2a already validates (e.g. the ``_dr_invalid_fail`` predicate explicitly
    excludes a2a). A fix that reserved a base for whichever transport it saw
    first would still deselect the ONE leg carrying the tripwire in that shape,
    and a symmetric-only fixture would never catch it.
    """
    tag_mark = pytest.Mark(tag, (), {})
    strict_xfail = pytest.mark.xfail(reason="mcp/rest validation gap", strict=True).mark
    items = [
        _FakeItem(
            f"tests/bdd/test_fake.py::test_thing[{t}-row]",
            marks=[tag_mark, *([strict_xfail] if t in strict_on else [])],
        )
        for t in transports
    ]
    config = _FakeConfig()
    for item in items:
        item.config = config
    return items, config


def _survivors(tag: str, order: tuple[int, ...] = (0, 1, 2), **kwargs) -> set[str]:
    """Run the REAL hook over one permutation and return the surviving nodeids."""
    items, _ = _make_items(tag, **kwargs)
    permuted = [items[i] for i in order]
    pytest_collection_modifyitems(permuted)
    return {item.nodeid for item in permuted}


_ALL_THREE = {
    "tests/bdd/test_fake.py::test_thing[a2a-row]",
    "tests/bdd/test_fake.py::test_thing[mcp-row]",
    "tests/bdd/test_fake.py::test_thing[rest-row]",
}


@pytest.mark.parametrize("tag", ["T-UC-999-fake", "T-UC-010-fake"])
@pytest.mark.parametrize("strict_on", [("mcp", "rest"), ("rest",), ("mcp",), ("a2a", "mcp", "rest")])
def test_every_strict_xfail_transport_leg_survives_collection(monkeypatch, tag, strict_on):
    """No leg may be dropped, whatever the marker shape or the tag.

    Asserts the EXACT surviving set, not a weaker property: an equality between
    two permutations would be satisfied by a hook that deselected BOTH mcp and
    rest, and a "some strict item survived" check would be satisfied by keeping
    only the one leg that happens to carry a marker. Both tags are covered
    because the deleted code had two separate opt-in paths (a T-UC-010 marker
    prefix, and an "a2a carries no strict marker" fallback).
    """
    monkeypatch.delenv("BDD_ALL_TRANSPORTS", raising=False)

    assert _survivors(tag, strict_on=strict_on) == _ALL_THREE


@pytest.mark.parametrize("order", [(0, 1, 2), (0, 2, 1), (2, 1, 0), (1, 0, 2)])
def test_collection_outcome_is_independent_of_item_order(monkeypatch, order):
    """pytest-randomly reshuffles `items` every run, so the hook's output must not
    depend on the order it sees them in. This is the property whose absence was
    the bug: a first-wins accumulator over a shuffled list."""
    monkeypatch.delenv("BDD_ALL_TRANSPORTS", raising=False)

    assert _survivors("T-UC-010-fake", order=order) == _ALL_THREE


def test_transport_of_resolves_e2e_rest_before_rest():
    """The one derivation, graded on the overlap that motivates centralizing it.

    `e2e_rest` winning over `rest` is not a property of the alternation order --
    a regex matches at the earliest POSITION first, and "rest" inside "e2e_rest"
    is always four characters later, so reordering cannot break it (verified by
    mutation). What this pins is the derivation's CONTRACT against a
    reimplementation that drops the bracket discipline: a bare `if t in nodeid`
    misses the real-HTTP leg entirely (`"[rest" in "…[e2e_rest-row]"` is False),
    and a membership test against a set that omits `e2e_rest` drops it — the two
    shapes found in the graduation tool that has since been retired. (A naive
    split-on-"-" does NOT mis-route here: `"e2e_rest-row".split("-", 1)[0]` is
    `"e2e_rest"`, so that reading, once recorded here, was wrong.) Roughly 25 predicates in
    the conftest branch on `is_rest` and several deliberately exclude e2e_rest
    (`_rdim_non_impl_fail` is `(is_mcp or is_rest)` because the real-HTTP leg is
    NOT excused), so a mis-route silently excuses a transport from a gap it was
    never granted.
    """
    from tests.bdd.conftest import _transport_of

    assert _transport_of("tests/bdd/test_x.py::test_y[e2e_rest-some row]") == "e2e_rest"
    assert _transport_of("tests/bdd/test_x.py::test_y[rest-some row]") == "rest"
    assert _transport_of("tests/bdd/test_x.py::test_y[rest]") == "rest"
    assert _transport_of("tests/bdd/test_x.py::test_y[a2a-verification_methods=[id_document]]") == "a2a"
    assert _transport_of("tests/bdd/test_x.py::test_y") is None


@pytest.mark.arch_guard
def test_real_module_collection_is_seed_independent_and_complete():
    """The observable locus: two pytest-randomly seeds must collect the same set,
    and nothing may be deselected.

    This is the shape the defect was FOUND in (a nodeid diff between two
    full-suite reports), so it is graded here against a real module. The
    deselection assertion is what stops the test passing for the wrong reason:
    with `BDD_ALL_TRANSPORTS=1` leaking in from the ambient environment the
    seed-invariance half alone passed vacuously, because the whole optimization
    block was skipped.
    """
    module = "tests/bdd/test_uc010_discover_seller_capabilities.py"
    node_re = re.compile(r"^<Function (.+)>$")
    deselect_re = re.compile(r"(\d+) deselected")

    # Explicitly scrubbed: the guarded property is what a DEFAULT run collects.
    env = {k: v for k, v in os.environ.items() if k != "BDD_ALL_TRANSPORTS"}

    def collect(seed: int) -> tuple[set[str], int]:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                module,
                "--collect-only",
                "-q",
                "--no-header",
                "-p",
                "no:cacheprovider",
                f"--randomly-seed={seed}",
            ],
            capture_output=True,
            text=True,
            cwd=str(_REPO_ROOT),
            timeout=300,
            check=False,
            env=env,
        )
        if result.returncode != 0:
            msg = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
            pytest.fail(f"collect-only of {module} failed (seed={seed}): {msg}")
        nodeids = {m.group(1) for line in result.stdout.splitlines() if (m := node_re.match(line.strip()))}
        if not nodeids:
            pytest.fail(f"collect-only of {module} returned no Function items (seed={seed})")
        deselected = max((int(m.group(1)) for m in deselect_re.finditer(result.stdout)), default=0)
        return nodeids, deselected

    first, first_deselected = collect(1)
    second, second_deselected = collect(987654)

    assert first == second, (
        "the collected test set depends on the pytest-randomly seed; "
        f"only in seed=1: {sorted(first - second)[:5]}; only in seed=987654: {sorted(second - first)[:5]}"
    )
    assert (first_deselected, second_deselected) == (0, 0), (
        f"{module} had items deselected at collection ({first_deselected}/{second_deselected}) — "
        "a keep-one-transport optimization was reintroduced"
    )


# ---------------------------------------------------------------------------
# Disease guard: ONE transport derivation, not N hand-rolled copies
# ---------------------------------------------------------------------------
# The bug this file guards was caused by an order-dependent accumulator, but the
# codebase scan found the enabling condition was duplication: transport identity
# was re-derived from a nodeid in FOUR places in tests/bdd/conftest.py, and one of
# those spellings silently omitted `e2e_rest`. Three copies were deleted with the
# optimization and the fourth became `_transport_of`. This guard keeps the count at
# one, because a second copy is how the derivations drift apart again.

_BDD_CONFTEST = _REPO_ROOT / "tests" / "bdd" / "conftest.py"

# `"[mcp]" in nodeid` / `"[mcp-" in nodeid` — the exact idiom that was migrated.
_HANDROLLED_DERIVATION = re.compile(
    r'"\[(?:impl|a2a|mcp|rest|e2e_rest)[-\]]?"\s*in\s+\w*nodeid',
)


def _handrolled_derivations(source: str) -> list[str]:
    return [m.group(0) for m in _HANDROLLED_DERIVATION.finditer(source)]


@pytest.mark.arch_guard
def test_bdd_conftest_has_exactly_one_transport_derivation():
    """No nodeid-substring transport test outside `_transport_of`.

    Deriving the transport by substring is not wrong in isolation -- it is wrong
    N times, because each copy is free to disagree about `e2e_rest`. Route new
    code through `_transport_of(nodeid)`.
    """
    hits = _handrolled_derivations(_BDD_CONFTEST.read_text())

    assert hits == [], (
        "transport identity is being re-derived from a nodeid by hand in "
        f"tests/bdd/conftest.py: {hits} -- use _transport_of(nodeid), which is the "
        "single derivation and the only one that handles the rest/e2e_rest overlap"
    )


@pytest.mark.arch_guard
@pytest.mark.parametrize(
    "snippet",
    [
        'is_mcp = "[mcp]" in nodeid or "[mcp-" in nodeid',  # the exact migrated idiom
        'if "[rest-" in nodeid:',  # bracket-and-dash form
        'is_e2e = "[e2e_rest]" in item_nodeid',  # a differently-named variable
        'x = "[impl" in nodeid',  # unterminated bracket — the would-be-missed variant
    ],
)
def test_guard_catches_every_handrolled_spelling(snippet):
    """Positive meta-tests, including the would-be-missed regex-slip case.

    `"[impl"` has no closing bracket or dash, which an over-anchored pattern
    would skip -- the same shape as a `_neo_isolated_input` key slipping a
    `^neo_`-anchored regex.
    """
    assert _handrolled_derivations(snippet), f"guard would not catch: {snippet}"


@pytest.mark.arch_guard
@pytest.mark.parametrize(
    "snippet",
    [
        "transport = _transport_of(nodeid)",  # the sanctioned derivation
        'is_mcp = transport == "mcp"',  # comparing the derived value is fine
        '_NODEID_TRANSPORTS = ("e2e_rest", "a2a", "mcp", "rest", "impl")',  # the source tuple
        '"[rest-invalid_oneOf_both",',  # a scenario ROW selector, not a derivation
        'if "[mcp]" in reason:',  # substring test against something other than a nodeid
    ],
)
def test_guard_does_not_flag_sanctioned_forms(snippet):
    """Negative meta-tests. Row selectors are the large legitimate population here
    (100 bracketed + 67 bare in this file), so a guard that flagged them would be
    unusable and would get disabled."""
    assert not _handrolled_derivations(snippet), f"guard false-positives on: {snippet}"
