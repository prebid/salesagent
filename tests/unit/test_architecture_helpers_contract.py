"""Contract tests for shared structural-guard helpers (PAT-01, PR 4 of #1234)."""

from __future__ import annotations

import ast
import warnings
from pathlib import Path

import pytest

from tests.unit._architecture_helpers import (
    SELECT_CHAIN_METHODS,
    SELECT_FILTER_METHODS,
    assert_anchor_consistency,
    assert_violations_match_allowlist,
    call_callee_name,
    fold_name,
    iter_call_expressions,
    iter_git_tracked_files,
    iter_scopes,
    orm_references,
    postgres_image_ref,
    postgres_tag_pattern_map,
    query_bindings,
    rel,
    scan_src,
    scope_bindings,
    scope_bound_names,
    scope_nodes,
    select_chain,
    select_entity,
    statement_entity,
    statement_root,
    uv_version_pattern_map,
    walk_with_enclosing_function,
)

_ITER_CALL_SOURCE = """
f()
g.h()
other.i()

def inner():
    inner_call()
"""


@pytest.mark.arch_guard
def test_iter_call_expressions_yields_all_calls_unfiltered() -> None:
    tree = ast.parse(_ITER_CALL_SOURCE)
    calls = list(iter_call_expressions(tree))
    assert len(calls) == 4


@pytest.mark.arch_guard
def test_iter_call_expressions_filters_by_name() -> None:
    tree = ast.parse(_ITER_CALL_SOURCE)
    f_calls = list(iter_call_expressions(tree, name="f"))
    assert len(f_calls) == 1
    assert isinstance(f_calls[0].func, ast.Name)
    assert f_calls[0].func.id == "f"

    h_calls = list(iter_call_expressions(tree, name="h"))
    assert len(h_calls) == 1
    assert isinstance(h_calls[0].func, ast.Attribute)
    assert h_calls[0].func.attr == "h"


@pytest.mark.arch_guard
def test_iter_call_expressions_name_matches_bare_and_attribute() -> None:
    tree = ast.parse("h()\nx.h()")
    h_calls = list(iter_call_expressions(tree, name="h"))
    assert len(h_calls) == 2


@pytest.mark.arch_guard
def test_iter_call_expressions_subtree_scope() -> None:
    tree = ast.parse(_ITER_CALL_SOURCE)
    inner_func = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef))
    calls = list(iter_call_expressions(inner_func))
    assert len(calls) == 1
    assert isinstance(calls[0].func, ast.Name)
    assert calls[0].func.id == "inner_call"


_SCOPE_SOURCE = """
from sqlalchemy import select
from src.core.database.models import User

def f(p):
    a = select(User)
    a = a.where(User.email == "x")
    b = 1
    b += 1
    for c in p:
        pass
    (d, e) = p
    g = [h for h in p]

    def inner():
        pass
"""


def _function(tree: ast.Module) -> ast.FunctionDef:
    return next(node for node in tree.body if isinstance(node, ast.FunctionDef))


@pytest.mark.arch_guard
def test_scope_bindings_keeps_only_names_bound_by_plain_assignment() -> None:
    func = _function(ast.parse(_SCOPE_SOURCE))
    bindings = scope_bindings(func)
    assert set(bindings) == {"a", "g"}  # b: +=; c: for target; d, e: unpacking; p: parameter
    assert len(bindings["a"]) == 2  # both assignments, in source order
    assert scope_bound_names(func) == {"p", "a", "b", "c", "d", "e", "g", "inner"}  # h is the comprehension's


@pytest.mark.arch_guard
def test_scope_nodes_yields_a_nested_scope_without_entering_it() -> None:
    func = _function(ast.parse(_SCOPE_SOURCE))
    nodes = list(scope_nodes(func))
    assert any(isinstance(node, ast.ListComp) for node in nodes)
    assert any(isinstance(node, ast.FunctionDef) for node in nodes)
    assert sum(isinstance(node, ast.Pass) for node in nodes) == 1  # the for body, not inner's
    comprehension = next(node for node in nodes if isinstance(node, ast.ListComp))
    assert scope_bound_names(comprehension) == {"h"}


