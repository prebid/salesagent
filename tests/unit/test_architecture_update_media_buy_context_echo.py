"""Structural guard: every ``UpdateMediaBuy{Success,Error}(...)`` site
inside ``_update_media_buy_impl`` MUST pass ``context=req.context``.

AdCP defines ``context`` as opaque correlation data the seller MUST
echo unchanged in responses (see
``adcp/types/.../update_media_buy_request.py:7427-7433``). PR #92
discovered the pause/resume branch and 7 error branches were silently
dropping the buyer's correlation key. Same defect class would re-enter
the codebase whenever a new branch is added in a future PR — this
guard catches it at unit-tier on every CI run.

The guard scans the AST of ``src/core/tools/media_buy_update.py``,
finds every constructor call to ``UpdateMediaBuySuccess`` or
``UpdateMediaBuyError``, and asserts each one passes a ``context=``
keyword argument. It does NOT verify the *value* of that argument —
that's covered by the behavioral tests in
``tests/unit/test_update_media_buy_context_echo.py``.
"""

from __future__ import annotations

import ast
from pathlib import Path

_TARGETS = {"UpdateMediaBuySuccess", "UpdateMediaBuyError"}
_SOURCE = Path(__file__).resolve().parents[2] / "src" / "core" / "tools" / "media_buy_update.py"


def _collect_response_constructors() -> list[tuple[int, str]]:
    """Walk the AST and return ``[(lineno, callee_name), ...]`` for every
    ``UpdateMediaBuy{Success,Error}(...)`` invocation that does NOT pass
    a ``context=`` keyword argument."""
    tree = ast.parse(_SOURCE.read_text())
    offenders: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = node.func
        callee_name: str | None = None
        if isinstance(callee, ast.Name):
            callee_name = callee.id
        elif isinstance(callee, ast.Attribute):
            callee_name = callee.attr
        if callee_name not in _TARGETS:
            continue
        passes_context = any(kw.arg == "context" for kw in node.keywords)
        if not passes_context:
            offenders.append((node.lineno, callee_name))
    return offenders


def test_every_response_constructor_passes_context_keyword() -> None:
    offenders = _collect_response_constructors()
    if offenders:
        formatted = "\n".join(f"  {_SOURCE.name}:{ln}  {name}(...)" for ln, name in offenders)
        raise AssertionError(
            "AdCP requires response.context to echo request.context unchanged. "
            "The following call sites do NOT pass `context=`:\n"
            f"{formatted}\n\n"
            "Add `context=req.context` to each. See #91 / PR #92 for context."
        )


def test_guard_can_actually_load_the_source() -> None:
    """Sanity: the source file exists at the path the guard scans.
    Catches a regression where the file is moved/renamed and the guard
    silently passes (no offenders to find = no failure)."""
    assert _SOURCE.exists(), f"Guard target missing: {_SOURCE}"
    assert _SOURCE.read_text(), "Guard target is empty"
