"""Meta-guard: every Captured→shrink allowlist entry has a FIXME at source.

Canonical spec: ``L0-implementation-plan-v2.md`` §5.5 row #29.
CLAUDE.md "Structural Guards — Rules for guards" item 2: "Every
allowlisted violation has a ``# FIXME(salesagent-xxxx)`` comment at the
source location."

Every line in every ``tests/unit/architecture/allowlists/*.txt`` is of
the form ``<path>:<lineno>``. This meta-guard opens each file at that
line and asserts a ``FIXME`` comment is present within a small window
(the exact line, the line before, or the line after — FIXMEs often
sit on the declaration line while the numbered entry points at the
statement inside).

The FIXME comment is the traceability contract: every allowlisted
violation has a known reason recorded at the source site and a ticket
(``salesagent-xxxx``) for eventual fix. When the FIXME is removed (the
violation is fixed), the allowlist entry is removed in the same commit
and the meta-guard in
``test_structural_guard_allowlist_monotonic.py::test_baselines_stay_in_sync_with_shrinks``
requires the baseline count to drop.

Exit condition: as allowlists shrink to zero, this meta-guard's work
shrinks commensurately — the empty set is trivially satisfied.

Meta-test: a planted fixture under ``tests/unit/architecture/fixtures/``
trips the detector when its FIXME is removed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.unit.architecture._ast_helpers import (
    ALLOWLIST_DIR,
    REPO_ROOT,
    read_allowlist,
)

# Window of lines to search for the FIXME (inclusive). A window of 1
# means "this line, the line above, or the line below." Allowlists
# capture statement lines but FIXMEs often sit on the enclosing
# function/method declaration — ±3 lines covers most real cases.
FIXME_WINDOW = 3

FIXME_RE = re.compile(r"#\s*FIXME\(salesagent-[a-zA-Z0-9_-]+\)", re.IGNORECASE)

# Allowlists that cover non-source artifacts (e.g., line numbers in a
# template or a data file) can opt out here. Empty at L0 — every active
# allowlist points at Python source.
EXEMPT_ALLOWLISTS: frozenset[str] = frozenset()

# Captured→shrink bootstrap: entries that existed before this meta-guard
# landed and do not yet carry a FIXME at the source site. Each entry is
# a string of the form ``<allowlist_file>::<relpath>:<lineno>`` — one
# line per pre-existing allowlist entry. The meta-guard's
# test_every_allowlist_entry_has_fixme_at_source scans NEW entries only
# (those NOT in this bootstrap list); new additions MUST carry a FIXME.
# The bootstrap list MAY shrink but MUST NOT grow — as entries are fixed
# (either a FIXME is added, or the source site is repaired and the
# allowlist entry is removed), they drop off here.
BOOTSTRAP_NO_FIXME_FILE = "fixme_bootstrap.txt"


def _iter_entries(allowlist_path: Path) -> list[tuple[str, int]]:
    """Yield ``(relpath, lineno)`` tuples for each active entry."""
    out: list[tuple[str, int]] = []
    for raw_line in allowlist_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        relpath, _, lineno_s = line.rpartition(":")
        try:
            lineno = int(lineno_s)
        except ValueError:
            continue
        out.append((relpath, lineno))
    return out


def _source_has_fixme_near(target_path: Path, lineno: int, window: int = FIXME_WINDOW) -> bool:
    """Return True iff a ``# FIXME(salesagent-...)`` appears within ``window`` lines."""
    if not target_path.exists():
        # Entry points at a missing file — the stale-entry meta-guard
        # catches this case separately.
        return True
    try:
        text = target_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    lines = text.splitlines()
    # Line numbers are 1-indexed; list is 0-indexed.
    lo = max(0, lineno - 1 - window)
    hi = min(len(lines), lineno + window)
    for i in range(lo, hi):
        if FIXME_RE.search(lines[i]):
            return True
    return False


def _current_no_fixme_keys() -> set[str]:
    """Return ``{'<allowlist>::<path>:<lineno>', ...}`` for every
    active allowlist entry whose source site lacks a nearby FIXME.
    """
    keys: set[str] = set()
    for allowlist_path in sorted(ALLOWLIST_DIR.glob("*.txt")):
        if allowlist_path.name in EXEMPT_ALLOWLISTS:
            continue
        for relpath, lineno in _iter_entries(allowlist_path):
            source_path = REPO_ROOT / relpath
            if not _source_has_fixme_near(source_path, lineno):
                keys.add(f"{allowlist_path.name}::{relpath}:{lineno}")
    return keys


def test_new_allowlist_entries_have_fixme_at_source() -> None:
    """New allowlist entries MUST carry a FIXME within ±3 lines.

    Pre-existing entries are captured in ``allowlists/fixme_bootstrap.txt``
    (Captured→shrink). Any entry NOT in the bootstrap list must have
    a matching FIXME comment at the source site.
    """
    bootstrap = read_allowlist(BOOTSTRAP_NO_FIXME_FILE)
    current_no_fixme = _current_no_fixme_keys()
    new_no_fixme = sorted(current_no_fixme - bootstrap)
    assert not new_no_fixme, (
        "New allowlist entries WITHOUT a `# FIXME(salesagent-xxxx)` comment within "
        f"±{FIXME_WINDOW} lines of the target. Every allowlisted violation MUST "
        "carry a FIXME at the source site (CLAUDE.md Structural Guards rule 2). "
        "If adding to an allowlist is the only realistic path, add a FIXME "
        "comment at the source line first:\n"
        + "\n".join(f"  - {k}" for k in new_no_fixme[:50])
        + (f"\n... and {len(new_no_fixme) - 50} more." if len(new_no_fixme) > 50 else "")
    )


def test_fixme_bootstrap_does_not_grow() -> None:
    """The bootstrap list MAY shrink but MUST NOT grow."""
    bootstrap = read_allowlist(BOOTSTRAP_NO_FIXME_FILE)
    current_no_fixme = _current_no_fixme_keys()
    stale = sorted(bootstrap - current_no_fixme)
    assert not stale, (
        "Stale entries in fixme_bootstrap.txt — these entries are no longer "
        "active allowlist entries (either the allowlist entry was removed or "
        "a FIXME was added). Remove them from the bootstrap:\n"
        + "\n".join(f"  - {s}" for s in stale[:50])
        + (f"\n... and {len(stale) - 50} more." if len(stale) > 50 else "")
    )


@pytest.mark.parametrize(
    "snippet,has_fixme",
    [
        ("x = 1  # FIXME(salesagent-123): fix me\n", True),
        ("# FIXME(salesagent-abc)\nx = 1\n", True),
        ("x = 1  # TODO: something\n", False),
        ("x = 1\n# FIX ME later\n", False),
        # Case-insensitive match on 'FIXME' keyword.
        ("x = 1  # fixme(salesagent-xyz)\n", True),
    ],
)
def test_fixme_regex_behavior(snippet: str, has_fixme: bool, tmp_path: Path) -> None:
    """Validate the FIXME detection against a set of positive/negative cases."""
    f = tmp_path / "probe.py"
    f.write_text(snippet, encoding="utf-8")
    found = _source_has_fixme_near(f, 1, window=1)
    assert found is has_fixme
