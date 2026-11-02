"""Guard: adapter creative modules must not raise bare ValueError/Exception.

Disease: a buyer-correctable creative-input problem (missing duration, non-URL
asset, missing source) raised as bare ``ValueError``/``Exception`` in an
adapter creative module. Depending on the reaching surface the buyer then got
either SERVICE_UNAVAILABLE / transient (retry-the-unretryable) or a reasonless
per-asset ``status="failed"`` — never the CREATIVE_REJECTED / correctable
family the pinned spec (v3.1.1 enums/error-code.json) assigns to creative
rejections.

Scope: ``creatives*.py`` modules under ``src/adapters``. Creative-input
rejections raise ``AdCPCreativeRejectedError`` (or another typed ``AdCPSalesAgentError``
for a different family); internal invariants raise ``AssertionError``.
Sibling guard: ``test_guards_adapter_capability_raise`` covers the
capability-gap phrasing family across all adapter modules.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

_BANNED = {"ValueError", "Exception"}

# (file, lineno-message-prefix) pairs permitted to violate — shrink-only.
ALLOWLIST: set[tuple[str, str]] = set()


def _scan_source_text(rel: str, text: str) -> list[tuple[str, str, int]]:
    hits: list[tuple[str, str, int]] = []
    for node in ast.walk(ast.parse(text)):
        if not (isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)):
            continue
        func = node.exc.func
        if isinstance(func, ast.Name) and func.id in _BANNED:
            hits.append((rel, func.id, node.lineno))
    return hits


def _creative_modules() -> list[Path]:
    return sorted(p for p in (REPO_ROOT / "src" / "adapters").rglob("creatives*.py"))


def _scan() -> list[tuple[str, str, int]]:
    hits: list[tuple[str, str, int]] = []
    for path in _creative_modules():
        rel = str(path.relative_to(REPO_ROOT))
        hits.extend(_scan_source_text(rel, path.read_text()))
    return hits


class TestCreativeInputRaiseGuard:
    def test_scope_is_nonempty(self):
        """The glob must keep matching the GAM creatives manager (guard not vacuous)."""
        assert any("gam/managers/creatives.py" in str(p) for p in _creative_modules())

    def test_no_bare_raises_in_adapter_creative_modules(self):
        violations = [v for v in _scan() if (v[0], v[1]) not in ALLOWLIST]
        assert not violations, (
            "Bare ValueError/Exception in adapter creative modules — creative-input "
            "rejections must raise AdCPCreativeRejectedError (CREATIVE_REJECTED/"
            "correctable per the pinned spec); internal invariants use AssertionError:\n"
            + "\n".join(f"  {f}:{line} raise {name}(...)" for f, name, line in violations)
        )

    def test_allowlist_entries_still_violate(self):
        actual = {(v[0], v[1]) for v in _scan()}
        stale = ALLOWLIST - actual
        assert not stale, f"Allowlist entries no longer violating — remove them: {sorted(stale)}"


class TestGuardMetaTests:
    def test_positive_detects_valueerror(self):
        src = 'raise ValueError("Video creative missing required duration field")\n'
        assert [h[1] for h in _scan_source_text("x.py", src)] == ["ValueError"]

    def test_positive_detects_bare_exception(self):
        src = 'raise Exception("No HTML5 source content found in asset")\n'
        assert [h[1] for h in _scan_source_text("x.py", src)] == ["Exception"]

    def test_negative_typed_raise_passes(self):
        src = 'raise AdCPCreativeRejectedError("missing required duration")\n'
        assert _scan_source_text("x.py", src) == []

    def test_negative_assertion_invariant_passes(self):
        src = 'raise AssertionError("Unsupported asset type: audio")\n'
        assert _scan_source_text("x.py", src) == []

    def test_would_be_missed_reraise_documented(self):
        """Known limitation: a bare ``raise`` (re-raise) and raises OUTSIDE
        creatives*.py modules are out of scope — the capability-raise sibling
        guard and reviewers own those residuals."""
        src = "raise\n"
        assert _scan_source_text("x.py", src) == []
