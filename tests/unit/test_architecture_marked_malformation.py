"""Guard: a deliberately-malformed test payload is DECLARED, and the declaration is auditable.

Two mechanisms grade the core invariant — "a test payload the pinned model rejects is
either a declared malformation or a defect, and the difference is stated at the site
where the payload is written" — and this file is only one of them.

    THE RUNTIME GATE FINDS UNMARKED MALFORMATIONS.
    ``assert_declared_malformations`` (``tests/factories/malformed.py``), reached through
    ``gate_and_record`` (``tests/bdd/steps/generic/_dispatch.py``) before ``json_safe``
    rebuilds the dicts, validates every dispatched item against the pinned model — at
    every position it sits, top-level ``creatives`` and ``packages[].creatives`` alike.
    It has to be a runtime gate and this file cannot take that job over: measured,
    ``ast.literal_eval`` succeeds on 0 of the 40 creative literals in
    ``uc006_sync_creatives.py`` (46 of 50 across ``--scope bdd``) — every one holds a
    Name, Attribute, Call or f-string — so a source-level validation gate would pass
    vacuously green over the file holding 80% of the subject.

    THIS FILE ALSO CHECKS THAT THE GATE IS REACHED AT ALL.
    ``test_every_path_to_a_transport_gates_first`` is the one part of the mechanism a
    static scan can own outright: it flags any ``env.call_via`` / ``client.call`` under
    ``tests/bdd`` whose enclosing function does not call ``gate_and_record`` above it.
    The gate shipped covering one step-level entry of three, which is what that test
    exists to stop happening a fourth time.

    THIS FILE KEEPS THE DECLARATIONS AUDITABLE, and owns the shrink-only list of sites
    that are known to hold an undeclared malformation. It asserts that every
    ``malformed(...)`` call names a kind from the closed vocabulary and a non-empty
    reason — NOT that malformations are absent, which it has no way to know.

Consequently ``test_known_violations_not_stale`` reports STALE entries (a site got
declared, remove it) and never NEW ones. New violations are the runtime gate's to
report, and it reports them where they happen: at dispatch, in the failing scenario.

``KNOWN_VIOLATIONS`` was a HANDOFF, not a tolerance, and it is now EMPTY: all five
seeded sites carry a declaration. The runtime gate has no allowlist either: measured,
all five sit in scenarios that are xfail-dormant, so the gate fires zero times over
them and a tolerance would only mask the malformation on the day the scenario is wired.

Site census comes from ``scan()``/``SCOPES`` in
``scripts/audit/creative_literal_sites.py`` rather than a second definition of "site",
so the audit and the guard cannot disagree about what they are counting. The runtime
gate takes ``EXTRA_FORBIDDEN`` from the same module on the same grounds, and
``test_the_gate_and_the_census_share_one_discriminator`` keeps it that way.

Structured on ``tests/unit/test_architecture_no_packages_field_literal.py`` (the
allowlist-plus-staleness idiom, and exemption of a legitimate producer BY IDENTITY) and
``tests/unit/test_architecture_harness_identity_sentinel.py`` (the three-control
meta-test shape).
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any, get_args

import pytest
from pydantic import ValidationError

import scripts.audit.creative_literal_sites as census_module
import tests.factories.malformed as malformed_module
from scripts.audit.creative_literal_sites import SCOPES, scan
from src.core.errors.codes import AppErrorCode, ErrorCode, Recovery
from src.core.schemas.creative import CreativeAssetRequest
from tests.factories.malformed import (
    GATED_ITEMS,
    MALFORMATION_KINDS,
    MALFORMATION_OBLIGATIONS,
    PIN_DERIVABLE_OBLIGATION,
    _Malformed,
    _pin_refuses_the_bytes,
    _pin_rejection,
    malformation_problems,
    malformed,
)
from tests.unit._architecture_helpers import (
    assert_detector_catches_ast_snippets,
    assert_guard_subject_resolves,
    assert_scanned_paths_exist,
    assert_violations_match_allowlist,
    call_callee_name,
    iter_call_expressions,
    repo_root,
    safe_parse,
    walk_with_enclosing_function,
)

MARKER_NAME = "malformed"

#: The one function carrying the two obligations a dispatch owes before its payload
#: reaches a transport (``tests/bdd/steps/generic/_dispatch.py``). A step that reaches a
#: transport calls THIS, not ``assert_declared_malformations`` on its own: the pair
#: travelling together is what stopped the gate covering one entry of three.
GATE_ENTRY = "gate_and_record"

# Census sites that dispatch a payload the pinned model rejects and carry no
# declaration yet. Keyed by (relative_path, enclosing_function). MUST ONLY SHRINK.
#
# EMPTY, and it stays empty. The 5 of 8 FIXABLE_NOW sites this set was seeded with
# (salesagent-hz7di.2's scan; 4954/4974/5347 are repaired by a later Given and were
# correctly never listed) all carry a ``malformed(...)`` declaration as of
# salesagent-hz7di.4, in tests/bdd/steps/domain/uc006_sync_creatives.py:
#
#     given_creative_with_known_format_no_media_url   absent_key
#     given_creative_with_name_no_format              explicit_none
#     given_creative_invalid_schema                   wrong_type
#     given_creative_with_no_format_id                absent_key
#     given_creative_with_invalid_format_id           semantic
#
# The last of those was handed over as a DEAD STEP to delete rather than mark. It is
# not dead: no feature file holds its literal text, but
# "Format validation — <partition>" substitutes ``format_setup`` = ``no format_id``
# into ``And a creative with <format_setup>``, which renders to exactly this step's
# name and resolves to it (checked against pytest-bdd's registered parsers). It grades
# ``test_format_validation__partition[a2a|mcp|rest-missing_format_id-...]``, all three
# xfail-dormant at SETUP — so the prescribed "did any scenario turn xfail" check would
# have passed vacuously over the deletion. It is marked instead.
#
# There is no runtime tolerance list to match this one: see the measurement recorded in
# tests/factories/malformed.py.
KNOWN_VIOLATIONS: set[tuple[str, str]] = set()

#: Sites ``scan()`` reports that are not payloads at all, excluded BY IDENTITY rather
#: than by allowlist: an allowlist entry says "known debt, will shrink", and these are
#: category errors that will never shrink. The site definition is a key-overlap
#: heuristic (>=2 of a 9-key set), so it catches dicts that never travel to a model.
#:
#: ``tests/factories/creative_asset.py`` is exempt on the same grounds — it is the
#: definition, not tolerated debt — and is additionally outside ``--scope bdd``, so it
#: is not listed here; a list entry that never matches would go stale by construction.
EXEMPT_BY_IDENTITY: dict[tuple[str, str], str] = {
    (
        "tests/bdd/steps/domain/uc026_package_media_buy.py",
        "given_product_with_pricing",
    ): "_LABEL_SPEC rows are spread as ORM kwargs into PricingOptionFactory(...). "
    "is_fixed is a DB column, not a PricingOption schema field, which is exactly why "
    "the schema reports extra_forbidden on it.",
}


# ---------------------------------------------------------------------------
# Detector: is every declaration auditable?
# ---------------------------------------------------------------------------


def _string_constant(node: ast.expr | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _marker_argument(call: ast.Call, position: int, name: str) -> ast.expr | None:
    if len(call.args) > position:
        return call.args[position]
    return next((kw.value for kw in call.keywords if kw.arg == name), None)


def find_unauditable_markers(tree: ast.Module) -> list[int]:
    """Line numbers of ``malformed(...)`` calls whose kind or reason cannot be audited.

    A marker is auditable only when both are literals at the site: a non-literal kind
    cannot be checked against the vocabulary, and a non-literal reason is a promise
    that some other line keeps.
    """
    bad: list[int] = []
    for call in iter_call_expressions(tree, MARKER_NAME):
        kind = _string_constant(_marker_argument(call, 0, "kind"))
        why = _string_constant(_marker_argument(call, 1, "why"))
        if kind not in MALFORMATION_KINDS or not (why or "").strip():
            bad.append(call.lineno)
    return bad


# ---------------------------------------------------------------------------
# Detector: does every path that reaches a transport gate first?
# ---------------------------------------------------------------------------


def _reaches_a_transport(call: ast.Call) -> bool:
    """Is *call* one of the two APIs that put a BDD payload on a transport?

    ``env.call_via(...)`` and ``AdCPTestClient.call(...)``. ``.call`` alone is far too
    common a method name to key on, so the receiver has to name a client — which is
    what both real sites spell (``client.call``), and what a third would spell too.
    """
    func = call.func
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr == "call_via":
        return True
    return func.attr == "call" and "client" in ast.unparse(func.value)


def find_ungated_transport_calls(tree: ast.Module) -> list[int]:
    """Line numbers of transport calls their enclosing function never gates ahead of.

    BEFORE, not merely present: a gate below the dispatch grades bytes that already
    crossed the wire. Order is checked by line number, which is exact for the shape
    every real site has (a straight-line step function) and is why the detector's
    positive controls include a gate-after-dispatch snippet.
    """
    gates: dict[str, list[int]] = {}
    dispatches: dict[str, list[int]] = {}
    for node, func in walk_with_enclosing_function(tree):
        if not isinstance(node, ast.Call):
            continue
        if call_callee_name(node) == GATE_ENTRY:
            gates.setdefault(func, []).append(node.lineno)
        elif _reaches_a_transport(node):
            dispatches.setdefault(func, []).append(node.lineno)
    return sorted(
        lineno
        for func, linenos in dispatches.items()
        for lineno in linenos
        if not any(gate < lineno for gate in gates.get(func, []))
    )


# ---------------------------------------------------------------------------
# Site census (shared with the audit script)
# ---------------------------------------------------------------------------


def _absolute(scope: str) -> list[str]:
    """``SCOPES[scope]`` anchored at the repo root — ``scan()`` globs relative to cwd."""
    return [str(repo_root() / pattern) for pattern in SCOPES[scope]]


def _rel(path: str) -> str:
    return str(Path(path).resolve().relative_to(repo_root()))


def _site_lines_by_file(scope: str) -> dict[str, set[int]]:
    creatives, pricing = scan(_absolute(scope))
    lines: dict[str, set[int]] = {}
    for site in [*creatives, *pricing]:
        lines.setdefault(_rel(site["file"]), set()).add(site["line"])
    return lines


def _marker_spans(tree: ast.Module) -> list[tuple[int, int]]:
    """Line ranges covered by a ``malformed(...)`` call — the technique ``factory_spans`` uses."""
    return [(call.lineno, call.end_lineno or call.lineno) for call in iter_call_expressions(tree, MARKER_NAME)]


def unmarked_site_functions(scope: str = "bdd") -> set[tuple[str, str]]:
    """``(relative_path, enclosing_function)`` for every census site not inside a marker."""
    found: set[tuple[str, str]] = set()
    for rel_path, linenos in _site_lines_by_file(scope).items():
        tree = safe_parse(repo_root() / rel_path)
        if tree is None:
            continue
        spans = _marker_spans(tree)
        for node, func in walk_with_enclosing_function(tree):
            if not isinstance(node, ast.Dict) or node.lineno not in linenos:
                continue
            if (rel_path, func) in EXEMPT_BY_IDENTITY:
                continue
            if not any(lo <= node.lineno <= hi for lo, hi in spans):
                found.add((rel_path, func))
    return found


# ---------------------------------------------------------------------------
# The guard
# ---------------------------------------------------------------------------


@pytest.mark.arch_guard
def test_every_declaration_is_auditable() -> None:
    """Every ``malformed(...)`` in the test tree names a known kind and a real reason."""
    # The whole test tree, not just the census scope: a declaration written anywhere
    # must be auditable, and a marker outside --scope bdd is exactly the one nobody
    # would think to look at.
    violations: list[str] = []
    for path in sorted((repo_root() / "tests").rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        tree = safe_parse(path)
        if tree is None:
            continue
        rel_path = str(path.relative_to(repo_root()))
        violations.extend(f"  {rel_path}:{lineno}" for lineno in find_unauditable_markers(tree))
    assert not violations, (
        f"{len(violations)} malformed(...) call(s) cannot be audited. The kind must be a "
        f"literal from {sorted(MALFORMATION_KINDS)} and the reason a non-empty literal — "
        "a bare or indirect marker is an opt-out, not a justification:\n" + "\n".join(violations)
    )


@pytest.mark.arch_guard
def test_every_path_to_a_transport_gates_first() -> None:
    """No BDD payload reaches a transport without being graded and recorded first.

    The gate shipped with ONE call site while THREE step-level entries reached a
    transport, so a malformed payload on either of the other two was never checked and
    a declared malformation silently repaired on them was never reported
    (salesagent-99w2t). ``when_request._call_via`` was then folded into
    ``dispatch_request``, ``dispatch_via_client`` gained the gate in place (it cannot be
    folded — ``AccountListDispatchMixin.is_list_request`` discriminates on
    ``isinstance``), and the two read-backs that legitimately bypass both entries — they
    must not clobber the ``ctx["result"]`` the scenario is grading — call
    ``gate_and_record`` themselves.

    A LIST OF FOUR SITES IS NOT THE INVARIANT; this test is. The recurring failure this
    repo names is a canonical helper whose call sites drift away from it one commit at a
    time, and the fix for that is a check that fails on the FOURTH site rather than a
    comment asking the next author to remember.
    """
    ungated: list[str] = []
    for path in sorted((repo_root() / "tests" / "bdd").rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        tree = safe_parse(path)
        if tree is None:
            continue
        rel_path = str(path.relative_to(repo_root()))
        ungated.extend(f"  {rel_path}:{lineno}" for lineno in find_ungated_transport_calls(tree))
    assert not ungated, (
        f"{len(ungated)} call(s) put a payload on a transport without calling "
        f"{GATE_ENTRY}() first. Route the dispatch through dispatch_request / "
        f"dispatch_via_client, or — if it is a read-back that must not overwrite "
        f"ctx['result'] — call {GATE_ENTRY}(payload) immediately above it:\n" + "\n".join(ungated)
    )


@pytest.mark.arch_guard
def test_known_violations_not_stale() -> None:
    """Every allowlisted entry still names an undeclared census site. Must only shrink."""
    unmarked = unmarked_site_functions()
    found = {entry for entry in KNOWN_VIOLATIONS if entry in unmarked}
    assert_violations_match_allowlist(
        found,
        KNOWN_VIOLATIONS,
        fix_hint=(
            "An entry goes stale when its site is wrapped in malformed(...) or deleted "
            "— remove it then. KNOWN_VIOLATIONS is never grown: an undeclared "
            "malformation is reported by the runtime gate at dispatch, in the failing "
            "scenario, not by this file."
        ),
    )


@pytest.mark.arch_guard
def test_identity_exemptions_still_name_real_sites() -> None:
    """An exemption that stops matching is a stale claim about the tree, not a free pass."""
    assert_scanned_paths_exist(
        [path for path, _ in EXEMPT_BY_IDENTITY],
        why="the identity exemptions in this guard would silently cover nothing.",
    )
    census = _site_lines_by_file("bdd")
    orphans: list[str] = []
    for rel_path, func in EXEMPT_BY_IDENTITY:
        tree = safe_parse(repo_root() / rel_path)
        linenos = census.get(rel_path, set())
        hit = tree is not None and any(
            isinstance(node, ast.Dict) and node.lineno in linenos and enclosing == func
            for node, enclosing in walk_with_enclosing_function(tree)
        )
        if not hit:
            orphans.append(f"  {rel_path}:{func}()")
    assert not orphans, (
        "EXEMPT_BY_IDENTITY names site(s) the census no longer reports. Remove the entry "
        "— an exemption for something that is not there teaches the next reader that a "
        "real site is tolerated:\n" + "\n".join(orphans)
    )


@pytest.mark.arch_guard
def test_guard_subjects_resolve() -> None:
    """The names this guard scans for still exist — a rename must fail loudly, not silently."""
    assert_guard_subject_resolves(
        "tests.factories.malformed",
        "malformed",
        "MALFORMATION_KINDS",
        "MALFORMATION_OBLIGATIONS",
        "assert_declared_malformations",
        why="nothing would declare or grade a deliberate malformation.",
    )
    assert_guard_subject_resolves(
        "scripts.audit.creative_literal_sites",
        "scan",
        "SCOPES",
        why="this guard would invent a second definition of 'site' and drift from the audit.",
    )
    assert_guard_subject_resolves(
        "tests.bdd.steps.generic._dispatch",
        "dispatch_request",
        "dispatch_via_client",
        GATE_ENTRY,
        why="the runtime gate would hang off nothing.",
    )


@pytest.mark.arch_guard
def test_gated_creative_model_is_the_pinned_request_item() -> None:
    """The gate validates against the DTO's OWN item type, derived rather than declared."""
    from src.core.schemas.creative import SyncCreativesRequest

    item_models = get_args(SyncCreativesRequest.model_fields["creatives"].annotation)
    assert len(item_models) == 1, f"expected list[<one model>], got {item_models}"
    item_model = item_models[0]
    assert GATED_ITEMS["creatives"][0] is item_model, (
        f"The runtime gate validates creatives against {GATED_ITEMS['creatives'][0].__name__}, "
        f"but SyncCreativesRequest.creatives holds {item_model.__name__}. Grading a model the "
        "request does not use is how a request shape became a response shape once already."
    )


