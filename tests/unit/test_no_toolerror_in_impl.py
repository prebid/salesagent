"""Tests that _impl functions raise AdCPError, not ToolError.

Validates the core invariant: business logic raises transport-agnostic
AdCPError subclasses, never fastmcp-specific ToolError.

This test scans source files to ensure no ToolError leaks into _impl functions.

beads: salesagent-9vcv, salesagent-a3vf
"""

import ast
import pathlib
import re

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

# Complex modules with many ToolError sites (salesagent-a3vf)
COMPLEX_MODULE_FILES = [
    "src/core/tools/products.py",
    "src/core/tools/media_buy_create.py",
    "src/core/tools/media_buy_update.py",
    "src/core/tools/creatives/listing.py",
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


def _find_error_dict_returns(filepath: str) -> list[tuple[int, str]]:
    """Find dict returns with 'success': False pattern in A2A handler methods.

    These should be replaced with proper exception raises.
    Returns list of (line_number, code_snippet) tuples.
    """
    path = pathlib.Path(filepath)
    if not path.exists():
        return []

    lines = path.read_text().splitlines()
    results = []
    for i, line in enumerate(lines, 1):
        # Match return statements containing "success": False
        if re.search(r"return\s*\{", line) or (
            '"success": False' in line and "return" in lines[i - 2] if i > 1 else False
        ):
            # Look at context: is this a return { "success": False, ... } block?
            # Check the surrounding lines for the pattern
            context = "\n".join(lines[max(0, i - 3) : min(len(lines), i + 5)])
            if '"success": False' in context and "return" in context:
                # Find the actual return line
                for j in range(max(0, i - 3), min(len(lines), i + 1)):
                    if "return {" in lines[j] or "return{" in lines[j]:
                        results.append((j + 1, lines[j].strip()))
                        break

    # Deduplicate by line number
    seen = set()
    unique = []
    for line_no, code in results:
        if line_no not in seen:
            seen.add(line_no)
            unique.append((line_no, code))
    return unique


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


class TestNoToolErrorInComplexModules:
    """Verify zero ToolError raises in the 4 complex module files."""

    def test_no_toolerror_in_complex_modules(self):
        """All 4 complex modules must raise AdCPError subclasses, not ToolError."""
        violations = []
        for filepath in COMPLEX_MODULE_FILES:
            sites = _find_toolerror_raises(filepath)
            for line_no, code in sites:
                violations.append(f"  {filepath}:{line_no}: {code}")

        assert not violations, (
            f"Found {len(violations)} ToolError raise(s) in complex _impl modules "
            f"(should use AdCPError subclasses):\n" + "\n".join(violations)
        )
