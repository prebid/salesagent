"""Guard: an Account row is constructed in exactly ONE place: AccountRepository.build_row.

This is a LAYER-PLACEMENT ratchet (CLAUDE.md Pattern #3): natural-key assembly
belongs on the layer that owns Account's identity, never in a caller. It is NOT
a dry_run guard, which is why it survives #1721's deletion of the shadow preview
path -- three sibling guards went with that path because they pinned its shape;
this one pins where row construction may live, a rule that has nothing to do with
dry_run and stays load-bearing after it.

The invariant WIDENED in #1721 rather than narrowing: the admin create form
(``src/admin/blueprints/accounts.py``) used to mint its own account_id and
hand-build an ``Account``, a second definition of what an account row is. It now
routes through ``build_row`` + ``create()``, so this guard covers it too.

Checks THREE modules:
- ``src/core/tools/accounts.py`` never constructs an ``Account`` row directly
- ``src/admin/blueprints/accounts.py`` likewise
- ``src/core/database/repositories/account.py`` constructs one only inside
  ``build_row`` (a second builder there is the same drift risk, just moved)

Note what this guard does NOT do: it pins the mechanism, not the behaviour. The
behavioural protection is the set of dry_run tests that run the SAME payload
through preview and through a live sync and compare -- the live run is the
oracle, so those tests cannot drift from the behaviour they mirror either.
"""

import ast

from tests.unit._architecture_helpers import REPO_ROOT, parse_module

TOOLS_MODULE = "src/core/tools/accounts.py"
ADMIN_MODULE = "src/admin/blueprints/accounts.py"
REPO_MODULE = "src/core/database/repositories/account.py"
BUILDER = "build_row"
ORM_MODULE = "src.core.database.models"
ORM_CLASS = "Account"


