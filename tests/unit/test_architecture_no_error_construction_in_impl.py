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

# Per-file caps captured at substrate landing. The cleanup sweep drains these to zero.
# Cannot be raised; only lowered. The guard fails if any file exceeds its cap
# or if a new file shows up with Pattern A sites.
#
# Every entry below is a MIGRATION TARGET. Capped files may carry
# ``# FIXME(salesagent-pattern-a): migrate to typed AdCPError raise`` comments
# at the Error(code=...) sites to help reviewers grep to cleanup work, but
# the cap dict + ratchet (`assert_caps_only_shrink`) is the actual
# enforcement; the comments are aspirational.
#
# Legitimate per-item advisory Error(code=...) sites in success envelopes
# (e.g., GetMediaBuysResponse.errors[]) live in this dict too — they're
# allowlist-permanent, not migration targets. Their entries are marked with
# an inline comment.
# When cleanup sub-batches land, drop the relevant entry below to zero rather
# than gradually lowering it — keep the cap honest.
PATTERN_A_PER_FILE_CAP: dict[str, int] = {
    "src/adapters/broadstreet/adapter.py": 13,
    "src/adapters/google_ad_manager.py": 22,
    "src/adapters/kevel.py": 5,
    "src/adapters/triton_digital.py": 5,
    "src/core/tools/accounts.py": 2,
    "src/core/tools/media_buy_create.py": 1,  # principal-not-found AUTH_REQUIRED return (main's established contract)
    "src/core/tools/media_buy_delivery.py": 5,
    # Advisory Error() in success envelope (2 AUTH_REQUIRED + 1 TARGETING_REHYDRATION_FAILED).
    # These are returned inside GetMediaBuysResponse.errors[] alongside successful media_buys[],
    # not raised as fatal errors — legitimate per-item failure surface, allowlist-permanent.
    "src/core/tools/media_buy_list.py": 3,
    "src/core/tools/media_buy_update.py": 21,
    "src/core/tools/signals.py": 3,
}

from tests.unit._ast_helpers import REPO_ROOT, SCAN_DIRS, safe_parse
from tests.unit._ast_helpers import rel as _rel


def _count_pattern_a_sites(filepath: Path) -> list[int]:
    """Return line numbers of ``Error(code=...)`` literals in the file."""
    from tests.unit._ast_helpers import collect_error_aliases

    tree = safe_parse(filepath)
    if tree is None:
        return []

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

        assert_capped_files_still_exist(PATTERN_A_PER_FILE_CAP, "PATTERN_A_PER_FILE_CAP", repo_root=REPO_ROOT)

    def test_caps_only_shrink(self):
        """Sites in capped files must equal the cap exactly (or be below it).

        If sites have shrunk, lower the cap immediately. Caps that lag reality
        weaken the ratchet — new violations can sneak in while the cap is high.
        """
        from tests.unit._per_file_cap_guard import assert_caps_only_shrink

        assert_caps_only_shrink(PATTERN_A_PER_FILE_CAP, _count_pattern_a_sites, repo_root=REPO_ROOT)
