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

import ast
import sys
from pathlib import Path

from count_ratchet import (
    iter_python_sources,
    json_baseline_io,
    parse_ratchet_args,
    resolve_ratchet_paths,
    run_count_ratchet,
)

BASELINE_FILE = ".admin-raw-session-baseline"
ADMIN_DIR = "src/admin"
MAIN_REF = "origin/main"
KEYS = ("admin_get_db_session", "admin_session_add")

#: Receivers of ``.add(`` under src/admin that ARE a database session, so the call is
#: raw-session debt this ratchet is paying down.
SESSION_RECEIVERS = frozenset({"db_session", "session"})

#: Receivers of ``.add(`` that are NOT sessions. Every one of these is a set, and
#: ``set.add`` has nothing to do with the UoW migration. Pinned rather than filtered by a
#: name pattern so a new receiver lands in neither set and forces a decision instead of
#: being silently classified by whichever regex happens to match it.
NON_SESSION_RECEIVERS = frozenset(
    {
        "ancestor_ids",
        "countries",
        "formats",
        "indices",
        "next_ids",
        "processed_currencies",
        "seen_tenant_ids",
        "selected_format_ids",
        "sizes",
    }
)


class UnclassifiedReceiver(Exception):
    """A ``.add(`` receiver in neither pin — refuse to guess which side it counts on."""


def _receiver_name(node: ast.Call) -> str:
    """The receiver of an attribute call, as written."""
    value = node.func.value  # type: ignore[union-attr]  # callers gate on ast.Attribute
    return value.id if isinstance(value, ast.Name) else ast.unparse(value)


def count_raw_session_usage(repo_root: Path) -> dict[str, int]:
    r"""Count raw-session CALL SITES under src/admin, from the AST.

    AST, not a regex over the text, and the difference is measurable rather than
    theoretical. The regex this replaces was ``\bget_db_session\\(``, which counted
    ``operations.py:403`` — the words "and get_db_session()'s exit closes the shared
    thread-scoped" inside a COMMENT — as one unit of raw-session debt. The baseline was
    190 against 189 real call sites, so one unit of the debt this ratchet exists to pay
    down was payable by editing a comment (salesagent-b341x.19).

    Its sibling ``\b(?:db_)?session\\.add\\(`` had the mirror-image hazard and, measured,
    no present instance: no receiver under src/admin is spelled ``local_session`` today,
    so renaming one would have silently lowered the count. A parse cannot be fooled by
    either, and the receiver pins below turn the remaining judgment — "is this receiver a
    session?" — into a reviewable list rather than a regex nobody re-reads.
    """
    counts = dict.fromkeys(KEYS, 0)
    for path, text in iter_python_sources(repo_root / ADMIN_DIR):
        try:
            tree = ast.parse(text)
        except SyntaxError:  # pragma: no cover - a broken tree fails elsewhere, loudly
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name) and func.id == "get_db_session":
                counts["admin_get_db_session"] += 1
            elif isinstance(func, ast.Attribute) and func.attr == "get_db_session":
                counts["admin_get_db_session"] += 1
            elif isinstance(func, ast.Attribute) and func.attr == "add":
                receiver = _receiver_name(node)
                if receiver in SESSION_RECEIVERS:
                    counts["admin_session_add"] += 1
                elif receiver not in NON_SESSION_RECEIVERS:
                    raise UnclassifiedReceiver(
                        f"{path}:{node.lineno}: `{receiver}.add(` is in neither SESSION_RECEIVERS "
                        f"nor NON_SESSION_RECEIVERS. Add it to whichever it is — a session receiver "
                        f"is ratcheted debt, a set is not — rather than leaving the counter to guess."
                    )
    return counts


def main() -> int:
    args = parse_ratchet_args("Check that src/admin raw-session call counts do not increase")
    repo_root, _src_path, baseline_file = resolve_ratchet_paths(baseline_name=BASELINE_FILE)
    read_baseline, write_baseline = json_baseline_io(KEYS)

    return run_count_ratchet(
        keys=KEYS,
        current=count_raw_session_usage(repo_root),
        baseline_file=baseline_file,
        update_baseline=args.update_baseline,
        repo_root=repo_root,
        count_upstream=count_raw_session_usage,
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
