"""Guard: concrete BDD step text must align with the fields asserted in code.

These checks target a recurring class of false-positive BDD steps where the
step text promises validation for one field, but the body inspects a different
field instead.
"""

from __future__ import annotations

import ast
import importlib.util
import re
import sys
from pathlib import Path

import pytest

from tests.unit._architecture_helpers import iter_call_expressions

_BDD_STEPS_DIR = Path(__file__).resolve().parents[1] / "bdd" / "steps"
_INSPECT_SCRIPT = Path(__file__).resolve().parents[2] / ".claude" / "scripts" / "inspect_bdd_steps.py"


def _load_extract_bdd_steps():
    """Load the shared BDD inspection script and return extract_bdd_steps()."""
    spec = importlib.util.spec_from_file_location("inspect_bdd_steps", _INSPECT_SCRIPT)
    assert spec is not None and spec.loader is not None, f"Could not load {_INSPECT_SCRIPT}"
    module = importlib.util.module_from_spec(spec)
    sys.modules["inspect_bdd_steps"] = module
    spec.loader.exec_module(module)
    return module.extract_bdd_steps


def _iter_then_steps() -> list[tuple[Path, ast.FunctionDef | ast.AsyncFunctionDef, str]]:
    """Yield Then step nodes plus their extracted step text."""
    extract_bdd_steps = _load_extract_bdd_steps()

    text_by_location: dict[tuple[str, int], str] = {}
    for step in extract_bdd_steps(_BDD_STEPS_DIR):
        if step.step_type == "then":
            text_by_location[(str(Path(step.file_path).resolve()), step.line_number)] = step.step_text

    results = []
    for py_file in sorted(_BDD_STEPS_DIR.rglob("*.py")):
        if py_file.name.startswith("_"):
            continue
        source = py_file.read_text()
        tree = ast.parse(source, filename=str(py_file))
        resolved = str(py_file.resolve())
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            step_text = text_by_location.get((resolved, node.lineno))
            if step_text is not None:
                results.append((py_file, node, step_text))
    return results


