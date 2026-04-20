"""Doc-drift guard: the abandoned double-submit-cookie CSRF strategy is
NOT mentioned positively in the planning docs.

Rationale — Invariant #5 refinement (Decision "Option A — SameSite=Lax
+ CSRFOriginMiddleware"): the original plan considered a double-submit
cookie strategy (``adcp_csrf`` cookie + ``XSRF-TOKEN`` header + form
tokens) and EXPLICITLY REJECTED IT because it would require changing
~80 fetch calls and ~47 forms for zero practical security gain.

Any positive mention of the abandoned strategy is drift — the planning
doc paragraph is describing a path that will NOT be implemented. The
guard flags ``adcp_csrf``, ``double-submit``, and ``XSRF-TOKEN`` tokens
anywhere in ``.claude/notes/flask-to-fastapi/`` UNLESS surrounded by a
rejection word (``NOT``, ``REJECTED``, ``rejected``, ``abandoned``).

Per ``.claude/notes/flask-to-fastapi/flask-to-fastapi-foundation-modules.md §11.34``
and ``.claude/notes/flask-to-fastapi/L0-implementation-plan-v2.md §L0-23``.
"""

from __future__ import annotations

from tests.unit.architecture._doc_parser import (
    ABANDONED_CSRF_TOKENS,
    has_rejection_context,
    iter_planning_docs,
    read_text,
)


def test_abandoned_csrf_tokens_only_appear_in_rejection_context() -> None:
    """Every mention of an abandoned CSRF token is within a rejection context.

    A mention without a nearby rejection word (``NOT``, ``REJECTED``,
    ``rejected``, ``abandoned``) indicates the doc paragraph is
    describing the abandoned strategy AS IF IT WERE CURRENT — i.e.
    drift from Decision 5 "Option A — SameSite=Lax".
    """
    violations: list[str] = []
    for doc in iter_planning_docs():
        text = read_text(doc)
        if not text:
            continue
        for token in ABANDONED_CSRF_TOKENS:
            start = 0
            while True:
                index = text.find(token, start)
                if index < 0:
                    break
                if not has_rejection_context(text, index):
                    line_no = text.count("\n", 0, index) + 1
                    rel = doc.relative_to(doc.parents[3])
                    violations.append(f"{rel}:{line_no} {token!r}")
                start = index + len(token)
    assert not violations, (
        "Abandoned CSRF-strategy tokens appear outside a rejection context in "
        ".claude/notes/flask-to-fastapi/. Per Decision 5 (SameSite=Lax + Origin "
        "validation) these tokens are forbidden unless explicitly rejected.\n"
        "Violations (path:line token):\n  - " + "\n  - ".join(violations)
    )


def test_abandoned_token_list_is_the_known_three() -> None:
    """The hard-coded abandoned-token list defends against silent edits."""
    assert set(ABANDONED_CSRF_TOKENS) == {"adcp_csrf", "double-submit", "XSRF-TOKEN"}
