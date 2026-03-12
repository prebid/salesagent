"""Guard: No raw select(MediaPackage) outside the repository module.

All MediaPackage data access in production code must go through
MediaBuyRepository. Direct select() calls bypass tenant isolation
and violate the repository pattern.

Integration tests are excluded — they need direct DB access for assertions.

beads: salesagent-rva2 (structural guard — no raw MediaPackage select)
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# The repository module is the ONLY allowed location for raw select(MediaPackage)
REPOSITORY_FILE = "src/core/database/repositories/media_buy.py"

# Model names that represent MediaPackage in select() calls
MEDIA_PACKAGE_MODELS = {"MediaPackage", "DBMediaPackage", "MediaPackageModel"}

# Pre-existing violations: (file_path, function_name)
# These existed before the guard was created. Allowlist shrinks as they're fixed.
# FIXME(salesagent-rva2): these should be migrated to repository calls
ALLOWLIST = {
    # media_buy_create.py — 2 raw selects missed by UoW migration (salesagent-0w1w)
    ("src/core/tools/media_buy_create.py", "execute_approved_media_buy"),
    ("src/core/tools/media_buy_create.py", "_create_media_buy_impl"),
}

EXPECTED_VIOLATION_COUNT = len(ALLOWLIST)


def _find_raw_media_package_selects() -> list[tuple[str, str, int]]:
    """Find select(MediaPackage/DBMediaPackage/MediaPackageModel) calls in src/.

    Returns list of (file_path, function_name, line_number).
    Skips the repository module itself and non-production code.
    """
    violations = []
    src_dir = ROOT / "src"

    for py_file in src_dir.rglob("*.py"):
        rel_path = str(py_file.relative_to(ROOT))

        # Skip the repository itself — it's allowed to use raw selects
        if rel_path == REPOSITORY_FILE:
            continue

        try:
            tree = ast.parse(py_file.read_text())
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            for child in ast.walk(node):
                if not isinstance(child, ast.Call):
                    continue

                func = child.func
                if not (isinstance(func, ast.Name) and func.id == "select"):
                    continue

                if not child.args:
                    continue

                model_arg = child.args[0]
                model_name = None
                if isinstance(model_arg, ast.Name):
                    model_name = model_arg.id
                elif isinstance(model_arg, ast.Attribute):
                    model_name = model_arg.attr

                if model_name in MEDIA_PACKAGE_MODELS:
                    violations.append((rel_path, node.name, child.lineno))
                    break  # One violation per function is enough

    return violations


class TestNoRawMediaPackageSelect:
    """No raw select(MediaPackage) outside the repository module."""

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

    def test_allowlist_entries_still_exist(self):
        """Every allowlisted violation must still exist (stale entry detection)."""
        all_violations = {(f, fn) for f, fn, _line in _find_raw_media_package_selects()}

        stale = ALLOWLIST - all_violations
        if stale:
            msg_lines = [
                "Stale allowlist entries (violation was fixed — remove from allowlist):",
                "",
            ]
            for f, fn in sorted(stale):
                msg_lines.append(f"  ({f!r}, {fn!r}),")
            raise AssertionError("\n".join(msg_lines))

    def test_violation_count_matches(self):
        """Total violations match expected count (catches undocumented additions)."""
        all_violations = _find_raw_media_package_selects()
        actual = len(all_violations)
        assert actual == EXPECTED_VIOLATION_COUNT, (
            f"Expected {EXPECTED_VIOLATION_COUNT} allowlisted violations, "
            f"found {actual}. If you fixed a violation, remove it from ALLOWLIST. "
            f"If you added one, DON'T — use the repository instead."
        )
