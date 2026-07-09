"""Guard: account-access authorization must not fail OPEN on a falsy principal.

Disease (hl35, #1417): an access-authorization decision guarded by a truthiness
check on ``principal_id`` — e.g. ``if principal_id and not repo.has_access(...)``
— silently SKIPS the ``has_access`` check when ``principal_id`` is empty/None, so
an unauthenticated caller is granted access (fail open). The fix is to obtain the
principal via ``require_principal_id(identity)`` (raises AUTH_REQUIRED on a falsy
principal) BEFORE the access check, so a missing principal fails CLOSED.

This guard bans the fail-open shape structurally (AST, not text) across ``src/``:
- Form A: ``if <principal_id> and <expr containing has_access(...)>`` — the AND
  short-circuits the access check on a falsy principal.
- Form B: ``if <principal_id>:`` (bare truthiness) whose body performs a
  ``has_access(...)`` check — a falsy principal skips the whole block.

The existing ``test_architecture_no_handrolled_identity_guard`` only scans
``*_impl`` functions; the motivating site was a helper, which slipped it.
"""

import ast

from tests.unit._architecture_helpers import (
    REPO_ROOT,
    assert_detector_catches_ast_snippets,
    parse_module,
    src_python_files,
)


def _is_principal_truthiness(node: ast.expr) -> bool:
    """A bare ``principal_id`` / ``x.principal_id`` used directly as a boolean operand."""
    if isinstance(node, ast.Name):
        return "principal_id" in node.id
    if isinstance(node, ast.Attribute):
        return node.attr == "principal_id"
    return False


def _contains_has_access_call(node: ast.AST) -> bool:
    return any(
        isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr == "has_access"
        for sub in ast.walk(node)
    )


def find_fail_open_access_violations(tree: ast.Module) -> list[int]:
    """Return line numbers of fail-open principal-gated access checks."""
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        # Form A: if principal_id and <...has_access...>
        if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And):
            if any(_is_principal_truthiness(v) for v in test.values) and any(
                _contains_has_access_call(v) for v in test.values
            ):
                lines.append(node.lineno)
                continue
        # Form B: if principal_id:  (bare truthiness) gating a has_access body
        if _is_principal_truthiness(test) and any(_contains_has_access_call(stmt) for stmt in node.body):
            lines.append(node.lineno)
    return lines


def test_no_fail_open_account_access_in_src():
    violations: list[str] = []
    for path in src_python_files(REPO_ROOT):
        for lineno in find_fail_open_access_violations(parse_module(path)):
            violations.append(f"{path.relative_to(REPO_ROOT)}:{lineno}")
    assert not violations, (
        "Fail-open account-access check(s) found — a falsy principal_id skips the "
        "has_access authorization. Obtain the principal via require_principal_id"
        "(identity) (raises AUTH_REQUIRED) BEFORE the access check, so it fails "
        "CLOSED (#1417):\n  " + "\n  ".join(violations)
    )


def test_guard_catches_known_bad_shapes():
    """Positive meta-tests: both fail-open forms must be detected."""
    assert_detector_catches_ast_snippets(
        find_fail_open_access_violations,
        snippets={
            "form_a_and_shortcircuit": (
                "def f(identity, account_id, repo):\n"
                "    principal_id = identity.principal_id\n"
                "    if principal_id and not repo.has_access(principal_id, account_id):\n"
                "        raise Boom()\n"
            ),
            "form_b_bare_truthiness_gate": (
                "def f(identity, account_id, repo):\n"
                "    if identity.principal_id:\n"
                "        if not repo.has_access(identity.principal_id, account_id):\n"
                "            raise Boom()\n"
            ),
        },
    )


def test_guard_ignores_fixed_shape():
    """Negative meta-test: the require_principal_id-first fix must NOT be flagged."""
    fixed = (
        "def f(identity, account_id, repo):\n"
        "    principal_id = require_principal_id(identity)\n"
        "    if not repo.has_access(principal_id, account_id):\n"
        "        raise Boom()\n"
    )
    assert find_fail_open_access_violations(ast.parse(fixed)) == []
