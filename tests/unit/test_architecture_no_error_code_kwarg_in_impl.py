"""Guard: error_code= must not bypass the typed AdCPError hierarchy.

The wire error code is the identity of a typed AdCPError subclass. Passing
``error_code=`` to an ``AdCP*Error(...)`` constructor bypasses that hierarchy and
can leak a code that is neither standard nor mapped (the original A1 defect). The
only sanctioned override is ``AdCPError.synthesize(error_code=...)``, used by the two
boundary helpers that must construct a wire code the class hierarchy does not model.

This guard scans ``src/core/`` and ``src/adapters/`` for any call that passes
``error_code=`` to an ``AdCP*Error`` constructor OR to ``.synthesize()``. After the
error-emission migration, the only such calls are the two ``synthesize()`` sites —
pinned in the allowlist so a new bypass (or a new synthesize caller) fails the build.
"""

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_DIRS = [REPO_ROOT / "src" / "core", REPO_ROOT / "src" / "adapters"]

# The only sanctioned error_code= sites: the two boundary helpers that call
# AdCPError.synthesize(). Keyed by (relative_path, enclosing_function_name) so the
# allowlist survives line-number shifts. This cap is exactly two.
KNOWN_VIOLATIONS = {
    ("src/core/context_manager.py", "audit_workflow_step_failure"),
    ("src/core/tool_error_logging.py", "handle_tool_error"),
}


def _func_targets_adcp_error_or_synthesize(func: ast.expr) -> bool:
    """True if a Call's func is an AdCP*Error constructor or a .synthesize() call."""
    if isinstance(func, ast.Name):
        return func.id.startswith("AdCP") and func.id.endswith("Error")
    if isinstance(func, ast.Attribute):
        if func.attr == "synthesize":
            return True
        return func.attr.startswith("AdCP") and func.attr.endswith("Error")
    return False


def _find_error_code_kwargs() -> list[tuple[str, str, int]]:
    """Find error_code= kwargs on AdCP*Error/synthesize calls inside any function.

    Returns list of (relative_path, enclosing_function_name, lineno).
    """
    violations: list[tuple[str, str, int]] = []
    for scan_dir in SCAN_DIRS:
        for py_file in scan_dir.rglob("*.py"):
            try:
                tree = ast.parse(py_file.read_text(), filename=str(py_file))
            except SyntaxError:
                continue
            rel_path = str(py_file.relative_to(REPO_ROOT))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for child in ast.walk(node):
                    if not isinstance(child, ast.Call):
                        continue
                    if not any(kw.arg == "error_code" for kw in child.keywords):
                        continue
                    if _func_targets_adcp_error_or_synthesize(child.func):
                        violations.append((rel_path, node.name, child.lineno))
    return violations


class TestNoErrorCodeKwargInImpl:
    """error_code= on AdCPError constructors/synthesize is allowlisted to two sites."""

    def test_no_new_error_code_kwargs(self):
        """No NEW error_code= bypass beyond the two sanctioned synthesize() callers."""
        new_violations = [
            f"  {rel}:{lineno} in {func}()"
            for rel, func, lineno in _find_error_code_kwargs()
            if (rel, func) not in KNOWN_VIOLATIONS
        ]
        assert not new_violations, (
            f"Found {len(new_violations)} NEW error_code= site(s) on AdCP*Error/synthesize.\n"
            "Raise a typed AdCPError subclass instead; the only sanctioned override is "
            "AdCPError.synthesize() at the two allowlisted boundary helpers.\n" + "\n".join(new_violations)
        )

    def test_known_violations_not_stale(self):
        """Every allowlisted (file, function) must still contain a sanctioned site."""
        actual = {(rel, func) for rel, func, _ in _find_error_code_kwargs()}
        stale = KNOWN_VIOLATIONS - actual
        assert not stale, (
            f"Found {len(stale)} stale allowlist entries — the synthesize() call moved or was "
            f"removed. Update the allowlist:\n" + "\n".join(f"  {rel} :: {func}" for rel, func in sorted(stale))
        )

    def test_violation_count_capped_at_two(self):
        """Exactly two sanctioned error_code= sites exist (the synthesize() callers)."""
        all_sites = {(rel, func) for rel, func, _ in _find_error_code_kwargs()}
        msg = f"error_code= sites changed.\nFound: {sorted(all_sites)}\nAllowlist: {sorted(KNOWN_VIOLATIONS)}"
        assert all_sites == KNOWN_VIOLATIONS, msg
