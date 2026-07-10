"""Guard: _update_media_buy_impl must return UpdateMediaBuyResult, not bare domain types.

_update_media_buy_impl must wrap every return path in UpdateMediaBuyResult so
wire transports (MCP/A2A/REST) surface ProtocolEnvelope.status as the root
'status' field. Returning bare UpdateMediaBuySuccess or UpdateMediaBuyError
breaks then_response_status assertions in BDD tests.

This guard ensures:
1. _update_media_buy_impl has return annotation UpdateMediaBuyResult
2. No direct `return UpdateMediaBuySuccess(...)` or `return UpdateMediaBuyError(...)`
   exists inside _update_media_buy_impl (only wrapped inside UpdateMediaBuyResult(response=...))

beads: salesagent-egnl
"""

import ast
from pathlib import Path

import pytest

_TOOLS_DIR = Path(__file__).resolve().parents[2] / "src" / "core" / "tools"

# Every *_impl that must return its ProtocolEnvelope wrapper (so wire transports
# can surface TaskStatus) instead of a bare domain success|error union.
# (file, impl_func, expected_result_type, {bare_domain_return_types})
# #1417: extended from update-only to also pin the create path.
_IMPLS = [
    (
        "media_buy_update.py",
        "_update_media_buy_impl",
        "UpdateMediaBuyResult",
        {"UpdateMediaBuySuccess", "UpdateMediaBuyError"},
    ),
    (
        "media_buy_create.py",
        "_create_media_buy_impl",
        "CreateMediaBuyResult",
        {"CreateMediaBuySuccess", "CreateMediaBuyError"},
    ),
]


def _get_impl_func(source: str, target: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == target:
                return node
    return None


def _return_annotation_name(func: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    ann = func.returns
    if ann is None:
        return None
    if isinstance(ann, ast.Name):
        return ann.id
    if isinstance(ann, ast.Attribute):
        return ann.attr
    return ast.unparse(ann)


def _find_bare_domain_returns(func: ast.FunctionDef | ast.AsyncFunctionDef, bare_types: set[str]) -> list[int]:
    """Find lines where a bare domain success/error type is returned (not inside the Result wrapper)."""
    bare_lines = []
    for node in ast.walk(func):
        if not isinstance(node, ast.Return):
            continue
        val = node.value
        if val is None:
            continue
        # Direct: return <DomainSuccess>(...) or return <DomainError>(...)
        if isinstance(val, ast.Call):
            func_name = None
            if isinstance(val.func, ast.Name):
                func_name = val.func.id
            elif isinstance(val.func, ast.Attribute):
                func_name = val.func.attr
            if func_name in bare_types:
                bare_lines.append(node.lineno)
    return bare_lines


@pytest.mark.parametrize("filename,target,result_type,bare_types", _IMPLS)
def test_impl_returns_result_envelope(filename, target, result_type, bare_types):
    """The impl's return annotation is its ProtocolEnvelope Result wrapper."""
    impl_file = _TOOLS_DIR / filename
    assert impl_file.exists(), f"Expected {impl_file} to exist"
    source = impl_file.read_text()
    func = _get_impl_func(source, target)
    assert func is not None, f"{target} not found in {impl_file}"
    ann = _return_annotation_name(func)
    assert ann == result_type, (
        f"{target} return annotation is '{ann}', expected '{result_type}'. "
        f"Wire transports cannot surface ProtocolEnvelope TaskStatus without the envelope wrapper."
    )


@pytest.mark.parametrize("filename,target,result_type,bare_types", _IMPLS)
def test_impl_no_bare_domain_returns(filename, target, result_type, bare_types):
    """The impl has no unwrapped bare domain success/error return."""
    impl_file = _TOOLS_DIR / filename
    source = impl_file.read_text()
    func = _get_impl_func(source, target)
    assert func is not None
    bare = _find_bare_domain_returns(func, bare_types)
    assert bare == [], (
        f"{target} has bare domain returns on lines {bare}. "
        f"Wrap them: {result_type}(response=..., status=AdcpTaskStatus.completed.value)"
    )


# --- Meta-tests: verify the guard logic itself ---


def test_guard_positive_rejects_wrong_annotation():
    """Guard catches an impl annotated with the bare domain union type."""
    source = """
async def _update_media_buy_impl(req, identity=None) -> UpdateMediaBuySuccess | UpdateMediaBuyError:
    return UpdateMediaBuySuccess(media_buy_id="x")
"""
    func = _get_impl_func(source, "_update_media_buy_impl")
    assert func is not None
    ann = _return_annotation_name(func)
    assert ann != "UpdateMediaBuyResult", "Meta-test: guard should reject this annotation"


def test_guard_negative_accepts_correct_annotation():
    """Guard accepts an impl correctly annotated with UpdateMediaBuyResult."""
    source = """
async def _update_media_buy_impl(req, identity=None) -> UpdateMediaBuyResult:
    return UpdateMediaBuyResult(response=UpdateMediaBuySuccess(media_buy_id="x"), status="completed")
"""
    func = _get_impl_func(source, "_update_media_buy_impl")
    assert func is not None
    ann = _return_annotation_name(func)
    assert ann == "UpdateMediaBuyResult"
    bare = _find_bare_domain_returns(func, {"UpdateMediaBuySuccess", "UpdateMediaBuyError"})
    assert bare == [], "Correctly wrapped return should not be a bare domain return"


def test_guard_detects_bare_return_inside_result_call():
    """Guard distinguishes bare returns from wrapped returns inside UpdateMediaBuyResult."""
    source = """
async def _update_media_buy_impl(req, identity=None) -> UpdateMediaBuyResult:
    return UpdateMediaBuySuccess(media_buy_id="x")
"""
    func = _get_impl_func(source, "_update_media_buy_impl")
    assert func is not None
    bare = _find_bare_domain_returns(func, {"UpdateMediaBuySuccess", "UpdateMediaBuyError"})
    assert bare == [3], f"Meta-test: bare return on line 3 should be detected, got {bare}"