# ---------------------------------------------------------------------------
# Meta-tests — break it on purpose, as committed tests
# ---------------------------------------------------------------------------

_CONFORMANT_CREATIVE: dict[str, Any] = {
    "creative_id": "creative-meta-001",
    "name": "Conformant",
    "format_id": {"id": "display_300x250", "agent_url": "https://agent.test"},
    "assets": {"image": [{"asset_type": "image", "url": "https://cdn.test/a.png", "width": 300, "height": 250}]},
}


def _rejected_creative(creative_id: str = "creative-meta-001") -> dict[str, Any]:
    """The conformant creative minus ``assets`` — measured REJECTED by the pin (``Field required``)."""
    return {**{k: v for k, v in _CONFORMANT_CREATIVE.items() if k != "assets"}, "creative_id": creative_id}


@pytest.mark.arch_guard
def test_positive_control_static_detector_catches_unauditable_markers() -> None:
    """The static detector flags every shape of unauditable declaration."""
    assert_detector_catches_ast_snippets(
        find_unauditable_markers,
        snippets={
            "kind-not-in-vocabulary": 'x = malformed("typo_kind", "a real reason", {"a": 1})\n',
            "reason-empty": 'x = malformed("wrong_type", "   ", {"a": 1})\n',
            "reason-missing": 'x = malformed("wrong_type")\n',
            "kind-not-a-literal": 'x = malformed(KIND, "a real reason", {"a": 1})\n',
            "reason-not-a-literal": 'x = malformed("wrong_type", REASON, {"a": 1})\n',
        },
    )


