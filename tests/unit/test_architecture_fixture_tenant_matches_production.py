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
from pathlib import Path

from tests.unit._architecture_helpers import called_function_names

_REPO_ROOT = Path(__file__).resolve().parents[2]

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


def _orm_model_names() -> frozenset[str]:
    """Every mapped ORM class, asked of the declarative registry rather than listed.

    The first version of this guard counted any CapWords call and flagged
    ``PrincipalRepository`` -- a repository, not a row. Pattern-matching the SPELLING of a
    name cannot tell a model from anything else that happens to be capitalised, so the
    population comes from the mapper registry: whatever is mapped to a table is a row, and
    nothing else is.
    """
    from src.core.database.models import Base

    return frozenset(mapper.class_.__name__ for mapper in Base.registry.mappers)


_ORM_MODELS = _orm_model_names()


def _models_constructed_in(relative_path: str) -> set[str]:
    """Every ORM MODEL constructed in *relative_path*, from its AST.

    Through ``_architecture_helpers.called_function_names``, which already yields every
    callable name in a tree -- bare and dotted alike -- rather than a local ``ast.walk`` over
    ``Call`` nodes. A hand-rolled version of that loop stood here and
    ``test_architecture_no_handrolled_call_walk`` refused it, correctly: two guards had grown
    the same loop independently before, which is why that guard exists.

    A trailing ``Factory`` is stripped so a fixture building ``AdapterConfigFactory(...)`` is
    recognised as creating an ``AdapterConfig`` row, and the result is intersected with
    :data:`_ORM_MODELS` so only real mapped classes contribute.
    """
    tree = ast.parse((_REPO_ROOT / relative_path).read_text(), filename=relative_path)
    called = called_function_names(tree)
    return {name.removesuffix("Factory") for name in called if name.removesuffix("Factory") in _ORM_MODELS}


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
