"""Doc-drift guard: the 6 critical invariants match between the folder
CLAUDE.md (source of truth) and the root CLAUDE.md banner.

Per ``flask-to-fastapi-foundation-modules.md §11.34``: editor drift — a
change to one file and not the other — is caught at CI time rather
than at "why is Alice's blocker 5 different from Bob's blocker 5?".

The assertion is fuzzy-substring, not byte-equal: the root banner uses
compact phrasing while the folder CLAUDE.md gives 2-3 sentences per
invariant. We assert the 6 title phrases (the bolded first phrase of
each numbered bullet) agree on the load-bearing keyword or a recognized
synonym class.

Mapping the root ``CLAUDE.md`` bullets (as of 2026-04-19) to the folder
CLAUDE.md §Critical Invariants — both are exactly 6 items. Each row's
``required_keyword`` is a load-bearing substring that MUST appear in
BOTH the root title and the folder title. The keyword is deliberately
short so paraphrases across files still pass.

Per ``.claude/notes/flask-to-fastapi/L0-implementation-plan-v2.md §L0-23``
inventory row 39.
"""

from __future__ import annotations

import pytest

from tests.unit.architecture._doc_parser import (
    FOLDER_CLAUDE_MD,
    ROOT_CLAUDE_MD,
    extract_invariant_titles,
    read_text,
)

# Per-invariant keyword: MUST appear in BOTH titles (case-insensitive).
# Order is the canonical 1-6 ordering per folder CLAUDE.md §Critical Invariants.
INVARIANT_KEYWORDS: tuple[str, ...] = (
    "url_for",  # #1 url_for / script_root
    "apirouter",  # #2 trailing-slash / APIRouter flags
    "accept-aware",  # #3 AdCPError Accept-aware handler (root banner says "Accept-aware")
    "sync",  # #4 sync def L0-L4
    "middleware order",  # #5 middleware ordering — but root CLAUDE.md says "Middleware order:"
    "oauth",  # #6 OAuth redirect URIs
)


# More flexible per-index keyword sets — if any one of the synonyms appears
# in both titles, the invariant is considered in-sync.
INVARIANT_SYNONYMS: tuple[tuple[str, ...], ...] = (
    ("url_for", "script_root", "templates use"),
    ("apirouter", "redirect_slashes", "include_in_schema"),
    ("accept-aware", "exception_handler", "adcperror"),
    ("sync", "async def", "def "),  # the "sync def L0-L4" invariant
    ("middleware order", "approximated", "csrf"),
    ("oauth", "redirect uri", "byte-immutable"),
)


def test_both_claude_md_files_exist() -> None:
    """The root banner and the folder source-of-truth must both exist."""
    assert ROOT_CLAUDE_MD.exists(), f"Missing root CLAUDE.md at {ROOT_CLAUDE_MD}"
    assert FOLDER_CLAUDE_MD.exists(), f"Missing folder CLAUDE.md at {FOLDER_CLAUDE_MD}"


def test_root_claude_md_has_six_invariants() -> None:
    """Root ``CLAUDE.md`` banner enumerates 6 invariants."""
    titles = extract_invariant_titles(read_text(ROOT_CLAUDE_MD))
    assert len(titles) == 6, (
        f"Root CLAUDE.md should have 6 numbered invariant titles under "
        f"§Active Migration; got {len(titles)}: {titles}"
    )


def test_folder_claude_md_has_six_invariants() -> None:
    """Folder ``CLAUDE.md`` §Critical Invariants enumerates 6 invariants."""
    titles = extract_invariant_titles(read_text(FOLDER_CLAUDE_MD))
    assert len(titles) == 6, f"Folder CLAUDE.md should have 6 numbered invariants; " f"got {len(titles)}: {titles}"


@pytest.mark.parametrize(
    "index,synonyms",
    list(enumerate(INVARIANT_SYNONYMS)),
    ids=[f"invariant-{i + 1}" for i in range(6)],
)
def test_invariant_shares_a_keyword_across_files(index: int, synonyms: tuple[str, ...]) -> None:
    """Each invariant has at least one shared synonym in both titles.

    A 0-hit result means the two files have drifted: one was edited
    to a new phrasing, the other was not. Grep for the invariant
    number in both files to reconcile.
    """
    root_titles = extract_invariant_titles(read_text(ROOT_CLAUDE_MD))
    folder_titles = extract_invariant_titles(read_text(FOLDER_CLAUDE_MD))
    if len(root_titles) <= index or len(folder_titles) <= index:
        pytest.skip(f"Not enough invariants to compare index {index}")
    root = root_titles[index]
    folder = folder_titles[index]
    hits = [s for s in synonyms if s in root and s in folder]
    assert hits, (
        f"Invariant #{index + 1} has drifted between root and folder CLAUDE.md.\n"
        f"  root:   {root!r}\n"
        f"  folder: {folder!r}\n"
        f"  expected at least one shared keyword from: {synonyms}"
    )
