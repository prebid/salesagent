"""Guard: the sync_accounts natural-key extractor rejects a brandless entry.

PR1399 R3-F1. SDK 5.7's ``SyncAccountsRequest.accounts`` is
``list[Accounts | Accounts3]``; the ``Accounts3`` (account-reference /
settings-update) arm makes ``brand`` optional, so a brandless entry parses with
``brand=None``. The pinned 3.1 spec (sync-accounts-request.json @ v3.1-04f59d2d5)
marks every entry ``required: ["brand", "operator", "billing"]`` — a brandless
entry MUST be a clean buyer-correctable 400, not an unguarded ``None.domain``
AttributeError that fell through to a 500.

``_extract_natural_key`` dereferences ``brand.domain``. This structural guard
pins the regression: the function MUST raise on a None brand BEFORE that
dereference. It is a defense-in-depth tripwire on the extractor; the primary
behavioral pin is
``tests/integration/test_sync_accounts.py::TestSyncAccountsBrandlessEntryRejected``
(wire VALIDATION_ERROR/correctable on A2A+REST) plus the boundary-to-boundary
``BR-UC-011-account-validation.feature`` scenario.

A whole-tree AST guard for "unguarded ``.brand.domain``" is intentionally NOT
used: the disease is control-flow/aliasing dependent (``brand = entry.brand``
then a guard on the local; or model-guaranteed sites like
``AccountReferenceByNaturalKey.brand`` which is structurally required), so a
tree-wide checker would false-positive. The disposition scan (in the bead)
confirmed the extractor was the only live instance.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ACCOUNTS_MODULE = REPO_ROOT / "src" / "core" / "tools" / "accounts.py"
EXTRACTOR_NAME = "_extract_natural_key"

FuncDef = (ast.FunctionDef, ast.AsyncFunctionDef)


def _is_brand_none_test(test: ast.expr) -> bool:
    """True for ``brand is None`` / ``entry.brand is None`` comparisons."""
    if not isinstance(test, ast.Compare):
        return False
    if not (len(test.ops) == 1 and isinstance(test.ops[0], ast.Is)):
        return False
    if not (len(test.comparators) == 1 and isinstance(test.comparators[0], ast.Constant)):
        return False
    if test.comparators[0].value is not None:
        return False
    left = test.left
    if isinstance(left, ast.Name) and left.id == "brand":
        return True
    return isinstance(left, ast.Attribute) and left.attr == "brand"


def _raises(body: list[ast.stmt]) -> bool:
    return any(isinstance(node, ast.Raise) for node in ast.walk(ast.Module(body=body, type_ignores=[])))


def _first_domain_access_lineno(func: ast.FunctionDef | ast.AsyncFunctionDef) -> int | None:
    """Lineno of the first ``<expr>.domain`` attribute access in the function body."""
    linenos = [node.lineno for node in ast.walk(func) if isinstance(node, ast.Attribute) and node.attr == "domain"]
    return min(linenos) if linenos else None


def function_guards_brand_before_domain(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True iff the function raises on a None brand before any ``.domain`` deref.

    Shared predicate so the real-source test and the positive/negative
    meta-tests exercise the exact same logic.
    """
    domain_lineno = _first_domain_access_lineno(func)
    if domain_lineno is None:
        return True  # No .domain deref at all → nothing to guard.
    for node in ast.walk(func):
        if (
            isinstance(node, ast.If)
            and _is_brand_none_test(node.test)
            and _raises(node.body)
            and node.lineno < domain_lineno
        ):
            return True
    return False


def _find_extractor() -> ast.FunctionDef | ast.AsyncFunctionDef:
    tree = ast.parse(ACCOUNTS_MODULE.read_text(), filename=str(ACCOUNTS_MODULE))
    for node in ast.walk(tree):
        if isinstance(node, FuncDef) and node.name == EXTRACTOR_NAME:
            return node
    raise AssertionError(f"{EXTRACTOR_NAME} not found in {ACCOUNTS_MODULE} — did the extractor move?")


def test_extractor_guards_brand_before_domain():
    """`_extract_natural_key` must raise on a None brand before dereferencing `.domain`."""
    func = _find_extractor()
    assert function_guards_brand_before_domain(func), (
        f"{EXTRACTOR_NAME} dereferences brand.domain without first raising on a None brand. "
        "A brandless Accounts3 (account-reference) entry would AttributeError → 500 instead of a "
        "clean VALIDATION_ERROR/400. Add `if brand is None: raise AdCPValidationError(...)` before the deref."
    )


# ── Meta-tests: prove the predicate catches the disease and accepts the cure ──

_GUARDED_SAMPLE = """
def _extract_natural_key(entry):
    brand = entry.brand
    if brand is None:
        raise AdCPValidationError("brand required")
    brand_domain = brand.domain
    return brand_domain
"""

_UNGUARDED_SAMPLE = """
def _extract_natural_key(entry):
    brand = entry.brand
    brand_domain = brand.domain
    return brand_domain
"""

_GUARD_AFTER_DEREF_SAMPLE = """
def _extract_natural_key(entry):
    brand = entry.brand
    brand_domain = brand.domain
    if brand is None:
        raise AdCPValidationError("too late")
    return brand_domain
"""


def _parse_single_func(src: str) -> ast.FunctionDef:
    return next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef))


def test_meta_guard_accepts_guarded_function():
    """Positive: a function that raises on None brand before deref passes."""
    assert function_guards_brand_before_domain(_parse_single_func(_GUARDED_SAMPLE))


def test_meta_guard_rejects_unguarded_function():
    """Negative: a function that derefs brand.domain with no guard is caught."""
    assert not function_guards_brand_before_domain(_parse_single_func(_UNGUARDED_SAMPLE))


def test_meta_guard_rejects_guard_after_deref():
    """Negative (ordering): a None-check that runs AFTER the deref does not count."""
    assert not function_guards_brand_before_domain(_parse_single_func(_GUARD_AFTER_DEREF_SAMPLE))
