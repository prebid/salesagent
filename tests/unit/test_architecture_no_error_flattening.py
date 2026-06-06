"""Guard: catch-all handlers must not flatten typed AdCPErrors.

A broad ``except Exception:`` (or bare ``except:``) that raises a freshly
constructed ``AdCP*Error`` re-wraps any typed ``AdCPError`` the ``try`` body
raised, collapsing its precise wire code + recovery into a generic one. The
buyer loses the specific contract — e.g. a deliberate ``AdCPValidationError``
(``VALIDATION_ERROR`` / correctable) gets re-emitted as ``AdCPAdapterError``
(``ADAPTER_ERROR`` / terminal).

Correct idiom — let typed errors propagate, wrap only the unexpected::

    try:
        ...
    except AdCPError:
        raise
    except Exception as e:
        raise AdCPAdapterError(...) from e

The inline variant ``except Exception as e: if isinstance(e, AdCPError): raise``
is ALSO a violation here: the protection is real but it must be a sibling
``except AdCPError:`` handler so the idiom is uniform and machine-checkable.

Scope: ``src/core/`` and ``src/adapters/`` — the layers that emit AdCP wire
errors. The shared ``_ast_helpers.SCAN_DIRS`` (``[src/core/tools, src/adapters]``)
is deliberately NOT reused: it would miss ``src/core/helpers/`` (and the rest of
``src/core/``), where a live flatten bug was found.

Allowlist is empty: every pre-existing flatten site was converted to the
except-AdCPError-first idiom. A new catch-all-flatten fails the build.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_DIRS = [REPO_ROOT / "src" / "core", REPO_ROOT / "src" / "adapters"]

# Known violations — allowlist must only shrink, never grow.
# Keyed by (relative_path_from_repo_root, enclosing_function_name) so it survives
# line-number shifts. Empty: all pre-existing flatten sites were fixed.
KNOWN_VIOLATIONS: set[tuple[str, str]] = set()


def _is_broad_handler(handler: ast.ExceptHandler) -> bool:
    """True for ``except Exception`` (or ``builtins.Exception``) or a bare ``except:``."""
    if handler.type is None:
        return True
    if isinstance(handler.type, ast.Name) and handler.type.id == "Exception":
        return True
    if isinstance(handler.type, ast.Attribute) and handler.type.attr == "Exception":
        return True
    return False


def _catches_adcp_base(handler: ast.ExceptHandler) -> bool:
    """True if the handler catches the ``AdCPError`` base class (alone or in a tuple)."""
    target = handler.type
    candidates: list[ast.expr] = (
        list(target.elts) if isinstance(target, ast.Tuple) else ([] if target is None else [target])
    )
    for node in candidates:
        if isinstance(node, ast.Name) and node.id == "AdCPError":
            return True
        if isinstance(node, ast.Attribute) and node.attr == "AdCPError":
            return True
    return False


def _has_bare_reraise(handler: ast.ExceptHandler) -> bool:
    """True if the handler body contains a bare ``raise`` (re-raises the active exception)."""
    return any(isinstance(node, ast.Raise) and node.exc is None for node in ast.walk(handler))


def _raises_fresh_adcp_error(handler: ast.ExceptHandler) -> str | None:
    """Return the name of a freshly-raised ``AdCP*Error`` in the handler body, else None.

    ``raise AdCPFoo(...)`` and ``raise AdCPFoo`` both count; a bare ``raise`` does not.
    """
    for node in ast.walk(handler):
        if not isinstance(node, ast.Raise) or node.exc is None:
            continue
        raised = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
        name = raised.id if isinstance(raised, ast.Name) else raised.attr if isinstance(raised, ast.Attribute) else None
        if name and name.startswith("AdCP") and name.endswith("Error"):
            return name
    return None


def _check_try(node: ast.Try) -> tuple[int, str] | None:
    """If this ``try`` flattens a typed error, return (handler_lineno, raised_class), else None."""
    broad_index = next((i for i, h in enumerate(node.handlers) if _is_broad_handler(h)), None)
    if broad_index is None:
        return None
    broad = node.handlers[broad_index]
    raised = _raises_fresh_adcp_error(broad)
    if raised is None:
        return None
    # Exempt when a PRECEDING sibling catches AdCPError and bare-re-raises (the correct idiom).
    if any(_catches_adcp_base(h) and _has_bare_reraise(h) for h in node.handlers[:broad_index]):
        return None
    return broad.lineno, raised


def _scan_module(tree: ast.Module, rel_path: str) -> list[tuple[str, str, int, str]]:
    """Yield (rel_path, enclosing_function, handler_lineno, raised_class) for each violation."""
    found: list[tuple[str, str, int, str]] = []

    def visit(node: ast.AST, func_name: str) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func_name = node.name
        if isinstance(node, ast.Try):
            hit = _check_try(node)
            if hit is not None:
                found.append((rel_path, func_name, hit[0], hit[1]))
        for child in ast.iter_child_nodes(node):
            visit(child, func_name)

    visit(tree, "<module>")
    return found


def _find_flatten_sites() -> list[tuple[str, str, int, str]]:
    """All catch-all-flatten sites across the scan dirs."""
    sites: list[tuple[str, str, int, str]] = []
    for scan_dir in SCAN_DIRS:
        for py_file in sorted(scan_dir.rglob("*.py")):
            if "__pycache__" in str(py_file):
                continue
            try:
                tree = ast.parse(py_file.read_text(), filename=str(py_file))
            except SyntaxError:
                continue
            sites.extend(_scan_module(tree, str(py_file.relative_to(REPO_ROOT))))
    return sites


def test_no_error_flattening():
    """No broad handler re-wraps a typed AdCPError into a fresh AdCP*Error."""
    new_violations = [
        f"  {rel}:{lineno} in {func}() — except Exception → raise {cls}"
        for rel, func, lineno, cls in _find_flatten_sites()
        if (rel, func) not in KNOWN_VIOLATIONS
    ]
    assert not new_violations, (
        f"Found {len(new_violations)} catch-all-flatten violation(s) in src/core + src/adapters.\n"
        "A broad `except Exception` that raises a fresh AdCP*Error flattens any typed AdCPError "
        "the try body raised. Add a preceding `except AdCPError: raise` sibling handler:\n\n"
        + "\n".join(new_violations)
    )


def test_known_violations_not_stale():
    """Every allowlisted (file, function) must still contain a flatten site."""
    actual = {(rel, func) for rel, func, _, _ in _find_flatten_sites()}
    stale = KNOWN_VIOLATIONS - actual
    assert not stale, (
        f"Found {len(stale)} stale allowlist entry(ies) — these flatten sites were fixed.\n"
        "Remove them from KNOWN_VIOLATIONS:\n\n" + "\n".join(f"  {rel} :: {func}" for rel, func in sorted(stale))
    )
