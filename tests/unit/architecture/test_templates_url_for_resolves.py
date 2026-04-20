"""Structural guard (captured→shrink at L0): every ``url_for(...)`` call in
admin Jinja templates references a ROUTE NAME known to the FastAPI admin
router, unless allowlisted as pre-codemod tech debt.

Per CLAUDE.md Critical Invariant #1: templates use
``{{ url_for('admin_<blueprint>_<endpoint>', **params) }}`` exclusively.
A missing route name raises ``NoMatchFound`` at render time, which is a
100%-reproducible bug but only surfaces when a user actually hits the
page. This guard catches it at CI time — before merge.

**L0 state.** The real ``templates/`` tree still contains ~55 unique
Flask-dotted names (``accounts.list_accounts`` etc.) that the L1a codemod
(scripts/codemod_script_root_to_url_for.py, L0-20 Green) rewrites to the
flat ``admin_<bp>_<ep>`` form. The allowlist at
``allowlists/templates_url_for_unknown_names.txt`` captures the current
unknown-name set as a baseline — it MAY shrink but MUST NOT grow. Every
entry disappears after the L1a codemod executes.

**Known-name universe.** Composed from two sources:

1. The static mount: ``name="static"`` (registered on the outer app in
   L1a per foundation-modules §11.2).
2. AST-scanned ``name=`` kwargs on ``@router.<method>`` decorators under
   ``src/admin/routers/`` once FastAPI routers exist.

Meta-guard plants a synthetic template-name-fixture containing a
``url_for('admin_nonexistent_endpoint', ...)`` call; the detector must
flag it.

Per `.claude/notes/flask-to-fastapi/L0-implementation-plan-v2.md` §5.5
row #17 (owner L0-20 per §7.3 canonicalization).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from tests.unit.architecture._ast_helpers import (
    FIXTURES_DIR,
    REPO_ROOT,
    SRC,
    iter_python_files,
    read_allowlist,
    relpath,
)

TEMPLATES_ROOT = REPO_ROOT / "templates"
ADMIN_ROUTERS_ROOT = SRC / "admin" / "routers"
FIXTURE = FIXTURES_DIR / "test_templates_url_for_resolves_meta_fixture.html.txt"
ALLOWLIST_NAME = "templates_url_for_unknown_names.txt"

# Matches ``url_for(<whitespace> ["']name["']`` — captures the first
# positional string argument. Good-enough syntax match for Jinja templates;
# catches both single and double quotes. Any keyword args or additional
# positional args are tolerated.
_URL_FOR_CALL = re.compile(r"""url_for\(\s*['"]([^'"]+)['"]""")

# FastAPI route decorator methods (mirrors
# test_architecture_admin_routes_named.py). Flask ``@bp.route(...)`` is
# out of scope until each router is ported at L1c/L1d.
_HTTP_METHODS = {"get", "post", "put", "delete", "patch", "head", "options", "api_route"}


def _iter_html_files(root: Path) -> list[Path]:
    """Every ``*.html`` under ``root`` in deterministic order."""
    return sorted(p for p in root.rglob("*.html") if p.is_file())


def _collect_known_route_names() -> set[str]:
    """Union of ``{"static"}`` and every ``name=`` string kwarg on admin FastAPI routers.

    The static mount name is a hard-coded constant (see foundation-modules §11.2:
    ``app.mount("/static", StaticFiles(directory="static"), name="static")``).
    At L0 no FastAPI admin routers exist, so the set is ``{"static"}``.
    """
    known: set[str] = {"static"}
    if not ADMIN_ROUTERS_ROOT.exists():
        return known
    for path in iter_python_files([ADMIN_ROUTERS_ROOT]):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute):
                continue
            if func.attr not in _HTTP_METHODS:
                continue
            for kw in node.keywords:
                if kw.arg == "name" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    known.add(kw.value.value)
    return known


def _iter_url_for_refs(text: str) -> list[tuple[int, str]]:
    """Yield ``(line_no, route_name)`` for every ``url_for('name', ...)`` in ``text``."""
    out: list[tuple[int, str]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for match in _URL_FOR_CALL.finditer(line):
            out.append((line_no, match.group(1)))
    return out


def test_every_template_url_for_references_known_route_or_is_allowlisted() -> None:
    """``url_for`` calls in templates resolve to a route in the known set.

    Pre-codemod Flask-dotted names are captured in the allowlist. After the
    L1a codemod runs, the allowlist shrinks to empty — at that point every
    ``url_for`` call must resolve cleanly.

    The assertion is weaker than "every call succeeds at render time" —
    it only checks that the NAME exists. Missing path params would still
    fail at render time, but that class of error is handled by the
    per-handler obligation tests at L1c/L1d, not this structural guard.
    """
    known = _collect_known_route_names()
    allowlisted_unknowns = read_allowlist(ALLOWLIST_NAME)
    violations: list[str] = []
    for path in _iter_html_files(TEMPLATES_ROOT):
        text = path.read_text(encoding="utf-8")
        for line_no, name in _iter_url_for_refs(text):
            if name in known:
                continue
            if name in allowlisted_unknowns:
                continue
            violations.append(f"{relpath(path)}:{line_no} → url_for('{name}')")
    assert not violations, (
        "Template url_for() references a route name not registered on any admin router "
        "and not listed in the captured→shrink allowlist.\n"
        f"Allowlist file: tests/unit/architecture/allowlists/{ALLOWLIST_NAME}\n"
        "Violations (file:line → unknown name):\n  - " + "\n  - ".join(sorted(violations))
    )


def test_allowlist_has_no_stale_entries() -> None:
    """Every allowlisted name must currently exist in some template.

    Stale entries silently permit new violations: if a name was allowlisted
    then all references removed, a fresh reference to the same name would
    pass unflagged. This test fails if any allowlisted name is no longer
    found in the real templates/ tree.
    """
    allowlisted = read_allowlist(ALLOWLIST_NAME)
    if not allowlisted:
        pytest.skip("Allowlist is empty (post-L1a codemod state).")
    seen: set[str] = set()
    for path in _iter_html_files(TEMPLATES_ROOT):
        text = path.read_text(encoding="utf-8")
        for _, name in _iter_url_for_refs(text):
            seen.add(name)
    stale = sorted(allowlisted - seen)
    assert not stale, (
        "Allowlist contains names no longer found in any template. Delete:\n  - "
        + "\n  - ".join(stale)
        + f"\nFrom: tests/unit/architecture/allowlists/{ALLOWLIST_NAME}"
    )


def test_meta_fixture_exists() -> None:
    assert FIXTURE.exists(), f"Meta-fixture missing at {FIXTURE}."


def test_detector_catches_meta_fixture() -> None:
    """The detector flags a synthetic unknown route name planted in the fixture."""
    text = FIXTURE.read_text(encoding="utf-8")
    refs = _iter_url_for_refs(text)
    assert refs, f"Detector found no url_for() calls in {FIXTURE.name}."
    known = _collect_known_route_names()
    allowlisted = read_allowlist(ALLOWLIST_NAME)
    # Every call in the fixture is deliberately unknown and NOT in the
    # allowlist. None should pass the guard's filter.
    unknowns = [(line_no, name) for line_no, name in refs if name not in known and name not in allowlisted]
    assert unknowns, (
        f"Detector FAILED to notice unknown route names in {FIXTURE.name}. "
        f"All refs resolved against known set or allowlist."
    )


@pytest.mark.parametrize(
    "line,expected_names",
    [
        ("{{ url_for('static', path='/css/app.css') }}", ["static"]),
        ('{{ url_for("admin_accounts_list", tenant_id=t) }}', ["admin_accounts_list"]),
        (
            "<a href=\"{{ url_for('admin_products_add') }}\">{{ url_for('admin_core_index') }}</a>",
            ["admin_products_add", "admin_core_index"],
        ),
        ("plain text, no match here", []),
        # Jinja filter on the result shouldn't affect extraction.
        ("{{ url_for('x_y') | safe }}", ["x_y"]),
    ],
)
def test_url_for_regex_extracts_expected_names(line: str, expected_names: list[str]) -> None:
    names = [name for _, name in _iter_url_for_refs(line)]
    assert names == expected_names