def _field_names_referenced(func: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Collect likely field names referenced in a function body."""
    names: set[str] = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            names.add(node.value)
    return names


def _is_account_id_load(node: ast.expr) -> bool:
    return isinstance(node, ast.Name) and node.id == "account_id" and isinstance(node.ctx, ast.Load)


def _call_carries_account_id(call: ast.Call) -> bool:
    """True if ``account_id`` (a ``Load`` Name) is a positional/keyword ARG of this call."""
    for arg in call.args:
        value = arg.value if isinstance(arg, ast.Starred) else arg
        if _is_account_id_load(value):
            return True
    return any(_is_account_id_load(kw.value) for kw in call.keywords)


def _account_id_graded_by_assertion(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True iff ``account_id`` is routed into a lookup whose RESULT is CONSUMED by an assertion.

    Recognizes helper-mediated inspection where the by-id lookup performs the ref-echo grade —
    ``acct = _wire_account(ctx, account_id); assert acct["status"] == ...`` — including a
    transitive chain (``acct`` → ``agents`` → ``actual`` → ``assert``). A lookup whose result is
    NEVER consumed by an assertion (``acct = _wire_account(ctx, account_id)`` followed only by
    ``assert ctx["other"] == 1``) does NOT satisfy the exemption — that was the hole finding 9
    named: the earlier "passed to any non-discarded call" form fired on a lookup that graded
    nothing. Two shapes count:

    * DIRECT — a call carrying ``account_id`` appears inside an ``assert`` TEST
      (``assert account_id in _wire_account_ids(ctx, account_id)``);
    * TAINT — a name bound from an ``account_id``-carrying call, propagated through subsequent
      assignments (``agents = acct.get(...)``, ``actual = agents[idx]["url"]``), is READ in some
      ``assert`` TEST.
    """
    # DIRECT: a call carrying account_id inside an assert test.
    for stmt in ast.walk(func):
        if isinstance(stmt, ast.Assert):
            for call in iter_call_expressions(stmt.test):
                if _call_carries_account_id(call):
                    return True

    # TAINT: bind names from account_id-carrying calls, propagate through assignments to a
    # fixpoint, then require an assert TEST to read a tainted name.
    assigns: list[tuple[list[str], ast.expr]] = []
    for stmt in ast.walk(func):
        if isinstance(stmt, ast.Assign) and stmt.value is not None:
            names = [t.id for t in stmt.targets if isinstance(t, ast.Name)]
            if names:
                assigns.append((names, stmt.value))
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.value is not None:
            assigns.append(([stmt.target.id], stmt.value))

    def _value_has_account_id_call(value: ast.expr) -> bool:
        return any(_call_carries_account_id(call) for call in iter_call_expressions(value))

    def _value_reads(value: ast.expr, names: set[str]) -> bool:
        return any(isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id in names for n in ast.walk(value))

    tainted: set[str] = set()
    for names, value in assigns:
        if _value_has_account_id_call(value):
            tainted.update(names)
    changed = True
    while changed:
        changed = False
        for names, value in assigns:
            if all(n in tainted for n in names):
                continue
            if _value_reads(value, tainted):
                tainted.update(names)
                changed = True

    for stmt in ast.walk(func):
        if isinstance(stmt, ast.Assert):
            if any(
                isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id in tainted for n in ast.walk(stmt.test)
            ):
                return True
    return False


class TestBddStepTextAlignment:
    """Structural guard: literal field names in Then steps must be referenced in code."""

    @pytest.mark.arch_guard
    def test_account_id_steps_reference_account_id(self):
        """Then steps mentioning account_id must inspect account_id somewhere in the body.

        "Inspect" is satisfied by a literal ``"account_id"`` string / ``.account_id``
        attribute (the by-key read) OR by the ``account_id`` step parameter being routed into a
        lookup whose RESULT is CONSUMED by an assertion (``_account_id_graded_by_assertion``) —
        e.g. ``acct = _wire_account(ctx, account_id); assert acct["status"] == ...``, whose by-id
        lookup performs the ref-echo grade. A lookup whose result is never asserted on
        (``acct = _wire_account(ctx, account_id)`` with only ``assert ctx["other"] == 1``) is
        NOT exempt — the earlier "passed to any non-discarded call" form let that vacuous shape
        through (#1329 finding 9). A step that mentions account_id in its text but neither reads
        it by key nor grades a lookup of it is still flagged.
        """
        violations = []
        for py_file, func, step_text in _iter_then_steps():
            if "account_id" not in step_text:
                continue
            referenced = _field_names_referenced(func)
            if "account_id" not in referenced and not _account_id_graded_by_assertion(func):
                violations.append(
                    f"{py_file.relative_to(Path.cwd())}:{func.lineno} {func.name} — step mentions account_id"
                )

        assert not violations, (
            f"Found {len(violations)} Then step(s) mentioning account_id without referencing it in code:\n"
            + "\n".join(f"  {v}" for v in violations)
        )

    @pytest.mark.arch_guard
    def test_literal_response_field_steps_reference_the_named_field(self):
        """Then steps about literal response fields must reference those field names in code."""
        violations = []
        pattern = re.compile(r'the response should (?:not )?contain "([^"{}/]+)" field')
        for py_file, func, step_text in _iter_then_steps():
            match = pattern.search(step_text)
            if match is None:
                continue
            field_name = match.group(1)
            referenced = _field_names_referenced(func)
            if field_name not in referenced:
                violations.append(
                    f"{py_file.relative_to(Path.cwd())}:{func.lineno} {func.name} — step claims response field '{field_name}'"
                )

        assert not violations, (
            f"Found {len(violations)} response-field Then step(s) that do not reference the named field:\n"
            + "\n".join(f"  {v}" for v in violations)
        )


def _parse_single_func(src: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node
    raise AssertionError("no function in snippet")


class TestAccountIdExemptionSelfTest:
    """Meta-tests for the ``_account_id_graded_by_assertion`` exemption (#1329 finding 9)."""

    @pytest.mark.parametrize(
        "body",
        [
            # Bare log/print/raise/message uses — account_id's result is discarded.
            "logger.info(account_id)",
            "print(account_id)",
            'logger.info("acct %s", account_id)',
            'logger.info("acct", extra=account_id)',
            'logger.info("acct %s", str(account_id))',
            "raise AssertionError(str(account_id))",
            "logger.info(repr(account_id))",
            # The hole finding 9 named: a lookup whose RESULT is never consumed by an assertion.
            "acct = _wire_account(ctx, account_id)",
            "return _wire_account(ctx, account_id)",
            'acct = _wire_account(ctx, account_id)\n    assert ctx["other"] == 1',
        ],
    )
    def test_lookup_not_graded_by_assertion_is_not_exempt(self, body):
        """A use of account_id whose result never reaches an assertion does NOT satisfy the exemption."""
        func = _parse_single_func(f"def then_x(ctx, account_id):\n    {body}\n")
        assert not _account_id_graded_by_assertion(func), f"vacuous use wrongly counted as inspection: {body!r}"

    @pytest.mark.parametrize(
        "body",
        [
            # Direct: the account_id-carrying call is in the assert test.
            "assert account_id in _wire_account_ids(ctx, account_id)",
            # Bound result consumed by an assertion.
            'acct = _wire_account(ctx, account_id)\n    assert acct["status"] == "synced"',
            # Transitive taint: acct -> agents -> actual -> assert.
            'acct = _wire_account(ctx, account_id)\n    agents = acct.get("governance_agents")'
            '\n    actual = agents[0]["url"]\n    assert actual == "https://h/"',
        ],
    )
    def test_lookup_graded_by_assertion_is_exempt(self, body):
        """Routing account_id into a lookup whose result is consumed by an assertion IS inspection."""
        func = _parse_single_func(f"def then_x(ctx, account_id):\n    {body}\n")
        assert _account_id_graded_by_assertion(func), f"real graded inspection wrongly rejected: {body!r}"
