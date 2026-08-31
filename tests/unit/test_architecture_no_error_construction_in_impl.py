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

Capped files may carry a ``migrate to typed
AdCPError raise`` comment at every Error(code=...) site so reviewers can grep
their way to the cleanup work. The comments are aspirational; the cap dict
+ ratchet (`assert_caps_only_shrink`) is the actual enforcement mechanism.

Spec: AdCP 3.0.0 (error-handling.mdx) — two-layer envelope is normative.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# Pattern A (``Error(code=...)`` construction in business logic) is fully drained:
# the cap is empty, so any new site fails the guard immediately. The handful of
# legitimate per-item advisory ``Error(code=...)`` sites in success envelopes
# (e.g., ``GetMediaBuysResponse.errors[]``) carry an inline
# ``# structural-guard:`` marker and are skipped by ``_count_pattern_a_sites``
# — legitimacy is recorded at the site, not in this dict. A plain comment
# (not a ruff suppression directive) is used so ruff does not parse it as a
# malformed directive.
PATTERN_A_PER_FILE_CAP: dict[str, int] = {}

# The ``# structural-guard:`` marker suppresses a legitimate per-item advisory
# ``Error(code=...)`` site (a success-envelope ``errors[]`` entry) from the cap above —
# but a free-form marker has no counter, so the number of SUPPRESSED sites could grow
# invisibly. Ratchet the marked-site count per file so a NEW advisory ``Error()`` site is
# a deliberate, reviewed change, not a silent suppression (#1329). Only shrinks.
# accounts.py + governance.py no longer construct advisory Error() directly: both route
# through the single ``build_advisory_error`` builder at the error boundary (outside the scan
# dirs), so their marker count dropped to zero (#1329 finding 9). media_buy_delivery /
# media_buy_list are pre-existing per-item advisory sites not yet migrated to the builder.
MARKED_PATTERN_A_SITES: dict[str, int] = {
    "src/core/tools/media_buy_delivery.py": 4,
    # 5, not 3: #1941 added two per-item advisory Error() sites here for the
    # get_media_buys success-envelope errors[] (spec-required envelope status).
    "src/core/tools/media_buy_list.py": 5,
}

_SKIP_MARKER = "# structural-guard:"

from tests.unit._architecture_helpers import REPO_ROOT, SCAN_DIRS, iter_call_expressions, safe_parse
from tests.unit._architecture_helpers import rel as _rel


def _pattern_a_site_lines(filepath: Path, *, keep_marked: bool) -> list[int]:
    """Line numbers of ``Error(code=...)`` literal construction sites.

    ``keep_marked=False`` returns the UNMARKED sites (the capped Pattern-A sites);
    ``keep_marked=True`` returns only the ``# structural-guard:``-marked sites (the
    legitimate per-item advisory results in a success envelope). A single scan with the
    marker check inverted keeps the two counters in lockstep (#1329).
    """
    from tests.unit._architecture_helpers import collect_error_aliases

    tree = safe_parse(filepath)
    if tree is None:
        return []

    source_lines = filepath.read_text().splitlines()
    aliases = collect_error_aliases(tree)
    lines: list[int] = []
    for node in iter_call_expressions(tree):
        func = node.func
        matched = False
        if isinstance(func, ast.Name) and func.id in aliases:
            matched = True
        elif isinstance(func, ast.Attribute) and func.attr == "Error":
            matched = True
        if not matched:
            continue
        # Match the ``Error`` alias construction REGARDLESS of how ``code`` is supplied.
        # Gating on a literal ``code=`` kwarg let ``Error(**advisory)`` and
        # ``Error(ErrorCode.X, "msg")`` (positional code) slip past both the Pattern-A cap
        # and the marked-site ratchet — a new advisory site could enter business logic
        # without moving either number (#1329 finding 9). The construction itself is the
        # wire-shape decision the guard forbids; how ``code`` is passed is immaterial.
        start = node.lineno - 1
        end = (getattr(node, "end_lineno", None) or node.lineno) - 1
        is_marked = any(_SKIP_MARKER in source_lines[i] for i in range(start, min(end + 1, len(source_lines))))
        if is_marked == keep_marked:
            lines.append(node.lineno)
    return lines


def _count_pattern_a_sites(filepath: Path) -> list[int]:
    """Return line numbers of ``Error(code=...)`` literals NOT marked for skip."""
    return _pattern_a_site_lines(filepath, keep_marked=False)