@pytest.mark.arch_guard
def test_positive_control_ungated_dispatch_detector_catches_every_shape() -> None:
    """The coverage detector flags each way a transport call can escape the gate."""
    assert_detector_catches_ast_snippets(
        find_ungated_transport_calls,
        snippets={
            "call-via-with-no-gate": "def step(ctx):\n    return ctx['env'].call_via(t, **kwargs)\n",
            "client-call-with-no-gate": "def step(ctx):\n    return ctx['client'].call('x', {}, t)\n",
            "gate-in-a-different-function": (
                "def other(p):\n    gate_and_record(p)\n\ndef step(ctx):\n    return ctx['env'].call_via(t)\n"
            ),
            "gate-AFTER-the-dispatch": (
                "def step(ctx):\n    r = ctx['env'].call_via(t, **kw)\n    gate_and_record(kw)\n    return r\n"
            ),
        },
    )


@pytest.mark.arch_guard
def test_negative_control_gated_dispatch_not_flagged() -> None:
    """A transport call preceded by the gate in the same function is left alone."""
    source = "def step(ctx):\n    gate_and_record(kwargs)\n    return ctx['env'].call_via(t, **kwargs)\n"
    assert find_ungated_transport_calls(ast.parse(source, filename="<known-good>")) == []


@pytest.mark.arch_guard
@pytest.mark.parametrize(
    "obligation",
    [
        pytest.param("INVALID_REQUEST", id="the-name-as-a-string"),
        pytest.param("AGENT_UNREACHABLE", id="a-seller-code-name-as-a-string"),
        pytest.param(True, id="the-boolean-this-axis-replaced"),
        pytest.param(None, id="nothing"),
        pytest.param(Recovery.CORRECTABLE, id="a-StrEnum-member-from-a-different-vocabulary"),
    ],
)
def test_malformed_refuses_an_obligation_outside_the_vocabulary(obligation: Any) -> None:
    """The axis is REFUSED at the call, not validated later — including the old boolean.

    ``pin_rejects=True`` is the spelling every migrated site used to carry, and a call
    that still passes ``True`` must not be silently read as "the pin rejects it": that
    is the collapse this axis exists to end. It cannot reach the gate wearing the new
    keyword.

    THE STRING CASES ARE THE LOAD-BEARING ONES, and there are two because there are two
    arms. ``ErrorCode`` and ``AppErrorCode`` are both ``StrEnum``s whose value is the
    member name, so ``"INVALID_REQUEST" in MALFORMATION_OBLIGATIONS`` is True and
    ``"AGENT_UNREACHABLE" in`` it is too — membership alone admits either, and the gate
    then compares with ``is`` and grades neither. Only an isinstance check over both arms
    refuses them.
    """
    with pytest.raises((ValueError, TypeError), match="obligation"):
        malformed("wrong_type", "a real reason", {"a": 1}, obligation=obligation)


