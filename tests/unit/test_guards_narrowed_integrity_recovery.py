"""Guard: an ``except IntegrityError`` must say WHICH constraint it recovers from.

Disease (#1721): a unique index is the only real defence against two concurrent
writers, so the loser necessarily takes an ``IntegrityError``. Recovering from it
is routine; recovering from the WRONG one is not. An unnarrowed
``except IntegrityError`` also catches foreign-key, CHECK and primary-key
violations and reports them as whatever collision the handler expected — turning
a real data defect into a plausible story about concurrency.

That is not hypothetical. With the narrowing removed from
``AccountRepository.create``, a ``ck_accounts_billing`` CHECK violation raised
during a race is reported to the caller as "Natural key already in use: account
'acc_narrow_winner'". The bad billing value disappears from the story entirely.

The rule: every ``except IntegrityError`` in ``src/`` either narrows via
``is_constraint_violation`` (``src/core/database/integrity.py``) or carries an
explicit ``# structural-guard: integrity-narrowing`` marker WITH a reason saying
why not — a handler that delegates the decision, or one that deliberately claims
no cause at all. The marker is per-site rather than a central allowlist so the
justification lives where the next reader needs it, and so no shared list has to
grow to admit a legitimate case. It uses this repo's own ``# structural-guard:``
comment convention rather than ``# noqa:``, which ruff parses as a rule-code list
and warns about.
"""

import ast

from tests.unit._architecture_helpers import (
    REPO_ROOT,
    handles_integrity_error,
    parse_module,
    src_python_files,
    structural_guard_marker_re,
)

GUARD_NAME = "integrity-narrowing"
MARKER = f"structural-guard: {GUARD_NAME}"
NARROWING_CALL = "is_constraint_violation"

#: The marker must be followed by a reason — a bare opt-out is not a justification.
_MARKER_WITH_REASON = structural_guard_marker_re(GUARD_NAME)


def _narrows(handler: ast.ExceptHandler) -> bool:
    return any(
        isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", None)) == NARROWING_CALL
        for node in ast.walk(handler)
    )


def _clause_text(handler: ast.ExceptHandler, lines: list[str]) -> str:
    """The source of the ``except ...:`` clause itself, however many lines it spans.

    The formatter is free to wrap a long clause across lines — it does exactly that
    to ``except (IntegrityError):  # <marker>`` — which would leave the marker off
    the handler's own ``lineno``. Scanning the whole clause, up to the first body
    statement, keeps the guard from depending on how the line happens to be wrapped.
    """
    first_body_line = handler.body[0].lineno if handler.body else handler.lineno + 1
    return "\n".join(lines[handler.lineno - 1 : max(first_body_line - 1, handler.lineno)])


def find_unnarrowed_integrity_handlers(tree: ast.Module, source: str) -> list[int]:
    """Return line numbers of IntegrityError handlers that neither narrow nor justify."""
    lines = source.splitlines()
    violations: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler) or not handles_integrity_error(node):
            continue
        if _narrows(node):
            continue
        if _MARKER_WITH_REASON.search(_clause_text(node, lines)):
            continue
        violations.append(node.lineno)
    return violations


def test_every_integrity_recovery_names_its_constraint():
    violations: list[str] = []
    for path in src_python_files(REPO_ROOT):
        source = path.read_text(encoding="utf-8")
        if "IntegrityError" not in source:
            continue
        for lineno in find_unnarrowed_integrity_handlers(parse_module(path), source):
            violations.append(f"{path.relative_to(REPO_ROOT).as_posix()}:{lineno}")
    assert not violations, (
        "Unnarrowed `except IntegrityError` — this handler also catches FK, CHECK and PK "
        "violations and will report them as whatever collision it expects (#1721). Narrow it "
        "with is_constraint_violation(exc, <constraint>), or mark the site "
        f"`# {MARKER} - <why>` if it delegates the decision or deliberately claims no cause:\n  "
        + "\n  ".join(violations)
    )


def _find(source: str) -> list[int]:
    return find_unnarrowed_integrity_handlers(ast.parse(source), source)


def test_guard_catches_unnarrowed_handlers():
    """Positive meta-tests: the shapes that misattribute a cause."""
    bare = "def f(s):\n    try:\n        s.flush()\n    except IntegrityError:\n        recover()\n"
    assert _find(bare), "a bare unnarrowed handler must be flagged"

    aliased = "def f(s):\n    try:\n        s.flush()\n    except exc.IntegrityError:\n        recover()\n"
    assert _find(aliased), "a dotted/aliased IntegrityError must be flagged too"

    # The would-be-missed case for a line-oriented scanner: IntegrityError is not
    # the only exception on the clause, so a regex anchored on `except IntegrityError:`
    # slips past it — while the tuple still catches every integrity violation.
    in_tuple = (
        "def f(s):\n    try:\n        s.flush()\n    except (OperationalError, IntegrityError):\n        recover()\n"
    )
    assert _find(in_tuple), "IntegrityError inside an exception tuple must be flagged"

    # Wrapped clause: the formatter moves a long `except ...:` onto several lines,
    # putting the marker below the handler's own lineno. A one-line check misses it
    # and reports a justified site as a violation.
    wrapped = (
        "def f(s):\n"
        "    try:\n"
        "        s.flush()\n"
        "    except (\n"
        "        IntegrityError\n"
        "    ):  # structural-guard: integrity-narrowing - delegated downstream\n"
        "        recover()\n"
    )
    assert _find(wrapped) == [], "a marker on a wrapped except clause must still count"

    # A bare marker with no reason is an opt-out, not a justification.
    unjustified = (
        "def f(s):\n"
        "    try:\n"
        "        s.flush()\n"
        "    except IntegrityError:  # structural-guard: integrity-narrowing\n"
        "        recover()\n"
    )
    assert _find(unjustified), "the marker must carry a reason"


def test_guard_accepts_narrowed_and_justified_handlers():
    """Negative meta-tests: neither a narrowed handler nor a justified one is a violation."""
    narrowed = (
        "def f(s):\n"
        "    try:\n"
        "        s.flush()\n"
        "    except IntegrityError as exc:\n"
        "        if not is_constraint_violation(exc, INDEX):\n"
        "            raise\n"
        "        recover()\n"
    )
    assert _find(narrowed) == []

    justified = (
        "def f(s):\n"
        "    try:\n"
        "        s.flush()\n"
        "    except IntegrityError:  # structural-guard: integrity-narrowing - best effort, no cause claimed\n"
        "        logger.info('could not cache')\n"
    )
    assert _find(justified) == []

    unrelated = "def f(s):\n    try:\n        s.flush()\n    except ValueError:\n        recover()\n"
    assert _find(unrelated) == []
