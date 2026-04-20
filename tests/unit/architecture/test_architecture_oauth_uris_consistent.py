"""Doc-drift guard: OAuth callback URIs listed as REGISTERED URIs in the
planning narrative must all be in the canonical set.

Rationale — Invariant #6 (OAuth byte-immutability): the Google Cloud
Console registration for the application lists exactly 3 callback URIs.
Any drift in the planning doc banner — an off-by-one path, stale
segment like ``{tenant_id}``, or accidental ``/auth/gam/callback``
(missing ``/admin`` prefix) — risks pairing that prose with a future
Cloud Console change and breaking production OAuth login.

Scope is deliberately narrow: we only flag URIs that appear in the
Invariant 6 narrative blocks of ``CLAUDE.md`` (root and folder), not
every occurrence across all planning files. Flask blueprint route
strings like ``@bp.route("/auth/google/callback")`` in worked examples
are NOT flagged — they document the Flask pattern, not the canonical
registered URI.

Per ``.claude/notes/flask-to-fastapi/flask-to-fastapi-foundation-modules.md §11.34``
and ``.claude/notes/flask-to-fastapi/L0-implementation-plan-v2.md §L0-23``.
"""

from __future__ import annotations

import re

from tests.unit.architecture._doc_parser import (
    CANONICAL_OAUTH_URIS,
    FOLDER_CLAUDE_MD,
    ROOT_CLAUDE_MD,
    has_rejection_context,
    iter_oauth_uri_matches,
    read_text,
)


def _invariant_6_block(text: str) -> str:
    """Return the Invariant 6 paragraph from a CLAUDE.md-like file.

    Matches the line starting with ``6. **OAuth``-or-``6. `OAuth`` and
    takes the remainder of that bullet (up to the next blank line or
    numbered list item). Returns "" if the file lacks an Invariant 6
    block.
    """
    # The bullet either starts with ``6. **OAuth`` (root CLAUDE.md) or
    # ``6. **OAuth`` / ``6. OAuth`` / ``6. `OAuth`` in folder CLAUDE.md.
    m = re.search(r"(?m)^\s*6\.\s.*?(?=\n\n|\n\s*7\.|\Z)", text, re.DOTALL)
    return m.group(0) if m else ""


def test_oauth_uris_in_invariant_6_are_canonical() -> None:
    """Every OAuth URI in the Invariant 6 block is canonical or marked NOT.

    Both the root CLAUDE.md banner and the folder-scoped Invariant 6
    bullet are scanned. Mentions of non-canonical URIs are allowed only
    when the paragraph explicitly rejects them (``NOT``, ``NOT``, etc.).
    """
    violations: list[str] = []
    for label, path in [("root", ROOT_CLAUDE_MD), ("folder", FOLDER_CLAUDE_MD)]:
        block = _invariant_6_block(read_text(path))
        if not block:
            continue
        for uri, index in iter_oauth_uri_matches(block):
            if uri in CANONICAL_OAUTH_URIS:
                continue
            if has_rejection_context(block, index):
                continue
            violations.append(f"{label} CLAUDE.md Invariant 6: {uri!r}")
    assert (
        not violations
    ), "Non-canonical OAuth callback URI in the Invariant 6 narrative. " "Each URI in Invariant 6 must be in " + ", ".join(
        sorted(CANONICAL_OAUTH_URIS)
    ) + " OR be accompanied by a rejection word (NOT/REJECTED/FORBIDDEN).\n" + "Violations:\n  - " + "\n  - ".join(
        violations
    )


def test_canonical_set_matches_invariant_6_contract() -> None:
    """The hard-coded canonical set matches Invariant 6 exactly.

    Defends against a silent edit to ``CANONICAL_OAUTH_URIS`` that
    would make the guard tautological. Checks: 3 URIs; every canonical
    URI is an ``/admin/auth/<word>/callback`` pattern; the three
    distinct ``<word>`` values are ``google``, ``oidc``, and ``gam``.
    """
    assert len(CANONICAL_OAUTH_URIS) == 3
    # Every canonical URI has the /admin prefix AND the /callback suffix.
    assert all(uri.startswith("/admin/auth/") for uri in CANONICAL_OAUTH_URIS)
    assert all(uri.endswith("/callback") for uri in CANONICAL_OAUTH_URIS)
    # The three leaf names are exactly google / oidc / gam.
    leaf_names = {uri.split("/")[-2] for uri in CANONICAL_OAUTH_URIS}
    assert leaf_names == {"google", "oidc", "gam"}


def test_invariant_6_blocks_exist_in_both_files() -> None:
    """Both the root banner and the folder source-of-truth enumerate Invariant 6."""
    assert _invariant_6_block(read_text(ROOT_CLAUDE_MD)), "Root CLAUDE.md Invariant 6 block not found"
    assert _invariant_6_block(read_text(FOLDER_CLAUDE_MD)), "Folder CLAUDE.md Invariant 6 block not found"
