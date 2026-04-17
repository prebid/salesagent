"""Guard: except blocks must not silently swallow exceptions.

Two patterns are banned in ``src/``:

1. ``except Exception: pass`` — catches everything, does nothing
2. ``except Exception: continue`` — catches everything, silently drops loop iteration

Both patterns hide bugs and data loss. If an exception should be caught, it must
be logged (at minimum ``logger.debug(..., exc_info=True)``).

Legitimate patterns that are NOT violations:
- ``except ImportError: pass`` — optional dependency guards (specific exception type)
- ``except IntegrityError:`` — race-condition upsert patterns (specific exception type)
- ``except Exception as e: logger.error(...)`` — logged exception handling
- ``except Exception as e: return {"error": str(e)}, 500`` — Flask error returns

beads: salesagent-q28c (H2), salesagent-gyn1 (H1)
GH #1078
"""

from __future__ import annotations

import ast
from pathlib import Path

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
# All 19 pre-existing violations were fixed in this PR. Allowlist is empty.
_KNOWN_VIOLATIONS: set[tuple[str, int]] = set()


def _is_broad_exception_handler(handler: ast.ExceptHandler) -> bool:
    """Check if this is a broad exception catch (Exception or bare except)."""
    if handler.type is None:
        # bare except:
        return True
    if isinstance(handler.type, ast.Name) and handler.type.id == "Exception":
        return True
    if isinstance(handler.type, ast.Attribute):
        # e.g., builtins.Exception — unlikely but possible
        attr = handler.type.attr
        if attr == "Exception":
            return True
    return False


def _handler_body_is_silent(handler: ast.ExceptHandler) -> tuple[bool, str]:
    """Check if except body is just pass or continue (silent swallow).

    Returns (is_silent, pattern_name).
    """
    # Filter out docstrings — body may start with a string constant
    stmts = [
        s
        for s in handler.body
        if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) and isinstance(s.value.value, str))
    ]

    if len(stmts) == 0:
        return True, "empty body"

    if len(stmts) == 1:
        stmt = stmts[0]
        if isinstance(stmt, ast.Pass):
            return True, "pass"
        if isinstance(stmt, ast.Continue):
            return True, "continue"

    return False, ""


def _scan_file(filepath: Path) -> list[tuple[str, int, str]]:
    """Scan a Python file for silent broad exception handlers.

    Returns list of (relative_path, line, pattern).
    """
    violations = []
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

        is_silent, pattern = _handler_body_is_silent(node)
        if is_silent:
            violations.append((rel_path, node.lineno, pattern))

    return violations


def test_no_silent_broad_except_in_src():
    """No except Exception: pass/continue without logging in src/."""
    all_violations = []

    for py_file in sorted(_SRC_DIR.rglob("*.py")):
        if "__pycache__" in str(py_file):
            continue
        all_violations.extend(_scan_file(py_file))

    # Filter out known violations
    new_violations = [
        (path, line, pattern) for path, line, pattern in all_violations if (path, line) not in _KNOWN_VIOLATIONS
    ]

    assert not new_violations, (
        f"Found {len(new_violations)} new silent broad-except violation(s) in src/.\n"
        "Every except Exception block must log (at minimum logger.debug(..., exc_info=True)).\n\n"
        + "\n".join(f"  {path}:{line} — except Exception: {pattern}" for path, line, pattern in new_violations)
        + "\n\nFix: add logging, or narrow the exception type."
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
