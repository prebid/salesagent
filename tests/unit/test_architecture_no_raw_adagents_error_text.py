"""Structural guard: a caught Adagents*Error's text may only reach a
``logger.*`` call or ``describe_adagents_error`` (GH #1802).

``adcp``'s ``AdagentsValidationError`` can carry a resolved IP address and an
SSRF range classification (confirmed by reading ``adcp.signing.jwks`` and
``adcp.adagents``, adcp==6.6.0 -- see ``src/services/adagents_error_messages.py``'s
module docstring). A catch site that builds its own message from the caught
exception (``str(e)``, ``f"{e}"``, ``{e!s}``) instead of calling
``describe_adagents_error(e)`` reintroduces the disclosure this bug fixed.

This guard AST-scans every place this codebase branches on one of the four
adcp adagents exception classes -- both ``except Adagents*Error as e:`` and
the ``isinstance(error, Adagents*Error)`` dispatch shape -- and fails if the
bound/checked variable is referenced anywhere in that branch OTHER than
inside a ``logger.*(...)`` call (operator-only, always allowed) or as an
argument to ``describe_adagents_error(...)``. A branch that never touches the
exception object at all (a pure hardcoded string, e.g. the already-safe
NotFoundError/TimeoutError branches this bug's fix left untouched) is fine
without calling the helper -- there is nothing in it to leak.
"""

from __future__ import annotations

import ast

from tests.unit._architecture_helpers import (
    assert_guard_subject_resolves,
    repo_root,
    src_python_files,
)

_ADAGENTS_EXCEPTION_NAMES = frozenset(
    {
        "AdagentsNotFoundError",
        "AdagentsTimeoutError",
        "AdagentsValidationError",
        "AdagentsAccessBlockedError",
    }
)

# The one file allowed to construct these messages directly -- it IS
# describe_adagents_error's own implementation.
_EXEMPT_FILE = "src/services/adagents_error_messages.py"


def test_the_adagents_exception_classes_this_guard_names_still_exist() -> None:
    """Resolve all four SDK classes rather than matching their names.

    These live in the pinned ``adcp`` SDK, so a pin bump can rename or drop one
    without touching this repo at all. The scan would then match nothing for that
    class and report clean over raw message text it was written to catch.
    """
    assert_guard_subject_resolves(
        "adcp.exceptions",
        *sorted(_ADAGENTS_EXCEPTION_NAMES),
        why=(
            "This guard scans for them by name, so each missing one is a silent hole rather "
            "than a failure -- and these live in the PINNED SDK, so a pin bump can drop one "
            "without touching this repo at all."
        ),
    )


def _exception_type_names(node: ast.expr) -> set[str]:
    """Names referenced by an except clause's type expression.

    Handles a bare name (``except Foo``), a tuple of names
    (``except (Foo, Bar)``), and falls back to an empty set for anything else
    (e.g. a dotted attribute) -- this guard only needs to recognize the
    adagents exception names, which are always imported bare in this codebase.
    """
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, ast.Tuple):
        return {elt.id for elt in node.elts if isinstance(elt, ast.Name)}
    return set()


def _is_safe_call(node: ast.expr) -> bool:
    """A ``logger.<method>(...)`` or ``describe_adagents_error(...)`` call --
    the only contexts a caught exception's text may appear in.
    """
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name) and func.id == "describe_adagents_error":
        return True
    return isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "logger"


def _leaks_exception_var(node: ast.AST, var_name: str) -> bool:
    """True if *var_name* is referenced anywhere under *node* outside a safe call.

    A safe call's own subtree is skipped entirely (its arguments are exempt),
    but the walk continues into the REST of the containing statement, so a
    leaky reference alongside a safe one in the same line is still caught.
    """
    if isinstance(node, ast.Name) and node.id == var_name:
        return True
    for child in ast.iter_child_nodes(node):
        if _is_safe_call(child):
            continue
        if _leaks_exception_var(child, var_name):
            return True
    return False


def _block_leaks_exception_var(block: list[ast.stmt], var_name: str) -> bool:
    return any(_leaks_exception_var(stmt, var_name) for stmt in block)


def _isinstance_check_names(test: ast.expr) -> tuple[str, set[str]] | None:
    """``(var_name, exception_names)`` for an ``isinstance(var, Adagents*Error)``
    test (bare or tuple-of-types second arg), else ``None``.
    """
    if not (isinstance(test, ast.Call) and isinstance(test.func, ast.Name) and test.func.id == "isinstance"):
        return None
    if len(test.args) != 2 or not isinstance(test.args[0], ast.Name):
        return None
    var_name = test.args[0].id
    names = _exception_type_names(test.args[1])
    return (var_name, names) if names else None


