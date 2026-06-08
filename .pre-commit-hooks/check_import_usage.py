#!/usr/bin/env python3
"""Pre-commit hook to catch usage of classes/functions without imports.

This prevents bugs like PR #332 where Error class was used but not imported,
causing NameError in production.

Usage:
    python check_import_usage.py <file1> <file2> ...

Exit codes:
    0: All classes/functions used are imported
    1: Found usage of classes/functions without imports
"""

import ast
import sys
from pathlib import Path


class ImportCollector(ast.NodeVisitor):
    """Collect all imported names and locally defined classes/functions from a module."""

    def __init__(self):
        self.imports: set[str] = set()
        self.has_star_import: bool = False

    def visit_Import(self, node):
        for alias in node.names:
            name = alias.asname if alias.asname else alias.name
            self.imports.add(name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        for alias in node.names:
            # Check for wildcard import (from foo import *)
            if alias.name == "*":
                self.has_star_import = True
            else:
                name = alias.asname if alias.asname else alias.name
                self.imports.add(name)
        self.generic_visit(node)

    def visit_ClassDef(self, node):
        """Track class definitions (they don't need imports)."""
        self.imports.add(node.name)
        self.generic_visit(node)

    def _add_params(self, node):
        """Function parameters are locally-defined names, not imports.

        Without this, a parameter used as a callable (dependency injection /
        factory pattern, e.g. ``def f(exc_type): raise exc_type(...)``) is
        falsely flagged as a missing import.
        """
        args = node.args
        for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs):
            self.imports.add(arg.arg)
        if args.vararg:
            self.imports.add(args.vararg.arg)
        if args.kwarg:
            self.imports.add(args.kwarg.arg)

    def visit_FunctionDef(self, node):
        """Track function definitions and their parameters (they don't need imports)."""
        self.imports.add(node.name)
        self._add_params(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node):
        """Track async function definitions and their parameters."""
        self.imports.add(node.name)
        self._add_params(node)
        self.generic_visit(node)

    def visit_Assign(self, node):
        """Collect variable assignments that create aliases or module-level constants."""
        # Track simple Name = ... assignments at module level
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            # Track the variable name
            var_name = node.targets[0].id
            # Add to imports so it's recognized as defined
            # This handles aliases (Task = WorkflowStep) and constants (SELECTED_ADAPTER = ...)
            self.imports.add(var_name)
        self.generic_visit(node)

    def visit_AnnAssign(self, node):
        """Collect annotated assignments (e.g., VAR: Type = value)."""
        # Track annotated assignments like TARGETING_CAPABILITIES: dict[str, X] = {...}
        if isinstance(node.target, ast.Name):
            self.imports.add(node.target.id)
        self.generic_visit(node)


class UsageCollector(ast.NodeVisitor):
    """Collect usage of names that might need imports."""

    def __init__(self):
        self.usages: set[tuple[str, int]] = set()  # (name, line_number)

    def visit_Call(self, node):
        """Check function calls and class instantiations."""
        name = self._get_name(node.func)
        if name and name[0].isupper():  # Likely a class (PascalCase)
            self.usages.add((name, node.lineno))
        self.generic_visit(node)

    def visit_Raise(self, node):
        """Check raised exceptions."""
        if node.exc and isinstance(node.exc, ast.Call):
            name = self._get_name(node.exc.func)
            if name:
                self.usages.add((name, node.lineno))
        self.generic_visit(node)

    def _get_name(self, node) -> str | None:
        """Extract name from node (handles Name and Attribute)."""
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            # For chained attributes like foo.bar.Baz, just get the first part
            # since that's what needs to be imported
            base = node
            while isinstance(base, ast.Attribute):
                base = base.value
            if isinstance(base, ast.Name):
                return base.id
        return None


def check_file(filepath: Path) -> list[str]:
    """Check a file for usage of names without imports.

    Returns:
        List of error messages (empty if no issues)
    """
    try:
        code = filepath.read_text()
        tree = ast.parse(code, filename=str(filepath))
    except SyntaxError as e:
        return [f"{filepath}:{e.lineno}: Syntax error: {e.msg}"]
    except Exception as e:
        return [f"{filepath}: Failed to parse: {e}"]

    # Collect imports
    import_collector = ImportCollector()
    import_collector.visit(tree)

    # Skip files with wildcard imports (can't reliably check them)
    if import_collector.has_star_import:
        return []

    # Collect usages
    usage_collector = UsageCollector()
    usage_collector.visit(tree)

    # Find usages without imports
    errors = []
    builtins = {
        "Exception",
        "ValueError",
        "TypeError",
        "KeyError",
        "AttributeError",
        "RuntimeError",
        "NameError",
        "ImportError",
        "NotImplementedError",
        "StopIteration",
        "IndexError",
        "OSError",
        "IOError",
        "FileNotFoundError",
        "PermissionError",
        "TimeoutError",
        "ConnectionError",
        "SystemExit",
        "KeyboardInterrupt",
        "dict",
        "list",
        "set",
        "tuple",
        "str",
        "int",
        "float",
        "bool",
        "bytes",
        "object",
        "type",
        "len",
        "range",
        "enumerate",
        "zip",
        "map",
        "filter",
        "sum",
        "min",
        "max",
        "abs",
        "print",
        "open",
        "input",
        "iter",
        "next",
    }

    for name, lineno in usage_collector.usages:
        # Skip builtins
        if name in builtins:
            continue

        # Skip if imported
        if name in import_collector.imports:
            continue

        # Skip common false positives
        if name in ["self", "cls", "super"]:
            continue

        # Report potential missing import
        errors.append(
            f"{filepath}:{lineno}: '{name}' is used but may not be imported "
            f"(add to imports or ignore if false positive)"
        )

    return errors


def main():
    """Check all provided files for missing imports."""
    if len(sys.argv) < 2:
        print("Usage: check_import_usage.py <file1> <file2> ...", file=sys.stderr)
        return 1

    all_errors = []
    for filepath_str in sys.argv[1:]:
        filepath = Path(filepath_str)

        # Only check Python files
        if filepath.suffix != ".py":
            continue

        # Skip test files (they have fixtures/mocks)
        if "test" in filepath.parts:
            continue

        # Skip __init__ files
        if filepath.name == "__init__.py":
            continue

        errors = check_file(filepath)
        all_errors.extend(errors)

    if all_errors:
        print("\n❌ Found potential missing imports:\n", file=sys.stderr)
        for error in all_errors:
            print(f"  {error}", file=sys.stderr)
        print("\n💡 Tip: Verify these classes/functions are imported at the top of the file.", file=sys.stderr)
        print("   If they're defined locally or are false positives, this can be ignored.", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
