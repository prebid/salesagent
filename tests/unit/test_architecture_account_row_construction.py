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

from tests.unit._architecture_helpers import (
    ORM_MODELS_MODULE,
    REPO_ROOT,
    iter_call_expressions,
    orm_references,
    parse_module,
    query_bindings,
)

TOOLS_MODULE = "src/core/tools/accounts.py"
ADMIN_MODULE = "src/admin/blueprints/accounts.py"
REPO_MODULE = "src/core/database/repositories/account.py"
BUILDER = "build_row"
ORM_CLASS = "Account"


def orm_row_names(relpath: str, tree: ast.Module) -> set[str]:
    """The local name(s) the module scope binds to the ORM ``Account`` model (``query_bindings``).

    Resolved from the module's own imports rather than hardcoded, because the
    Pydantic SCHEMA is also called ``Account`` in some modules and constructing
    it is not a row construction at all. ``accounts.py`` imports the ORM model
    as ``DBAccount`` precisely to keep the two apart; reading the alias means
    the guard follows a rename instead of silently going blind or crying wolf.
    A relative import resolves too.
    """
    return {local for local, original in query_bindings(relpath, tree).orm.items() if original == ORM_CLASS}


def _row_constructions(tree: ast.Module, relpath: str) -> list[ast.Call]:
    """Every call whose callee is the ORM ``Account`` as the call's own scope binds it (``orm_references``)."""
    references = orm_references(relpath, tree)
    return [call for call in iter_call_expressions(tree) if references.get(id(call.func)) == ORM_CLASS]


def find_any_row_construction(tree: ast.Module, relpath: str) -> list[int]:
    """Line numbers of EVERY ORM Account row construction in the module."""
    return sorted(call.lineno for call in _row_constructions(tree, relpath))


def find_row_constructions_outside_builder(tree: ast.Module, relpath: str, builder: str = BUILDER) -> list[int]:
    """Line numbers where an ORM Account row is constructed outside ``builder``."""
    inside_builder = {
        id(node)
        for function in ast.walk(tree)
        if isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef) and function.name == builder
        for node in ast.walk(function)
    }
    return sorted(call.lineno for call in _row_constructions(tree, relpath) if id(call) not in inside_builder)


def _tree(relpath: str) -> ast.Module:
    return parse_module(REPO_ROOT / relpath)


def test_the_orm_row_alias_is_still_resolvable_in_accounts_module():
    """The guard is only as good as the alias it resolves — pin the one it finds.

    If accounts.py stopped importing the ORM model under a name this can see, the
    scan above would return an empty list and pass while checking nothing.
    """
    assert orm_row_names(TOOLS_MODULE, _tree(TOOLS_MODULE)) == {"DBAccount"}, (
        f"{TOOLS_MODULE} no longer imports {ORM_CLASS} from {ORM_MODELS_MODULE} as DBAccount — the guard would be inert"
    )


def test_the_orm_row_alias_is_still_resolvable_in_repository_module():
    assert orm_row_names(REPO_MODULE, _tree(REPO_MODULE)) == {ORM_CLASS}, (
        f"{REPO_MODULE} no longer imports {ORM_CLASS} from {ORM_MODELS_MODULE} — the guard would be inert"
    )


def test_accounts_tools_module_never_constructs_a_row_directly():
    """accounts.py must route every Account row through AccountRepository.build_row —
    it must never construct one itself (the tools layer owns no ORM construction)."""
    violations = find_any_row_construction(_tree(TOOLS_MODULE), TOOLS_MODULE)
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
    violations = find_any_row_construction(_tree(ADMIN_MODULE), ADMIN_MODULE)
    assert not violations, (
        f"{ADMIN_MODULE} constructs an Account row directly at line(s) {violations}. "
        "Route it through AccountRepository.build_row(...) + create() instead (#1721)."
    )


def test_account_repository_builds_its_row_in_one_place():
    violations = find_row_constructions_outside_builder(_tree(REPO_MODULE), REPO_MODULE)
    assert not violations, (
        f"{REPO_MODULE} constructs an Account row outside {BUILDER}() at line(s) {violations}. "
        "Every caller must describe the SAME row -- two field lists in one module is the "
        "drift this builder exists to prevent. Route it through the shared builder."
    )


#: Where the meta-tests' synthetic modules live: under the repositories package, so a
#: relative import resolves the way it would in the real repository module.
_META_PATH = "src/core/database/repositories/x.py"


def test_guard_catches_a_second_construction_site_in_the_repository():
    """Positive meta-test: a second builder anywhere in the repository module is a violation."""
    drifted = (
        "from src.core.database.models import Account\n"
        "def build_row(**kw):\n"
        "    return Account(**kw)\n"
        "\n"
        "def _preview(entry):\n"
        "    return Account(tenant_id=entry.tenant_id, name=entry.name)\n"
    )
    assert find_row_constructions_outside_builder(ast.parse(drifted), _META_PATH) == [6], (
        "a construction site outside the builder must be flagged"
    )

    # The aliased form is the same defect wearing a different name, and so is the
    # construction through the models module.
    aliased = (
        "from src.core.database import models\n"
        "from src.core.database.models import Account as DBAccount\n"
        "def build_row(**kw):\n"
        "    return DBAccount(**kw)\n"
        "\n"
        "def _preview(entry):\n"
        "    return models.Account(tenant_id=entry.tenant_id)\n"
    )
    assert find_row_constructions_outside_builder(ast.parse(aliased), _META_PATH) == [7]


def test_guard_catches_any_construction_in_the_tools_module():
    """Positive meta-test: accounts.py must be flagged for ANY row construction, builder or not."""
    reintroduced = (
        "from src.core.database.models import Account as DBAccount\n"
        "def _new_account_row(**kw):\n"
        "    return DBAccount(**kw)\n"
        "\n"
        "def _preview(entry):\n"
        "    return _new_account_row(t=entry.t)\n"
    )
    assert find_any_row_construction(ast.parse(reintroduced), TOOLS_MODULE) == [3], (
        "any Account construction reintroduced into the tools module must be flagged, "
        "even inside a locally-named 'builder'"
    )


def test_guard_ignores_calls_that_are_not_row_construction():
    """Negative meta-test: routing through the repository method is not itself a violation,
    and neither is the Pydantic ``Account``, a parameter that shadows the model, or
    another object's attribute that happens to share the alias."""
    routed = (
        "from src.core.database.models import Account as DBAccount\n"
        "from src.core.schemas import Account\n"
        "def _preview(entry, repo):\n"
        "    return AccountRepository.build_row(tenant_id=entry.t, account_id=entry.a)\n"
        "\n"
        "def _other(entry):\n"
        "    return AccountRepository(session, tenant_id='t'), Account(account_id=entry.a)\n"
        "\n"
        "def _shadowed(DBAccount, x):\n"
        "    return DBAccount(tenant_id='t'), x.DBAccount(tenant_id='t')\n"
    )
    tree = ast.parse(routed)
    assert orm_row_names(TOOLS_MODULE, tree) == {"DBAccount"}
    assert find_any_row_construction(tree, TOOLS_MODULE) == []


def test_a_relative_import_of_the_model_resolves():
    """``from ..models import Account`` inside the repositories package is the ORM model too."""
    relative = "from ..models import Account\ndef _preview(entry):\n    return Account(tenant_id=entry.t)\n"
    tree = ast.parse(relative)
    assert orm_row_names(_META_PATH, tree) == {ORM_CLASS}
    assert find_row_constructions_outside_builder(tree, _META_PATH) == [3]
