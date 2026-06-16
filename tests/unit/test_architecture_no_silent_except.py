"""Guard: except blocks must not silently swallow exceptions.

Three patterns are banned in ``src/``:

1. ``except Exception: pass`` — silently swallows
2. ``except Exception: continue`` — silently swallows in a loop
3. ``except Exception: print(...) | console.print(...) | traceback.print_exc()`` —
   uses ad-hoc print/Rich console output instead of structured logging. Whether
   the handler ends with a terminator (``continue``/``return``/etc.) or falls
   through, the failure never reaches log aggregation or alerting.

All three hide bugs and data loss. To handle a broad exception, log it via
``logger.exception(...)`` (which auto-attaches the traceback) and either re-raise
when the failure must propagate or document why silent-skip is correct. Or
narrow the exception type to a specific failure mode.

Legitimate patterns (NOT violations):

- ``except ImportError: pass`` — optional-dependency guards (specific type)
- ``except IntegrityError:`` — race-condition upsert patterns (specific type)
- ``except Exception: logger.error(...); return ...`` — structured logging + boundary return
- ``except Exception: logger.exception(...); raise`` — log and re-raise

beads: salesagent-q28c (H2), salesagent-gyn1 (H1)
GH #1078
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_SRC_DIR = Path(__file__).resolve().parents[2] / "src"

# Exception types that are acceptable to catch with pass/continue
# (they indicate expected, specific failure modes, not catch-all swallowing)
_ACCEPTABLE_EXCEPTION_TYPES = frozenset(
    {
        "ImportError",
        "ModuleNotFoundError",
        "IntegrityError",
        "KeyboardInterrupt",
        "SystemExit",
        "StopIteration",
        "StopAsyncIteration",
        "GeneratorExit",
    }
)

# Known violations — allowlist must only shrink, never grow.
# Format: (relative_path_from_src, line_number)
# Both pass/continue and print-swallow patterns share this allowlist; their
# matches are mutually exclusive in practice, so the same (path, line) is
# never reported twice.
_KNOWN_VIOLATIONS: set[tuple[str, int]] = set()


def _is_broad_exception_handler(handler: ast.ExceptHandler) -> bool:
    """True if the handler catches Exception or is a bare except.

    Also handles tuple-of-types if ``Exception`` (or ``builtins.Exception``)
    appears in the tuple — e.g., ``except (Exception, KeyError):`` is broad.
    """
    if handler.type is None:
        return True
    if isinstance(handler.type, ast.Name) and handler.type.id == "Exception":
        return True
    if isinstance(handler.type, ast.Attribute) and handler.type.attr == "Exception":
        return True
    if isinstance(handler.type, ast.Tuple):
        for elt in handler.type.elts:
            if isinstance(elt, ast.Name) and elt.id == "Exception":
                return True
            if isinstance(elt, ast.Attribute) and elt.attr == "Exception":
                return True
    return False


def _strip_docstrings(body: list[ast.stmt]) -> list[ast.stmt]:
    """Return ``body`` with standalone string-constant statements removed.

    These are usually leading docstrings or in-code multi-line "comment" strings.
    Stripping them lets the predicates focus on real control flow.
    """
    return [
        s
        for s in body
        if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) and isinstance(s.value.value, str))
    ]


def _handler_body_is_silent(handler: ast.ExceptHandler) -> tuple[bool, str]:
    """Empty body / single ``pass`` / single ``continue`` (existing pattern).

    Returns (is_silent, pattern_name).
    """
    stmts = _strip_docstrings(handler.body)

    if not stmts:
        return True, "empty body"

    if len(stmts) == 1:
        stmt = stmts[0]
        if isinstance(stmt, ast.Pass):
            return True, "pass"
        if isinstance(stmt, ast.Continue):
            return True, "continue"

    return False, ""


def _classify_print_like(stmt: ast.stmt) -> str | None:
    """Return a label describing the print-like call, or None if ``stmt`` isn't one.

    Matches:

    - ``print(...)`` (the builtin)
    - ``console.print(...)`` (Rich console — bound to a Name ``console``)
    - ``<any>.print_exc(...)`` / ``<any>.print_stack(...)`` (alias-tolerant for traceback)

    Logger calls (``logger.error``, ``logger.exception``, etc.) intentionally do
    NOT match — they reach structured logging and are not silent swallows.
    """
    if not isinstance(stmt, ast.Expr) or not isinstance(stmt.value, ast.Call):
        return None
    func = stmt.value.func
    if isinstance(func, ast.Name) and func.id == "print":
        return "print"
    if isinstance(func, ast.Attribute):
        if isinstance(func.value, ast.Name) and func.value.id == "console" and func.attr == "print":
            return "console.print"
        if func.attr in ("print_exc", "print_stack"):
            return func.attr
    return None


def _has_raise_excluding_closures(body: list[ast.stmt]) -> bool:
    """Walk ``body`` looking for ``ast.Raise`` but skip into nested closures.

    An ``ast.Raise`` *defined inside* a nested function/lambda is just code that
    *would* raise if the closure ran — it doesn't make the enclosing handler
    propagate. Skipping ``FunctionDef``/``AsyncFunctionDef``/``Lambda`` avoids
    false negatives where a closure's raise masks the swallowing handler.
    """
    skip = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
    stack: list[ast.AST] = list(body)
    while stack:
        node = stack.pop()
        if isinstance(node, ast.Raise):
            return True
        if isinstance(node, skip):
            continue
        stack.extend(ast.iter_child_nodes(node))
    return False


def _handler_body_is_print_swallow(handler: ast.ExceptHandler) -> tuple[bool, str]:
    """Body uses print-family logging without re-raising.

    Catches handlers like ``except Exception: console.print(...)`` regardless of
    whether they end with a terminator (``continue``/``return``/etc.) or fall
    through. Logger calls are intentionally NOT flagged — they reach structured
    logging.

    Returns (is_match, pattern_name).
    """
    stmts = _strip_docstrings(handler.body)
    if not stmts:
        return False, ""
    if _has_raise_excluding_closures(stmts):
        return False, ""

    for s in stmts:
        label = _classify_print_like(s)
        if label is not None:
            return True, f"{label} + swallow"
    return False, ""


def _scan_file(filepath: Path) -> list[tuple[str, int, str]]:
    """Scan a Python file for silent broad-exception handlers.

    Returns list of (relative_path, line, pattern). A handler matches at most
    one predicate; the predicates are mutually exclusive in practice (single
    ``pass``/``continue`` doesn't contain a print-like call).
    """
    violations: list[tuple[str, int, str]] = []
    try:
        source = filepath.read_text()
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError:
        return violations

    rel_path = str(filepath.relative_to(_SRC_DIR))

    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if not _is_broad_exception_handler(node):
            continue

        for predicate in (_handler_body_is_silent, _handler_body_is_print_swallow):
            matches, pattern = predicate(node)
            if matches:
                violations.append((rel_path, node.lineno, pattern))
                break

    return violations


def test_no_silent_broad_except_in_src():
    """No silent broad-except (pass/continue/print-swallow) in src/."""
    all_violations = []

    for py_file in sorted(_SRC_DIR.rglob("*.py")):
        if "__pycache__" in str(py_file):
            continue
        all_violations.extend(_scan_file(py_file))

    new_violations = [
        (path, line, pattern) for path, line, pattern in all_violations if (path, line) not in _KNOWN_VIOLATIONS
    ]

    assert not new_violations, (
        f"Found {len(new_violations)} new silent broad-except violation(s) in src/.\n"
        "Broad except handlers must log via logger.* (logger.exception attaches the traceback)\n"
        "and either re-raise or document why silent-skip is correct.\n\n"
        + "\n".join(f"  {path}:{line} — except Exception: {pattern}" for path, line, pattern in new_violations)
        + "\n\nFix: switch print/console.print to logger.* or narrow the exception type."
    )


def test_known_violations_not_stale():
    """Every allowlisted violation must still exist in the source."""
    all_violations = []
    for py_file in sorted(_SRC_DIR.rglob("*.py")):
        if "__pycache__" in str(py_file):
            continue
        all_violations.extend(_scan_file(py_file))

    actual = {(path, line) for path, line, _ in all_violations}
    stale = _KNOWN_VIOLATIONS - actual

    assert not stale, (
        f"Found {len(stale)} stale allowlist entry(ies) — these violations were fixed.\n"
        "Remove them from _KNOWN_VIOLATIONS:\n\n" + "\n".join(f"  ({path!r}, {line})," for path, line in sorted(stale))
    )


# === Predicate self-tests ===
#
# These exercise the predicate logic against constructed ASTs. The main scan
# tests above exercise the predicates against real source.


def _make_handler(*, type_code: str | None, body_code: str) -> ast.ExceptHandler:
    """Construct an ``ast.ExceptHandler`` with a given ``except`` type and body.

    ``type_code`` is the source for the exception type expression
    (e.g. ``"Exception"``, ``"(Exception, KeyError)"``); ``None`` means a bare
    ``except:``. ``body_code`` is the source for the handler body.
    """
    type_expr = ast.parse(type_code, mode="eval").body if type_code is not None else None
    body = ast.parse(body_code).body
    return ast.ExceptHandler(type=type_expr, name=None, body=body)


@pytest.mark.parametrize(
    ("type_code", "expected"),
    [
        ("Exception", True),
        (None, True),  # bare except:
        ("builtins.Exception", True),  # qualified
        ("(Exception, KeyError)", True),  # tuple containing Exception
        ("(KeyError, ValueError)", False),  # tuple without Exception
        ("ValueError", False),
        ("ImportError", False),
    ],
)
def test_is_broad_exception_handler(type_code, expected):
    handler = _make_handler(type_code=type_code, body_code="pass")
    assert _is_broad_exception_handler(handler) is expected


@pytest.mark.parametrize(
    ("body_code", "expected"),
    [
        ("raise", True),
        ("raise ValueError", True),
        ("if True:\n    raise", True),
        ("for x in []:\n    raise", True),
        ("with open('f'):\n    raise", True),
        ("def helper():\n    raise ValueError", False),  # closure — not a real raise of THIS handler
        ("def helper():\n    raise\nraise", True),  # raise outside the closure
        ("logger.error('x')", False),
        ("pass", False),
    ],
)
def test_has_raise_excluding_closures(body_code, expected):
    body = ast.parse(body_code).body
    assert _has_raise_excluding_closures(body) is expected


@pytest.mark.parametrize(
    "body_code",
    [
        # Site shapes (pre-fix)
        "print('x')\ncontinue",  # default_products.py:226
        "console.print('x')",  # context_manager.py:743 — fall-off
        "console.print('x')\nimport traceback\ntraceback.print_exc()",  # context_manager.py:765
        # Other terminators
        "print('x')\npass",
        "print('x')\nbreak",
        "print('x')\nreturn",
        # Aliased traceback (e.g., import traceback as tb)
        "tb.print_exc()",
        # Closure that raises must NOT mask the swallow
        "def helper():\n    raise ValueError\nconsole.print('x')",
    ],
)
def test_print_swallow_predicate_positive_cases(body_code):
    handler = _make_handler(type_code="Exception", body_code=body_code)
    matches, _ = _handler_body_is_print_swallow(handler)
    assert matches is True


@pytest.mark.parametrize(
    "body_code",
    [
        # Has raise — must not match
        "print('x')\nraise",
        "console.print('x')\nraise CustomError",
        "logger.exception('x')\nraise",
        # logger.* is intentionally out of narrow predicate scope
        "logger.error('x')\nreturn",
        "logger.warning('x')\ncontinue",
        # Existing predicate's domain — single pass/continue (no print-like call)
        "pass",
        "continue",
        # No print-like call at all
        "x = 1",
        "return None",
    ],
)
def test_print_swallow_predicate_negative_cases(body_code):
    handler = _make_handler(type_code="Exception", body_code=body_code)
    matches, _ = _handler_body_is_print_swallow(handler)
    assert matches is False
