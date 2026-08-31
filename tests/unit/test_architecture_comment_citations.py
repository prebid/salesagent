"""Guard: every ``test_*`` name cited in a src/ or tests/ COMMENT or DOCSTRING resolves.

Round-12/13 review (#1329 item 10) prescribed this guard: design decisions are recorded in
comments ("enforced by ``test_foo``", "graded by ``test_bar``"), and nothing coupled a comment
to the symbol it named — so each push edited the mechanism and the map drifted. A concrete
instance: a comment cited ``test_happy_path_synced_on_mcp_and_a2a_wire`` after that test was
renamed to ``test_happy_path_synced_wire`` — a dead pointer no test caught.

This guard closes the ``test_*`` half of that gap (the highest-value, lowest-false-positive
subset of "every test_* or registry symbol named in a comment resolves"): it collects every
real ``def test_*`` across ``tests/`` and asserts every ``test_*`` identifier mentioned in a
COMMENT or DOCSTRING (in ``src/`` or ``tests/``) is a real test name. A rename that leaves a
stale citation now reddens here.

Allowlist policy (mirrors the other architecture guards): pre-existing stale/illustrative
citations are tracked in ``_ALLOWED_MISSING`` with a reason and shrink over time; NEW stale
citations are never added — fix the citation instead.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SCAN_DIRS = (_ROOT / "src", _ROOT / "tests")

# Pre-existing citations (NOT introduced by #1329) that name a test that no longer resolves.
# Repo-wide citation debt is out of this PR's scope; these are tracked here (shrink-only, never
# grow) so a NEW stale citation still reddens. Each is a genuine dead/renamed reference or a
# test-data-shaped token the collectors below do not model.
_ALLOWED_MISSING: dict[str, str] = {
    # An illustrative example of a generated per-test database NAME (hex suffix), not a test
    # citation — `integration_db` builds names like this; it is documentation, not a pointer.
    "test_a3f8d92c": "illustrative generated-db-name example in integration_db fixture docstring",
}

# A test citation is BACKTICK-QUOTED (`` `test_foo` `` / `` ``test_foo`` ``) — the convention for
# naming an enforcing/graded test in this codebase's comments + docstrings. Requiring the backticks
# is what makes the guard high-signal: a bare prose mention (a `test_mode` config flag, a
# `test_guards_*` prefix, a `test_delivery_behavioral` suite shorthand, a `test_<hex>` data token)
# is NOT a citation and is not required to resolve. The trailing name char excludes a bare prefix
# (`` `test_guards_` `` ends in `_`, not matched by the `[a-z0-9]` final class).
_TEST_NAME_RE = re.compile(r"`+(test_[a-z0-9_]*[a-z0-9])`+")
# Test-DATA identifiers (``test_principal_001``, ``test_token_123``) end in ``_<digits>`` — these
# are fixture ids, not test citations, so they are not required to resolve even if backticked.
_DATA_ID_RE = re.compile(r"_\d+$")


def _real_test_names() -> set[str]:
    """Every real test IDENTIFIER: ``def test_*`` names AND ``test_*.py`` module stems.

    Comments cite BOTH forms ("enforced by ``test_specialism_audit_gate``" — a function; "see
    ``test_architecture_no_model_dump_in_impl``" — a module), so both must count as resolved.
    """
    names: set[str] = set()
    for path in (_ROOT / "tests").rglob("*.py"):
        if path.stem.startswith("test_"):
            names.add(path.stem)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith("test_"):
                names.add(node.name)
    return names


def _cited_test_names(path: Path) -> set[str]:
    """``test_*`` identifiers appearing in COMMENT tokens or DOCSTRINGS of ``path``.

    Scans comments (via ``tokenize``) and docstrings (module + every function/class, via ``ast``)
    — deliberately NOT arbitrary string literals, so a test asserting ``"test_x" in body`` is not
    mistaken for a citation.
    """
    text = path.read_text(encoding="utf-8")
    cited: set[str] = set()

    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                cited.update(_TEST_NAME_RE.findall(tok.string))
    except (tokenize.TokenError, IndentationError):
        pass

    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError:
        return cited
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                cited.update(_TEST_NAME_RE.findall(doc))
    return cited


def test_cited_test_names_resolve() -> None:
    """Every ``test_*`` cited in a src/ or tests/ comment/docstring is a real test name."""
    real = _real_test_names()
    assert len(real) > 500, f"sanity: expected to discover many real test names, got {len(real)}"

    violations: list[str] = []
    for scan_dir in _SCAN_DIRS:
        for path in scan_dir.rglob("*.py"):
            if path.name == Path(__file__).name:
                continue  # this guard names test_* tokens in its own prose
            for name in _cited_test_names(path) - real - set(_ALLOWED_MISSING):
                if _DATA_ID_RE.search(name):
                    continue  # test-data fixture id (test_principal_001), not a test citation
                violations.append(f"{path.relative_to(_ROOT)}: cites unknown test {name!r}")

    assert not violations, (
        "Stale test citation(s) in comments/docstrings — the named test does not exist "
        "(renamed/deleted?). Fix the citation to the current test name, or add to "
        "_ALLOWED_MISSING with a reason if it is intentionally illustrative:\n  " + "\n  ".join(sorted(violations))
    )
