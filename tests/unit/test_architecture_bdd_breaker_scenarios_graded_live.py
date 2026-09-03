"""Guard: the two UC-004 circuit-breaker scenarios are graded on the live stack (GH #1802).

``@T-UC-004-webhook-circuit-open`` and ``@T-UC-004-webhook-circuit-recovery``
claim to grade the webhook circuit breaker over ``e2e_rest``. They grade a
dictionary in the test process: the Given steps seed ``_circuit_breakers``
through the harness's breaker write seam, and the Then steps read the same
object back. Under ``e2e_rest`` the server runs in its own container holding its
own ``WebhookDeliveryService``, so the server's breaker is never touched — and
``tests/bdd/conftest.py`` routes both tags through a NON-STRICT xfail, which
turns any outcome at all into a green run.

Two cushions — evidence the test process manufactured for itself, and an xfail
that forgives any outcome — and the scenarios cannot fail on the transport that
matters until both are gone. This guard is the standing check that they are:

1. Neither tag is xfail-routed on ``e2e_rest`` (the routing set in
   ``tests/bdd/conftest.py``, or the ``e2e_rest`` known-failures ledger).
2. No step bound to either scenario reaches the harness's breaker WRITE seam —
   the four methods that fake breaker state in the test process.
3. No step bound to either scenario READS breaker state out of the test process
   either. Under ``e2e_rest`` those accessors build a fresh in-process
   ``WebhookDeliveryService``, disconnected from the deployed server's breaker,
   so an assertion on what they return is an assertion about an object the
   scenario itself just created.

WHAT THIS GUARD DOES NOT PROVE. It grades the STRUCTURAL acceptance criteria of
GH #1802 ("neither scenario seeds ``_circuit_breakers`` in the test
process on any transport", and "both scenarios run on ``e2e_rest`` without an
xfail marker"), plus the read-side half of the same Core Invariant. It says
nothing about the behavioural criteria — that the DEPLOYED
server's breaker opens after real rejected deliveries and then suppresses real
ones. That is a cross-process property, and the only artifact that can grade it
is a bdd-in-network run in which both node ids appear as plain PASS:

* ``test_persistent_webhook_failures_open_circuit_breaker[e2e_rest]``
* ``test_circuit_breaker_closes_after_successful_recovery_probes[e2e_rest]``

And a plain PASS on those node ids is not the end of it either. Both scenarios
XPASSED on e2e_rest for years while grading nothing, so "it passes in-network"
is the claim under suspicion, not the answer to it. The anti-vacuity guarantee
is the mutation check — scripts/mutation-check-webhook-breaker.sh — which
deletes ``circuit_breaker.record_failure()`` from the server, rebuilds its
image, and requires the OPEN leg to REDDEN. Until that runs, nothing here or
in-network shows the breaker works.

A source scan is a substitute for none of that. It is the part of the claim that
is checkable without a Docker stack, and it fails the build the moment either
cushion comes back.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

# DEFERRED to prebid/salesagent#2060. Every check below encodes the TARGET state,
# and none of it holds yet: the scenarios still seed and read breaker state in the
# test process, and conftest still routes both tags through a non-strict xfail.
#
# strict=True is load-bearing and is the OPPOSITE of the defect this file exists to
# catch. A non-strict xfail reports a pass and a failure identically, which is how
# these scenarios stayed green for years while grading nothing. Strict fails the
# build the moment any of these starts passing, so #2060's rewrite cannot land
# without this guard turning green LOUDLY and being un-deferred in the same change.
pytestmark = pytest.mark.xfail(
    strict=True,
    reason=(
        "prebid/salesagent#2060: the circuit-breaker scenarios do not yet grade the "
        "deployed server. Deleting circuit_breaker.record_failure() leaves the e2e_rest "
        "leg passing (test-results/innet_260826_1216 vs _1221) — verify with "
        "`make mutation-check-breaker`, which must print PASSED before this is removed."
    ),
)

_TESTS_ROOT = Path(__file__).resolve().parents[1]
_BDD_ROOT = _TESTS_ROOT / "bdd"
_STEPS_DIR = _BDD_ROOT / "steps"
_CONFTEST = _BDD_ROOT / "conftest.py"
_KNOWN_FAILURES = _BDD_ROOT / "e2e_rest_known_failures.txt"
_FEATURE = _BDD_ROOT / "features" / "BR-UC-004-deliver-media-buy-metrics.feature"

_SCENARIO_TAGS = ("T-UC-004-webhook-circuit-open", "T-UC-004-webhook-circuit-recovery")

_E2E_XFAIL_ROUTING_SETS = ("_UC004_E2E_WEBHOOK_INTERNAL_TAGS",)

# The harness's breaker write seam, pinned by
# tests/unit/test_architecture_bdd_wire_discipline.py::_BREAKER_SEAM_METHODS.
# Reaching any of these from a step means the scenario ARRANGED breaker state
# rather than earning it from real delivery outcomes — which is precisely what
# no process outside the server can do to the server's own breaker.
_BREAKER_WRITE_SEAM = {
    "seed_breaker_failures",
    "set_breaker_state",
    "elapse_breaker_timeout",
    "drive_breaker_transition",
}

# The harness's breaker READ side. Each of these resolves breaker state inside
# the test process: ``get_service()`` constructs a fresh WebhookDeliveryService
# under e2e_rest, so everything read off it describes that fresh object rather
# than the server's. A Then that names one of these grades the wrong process.
_BREAKER_READ_ACCESSORS = {
    "get_breaker_state",
    "breaker_snapshot",
    "get_breaker",
    "assert_circuit_breaker_failure_recorded",
}

_STEP_DECORATORS = {"given", "when", "then"}
_GHERKIN_KEYWORD_RE = re.compile(r"^\s*(Given|When|Then|And|But)\s+(?P<text>.+?)\s*$")


def _scenario_step_texts(tag: str) -> list[str]:
    """The step texts of the scenario carrying ``tag``, keyword stripped."""
    lines = _FEATURE.read_text(encoding="utf-8").splitlines()
    starts = [i for i, line in enumerate(lines) if f"@{tag}" in line]
    assert len(starts) == 1, f"expected exactly one scenario tagged @{tag} in {_FEATURE.name}, found {len(starts)}"

    texts: list[str] = []
    for line in lines[starts[0] + 1 :]:
        stripped = line.strip()
        if stripped.startswith("@") or stripped.startswith("Scenario"):
            if texts:  # the next scenario begins
                break
            continue
        match = _GHERKIN_KEYWORD_RE.match(line)
        if match:
            texts.append(match.group("text"))
    assert texts, f"scenario @{tag} has no steps"
    return texts


def _decorator_templates(func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """Every step-text template this function is registered under."""
    templates: list[str] = []
    for dec in func.decorator_list:
        if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name) and dec.func.id in _STEP_DECORATORS):
            continue
        if not dec.args:
            continue
        arg = dec.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            templates.append(arg.value)
        elif isinstance(arg, ast.Call) and arg.args:
            inner = arg.args[0]
            if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                templates.append(inner.value)
    return templates


def _template_to_regex(template: str) -> re.Pattern[str]:
    """Turn a ``parsers.parse`` template into a matcher for a concrete Gherkin line.

    ``{name:d}`` matches digits, ``{name}`` matches anything non-greedy, and a
    plain string matches itself. A ``parsers.re`` pattern is already a regex, but
    escaping it here would only ever make it match less, never more — and this
    guard errs towards resolving MORE steps, since an unresolved step is a step
    whose breaker access goes unseen.
    """
    parts: list[str] = []
    cursor = 0
    for placeholder in re.finditer(r"\{(\w+)(?::[^}]+)?\}", template):
        parts.append(re.escape(template[cursor : placeholder.start()]))
        parts.append(r"\d+" if ":d" in placeholder.group(0) else r".+?")
        cursor = placeholder.end()
    parts.append(re.escape(template[cursor:]))
    return re.compile(rf"^{''.join(parts)}$")


def _step_functions_by_template() -> list[tuple[re.Pattern[str], str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    """Every registered step definition as (matcher, ``path::name``, node)."""
    registered = []
    for py_file in sorted(_STEPS_DIR.rglob("*.py")):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for template in _decorator_templates(node):
                key = f"{py_file.relative_to(_TESTS_ROOT.parent)}::{node.name}"
                registered.append((_template_to_regex(template), key, node))
    return registered


def _breaker_members_in(func: ast.FunctionDef | ast.AsyncFunctionDef, members: set[str]) -> set[str]:
    return {node.attr for node in ast.walk(func) if isinstance(node, ast.Attribute) and node.attr in members}


def _steps_touching(tag: str, members: set[str]) -> dict[str, tuple[str, set[str]]]:
    """``path::name`` -> (step text, members touched) for every step of ``tag`` that touches ``members``."""
    registered = _step_functions_by_template()
    touched: dict[str, tuple[str, set[str]]] = {}
    unresolved: list[str] = []

    for step_text in _scenario_step_texts(tag):
        matches = [(key, node) for matcher, key, node in registered if matcher.match(step_text)]
        if not matches:
            unresolved.append(step_text)
            continue
        for key, node in matches:
            hits = _breaker_members_in(node, members)
            if hits:
                touched[key] = (step_text, hits)

    assert not unresolved, (
        f"@{tag} has steps this guard could not resolve to a step definition: {unresolved}. "
        "An unresolved step is an unchecked step — fix the resolver or the step text before trusting a pass."
    )
    return touched


@pytest.mark.parametrize("tag", _SCENARIO_TAGS)
def test_scenario_is_not_xfail_routed_on_e2e_rest(tag: str) -> None:
    """A non-strict xfail makes every outcome green, so nothing about the server is graded."""
    tree = ast.parse(_CONFTEST.read_text(encoding="utf-8"), filename=str(_CONFTEST))
    routed: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        names = {t.id for t in targets if isinstance(t, ast.Name)}
        if not (names & set(_E2E_XFAIL_ROUTING_SETS)):
            continue
        for element in ast.walk(node):
            if isinstance(element, ast.Constant) and element.value == tag:
                routed.extend(names)

    assert not routed, (
        f"@{tag} is still routed through {sorted(set(routed))} in {_CONFTEST.name}, which marks its "
        "e2e_rest leg xfail(strict=False). Under that marker a pass reports XPASS and a failure reports "
        "xfail, so the deployed server's circuit breaker is not graded either way. Remove the tag once "
        "the scenario is driven through the live stack (GH #1802)."
    )

    if _KNOWN_FAILURES.exists():
        ledger = _KNOWN_FAILURES.read_text(encoding="utf-8")
        assert tag not in ledger, f"@{tag} is listed in {_KNOWN_FAILURES.name}, which excuses its e2e_rest leg."


@pytest.mark.parametrize("tag", _SCENARIO_TAGS)
def test_scenario_never_seeds_breaker_state_in_the_test_process(tag: str) -> None:
    """Breaker state the test process wrote is evidence about the test process, on every transport."""
    violations = _steps_touching(tag, _BREAKER_WRITE_SEAM)

    assert not violations, (
        f"@{tag} arranges circuit-breaker state in the test process:\n"
        + "\n".join(f"  {key} — {text!r} calls {sorted(writes)}" for key, (text, writes) in sorted(violations.items()))
        + "\nThe server owns its breaker. A step that seeds one in the test process proves a Python "
        "dictionary works and says nothing about the deployed system. Open the breaker with real rejected "
        "deliveries against the webhook-capture service instead (GH #1802)."
    )


@pytest.mark.parametrize("tag", _SCENARIO_TAGS)
def test_scenario_never_reads_breaker_state_from_the_test_process(tag: str) -> None:
    """Reading a fresh in-process breaker under e2e_rest describes the reader, not the server.

    This is the read-side half of the same Core Invariant: a scenario may assert
    only on evidence produced by the process that owns the behaviour. It is
    scoped to these two scenarios, so a shared step that other scenarios still
    need is satisfied by rewording THIS scenario onto its own observable, not by
    deleting the step other scenarios depend on.
    """
    violations = _steps_touching(tag, _BREAKER_READ_ACCESSORS)

    assert not violations, (
        f"@{tag} asserts on breaker state resolved inside the test process:\n"
        + "\n".join(f"  {key} — {text!r} calls {sorted(reads)}" for key, (text, reads) in sorted(violations.items()))
        + "\nUnder e2e_rest these accessors build a fresh WebhookDeliveryService in the test process, so "
        "the state they return was never the deployed server's. Grade the observable the scenario names "
        "instead — what the webhook-capture service received, and the webhook_delivery_log rows the server "
        "wrote (GH #1802)."
    )
