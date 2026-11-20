"""Guard: the number of blanket ``except`` clauses in the admin blueprints may only shrink.

The admin app registers one error handler for every exception a route lets escape
(``src/admin/utils/operator_errors.py::handle_unexpected_error``). It logs the
exception under a reference and answers with fixed text, so a route has no reason
to catch ``Exception`` in order to build an error response, and every such block
is a place where exception text can reach the operator. Counting the clauses
themselves catches every spelling, however the exception is named or wherever its
text is sent.

When you remove blanket handlers, lower ``_BASELINE`` to the new count in the same
change. Never raise it: catch the specific exception you expect, or let the
central handler answer.
"""

import ast

from tests.unit._architecture_helpers import REPO_ROOT

_BLUEPRINTS = REPO_ROOT / "src" / "admin" / "blueprints"
_BLANKET = {"Exception", "BaseException"}
_BASELINE = 82


def _is_blanket(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return True
    types = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return any(isinstance(t, ast.Name) and t.id in _BLANKET for t in types)


def _count(source: str) -> int:
    return sum(1 for node in ast.walk(ast.parse(source)) if isinstance(node, ast.ExceptHandler) and _is_blanket(node))


def _current() -> int:
    return sum(_count(path.read_text(encoding="utf-8")) for path in sorted(_BLUEPRINTS.rglob("*.py")))


def test_blanket_except_count_does_not_grow():
    current = _current()
    assert current <= _BASELINE, (
        f"{current} blanket except clauses under src/admin/blueprints, baseline {_BASELINE}. "
        "Catch the specific exception, or let the admin app's error handler answer."
    )


def test_baseline_is_current():
    current = _current()
    assert current >= _BASELINE, f"Blanket except clauses went down to {current}; set _BASELINE = {current}."


class TestCounterMetaCases:
    """What the counter treats as blanket."""

    def test_counts_except_exception(self):
        assert _count("try:\n    x()\nexcept Exception as e:\n    pass\n") == 1

    def test_counts_bare_except_and_tuples(self):
        src = "try:\n    x()\nexcept:\n    pass\ntry:\n    y()\nexcept (ValueError, Exception):\n    pass\n"
        assert _count(src) == 2

    def test_ignores_specific_exceptions(self):
        assert _count("try:\n    x()\nexcept ValueError as e:\n    pass\n") == 0