@pytest.mark.arch_guard
def test_select_entity_resolves_by_import_and_never_by_spelling() -> None:
    source = (
        "from sqlalchemy import select as sa_select\n"
        "from src.core.database.models import User as DBUser\n"
        "from src.core.schemas import Product\n"
        "import src.core.database.models as models\n"
        "sa_select(DBUser)\n"
        "sa_select(DBUser.email)\n"
        "sa_select(Product)\n"
        "sa_select(models.User)\n"
        "sa_select(func.count())\n"
        "select(DBUser)\n"
    )
    tree = ast.parse(source)
    bindings = query_bindings("src/x.py", tree)
    calls = [stmt.value for stmt in tree.body if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)]
    assert [select_entity(call, bindings) for call in calls] == ["User", "User", None, None, None, None]


@pytest.mark.arch_guard
def test_statement_entity_answers_for_each_statement_root() -> None:
    """A row per statement root ``statement_root`` accepts, and the class ``statement_entity`` reads off it."""
    source = (
        "from sqlalchemy import select\n"
        "from src.core.database.models import User\n"
        "select(User)\n"
        "select(User.email)\n"
        "select(func.count())\n"
        "session.query(User)\n"
        "session.query(User.email)\n"
        "session.query()\n"
        "sa.select(User)\n"
        "wrap(User)\n"
    )
    tree = ast.parse(source)
    bindings = query_bindings("src/x.py", tree)

    def read(expr: ast.expr) -> tuple[bool, str | None]:
        root = statement_root(expr, bindings)
        return root is not None, statement_entity(root, bindings) if root is not None else None

    assert [read(stmt.value) for stmt in tree.body if isinstance(stmt, ast.Expr)] == [
        (True, "User"),
        (True, "User"),  # a column projection addresses its class
        (True, None),  # a statement on no mapped class
        (True, "User"),  # the legacy <session>.query(M)
        (True, "User"),
        (True, None),
        (False, None),  # not a statement root: unresolved, never guessed
        (False, None),
    ]


@pytest.mark.arch_guard
def test_query_bindings_drops_a_name_the_module_rebinds() -> None:
    source = (
        "from sqlalchemy import select\n"
        "from src.core.database.models import Tenant, User\n"
        "def select(x):\n    return x\n"
        "User = 1\n"
    )
    bindings = query_bindings("src/x.py", ast.parse(source))
    assert bindings.select == frozenset()
    assert bindings.orm == {"Tenant": "Tenant"}


@pytest.mark.arch_guard
def test_select_chain_crosses_only_the_named_methods() -> None:
    chain = ast.parse("select(User).where(a).with_only_columns(b).order_by(c).limit(1)", mode="eval").body
    base, calls = select_chain(chain, SELECT_CHAIN_METHODS)
    assert [call_callee_name(call) for call in calls] == ["limit", "order_by"]  # outermost first
    assert call_callee_name(base) == "with_only_columns"  # not a chain method: the walk stops

    chain = ast.parse("stmt.order_by(c).where(a)", mode="eval").body
    base, calls = select_chain(chain, SELECT_FILTER_METHODS)
    assert [call_callee_name(call) for call in calls] == ["where"]
    assert call_callee_name(base) == "order_by"


_SCOPES_SOURCE = """
from sqlalchemy import select
from src.core.database.models import User

def f(select):
    g = lambda: select(User)

class K:
    User = 1

    def m(self):
        return select(User)
"""


@pytest.mark.arch_guard
def test_iter_scopes_shadows_through_enclosing_functions_but_not_class_bodies() -> None:
    tree = ast.parse(_SCOPES_SOURCE)
    views = {
        getattr(scope, "name", type(scope).__name__): view
        for scope, view, _ in iter_scopes(tree, query_bindings("src/x.py", tree))
    }
    assert list(views) == ["Module", "f", "Lambda", "K", "m"]  # the module first, then source order
    assert views["Module"].select == {"select"} and views["Module"].orm == {"User": "User"}
    assert views["f"].select == frozenset()  # a parameter shadows the import...
    assert views["Lambda"].select == frozenset()  # ...in every scope nested inside it
    assert views["K"].orm == {}  # a class body binds User for itself...
    assert views["m"].orm == {"User": "User"} and views["m"].select == {"select"}  # ...not for its methods


