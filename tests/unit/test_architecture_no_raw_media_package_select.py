"""Guard: No raw select(MediaPackage) outside the repository module.

All MediaPackage data access in production code must go through
MediaBuyRepository. Direct select() calls bypass tenant isolation
and violate the repository pattern.

Integration tests are excluded — they need direct DB access for assertions.

beads: salesagent-rva2 (structural guard — no raw MediaPackage select)
"""

from pathlib import Path

import pytest

from tests.unit._architecture_helpers import assert_violations_match_allowlist, find_raw_select_violations

ROOT = Path(__file__).resolve().parents[2]

# The repository module is the ONLY allowed location for raw select(MediaPackage)
REPOSITORY_FILE = "src/core/database/repositories/media_buy.py"

# Model names that represent MediaPackage in select() calls
MEDIA_PACKAGE_MODELS = {"MediaPackage", "DBMediaPackage", "MediaPackageModel"}

# Pre-existing violations: (file_path, function_name)
# These existed before the guard was created. Allowlist shrinks as they're fixed.
# FIXME(salesagent-rva2): these should be migrated to repository calls
ALLOWLIST = {
    # media_buy_create.py — raw select missed by UoW migration (salesagent-0w1w)
    ("src/core/tools/media_buy_create.py", "execute_approved_media_buy"),
}

EXPECTED_VIOLATION_COUNT = len(ALLOWLIST)


def _find_raw_media_package_selects() -> list[tuple[str, str, int]]:
    """Find select(MediaPackage/DBMediaPackage/MediaPackageModel) calls in src/.

    Returns list of (file_path, function_name, line_number).
    Skips the repository module itself and non-production code.
    """
    return [
        (rel_path, func_name, lineno)
        for rel_path, func_name, _model, lineno in find_raw_select_violations(
            skip=lambda rel_path: rel_path == REPOSITORY_FILE,
            model_names=MEDIA_PACKAGE_MODELS,
        )
    ]


class TestNoRawMediaPackageSelect:
    """No raw select(MediaPackage) outside the repository module."""

    @pytest.mark.arch_guard
    def test_no_new_raw_media_package_select(self):
        """New raw select(MediaPackage) calls fail immediately."""
        all_violations = _find_raw_media_package_selects()

        new_violations = [(f, fn, line) for f, fn, line in all_violations if (f, fn) not in ALLOWLIST]

        if new_violations:
            msg_lines = [
                "New raw select(MediaPackage) calls found outside repository:",
                "",
            ]
            for f, fn, line in new_violations:
                msg_lines.append(f"  {f}:{line} in {fn}()")
            msg_lines.append("")
            msg_lines.append(
                "Fix: Use MediaBuyRepository.get_package() or get_packages() instead. "
                "See CLAUDE.md Pattern #3 for the repository pattern."
            )
            raise AssertionError("\n".join(msg_lines))

    @pytest.mark.arch_guard
    def test_allowlist_entries_still_exist(self):
        """Every allowlisted violation must still exist (stale entry detection)."""
        all_violations = {(f, fn) for f, fn, _line in _find_raw_media_package_selects()}
        assert_violations_match_allowlist(
            all_violations,
            ALLOWLIST,
            fix_hint="Remove fixed entries from ALLOWLIST.",
        )

    @pytest.mark.arch_guard
    def test_violation_count_matches(self):
        """Total violations match expected count (catches undocumented additions)."""
        all_violations = _find_raw_media_package_selects()
        actual = len(all_violations)
        assert actual == EXPECTED_VIOLATION_COUNT, (
            f"Expected {EXPECTED_VIOLATION_COUNT} allowlisted violations, "
            f"found {actual}. If you fixed a violation, remove it from ALLOWLIST. "
            f"If you added one, DON'T — use the repository instead."
        )
