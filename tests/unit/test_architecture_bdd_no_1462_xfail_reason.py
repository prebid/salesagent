"""Guard: BDD strict-xfail markers must not attribute their failure to #1462.

#1462 is the in-process ``_impl`` request-path gap (it drops
``attribution_window.post_click`` before validation runs). BDD scenarios do
NOT parametrize the IMPL transport — they dispatch only over a2a/mcp/rest/
e2e_rest (the IMPL fallback was removed; see ``tests/bdd/steps/generic/
_dispatch.py``). Therefore any BDD ``pytest.mark.xfail(reason=...)`` that blames
#1462 is necessarily MIS-ATTRIBUTED: the real cause is something the wire
transports actually exercise (e.g. the generic ``with {request_params}`` step
shadowing a specific partition step and dropping the window — #1417).

This guard exists because exactly that mis-attribution accreted across five
marker sites in ``tests/bdd/conftest.py`` and was corrected in #1417.
The disease's recurrence is also caught at runtime by ``strict=True`` (a stale
marker that starts passing becomes an XPASS->FAILED in the full in-network run),
but this static guard stops the mis-attributed *reason string* from being
copy-pasted back in.

Scanning approach: AST — find ``pytest.mark.xfail(...)`` calls under
``tests/bdd/`` and assert no ``reason=`` string contains "1462". (Clarifying
*comments* that mention #1462 to explain it is the IMPL path are fine — only the
marker reason strings are scanned.)

GH: #1417 (disease), #1417 (the real partition cause)
"""

from __future__ import annotations

import ast
from pathlib import Path

_BDD_DIR = Path(__file__).resolve().parents[1] / "bdd"


def _string_parts(node: ast.AST) -> list[str]:
    """Collect every string literal in an expression subtree.

    Handles a plain ``Constant``, implicit adjacent concatenation (already one
    Constant after parse), explicit ``a + b`` concatenation (BinOp), and
    f-strings (JoinedStr) — so a reason split across several lines cannot slip
    the check.
    """
    parts: list[str] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            parts.append(child.value)
    return parts


def _is_xfail_call(node: ast.Call) -> bool:
    """True if ``node`` is a ``pytest.mark.xfail(...)`` (or bare ``xfail(...)``) call."""
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr == "xfail"
    return isinstance(func, ast.Name) and func.id == "xfail"


def _find_1462_reasons(path: Path) -> list[tuple[int, str]]:
    """Return (lineno, reason) for every xfail reason string mentioning 1462."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _is_xfail_call(node)):
            continue
        for kw in node.keywords:
            if kw.arg != "reason":
                continue
            reason = "".join(_string_parts(kw.value))
            if "1462" in reason:
                hits.append((node.lineno, reason))
    return hits


def test_no_bdd_xfail_reason_attributes_to_1462() -> None:
    """No BDD strict-xfail reason may blame #1462 (an IMPL-only gap BDD never runs)."""
    violations: list[str] = []
    for path in sorted(_BDD_DIR.rglob("*.py")):
        for lineno, reason in _find_1462_reasons(path):
            rel = path.relative_to(_BDD_DIR.parents[1])
            violations.append(f"{rel}:{lineno}: xfail reason blames #1462 -> {reason!r}")
    assert not violations, (
        "BDD xfail markers must not attribute failures to #1462 (the in-process _impl "
        "request-path gap). BDD does not parametrize IMPL, so a #1462-attributed BDD "
        "marker is mis-attributed — find the transport the wire path actually exercises "
        "(e.g. step shadowing, salesagent-50hl). Violations:\n" + "\n".join(violations)
    )


# ── Meta-tests (the guard catches the disease, and tolerates the corrected form) ──


def _scan_source(src: str) -> list[tuple[int, str]]:
    tree = ast.parse(src)
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_xfail_call(node):
            for kw in node.keywords:
                if kw.arg == "reason" and "1462" in "".join(_string_parts(kw.value)):
                    hits.append((node.lineno, "".join(_string_parts(kw.value))))
    return hits


def test_meta_positive_catches_1462_reason() -> None:
    """Positive: a #1462-attributed xfail reason is flagged."""
    src = 'import pytest\npytest.mark.xfail(reason="window dropped (#1462)", strict=True)\n'
    assert _scan_source(src), "guard must flag a #1462 xfail reason"


def test_meta_positive_catches_multiline_concatenated_reason() -> None:
    """Positive (would-be-missed): a reason split across implicit-concatenated parts."""
    src = (
        "import pytest\n"
        "pytest.mark.xfail(\n"
        '    reason="attribution_window: validation can\'t fire — "\n'
        '    "request path drops post_click (#1462)",\n'
        "    strict=True,\n"
        ")\n"
    )
    assert _scan_source(src), "guard must flag a #1462 reason even when concatenated across lines"


def test_meta_negative_allows_corrected_reason() -> None:
    """Negative: the corrected #1417 reason is NOT flagged."""
    src = 'import pytest\npytest.mark.xfail(reason="window dropped by step shadowing (salesagent-50hl)", strict=True)\n'
    assert not _scan_source(src), "guard must tolerate a non-#1462 reason"


def test_meta_negative_ignores_1462_in_comments() -> None:
    """Negative: #1462 in a comment (not a reason string) is fine."""
    src = (
        "import pytest\n"
        "# #1462 is the in-process _impl path; BDD does not run impl\n"
        'pytest.mark.xfail(reason="step shadowing (salesagent-50hl)", strict=True)\n'
    )
    assert not _scan_source(src), "guard must scan reason strings only, not comments"
