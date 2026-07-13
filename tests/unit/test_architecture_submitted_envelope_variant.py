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
`status` kwarg resolves to "submitted" (literal or `AdcpTaskStatus.submitted
.value`) and whose `response` kwarg is built from a non-Submitted constructor
(`CreateMediaBuySuccess(...)`, `.sync_success(...)`, `CreateMediaBuyError(...)`).
The update path returns its response model bare (no Result wrapper), so this
guard pins the create-side wrapper — the only place the envelope status and
the body variant are chosen independently and can diverge.
"""

from __future__ import annotations

import ast
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"

# No violations are permitted — this list must stay empty (allowlists only shrink).
_ALLOWED: set[str] = set()


def _status_is_submitted(node: ast.expr) -> bool:
    """True when the status kwarg is the submitted literal or enum value."""
    if isinstance(node, ast.Constant) and node.value == "submitted":
        return True
    # AdcpTaskStatus.submitted.value / TaskStatus.submitted.value
    if isinstance(node, ast.Attribute) and node.attr == "value":
        inner = node.value
        return isinstance(inner, ast.Attribute) and inner.attr == "submitted"
    return False


def _response_is_non_submitted_variant(node: ast.expr) -> bool:
    """True when the response kwarg is constructed from a non-Submitted class."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    # CreateMediaBuySuccess(...) / CreateMediaBuyError(...)
    if isinstance(func, ast.Name):
        return func.id in {"CreateMediaBuySuccess", "CreateMediaBuyError"}
    if isinstance(func, ast.Attribute):
        # CreateMediaBuySuccess.sync_success(...)
        if func.attr == "sync_success":
            return True
        return func.attr in {"CreateMediaBuySuccess", "CreateMediaBuyError"}
    return False


def _scan_violations() -> list[str]:
    violations: list[str] = []
    for path in sorted(_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
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
            if _status_is_submitted(status) and _response_is_non_submitted_variant(response):
                rel = path.relative_to(_SRC.parent)
                entry = f"{rel}:{node.lineno}"
                if entry not in _ALLOWED:
                    violations.append(entry)
    return violations


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