@pytest.mark.arch_guard
@pytest.mark.parametrize(
    "order",
    [
        pytest.param(("extra_forbidden", "missing"), id="undeclared-key-emitted-FIRST"),
        pytest.param(("missing", "extra_forbidden"), id="undeclared-key-emitted-last"),
    ],
)
def test_the_obligation_reads_the_whole_error_set_not_the_first_error(order: tuple[str, str]) -> None:
    """A payload wrong on the merits AND carrying an extra key is INVALID_REQUEST, either way round.

    The errors are hand-built rather than provoked out of the real model, and that is
    the point of the control. MEASURED: on ``CreativeAssetRequest`` pydantic emits
    ``extra_forbidden`` LAST in every mixed case constructible against it, so replacing
    the whole-set read with ``errors()[0]`` changes no answer and every other test in
    this file stays green — the rule would be committed and never once exercised.
    Controlling the order is the only way to grade it.

    Emission order is not part of pydantic's contract. If it flipped, an
    ``errors()[0]`` reading would start classifying a genuinely malformed payload as an
    undeclared key and REFUSE its declaration, which is the noisiest possible way to be
    wrong about the one distinction this axis exists to draw.
    """
    rejection = ValidationError.from_exception_data(
        "CreativeAssetRequest",
        [{"type": kind, "loc": (kind,), "input": "x"} for kind in order],
    )
    assert [error["type"] for error in rejection.errors()] == list(order), "the control lost control of the order"
    assert _pin_refuses_the_bytes(rejection) is True


