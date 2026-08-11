#!/usr/bin/env python3
"""
Quality-ci hook: ratchet local-beads-id citations in FIXME/TODO comments.

CLAUDE.md requires deferral comments to cite a GitHub issue/PR number, never a
local beads id (beads ids don't resolve for outside contributors — see
`FIXME(#<gh-issue>)` in "Rules for guards"). That rule had zero enforcement:
review round 2 of PR #1721 found 138 live `FIXME(salesagent-xxxx)` /
`FIXME(bd-xxxx)` citations already in the tree (19 in src/, 120 in tests/) with
no hook catching new ones. A binary hook would fail on every commit today —
this ratchets instead, per owner direction (2026-08-05): count may only shrink.

- Track `FIXME(salesagent-`/`FIXME(bd-`/`TODO(salesagent-`/`TODO(bd-` counts,
  per tree (src/ vs tests/) so a new violation in one tree can't hide behind a
  removal in the other
- Fail only when a count increases (new local-beads-id citation)
- Auto-lower the baseline when a count decreases
- Compares each baseline key against origin/main once the file exists there

Uses shared ``count_ratchet`` for the skeleton and JSON codec, mirroring
check_admin_raw_session_count.py (incl. the origin/main soft-land rule for
keys main does not carry yet).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from count_ratchet import (
    json_baseline_io,
    parse_ratchet_args,
    read_json_baseline,
    resolve_ratchet_paths,
    run_count_ratchet,
)

BASELINE_FILE = ".fixme-citation-baseline"
TRACKED_DIRS = ("src", "tests")
MAIN_REF = "origin/main"
KEYS = ("src_fixme_beads", "tests_fixme_beads", "src_quoted_beads", "tests_quoted_beads")

#: ``FIXME(salesagent-x)`` / ``TODO(bd-x)`` -- the call-syntax spelling. Ratcheted debt.
_LOCAL_BEADS_CITATION = re.compile(r"\b(?:FIXME|TODO)\(\s*(?:salesagent|bd)-")

#: A bare local beads id used as a quoted VALUE -- an allowlist tuple's ticket element, or an
#: id embedded in a developer-facing message. CLAUDE.md's rule ("cite a GitHub issue, never a
#: local beads id -- beads ids don't resolve for outside contributors") applies just as much
#: there, but the call-syntax pattern above cannot see it: that is exactly how
#: ``("tests/harness/creative_sync.py", "set_run_async_result", "salesagent-jlug")`` sat
#: invisible to this hook (#1721 review F4).
#:
#: Baselined at ZERO, deliberately. Both pre-existing sites are fixed in the same change, so
#: this half is a HARD GATE rather than another ratcheted debt number to be paid down later.
_QUOTED_BEADS_ID = re.compile(r"""(['"])\s*(?:salesagent|bd)-[0-9a-z]+(?:\.[0-9a-z]+)*\s*\1""")


def count_beads_citations(repo_root: Path) -> dict[str, int]:
    """Count local-beads-id citations under src/ and tests/, in both spellings."""
    counts = dict.fromkeys(KEYS, 0)
    for tracked_dir in TRACKED_DIRS:
        fixme_key = f"{tracked_dir}_fixme_beads"
        quoted_key = f"{tracked_dir}_quoted_beads"
        for path in sorted((repo_root / tracked_dir).rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            counts[fixme_key] += len(_LOCAL_BEADS_CITATION.findall(text))
            counts[quoted_key] += len(_QUOTED_BEADS_ID.findall(text))
    return counts


def read_main_baseline(repo_root: Path) -> dict[str, int] | None:
    """Load origin/main's baseline, or None before the file first lands on main.

    Keys absent from main's file are omitted rather than read as 0 — a key main
    never tracked has no ceiling to be raised against (same soft-land rule as
    check_ruff_complexity_count.read_main_baseline).
    """
    result = subprocess.run(
        ["git", "show", f"{MAIN_REF}:{BASELINE_FILE}"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
        if not isinstance(data, dict):
            raise ValueError(f"baseline must be a JSON object, got {type(data).__name__}")
        return {key: int(data[key]) for key in KEYS if key in data}
    except (ValueError, TypeError) as e:
        print(f"ERROR: {MAIN_REF}:{BASELINE_FILE} is not a valid JSON object: {e}", file=sys.stderr)
        raise SystemExit(1) from e


def check_baseline_not_raised(repo_root: Path, local: dict[str, int]) -> int:
    """Fail when any local baseline key exceeds origin/main's (once main has the file)."""
    main_baseline = read_main_baseline(repo_root)
    if main_baseline is None:
        return 0
    raised = False
    for key in KEYS:
        if key not in main_baseline:
            continue
        if local[key] > main_baseline[key]:
            if not raised:
                print("FIXME beads-citation baseline raised vs origin/main!", file=sys.stderr)
                raised = True
            print(
                f"  {key}: {MAIN_REF}={main_baseline[key]} local={local[key]} (+{local[key] - main_baseline[key]})",
                file=sys.stderr,
            )
    if raised:
        print("", file=sys.stderr)
        print("The FIXME beads-citation baseline may only shrink. Cite a GitHub", file=sys.stderr)
        print("issue/PR number instead — FIXME(#<gh-issue>) — beads ids don't", file=sys.stderr)
        print("resolve for outside contributors.", file=sys.stderr)
        return 1
    return 0


def raise_probe_counts(
    *,
    baseline: dict[str, int] | None,
    current: dict[str, int],
    update_baseline: bool,
) -> dict[str, int]:
    """Per-key values probed against origin/main before any baseline write."""
    if baseline is None or update_baseline:
        return dict(current)
    return {key: min(baseline.get(key, 0), current[key]) for key in KEYS}


def main() -> int:
    args = parse_ratchet_args("Check that local-beads-id FIXME/TODO citation counts do not increase")
    repo_root, _src_path, baseline_file = resolve_ratchet_paths(baseline_name=BASELINE_FILE)
    read_baseline, write_baseline = json_baseline_io(KEYS)

    current = count_beads_citations(repo_root)
    baseline = read_json_baseline(baseline_file, KEYS)

    if (
        check_baseline_not_raised(
            repo_root,
            raise_probe_counts(baseline=baseline, current=current, update_baseline=args.update_baseline),
        )
        != 0
    ):
        return 1

    return run_count_ratchet(
        keys=KEYS,
        current=current,
        baseline_file=baseline_file,
        update_baseline=args.update_baseline,
        read_baseline=read_baseline,
        write_baseline=write_baseline,
        increase_header="FIXME beads-citation count increased!",
        increase_hints=(
            "New deferral comments must cite a GitHub issue/PR number, never a",
            "local beads id — beads ids don't resolve for outside contributors",
            "(CLAUDE.md 'Rules for guards'). Use FIXME(#<gh-issue>), file the",
            "issue first if one doesn't exist yet.",
            "",
            "To inspect:",
            "  git diff origin/main -- src/ tests/",
            "  uv run python .pre-commit-hooks/check_fixme_citation_count.py --update-baseline",
        ),
    )


if __name__ == "__main__":
    sys.exit(main())
