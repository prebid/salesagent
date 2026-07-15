"""Guard: a namespace must not bind the same name twice.

Python binds the LAST ``def`` to the name, so an earlier same-named definition
is dead code — and worse, it is *invisibly* dead: every call site resolves to
the later body at call time, regardless of where it sits in the file. A reader
scrolling to the first definition sees a body that never runs.

Why a guard and not the linter: ruff/pyflakes F811 (``redefinition-of-unused``)
already reports this — but ONLY for names that do not match
``lint.dummy-variable-rgx`` (default: anything with a leading underscore). Every
*private helper* (``_foo``) is therefore exempt from F811 by construction, which
is exactly the blind spot that let uc004_delivery.py carry two divergent
``_resolve_media_buy_id`` bodies. Widening ``dummy-variable-rgx`` to close it is
not an option: that regex also governs F841, where the leading-underscore
convention marks *intentionally* unused bindings (~208 such bindings in this
tree). So the narrow structural guard is the right instrument.

Scanning approach: AST — collect the names bound by ``def``/``async def``/``class``
in each *namespace* (module body, class body, and function/async-function body)
and flag any name bound twice within one namespace. A name reused across
DIFFERENT namespaces (e.g. a module helper and an unrelated function-local of the
same name) is legal and never flagged — each namespace scans only its own direct
body statements.

Legitimate redefinitions are exempted structurally, not allowlisted:
  - ``@overload`` stubs (see src/core/enum_helpers.py)
  - ``@<name>.register`` singledispatch implementations
  - property descriptor groups — but only when PAIR-MATCHED: exactly one bare
    ``@property`` plus ``@<name>.setter/getter/deleter``. Two ``@x.setter``
    bodies for one name are still a violation (the first is dead).

Deliberately NOT exempt: Pydantic ``@field_validator`` / ``@model_validator``.
Pydantic v2 does not error on two same-named validators in a class body — it
warns and silently DROPS the first. That is this exact disease, so the guard
reports it.

Function-local shadowing IS scanned (a name bound twice in one function body —
e.g. the ``_get_or_create_context`` duplicate that once lived in
tests/harness/media_buy_create.py, removed in the evb4 dedup). A function-local
duplicate is labeled ``<function>.<name>`` — the same single-segment convention
as class methods. That key is not globally unique across nested scopes, so it can
under-report (collapse two distinct violations to one entry), but it can never
produce a false green: any real violation still leaves the found-set non-empty and
fails against the empty allowlist. A globally-unique recursive qualname is
unwarranted at the current zero-violation count.

Out of scope:
  - Conditional definitions (inside ``if TYPE_CHECKING:`` / ``try: ... except
    ImportError:``) — not direct namespace-body statements, at any scope.

GitHub: #1619 (module/class scopes), salesagent-ihd4 (function-local scopes)
"""

from __future__ import annotations

import ast

import pytest

from tests.unit._architecture_helpers import (
    REPO_ROOT,
    assert_violations_match_allowlist,
    iter_module_trees,
)

_SCAN_DIRS = [
    REPO_ROOT / "src",
    REPO_ROOT / "tests",
    REPO_ROOT / "scripts",
    REPO_ROOT / ".pre-commit-hooks",
]

# Allowlist can only shrink (stale entries are reported by
# assert_violations_match_allowlist). Empty by construction: the sole violation
# this guard was written for (uc004_delivery.py::_resolve_media_buy_id) was
# deleted rather than allowlisted, and legal redefinitions are exempted
# structurally. Entries are (repo_relative_path, qualified_name).
_ALLOWED_DUPLICATES: set[tuple[str, str]] = set()

_Def = ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
_Namespace = ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef

# Decorators under which a name may legally be bound more than once in a namespace.
_OVERLOAD = "overload"
_REGISTER = "register"
_DESCRIPTOR_ATTRS = frozenset({"setter", "getter", "deleter"})


def _decorators(node: _Def) -> tuple[set[str], set[str]]:
    """Return (bare_names, attribute_names) for the node's decorators.

    ``@overload`` -> bare {"overload"}; ``@size.setter`` -> attrs {"setter"}.
    """
    bare: set[str] = set()
    attrs: set[str] = set()
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(target, ast.Name):
            bare.add(target.id)
        elif isinstance(target, ast.Attribute):
            attrs.add(target.attr)
    return bare, attrs


