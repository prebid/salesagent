"""Structural guard: ``Error(code=...)`` construction is forbidden in business logic.

Wire-shape decisions live at the transport boundary, not in ``_impl``. Tools and
adapters that need to surface an error to the buyer MUST raise a typed
``AdCPError`` subclass; the boundary translator runs
``build_two_layer_error_envelope()`` once at the boundary.

This guard counts ``Error(code=...)`` literal construction sites in
``src/core/tools/`` and ``src/adapters/`` per file, with a per-file CAP frozen
at substrate landing. The cap can only SHRINK over time (per PR 2 cleanup
sweep). New code is never added to the cap — the only way to add a new file or
raise a cap is to land a fix that exceeds it intentionally, which is a code-
review red flag.

Each capped file should carry a ``# FIXME(error-emission-architecture-#N)``
comment at every Error(code=...) site referencing the architecture issue.

Spec: AdCP 3.0.6 CHANGELOG 91b6e2c — two-layer envelope is normative.
"""

from __future__ import annotations

import ast
from pathlib import Path

# Per-file caps captured at substrate landing. PR 2 drains these to zero.
# Cannot be raised; only lowered. The guard fails if any file exceeds its cap
# or if a new file shows up with Pattern A sites.
#
# Every entry below is a MIGRATION TARGET tracked by issue #1304 — each
# capped file carries ``# FIXME(#1304): migrate to typed AdCPError raise``
# comments at the Error(code=...) sites so reviewers can grep their way to
# the cleanup work. There are no legitimate-floor exceptions in this dict;
# legitimate per-item advisory Error(code=...) sites in success envelopes
# use the ``# noqa: structural-guard`` marker (4 known sites in PR 2 plan).
# When PR 2 sub-batches land, drop the relevant entry below to zero rather
# than gradually lowering it — keep the cap honest.
PATTERN_A_PER_FILE_CAP: dict[str, int] = {
    "src/adapters/broadstreet/adapter.py": 13,
    "src/adapters/google_ad_manager.py": 22,
    "src/adapters/kevel.py": 5,
    "src/adapters/triton_digital.py": 5,
    "src/core/tools/accounts.py": 2,
    "src/core/tools/creative_formats.py": 1,
    "src/core/tools/creatives/_processing.py": 1,
    "src/core/tools/media_buy_create.py": 4,
    "src/core/tools/media_buy_delivery.py": 5,
    "src/core/tools/media_buy_update.py": 21,
    "src/core/tools/signals.py": 3,
}

# Anchor scan paths to the repo root so the guard works regardless of CWD
# (CI runs from the repo root; agents/IDEs may launch pytest from a subdir).
_REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_DIRS = [_REPO_ROOT / "src/core/tools", _REPO_ROOT / "src/adapters"]


def _rel(path: Path) -> str:
    """Return path relative to repo root for stable allowlist keys."""
    return str(path.relative_to(_REPO_ROOT))


def _count_pattern_a_sites(filepath: Path) -> list[int]:
    """Return line numbers of ``Error(code=...)`` literals in the file."""
    # Reuse the shared alias collector from the existing code-compliance guard
    # rather than duplicate the AST walk; both guards target the same Error
    # imports.
    from tests.unit.test_architecture_error_code_compliance import _collect_error_aliases

    if not filepath.exists():
        return []
    try:
        tree = ast.parse(filepath.read_text(), filename=str(filepath))
    except SyntaxError:
        return []

    aliases = _collect_error_aliases(tree)
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        matched = False
        if isinstance(func, ast.Name) and func.id in aliases:
            matched = True
        elif isinstance(func, ast.Attribute) and func.attr == "Error":
            matched = True
        if not matched:
            continue
        for kw in node.keywords:
            if kw.arg == "code":
                lines.append(node.lineno)
                break
    return lines


class TestNoErrorConstructionInImpl:
    """Pattern A (``Error(code=...)`` in business logic) is forbidden and shrinking."""

    def test_pattern_a_sites_within_caps(self):
        """Every scanned file must be at or below its allowlisted cap. New files fail immediately."""
        from tests.unit._per_file_cap_guard import assert_per_file_caps

        assert_per_file_caps(
            cap_dict=PATTERN_A_PER_FILE_CAP,
            count_sites=_count_pattern_a_sites,
            scan_dirs=SCAN_DIRS,
            site_label="Pattern A",
            typed_raise_hint="convert to typed AdCPError raise (e.g., AdCPMediaBuyNotFoundError)",
            rel=_rel,
        )

    def test_capped_files_still_exist(self):
        """Stale-cap detection: if a file in the cap dict no longer exists, the cap is stale."""
        from tests.unit._per_file_cap_guard import assert_capped_files_still_exist

        assert_capped_files_still_exist(PATTERN_A_PER_FILE_CAP, "PATTERN_A_PER_FILE_CAP", repo_root=_REPO_ROOT)

    def test_caps_only_shrink(self):
        """Sites in capped files must equal the cap exactly (or be below it).

        If sites have shrunk, lower the cap immediately. Caps that lag reality
        weaken the ratchet — new violations can sneak in while the cap is high.
        """
        from tests.unit._per_file_cap_guard import assert_caps_only_shrink

        assert_caps_only_shrink(PATTERN_A_PER_FILE_CAP, _count_pattern_a_sites, repo_root=_REPO_ROOT)
