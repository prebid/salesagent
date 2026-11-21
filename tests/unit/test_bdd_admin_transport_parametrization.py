"""Collection guard: admin BDD scenarios are graded on BOTH admin transports.

Core invariant: a BDD scenario's transport is chosen by the
parametrization at collection time and passed down through the harness env —
never inferred inside the env from ambient environment variables — so the
feature file's declared transports and the transports that actually grade the
scenario are the same set.

BR-ADMIN-ACCOUNTS.feature declares two transports in its own header
("integration: Flask test_client", "e2e: requests.Session against Docker
stack") and AdminAccountEnv implements both, but ``pytest_generate_tests``
returns early for ``T-ADMIN-*`` so ``ctx`` is never parametrized: all 13
scenarios grade one transport and the feature's claim of two is not true.

BR-ADMIN-TENANT-SCOPING.feature (#2203) had the next half of the same defect:
its ten scenarios were collected under ``e2e_admin``, but their env builder
ignored ``e2e_config`` and always returned the in-process env, so a run over the
stack reported Flask test_client results under an e2e node id.

Everything below runs on the host with no Docker stack — the e2e leg's
*parametrization* is decided at collection time by ``BDD_E2E_ENABLED`` alone
(the stack is only needed later, when the ``ctx`` fixture resolves
``e2e_stack``), and a route's builder is checked on the env it constructs, which
is never entered.

  1. ``pytest_generate_tests`` must parametrize every admin scenario (every
     feature carrying a ``T-ADMIN-`` tag) over ``admin_integration`` (always)
     plus ``e2e_admin`` (when ``BDD_E2E_ENABLED=true``) — the same condition the
     AdCP branch uses for ``e2e_rest``.
  2. Every ``ENV_ROUTES`` row an admin scenario resolves to, and that builds an
     ``AdminAccountEnv``, must build the env the parametrized transport names:
     the live-stack env at ``e2e_config.base_url`` when handed an ``e2e_config``,
     the in-process env when handed ``None``. The builder is taken from the row,
     so a row that names a builder of its own is graded like the rest.
  3. ``_harness_env`` must never pin a branch's DB scope with a hardcoded
     ``getfixturevalue("integration_db")``. ``_db_scope_for`` is the single
     primitive that turns the parametrized transport into a DB scope (per-test
     DB in process, live server DB over e2e); a branch that calls
     ``getfixturevalue`` directly points production at an empty database while
     the env writes to the server's.

The hook is driven directly through a stub metafunc that records
``parametrize()`` calls — the same "drive the REAL hook through a minimal stub"
shape as tests/unit/test_bdd_e2e_enabled_xdist_guard.py.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.audit import storyboard_spec
from tests.bdd.conftest import ENV_ROUTES, pytest_generate_tests
from tests.harness.admin_accounts import AdminAccountEnv

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FEATURES_DIR = _REPO_ROOT / "tests" / "bdd" / "features"
# Admin features are found by tag, the way pytest_generate_tests finds admin
# scenarios, not by file name, so a new admin feature is graded the day it is
# authored.
_ADMIN_FEATURES = sorted(
    p.name for p in _FEATURES_DIR.glob("*.feature") if "@T-ADMIN-" in p.read_text(encoding="utf-8")
)
_BDD_CONFTEST = _REPO_ROOT / "tests" / "bdd" / "conftest.py"

# The two admin transports the feature file itself declares. Ids are the pytest
# param ids that must appear in the node id of every admin scenario.
_INTEGRATION_ID = "admin_integration"
_E2E_ID = "e2e_admin"


def _admin_scenario_tag_sets(feature: str) -> list[tuple[str, frozenset[str]]]:
    """Return (scenario tag, marker names) for every T-ADMIN-* scenario in *feature*.

    Read from the feature file rather than hardcoded, so a new scenario is
    graded by this guard the day it is authored.
    """
    tag_lines = re.findall(
        r"^\s*(@T-ADMIN-[\w@\- ]+)$", (_FEATURES_DIR / feature).read_text(encoding="utf-8"), re.MULTILINE
    )
    scenarios = []
    for line in tag_lines:
        names = frozenset(tag.lstrip("@") for tag in line.split())
        scenario_tag = next(t for t in sorted(names) if t.startswith("T-ADMIN-"))
        scenarios.append((scenario_tag, names))
    return scenarios


_ADMIN_SCENARIOS = [scenario for feature in _ADMIN_FEATURES for scenario in _admin_scenario_tag_sets(feature)]


def _declared_scenario_count(feature: str) -> int:
    """How many scenarios *feature* declares, counted from its ``Scenario:`` / ``Scenario Outline:`` lines."""
    text = (_FEATURES_DIR / feature).read_text(encoding="utf-8")
    return len(re.findall(r"^\s*Scenario(?: Outline)?:", text, re.MULTILINE))


def _admin_routes() -> list:
    """Every ``ENV_ROUTES`` row an admin scenario resolves to, once each.

    Resolved through the same ``storyboard_spec.resolve_env_route`` call
    ``_harness_env`` makes, so a future admin feature's own row is graded as
    soon as the feature exists.
    """
    routes: dict[str, object] = {}
    for scenario_tag, marker_names in _ADMIN_SCENARIOS:
        route = storyboard_spec.resolve_env_route(marker_names, ENV_ROUTES)
        assert route is not None, f"{scenario_tag}: no ENV_ROUTES row claims this admin scenario"
        routes.setdefault(route.tag, route)
    return list(routes.values())


class _StubMetafunc:
    """Minimal metafunc that records ``parametrize()`` calls.

    ``pytest_generate_tests`` reads only ``fixturenames`` and
    ``definition.iter_markers()``, so this is the whole surface it needs.
    """

    def __init__(self, marker_names: frozenset[str]) -> None:
        self.fixturenames = ["ctx", "request"]
        self.definition = SimpleNamespace(iter_markers=lambda: [SimpleNamespace(name=n) for n in sorted(marker_names)])
        self.calls: list[SimpleNamespace] = []

    def parametrize(self, argnames, argvalues, ids=None, indirect=False, **kwargs):
        self.calls.append(
            SimpleNamespace(
                argnames=argnames,
                argvalues=list(argvalues),
                ids=list(ids) if ids is not None else None,
                indirect=indirect,
            )
        )


def _run_hook(marker_names: frozenset[str]) -> _StubMetafunc:
    metafunc = _StubMetafunc(marker_names)
    pytest_generate_tests(metafunc)  # type: ignore[arg-type]
    return metafunc


def _sole_call(metafunc: _StubMetafunc, scenario_tag: str) -> SimpleNamespace:
    assert len(metafunc.calls) == 1, (
        f"{scenario_tag}: expected exactly one ctx parametrization at collection, "
        f"got {len(metafunc.calls)}. Zero means pytest_generate_tests returned early for "
        f"T-ADMIN-* and the scenario's transport is decided inside the harness instead of "
        f"at collection time."
    )
    call = metafunc.calls[0]
    assert call.argnames == "ctx", f"{scenario_tag}: must parametrize 'ctx', got {call.argnames!r}"
    assert call.indirect is True, (
        f"{scenario_tag}: ctx must be parametrized indirect=True so the ctx fixture stashes "
        f"transport/e2e_config for the harness"
    )
    return call


def test_the_tag_regex_sees_every_scenario_of_every_admin_feature() -> None:
    """Anchor: the guards below cover each admin feature's scenarios, counted from the file itself.

    A scenario whose tag line the regex does not match would be graded by nothing here. The
    feature's own ``Scenario`` lines say how many there are, so a miss shows as a difference.
    """
    assert _ADMIN_FEATURES, "no feature file carries a @T-ADMIN- tag"
    found = {feature: len(_admin_scenario_tag_sets(feature)) for feature in _ADMIN_FEATURES}
    declared = {feature: _declared_scenario_count(feature) for feature in _ADMIN_FEATURES}

    assert found == declared, f"T-ADMIN-* scenarios the regex found {found} vs scenarios declared {declared}"


@pytest.mark.parametrize(("scenario_tag", "marker_names"), _ADMIN_SCENARIOS, ids=[s[0] for s in _ADMIN_SCENARIOS])
def test_admin_scenario_parametrizes_integration_transport(
    scenario_tag: str, marker_names: frozenset[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without the e2e stack requested, an admin scenario grades admin_integration."""
    monkeypatch.delenv("BDD_E2E_ENABLED", raising=False)

    call = _sole_call(_run_hook(marker_names), scenario_tag)

    assert call.ids == [_INTEGRATION_ID], f"{scenario_tag}: expected ids [{_INTEGRATION_ID!r}], got {call.ids!r}"


