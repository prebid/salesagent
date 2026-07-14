"""Guard: structural-guard test files must not embed local beads tracking ids.

Project convention (CLAUDE.md § Structural Guards, feedback_no_beads_in_code):
code comments, docstrings, and messages reference GitHub issue/PR numbers
(``#1234``), never local beads ids (``salesagent-xxxx`` / ``beads-xxxx``) — they
don't resolve for outside contributors.

The ``tests/unit/test_guards_*.py`` family is the highest-risk locus for this:
guard files are authored fresh alongside the code they guard, and #1417's
re-review found two brand-new guard files (test_guards_rest_request_boundary.py,
test_guards_bdd_no_duplicate_elif_branches.py) that embedded beads ids in their
docstrings and assertion messages. This guard keeps every guard-family file
clean so a new one cannot reintroduce the pattern.

Scope note: this guard covers ONLY the guard-family files (which never contain
xfail-ledger data), so it stays clear of the load-bearing ledger reason strings
elsewhere in the tree. A legitimate literal (e.g. a future guard that itself
detects beads ids) can exempt a single line with a trailing ``# noqa: beads-id``.
"""

import re
from pathlib import Path

from tests.unit._architecture_helpers import REPO_ROOT

# Two fragments joined at runtime so this guard's own pattern definition does
# not textually contain a scannable id.
_BEADS = re.compile(r"salesagent-" + r"[a-z0-9]{3,}|beads-" + r"[a-z0-9]+")
_EXEMPT = re.compile(r"#\s*noqa:\s*beads-id")

_GUARD_DIR = REPO_ROOT / "tests" / "unit"
_THIS_FILE = Path(__file__).resolve()


def _guard_family_files():
    for path in sorted(_GUARD_DIR.glob("test_guards_*.py")):
        if path.resolve() != _THIS_FILE:
            yield path


def test_guard_files_have_no_beads_ids():
    offenders = []
    for path in _guard_family_files():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if _EXEMPT.search(line):
                continue
            if _BEADS.search(line):
                rel = path.relative_to(REPO_ROOT)
                offenders.append(f"{rel}:{lineno}: {line.strip()}")
    assert not offenders, (
        "Guard-family test files must reference GitHub issue/PR numbers, not local "
        "beads ids. Replace each with a #<gh> reference (or exempt a legitimate "
        "literal with a trailing '# noqa: beads-id'):\n" + "\n".join(offenders)
    )