@pytest.mark.arch_guard
def test_the_gate_and_the_census_share_one_discriminator() -> None:
    """The runtime gate and the static census read the SAME constant, not two copies.

    Both instruments answer "did the pin refuse these bytes, or only this key", and both
    answer it off ``extra_forbidden`` with the same dominance rule: a reason set of
    exactly ``{extra_forbidden}`` is the field-dropped guarantee, and any other reason
    dominates. Two spellings of that is the two instruments disagreeing about what
    "invalid" means — which is the disease both salesagent-b341x.26 and
    salesagent-b341x.18 were filed against, and the reason the ticket required them to
    land in this order.

    Checked STRUCTURALLY, on the import, and not by comparing the two values. The first
    version of this test asserted ``malformed.EXTRA_FORBIDDEN is census.EXTRA_FORBIDDEN``
    and passed with the constant re-declared locally — CPython interns
    ``"extra_forbidden"``, so a second copy is the same object and ``is`` cannot tell
    them apart. Observed: the deliberate break went green. Where the constant COMES FROM
    is the property; equality of two copies is the thing that stops being true later.
    """
    tree = safe_parse(repo_root() / "tests" / "factories" / "malformed.py")
    assert tree is not None
    imported = any(
        isinstance(node, ast.ImportFrom)
        and node.module == census_module.__name__
        and any(alias.name == "EXTRA_FORBIDDEN" for alias in node.names)
        for node in ast.walk(tree)
    )
    redefined = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "EXTRA_FORBIDDEN" for target in node.targets)
    ]
    assert imported and not redefined, (
        "tests/factories/malformed.py must IMPORT EXTRA_FORBIDDEN from "
        f"{census_module.__name__}, not define its own "
        f"(imported={imported}, local definitions at {redefined}). Two spellings of the "
        "same discriminator is the gate and the census disagreeing about what 'invalid' "
        "means."
    )
    assert malformed_module.EXTRA_FORBIDDEN == census_module.EXTRA_FORBIDDEN


@pytest.mark.arch_guard
def test_the_obligation_vocabulary_is_this_repos_own_table_both_arms() -> None:
    """The vocabulary is ``ErrorCodeT`` — the spec's codes AND this seller's — and derived.

    Importing the SDK enum alone silently drops ``AppErrorCode``'s 8 members, and that
    arm is not decoration: ``AGENT_UNREACHABLE`` is a graded outcome in
    ``BR-UC-006-sync-creatives.feature``'s own Examples table, and across
    ``tests/bdd/features`` the seller's codes are named 22 times. An author whose
    malformation obliges one of those would otherwise have to name a code the buyer does
    not receive.

    Derived from the enums rather than listed, so it cannot drift from them — a written
    list would be a third declaration of a vocabulary that already exists twice.
    """
    assert MALFORMATION_OBLIGATIONS == set(ErrorCode) | set(AppErrorCode)
    assert ErrorCode.INVALID_REQUEST in MALFORMATION_OBLIGATIONS
    assert AppErrorCode.AGENT_UNREACHABLE in MALFORMATION_OBLIGATIONS, (
        "the seller's own codes must be nameable, or a malformation whose obligation is "
        "AGENT_UNREACHABLE has no honest way to say so"
    )
    assert all(isinstance(obligation, ErrorCode | AppErrorCode) for obligation in MALFORMATION_OBLIGATIONS)


@pytest.mark.arch_guard
def test_the_graded_partition_is_the_only_thing_the_pin_can_derive() -> None:
    """The gate grades ``is it INVALID_REQUEST or not`` — and that is the honest limit.

    Nothing about bytes the pin ACCEPTS says which of the seller's rules refuses them:
    ``assets: {}`` obliges VALIDATION_ERROR and an unreachable format agent obliges
    AGENT_UNREACHABLE, and the schema cannot tell those apart. So the pin's own answer is
    a three-state — refuses / accepts / objects-only-to-a-key — and the declaration is
    graded against the one distinction in it.
    """
    assert PIN_DERIVABLE_OBLIGATION is ErrorCode.INVALID_REQUEST
    assert PIN_DERIVABLE_OBLIGATION in MALFORMATION_OBLIGATIONS
    accepted = _pin_rejection(CreativeAssetRequest, _CONFORMANT_CREATIVE)
    assert _pin_refuses_the_bytes(accepted) is False
    refused = _pin_rejection(CreativeAssetRequest, _rejected_creative())
    assert _pin_refuses_the_bytes(refused) is True
    key_only = _pin_rejection(CreativeAssetRequest, {**_CONFORMANT_CREATIVE, "snippet": "<div/>"})
    assert _pin_refuses_the_bytes(key_only) is None


@pytest.mark.arch_guard
@pytest.mark.parametrize(
    "obligation",
    [
        pytest.param(ErrorCode.VALIDATION_ERROR, id="a-spec-code"),
        pytest.param(AppErrorCode.AGENT_UNREACHABLE, id="a-seller-code"),
        pytest.param(ErrorCode.CREATIVE_NOT_FOUND, id="an-unrelated-spec-code"),
    ],
)
def test_a_declaration_that_is_not_the_pins_obligation_is_graded_not_ignored(obligation: Any) -> None:
    """Every code other than INVALID_REQUEST is MIS-DECLARED on bytes the pin refuses.

    This is what the partition buys and what a two-member comparison lost. MEASURED
    before the change: a site declaring ``AGENT_UNREACHABLE`` over a payload the pin
    refuses produced 0 problems — the ``declared is VALIDATION_ERROR`` branch did not
    match, the ``declared is INVALID_REQUEST`` branch did not match, and the site was
    graded by NEITHER. Widening the vocabulary without widening the comparison would have
    opened exactly the hole the vocabulary was widened to close.
    """
    declared = malformed(
        "absent_key",
        "claims these bytes reach the seller's rules; measured, the pin refuses them first",
        _rejected_creative("creative-meta-third-code-001"),
        obligation=obligation,
    )
    problems = malformation_problems({"creatives": [declared]})
    assert len(problems) == 1, (
        f"a declaration of {obligation.name} over bytes the pin refuses must be reported; "
        f"got {len(problems)}. Grading only two named members is how a third code goes silent."
    )
    assert "MIS-DECLARED" in problems[0]
    assert obligation.name in problems[0], "the report must name what was declared"


