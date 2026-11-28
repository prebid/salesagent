"""Guard: the harness's default tenant carries the rows a production tenant carries.

WHY THIS GUARD AND NOT A SCENARIO. Both halves of ``salesagent-8mynh`` were one defect
wearing two faces: the in-process environment supplied tenant state production cannot be in,
so three transports graded the FIXTURE and only ``e2e_rest`` graded the seller. Six rows came
from a format id the pinned creative agent does not serve; two came from a missing
``adapter_config`` row, which made ``get_adapter`` take a different branch for a sandbox
account than for any other. A scenario cannot catch that class — it asserts about the seller,
and the fixture is what differs. This asserts about the fixture.

DERIVED, NOT DECLARED. The expected row set is read out of the PRODUCTION creation paths by
walking their ASTs for ORM model constructions, so adding a table to a signup path makes this
guard demand it of the fixture automatically. A hand-maintained tuple of table names here
would be a second declaration of what production creates, free to drift from it — which is
the failure mode the repo's own ``_NO_E2E_REST_TAGS`` and dimension-map incidents both had.

WHAT IT DOES NOT CLAIM. Not that the fixture equals production: a test tenant legitimately
has fewer rows (no ``User``, no ``AuthorizedProperty`` on every path) and some paths create
rows only under a branch. The assertion is the weaker, checkable one -- that the fixture's row
set is not MISSING a model every production path creates. One model, today
(``AdapterConfig``), and the point is that the NEXT one is caught without anybody remembering
this file exists.
"""

from __future__ import annotations

import ast
import importlib

import factory

from tests.unit._architecture_helpers import (
    iter_scopes,
    orm_classes,
    orm_references,
    parse_module,
    query_bindings,
    repo_root,
    scope_nodes,
)

#: Where the factories live: a callee imported from under it may be a factory_boy factory.
_FACTORIES_PACKAGE = "tests.factories"

#: The production paths that CREATE a tenant. Each is read for what else it creates alongside.
#: These are paths, not a row list -- the rows are derived from them below.
_TENANT_CREATION_PATHS = (
    "src/admin/blueprints/public.py",
    "src/admin/tenant_management_api.py",
    "scripts/setup/setup_tenant.py",
    "scripts/setup/init_database.py",
)

#: Where the harness builds the tenant every in-process scenario runs against.
_FIXTURE_PATH = "tests/harness/_base.py"

#: Models a test tenant is not expected to carry. Each is a row production creates for a
#: reason a scenario does not need, and each is named rather than pattern-matched so adding
#: one is a decision somebody records.
_NOT_REQUIRED_OF_A_FIXTURE = frozenset(
    {
        "User",  # an operator login; no scenario authenticates as one
        "AuthorizedProperty",  # seeded per-scenario by the envs that assert on properties
        "CurrencyLimit",  # seeded by the envs whose scenarios price in a currency
        "PropertyTag",  # same
        "Product",  # every env seeds its own products
        "Principal",  # seeded by setup_default_data itself, under its own name
        "Tenant",  # the subject, not a companion row
    }
)


def _factory_model(target: str | None) -> str | None:
    """The mapped class the factory_boy factory at *target* (a dotted import) declares as its
    ``Meta.model``, when *target* is imported from :data:`_FACTORIES_PACKAGE`; else None."""
    if target is None or not target.startswith(f"{_FACTORIES_PACKAGE}."):
        return None
    module, _, name = target.rpartition(".")
    built = getattr(importlib.import_module(module), name, None)
    if not (isinstance(built, type) and issubclass(built, factory.Factory)):
        return None
    model = built._meta.model
    return model.__name__ if orm_classes().get(model.__name__) is model else None


