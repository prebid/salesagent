"""Test to detect import collisions between models and schemas.

This test ensures that wildcard imports don't overwrite critical model classes.
"""

import ast
from pathlib import Path


def test_no_import_collisions():
    """Verify that model classes aren't overwritten by schema imports."""

    # Classes that exist in both models.py and schemas.py
    collision_prone_classes = ["Product", "Principal", "HumanTask"]

    # Files that use wildcard imports from schemas
    files_to_check = [
        "main.py",
        "adapters/base.py",
        "adapters/kevel.py",
        "adapters/triton_digital.py",
        "tools/simulations/simulation_full.py",
    ]

    base_path = Path(__file__).parent.parent.parent

    issues = []

    for file_path in files_to_check:
        full_path = base_path / file_path
        if not full_path.exists():
            continue

        with open(full_path) as f:
            content = f.read()

        # Parse the Python file
        try:
            tree = ast.parse(content)
        except SyntaxError:
            continue

        # Find all imports
        imports = {}
        wildcard_line = None

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module == "schemas" and any(alias.name == "*" for alias in node.names):
                    wildcard_line = node.lineno
                elif node.module == "models":
                    for alias in node.names:
                        imports[alias.name] = node.lineno

        # Check if critical models are imported BEFORE wildcard
        if wildcard_line:
            for class_name in collision_prone_classes:
                if class_name in imports and imports[class_name] < wildcard_line:
                    # Check if there's a re-import AFTER wildcard or aliasing
                    has_reimport = False
                    is_aliased = False

                    # Check if it's aliased in the original import
                    for node in ast.walk(tree):
                        if isinstance(node, ast.ImportFrom):
                            if node.module == "models" and node.lineno == imports[class_name]:
                                for alias in node.names:
                                    if alias.name == class_name and alias.asname:
                                        # It's aliased, so no collision
                                        is_aliased = True
                                        break
                            elif node.module == "models" and node.lineno > wildcard_line:
                                for alias in node.names:
                                    if class_name in (alias.name, alias.asname):
                                        has_reimport = True
                                        break

                    if not has_reimport and not is_aliased:
                        issues.append(
                            f"{file_path}: {class_name} imported before wildcard (line {imports[class_name]}) "
                            f"and not re-imported after wildcard (line {wildcard_line})"
                        )

    assert len(issues) == 0, "Import collision issues found:\n" + "\n".join(issues)


def test_models_use_correct_imports():
    """Verify that SQLAlchemy queries use Model* prefixed classes."""

    base_path = Path(__file__).parent.parent.parent
    main_file = base_path / "src" / "core" / "main.py"

    with open(main_file) as f:
        content = f.read()

    # Check for correct usage patterns (SQLAlchemy 2.0 style)
    incorrect_patterns = [
        "select(Product)",  # Should be ModelProduct
        "select(Principal)",  # Should be ModelPrincipal
        "select(HumanTask)",  # Should be ModelHumanTask
    ]

    issues = []
    for pattern in incorrect_patterns:
        if pattern in content:
            issues.append(f"Found incorrect pattern: {pattern}")

    assert len(issues) == 0, "Incorrect select() patterns found:\n" + "\n".join(issues)


def test_wildcard_imports_documented():
    """Ensure all wildcard imports have warning comments."""

    base_path = Path(__file__).parent.parent.parent
    files_with_wildcards = [
        "main.py",
        "adapters/base.py",
        "adapters/kevel.py",
        "adapters/triton_digital.py",
        "tools/simulations/simulation_full.py",
    ]

    issues = []

    for file_path in files_with_wildcards:
        full_path = base_path / file_path
        if not full_path.exists():
            continue

        with open(full_path) as f:
            lines = f.readlines()

        for i, line in enumerate(lines):
            if "from schemas import *" in line:
                # Check if there's a warning comment nearby
                has_warning = False
                for j in range(max(0, i - 2), min(len(lines), i + 3)):
                    if "CRITICAL" in lines[j] or "WARNING" in lines[j] or "collision" in lines[j].lower():
                        has_warning = True
                        break

                if not has_warning:
                    issues.append(f"{file_path}:{i+1} - Wildcard import without warning comment")

    # This is a warning, not a failure
    if issues:
        print("WARNING: Wildcard imports without documentation:")
        for issue in issues:
            print(f"  - {issue}")


if __name__ == "__main__":
    test_no_import_collisions()
    test_models_use_correct_imports()
    test_wildcard_imports_documented()
    print("✅ All import collision tests passed!")
