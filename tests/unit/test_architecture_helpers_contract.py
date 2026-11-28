"""Contract tests for shared structural-guard helpers (PAT-01, PR 4 of #1234)."""

from __future__ import annotations

import ast
import warnings
from pathlib import Path

import pytest

from tests.unit._architecture_helpers import (
    SELECT_CHAIN_METHODS,
    SELECT_FILTER_METHODS,
    SelectCall,
    assert_anchor_consistency,
    assert_violations_match_allowlist,
    call_callee_name,
    extract_select_calls,
    filtered_columns,
    fold_name,
    iter_call_expressions,
    iter_git_tracked_files,
    iter_queries,
    iter_scopes,
    mapped,
    orm_references,
    postgres_image_ref,
    postgres_tag_pattern_map,
    query_bindings,
    rel,
    scan_rows,
    scan_src,
    scope_bindings,
    scope_bound_names,
    scope_nodes,
    select_chain,
    select_entity,
    statement_entity,
    statement_root,
    uv_version_pattern_map,
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
        "from src.core.database import models as m\n"
        "sa_select(DBUser)\n"
        "sa_select(DBUser.email)\n"
        "sa_select(Product)\n"
        "sa_select(models.User)\n"
        "sa_select(m.User.email)\n"
        "sa_select(m.UserRole)\n"
        "sa_select(func.count())\n"
        "select(DBUser)\n"
    )
    tree = ast.parse(source)
    bindings = query_bindings("src/x.py", tree)
    calls = [stmt.value for stmt in tree.body if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)]
    assert [select_entity(call, bindings) for call in calls] == [
        "User",
        "User",
        None,  # a Pydantic namesake
        "User",  # the models module, imported as a module
        "User",  # ...and from its package
        None,  # a name models.py exports that no mapper maps
        None,
        None,  # select is not SQLAlchemy's here
    ]


@pytest.mark.arch_guard
def test_statement_entity_answers_for_each_statement_root() -> None:
    """A row per statement root ``statement_root`` accepts, the class ``statement_entity`` reads off
    it, and the class ``orm_references`` gives the same node: one answer, read two ways."""
    source = (
        "from sqlalchemy import select\n"
        "from src.core.database.models import User\n"
        "import src.core.database.models as models\n"
        "select(User)\n"
        "select(User.email)\n"
        "select(models.User)\n"
        "select(func.count())\n"
        "session.query(User)\n"
        "session.query(User.email)\n"
        "session.query(models.User.email)\n"
        "session.query()\n"
        "sa.select(User)\n"
        "wrap(User)\n"
    )
    tree = ast.parse(source)
    bindings = query_bindings("src/x.py", tree)
    references = orm_references("src/x.py", tree)

    def read(expr: ast.expr) -> tuple[bool, str | None, str | None]:
        root = statement_root(expr, bindings)
        return (
            root is not None,
            statement_entity(root, bindings) if root is not None else None,
            references.get(id(expr)),
        )

    assert [read(stmt.value) for stmt in tree.body if isinstance(stmt, ast.Expr)] == [
        (True, "User", "User"),
        (True, "User", "User"),  # a column projection addresses its class
        (True, "User", "User"),  # through the models module
        (True, None, None),  # a statement on no mapped class
        (True, "User", "User"),  # the legacy <session>.query(M)
        (True, "User", "User"),
        (True, "User", "User"),
        (True, None, None),
        (False, None, None),  # not a statement root: unresolved, never guessed
        (False, None, None),
    ]


_IMPORT_SCOPES_SOURCE = """
from src.core.schemas import Product
from src.core.database.models import Product, User

def f():
    from src.core.database.models import Tenant
    from src.core.schemas import User
    return Tenant, User, Product

def g():
    return Tenant, User, Product
"""


@pytest.mark.arch_guard
def test_an_import_binds_the_scope_that_holds_it() -> None:
    """An import inside ``f`` binds ``f`` alone, as Python binds it: its ORM class does not
    resolve in ``g``, and a non-ORM import of a module-level model's name shadows it in ``f``
    only. A name the module imports from two places resolves to nothing anywhere."""
    tree = ast.parse(_IMPORT_SCOPES_SOURCE)
    references = orm_references("src/x.py", tree)
    returned = {
        function.name: [references.get(id(name)) for name in statement.value.elts]
        for function in tree.body
        if isinstance(function, ast.FunctionDef)
        and isinstance(statement := function.body[-1], ast.Return)
        and isinstance(statement.value, ast.Tuple)
    }
    assert returned == {"f": ["Tenant", None, None], "g": [None, "User", None]}


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
        for scope, view, _, _ in iter_scopes(tree, query_bindings("src/x.py", tree))
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

    The rule is ``enclosing_function``, which ``walk_with_enclosing_function`` applies per node.
    """
    tree = ast.parse(_FUNCTIONS_SOURCE)
    scopes = list(iter_scopes(tree, query_bindings("src/x.py", tree)))
    assert [(getattr(scope, "name", type(scope).__name__), function) for scope, _, function, _ in scopes] == [
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


@pytest.mark.arch_guard
def test_orm_references_resolves_names_and_selects_per_scope() -> None:
    tree = ast.parse(_SCOPES_SOURCE)
    references = orm_references("src/x.py", tree)
    calls = {
        getattr(scope, "name", type(scope).__name__): next(n for n in scope_nodes(scope) if isinstance(n, ast.Call))
        for scope, _, _, _ in iter_scopes(tree, query_bindings("src/x.py", tree))
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
    ((scope, view),) = [(s, v) for s, v, _, _ in iter_scopes(tree, query_bindings("src/x.py", tree)) if s is not tree]
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


_QUERIES_SOURCE = """
from sqlalchemy import select
from src.core.database.models import Creative, Tenant, User

