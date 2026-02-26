"""Guard: Test fixtures must use valid brand_manifest_policy values.

The brand_manifest_policy field accepts only: require_auth, require_brand, public.
Invalid values like "flexible" silently fall through policy checks, acting like "public"
without the developer's intent being explicit.

beads: salesagent-fd3i
"""

import ast
from pathlib import Path

VALID_POLICIES = {"require_auth", "require_brand", "public"}

# Directories to scan
TEST_DIRS = [
    Path("tests/e2e"),
    Path("tests/integration"),
    Path("tests/integration_v2"),
]


def _find_string_values_for_key(tree: ast.Module, key: str) -> list[tuple[int, str]]:
    """Find all string values assigned to a given keyword in function calls or dict literals."""
    results: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        # keyword argument: func(..., key="value")
        if (
            isinstance(node, ast.keyword)
            and node.arg == key
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            results.append((node.value.lineno, node.value.value))
        # dict literal: {"key": "value"}
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values, strict=True):
                if (
                    isinstance(k, ast.Constant)
                    and k.value == key
                    and isinstance(v, ast.Constant)
                    and isinstance(v.value, str)
                ):
                    results.append((v.lineno, v.value))
    return results


def test_no_invalid_brand_manifest_policy_in_test_fixtures():
    """All brand_manifest_policy values in test fixtures must be valid."""
    violations: list[str] = []

    for test_dir in TEST_DIRS:
        if not test_dir.exists():
            continue
        for py_file in sorted(test_dir.rglob("*.py")):
            source = py_file.read_text()
            if "brand_manifest_policy" not in source:
                continue
            tree = ast.parse(source, filename=str(py_file))
            for lineno, value in _find_string_values_for_key(tree, "brand_manifest_policy"):
                if value not in VALID_POLICIES:
                    violations.append(
                        f"{py_file}:{lineno} — brand_manifest_policy={value!r} "
                        f"(valid: {', '.join(sorted(VALID_POLICIES))})"
                    )

    assert not violations, "Invalid brand_manifest_policy values found in test fixtures:\n" + "\n".join(
        f"  {v}" for v in violations
    )
