"""Guard: no stale AdCP citations — pre-GA markers, and the dangling prose root.

Two diseases, one scanner. Both are "a citation that looks authoritative and
is not", and both regressed by hand before a guard existed.

Stale pre-GA markers (#1417)
----------------------------
AdCP 3.1.0 and 3.1.1 are published as GA compliance dirs. Comments that still
cite the pre-GA ``3.1.0-rc.12`` storyboard as "latest published compliance", or
assert "no GA 3.1.0 dir exists yet", are now factually wrong and mislead the
next reader about which spec artifact grades the pinned behavior.

This regressed once (#1417 re-review): a citation-refresh commit enumerated 8
sites but touched 7, leaving stale ``rc.12`` / "no GA 3.1.0" citations in
tests/bdd/features/BR-UC-002-media-buy-status-dual-emit.feature. "Done" was a
subjective "refreshed the citations" instead of a grep that must return empty.

The dangling prose root (#1757)
-------------------------------
Spec prose is read from the repository-root ``docs/`` tree at the pinned tag.
The built ``dist/docs/`` tree stops at ``3.1.0``, so a citation naming
``dist/docs/3.1.1/`` resolves at NO tag — it is not stale, it is unresolvable,
and ``git cat-file -e`` on it exits non-zero. Five such citations sat in
tests/bdd/features/BR-UC-010-discover-seller-capabilities.feature and were
corrected by hand.

That file is GENERATED, which is what makes a guard load-bearing rather than
tidy: a regeneration run reinstates whatever the generator emits, and the
generator is not in this repository (searched: docs/test-obligations/, its
bdd-traceability.yaml, scripts/, src/, tests/, and the surface skill and
formula — nothing writes these ``@source`` headers). Until it is found and the
correction mirrored into it, this guard is what turns a silent regression into
a red test.

Why the marker is narrow, and must stay narrow
----------------------------------------------
Only the ``3.1.1`` root dangles. ``dist/docs/3.1.0``, and the ``-rc`` and
``-beta`` roots, all RESOLVE at ``v3.1.1`` and are legitimate citations — a
marker on ``dist/docs`` broadly would redden working ones, and a marker on
``dist/`` would redden 323 lines, since ``dist/schemas/3.1.1`` and
``dist/compliance/3.1.1`` both keep version-numbered roots.

The marker also has to tell a CITATION from a WARNING. Two files inside the
scan roots name the dangling root precisely in order to refuse it —
docs/adcp-spec-version.md and src/core/signing/request_verifier_middleware.py
— and reddening those would delete the warning to satisfy the guard. The
discriminator is what FOLLOWS the root: a citation continues into a path
(``dist/docs/3.1.1/building/...``), while a warning stops at the trailing
slash and closes its quoting (``there is no ``dist/docs/3.1.1/`` at that
tag``). Requiring a path character after the slash separates them with no
allowlist at all, which matters because an allowlist may only shrink and this
one would need entries for prose that is doing the right thing.
"""

import re
from pathlib import Path

from tests.unit._architecture_helpers import REPO_ROOT, iter_git_tracked_files

# The two disease markers from #1417 (mirrors the task's acceptance grep
# `rc\.12|no GA 3\.1\.0`). Kept as separate strings so this guard file itself
# does not textually contain the joined pattern it scans for.
_STALE_MARKERS = re.compile(r"rc\.12|no GA 3\.1\.0")

# The dangling prose root (#1757). The trailing character class is the whole
# point: it requires the root to continue into a PATH, so a line that names the
# root to warn against it (next char is a quote or a space) does not match.
_DANGLING_PROSE_ROOT = re.compile(r"dist/docs/3\.1\.1/[A-Za-z0-9_-]")

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


def _offending_lines(marker: re.Pattern[str]) -> list[str]:
    """Every scanned line matching *marker*, as ``path:lineno: text``.

    One implementation for both markers. A second copy that walked the files
    again is how the two diseases end up scanned by rules that quietly differ
    on which files they cover.
    """
    offenders = []
    for path, rel in _scanned_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if marker.search(line):
                offenders.append(f"{rel}:{lineno}: {line.strip()}")
    return offenders


def test_no_stale_rc_or_pre_ga_citations():
    offenders = _offending_lines(_STALE_MARKERS)
    assert not offenders, (
        "Stale pre-GA AdCP citations found (GA 3.1.0/3.1.1 are published). "
        "Refresh these to cite the GA storyboard:\n" + "\n".join(offenders)
    )


def test_no_citations_under_the_dangling_prose_root():
    offenders = _offending_lines(_DANGLING_PROSE_ROOT)
    assert not offenders, (
        "Citation(s) under a spec prose root that resolves at no tag. "
        "dist/docs/ stops at 3.1.0, so dist/docs/3.1.1/<path> cannot be read at "
        "v3.1.1. Prose for the pinned version lives in the repository-root docs/ "
        "tree at the tag — for example docs/protocol/get_adcp_capabilities.mdx. "
        "Verify the replacement with `git cat-file -e v3.1.1:<path>` before "
        "relying on it, and re-derive any line anchors against the new file "
        "rather than carrying the old ones over:\n" + "\n".join(offenders)
    )


def test_the_dangling_root_marker_still_matches_a_citation():
    """The marker must match a real citation, or its green means nothing.

    A citation guard that matches nothing passes for the same reason a broken
    one does. This pins the discriminator in BOTH directions against the exact
    lines that motivated it: the pre-fix BR-UC-010 citation form must match,
    and the two in-scan-root warning forms must not.
    """
    cited = "    # @source repo=adcp ref=v3.1.1 path=dist/docs/3.1.1/building/implementation/x.mdx (L23)"
    assert _DANGLING_PROSE_ROOT.search(cited), "marker no longer matches a known-bad citation"

    for warning in (
        "does not: `dist/docs/` stops at `3.1.0`, so `dist/docs/3.1.1/` resolves at no tag. Prose",
        "``v3.1.1:docs/building/by-layer/L1/security.mdx`` (there is no ``dist/docs/3.1.1/`` at",
    ):
        assert not _DANGLING_PROSE_ROOT.search(warning), f"marker reddens a warning form: {warning}"

    for resolving in (
        "``dist/docs/3.1.0/reference/url-canonicalization.mdx``",
        "``dist/docs/3.1.0-rc.15/building/by-layer/L1/security.mdx``",
        "# dist/docs/3.1.0-beta.3/ tree",
    ):
        assert not _DANGLING_PROSE_ROOT.search(resolving), f"marker reddens a resolving root: {resolving}"