def orm_row_names(tree: ast.Module) -> set[str]:
    """The local name(s) a module binds to the ORM ``Account`` model.

    Resolved from the module's own imports rather than hardcoded, because the
    Pydantic SCHEMA is also called ``Account`` in some modules and constructing
    it is not a row construction at all. ``accounts.py`` imports the ORM model
    as ``DBAccount`` precisely to keep the two apart; reading the alias means
    the guard follows a rename instead of silently going blind or crying wolf.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == ORM_MODULE:
            names.update(alias.asname or alias.name for alias in node.names if alias.name == ORM_CLASS)
    return names


def _is_row_construction(call: ast.Call, names: set[str]) -> bool:
    return (getattr(call.func, "id", None) or getattr(call.func, "attr", None)) in names


def find_any_row_construction(tree: ast.Module, row_names: set[str] | None = None) -> list[int]:
    """Line numbers of EVERY ORM Account row construction in the module."""
    names = orm_row_names(tree) if row_names is None else row_names
    if not names:
        return []
    return sorted(
        node.lineno for node in ast.walk(tree) if isinstance(node, ast.Call) and _is_row_construction(node, names)
    )


def find_row_constructions_outside_builder(
    tree: ast.Module, row_names: set[str] | None = None, builder: str = BUILDER
) -> list[int]:
    """Line numbers where an ORM Account row is constructed outside ``builder``."""
    names = orm_row_names(tree) if row_names is None else row_names
    if not names:
        return []

    inside_builder: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == builder:
            inside_builder.update(
                sub.lineno for sub in ast.walk(node) if isinstance(sub, ast.Call) and _is_row_construction(sub, names)
            )

    return sorted(
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _is_row_construction(node, names) and node.lineno not in inside_builder
    )


def test_the_orm_row_alias_is_still_resolvable_in_accounts_module():
    """The guard is only as good as the alias it resolves — pin that it found one.

    If accounts.py stopped importing the ORM model under a name this can see, the
    scan above would return an empty list and pass while checking nothing.
    """
    names = orm_row_names(parse_module(REPO_ROOT / TOOLS_MODULE))
    assert names, f"{TOOLS_MODULE} no longer imports {ORM_CLASS} from {ORM_MODULE} — the guard would be inert"


def test_the_orm_row_alias_is_still_resolvable_in_repository_module():
    names = orm_row_names(parse_module(REPO_ROOT / REPO_MODULE))
    assert names, f"{REPO_MODULE} no longer imports {ORM_CLASS} from {ORM_MODULE} — the guard would be inert"


def test_accounts_tools_module_never_constructs_a_row_directly():
    """accounts.py must route every Account row through AccountRepository.build_row —
    it must never construct one itself (the tools layer owns no ORM construction)."""
    path = REPO_ROOT / TOOLS_MODULE
    violations = find_any_row_construction(parse_module(path))
    assert not violations, (
        f"{TOOLS_MODULE} constructs an Account row directly at line(s) {violations}. "
        "Route it through AccountRepository.build_row(...) instead -- ORM row construction "
        "in the tools layer is the Pattern #3 violation this guard ratchets (#1721 M2)."
    )


def test_admin_blueprint_never_constructs_a_row_directly():
    """The admin create form must route through the repository like every other caller.

    It hand-built an Account with its own ``acc_{uuid}`` mint until #1721. A form
    that assembles its own row is a second answer to "what is an account row",
    and the two answers drift -- which is the whole reason this file exists.
    A module that does not import the ORM model at all trivially satisfies this
    (no names resolved -> nothing to construct), which is the desired end state.
    """
    violations = find_any_row_construction(parse_module(REPO_ROOT / ADMIN_MODULE))
    assert not violations, (
        f"{ADMIN_MODULE} constructs an Account row directly at line(s) {violations}. "
        "Route it through AccountRepository.build_row(...) + create() instead (#1721)."
    )


def test_account_repository_builds_its_row_in_one_place():
    path = REPO_ROOT / REPO_MODULE
    violations = find_row_constructions_outside_builder(parse_module(path))
    assert not violations, (
        f"{REPO_MODULE} constructs an Account row outside {BUILDER}() at line(s) {violations}. "
        "Every caller must describe the SAME row -- two field lists in one module is the "
        "drift this builder exists to prevent. Route it through the shared builder."
    )


def test_guard_catches_a_second_construction_site_in_the_repository():
    """Positive meta-test: a second builder anywhere in the repository module is a violation."""
    drifted = (
        "def build_row(**kw):\n"
        "    return Account(**kw)\n"
        "\n"
        "def _preview(entry):\n"
        "    return Account(tenant_id=entry.tenant_id, name=entry.name)\n"
    )
    assert find_row_constructions_outside_builder(ast.parse(drifted), {"Account"}), (
        "a construction site outside the builder must be flagged"
    )

    # The aliased form is the same defect wearing a different name.
    aliased = (
        "def build_row(**kw):\n"
        "    return DBAccount(**kw)\n"
        "\n"
        "def _preview(entry):\n"
        "    return models.DBAccount(tenant_id=entry.tenant_id)\n"
    )
    assert find_row_constructions_outside_builder(ast.parse(aliased), {"DBAccount"})


def test_guard_catches_any_construction_in_the_tools_module():
    """Positive meta-test: accounts.py must be flagged for ANY row construction, builder or not."""
    reintroduced = (
        "def _new_account_row(**kw):\n"
        "    return DBAccount(**kw)\n"
        "\n"
        "def _preview(entry):\n"
        "    return _new_account_row(t=entry.t)\n"
    )
    assert find_any_row_construction(ast.parse(reintroduced), {"DBAccount"}), (
        "any Account construction reintroduced into the tools module must be flagged, "
        "even inside a locally-named 'builder'"
    )


def test_guard_ignores_calls_that_are_not_row_construction():
    """Negative meta-test: routing through the repository method is not itself a violation."""
    routed = (
        "def _preview(entry, repo):\n"
        "    return AccountRepository.build_row(tenant_id=entry.t, account_id=entry.a)\n"
        "\n"
        "def _other(entry):\n"
        "    return AccountRepository(session, tenant_id='t')\n"
    )
    assert find_any_row_construction(ast.parse(routed), {"DBAccount"}) == []
