"""Shared helpers for the §11.34 doc-drift guards.

Each of the 6 doc-drift guards parses one or more Markdown files and
asserts a property (invariants numbering, OAuth URI whitelist, CSRF
implementation strategy, layer scope consistency, spike-table SSoT,
proxy-header flags in the canonical entrypoint).

Co-locating the shared I/O + regex helpers here keeps each guard file
focused on its assertion rather than file discovery. DRY per
``CLAUDE.md §DRY (Don't Repeat Yourself)``.

Per ``.claude/notes/flask-to-fastapi/flask-to-fastapi-foundation-modules.md §11.34``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
MIGRATION_NOTES = REPO_ROOT / ".claude" / "notes" / "flask-to-fastapi"
ROOT_CLAUDE_MD = REPO_ROOT / "CLAUDE.md"
FOLDER_CLAUDE_MD = MIGRATION_NOTES / "CLAUDE.md"
EXECUTION_PLAN_MD = MIGRATION_NOTES / "execution-plan.md"
IMPLEMENTATION_CHECKLIST_MD = MIGRATION_NOTES / "implementation-checklist.md"
FOUNDATION_MODULES_MD = MIGRATION_NOTES / "flask-to-fastapi-foundation-modules.md"

# Canonical set of OAuth callback URIs (Invariant 6). Any callback URI
# mentioned in the planning docs or production source MUST be in this
# set — otherwise the OAuth byte-immutability contract with Google
# Cloud Console is at risk of drift.
CANONICAL_OAUTH_URIS: frozenset[str] = frozenset(
    {
        "/admin/auth/google/callback",
        "/admin/auth/oidc/callback",
        "/admin/auth/gam/callback",
    }
)

# Tokens associated with the ABANDONED double-submit-cookie CSRF strategy.
# The L0 csrf_implementation_consistent guard flags any appearance OUTSIDE
# of rejection/comparison contexts (NOT/REJECTED/FORBIDDEN/abandoned).
ABANDONED_CSRF_TOKENS: tuple[str, ...] = (
    "adcp_csrf",
    "double-submit",
    "XSRF-TOKEN",
)

OAUTH_URI_REGEX: re.Pattern[str] = re.compile(r"(?P<uri>/(?:admin/)?auth/[A-Za-z0-9_\-]+/callback)")


def iter_planning_docs() -> Iterator[Path]:
    """Yield every .md file under the planning-notes folder (non-recursively).

    The planning folder has ~20 markdown files. Sub-folders like
    ``async-audit/`` are scanned too because drift can leak there.
    """
    if not MIGRATION_NOTES.exists():
        return
    yield from sorted(MIGRATION_NOTES.rglob("*.md"))


def read_text(path: Path) -> str:
    """Read a markdown file as utf-8 text; return '' if missing."""
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


_REJECTION_CONTEXT_WORDS: tuple[str, ...] = (
    "NOT",
    "REJECTED",
    "FORBIDDEN",
    "abandoned",
    "rejected",
    "forbidden",
    "do not use",
    "Do not use",
    # Meta-guard context — when a doc-drift guard DEFINITION lists the
    # token it catches, the context explicitly labels the mention as a
    # failure-plant scenario ("Plant X", "catch X"), or names a guard
    # test file that exists TO reject the token.
    "Plant ",
    "catches",
    "Doc-drift:",
    # Historical-audit context — async-audit reports quote the
    # pre-Decision-5 plan for traceability; those mentions are not
    # drift but preservation of how the plan evolved.
    "async-audit",
    "historical",
    # Hypothetical / contingent mentions — "if we ever switched to X"
    # is narrative acknowledgment that X is NOT the current strategy.
    "if we ever",
    "if we had",
    "hypothetical",
    "would require",
)


def has_rejection_context(text: str, index: int, window_chars: int = 200) -> bool:
    """Return True if a rejection/negation word is within ``window_chars`` of ``index``.

    Used to allow mentions of forbidden patterns (old CSRF strategy, stale
    OAuth URIs) in documentation that explains WHY they are rejected.
    """
    start = max(0, index - window_chars)
    end = min(len(text), index + window_chars)
    snippet = text[start:end]
    return any(word in snippet for word in _REJECTION_CONTEXT_WORDS)


def extract_invariant_titles(text: str) -> list[str]:
    """Extract the 6 numbered invariant titles from a CLAUDE.md-like doc.

    Matches lines of the form ``1. **Title phrase** — rest…`` in the
    invariants block that directly follows a heading matching one of:

    - ``## Critical Invariants``                     (folder CLAUDE.md)
    - ``## 🚧 Active Migration: Flask → FastAPI …``  (root CLAUDE.md)

    The block is bounded by the NEXT top-level ``##`` heading. This
    prevents accidental collisions with unrelated numbered lists
    further down each file (e.g. the "Working with This Codebase"
    tutorial block in the root CLAUDE.md).

    Returns the 6 title phrases (the first bolded phrase of each
    numbered bullet), lowercased and punctuation-stripped, so fuzzy
    comparison across files ignores wording-only differences.
    """
    heading_patterns = [
        r"## Critical Invariants",
        r"## 🚧 Active Migration.*",
    ]
    start = -1
    for pat in heading_patterns:
        m = re.search(pat, text)
        if m:
            start = m.end()
            break
    if start < 0:
        return []
    # Bound the block by the NEXT ``## `` top-level heading so stray
    # numbered lists further down don't bleed into this slice.
    tail = text[start:]
    next_heading = re.search(r"\n## \S", tail)
    if next_heading:
        tail = tail[: next_heading.start()]

    titles: list[str] = []
    for num in range(1, 7):
        # Two shapes:
        #   4. **Leading Phrase** rest of line ...
        #   2. `APIRouter(...)` rest of line ...
        # Grab the first 200 chars of the bullet text after the number
        # so a keyword substring check still works across both shapes.
        pattern = rf"(?m)^\s*{num}\.\s+(.{{0,500}})"
        m = re.search(pattern, tail)
        if not m:
            continue
        raw = m.group(1).strip()
        # Strip enclosing bold markers without losing the inner text.
        raw = raw.replace("**", "")
        title = raw.lower()
        titles.append(title)
    return titles


def iter_oauth_uri_matches(text: str) -> Iterable[tuple[str, int]]:
    """Yield ``(uri, start_index)`` for every OAuth callback URI in ``text``."""
    for m in OAUTH_URI_REGEX.finditer(text):
        yield m.group("uri"), m.start()


__all__ = [
    "ABANDONED_CSRF_TOKENS",
    "CANONICAL_OAUTH_URIS",
    "EXECUTION_PLAN_MD",
    "FOLDER_CLAUDE_MD",
    "FOUNDATION_MODULES_MD",
    "IMPLEMENTATION_CHECKLIST_MD",
    "MIGRATION_NOTES",
    "OAUTH_URI_REGEX",
    "REPO_ROOT",
    "ROOT_CLAUDE_MD",
    "extract_invariant_titles",
    "has_rejection_context",
    "iter_oauth_uri_matches",
    "iter_planning_docs",
    "read_text",
]
