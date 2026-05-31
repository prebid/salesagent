"""Structural guard: ``Error(code=...)`` construction is forbidden in business logic.

Wire-shape decisions live at the transport boundary, not in ``_impl``. Tools and
adapters that need to surface an error to the buyer MUST raise a typed
``AdCPError`` subclass; the boundary translator runs
``build_two_layer_error_envelope()`` once at the boundary.

This guard counts ``Error(code=...)`` literal construction sites in
``src/core/tools/`` and ``src/adapters/`` per file, with a per-file CAP frozen
at substrate landing. The cap can only SHRINK over time as the cleanup
sweep lands. New code is never added to the cap — the only way to add a new file or
raise a cap is to land a fix that exceeds it intentionally, which is a code-
review red flag.

Capped files may carry a ``# FIXME(salesagent-pattern-a): migrate to typed
AdCPError raise`` comment at every Error(code=...) site so reviewers can grep
their way to the cleanup work. The comments are aspirational; the cap dict
+ ratchet (`assert_caps_only_shrink`) is the actual enforcement mechanism.

Spec: AdCP 3.0.0 (error-handling.mdx) — two-layer envelope is normative.
"""

from __future__ import annotations

import ast
from pathlib import Path

# Pattern A (``Error(code=...)`` construction in business logic) is fully drained:
# the cap is empty, so any new site fails the guard immediately. The handful of
# legitimate per-item advisory ``Error(code=...)`` sites in success envelopes
# (e.g., ``GetMediaBuysResponse.errors[]``) carry an inline
# ``# structural-guard:`` marker and are skipped by ``_count_pattern_a_sites``
# — legitimacy is recorded at the site, not in this dict. A plain comment
# (not ``# noqa:``) is used so ruff does not parse it as a malformed directive.
PATTERN_A_PER_FILE_CAP: dict[str, int] = {}

_SKIP_MARKER = "# structural-guard:"

from tests.unit._ast_helpers import REPO_ROOT, SCAN_DIRS, safe_parse
from tests.unit._ast_helpers import rel as _rel


def _count_pattern_a_sites(filepath: Path) -> list[int]:
    """Return line numbers of ``Error(code=...)`` literals not marked for skip.

    Sites carrying a ``# structural-guard:`` comment anywhere within the
    call's line span are legitimate per-item advisory results in a success
    envelope and are excluded.
    """
    from tests.unit._ast_helpers import collect_error_aliases

    tree = safe_parse(filepath)
    if tree is None:
        return []

    source_lines = filepath.read_text().splitlines()
    aliases = collect_error_aliases(tree)
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
        if not any(kw.arg == "code" for kw in node.keywords):
            continue
        start = node.lineno - 1
        end = (getattr(node, "end_lineno", None) or node.lineno) - 1
        if any(_SKIP_MARKER in source_lines[i] for i in range(start, min(end + 1, len(source_lines)))):
            continue
        lines.append(node.lineno)
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

        assert_capped_files_still_exist(PATTERN_A_PER_FILE_CAP, "PATTERN_A_PER_FILE_CAP", repo_root=REPO_ROOT)

    def test_caps_only_shrink(self):
        """Sites in capped files must equal the cap exactly (or be below it).

        If sites have shrunk, lower the cap immediately. Caps that lag reality
        weaken the ratchet — new violations can sneak in while the cap is high.
        """
        from tests.unit._per_file_cap_guard import assert_caps_only_shrink

        assert_caps_only_shrink(PATTERN_A_PER_FILE_CAP, _count_pattern_a_sites, repo_root=REPO_ROOT)
