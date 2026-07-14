"""Guard: a submitted protocol envelope must carry a Submitted response variant.

Disease pattern (PR #1567 round-2 blocker 2): wrapping a
*Success* response body in a protocol envelope whose status is "submitted".
Spec 3.1.1 models the pending case as the mutually exclusive *Submitted*
task-envelope variant (status="submitted" + task_id, NO media_buy_id/
confirmed_at/revision); the adcp-6.6 Success subclasses default
confirmed_at/revision, so a Success-under-submitted wire falsely asserts the
seller confirmed a buy (or applied an update) that is still awaiting a human
decision. The create path shipped exactly this; update_media_buy had the same
bug fixed in b8b7e751b.

Detection: AST scan of src/ for `CreateMediaBuyResult(...)` calls whose
`status` kwarg resolves to "submitted" (literal, `AdcpTaskStatus.submitted`
bare StrEnum member, or `AdcpTaskStatus.submitted.value`) and whose `response`
kwarg is built from a non-Submitted constructor (`CreateMediaBuySuccess(...)`,
`.sync_success(...)`, `CreateMediaBuyError(...)`) — directly or via a simple
variable hoisted earlier in the same function body. The update path returns
its response model bare (no Result wrapper), so this guard pins the
create-side wrapper — the only place the envelope status and the body variant
are chosen independently and can diverge.

Self-tests (PR #1567 round-3): with an empty allowlist and a clean tree the
guard passing is indistinguishable from a silently broken matcher, so
known-bad/known-good inline fixtures pin the detector itself.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.unit._architecture_helpers import assert_detector_catches_ast_snippets

_SRC = Path(__file__).resolve().parents[2] / "src"

# No violations are permitted — this list must stay empty (allowlists only shrink).
_ALLOWED: set[str] = set()

_NON_SUBMITTED_CONSTRUCTORS = {"CreateMediaBuySuccess", "CreateMediaBuyError"}


def _status_is_submitted(node: ast.expr) -> bool:
    """True when the status kwarg is the submitted literal, enum member, or .value."""
    if isinstance(node, ast.Constant) and node.value == "submitted":
        return True
    if isinstance(node, ast.Attribute):
        # AdcpTaskStatus.submitted.value / TaskStatus.submitted.value
        if node.attr == "value":
            inner = node.value
            return isinstance(inner, ast.Attribute) and inner.attr == "submitted"
        # Bare StrEnum member: AdcpTaskStatus.submitted (adcp 6.6 ships StrEnum,
        # so this compiles and equals the string on the wire).
        return node.attr == "submitted"
    return False


def _response_is_non_submitted_variant(node: ast.expr) -> bool:
    """True when the response kwarg is constructed from a non-Submitted class."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    # CreateMediaBuySuccess(...) / CreateMediaBuyError(...)
    if isinstance(func, ast.Name):
        return func.id in _NON_SUBMITTED_CONSTRUCTORS
    if isinstance(func, ast.Attribute):
        # CreateMediaBuySuccess.sync_success(...)
        if func.attr == "sync_success":
            return True
        return func.attr in _NON_SUBMITTED_CONSTRUCTORS
    return False


def _non_submitted_assignments(tree: ast.AST) -> dict[str, ast.expr]:
    """Map simple ``name = <non-Submitted constructor>(...)`` assignments in *tree*.

    Catches the hoisted-variable form: ``response = CreateMediaBuySuccess(...)``
    assigned before the ``CreateMediaBuyResult(response=response, ...)`` call.
    Last assignment wins (matches runtime for straight-line code); a later
    reassignment to a Submitted constructor clears the name.
    """
    assigned: dict[str, ast.expr] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        if _response_is_non_submitted_variant(node.value):
            assigned[target.id] = node.value
        else:
            assigned.pop(target.id, None)
    return assigned