_FUNCTIONS_SOURCE = """
x = [y for y in ()]

class K:
    k = lambda: 1

    def m(self):
        return [lambda: n for n in ()]

@decorate(lambda: 0)
async def f(a=lambda: 0):
    class L:
        pass

    def g():
        return (h for h in ())
"""


@pytest.mark.arch_guard
def test_iter_scopes_names_the_function_that_holds_each_scope() -> None:
    """A ``def`` names itself; any other scope, the innermost ``def`` around it, else ``<module>``.

    For every positioned node a scope owns, that is the name ``walk_with_enclosing_function``
    gives the node, except a ``def`` statement itself, which runs in the scope around it.
    (Context and operator nodes are shared singletons, so ``id()`` cannot tell them apart.)
    """
    tree = ast.parse(_FUNCTIONS_SOURCE)
    scopes = list(iter_scopes(tree, query_bindings("src/x.py", tree)))
    assert [(getattr(scope, "name", type(scope).__name__), function) for scope, _, function in scopes] == [
        ("Module", "<module>"),
        ("ListComp", "<module>"),
        ("K", "<module>"),  # a class body is not a function
        ("Lambda", "<module>"),
        ("m", "m"),
        ("ListComp", "m"),
        ("Lambda", "m"),
        ("f", "f"),
        ("Lambda", "f"),  # the default
        ("L", "f"),
        ("g", "g"),
        ("GeneratorExp", "g"),
        ("Lambda", "f"),  # the decorator
    ]
    walked = {id(node): function for node, function in walk_with_enclosing_function(tree)}
    mismatched = [
        (type(node).__name__, node.lineno, function, walked[id(node)])
        for scope, _, function in scopes
        for node in scope_nodes(scope)
        if hasattr(node, "lineno")
        and not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and walked[id(node)] != function
    ]
    assert mismatched == []


@pytest.mark.arch_guard
def test_orm_references_resolves_names_and_selects_per_scope() -> None:
    tree = ast.parse(_SCOPES_SOURCE)
    references = orm_references("src/x.py", tree)
    calls = {
        getattr(scope, "name", type(scope).__name__): next(n for n in scope_nodes(scope) if isinstance(n, ast.Call))
        for scope, _, _ in iter_scopes(tree, query_bindings("src/x.py", tree))
        if any(isinstance(n, ast.Call) for n in scope_nodes(scope))
    }
    assert references.get(id(calls["m"])) == "User"  # select(User) in the method
    assert id(calls["Lambda"]) not in references  # select is the enclosing function's parameter
    assert references.get(id(calls["Lambda"].args[0])) == "User"  # ...while User itself still resolves
    assert all(isinstance(node, (ast.Name, ast.Call)) for node in ast.walk(tree) if id(node) in references)


_FOLD_SOURCE = """
from sqlalchemy import select
from src.core.database.models import Creative, User

def f(c, cid, p, session):
    s = select(Creative).where(Creative.creative_id == cid)
    s = s.where(Creative.principal_id == p)
    s = wrap(s)
    t = select(User)
    t = t.order_by(User.email)
    u = t.where(User.is_active)
    if c:
        v = select(User)
    else:
        v = session.query(Creative)
    w = make()
    w = w.where(User.is_active)
"""


@pytest.mark.arch_guard
def test_fold_name_folds_rebinds_in_source_order_and_marks_what_it_cannot_read() -> None:
    tree = ast.parse(_FOLD_SOURCE)
    ((scope, view),) = [(s, v) for s, v, _ in iter_scopes(tree, query_bindings("src/x.py", tree)) if s is not tree]
    assigned = scope_bindings(scope)

    def read(name: str) -> tuple[str, list[str | None], bool] | None:
        fold = fold_name(name, assigned, view)
        return fold and (ast.unparse(fold.root), [call_callee_name(call) for call in fold.calls], fold.opaque)

    assert read("s") == ("select(Creative)", ["where", "where"], True)  # wrap(s): the filters stand, opaque
    assert read("t") == ("select(User)", ["order_by"], False)
    assert read("u") == ("select(User)", ["where", "order_by"], False)  # folded through another name
    assert read("v") == ("session.query(Creative)", [], True)  # two statements: the last one, opaque
    assert read("w") is None  # no binding starts a statement
    assert read("c") is None  # a parameter