def _models_constructed_in_tree(tree: ast.Module, relative_path: str) -> set[str]:
    """Every ORM MODEL *tree* constructs: by calling it, or a factory that declares it.

    A row is a call whose callee denotes a mapped class as the call's own scope binds it
    (``orm_references``), or a call to a factory_boy factory that scope imports, which builds
    the model the factory declares (``class Meta: model = ...``). The first version of this
    guard counted any CapWords call and flagged ``PrincipalRepository`` -- a repository, not a
    row -- and the next stripped a ``Factory`` suffix off a callee's spelling. A name's
    spelling cannot tell a model from anything else that happens to be capitalised, so the
    class comes from the import and the mapper registry, and nothing else is a row.

    Walked scope by scope (``iter_scopes``), not with ``iter_call_expressions``, because a
    factory callee resolves through the imports of the scope that calls it.
    """
    references = orm_references(relative_path, tree)
    built: set[str] = set()
    for scope, view, _, _ in iter_scopes(tree, query_bindings(relative_path, tree)):
        for node in scope_nodes(scope):
            if not isinstance(node, ast.Call):
                continue
            model = references.get(id(node.func))
            if model is None and isinstance(node.func, ast.Name):
                model = _factory_model(view.imports.get(node.func.id))
            if model is not None:
                built.add(model)
    return built


def _models_constructed_in(relative_path: str) -> set[str]:
    """Every ORM MODEL constructed in the module at *relative_path*; see :func:`_models_constructed_in_tree`."""
    return _models_constructed_in_tree(parse_module(repo_root() / relative_path), relative_path)


def test_the_fixture_tenant_is_not_missing_a_row_every_production_path_creates() -> None:
    """Every model created by ALL tenant-creation paths must be created by the fixture too.

    The intersection across production paths, not the union: a model only one path creates is
    conditional on that path's options, and demanding it of the fixture would be demanding
    more than production guarantees. A model EVERY path creates is one a tenant always has.
    """
    per_path = [_models_constructed_in(p) for p in _TENANT_CREATION_PATHS]
    always_created = set.intersection(*per_path) - _NOT_REQUIRED_OF_A_FIXTURE
    fixture_creates = _models_constructed_in(_FIXTURE_PATH)

    missing = always_created - fixture_creates
    assert not missing, (
        f"{_FIXTURE_PATH} builds a tenant without {sorted(missing)}, which every production "
        f"tenant-creation path creates ({', '.join(_TENANT_CREATION_PATHS)}). An in-process "
        f"scenario would then grade a tenant production cannot be in, and only e2e_rest would "
        f"see the difference — which is how salesagent-8mynh's eight rows failed on one "
        f"transport while passing on three. Seed the row in setup_default_data, or add the "
        f"model to _NOT_REQUIRED_OF_A_FIXTURE with the reason it is not needed."
    )


def test_the_derivation_has_a_non_empty_population() -> None:
    """The guard must be asserting about something.

    Without this, a parsing change that made ``_models_constructed_in`` return empty sets
    would leave the assertion above vacuously true — the failure mode of every derived guard,
    and the one the repo has been bitten by (16 architecture guards green on an empty scan).
    """
    per_path = [_models_constructed_in(p) for p in _TENANT_CREATION_PATHS]
    for path, models in zip(_TENANT_CREATION_PATHS, per_path, strict=True):
        assert models, f"no model constructions parsed out of {path}; the derivation is broken"

    always_created = set.intersection(*per_path) - _NOT_REQUIRED_OF_A_FIXTURE
    assert "AdapterConfig" in always_created, (
        "AdapterConfig is the row whose absence produced salesagent-8mynh's two account rows; "
        "if the derivation no longer finds it across every production path, this guard has "
        "stopped covering the case it was written for"
    )


def test_the_derivation_reads_the_model_a_call_builds_not_its_spelling() -> None:
    """The class comes from the import and the factory's declared ``Meta.model``: a parameter
    named like a model builds no row, and a factory builds its model whatever it is called."""

    def built(source: str) -> set[str]:
        return _models_constructed_in_tree(ast.parse(source), "tests/harness/x.py")

    assert (
        built("from src.core.database.models import AdapterConfig\ndef f(AdapterConfig):\n    AdapterConfig()\n")
        == set()
    )
    assert built("from tests.factories import AdapterConfigFactory as Seed\ndef f(t):\n    Seed(tenant=t)\n") == {
        "AdapterConfig"
    }
    assert built(
        "def f(t):\n    from tests.factories import TenantFactory as AdapterConfigFactory\n    AdapterConfigFactory()\n"
    ) == {"Tenant"}