@pytest.mark.arch_guard
@pytest.mark.parametrize(
    "obligation",
    [
        pytest.param(ErrorCode.INVALID_REQUEST, id="declared-INVALID_REQUEST"),
        pytest.param(ErrorCode.VALIDATION_ERROR, id="declared-VALIDATION_ERROR"),
        pytest.param(AppErrorCode.AGENT_UNREACHABLE, id="declared-a-seller-code"),
    ],
)
def test_the_undeclared_key_refusal_is_not_author_trusted(obligation: Any) -> None:
    """An unknown key is refused WHATEVER the author named — the pin's reason set decides.

    The vocabulary having no member for an undeclared key prevents the honest mistake.
    It does not prevent the careless one: nothing stops an author writing
    ``obligation=INVALID_REQUEST`` on a site whose only defect is an unknown key, and it
    is the careless one that ships. So the refusal is keyed on the pin's ACTUAL reason
    set — exactly ``{extra_forbidden}`` — and not on what was declared.
    """
    declared = malformed(
        "semantic",
        "a pre-3.1.1 snippet key the schema never declared",
        {**_CONFORMANT_CREATIVE, "creative_id": "creative-meta-key-only-001", "snippet": "<div/>"},
        obligation=obligation,
    )
    problems = malformation_problems({"creatives": [declared]})
    assert len(problems) == 1, (
        f"declaring {obligation.name} must not buy a pass on an undeclared-key site; got {len(problems)}"
    )
    assert "UNDECLARED KEY" in problems[0]
    assert "_accept_only_declared_fields" in problems[0], "the report must point at the guarantee that grades it"


@pytest.mark.arch_guard
def test_negative_control_conformant_declaration_not_flagged() -> None:
    """A well-formed declaration — positional or keyword — is left alone."""
    source = (
        'a = malformed("wrong_type", "assets is a string where the pin requires an object", {"assets": "x"})\n'
        'b = malformed(kind="absent_key", why="no format_id key at all", payload={"name": "n"})\n'
    )
    assert find_unauditable_markers(ast.parse(source, filename="<known-good>")) == []


@pytest.mark.arch_guard
def test_positive_control_runtime_gate_flags_unmarked_malformation() -> None:
    """An undeclared item the pinned model rejects is reported, with the pydantic reason."""
    payload = _rejected_creative("creative-meta-unmarked-001")
    problems = malformation_problems({"creatives": [payload]})
    assert len(problems) == 1, problems
    assert "creative-meta-unmarked-001" in problems[0]
    assert "assets" in problems[0]


@pytest.mark.arch_guard
def test_positive_control_runtime_gate_accepts_the_marked_form() -> None:
    """The SAME rejected bytes, declared, pass — and stay byte-identical on the way."""
    payload = _rejected_creative()
    declared = malformed(
        "absent_key",
        "no assets key: exercises the preview-failure branch",
        payload,
        obligation=ErrorCode.INVALID_REQUEST,
    )
    assert malformation_problems({"creatives": [declared]}) == []
    assert isinstance(declared, _Malformed)
    assert dict(declared) == payload, "the declaration must not alter what reaches the wire"


@pytest.mark.arch_guard
def test_negative_control_runtime_gate_passes_a_conformant_literal() -> None:
    """A conformant creative is NOT flagged — the gate asks the model, not for markers."""
    assert malformation_problems({"creatives": [_CONFORMANT_CREATIVE]}) == []


def _stub_dispatch_ctx(dispatched: list[dict[str, Any]]) -> dict[str, Any]:
    """A ctx whose env and client record the bag instead of putting it on a wire."""
    from tests.harness.transport import Transport

    def call_via(transport: Any, **kwargs: Any) -> SimpleNamespace:
        dispatched.append(kwargs)
        return SimpleNamespace(is_error=False, wire_response=None)

    def client_call(tool: str, payload: dict[str, Any], transport: Any, **_: Any) -> SimpleNamespace:
        dispatched.append(payload)
        return SimpleNamespace(is_error=False, wire_response=None)

    return {
        "env": SimpleNamespace(call_via=call_via),
        "client": SimpleNamespace(call=client_call),
        "transport": Transport.MCP,
    }


def _via_dispatch_request(ctx: dict[str, Any], bag: dict[str, Any]) -> None:
    from tests.bdd.steps.generic._dispatch import dispatch_request

    dispatch_request(ctx, **bag)


def _via_dispatch_via_client(ctx: dict[str, Any], bag: dict[str, Any]) -> None:
    from tests.bdd.steps.generic._dispatch import dispatch_via_client

    dispatch_via_client(ctx, "sync_creatives", bag)


def _via_call_via(ctx: dict[str, Any], bag: dict[str, Any]) -> None:
    from tests.bdd.steps.generic.when_request import _call_via

    _call_via(ctx, ctx["transport"], **bag)


#: EVERY step-level entry that reaches a transport, each one exercised below. The gate
#: shipped covering ONE of these while all three were live (salesagent-99w2t); a list
#: this test walks is the difference between "the other two are gated" as a claim and as
#: an observation. ``_call_via`` is an adapter over ``dispatch_request`` today, and it is
#: still exercised as an ENTRY: what a caller must not be able to do is reach a wire
#: through it ungated, whichever way it is implemented underneath.
_DISPATCH_ENTRIES = {
    "dispatch_request": _via_dispatch_request,
    "dispatch_via_client": _via_dispatch_via_client,
    "when_request._call_via": _via_call_via,
}


