"""Guard: no stale AdCP release-candidate citations after GA publication.

AdCP 3.1.0 and 3.1.1 are published as GA compliance dirs. Comments that still
cite the pre-GA ``3.1.0-rc.12`` storyboard as "latest published compliance", or
assert "no GA 3.1.0 dir exists yet", are now factually wrong and mislead the
next reader about which spec artifact grades the pinned behavior.

This regressed once (#1417 re-review): a citation-refresh commit enumerated 8
sites but touched 7, leaving stale ``rc.12`` / "no GA 3.1.0" citations in
tests/bdd/features/BR-UC-002-media-buy-status-dual-emit.feature. "Done" was a
subjective "refreshed the citations" instead of a grep that must return empty.

This test makes that grep permanent: any ``rc.12`` or "no GA 3.1.0" marker in
tests/, docs/, or src/ fails the build so a stale citation cannot reappear.
"""

import re
from pathlib import Path

from tests.unit._architecture_helpers import REPO_ROOT, iter_git_tracked_files

# The two disease markers from #1417 (mirrors the task's acceptance grep
# `rc\.12|no GA 3\.1\.0`). Kept as separate strings so this guard file itself
# does not textually contain the joined pattern it scans for.
_STALE_MARKERS = re.compile(r"rc\.12|no GA 3\.1\.0")

_SCAN_ROOTS = ("tests", "docs", "src")
# Text extensions worth scanning; skips binaries/fixtures where a coincidental
# byte match is meaningless.
_TEXT_SUFFIXES = {".py", ".feature", ".md", ".yaml", ".yml", ".txt", ".rst"}

_THIS_FILE = Path(__file__).resolve()


def _scanned_files():
    for path in iter_git_tracked_files(REPO_ROOT):
        if path.resolve() == _THIS_FILE:
            continue  # this guard names the markers on purpose
        if path.suffix not in _TEXT_SUFFIXES:
            continue
        try:
            rel = path.relative_to(REPO_ROOT)
        except ValueError:
            continue
        if rel.parts and rel.parts[0] in _SCAN_ROOTS:
            yield path, rel


def test_no_stale_rc_or_pre_ga_citations():
    offenders = []
    for path, rel in _scanned_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _STALE_MARKERS.search(line):
                offenders.append(f"{rel}:{lineno}: {line.strip()}")
    assert not offenders, (
        "Stale pre-GA AdCP citations found (GA 3.1.0/3.1.1 are published). "
        "Refresh these to cite the GA storyboard:\n" + "\n".join(offenders)
    )