@pytest.mark.parametrize(("scenario_tag", "marker_names"), _ADMIN_SCENARIOS, ids=[s[0] for s in _ADMIN_SCENARIOS])
def test_admin_scenario_parametrizes_both_transports_when_e2e_enabled(
    scenario_tag: str, marker_names: frozenset[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """With BDD_E2E_ENABLED=true, the same scenario also grades e2e_admin.

    This is the condition the AdCP branch uses for e2e_rest, and it is decided at
    collection — no live stack is needed to assert it.
    """
    monkeypatch.setenv("BDD_E2E_ENABLED", "true")

    call = _sole_call(_run_hook(marker_names), scenario_tag)

    assert call.ids == [_INTEGRATION_ID, _E2E_ID], (
        f"{scenario_tag}: with BDD_E2E_ENABLED=true the feature's two declared transports must "
        f"both be collected; expected ids [{_INTEGRATION_ID!r}, {_E2E_ID!r}], got {call.ids!r}"
    )


def test_admin_transport_values_satisfy_the_ctx_fixture_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    """The parametrized values must carry the properties downstream code keys on.

    The ctx fixture stashes ``e2e_config`` (and hard-errors on an unreachable
    stack) for any param whose ``.value`` starts with ``e2e_``; ``is_e2e()``
    keys on the same prefix. The integration member must NOT match, or every
    in-process admin run would demand a live stack.

    They must also stay out of the AdCP ``Transport`` enum: every ``Transport``
    member maps to an AdCP ResolvedIdentity protocol, which an HTML admin
    surface does not have.
    """
    from tests.harness.transport import Transport

    monkeypatch.setenv("BDD_E2E_ENABLED", "true")
    scenario_tag, marker_names = _ADMIN_SCENARIOS[0]

    call = _sole_call(_run_hook(marker_names), scenario_tag)
    integration, e2e = call.argvalues

    assert not str(getattr(integration, "value", integration)).startswith("e2e_"), (
        f"{integration!r} would be treated as an e2e transport by the ctx fixture"
    )
    assert str(getattr(e2e, "value", e2e)).startswith("e2e_"), (
        f"the admin e2e transport value must start with 'e2e_' so the ctx fixture stashes "
        f"e2e_config and hard-errors on a missing stack; got {getattr(e2e, 'value', e2e)!r}"
    )
    for value in (integration, e2e):
        assert not isinstance(value, Transport), (
            f"{value!r} must not be an AdCP Transport member — the admin UI is an HTML form "
            f"surface with no ResolvedIdentity protocol"
        )


def test_admin_rows_build_the_env_their_transport_names() -> None:
    """Handed an ``e2e_config`` a row's env drives the stack at its address; handed ``None`` it runs in process.

    An in-process env under an ``e2e_config`` is the defect this guards: the node id says
    ``e2e_admin``, the stack is up, and the scenario runs on the Flask test_client. The
    builder comes from the ``ENV_ROUTES`` row each admin scenario resolves to. A row that
    builds an env of another family (an ``IntegrationEnv`` taking ``e2e_config`` whole) has
    no mode to check and is left to that family's own tests.
    """
    e2e_config = SimpleNamespace(base_url="http://stack-under-test:8080")
    graded = []
    for route in _admin_routes():
        in_process = route.env_builder(None)
        if not isinstance(in_process, AdminAccountEnv):
            continue
        on_the_stack = route.env_builder(e2e_config)
        assert in_process.mode == "integration", f"{route.tag}: without an e2e_config the env must run in process"
        assert (on_the_stack.mode, on_the_stack._base_url) == ("e2e", e2e_config.base_url), (
            f"{route.tag}: handed an e2e_config, the builder built a {on_the_stack.mode!r} env at "
            f"{on_the_stack._base_url!r}, so every e2e_admin node of this row runs somewhere else"
        )
        graded.append(route.tag)

    assert graded, "no admin scenario resolves to a row that builds an AdminAccountEnv"


def _pinned_db_scope_calls() -> list[int]:
    """Line numbers of ``getfixturevalue("integration_db")`` inside ``_harness_env``."""
    tree = ast.parse(_BDD_CONFTEST.read_text(encoding="utf-8"), filename=str(_BDD_CONFTEST))
    harness_env = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "_harness_env"
    )
    return [
        node.lineno
        for node in ast.walk(harness_env)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "getfixturevalue"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "integration_db"
    ]


def test_harness_env_never_pins_its_db_scope() -> None:
    """Every ``_harness_env`` branch must derive its DB scope from the transport.

    ``_db_scope_for(request, e2e_config)`` is that derivation: ``integration_db``
    in process, ``production_db_pointed_at(e2e_config.postgres_url)``
    (``tests/utils/database_helpers.py``) over e2e.
    A branch calling ``getfixturevalue("integration_db")`` directly hardcodes the
    in-process answer, so over an e2e transport production reads an empty
    per-test DB while the env writes to the live server DB.
    """
    pinned = _pinned_db_scope_calls()

    assert pinned == [], (
        f"tests/bdd/conftest.py:{pinned} pin the DB scope inside _harness_env with "
        f'getfixturevalue("integration_db"). Route these branches through '
        f"_db_scope_for(request, e2e_config) like the UC-011/UC-002/COMPAT branches do."
    )
