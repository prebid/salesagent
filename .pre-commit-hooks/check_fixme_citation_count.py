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

import re
import sys
from pathlib import Path

from count_ratchet import (
    iter_python_sources,
    json_baseline_io,
    parse_ratchet_args,
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
        for _path, text in iter_python_sources(repo_root / tracked_dir):
            counts[fixme_key] += len(_LOCAL_BEADS_CITATION.findall(text))
            counts[quoted_key] += len(_QUOTED_BEADS_ID.findall(text))
    return counts


def main() -> int:
    args = parse_ratchet_args("Check that local-beads-id FIXME/TODO citation counts do not increase")
    repo_root, _src_path, baseline_file = resolve_ratchet_paths(baseline_name=BASELINE_FILE)
    read_baseline, write_baseline = json_baseline_io(KEYS)

    return run_count_ratchet(
        keys=KEYS,
        current=count_beads_citations(repo_root),
        baseline_file=baseline_file,
        update_baseline=args.update_baseline,
        repo_root=repo_root,
        count_upstream=count_beads_citations,
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