def f(session, tid, cids, pid, email, sub):
    stmt = select(Creative).where(Creative.tenant_id == tid)
    stmt = stmt.where(Creative.creative_id.in_(cids))
    first = session.scalars(stmt.where(Creative.principal_id != pid)).first()
    joined = (
        select(User)
        .join(Tenant, (User.tenant_id == Tenant.tenant_id) & (Tenant.subdomain == sub))
        .where(User.email == email)
    )
    by_keyword = select(Tenant).join(User).filter_by(email=email)
    return session.query(User).filter_by(tenant_id=tid).order_by(User.email)
"""


@pytest.mark.arch_guard
def test_iter_queries_reads_each_query_once_and_filtered_columns_reads_its_filter_stages() -> None:
    """A row per query: an accumulator is one query on its fold, a chain on a folded name is
    the fold's calls with its own on top, and a legacy ``.query(M)`` is a query. Its columns are
    the ones a filter stage pins to a value: not a join's ON clause, not ``!=``, and a
    ``filter_by`` keyword on the entity ``filter_by`` reads (the joined one)."""
    tree = ast.parse(_QUERIES_SOURCE)
    rows = [
        (
            query.entity,
            [call_callee_name(call) for call in query.calls],
            sorted(filtered_columns(query)),
            query.opaque,
            query.holder,
        )
        for query in iter_queries(tree, "src/x.py")
    ]
    creative = [("Creative", "creative_id"), ("Creative", "tenant_id")]
    assert rows == [
        ("Creative", ["where", "where"], creative, False, "f"),  # stmt, both bindings, once
        ("User", ["where", "join"], [("User", "email")], False, "f"),  # the ON clause filters nothing
        ("Tenant", ["filter_by", "join"], [("User", "email")], False, "f"),
        ("Creative", ["where", "where", "where"], creative, False, "f"),  # stmt.where(...): != pins nothing
        ("User", ["order_by", "filter_by"], [("User", "tenant_id")], False, "f"),
    ]


@pytest.mark.arch_guard
def test_mapped_refuses_a_name_no_mapper_maps() -> None:
    assert mapped("MediaBuy", "Tenant") == frozenset({"MediaBuy", "Tenant"})
    with pytest.raises(AssertionError, match="MediaBuyz"):
        mapped("MediaBuy", "MediaBuyz")


@pytest.mark.arch_guard
def test_extract_select_calls_reports_the_class_a_query_addresses(tmp_path: Path) -> None:
    """An aliased ``select`` and a column projection report the class, and the tenant filter is
    the query's own, not a ``tenant_id`` written near it; a ``select`` that is not SQLAlchemy's
    reports nothing."""
    source = tmp_path / "queries.py"
    source.write_text(
        "from sqlalchemy import select as sa_select\n"
        "from src.core.database.models import Creative as DBCreative\n"
        "\n"
        "def load(session, tid):\n"
        "    rows = session.scalars(sa_select(DBCreative).where(DBCreative.tenant_id == tid)).all()\n"
        "    return rows, session.scalars(sa_select(DBCreative.creative_id)).all()\n"
        "\n"
        "def other(select, tenant_id):\n"
        "    return select(DBCreative)\n",
        encoding="utf-8",
    )
    assert extract_select_calls(source, "load", models=["Creative"]) == [
        SelectCall("Creative", True, 5),
        SelectCall("Creative", False, 6),
    ]
    assert extract_select_calls(source, "other", models=["Creative"]) == []


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


# ---------------------------------------------------------------------------
# scan_rows: the row-returning scan. It owns the walk and the accumulation, and it
# refuses the two scans that grade nothing: a root that is not there, and a scan
# whose skip leaves no module.
# ---------------------------------------------------------------------------


def _marker_rows(tree: ast.Module, relpath: str) -> list[tuple[str, int]]:
    return [(relpath, line) for line in _flag_marker(tree)]


def test_scan_rows_returns_every_row_of_every_module(tmp_path):
    dirs = _tree(tmp_path, **{"a.py": "MARKER\nMARKER\n", "pkg/b.py": "MARKER\n", "clean.py": "x = 1\n"})

    rows = scan_rows(dirs, _marker_rows)

    a, b = rel(dirs[0] / "a.py"), rel(dirs[0] / "pkg" / "b.py")
    assert sorted(rows) == [(a, 1), (a, 2), (b, 1)]


def test_scan_rows_does_not_scan_a_file_skip_names(tmp_path):
    dirs = _tree(tmp_path, **{"a.py": "MARKER\n", "b.py": "MARKER\n"})

    rows = scan_rows(dirs, _marker_rows, skip=lambda relpath: relpath.endswith("b.py"))

    assert rows == [(rel(dirs[0] / "a.py"), 1)]


def test_scan_rows_raises_on_a_root_that_does_not_exist(tmp_path):
    dirs = _tree(tmp_path, **{"a.py": "MARKER\n"})

    with pytest.raises(AssertionError, match="not directories"):
        scan_rows([*dirs, tmp_path / "srcc"], _marker_rows)


def test_scan_rows_raises_on_a_scan_that_grades_no_module(tmp_path):
    dirs = _tree(tmp_path, **{"a.py": "MARKER\n"})

    with pytest.raises(AssertionError, match="was scanned"):
        scan_rows(dirs, _marker_rows, skip=lambda relpath: True)