def _is_legal_redefinition_group(nodes: list[_Def]) -> bool:
    """True when these same-named definitions are a legal redefinition group.

    Evaluated over the WHOLE group, not per-node, so a group cannot be waved
    through by decorating only some of its members.
    """
    decorated = [_decorators(node) for node in nodes]

    # @overload: every stub but the final implementation carries @overload.
    if all(_OVERLOAD in bare for bare, _ in decorated[:-1]):
        return True

    # singledispatch: every implementation carries @<dispatcher>.register.
    if all(_REGISTER in attrs for _, attrs in decorated):
        return True

    # Property descriptors: exactly one bare @property, and every other member
    # is a @<name>.setter/getter/deleter. Two @x.setter bodies do NOT qualify.
    properties = [i for i, (bare, _) in enumerate(decorated) if "property" in bare]
    accessors = [attrs & _DESCRIPTOR_ATTRS for _, attrs in decorated]
    if len(properties) == 1:
        others = [accessor for i, accessor in enumerate(accessors) if i != properties[0]]
        if others and all(others) and len({frozenset(a) for a in others}) == len(others):
            return True

    return False


def _duplicate_defs(source: str, filename: str = "<test>") -> dict[str, list[int]]:
    """Return {qualified_name: [linenos]} for names bound 2+ times in one namespace.

    Namespaces scanned: the module body, every class body, and every function
    body (see the module docstring for what function-LOCAL shadowing looks like).
    """
    return _duplicate_defs_in_tree(ast.parse(source, filename=filename))


def _duplicate_defs_in_tree(tree: ast.Module) -> dict[str, list[int]]:
    duplicates: dict[str, list[int]] = {}

    namespaces: list[_Namespace] = [tree]
    namespaces.extend(
        node for node in ast.walk(tree) if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    )

    for namespace in namespaces:
        bindings: dict[str, list[_Def]] = {}
        for node in namespace.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bindings.setdefault(node.name, []).append(node)

        for name, nodes in bindings.items():
            if len(nodes) > 1 and not _is_legal_redefinition_group(nodes):
                qualified = name if isinstance(namespace, ast.Module) else f"{namespace.name}.{name}"
                duplicates[qualified] = [node.lineno for node in nodes]

    return duplicates


def _scan_repo() -> set[tuple[str, str]]:
    """Return {(repo_relative_path, qualified_name)} for every shadowed definition."""
    return {
        (relative, name) for tree, relative in iter_module_trees(_SCAN_DIRS) for name in _duplicate_defs_in_tree(tree)
    }


