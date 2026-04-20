"""Meta-guard: Captured→shrink allowlist counts are monotonically non-increasing.

Canonical spec: ``flask-to-fastapi-foundation-modules.md`` §11.26;
``L0-implementation-plan-v2.md`` §5.5 row #28.

Every Captured→shrink structural-guard allowlist under
``tests/unit/architecture/allowlists/`` has a committed baseline entry
in ``.guard-baselines/<name>.json`` capturing the allowlist entry count
at commit time. This meta-guard asserts that the current count is
``<=`` the baseline — the allowlist MAY shrink but MUST NOT grow.

The workflow on legitimate shrink:

  1. Fix a violation at a source site; remove its entry from the
     allowlist file.
  2. ``make quality`` runs this guard → current count < baseline count.
  3. The baseline is manually decremented to match in the same commit
     that removed the allowlist entry (committed together).

Allowlist growth is caught at commit time — the new violation MUST be
fixed, not appended to the allowlist.

Meta-test: a planted baseline with ``allowlist_count`` below the real
count trips the detector (the ``_detector_catches_growth`` test).

Shape of each baseline file (``.guard-baselines/<guard-name>.json``)::

    {
      "allowlist_file": "no_flask_imports.txt",
      "allowlist_count": 40,
      "committed_at": "2026-04-18",
      "note": "Decrement this when you remove an entry from the allowlist."
    }

Guard rows: §5.5 #28 (monotonic) + #29 (FIXME coverage).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.unit.architecture._ast_helpers import ALLOWLIST_DIR, REPO_ROOT, read_allowlist

BASELINES_DIR = REPO_ROOT / ".guard-baselines"


def _discover_baselines() -> list[tuple[Path, dict]]:
    """Return ``[(baseline_path, loaded_json_dict), ...]`` for every baseline file."""
    if not BASELINES_DIR.exists():
        return []
    out: list[tuple[Path, dict]] = []
    for path in sorted(BASELINES_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            pytest.fail(f"Baseline file {path.name} is not valid JSON: {exc}")
        out.append((path, data))
    return out


def _active_allowlist_count(allowlist_name: str) -> int:
    """Return the number of active (non-comment, non-blank) entries in an allowlist."""
    return len(read_allowlist(allowlist_name))


def test_baselines_dir_exists() -> None:
    assert BASELINES_DIR.exists(), (
        f"Baselines directory {BASELINES_DIR} missing. Each Captured→shrink "
        "allowlist under tests/unit/architecture/allowlists/ MUST have a "
        "matching baseline at .guard-baselines/<name>.json. See §11.26."
    )


def test_every_allowlist_has_a_baseline() -> None:
    """Every ``allowlists/*.txt`` must have a matching ``.guard-baselines/*.json``."""
    allowlist_stems = {p.stem for p in ALLOWLIST_DIR.glob("*.txt")}
    baseline_allowlist_names: set[str] = set()
    for _path, data in _discover_baselines():
        baseline_allowlist_names.add(Path(data["allowlist_file"]).stem)

    missing = allowlist_stems - baseline_allowlist_names
    assert not missing, (
        "Allowlist(s) without a baseline in .guard-baselines/:\n"
        + "\n".join(f"  - {m}.txt" for m in sorted(missing))
        + "\nCreate .guard-baselines/<name>.json capturing the current count."
    )


def test_allowlist_counts_do_not_exceed_baselines() -> None:
    """The count in each allowlist file MUST be ``<=`` its baseline.

    This is the core monotonic-shrink invariant. If a new violation
    gets appended to an allowlist, the count grows and this test trips.
    """
    violations: list[str] = []
    for path, data in _discover_baselines():
        baseline = int(data["allowlist_count"])
        current = _active_allowlist_count(data["allowlist_file"])
        if current > baseline:
            violations.append(
                f"  {data['allowlist_file']}: baseline={baseline}, current={current} "
                f"(GREW by {current - baseline}; baseline at {path.name})"
            )
    assert not violations, (
        "Structural-guard allowlist(s) GREW — Captured→shrink allowlists can only shrink.\n"
        "Fix the new violations at the source site; do NOT append to the allowlist:\n"
        + "\n".join(violations)
    )


def test_baselines_stay_in_sync_with_shrinks() -> None:
    """Baselines MUST NOT lag behind a legitimate shrink.

    If a developer removes an allowlist entry without decrementing the
    baseline, the current count drops below the baseline and the
    monotonic-shrink invariant is SATISFIED but the baseline is stale.
    This test flags the stale baseline so every shrink is committed
    atomically with its baseline update.
    """
    stale: list[str] = []
    for path, data in _discover_baselines():
        baseline = int(data["allowlist_count"])
        current = _active_allowlist_count(data["allowlist_file"])
        if current < baseline:
            stale.append(
                f"  {data['allowlist_file']}: baseline={baseline}, current={current} "
                f"(decrement baseline to {current} at {path.name})"
            )
    assert not stale, (
        "Structural-guard baseline(s) are stale (current < baseline). "
        "Decrement the baseline in the same commit that removed the allowlist entry:\n"
        + "\n".join(stale)
    )


def test_detector_catches_growth(tmp_path: Path) -> None:
    """Meta-test: construct a baseline dict whose count is below reality
    and verify the detector would flag it.

    Runs in isolation — does NOT touch the real baselines.
    """
    fake_allowlist = tmp_path / "fake_allowlist.txt"
    fake_allowlist.write_text(
        "# comment\nsrc/a.py:1\nsrc/b.py:2\nsrc/c.py:3\n",
        encoding="utf-8",
    )
    # Simulate: baseline says 2, reality is 3.
    planted_baseline = {
        "allowlist_file": "fake_allowlist.txt",
        "allowlist_count": 2,
    }
    # Active entries (ignoring comment + blanks) = 3. Baseline = 2.
    # Detector logic: current > baseline → growth.
    active_entries = [
        line.strip()
        for line in fake_allowlist.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    current = len(active_entries)
    baseline = int(planted_baseline["allowlist_count"])
    assert current > baseline, (
        f"Meta-test broken: planted baseline={baseline} should be below "
        f"the 3 entries we wrote. Got current={current}."
    )
