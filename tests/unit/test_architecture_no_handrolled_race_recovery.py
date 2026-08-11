"""Guard: nobody hand-rolls the SAVEPOINT + IntegrityError race recovery.

``src/core/database/integrity.py::resolve_or_write`` exists because the recovery it
performs is load-bearing in a way that is easy to get subtly wrong. Its docstring
spends ~20 lines on two structural rules — the SAVEPOINT must CONTAIN the flush, and
the ``except`` must sit OUTSIDE the ``with`` — and on why the obvious alternatives
(a decorator, a bare context manager) cannot work. All of that is there so the next
caller does not re-derive it by hand.

``AccountRepository.create()`` re-derived it by hand anyway: pre-check ->
``begin_nested()`` -> add -> flush -> catch ``IntegrityError`` -> re-run the pre-check
-> re-raise. Same shape, written out again, in the same branch that generalized the
helper (#1721 review F6). Nothing was wrong with it — that is the point. A hand-rolled
copy that happens to be correct today is the one that silently diverges tomorrow, and
a reviewer has no cheap way to notice.

A function is a violation when it contains BOTH a ``begin_nested()`` call and an
``except IntegrityError`` handler. That pair IS the pattern; either alone is fine
(a savepoint for other reasons, or catching IntegrityError without one).

ALLOWLIST is shrink-only. Every entry cites a GitHub issue.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"

#: The one sanctioned implementation — the helper itself is allowed to be the pattern.
SANCTIONED: frozenset[tuple[str, str]] = frozenset(
    {
        ("core/database/integrity.py", "resolve_or_write"),
    }
)

#: Known hand-rolled copies awaiting migration. SHRINK ONLY — never add.
ALLOWLIST: dict[tuple[str, str], str] = {}


def _handles_integrity_error(node: ast.AST) -> bool:
    """True when *node* contains an ``except IntegrityError`` handler."""
    for sub in ast.walk(node):
        if not isinstance(sub, ast.ExceptHandler) or sub.type is None:
            continue
        names = [n.id for n in ast.walk(sub.type) if isinstance(n, ast.Name)]
        names += [a.attr for a in ast.walk(sub.type) if isinstance(a, ast.Attribute)]
        if "IntegrityError" in names:
            return True
    return False


def _opens_savepoint(node: ast.AST) -> bool:
    """True when *node* calls ``begin_nested()``."""
    return any(
        isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr == "begin_nested"
        for sub in ast.walk(node)
    )


def collect_handrolled_recoveries() -> dict[tuple[str, str], int]:
    """(relative_path, function_name) -> lineno, for every hand-rolled recovery."""
    found: dict[tuple[str, str], int] = {}
    for path in sorted(SRC.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(), filename=str(path))
        except SyntaxError:
            continue
        rel = str(path.relative_to(SRC))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if not (_opens_savepoint(node) and _handles_integrity_error(node)):
                continue
            key = (rel, node.name)
            if key in SANCTIONED:
                continue
            found[key] = node.lineno
    return found


@pytest.mark.arch_guard
def test_no_handrolled_race_recovery() -> None:
    """Race-safe writes go through resolve_or_write, not a second copy of its shape."""
    found = collect_handrolled_recoveries()
    unexpected = {key: lineno for key, lineno in found.items() if key not in ALLOWLIST}
    assert not unexpected, (
        "Hand-rolled SAVEPOINT + IntegrityError recovery found. Use "
        "src/core/database/integrity.py::resolve_or_write — its docstring explains which "
        "parts of the shape are load-bearing.\n"
        + "\n".join(f"  src/{path}:{lineno} in {func}()" for (path, func), lineno in sorted(unexpected.items()))
    )


@pytest.mark.arch_guard
def test_allowlist_has_no_stale_entries() -> None:
    """A migrated call site must be removed from ALLOWLIST."""
    found = collect_handrolled_recoveries()
    stale = sorted(key for key in ALLOWLIST if key not in found)
    assert not stale, f"ALLOWLIST entries no longer hand-rolled — delete them: {stale}"


@pytest.mark.arch_guard
def test_sanctioned_implementation_still_exists() -> None:
    """If resolve_or_write stops being the pattern, this guard is pinning nothing."""
    tree = ast.parse((SRC / "core/database/integrity.py").read_text())
    node = next(
        (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "resolve_or_write"),
        None,
    )
    assert node is not None, "resolve_or_write no longer exists — this guard needs rewriting"
    assert _opens_savepoint(node) and _handles_integrity_error(node), (
        "resolve_or_write no longer implements the SAVEPOINT + IntegrityError shape; "
        "the guard's SANCTIONED exemption is now misleading"
    )


class TestDetectorMetaTests:
    """Both halves of the pair must be required, and neither alone may fire."""

    @staticmethod
    def _is_violation(source: str) -> bool:
        node = next(n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.FunctionDef))
        return _opens_savepoint(node) and _handles_integrity_error(node)

    @pytest.mark.arch_guard
    def test_positive_savepoint_plus_integrity_handler(self) -> None:
        assert self._is_violation(
            "def create(self, row):\n"
            "    try:\n"
            "        with self._session.begin_nested():\n"
            "            self._session.add(row)\n"
            "            self._session.flush()\n"
            "    except IntegrityError:\n"
            "        raise\n"
        )

    @pytest.mark.arch_guard
    def test_negative_savepoint_without_integrity_handling(self) -> None:
        """A savepoint for unrelated reasons is not this pattern."""
        assert not self._is_violation(
            "def bulk(self, rows):\n    with self._session.begin_nested():\n        self._session.add_all(rows)\n"
        )

    @pytest.mark.arch_guard
    def test_negative_integrity_handling_without_a_savepoint(self) -> None:
        """Catching IntegrityError without a savepoint is a different (and lesser) thing."""
        assert not self._is_violation(
            "def create(self, row):\n"
            "    try:\n"
            "        self._session.add(row)\n"
            "    except IntegrityError:\n"
            "        raise ValueError('taken')\n"
        )

    @pytest.mark.arch_guard
    def test_would_be_missed_qualified_exception_name(self) -> None:
        """``except sa_exc.IntegrityError`` must count — matching bare Names alone would miss it."""
        assert self._is_violation(
            "def create(self, row):\n"
            "    try:\n"
            "        with self._session.begin_nested():\n"
            "            self._session.flush()\n"
            "    except sa_exc.IntegrityError:\n"
            "        raise\n"
        )

    @pytest.mark.arch_guard
    def test_would_be_missed_tuple_of_exceptions(self) -> None:
        """``except (IntegrityError, OperationalError)`` must count too."""
        assert self._is_violation(
            "def create(self, row):\n"
            "    try:\n"
            "        with self._session.begin_nested():\n"
            "            self._session.flush()\n"
            "    except (IntegrityError, OperationalError):\n"
            "        raise\n"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