@pytest.mark.arch_guard
@pytest.mark.parametrize("entry", list(_DISPATCH_ENTRIES), ids=list(_DISPATCH_ENTRIES))
@pytest.mark.parametrize("position", ["top-level", "nested"])
def test_scenario_level_control_every_dispatch_entry_refuses_an_unmarked_malformation(
    entry: str, position: str
) -> None:
    """Each entry, at each position: an undeclared malformation fails before the wire.

    The scenario-level equivalent of the pure-function controls above — and the one
    that would have caught the original defect, where two of the three entries reached
    a transport with nothing grading the payload.

    The second half matters as much as the first: the same bytes DECLARED must reach
    the transport untouched, or the gate would be quietly rewriting the payloads these
    scenarios exist to send.
    """
    payload = _rejected_creative(f"creative-meta-entry-{position}-001")
    bag = _nested(payload) if position == "nested" else {"creatives": [payload]}
    expected_site = r"packages\[0\]\.creatives\[0\]" if position == "nested" else r"creatives\[0\]"

    dispatched: list[dict[str, Any]] = []
    ctx = _stub_dispatch_ctx(dispatched)
    with pytest.raises(AssertionError, match=expected_site):
        _DISPATCH_ENTRIES[entry](ctx, bag)
    assert dispatched == [], "the gate must refuse BEFORE anything reaches the transport"

    declared = malformed(
        "absent_key",
        "no assets key: exercises the preview-failure branch",
        payload,
        obligation=ErrorCode.INVALID_REQUEST,
    )
    declared_bag = _nested(declared) if position == "nested" else {"creatives": [declared]}
    _DISPATCH_ENTRIES[entry](ctx, declared_bag)
    assert len(dispatched) == 1, "the declared form must reach the transport"
    sent = dispatched[0]["packages"][0]["creatives"][0] if position == "nested" else dispatched[0]["creatives"][0]
    assert dict(sent) == payload, "the declaration must not alter what reaches the wire"


# ---------------------------------------------------------------------------
# The gate reaches the item WHEREVER it sits, not only at the top level
# ---------------------------------------------------------------------------
#
# WHY THE NESTED POSITION IS NOT A CORNER CASE. Measured on the baseline payload
# artifact (test-results/innet_080926_1859/, 8182 collected / 8182 payload rows):
# 158 creative items were dispatched at the top level and the pin rejected 0 of them;
# 6 arrived under ``packages[0].creatives`` and the pin rejected 3. The unwatched
# position carried 3 of the 3 pin rejections in the whole corpus, two of which were
# real ``asset_type`` defects — found by validating the census, because the gate whose
# job that is could not see them (salesagent-b341x.15).
#
# The DTOs are why the position matters rather than merely differing:
# ``AdCPPackageUpdate.creatives`` is ``list[adcp ... CreativeAsset]`` (strict) while
# ``PackageRequest.creatives`` is ``list[src ... Creative]`` (permissive), so on the
# CREATE path an item the pinned request model rejects passes the DTO untouched and
# reaches production as a raw dict. The gate grades the ITEM against the pinned item
# model at every position, which is the one verdict that does not depend on which DTO
# happens to hold it.


def _nested(creative: dict[str, Any]) -> dict[str, Any]:
    """*creative* in the position ``update_media_buy`` puts it in."""
    return {"media_buy_id": "mb_001", "packages": [{"package_id": "pkg_001", "creatives": [creative]}]}


@pytest.mark.arch_guard
def test_positive_control_runtime_gate_flags_unmarked_nested_malformation() -> None:
    """An undeclared malformation under ``packages[].creatives`` is reported, with its path."""
    problems = malformation_problems(_nested(_rejected_creative("creative-meta-nested-001")))
    assert len(problems) == 1, problems
    assert "packages[0].creatives[0]" in problems[0], (
        "the report must name the POSITION, or nobody can find the payload it is about:\n" + problems[0]
    )
    assert "creative-meta-nested-001" in problems[0]


@pytest.mark.arch_guard
def test_positive_control_runtime_gate_accepts_the_marked_nested_form() -> None:
    """The same nested bytes, declared, pass — and reach the wire unaltered."""
    payload = _rejected_creative("creative-meta-nested-declared-001")
    declared = malformed(
        "absent_key", "no assets key on an inline package creative", payload, obligation=ErrorCode.INVALID_REQUEST
    )
    assert malformation_problems(_nested(declared)) == []
    assert dict(declared) == payload, "the declaration must not alter what reaches the wire"


@pytest.mark.arch_guard
def test_negative_control_runtime_gate_passes_a_conformant_nested_literal() -> None:
    """A conformant nested creative is NOT flagged — the gate asks the model, not for markers."""
    assert malformation_problems(_nested(_CONFORMANT_CREATIVE)) == []


@pytest.mark.arch_guard
def test_nested_declaration_is_graded_against_the_pins_actual_verdict() -> None:
    """A REPAIRED declaration is reported in the nested position too, not only at the top."""
    declared = malformed(
        "absent_key",
        "declares the assets key absent — these bytes carry it, which is what a factory default does",
        {**_CONFORMANT_CREATIVE, "creative_id": "creative-meta-nested-repaired-001"},
        obligation=ErrorCode.INVALID_REQUEST,
    )
    problems = malformation_problems(_nested(declared))
    assert len(problems) == 1, problems
    assert "packages[0].creatives[0]" in problems[0]
    assert "REPAIRED" in problems[0]


@pytest.mark.arch_guard
def test_an_object_that_is_not_a_dict_or_list_is_not_walked_into() -> None:
    """The walk descends dicts and lists ONLY, and this is the boundary that says so.

    ``dispatch_request`` bags routinely carry ``req=<a typed request model>``. That
    object came from a builder the pin already ran, and the gate's subject is the
    hand-built inline LITERAL — so reaching through attributes would grade the builder
    and would do it on every dispatch. The control uses a bare namespace rather than a
    real DTO because a DTO cannot be constructed around a rejected creative at all,
    which would make the assertion vacuous.
    """
    carrier = SimpleNamespace(creatives=[_rejected_creative("creative-meta-attribute-001")])
    assert malformation_problems({"req": carrier}) == []