@pytest.mark.arch_guard
def test_assert_violations_match_allowlist_passes_when_sets_equal() -> None:
    allowlist = {("tests/unit/test_foo.py", "test_bar")}
    assert_violations_match_allowlist(allowlist, allowlist)


@pytest.mark.arch_guard
def test_assert_violations_match_allowlist_flags_new_violations() -> None:
    with pytest.raises(AssertionError, match="new violations"):
        assert_violations_match_allowlist(
            {("src/core/new.py", "bad_fn")},
            set(),
            fix_hint="Fix the violation or add to the guard allowlist.",
        )


@pytest.mark.arch_guard
def test_assert_violations_match_allowlist_flags_stale_entries() -> None:
    with pytest.raises(AssertionError, match="stale entries"):
        assert_violations_match_allowlist(
            set(),
            {("src/core/fixed.py", "was_bad")},
            fix_hint="Remove fixed entries from the allowlist.",
        )


@pytest.mark.arch_guard
def test_assert_anchor_consistency_passes_when_values_match() -> None:
    sources = [
        (Path("Dockerfile"), "FROM python:3.12-slim"),
        (Path(".python-version"), "3.12.4\n"),
    ]
    pattern_map = {
        "Dockerfile": r"FROM python:([0-9]+\.[0-9]+)",
        ".python-version": r"^\s*([0-9]+\.[0-9]+)",
    }
    assert_anchor_consistency(sources, pattern_map, label="python")


@pytest.mark.arch_guard
def test_assert_anchor_consistency_flags_drift() -> None:
    sources = [
        (Path(".uv-version"), "0.11.15\n"),
        (Path("Dockerfile"), "ARG UV_VERSION=0.11.14\n"),
    ]
    with pytest.raises(AssertionError, match="uv version drift"):
        assert_anchor_consistency(sources, uv_version_pattern_map(), label="uv version")


@pytest.mark.arch_guard
def test_assert_anchor_consistency_flags_intra_file_drift() -> None:
    sources = [
        (
            Path("ci.yml"),
            f"services:\n  db1:\n    image: {postgres_image_ref('17-alpine')}\n  db2:\n    image: {postgres_image_ref('15-alpine')}\n",
        ),
    ]

    with pytest.raises(AssertionError, match="postgres image drift"):
        assert_anchor_consistency(sources, postgres_tag_pattern_map(), label="postgres image")


# ---------------------------------------------------------------------------
# iter_git_tracked_files fallback (PR #1567 round-3): the filesystem-walk
# fallback must be LOUD (RuntimeWarning) and hermetic w.r.t. untracked local
# dirs (.claude/ notes were producing spurious version-anchor guard failures
# in the in-network container, where the bind-mounted worktree's .git
# back-reference is unreachable and the fallback engages).
# ---------------------------------------------------------------------------


@pytest.mark.arch_guard
def test_git_unavailable_fallback_warns_and_prunes_claude(tmp_path: Path) -> None:
    """Positive fixture: no git metadata -> fallback engages loudly, .claude pruned."""
    (tmp_path / "kept.py").write_text("x = 1\n", encoding="utf-8")
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "notes.md").write_text("untracked local notes\n", encoding="utf-8")

    with pytest.warns(RuntimeWarning, match="git ls-files.*failed.*falling back"):
        files = list(iter_git_tracked_files(tmp_path))

    names = {rel(f, tmp_path) for f in files}
    assert "kept.py" in names, f"fallback walk must yield regular files, got {sorted(names)}"
    assert not any(n.startswith(".claude/") for n in names), (
        f"fallback walk must prune .claude/ (untracked local notes leak into guard scans), got {sorted(names)}"
    )


@pytest.mark.arch_guard
def test_git_available_never_engages_fallback(tmp_path: Path) -> None:
    """Negative fixture: working git -> tracked files only, no fallback warning."""
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "tracked.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "untracked.py").write_text("y = 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.py"], cwd=tmp_path, check=True)

    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        files = list(iter_git_tracked_files(tmp_path))

    names = {rel(f, tmp_path) for f in files}
    assert names == {"tracked.py"}, (
        f"with working git the helper must yield exactly the tracked set (hermetic), got {sorted(names)}"
    )


