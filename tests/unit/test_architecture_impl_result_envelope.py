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

_TOOLS_DIR = Path(__file__).resolve().parents[2] / "src" / "core" / "tools"
_TARGET_FUNC = "_update_media_buy_impl"
_BARE_RETURN_TYPES = {"UpdateMediaBuySuccess", "UpdateMediaBuyError"}


def _get_impl_func(source: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == _TARGET_FUNC:
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


def _find_bare_domain_returns(func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[int]:
    """Find lines where UpdateMediaBuySuccess/Error are returned bare (not inside UpdateMediaBuyResult)."""
    bare_lines = []
    for node in ast.walk(func):
        if not isinstance(node, ast.Return):
            continue
        val = node.value
        if val is None:
            continue
        # Direct: return UpdateMediaBuySuccess(...) or return UpdateMediaBuyError(...)
        if isinstance(val, ast.Call):
            func_name = None
            if isinstance(val.func, ast.Name):
                func_name = val.func.id
            elif isinstance(val.func, ast.Attribute):
                func_name = val.func.attr
            if func_name in _BARE_RETURN_TYPES:
                bare_lines.append(node.lineno)
    return bare_lines


def test_update_impl_returns_result_envelope():
    """_update_media_buy_impl return annotation is UpdateMediaBuyResult."""
    impl_file = _TOOLS_DIR / "media_buy_update.py"
    assert impl_file.exists(), f"Expected {impl_file} to exist"
    source = impl_file.read_text()
    func = _get_impl_func(source)
    assert func is not None, f"{_TARGET_FUNC} not found in {impl_file}"
    ann = _return_annotation_name(func)
    assert ann == "UpdateMediaBuyResult", (
        f"{_TARGET_FUNC} return annotation is '{ann}', expected 'UpdateMediaBuyResult'. "
        f"Wire transports cannot surface ProtocolEnvelope TaskStatus without the envelope wrapper."
    )


def test_update_impl_no_bare_domain_returns():
    """_update_media_buy_impl has no unwrapped return UpdateMediaBuySuccess/Error."""
    impl_file = _TOOLS_DIR / "media_buy_update.py"
    source = impl_file.read_text()
    func = _get_impl_func(source)
    assert func is not None
    bare = _find_bare_domain_returns(func)
    assert bare == [], (
        f"{_TARGET_FUNC} has bare domain returns on lines {bare}. "
        f"Wrap them: UpdateMediaBuyResult(response=..., status=AdcpTaskStatus.completed.value)"
    )


# --- Meta-tests: verify the guard logic itself ---


def test_guard_positive_rejects_wrong_annotation():
    """Guard catches an impl annotated with the bare domain union type."""
    source = """
async def _update_media_buy_impl(req, identity=None) -> UpdateMediaBuySuccess | UpdateMediaBuyError:
    return UpdateMediaBuySuccess(media_buy_id="x")
"""
    func = _get_impl_func(source)
    assert func is not None
    ann = _return_annotation_name(func)
    assert ann != "UpdateMediaBuyResult", "Meta-test: guard should reject this annotation"


def test_guard_negative_accepts_correct_annotation():
    """Guard accepts an impl correctly annotated with UpdateMediaBuyResult."""
    source = """
async def _update_media_buy_impl(req, identity=None) -> UpdateMediaBuyResult:
    return UpdateMediaBuyResult(response=UpdateMediaBuySuccess(media_buy_id="x"), status="completed")
"""
    func = _get_impl_func(source)
    assert func is not None
    ann = _return_annotation_name(func)
    assert ann == "UpdateMediaBuyResult"
    bare = _find_bare_domain_returns(func)
    assert bare == [], "Correctly wrapped return should not be a bare domain return"


def test_guard_detects_bare_return_inside_result_call():
    """Guard distinguishes bare returns from wrapped returns inside UpdateMediaBuyResult."""
    source = """
async def _update_media_buy_impl(req, identity=None) -> UpdateMediaBuyResult:
    return UpdateMediaBuySuccess(media_buy_id="x")
"""
    func = _get_impl_func(source)
    assert func is not None
    bare = _find_bare_domain_returns(func)
    assert bare == [3], f"Meta-test: bare return on line 3 should be detected, got {bare}"
