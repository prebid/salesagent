"""Structural guard: every adapter-raised typed AdCPError has a raise-site test.

Adapters are the layer most likely to introduce a fresh typed error
(``raise AdCPLineItemError(...)``, ``raise AdCPProductUnavailableError(...)``)
that the unit suite never exercises through production code. A class -> wire-code
mapping test (test_typed_error_wire_codes.py) pins the code by *constructing* the
exception directly, but it cannot catch a class-swap at the raise site: if a site
silently changes ``raise AdCPProductUnavailableError`` to ``raise AdCPError`` the
mapping test stays green while buyers start seeing INTERNAL_ERROR.

The defense is a raise-site test (``pytest.raises(<Class>)`` driving the real
adapter method to the actual ``raise``). This guard makes that coverage
mandatory: it AST-scans ``src/adapters/`` for every concrete ``AdCPError``
subclass raised via ``raise <Name>(...)``, AST-scans ``tests/`` for every class
named in ``pytest.raises(<Name>)``, and asserts the adapter raise-set is a subset
of the tested set.

Scope note — the base ``AdCPError`` is excluded. It is the abstract root of the
taxonomy (default code INTERNAL_ERROR), not a concrete typed subclass, and the
coverage requirement targets the *typed* classes whose specific wire code a
class-swap would erase. The lone base-class raise in the mock adapter
(``mock_ad_server.py``'s test-scenario error simulation) is a generic passthrough,
not a typed-taxonomy member.

The ALLOWLIST is EMPTY and MUST STAY EMPTY: a newly raised typed adapter error
with no ``pytest.raises`` test fails this guard immediately. Adding an entry is a
code-review red flag — write the raise-site test instead. See
``tests/unit/adapters/test_mock_adapter_error_raise_sites.py`` and the
``TestGAM*RaiseSites`` classes for the established pattern (drive the real method,
assert ``pytest.raises`` + the exact ``error_code``, negative-control by reasoning
that a class-swap breaks it).
"""

from __future__ import annotations

import ast

from tests.unit._architecture_helpers import REPO_ROOT, iter_call_expressions, safe_parse

# Adapter-raised typed errors with no raise-site ``pytest.raises`` test. MUST stay
# empty — a new uncovered adapter raise fails the guard immediately. Do not add
# entries; add a raise-site test (see module docstring) instead.
ALLOWLIST: frozenset[str] = frozenset()

_ADAPTERS_DIR = REPO_ROOT / "src/adapters"
_TESTS_DIR = REPO_ROOT / "tests"


def _is_adcp_error_name(name: str) -> bool:
    """True for concrete typed AdCPError subclass names (``AdCP<Something>Error``).

    Excludes the abstract base ``AdCPError`` — it is the taxonomy root, not a
    typed subclass, so it is out of scope for typed-error coverage.
    """
    return name.startswith("AdCP") and name.endswith("Error") and name != "AdCPError"


def collect_adapter_raised_errors() -> dict[str, list[str]]:
    """Map each typed ``AdCPError`` subclass raised in ``src/adapters/`` to its sites.

    Only fresh typed raises (``raise <Name>(...)``) count — a bare ``raise`` that
    re-raises a caught exception carries no class name in the AST and is correctly
    ignored, so re-raising a caught typed error does not create a coverage
    obligation.
    """
    raised: dict[str, list[str]] = {}
    for filepath in sorted(_ADAPTERS_DIR.rglob("*.py")):
        tree = safe_parse(filepath)
        if tree is None:
            continue
        rel = str(filepath.relative_to(REPO_ROOT))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Raise) or node.exc is None:
                continue
            exc = node.exc
            if isinstance(exc, ast.Call) and isinstance(exc.func, ast.Name) and _is_adcp_error_name(exc.func.id):
                raised.setdefault(exc.func.id, []).append(f"{rel}:{node.lineno}")
    return raised


def collect_pytest_raises_classes() -> set[str]:
    """Collect every typed ``AdCPError`` subclass named in a ``pytest.raises(...)`` call across ``tests/``.

    Handles both ``pytest.raises(Foo)`` (Attribute call) and a bare
    ``raises(Foo)`` (Name call), and unpacks tuple forms
    (``pytest.raises((Foo, Bar))``). The class may be referenced as a bare name
    or a dotted attribute (``exceptions.Foo``); both resolve to the class name.
    """
    caught: set[str] = set()
    for filepath in sorted(_TESTS_DIR.rglob("*.py")):
        tree = safe_parse(filepath)
        if tree is None:
            continue
        for node in iter_call_expressions(tree, name="raises"):
            func = node.func
            is_raises = isinstance(func, ast.Attribute) or isinstance(func, ast.Name)
            if not is_raises or not node.args:
                continue
            first = node.args[0]
            candidates = first.elts if isinstance(first, ast.Tuple) else [first]
            for cand in candidates:
                name = (
                    cand.id if isinstance(cand, ast.Name) else (cand.attr if isinstance(cand, ast.Attribute) else None)
                )
                if name is not None and _is_adcp_error_name(name):
                    caught.add(name)
    return caught


class TestAdapterRaiseSiteCoverage:
    """Every typed AdCPError raised in an adapter must have a ``pytest.raises`` test."""

    def test_every_adapter_raised_error_has_a_raise_site_test(self):
        raised = collect_adapter_raised_errors()
        # Self-check: the scan must actually find adapter raises. A zero result
        # means the AST walk silently broke (e.g. a refactor moved the adapters
        # dir), which would make this guard vacuously pass.
        assert raised, f"no adapter AdCPError raises found under {_ADAPTERS_DIR} — scan likely broken"

        tested = collect_pytest_raises_classes()
        uncovered = (set(raised) - tested) - ALLOWLIST

        if uncovered:
            detail = "\n".join(
                f"  - {name} (raised at {', '.join(raised[name])}) has no pytest.raises({name}) test"
                for name in sorted(uncovered)
            )
            msg = (
                "Adapter-raised typed AdCPError subclasses lack a raise-site test:\n"
                f"{detail}\n\n"
                "Add a test that drives the real adapter method to the raise and asserts "
                "pytest.raises(<Class>) + the exact error_code. See "
                "tests/unit/adapters/test_mock_adapter_error_raise_sites.py for the pattern. "
                "Do NOT add the class to ALLOWLIST — it must stay empty."
            )
            raise AssertionError(msg)

    def test_allowlist_is_empty(self):
        """The allowlist must stay empty — coverage is mandatory, not opt-out."""
        msg = (
            f"ALLOWLIST must be empty but contains {sorted(ALLOWLIST)}. "
            "Adapter raise-site coverage is mandatory: write the raise-site test "
            "instead of allowlisting the class."
        )
        assert ALLOWLIST == frozenset(), msg
