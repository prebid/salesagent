#!/usr/bin/env python3
"""
Pre-commit hook to track and prevent increases in `# type: ignore` comments.

This hook enforces a ratcheting approach to type checking:
- Prevents new type: ignore comments from being added
- Tracks the current count in .type-ignore-baseline
- Automatically updates baseline when count decreases
- Compares the baseline VALUE against origin/main so a committed baseline
  raise cannot slip through green (the count-vs-local-baseline check alone
  is blind to it)
- Encourages gradual improvement of type safety

Uses shared ``count_ratchet`` for the create/compare/auto-lower skeleton, CLI
prelude, and int baseline codec; the origin/main raise guard stays here
(baseline-file integrity, not a count method).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from count_ratchet import (
    int_baseline_io,
    iter_python_sources,
    parse_ratchet_args,
    resolve_ratchet_paths,
    run_count_ratchet,
)

BASELINE_FILE = ".type-ignore-baseline"
SRC_DIR = "src"
MAIN_REF = "origin/main"
KEY = "type_ignores"
KEYS = (KEY,)


def count_type_ignores(src_path: Path) -> int:
    """Count all # type: ignore comments in Python files within src/."""
    count = 0
    pattern = re.compile(r"#\s*type:\s*ignore")

    for _path, content in iter_python_sources(src_path):
        count += len(pattern.findall(content))

    return count


def main() -> int:
    args = parse_ratchet_args("Check that # type: ignore count doesn't increase")
    repo_root, src_path, baseline_file = resolve_ratchet_paths(baseline_name=BASELINE_FILE)
    read_baseline, write_baseline = int_baseline_io(KEY)

    return run_count_ratchet(
        keys=KEYS,
        current={KEY: count_type_ignores(src_path)},
        baseline_file=baseline_file,
        update_baseline=args.update_baseline,
        repo_root=repo_root,
        parse_upstream=lambda text: {KEY: int(text.strip())},
        count_upstream=lambda tree: {KEY: count_type_ignores(tree / SRC_DIR)},
        read_baseline=read_baseline,
        write_baseline=write_baseline,
        increase_header="Type ignore count increased!",
        increase_hints=(
            "Fix the type errors instead of adding # type: ignore comments.",
            "Run: mypy src/your_file.py --config-file=mypy.ini",
        ),
        format_key=lambda _key: "type: ignore",
    )


if __name__ == "__main__":
    sys.exit(main())
