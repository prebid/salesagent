"""Guard: a living documentation page names a code location by symbol, never by line number.

A line number goes stale with the next edit to the file it points into, and nothing fails
when it does: the page keeps citing a line that now holds something else. A reader who
follows it lands on the wrong code and has no way to tell. The page must name the
function, class, test, constant, or section instead -- those survive an edit above them.

What counts as a line-number reference (in running text and in fenced code alike, because a
reference inside a code comment goes stale the same way):

- a file path with a line or a range: ``src/core/tools/media_buy_create.py:2331``,
  ``tests/harness/_realize.py:94-105``
- a line or a range in code font after a path: ``:992``, ``:4104-4135``
- a GitHub line anchor on a branch URL: ``/blob/main/...#L120``
- the same in words: ``line 54``, ``lines 2488, 2514-2518``

What does not: a permalink pinned to a commit SHA (``/blob/<40 hex>/...#L12``), which does
not move; a host and port (``localhost:8000``) or a URL port; a time (``10:04:10``); anything
else with no file extension before the colon. A port alone in code font (``:8000``) reads
exactly like a line, so a page writes it with its host or in words: port 8000.

The scan covers the living pages: ``docs/``, the Markdown at the repository root, every
``CLAUDE.md`` under ``src/`` and ``tests/``, every ``README.md`` under ``tests/``, and the
rules, skills and agents under ``.claude/``. Dated records are not scanned: they describe the
code at the moment they were written, so their line numbers are right for the commit they
cite. The scan never reaches ``archive/`` or the rest of ``.claude/``; the records inside the
scanned areas are ``DATED_RECORD_DIRS`` and ``DATED_RECORD_FILES``, the one list of them --
no second copy lives anywhere else.

Pages that cannot be fixed yet are held in ``BASELINE``, per page, and the counts may only
shrink: a page's count rising fails, a page outside the baseline gaining a reference fails,
and an entry above its page's count, or for a page that is gone, fails too, so every fix
lowers it. A baselined page carries ``FIXME_MARKER`` in an HTML comment at its top for as
long as it is in the baseline, and no longer.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

import pytest

from tests.unit._architecture_helpers import (
    REPO_ROOT,
    assert_violations_match_allowlist,
    format_failure,
    iter_git_tracked_files,
    rel,
)
from tests.unit._per_file_cap_guard import assert_capped_files_still_exist, assert_caps_only_shrink
from tests.utils.derived_population import assert_derived_population

_EXTENSIONS = "py|pyi|js|ts|html|j2|toml|ya?ml|ini|cfg|conf|template|sh|feature|sql|json|md|txt|c4"

# One alternation, four forms. A host and port, a time and a bare ``name:1`` have no listed
# extension before the colon, so the first form never reaches them.
LINE_REFERENCE = re.compile(
    "|".join(
        (
            rf"[A-Za-z0-9_./-]+\.(?:{_EXTENSIONS}):[0-9]+(?:-[0-9]+)?",
            r"`:[0-9]+(?:-[0-9]+)?`",
            # Branch names only. A 40-hex SHA is not one of them, so a pinned permalink never matches.
            r"/blob/(?:main|master|develop)/[^ )]*#L[0-9]+",
            r"\b[Ll]ines? [0-9]+",
        )
    )
)

#: Pages that describe the code as of a past moment. A line number in one is correct for the
#: commit it cites, so it is not scanned. Each prefix ends in ``/``. The records outside the
#: scanned areas, ``archive/`` and everything under ``.claude/`` but its rules, skills and
#: agents, need no entry: ``is_living_doc`` never reaches them.
DATED_RECORD_DIRS = (
    "docs/reports/",
    "docs/audits/",
    "docs/releases/",
    "docs/decisions/",
)

#: release-please writes it from commit subjects, so it is a record, not a page anyone edits.
DATED_RECORD_FILES = ("CHANGELOG.md",)

_CLAUDE_TOOLING_DIRS = frozenset({"rules", "skills", "agents"})

FIX_HINT = (
    "Name the function, class, test, constant, or section the line pointed at, instead of the line. "
    "Open the code, find that symbol, and write its name. If the code moved or is gone, rewrite or "
    "delete the sentence. Never replace one line number with another. A port is not a line: write it "
    "with its host (localhost:8000) or in words (port 8000)."
)

#: The issue that tracks the pages in ``BASELINE``. Each of them carries this marker in an
#: HTML comment at its top while it is baselined.
FIXME_MARKER = "FIXME(#2613)"

# FIXME(#2613): these pages predate the guard. Fix a page's references and lower its count in
# the same change; at zero, delete the entry and the page's FIXME comment.
#: Reference counts per page that the guard tolerates today. Counts only go down.
BASELINE: dict[str, int] = {
    "docs/design/bdd-harness-architecture.md": 84,
    "docs/design/error-architecture.md": 60,
    "docs/design/sdk-type-generation.md": 60,
    "docs/design/webhook-testing-architecture.md": 121,
    "docs/development/a2a-mcp-agent-flows.md": 37,
    "docs/development/request-lifecycle.md": 92,
    "docs/test-debt-bdd-strict-markers.md": 39,
}


def is_living_doc(path: str) -> bool:
    """Is the repo-relative *path* a Markdown page the guard scans?"""
    if not path.endswith(".md") or path in DATED_RECORD_FILES or path.startswith(DATED_RECORD_DIRS):
        return False
    parts = PurePosixPath(path).parts
    name = parts[-1]
    if len(parts) == 1 or parts[0] == "docs":
        return True
    if parts[0] in ("src", "tests") and name == "CLAUDE.md":
        return True
    if parts[0] == "tests" and name == "README.md":
        return True
    return parts[0] == ".claude" and parts[1] in _CLAUDE_TOOLING_DIRS


def living_doc_paths() -> list[Path]:
    """Every page the guard scans, from ``git ls-files``."""
    return [path for path in iter_git_tracked_files(REPO_ROOT) if is_living_doc(rel(path))]


def find_references(text: str) -> list[tuple[int, str]]:
    """Every ``(line in the page, matched text)`` in *text*."""
    return [
        (lineno, m.group()) for lineno, line in enumerate(text.splitlines(), 1) for m in LINE_REFERENCE.finditer(line)
    ]


def reference_lines(path: Path) -> list[int]:
    """The line in the page of each reference in *path*: the per-site shape the shared cap helpers count."""
    return [lineno for lineno, _ in find_references(path.read_text(encoding="utf-8"))]


def references_by_page() -> dict[str, list[tuple[int, str]]]:
    """Line-number references per living page, for the pages that hold any."""
    found: dict[str, list[tuple[int, str]]] = {}
    for path in living_doc_paths():
        if refs := find_references(path.read_text(encoding="utf-8")):
            found[rel(path)] = refs
    return found


def over_baseline(found: dict[str, list[tuple[int, str]]], baseline: dict[str, int]) -> list[str]:
    """Each page holding more references than *baseline* allows, followed by every reference it holds."""
    report: list[str] = []
    for page, refs in sorted(found.items()):
        if len(refs) > (allowed := baseline.get(page, 0)):
            report.append(f"{page} holds {len(refs)} line-number reference(s); BASELINE allows {allowed}:")
            report.extend(f"    {page}:{lineno}: {text}" for lineno, text in refs)
    return report


@pytest.mark.arch_guard
@pytest.mark.parametrize(
    "text",
    [
        "src/core/tools/media_buy_create.py:2331",
        "tests/harness/_realize.py:94-105",
        "BR-ADMIN-ACCOUNTS.feature:13-15",
        "see `tests/unit/test_architecture_bdd_wire_discipline.py` (`:992` onward)",
        "the AdCP branch uses (`tests/bdd/conftest.py`, `:4104-4135`)",
        "https://github.com/prebid/salesagent/blob/main/src/app.py#L120",
        "https://github.com/prebid/salesagent/blob/develop/src/app.py#L12-L30",
        "# defined at config/app.yaml:7",
        "through the map in `config/nginx/nginx-tls-test.conf.template:39-44`",
        "`_assert_compliant` (line 54) is outcome-aware",
        "for example lines 2488, 2514-2518",
        "Line 12 of the feature names the transports",
    ],
)
def test_detector_catches_a_line_number_reference(text: str) -> None:
    assert find_references(text), f"the detector let a line-number reference through: {text!r}"


@pytest.mark.arch_guard
@pytest.mark.parametrize(
    "text",
    [
        "https://github.com/prebid/salesagent/blob/" + "0123456789abcdef0123456789abcdef01234567" + "/src/app.py#L12",
        "Spec: https://github.com/adcontextprotocol/adcp/blob/{COMMIT}/dist/schemas/core/media-buy.json#L42",
        "curl http://localhost:8000/mcp/",
        "the admin UI listens on `localhost:8000`",
        "nginx terminates TLS on port 8443",
        "logged at 10:04:10 and again at 10:04:11",
        "src/core/tools/media_buy_create.py names the controller",
        "`tests/unit/test_architecture_bdd_wire_discipline.py` bans that key",
        "a 4,000-line conftest, a line-length of 120, and a pipeline 3 stages deep",
        "key: value",
    ],
)
def test_detector_passes_a_permitted_form(text: str) -> None:
    assert find_references(text) == [], f"the detector flagged a permitted form: {text!r}"


@pytest.mark.arch_guard
def test_detector_reports_the_line_in_the_page_and_the_matched_text() -> None:
    page = "first\nsecond cites src/app.py:12 and `:34`\nthird"
    assert find_references(page) == [(2, "src/app.py:12"), (2, "`:34`")]


@pytest.mark.arch_guard
@pytest.mark.parametrize(
    "path",
    [
        "README.md",
        "CLAUDE.md",
        "SOURCE.md",
        "docs/development/request-lifecycle.md",
        "docs/design/error-architecture.md",
        "src/core/tools/CLAUDE.md",
        "tests/CLAUDE.md",
        "tests/unit/CLAUDE.md",
        "tests/bdd/README.md",
        ".claude/rules/patterns/code-patterns.md",
        ".claude/skills/guard/SKILL.md",
        ".claude/agents/qc-validator.md",
    ],
)
def test_living_doc_is_scanned(path: str) -> None:
    assert is_living_doc(path)


@pytest.mark.arch_guard
@pytest.mark.parametrize(
    "path",
    [
        "archive/old-plan.md",
        ".claude/reviews/pr-1.md",
        ".claude/reports/audit.md",
        ".claude/merge-review/main.md",
        "docs/reports/coverage.md",
        "docs/audits/epic-8qela-triage.md",
        "docs/releases/v1.md",
        "docs/decisions/0001.md",
        "CHANGELOG.md",
        "docs/design/diagram.html",
        "src/a2a_server/README.md",
        ".claude/commands/team.md",
        "tests/unit/test_something.py",
    ],
)
def test_dated_record_or_other_file_is_not_scanned(path: str) -> None:
    assert not is_living_doc(path)


@pytest.mark.arch_guard
def test_every_dated_record_dir_is_a_directory_prefix() -> None:
    """``docs/reports`` without the slash would also exclude ``docs/reports-index.md``."""
    assert all(d.endswith("/") for d in DATED_RECORD_DIRS)


@pytest.mark.arch_guard
def test_the_scan_reads_a_real_population() -> None:
    """An empty walk finds no references and passes; this keeps it from passing over nothing."""
    assert_derived_population(
        living_doc_paths(),
        name="living documentation pages",
        derived_from="git ls-files filtered by is_living_doc",
        minimum=100,
    )


@pytest.mark.arch_guard
def test_over_baseline_fails_a_new_page_and_a_rise_but_not_a_page_within_its_count() -> None:
    found = {"docs/a.md": [(3, "x.py:1")], "docs/b.md": [(1, "x.py:1"), (2, "`:2`")]}
    assert over_baseline(found, {"docs/a.md": 1, "docs/b.md": 2}) == []
    assert over_baseline(found, {"docs/b.md": 2}) == [
        "docs/a.md holds 1 line-number reference(s); BASELINE allows 0:",
        "    docs/a.md:3: x.py:1",
    ]
    assert over_baseline(found, {"docs/a.md": 1, "docs/b.md": 1})[0].startswith("docs/b.md holds 2")


@pytest.mark.arch_guard
def test_a_lowered_count_and_a_deleted_page_fail_their_baseline_entries(tmp_path: Path) -> None:
    (tmp_path / "page.md").write_text("cites `src/app.py:12`\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="lower cap to 1"):
        assert_caps_only_shrink({"page.md": 2}, reference_lines, repo_root=tmp_path)
    with pytest.raises(AssertionError, match="gone.md"):
        assert_capped_files_still_exist({"gone.md": 1}, "BASELINE", repo_root=tmp_path)


@pytest.mark.arch_guard
def test_no_living_doc_gains_a_line_number_reference() -> None:
    report = over_baseline(references_by_page(), BASELINE)
    assert not report, format_failure(
        summary="Living documentation names code by line number:", violations=report, fix_hint=FIX_HINT
    )


@pytest.mark.arch_guard
def test_the_baseline_only_shrinks() -> None:
    """An entry above its page's count is room a new reference would land in unnoticed."""
    assert_caps_only_shrink(BASELINE, reference_lines, repo_root=REPO_ROOT)


@pytest.mark.arch_guard
def test_every_baseline_entry_is_a_living_page_that_still_needs_one() -> None:
    assert_capped_files_still_exist(BASELINE, "BASELINE", repo_root=REPO_ROOT)
    misplaced = sorted(page for page, allowed in BASELINE.items() if allowed < 1 or not is_living_doc(page))
    assert not misplaced, f"BASELINE entries at zero or for a page the guard does not scan; delete them: {misplaced}"


@pytest.mark.arch_guard
def test_a_page_carries_the_fixme_exactly_while_it_is_baselined() -> None:
    marked = {rel(path) for path in living_doc_paths() if FIXME_MARKER in path.read_text(encoding="utf-8")}
    assert_violations_match_allowlist(
        marked,
        set(BASELINE),
        fix_hint=f"A baselined page opens with <!-- {FIXME_MARKER}: ... -->; delete the comment with its entry.",
    )