def _find_violations_in_tree(tree: ast.Module) -> list[int]:
    """Line numbers of Success/Error bodies wrapped in a submitted envelope."""
    hoisted = _non_submitted_assignments(tree)
    lines: list[int] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        if node.func.id != "CreateMediaBuyResult":
            continue
        kwargs = {kw.arg: kw.value for kw in node.keywords if kw.arg}
        status = kwargs.get("status")
        response = kwargs.get("response")
        if status is None or response is None:
            continue
        if not _status_is_submitted(status):
            continue
        direct = _response_is_non_submitted_variant(response)
        via_variable = isinstance(response, ast.Name) and response.id in hoisted
        if direct or via_variable:
            lines.append(node.lineno)
    return lines


def _scan_violations() -> list[str]:
    violations: list[str] = []
    for path in sorted(_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for lineno in _find_violations_in_tree(tree):
            rel = path.relative_to(_SRC.parent)
            entry = f"{rel}:{lineno}"
            if entry not in _ALLOWED:
                violations.append(entry)
    return violations


_KNOWN_BAD_SNIPPETS = {
    "success-under-submitted-literal": (
        "CreateMediaBuyResult(response=CreateMediaBuySuccess.sync_success(media_buy_id='x'), status='submitted')"
    ),
    "success-under-submitted-enum-value": (
        "CreateMediaBuyResult(response=CreateMediaBuySuccess(media_buy_id='x'), status=AdcpTaskStatus.submitted.value)"
    ),
    "success-under-submitted-bare-enum": (
        "CreateMediaBuyResult(response=CreateMediaBuySuccess(media_buy_id='x'), status=AdcpTaskStatus.submitted)"
    ),
    "error-under-submitted": ("CreateMediaBuyResult(response=CreateMediaBuyError(errors=[]), status='submitted')"),
    "hoisted-response-variable": (
        "def f():\n"
        "    response = CreateMediaBuySuccess.sync_success(media_buy_id='x')\n"
        "    return CreateMediaBuyResult(response=response, status='submitted')"
    ),
}

_KNOWN_GOOD_SNIPPETS = {
    "submitted-under-submitted": (
        "CreateMediaBuyResult(response=CreateMediaBuySubmitted(task_id='ws_1'), status='submitted')"
    ),
    "success-under-completed": (
        "CreateMediaBuyResult(response=CreateMediaBuySuccess.sync_success(media_buy_id='x'), status='completed')"
    ),
    "hoisted-submitted-variable": (
        "def f():\n"
        "    response = CreateMediaBuySubmitted(task_id='ws_1')\n"
        "    return CreateMediaBuyResult(response=response, status='submitted')"
    ),
}


class TestSubmittedEnvelopeVariant:
    """Structural guard: no Success/Error body under a submitted envelope."""

    def test_no_success_body_under_submitted_envelope(self):
        """Every submitted create envelope must wrap CreateMediaBuySubmitted.

        A Success body under status="submitted" puts confirmed_at/revision/
        media_buy_id on the wire for a buy that is NOT confirmed (spec 3.1.1
        create-media-buy-response.json, CreateMediaBuySubmitted variant).
        """
        violations = _scan_violations()
        assert not violations, (
            "Success/Error response body wrapped in a status='submitted' envelope — "
            "use CreateMediaBuySubmitted (spec 3.1.1; PR #1567):\n  " + "\n  ".join(violations)
        )

    def test_detector_flags_known_bad_fixtures(self):
        """Positive self-test: every known-bad inline form MUST be flagged.

        Pins the matcher against silent breakage — with an empty allowlist and
        a clean tree, the guard passing proves nothing about the detector.
        """
        assert_detector_catches_ast_snippets(_find_violations_in_tree, snippets=_KNOWN_BAD_SNIPPETS)

    def test_detector_passes_known_good_fixtures(self):
        """Negative self-test: correct constructions must NOT be flagged."""
        flagged = {
            label: _find_violations_in_tree(ast.parse(source, filename=f"<known-good:{label}>"))
            for label, source in _KNOWN_GOOD_SNIPPETS.items()
        }
        false_positives = {label: lines for label, lines in flagged.items() if lines}
        assert not false_positives, f"Detector flagged known-good snippet(s): {false_positives}"
