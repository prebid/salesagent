"""Guard: Parent models with nested model fields must override model_dump().

Enforces CLAUDE.md Pattern #4: Pydantic doesn't auto-call custom model_dump()
on nested models. Any class extending AdCPBaseModel that declares a list[X]
or X | None field where X has its own custom model_dump() MUST override
model_dump() to explicitly serialize the children.

Scanning approach: AST — parse src/core/schemas*.py, find class defs that
inherit AdCPBaseModel (transitively), inspect their annotations for nested
model fields, verify the class body defines model_dump.
"""

import ast

from tests.unit._architecture_helpers import (
    parse_module,
    repo_root,
    src_python_files,
)

# Models with custom model_dump() that need explicit nested serialization
# upstream (manually maintained — populate by inspecting src/core/schemas*.py).
_MODELS_WITH_CUSTOM_DUMP: set[str] = {
    "Creative",
    "Product",
    "MediaBuy",
    "Format",
    "Package",
    "DeliveryMetrics",
}

# Allowlist — classes that legitimately don't need an override (e.g., the
# nested type's default Pydantic dump is sufficient because no internal-only
# fields are involved).
_ALLOWLIST: set[tuple[str, str]] = set()


def _has_nested_dumpable_field(cls_node: ast.ClassDef) -> bool:
    for stmt in cls_node.body:
        if not isinstance(stmt, ast.AnnAssign):
            continue
        ann = stmt.annotation
        # Match: list[Foo] where Foo in _MODELS_WITH_CUSTOM_DUMP
        if isinstance(ann, ast.Subscript) and isinstance(ann.value, ast.Name):
            if ann.value.id in {"list", "List"}:
                slice_node = ann.slice
                if isinstance(slice_node, ast.Name) and slice_node.id in _MODELS_WITH_CUSTOM_DUMP:
                    return True
        # Match: Foo | None
        if isinstance(ann, ast.BinOp) and isinstance(ann.op, ast.BitOr):
            if isinstance(ann.left, ast.Name) and ann.left.id in _MODELS_WITH_CUSTOM_DUMP:
                return True
    return False


def _defines_model_dump(cls_node: ast.ClassDef) -> bool:
    return any(isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)) and s.name == "model_dump" for s in cls_node.body)


def test_classes_with_nested_models_override_model_dump():
    repo = repo_root()
    violations: list[str] = []
    for path in src_python_files(repo):
        if "schemas" not in path.name and "/schemas" not in str(path):
            continue
        tree = parse_module(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            rel = str(path.relative_to(repo))
            if (rel, node.name) in _ALLOWLIST:
                continue
            if _has_nested_dumpable_field(node) and not _defines_model_dump(node):
                violations.append(f"{rel}:{node.lineno}:{node.name}")
    assert not violations, "Classes with nested-model fields must override model_dump():\n" + "\n".join(
        f"  {v}" for v in violations
    )