class TestNoDuplicateModuleDefs:
    """Structural guard: one name, one definition, per namespace."""

    @pytest.mark.arch_guard
    def test_no_duplicate_definitions(self) -> None:
        """No module or class body may bind the same name twice."""
        assert_violations_match_allowlist(
            _scan_repo(),
            _ALLOWED_DUPLICATES,
            fix_hint=(
                "Python binds the LAST def, so the earlier body is dead code no call site "
                "can reach. Delete the dead definition (or, for a legal redefinition group, "
                "decorate every member: @overload / @<fn>.register / @property+@<name>.setter)."
            ),
        )

    @pytest.mark.arch_guard
    def test_guard_detects_shadowed_private_helper(self) -> None:
        """Non-vacuity: the scanner must catch the F811 blind spot — a shadowed `_private` helper.

        ruff F811 does NOT report this case (leading underscore matches
        dummy-variable-rgx), which is precisely why this guard exists.
        """
        source = (
            "def _resolve(ctx, x):\n"
            "    return ctx['aliases'].get(x, x)\n"
            "\n"
            "def caller(ctx, x):\n"
            "    return _resolve(ctx, x)\n"
            "\n"
            "def _resolve(ctx, x):\n"
            "    return ctx['labels'].get(x, x)\n"
        )
        assert _duplicate_defs(source) == {"_resolve": [1, 7]}

    @pytest.mark.arch_guard
    def test_guard_detects_shadowed_method_in_class_body(self) -> None:
        """A method bound twice in one class body is the same disease, one namespace down."""
        source = "class Repo:\n    def get(self, x):\n        return 1\n\n    def get(self, x):\n        return 2\n"
        assert _duplicate_defs(source) == {"Repo.get": [2, 5]}

    @pytest.mark.arch_guard
    def test_guard_detects_shadowed_local_helper(self) -> None:
        """A nested `_helper` bound twice in one function body is the same disease, one scope deeper.

        FAILS against the un-widened helper: function bodies are not yet a scanned
        namespace, so the shadow is invisible (returns {}). Pins that widening to
        function-local scopes catches it. salesagent-ihd4 / #1619.
        """
        source = (
            "def outer():\n"
            "    def _helper():\n"
            "        return 1\n"
            "    def _helper():\n"
            "        return 2\n"
            "    return _helper()\n"
        )
        assert _duplicate_defs(source) == {"outer._helper": [2, 4]}

    @pytest.mark.arch_guard
    def test_guard_detects_shadowed_class_in_function(self) -> None:
        """A `class C` bound twice inside a function body — a ClassDef-in-FunctionDef namespace path.

        Reachable only after widening (a class nested in a function body is not a
        module- or class-body statement today), so it FAILS against the un-widened
        helper. salesagent-ihd4 / #1619.
        """
        source = "def f():\n    class C:\n        pass\n    class C:\n        pass\n    return C\n"
        assert _duplicate_defs(source) == {"f.C": [2, 4]}

    @pytest.mark.arch_guard
    def test_guard_ignores_cross_namespace_reuse(self) -> None:
        """Core Invariant: a name at module scope and the SAME name inside a function are legal.

        Different namespaces => not a shadow. The only thing that could wrongly
        trip this is a cross-namespace collision, so it guards the per-namespace
        `.body` grouping against regression. Passes both before AND after widening
        (invariant guard, not a red test). salesagent-ihd4 / #1619.
        """
        source = (
            "def helper():\n    return 1\n\ndef other():\n    def helper():\n        return 2\n    return helper()\n"
        )
        assert _duplicate_defs(source) == {}

    @pytest.mark.arch_guard
    def test_guard_detects_duplicate_pydantic_validator(self) -> None:
        """Two same-named @field_validator methods: Pydantic v2 silently DROPS the first.

        Pydantic warns rather than errors here, so the first validator's body never
        runs — a silently-dead def. The guard must report it, NOT exempt it.
        """
        source = (
            "class M(BaseModel):\n"
            "    @field_validator('x')\n"
            "    def check(cls, v):\n"
            "        return v + 1\n"
            "\n"
            "    @field_validator('x')\n"
            "    def check(cls, v):\n"
            "        return v + 100\n"
        )
        # Linenos are the `def` lines (ast reports the def, not the decorator).
        assert _duplicate_defs(source) == {"M.check": [3, 7]}

    @pytest.mark.arch_guard
    def test_guard_detects_duplicate_setter(self) -> None:
        """Two @x.setter bodies for one name: the first is dead, so it is NOT a legal group."""
        source = (
            "class C:\n"
            "    @property\n"
            "    def size(self):\n"
            "        return self._size\n"
            "\n"
            "    @size.setter\n"
            "    def size(self, v):\n"
            "        self._size = v\n"
            "\n"
            "    @size.setter\n"
            "    def size(self, v):\n"
            "        self._size = v * 2\n"
        )
        # Linenos are the `def` lines (ast reports the def, not the decorator).
        assert _duplicate_defs(source) == {"C.size": [3, 7, 11]}

    @pytest.mark.arch_guard
    def test_guard_exempts_legal_redefinition_groups(self) -> None:
        """@overload, singledispatch @x.register, and a property+setter pair are legal."""
        source = (
            "from typing import overload\n"
            "\n"
            "@overload\n"
            "def enum_value(v: None) -> None: ...\n"
            "@overload\n"
            "def enum_value(v: object) -> str: ...\n"
            "def enum_value(v):\n"
            "    return str(v)\n"
            "\n"
            "@fn.register\n"
            "def _(v: int):\n"
            "    return v\n"
            "@fn.register\n"
            "def _(v: str):\n"
            "    return v\n"
            "\n"
            "class C:\n"
            "    @property\n"
            "    def size(self):\n"
            "        return self._size\n"
            "\n"
            "    @size.setter\n"
            "    def size(self, v):\n"
            "        self._size = v\n"
        )
        assert _duplicate_defs(source) == {}