def _count_marked_pattern_a_sites(filepath: Path) -> list[int]:
    """Return line numbers of ``Error(code=...)`` literals that ARE marked `# structural-guard:`."""
    return _pattern_a_site_lines(filepath, keep_marked=True)


class TestNoErrorConstructionInImpl:
    """Pattern A (``Error(code=...)`` in business logic) is forbidden and shrinking."""

    @pytest.mark.arch_guard
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

    @pytest.mark.arch_guard
    def test_capped_files_still_exist(self):
        """Stale-cap detection: if a file in the cap dict no longer exists, the cap is stale."""
        from tests.unit._per_file_cap_guard import assert_capped_files_still_exist

        assert_capped_files_still_exist(PATTERN_A_PER_FILE_CAP, "PATTERN_A_PER_FILE_CAP", repo_root=REPO_ROOT)

    @pytest.mark.arch_guard
    def test_caps_only_shrink(self):
        """Sites in capped files must equal the cap exactly (or be below it).

        If sites have shrunk, lower the cap immediately. Caps that lag reality
        weaken the ratchet — new violations can sneak in while the cap is high.
        """
        from tests.unit._per_file_cap_guard import assert_caps_only_shrink

        assert_caps_only_shrink(PATTERN_A_PER_FILE_CAP, _count_pattern_a_sites, repo_root=REPO_ROOT)

    @pytest.mark.arch_guard
    def test_marked_advisory_error_sites_ratcheted(self):
        """The count of ``# structural-guard:``-suppressed ``Error()`` sites only shrinks.

        A free-form suppression marker has no counter (#1329): without this
        ratchet a new advisory ``Error(code=...)`` site could be added invisibly, since
        the marker excludes it from the Pattern-A cap. Assert no file exceeds its recorded
        marked-site count and no new file appears; when a marked site is converted to a
        typed raise, LOWER the recorded number.
        """
        actual: dict[str, int] = {}
        for scan_dir in SCAN_DIRS:
            for filepath in sorted(Path(scan_dir).rglob("*.py")):
                count = len(_count_marked_pattern_a_sites(filepath))
                if count:
                    actual[_rel(filepath)] = count

        increased = {
            k: (v, MARKED_PATTERN_A_SITES.get(k, 0)) for k, v in actual.items() if v > MARKED_PATTERN_A_SITES.get(k, 0)
        }
        assert not increased, (
            "New or increased `# structural-guard:`-suppressed Error() site(s) "
            f"(file -> (actual, recorded)): {increased}. A per-item advisory Error() is legitimate, "
            "but the count is ratcheted — raise the recorded number here only as a deliberate, reviewed change."
        )
        stale = {k: (actual.get(k, 0), v) for k, v in MARKED_PATTERN_A_SITES.items() if actual.get(k, 0) < v}
        assert not stale, (
            f"Marked-site count shrank (file -> (actual, recorded)): {stale}. Lower the recorded number in "
            "MARKED_PATTERN_A_SITES so the ratchet tracks reality."
        )


class TestErrorConstructionMatcherSelfTest:
    """Meta-tests: the matcher must not depend on HOW ``code`` is supplied (#1329 finding 9)."""

    @pytest.mark.parametrize(
        "body",
        [
            'Error(code="X", message="m")',  # literal kwarg (the form the caps were built from)
            "Error(**advisory)",  # dict-unpack — previously invisible to the guard
            'Error(ErrorCode.X, "m")',  # positional code — previously invisible to the guard
        ],
    )
    def test_all_error_construction_forms_are_detected(self, tmp_path, body):
        from tests.unit._architecture_helpers import collect_error_aliases  # noqa: F401 (import-check parity)

        src = "from adcp.types import Error\n\n\ndef _impl():\n    return " + body + "\n"
        f = tmp_path / "probe.py"
        f.write_text(src)
        # Unmarked (no ``# structural-guard:``) → counts as a Pattern-A site regardless of form.
        assert _count_pattern_a_sites(f) == [5], f"form not detected: {body!r}"

    def test_marked_site_is_excluded_for_any_form(self, tmp_path):
        src = (
            "from adcp.types import Error\n\n\n"
            "def _impl():\n"
            "    return Error(**advisory)  # structural-guard: advisory per-item result\n"
        )
        f = tmp_path / "probe_marked.py"
        f.write_text(src)
        assert _count_pattern_a_sites(f) == []  # marked -> not a capped Pattern-A site
        assert _count_marked_pattern_a_sites(f) == [5]  # but IS counted in the marked ratchet
