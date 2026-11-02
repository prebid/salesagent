#!/usr/bin/env python3
"""
Pre-commit hook to detect and prevent code duplication using pylint's similarities checker.

Enforces a ratcheting approach — the duplication count can only go down, never up:
- New duplicated blocks fail the build immediately
- Fixing existing duplication automatically lowers the baseline
- Separate baselines for src/ and tests/

Uses pylint R0801 (duplicate-code) with these filters:
- Ignores imports, docstrings, comments, and function signatures
- Minimum 6 similar lines to trigger (catches copy-paste-modify patterns)

Uses shared ``count_ratchet`` for the create/compare/auto-lower skeleton, CLI
prelude, and JSON baseline codec; this module owns the pylint count method only.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from count_ratchet import (
    json_baseline_io,
    parse_ratchet_args,
    resolve_ratchet_paths,
    run_count_ratchet,
    run_counting_tool,
)

BASELINE_FILE = ".duplication-baseline"
SCOPES = ("src", "tests", "scripts")

#: pylint's exit status is a bitmask: 1 fatal, 2 error, 4 warning, 8 refactor,
#: 16 convention, 32 usage error. ``--disable=all --enable=R0801`` leaves
#: refactor as the only category that can fire, so a COMPLETE run exits 0 (no
#: duplication) or 8 (duplication found) and nothing else.
COMPLETE_RETURNCODES = frozenset({0, 8})

#: pylint prints its score once, after the entire run. A process that died on
#: one module — or was killed — never reaches it, which is the difference an
#: exit status cannot express: the old guard was ``returncode & 33 and count
#: == 0``, and a crash that had already emitted R0801 lines satisfied neither
#: half, so the partial tally was returned as the answer (salesagent-b341x.20).
SCORE_SENTINEL = "Your code has been rated at"


def _pylint_finished(stdout: str) -> str | None:
    """The score line, or ``None`` when pylint never got to print one."""
    for line in reversed(stdout.splitlines()):
        if line.startswith(SCORE_SENTINEL):
            return line.strip()
    return None


#: A pylint message line: ``path:line:col: R0801: Similar lines in N files``. Anchored on
#: the CODE FIELD, not on the bare id, because pylint echoes the duplicated source under
#: each message — so a duplicated block that itself mentions R0801 (this tree has 15 such
#: literals, in comments explaining the DRY ratchet) would be tallied as extra duplication
#: by a substring count. Measured at the time of the change: substring and message counts
#: agree exactly, 29/29 on src and 61/61 on tests, which is what makes replacing the
#: method provably baseline-neutral rather than a silent re-scoping (salesagent-b341x.19).
_R0801_MESSAGE = re.compile(r"^.*?:\d+:\d+: R0801:", re.MULTILINE)


def _count_r0801_messages(stdout: str) -> int:
    """Count pylint R0801 MESSAGES, not occurrences of the string "R0801"."""
    return len(_R0801_MESSAGE.findall(stdout))


def count_duplications(directory: str) -> int:
    """Count pylint R0801 violations in a directory, or refuse to return a number."""
    # Similarity tuning (min-similarity-lines, ignore-imports, etc.) lives in
    # pyproject.toml [tool.pylint.similarities] — single source of truth.
    cmd = [
        sys.executable,
        "-m",
        "pylint",
        "--disable=all",
        "--enable=R0801",
        directory,
    ]
    result = run_counting_tool(
        cmd,
        cwd=Path(__file__).parent.parent,
        label=f"pylint R0801 {directory}",
        accepts_returncode=COMPLETE_RETURNCODES.__contains__,
        completion_marker=_pylint_finished,
    )
    return _count_r0801_messages(result.stdout or "")


def main() -> int:
    args = parse_ratchet_args("Check that code duplication count doesn't increase")
    repo_root, _src_path, baseline_file = resolve_ratchet_paths(baseline_name=BASELINE_FILE)
    read_baseline, write_baseline = json_baseline_io(SCOPES)

    print("Scanning for code duplication (pylint R0801)...")
    current = {
        "src": count_duplications("src/"),
        "tests": count_duplications("tests/"),
        "scripts": count_duplications("scripts/"),
    }

    return run_count_ratchet(
        keys=SCOPES,
        current=current,
        baseline_file=baseline_file,
        update_baseline=args.update_baseline,
        repo_root=repo_root,
        # No count_upstream: a pylint R0801 pass over an extracted upstream
        # tree doubles this hook's runtime. The upstream baseline FILE is the
        # ceiling — which is a ceiling this hook had none of until now.
        read_baseline=read_baseline,
        write_baseline=write_baseline,
        increase_header="Code duplication increased! DRY is a non-negotiable invariant.",
        increase_hints=(
            "Extract repeated logic into shared helper functions.",
            "",
            "To inspect violations:",
            "  uv run pylint --disable=all --enable=R0801 src/",
            "  uv run pylint --disable=all --enable=R0801 tests/",
        ),
        format_key=lambda scope: f"{scope}/",
        unit="duplicate blocks",
    )


if __name__ == "__main__":
    sys.exit(main())
