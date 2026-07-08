"""Guard: production must never emit the buyer suggestion inside ``details``.

Regression for salesagent-cx41/58hl (surfaced by salesagent-9val's strict
harness): auth and account emitters raised AdCP errors with
``details={"suggestion": ...}`` instead of the first-class ``suggestion=``
param, leaving the error.json TOP-LEVEL ``suggestion`` empty on the wire.
This guard AST-scans ``src/`` for any call passing a ``details=`` keyword
whose dict literal contains a ``"suggestion"`` key. The typed path is
``AdCPError(..., suggestion=...)`` — serialization promotes it to the
protocol position (``exceptions.py`` ``to_dict``/envelope builder).

Companion of the accepting-side guard
(``test_guards_suggestion_details_read.py``), which bans test infra from
READING a suggestion out of ``details``.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_ROOT = REPO_ROOT / "src"


def _dict_has_suggestion_key(node: ast.AST) -> bool:
    if isinstance(node, ast.Dict):
        return any(isinstance(k, ast.Constant) and k.value == "suggestion" for k in node.keys)
    # dict(suggestion=...) builds the same buried payload as a {"suggestion": ...}
    # literal — a Dict-only matcher would miss the Call form.
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "dict":
        return any(kw.arg == "suggestion" for kw in node.keywords)
    return False


def find_buried_suggestion_emits(tree: ast.AST) -> list[str]:
    """Unparsed source for every call passing details={... "suggestion": ...}."""
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg == "details" and _dict_has_suggestion_key(kw.value):
                offenders.append(ast.unparse(node))
    return offenders


def test_no_buried_suggestion_emitters_in_src():
    """No production call buries the buyer suggestion in details."""
    violations: list[str] = []
    for path in sorted(SCAN_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for offender in find_buried_suggestion_emits(tree):
            violations.append(f"{path.relative_to(REPO_ROOT)}: {offender}")
    assert not violations, (
        "error.json places `suggestion` at the TOP LEVEL of the error object. "
        "Pass suggestion=... to the AdCPError (serialization promotes it); a "
        "details={'suggestion': ...} copy never reaches the protocol position "
        "(salesagent-cx41/58hl). Violations:\n  " + "\n  ".join(violations)
    )


# ── Meta-tests: the detector itself ─────────────────────────────────────────


def _detect(snippet: str) -> list[str]:
    return find_buried_suggestion_emits(ast.parse(snippet))


class TestGuardDetector:
    def test_positive_suggestion_only_details(self):
        assert _detect('raise AdCPAccountNotFoundError("x", details={"suggestion": "use list_accounts"})')

    def test_positive_suggestion_among_other_keys_would_be_missed_case(self):
        # A suggestion smuggled alongside legit detail keys must still be caught —
        # a detector comparing the whole dict to {"suggestion": ...} would miss it.
        assert _detect('err("x", details={"minimum_budget": 500, "suggestion": "raise budget"})')

    def test_positive_dict_call_form(self):
        # dict(suggestion=...) is the same disease as the {"suggestion": ...}
        # literal — an ast.Dict-only matcher would miss the Call form.
        assert _detect('raise AdCPAccountNotFoundError("x", details=dict(suggestion="use list_accounts"))')

    def test_negative_details_without_suggestion(self):
        assert not _detect('raise AdCPValidationError("x", details={"minimum_budget": 500})')

    def test_negative_dict_call_without_suggestion(self):
        assert not _detect('raise AdCPValidationError("x", details=dict(minimum_budget=500))')

    def test_negative_first_class_suggestion_kwarg(self):
        assert not _detect('raise AdCPAuthRequiredError("x", suggestion=AUTH_REQUIRED_SUGGESTION)')