def find_raw_adagents_error_violations(tree: ast.Module) -> list[tuple[int, str]]:
    """Return (lineno, exception_name) for every unsafe Adagents*Error handling site.

    Covers both shapes used in this codebase: ``except Adagents*Error as e:``
    and ``if/elif isinstance(error, Adagents*Error):``.
    """
    violations: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.type is not None:
            matched = _exception_type_names(node.type) & _ADAGENTS_EXCEPTION_NAMES
            if not matched:
                continue
            if node.name is None:
                # `except AdagentsValidationError:` with no `as e` -- cannot echo
                # the exception's text at all (nothing bound to reference).
                continue
            if _block_leaks_exception_var(node.body, node.name):
                violations.append((node.lineno, ", ".join(sorted(matched))))
        elif isinstance(node, ast.If):
            check = _isinstance_check_names(node.test)
            if check is None:
                continue
            var_name, names = check
            matched = names & _ADAGENTS_EXCEPTION_NAMES
            if not matched:
                continue
            if _block_leaks_exception_var(node.body, var_name):
                violations.append((node.lineno, ", ".join(sorted(matched))))
    return violations


class TestNoRawAdagentsErrorText:
    def test_every_catch_routes_through_describe_adagents_error(self) -> None:
        repo = repo_root()
        all_violations: dict[str, list[str]] = {}
        for path in src_python_files(repo):
            rel = str(path.relative_to(repo))
            if rel == _EXEMPT_FILE:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            lines = find_raw_adagents_error_violations(tree)
            if lines:
                all_violations[rel] = [f"{lineno}: except {names}" for lineno, names in lines]

        assert not all_violations, (
            "except-Adagents*Error clause(s) do not route through describe_adagents_error() "
            f"-- risk of echoing the library's raw exception text (GH #1802): {all_violations}"
        )


class TestDetectorCatchesKnownBadSnippets:
    def test_catches_str_interpolation_without_the_helper(self) -> None:
        snippet = """
try:
    pass
except AdagentsValidationError as e:
    msg = f"Invalid: {e}"
"""
        tree = ast.parse(snippet)
        violations = find_raw_adagents_error_violations(tree)
        assert violations == [(4, "AdagentsValidationError")]

    def test_catches_all_four_exception_names(self) -> None:
        snippet = """
try:
    pass
except AdagentsNotFoundError as e:
    msg = str(e)
except AdagentsTimeoutError as e:
    msg = str(e)
except AdagentsValidationError as e:
    msg = str(e)
except AdagentsAccessBlockedError as e:
    msg = str(e)
"""
        tree = ast.parse(snippet)
        violations = find_raw_adagents_error_violations(tree)
        assert len(violations) == 4

    def test_catches_the_isinstance_dispatch_shape(self) -> None:
        """property_discovery_service.py's shape: isinstance checks, not except clauses."""
        snippet = """
def _log_fetch_error(domain, error, stats):
    if isinstance(error, AdagentsNotFoundError):
        msg = f"{domain}: not found"
    elif isinstance(error, AdagentsValidationError):
        msg = f"{domain}: {error!s}"
"""
        tree = ast.parse(snippet)
        violations = find_raw_adagents_error_violations(tree)
        assert violations == [(5, "AdagentsValidationError")]


class TestDetectorAllowsSafeSnippets:
    def test_allows_a_call_through_describe_adagents_error(self) -> None:
        snippet = """
try:
    pass
except AdagentsValidationError as e:
    logger.error(f"detail: {e}")
    msg = describe_adagents_error(e)
"""
        tree = ast.parse(snippet)
        assert find_raw_adagents_error_violations(tree) == []

    def test_allows_a_bare_except_with_no_bound_name(self) -> None:
        snippet = """
try:
    pass
except AdagentsValidationError:
    msg = "adagents.json could not be validated"
"""
        tree = ast.parse(snippet)
        assert find_raw_adagents_error_violations(tree) == []

    def test_allows_the_isinstance_dispatch_shape_when_routed_through_the_helper(self) -> None:
        snippet = """
def _log_fetch_error(domain, error, stats):
    if isinstance(error, AdagentsNotFoundError):
        msg = f"{domain}: not found"
    elif isinstance(error, AdagentsValidationError):
        logger.error(f"{domain}: {error}")
        msg = f"{domain}: {describe_adagents_error(error)}"
"""
        tree = ast.parse(snippet)
        assert find_raw_adagents_error_violations(tree) == []

    def test_ignores_unrelated_exception_types(self) -> None:
        snippet = """
try:
    pass
except ValueError as e:
    msg = str(e)
"""
        tree = ast.parse(snippet)
        assert find_raw_adagents_error_violations(tree) == []
