#!/usr/bin/env python3
"""
Quality-ci hook: ratchet raw-session usage in src/admin.

The admin blueprints predate the repository/UoW pattern and talk to the
database directly — the repository-pattern guard family covers ``_impl``
functions and tests, so this surface had NO ratchet pressure and the debt kept
producing real defects (unhandled uniqueness races, driver text leaking into
operator responses, check-then-write shapes). Owner direction 2026-07-29: admin
code must migrate onto repositories/UoW; until each blueprint moves, these
counts may only shrink.

- Track ``get_db_session(`` and inline ``.add(`` (session/db_session receivers)
  counts under ``src/admin``
- Fail only when a count increases (new raw-session debt)
- Auto-lower the baseline when a count decreases
- Compares each baseline key against origin/main once the file exists there

Uses shared ``count_ratchet`` for the skeleton and JSON codec, mirroring
check_ruff_complexity_count.py (incl. the origin/main soft-land rule for keys
main does not carry yet).
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

BASELINE_FILE = ".admin-raw-session-baseline"
ADMIN_DIR = "src/admin"
MAIN_REF = "origin/main"
KEYS = ("admin_get_db_session", "admin_session_add")

_GET_DB_SESSION = re.compile(r"\bget_db_session\(")
_SESSION_ADD = re.compile(r"\b(?:db_)?session\.add\(")


def count_raw_session_usage(repo_root: Path) -> dict[str, int]:
    """Count raw-session call sites under src/admin."""
    counts = dict.fromkeys(KEYS, 0)
    for path in sorted((repo_root / ADMIN_DIR).rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        counts["admin_get_db_session"] += len(_GET_DB_SESSION.findall(text))
        counts["admin_session_add"] += len(_SESSION_ADD.findall(text))
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
                print("Admin raw-session baseline raised vs origin/main!", file=sys.stderr)
                raised = True
            print(
                f"  {key}: {MAIN_REF}={main_baseline[key]} local={local[key]} (+{local[key] - main_baseline[key]})",
                file=sys.stderr,
            )
    if raised:
        print("", file=sys.stderr)
        print("The admin raw-session baseline may only shrink. Route the access", file=sys.stderr)
        print("through a repository/UoW instead.", file=sys.stderr)
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
    args = parse_ratchet_args("Check that src/admin raw-session call counts do not increase")
    repo_root, _src_path, baseline_file = resolve_ratchet_paths(baseline_name=BASELINE_FILE)
    read_baseline, write_baseline = json_baseline_io(KEYS)

    current = count_raw_session_usage(repo_root)
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
        increase_header="Admin raw-session count increased!",
        increase_hints=(
            "New admin code must not call get_db_session()/session.add() directly —",
            "route data access through a repository/UoW (CLAUDE.md pattern #3).",
            "",
            "To inspect:",
            "  git diff origin/main -- src/admin",
            "  uv run python .pre-commit-hooks/check_admin_raw_session_count.py --update-baseline",
        ),
    )


if __name__ == "__main__":
    sys.exit(main())