# ---------------------------------------------------------------------------
# The declaration is graded against the pin's ACTUAL verdict — both directions
# ---------------------------------------------------------------------------
#
# ``kind`` is the SHAPE of the wrongness; what the SELLER OWES the buyer for those bytes
# is an INDEPENDENT axis, which is why ``obligation`` carries it rather than being
# derived from a partition of kinds. Measured counterexample to any such partition:
# ``uc006_sync_creatives.py:6190`` is declared ``semantic`` and the pin REJECTS it
# (format_id.id must match ``^[a-zA-Z0-9_-]+$``), while a DIFFERENT semantic case — an
# unknown but well-formed format id — the pin ACCEPTS and the seller's rules refuse.
#
# The comparison is a PARTITION — is the declared code INVALID_REQUEST, and does the pin
# agree — because that is the only distinction the pin can derive:
#
#     INVALID_REQUEST  and the pin ACCEPTS       -> REPAIRED malformation. Report it,
#                                                   naming the site and the kind.
#     ANY OTHER CODE   and the pin REJECTS       -> MIS-DECLARED. Report it, quoting the
#                                                   pydantic error.
#     any code at all, and the pin's ONLY
#     objection is an UNDECLARED KEY             -> REFUSED. Not a malformation at all,
#                                                   and NOT author-trusted: the pin's
#                                                   reason set decides, not the marker.
#     the pin's verdict matches the declaration  -> nothing to report.
#
# THE AXIS USED TO BE A BOOLEAN, and one boolean spanned two obligations. MEASURED on
# CreativeAssetRequest, reproduced in this file's own controls:
#
#     ENVIRONMENT=development  extra=forbid   extra key only -> REJECTED [extra_forbidden]
#                                             missing assets -> REJECTED [missing]
#     ENVIRONMENT=production   extra=ignore   extra key only -> ACCEPTED
#                                             missing assets -> REJECTED [missing]
#
# So pin_rejects=True meant one thing for bytes the model refuses on their MERITS and
# another for bytes the boundary STRIPS, and a declaration on the second kind was honest
# in development and reported itself REPAIRED in production, from the same literal. The
# obligation names the wire code instead, which is both environment-independent and the
# thing a Then step needs in order to be written at all.
#
# WHY IT IS NEEDED: the gate USED TO skip every declared item without validating it, so a
# declaration that had stopped being true was invisible. Once a ``CreativeAssetRequest``
# factory exists, ``malformed("absent_key", why, Factory.payload())`` declares an absence
# the factory has just supplied — the marker lies and the gate says nothing.
#
# THE UNDECLARED HALF IS UNCHANGED and is anchored by two controls already in this file
# rather than restated here: ``test_positive_control_runtime_gate_flags_unmarked_malformation``
# (undeclared + pin-rejected still fails) and
# ``test_negative_control_runtime_gate_passes_a_conformant_literal`` (undeclared +
# conformant still passes). A declaration stays REQUIRED only on items the pin rejects
# and is never demanded on a conformant payload — b341x.1's deliberate asymmetry, which
# grading declared items in both directions does not disturb.


@pytest.mark.arch_guard
@pytest.mark.parametrize(
    ("declared", "must_report"),
    [
        pytest.param(
            malformed(
                "absent_key",
                "declares the assets key absent — but these bytes carry it, which is exactly "
                "what a factory default does to a malformation",
                {**_CONFORMANT_CREATIVE, "creative_id": "creative-meta-repaired-001"},
                obligation=ErrorCode.INVALID_REQUEST,
            ),
            ("creatives[0]", "creative-meta-repaired-001", "absent_key", "REPAIRED"),
            id="repaired",
        ),
        pytest.param(
            malformed(
                "absent_key",
                "claims the pin tolerates a creative with no assets key; measured, it does not",
                _rejected_creative("creative-meta-misdeclared-001"),
                obligation=ErrorCode.VALIDATION_ERROR,
            ),
            ("creatives[0]", "creative-meta-misdeclared-001", "Field required", "MIS-DECLARED"),
            id="mis-declared",
        ),
        pytest.param(
            malformed(
                "absent_key",
                "no assets key: exercises the preview-failure branch",
                _rejected_creative("creative-meta-honest-reject-001"),
                obligation=ErrorCode.INVALID_REQUEST,
            ),
            (),
            id="honest-rejection",
        ),
        pytest.param(
            malformed(
                "empty_string",
                "an empty name is conformant to the pin and wrong downstream, which is the scenario's subject",
                {**_CONFORMANT_CREATIVE, "creative_id": "creative-meta-honest-accept-001", "name": ""},
                obligation=ErrorCode.VALIDATION_ERROR,
            ),
            (),
            id="honest-acceptance",
        ),
        pytest.param(
            malformed(
                "semantic",
                "a pre-3.1.1 snippet key the schema never declared; the only thing wrong with these "
                "bytes is a key the boundary strips, which is not a malformation",
                {**_CONFORMANT_CREATIVE, "creative_id": "creative-meta-undeclared-key-001", "snippet": "<div/>"},
                obligation=ErrorCode.INVALID_REQUEST,
            ),
            ("creatives[0]", "creative-meta-undeclared-key-001", "UNDECLARED KEY", "_accept_only_declared_fields"),
            id="undeclared-key-is-refused",
        ),
    ],
)
def test_declaration_is_graded_against_the_pins_actual_verdict(
    declared: _Malformed, must_report: tuple[str, ...]
) -> None:
    """A DECLARED item is validated rather than skipped, and every disagreement fails."""
    problems = malformation_problems({"creatives": [declared]})
    if not must_report:
        assert problems == [], (
            f"the declaration agrees with the pin (obligation={declared.obligation.name}), so the "
            "gate must stay silent:\n" + "\n".join(problems)
        )
        return
    assert len(problems) == 1, (
        f"a declaration the pin disagrees with (obligation={declared.obligation.name}) must be "
        f"reported exactly once; got {len(problems)}. Skipping declared items is how a "
        "declaration that has stopped being true stays invisible:\n" + "\n".join(problems)
    )
    missing = [needle for needle in must_report if needle not in problems[0]]
    assert not missing, f"the report omits {missing}, so nobody can act on it:\n{problems[0]}"
