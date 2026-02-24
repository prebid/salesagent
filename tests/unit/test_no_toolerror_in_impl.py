"""Tests that _impl functions raise AdCPError, not ToolError.

Validates the core invariant: business logic raises transport-agnostic
AdCPError subclasses, never fastmcp-specific ToolError.

This test scans source files to ensure no ToolError leaks into _impl functions.

beads: salesagent-9vcv
"""

import ast
import pathlib

# Files that should have zero ToolError raises in _impl functions
SIMPLE_MODULE_FILES = [
    "src/core/main.py",
    "src/core/auth.py",
    "src/core/helpers/creative_helpers.py",
    "src/core/tools/performance.py",
    "src/core/tools/creatives/_workflow.py",
    "src/core/tools/creatives/_sync.py",
    "src/core/tools/creatives/_assignments.py",
    "src/core/tools/media_buy_delivery.py",
    "src/core/tools/creative_formats.py",
    "src/core/tools/properties.py",
    "src/core/tools/task_management.py",
    "src/core/tools/signals.py",
]


def _find_toolerror_raises(filepath: str) -> list[tuple[int, str]]:
    """Find all 'raise ToolError(...)' in a file using AST parsing.

    Returns list of (line_number, code_snippet) tuples.
    """
    path = pathlib.Path(filepath)
    if not path.exists():
        return []

    source = path.read_text()
    try:
        tree = ast.parse(source, filename=filepath)
    except SyntaxError:
        return []

    results = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Raise) and node.exc is not None:
            # Check for raise ToolError(...)
            exc = node.exc
            if isinstance(exc, ast.Call):
                func = exc.func
                name = None
                if isinstance(func, ast.Name):
                    name = func.id
                elif isinstance(func, ast.Attribute):
                    name = func.attr
                if name == "ToolError":
                    line = source.splitlines()[node.lineno - 1].strip()
                    results.append((node.lineno, line))

    return results


class TestNoToolErrorInSimpleModules:
    """Verify zero ToolError raises in the 12 simple module files."""

    def test_no_toolerror_in_simple_modules(self):
        """All 12 simple modules must raise AdCPError subclasses, not ToolError."""
        violations = []
        for filepath in SIMPLE_MODULE_FILES:
            sites = _find_toolerror_raises(filepath)
            for line_no, code in sites:
                violations.append(f"  {filepath}:{line_no}: {code}")

        assert not violations, (
            f"Found {len(violations)} ToolError raise(s) in _impl modules "
            f"(should use AdCPError subclasses):\n" + "\n".join(violations)
        )