# ---------------------------------------------------------------------------
# scan_src: the two suppression axes, and why their liveness rules differ.
#
# These run over a temporary tree via ``scan_dirs=`` rather than the real
# ``src/``, so they grade the RULE rather than whatever the repo happens to
# contain today. Both halves are tested: the exempt raise is exercised
# incidentally by a caller, but the skip_prefixes raise -- the semantically
# distinct half, and the one that makes a guard's scope boundary honest -- was
# graded by nothing until these landed.
# ---------------------------------------------------------------------------


def _flag_marker(tree: ast.Module) -> list[int]:
    """Trivial detector: every ``MARKER`` name reference is a violation."""
    return sorted(n.lineno for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id == "MARKER")


def _tree(tmp_path: Path, **files: str) -> list[Path]:
    root = tmp_path / "src"
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return [root]


def test_scan_src_reports_only_files_the_detector_flags(tmp_path):
    dirs = _tree(tmp_path, **{"dirty.py": "MARKER\n", "clean.py": "x = 1\n"})

    found = scan_src(_flag_marker, scan_dirs=dirs)

    assert set(found) == {rel(dirs[0] / "dirty.py")}, "a file with no findings must be omitted entirely"


def test_scan_src_suppresses_an_exempt_file(tmp_path):
    dirs = _tree(tmp_path, **{"sanctioned.py": "MARKER\n"})

    found = scan_src(_flag_marker, exempt=frozenset({rel(dirs[0] / "sanctioned.py")}), scan_dirs=dirs)

    assert found == {}, "a live exemption must suppress its file"


def test_scan_src_raises_on_an_exemption_that_suppresses_nothing(tmp_path):
    """An exempt entry the detector never flags is worse than no entry.

    It reads as a considered decision while doing nothing, and pre-authorizes
    the violation if the file ever acquires one.
    """
    dirs = _tree(tmp_path, **{"clean.py": "x = 1\n"})

    with pytest.raises(AssertionError, match="dead exemption"):
        scan_src(_flag_marker, exempt=frozenset({rel(dirs[0] / "clean.py")}), scan_dirs=dirs)


def test_scan_src_raises_on_an_exemption_for_a_path_that_does_not_exist(tmp_path):
    dirs = _tree(tmp_path, **{"dirty.py": "MARKER\n"})

    with pytest.raises(AssertionError, match="dead exemption"):
        scan_src(_flag_marker, exempt=frozenset({"src/nonexistent.py"}), scan_dirs=dirs)


def test_scan_src_suppresses_a_prefix_when_at_least_one_file_under_it_is_flagged(tmp_path):
    """A scope boundary need only cover ONE violation, unlike an exemption.

    Most files under a legitimately-excluded subtree are clean; requiring each
    to be flagged would red the build on innocent files, which is why this rule
    is deliberately weaker than the exempt one.
    """
    dirs = _tree(tmp_path, **{"pkg/dirty.py": "MARKER\n", "pkg/clean.py": "x = 1\n", "outside.py": "MARKER\n"})

    found = scan_src(_flag_marker, skip_prefixes=(rel(dirs[0] / "pkg"),), scan_dirs=dirs)

    assert set(found) == {rel(dirs[0] / "outside.py")}, "the whole prefix must be excluded, clean files included"


def test_scan_src_raises_on_a_prefix_that_excludes_nothing(tmp_path):
    """A boundary with no violation behind it would silently permit the first one."""
    dirs = _tree(tmp_path, **{"pkg/clean.py": "x = 1\n", "outside.py": "MARKER\n"})

    with pytest.raises(AssertionError, match="dead scope boundary"):
        scan_src(_flag_marker, skip_prefixes=(rel(dirs[0] / "pkg"),), scan_dirs=dirs)


def test_scan_src_raises_on_a_prefix_matching_no_file_at_all(tmp_path):
    dirs = _tree(tmp_path, **{"dirty.py": "MARKER\n"})

    with pytest.raises(AssertionError, match="dead scope boundary"):
        scan_src(_flag_marker, skip_prefixes=("src/nonexistent/",), scan_dirs=dirs)


def test_scan_src_does_not_raise_when_nothing_is_suppressed(tmp_path):
    """Empty suppression sets have nothing that could be dead."""
    dirs = _tree(tmp_path, **{"dirty.py": "MARKER\n"})

    assert scan_src(_flag_marker, exempt=frozenset(), skip_prefixes=(), scan_dirs=dirs)
