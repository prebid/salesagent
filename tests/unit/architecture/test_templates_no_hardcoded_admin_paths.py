"""Structural guard (captured→shrink at L0): no admin Jinja template hard-codes
an admin URL via ``{{ request.script_root }}``, ``{{ script_root }}``,
``{{ admin_prefix }}``, ``{{ static_prefix }}``, ``{{ request.script_name }}``,
``{{ script_name }}``, or a bare ``"/admin/..."`` / ``"/static/..."`` string
literal.

Per CLAUDE.md Critical Invariant #1 and foundation-modules §11.1: every admin
URL in every template uses ``{{ url_for('admin_<blueprint>_<endpoint>',
**params) }}`` exclusively — for routes AND static assets. Starlette's
``include_router(prefix=...)`` does NOT populate ``scope["root_path"]`` the
way Flask's blueprint mounting populated ``request.script_root``, so any
template that still relies on a prefix global produces broken HTML in
reverse-proxy deployments.

**L0 state.** The Phase-A codemod at ``scripts/codemod_script_root_to_url_for.py``
(L0-20 Green) rewrites every one of these patterns to its ``url_for`` equivalent.
At L0 the codemod has been AUTHORED but NOT executed against the real
``templates/`` tree (execution lands at L1a). The allowlist at
``allowlists/hardcoded_admin_paths.txt`` captures the current 148 violations
as a baseline — it MAY shrink but MUST NOT grow. The L1a codemod-execution
PR drops every entry in the same commit that mutates the templates.

Per `.claude/notes/flask-to-fastapi/L0-implementation-plan-v2.md` §5.5 row
#18 (owner L0-20 per §7.3 canonicalization).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.unit.architecture._ast_helpers import (
    FIXTURES_DIR,
    REPO_ROOT,
    read_allowlist,
    relpath,
)

TEMPLATES_ROOT = REPO_ROOT / "templates"
ALLOWLIST_NAME = "hardcoded_admin_paths.txt"
FIXTURE = FIXTURES_DIR / "test_templates_no_hardcoded_admin_paths_meta_fixture.html.txt"

# Jinja-global forms. Each pattern is checked against the raw text of every
# template line. The patterns are strict enough to avoid false positives on
# unrelated words that happen to contain "script_root" etc. — we require the
# Jinja ``{{ ... }}`` wrapper.
_FORBIDDEN_JINJA_GLOBALS = [
    re.compile(r"\{\{\s*request\.script_root\s*\}\}"),
    re.compile(r"\{\{\s*script_root\s*\}\}"),
    re.compile(r"\{\{\s*request\.script_name\s*\}\}"),
    re.compile(r"\{\{\s*script_name\s*\}\}"),
    re.compile(r"\{\{\s*admin_prefix\s*\}\}"),
    re.compile(r"\{\{\s*static_prefix\s*\}\}"),
]

# Bare string literals containing hardcoded admin paths. These are narrower
# than a general "/admin/" scan — only quoted forms count. Unquoted comment
# text or URL references in documentation/help text are deliberately NOT
# flagged (they're not URL-construction sites).
_FORBIDDEN_HARDCODED = re.compile(r"""(?:["'])/(?:admin|static)/""")


def _iter_html_files(root: Path) -> list[Path]:
    """Every ``*.html`` under ``root`` in deterministic order."""
    return sorted(p for p in root.rglob("*.html") if p.is_file())


def _iter_violations(text: str) -> list[int]:
    """Yield 1-indexed line numbers where a forbidden pattern matches."""
    hits: list[int] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if any(pat.search(line) for pat in _FORBIDDEN_JINJA_GLOBALS):
            hits.append(line_no)
            continue
        if _FORBIDDEN_HARDCODED.search(line):
            hits.append(line_no)
    return hits


def test_no_new_hardcoded_admin_paths_in_templates() -> None:
    """Every forbidden pattern appears ONLY in allowlisted file:line entries.

    New violations fail immediately. Existing violations shrink over time as
    the L1a codemod is executed and as hand-authored templates are migrated.
    """
    allowlisted = read_allowlist(ALLOWLIST_NAME)
    violations: list[str] = []
    for path in _iter_html_files(TEMPLATES_ROOT):
        text = path.read_text(encoding="utf-8")
        rel = relpath(path)
        for line_no in _iter_violations(text):
            entry = f"{rel}:{line_no}"
            if entry not in allowlisted:
                violations.append(entry)
    assert not violations, (
        "Templates contain hardcoded admin paths or Jinja-global URL prefixes. "
        "Use `{{ url_for('admin_<blueprint>_<endpoint>', **params) }}` instead "
        "(CLAUDE.md Critical Invariant #1).\n"
        f"Allowlist file: tests/unit/architecture/allowlists/{ALLOWLIST_NAME}\n"
        "New violations (file:line):\n  - " + "\n  - ".join(sorted(violations))
    )


def test_allowlist_has_no_stale_entries() -> None:
    """Every allowlisted file:line must currently contain a violation.

    Stale entries silently permit regressions: if the template line changes
    or the file is renamed, the allowlist entry becomes a free pass. The
    L1a codemod-execution PR removes every entry from the allowlist as it
    mutates the templates.
    """
    allowlisted = read_allowlist(ALLOWLIST_NAME)
    if not allowlisted:
        pytest.skip("Allowlist is empty (post-L1a codemod state).")
    live_entries: set[str] = set()
    for path in _iter_html_files(TEMPLATES_ROOT):
        text = path.read_text(encoding="utf-8")
        rel = relpath(path)
        for line_no in _iter_violations(text):
            live_entries.add(f"{rel}:{line_no}")
    stale = sorted(allowlisted - live_entries)
    assert not stale, (
        "Allowlist contains file:line entries that no longer violate. Delete them:\n  - "
        + "\n  - ".join(stale)
        + f"\nFrom: tests/unit/architecture/allowlists/{ALLOWLIST_NAME}"
    )


def test_meta_fixture_exists() -> None:
    assert FIXTURE.exists(), f"Meta-fixture missing at {FIXTURE}."


def test_detector_catches_meta_fixture() -> None:
    """The detector flags every planted violation in the meta fixture."""
    text = FIXTURE.read_text(encoding="utf-8")
    hits = _iter_violations(text)
    assert hits, (
        f"Detector FAILED to notice planted violations in {FIXTURE.name}. "
        "Either the fixture is missing its planted patterns or the detector "
        "regexes have drifted."
    )


@pytest.mark.parametrize(
    "line,expect_violation",
    [
        ('<a href="{{ request.script_root }}/foo">x</a>', True),
        ('<a href="{{ script_root }}/foo">x</a>', True),
        ('<a href="{{ admin_prefix }}/foo">x</a>', True),
        ('<a href="{{ static_prefix }}/foo">x</a>', True),
        ('<a href="{{ request.script_name }}/foo">x</a>', True),
        ('<img src="/admin/logo.png">', True),
        ('<link rel="stylesheet" href="/static/x.css">', True),
        # Clean forms — must NOT flag.
        ("<a href=\"{{ url_for('admin_foo') }}\">x</a>", False),
        ("<img src=\"{{ url_for('static', path='/x.png') }}\">", False),
        ("plain text with no URLs", False),
        # Words containing forbidden substrings in non-Jinja context — must NOT flag.
        ("<p>the script_root of a file</p>", False),
        # Comment text that mentions /admin/ without quotes — NOT flagged
        # (we match only quoted literals).
        ("{# navigate to /admin/foo to test #}", False),
    ],
)
def test_detector_behavior_parametrized(line: str, expect_violation: bool) -> None:
    hits = _iter_violations(line)
    assert bool(hits) == expect_violation, f"line={line!r} hits={hits!r}"
