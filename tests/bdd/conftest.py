"""
BDD test configuration and fixtures.

Every scenario runs against real production code through harness environments:
  - UC-005 (Creative Formats): CreativeFormatsEnv
  - UC-004 (Delivery Metrics): DeliveryPollEnv / WebhookEnv / CircuitBreakerEnv

There is no stub mode — steps call the harness directly and assert on
real response objects.

Unimplemented scenarios (missing step definitions) are auto-xfailed at runtime
via ``pytest_runtest_makereport``. No metadata or @pending tags needed — the
code is the source of truth.

Scenarios for unimplemented *production* features use explicit ``xfail`` markers
with a reason (e.g., "MCP wrapper does not accept disclosure_positions").
"""

from __future__ import annotations

import dataclasses
import functools
import os
import re
import ssl
from collections.abc import Callable, Generator, Sequence
from contextlib import AbstractContextManager, contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple, NoReturn

import pytest

from scripts.audit import storyboard_spec
from tests.helpers.bdd_transport import NODEID_TRANSPORTS, transport_of
from tests.helpers.ledger import load_ledger_nodeids
from tests.helpers.marker_names import derive_marker_names

# Known mock-incompatible e2e_rest BDD scenarios — these dispatch over real HTTP
# to the separate server, so in-process mock injection (set_registry_formats /
# set_adapter_response / account billing-state fixtures) is invisible to it and
# the scenario cannot pass. xfail(strict=False)'d by exact nodeid in the
# collection hook. Regenerate from a clean in-network e2e_rest run. See the beads
# ledger task. File lives next to this conftest.
_E2E_REST_KNOWN_FAILURES: frozenset[str] = load_ledger_nodeids(Path(__file__).parent / "e2e_rest_known_failures.txt")

if TYPE_CHECKING:
    # Real types for the EnvRoute callbacks. Under TYPE_CHECKING so
    # the annotations stay honest without importing the harness at conftest
    # import time.
    from tests.harness._base import BaseTestEnv
    from tests.harness.transport import E2EConfig

# Register step definition modules as pytest plugins so that the fixtures
# created by @given/@when/@then decorators are visible to pytest-bdd's
# fixture lookup. Simple ``import`` is not enough — pytest only discovers
# fixtures from conftest files and registered plugins.
pytest_plugins = [
    "tests.bdd.payload_capture",
    "tests.bdd.scenario_liveness",
    "tests.bdd.steps.generic.given_auth",
    "tests.bdd.steps.generic.given_config",
    "tests.bdd.steps.generic.given_entities",
    "tests.bdd.steps.generic.given_media_buy",
    "tests.bdd.steps.generic.when_request",
    "tests.bdd.steps.generic.then_success",
    "tests.bdd.steps.generic.then_error",
    "tests.bdd.steps.generic.then_payload",
    "tests.bdd.steps.generic.then_schema",
    "tests.bdd.steps.domain.uc004_delivery",
    "tests.bdd.steps.domain.uc002_create_media_buy",
    "tests.bdd.steps.domain.uc002_nfr",
    "tests.bdd.steps.domain.uc002_task_query",
    "tests.bdd.steps.domain.uc002_v31_request",
    "tests.bdd.steps.domain.uc002_v31_proposal_targeting",
    "tests.bdd.steps.domain.uc002_invariants",
    "tests.bdd.steps.domain.uc003_update_media_buy",
    "tests.bdd.steps.domain.uc003_ext_error_scenarios",
    "tests.bdd.steps.domain.uc003_storyboard_generic_client",
    # UC-026 was disconnected in TWO places, and this was the second: the module
    # holding its 119 step definitions was never registered, so pytest-bdd saw no
    # binding for any of its sentences. Together with the missing ENV_ROUTES row
    # (see "UC-026" in _UC_BUCKET_ROUTES) that is why all 75 scenarios graded
    # nothing while the file kept being maintained.
    "tests.bdd.steps.domain.uc026_package_media_buy",
    "tests.bdd.steps.domain.uc006_sync_creatives",
    "tests.bdd.steps.domain.uc006_storyboard_creative_sync",
    "tests.bdd.steps.domain.uc006_dry_run_parity",
    "tests.bdd.steps.domain.uc005_creative_formats",
    "tests.bdd.steps.domain.uc005_format_id_shape",
    "tests.bdd.steps.domain.uc005_format_id_third_party",
    "tests.bdd.steps.domain.uc005_peer_catalog",
    "tests.bdd.steps.domain.signing_enforcement",
    "tests.bdd.steps.domain.uc010_capabilities",
    "tests.bdd.steps.domain.uc011_accounts",
    "tests.bdd.steps.domain.admin_accounts",
    "tests.bdd.steps.domain.uc_get_products_inventory",
    "tests.bdd.steps.domain.uc_get_products_pricing",
    "tests.bdd.steps.domain.uc_get_products_format_options",
    "tests.bdd.steps.domain.uc_get_products_format_id_filter",
    "tests.bdd.steps.domain.uc_get_products_canonical_format_filters",
    "tests.bdd.steps.domain.uc_format_satisfaction",
    "tests.bdd.steps.domain.egress_ssrf",
    "tests.bdd.steps.domain.local_constraint_relaxations",
    "tests.bdd.steps.domain.local_context_echo",
    "tests.bdd.steps.domain.tenant_identification",
    "tests.bdd.steps.domain.agent_card_discovery",
    "tests.bdd.steps.domain.publisher_format_catalog",
    "tests.bdd.steps.domain.publisher_authorization",
    "tests.bdd.steps.domain.pre_dispatch_refusals",
    "tests.bdd.steps.domain.codes_open_vocabulary",
    "tests.bdd.steps.domain.security_wire_safety",
    "tests.bdd.steps.domain.security_tenant_isolation",
    "tests.bdd.steps.domain.database_fail_fast",
    "tests.bdd.steps.domain.protocol_version_negotiation",
]

# ---------------------------------------------------------------------------
# Auto-xfail: missing step definitions
# ---------------------------------------------------------------------------
# Instead of predicting which scenarios are "pending" via metadata tags,
# we let pytest-bdd tell us at runtime. If a scenario fails because a step
# definition is missing, we convert the failure to xfail. The code is the
# source of truth — no stale metadata needed.

# nodeid -> human-readable classification of the step that actually failed,
# populated by the pytest_bdd_step_* hooks below. Consumed (and popped) by
# pytest_runtest_makereport's dormancy-vs-production-gap tripwire (#1721 M4):
# a strict-xfail whose reason claims a graded "production gap" but whose real
# failure is a missing step binding or a Given-side setup error is a
# MISCLASSIFIED entry -- dormancy masquerading as a graded gap, exactly the
# pattern six independent reviewers converged on. This is a bounded
# conftest function extending the existing tripwire, not a new guard file.
_STEP_ERROR_CLASSIFICATION: dict[str, str] = {}


#: nodeid -> the dormancy BASELINE KEY for a missing binding, "<keyword> <normalized step>".
#: Separate from the human-readable text above because the key must survive scenario
#: edits that the message deliberately includes (line numbers).
_MISSING_STEP_KEY: dict[str, str] = {}


def _normalize_step_text(name: str) -> str:
    """Collapse Examples-row variation so one gap is one baseline entry.

    A Scenario Outline substitutes its placeholders before the lookup fails, so the
    SAME missing binding arrives here once per row with different literals baked in.
    Without this, adding a row to an already-dormant scenario would read as NEW
    dormancy and fail a build for no loss of coverage. Quoted literals, bare numbers
    and inline objects become placeholders; the step's identity is what remains.
    """
    text = re.sub(r'"[^"]*"', '"<>"', name)
    text = re.sub(r"\b\d+\b", "<n>", text)
    text = re.sub(r"\{[^}]*\}", "{<>}", text)
    return re.sub(r"\s+", " ", text).strip()


def pytest_bdd_step_func_lookup_error(request, feature, scenario, step, exception) -> None:  # noqa: ANN001
    """Record that this scenario's failure is a missing step BINDING (dormancy)."""
    _STEP_ERROR_CLASSIFICATION[request.node.nodeid] = (
        f"a missing step definition for {step.type} {step.name!r} (line {step.line_number})"
    )
    _MISSING_STEP_KEY[request.node.nodeid] = f"{step.type} {_normalize_step_text(step.name)}"


def pytest_bdd_step_error(request, feature, scenario, step, step_func, step_func_args, exception) -> None:  # noqa: ANN001
    """Record a Given-side setup failure -- test-wiring, not the graded behavior.

    Only the FIRST failing step's classification is kept (a scenario has one
    failure); only Given steps are flagged here -- a When/Then failure is (by
    construction) the scenario grading the behavior it exists to grade, never
    dormancy.
    """
    if step.type == "given" and request.node.nodeid not in _STEP_ERROR_CLASSIFICATION:
        _STEP_ERROR_CLASSIFICATION[request.node.nodeid] = (
            f"a Given-side setup error on {step.name!r} (line {step.line_number}): {exception!r}"
        )


DORMANT_SCENARIOS_PATH = Path(__file__).parent / "dormant_scenarios.txt"


@functools.lru_cache(maxsize=1)
def _dormant_baseline() -> frozenset[tuple[str, str]]:
    """The committed set of (scenario tag, missing step) pairs that already grade nothing.

    Read once. See ``dormant_scenarios.txt`` for why the key is the TAG and the STEP
    rather than anything positional, and why enforcement is per-test rather than a
    count.
    """
    entries = set()
    for line in DORMANT_SCENARIOS_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or " :: " not in line:
            continue
        tag, step = line.split(" :: ", 1)
        entries.add((tag.strip(), step.strip()))
    return frozenset(entries)


def _scenario_tag(item: pytest.Item) -> str | None:
    """The scenario's ``T-...`` tag, which is its stable identity across rewrites."""
    return next((k for k in item.keywords if re.match(r"^T-[A-Z0-9-]", k)), None)


def _record_dormancy(item: pytest.Item, report: pytest.TestReport) -> bool:
    """Report a missing-binding scenario AS DORMANCY, and refuse a NEW one.

    Returns True when the scenario is an already-recorded dormant entry (caller
    converts it to xfail), False when it is new (caller leaves it FAILING).

    WHY THIS EXISTS. A scenario whose step has no binding executes nothing, and the
    auto-convert below reported it as a plain xfail -- indistinguishable in any
    summary from a graded spec-production gap. A suite could shrink to nothing while
    every run stayed green, which is the state that audit found.

    The judgement was already in this file and was simply not reached:
    ``pytest_bdd_step_func_lookup_error`` classifies a missing binding as dormancy,
    and ``_classify_strict_xfail_dormancy`` fails a strict-xfail that CLAIMS a
    production gap when the cause is that classification. But the auto-convert's own
    reason claims nothing, so it sailed past the check written for it. This routes it
    through the same vocabulary instead of adding a third rule beside the two
    (#1929).

    The key is published as a ``user_property`` because pytest-json-report does NOT
    serialize ``wasxfail``: the reason string is invisible in the JSON reports, so the
    only trace of dormancy there is the exception class inside a traceback. Measuring
    this required knowing that trick, and the first attempt at it returned zero
    against a real count of 1356. A user_property IS serialized, so
    the next audit does not depend on folklore.
    """
    tag = _scenario_tag(item)
    step_key = _MISSING_STEP_KEY.pop(item.nodeid, None)
    if step_key is None or tag is None:
        return True  # not a pytest-bdd scenario we can key; leave prior behaviour

    # CONSUME the classification. ``_classify_strict_xfail_dormancy`` exists to catch an
    # xfail that CLAIMS a production gap while the real cause is dormancy; once this
    # function has named the dormancy honestly there is nothing left for it to catch, and
    # its early return on a missing classification is exactly the right seam to use.
    #
    # This is also what keeps the two rules from being coupled by SUBSTRING. That
    # tripwire greps candidate reasons for "production gap"/"spec-production", so the
    # first draft of the honest reason below -- which ended "this is NOT a graded
    # spec-production gap" -- MATCHED IT and turned every recorded dormant scenario into
    # a MISCLASSIFIED failure. 138 of them, from a disclaimer. Popping removes the
    # coupling; the reason text avoids those words as well, so re-introducing the
    # coupling would take two mistakes rather than one.
    #
    # The tripwire's other job is untouched: a scenario rescued by an explicit strict
    # xfail MARKER never reaches here (it is not ``report.failed``), so a marker lying
    # about a production gap is still caught there.
    _STEP_ERROR_CLASSIFICATION.pop(item.nodeid, None)

    item.user_properties.append(("dormant_scenario", f"{tag} :: {step_key}"))
    if (tag, step_key) in _dormant_baseline():
        # KEEP THE "Step definition not found:" PREFIX. It is not decoration: three
        # other instruments classify this event by matching it, and
        # ``scenario_liveness._classify_reason`` buckets anything that does not start
        # with it as "ledgered", which then sets ``harness_wired=True`` on a scenario
        # that binds no steps at all -- an instrument overstating its own coverage,
        # which is the exact fault
        # ``test_provenance_tag_is_a_recorded_field_not_a_collection_filter`` exists to
        # catch. Rewording this line without the prefix silently flipped two dormant
        # UC-006 scenarios to "wired"; the detail is appended AFTER the prefix so the
        # reason can stay honest without being the machine-readable channel.
        #
        # It is not the ONLY channel either -- the user_property above and
        # scenario_liveness's typed classification both carry it, so losing this
        # prefix costs a worse message rather than a wrong measurement.
        report.wasxfail = (
            f"Step definition not found: DORMANT (test-wiring) — no step definition for "
            f"{step_key!r} in {tag}, so this scenario grades nothing. Recorded in "
            f"tests/bdd/dormant_scenarios.txt; closing the hole means writing the step."
        )
        return True
    report.outcome = "failed"
    report.wasxfail = ""
    report.longrepr = (
        f"NEW DORMANT SCENARIO: {item.nodeid}\n"
        f"  {tag} has no step definition for {step_key!r}, so this scenario grades NOTHING.\n"
        f"  It is not in tests/bdd/dormant_scenarios.txt, so it is new coverage loss.\n"
        f"  Wire the step. Do NOT add a line to that file -- the list may only shrink.\n"
        f"  (If you just deleted a step definition, this is what that deletion cost.)"
    )
    return False


def _classify_strict_xfail_dormancy(item: pytest.Item, report: pytest.TestReport) -> None:
    """Fail loud when a strict-xfail claiming a production/spec gap is actually dormancy.

    Checks BOTH an explicit ``xfail`` marker's reason AND a ``wasxfail`` string
    this same hook may have just set (the missing-step-definition auto-convert
    above) -- either can carry the misleading "production gap" wording
    exhibited. Leaves alone any xfail that already reports honestly (e.g. "UC-010
    harness wiring not extended... dormant, never graded" names itself
    correctly) or that grades a real Then/When failure.
    """
    classification = _STEP_ERROR_CLASSIFICATION.pop(item.nodeid, None)
    if classification is None:
        return
    reasons = [str(report.wasxfail)] if getattr(report, "wasxfail", None) else []
    reasons += [str(m.kwargs.get("reason", "")) for m in item.iter_markers("xfail")]
    if not any("production gap" in r.lower() or "spec-production" in r.lower() for r in reasons):
        return
    if report.outcome not in ("skipped", "failed"):
        return
    report.outcome = "failed"
    report.wasxfail = ""
    report.longrepr = (
        f"MISCLASSIFIED strict-xfail: {item.nodeid} is cited as a production/spec gap "
        f"but the underlying failure is {classification} -- this is DORMANCY (test-wiring), "
        "not a graded production gap. Fix the wiring, or correct the xfail reason "
        "to say so honestly, before recording an xfail."
    )


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo) -> Generator[None, None, None]:
    """Auto-xfail scenarios that fail due to genuinely missing step definitions.

    Only StepDefinitionNotFoundError and NotImplementedError are converted to
    xfail. KeyError is NOT caught — use pytest.skip() in _harness_env for
    scenarios without a harness instead of relying on runtime KeyError interception.
    """
    outcome = yield
    report = outcome.get_result()

    if report.when == "call" and report.failed and call.excinfo is not None:
        from pytest_bdd.exceptions import StepDefinitionNotFoundError

        from tests.harness._realize import E2EUnsupportedSetup

        if call.excinfo.errisinstance(StepDefinitionNotFoundError):
            # Dormancy, not an expected failure. _record_dormancy names it honestly and
            # refuses a scenario that is not already on the committed baseline.
            if _record_dormancy(item, report):
                report.outcome = "skipped"
        elif call.excinfo.errisinstance(NotImplementedError):
            report.outcome = "skipped"
            report.wasxfail = f"Not implemented: {call.excinfo.value}"
        elif call.excinfo.errisinstance(E2EUnsupportedSetup):
            # A mock-setup intent the live e2e stack has no surface for. The
            # reason is declared at the env method (not a nodeid ledger), so it
            # is visible in the report. Non-strict xfail — in-process transports
            # of the same scenario still run normally.
            report.outcome = "skipped"
            report.wasxfail = f"impl-only setup declared in env: {call.excinfo.value}"

    if report.when == "call":
        _classify_strict_xfail_dormancy(item, report)


# ---------------------------------------------------------------------------
# Auto-register BDD tag markers
# ---------------------------------------------------------------------------


def pytest_configure(config: pytest.Config) -> None:
    """Register BDD tag markers dynamically."""
    # Guard: BDD_E2E_ENABLED is incompatible with xdist. Under -n>0 the e2e_rest
    # transport is silently dropped at collection (the worker's
    # pytest_generate_tests never appends it), so the suite goes green WITHOUT
    # ever running the e2e_rest transport. The ctx fixture's hard-error can't catch
    # this — collection never happens. Turn the silent drop into a hard error.
    # In-network bdd already pins BDD_XDIST_N=0 (docker-compose.e2e.yml). (#1420)
    # Exception: with E2E_PER_WORKER=1 each xdist worker targets its OWN server +
    # DB (Phase B), so e2e_rest CAN run in parallel. The worker inherits
    # BDD_E2E_ENABLED, and the bdd_e2e env runs `-k e2e_rest`, which pytest exits
    # 5 (no tests selected) on if the transport were dropped — so a silent drop
    # can't pass unnoticed. Keep the guard for the shared-server case (where the
    # silent-drop hazard genuinely remains).
    if os.environ.get("BDD_E2E_ENABLED") == "true" and os.environ.get("E2E_PER_WORKER") != "1":
        numprocesses = getattr(config.option, "numprocesses", None)
        if numprocesses not in (None, 0, "0"):
            raise pytest.UsageError(
                f"BDD_E2E_ENABLED=true is incompatible with xdist (-n {numprocesses!r}): "
                "the e2e_rest transport is silently dropped at collection and the suite "
                "passes without ever running it. Run serially (BDD_XDIST_N=0) or use "
                "per-worker servers (E2E_PER_WORKER=1)."
            )

    import pathlib

    features_dir = pathlib.Path(__file__).parent / "features"
    if not features_dir.exists():
        return

    seen: set[str] = set()
    for feature_file in features_dir.glob("**/*.feature"):
        text = feature_file.read_text()
        for match in re.finditer(r"@([\w.\-]+)", text):
            tag = match.group(1)
            if tag not in seen:
                seen.add(tag)
                config.addinivalue_line("markers", f"{tag}: BDD scenario tag")


# ---------------------------------------------------------------------------
# xfail: scenarios for unimplemented production features
# ---------------------------------------------------------------------------
# These tags correspond to features not yet implemented in production code.
# Each xfail has a FIXME pointing to the work needed.

#: The tenant arm of BR-RULE-017 on update_media_buy, for every route that holds a
#: tenant-only approval scenario or row: the scenario in _XFAIL_TAGS, the boundary row in
#: _SELECTIVE_XFAIL, and the partition and boundary outline rows in _UC002_UC003_LEDGER.
_UPDATE_TENANT_REVIEW_DIVERGENCE = (
    "update_media_buy consults only the adapter's manual_approval_required, never "
    "tenant.human_review_required, which create_media_buy treats as authoritative, so a "
    "tenant-only review answers completed where submitted is owed; create and update "
    "disagree — prebid/salesagent#2483"
)

_XFAIL_TAGS: dict[str, str] = {
    # ── Wired by this sweep; they FAIL, and that failure is the finding. ──
    # These three scenarios were dormant on main (routed to the uc003/uc006
    # not-wired catch-alls). This sweep wired them deliberately, because a
    # storyboard step graded them and the matching BDD scenario existed. They
    # now execute and fail, which CORROBORATES an already-filed gap from the
    # opposite direction: the GitHub issue predicted the storyboard step was
    # unreachable; the BDD scenario independently shows the behavior is absent.
    #
    # Ledgered, not hidden: each carries the issue that owns the fix, and each
    # graduates the moment that issue lands. See the PR description's
    # "corroborated gaps" table for the full evidence chain.
    #
    # Re-derived at this head; the previous reason here was wrong in both of its
    # claims. Re-cancel does NOT return silent success and production DOES have a
    # terminal-state guard: src/core/tools/media_buy_update.py:411 raises
    # AdCPGoneError, which the boundary translates to INVALID_STATE
    # ("Cannot update media buy in terminal state: canceled"). The scenario fails
    # on the CODE, not on the absence of enforcement.
    #
    # GRADUATED: T-UC-003-storyboard-not-cancellable-on-recancel. Its diagnosis was exactly
    # right and its graduation trigger was wrong. The gap WAS code specialization -- the
    # pinned enum carries both codes, both `recovery: correctable`, and
    # BR-UC-003-update-media-buy.feature states the split: INVALID_STATE covers non-cancel
    # updates to a terminal buy, NOT_CANCELLABLE is reserved for re-cancel attempts. What
    # the entry then said was "no issue currently owns specializing the code on
    # update_media_buy; this entry graduates when one lands", and that is a condition no
    # code satisfies: the specialization is three lines at the guard the entry itself cites.
    # It reads `req.canceled` at the terminal-state branch and raises
    # AdCPNotCancellableError for a cancel, AdCPGoneError for anything else. The class did
    # not exist either, so the code had a graded scenario and nothing bound to it; a fixture
    # named it on the base class instead, which is how that survived.
    # NOT_CANCELLABLE also had no row in codes.py's _HTTP_STATUS and so answered the 500
    # default, which this scenario's own "should NOT be a 500" line refuses -- it is 410 now,
    # in the band whose comment is this code's sentence ("the resource's own status forbids
    # the operation"). Measured as XPASS(strict) before the marker came out, not inferred
    # from it going green.
    # Graduated (GH #1075, sync_creatives half): T-UC-006-idempotency-replay and
    # T-UC-006-idempotency-conflict. Both reasons are now false of production —
    # 981776bdb gave sync_creatives the shared replay path (src/core/idempotency_replay.py:
    # probe → conflict → cache), so a repeated key replays the stored envelope and a
    # reused key with a different canonical payload raises IDEMPOTENCY_CONFLICT.
    #
    # Per the graduation workflow, both were inspected before the rows came out rather
    # than removed on the strength of a green mark: the scenarios carry the full
    # obligation (the replay Then counts approval workflow steps against a pre-retry
    # baseline and asserts the per-creative `changes` list stayed empty; the conflict
    # Then asserts code AND recovery through the wire envelope via
    # ``result.assert_wire_error``), the Given performs the first sync through the
    # scenario's OWN transport so the retry is indistinguishable from a network retry,
    # and the demanded code/recovery match the pinned enum.
    #
    # a2a XPASSed alone only because the strict marker deselected the mcp/rest siblings;
    # the marker's removal re-selects them, and all three pass because replay is decided at
    # the shared boundary (``src/core/tools/_boundary.py``). It used to be per-transport
    # plumbing, and MCP was the transport that stopped threading the hash -- so replay was
    # dead there alone.
    # No sibling entry in e2e_rest_known_failures.txt.
    # FIXME: UC-003 main/alt-timing — production doesn't populate these fields
    # Steps have hard assertions now; xfail at scenario level until production catches up.
    "T-UC-003-main": "implementation_date, budget, sandbox not populated in update response — spec-production gap",
    # FIXME: UC-003 pause — sandbox flag not populated in update response
    "T-UC-003-alt-pause": "sandbox not populated in pause response — spec-production gap",
    # FIXME: UC-003 optimization_goals — affected_packages empty in response
    "T-UC-003-alt-optimization-goals": "affected_packages not populated for optimization_goals changes — spec-production gap",
    # FIXME: UC-003 ext-t — invoice_recipient authorization (BR-RULE-214) not implemented;
    # production accepts the override without an authorization check, so no VALIDATION_ERROR is raised.
    "T-UC-003-ext-t": "invoice_recipient authorization not implemented (BR-RULE-214) — production gap",
    # ── create_media_buy and update_media_buy disagree (#2483) ──
    # Each scenario states, on update_media_buy, work create_media_buy already does. Neither
    # tool is patched alone: #2483 moves both onto shared services, and each entry
    # graduates (XPASS strict on all four transports) when it lands.
    "T-UC-003-local-inline-creatives-ad-server-push": (
        "update_media_buy syncs and assigns a package's inline creatives but never calls the "
        "ad server's add_creative_assets, which create_media_buy does; create and update "
        "disagree — prebid/salesagent#2483"
    ),
    # The tenant arm of BR-RULE-017. Its Given now sets the tenant flag alone (see
    # given_tenant_human_review_flag), so the scenario grades the tenant column; before, the
    # Given also raised the adapter's flag and the pass rode the adapter arm.
    "T-UC-003-approval-tenant": _UPDATE_TENANT_REVIEW_DIVERGENCE,
    # Graduated: T-UC-003-ext-u (new_packages refused UNSUPPORTED_FEATURE). XPASS(strict) on
    # a2a, mcp, rest and e2e_rest in box run innet_291026_0131. Inspected per
    # .claude/rules/workflows/xpass-graduation.md: the scenario states the full obligation
    # (BR-RULE-217 INV-1, code, suggestion), and the code is the one
    # media-buy/update-media-buy-request.json /properties/new_packages permits at the pin
    # ("MUST reject with ACTION_NOT_ALLOWED (preferred) or UNSUPPORTED_FEATURE (legacy)").
    # The Givens build a complete package-request through the shared builder and read the
    # seller's advertisement from _adcp_status_and_actions (non-empty, no add_packages); the
    # Thens grade the wire envelope (_wire_code, no reconstructed fallback). Production:
    # media_buy_update._refuse_unsupported_actions raises AdCPCapabilityNotSupportedError
    # above the UoW from UNSUPPORTED_ACTIONS, the declaration that also keeps add_packages out
    # of valid_actions[]. Not in e2e_rest_known_failures.txt.
    # FIXME: UC-002 ASAP — response doesn't expose resolved start_time
    "T-UC-002-alt-asap": "response lacks resolved start_time field — spec-production gap",
    # Graduated: T-UC-002-inv-087-5 and T-UC-002-inv-087-6. Both entries XPASSed strictly on
    # a2a, mcp, rest and e2e_rest in measure_innet_291026_1202, the first run in which the
    # uc002-not-wired catch-all did not xfail them at setup. -5 grades acceptance of tied
    # priorities against the success branch and a created buy (core/optimization-goal.json
    # resolves ties by array order); -6 grades INVALID_REQUEST, correctable and a suggestion
    # on the wire for an empty optimization_goals array (minItems 1).
    # Graduated: T-UC-002-inv-087-7 (strict XPASS on a2a, mcp, rest and e2e_rest in
    # innet_291026_2041). The scenario names VALIDATION_ERROR for per_ad_spend without
    # value_field, the code production answers: optimization-goal.json value_field says
    # sellers must reject the target kind, a rule the schema does not encode.
    # Graduated: T-UC-005-inv-049-8-holds, together with the -violated and -nofield rows
    # that used to sit in _UC005_PARTIAL_TAGS below. The disclosure_positions filter IS
    # implemented now -- src/core/tools/creative_formats.py applies AND semantics
    # (requested set must be a SUBSET of the format's) over the two-source lookup
    # get_format_disclosure_positions, which is disclosure_capabilities[].position when
    # present and supported_disclosure_positions otherwise, the order
    # media-buy/list-creative-formats-request.json prescribes on the filter itself.
    #
    # The old note here -- "violated/nofield pass vacuously (field rejected at schema
    # level)" -- was WRONG on its stated cause, and being wrong is what kept the gap
    # alive: nothing was ever rejected at the schema level. ListCreativeFormatsRequest
    # inherits disclosure_positions from its library parent and model_fields carries it,
    # so the field was ACCEPTED on every transport and then silently dropped by an _impl
    # that had no filter for it -- a buyer asking for a disclosure position got formats
    # that do not support it, with no error. The vacuity had an unrelated cause: the
    # UC-005 route seeded no tenant in-process, the seller answered with an empty catalog,
    # and an exclusion-only Then passes on any empty result. Both rows now carry a
    # positive control (see the scenarios) so an empty catalog can no longer satisfy them.
    #
    # NOT #1660: that issue is adcp 6.6.0 codegen divergence (the generated request
    # omitting the `type` filter, and missing uniqueItems on
    # disclosure_positions/persistence). Presence was never the defect here; application
    # was. #1660 stays open on its own subject.
    # REGROUNDED, not graduated-by-hope: T-UC-005-main-filtered,
    # T-UC-005-inv-031-1-holds and T-UC-005-inv-031-1-violated were xfailed as
    # "adcp 3.12: type filter removed". Two things were wrong with that. The version
    # does not exist -- `type` left the media-buy list-creative-formats request at the
    # 3.0 major (2.5 declares it) and still stands on the "(Creative Agent)" variant,
    # which this seller does not serve. And the scenarios were stated over a parameter
    # the request never carries, so they graded nothing either way. Each now filters on
    # fields the request declares and production applies -- asset_types
    # (creative_formats.py:365) and name_search (:360) -- with their Givens and Thens
    # unchanged, so the obligations they always claimed (POST-S3, and BR-RULE-031's AND
    # semantics) are reached for the first time. No entry here is needed.
    # T-UC-005-inv-031-2-holds GRADUATED. It was xfailed as "adcp 3.12: type field removed —
    # sort by type then name not possible", which described an OBSOLETE SCENARIO, not a
    # production gap: the rule it graded no longer exists. The scenario now grades the rule
    # that does -- sorted by name, which is what production does
    # (src/core/tools/creative_formats.py:386). It stays xfailed on e2e_rest alone, via
    # _UC005_E2E_FIXTURE_INJECTION_TAGS, because that stack cannot be told to serve
    # specific format fixtures.
    # DELETED: T-UC-005-inv-049-1-holds and -violated. Not a graduation — the two tags
    # ROUTE NOTHING. `git grep T-UC-005-inv-049-1-holds -- tests/bdd/features/` returns
    # nothing, and the invariant tags BR-UC-005 actually carries run inv-049-2 through
    # inv-049-11 (plus -8-capabilities); there is no inv-049-1 scenario to xfail. Both
    # entries are matched by exact set membership (`tag in marker_names`), so the
    # similarly-named -10-* and -11-* tags were never covered by them either. They also
    # stood in _UC005_E2E_FIXTURE_INJECTION_TAGS, equally inert, and go from there in the
    # same change.
    #
    # Their shared citation was "adcp 3.12", which is not a version that exists in the
    # schema tree. `type` was really removed, at the 3.0 MAJOR — present in 2.5, absent in
    # 3.0/3.1/3.2 — and it still stands on the "(Creative Agent)" variant of the request.
    # Two lanes found this pair independently, which is why the entries are gone once here
    # rather than twice.
    # Graduated: T-UC-005-sandbox-happy. Two halves, both real. The When named no account,
    # so the request was a production request whatever account the Given seeded --
    # sandbox.mdx makes sandbox account-level ("Once a request references a sandbox
    # account, the entire request is treated as sandbox"). And production answered
    # sandbox=None unconditionally. The When now names the seeded account, and
    # _list_creative_formats_impl reads the flag off the account the resolver loaded for it
    # (a request naming an account carries a credential even on this public task), which
    # is the SHOULD sandbox.mdx states: "include `sandbox: true` in success responses".
    # Graduated: T-UC-005-sandbox-validation. "Sandbox validation not triggered" was the
    # When, not the seller: it sent min_width=-1, and the pinned request declares min_width
    # {"type": "integer"} with no minimum, so the seller rightly served it. The When now
    # sends a non-integer, and the Thens assert the INVALID_REQUEST a schema violation
    # earns rather than VALIDATION_ERROR (see the scenario).
    # Graduated 2026-09-15: T-UC-005-main-referrals. Its reason had two halves and neither
    # was production. The first -- "in-process registry mock returns no agents" -- was the
    # MOCK: CreativeFormatsEnv left ``_get_tenant_agents`` as a MagicMock attribute, and
    # MagicMock makes that iterable AND empty, so production walked zero agents and
    # answered ``creative_agents: []``. The env now binds the real method, and the Given
    # seeds a ``creative_agents`` ROW (given_entities.py), which is what production reads
    # in both worlds; the scenario grades POST-S4 on a2a, its only in-process
    # parametrization. The second half -- upstream adcp#7338 on e2e_rest -- is real and
    # unfixable here, so that ONE NODE sits on tests/bdd/e2e_rest_known_failures.txt under
    # the #7338 block with its sibling, rather than parking all transports by tag. The
    # e2e_rest ``break`` this key used to need in the apply loop is gone with it.
    # Graduated: T-UC-005-main. Both halves of its old reason were the Given, not
    # production: it minted fmt_N formats, which carry no assets (so "asset requirements"
    # graded nothing in-process and read as a spec-production gap) and which the live
    # stack cannot serve (E2EUnsupportedSetup on e2e_rest, flagged by the misclassification
    # detector). The Given now draws three reference-catalog formats (given_entities.py),
    # so the scenario grades POST-S1/S2 for real on a2a, mcp and rest. On e2e_rest the live
    # server answers with the WHOLE catalog, pixel_tracker assets included, and the
    # compliance Then fails on adcp#7338 exactly as its siblings do -- that one node is on
    # tests/bdd/e2e_rest_known_failures.txt under the #7338 block, not parked by tag.
    # Graduated: T-UC-005-ext-a ("No tenant context"). Its Givens never removed the tenant
    # -- the token-less credential still addressed the env's seller, and this public task
    # served it -- so no envelope was captured at all, and the AUTH_REQUIRED/TENANT_REQUIRED
    # reason described neither the pin nor the run. The scenario now sends no headers and
    # asserts TENANT_UNDEFINED/terminal, as the UC-010, UC-013 and UC-018 scenarios on the
    # same Given do.
    # Graduated: creative agent partition/boundary tests
    # Steps now dispatch through harness — all 34 tests pass across 4 transports.
    # FIXME(#1660): suggestion field not in production error model
    # Graduated: T-UC-005-ext-b, xfailed as "suggestion field not implemented in error
    # responses". NOTE(ah98 red-step inspection, 2026-07-06) had already found it "NOT
    # graduatable as-is -- the When step no-ops (type filter removed), so the scenario
    # fails on 'operation should fail', not on the missing suggestion". That inspection
    # was right about the cause and could not act on it: the scenario named a `type`
    # filter this seller's request does not declare, so no rejection was reachable and
    # the cited gap was never what stopped it. The When now sends an out-of-enum
    # `asset_types` member, which production does reject, and the scenario passes END TO
    # END on mcp, a2a and rest -- the error code, the `field` pointer naming asset_types,
    # AND the suggestion. Measured: [XPASS(strict)] on all three transports.
    #
    # Not a vacuous pass: `the error code should be` and `the error field should contain`
    # read the wire envelope with no reconstructed fallback (then_error.py:539, :1554),
    # and `suggestion` is wire-first on all three of these transports. `suggestion` is a
    # read-only property over CODE_TABLE, which is why the 2026-07 reason had gone stale.
    # No e2e_rest ledger row for this tag, so there is no sibling to reconcile.
    # Suggestion parity for list_creative_formats stays additionally pinned by
    # tests/integration/test_request_validation_suggestion_parity.py.
    # Graduated (cassini run 4e57e3338ca3407ab0d78d70f3a20a09):
    # T-UC-005-ext-b-disclosure-invalid, -disclosure-empty, -output-empty,
    # -output-invalid, -output-noid, -input-empty, -input-invalid, -input-noid.
    #
    # The gap was never in production — it was in the harness, the same finding as
    # T-UC-002-ext-f above. These scenarios reach production through
    # when_request.py's filter steps, which built ListCreativeFormatsRequest IN THE
    # TEST PROCESS; an out-of-enum disclosure position, an empty array or a FormatId
    # missing a member therefore raised pydantic HERE and never crossed a transport.
    # The recorded reasons ("validation not implemented", "specific validation error
    # codes not implemented") described the harness's own exception, not the seller.
    #
    # With the payload dispatched raw, production answers correctly and visibly:
    #   "A2A boundary translating AdCPInvalidRequestError to envelope: INVALID_REQUEST"
    # which is what the scenarios asked for all along. All eight xpassed strictly.
    # These scenarios are parametrized on a2a ONLY (verified: one test collected per
    # scenario), and none appears in tests/bdd/e2e_rest_known_failures.txt, so there is
    # no sibling-transport or e2e ledger entry to graduate alongside them.
    #
    # GRADUATED — T-UC-005-ext-b-disclosure-dupes. Its reason asserted a fact about the pin
    # and the fact was FALSE: "the pinned schema declares no uniqueItems, so
    # ["prominent","prominent"] violates no schema constraint and production is right to
    # accept it". MEASURED against the pin this tree installs (adcp
    # 9.0.0b2+prs1435.1415.1447.1456, get_adcp_spec_version() == 3.2.1):
    # _schemas/3.2/media-buy/list-creative-formats-request.json AND
    # _schemas/3.2/creative/list-creative-formats-request.json both declare
    # "uniqueItems": true on disclosure_positions — and on disclosure_persistence. So the
    # scenario was never over-specified; the seller was under-implemented, and the entry
    # closed the question in the wrong direction for the whole life of the marker.
    #
    # What hid it: adcp 9's types._ergonomic._patch_unique_enum_list attaches a WrapValidator
    # whose coerce_to_unique_enum_list DEDUPES (list(dict.fromkeys(...))), so the generated
    # model silently rewrote the buyer's duplicate array instead of refusing it, and reading
    # the SDK rather than the schema produced the reason above. The constraint is now asserted
    # on the DTO (ListCreativeFormatsRequest._refuse_duplicate_disclosure_members) and the
    # scenario gets the INVALID_REQUEST / error.field=disclosure_positions / suggestion it
    # always asked for. Verified a2a-only, as the note above records, and absent from
    # e2e_rest_known_failures.txt, so there is no sibling entry to graduate with it.
    # Graduated: T-UC-002-ext-f. The gap was never in production -- it was in the harness.
    # The step built CreateMediaBuyRequest IN THE TEST PROCESS, so an unknown targeting
    # field raised pydantic's ValidationError there and never crossed a transport; the
    # scenario graded the harness's own exception. Dispatching the raw parameter bag lets
    # the payload reach the server, which answers INVALID_REQUEST with a suggestion, which
    # is what the scenario asked for all along (prkv.33).
    # Graduated (#1417): T-UC-002-ext-d. create_media_buy now reads the currency from the
    # pricing option the package SELECTS, not the product's first (3b1339f63), so an
    # option in a currency outside the tenant's CurrencyLimit rows is refused
    # UNSUPPORTED_FEATURE / correctable, the closest code the pinned enum defines ("a
    # requested feature or field is not supported by this seller"). The Given seeds the
    # option through PricingOptionFactory; the code and recovery Thens read the wire only.
    # Strict XPASS on a2a, mcp, rest and e2e_rest in box run innet_281026_1713; not in
    # e2e_rest_known_failures.txt.
    # Graduated (#1417/gh8p.10): duplicate product_id now raises AdCPValidationError
    # with a buyer-facing suggestion ("Each package must reference a distinct
    # product_id ..."), surfaced on the wire. T-UC-002-ext-e passes.
    # GRADUATED: T-UC-002-ext-k. Its entry said "stale .feature expectation, NOT a
    # production gap ... Graduates once adcp-req is reconciled and BR-UC-002 is
    # regenerated", which diagnosed it correctly and then waited on a regen instead of
    # fixing it. tests/CLAUDE.md is explicit that a generated feature is EDITABLE and gets
    # corrected in place, because "wait for an upstream regen" is not a plan -- there may
    # never be one; a marker parked on that condition is permanent. The scenario now asserts
    # BUDGET_EXCEEDED, which 3.1/enums/error-code.json distinguishes from BUDGET_TOO_LOW by
    # direction in BUDGET_EXCEEDED's own description, and the diff is mirrored upstream.
    # FIXME(#1417): proposal-based create_media_buy is an unbuilt spec feature.
    # BR-UC-002-alt-proposal (status: active) + BR-UC-002-ext-l/ext-m define a full
    # proposal flow: resolve proposal_id, expiry check (PROPOSAL_EXPIRED), and
    # total_budget vs total_budget_guidance.min (BUDGET_TOO_LOW). The pinned
    # adcp library CreateMediaBuyRequest carries proposal_id, but production
    # src/core/tools/media_buy_create.py never reads it — no resolve_proposal,
    # no validate_proposal_budget, no proposal store. Scenario-level strict xfail
    # until the proposal feature is built (no proposal masking; Then steps still
    # hard-assert the BR error codes).
    "T-UC-002-ext-l": "BR-UC-002-ext-l: proposal_id resolution / PROPOSAL_EXPIRED unbuilt — proposal feature not implemented in production (spec-production gap)",
    "T-UC-002-ext-m": "BR-UC-002-ext-m: proposal total_budget_guidance.min validation / BUDGET_TOO_LOW unbuilt — proposal feature not implemented in production (spec-production gap)",
    # Graduated (#1417): the .feature now asserts the standard VALIDATION_ERROR
    # (PRICING_ERROR is not in the AdCP vocabulary @04f59d2d5) and production emits it with
    # a recovery suggestion. T-UC-002-ext-n / -ext-n-bid / -ext-n-floor pass; xfails removed.
    # FIXME: production errors lack suggestion field
    # AdCPNotFoundError/AdCPValidationError/AdCPAdapterError raised with details={"error_code": ...}
    # but no details["suggestion"]. Spec requires suggestion for buyer remediation.
    # FIXME: creative/format_id validation errors lack suggestion field
    # ext-g: _validate_creatives_before_adapter_call raises INVALID_CREATIVES without suggestion
    # ext-h: plain string format_id caught by Pydantic, not structured AdCPSalesAgentError
    # Graduated 2026-09-01: the reason named the defect exactly -- "produces Pydantic error,
    # not AdCPSalesAgentError with suggestion". d2d6609da maps a pydantic ValidationError to
    # AdCPInvalidRequestError, so the boundary now emits the typed error WITH a suggestion and
    # the scenario's three wire assertions (fails, code INVALID_REQUEST, suggestion present)
    # all hold. Inspected per .claude/rules/workflows/xpass-graduation.md: the assertions are
    # wire-level, not truthiness, so the pass is not vacuous.
    # Graduated: T-UC-002-ext-h-agent. media-buy/package-request.json @ 3.2.7, "Format
    # selector contract (normative)", codes a legacy format_id that does not normalize, or
    # resolves and satisfies nothing the product declares, UNSUPPORTED_FEATURE. 88edb921b
    # corrected the scenario from REFERENCE_NOT_FOUND to that code. c0e130f1b makes the
    # package selector gate refuse the deprecated lane too: the seeded product declares only
    # display_300x250_image at the reference agent, so a format_id at an unregistered agent is
    # LEGACY_NOT_ACCEPTED and canonical_refusal raises AdCPCapabilityNotSupportedError, whose
    # suggestion comes from CODE_TABLE. The Thens are wire-level (code, suggestion present),
    # and the scenario is not in e2e_rest_known_failures.txt, so all four transports are
    # graded. The serial box run of the UC-002 module that confirms plain PASS is the lead's.
    # Graduated: T-UC-002-ext-i, for the same reason as ext-f above -- the auth error was
    # never reached. With the raw dispatch the request crosses the transport, the auth
    # boundary answers, and its envelope does carry a suggestion (prkv.33).
    # FIXME: adapter failure raises exception instead of returning failed result
    # Production wraps adapter exceptions as AdCPAdapterError and re-raises instead of
    # returning CreateMediaBuyResult(status="failed"). Also no suggestion field on error.
    "T-UC-002-ext-j": "adapter failure raises exception, no failed result envelope or suggestion — spec-production gap",
    # Graduated: T-UC-002-inv-026-2 and T-UC-002-inv-026-4 ("INVALID_CREATIVES error lacks
    # suggestion field"). XPASS(strict) on a2a, mcp, rest and e2e_rest in
    # measure_innet_291026_1202, the first run that dispatched them. Each Then pins its code
    # on the wire (INVALID_STATE for a rejected creative, VALIDATION_ERROR for a format
    # outside the product's set) and requires the wire suggestion.
    # Graduated (#1417/gh8p.10): the request-construction boundary now derives a
    # field-aware suggestion (suggest_validation_fix) and attaches it to the
    # AdCPValidationError, so a missing idempotency_key rejects with a non-empty
    # wire suggestion. T-UC-002-v31-idempotency-missing passes.
    # FIXME: optimization_goals not in adcp v3.6.0 or production schemas
    # PackageRequest(extra='forbid') rejects the field with generic validation error,
    # not spec-expected UNSUPPORTED_FEATURE / INVALID_REQUEST with structured codes.
    "T-UC-002-ext-u": "optimization_goals not in production schemas — spec-production gap",
    # Graduated 2026-09-01 with T-UC-002-ext-h above, same cause: the scenario asserts the
    # operation fails with INVALID_REQUEST, recovery correctable, and a suggestion -- which is
    # what the boundary now emits for a schema rejection. T-UC-002-ext-u (the non-event row)
    # stays ledgered: it is a different assertion and has not been shown to pass.
    # RESOLVED: optimization_goals now accepted by production schemas (UC-003).
    # Removed stale xfails: T-UC-002-partition-optimization-goals, T-UC-002-boundary-optimization-goals
    # Valid rows now pass; invalid rows xfail via _assert_error_outcome _SPEC_PRODUCTION_CODE_MAP.
    # Removed: T-UC-003-partition-optimization-goals, T-UC-003-boundary-optimization-goals, T-UC-003-alt-optimization-goals
    # RESOLVED: UpdateMediaBuySuccess status="submitted" now handled
    # by then_response_status (empty affected_packages = approval pending).
    # Removed T-UC-003-alt-manual xfail — tests pass with the fix.
    # FIXME: catalog validation not implemented in production
    # PackageRequest accepts catalogs (inherited from adcp library) but production
    # code never validates duplicate types or catalog_id existence.
    # Graduated in wave 5 (integrate/wave1 a3687d971): T-UC-002-ext-v (w5b10). create_media_buy
    # now refuses two catalogs of one type with VALIDATION_ERROR (refuse_duplicate_catalog_types).
    "T-UC-002-ext-v-notfound": "catalog validation not implemented in production — spec-production gap",
    # FIXME: proposal-based creation not implemented in production
    # proposal_id exists on adcp library CreateMediaBuyRequest but production code
    # never reads it — no proposal store, no allocation derivation, no budget distribution.
    # The reason used to name the missing proposal store, but the measured failure in
    # measure_innet_291026_1202 is earlier: the Given that seeds the proposal has no step
    # definition, so the conftest's dormancy tripwire rejected the old wording.
    "T-UC-002-alt-proposal": (
        'dormant (test-wiring): no step definition binds \'proposal "prop-2026-001" exists and has '
        "not expired'; create_media_buy also never reads proposal_id, which is the next thing a "
        "wired step will meet"
    ),
    # FIXME: pricing XOR invariant not enforced during create_media_buy
    # Schema-level validate_pricing_option() enforces XOR but _validate_pricing_model_selection()
    # works at ORM level (is_fixed + rate + price_guidance) and doesn't check for both/neither.
    "T-UC-002-inv-006-3": "pricing XOR invariant (both set) not validated in create flow — spec-production gap",
    "T-UC-002-inv-006-4": "pricing XOR invariant (neither set) error lacks suggestion field — spec-production gap",
    # RESOLVED: budget positivity validation now works — removed stale xfail T-UC-002-inv-008-2
    # Graduated: T-UC-002-inv-013-5 (wrong-case "ASAP"). core/start-timing.json admits the
    # const "asap" or a date-time, so "ASAP" violates the schema, and the boundary's DTO
    # parse answers INVALID_REQUEST with field start_time (adcp_error_for's ValidationError
    # branch). The Given sets start_time through _ensure_request_defaults on every transport;
    # the Thens read the wire code and the wire suggestion. Measured XPASS(strict) on a2a,
    # mcp, rest and e2e_rest in run measure_innet_281026_1501; no e2e_rest ledger line.
    # FIXME: sandbox mode not implemented in create_media_buy
    # CreateMediaBuyResult has no sandbox field; no sandbox suppression logic exists.
    # sandbox-production passes vacuously (sandbox absent from response by default).
    # Graduated: T-UC-002-sandbox-happy. XPASS(strict) on a2a, mcp, rest and e2e_rest in
    # measure_innet_291026_1202, the first run that dispatched it: the Thens grade the success
    # branch, status "completed", sandbox true on the wire, and no billing records.
    "T-UC-002-sandbox-validation": "sandbox mode not implemented in create_media_buy — spec-production gap",
    # FIXME(production-gap bead): natural-key sandbox resolution
    # without prior provisioning is unimplemented. _resolve_by_natural_key
    # (account_helpers.py:110) requires the sandbox account to already exist —
    # raises ACCOUNT_NOT_FOUND rather than auto-provisioning — and
    # CreateMediaBuyResult exposes no sandbox field to echo. Step dispatches the
    # real natural-key create on the wire; flips to a pass when sandbox
    # auto-provisioning + the sandbox echo land. BR-RULE-209 INV-8.
    "T-UC-002-sandbox-natural-key": "natural-key sandbox auto-provisioning + sandbox echo not implemented "
    "in create_media_buy (ACCOUNT_NOT_FOUND without prior provisioning) — spec-production gap",
    # Graduated: T-UC-002-alt-creatives. The inline creatives and their assignment rows were
    # persisted all along; the failing Then was packages[0].creative_assignments on the
    # create response. c0085b9df reports each created package's assignments from the rows
    # written (core/package.json creative_assignments), on all four transports.
    # RESOLVED: T-UC-004-webhook-hmac — no longer xfailed on any transport as of
    # salesagent-n78j0.13. The sentence that stood here ("Then steps are pending (no-op),
    # test passes trivially") was TRUE WHEN WRITTEN and has been false since #1291 C1 /
    # salesagent-n78j0.1.4: all three Thens assert, through `env.last_delivery()`, and the
    # last recomputes the HMAC over the received bytes. Corrected rather than deleted
    # because it was read as current during the e2e_rest graduation and pointed the
    # opposite way from the tree.
    # RESOLVED: T-UC-004-webhook-creds-short — DB setup fix exposed that Then steps are pending (no-op).
    # Test passes trivially; real credential assertion gap tracked separately.
    # Graduated: T-UC-002-inv-080-1 ("account field absent"). The entry said production
    # accepts a create_media_buy without account while BR-RULE-080 INV-1 and
    # create-media-buy-request.json /required both demand it. CreateMediaBuyRequest.account
    # is REQUIRED now (it was the last surviving instance of the optional-account
    # shape; update_media_buy and sync_creatives had already been fixed), so
    # an absent account is refused at the request boundary as the scenario always said.
    # FIXME: rate limiting + payload size validation not implemented
    # Rate limiting middleware does not exist (AdCPRateLimitError never raised).
    # No ASGI middleware checks content-length for oversized bodies.
    "T-UC-002-nfr-001": "rate limiting + payload size validation not implemented — spec-production gap",
    # ── UC-010 batch-1 wiring — remaining per-family gaps re-cited to their GH homes ──
    # Verified against a real run 2026-07-14: every entry below fails on all
    # three wire transports (strict holds); per-row / per-transport gaps use
    # _SELECTIVE_XFAIL / _MCP_SELECTIVE_XFAIL instead.
    # T-UC-010-main's live gap, MEASURED not assumed (#1721). The previous reason
    # here claimed reporting_delivery_methods; that was stale -- the scenario never
    # reaches it. It stops EARLIER, at media_buy.portfolio.primary_channels, which
    # comes back ["display"] (the "couldn't determine from adapter" default)
    # because the harness's set_adapter_channels has no realize_e2e write-through:
    # unlike its sibling set_targeting_capabilities, it configures only the
    # in-process adapter mock, so the real MCP/A2A/REST auth chain resolves an
    # adapter that never saw the channels. Verified by running the split scenario
    # against PRISTINE source in a separate worktree: identical failure, so it is
    # pre-existing and not caused by #1721's changes.
    # The fix is the AdapterConfig.test_behavior write-through — owned by this
    # plan's Lane E step 2, tracked as #1871 — NOT a production defect.
    # SPLIT (#1721): the scenario's one SPEC-blocked assert no longer sits here. Its single undeliverable
    # assert -- media_buy.reporting_delivery_methods -- moved to its own scenario,
    # @T-UC-010-main-reporting-delivery, which carries the xfail below. The rest of
    # T-UC-010-main (account.*, supported_pricing_models, media_buy.features,
    # execution.targeting.geo_*, portfolio, last_updated) now EXECUTES on every
    # transport for the first time; those asserts were being masked by this entry.
    "T-UC-010-main-reporting-delivery": "media_buy.reporting_delivery_methods not emitted for THIS "
    "scenario's tenant, which declares nothing and holds no signing key -- and its absence there is "
    "spec-mandated, not a gap: get-adcp-capabilities-response.json must_equal_when requires "
    "webhook_signing.supported=true whenever the method list contains 'webhook', and that value is DERIVED "
    "from an active key this deployment can open on a publishable origin. The emitting case is graded by "
    "@T-UC-010-v31-webhook-signing-required-when, whose Given seeds a keyed tenant and declares the method; "
    "this scenario's Given asks only for a resolvable tenant, so it cannot reach that state",
    # Graduated: _build_adcp_block() now always emits
    # adcp.supported_versions (derived from SUPPORTED_ADCP_VERSIONS) on both
    # the no-tenant and tenant-resolved paths. T-UC-010-ext-a removed.
    # Graduated: T-UC-010-auth-data-identity — capability
    # discovery now resolves the adapter CLASS tenant-only (INV-4), identical
    # for anonymous and authenticated callers.
    # Graduated: T-UC-010-ext-c-a2a — A2A public-skill list
    # now always validates a presented token (adcp_a2a_server.py), rejecting
    # an invalid one with AUTH_INVALID regardless of skill-level auth
    # requirement, matching v3.1.1 error-code.json.
    # Graduated: T-UC-010-ext-c-mcp — MCP ToolResult now
    # pre-serializes via model_dump(mode="json"), so audience_targeting is
    # correctly omitted instead of serialized as null.
    # T-UC-010-ext-d-filter FULLY GRADUATED: the new POST
    # /api/v1/capabilities route carries protocols/context/adcp_version on all
    # 3 transports, so a2a/mcp/rest all now pass (removed from both this dict
    # and the _SELECTIVE_XFAIL rest-only entry below).
    # T-UC-010-ext-d-invalid-value / -empty / T-UC-010-ext-e-echo / -nested / -empty
    # FULLY GRADUATED: GetAdcpCapabilitiesRequest now
    # constructs a real typed GetAdcpCapabilitiesRequest (Pydantic enforces the
    # protocols enum + minItems:1), and _get_adcp_capabilities_impl echoes
    # req.context verbatim onto the response on every transport.
    # Graduated: T-UC-010-ext-d-all-protocols. The reason demanded the signals, governance,
    # sponsored_intelligence and creative sections, which v3.2.7
    # get-adcp-capabilities-response.json makes "Only present if <protocol> is in
    # supported_protocols". The scenario's Thens were corrected to that rule: a filter naming
    # every request-enum value is accepted, media_buy is present, and the four unsupported
    # sections are absent. For a media_buy-only tenant that absence is the spec-true answer,
    # and production already emits it. No e2e_rest_known_failures.txt row existed.
    # Graduated: T-UC-010-v31-supported-versions removed —
    # see T-UC-010-ext-a graduation note above (same _build_adcp_block fix).
    # Graduated: version negotiation now implemented
    # (src/core/version_negotiation.py) — a bad adcp_version/adcp_major_version
    # pin raises AdCPVersionUnsupportedError -> VERSION_UNSUPPORTED on all
    # transports. T-UC-010-v31-version-unsupported /
    # -major-fallback / -build-version-advisory removed from this dict.
    # Wired non-dormant + strengthened: steps execute and grade the
    # spec-pinned shape, then fail on the unemitted/hard-coded block (strict xfail on all transports).
    "T-UC-010-v31-compliance-testing": "compliance_testing block not emitted by the capabilities builder; no comply_test_controller surface — #1724",
    # Graduated: T-UC-010-v31-specialisms. The scenario was corrected to the claims this
    # seller backs (sales-non-guaranteed under media_buy, signal-owned under signals): the
    # pinned specialisms description says "Only list specialisms your agent actually
    # implements", so the creative-generative claim it used to make was not owed.
    # Ledger SHRINK (a recorded gap batch B5): T-UC-010-v31-advisory-errors removed —
    # the capabilities builder now emits top-level advisory errors[] for genuinely
    # faulted discovery lookups (except-path only), so the gap the row recorded is closed.
    # T-UC-010-account-supported-billing / T-UC-010-account-block-presence GRADUATED
    #: account.supported_billing now derives from resolve_supported_billing(tenant)
    # and the account block is now emitted on the tenant-resolved path.
    # Graduated (a recorded gap R1): media_buy.supported_pricing_models now derives from
    # adapter.get_supported_pricing_models() (mirrors products.py:721). T-UC-010-pricing removed.
    "T-UC-010-audience-caps": "media_buy.audience_targeting not emitted by the capabilities builder — #1855",
    # Wired non-dormant + strengthened: steps execute and grade the
    # spec-pinned shape, then fail on the missing block (strict xfail on all transports).
    "T-UC-010-conversion-caps": "media_buy.conversion_tracking not emitted by the capabilities builder — #1855",
    # Graduated: T-UC-010-creative-caps. Rewritten as a seller that does not claim the
    # creative protocol: it grades "creative" absent from supported_protocols and the
    # creative section absent from the wire, which is the pin's answer for this seller.
    # Graduated (a recorded gap R2): CHANNEL_MAPPING now includes sponsored_intelligence,
    # the 20th canonical channel. T-UC-010-channel-all-canonical removed.
    # Wired non-dormant + strengthened: each scenario executes and grades the
    # spec-pinned shapes, then fails on a block the capabilities builder never emits (strict
    # xfail on all transports).
    "T-UC-010-features": "media_buy.content_standards / conversion_tracking / audience_targeting presence-objects not emitted (#1855) and the account block (account.sandbox) not emitted (#1856) by the capabilities builder",
    # _build_geo_postal_areas builds the native country-keyed map correctly --
    # geo_postal_areas is not part of the gap. The remaining gap is the non-geo
    # targeting dimensions never being built.
    "T-UC-010-targeting": "targeting emits only geo_countries/geo_regions/geo_metros/geo_postal_areas — "
    "age_restriction, language, keyword_targets, negative_keywords, geo_proximity not built "
    "— #1857 non-geo targeting capability dimensions",
    # Wired non-dormant + strengthened: each scenario executes and grades the
    # spec-pinned v3.1.1 shape, then fails on a block the capabilities builder never emits
    # (brand is not in supported_protocols; measurement block never built). Strict xfail, all
    # transports.
    # Re-cited #1592 -> #1724 (a recorded gap, owner decision 2026-07-27). The brand family
    # was re-homed ENTIRELY rather than partially delivered: `brand` in supported_protocols
    # commits the seller to `get_brand_identity` (protocols/brand/index.yaml#required_tools),
    # which has zero implementations here, and the schema forbids emitting the block without
    # that protocol claim ("Only present if brand is in supported_protocols"). Emitting roster
    # facts either way would be the over-advertising STRICT exists to prevent.
    "T-UC-010-v31-brand-block": "scenario requires the brand protocol claim, which commits to get_brand_identity (unimplemented), and brand.rights=true, an unbacked tool commitment — #1724",
    # Ledger SHRINK (a recorded gap batch B1): T-UC-010-v31-measurement-catalog removed.
    # The tenant's measurement catalog is a declarable business fact, so the scenario is
    # graded by the capability-declaration store (measurement block + supported_protocols
    # union + the measurement.core experimental-feature implication) rather than ledgered
    # as a permanent production gap.
    # Wired non-dormant + strengthened: each row executes and grades the
    # spec-pinned bound/relation, then fails on all transports because the capabilities builder
    # never derives idempotency from tenant config and runs no version negotiation (#1592).
    # Strict tag-level xfail — every parametrized row fails.
    # T-UC-010-v31-request-signing-monotonicity / T-UC-010-v31-webhook-signing-bounds moved to
    # _SELECTIVE_XFAIL: request_signing/webhook_signing={supported:false} now
    # emitted, so the "valid" rows (which only assert schema-valid subset/disjoint relations or
    # must_equal_when bounds against an unsupported posture) pass; the "invalid" rows (which
    # require the builder to REJECT a relation-violating/out-of-bounds posture with
    # CONFIGURATION_ERROR) still fail. NOTE: a per-tenant config surface DOES now
    # exist (tenants.capability_declarations, #1592 T1a) — what it deliberately lacks is any
    # signing field, under the STRICT capability policy. Re-cited #1592 -> #1291.
    # Graduated: get_idempotency_posture() now returns a
    # typed IdempotencyPosture whose check_bounds() enforces the
    # replay_ttl_seconds/in_flight_max_seconds schema bounds, raising
    # CONFIGURATION_ERROR (terminal) on the invalid rows; the harness
    # CapabilitiesEnv.set_idempotency_posture override lets the boundary rows
    # drive it. T-UC-010-v31-idempotency-ttl-bounds removed from this dict.
    # Graduated: version negotiation now emits a non-empty,
    # release-precision supported_versions in VERSION_UNSUPPORTED details on
    # every row. T-UC-010-v31-version-unsupported-details-bounds removed.
    # ── UC-011 list wiring — graduated; provenance below ───────────────────
    # Graduated: _apply_list_account_filters honors req.account
    # (AccountReference oneOf, both account_id and natural-key branches), forwarded by
    # all 3 transports. T-UC-011-list-account-filter removed.
    # T-UC-011-list-authorization: the Account schema carries no authorization
    # object (account-with-authorization item shape is new in 3.1.1), so the
    # wire items never expose allowed_tasks. Out of scope (GH #1615).
    "T-UC-011-list-authorization": "per-account authorization block (account-with-authorization / allowed_tasks) not "
    "emitted — production Account schema has no authorization field, list items are bare — tracked as GH #1615, "
    "out of #1592 A3 core scope",
    # Graduated: ListAccountsRequest.idempotency_key added --
    # the read wrapper now tolerates the 3.1 idempotency envelope instead of
    # rejecting it under extra=forbid. T-UC-011-list-read-idempotency-tolerance removed.
    # Graduated: settings-update (AccountReference) mode implemented
    # via _process_settings_update_entry (both AccountReference1/account_id and
    # AccountReference2/natural-key branches), mode-exclusivity enforced in _impl before
    # dispatch (VALIDATION_ERROR naming accounts[i]), unmatched references rejected
    # with UNSUPPORTED_PROVISIONING. T-UC-011-sync-settings-update,
    # T-UC-011-sync-settings-update-no-provision, T-UC-011-sync-mode-exclusive removed.
    # Graduated: _check_billing_policy now emits recovery="correctable"
    # + details={scope, supported_billing} (conditionally, honest-absence on an empty
    # policy) on the per-account BILLING_NOT_SUPPORTED error. T-UC-011-ext-c-rejected removed.
    # ── UC-011 per-buyer-agent commercial gate — GRADUATED (#1772) ──
    # Graduated: T-UC-011-billing-agent-gate-reject and -recover removed.
    # _check_agent_billing_permission (src/core/tools/accounts.py) runs after the
    # capability gate and reads principals.billing_passthrough_only off the resolved
    # identity; a passthrough-only agent asking for capability-supported "agent"
    # billing gets BILLING_NOT_PERMITTED_FOR_AGENT with the clamped
    # {rejected_billing, suggested_billing} details, and the recover leg retries with
    # the suggested value read off that response. The passthrough-only Given now
    # writes the principal row, so the state is the same on all four transports.
    # ── UC-011 account-level notification_configs + sandbox capability gate — ALL GRADUATED ──
    # Graduated (T2 increment F4a): T-UC-011-notif-register-paused,
    # -notif-replace-clear and -notif-omit-preserves removed. accounts.notification_configs now
    # persists as a whole-array JSONType column with declarative-replace semantics (omit preserves,
    # [] clears, re-sent subscriber_id replaces in place) and is echoed on both sync_accounts and
    # list_accounts with authentication.credentials scrubbed. The three scenarios grade that surface
    # on a2a/mcp/rest.
    # Graduated: _check_sandbox_capability gate added -- rejects
    # sandbox provisioning with UNSUPPORTED_FEATURE (accounts[i].sandbox) when the
    # tenant's account_sandbox capability is not declared. T-UC-011-sandbox-capability-not-declared removed.
    # ── UC-011 notification_configs per-account rejections ──
    # Graduated (T2 increment F4b): T-UC-011-notif-duplicate-subscriber removed.
    # _check_notification_configs runs pre-persist in BOTH entry handlers and emits a
    # per-account failure inside a transport-level success, with the exact error.field
    # pointers the storyboards grade.
    #
    # Graduated (#2353): T-UC-011-notif-event-scope-reject removed. The event-scope half of
    # the F4b graduation above was ledgered because the gate's branch was unreachable — the
    # DTO typed ``event_types`` with the account-surface enum, so pydantic refused every
    # value the gate refuses, and all three transports answered INVALID_REQUEST at
    # ``accounts.0.…notification_configs.0.event_types.0`` instead of reporting the entry.
    # ``src/core/schemas/notification.py`` now declares the field over the wide
    # ``enums/notification-type.json``, which is what the pinned field asks for in the same
    # sentence that states its enum: the eight off-surface types "are schema-invalid on this
    # surface AND sellers MUST reject those entries as PER-ACCOUNT validation failures …
    # RATHER THAN SILENTLY DROPPING THEM" (core/notification-config.json
    # #/properties/event_types, AdCP 3.2.1; account/sync-accounts-request.json repeats it at
    # #/properties/accounts/items/properties/notification_configs). The enum is still
    # enforced, by ``_check_notification_configs``, which refuses exactly
    # ``NotificationType - EventType`` and names ``notification_configs[i].event_types[j]``
    # — so admitted minus refused IS the account-surface enum, member for member, and a
    # value outside the vocabulary entirely still earns a request-level INVALID_REQUEST.
    # The weakened axis is named in the model's own docstring.
    #
    # tests/storyboard/known_failures.txt:124,137 still carry
    # ``notification_config_event_scope::sync_accounts_rejects_scheduled_account_notification``
    # for a2a and mcp. Those rows are xfail(strict=False) and are NOT graduated here: this
    # ledger's own header says it has not been re-measured against 3.2.1, and only
    # ``tox -e storyboard`` on the stack can say whether that check now passes.
    #
    # Graduated (T2 increment F4c): T-UC-011-notif-activation-proof-fail
    # removed. NotificationProofService performs a bounded proof-of-control challenge BEFORE the
    # write transaction opens; a failed proof
    # rejects the entry with VALIDATION_ERROR at notification_configs[j].url and writes nothing,
    # so the prior array is untouched.
    #
    # Graduated: the fourteen "the webhook payload is compliant with the AdCP delivery
    # webhook spec" scenarios, ledgered as #2058 violation 2. They failed on the ENVELOPE
    # layer because WebhookDeliveryService posted the delivery report bare -- the labelled
    # counter-example at L3/webhooks.mdx :254. Both senders now build the body through
    # ``build_webhook_envelope`` (src/core/webhooks/delivery.py), so there is one shape and
    # it is the envelope. The UC-004 Then steps were re-grounded in the same change: report
    # fields are read from ``result``, at the nesting
    # media-buy-delivery-webhook-result.json declares, rather than from the top level where
    # several of them had been looking and finding nothing.
    #
    # Graduated: the two adcp#7338 whole-scenario tags
    # (@T-UC-005-storyboard-baseline-format-id-object-shape, @T-UC-005-sandbox-production),
    # 6 nodes. THE BLOCK STATED ITS OWN CONDITION -- "graduates when the SPEC pin moves to
    # v3.1.22 or later" -- and the pin is now 3.2: core/format.json admits pixel_tracker,
    # vast_tracker, daast_tracker, card and published_post, the five adcp#7421 added. The
    # condition was met by the SDK bump; what made it visible was the schema-category fix,
    # because until then these scenarios failed on a PinnedSchemaError
    # ('list-creative-formats-response.json' is ambiguous) rather than on the asset union
    # they are ledgered for -- an xfail satisfied by the wrong defect.
    #
    # NOT A VACUOUS XPASS, which is the other thing an xpass can mean and the only reason
    # to refuse graduation. Three measurements:
    #   - the obligation is real: 45 of the 71 formats in
    #     tests/fixtures/creative_formats/reference_formats.json declare a pixel_tracker
    #     asset, so the compliance Then validates documents that genuinely carry one;
    #   - the check DISCRIMINATES: a served pixel_tracker format validates against
    #     core/format.json, and the same format with its asset_type changed to a bogus
    #     value is rejected. It is grading asset types, not accepting anything;
    #   - each scenario carries a specific assertion beyond the compliance line --
    #     "should not include a sandbox field" (BR-RULE-209 INV-5) and "every entry's
    #     format_id should be an object carrying both agent_url and id / no entry's
    #     format_id should be a bare string" (core/format-id.json required [agent_url, id]).
    #     Neither is a presence check.
    #
    # Verified on lane/bdd-category at 549c3bc5d, own agent-db, artifacts redirected:
    # 69 XPASS(strict) over uc005, every one citing adcp#7338 and nothing else, grouping
    # exactly onto these two tags (3 + 3) and the four outlines below (24/18/12/9).
    #
    # tests/bdd/e2e_rest_known_failures.txt keeps its one #7338 row: that ledger names the
    # same spec-pin condition, but only a bdd-in-network run grades it and none was run here.
}

# Selective xfail for parametrized scenarios where only
# some examples exercise unimplemented features. Each entry: (tag, node_id
# substrings that should xfail, reason).
_SELECTIVE_XFAIL: list[tuple[str, set[str], str]] = [
    # ── UC-006 ROUTE PARTITION ──
    # These 16 Scenario Outlines disagree ROW TO ROW, which is why they are here and not
    # in _UC006_WIRED_SCENARIOS: a route matches on a scenario's markers and cannot say
    # "these Examples rows, not those". Wiring them wakes 128 passing nodes; these 70 are
    # the rows that do not pass, parked one row at a time.
    #
    # MEASURED, not inferred from the group. The uc006 catch-all's xfail_reason was set to
    # None locally and the file run against a real Postgres (410 passed / 209 failed /
    # 85 xfailed over its 617 catch-all nodes); each row below carries ITS OWN blocker,
    # and a row is parked only if a node of that row actually failed. A row whose siblings
    # fail is not parked -- that is a row nobody looked at.
    #
    # The reasons are BLOCKER CATEGORIES, deliberately. 81% of what wiring reveals is
    # test-side (missing step definitions, steps that do not cover their own Examples rows,
    # payloads the malformation gate refuses), so "production behaviour not implemented"
    # would reproduce the catch-all's own mislabelling one level finer. Where the blocker
    # is unverified this says so rather than guessing.
    #
    # strict=True is the consumer's default here, so a parked row that starts passing
    # becomes XPASS(strict) and fails -- the list cannot rot quietly.
    # No UC-006 rows are parked here any more. What stood here, and why each is live:
    # boundary-approval's ai-powered row read a column the mapping table does not have
    # (workflow_step_id); the assignments-structure "entry missing a field" rows now send
    # the entry itself, which the request schema refuses; the generative no-GEMINI-key
    # row reads CONFIGURATION_ERROR on the entry, as ext-i does; the validation_mode
    # "partial" row names the code; main-lenient-warnings built its two valid packages
    # from one factory-default id, so they collapsed into one assignment; and the
    # assignment-weight / provenance rows are described where the Given changed.
    # ── GRADUATED: adcontextprotocol/adcp#7338 ──
    # The four UC-005 Scenario Outlines parked row-level here
    # (@T-UC-005-partition-agent-type, -partition-agent-asset, -boundary-agent-type,
    # -boundary-agent-asset, "-valid" rows only) are graduated: 63 nodes, plus the 6 from
    # the two whole-scenario tags in _XFAIL_TAGS, which is the 69 that were XPASS(strict).
    # Two of those four are since DELETED rather than graduated -- the two TYPE
    # outlines graded nothing, see the record below and salesagent-8qela.149 -- so
    # the 63 is a count of what this block released, not of what still exists.
    #
    # THE CONDITION THIS BLOCK NAMED IS MET. It said "graduates when the SPEC pin moves to
    # v3.1.22 or later, not when the agent pin moves". adcp#7421 (merged 2026-09-11, first
    # released v3.1.22) added the five missing branches, and at the 3.2 pin core/format.json
    # admits pixel_tracker, vast_tracker, daast_tracker, card and published_post. The SDK
    # bump earned this; the schema-category fix only made it visible, because until then
    # these rows failed on a PinnedSchemaError ('list-creative-formats-response.json' is
    # ambiguous across creative/ and media-buy/) rather than on the asset union they were
    # parked for. An xfail was being satisfied by a different defect than the one it cited.
    #
    # INSPECTED FOR VACUITY BEFORE REMOVAL, because the other thing an xpass can mean is a
    # scenario too weak to fail. Everything below is measured on lane/bdd-category at
    # 549c3bc5d, own agent-db, artifacts redirected:
    #   - the obligation is real: 45 of the 71 formats in reference_formats.json declare a
    #     pixel_tracker asset, so the compliance Then grades documents that carry one;
    #   - the compliance check DISCRIMINATES: a served pixel_tracker format validates
    #     against core/format.json, and the same format with asset_type set to a bogus
    #     value is REJECTED;
    #   - the asset outlines' second Then is live, not skipped. _assert_filter_content
    #     guards its asset_types content check with `if was_narrowed`, so it would pass on
    #     nothing if production ignored the filter. It does not: measured over the
    #     reference catalog, asset_types=[image] keeps 34/71, [video] 13, [audio] 3,
    #     [text] 19, [html] 7, [javascript] 1, [url] 35 -- every filtered row narrows, so
    #     the branch fires. The `not_provided` rows are correctly exempt.
    #   - the type outlines' second Then asserts the result EQUALS the full registry (the
    #     post-3.12 obligation: ListCreativeFormatsRequest has no `type` field, so every
    #     value dispatches unfiltered) and that every format carries a type attribute.
    #     Falsifiable: a production path that narrowed would fail it.
    #
    # The row-level granularity this block argued for was kept to the end and is why the
    # 16 "-INVALID_REQUEST" rows never needed graduating -- they were never parked.
    #
    # tests/bdd/e2e_rest_known_failures.txt keeps its one #7338 row
    # (test_discover_full_format_catalog[e2e_rest]). That ledger names this same spec-pin
    # condition, so it is very likely graduatable too, but only a bdd-in-network run grades
    # it and none was run here -- graduating it on an in-process measurement would be citing
    # the wrong run, which is what the UC-005 baseline-format-id row in that file refuses on
    # the same grounds.
    # #1721 M4: @T-UC-010-v31-account-sandbox newly wired. The true/false rows
    # pass for real; the "absent" row expects the wire to OMIT account.sandbox
    # (buyer applies the schema default) but _build_account_block
    # (capabilities.py) always assigns an explicit tenant.get("account_sandbox",
    # True) value and never conditionally omits it — same root as the other
    # #1856 account-config-surface entries (require_operator_auth,
    # required_for_products, authorization_endpoint).
    (
        "T-UC-010-v31-account-sandbox",
        {"sandbox absent in response"},
        "account.sandbox is always assigned an explicit boolean by _build_account_block, "
        "never conditionally omitted — #1856 account-config surface",
    ),
    # #1417 wiring surfaced pre-existing UC-003 targeting-overlay gaps
    # (tracked separately). The geo include/exclude overlap partitions DO reach the
    # converged update.py:444 raise and PASS (proving da07); these other partitions
    # hit unrelated gaps: pydantic extra='forbid' on unknown/managed/device_platform
    # fields raising a raw ValidationError before dispatch, GeoProximity requiring
    # lat/lng (geometry/radius/travel_time-only modes + method-conflict unmodeled),
    # frequency_cap field-combo validation, keyword-duplicate detection, and
    # device_type include/exclude overlap validation.
    (
        "T-UC-003-partition-targeting-overlay",
        {
            # GRADUATED on every transport: unknown_field, undeclared_dimension (formerly
            # managed_only_dimension, salesagent-3cs7o.22) and proximity_method_conflict. The recorded gap was "pydantic extra='forbid'
            # raising a raw ValidationError before dispatch", i.e. a rejection that never
            # reached the buyer as an envelope. It does now.
            #
            # Reached by measuring three times, not by deleting the entry: a2a xpassed
            # first, so the rows were split per transport; that run showed mcp xpassing
            # too; removing mcp showed rest xpassing as well. Splitting first is what made
            # each transport's evidence separable -- graduating the bare label on a2a's
            # xpass alone would have been right by luck.
            "multiple_dimensions",
            "device_type_overlap",
            "proximity_geometry",
            "proximity_radius",
            "proximity_travel_time",
            # Graduated: frequency_cap_missing_fields, on all three transports this
            # outline parametrizes (rest, a2a, mcp — enumerated from the collected
            # ids, not assumed; this module has no impl or e2e_rest row, and
            # e2e_rest_known_failures.txt carries no entry for it, so no sibling is
            # left behind). The gap was "frequency_cap field-combo validation": the
            # seller accepted {"max_impressions": 3} with no per/window.
            # core/frequency-cap.json has always declared
            # dependencies.max_impressions -> [per, window]. The SDK's generated class
            # does not compile `dependencies`, so the local FrequencyCap carries the map
            # (SchemaDependencies, src/core/schemas/_dependencies.py), the DTO refuses
            # the document in validated_request (_boundary.py) and the buyer gets
            # INVALID_REQUEST with a suggestion — which is what the scenario demands.
            # Not a vacuous pass: the Then reaches
            # TransportResult.assert_wire_error(code, require_suggestion=True) via
            # _assert_error_outcome, i.e. the captured envelope, not a reconstructed
            # exception. Run: bdd_inprocess at 9c88c3b2e, 6 strict XPASSes, the only
            # failures in 8648 items. The other labels in this set still xfail, so
            # this is one scenario graduated, not the entry emptied.
            #
            # Graduated: keyword_duplicate. The gap was "keyword-dup": the seller accepted
            # a keyword_targets list repeating a (keyword, match_type) pair, which
            # core/targeting.json says sellers SHOULD reject. collect_targeting_violations
            # now reports it (keyword_duplicates), and update_media_buy raises that as
            # INVALID_REQUEST on every transport, the code and suggestion the row grades
            # on the wire envelope (_assert_error_outcome -> assert_wire_error).
        },
        "Pre-existing UC-003 targeting-overlay validation gaps (not da07): pydantic "
        "extra='forbid' / GeoProximity coordinate modes / keyword-dup / device_type overlap",
    ),
    (
        "T-UC-003-boundary-targeting-overlay",
        {
            # GRADUATED on every transport, same three-step measurement as the partition
            # entry above.
            "device_type include/exclude overlap",
            "with travel_time only",
            "with radius only",
            "with geometry only",
            # Graduated: "frequency_cap max_impressions without per", the boundary
            # half of the same gap the partition entry above graduates. Same three
            # transports, same measurement, same wire-envelope assertion.
            # Graduated: "keyword_targets with duplicate", the boundary half of the
            # partition entry's keyword_duplicate, for the same reason.
        },
        "Pre-existing UC-003 targeting-overlay validation gaps (not da07): pydantic "
        "extra='forbid' / GeoProximity coordinate modes / keyword-dup / device_type overlap",
    ),
    # ── #1721 lane D: three UC-018 outlines newly wired ──
    # The lane converts _handle_list_creatives_skill to the shared build_*_request
    # seam and moves the MCP structured->flat sort/pagination coercion into
    # ListCreativesRequest. Only the rows whose behavior that conversion can
    # silently delete are authored; the siblings below grade production the lane does
    # NOT touch, so they are parked PER ROW rather than the whole outline being left
    # dormant at the harness gate (which is how the merge and coercion rows came to be
    # ungraded in the first place). Every entry cites #1721.
    # T-UC-018-partition-filters is GRADUATED IN FULL. The three flat/structured
    # precedence rows are deleted from the feature (there are no flat filter params in
    # 3.1.1, so they graded a precedence rule between a spec field and a non-field), and
    # every remaining row now executes: tags AND / tags_any OR ask jsonb for containment
    # over the creative's own tags, creative_ids is threaded into the query, and the
    # date-range and validation rows are refusals the DTO already makes.
    # T-UC-018-partition-field-selector IS GRADUATED IN FULL. `fields` is read now: the
    # projection narrows the OPTIONAL members and keeps the six list-creatives-response.json
    # marks required, which is the reading that satisfies both halves of the pin and is why
    # the rows are gradeable at all. The one row that did not graduate was deleted rather
    # than parked: its request_params cell named a database fixture, and that obligation is
    # graded by @T-UC-018-inv-149-6-holds.
    # T-UC-018-boundary-pagination IS GRADUATED IN FULL. assignment_count sorting was the
    # last row parked here, and it is implemented: the repository orders by the creative's
    # assignment count through a correlated subquery, and the response carries the count in
    # the assignments block the pin defaults to including — which is what makes the ordering
    # observable on the wire at all. The 60-creative library now seeds rotating counts, so
    # the row cannot pass over a constant column.
    # GRADUATED: the duplicate_positions row of T-UC-005-partition-disclosure and the
    # "duplicate positions" row of T-UC-005-boundary-disclosure. Their shared reason,
    # "disclosure_positions filter/validation not implemented", was half true and half stale:
    # the FILTER has been implemented since creative_formats.py:405, and what was missing was
    # the uniqueItems VALIDATION — which is now on the DTO
    # (ListCreativeFormatsRequest._refuse_duplicate_disclosure_members), so both rows get the
    # INVALID_REQUEST with error.field=disclosure_positions they ask for. Measured XPASS
    # (strict) with these two entries still in place, on a2a/mcp/rest.
    # Graduated: all_positions, no_matching_formats on impl (disclosure filter now partially works)
    # Non-impl transports still fail — handled in transport-aware section below.
    # MCP-specific disclosure xfails are in _MCP_SELECTIVE_XFAIL
    # Graduated: "all 8 positions", "format has no" on impl (disclosure filter now partially works)
    # Non-impl transports still fail — handled in transport-aware section below.
    # MCP-specific boundary disclosure xfails are in _MCP_SELECTIVE_XFAIL
    # DELETED with their outlines: T-UC-005-partition-type-filter and
    # T-UC-005-boundary-type-filter. Each row here parked the ONE example that expected
    # a rejection, which left the outline's five valid rows passing for asserting that
    # type filtering had not happened. Both outlines are gone from the feature.
    # Graduated: T-UC-005-boundary-asset-types (all 4 transports pass — brief/catalog now in enum)
    # Graduated: T-UC-005-boundary-agent-asset — passes now that the When step
    # dispatches through the harness, and it GRADES: the media-buy variant declares
    # asset_types, _partition_agent_asset_types sends it via _call_raw (invalid rows
    # included), and the Examples table keeps real INVALID_REQUEST rows.
    #
    # DELETED with their outlines: T-UC-005-partition-agent-type and
    # T-UC-005-boundary-agent-type. The graduation that listed them beside the asset
    # one was premature — they passed while grading NOTHING, because
    # _partition_agent_type dispatched a bare _call(ctx) and the request carried no
    # `type` for any partition, so every row would pass whatever the table named
    # (salesagent-8qela.129, .149).
    #
    # THE SELLER HAS NO `type` FILTER TO GRADE, and the chain is read not inferred:
    # schemas/cache/3.2 declares `type` on creative/list-creative-formats-request.json
    # and NOT on media-buy/; adcp/types/legacy.py:112 aliases
    # LegacyListCreativeFormatsRequest to the MEDIA-BUY class (the creative one is
    # separately named ListCreativeFormatsRequestCreativeAgent); and
    # src/core/schemas/creative.py inherits that alias. So `type` is not a field on
    # the accepted shape, and under critical pattern #7 an undeclared field is
    # refused in dev and dropped in production.
    #
    # NOT a role this seller claims: creative_formats.py's
    # ADVERTISED_CREATIVE_AGENT_CAPABILITIES is attached to REFERRALS to
    # tenant-registered remote agents in the media-buy response, not to its own tool
    # surface.
    #
    # Same action and same ground as T-UC-005-partition-type-filter /
    # -boundary-type-filter above: rows that assert type filtering did not happen.
    # FIXME: BR-RULE-029 defines 4 notification types but production
    # WebhookDeliveryService only emits {scheduled, final, adjusted}. No is_delayed flag.
    (
        "T-UC-004-webhook-notification-type",
        {"delayed"},
        "BR-RULE-029: production webhook service has no is_delayed flag — only scheduled/final/adjusted emitted",
    ),
    # ── UC-010 batch-1 per-row gaps — re-cited to their GH homes ───────────
    # The 'omitted' / absence rows of these outlines pass vacuously (the field
    # is absent because the whole block is missing), so only the value rows xfail.
    # Graduated: invalid_token_a2a row — A2A now always
    # validates a presented token, rejecting invalid ones with AUTH_INVALID.
    # operator_auth_not_required GRADUATED: require_operator_auth is now
    # emitted as the true constant False. operator_auth_required (expects True) can never
    # pass with this plan — no per-tenant operator-auth config surface exists.
    (
        "T-UC-010-account-require-operator-auth",
        {"operator_auth_required"},
        "account.require_operator_auth is a hardcoded False constant — no config surface to "
        "make it True exists — #1856",
    ),
    (
        "T-UC-010-account-required-for-products",
        {"products_gated", "products_open"},
        "account.required_for_products not emitted — #1856",
    ),
    (
        "T-UC-010-account-authorization-endpoint",
        {"oauth_supported"},
        "account.authorization_endpoint not emitted — #1856",
    ),
    # Graduated (#1721 M4): given_capability_config now writes account_sandbox
    # through configure_tenant_field when the row spells sandbox={true,false} --
    # sandbox_disabled passes for real on all 3 transports.
    (
        "T-UC-010-degradation-account",
        {"account_degraded"},
        # _build_account_block (src/core/tools/capabilities.py) always emits
        # require_operator_auth (a constant) and sandbox (tenant.account_sandbox,
        # default True) as real, non-null values -- only authorization_endpoint/
        # required_for_products/account_financials are honestly omitted. The
        # scenario expects a supported_billing-only shape, which this design
        # cannot produce.
        "account_degraded expects a supported_billing-only account block, but "
        "_build_account_block always emits require_operator_auth and sandbox as real "
        "constant/config values, not honestly omitted — #1856 account-config surface",
    ),
    # Wired non-dormant + strengthened: the 'absent' rows (adapter
    # fails / capability disabled) pass — the block is genuinely off the wire; only the
    # 'present' rows (full_response: adapter succeeds AND capability enabled) fail,
    # because production never emits the media_buy.audience_targeting /
    # conversion_tracking blocks yet. Strict on the present rows only.
    (
        "T-UC-010-degradation-sections",
        {"full_response"},
        "media_buy.audience_targeting / conversion_tracking sections not emitted by the capabilities builder — #1855",
    ),
    # Wired non-dormant + strengthened: targeting-partitions rows that
    # production satisfies (adapter_unavailable_defaults, nested_absent) pass; the rest execute
    # the real assertion and fail because the capabilities builder never emitted the richer
    # non-geo dimensions (age_restriction/language/keyword_targets/negative_keywords/geo_proximity
    # -- R8 follow-up, out of core scope). Graduated (a recorded gap R4): nested_populated /
    # postal_areas_native / postal_areas_legacy_alias now pass -- the native country-keyed
    # geo_postal_areas map is built (_build_geo_postal_areas, capabilities.py), no longer the
    # deprecated boolean-alias shape.
    (
        "T-UC-010-targeting-partitions",
        {
            "full_adapter",
            "partial_dimensions",
            "age_restriction_supported",
            "keyword_targeting",
            "geo_proximity_supported",
        },
        "targeting builder never emits the non-geo dimensions (age_restriction/language/"
        "keyword_targets/negative_keywords/geo_proximity) — #1857",
    ),
    # Wired non-dormant + strengthened: degradation-partitions rows that
    # production satisfies (adapter_fail, db_fail, adapter_and_db_fail, *_absent) pass; the
    # gap rows fail — no_principal expects [display] but INV-4 keeps the adapter
    # principal-free so channels are NOT degraded by a missing principal. full_response
    # GRADUATED: the account block is now emitted with non-empty supported_billing and
    # adcp.idempotency is already present. account_degraded stays xfailed — a separate,
    # still-ungraded gap (needs investigation).
    #
    # no_tenant is GONE from this set because the ROW is gone: a request that names no
    # seller is now refused rather than answered with a minimal document, so there is no
    # degraded response shape to grade. The refusal is graded by @T-UC-010-ext-a.
    (
        "T-UC-010-degradation-partitions",
        {"no_principal", "account_degraded"},
        "INV-4 keeps adapter channels principal-free so no_principal does not degrade to "
        "the adapter-unavailable shape; account_degraded expects a supported_billing-only account block but "
        "_build_account_block always emits require_operator_auth/sandbox as real values "
        "— #1856 account-config surface",
    ),
    # Graduated: T-UC-010-v31-creative-approval-mode approval_auto. Its reason ("no backing
    # config surface (Q2 deferred) — #1724") stopped being true when the signal moved onto
    # the creative-review setting: the Given writes tenant.approval_mode="auto-approve",
    # describe_seller (seller_capabilities.py) advertises auto_approve from
    # TenantContext.auto_approves_creatives, and _apply_approval_mode (creatives/
    # _processing.py) asks the same predicate and sets status=approved at sync -- the
    # auto-approval behaviour #1724 requires before the claim. approval_human and approval_ai
    # take the require_human branch of the same expression. No transport carries its own
    # leg: the Given writes the DB row every wire resolver reads, and the scenario has no
    # line in e2e_rest_known_failures.txt.
    # Moved from _XFAIL_TAGS: request_signing/webhook_signing={supported:false}
    # now emitted, so "valid" rows (asserting schema-valid relations/bounds against an
    # unsupported posture) pass; "invalid" rows (requiring the builder to REJECT a
    # relation-violating/out-of-bounds posture with CONFIGURATION_ERROR) still fail — no
    # per-tenant signing-posture config surface exists to reject against.
    # T-UC-010-v31-request-signing-monotonicity is not xfailed at all: request_signing is a
    # real tenant declaration, so CapabilityDeclarations._validate_bucket_monotonicity has a
    # posture to reject. The outline's invalid row raises AdCPConfigurationError naming
    # capability_declarations.request_signing.required_for, graded on the wire as
    # CONFIGURATION_ERROR / recovery terminal / field naming request_signing. Its Given
    # declares the concrete posture rather than recording a label, writing the narrowing
    # bucket EXPLICITLY -- the rule keys on model_fields_set, so an omitted superset would
    # skip the check and the row would grade nothing.
    #
    # The outline carries TWO rows over one relation (required_for subset_of supported_for).
    # It carried six over three until warn_for and the protocol-method buckets stopped being
    # declarable: a declaration naming one is now refused for being undeclarable, which would
    # have satisfied the invalid rows for the wrong reason and graded nothing on the valid
    # ones (docs/design/request-signing-subset.md).
    # Graduated 2026-08-12 (#1291 D2), PARTIALLY: reporting_delivery_methods=['webhook'],
    # supported=false removed — a KEYLESS tenant declaring webhook report delivery is the one
    # reachable violation of must_equal_when, and validate_signing_platform_backing rule (d)
    # rejects it naming capability_declarations.webhook_signing.supported. The outline's three
    # realizable VALID rows (the keyed [webhook] trigger and the two in-profile algorithm sets)
    # now derive their posture from real key material too, instead of passing against an absent
    # block. The four rows below cannot be realized here, for two different reasons, and each
    # carries its own — none of them is signing's to fix.
    #
    # Two MORE rows of this outline left the passing count in the same change, and did not come
    # here: supports_webhook_delivery=true / wholesale_feed_webhooks.supported=true (both "valid")
    # passed only because the Given recorded intent, so their trigger never reached the wire and
    # the must_equal_when assertion short-circuited on a block that graded nothing. A test that
    # cannot fail is not coverage, so they are parked (owner decision, 2026-07-30) — in
    # _UC010_PARKED_ROWS, not here, because a STRICT xfail cannot hold a row that passes: it
    # converts the vacuous pass into an XPASS build failure (measured: 6 failures, 2 rows x 3
    # transports, before the park moved).
    #
    # Net effect on this tag, and it is DOWN on purpose: +1 graduated row and -2 parked rows, so
    # the uc010 slice went 344 -> 341 passed with 252 -> 255 xfailed, 0 failed, 0 xpassed. In
    # network, bdd_e2e test-results/innet_120826_1507: on e2e_rest the graduated
    # supported=false row PASSES, both algorithm rows and the keyed trigger row pass, and all four
    # unreachable rows xfail; 535 passed / 0 failed.
    (
        "T-UC-010-v31-webhook-signing-bounds",
        {
            "supports_webhook_delivery=true, supported absent",
            "algorithms=['rsa-pss-sha512']",
        },
        "these two boundaries have no reachable state in this deployment, and both FAIL rather "
        "than pass: the supports_webhook_delivery row names a must_equal_when trigger whose block "
        "is unbacked — media_buy.content_standards has no surface (#1855) — and declaring it is "
        "refused NAMING THAT BLOCK, so realizing it would grade the wrong refusal. The "
        "rsa-pss-sha512 row asks for an algorithm outside the AdCP profile, but "
        "webhook_signing.algorithms is DERIVED from the ACTIVE key row and narrow_alg refuses an "
        "off-profile algorithm at MINT time, so the value can never exist in the store to be "
        "rejected on the read path: the obligation is met by construction rather than by a "
        "rejection, and the row becomes gradable only if the profile itself widens. The two "
        "same-outline rows that pass VACUOUSLY are parked in _UC010_PARKED_ROWS instead — a strict "
        "xfail cannot hold a row that passes",
    ),
    # Wired non-dormant + strengthened: the baseline-absence row passes
    # (polling_only → reporting_delivery_methods/offline_delivery_protocols absent, webhook_signing
    # honest-tautology); the push-delivery rows fail because the capabilities builder never emits
    # media_buy.reporting_delivery_methods / offline_delivery_protocols / webhook_signing.
    # Graduated 2026-08-12 (#1291 D2), PARTIALLY: webhook_only removed — reporting_delivery_methods
    # is declarable and [webhook] is backed, so a keyed, publishable tenant now emits
    # media_buy.reporting_delivery_methods=[webhook] with webhook_signing.supported=true, which is
    # all three of that row's Thens. The remaining two rows never needed signing; their blocker is
    # offline report delivery, which this epic does not build. Serial in-process run 2026-08-12:
    # uc010 slice 341 -> 344 passed (+1 row x 3 transports), 255 -> 252 xfailed, 0 failed,
    # 0 xpassed. In-network bdd_e2e test-results/innet_120826_1434: webhook_only PASSES on
    # e2e_rest, offline_only and mixed_delivery still xfailed, 536 passed / 0 failed.
    (
        "T-UC-010-v31-reporting-delivery-methods",
        {"offline_only", "mixed_delivery"},
        "no bucket report delivery is implemented — production refuses a method list containing "
        "'offline' and carries no offline_delivery_protocols field at all, so neither row can be "
        "realized without grading the unbacked-block refusal instead of this outline's rule. "
        "mixed_delivery declares [webhook, offline] and is blocked by the offline member alone. "
        "Both graduate when bucket report delivery lands — #1729",
    ),
    # Wired non-dormant + strengthened: the no-emission row passes (no
    # must_equal_when trigger fires → webhook_signing absent is schema-valid); the emission rows
    # grade the conditional invariant (supported MUST equal true) and fail because the
    # capabilities builder emits no webhook_signing block.
    # Graduated 2026-08-12 (#1291 D2), PARTIALLY: reporting_webhook_emission removed — a keyed,
    # publishable tenant declaring reporting_delivery_methods=[webhook] now derives
    # webhook_signing.supported=true, which is the must_equal_when invariant the row grades, and
    # its Given declares that state instead of recording it. The other two rows stay: their
    # triggers are not declarable HERE, and the blocker is not signing. Serial in-process run
    # 2026-08-12: uc010 slice 338 -> 341 passed (+1 row x 3 transports), 258 -> 255 xfailed,
    # 0 failed, 0 xpassed. In-network (the authority, and the leg where a runner-minted key must
    # be openable by the live server): bdd_e2e test-results/innet_120826_1420 has
    # reporting_webhook_emission PASSING on e2e_rest with the two rows below still xfailed,
    # 535 passed / 0 failed.
    (
        "T-UC-010-v31-webhook-signing-required-when",
        {"content_standards_webhook", "wholesale_feed_webhook"},
        "the other two must_equal_when triggers name blocks this deployment does not implement, so "
        "there is no honest way to make either fire: media_buy.content_standards has no surface at "
        "all (#1855) and wholesale_feed_webhooks has no model field (#1867). Declaring either is "
        "refused NAMING THAT BLOCK, so realizing them would grade these rows by the wrong refusal "
        "rather than by the webhook-signing invariant. They graduate when those surfaces land",
    ),
    # Graduated 2026-08-12 (#1291 D2): T-UC-010-v31-identity-required-when-signing (rows
    # posture_declared_identity_absent, posture_declared_identity_empty) and
    # T-UC-010-v31-identity-brand-json-url-bounds (rows posture_url_absent,
    # posture_identity_empty) removed ENTIRELY — both outlines grade one rule and it is now
    # real. The identity block became declarable with the signing family (IdentityDeclaration,
    # src/core/signing/posture.py), and CapabilityDeclarations._validate_identity_relations
    # rule (e) rejects a bucket-naming posture whose trust-root pointer is missing or empty
    # with AdCPConfigurationError naming capability_declarations.identity.brand_json_url —
    # which the Thens grade on the wire via assert_envelope_shape(CONFIGURATION_ERROR,
    # recovery='terminal', message_substr='brand_json_url'). The Givens now realize the
    # posture and the identity state (absent / {} / derived) as real tenant declarations
    # through CapabilitiesEnv.declare_signing instead of recording intent, so the four rows
    # fail for the reason they name. Inspected per .claude/rules/workflows/xpass-graduation.md
    # (feature :1215-1240 and :1551-1580 are the authority; obligation re-verified against the
    # pinned 3.1.1 required_when; no assertion weakened). Verified serially on a2a/mcp/rest,
    # 2026-08-12: tests/bdd/test_uc010_discover_seller_capabilities.py +
    # test_uc010_declaration_backing.py -rxX went 317 -> 329 passed (+4 rows x 3 transports),
    # 279 -> 267 xfailed, 0 failed, 0 xpassed. These entries are NOT gated for e2e_rest
    # (:1314-1322), so graduation was decided on the in-network leg, which is the authority:
    # bdd_e2e run sa-0a76f566 (test-results/innet_120826_1349) has all 7 rows of both outlines
    # PASSING on e2e_rest, 0 failed. tests/bdd/e2e_rest_known_failures.txt carries no UC-010
    # entry for either outline, so nothing to graduate there.
    # ── create_media_buy and update_media_buy disagree (#2483), one row ──
    # The tenant row of the submitted-envelope boundary outline. Its Given
    # (given_approval_configuration) delegates to given_tenant_human_review_flag, which sets
    # the tenant flag alone since @T-UC-003-approval-tenant was routed above; before, it also
    # raised the adapter's flag and the row passed on the adapter arm. The adapter row of the
    # same outline still passes, so the entry names the one row. Strict on all four
    # transports; it graduates (XPASS strict) when #2483 lands.
    (
        "T-UC-003-bva-approval-workflow",
        {"tenant flag true (submitted task envelope)"},
        _UPDATE_TENANT_REVIEW_DIVERGENCE,
    ),
]


# UC-010 scenarios that are PARKED WITH A REASON rather than silently unwired.
#
# Before this table, a scenario missing from the wired set xfailed with the blanket
# "UC-010 wiring batch 2/3 pending" message whether it was merely un-got-to or genuinely
# blocked on unimplemented backing. That is indistinguishable from the dormant-scenario
# defect: nobody reading the ledger could tell "no one has written the steps" from "the
# steps cannot be written honestly yet".
#
# Every entry names WHAT is missing. WHERE it is tracked is NOT repeated here: every key
# is also a ``_UC010_DORMANT_TRACKING`` key, and ``_uc010_dormancy_rows()`` builds this
# tag's row as "<this text> — tracked by <that map's issue>". So a narrative below must
# name no issue of its own: a second number would contradict the map for that tag, which
# tests/unit/test_architecture_uc010_dormancy_citations.py fails on (it also fails on a
# key that is missing from the map, which would otherwise drop the text silently).
_UC010_PARKED_TAGS: dict[str, str] = {
    # #1291 D1 made the signing family declarable, and four of its main-flow scenarios are
    # wired as a result (see _uc010_wired_tags()). These three CANNOT be wired
    # honestly, and each for a reason that has nothing to do with signing:
    "T-UC-010-v31-identity-brand-json-url": (
        "the scenario's second Given declares sponsored_intelligence.brand_url, so its "
        "distinct_from assertion is non-vacuous. Nothing in src/ implements the "
        "sponsored_intelligence surface, so under the STRICT capability policy the block is "
        "undeclarable and unemitted: the Given cannot be realized, and wiring it anyway "
        "would compare the emitted trust root against an ABSENT value and pass vacuously. "
        "Needs a backed sponsored_intelligence block. The brand_json_url half it shares "
        "with -identity-brand-json-url-bounds IS graded (the D1 increment of the issue "
        "this row cites)"
    ),
    "T-UC-010-v31-identity-key-origins": (
        "three of the four rows declare a key-origin purpose this deployment does not back: "
        "governance_signing and tmp_signing need a separate governance/TMP signing JWKS "
        "origin that nothing serves, and webhook_signing names a delivery-surface origin we "
        "do not publish separately. Emitting any of them would break the pin's "
        "purpose_anchoring constraint (x-adcp-validation.verifier_constraints), which "
        "requires a declared origin to have its posture. The request_signing row IS "
        "gradable today — key_origins.request_signing is emitted from jwks_origin() exactly "
        "when a bucket is declared — so this graduates row-by-row, not as a scenario"
    ),
    "T-UC-010-v31-identity-compromise-notification": (
        "asserts the seller declares identity.compromise_notification.emits=true, i.e. that "
        "it emits the compromise-notification webhook on revocation-due-to-compromise. "
        "Zero implementation exists (no hits for 'compromise' anywhere in src/), so "
        "declaring it would be exactly the over-promise the STRICT policy exists to "
        "prevent. Needs the compromise-notification webhook event itself"
    ),
    # content_standards is refused by _UNBACKED_BLOCKS for a reason that OUTLIVES the
    # signing work (#1291): nothing implements local evaluation, artifacts, verdicts or
    # artifact_webhook delivery, so signing landing does not make the block declarable.
    # That is why the map homes it on the presence-object issue instead.
    "T-UC-010-v31-content-standards-block": (
        "media_buy.content_standards is undeclarable and unemitted — no content-standards "
        "surface exists in this deployment (no local evaluation, artifacts, verdicts or "
        "artifact_webhook delivery). Re-homed off the signing issue, which does not unblock it"
    ),
}


# The same park, one level finer: individual ROWS of a wired outline whose state cannot be
# realized here, in outlines whose other rows ARE graded. _SELECTIVE_XFAIL cannot express
# this, because it is strict — and a row parked HERE is one that would PASS, vacuously, on
# a Given that could not realize its trigger. Strict-xfailing it turns the vacuous pass into
# a build failure; leaving it alone counts a test that cannot fail as coverage.
#
# So the park is imperative (``pytest.xfail`` before the harness is built), exactly like
# _UC010_PARKED_TAGS: the row does not run, and the reason says what would make it runnable.
# An entry leaves this table when production backs the block — never by weakening the
# scenario. (tag, nodeid substrings, reason)
_UC010_PARKED_ROWS: list[tuple[str, set[str], str]] = [
    (
        "T-UC-010-v31-webhook-signing-bounds",
        {
            "supports_webhook_delivery=true, supported=true",
            "wholesale_feed_webhooks.supported=true, supported=true",
        },
        "the row's must_equal_when trigger is an unbacked block — media_buy.content_standards "
        "has no surface in this deployment (#1855) and wholesale_feed_webhooks has no model "
        "field at all (#1867) — so the trigger can never reach the wire and "
        "_assert_webhook_signing_must_equal_when short-circuits: the row would PASS while "
        "grading nothing but a generic schema-valid block. Parked rather than left green "
        "(#1291 D2, owner decision): a test that cannot fail is not coverage. It graduates with "
        "the surface that makes its trigger declarable",
    ),
]
# XPASS INSPECTED AND REFUSED 2026-10-09: both rows of the entry above reported XPASS
# over e2e_rest in innet_091026_1343, with a real call phase against the live server
# (POST /api/v1/capabilities -> 200). They were not supposed to RUN at all. The cause
# was not this table: the e2e_rest marker collapse (:4489) rebuilt the marker from
# reason+strict and dropped ``run=False``, so the park degraded into a running
# non-strict xfail on that transport alone — and the XPASS it produced is precisely the
# vacuous green the park's own reason predicts. The collapse now carries ``run``; the
# entry stays exactly as it is, because the surface its reason names (#1855, #1867)
# still does not exist.


# The wired-tag gate lives in ``_uc010_wired_tags()`` (one definition, below): the
# capabilities row's ``when`` predicate and every guard that asks "is this tag wired?"
# read that accessor. A second module-level copy of the set stood here after the merge
# and the two had already drifted apart by four tags each, so the routing table carried
# two answers to the same question -- which is the duplication the accessor exists to
# prevent. Its members were folded into the accessor's literal.


# MCP selective xfails: previously the MCP wrapper did not accept the
# disclosure_positions keyword. #1417 added disclosure_positions +
# disclosure_persistence to the MCP list_creative_formats wrapper, so the param
# is now accepted on MCP exactly like A2A/REST. The disclosure *filter* gap it
# also described ("_impl does not filter by disclosure", all-transport, routed via
# _UC005_PARTIAL_TAGS / _XFAIL_TAGS) is CLOSED: creative_formats.py applies the
# disclosure_positions filter, and both routings were graduated with it. Either
# way no UC-005 MCP-specific entries remain.
# (tag, example_substrings, reason, strict)
# strict=True  → must fail (genuine xfail)
# strict=False → may pass vacuously (MCP errors → empty list → exclusion assertions pass)
_MCP_SELECTIVE_XFAIL: list[tuple[str, set[str], str, bool]] = [
    # Graduated: MCP ToolResult now pre-serializes via
    # model_dump(mode="json") (src/core/tools/_mcp.py), so unset
    # fields are correctly omitted instead of serialized as JSON null.
    # Former entries: T-UC-010-ext-e-absent (context: null), T-UC-010-
    # degradation-account/no_tenant (account: null).
]

# NOTE: the former _REST_XFAIL_TAGS set was retired once the stale
# CreativeFormatsEnv.build_rest_body override (which returned {}) was removed.
# In-process REST now serializes the request body and filters for real, so these
# UC-005 filter scenarios pass on [rest] like every other transport. inv-031-1-holds
# / inv-031-1-violated used to be named here as the exception, xfailed for ALL
# transports because the `type` filter they stated does not exist on this seller's
# request. They no longer state it: both filter on asset_types + name_search, so
# there is no remaining UC-005 filter tag that cannot hold.


# Every transport a BDD nodeid can be parametrized over. `impl` is vestigial
# (sunsetted from BDD parametrization by #1417) but two predicates below still
# consume it, so it stays in the alternation.
#
# `e2e_rest` is listed before `rest` for readability only — it is NOT what
# disambiguates them, and a comment claiming otherwise was wrong. A regex matches
# at the earliest POSITION before it consults alternation order, and the "rest"
# inside "e2e_rest" always sits four characters later, so neither reordering this
# tuple nor dropping the `\[` anchor can make an `[e2e_rest-row]` nodeid report as
# `rest` (all three mutations verified to leave the guard green). What the
# bracket discipline DOES buy is refusing a row id that merely contains a
# transport name.
# IMPORTED, not declared here. It was "one derivation for the whole file" and needed to be
# one for the whole SUITE: tests/bdd/scenario_liveness.py needs the same answer and cannot
# import this conftest (2098 modules at plugin-load time), so it grew its own copy and the
# copy split on the LAST bracket -- 433 corrupted transports in one run
# (salesagent-8qela.159). The derivation now lives in a stdlib-only helper both can reach.
# The aliases keep this file's fourteen call sites spelled as they were.
_NODEID_TRANSPORTS = NODEID_TRANSPORTS
_transport_of = transport_of


#: Causes a typed xfail reason may declare. A reason whose ``cause=`` is not here is a
#: typo or an invention, and either way the row it exempts would be silently mis-routed.
#:
#: ENUMERATED FROM THE TREE, not chosen. The first version of this set was written from
#: imagination -- "spec-gap", "harness-gap", "upstream-defect" -- none of which exists
#: here, while the real "harness-limitation" was missing, so every typed reason in the
#: suite failed to parse. An AST scan of the strings that actually BEGIN with "cause="
#: gives the population; extend these sets from that scan, never from a guess.
_XFAIL_CAUSES = frozenset({"transport-drops-parameter", "production-gap", "harness-limitation"})

#: Scopes a typed xfail reason may declare.
_XFAIL_SCOPES = frozenset({"per-transport", "transport-independent"})

#: The DECLARATION: a run of ``key=value`` tokens at the HEAD of the reason, ending at
#: the first word that is not one. Anchored with ``\A`` so prose later in the string is
#: not part of the declaration -- which is the entire point of parsing instead of
#: substring-matching.
_XFAIL_DECLARATION_RE = re.compile(r"\A(?:\s*(?:cause|scope|ref)=\S+)+")
_XFAIL_TOKEN_RE = re.compile(r"(?P<key>cause|scope|ref)=(?P<value>\S+)")


class XfailReasonError(AssertionError):
    """A typed xfail reason declares a token this suite does not recognise."""


class XfailReason(NamedTuple):
    """The parsed form of a ``cause=... scope=... ref=...`` xfail reason."""

    cause: str
    scope: str
    ref: str


def parse_xfail_reason(reason: str) -> XfailReason | None:
    """Parse a typed xfail reason, or return None if it is free text.

    Why a parse and not a tighter substring match. The predicate this replaces asked
    ``"scope=per-transport" in reason``, and that is satisfied by any text CONTAINING the
    phrase -- including a sentence *describing* the token. Measured: retyping the
    declaration at :687 while leaving the prose at :706 ("scope=per-transport because each
    transport enforces ... independently") changed the collected set by ZERO rows. The
    declaration was decorative and the English sentence was load-bearing, by accident.
    A substring predicate cannot tell a declaration from a description of one; only
    position can, so ONLY the leading run of ``key=value`` tokens is read — the
    declaration ends at the first word that is not one, and everything after it is prose
    the parser never consults.

    Free text returns None rather than raising: 62 reasons in this tree are prose, three
    of them carrying unrelated ``k=`` pairs, and sweeping them is not this change's job.
    Only a reason that ANNOUNCES itself typed -- by OPENING with a run of ``key=value``
    tokens, in any order -- is held to the vocabulary. A reason that merely mentions one
    later is prose.

    The residue, stated rather than papered over: a typo in ``cause=`` ITSELF -- or in the
    leading token's key, which is the same thing -- degrades the reason into free text and
    returns None. So this DETECTS an unknown value; it does not make one unrepresentable.
    Leading whitespace is tolerated (``lstrip``) so that a reason indented in a source
    literal is still recognised as typed rather than silently becoming prose.
    """
    # Recognition is the leading TOKEN RUN, not the literal "cause=". Gating on that
    # keyword made a complete, in-vocabulary declaration whose tokens were written in a
    # different ORDER — `scope=... cause=... ref=...` — classify as free text and route
    # nothing, silently: exactly the drop this function exists to prevent, reachable by
    # word order rather than by a typo. All three live reasons happen to lead with
    # `cause=`, so it was latent, in the same way the first-occurrence bug was.
    # Only the leading declaration is parsed. Scanning the whole string was the first
    # version of this function and it carried the defect it was written to remove:
    #   'cause=production-gap — unlike scope=per-transport rows this one is global.
    #    scope=transport-independent ref=#1607'
    # parsed to scope='per-transport', because finditer took the first match ANYWHERE and
    # the first one was inside an English sentence. A reason DECLARING
    # transport-independent would have been routed per-transport by its own prose. The
    # live reason at conftest.py:781 contains 'This is NOT scope=per-transport' and
    # parsed correctly only because its declaration happened to come first — one word
    # order away from wrong.
    declaration = _XFAIL_DECLARATION_RE.match(reason.lstrip())
    if declaration is None:
        return None
    found: dict[str, str] = {}
    for match in _XFAIL_TOKEN_RE.finditer(declaration.group(0)):
        found.setdefault(match.group("key"), match.group("value").rstrip(",;."))
    missing = {"cause", "scope", "ref"} - found.keys()
    if missing:
        raise XfailReasonError(f"typed xfail reason is missing {sorted(missing)}: {reason!r}")
    if found["cause"] not in _XFAIL_CAUSES:
        raise XfailReasonError(f"unknown xfail cause={found['cause']!r} in {reason!r}; known: {sorted(_XFAIL_CAUSES)}")
    if found["scope"] not in _XFAIL_SCOPES:
        raise XfailReasonError(f"unknown xfail scope={found['scope']!r} in {reason!r}; known: {sorted(_XFAIL_SCOPES)}")
    if not found["ref"].startswith("#"):
        raise XfailReasonError(f"xfail ref={found['ref']!r} must be an issue reference like '#1607': {reason!r}")
    return XfailReason(cause=found["cause"], scope=found["scope"], ref=found["ref"])


# ── The UC-002 / UC-003 explicit ledger ─────────────────────────────────────
# The two catch-all routes, `uc002-not-wired` and `uc003-not-wired`, used to xfail every
# UC-002 / UC-003 scenario no earlier ENV_ROUTES row claimed, at FIXTURE SETUP. 2397 items
# graded nothing behind two sentences. Those routes now build their env and seed like any
# other row, and what fails is named here instead, scenario by scenario and row by row.
#
# THE EVIDENCE. measure_innet_291026_1202 (tree 22bb2b72b = 1b5c6880b with the two
# xfail_reasons lifted), both BDD suites: of the scenario functions the catch-alls held,
# 99 passed outright, 57 failed outright, 46 were mixed and 32 stayed xfailed by markers of
# their own. Every FAILED nodeid of that run is matched by exactly one entry of
# _UC002_UC003_LEDGER, with the cause its first failure line shows, except where the fix
# was made in this change instead: an existing marker that XPASSed came out, a reason the
# dormancy tripwire rejected was corrected, and six UC-003 Examples rows that contradicted
# the pin were corrected in place to the code production answers (each carries a LOCAL
# CORRECTION note and an @source). 890 nodeids are ledgered.
#
# Graduated: 1263 of the run's 2082 passing nodeids carry no marker now and are graded as
# they stand (measure_innet_291026_1202). Four of them are the UC-002 zero-budget boundary
# row, whose outcome now pins the field, so its nodeid changed. The other 819 passes are in
# _UC002_UC003_VACUOUS_PARKS, drawn from a per-scenario inspection of these passes made the
# way .claude/rules/workflows/xpass-graduation.md describes. Also graduated: the
# 20 nodeids whose own strict markers XPASSed (see _XFAIL_TAGS), and the 32 nodeids of rows
# corrected in place to the code production already answers (ext-f, ext-g, and the UC-003
# daily-cap, idempotency, negative-budget and naive-start rows).
#
# THE TWO LISTS. _UC002_UC003_LEDGER holds what FAILS: strict xfails, so a row that starts
# passing reports XPASS(strict) and has to be looked at. _UC002_UC003_VACUOUS_PARKS holds what
# PASSES but grades nothing (or grades a claim the pin contradicts): xfail(run=False), the
# UC-010 park's shape, because a vacuous pass counted as coverage is the defect and a running
# xfail would report it as an XPASS. No nodeid is matched by both lists, and a park on a
# Scenario Outline names the measured rows it parks, so a ledgered row that starts passing,
# or a row added to the outline later, runs instead of falling into a park nobody reads.
#
# Both lists may only SHRINK. A scenario that newly fails is not added here; it is fixed, or
# its cause is measured and given its own entry with that cause. The entry counts, the
# nodeids each list matches, the no-overlap rule and the outline-rows rule are pinned by
# tests/unit/test_architecture_route_xfail_census.py.
#
# Reasons avoid the words the dormancy tripwire (_classify_strict_xfail_dormancy) reads as a
# claim about production, because several entries ARE test-wiring and say so.

_CATCHALL_RUN = "measure_innet_291026_1202"


def _dormant(step: str) -> str:
    """The reason for a scenario whose step has no definition at all."""
    return f"dormant (test-wiring): no step definition binds {step!r}, so the scenario executes nothing"


def _no_given_branch(given: str, value: str) -> str:
    """The reason for an Examples value a Given step has no branch for."""
    return f"Given gap: {given} has no branch for {value!r} (ValueError), so nothing is dispatched"


def _no_datatable_field(field: str) -> str:
    """The reason for a package-update datatable field the shared step does not accept."""
    return f"step gap: the package-update datatable step does not accept {field!r}, so nothing is dispatched"


_LEDGER_SEED_MEDIA_BUY = (
    "harness: the Given calls env.seed_media_buy, which MediaBuyCreateEnv does not have "
    "(AttributeError), so nothing is dispatched"
)
_LEDGER_CATALOG_NOT_FOUND = (
    "production accepts a catalog the seller never synced; the Given also sends it as an inline ids "
    "filter rather than a catalog_id reference, and the pinned code for a missing reference is "
    "REFERENCE_NOT_FOUND, so the row needs regrounding as well"
)
_LEDGER_OPT_CAPABILITY = (
    "production does not check optimization_goals against the product's declared optimization "
    "capability, so the goal is accepted where the row expects UNSUPPORTED_FEATURE"
)
_LEDGER_OPT_UNVALIDATED = (
    "production validates optimization_goals only against the request schema, so this cross-field or "
    "per-account rule is not enforced and the create succeeds"
)
_LEDGER_PRODUCT_CONFIG = (
    "Given gap: given_product_configuration has no branch for 'proposal_id, no packages' (ValueError), "
    "so nothing is dispatched"
)
_LEDGER_DEVICE_OVERLAP = (
    "production accepts a device_type that is both included and excluded, where the row expects INVALID_REQUEST"
)
_LEDGER_DEVICE_OVERLAP_E2E = (
    "on e2e_rest production answers UNSUPPORTED_FEATURE for the overlapping device_type overlay, where "
    "the row expects INVALID_REQUEST"
)
_LEDGER_NO_ADD_PACKAGES = (
    "the seeded buy does not advertise add_packages in valid_actions (this seller refuses "
    "new_packages), so the scenario stops at its own precondition"
)
_LEDGER_INVOICE_RECIPIENT = (
    "production accepts an invoice_recipient the account does not authorize (BR-RULE-214 is not "
    "implemented), where the row expects VALIDATION_ERROR"
)


def _example_row(nodeid: str) -> str:
    """The Examples-row part of a parametrized nodeid: everything after its transport.

    ``…::test_x[mcp-zero_amount-0-error …]`` gives ``zero_amount-0-error …``; a nodeid with no
    Examples row gives "". Keyed off ``_transport_of`` so the transport is derived once.
    """
    transport = _transport_of(nodeid)
    head = f"[{transport}-"
    start = nodeid.find(head) if transport else -1
    return nodeid[start + len(head) : -1] if start >= 0 else ""


@dataclass(frozen=True)
class _CatchallXfail:
    """One entry of the UC-002 / UC-003 explicit ledger or of its vacuous parks.

    ``rows`` are leading Examples columns of the row (``_example_row``), matched whole: each
    one is a row's first column, so an entry reads as the Examples line it names, and
    ``view_duration_seconds = 0`` does not also claim ``view_duration_seconds = 0.001``. A
    non-ASCII character is written the way the nodeid escapes it (``\\u2014``). Empty means
    every row. ``transports`` empty means every transport.
    """

    tag: str
    reason: str
    rows: tuple[str, ...] = ()
    transports: tuple[str, ...] = ()

    def matches(self, marker_names: set[str], nodeid: str) -> bool:
        if self.tag not in marker_names:
            return False
        if self.transports and _transport_of(nodeid) not in self.transports:
            return False
        row = _example_row(nodeid)
        return not self.rows or any(row == r or row.startswith(f"{r}-") for r in self.rows)


_X = _CatchallXfail

#: What FAILED in measure_innet_291026_1202 once the catch-alls were lifted. Strict xfails.
# Graduated in wave 5 (integrate/wave1 a3687d971): w5b01, w5b02, w5b03, w5b04, w5b06, w5b07,
# w5b08, w5b09, w5b10, w5b11, w5b13, w5b14, w5b15, w5b16, w5b17, w5b18, w5b19, w5b20, w5b21.
# Graduated: T-UC-002-partition-pricing-option-xor cpa_model. Strict XPASS on a2a/mcp/rest in
# innet_291026_2322 because MediaBuyCreateEnv stubbed the adapter's pre-creation check to "no
# problems"; e2e_rest answered VALIDATION_ERROR (UNSUPPORTED_FEATURE on
# packages[0].pricing_option_id). The harness now runs MockAdServer's real check and the row
# expects that refusal.
_UC002_UC003_LEDGER: tuple[_CatchallXfail, ...] = (
    _X("T-UC-002-alt-manual-reject-override", _LEDGER_SEED_MEDIA_BUY),
    _X(
        "T-UC-002-boundary-catalog-distinct-type",
        _LEDGER_CATALOG_NOT_FOUND,
        rows=("catalog_id references a non-existent catalog",),
    ),
    _X(
        "T-UC-002-boundary-io-acceptance",
        _dormant('given a proposal-based create_media_buy request matching "<>"'),
        rows=(
            "requires_signature false, io_acceptance absent (no gate)",
            "requires_signature false, io_acceptance present (no-op)",
            "requires_signature true, io_acceptance absent (gate blocks)",
            "requires_signature true, io_acceptance present with all required members",
            "requires_signature true, io_acceptance present with signature_id also supplied",
        ),
    ),
    _X(
        "T-UC-002-boundary-optimization-goals",
        _LEDGER_OPT_CAPABILITY,
        rows=(
            "product lacks conversion_tracking + event goal submitted",
            "product lacks metric_optimization + metric goal submitted",
        ),
    ),
    _X(
        "T-UC-002-boundary-pricing-option-xor",
        _no_given_branch("given_pricing_option_configuration", "max_bid=true"),
        rows=("max_bid=true on bid-based auction model",),
    ),
    _X(
        "T-UC-002-boundary-pricing-option-xor",
        _no_given_branch("given_pricing_option_configuration", "neither (v3.1 schema)"),
        rows=("neither present, v3.1 schema (auction, no floor)",),
    ),
    _X(
        "T-UC-002-boundary-product-uniqueness",
        _LEDGER_PRODUCT_CONFIG,
        rows=("proposal_id supplied, packages omitted (no buyer packages array)",),
    ),
    _X(
        "T-UC-002-boundary-targeting-overlay",
        _LEDGER_DEVICE_OVERLAP,
        rows=("device_type include/exclude overlap",),
        transports=("a2a", "mcp", "rest"),
    ),
    _X(
        "T-UC-002-boundary-targeting-overlay",
        _LEDGER_DEVICE_OVERLAP_E2E,
        rows=("device_type include/exclude overlap",),
        transports=("e2e_rest",),
    ),
    _X(
        "T-UC-002-inv-006-5",
        _dormant("given a package uses a bid-based auction pricing model with max_bid set to true"),
    ),
    _X(
        "T-UC-002-inv-214-8",
        _dormant(
            "given a create_media_buy request that carries an invoice_recipient overriding the account default billing entity"
        ),
    ),
    _X("T-UC-002-partition-catalog-distinct-type", _LEDGER_CATALOG_NOT_FOUND, rows=("catalog_not_found",)),
    _X(
        "T-UC-002-partition-optimization-goals",
        _LEDGER_OPT_CAPABILITY,
        rows=(
            "event_not_supported_by_product",
            "unsupported_attribution_window",
            "unsupported_metric",
            "unsupported_reach_unit",
        ),
    ),
    _X("T-UC-002-partition-optimization-goals", _LEDGER_OPT_UNVALIDATED, rows=("unregistered_event_source",)),
    _X(
        "T-UC-002-partition-targeting-overlay",
        _LEDGER_DEVICE_OVERLAP,
        rows=("device_type_overlap",),
        transports=("a2a", "mcp", "rest"),
    ),
    _X(
        "T-UC-002-partition-targeting-overlay",
        _LEDGER_DEVICE_OVERLAP_E2E,
        rows=("device_type_overlap",),
        transports=("e2e_rest",),
    ),
    _X(
        "T-UC-002-storyboard-async-submitted-envelope-task-id-roundtrip",
        _dormant(
            'given a comply_test_controller directive registered force_create_media_buy_arm with branch "<>" and task_id "<>"'
        ),
    ),
    _X(
        "T-UC-002-storyboard-governance-approved",
        _dormant('given the buyer\'s governance agent has returned decision "<>" for the proposed buy'),
    ),
    _X(
        "T-UC-002-storyboard-governance-denied",
        _dormant('given the buyer\'s governance agent has returned decision "<>" with a denial reason'),
    ),
    _X(
        "T-UC-002-storyboard-governance-denied-recovery",
        _dormant('given a previous create_media_buy attempt failed with error code "<>"'),
    ),
    _X(
        "T-UC-002-storyboard-governance-with-conditions",
        _dormant('given the buyer\'s governance agent has returned decision "<>" with a non-empty conditions array'),
    ),
    _X(
        "T-UC-002-storyboard-inventory-list-no-match",
        _dormant("given the buyer references a property_list whose entries do not match any seller inventory"),
    ),
    _X(
        "T-UC-002-storyboard-inventory-list-targeting-parity",
        _dormant("given the buyer holds a property_list (agent_url, list_id) that matches seller inventory"),
    ),
    _X(
        "T-UC-002-v31-artifact-webhook-config",
        _dormant("then the seller should accept the artifact_webhook configuration for governance content delivery"),
    ),
    _X(
        "T-UC-002-v31-committed-metrics-standard-accepted",
        _dormant('given the request package includes committed_metrics with one entry of scope "<>"'),
    ),
    _X(
        "T-UC-002-v31-error-policy-violation-details",
        _dormant("given a create_media_buy request that violates the seller's editorial policy"),
    ),
    _X(
        "T-UC-002-v31-invoice-recipient-echoed",
        _dormant("given a valid create_media_buy request with an invoice_recipient override"),
    ),
    _X(
        "T-UC-002-v31-io-acceptance-not-required",
        _dormant('given a valid create_media_buy request with proposal_id "<>" and the io_acceptance field omitted'),
    ),
    _X(
        "T-UC-002-v31-io-acceptance-provided",
        _dormant("given the proposal's insertion_order requires_signature is true"),
    ),
    _X(
        "T-UC-002-v31-io-acceptance-required",
        _dormant("given the proposal's insertion_order requires_signature is true"),
    ),
    _X(
        "T-UC-002-v31-io-acceptance-signature-id",
        _dormant("given the proposal's insertion_order requires_signature is true"),
    ),
    _X(
        "T-UC-002-v31-plan-id-required-when-governance",
        _dormant("given a create_media_buy request with the plan_id field omitted"),
    ),
    _X("T-UC-002-v31-plan-id-resolves", _dormant('given a valid create_media_buy request with plan_id "<>"')),
    _X(
        "T-UC-002-v31-planned-delivery-with-governance",
        _dormant('given a valid create_media_buy request with plan_id "<>"'),
    ),
    _X("T-UC-003-boundary-approval-workflow", _UPDATE_TENANT_REVIEW_DIVERGENCE, rows=("tenant flag true (pending)",)),
    _X(
        "T-UC-003-bva-billing-eligibility",
        _LEDGER_INVOICE_RECIPIENT,
        rows=("invoice_recipient supplied = business entity not authorized for this account",),
    ),
    _X("T-UC-003-cancel-package", _no_datatable_field("canceled")),
    _X("T-UC-003-new-packages-add", _LEDGER_NO_ADD_PACKAGES),
    _X("T-UC-003-new-packages-duplicate", _LEDGER_NO_ADD_PACKAGES),
    _X("T-UC-003-partition-approval-workflow", _UPDATE_TENANT_REVIEW_DIVERGENCE, rows=("pending_human_review",)),
    _X("T-UC-003-storyboard-creative-fate-after-cancellation", _dormant("given a media buy has been canceled")),
)

_VACUOUS_OPT_CREATE = (
    "vacuous pass: production does not read optimization_goals after schema validation, and "
    "'optimization validation passes' checks only for a media_buy_id"
)
_VACUOUS_OVERLAY_UPDATE = (
    "vacuous pass: 'success' never reads back the stored targeting_overlay, so a seller that drops the overlay passes"
)

#: What PASSES but grades nothing, or grades a claim the pin contradicts. xfail(run=False).
# Graduated in wave 5 (integrate/wave1 a3687d971): w5b01, w5b07, w5b08, w5b10, w5b11, w5b12,
# w5b13, w5b14, w5b15, w5b16, w5b17, w5b18, w5b19, w5b21, w5b22, w5b23, w5b24.
_UC002_UC003_VACUOUS_PARKS: tuple[_CatchallXfail, ...] = (
    # ── UC-002 ──
    _X(
        "T-UC-002-boundary-optimization-goals",
        _VACUOUS_OPT_CREATE,
        rows=(
            "product has conversion_tracking + event goal submitted",
            "product has metric_optimization + metric goal submitted",
        ),
    ),
    # ── UC-003 ──
    _X(
        "T-UC-003-boundary-targeting-overlay",
        _VACUOUS_OVERLAY_UPDATE,
        rows=("keyword_targets with unique tuples",),
    ),
    _X(
        "T-UC-003-partition-targeting-overlay",
        _VACUOUS_OVERLAY_UPDATE,
        rows=("keyword_targeting",),
    ),
)

_NO_UPDATE_WITHOUT_ID_ON_REST = (
    "the REST binding carries media_buy_id as the resource path (PUT /media-buys/{media_buy_id}, "
    "src/core/tools/registry.py), so an update_media_buy that names no media_buy_id has no REST "
    "request to send; the row grades on a2a and mcp, which carry the id in the body"
)

#: Examples rows a transport cannot express: its binding has no request for the state the row
#: names, so there is nothing to send and nothing to grade on that transport. This is neither a
#: failure (the ledger) nor a pass that grades nothing (the parks); nothing is wrong on either
#: side. Each entry names its rows and its transports, the row is xfail(run=False) on exactly
#: those transports with the reason below, and it runs and grades on every other transport.
#: Pinned by tests/unit/test_architecture_route_xfail_census.py with the two lists above.
_TRANSPORT_INEXPRESSIBLE_ROWS: tuple[_CatchallXfail, ...] = (
    _X(
        "T-UC-003-partition-media-buy-identification",
        _NO_UPDATE_WITHOUT_ID_ON_REST,
        rows=("neither_provided",),
        transports=("rest", "e2e_rest"),
    ),
    _X(
        "T-UC-003-boundary-media-buy-identification",
        _NO_UPDATE_WITHOUT_ID_ON_REST,
        rows=("neither identifier (missing)",),
        transports=("rest", "e2e_rest"),
    ),
)


def _catchall_ledger_marker(marker_names: set[str], nodeid: str) -> pytest.MarkDecorator | None:
    """The marker the explicit row lists assign, or None.

    A row a transport cannot express is not run there, whatever else names it; then a ledger
    entry wins over a park.
    """
    for entry in _TRANSPORT_INEXPRESSIBLE_ROWS:
        if entry.matches(marker_names, nodeid):
            reason = f"not applicable on {_transport_of(nodeid)}: {entry.reason}"
            return pytest.mark.xfail(reason=reason, run=False, strict=True)
    for entry in _UC002_UC003_LEDGER:
        if entry.matches(marker_names, nodeid):
            return pytest.mark.xfail(reason=f"{entry.reason} ({_CATCHALL_RUN})", strict=True)
    for entry in _UC002_UC003_VACUOUS_PARKS:
        if entry.matches(marker_names, nodeid):
            return pytest.mark.xfail(reason=f"{entry.reason} ({_CATCHALL_RUN})", run=False, strict=True)
    return None


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Apply xfail markers to scenarios with unimplemented production features."""
    for item in items:
        marker_names = {m.name for m in item.iter_markers()}
        nodeid = item.nodeid

        transport = _transport_of(nodeid)
        is_mcp = transport == "mcp"
        is_a2a = transport == "a2a"
        is_rest = transport == "rest"
        is_impl = transport == "impl"
        is_e2e_rest = transport == "e2e_rest"

        # Graduated: T-UC-002-ext-i on MCP. The reason said MCP validates the payload
        # before checking auth, which is TRUE and is filed as
        # https://github.com/prebid/salesagent/issues/2243 -- but it was not what made
        # this scenario red. The scenario sent a near-empty body while asserting
        # AUTH_MISSING, so it was grading schema validation, and on a2a and rest it was
        # failing OUTSIDE this routing for the same reason. It now carries `And a valid
        # create_media_buy request`, which is what an auth scenario has to send, and all
        # three transports answer AUTH_MISSING. The ordering divergence needs its own
        # scenario -- no credential AND a malformed body -- which #2243 describes.

        # THIS ESCAPE HATCH IS NOW EMPTY, and both halves were emptied by different lanes
        # for independent reasons. A UNION MERGE: each lane deleted the entries it had
        # earned the right to delete, and neither deletion subsumes the other.
        #
        # The three TYPE-FILTER entries are gone because their scenarios are gone.
        # inv-031-1-violated now filters on asset_types + name_search, and the two type
        # outlines are deleted, so nothing here "passes vacuously over the live server"
        # any more — nothing here dispatches unfiltered. The reason they used to carry
        # was also wrong: "removed from the SDK 5.7 request model (adcp 3.12)" read as
        # codegen drift, and 3.12 is not a version that exists in the schema tree. The pin
        # ships TWO list-creative-formats requests; `type` belongs to the "(Creative
        # Agent)" one, and the media-buy request this seller serves has not declared it
        # since the 3.0 MAJOR. There was never a filter to carry.
        #
        # The two DISCLOSURE entries are gone because PRODUCTION WAS FIXED: the pin's
        # uniqueItems refusal on disclosure_positions now exists, so the transport-
        # independent gap the hatch existed for is closed. The consequence is deliberate
        # and belongs in a bdd-in-network run's reading: with no escape hatch, the
        # disclosure outlines are now GRADED over e2e_rest, where the duplicate rows
        # should pass for the same DTO-level reason they pass in-process.
        #
        # With the set empty, the two `tag in uc005_filter_e2e_untestable` branches below
        # can never match. They are inert rather than wrong, and deleting them changes
        # control flow, so that is a follow-up and not a merge resolution.
        uc005_filter_e2e_untestable: set[str] = set()
        uc005_filter_e2e_reason = (
            "e2e_rest: unreachable — uc005_filter_e2e_untestable is empty, so no tag selects "
            "this reason. Kept only so the branches that read it still type-check; see the "
            "comment above for why both halves were removed"
        )

        # Graduated: UC-005 creative agent type/asset_type filter tests now pass —
        # When steps dispatch through harness (blanket xfail removed).

        # NOTE (#1417/S5 reconciliation): the UC-002 @account error
        # scenarios (ext-r / ext-r-nk / ext-s / ext-t) are NOT impl-exclusive and
        # are NOT a wire-only gap — they failed on ALL four transports (impl + wire)
        # in the pre-drop baseline. They are the pre-existing budget-branch When-step
        # routing bug (create_media_buy account-resolution scenarios build a request
        # with `account_ref`, which CreateMediaBuyRequest rejects). That is a step
        # bug fixable in the When step, not a production wire gap, so it is left as a
        # genuine (pre-existing) failure rather than masked with an xfail. The
        # drop-impl change introduces 0 new failures; this debt is out of scope.

        # Transport-specific xfails: MCP wrappers don't accept certain filter params
        if is_mcp:
            for tag, substrings, reason, strict in _MCP_SELECTIVE_XFAIL:
                if tag in marker_names:
                    if not substrings or any(s in nodeid for s in substrings):
                        item.add_marker(pytest.mark.xfail(reason=reason, strict=strict))
                    break

        # UC-011 REST: per-request auth implemented
        # UC-011 MCP: billing policy and approval mode now populated from DB via
        # account_approval_mode column + proper harness writes (#1184 complete).

        # Graduated: T-UC-011-ext-d-push — push notification test now passes
        # (approval workflow implemented or assertion adjusted)

        # Graduated: UC-006 REST account resolution (success AND error paths).
        # SyncCreativesBody now forwards `account`, so the sync_creatives REST route
        # resolves accounts and raises ACCOUNT_* errors instead of returning 200.
        # The former xfail block for T-UC-006-partition-account/boundary-account error
        # rows on rest is removed — those scenarios pass.

        # RESOLVED: MCP transport suggestion field now correctly unpacked by
        # _unwrap_mcp_tool_error (was double-nesting the extra JSON blob).

        # RESOLVED: in-process REST no longer drops UC-005 filter params. The
        # CreativeFormatsEnv.build_rest_body override that returned {} was removed,
        # so [rest] serializes the request body and filters for real — the former
        # _REST_XFAIL_TAGS block is gone (see note above the function).

        # Graduated: the e2e_rest route for T-UC-005-empty-catalog ("E2E Docker always has
        # creative agents — cannot test empty catalog"). XPASS(strict) on e2e_rest in box run
        # innet_291026_0131, and passed on a2a, mcp and rest in the same run. Its premise is
        # gone: the Given seeds the catalog through set_registry_formats ->
        # seed_own_creative_agent, which serves the empty catalog from the tenant's own peer
        # creative agent and turns off use_default_creative_agent, so the live server's
        # registry (_get_tenant_agents) lists that peer alone. The scenario now also grades
        # that no errors[] rides the response, so a peer that failed (AGENT_UNREACHABLE,
        # zero formats) cannot satisfy it.

        # E2E_REST: the 3 UC-003 manual-approval scenarios (T-UC-003-alt-manual,
        # T-UC-003-approval-tenant, T-UC-003-approval-adapter) GRADUATED — the old
        # strict xfail ("RestE2EDispatcher lacks update-endpoint support") became
        # stale when MediaBuyDualEnv gained dynamic REST_ENDPOINT/REST_METHOD update
        # dispatch (_active_update, PR #1567 lineage) and the trio XPASSed the
        # in-network run. They now grade on all four transports.
        # Per-scenario graduation inspection (scenario → BR → siblings → production):
        # - T-UC-003-alt-manual → GRADUATE — POST-S7/S8: MediaBuyDualEnv.build_rest_body
        #   sets _active_update + _update_target_id, so RestE2EDispatcher PUTs the real
        #   /api/v1/media-buys/{id} route (src/routes/api_v1.py:345) and stashes the raw
        #   HTTP JSON as wire_response; task_id/NOT-contain steps grade that wire via
        #   _submitted_wire_dict (loud failure if wire_response missing on non-IMPL).
        # - T-UC-003-approval-tenant → GRADUATE — BR-RULE-017 INV-2: same real-wire path;
        #   status "submitted" asserted on the typed payload parsed from the live wire.
        #   That pass rode the adapter flag its Given also raised; with the Given setting the
        #   tenant flag alone it is xfailed in _XFAIL_TAGS against #2483 on every transport.
        # - T-UC-003-approval-adapter → GRADUATE — BR-RULE-017 INV-3: same real-wire path;
        #   the last_a2a_task guard is Transport.A2A-gated and inert on e2e_rest.
        # No UC-003 entries remain in e2e_rest_known_failures.txt — no sibling conflict.

        # Graduated: eleven of the twelve scenarios of the _UC005_E2E_FIXTURE_INJECTION_TAGS
        # set that stood here, a strict=False xfail on e2e_rest: inv-031-1-holds,
        # inv-031-2-holds, inv-049-2-violated, inv-049-3-holds, inv-049-3-group,
        # inv-049-4-holds, inv-049-5-holds, inv-049-6-holds, inv-049-7-holds,
        # inv-049-7-violated and dim-boundary. All eleven XPASSED on e2e_rest in box run
        # innet_291026_0257 and passed on a2a, mcp and rest in the same run. The twelfth,
        # inv-049-2-holds, graduated later (see below). The set's reason ("set_registry_formats has
        # no sidecar mock") stopped being true when the format Givens began seeding through
        # CreativeFormatsEnv.set_registry_formats -> seed_own_creative_agent: the tenant's
        # own peer creative agent (the compose stack's capture service on e2e) serves
        # exactly the Given's formats and use_default_creative_agent is off, so the live
        # server lists those formats and no others. Each filter is graded against
        # media-buy/list-creative-formats-request.json @ 3.2.7 (ANY-render inclusive
        # widths, case-insensitive partial name_search, is_responsive, format_ids).
        # The exclusion Thens ("no formats" / "X should not be returned") and
        # inv-049-2-violated's "no error should be raised" now require a successful
        # response on the wire, so a refusal can no longer satisfy them.
        #
        # Graduated: T-UC-005-inv-049-2-holds on e2e_rest. The scenario seeds
        # "foreign-leaderboard" under the same id from another agent, which grades the
        # (agent_url, id) pair identity of core/format-id.json @ 3.2.7; it passed on a2a, mcp
        # and rest and XPASSED on e2e_rest in innet_291026_2041, the route's exit condition.

        # FIXME(#1291, #2098): E2E_REST — these Thens observe env.mock['post'],
        # the in-process local origin, or CircuitBreaker state, none of which is
        # visible through the Docker HTTP path (the local origin listens on the
        # RUNNER's loopback, not the container's, so a container-side delivery
        # could never reach it even if the Then could see it).
        #
        # The cited id used to be a beads id that DOES NOT RESOLVE
        # ('bd show' returns no issue found). This project's rule is that code
        # comments cite a GitHub issue number precisely so they resolve for outside
        # contributors — a dangling internal id is worse than no reference, because
        # it reads as tracked.
        #
        # The stated unblock condition is now MET: a TLS-fronted receiver exists
        # (tests/e2e/webhook_capture_service.py) and the e2e webhook suites deliver
        # to it. What remains is per-scenario work, NOT a bulk removal: each Then
        # must be rewritten to read that receiver's captures instead of the
        # in-process mock, and each scenario re-checked for vacuity under
        # .claude/rules/workflows/xpass-graduation.md. Removing the tags without
        # that would turn the remaining 11 scenarios green against assertions that
        # can no longer observe anything.
        #
        # The receiver is no longer the only prerequisite: a delivery that happens
        # IN the test process is one the live server never made. `env.deliver_webhook()`
        # / `env.last_delivery()` (tests/harness/_mixins.py) are the seam that fixes
        # that; a tag graduates when its Thens read through them AND the behaviour it
        # asserts is one the live delivery path actually has.
        _UC004_E2E_WEBHOOK_INTERNAL_TAGS: set[str] = {
            # Graduated e2e_rest (salesagent-n78j0.13): T-UC-004-webhook-bearer. Traced
            # independently of the hmac row graduated below it — the two share a tag set, a
            # step layer and a harness, and this epic has twice had such neighbours be wrong
            # about each other, so "the sibling graduated" was treated as a hypothesis to
            # test rather than a reason.
            #
            # This row is STRUCTURALLY WEAKER than its hmac sibling and the inspection was
            # aimed at that: hmac has three Thens, one of which RECOMPUTES the digest over
            # the received bytes; bearer has ONE Then and no recompute of any kind. So the
            # question that decided it was not "does the delivery happen" but "does the lone
            # Then grade the token's VALUE, or merely the header's PRESENCE?" A presence-only
            # assertion is the vacuity signature: it passes for any Authorization header
            # anything happens to attach.
            #
            # It grades the VALUE. The chain, verified end to end rather than assumed:
            # `given_bearer_token_valid` sets ctx['webhook_bearer_token'] = 'b'*32;
            # `_auth_scheme_to_db_fields` maps scheme 'bearer' onto authentication_type +
            # authentication_token; `_persist_webhook_config_if_needed` writes that row; the
            # live server's `_send_report_for_media_buy` finds it by
            # (principal, tenant, url, is_active) — NOT the auth-less
            # `raw_request["reporting_webhook"]` — so `legacy_auth_mode` returns LEGACY_BEARER
            # (scheme not in _HMAC_SCHEMES, token non-empty) and `build_webhook_sender` takes
            # the `from_bearer_token` arm. `then_bearer_header` reads
            # `env.last_delivery()` — the TLS capture receiver, never `env.mock["post"]` —
            # and asserts token == ctx's token, so expected is TEST-owned and actual is
            # off the wire. Not circular.
            #
            # Verified by mutation, and the mutation shape was chosen to separate the two
            # questions: a WRONG-BUT-PRESENT token (sign with a different 32-char value),
            # NOT a removed header. Removing the header would only re-prove presence, the
            # half that was never in doubt. The leg goes RED on the wrong value, which is
            # what "grades the VALUE" means. Run ids for the pair are in the COMMIT BODY,
            # for the reason the sibling note below records.
            #
            # ONE LATENT WEAKNESS, RECORDED NOT FIXED (it does not affect this graduation,
            # and widening scope mid-graduation is how a row gets strengthened into passing):
            # the value assertion is CONDITIONAL — `if expected_token:` — so it silently
            # degrades to presence-only if a future Given ever stops setting
            # ctx['webhook_bearer_token']. Today the Given sets it unconditionally and the
            # only step that pops it (`given_webhook_no_authentication`, :569) belongs to
            # the 9421 scenario, so the branch is live here. Also, unlike its 9421 twin this
            # scenario asserts no NEGATIVE: :1425 forbids signing the same webhook both
            # ways, and nothing here would catch a delivery that carried Authorization AND
            # a 9421 Signature.
            #
            # Graduated e2e_rest (salesagent-n78j0.13): T-UC-004-webhook-hmac. Traced
            # independently of its 9421 sibling rather than carried by it, because "the
            # neighbour was fixed" is inference and not evidence. What the inspection
            # found, end to end: the When routes through `env.deliver_webhook()`, whose
            # e2e realization drives the live server's own
            # /admin/.../trigger-delivery-webhook route; the server's
            # `_send_report_for_media_buy` looks up DBPushNotificationConfig by
            # (principal, tenant, url, is_active) and that row — NOT the auth-less
            # `raw_request["reporting_webhook"]` that `_attach_reporting_webhook` writes —
            # is what carries the HMAC registration, so `build_webhook_sender` takes the
            # LEGACY_HMAC arm (`legacy_auth_mode` :463) and signs with
            # `from_adcp_legacy_hmac`. All three Thens read `env.last_delivery()`, i.e.
            # the TLS capture receiver, and the last one RECOMPUTES the digest over
            # `captured.content` — the bytes the receiver actually got — so it fails when
            # the bytes signed are not the bytes sent. None of them touches
            # `env.mock["post"]`, which is what made the rest of this set unobservable
            # over Docker HTTP.
            #
            # STALE COMMENT CORRECTED: this file's :412 still says of this same tag
            # "DB setup fix exposed that Then steps are pending (no-op) — test passes
            # trivially". That has not been true since #1291 C1 / salesagent-n78j0.1.4
            # routed the Thens; it described the tree at the time it was written and was
            # never revisited. It is the exact claim this graduation had to disprove, and
            # a reader who trusted it would have concluded the opposite of the truth.
            #
            # Verified by mutation, not by the green mark: blanking the legacy-HMAC arm in
            # `build_webhook_sender` turns this e2e_rest leg RED. Run ids for the pair are
            # in the COMMIT BODY, for the reason the sibling note below records — any edit
            # to this file voids the pair, so a citation kept here could never stay valid.
            #
            # Graduated e2e_rest (salesagent-n78j0.1.4): T-UC-004-webhook-9421. The
            # bypass this set records is now GONE for that scenario — the delivery
            # ACTION moved out of the step layer into `env.deliver_webhook()`, which
            # over e2e drives the live server's own
            # /admin/.../trigger-delivery-webhook route, its key is minted INSIDE the
            # container (the feature file's :301 comment recorded that no key was ever
            # provisioned for this leg), and its Thens read the TLS capture receiver
            # through `env.last_delivery()`. Verified by mutation: deleting
            # `_rfc9421_sender`'s signing arm turns the e2e_rest leg red. The two run ids
            # behind that sentence are cited in the COMMIT BODY rather than here, and
            # deliberately: any edit to this file voids the pair (tox.ini :181 collects
            # `pytest tests/bdd/`), so a comment that must be rewritten with each new pair
            # can never hold a valid citation. Whichever pair the commit cites, this
            # sentence is only true of a run on the committed tree.
            #
            # ---- the note below governs the NEXT tag, not the graduated one ----
            #
            # STAYS PARKED, and the transport bypass is no longer the reason — that
            # one is fixed (above). What remains is a real SERVER-SIDE gap: the live
            # delivery path emits NotificationType.scheduled unconditionally
            # (delivery_webhook_scheduler.py :267), so the `final` / `delayed` /
            # `adjusted` Examples rows cannot pass over e2e_rest whatever the harness
            # does. Unparks when production selects the notification type; grading that
            # is not this atom's scope (salesagent-n78j0.1.4).
            #
            # MERGE NOTE (#1721 x #1291): the #1721 side graduated this tag and
            # -no-aggregated off this set, measured on a tree where the webhook sender ran
            # IN THE TEST PROCESS on every transport, e2e_rest included. That premise does
            # not survive the merge: `deliver_webhook` is now
            # @realize_e2e(_deliver_via_live_server) (tests/harness/_mixins.py), so the
            # Whens both scenarios use (`_call_webhook_service`) drive the LIVE server over
            # e2e_rest, where the scheduler still pins notification_type=scheduled
            # (delivery_webhook_scheduler.py) and the delivery result still carries
            # aggregated_totals (GetMediaBuyDeliveryResponse requires it). Both stay routed.
            # The same premise change is owed a re-grade for the four tags the merge DID
            # drop from this set — -retry-success, -sequence, -retry-5xx, -no-retry-4xx —
            # and only a bdd-in-network run can settle it.
            #
            # XPASS INSPECTED AND REFUSED 2026-10-09 (innet_091026_1343). One of this
            # outline's four e2e_rest rows — [scheduled-should] — reported XPASS while
            # [adjusted], [final] and [delayed] reported xfail, so the tag-level route is
            # the only thing that can hold it: remove the tag and three rows go RED. And
            # the one row that passes is the one that CANNOT fail here, for the reason the
            # note above states: delivery_webhook_scheduler.py:332 assigns
            # NotificationType.scheduled unconditionally, so a request for a scheduled
            # report gets `scheduled` whether or not production selects anything. The
            # XPASS is the hardcode, not the behavior. A row-level narrowing would turn
            # that into a plain PASS and read as coverage, which is strictly worse.
            # Graduates with the same production change the note already names.
            "T-UC-004-webhook-notification-type",
            # STAYS (salesagent-n78j0.13) — FAILED INSPECTION STEP 5 (Then assertions), and
            # it fails it in a shape neither graduated neighbour had, which is why "hmac and
            # bearer graduated" was not allowed to carry it.
            #
            # The seam is RIGHT here: the Then reads `env.last_delivery()` (the TLS capture
            # receiver), parses `captured.content`, and both `_get_last_webhook_payload` :77
            # and `last_delivery()` (_mixins.py :859) fail loudly on an empty payload / absent
            # delivery. So the usual vacuity routes — empty box, failed delivery, never read —
            # are all closed. It is still vacuous, structurally:
            #
            # `assert "aggregated_totals" not in payload` inspects ONLY TOP-LEVEL keys, and the
            # two production webhook builders emit different body SHAPES:
            #   in-process (WebhookDeliveryService.send_delivery_webhook :302) -> FLAT body, so
            #     top level is where the field would sit and the assertion is meaningful;
            #   live server / e2e_rest (admin trigger -> _send_report_for_media_buy ->
            #     create_mcp_webhook_payload :332) -> McpWebhookPayload ENVELOPE, whose
            #     top-level field set is FIXED. `aggregated_totals` is structurally impossible
            #     there, so this leg cannot fail for any behaviour of the delivery path.
            #
            # And the obligation is not merely ungraded, it is VIOLATED. Pinned 3.1.1,
            # building/by-layer/L3/webhooks.mdx :253 — "aggregated_totals ... must not be
            # emitted in reporting webhook RESULT PAYLOADS" — names `result`, which is exactly
            # where production puts it. Observed on the wire, run innet_220826_0718 (probe over
            # a strict e2e_rest leg, tag temporarily lifted, then restored by content copy):
            #   top_level=[idempotency_key, protocol, result, status, task_id, task_type,
            #              timestamp]
            #   result.aggregated_totals={'impressions': 11111.0, 'spend': 111.11,
            #                             'media_buy_count': 1}
            # with result also carrying currency / media_buy_deliveries / reporting_period /
            # sequence_number — i.e. the delivery ARRIVED intact, so the top-level absence was
            # observed against a populated payload, not an empty one. The real Then was GREEN
            # in that same run. No mutation was needed to "add aggregated totals": production
            # already ships them and this assertion does not see them. GH #2058.
            #
            # Unparks when the Then grades the RESULT payload (and production stops emitting
            # the field). Strengthening it now would strengthen a row into passing, which
            # proves nothing about production — so it is recorded, not patched, here.
            "T-UC-004-webhook-no-aggregated",
            # DEFERRED to prebid/salesagent#2060, which owns both halves of the
            # breaker's missing coverage. These two were briefly un-routed by
            # #2098's rewrite attempt; they are RESTORED here because #2060's
            # Conditions are explicit that the routing stays until the scenario
            # actually grades the live server. Un-routed, the leg reports a plain
            # PASS, which reads as real coverage — strictly worse than an XPASS,
            # which at least records that nothing is being graded.
            #
            # Measured, not assumed: deleting circuit_breaker.record_failure() from
            # the server and re-running in-network leaves this leg passing
            # (test-results/innet_260826_1216 vs _1221, byte-identical counts).
            # Re-run it yourself with `make mutation-check-breaker`.
            "T-UC-004-webhook-circuit-open",
            # PARKED, NOT GRADUABLE (salesagent-n78j0.13): T-UC-004-webhook-circuit-recovery.
            # Inspected in full against .claude/rules/workflows/xpass-graduation.md. The
            # reason recorded in the xfail above — "CircuitBreaker state not observable
            # through Docker HTTP" — is TRUE of this scenario but FALSE of the behaviour,
            # and the difference is the whole finding. Do not un-route on the strength of
            # the second half.
            #
            # (a) THE SCENARIO NEVER DELIVERS ANYTHING. Its When —
            # `when_deliver_probe_reports` (steps/domain/uc004_delivery.py:1063) — does not
            # send a webhook. It reaches into the breaker and increments the counter:
            #     cb = service._circuit_breakers.get(endpoint_key)
            #     for _ in range(n): cb.record_success()
            # The Given `the webhook endpoint has recovered and returns 200` sets a mock
            # HTTP status that is never read, because no HTTP call is made. So "the system
            # delivers 2 successful probe reports" is not what is executed.
            #
            # (b) THE BREAKER IT POKES IS NOT PRODUCTION'S. `CircuitBreakerMixin.get_service`
            # (tests/harness/_mixins.py:1005) constructs a FRESH WebhookDeliveryService().
            # Production's consumer reads the module singleton (webhook_delivery_service,
            # src/services/webhook_delivery_service.py:647) via `_is_circuit_breaker_open`.
            # Different objects — so even in-process, the state this scenario sets is state
            # production would never consult. `e2e_config` does not change this; it only
            # scopes the DB.
            #
            # MEASURED, NOT INFERRED. Two mutations, restored by content copy (md5
            # cbbda5965914e9d163e70faa17bfd6e9, empty diff):
            #   M1  break the HALF_OPEN->CLOSED transition inside record_success  -> RED (3/3)
            #   M2  break the DELIVERY path's record_success() call (:558, the 2xx
            #       branch of _deliver_with_backoff)                              -> GREEN
            # M1 going red proves only that the Then reads a real CircuitBreaker object by
            # direct in-process attribute access — NOT that any transport observed it. M2 is
            # the one that decides the row: production can stop recording delivery success
            # entirely and this scenario, the whole UC-004 module (516 passed) and the
            # unit/harness circuit tests (141 passed) all stay green.
            #
            # THE BEHAVIOUR IS OBSERVABLE OVER THE WIRE — the parked reason is too strong.
            # An open breaker is buyer-visible: _get_media_buy_delivery_impl consults it per
            # request (src/core/tools/media_buy_delivery.py:257) and rewrites the reported
            # status (:297) `if status == "active" and reporting_circuit_open: status =
            # "reporting_delayed"`. A scenario that drives REAL failing deliveries until the
            # status flips to reporting_delayed, then REAL succeeding ones until it returns
            # to active, grades open->recover->close through production's own singleton on
            # every transport including e2e_rest.
            #
            # Un-routing now would be a false green precisely because the scenario passes on
            # e2e_rest WITHOUT contacting the live server. Stays until rewritten against the
            # reporting_delayed seam, and any rewrite must show M2 RED before graduating.
            # Production coverage gap tracked as GH #2060.
            "T-UC-004-webhook-circuit-recovery",
            # #1873: retry observability — assert on the requests the endpoint
            # received. -retry-success and -sequence graduated off this note (see
            # above); this one still asserts on a connection that is REFUSED, so no
            # request ever exists for the capture service to record.
            "T-UC-004-webhook-retry-network",
            # Graduated 2026-09-15 (XPASS in-network, innet_150926_0531):
            # -retry-5xx and -no-retry-4xx. The note above put them here for a reason
            # that does not hold. The webhook SENDER runs in the test process on every
            # transport, e2e_rest included — it is not the deployed server's poller —
            # so the process-local sleep patch observes the real retry schedule there
            # exactly as it does on a2a/mcp/rest, and the POST count is a real readback
            # of the compose capture service, which records a 5xx- or 401-answered
            # request before answering it. Neither scenario reads CircuitBreaker state.
        }
        if is_e2e_rest and (marker_names & _UC004_E2E_WEBHOOK_INTERNAL_TAGS):
            item.add_marker(
                pytest.mark.xfail(
                    reason="E2E: in-process webhook origin + CircuitBreaker state not observable through Docker HTTP",
                    strict=False,
                )
            )

        # GRADUATED (#1417/nzjx): UC-003 empty update now rejected. Production raises
        # AdCPInvalidRequestError (INVALID_REQUEST + buyer suggestion) per BR-RULE-022
        # INV-3. Grounded against AdCP 3.1 GA: update fields are all optional in
        # update-media-buy-request.json, so an empty update passes schema validation and
        # is a SEMANTIC rejection → INVALID_REQUEST, not the schema-level VALIDATION_ERROR
        # (GA L3 error-handling). The two Scenario-Outline rows that asserted
        # VALIDATION_ERROR were corrected to INVALID_REQUEST in the same change.

        # Graduated: T-UC-003-alt-keyword-ops. Its xfail reason was factually wrong --
        # "production applies the keyword additions but returns empty affected_packages".
        # MEASURED: `git grep keyword_targets -- src/` returns ZERO hits, so production
        # never applied anything; it accepted a declared field and dropped it, answering
        # success. The scenario was over-specified too, asserting the obligation of a seller
        # that SUPPORTS keyword targeting.
        #
        # Both are fixed together: the scenario now asserts UNSUPPORTED_FEATURE (the
        # obligation this seller actually holds, per core/targeting.json's "applied exactly
        # or rejected") and _update_media_buy_impl refuses the op above the UoW.
        #
        # CORRECTION to the reason this marker was first removed. The removal was justified
        # as "with strict=True the now-passing scenario would fail as an unexpected pass".
        # That was WRONG: this scenario is not graded at all. MEASURED on a UC-003 keyword
        # slice -- test_add_keyword_targets_incrementally, _remove_, and the negative-keyword
        # sibling are XFAIL on every transport, because the `uc003-not-wired` EnvRoute below
        # xfails every UC-003 scenario that is not `T-UC-003-ext-*` with "harness not yet
        # wired for non-extension scenarios". So the marker was redundant with that route,
        # not standing between a pass and a failure.
        #
        # It stays removed because its TEXT was false twice over -- production applied
        # nothing, and the scenario no longer claims a production gap -- and because the
        # not-wired route already carries the honest reason. The thing to fix here is the
        # WIRING, not the marker: until UC-003's non-extension scenarios are wired, this
        # scenario and the two keyword siblings grade nothing whatever they assert.
        # They are wired now: the catch-all's reason is gone, and measure_innet_291026_1202
        # measured all three passing on a2a, mcp, rest and e2e_rest.

        # The strict xfail that stood here for T-UC-003-alt-creatives-inline ("FK violation in
        # _sync_creatives_impl assignment path") XPASSed on a2a, mcp, rest and e2e_rest in
        # measure_innet_291026_1202, the first run that dispatched it. The FK failure is gone,
        # but the pass is not graduated: its Thens read only the success branch, status
        # "completed" and affected_packages, so nothing observes the upload or the
        # assignment. It is parked as a vacuous pass in _UC002_UC003_VACUOUS_PARKS instead.

        # T-UC-003-boundary-revision / -partition-revision are wired (see _UC003_REVISION_TAGS
        # and the uc003-manual-approval row in the routing registry below), so their steps
        # RUN and each row fails or passes on its own assertion.
        #
        # Graduated: the CONFLICT rows (stale_revision, ahead_revision, and the boundary's
        # revision-below/above-current rows). They were strict xfails on two reasons: #1607,
        # update_media_buy accepting a stale or ahead revision; and on a2a, #1885, the skill
        # handler dropping `revision`. a2a now dispatches through `serve` like every other
        # transport (the handler log quoted below shows `revision` arriving), and
        # `_refuse_stale_revision` answers a mismatch with CONFLICT under the row lock
        # `MediaBuyRepository.lock_revision` takes -- media-buy/update-media-buy-request.json
        # /properties/revision, "sellers MUST reject the update with CONFLICT". The box run
        # that lands this change is the evidence each row passes on all four transports.
        # GRADUATED at the adcp 9.0.0 / AdCP 3.2.1 pin: the wrong_type row ('"7"').
        # Its reason was "pydantic runs in lax mode, so the string "7" is coerced to
        # 7 and a revision whose JSON type is wrong is ACCEPTED", and its named REMEDY
        # was strict typing on the field — deferred because `revision` is INHERITED
        # from the SDK model, so the decision was cross-cutting. The SDK made that
        # decision: at v9 the inherited field is
        #   Optional[Annotated[int, Strict(strict=True),
        #                      BeforeValidator(as_json_schema_integer)]]   (+ Ge(1))
        # and the measured behaviour now separates the two values the scenario says
        # must not behave alike — model_validate(revision="7") REFUSES with
        # [type=int_type] while revision=7 parses. The obligation itself did not move:
        # media-buy/update-media-buy-request.json declares revision
        # {"type": "integer", "minimum": 1} at the 3.2 pin, as it did at 3.1.
        # Not vacuous: the row's Then is `error "INVALID_REQUEST" with suggestion`,
        # which routes to TransportResult.assert_wire_error and raises outright when
        # no wire envelope was captured — a request that died building in the step
        # cannot pass it. The Given passes the string through UNCONVERTED into the
        # shared harness env's dispatch kwargs, so all three transports send the same
        # wrong-typed document. No second routing carries this tag: the only other
        # mention of T-UC-003-partition-revision is the uc003-manual-approval EnvRoute
        # (setup, not an exemption).
        # GRADUATED (the remaining INVALID_REQUEST rows). The entry described a harness
        # limitation -- "revision 0 is rejected by UpdateMediaBuyRequest's ge=1 during
        # request construction in the step, so the request never reaches the seller" --
        # and prescribed its own remedy: dispatch the raw payload instead of the typed
        # model. That remedy is in place. The a2a handler log for these rows now reads
        #   Found explicit skill invocation: update_media_buy with params:
        #   ['revision', 'idempotency_key', 'media_buy_id', 'paused', 'account']
        # so `revision` DOES reach the seller and the rows grade the seller's rejection
        # as written. Strict XPASS on all three transports.
        #
        # The wrong_type row ("7" as a string) came out of this graduation red, because
        # the blanket entry had been masking a REAL gap: pydantic lax mode coerced the
        # string to an int and the request succeeded. That is fixed at the field
        # instead of re-parked here -- see UpdateMediaBuyRequest.revision.

        # FIXME: UC-003 extension/error scenarios — production uses
        # different error codes than spec, or doesn't validate at all. These are
        # spec-production gaps where the step definitions are correct but production
        # code doesn't implement the expected validation.
        _UC003_EXT_XFAILS: dict[str, str] = {
            # Error code mismatches (production uses different codes than spec)
            # Graduated (#1417/gh8p.10): both auth error paths now carry a buyer-facing
            # suggestion. The REST auth boundary (_require_auth_dep) raises
            # AdCPAuthRequiredError with AUTH_REQUIRED_SUGGESTION (REST no-identity envelope
            # no longer drops it), and the unknown-principal ownership check
            # (AdCPAuthorizationError) carries a "verify your x-adcp-auth token" suggestion.
            # T-UC-003-ext-a / -ext-a-unknown pass on a2a/mcp/rest.
            # Graduated: T-UC-003-ext-c (xpassed on a2a, mcp, rest and e2e_rest in
            # innet_291026_2041). The scenario expects MEDIA_BUY_NOT_FOUND for a buy another
            # principal owns, per L1/security.mdx @ 3.2.7 (the body MUST NOT distinguish
            # unauthorized from not found), which production answers.
            # Graduated: T-UC-003-ext-d, T-UC-003-ext-d-negative (production now returns BUDGET_TOO_LOW)
            # Production doesn't validate these cases at all
            # Graduated in wave 5 (integrate/wave1 a3687d971): T-UC-003-ext-e and
            # T-UC-003-ext-e-equal (w5b21). update_media_buy refuses an inverted or equal
            # flight with INVALID_REQUEST on end_time (refuse_inverted_flight).
            # Graduated: T-UC-003-ext-f and T-UC-003-ext-g. Both entries named a stale .feature
            # expectation, and measure_innet_291026_1202 measured exactly that on all four
            # transports ("Expected INVALID_REQUEST, got UNSUPPORTED_FEATURE" and "Expected
            # BUDGET_TOO_LOW, got BUDGET_EXCEEDED"). The scenarios are corrected in place, with
            # @source citations to the pinned enums/error-code.json: a currency this seller does
            # not support is UNSUPPORTED_FEATURE, and a budget above the daily cap is
            # BUDGET_EXCEEDED, never BUDGET_TOO_LOW ("below the seller's minimum").
            # Graduated (#1417/gh8p.10): a failed creative sync no longer crashes with an
            # FK violation. _process_assignments skips assignment for un-synced creatives,
            # and update_media_buy raises a clean retryable AdCPAdapterError carrying a
            # buyer-facing retry suggestion. T-UC-003-ext-k passes on a2a/mcp/rest.
            # Graduated (#1417): the .feature now asserts standard codes
            # (VALIDATION_ERROR for an invalid placement id, UNSUPPORTED_FEATURE when the
            # product defines no placements; invalid_placement_ids is not in the AdCP
            # vocabulary @04f59d2d5) and production emits them with a recovery suggestion.
            # The placement fixture gap (placement_configs -> placements) is fixed.
            # T-UC-003-ext-m / -ext-m-unsupported pass; xfails removed.
            # T-UC-003-ext-n moved to a dedicated STRICT xfail below (production gap).
            # Graduated: T-UC-003-ext-o (rczc: adapter failure returns correct shape on all 4 transports)
            "T-UC-003-ext-q-rejected": "production doesn't reject updates to terminal-status media buys",
            "T-UC-003-ext-q-canceled": "production doesn't reject updates to terminal-status media buys",
            "T-UC-003-ext-q-completed": "production doesn't reject updates to terminal-status media buys",
            "T-UC-003-ext-r-keyword": "production doesn't validate keyword operation conflicts",
            "T-UC-003-ext-r-negative": "production doesn't validate negative keyword conflicts",
        }
        for tag, reason in _UC003_EXT_XFAILS.items():
            if tag in marker_names:
                item.add_marker(
                    pytest.mark.xfail(
                        reason=f"spec-production gap: {reason}",
                        strict=False,
                    )
                )
                break  # One xfail per scenario is sufficient

        # FIXME(production-gap bead): UC-003 ext-n insufficient
        # privileges. Storyboard BR-UC-003-ext-n grounds an ADMIN-only adapter gate
        # (e.g. GAM guaranteed-item activation) that emits the canonical
        # PERMISSION_DENIED (pinned enum @04f59d2d5; reconciled from the prose's
        # non-canonical "insufficient_privileges" in adcp-req BR-UC-003 impl-coverage).
        # Production has NO privilege gate on update, and the AdCP buyer protocol has
        # no principal-role concept (roles live on the admin-UI User model, not
        # Principal). The fields-less ext-n request also short-circuits through the
        # empty-update INVALID_REQUEST path before any adapter call. The step now
        # branches the real update adapter with a canonical PERMISSION_DENIED rejection,
        # so this strict xfail flips to a wire-asserted pass the moment production
        # gates admin-only update actions. Strict: fails loudly when that lands.
        if "T-UC-003-ext-n" in marker_names:
            item.add_marker(
                pytest.mark.xfail(
                    reason="production gap: no admin-only privilege gate on update_media_buy; "
                    "AdCP buyers have no principal-role concept and the fields-less request "
                    "short-circuits via empty-update INVALID_REQUEST before any adapter call "
                    "(canonical target: PERMISSION_DENIED)",
                    strict=True,
                )
            )

        # FIXME(production-gap bead): UC-003 ext-v cancellation
        # refused. canceled IS a valid UpdateMediaBuyRequest field but production
        # never reads it, has no state-based NOT_CANCELLABLE check, and
        # has_updatable_fields() omits canceled — so a media_buy_id+canceled
        # request trips the empty-update INVALID_REQUEST path instead of
        # NOT_CANCELLABLE. The step branches the update adapter with the canonical
        # NOT_CANCELLABLE refusal and dispatches the real cancel on the wire, so
        # this strict xfail flips to a pass when production wires the cancel path.
        if "T-UC-003-ext-v" in marker_names:
            item.add_marker(
                pytest.mark.xfail(
                    reason="production gap: update_media_buy never reads canceled and has no state-based "
                    "cancellation gate; has_updatable_fields() omits canceled so the request short-circuits "
                    "via empty-update INVALID_REQUEST (canonical target: NOT_CANCELLABLE)",
                    strict=True,
                )
            )

        # Retired (both sides, 20e5b60d8 / PR #1567 round-2 item 2): the former
        # T-UC-002-alt-manual workflow_step_id xfail targeted the pre-3.1.1
        # scenario assertion. The scenario was reconciled to the 3.1.1
        # CreateMediaBuySubmitted contract (task_id, no media_buy_id/
        # workflow_step_id) and passes on all 4 transports — a strict xfail here
        # would XPASS-fail.

        # Graduated: the _UC005_PARTIAL_TAGS set that stood here — a strict=False
        # xfail over T-UC-005-inv-049-8-violated and -nofield, citing FIXME(#1660)
        # "disclosure_positions ... not implemented in _impl (all transports)". That
        # sentence was true and is no longer: creative_formats.py now applies the filter
        # (AND semantics over the disclosure_capabilities -> supported_disclosure_positions
        # lookup), so both rows grade the real obligation on a2a/mcp/rest. Their reason
        # and the false "rejected at schema level" note are unpicked in full at the
        # _XFAIL_TAGS entry for the sibling -holds row.
        #
        # Being strict=False is why this hid for so long: both rows XPASSED on mcp/rest
        # against an empty catalog and a non-strict marker swallows an xpass, so nothing
        # ever said the filter was missing. The scenarios each carry a positive control
        # now, which is what makes an empty catalog fail them instead of satisfying them.
        #
        # The branch also gated the selective-xfail loop below (it was the `if`, the loop
        # was its `else`), so the loop is now unconditional — which is the behaviour every
        # non-UC-005 tag already got.

        # Graduated (#1417): the partition/boundary-disclosure "valid"
        # examples (all_positions / no_matching_formats / all 8 positions /
        # "format has no") return unfiltered results that satisfy the assertion,
        # so they now PASS on every wire transport (a2a/mcp/rest) — no marker.
        # NOTE: main's MCP-specific strict xfails ("MCP wrapper does not accept
        # the disclosure_positions keyword") are intentionally dropped here —
        # #1417 added disclosure_positions to the MCP list_creative_formats
        # wrapper, so MCP now accepts the
        # keyword exactly like a2a/rest and the valid examples pass on MCP too.

        # The UC-002 / UC-003 explicit ledger that replaced the two not-wired catch-alls.
        catchall_marker = _catchall_ledger_marker(marker_names, nodeid)
        if catchall_marker is not None:
            item.add_marker(catchall_marker)

        # Selective xfail for parametrized scenarios
        for tag, substrings, reason in _SELECTIVE_XFAIL:
            if tag in marker_names:
                if is_e2e_rest and tag in uc005_filter_e2e_untestable:
                    # tolerate either outcome — see uc005_filter_e2e_reason
                    item.add_marker(pytest.mark.xfail(reason=uc005_filter_e2e_reason, strict=False))
                    break
                if any(s in item.nodeid for s in substrings):
                    item.add_marker(pytest.mark.xfail(reason=reason, strict=True))
                break  # tag matched — skip remaining selective entries

        # Original rejection scenario missing webhook Given step.
        # Replaced by BR-UC-002-manual-overrides.feature with webhook config.
        if "T-UC-002-alt-manual-reject" in marker_names and "T-UC-002-alt-manual-reject-override" not in marker_names:
            item.add_marker(
                pytest.mark.xfail(
                    reason="missing webhook Given step — see test_uc002_manual_overrides.py",
                    strict=False,
                )
            )

        # NFR-006: original dispatch-in-Then scenario replaced by
        # BR-UC-002-nfr-enforcement.feature with proper Given/When/Then structure.
        if "T-UC-002-nfr-006" in marker_names:
            item.add_marker(
                pytest.mark.skip(
                    reason="replaced by test_uc002_nfr_enforcement.py::test_budget_below_minimum_order_size_is_rejected",
                )
            )

        # UC-002: e2e_rest auth middleware — unauthenticated_request graduated (pzqp),
        # but identity_missing still fails (error shape differs from spec).
        if is_e2e_rest and "T-UC-002-nfr-001-enforcement" in marker_names:
            if "unauthenticated_request" not in nodeid:
                item.add_marker(
                    pytest.mark.xfail(
                        reason="e2e_rest: Docker auth middleware rejects with AUTH_REQUIRED "
                        "before business logic — error shape differs from spec",
                        strict=True,
                    )
                )

        # UC-010 ROW-level park (_UC010_PARKED_ROWS). Keyed on the nodeid, so it is
        # the one half of the UC-010 park that CANNOT be an ENV_ROUTES row: a row's
        # `when` predicate sees the marker set and nothing else. Its tag-level half
        # (_UC010_PARKED_TAGS) IS a row — the dormancy row for that tag, which uses
        # the park's text as its reason — which is why only this loop survives here.
        #
        # `run=False` is what preserves the former behavior exactly. The park used
        # to be an imperative `pytest.xfail()` in the UC-010 fixture branch, BEFORE
        # the harness was built: the row does not execute. These rows would PASS
        # vacuously (their must_equal_when trigger is an unbacked block, so the
        # assertion short-circuits), so a plain `xfail` marker would run them and
        # report XPASS — counting a test that cannot fail as coverage, which is the
        # exact thing the park exists to refuse. Non-strict for the same reason.
        for tag, substrings, reason in _UC010_PARKED_ROWS:
            if tag in marker_names and any(s in nodeid for s in substrings):
                item.add_marker(pytest.mark.xfail(reason=f"{tag}: {reason}", run=False, strict=False))
                break

        # Tag-based xfail for all other scenarios
        for tag, reason in _XFAIL_TAGS.items():
            if tag in marker_names:
                # DELETED (#1721 F14b): the e2e_rest branch of T-UC-005-main used to add a
                # SECOND, strict=False escape hatch here. It was redundant with the first
                # one, by its own account: over e2e_rest the Given never reaches the graded
                # gap because CreativeFormatsEnv._validate_registry_formats raises
                # E2EUnsupportedSetup ("the live stack can't be told to serve arbitrary
                # synthetic format ids"), and that declaration is already pinned in
                # EXPECTED_UNSUPPORTED_DECLARATIONS and already surfaced as xfail by the
                # report hook above -- with its reason readable at the env method rather
                # than buried in a conftest branch. Two mechanisms for one gap is how an
                # escape-hatch registry grows; the weaker one goes. strict=False was the
                # weaker one in the literal sense too: it would have swallowed an xpass, so
                # if the live catalog ever DOES serve these ids, nothing would have said so.
                # DELETED 2026-09-15: the e2e_rest ``break`` for T-UC-005-main-referrals.
                # It existed because the tag carried an in-process-only xfail that e2e_rest
                # had to escape. The tag is gone from _XFAIL_TAGS entirely (the mock was the
                # gap, see the note at its former entry), so there is no marker for e2e_rest
                # to break out of; its remaining #7338 failure is one ledger row.
                if is_e2e_rest and tag in uc005_filter_e2e_untestable:
                    # tolerate either outcome — see uc005_filter_e2e_reason
                    item.add_marker(pytest.mark.xfail(reason=uc005_filter_e2e_reason, strict=False))
                    break
                item.add_marker(pytest.mark.xfail(reason=reason, strict=True))
                break

        # --- UC-002: validation xfails (production not implemented) ---
        # NOTE: the former account-ref entries (missing_account / invalid_oneOf_both /
        # "account field absent" / "both account_id and brand") were REMOVED by
        # #1417: those scenarios now dispatch a full create_media_buy on
        # the wire. account is OPTIONAL, so an absent account SUCCEEDS (not
        # INVALID_REQUEST); the oneOf-both shape is rejected by Pydantic at the
        # boundary as VALIDATION_ERROR. The feature outcomes were reconciled to
        # match production and the scenarios now pass on a2a/mcp/rest.
        # One reason, two rows' worth of entries: the two outlines exhibit the SAME fixture
        # GRADUATED: the nine inline-upload rows of the two creative-asset outlines, and the
        # shared _UC002_INLINE_FORMAT_FIXTURE reason with them. They graduated on exactly the
        # condition that reason named -- "These rows graduate when tpr3 reconciles the id" --
        # which this change is: the seeded product arm now carries display_300x250_image, the
        # id the pinned capture serves, and the per-id format-spec stub that made the old
        # synthetic id resolvable in process is deleted.
        #
        # GRADUATION RECORD, per .claude/rules/workflows/xpass-graduation.md, for all nine
        # together because they share ONE cause and ONE routing entry pair -- the protocol's
        # "one at a time" is about not sweeping unrelated xpasses into a bulk delete, and
        # these nine are one finding with nine nodeids:
        #
        #   ROUTING   two entries in _UC002_VALIDATION_XFAIL below, keyed
        #             ("T-UC-002-partition-creative-asset", {"uploads_only","both_paths"})
        #             and ("T-UC-002-boundary-creative-asset", {"upload with format"}),
        #             both strict=True.
        #   WHY NOT A WEAK PASS  strict=True is what proves it. A non-strict marker hides a
        #             weak scenario because failing and passing both read as xfail/xpass; a
        #             STRICT one turns the pass into a hard FAILED, which is how these nine
        #             announced themselves (9 XPASS, 9 FAILED, zero silent). The scenarios
        #             assert creative validation PASSES and then that the buy is created, so
        #             a vacuous pass would need the dispatch to succeed while seeding nothing
        #             -- and the rows that do NOT graduate here (the library-reference rows of
        #             the same outlines) stayed green throughout, which is the control.
        #   OBLIGATION RE-VERIFIED  the scenarios demand that a resolvable creative whose
        #             identity matches the product's arm passes the pre-adapter gate. Nothing
        #             in the pin changed; what changed is that the fixture can now satisfy
        #             both halves at once. Previously it could not: a referenced creative must
        #             be RESOLVABLE (id in the capture) and ACCEPTED (identity equal to the
        #             arm, compared whole), and with the arm on the synthetic id the only id
        #             satisfying the second failed the first.
        #   PRODUCTION TRACED  no production change. The seller was right in both directions
        #             all along -- it answered VALIDATION_ERROR "unknown format ... must be
        #             registered with the creative agent" for the unserved id, and
        #             "not one this product accepts" when only the creative moved. The reason
        #             string said so too ("not a seller gap").
        #   SIBLING TRANSPORTS  all three in-process transports graduate together (a2a, mcp,
        #             rest), which is expected: the marker was transport-independent. The
        #             e2e_rest rows of these outlines are the six this change exists to turn
        #             green and are measured in-network, not here.
        #
        # THE NINE NODEIDS, enumerated so membership is re-checkable without re-running
        # anything. All under tests/bdd/test_uc002_create_media_buy.py, each verified
        # individually as XPASS by nodeid (9 XPASS in the run, 9 rows leaving the xfail set,
        # same nine):
        #
        #   test_creative_asset_boundary_validation__boundary_point
        #     [a2a-valid inline upload-upload with format-creative validation passes]
        #     [mcp-valid inline upload-upload with format-creative validation passes]
        #     [rest-valid inline upload-upload with format-creative validation passes]
        #   test_creative_asset_partition_validation__partition
        #     [a2a-both_paths-creative validation passes]
        #     [a2a-uploads_only-creative validation passes]
        #     [mcp-both_paths-creative validation passes]
        #     [mcp-uploads_only-creative validation passes]
        #     [rest-both_paths-creative validation passes]
        #     [rest-uploads_only-creative validation passes]
        #
        # WHICH ENVIRONMENT THE XPASS WAS OBSERVED IN, stated because it is a condition on
        # the claim and not a footnote. Measured locally, same tree, three repeats each:
        #
        #   CREATIVE_AGENT_URL unset  -> 3 failed / 252 passed; the nine XPASS. Format
        #       resolution is served from the committed capture under ADCP_TESTING and
        #       nothing is dialled. One row (mcp-both_paths) failed in 1 of 4 runs, and ONLY
        #       in the run that reached the live public host (4 connection-attempt failures,
        #       30 host mentions; the clean runs had 0 of each) -- so that was the harness
        #       going networked on a missing env var, not the scenario.
        #   CREATIVE_AGENT_URL=agent.localhost:9443 -> 12 failed / 243 passed, IDENTICAL
        #       across three runs, and the nine fail `inline-cr-001: Configuration error`.
        #       That is salesagent-ol033.18's signature exactly: AdCPConfigurationError is
        #       the mapped form of an outbound refusal, the egress gateway refuses that
        #       destination, and SSL_CERT_FILE alone does not fix it.
        #
        # So BOTH local configurations are known-broken in documented ways and neither is
        # the authoritative environment. The nine XPASS in the hermetic one; their verdict in
        # the environment the in-network runner builds is NOT established here and is the
        # thing to confirm before this graduation is relied on.
        #
        # NOTE WHAT SELECTED THEM, because it is the quiet half: the deleted entries keyed on
        # Examples ROW-ID SUBSTRINGS ("uploads_only", "both_paths", "upload with format"),
        # not on the format id any step reads. So this routing is de-routed by renaming an
        # Examples row and NOT by changing a fixture's format id -- these nine graduated
        # because they started PASSING, which is the only reason that counts, and no row
        # label was touched. A fixture change that renames a row would drop a marker with no
        # error at all; that hazard is filed separately.
        #
        # The two entries are deleted below rather than left with a corrected reason: a
        # marker whose condition is met is not a marker.
        _UC002_VALIDATION_XFAIL: list[tuple[str, set[str], str]] = [
            # GRADUATED: the two daily-spend-cap entries are gone. Their reason -- "production
            # raises plain ValueError -> code=validation_error, no suggestion. Spec expects
            # BUDGET_TOO_LOW with suggestion field" -- described a production that no longer
            # exists: the raise sites are typed (AdCPBudgetExceededError for the daily cap,
            # AdCPBudgetTooLowError for minimum spend) and the feature was reconciled to
            # BUDGET_EXCEEDED, which is what the pin associates with a daily ceiling.
            #
            # They did not graduate on the strength of that alone. The scenarios also sat on
            # the uc002-not-wired ENV_ROUTES catch-all, so nothing dispatched and the xpass
            # this marker was supposed to expose could never occur -- 48 items, 48 xfailed,
            # zero executed. Both markers came off together: the routing moved to the
            # uc002-ext row and this pair was deleted, because removing either one alone
            # leaves the scenarios exactly as dormant as before.
            # GRADUATED, and the four entries that stood here are replaced by the two below
            # — a narrowing from seven marked rows to three, every one of them measured.
            #
            # The four deleted entries carried two reasons and BOTH WERE FALSE. "Production
            # uses NOT_FOUND/VALIDATION_ERROR/INVALID_CREATIVES, spec expects
            # CREATIVE_REJECTED" described a disagreement the feature itself had already
            # settled in production's favour: the three invalid partition rows were
            # regrounded to CREATIVE_NOT_FOUND / VALIDATION_ERROR (the citation sits above
            # those Examples), so there was nothing left to mismatch. "max_creatives limit
            # not enforced in production" was wrong about its own row, which asks for
            # INVALID_REQUEST — exactly what production raises for 101 creatives.
            #
            # Neither could be tested while it stood. Both outlines also sat on the
            # uc002-not-wired ENV_ROUTES catch-all, which xfails at FIXTURE SETUP, so nothing
            # dispatched and these strict=True markers had nothing to grade. MEASURED
            # baseline (box run 3b83d400e1234a7bb0ec79df6a32fadd): 54 items, 54 xfailed,
            # ZERO executed. Both markers come off together, exactly as the daily-spend-cap
            # pair above did: removing either alone leaves the rows as ungraded as before.
            #
            # With the outlines claimed on the uc002-ext row, 45 of the 54 items PASS across
            # a2a/mcp/rest — 24 of 30 on the partition outline (box run
            # d376b293041b48629441884f67699a44) and 21 of 24 on the boundary outline (box run
            # d9e341c094e845b3b1ff3a2608e67295), the latter reported as XPASS(strict) because
            # these markers were still on them.
            #
            # WHAT THE THREE SURVIVING ROWS ARE, and why the reason is a fixture and not a
            # seller. They are the three rows that UPLOAD AN INLINE CREATIVE, and they cannot
            # pass on either spelling of this suite's canonical test format id:
            #   display_300x250       — no agent serves it. An inline creative goes through
            #     sync_creatives, whose creatives/_validation.py resolves format_id through
            #     the creative-agent registry, and in a test process
            #     get_creative_agent_registry() returns ReferenceFormatsRegistry — the
            #     formats in tests/fixtures/creative_formats/reference_formats.json, which is
            #     also what the e2e server serves. That catalog carries
            #     display_300x250_image / _html / _generative / _2x and NO bare
            #     display_300x250, so the seller correctly answered REFERENCE_NOT_FOUND and
            #     the create surfaced it as CREATIVE_REJECTED.
            #   display_300x250_image — the agent serves it, but the product
            #     _seed_media_buy_chain creates advertises the bare id
            #     (tests/harness/media_buy_create.py:180), so the create correctly answers
            #     VALIDATION_ERROR "not accepted by product prod_1".
            # Production is right in both directions; the FIXTURE is wrong.
            #
            # AND THE FIX IS NOT LOCAL TO THE HARNESS — MEASURED, not estimated. Moving the
            # product's seeded format_ids to the catalog id (tests/harness/
            # media_buy_create.py:180) and renaming the fabricated spec at :340 was tried on
            # this branch and REVERTED: box run e2370f95c3804e7ea214da9c64fffa8a turned these
            # two outlines from 45 passed / 9 xfailed into 24 passed / 23 failed, and every
            # new failure is a LIBRARY-REFERENCE row — assignments_only,
            # assignment_with_weight_zero, assignment_with_placement_targeting, "assignment
            # cr-001", weight=0, weight=100. The cause is symmetrical to the one above: those
            # rows seed their creative through given_media_buy._create_approved_creative,
            # whose default fmt is the BARE id, so moving only the product inverts the
            # product-vs-creative compatibility check for every one of them.
            #
            # THOSE SIX ROW LABELS ARE EXACTLY THE SIX THAT FAIL ON e2e_rest, which nobody
            # had connected to this comment. Read off box run innet_161026_2101 (commit
            # 72e80051d): the six bdd_e2e failures in this module are `valid library
            # reference / assignment cr-001`, `weight = 0 (paused)`, `weight = 100 (max)`,
            # `assignments_only`, `assignment_with_weight_zero` and
            # `assignment_with_placement_targeting` -- this list, label for label. They fail
            # with VALIDATION_ERROR "Creative ... has unknown format 'display_300x250' ...
            # must be registered with the creative agent" (`::1` and `upload` appear ZERO
            # times in that artifact; the SSRF rows carrying `::1` all PASS).
            #
            # So the in-process and e2e_rest symptom sets are DISJOINT and the stub is what
            # splits them: these six resolve `display_300x250` through the per-id stub in
            # process and so PASS there, failing only where resolution is real; the nine
            # INLINE-UPLOAD rows graduated above are the other half of the same root. One
            # cause, two symptom sets, and this comment had already enumerated one of them
            # from the in-process side months before the box run named the other.
            # So the bare id is a shared fixture CONSTANT, not three sites: ~10 more in
            # tests/bdd/steps/generic/given_media_buy.py (including an @T-UC-002-ext-g step
            # that ASSERTS the literal, and an ext-q Given that adds the catalog id to the
            # product precisely because the default lacks it), ~9 in
            # uc003_update_media_buy.py and ~7 in uc006_sync_creatives.py — and
            # MediaBuyDualEnv SUBCLASSES MediaBuyCreateEnv, so the UC-003 and UC-026 products
            # move with it. That is the scope tpr3 actually has.
            #
            # GRADUATED — the two entries that stood here are gone; the record is at the top
            # of this block. The analysis above is kept because it is the measurement that
            # made the graduation possible, and because its central claim turned out to be
            # exactly right: "the bare id is a shared fixture CONSTANT, not three sites". The
            # reconciliation moved the harness arm, deleted the per-id spec stub, and moved
            # the creative-side defaults in given_media_buy (fmt AND asset_key together,
            # since the capture declares banner_image rather than the stub's `primary`).
            #
            # One correction to it, since it is load-bearing for anyone reading this next:
            # the earlier attempt it describes as REVERTED ("24 passed / 23 failed") moved
            # only the PRODUCT arm and the spec name. The creative-side defaults have to move
            # in the same change — that is what inverted the product-vs-creative check for
            # every library-reference row, and it is why the full move lands instead.
        ]
        if any(t.startswith("T-UC-002") for t in marker_names):
            for tag, substrings, reason in _UC002_VALIDATION_XFAIL:
                if tag in marker_names and any(s in nodeid for s in substrings):
                    item.add_marker(pytest.mark.xfail(reason=reason, strict=True))
                    break

        # GRADUATED (#1534 merge): the former UC-002 oneOf-both account and
        # UC-004 webhook short-credential MCP routes are retired. The documented
        # "MCP TypeAdapter forward-compat gap" (FastMCP's TypeAdapter rejected
        # the request as a bare ToolError before the AdCP boundary translator
        # ran) is closed by RequestCompatMiddleware (#1534,
        # src/core/mcp_compat_middleware.py): TypeAdapter ValidationErrors are
        # now normalized to the AdCP two-layer VALIDATION_ERROR envelope on the
        # MCP wire (spec 3.1.1 enums/error-code.json names VALIDATION_ERROR for
        # schema-level rejections), matching what a2a/rest already emitted. The
        # strict=True markers fired as designed — deterministic XPASS on the
        # merged in-network run for both the oneOf-both rows (partition +
        # boundary) and webhook-creds-short — so the routes are removed and the
        # scenarios grade live on all transports.

        # Graduated: UC-002 ext-g inline-creative missing URL (#1417) no longer
        # xfails on MCP. The gap was: the inline creative carries a FormatId on the
        # wire, and `_upgrade_legacy_format_ids` (src/core/schemas/_base.py) wrote
        # LIVE FormatId objects into the caller's own request dicts — pydantic hands
        # a mode="before" validator its input by reference — so the dict that
        # reached rfc8785 idempotency canonicalization held an unserializable
        # object and raised a bare CanonicalizationError BEFORE the AdCP boundary
        # translator ran, yielding no two-layer envelope on MCP.
        #
        # `copy_before_mutating()` (same module) now gives that validator a
        # defensive copy, so the canonicalized dict stays plain JSON,
        # canonicalization succeeds, the boundary translator runs, and MCP emits the
        # same CREATIVE_REJECTED envelope a2a/rest already did. The strict=True
        # marker fired as designed — deterministic XPASS on run sa-d9585e1a — so the
        # route is removed and the scenario grades live on all transports.
        #
        # NOTE: this scenario's Then steps are weaker than the obligation (they
        # assert failure + "URL" in the RECONSTRUCTED message and never name an
        # error code, so CREATIVE_REJECTED itself is ungraded). That weakness is
        # pre-existing, not introduced by graduating this route; strengthening it to
        # a wire-envelope + error-code assertion is tracked separately.

        # Graduated: T-UC-006-ext-a. The strict xfail here said production answered
        # VALIDATION_ERROR for a missing principal; it answers AUTH_MISSING, which the
        # scenario names, so the route that parked it as "passes under a stale xfail" is
        # gone with the xfail and the scenario grades live with ext-a-empty.

        # Graduated: the UC-006 account rows that used to xfail as "INVALID_REQUEST
        # validation not implemented". The schema always refused them; the harness did
        # not send them -- a "not provided" account was replaced by the default one and
        # the both-branches reference was dropped before dispatch. They go on the wire
        # now (OMIT_ACCOUNT, and the oneOf violation verbatim) and the request model's
        # refusal is what the rows grade.

        # --- UC-006: spec-production gaps surfaced by Wave 1B step implementations ---
        # Production uses generic error codes / plain-string errors where the spec
        # demands specific codes and structured AdCPSalesAgentError with suggestion fields.
        _UC006_SPECGAP_XFAIL_TAGS: dict[str, str] = {
            # Graduated: T-UC-006-storyboard-multi-format-sync-status. SyncCreativeResult
            # now derives the spec `status` from the row's review state on every action
            # that has one (src/core/schemas/creative.py), so each per-creative entry
            # carries a creative-status member on the wire; the scenario grades live on
            # every transport.
            # Graduated: the four storyboard provenance scenarios (#1858). Production
            # refuses a creative that does not meet the product's provenance policy with
            # the pin's PROVENANCE_REQUIRED / _DIGITAL_SOURCE_TYPE_MISSING /
            # _DISCLOSURE_MISSING codes (check_provenance_policy,
            # src/core/tools/creatives/_validation.py) instead of appending a warning, and
            # Creative.provenance inherits the pinned core/provenance.json shape, so the
            # corrected resubmission is accepted.
            # Error-path scenarios: production returns plain-string errors[] instead of a
            # structured error. See _processing.py error handling paths. The ext-c/d/e/f/g
            # entries that stood here named codes the pinned enum does not define
            # (CREATIVE_VALIDATION_FAILED, CREATIVE_FORMAT_REQUIRED, CREATIVE_FORMAT_UNKNOWN,
            # CREATIVE_AGENT_UNREACHABLE); the scenarios were corrected to the pin and pass.
            # Graduated: T-UC-006-rule-037-inv4. The "Slack sent during the sync" it
            # recorded was the seam, not production: the env replaced the notification
            # function wholesale and the Then counted entries into it. The env runs the
            # real function now, whose guard skips Slack for ai-powered, and the Then reads
            # the Slack sender -- unused during the sync, with the review submitted to
            # defer to.
            # Invariant scenarios: production behaviour diverges from spec
            # Graduated: the gap this named is closed. The entry said the scenario asserted
            # the non-canonical FORMAT_MISMATCH while production emitted CREATIVE_REJECTED.
            # The .feature had already moved to VALIDATION_ERROR (the entry went stale), and
            # production now RAISES AdCPValidationError for a format outside the product's
            # declared set -- adcp 3.1.1's enum codes that "violates business rules beyond
            # schema validation", and reserves CREATIVE_REJECTED for "Creative failed content
            # policy review". Verified: the scenario passes under --runxfail on a2a, the only
            # transport it routes to, and it is not listed in e2e_rest_known_failures.txt.
            # T-UC-006-rule-037-inv5: e2e_rest only — handled below with transport check
            # Graduated: the sandbox flag. sync_creatives reads the account the request
            # names and sets sandbox=true on the success shape for a sandbox account;
            # the sandbox scenarios are the rows of @T-UC-006-boundary-sandbox now.
        }
        for tag, reason in _UC006_SPECGAP_XFAIL_TAGS.items():
            if tag in marker_names:
                item.add_marker(pytest.mark.xfail(reason=reason, strict=True))

        # Graduated: T-UC-004-webhook-bearer, T-UC-004-webhook-hmac,
        # T-UC-004-webhook-no-aggregated, T-UC-004-webhook-notification-type
        # (integration CircuitBreakerEnv now has make_webhook_config/set_db_webhooks
        # so webhook POST fires on all transports)

        # Graduated: UC-004 boundary-account a2a valid rows
        # ("account exists" / "single match" / "sandbox account exists") now resolve
        # once their accounts are seeded — the former "dict lacks .root serialization
        # gap" xfail was masking the missing seed, not a real transport gap.

        # --- UC-004: xfails for unimplemented production features ---
        # FIXME: These production features are not yet implemented.
        # strict=True: test MUST fail. strict=False: test MAY pass (some examples work).
        _UC004_XFAIL_TAGS: dict[str, tuple[str, bool]] = {
            # Graduated: T-UC-004-identify-empty. The reason -- "empty media_buy_ids=[] not
            # rejected by schema" -- no longer holds. The a2a boundary log shows the real
            # thing on this path: "A2A boundary translating AdCPInvalidRequestError to
            # envelope: INVALID_REQUEST (operation=get_media_buy_delivery)", i.e. a typed
            # error reaching the wire, which is what the scenario asserts. Strict XPASS, so
            # the pass is graded on the envelope rather than a reconstruction.
            "T-UC-004-identify-buyer-refs-empty": (
                "buyer_refs removed in adcp 3.12 — empty buyer_refs=[] is now an unknown field, silently ignored",
                True,
            ),
            # BR-RULE-092 INV-2: a seller that does NOT support configurable attribution must
            # DISCARD the buyer's requested window and answer with its platform default.
            # Production echoes the request back instead, so the response reports an
            # attribution window the seller will not actually honour.
            #
            # This was `try: assert ... except AssertionError: pytest.xfail(...)` inside
            # then_attribution_default — catch the failure and excuse it, the purest form of
            # the shape the conditional-xfail sweep removed. It could not fail in either direction, so
            # the echo had been reported as an expected failure on every run. strict=True
            # here means it XPASSes loudly the day production starts stripping the request.
            "T-UC-004-attr-unsupported": (
                "production echoes the buyer's attribution_window instead of discarding it and "
                "returning the platform default (BR-RULE-092 INV-2)",
                True,
            ),
            # Invalid status filter: NOT a production gap — the generic
            # 'with {request_params}' When step shadows the specific
            # status_filter step and parses 'status_filter "X"' (no '=') to {},
            # so the request dispatches with NO params and succeeds (ah98
            # red-step inspection, 2026-07-06). GetMediaBuyDeliveryRequest DOES
            # reject invalid values; the REST wire already returns 400.
            # Suggestion parity for this path is pinned by
            # tests/integration/test_request_validation_suggestion_parity.py.
            # Graduated: T-UC-004-filter-invalid. This entry recorded a TEST defect, not a
            # production gap -- the generic 'with {request_params}' When step shadowed the
            # specific status_filter step and parsed 'status_filter "X"' (no '=') to {}, so
            # the request dispatched with no params and succeeded. The generic step now
            # requires the \w+=... key=value form, which is mutually exclusive with the
            # space form, so the specific step matches and the invalid value reaches
            # GetMediaBuyDeliveryRequest -- which, as the comment above always said, DOES
            # reject it. Strict XPASS on a2a.
            # Date range validation: production doesn't validate start>end
            "T-UC-004-daterange-invalid": ("date range validation (start>end) not implemented", True),
            "T-UC-004-daterange-equal": ("date range validation (start==end) not implemented", True),
            # Webhook delivery: not yet in production
            "T-UC-004-webhook-scheduled": ("webhook delivery not implemented", True),
            # Graduated: T-UC-004-webhook-sequence (production fixed: sequence numbers now strictly ascending)
            # Graduated: T-UC-004-webhook-circuit-halfopen (merge from main fixed circuit breaker probe timing)
            # Graduated: T-UC-004-webhook-retry-5xx (production fixed: retry count now correct)
            # Graduated: T-UC-004-webhook-retry-network (ebb527c6 fixed the off-by-one)
            # Sandbox: not yet in delivery _impl
            "T-UC-004-sandbox-happy": ("sandbox mode not implemented in delivery", True),
            "T-UC-004-sandbox-validation": ("sandbox mode not implemented in delivery", True),
        }
        for tag, (reason, strict) in _UC004_XFAIL_TAGS.items():
            if tag in marker_names:
                item.add_marker(pytest.mark.xfail(reason=reason, strict=strict))
                break

        # UC-004: additional xfails for features needing production enhancements
        # FIXME: These require production changes, not BDD wiring.
        _UC004_XFAIL_ADDITIONAL: dict[str, tuple[str, bool]] = {
            # Graduated (#1721 M4 dormancy tripwire): T-UC-004-status-pending-legacy-alias
            # was masked by a missing second Then step (never actually reached the
            # assertion this xfail claimed was failing) -- production DOES correctly
            # surface the persisted pending_start status (XPASS(strict) once the
            # missing step was bound). Removed.
            # Graduated: T-UC-004-aggregated-roas-and-cpa (production now computes
            # conversions/conversion_value/roas/cost_per_acquisition in
            # aggregated_totals — DeliveryTotals.conversion_value + aggregation
            # quotients with omit-on-zero semantics).
            # T-UC-004-attr-supported: resolved — steps now assert attribution_window model and echo
            # T-UC-004-attr-unsupported: resolved — xfail now in step function for specific production gap
            # T-UC-004-attr-echo: resolved — vvx9 + ral2 fixed enum→str handling
            # T-UC-004-attr-omitted: resolved — vvx9 + ral2 fixed enum→str handling
            # T-UC-004-attr-campaign-valid: resolved — _impl now resolves campaign unit to days
            # T-UC-004-attr-campaign-invalid: GRADUATED (#1417). The standalone
            # "Campaign unit with interval != 1 - rejected" scenario now asserts on the wire
            # envelope (its When uses the non-shadowed 'for "mb-001" with attribution_window'
            # regex step, so the window reaches production and INV-5 fires VALIDATION_ERROR
            # with a suggestion on a2a and e2e_rest). The old transport-blind strict marker
            # was stale — removed rather than re-scoped (BDD has no in-process/_impl variant).
            # FIXME: _impl uses str(enum) instead of enum.value for sort_by metric
            # T-UC-004-dim-sortby-valid: resolved — sort_by now works in _impl
            # Graduated: T-UC-004-dim-sortby-fallback (impl, mcp, rest pass — only a2a still fails)
            # T-UC-004-dim-supported: resolved — by_device_type now populated by _impl (#1376)
            # T-UC-004-dim-truncated: resolved — truncation flags (by_*_truncated) now implemented (#1376)
            # T-UC-004-dim-complete: resolved — by_device_type_truncated flag now implemented (#1376)
            # T-UC-004-dim-geo-system: resolved — by_geo now populated by _impl
            # T-UC-004-dim-geo-postal: resolved — by_geo now populated by _impl
            # T-UC-004-dim-multi: resolved — by_device_type now on PackageDelivery (#1376)
            # Partial-success Error model lacks suggestion field and rich messages
            # SPEC-PRODUCTION GAP (#2229). get-media-buy-delivery-response.json says of
            # by_package[].rate: "For auction-based pricing, this represents the effective
            # rate based on actual delivery." _package_pricing derives nothing — it reads a
            # STATIC rate from pricing_info / PricingOption. For an auction package there is
            # no stored rate to read (an auction option's rate column is NULL, and
            # _validate_pricing_model_selection stores rate=None with the bid in
            # bid_price), so the report does not merely state a wrong number: it REFUSES,
            # raising AdCPInternalError and dropping the buy from the response. Measured on
            # this scenario: INTERNAL_ERROR on all three transports.
            "T-UC-004-package-auction-rate": (
                "auction by_package[].rate must be the effective rate from actual delivery; "
                "production reads a static rate and refuses when none is stored (#2229)",
                True,
            ),
            # Graduated: T-UC-004-ext-a and T-UC-004-ext-b. Both reasons named a missing
            # suggestion field; no suggestion was ever missing (every CODE_TABLE entry
            # carries one, which is why the same reason was already retired on ext-f).
            # What actually failed was each scenario's own demand: ext-a asked for the
            # code "principal_id_missing" and ext-b for "principal_not_found", neither of
            # which is among the pin's 92 codes. Corrected to AUTH_MISSING (nothing
            # presented) and AUTH_INVALID (presented and rejected) per 3.1.1
            # enums/error-code.json, with ext-b's setup replaced by a credential the
            # resolver really rejects -- its old Given named an absent principal id that
            # changed nothing about the request. Both now XPASS, wire-graded through
            # then_error_code, which has no reconstructed fallback.
            "T-UC-004-ext-c": ("partial-success Error model needs suggestion field — production enhancement", True),
            # Graduated: T-UC-004-ext-d. Its reason named a missing suggestion field; what
            # actually failed was the scenario's own demand for a hard refusal coded
            # "media_buy_not_found" (lowercase, and the pin's members are upper snake).
            # Corrected to the per-id MEDIA_BUY_NOT_FOUND advisory the response schema
            # declares, which keeps the security property -- a non-owned id is answered
            # exactly like a nonexistent one.
            # Graduated: T-UC-004-identify-partial, T-UC-004-identify-batch-ownership.
            # Both grade BR-RULE-030 INV-5 as ADVISORY PER ID: an id that resolves to no
            # buy the caller owns gets no delivery data and a MEDIA_BUY_NOT_FOUND entry in
            # the response's errors[], which get-media-buy-delivery-response.json declares
            # for "missing delivery data".
            # Adapter error: message text + suggestion not wired in partial-success response
            # Graduated (subdl): T-UC-004-ext-f — the reason was "needs suggestion field
            # and message refinement". The suggestion field was never missing: every one
            # of the 100 CODE_TABLE entries carries one, and AdCPAdapterError resolves to
            # SERVICE_UNAVAILABLE / transient / "retry with exponential backoff", matching
            # the pin verbatim ("Seller service is temporarily unavailable. Retry with
            # exponential backoff."). What blocked it was "message refinement" — the
            # scenario demanding authored sentences that CODE_TABLE derivation makes
            # unconstructible. Removing those tautologies un-xfailed it; the scenario now
            # grades the code and the suggestion-presence (which does grade envelope
            # serialization) and nothing derived.
            # Adapter partial failure: _impl silently swallows data construction exceptions
            "T-UC-004-adapter-partial": (
                "adapter partial failure handling needs enriched test data or production fix",
                True,
            ),
            # Graduated (subdl): T-UC-004-response-error — the reason claimed the
            # suggestion field was missing and needed a "production enhancement". It
            # was never missing: ALL 100 CODE_TABLE entries carry a suggestion, so
            # the presence check cannot fail for any emittable error. The scenario
            # was actually blocked by demanding the SUGGESTION read "provide valid
            # authentication", while the no-auth path raises AdCPAuthRequiredError
            # -> AUTH_MISSING, whose table suggestion is "provide credentials via
            # the auth header and retry". Replacing that sentence-match with the
            # AUTH_MISSING code assertion — what the pin actually mandates for a
            # request carrying no Authorization header — un-xfailed it.
        }
        for tag, (reason, strict) in _UC004_XFAIL_ADDITIONAL.items():
            if tag in marker_names:
                item.add_marker(pytest.mark.xfail(reason=reason, strict=strict))
                break

        # Graduated: T-UC-004-dim-sortby-fallback — all transports pass.
        # A2A previously dropped by_placement; that serialization gap is fixed.
        #
        # CORRECTION (salesagent-n78j0.13). TWO SEPARATE FAILURES HERE — the second is
        # the serious one.
        #
        # (a) The assertion is vacuous. Descending-ness is not the obligation; INV-6 is
        #     about WHICH metric is sorted on. Production synthesizes every placement
        #     metric from one weight vector, so the rows are descending by all metrics
        #     at once and `values == sorted(values, reverse=True)` holds for any sort
        #     key — including none. Mutation-proved: deleting the spend-fallback (M1)
        #     and falling back to the wrong metric (M2) each left all 6 tests GREEN.
        #
        # (b) THE CERTIFICATE THAT STOOD HERE WAS WRONG. It read: "Verified: ... so the
        #     pass is real, not a weakened assertion" — and it cited, AS ITS EVIDENCE OF
        #     RIGOUR, the two things that hid the vacuity: the sorted(...) assert (which
        #     cannot fail on this data) and the inline pytest.xfail (called a "guard",
        #     but it is a SILENT ESCAPE — it converts "no data to grade" into xfail
        #     rather than failure). A graduation therefore shipped on a pass that could
        #     not fail, carrying a note telling the next reader it had been checked.
        #     A weak assertion is a gap; a false certificate PROPAGATES, because it is
        #     precisely what stops the next reader from looking. Do not restore any
        #     "verified" claim here without a mutation that goes RED.
        #
        # NOT re-routed: the rows do pass, so a strict xfail would fail the suite, and
        # xfail sets only ever shrink. Correcting the claim in place is the only move
        # that does not trade one false state for another. The fix is to make the
        # scenario discriminating (see the sortby-fallback e2e_rest note below) —
        # tracked in GH #2059.

        # UC-004 status filter: "active" works, other values may not
        # NOTE: the T-UC-004-filter / -empty / -array shadow entries were removed
        # once the generic `{request_params}` step was restricted to key=value
        # form (#1545): the specific status_filter step is no longer shadowed, so
        # values (single, empty-result, array) are all honored and pass.
        _UC004_FILTER_SELECTIVE: list[tuple[str, set[str], str]] = [
            (
                "T-UC-004-filter-default",
                set(),  # all examples
                "default status_filter=active not applied when no explicit IDs",
            ),
        ]
        if any(t.startswith("T-UC-004-filter") for t in marker_names):
            for tag, substrings, reason in _UC004_FILTER_SELECTIVE:
                if tag in marker_names:
                    if not substrings or any(s in nodeid for s in substrings):
                        item.add_marker(pytest.mark.xfail(reason=reason, strict=False))
                    break

        # Graduated: T-UC-004-daterange. When both start_date and end_date are
        # supplied, src/core/tools/media_buy_delivery.py uses them verbatim on
        # all transports (only the single-sided start-only/end-only defaulting
        # paths have a real gap, tracked separately as T-UC-004-daterange-end-only
        # / debt C7 below).

        # Per-row strict=True xfails for partition/boundary scenarios where
        # blanket markers were removed and production gaps are real and named
        # (see docs/test-debt-bdd-strict-markers.md). strict=True forces marker
        # removal the moment the underlying gap closes.
        _UC004_GENUINE_XFAIL_ROWS: list[tuple[str, set[str], str]] = [
            # Graduated (run innet_010926_0144): geo_missing_geo_level, limit_zero and
            # limit_negative. The C4 reason -- "Pydantic raises ValidationError, not
            # AdCPSalesAgentError(INVALID_REQUEST, suggestion)" -- no longer holds:
            # adcp_error_for now maps a pydantic ValidationError to
            # AdCPInvalidRequestError, so those three reach the wire as an
            # INVALID_REQUEST envelope with a suggestion, which is what the rows assert.
            # They xpassed strictly on a2a, i.e. the pass is graded on the real envelope,
            # not on a reconstructed exception.
            # geo_metro_missing_system stays: it did NOT xpass, so its gap is a different
            # one than the code mapping and has not been shown to be closed.
            (
                "T-UC-004-partition-reporting-dims",
                {"geo_metro_missing_system"},
                "Pydantic raises ValidationError, not AdCPSalesAgentError(INVALID_REQUEST, suggestion). See docs/test-debt-bdd-strict-markers.md item C4.",
            ),
            # GRADUATED (removed): T-UC-004-partition-attribution interval_zero /
            # interval_negative / invalid_unit / invalid_model — the attribution_window
            # reference now asserts the wire envelope (error "INVALID_REQUEST" with
            # suggestion), which a2a/mcp/rest emit, closing the old reconstructed-path
            # C4 gap. campaign_interval_not_one is xfailed separately below — its window
            # never reaches production due to generic-step shadowing (#1417),
            # not the #1462 in-process drop.
            (
                "T-UC-004-boundary-reporting-dims",
                {"geo with geo_level=metro but no system"},
                "AdCP spec defines metro/postal_area system requirement only in field description; no validator. See docs/test-debt-bdd-strict-markers.md item C10.",
            ),
            # GRADUATED (removed): T-UC-004-boundary-attribution "unit=campaign with
            # interval=2" — BR-RULE-092 INV-5 is now enforced by the _validate_attribution_window
            # check in _get_media_buy_delivery_impl (returns INVALID_REQUEST on all
            # transports), so the description-only C10 gap is closed.
            # GRADUATED (#1534 merge): the boundary-reporting-dims and
            # boundary-attribution mcp/rest invalid-row entries (the C4
            # transport-boundary error-normalization gap: Pydantic rejected but
            # the wire got a bare ToolError / 422 detail instead of the AdCP
            # envelope) are retired. RequestCompatMiddleware (#1534) normalizes
            # MCP TypeAdapter ValidationErrors to the two-layer VALIDATION_ERROR
            # envelope, and the merged REST boundary emits the same envelope for
            # these schema rejections — the strict=True rows fired as designed
            # (deterministic XPASS on the merged in-network run for
            # mcp-geo-without-geo_level / mcp-limit=0 / mcp-limit-negative and
            # mcp-unit=weeks / rest-interval=0 / rest-model=last_click; the
            # remaining siblings are the same rejection class on the same
            # boundary). a2a graduated earlier (#1417). Rows removed so the
            # scenarios grade live on all transports.
            # C11 retired: the "production ignores buyer
            # start_date" failure was an artefact of the greedy with-params
            # step shadowing when_request_date_range and mis-parsing the
            # request. With correct step routing, production echoes the
            # buyer-supplied start_date/end_date in response.reporting_period,
            # so T-UC-004-daterange now genuinely passes (no strict xfail).
            #
            # date-range partition (#1545): the a2a rows GRADUATED —
            # the Examples now name the wire code (error "VALIDATION_ERROR" with
            # suggestion) and production emits exactly that on the a2a wire ("Start date
            # must be before end date", media_buy_delivery.py:209-218 via
            # AdCPValidationError). Under the transport-aware harness (e2e-harness-wiring)
            # mcp/rest ARE parametrized for this partition and still gap, so they retain a
            # marker below.
            # date-range partition: fully GRADUATED. a2a first (a recorded gap,
            # #1545: "Start date must be before end date",
            # media_buy_delivery.py via AdCPValidationError), then mcp/rest
            # (2026-07-25, below). The mcp/rest partition entry the merge
            # temporarily re-added from main's e2e-harness-wiring lineage was
            # STALE — the pre-merge feature run already had all four mcp/rest
            # invalid rows passing, and on the merged in-network run the
            # re-added rows fired as deterministic strict XPASS — so it is
            # removed again (no partition marker remains).
            # Transport-scoped: impl genuinely PASSES start>=end on the _impl
            # path now.
            # GRADUATED (2026-07-25): mcp/rest now also validate
            # start_date>=end_date (confirmed XPASS on both once the single-transport
            # dedup fix stopped hiding them) — entry removed. The stricter standalone
            # T-UC-004-daterange-invalid/-equal scenarios (exact error_code/message/
            # suggestion pin) are unaffected and still genuinely xfail — this boundary
            # outline only asserts the looser "date handling should be invalid".
            # end-only date_range default (debt C7, Gap G40):
            # when only end_date is provided, the spec says start_date defaults
            # to MediaBuy.created_at but production sets start = today-30d
            # (src/core/tools/media_buy_delivery.py:162-165). The scenario's
            # Then-step asserts the exact creation-date (2025-12-01), so the
            # row genuinely fails today — upgraded from the former vacuous
            # strict=False in _UC004_DATE_SELECTIVE to strict=True here.
            (
                "T-UC-004-daterange-end-only",
                set(),
                "production defaults start_date to today-30d when only end_date is given; "
                "spec says default to MediaBuy.created_at. See docs/test-debt-bdd-strict-markers.md item C7.",
            ),
            # ---- 18h.10 Phase-2: 7 more UC-004 fields reconciled ----
            # Each field's when_partition/boundary_<field> now translates the
            # Gherkin descriptor into the real request kwargs/setup it
            # represents (mirroring the typed when_request_* steps) instead of
            # routing the axis name through _dispatch_partition. With real
            # wiring the "valid" descriptors genuinely PASS (no marker); only
            # the descriptors below genuinely fail for a real, named
            # production gap, so they carry strict=True (forces marker removal
            # the moment the gap closes). See docs/test-debt-bdd-strict-markers.md.
            #
            # daily-breakdown: include_package_daily_breakdown
            # is a real bool field; production lax-coerces non-boolean strings
            # ("yes"/"true" → True) instead of raising INVALID_REQUEST.
            #
            # GRADUATED at the adcp 9.0.0 / AdCP 3.2.1 pin (whole entry removed, not
            # emptied). The lax coercion the reason names is gone: the SDK now types
            # the field Optional[Annotated[bool, Strict(strict=True)]], so
            # GetMediaBuyDeliveryRequest.model_validate(
            #     {"include_package_daily_breakdown": "yes"})
            # REFUSES with [type=bool_type], and the boundary frames that refusal as
            # the INVALID_REQUEST-with-suggestion envelope the row asserts.
            # Not vacuous: this row's Then resolves to error "INVALID_REQUEST" with
            # suggestion, which goes through TransportResult.assert_wire_error — the
            # sanctioned wire assertion, which refuses a missing envelope outright and
            # checks code, pin-sourced recovery and the suggestion in BOTH mirrored
            # layers. There is no reconstructed-exception path for it to pass on.
            # The strict=False twin in _UC004_PARTITION_SELECTIVE is deleted in the
            # same change; left alone it would turn this graduation into a silent XPASS.
            # VERIFIED 2026-08-24 (xpass-graduation walk of e2e_rest ledger line
            # :55). This entry is CORRECT and stays. Three independent checks:
            #  1. The mechanism in the reason above is real, not inferred. Direct
            #     probe of the request model:
            #       GetMediaBuyDeliveryRequest.model_validate(
            #           {"include_package_daily_breakdown": "true"})
            #     -> ACCEPTED, field == True. Same for "TRUE"/"yes"/"1". The value
            #     is a DECLARED bool|None field, so it never reaches extra="forbid";
            #     Pydantic v2 lax mode coerces it. Production raises nothing at all.
            #  2. The obligation is spec-grounded, not over-specified. The pinned
            #     adcp 3.1 schema (media-buy/get-media-buy-delivery-request.json)
            #     declares include_package_daily_breakdown as {"type": "boolean"};
            #     JSON Schema type:boolean does not admit the string "true". The
            #     scenario is right and production is lax — a real gap.
            #  3. The row is NOT an xpass and never was. Full slice, all three
            #     in-process wire transports:
            #       saci test bdd tests/bdd/test_uc004_deliver_media_buy_metrics.py \
            #         -k daily_breakdown -- -rxX
            #     -> 18 passed, 12 xfailed, 0 XPASSED. a2a/mcp/rest all XFAIL on
            #     this reason. (Local slices persist no test-results/ report, so
            #     there is no run id to cite; the command above reproduces it.)
            # e2e_rest ledger line :55 therefore STAYS. No bdd-in-network run was
            # performed, and none is required: this change removes no routing, and
            # e2e_rest exercises the same app/request model as the in-process rest
            # transport, which XFAILs here for the reason above.
            # CAVEAT on the reason text: the "item C4" pointer is WRONG for this
            # row. C4 is "Pydantic ValidationError not translated to AdCPError";
            # here no ValidationError is ever raised (the value is coerced and
            # accepted), so C4's remedy — a boundary translator wrapping
            # ValidationError — would not move these rows. C4's "one change clears
            # ~32 rows" estimate over-counts by however many of them are coercion,
            # not translation. Fixing this needs strict-bool validation, not C4.
            #
            # GRADUATED at the adcp 9.0.0 / AdCP 3.2.1 pin (whole entry removed, not
            # emptied — an empty substring set xfails EVERY row carrying the tag).
            # The strict-bool validation the reason said production lacks is now what
            # the SDK ships, so the remedy the CAVEAT above names is in place:
            #   GetMediaBuyDeliveryRequest.model_fields[
            #       "include_package_daily_breakdown"].annotation
            #   -> Optional[Annotated[bool, Strict(strict=True)]]
            # and model_validate({"include_package_daily_breakdown": "true"}) now
            # REFUSES with [type=bool_type] (same for "TRUE"/"yes"/"1"), while a real
            # bool and an absent field still parse. The boundary turns that refusal
            # into an INVALID_REQUEST envelope (_boundary.py validated_request ->
            # HTTP 400), so the row grades production, not a reconstruction.
            # Not vacuous, and that was measured rather than argued — but it took TWO
            # findings, and the first was not enough:
            #  1. The row reached a real wire envelope. Its outcome cell was a bare
            #     `invalid`, which resolves to _assert_wire_rejection (uc004_delivery.py);
            #     that helper accepts ONE outcome besides an envelope, a request that died
            #     building in the step. Poisoning the no-envelope branch with a raise left
            #     all three transports passing, so none takes it; moving the same raise to
            #     the top of the helper reddened all three, proving the probe live.
            #  2. But reaching an envelope is not grading the obligation. The bare
            #     `invalid` branch only asserts the code is NOT one of nine server-fault /
            #     auth codes and that recovery is correctable|terminal. It never asserts
            #     WHICH code, so production answering BUDGET_TOO_LOW would have passed.
            #     The cell is therefore STRENGTHENED to `error "INVALID_REQUEST" with
            #     suggestion` (BR-UC-004 Examples), which routes to _assert_error_outcome
            #     -> assert_wire_error(code, require_suggestion=True). Grounded in the pin:
            #     get-media-buy-delivery-request.json types the field {"type":"boolean"}
            #     and enums/error-code.json gives INVALID_REQUEST recovery "correctable"
            #     with a suggestion. Measured after strengthening: the three rows pass, and
            #     substituting BUDGET_TOO_LOW into the cell reddens all three (the failure
            #     names the real INVALID_REQUEST) — so the row now grades which code.
            # Per-row evidence for the sibling partition entry below.
            # account: only the omitted/(field absent) rows
            # pass on every transport. The other rows fail transport-asym-
            # metrically — a2a/mcp/rest never parse/resolve AccountReference
            # at the boundary (resolve_account does account_ref.root on a raw
            # dict → RuntimeError); the invalid-account rows raise Pydantic
            # ValidationError instead of AdCPSalesAgentError(INVALID_REQUEST/
            # ACCOUNT_NOT_FOUND). Substrings are transport-prefixed so only
            # the genuinely-failing rows are marked (impl valid rows pass).
            # GRADUATED (whole entry removed, not emptied): the invalid-oneOf / empty
            # account reference now resolves into a typed AdCPSalesAgentError and reaches
            # the wire as INVALID_REQUEST. NOTE FOR NEXT TIME -- the rows cannot simply be
            # deleted one by one: this matcher reads
            #     if not substrings or any(s in nodeid for s in substrings)
            # so an entry whose set becomes EMPTY xfails EVERY row carrying its tag, and
            # rows that were passing turn into strict xpasses. Thinning this set to zero
            # produced 12 failures, including a row graduated earlier. Remove the entry.
            (
                "T-UC-004-boundary-account",
                {
                    "impl-account_id present + not found",
                    # Valid rows (account exists / single match = "brand + operator
                    # present", incl. the sandbox:true variant) now resolve on a2a/mcp/rest
                    # once their accounts are seeded — removed. a2a invalid rows (both / not found / empty) already
                    # raise AdCPSalesAgentError (wire-drop XPASS, #1417) — removed.
                    # GRADUATED (#1534 merge): mcp-both / mcp-empty-object —
                    # RequestCompatMiddleware normalizes the MCP TypeAdapter oneOf
                    # rejection to the VALIDATION_ERROR envelope; both rows fired
                    # as deterministic strict XPASS on the merged in-network run
                    # — removed.
                    # mcp-account_id present + not found genuinely passes
                    # (ValidationError satisfies 'invalid') — NOT marked.
                },
                "impl does not resolve the account_id-not-found reference into an "
                "AdCPSalesAgentError at the _impl boundary for this row. "
                "See docs/test-debt-bdd-strict-markers.md items C1/C2/C4.",
            ),
            # The two sampling entries are DELETED with their scenarios (2026-09-15).
            # Their own first line said it: sampling_method is not a
            # GetMediaBuyDeliveryRequest field. It is not an AdCP 3.1.1 field either --
            # zero hits across the pinned schemas -- so the outlines demanded this seller
            # accept an invented field and refuse an invented enum value, and the rows
            # that "passed" were collecting the undeclared-field rejection instead. The
            # feature file carries the full reasoning where the scenarios stood; the real
            # pinned concept (failures_only, a boolean on get_media_buy_artifacts) is
            # graded in BR-UC-024.
            # resolution (#1545): GRADUATED on all transports. The
            # Examples now name error "VALIDATION_ERROR" with suggestion, and the empty
            # media_buy_ids=[] hits the SDK min_length=1 constraint, surfacing as
            # AdCPValidationError(VALIDATION_ERROR)+suggestion on the a2a/mcp/rest wire
            # (empirically verified: a2a/mcp/rest all PASS the named code). The earlier
            # INVALID_REQUEST framing (and the "A2A wraps in RuntimeError" note) were both
            # stale — production emits VALIDATION_ERROR here, not INVALID_REQUEST — so no
            # partition marker remains. (e2e-harness-wiring corroborates: strict XPASS
            # observed on the merged tree 2026-07-09, the merged A2A boundary raises
            # AdCPSalesAgentError on the empty-array reject — adcp_validation_boundary from the
            # #1417 embed — matching the boundary-resolution graduation below. Entry removed.)
            # T-UC-004-boundary-resolution: a2a now raises AdCPSalesAgentError on the empty-array
            # reject (wire-drop confirmed XPASS, #1417); the only remaining
            # transport-aware failure (a2a empty array) is handled below — entry removed
            # here so it does not blanket-xfail every boundary-resolution row.
            # Graduated: T-UC-004-partition-ownership row owner_mismatch. The C3 gap it
            # named -- cross-principal access answering 200 + empty instead of a refusal --
            # is closed: production reports a buy owned by another principal as
            # MEDIA_BUY_NOT_FOUND, which is both a pinned code and the answer 3.1.1's L1
            # security prose requires ("the body MUST NOT distinguish 'unauthorized' from
            # 'not found'"). Observed as a deterministic strict XPASS. The sibling
            # T-UC-004-boundary-ownership routing below graduates with it.
            # boundary-ownership: fully GRADUATED. a2a first (wire-drop XPASS,
            # #1417), then mcp/rest at the #1534 merge — production reports the
            # cross-principal buy as MEDIA_BUY_NOT_FOUND (spec 3.1.1
            # enums/error-code.json; the tenant-scoped repository excludes
            # foreign buys, media_buy_delivery.py not_found_errors) on every
            # wire transport, not the old 200+empty. The mcp row fired as a
            # deterministic strict XPASS on the merged in-network run; entry
            # removed so the boundary grades live. (The stricter
            # PERMISSION_DENIED partition/boundary Examples remain genuinely
            # xfailed via _UC004_PARTITION_SELECTIVE — that expectation gap is
            # separate and still open.)
            # status-filter: all valid single statuses +
            # arrays + (field absent) pass. pending_activation rows fail
            # (Gherkin uses a non-spec MediaBuyStatus — item B1); empty-array /
            # unknown-value "failed" rows raise ValidationError not
            # AdCPSalesAgentError(INVALID_REQUEST) — item C4.
            # partition: impl now genuinely PASSES single_pending (production
            # normalizes the legacy 'pending_activation' label). a2a/mcp/rest
            # still fail on the unknown-value/empty-array C4 normalization.
            # GRADUATED (whole entry removed, not emptied). Both halves of its bundled
            # reason are closed: single_pending's legacy-label normalization was retired
            # earlier, and the empty_array / unknown_value rows now reach the wire as a
            # typed AdCPSalesAgentError on a2a, mcp AND rest. The entry is DELETED rather
            # than left with an empty set, because this matcher reads
            #     if not substrings or any(s in nodeid for s in substrings)
            # so an empty set xfails EVERY row carrying the tag.
            # boundary: pending_activation fails everywhere; the 'failed' /
            # '[] (empty array...)' rows pass on impl/rest (ValidationError
            # satisfies 'invalid') but fail on a2a/mcp — transport-prefixed
            # substrings so only the genuinely-failing rows are marked.
            (
                "T-UC-004-boundary-status-filter",
                {
                    "impl-pending_activation (first enum value)",
                    "a2a-pending_activation (first enum value)",
                    # a2a now raises AdCPSalesAgentError on failed/[] (wire-drop confirmed XPASS,
                    # #1417) — removed.
                    # GRADUATED (#1534 merge): mcp-failed — RequestCompatMiddleware
                    # normalizes the MCP TypeAdapter enum rejection to the
                    # VALIDATION_ERROR envelope; the row fired as a deterministic
                    # strict XPASS on the merged in-network run — removed.
                    "mcp-pending_activation (first enum value)",
                    "[rest-pending_activation (first enum value)",
                },
                "pending_activation: Gherkin value not a valid AdCP MediaBuyStatus "
                "(item B1). See docs/test-debt-bdd-strict-markers.md.",
            ),
            # credentials: FULLY reconciled — the When step
            # now validates the real AdCP reporting_webhook Authentication
            # model (scheme enum + credentials min_length=32). All 40 rows
            # genuinely PASS on all transports; NO strict=True entry needed
            # (same shape as the reconciled date-range valid rows).
        ]
        # e2e_rest items must NOT be marked by this loop: it would stamp a strict=True
        # in-process reason onto e2e_rest items, contradicting the ledger's non-strict
        # policy and, once e2e_rest reaches the real boundary and passes (e.g.
        # INVALID_REQUEST now emitted), turning that pass into a spurious strict-XPASS
        # failure. e2e_rest xfails are owned by the dedicated tripwire blocks and the
        # ledger collapse. (PR #1420)
        #
        # The gate is needed because the entries match by TAG plus a row substring, and
        # an e2e_rest item carries the same scenario tags as its in-process siblings —
        # the selector shape is irrelevant to that. An earlier version of this comment
        # justified the gate by claiming the row substrings are bare prefixes that let
        # a `"rest-…"` selector match an `[e2e_rest-…]` nodeid; that was wrong twice
        # over (measured 2026-07-30): there are ZERO bare `"rest-` selectors in this
        # file — the 67 bare ones are impl (23), a2a (22) and mcp (22), none of which
        # can appear inside `e2e_rest` — and all 100 bracketed selectors are
        # `"[<transport>-` guarded. Do not build a guard on the old mechanism.
        if not is_e2e_rest:
            for tag, substrings, reason in _UC004_GENUINE_XFAIL_ROWS:
                if tag in marker_names and (not substrings or any(s in nodeid for s in substrings)):
                    item.add_marker(pytest.mark.xfail(reason=reason, strict=True))
                    break

        # UC-004 boundary scenarios: strict=False because some examples pass.
        # Invalid boundary values SHOULD fail validation but production doesn't validate.
        # Valid boundary values pass through fine.
        # Graduated to transport-aware selective xfail:
        # T-UC-004-boundary-attribution, T-UC-004-boundary-daily-breakdown,
        # T-UC-004-boundary-account, T-UC-004-boundary-status-filter,
        # T-UC-004-boundary-resolution, T-UC-004-boundary-ownership,
        # T-UC-004-boundary-reporting-dims, T-UC-004-boundary-sampling,
        # T-UC-004-boundary-date-range
        _UC004_BOUNDARY_TAGS: set[str] = set()
        # Graduated: T-UC-004-boundary-credentials (transport-aware selective below)
        # Graduated: T-UC-004-boundary-reporting-dims (transport-aware selective below)
        # Graduated: T-UC-004-boundary-sampling (transport-aware selective below)
        # Graduated: T-UC-004-boundary-date-range (transport-aware selective below)
        # Graduated: T-UC-004-boundary-ownership (transport-aware below)
        if marker_names & _UC004_BOUNDARY_TAGS:
            item.add_marker(pytest.mark.xfail(reason="boundary validation partially implemented", strict=False))

        # Graduated: T-UC-004-boundary-credentials — the When now validates the real
        # AdCP reporting_webhook Authentication at the create_media_buy boundary
        # (scheme enum + credentials min_length=32), so all rows pass on all transports.

        # T-UC-004-boundary-ownership. The mcp XPASS this replaces was VACUOUS, so it
        # was NOT graduated -- the step was fixed instead (debt item B3, previously
        # RECONCILED for the partition twin only). when_boundary_ownership sent the
        # Gherkin label as a literal `ownership=` kwarg; that is not a request field, so
        # FastMCP's TypeAdapter rejected it as unrecognized before
        # _get_media_buy_delivery_impl ran, matching `invalid` regardless of what
        # production does about cross-principal access. The old per-transport table
        # below was therefore a table of "which transport rejects an unknown argument",
        # not of ownership enforcement. The When now routes through
        # _dispatch_ownership_partition, the same real identity swap the partition
        # outline uses, so the split is the obligation's: querying as the owner returns
        # the buy on every transport, and querying a non-owned id hits the C3 gap --
        # production answers 200 + empty instead of MEDIA_BUY_NOT_FOUND -- on every
        # transport, exactly like T-UC-004-partition-ownership/owner_mismatch above.
        # Graduated: T-UC-004-boundary-ownership "principal differs from owner". The block
        # above correctly diagnosed that the old per-transport table was measuring which
        # transport rejects an unknown argument -- and the helper it routed both outlines
        # through kept injecting that argument, so the trap moved rather than closed. The
        # When now seeds a REAL second principal and presents its token, so every transport
        # runs the same request; and the row asserts the per-id MEDIA_BUY_NOT_FOUND advisory
        # that get-media-buy-delivery-response.json declares, rather than a hard refusal.

        # Graduated: T-UC-004-boundary-reporting-dims — "metro but no system" is the
        # only row still genuinely gapped (prose-only spec constraint, no formal
        # validator; separately tracked as C10 in _UC004_GENUINE_XFAIL_ROWS above).
        # "geo without geo_level", "limit=0", "limit negative" also now genuinely
        # reject on mcp/rest (a2a already passed, #1417) — required geo_level /
        # limit>=1 per the pinned v3.1.1 get-media-buy-delivery-request.json, and
        # RequestCompatMiddleware normalizes the ToolError to a two-layer envelope
        # on mcp/rest.
        if "T-UC-004-boundary-reporting-dims" in marker_names:
            _rdim_all_transport_fail = "geo_level=metro but no system" in nodeid
            if _rdim_all_transport_fail:
                item.add_marker(
                    pytest.mark.xfail(
                        reason="reporting_dimensions boundary: validation gaps on some transports", strict=False
                    )
                )
            # Graduated: e2e_rest invalid reporting_dimensions schema violations now
            # return 400 INVALID_REQUEST (the RequestValidationError handler in
            # src/app.py; not a raw 500/empty body), so the wire-envelope assertion
            # handles them.

        # The T-UC-004-boundary-sampling branch is DELETED with its scenario
        # (2026-09-15). This block already carried the answer in its own comment --
        # "sampling_method is not a real get_media_buy_delivery request field (does not
        # exist in the pinned v3.1.1 schema at all) ... this scenario cannot distinguish
        # 'enum rejected' from 'field doesn't exist' ... the documented fix is to
        # relocate/delete this scenario family, not graduate rows" (debt item B4). The
        # family is deleted; the feature file records why where the scenarios stood.

        # Graduated: T-UC-004-boundary-date-range. a2a/mcp/rest all accept a valid
        # start_date<end_date pair and omitted dates without error — the shared
        # _get_media_buy_delivery_impl (src/core/tools/media_buy_delivery.py) has
        # no transport-specific date-range branch. Production also validates date
        # range over e2e_rest, rejecting the invalid cases (equals, after).

        # T-UC-004-daterange-end-only over e2e_rest: same Gap G40 (debt C7) as
        # in-process — when only end_date is given, production defaults start to
        # today-30d, not the media buy creation date the Then-step asserts. The
        # _UC004_GENUINE_XFAIL_ROWS loop is gated to in-process only (see :1422),
        # so e2e_rest needs its own strict tripwire. Deterministic: the live
        # server reliably returns today-30d. Retire when Gap G40 is closed.
        if is_e2e_rest and "T-UC-004-daterange-end-only" in marker_names:
            item.add_marker(
                pytest.mark.xfail(
                    reason="e2e_rest: Gap G40 — start defaults to today-30d, not media buy creation date",
                    strict=True,
                )
            )

        # attribution_window REFERENCE (clean scenario->step->harness path): the Examples
        # name the exact error code (error "VALIDATION_ERROR" — the schema-canonical code
        # for value/enum/range/business-rule violations; reconciled from the earlier
        # INVALID_REQUEST mis-pin per the AdCP graded error-compliance storyboard), the
        # step asserts it on the harness wire envelope. interval=0 / unit=weeks /
        # model=last_click PASS on a2a/mcp/rest (VALIDATION_ERROR).
        # GRADUATED (#1545): the partition "campaign with interval=2"
        # (campaign_interval_not_one) now passes on a2a — the only transport parametrized
        # for that row — because the attribution_window.post_click reaches production and
        # INV-5 fires (VALIDATION_ERROR "interval must be 1 when unit is 'campaign'"), which
        # the Examples now name and the step asserts on the wire. So the former strict=True
        # _aw_partition_campaign leg is dropped; the row passes unmasked. (The old #1462
        # "request path drops post_click" framing was wrong for the wire transports; #1462 is
        # the in-process _impl path, which BDD does not parametrize.)
        # Graduated: T-UC-004-partition-attribution error "INVALID_REQUEST" rows on
        # e2e_rest. The step-binding bug this routed around is FIXED, and the fix is
        # visible at the source: the generic step is now
        # ``parsers.re(r"the Buyer Agent requests delivery metrics with (?P<request_params>\w+=.+)")``
        # (uc004_delivery.py:733), and requiring ``\w+=`` means the JSON-form window
        # ``with attribution_window {"post_click": ...}`` no longer matches it. It binds to
        # the specific step instead, so the window reaches the live server, validation
        # fires, and the rejection assertion the row makes can actually be met -- the pass
        # is explained by real behavior, not by the assertion going vacuous.
        # Verified: bdd_inprocess OK on all in-process transports; these four rows XPASS
        # (strict) on e2e_rest in the in-network full run, which is the only job that
        # grades them. They are NOT listed in tests/bdd/e2e_rest_known_failures.txt, so
        # there is no sibling ledger entry to retire alongside this.
        # (#1545/x18x had already dropped the campaign leg here for the same reason: INV-5
        # fires VALIDATION_ERROR with suggestion on a2a.)

        # Graduated: T-UC-004-boundary-account — transport-aware.
        # "account_id present"/"brand + operator" (valid): fail on mcp/rest only.
        # "both account_id"/"empty object" (invalid): fail on a2a only.
        # "account_id not found" (invalid): fail on impl/a2a only.
        # "omitted": already PASS everywhere.
        if "T-UC-004-boundary-account" in marker_names:
            # a2a now raises AdCPSalesAgentError on invalid-account rows (both / empty / not found)
            # (wire-drop confirmed XPASS, #1417). Valid rows (account exists / single
            # match / sandbox account exists) now pass on mcp/rest once their accounts
            # are seeded — the former "production gaps" mask hid the
            # missing seed. impl still gaps on not-found (impl is not in the default
            # BDD parametrization).
            # mcp's "both account_id"/"empty object" invalid rows also now reject
            # correctly — FastMCP's TypeAdapter validates the account param against
            # the adcp library's AccountReference oneOf (RootModel,
            # additionalProperties:false per branch) BEFORE the tool body runs,
            # normalized to VALIDATION_ERROR via the shared adcp_error_for().
            _acc_notfound_fail = is_impl and "not found" in nodeid
            if _acc_notfound_fail:
                item.add_marker(
                    pytest.mark.xfail(
                        reason="delivery account boundary: production gaps on this transport", strict=False
                    )
                )
            # e2e_rest fully graduated: invalid rows ("not found", "both
            # account_id", "empty object") passed first; the valid rows
            # ("account exists", "single match") followed at the #1417 merge —
            # the jr5b seeded-account Given is realized against the server DB,
            # so the account fixture IS visible now (XPASS innet_140726_1516).

        # --- UC-004 boundary: selective xfail for graduated strong groups ---
        # Only the failing subset gets xfailed; clean-pass examples graduate to PASS.
        _UC004_BOUNDARY_SELECTIVE: list[tuple[str, set[str], str]] = [
            # include_package_daily_breakdown: only non_boolean fails (all transports)
            #
            # SHADOW — DO NOT REMOVE THE strict=True ENTRY WITHOUT REMOVING THIS ONE.
            # This entry duplicates the routing of the SAME tag + the SAME row that
            # the strict=True Phase-2 entry above (search: "lax-coerces non-boolean")
            # already covers, but with strict=False. Measured 2026-08-24:
            #   * With both present, the strict=True entry governs — the reported
            #     reason is the Phase-2 one, so the ratchet works TODAY.
            #   * Mutation M1 (this entry left in place, the strict=True entry
            #     temporarily deleted) -> the row still XFAILs, now reporting THIS
            #     reason. So this entry is live and reachable, not dead code.
            # Consequence: the moment production grows strict-bool validation, the
            # strict=True entry fires (XPASS -> failure) and forces its own removal —
            # which is the intended ratchet. But removing it hands the row straight
            # to this strict=False entry, under which the now-passing row reports a
            # silent XPASS forever instead of graduating. That is a mechanism for
            # MANUFACTURING xpass residue out of a completed fix, and the route pin
            # (EXPECTED_XFAIL_ROUTES in tests/unit/test_architecture_e2e_rest_escape_
            # hatches.py) cannot catch it: it records conditions only, never `strict`,
            # so a strict=False shadow behind a strict=True route is invisible to it.
            # 62 tags in this file are routed more than once; only this one has been
            # checked.
            #
            # GRADUATED at the adcp 9.0.0 / AdCP 3.2.1 pin, together with the
            # strict=True entry whose removal this comment made a precondition. The
            # predicted sequence is exactly what happened: the SDK grew strict-bool
            # validation, the strict=True entry fired as an XPASS failure, and
            # removing it alone handed the row to this shadow, which reported a
            # silent XPASS (measured: 3 failed -> 3 xpassed on a2a/mcp/rest). Both
            # entries are therefore deleted in one change, and the row now grades
            # live. Evidence for the behaviour is on the strict=True entry above.
            # Graduated: "buyer_refs only" and "zero resolution" (all 4 transports pass)
            # Graduated: "empty array" passes on impl/mcp/rest (only a2a fails)
            # Graduated: "partial resolution" -- the transport-agnostic _impl
            # (src/core/tools/media_buy_delivery.py) diffs requested media_buy_ids
            # vs. resolved buys and appends an advisory MEDIA_BUY_NOT_FOUND to
            # response.errors[] instead of hard-failing, which is exactly the shape
            # get-media-buy-delivery-response.json#/properties/errors documents
            # (v3.1.1), on all 3 transports.
            # Clean-pass: media_buy_ids only, both provided, neither provided
            # Graduated: status_filter "not in AdCP enum" passes on impl+rest,
            # "empty array, violates" passes on impl+mcp+rest (transport-aware below)
        ]
        for tag, substrings, reason in _UC004_BOUNDARY_SELECTIVE:
            if tag in marker_names:
                if any(s in nodeid for s in substrings):
                    item.add_marker(pytest.mark.xfail(reason=reason, strict=False))
                break

        # T-UC-004-boundary-resolution "empty array": a2a now raises AdCPSalesAgentError
        # (wire-drop confirmed XPASS, #1417) — no transport still fails here.
        # T-UC-004-boundary-status-filter: graduated per-transport
        # "not in AdCP enum" (failed): all transports now pass.
        # "empty array, violates" ([]): a2a now passes — no transport still fails
        if "T-UC-004-boundary-status-filter" in marker_names:
            # mcp's "not in AdCP enum" (status_filter="failed") row also now
            # rejects correctly — FastMCP's TypeAdapter validates status_filter
            # against the adcp library's MediaBuyStatus enum before the tool body
            # runs, same mechanism/adcp_error_for() path as the account
            # boundary graduation above.
            # Graduated: e2e_rest invalid status_filter (unknown enum value) now
            # returns 400 INVALID_REQUEST (the RequestValidationError handler in
            # src/app.py; not a raw 500/empty body), so the wire-envelope assertion
            # handles it.
            # adcp 3.12: pending_activation renamed to pending_start — feature file
            # still uses old name, schema rejects it as unknown enum value.
            if "pending_activation" in nodeid or "all 6 statuses" in nodeid:
                item.add_marker(
                    pytest.mark.xfail(
                        reason="adcp 3.12: pending_activation renamed to pending_start — feature file needs update",
                        strict=True,
                    )
                )

        # Graduated: "both provided (priority rule)". #1417 already retired
        # buyer_refs and rewrote _dispatch_resolution
        # (tests/bdd/steps/domain/uc004_delivery.py) to send media_buy_ids +
        # status_filter instead, so the row tests a real, spec-permitted
        # combination, not obsolete content.

        # Graduated: e2e_rest media_buy_resolution "empty array" now returns a
        # structured AdCP error envelope (not a raw 500/empty body), so the
        # wire-envelope assertion handles it.

        # Graduated: e2e_rest T-UC-004-boundary-ownership "differs from owner". The reason
        # read "ownership check not enforced through REST — test succeeds unexpectedly",
        # which is what a strict xfail says when the scenario demanded a HARD refusal and
        # production answered 200 + empty. The obligation was the wrong one: 3.1.1's
        # get-media-buy-delivery-response.json declares errors[] for missing delivery data,
        # and the row now asserts the per-id MEDIA_BUY_NOT_FOUND advisory that
        # @T-UC-004-identify-batch-ownership already graded for the batch case. Production
        # emits it on the REST wire too, so this xpassed strictly on the in-network run
        # innet_150926_0049 — the last of that run's 11 e2e failures that was a routing
        # artifact rather than a defect.

        # Graduated: the e2e_rest route for T-UC-004-dim-sortby-fallback, a strict=False
        # xfail kept only because no in-network run had carried by_placement through the
        # delivery_simulation_configs row, nor written that row three times in one
        # scenario as the accumulating placement Given does. Box run innet_291026_0257 is
        # that measurement: XPASS on e2e_rest, passed on a2a, mcp and rest. The scenario
        # names three placements whose spend, impressions and clicks rank differently and
        # pins the resulting order ("pl-B", "pl-A", "pl-C"), so only a fallback to spend
        # satisfies it; the order Then fails loudly when no package carries by_placement.
        # Obligation: media-buy/get-media-buy-delivery-request.json @ 3.2.7,
        # reporting_dimensions.placement.sort_by -- "Falls back to 'spend' when the seller
        # does not report the requested metric at this breakdown's row grain". GH #2059.

        # UC-004 partition scenarios: adcp 3.10 changed schema validation behavior.
        # Partition tests exercise valid/invalid value ranges per field.
        # strict=False: some partition values pass, others fail depending on schema version.
        _UC004_PARTITION_TAGS: set[str] = set()
        # Graduated (all 4 transports pass with strong assertions):
        # T-UC-004-partition-reporting-dims, T-UC-004-partition-attribution,
        # T-UC-004-partition-daily-breakdown, T-UC-004-partition-account,
        # T-UC-004-partition-sampling, T-UC-004-partition-status-filter,
        # T-UC-004-partition-date-range, T-UC-004-partition-resolution,
        # T-UC-004-partition-ownership
        # Graduated: T-UC-004-partition-credentials (transport-aware selective below)
        if marker_names & _UC004_PARTITION_TAGS:
            item.add_marker(
                pytest.mark.xfail(reason="partition validation behavior varies with adcp schema version", strict=False)
            )

        # --- UC-004 partition: selective xfail for error-expecting examples ---
        # FIXME: Graduated partition tags still have invalid-value
        # examples that expect INVALID_REQUEST/ACCOUNT_NOT_FOUND but production
        # doesn't validate. Only xfail the failing subset; valid-value examples pass.
        _UC004_PARTITION_SELECTIVE: list[tuple[str, set[str], str]] = [
            # Graduated: geo_missing_geo_level, limit_zero, limit_negative. The reason here
            # -- "production accepts invalid configs" -- is no longer true of them: they
            # XPASS on a2a, mcp AND rest, so all three now reject the config and answer
            # INVALID_REQUEST with a suggestion, which is what the rows assert. This entry
            # is the non-strict twin of the one in _UC004_GENUINE_XFAIL_ROWS; both listed
            # the same four rows, so leaving this one in place turned the strict
            # graduation into a silent XPASS instead of a pass.
            # geo_metro_missing_system stays: it does not XPASS, so production still
            # accepts it and the reason still holds for that row alone.
            (
                "T-UC-004-partition-reporting-dims",
                {"geo_metro_missing_system"},
                "reporting_dimensions validation not implemented — production accepts invalid configs",
            ),
            # Graduated: T-UC-004-partition-attribution
            # interval_zero/interval_negative/invalid_unit/invalid_model. The
            # generic "with {request_params}" step no longer shadows the specific
            # "with attribution_window {value}" step (the generic step now
            # requires \w+=... key=value form, mutually exclusive with the
            # space-form "attribution_window {json}" step). attribution_window is
            # a real-wire-asserted field (_WIRE_ASSERTED_FIELDS), and all 4 rows
            # pass with the correct VALIDATION_ERROR+suggestion on all 3
            # transports.
            # daily breakdown: GRADUATED with its strict=True twin in
            # _UC004_GENUINE_XFAIL_ROWS (see the evidence there). The reason —
            # "production accepts non-boolean" — is disproved by the pinned SDK's
            # strict-bool field; leaving this entry would have absorbed the
            # graduation as a silent XPASS, exactly as the boundary twin's SHADOW
            # note predicted. Entry deleted, not emptied.
            # Graduated: T-UC-004-partition-account. ENTRY DELETED, not thinned — its set
            # named invalid_oneOf_both and empty_object, and with impl sunsetted (#1417)
            # a2a/mcp/rest are the only in-process transports, so all SIX matching rows
            # graduate at once and nothing is left for the entry to mark. (Thinning to an
            # empty set would xfail every row carrying the tag; see the account note in
            # _UC004_GENUINE_XFAIL_ROWS.) The reason -- "raises ValidationError, not
            # AdCPSalesAgentError(INVALID_REQUEST)" -- is disproved by production: the
            # pydantic oneOf/extra_forbidden rejection is translated into a typed
            # AdCPInvalidRequestError, which the boundary frames as INVALID_REQUEST.
            # (This used to cite adcp_validation_boundary inside the builder; that wrapper
            # is gone and the TRANSPORT boundary makes the identical conversion, off the
            # same exception, through the same adcp_error_for. The outcome is unchanged --
            # which is the point -- but the citation would send a reader to a frame that no
            # longer exists.) -- the first code in the pinned
            # v3.1.1 enums/error-code.json, correctable, with a suggestion. The scenario
            # names that exact outcome (error "INVALID_REQUEST" with suggestion) and grades
            # it through TransportResult.assert_wire_error -> assert_envelope_shape on the
            # captured envelope, both mirrored layers, so the pass is not vacuous. The Given
            # and the When run the shared cross-transport path (_dispatch_partition ->
            # dispatch_request) with no per-transport branch. Six deterministic XPASS rows
            # on the serial box slice, 2026-09-02 (534 passed / 484 xfailed / 16 xpassed).
            # account_not_found was never in this set: with the valid siblings seeded,
            # resolution runs and the unseeded id correctly raises ACCOUNT_NOT_FOUND.
            # Graduated: T-UC-004-partition-sampling (transport-aware block below)
            # "not_provided" passes all transports; valid named methods pass on REST only.
            # Graduated: T-UC-004-partition-status-filter. ENTRY DELETED, not thinned — the
            # set named unknown_value and empty_array, and a2a/mcp/rest are the only
            # in-process transports since impl was sunsetted (#1417), so all six matching
            # rows graduate together and nothing is left to mark.
            #
            # unknown_value ("failed") graduated on the evidence as it stood: the reason
            # "production accepts invalid values" is false — the value is not a pinned
            # MediaBuyStatus, the transport boundary (adcp_error_for) converts the
            # pydantic rejection to a typed AdCPInvalidRequestError -- it used to be
            # converted a frame earlier, by adcp_validation_boundary inside the builder;
            # same call, same result -- and the wire carries INVALID_REQUEST +
            # suggestion, exactly what the Example names and what assert_wire_error grades
            # on the captured envelope.
            #
            # empty_array did NOT graduate on its xpass — that xpass was VACUOUS and the
            # step was fixed first. when_request_with_status_filter wrapped the Example
            # cell, sending status_filter=["[]"], so the row rejected as an unknown enum
            # value: a duplicate of its unknown_value sibling, never the minItems
            # obligation its name claims. With the cell now parsed as the array it spells,
            # the row sends status_filter=[] and rejects on the pinned v3.1.1 StatusFilter
            # min-items constraint ("List should have at least 1 item after validation,
            # not 0") — same INVALID_REQUEST on the wire, but now for the reason the row
            # is named for.
            # date range partition GRADUATED (#1545): only [a2a-…] is
            # parametrized for start_equals_end/start_after_end, and a2a now emits
            # VALIDATION_ERROR+suggestion ("Start date must be before end date",
            # media_buy_delivery.py:209-218) for the named Examples — passes unmasked. Entry
            # removed. (mcp/rest are only parametrized on the BOUNDARY counterpart, which
            # stays masked in _UC004_GENUINE_XFAIL_ROWS above.)
            # resolution partition GRADUATED (#1545): empty media_buy_ids=[]
            # hits the SDK min_length=1 constraint -> VALIDATION_ERROR+suggestion on the
            # a2a/mcp/rest wire (all three empirically PASS the named Example). Entry removed.
            # Graduated: T-UC-004-partition-ownership row owner_mismatch, the second of two
            # routings this row carried. "Production accepts non-owned media buys" was
            # true of the OLD scenario, which demanded a hard refusal; the obligation for
            # this rule on this tool is a per-id advisory, which production emits and
            # @T-UC-004-identify-batch-ownership already graded for the batch case. The row
            # now names that outcome instead of the word "invalid".
        ]
        for tag, substrings, reason in _UC004_PARTITION_SELECTIVE:
            if tag in marker_names:
                if not substrings or any(s in nodeid for s in substrings):
                    item.add_marker(pytest.mark.xfail(reason=reason, strict=False))
                break

        # (#1545 review) The generic `{request_params}` step was restricted to
        # key=value form, which un-shadowed the date-range / ownership / resolution
        # partition steps so their params are now genuinely applied. The latent
        # step-plumbing bugs that exposed (labels leaking as bogus request kwargs;
        # a partial-resolution assertion demanding the deliberately-absent id) are
        # fixed in uc004_delivery.py's _dispatch_date_range_partition /
        # _dispatch_ownership_partition / _dispatch_resolution, so these rows are
        # graded rather than deferred. The genuinely-unimplemented rows
        # (start>=end, owner_mismatch, empty_array) remain in _UC004_PARTITION_SELECTIVE.

        # Graduated: T-UC-004-partition-credentials — the When now validates the real
        # AdCP reporting_webhook Authentication at the create_media_buy boundary
        # (scheme enum + credentials min_length=32), so all rows pass on all transports.

        # The T-UC-004-partition-sampling branch is DELETED with its scenario
        # (2026-09-15), for the reason recorded at its boundary twin above: the outline
        # graded a request field AdCP 3.1.1 does not define. Its "passes on REST only"
        # clause was itself an artifact -- the REST body builder whitelists known fields
        # and silently dropped the unknown kwarg, so those rows graded a request that
        # never carried the field at all.

        # FIXME: catalog distinct type partition/boundary
        # Production accepts catalogs but never validates duplicate types or catalog_id
        # existence. Valid partitions pass; invalid partitions succeed when they should fail.
        # Graduated (all 4 transports pass with strong assertions):
        # T-UC-002-partition-catalog-distinct-type, T-UC-002-boundary-catalog-distinct-type
        _UC002_CATALOG_TAGS: set[str] = set()
        if marker_names & _UC002_CATALOG_TAGS:
            item.add_marker(
                pytest.mark.xfail(
                    reason="catalog validation not implemented in production — spec-production gap", strict=False
                )
            )

        # --- UC-019: xfails for spec-production gaps ---
        # Graduated (k31s): status_computation active variants, default_status_filter
        # simple variants, status_filter boundary simple variants, inv-150-2/4,
        # inv-151-1, inv-152-1/2/3/5, inv-154-tenant, sandbox-production,
        # snapshot available variants, principal_scoping valid variants.
        _UC019_XFAIL_TAGS: set[str] = {
            # Graduated: T-UC-019-partition-status-filter-invalid. The 2026-07-06 note
            # said "NOT graduatable" on two grounds, both since removed: the scenario was
            # not env-wired (it is now), and its examples asserted
            # STATUS_FILTER_INVALID_VALUE / STATUS_FILTER_EMPTY, codes absent from the
            # pin's 92. Both rows now assert INVALID_REQUEST, which is what the pin gives
            # for a schema-constraint violation — get-media-buys-request.json types
            # status_filter as oneOf [MediaBuyStatus, array with minItems 1], so an
            # out-of-enum value and an empty array both fail the SCHEMA, not a business
            # rule. Wire-graded on both envelope layers by then_fail_with_code, with the
            # field pointer graded by then_error_field_contains; xpassing on a2a, mcp and
            # rest. Suggestion parity for get_media_buys stays pinned by
            # tests/integration/test_request_validation_suggestion_parity.py.
            # Package details omit the flight window. The buyer supplies start/end in the
            # Given, the pinned 3.1 core/package.json names the echo fields start_time /
            # end_time (optional — required is ["package_id"]), and production does not
            # populate them on the packages get_media_buys returns.
            #
            # Declared HERE rather than inside _assert_flight_dates_present, where an
            # xfail keyed on the outcome could not fail in either direction: it passed
            # when the fields were present and excused itself when they were not, so a
            # seller that stopped echoing them entirely would never have turned it red
            # . As a tag this XPASSes the day production populates them.
            "T-UC-019-main",
            # GRADUATED: T-UC-019-partition-approval and T-UC-019-boundary-approval. The
            # entry read "Creative approval mapping — not implemented", which was false on
            # both halves: production maps internal status to the AdCP enum (its four
            # invariant siblings inv-152-1/2/3/5 are graduated and green on that very
            # mapping), and these two tags were never the thing reporting the failure. Both
            # outlines were held by ONE imperative pytest.xfail() in
            # given_package_creative_assignment, whose stated reason ("No
            # CreativeAssignmentFactory") was false too -- the factory is imported at the top
            # of uc019_query_media_buys.py and used by the two Givens directly above it.
            # pytest.xfail() ABORTS the scenario, so this tag never fired and the four
            # further inline xfails below that Given were dead code. MEASURED before the fix:
            # 39 items, 39 xfailed, ZERO executed, every one of them citing that one Given.
            # The Givens now seed and move real rows, and the boundary outline's four outcome
            # sentences have step definitions for the first time.
            #
            # T-UC-019-partition-approval-invalid is retired from BR-UC-019, not routed: the
            # pin states no rule for an assignment whose creative is missing, and the
            # composite FK makes that state unreachable here. The note where the scenario
            # stood records both.
            # Graduated (#1545 review), now wired + passing on the A2A/MCP wire:
            #   inv-150-1 (pre-flight active -> pending_start)
            #   inv-150-3 (post-flight active -> completed)
            # Graduated: T-UC-019-inv-150-5 (status filter no longer blocks by-ID queries)
            # Graduated: T-UC-019-inv-151-4 (unknown status value rejected). Asserts
            # INVALID_REQUEST with the field pointer naming status_filter and a
            # suggestion, all read off the wire on both envelope layers, and xpasses on
            # a2a, mcp and rest. Same correction as the two status-filter outlines: the
            # obligation is the pin's schema-constraint code, not a STATUS_FILTER_* code
            # the protocol never declared.
            # inv-153-3/4/5 moved to _UC019_SNAPSHOT_HARNESS_GAP_TAGS (#1721 M4):
            # they were mislabeled here as production gaps but actually fail on the
            # Given (no adapter mock in this harness), never reaching graded behavior.
            # Sandbox mode (response echo) — not implemented
            "T-UC-019-sandbox-happy",
            # Graduated (6szx): T-UC-019-sandbox-validation — BR-RULE-209 INV-7:
            # invalid status_filter on a sandbox account yields a REAL rejection
            # (_resolve_status_filter → AdCPValidationError → VALIDATION_ERROR wire
            # envelope). Given now seeds a real sandbox Account + AgentAccountAccess
            # (was an inert ctx flag); Then steps assert wire-first.
            # Graduated: T-UC-019-partition-principal-invalid identity_missing (impl/a2a/mcp pass)
            # — moved to _UC019_PARAM_XFAIL for selective identity_missing exclusion.
            # Graduated: T-UC-019-ext-a (no-auth get_media_buys)
            # now correctly emits AUTH_MISSING per the v3.1.1 AUTH_MISSING/
            # AUTH_INVALID split — was previously stale on AUTH_TOKEN_INVALID/
            # AUTH_REQUIRED.
            # Extension errors — error code mismatches / not implemented.
            # T-UC-019-ext-b is gone with its scenario, not graduated: it asked for an
            # identity resolved without a principal, which ResolvedIdentity makes
            # unconstructible, and for an errors[] code ("principal_id_missing") that is
            # not among the pin's 92 and is not even their shape. The reasoning is in
            # BR-UC-019-query-media-buys.feature where the scenario stood, and the real
            # refusals are graded by the principal scoping boundary outline.
            # T-UC-019-ext-c is gone with its scenario, not graduated: it asked for a
            # credential that resolves to no principal to be answered with an empty
            # media_buys array and an errors[] code ("principal_not_found") that is not
            # among the pin's 92 and is not their shape, and its Given reached that shape
            # by injecting a fabricated identity past the resolver. The reasoning is in
            # BR-UC-019-query-media-buys.feature where the scenario stood; the real
            # refusal (AUTH_INVALID, terminal, hard) is graded by the principal scoping
            # boundary outline on every transport.
            # Graduated (6szx): T-UC-019-ext-d — invalid parameter types are rejected at
            # request construction (GetMediaBuysRequest) and translated at the
            # transport boundary, with field-level details (field="media_buy_ids"),
            # recovery=correctable and a top-level suggestion, on the A2A wire and via
            # the typed exception on the legacy MCP wrapper. Then steps assert wire-first.
            # Graduated (subdl): T-UC-019-ext-e — the xfail reason "feature not yet
            # implemented" was WRONG. The feature IS implemented: media_buy_list.py:107
            # raises AdCPCapabilityNotSupportedError -> UNSUPPORTED_FEATURE / correctable
            # / 422, which is exactly what the pin defines ("A requested feature or field
            # is not supported by this seller"). What actually failed was the scenario
            # demanding the MESSAGE contain "account_id filtering is not yet supported" —
            # an authored sentence that cannot exist, since AdCPSalesAgentError.message is a
            # read-only property returning CODE_TABLE[code].message ("Feature not
            # supported"). Removing that tautology is what un-xfailed it. Then steps are
            # wire-graded via then_fail_with_code (both envelope layers must agree, and
            # a no-wire run raises). Verified xpassing on a2a and mcp — the only
            # transports this module collects; its total absence of [rest] is a
            # module-wide parametrize-time gap filed separately.
        }
        # Snapshot scenarios (main-snapshot, inv-153-3/4/5): given_adapter_supports_reporting /
        # given_adapter_no_reporting assert "adapter" in env.mock, but MediaBuyListEnv
        # (the UC-019 harness) deliberately runs get_media_buys against a real DB with
        # NO adapter mock at all ("list is a pure read" — see the UC-019 harness comment).
        # This is a TEST-HARNESS gap (the snapshot Given can never succeed), not a
        # production behavior gap -- was mislabeled "spec-production gap" (#1721 M4
        # dormancy tripwire caught it: the scenarios fail on the Given, before ever
        # reaching the production code the reason claimed was ungraded).
        _UC019_SNAPSHOT_HARNESS_GAP_TAGS: set[str] = {
            "T-UC-019-main-snapshot",
            "T-UC-019-inv-153-3",
            "T-UC-019-inv-153-4",
            "T-UC-019-inv-153-5",
        }
        if marker_names & _UC019_SNAPSHOT_HARNESS_GAP_TAGS:
            item.add_marker(
                pytest.mark.xfail(
                    reason="UC-019 test-harness gap: MediaBuyListEnv wires no adapter mock "
                    "(get_media_buys list is a pure DB read), so the snapshot Given steps "
                    "(given_adapter_supports_reporting / given_adapter_no_reporting) cannot "
                    "configure anything and fail before reaching the graded behavior — FIXME",
                    strict=False,
                )
            )
        elif marker_names & _UC019_XFAIL_TAGS:
            item.add_marker(
                pytest.mark.xfail(
                    reason="UC-019 spec-production gap — feature not yet implemented",
                    strict=False,
                )
            )

        # --- UC-019: selective boundary xfails for un-implemented sub-features ---
        # These scenario outlines are mostly graduated; only the rows exercising a
        # not-yet-implemented sub-feature are xfailed. All are pre-existing gaps
        # unrelated to this PR's status-taxonomy work.
        _UC019_BOUNDARY_SELECTIVE: list[tuple[str, set[str], str]] = [
            # Graduated: T-UC-019-boundary-status-filter rows pending_activation and
            # expired. The reason demanded a dedicated STATUS_FILTER_INVALID_VALUE code,
            # which is not among the pin's 92; both rows now assert INVALID_REQUEST,
            # the code 3.1.1 gives for a schema-constraint violation, and both xpass on
            # a2a, mcp and rest. Graded by then_error_code_with_suggestion, which asserts
            # through assert_wire_error with require_suggestion, so the suggestion must
            # sit in error.json's own position rather than inside details.
            # Sandbox echo (sandbox=true/false in the response) is not implemented;
            # only the production-absent row is graded.
            (
                "T-UC-019-boundary-sandbox",
                {"sandbox account", "explicit production"},
                "sandbox response echo not implemented (BR-RULE-209)",
            ),
        ]
        for tag, substrings, reason in _UC019_BOUNDARY_SELECTIVE:
            if tag in marker_names and any(s in nodeid for s in substrings):
                item.add_marker(pytest.mark.xfail(reason=reason, strict=False))
                break

        # --- UC-019: the principal boundary rows this parked are gone ---
        # It matched three example names — "principal_id is null", "principal_id is empty
        # string", "principal_id not in registry" — and its own reason said why they could
        # not be graded: "a valid token always resolves to a real principal; an invalid
        # token gets rejected by auth middleware before _impl", so they were "only testable
        # at the _impl level". That reasoning was right about the rows and named a layer
        # that no longer exists: there is no IMPL transport (tests/CLAUDE.md), because
        # modelling a direct call as one gave every assert-on-the-wire rule an escape hatch.
        #
        # So the rows were corrected rather than parked. The two that posited an
        # authenticated identity with no principal are gone: adcp 3.1.1
        # enums/error-code.json gives AUTH_MISSING as "No credentials were presented", which
        # a caller presenting a credential never is, and ResolvedIdentity makes the state
        # unconstructible anyway. The third collapsed into one row that names what the
        # architecture does have — a presented credential that verifies against no
        # principal — refused hard with AUTH_INVALID, whose recovery the pin sets to
        # terminal. It grades on a2a, mcp and rest alike, so nothing needs parking here.

        # --- UC-019: HTTP transport xfails for auth suggestion mismatch ---
        # Graduated on `rest`: the reason below -- a REST-only
        # suggestion string -- cannot be true any more. Suggestions derive from
        # CODE_TABLE[code], one source for every transport, so no transport can
        # carry a different one. Verified xpassing on rest
        # once UC-019 regained REST parametrization.
        #
        # Graduated 2026-09-15 (XPASS in-network, innet_150926_0531): the e2e_rest
        # branch for T-UC-019-ext-a. The reason it carried — a REST-only suggestion
        # saying "authenticate" rather than "authentication" — is structurally
        # impossible now: every transport reads the suggestion from the pin through
        # CODE_TABLE, so no transport can carry a different one, which is what the
        # paragraph above already says. A token-less get_media_buys over real HTTP is
        # refused by the shared resolver with AUTH_MISSING / correctable and the pinned
        # suggestion on both envelope layers, and the scenario's Thens hard-fail when no
        # real wire envelope was captured.
        if (is_rest or is_e2e_rest) and "T-UC-019-partition-principal-invalid" in marker_names:
            if "identity_missing" in nodeid:
                item.add_marker(
                    pytest.mark.xfail(
                        reason="HTTP transport: auth error suggestion says 'authenticate' not 'authentication' — spec-production gap",
                        strict=False,
                    )
                )

        # --- UC-019: parametrization-specific xfails for partially-passing scenarios ---
        # These scenario outlines have some parametrizations that pass (graduated)
        # and some that still fail. Only the failing variants are xfailed.
        _UC019_PARAM_XFAIL: list[tuple[str, set[str], str]] = [
            # Graduated: T-UC-019-partition-status pre_flight/post_flight
            # (status filter no longer blocks by-ID queries)
            # Graduated: T-UC-019-boundary-status day before/day after
            # (status filter no longer blocks by-ID queries)
            # Graduated (#1545 review): T-UC-019-partition-status-filter
            # multiple_statuses / all_statuses — multi-status filtering works on the
            # wire once status_filter is coerced to the MediaBuyStatus enum and the
            # scenario pins its clock. The remaining status-filter gaps are the
            # value/empty VALIDATION rows below, not the mapping.
            # Graduated: T-UC-019-boundary-status-filter row "empty array". Its reason
            # named STATUS_FILTER_EMPTY, a code the pin does not declare, and claimed an
            # empty array returns an empty success. The row now asserts INVALID_REQUEST
            # and xpasses on all three transports: get-media-buys-request.json gives the
            # array branch minItems 1, so [] fails the schema. "all seven" grades and
            # passes as before.
            # Snapshot: not-requested variant fails (include_snapshot=false path)
            (
                "T-UC-019-partition-snapshot",
                {"snapshot_not_requested"},
                "UC-019: snapshot_not_requested path not implemented",
            ),
            # Snapshot boundary: omitted/false/mixed variants fail
            (
                "T-UC-019-boundary-snapshot",
                {"include_snapshot omitted", "include_snapshot explicitly false", "mixed"},
                "UC-019: snapshot boundary omitted/false/mixed paths not implemented",
            ),
            # Graduated: identity_missing (impl/a2a/mcp) — only missing_principal_id
            # and principal_not_found still fail.
            (
                "T-UC-019-partition-principal-invalid",
                {"missing_principal_id", "principal_not_found"},
                "UC-019: principal_id missing/not-found not implemented",
            ),
        ]
        if any(t.startswith("T-UC-019") for t in marker_names):
            for tag, substrings, reason in _UC019_PARAM_XFAIL:
                if tag in marker_names and any(s in nodeid for s in substrings):
                    item.add_marker(pytest.mark.xfail(reason=reason, strict=False))
                    break

        # --- UC-019: e2e_rest xfails for Givens that seed the SUITE db ---
        # UC-019 regained REST parametrization when get_media_buys got a REST
        # route. That correctly enabled `rest`, and it also
        # enabled `e2e_rest`, which is a different proposition: e2e_rest sends
        # real HTTP to the LIVE server, and the server reads its OWN database.
        #
        # Every UC-019 Given seeds through MediaBuyFactory into the harness
        # session -- the step file contains no realize_e2e call at all -- so on
        # e2e_rest the rows land in the suite DB while the request is answered
        # from the server's, and the buys are simply not there. It presents as
        # "Filter 'active' returned no media buys" and "got IDs: []", which reads
        # like a filtering bug and is not one.
        #
        # Routed rather than dropped from parametrization: an xfail is visible to
        # the escape-hatch detectors in
        # test_architecture_e2e_rest_escape_hatches.py, and a parametrize-time
        # exclusion is not -- that invisibility is exactly what hid UC-019's
        # missing REST coverage in the first place. Graduating these needs the
        # Givens seeding through realize_e2e (a recorded gap separately), not
        # a change to production.
        # (folded into the existing UC-019 e2e_rest block below rather than
        # opening a second one on the same condition -- one guard, several
        # reasons, and no new entry in EXPECTED_XFAIL_ROUTES.)
        # Graduated 2026-09-15 (XPASS in-network, innet_150926_0531): T-UC-019-inv-150-11.
        # The set's stated reason does not hold for it, and on inspection does not hold
        # for this tree at all: over e2e_rest the conftest points production AND the
        # factories at the live server's own database, so a Given that seeds through
        # MediaBuyFactory is seeding the database the server reads. That scenario refuses
        # an unmapped persisted status with CONFIGURATION_ERROR / terminal naming the row,
        # which is production behaviour reached before any flight refinement and so
        # independent of the clock. The three tags that remain here are NOT covered by
        # that correction -- each still needs its own audit, and the reason string below
        # is known to be wrong for them too.
        # 2026-09-15: the two status-filter tags left this set with the clock rewrite.
        # Their seed ("owns media buys in various statuses") now anchors its three
        # windows on the REAL date instead of on ctx["mock_today"], and their scenarios
        # pin no clock, so the buys hold their intended statuses on the live server as
        # well as in-process. Under the old shape every window sat months in the past
        # over e2e_rest and all three buys read "completed" -- which is what made the
        # "completed" and "all seven" rows xpass while the rows that discriminate
        # between statuses failed.
        _UC019_E2E_SUITE_DB_SEED_TAGS: set[str] = {
            "T-UC-019-inv-150-1",
        }

        # --- UC-019: e2e_rest xfails for datetime-mock-dependent tests ---
        # These scenarios use `And today is "<date>"` which patches datetime
        # in-process. The patch has no effect on Docker — real datetime.now()
        # is used, so status assertions fail.
        #
        # THE FIX IS THE SCENARIO, not this route. 2026-09-15: the three tags below
        # that carried the whole status-refinement contract were rewritten to state
        # their flight windows as offsets from the run date and to pin no clock at
        # all, so each boundary is now graded identically on a2a, mcp, rest and
        # e2e_rest. They are gone from this set. What that removed was not coverage
        # but a false reading: under the old shape the rows expecting "completed"
        # xpassed merely because the real calendar had drifted past a fixed 2026-03
        # window, so a production that ignored the flight window entirely would have
        # passed them, while the rows naming every other boundary could not run here
        # at all. The three tags that REMAIN genuinely need a clock they cannot set
        # (they pin start_time / end_time precedence against a fixed date) and are
        # the real remainder of this gap.
        if is_e2e_rest and any(t.startswith("T-UC-019") for t in marker_names):
            _UC019_E2E_DATETIME_TAGS: set[str] = {
                "T-UC-019-inv-150-2",
                "T-UC-019-inv-150-4",
                "T-UC-019-inv-150-5",
            }
            _UC019_E2E_MOCK_TAGS: set[str] = {
                # Adapter mock (get_adapter patch) has no effect in Docker.
                "T-UC-019-partition-snapshot",
                "T-UC-019-boundary-snapshot",
            }
            # The per-example exemption that used to sit here is deleted with the tags
            # it exempted. It had also silently stopped matching: it keyed on nodeid
            # substrings ("day after end_date", "post_flight") that a feature
            # regeneration had renamed, which is why those rows reported XPASS rather
            # than PASS. A substring exemption that decays into a no-op the moment
            # someone rewords an Examples cell is the wrong mechanism; the scenario
            # rewrite removes the need for one.
            _inv150_5_graduated = "T-UC-019-inv-150-5" in marker_names  # all examples pass
            if marker_names & _UC019_E2E_DATETIME_TAGS and not _inv150_5_graduated:
                item.add_marker(
                    pytest.mark.xfail(
                        reason="e2e_rest: datetime.now() mock has no effect in Docker — status computed from real date",
                        strict=False,
                    )
                )
            if marker_names & _UC019_E2E_SUITE_DB_SEED_TAGS:
                item.add_marker(
                    pytest.mark.xfail(
                        reason=(
                            "e2e_rest: UC-019 Givens seed via MediaBuyFactory into the suite DB; "
                            "the live server reads its own DB, so the seeded buys are invisible. "
                            "Needs realize_e2e seeding, not a production change."
                        ),
                        strict=False,
                    )
                )
            _UC019_E2E_MOCK_GRADUATED = {
                ("T-UC-019-partition-snapshot", "supported_but_unavailable"),
                # Only "snapshot null" passes on e2e_rest: Docker's mock adapter
                # has no test media buy data, so get_packages_snapshot returns None,
                # and production maps that to SNAPSHOT_TEMPORARILY_UNAVAILABLE —
                # matching the expected outcome. Other variants FAIL because:
                # - "snapshot returned"/"all packages" expect real snapshot data
                # - "does not support" expects UNSUPPORTED but mock says supported=True
                ("T-UC-019-boundary-snapshot", "snapshot null"),
            }
            _mock_graduated = any(tag in marker_names and substr in nodeid for tag, substr in _UC019_E2E_MOCK_GRADUATED)
            if marker_names & _UC019_E2E_MOCK_TAGS and not _mock_graduated:
                item.add_marker(
                    pytest.mark.xfail(
                        reason="e2e_rest: adapter mock has no effect in Docker — snapshot data not controllable",
                        strict=False,
                    )
                )
            # Graduated 2026-09-15 (XPASS in-network, innet_150926_0531):
            # T-UC-019-inv-154-tenant. Its reason -- "in-process fixtures don't populate
            # Docker DB" -- has not been true since the harness bound its factories to
            # e2e_config.postgres_url: in e2e mode the factories write the live server's
            # own Postgres and the ctx fixture wipes that database before each scenario,
            # so the seeded buys ARE visible and the wire-strict "include mb-001" Then
            # grades the real HTTP response. Production's tenant-and-principal WHERE
            # explains the pass.
            #
            # A real gap this graduation does NOT close, filed separately: the scenario
            # names INV-1 (database scoped to tenant) but seeds both principals inside
            # ONE tenant, so the tenant half of the isolation is unexercised on every
            # transport. Keeping a false-reason xfail here neither graded it nor made it
            # visible; a cross-tenant example is what will.
            # Graduated: T-UC-019-inv-152-1/2/5 (: creative approval data seeded)
            # — only in-process transports graduated; e2e_rest still fails (below).

            # principal_scoping_boundary error cases are excluded on e2e_rest
            # (handled by the REST+e2e_rest block below, outside this if-block).

            # Graduated: T-UC-019-inv-152-1, T-UC-019-inv-152-2, T-UC-019-inv-152-5
            # (: creative approval data now visible to e2e_rest Docker)

        # --- UC-026: xfails for spec-production gaps ---
        # Transport wiring done (a3xo: MediaBuyDualEnv routes updates correctly).
        # Remaining failures are production-level: AffectedPackage lacks full state,
        # keyword targeting ops not implemented, error codes/suggestions missing.
        # FIXME: UC-026 production gaps in update response and validation.
        _UC026_XFAIL_TAGS: set[str] = {
            # (The qq6f "Graduated: T-UC-026-main-explicit-formats" note that stood here
            # was stale: the tag was re-added in the 2026-09-15 block below, which now
            # carries the graduation record.)
            # Full-config: optimization_goals missing `kind`, targeting_overlay.audiences extra_forbidden
            "T-UC-026-main-full-config",
            # Graduated: T-UC-026-alt-update, T-UC-026-alt-pause, T-UC-026-alt-resume
            # (innet_291026_0008, all four transports xpass). update_media_buy now answers
            # one affected_packages snapshot per modified package (_affected_package,
            # media_buy_update.py), which 3.2.7 media-buy/update-media-buy-response.json
            # requires ("full Package objects showing complete post-update state for each
            # modified package"). The snapshot's budget and paused are the values the
            # budget and paused branches applied, and a field no branch touched is read off
            # the stored package -- so a seller that skipped either branch would report the
            # stored 5000 budget, or the stored pause state the Given realized (created
            # active for pause, created paused for resume), and fail these Thens. Scope of
            # the grade: in-process, MediaBuyDualEnv's update adapter is a MagicMock, so the
            # package_config write an update pause makes on the ad server side is graded on
            # no transport; the Thens grade the tool's branch, not the adapter's record.
            # Keyword alt-flows: the seller refuses keyword ops with UNSUPPORTED_FEATURE and
            # these scenarios still assert the effect of applying them.
            "T-UC-026-alt-keyword-add",
            "T-UC-026-alt-keyword-upsert",
            "T-UC-026-alt-keyword-remove",
            "T-UC-026-alt-keyword-remove-noop",
            "T-UC-026-alt-negative-keyword-add",
            "T-UC-026-alt-negative-keyword-remove-noop",
            "T-UC-026-alt-dedup",
            # Graduated: T-UC-026-alt-dedup-crossbuy (all 4 transports pass)
            # Extension error scenarios — error codes/suggestions not implemented
            # Graduated: T-UC-026-ext-a (all 4 transports pass)
            # Graduated to _UC026_PARTITION_SELECTIVE: T-UC-026-ext-b. Two of its three
            # rows (product_id, pricing_option_id) pass on all four transports; the
            # budget row is a 3.2.1 regrounding failure, not a production gap, and is
            # row-keyed below.
            "T-UC-026-ext-c",
            "T-UC-026-ext-d",
            # Graduated in wave 5 (integrate/wave1 a3687d971): T-UC-026-ext-f (w5b10), the
            # duplicate-catalog refusal create_media_buy now makes with VALIDATION_ERROR.
            # Graduated: T-UC-026-ext-g-product, T-UC-026-ext-g-format,
            # T-UC-026-ext-g-pricing (innet_091026_1343, all four transports xpass).
            # Production refuses each immutable field on update from
            # UpdateMediaBuyRequest._refuse_forbidden_package_shapes, which raises one
            # pydantic `not` error per offending field; adcp_error_for maps a pydantic
            # ValidationError to AdCPInvalidRequestError, so the buyer gets
            # INVALID_REQUEST + recovery=correctable + the CODE_TABLE suggestion with
            # field='packages[0].<name>'. Measured on this tree at the DTO boundary:
            # product_id, format_ids and pricing_option_id each resolve to
            # INVALID_REQUEST with that field; the wire-level evidence is the xpass on
            # all four transports. 3.2.1 media-buy/package-update.json grounds it -- it
            # declares none of the three and its root description names them
            # "Fully-immutable fields ... cannot appear in update payloads".
            # Each scenario's POST-F2 said "Error identifies immutable <name> field" and
            # no Then graded it, so the graduation added `the error field should contain
            # "<name>"` to all three; they still xpass under it.
            "T-UC-026-ext-h-keyword",
            "T-UC-026-ext-h-negative",
            "T-UC-026-ext-h-cross-ok",
            "T-UC-026-ext-h-cross-reverse",
            # NOT graduated, and the reason is a HARNESS gap this xpass was hiding.
            #
            # T-UC-026-ext-i xpassed on all four transports, and the xpass was grading the
            # WRONG TOOL. Its Givens are "a valid update_media_buy request with package
            # update: | budget | 7000 |" and "the package update contains no package_id"
            # -- and nothing before them creates a media buy. So ``_ensure_update_kwargs``
            # has no ``existing_media_buy_id`` to copy, ``update_kwargs`` carries no
            # ``media_buy_id``, and ``_is_update_request`` (tests/harness/media_buy_dual.py:37)
            # discriminates on exactly that key: with no ``req`` and no ``media_buy_id`` it
            # returns False and the flat body is dispatched to CREATE_media_buy. Production
            # then refuses a create package of ``{budget: 7000}`` -- no product_id -- and the
            # scenario's INVALID_REQUEST / suggestion / correctable assertions all held,
            # because create and update share one error-envelope schema.
            #
            # Measured: the graduation added `the error field should contain "package_id"`
            # (the scenario's own ungraded POST-F2, "Error explains that a package
            # identifier is required"), and all three in-process transports went from
            # xpass to FAIL with "Expected error field to contain 'package_id', got
            # 'packages[0].product_id'". The field pointer is the one part of the envelope
            # that says which request was validated, which is why adding it is what made
            # the misdispatch visible.
            #
            # The assertion STAYS -- it is the correct obligation (3.2.1
            # media-buy/package-update.json has required: ["package_id"]) and deleting it
            # would restore the vacuum. Graduating this needs the Given to establish a
            # media buy so the dispatch reaches update_media_buy at all; same family as
            # T-UC-026-ext-j below, a harness gap stated as such rather than blamed on the
            # seller.
            "T-UC-026-ext-i",
            # Invariant scenarios — production validation gaps
            # Graduated: T-UC-026-inv-194-1 (all 4 transports pass)
            "T-UC-026-inv-194-2",
            # Graduated: T-UC-026-inv-195-1 (innet_091026_1343, all four transports
            # xpass). 3.2.1 core/package.json declares pricing_option_id on the returned
            # package, and the Then compares it for equality against the id the scenario
            # named (resolved through the seeded pricing option), so an unechoed or
            # substituted value fails.
            "T-UC-026-inv-195-2",
            # Graduated: T-UC-026-inv-195-3 (rczc: bid_price ceiling semantics pass all 4 transports)
            # Graduated: T-UC-026-inv-195-4 (rczc: bid_price exact semantics pass all 4 transports)
            # Graduated: T-UC-026-inv-196-3 (all 4 transports pass)
            # Graduated: T-UC-026-inv-197-4 (innet_091026_1343, all four transports
            # xpass). 3.2.1 media-buy/package-request.json keeps minItems: 1 on
            # format_ids, so an empty array is a schema-constraint violation, which
            # 3.2.1 enums/error-code.json puts under INVALID_REQUEST ("violates schema
            # constraints", recovery correctable). Measured on this tree: the refusal
            # comes back INVALID_REQUEST with field='packages[0].format_ids' and one
            # issue carrying keyword 'minItems' -- measured at the DTO boundary, with the
            # xpass as the wire-level evidence. The scenario was strengthened with the
            # graduation to assert that field pointer and recovery=correctable.
            # Graduated: T-UC-026-inv-198-4 (innet_291026_0008, all four transports xpass).
            # The Then reads budget 9000 and pacing "even" off the affected_packages
            # snapshot; pacing is re-read from the MediaPackage.pacing column the update
            # writes, and the Given's package was created with no pacing, so an update that
            # dropped pacing reports None and fails.
            "T-UC-026-inv-199-3",
            "T-UC-026-inv-199-4",
            # Graduated: T-UC-026-inv-200-1 (all 4 transports pass)
            # Graduated: T-UC-026-inv-200-2 (PASSED on a2a, mcp and rest -- the three
            # transports this module parametrizes; not in e2e_rest_known_failures.txt,
            # so there is no ledger line to mirror).
            # 3.2.1 media-buy/package-request.json types the request's ``paused``
            # "Whether this package should be created in a paused state. Paused packages
            # do not deliver impressions. Defaults to false." The seller used to write a
            # hardcoded False into package_config and read the pause state back off it,
            # so a package asked for paused delivered AND reported paused=false. The ask
            # now rides MediaPackage to the ad server, which is where the response's
            # value comes from (``Package.SELLER_DECIDED`` names paused), and GAM/Kevel/
            # Triton create the line item held. The scenario's Then was strengthened with
            # the graduation to read the persisted package_config as well as the response
            # -- the record get_media_buys serves the field from -- so an echo cannot
            # satisfy it. Fire-probed: restoring the hardcoded literal turns it red.
            # Graduated: T-UC-026-inv-201-1 (xpassed on a2a, mcp, rest and e2e_rest in
            # innet_291026_2041). Create records the request's catalogs in package_config on
            # both arms of media_buy_create.py, and 7685a2908 made the scenario read the
            # created package's stored catalogs back before the update and compare the
            # replaced list by exact (type, catalog_id) pair, so a seller that echoes
            # catalogs without storing them, or merges instead of replacing, fails it.
            "T-UC-026-inv-201-2",
            "T-UC-026-inv-201-3",
            "T-UC-026-inv-201-4",
            "T-UC-026-inv-201-5",
            # Graduated: T-UC-026-inv-089-2 (t8iq: catalogs now echoed, default pkg fields added)
            # Graduated: T-UC-026-inv-089-3 (all 4 transports pass)
            # Graduated to _UC026_PARTITION_SELECTIVE (x2l0): keyword boundary/partition
            # tags now mostly pass — only REST update dispatch + specific cross-transport
            # validation gaps remain. Selective xfail handles the narrower failure set.
            #
            # ── Added 2026-09-15, when UC-026 was connected to the suite at all ──
            # These tags never reported a verdict before: the use case had no ENV_ROUTES
            # row and its step module was absent from pytest_plugins, so all 75 scenarios
            # xfailed at fixture setup. Now that they dispatch, each of these fails on a
            # response the seller does not produce. Every reason below is the assertion
            # the scenario actually reports, not a guess -- the misclassification tripwire
            # above rejects a dormancy or a Given-side error dressed as a production gap,
            # and these were each cleared through it.
            #
            # ONLY tags whose EVERY row fails belong in this set. A tag here xfails all
            # of its parametrized rows, so listing an outline that fails two rows out of
            # twelve converts the other ten from passing to xpassed -- grading removed,
            # not gained. Measured before listing: these four are whole-scenario failures
            # on a2a, mcp and rest alike. The outlines that fail only some rows are in
            # _UC026_PARTITION_SELECTIVE below, keyed by the row.
            #
            # format_ids is echoed only when the request carried it. core/package.json @
            # 3.2.7: "Omitted means the request did not carry legacy format_ids", so a
            # package that omits them gets none back, and 10c593143 corrected the scenario
            # to assert exactly that, which production already does. The expected remaining
            # failure is the format_ids_to_provide line: on a product with no canonical
            # snapshot, _legacy_formats_to_provide returns the request's format_ids, which
            # are absent here, so the Then reads None. Not yet measured on a box run.
            "T-UC-026-main-required-fields",
            # Graduated: T-UC-026-main-explicit-formats (innet_091026_1343, all four
            # transports xpass) -- the other half of that note is fixed: a package that
            # SUPPLIES format_ids now gets them echoed, and the Then compares the echoed
            # set for equality, so a dropped or widened set fails. format_ids_to_provide
            # is graded in both directions (assigned formats excluded, unassigned ones
            # present) by then_package_formats_to_provide_based_on_creatives.
            # Graduated: T-UC-026-inv-089-2 (innet_091026_1343, all four transports
            # xpass) -- catalogs are echoed on the created package now. 3.2.1
            # core/package.json: "Echoed from the create_media_buy request". The Then was
            # strengthened with the graduation from a count-plus-distinct-types tally to
            # set equality of (type, catalog_id) against the request, which is what an
            # echo obligation actually says; it still xpasses under the stronger rule.
            # price_breakdown absent from the create response, so the default
            # list_price == option rate claim has nothing to read.
            "T-UC-026-inv-196-3",
            # Cancellation, now that both scenarios actually dispatch. Neither is
            # dormant any more -- their Givens realize a canceled package through the
            # real update path, and their Thens read the wire -- so these two reasons
            # are production's measured answers:
            #
            #   alt-cancel: the update response carries NO packages at all, so the
            #   canceled=true echo has nothing to be read from.
            "T-UC-026-alt-cancel",
            #   alt-cancel-irreversible: the seller DOES refuse canceled=false, but the
            #   envelope names field 'media_buy_id' rather than 'canceled', so the buyer
            #   is not told which field violated the const. The refusal is right and the
            #   field pointer is wrong.
            "T-UC-026-alt-cancel-irreversible",
            # Graduated: T-UC-026-ext-e (innet_291026_0008, all four transports xpass).
            #   Production answers UNSUPPORTED_FEATURE since c0e130f1b, the code
            #   media-buy/package-request.json @ 3.2.7 ("Format selector contract
            #   (normative)") gives "a single resolved route that does not satisfy the
            #   product"; _resolve_package_selectors raises canonical_refusal with
            #   field=packages[0].format_ids. The scenario's POST-F2 ("Error identifies
            #   unsupported format_ids") was ungraded, so the graduation added `the error
            #   field should contain "format_ids"`.
            # NOT graduated although it xpassed on all four transports (innet_291026_0008):
            # the xpass was vacuous. Its only Then checked that format_ids is not echoed,
            # which a seller applying no default also passes, so INV-3 ("format_ids omitted
            # defaults to all product formats") was graded by nothing. The scenario now reads
            # the default off format_ids_to_provide, and is expected to fail there: a request
            # naming no selector records no snapshot (_package_format_snapshots), so
            # _legacy_formats_to_provide falls back to the request's absent format_ids and
            # the field is None. That is the failure T-UC-026-main-required-fields measured
            # on all four transports in innet_291026_0008 ("Package format_ids_to_provide
            # is None") through the same code path; this scenario's own failure is traced,
            # not yet measured on a box run.
            "T-UC-026-inv-197-3",
            # HARNESS gap, not a production one, and the distinction is the point. The
            # scenario opens "the Buyer owns a media buy with a package that has already
            # SETTLED", and nothing reaches that state: settlement is billing-side, no
            # buyer-facing request performs it, and no seeding path writes one. Its Given
            # creates the package and stops, so production is handed an ordinary active
            # package and cancels it instead of refusing with NOT_CANCELLABLE. Production
            # is not failing here -- it is being asked a different question than the
            # scenario means to ask. Graduating this needs a way to persist a settled
            # package, after which the scenario grades the refusal for real.
            "T-UC-026-ext-j",
        }
        # THE SHARED REASON BELOW IS A GROUP LABEL, not each member's cause; the cause of
        # each is the comment beside its tag. strict=False means an xpass here is SILENT,
        # so a member that starts passing is graduated by nobody unless a box run's xpass
        # list is read.
        if marker_names & _UC026_XFAIL_TAGS:
            item.add_marker(
                pytest.mark.xfail(
                    reason="UC-026 spec-production gap — see the comment beside the tag in "
                    "_UC026_XFAIL_TAGS for this scenario's measured cause",
                    strict=False,
                )
            )

        # --- UC-026 partition/boundary: selective xfail for graduated tags ---
        # FIXME: Remaining failures are production-level gaps.
        # x2l0: narrowed from set() (all-fail) after a3xo MediaBuyDualEnv wiring
        # graduated most partition/boundary examples.
        #
        # ── § REST update rows ─────────────────────────────────────────────────
        # Graduated (innet_291026_0008): every REST row this block used to park as "REST
        # update dispatch" or "vacuous because update_media_buy returns
        # affected_packages=[]". Both causes are gone, and each row now passes on a2a, mcp,
        # rest and e2e_rest alike:
        #   * The keyword-op rows (keyword_targets_add/_remove, negative_keywords_add/
        #     _remove, partition and boundary, shared and unshared) were regrounded by
        #     051877652 to expect UNSUPPORTED_FEATURE -- core/targeting.json "MUST be
        #     applied exactly or rejected", and this seller declares no overlay_support --
        #     and _refuse_unapplied_package_fields (media_buy_update.py) raises it before
        #     the UoW opens. then_outcome grades the code and the suggestion on the wire
        #     envelope through assert_wire_error, and the refusal exists on the update
        #     path only, so it also proves the REST row reached update_media_buy.
        #   * The immutable and paused success rows grade their effect through
        #     _assert_requested_package_effects against the one affected_packages snapshot
        #     per modified package that _affected_package now builds. 3.2.7
        #     media-buy/update-media-buy-response.json: "full Package objects showing
        #     complete post-update state for each modified package". The paused resume and
        #     already-paused rows now start from a package created paused, so a seller that
        #     ignored the resume reports the stored pause and fails.
        # The two "existing values preserved" REST rows stay routed below, with their own
        # reason: their xpass is vacuous on the claim the row names. They XPASSED again in
        # innet_291026_0257 on the same vacuous Then, so they are not graduated from it. The
        # vacuity is now removed: the Given creates the package with catalogs, create records
        # them in package_config, and _assert_unnamed_catalogs_preserved compares the
        # snapshot's catalogs with the created ones (3.2.7 media-buy/package-update.json,
        # "Omit to leave catalogs unchanged"). They graduate, with their a2a, mcp and
        # e2e_rest siblings now graded by the same check, on the first box run of that change.
        _UC026_SHAPE_ROW_ANSWERED_UNSUPPORTED = (
            "answered UNSUPPORTED_FEATURE where the row expects INVALID_REQUEST, on a2a, mcp, "
            "rest and e2e_rest (innet_291026_0008)"
        )
        _UC026_PRESERVATION_UNGRADED = (
            "the row's xpass in innet_291026_0257 graded nothing about preservation (the package "
            "was created with no catalogs); the Given now seeds catalogs, create persists them and "
            "the effect check compares them, so this row graduates on the first box run that "
            "measures that change"
        )
        _UC026_PARTITION_SELECTIVE: list[tuple[str, set[str], str]] = [
            # Moved here from _UC026_XFAIL_TAGS, narrowed from the whole tag to one row.
            # T-UC-026-ext-b has three rows and two of them -- product_id and
            # pricing_option_id -- xpass on all four transports (innet_091026_1343):
            # measured on this tree, each comes back INVALID_REQUEST with
            # field='packages[0].<name>' and one issue whose keyword is 'required', which
            # is exactly what the scenario grades.
            #
            # The budget row is a REGROUNDING failure, not a production gap, and it is the
            # reason the tag could not simply be deleted. 3.2.1
            # media-buy/package-request.json has required: ["product_id",
            # "pricing_option_id"] -- budget is NOT required ("Required in fixed
            # allocation mode. Optional in seller-optimized mode"). The scenario was
            # written against 3.1.1 and lists budget as a third required field. This
            # seller does refuse a budget-less package, but through the request-level
            # accepted-shape oneOf ("matches no accepted shape. One of: packages, each
            # with budget and without min_spend_target ..."), whose pydantic error has
            # loc () -- so `field` comes out EMPTY and `issues` empty, and the scenario's
            # `the error field should contain "budget"` has nothing to read. Two separate
            # things to fix upstream, neither of them here: reconcile the row against the
            # 3.2.1 required list, and give the accepted-shape refusal a field pointer
            # (the spec's own answer for the seller-optimized case is UNSUPPORTED_FEATURE,
            # not an unattributed INVALID_REQUEST).
            (
                "T-UC-026-ext-b",
                {"-budget]"},
                "3.2.1 package-request.json does not require budget, and the accepted-shape "
                "refusal that does reject it carries loc () so the envelope names no field — "
                "scenario needs upstream reconciliation, not graduation",
            ),
            # ── Added 2026-09-15 with the UC-026 wiring. Row-keyed, not tag-keyed,
            # because each of these outlines fails a MINORITY of its rows and the rest
            # genuinely pass; xfailing the whole tag would have turned those passes into
            # xpasses and deleted the coverage the wiring just produced.
            # Graduated: T-UC-026-boundary-format-ids ("format_id from different product",
            # "one unsupported format_id among valid ones") and T-UC-026-partition-format-ids
            # (unsupported_format) -- innet_291026_0008, all four transports xpass.
            # media-buy/package-request.json @ 3.2.7, "Format selector contract
            # (normative)": "A single resolved route that does not satisfy the product is
            # rejected with UNSUPPORTED_FEATURE"; _resolve_package_selectors raises it
            # through canonical_refusal since c0e130f1b, and then_outcome grades the code
            # and suggestion on the wire envelope.
            #
            # Keyword / negative-keyword shape rows that still fail on every transport: an
            # empty keyword, or a keyword op sent beside the same dimension in
            # targeting_overlay. ONE row each, any transport, so these entries are the whole
            # routing for those rows.
            *(
                (tag, {row}, f"{what} {_UC026_SHAPE_ROW_ANSWERED_UNSUPPORTED}")
                for tag, row, what in (
                    ("T-UC-026-boundary-keyword-add", "empty keyword string", "an empty keyword is"),
                    ("T-UC-026-boundary-keyword-remove", "empty keyword string", "an empty keyword is"),
                    (
                        "T-UC-026-boundary-kw-add-shared",
                        "keyword_targets_add WITH targeting_overlay.keyword_targets",
                        "a keyword op beside the same overlay dimension is",
                    ),
                    (
                        "T-UC-026-boundary-kw-remove-shared",
                        "keyword_targets_remove WITH targeting_overlay.keyword_targets",
                        "a keyword op beside the same overlay dimension is",
                    ),
                    (
                        "T-UC-026-boundary-neg-kw-add",
                        "negative_keywords_add WITH targeting_overlay.negative_keywords",
                        "a keyword op beside the same overlay dimension is",
                    ),
                    (
                        "T-UC-026-boundary-neg-kw-remove",
                        "negative_keywords_remove WITH targeting_overlay.negative_keywords",
                        "a keyword op beside the same overlay dimension is",
                    ),
                    (
                        "T-UC-026-partition-kw-add-shared",
                        "conflict_with_overlay",
                        "a keyword op beside the same overlay dimension is",
                    ),
                    (
                        "T-UC-026-partition-kw-remove-shared",
                        "conflict_with_overlay",
                        "a keyword op beside the same overlay dimension is",
                    ),
                    (
                        "T-UC-026-partition-neg-kw-add",
                        "conflict_with_overlay",
                        "a keyword op beside the same overlay dimension is",
                    ),
                    (
                        "T-UC-026-partition-neg-kw-remove",
                        "conflict_with_overlay",
                        "a keyword op beside the same overlay dimension is",
                    ),
                )
            ),
            # Retired: the targeting_overlay replacement rows. 2bab7f2d3 removed them from
            # both outlines because they contradicted the pin (an undeclared `audiences`
            # dimension, and a "full swap" where the pin makes the overlay a per-dimension
            # patch); @T-UC-003-alt-targeting grades the replacement on a declared dimension.
            # budget=0 rejected with BUDGET_TOO_LOW — spec says 0 is valid
            (
                "T-UC-026-partition-required-fields",
                {"budget_zero"},
                "production rejects budget=0 with BUDGET_TOO_LOW — spec allows zero budget",
            ),
            (
                "T-UC-026-boundary-required-fields",
                {"budget = 0"},
                "production rejects budget=0 with BUDGET_TOO_LOW — spec allows zero budget",
            ),
            # Graduated: T-UC-026-partition-format-ids (all 4 transports pass after a3xo)
            # max_bid validation: production requires bid_price for auction-based pricing
            (
                "T-UC-026-partition-pricing-option",
                {"valid_with_max_bid"},
                "max_bid pricing validation rejects valid ceiling semantics — spec-production gap",
            ),
            # FIXME: pricing option not-found / wrong-product returns
            # 'validation_error' instead of AdCP-spec 'INVALID_REQUEST'.
            (
                "T-UC-026-partition-pricing-option",
                {"pricing_option_not_found", "pricing_option_wrong_product"},
                "Production returns 'validation_error' instead of AdCP-spec 'INVALID_REQUEST' — "
                "AdCPValidationError caught and re-raised as plain ValueError, stripping error code",
            ),
            # Immutable: graduated, the last REST row with it.
            #
            # Graduated: [rest-no_immutable_fields_present (salesagent-mx7a8). That row
            # sends packages[0] = {package_id, budget: 8000, pacing: "even"} and the
            # outcome step reads BOTH back off the response package. pacing is now applied
            # — update_media_buy writes the MediaPackage.pacing column that create already
            # dual-writes — and the one affected_packages snapshot per modified package
            # carries the column, so a2a and mcp moved failed->passed and this REST row
            # moved xfail->xpass in the same run. The pass is NOT vacuous: the Then is
            # _assert_requested_package_effects, which derives its assertion from the
            # request and fails BY NAME on any requested field it cannot read back.
            # Fire-probed by reverting the pacing write: the row fails on
            # "Requested pacing='even', package carries None".
            # Graduated: [rest-update_mutable_only (partition) and [rest-update with only
            # mutable fields (boundary) -- innet_291026_0008, passing on a2a, mcp and
            # e2e_rest and xpassing on rest. Both send {package_id, budget: 9000}; the
            # snapshot reports the budget the budget branch applied, and a branch that
            # skipped it reports the stored 5000.
            #
            # Graduated (innet_291026_0008, see § REST update rows above): every REST row of
            # T-UC-026-partition-keyword-add / -keyword-remove, the boundary keyword and
            # negative-keyword outlines (shared and unshared), and the partition shared
            # outlines -- now UNSUPPORTED_FEATURE rows -- and the REST pause/resume rows of
            # T-UC-026-partition-paused and T-UC-026-boundary-paused.
            # d09y: boundary scenarios exposing real production gaps after step-parser fix.
            (
                "T-UC-026-boundary-pricing-option",
                {"empty string", "different product", "max_bid=true", "not in product", "matches last entry"},
                "pricing_option validation returns 'validation_error' instead of AdCP 'INVALID_REQUEST' / "
                "max_bid pricing requires bid_price / last-entry pricing_option rejects valid id — spec-production gap",
            ),
            # Replacement: the REST "existing values preserved" rows, which are routed for their own
            # reason (_UC026_PRESERVATION_UNGRADED). Their a2a, mcp and e2e_rest siblings run
            # unrouted; the Given now seeds catalogs and create persists them, so all four
            # rows grade preservation (see the note above _UC026_SHAPE_ROW_ANSWERED_UNSUPPORTED).
            #
            # Graduated: replace_catalogs on every transport, and the REST siblings
            # [rest-replace_catalogs / [rest-catalogs provided / [rest-only scalar fields
            # updated (salesagent-mx7a8). catalogs is now applied: 3.2
            # media-buy/package-update.json defines it as "Replace the catalogs this
            # package promotes. Uses replacement semantics — the provided array replaces
            # the current list", and update_media_buy writes that whole document through
            # MediaBuyRepository.update_package_config, which the one affected_packages
            # snapshot then reads back. Measured in one serial run over this module's
            # 63 immutable/replacement rows: 29 passed / 8 failed / 22 xfailed / 4 xpassed
            # -> 37 passed / 0 failed / 18 xfailed / 8 xpassed, the four new xpasses being
            # exactly the REST siblings of the four rows that went green.
            #
            # Retired: the creative_assignments and optimization_goals rows. 2bab7f2d3
            # removed them from both outlines because each contradicted the pin (a goal with
            # no `kind`; an assignment to a creative never put in the library); the
            # @T-UC-003-*-optimization-goals and @T-UC-003-*-creative-replacement outlines
            # grade both replacements.
            ("T-UC-026-partition-replacement", {"[rest-omit_array_fields"}, _UC026_PRESERVATION_UNGRADED),
            ("T-UC-026-boundary-replacement", {"[rest-all array fields omitted"}, _UC026_PRESERVATION_UNGRADED),
        ]
        for tag, substrings, reason in _UC026_PARTITION_SELECTIVE:
            if tag in marker_names:
                if not substrings or any(s in nodeid for s in substrings):
                    item.add_marker(pytest.mark.xfail(reason=reason, strict=False))

        # --- UC-011: xfails for spec-production gaps ---
        # FIXME: Production doesn't implement these UC-011 features.
        # Graduated: T-UC-011-list-status-filter payment_required (all 4 transports pass — status now mapped)
        # Graduated: T-UC-011-ext-g-echo list_accounts (all 4 transports pass — context echo implemented)

        # Graduated: no-token/no-principal scenarios now pass after Gherkin
        # correction to AUTH_REQUIRED (commit 13b4ca8d). Production returns
        # AUTH_REQUIRED on rest/e2e_rest, matching the corrected Gherkin.
        # Graduated: expired-token also passes — AUTH_REQUIRED matches.

        # T-UC-011-ext-g-echo-error: impl passes (AdCPSalesAgentError carries context=req.context);
        # a2a/mcp/rest xfail+note via the context-echo Then step (pytest.xfail) because the
        # wire error envelope does not echo context — #1417 / D2. No marker here.
        # Graduated: T-UC-011-sync-missing-brand (all 4 transports pass — ValidationError now structured)
        # Graduated: T-UC-011-sync-missing-operator (all 4 transports pass — ValidationError now structured)
        # Graduated: T-UC-011-ext-f-scoped (all 4 transports now pass — deactivation scoping works on a2a)

        # --- Entity marker auto-application based on BDD tags ---
        # BDD tests don't have entity keywords in filenames; instead they
        # use tags like T-UC-004-* (delivery) and T-UC-005-* (creative).
        if any(t.startswith("T-UC-002") for t in marker_names):
            item.add_marker(pytest.mark.media_buy)
        if any(t.startswith("T-UC-006") for t in marker_names):
            item.add_marker(pytest.mark.creative)
        if any(t.startswith("T-UC-004") for t in marker_names):
            item.add_marker(pytest.mark.delivery)
        if any(t.startswith("T-UC-005") for t in marker_names):
            item.add_marker(pytest.mark.creative)
        if any(t.startswith("T-UC-026") for t in marker_names):
            item.add_marker(pytest.mark.media_buy)
        if any(t.startswith(_ADMIN_TAG_PREFIX) for t in marker_names):
            item.add_marker(pytest.mark.admin)

        # ── E2E_REST ledger + non-strict policy ──────────────────────
        # The e2e_rest transport dispatches over real HTTP to a separate server,
        # so scenarios relying on in-process mock injection can't pass. xfail the
        # known ones (ledger) as non-strict — e2e is environment-dependent, so a
        # ledger xpass must not fail CI. Authored strict=True markers (the #1270
        # validation tripwires at ~1475/~1502) are PRESERVED by the collapse
        # below, so a real production fix still surfaces as a strict xpass.
        if is_e2e_rest:
            if nodeid in _E2E_REST_KNOWN_FAILURES:
                item.add_marker(
                    pytest.mark.xfail(
                        reason="e2e_rest: mock-incompatible scenario (tests/bdd/e2e_rest_known_failures.txt)",
                        strict=False,
                    )
                )
            # Collapse the e2e_rest xfail markers into ONE, but PRESERVE authored
            # strictness: if any source marker is strict=True (the #1270 validation
            # tripwires at ~1475/~1502), the collapsed marker stays strict so a
            # production fix surfaces as a strict xpass instead of being silently
            # swallowed. Ledger-only items carry only non-strict markers, so they
            # stay non-strict — an environment-dependent xpass must not fail CI.
            #
            # ``run`` is carried over for the same reason ``strict`` is, and it was NOT
            # until 2026-10-09. The collapse rebuilt the marker from reason+strict only,
            # so a ``run=False`` park (the one in _UC010_PARKED_ROWS, which exists
            # precisely because those rows would PASS while grading nothing) silently
            # became a RUNNING non-strict xfail on this transport alone — and the two
            # webhook-signing-bounds rows duly executed against the live server and
            # reported XPASS in innet_091026_1343, which is the exact vacuous green the
            # park was written to refuse. Dropping a kwarg in a collapse is not a
            # weaker version of the same routing; it is a different routing.
            xfails = [m for m in item.own_markers if m.name == "xfail"]
            if xfails:
                strict = next((m for m in xfails if m.kwargs.get("strict", False)), None)
                chosen = strict or xfails[0]
                item.own_markers = [m for m in item.own_markers if m.name != "xfail"]
                item.add_marker(
                    pytest.mark.xfail(
                        reason=chosen.kwargs.get("reason", "e2e_rest xfail"),
                        strict=strict is not None,
                        run=all(m.kwargs.get("run", True) for m in xfails),
                    )
                )

    # ── Every strict-xfail leg runs on every wire transport ──────────
    # There is deliberately NO single-transport optimization here. Until
    # 2026-07-30 this block kept ONE mcp/rest "representative" per strict-xfail
    # scenario and deselected the sibling, which had two consequences:
    #
    #   * the representative was whichever variant appeared FIRST in `items`,
    #     and pytest-randomly (active for the bdd env — only `integration`
    #     passes `-p no:randomly`) reshuffles `items` per run, so the surviving
    #     transport was a per-run coin flip (GH #1291 work, 22 UC-010 nodeids
    #     traded mcp<->rest between full runs with the totals conserved);
    #   * transports diverge one at a time in this repo, so a single
    #     representative structurally cannot see a transport-specific
    #     production fix: the XPASS(strict) tripwire simply is not on the
    #     transport that got fixed.
    #
    # The fix is completeness, not a deterministic tie-break: a deterministic
    # representative would have turned an intermittent blind spot into a
    # permanent one. Every strict-xfail scenario now runs on a2a AND mcp AND
    # rest, each with strict=True, so an xpass surfaces on whichever transport
    # production actually fixed. The price is ~341 extra items (4.2% of the BDD
    # suite), all of them strict xfails.
    #
    # Do not reintroduce a keep-one optimization. If runtime ever forces one, it
    # must be expressed as an explicit per-scenario decision, not as an
    # order-dependent accumulator — see
    # tests/unit/test_guards_bdd_strict_xfail_representative.py, which fails on
    # any deselection of a strict-xfail transport leg.
    #
    # The `scope=per-transport` exemption that PR #1941 added to the former
    # deselection loop is SUBSUMED, not dropped: it existed to stop a
    # per-transport obligation being graded on one transport and called covered,
    # and nothing is deselected any more, so every row gets what the exemption
    # was buying. What is NOT subsumed is the vocabulary check that came with
    # it, so it stays below on its own — a reason that ANNOUNCES itself typed
    # and then fails to parse is a declaration nobody validated, and it would
    # have routed by accident under the old loop.
    reason_errors: list[str] = []
    for item in items:
        for marker in item.iter_markers():
            if marker.name != "xfail" or not marker.kwargs.get("strict", False):
                continue
            try:
                parse_xfail_reason(str(marker.kwargs.get("reason", "")))
            except XfailReasonError as exc:
                reason_errors.append(f"{item.nodeid}: {exc}")
    if reason_errors:
        # Collected rather than raised in-loop: an exception escaping
        # pytest_collection_modifyitems surfaces as INTERNALERROR, which reports the
        # hook rather than the malformed reason and truncates the run. Gathering them
        # and failing once at the end names every offender.
        raise pytest.UsageError(
            "malformed typed xfail reason(s) — a reason that opens with a `key=value` run "
            "declares itself typed and must parse:\n  " + "\n  ".join(reason_errors)
        )


# ---------------------------------------------------------------------------
# Core fixtures
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Multi-transport dispatch
# ---------------------------------------------------------------------------
# Tags that indicate a scenario already dispatches through a specific transport.
# These scenarios must NOT be multiplied — they have explicit When steps.
#
# The former ``_CHANNEL_COLUMN_TAGS`` member is GONE with the column that justified
# it: @T-UC-010-auth no longer takes the transport as an Examples value, there is no
# ``when_invoke_via_channel`` step to hard-set ``ctx["transport"]``, and the BR-UC-010
# header records why (auth policy is a property of the tool, not of the channel). A tag
# left here would hold that outline out of transport parametrization for a reason that
# no longer exists.
_TRANSPORT_SPECIFIC_TAGS = {"rest", "mcp", "a2a"}

# Scenarios whose graded production is reachable on ONE wire transport only.
#
# @a2a_untyped_ingest: the two surviving scenarios are A2A PROTOCOL-ENVELOPE
# surfaces — the ``message/send`` push config — which has no counterpart on MCP
# or REST at all. That, and only that, is what makes them single-transport.
#
# A tool-surface scenario does NOT belong here. Every transport reports the ABSOLUTE field
# path (e.g. ``push_notification_config.authentication.credentials``), not one relative to
# the sub-model it validated, so a tool-surface scenario runs on all four transports and
# their agreement is standing executable proof rather than a claim in a comment.
#
# The tag NAME is a misnomer — neither scenario is an untyped ingest. It is
# left for the rename that owns the registry.
#
# PARAMETRIZED on that one transport rather than dropped from parametrization:
# an excluded transport is exactly as ungraded as an xfail but invisible to both
# escape-hatch detectors (GH #1892), whereas this keeps a real ``[a2a]`` test id
# that ``--collect-only`` shows.
_SINGLE_TRANSPORT_TAGS = {
    "a2a_untyped_ingest": "A2A",
    # local-pre-dispatch-refusals.feature: the refused shape is the transport's own frame,
    # so each scenario names the one transport whose frame it sends.
    "predispatch-rest": "REST",
    "predispatch-a2a": "A2A",
    "predispatch-mcp": "MCP",
}

# UC + tag combinations that should run IMPL-only (no 4-way parametrization).
# EMPTY, and it stays empty: BDD grades the wire, so every scenario parametrizes across
# a2a/mcp/rest (#1417).
_IMPL_ONLY: set[tuple[str, str]] = set()

# UC-002 idempotency scenarios wired to MediaBuyCreateEnv (run a real
# create_media_buy across all 4 transports). Only these two @idempotency-key
# tags are live; the rest stay blanket-xfailed in _harness_env until their
# production gaps + steps are wired.
_UC002_IDEMPOTENCY_WIRED: set[str] = {
    "T-UC-002-v31-idempotency-replay",
    "T-UC-002-v31-idempotency-missing",
}

# UC-002 manual-approval scenario wired to MediaBuyCreateEnv (PR #1567 round-2 item 2):
# grades the spec-3.1.1 CreateMediaBuySubmitted envelope (status="submitted" +
# task_id, no media_buy_id/confirmed_at/revision) across all 4 transports —
# the create mirror of the BR-UC-003 wiring (1b2f03bc9). Other @alt-manual
# scenarios (reject/approve flows) stay dormant until their steps are wired.
_UC002_MANUAL_APPROVAL_WIRED: set[str] = {
    "T-UC-002-alt-manual",
}

#: BR-CODES-001 — a declared error code reaches the buyer unrewritten. Needs the same
#: FULL create-through-the-wire dispatch as the manual-approval scenarios, but is not a
#: manual-approval scenario, so it gets its own set rather than overloading that name.
_UC002_FULL_CREATE_WIRED: set[str] = {
    "T-CODES-001-platform-code-reaches-buyer",
    # BR-CODES-002's bare-raise scenario deliberately reuses that same rejection path:
    # it is the cheapest BARE, non-auth raise site already wired to all four transports.
    "T-CODES-002-suggestion-appears-on-a-bare-raise",
}

# The v3.1 sync-success envelope scenario. It was dormant because it had no step
# definitions, not because the harness could not reach it — it needs exactly the full
# create the manual-approval branch already runs. It grades revision / confirmed_at /
# valid_actions on the response the buyer meets first, which is the surface where
# those three were being fabricated from schema defaults.
_UC002_V31_SUCCESS_WIRED: set[str] = {
    "T-UC-002-v31-success-revision-and-actions",
}


# Admin scenarios have their own transport (Flask test_client / requests.Session).
# They must NOT be parametrized across MCP/A2A/REST/IMPL API transports.
_ADMIN_TAG_PREFIX = "T-ADMIN-"

# (Deleted) A one-tag exemption, "T-UC-010-auth", held the capabilities auth outline out of
# transport parametrization because its <channel> column supplied the transport instead.
# That column is gone: an outline that takes the transport as DATA can grade one transport
# differently from another, and that one did -- A2A AUTH_INVALID where MCP and REST said
# success. The scenario names no transport now and is parametrized like every other, so a
# per-transport answer is unwritable in it.


def _parametrize_ctx(
    metafunc: pytest.Metafunc,
    base_transports: Sequence[Any],
    e2e_members: Sequence[Any],
) -> None:
    """Parametrize ``ctx`` over the in-process transports, plus the e2e ones when enabled.

    Extracted so the AdCP branch and the admin branch share ONE copy of the
    append-e2e-when-enabled tail. Duplicating it would be the
    same logical operation with substituted enum members — the R0801 shape the
    DRY invariant treats as a defect, against a duplication baseline that may
    only shrink.

    A SEQUENCE of e2e members, not one. The single-member signature is why the suite
    graded four transports rather than six: ``McpE2EDispatcher`` and ``A2AE2EDispatcher``
    have been built and registered in ``DISPATCHERS`` since #1858, and nothing here could
    name them, so ``E2E_MCP`` and ``E2E_A2A`` appeared nowhere under ``tests/bdd/``. The
    limit was the parameter, not the harness. The admin branch passes a one-element
    sequence and is unchanged in behaviour.

    The pytest ids are DERIVED, not passed. Both transport enums are ``StrEnum``\\ s whose
    value IS the id — ``Transport.E2E_MCP`` is ``"e2e_mcp"``, ``AdminTransport.E2E`` is
    ``"e2e_admin"`` — so a parallel list of strings restated what the members already
    carry and could disagree with them. That disagreement would not be cosmetic: the id is
    what ``tox.ini``'s ``-k "e2e_rest or e2e_mcp or ..."`` matches on, so a typo'd or
    forgotten id collects a transport that no env ever selects, which is precisely the
    "dies dormant while CI stays green" failure that selector's own comment warns about.
    """
    transports = list(base_transports)
    if e2e_members and os.environ.get("BDD_E2E_ENABLED") == "true":
        transports.extend(e2e_members)
    metafunc.parametrize("ctx", transports, ids=[t.value for t in transports], indirect=True)


#: What a dormant UC-010 tag says when nothing more specific is known about it: the
#: harness was never extended to it, so it has never been graded. One definition, used
#: by the per-tag rows and by the catch-all, so the two cannot drift into saying
#: different things about the same state. It carries NO issue number — the citation is
#: appended per tag from ``_UC010_DORMANT_TRACKING``.
_UC010_DORMANT_REASON = "UC-010 harness wiring not extended to this tag (dormant, never graded)"

#: Per-tag tracking issue for the dormant UC-010 scenarios.
#:
#: There was ONE shared reason string here, citing #1855 for all 33 dormant T-UC-010-*
#: tags. It was right for the media_buy presence-object cluster and wrong for everything
#: else, and because it was a single hardcoded fallback rather than a per-tag reason,
#: neither stale-citation guard could see it (they read .feature comments and _XFAIL_TAGS,
#: not this branch). Swapping it to #1291 would only have inverted the defect onto the tags
#: #1855 genuinely homes (#1721 review F2).
#:
#: Every entry was checked with `gh issue view` against the scenario it labels. A tag with
#: no ESTABLISHED home is deliberately ABSENT rather than guessed: a citation-free reason is
#: honest, an invented one is the defect this map exists to remove.
_UC010_DORMANT_TRACKING: dict[str, str] = {
    # RFC 9421 signing + agent key lifecycle. #1291's title scopes it to "inbound, outbound
    # and key lifecycle"; the in-file _SELECTIVE_XFAIL entries already cite #1291 for
    # webhook_signing, so this keeps the file internally consistent.
    #
    # The FOUR main-flow signing tags that used to head this map are gone from it, because
    # this map may only SHRINK as batches land and theirs landed: request_signing is a real
    # tenant declaration and webhook_signing is realized as platform state, so they sit in
    # _uc010_wired_tags()'s literal instead. Leaving them in BOTH places is the failure mode
    # the shrink rule exists to prevent -- the wired set says "graded", the dormancy row says
    # "xfail, not yet wired", and the non-strict xfail wins, so four scenarios whose capability
    # exists would have gone on reporting as expected failures with nothing red.
    "T-UC-010-v31-identity-brand-json-url": "#1291",
    "T-UC-010-v31-identity-key-origins": "#1291",
    "T-UC-010-v31-identity-compromise-notification": "#1291",
    "T-UC-010-v31-agent-signing-key-bounds": "#1291",
    "T-UC-010-v31-agent-encryption-key-bounds": "#1291",
    # media_buy presence-object sections. #1855's body enumerates these by name, including
    # media_buy.content_standards -- which the old blanket citation got right by accident
    # and a naive #1855 -> #1291 swap would have got wrong.
    "T-UC-010-v31-governance-aware": "#1855",
    "T-UC-010-v31-vendor-metric-optimization": "#1855",
    "T-UC-010-v31-content-standards-block": "#1855",
    # Capability surfaces excluded from declaration under the strict policy. #1724 names
    # adapter creative_specs and generative creative; conftest already cites it in-file for
    # the specialism tags.
    "T-UC-010-v31-creative-specs": "#1724",
}


def _uc010_wired_tags() -> frozenset[str]:
    """The UC-010 tags whose step batch has landed, so CapabilitiesEnv serves them.

    get_adcp_capabilities wiring lands in BATCHES: only tag families whose steps
    exist pay ``integration_db`` + env setup; every other ``T-UC-010-*`` tag is
    routed to its own dormancy row (built from ``_UC010_DORMANT_TRACKING`` below)
    and xfails fast, citing that tag's OWN tracking issue. The set SHRINKS as
    batches land — a tag added here must be deleted from the tracking map, which
    ``tests/unit/test_architecture_uc010_dormancy_citations.py`` enforces (it
    reads this literal, so keep the name and the set literal here).
    """
    _UC010_WIRED_TAGS = frozenset(
        {
            # Batch 1 — envelope + account families
            "T-UC-010-main",
            # Split out of T-UC-010-main (#1721); wired by the same steps, so it
            # must join the wired set or it would xfail as "not yet wired" rather
            # than for its real, cited reason (#1291).
            "T-UC-010-main-reporting-delivery",
            "T-UC-010-degradation-no-cascade",
            "T-UC-010-main-timestamp",
            "T-UC-010-main-readonly",
            "T-UC-010-pricing",
            "T-UC-010-audience-caps",
            "T-UC-010-conversion-caps",
            "T-UC-010-creative-caps",
            "T-UC-010-ext-b-schema-valid",
            "T-UC-010-ext-a",
            "T-UC-010-account-require-operator-auth",
            "T-UC-010-account-authorization-endpoint",
            "T-UC-010-account-required-for-products",
            "T-UC-010-account-supported-billing",
            "T-UC-010-account-financials-declaration",
            "T-UC-010-account-block-presence",
            "T-UC-010-degradation-account",
            "T-UC-010-features-partitions",
            "T-UC-010-auth",
            "T-UC-010-auth-data-identity",
            "T-UC-010-ext-c-a2a",
            "T-UC-010-ext-c-mcp",
            "T-UC-010-ext-e-echo",
            "T-UC-010-ext-e-absent",
            "T-UC-010-ext-e-nested",
            "T-UC-010-ext-e-empty",
            "T-UC-010-ext-d-filter",
            "T-UC-010-ext-d-all-protocols",
            "T-UC-010-ext-d-invalid-value",
            "T-UC-010-ext-d-empty",
            "T-UC-010-v31-supported-versions",
            # The other half of the same version-negotiation storyboard step: the
            # advertisement rides in the body, the echo on the envelope. Wired with the
            # sibling because it needs no setup the sibling does not already have.
            "T-UC-010-v31-adcp-version-echo",
            "T-UC-010-v31-version-unsupported",
            "T-UC-010-v31-version-unsupported-major-fallback",
            "T-UC-010-v31-version-unsupported-build-version-advisory",
            # Batch 3 — degradation-sections + channel-all-canonical
            "T-UC-010-degradation-sections",
            "T-UC-010-channel-all-canonical",
            # Wired when their Thens were corrected to the pin: the channel outline seeds the
            # catalog production reads, and the protocols scenario reads its enum from the
            # pin; both need only setup the env already has.
            "T-UC-010-channel-mapping",
            "T-UC-010-v31-supported-protocols-extended",
            # Batch 4 — features / targeting / idempotency-required
            "T-UC-010-features",
            "T-UC-010-targeting",
            "T-UC-010-targeting-partitions",
            "T-UC-010-degradation-partitions",
            "T-UC-010-v31-idempotency-required",
            # Batch 5 — v3.1 signing / brand / reporting / measurement
            "T-UC-010-v31-reporting-delivery-methods",
            "T-UC-010-v31-brand-block",
            "T-UC-010-v31-webhook-signing-required-when",
            "T-UC-010-v31-identity-required-when-signing",
            "T-UC-010-v31-measurement-catalog",
            # Batch 6 — compliance_testing / specialisms / advisory errors
            "T-UC-010-v31-compliance-testing",
            "T-UC-010-v31-specialisms",
            "T-UC-010-v31-advisory-errors",
            # Batch 7 — bounds / monotonicity outlines
            "T-UC-010-v31-request-signing-monotonicity",
            "T-UC-010-v31-idempotency-ttl-bounds",
            "T-UC-010-v31-version-unsupported-details-bounds",
            "T-UC-010-v31-identity-brand-json-url-bounds",
            # Batch 8 — webhook-signing bounds outline
            "T-UC-010-v31-webhook-signing-bounds",
            # Batch 9 — version negotiation + idempotency posture
            "T-UC-010-v31-idempotency-supported",
            "T-UC-010-v31-idempotency-in-flight-bound",
            # Batch 10 — creative_approval_mode (tenant.approval_mode)
            "T-UC-010-v31-creative-approval-mode",
            # Batch 11 — trusted_match surfaces
            "T-UC-010-v31-trusted-match-surfaces",
            # Batch 12 — measurement accreditations
            "T-UC-010-v31-measurement-accreditations",
            # Batch 13 — locally-added declaration-backing graders.
            # These grade validate_backing()'s rejection rules, which the generated
            # specialisms scenario cannot: it declares only the claims this seller backs.
            # local-inline-creative-management grades the inline_creative_management
            # declaration against the create and update paths that honor it.
            "T-UC-010-local-backed-specialism",
            "T-UC-010-local-unbacked-specialism",
            "T-UC-010-local-orphaned-specialism",
            "T-UC-010-local-unbacked-protocol",
            "T-UC-010-local-inline-creative-management",
            # Batch 14 — account.sandbox boundary outline (#1721 M4). Was dormant
            # (no bound Given for "the tenant account is configured for
            # {boundary_point}"), citing #1855 (generic wiring) instead of the
            # accurate #1856 (account-config surface) -- both fixed.
            "T-UC-010-v31-account-sandbox",
            # Batch 15 — request-ext acceptance (#1721 lane D / ).
            # Authored as the grader for adding `ext` to the get_adcp_capabilities
            # MCP wrapper, get_adcp_capabilities_raw and the REST body: the request
            # schema declares core/ext.json, so a vendor-namespaced ext must be
            # served the normal response on every transport.
            "T-UC-010-ext-request-vendor-namespaced",
            # Batch 16 — the signing family's MAIN-FLOW scenarios (#1291 D1). These
            # were dormant TWICE over: their Givens had no step definition anywhere
            # (which pytest_runtest_makereport converts to xfail) AND their tags were
            # absent from this set (which xfails at fixture setup, before a single step
            # runs). Both halves are fixed on this tree: the Givens are bound in
            # tests/bdd/steps/domain/uc010_capabilities.py, `request_signing` is a real
            # tenant declaration and `webhook_signing` is realized as platform state.
            # They are correspondingly absent from _UC010_DORMANT_TRACKING, which is
            # where they used to be cited.
            "T-UC-010-v31-request-signing-posture",
            # Split out of T-UC-010-v31-request-signing-posture: under 3.2 a
            # covers_content_digest of `either` or `forbidden` is not an emission this
            # seller may make, so the two rows that used to assert it moved to a scenario
            # that grades the refusal. Wired by the SAME steps as the posture outline it
            # left, so it must join this set or it would xfail as "not yet wired" rather
            # than run.
            "T-UC-010-v31-request-signing-legacy-digest",
            "T-UC-010-v31-request-signing-subset",
            "T-UC-010-v31-webhook-signing",
        }
    )
    return _UC010_WIRED_TAGS


def _uc010_dormancy_rows() -> list[EnvRoute]:
    """One row per dormant UC-010 tag, each citing that tag's OWN tracking issue.

    There was ONE shared reason string for all 33 dormant tags, citing #1855 for
    every one of them — right for the media_buy presence-object cluster, wrong
    for the signing, identity and unbacked-capability clusters, and invisible to
    both stale-citation guards because it was a hardcoded fallback rather than a
    per-tag reason (#1721 review F2). A citation that is plausible but wrong is
    worse than none: it reads as tracked work, so nobody re-checks it.

    Rows, not an inline branch: a marker-set predicate inside the routing
    fixture is exactly what the ENV_ROUTES registry replaced, and a row is
    visible to ``scripts/audit``'s join, which resolves the same table.

    A tag that ``_UC010_PARKED_TAGS`` describes gets THAT text in place of the
    generic dormancy sentence, plus the same citation: "un-got-to" and "cannot be
    wired honestly yet" are different states, and the park is the only place the
    difference is written down. The citation still comes from the map alone, so a
    parked narrative may not name an issue of its own — it would contradict the
    map for its tag, which ``test_architecture_uc010_dormancy_citations.py``
    fails on.
    """
    rows = [
        EnvRoute(
            tag=f"uc010-dormant-{tag}",
            when=(lambda dormant: lambda m: dormant in m)(tag),
            env_builder=_build_capabilities_env,
            xfail_reason=f"{_UC010_PARKED_TAGS.get(tag, _UC010_DORMANT_REASON)} — tracked by {issue}",
        )
        for tag, issue in sorted(_UC010_DORMANT_TRACKING.items())
    ]
    # A dormant tag with no ESTABLISHED tracking home is deliberately absent from
    # the map — a citation-free reason is honest, an invented one is the defect
    # the map exists to remove — so it lands here, on the same catch-all shape
    # every other branch UC carries.
    rows.append(
        EnvRoute(
            tag="uc010-not-wired",
            when=_uc("UC-010", lambda m: True),
            env_builder=_build_capabilities_env,
            xfail_reason=_UC010_DORMANT_REASON,
        )
    )
    return rows


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Parametrize BDD scenarios across the wire transports (a2a/mcp/rest).

    The IMPL transport was dropped from the BDD default parametrization
    (#1417): BDD asserts AdCP *wire* conformance only. IMPL/call_impl
    remain available for unit/integration tests via the harness; they are simply
    not auto-parametrized here.

    Scenarios tagged with @rest, @mcp, or @a2a are transport-specific
    and skip parametrization — they already dispatch through their
    explicit transport in the When step.

    Uses ``ctx`` as the parametrize target (indirect) so every scenario
    gets a fresh dict with ``ctx["transport"]`` set to the Transport enum.
    """
    if "ctx" not in metafunc.fixturenames:
        return

    from tests.harness.transport import Transport

    marker_names = {m.name for m in metafunc.definition.iter_markers()}
    if marker_names & _TRANSPORT_SPECIFIC_TAGS:
        # Transport-specific scenario — don't multiply: its When step names the
        # transport itself.
        return

    # Single-transport scenarios still get a real (one-element) parametrization,
    # so the transport that grades them is visible at collection. See
    # _SINGLE_TRANSPORT_TAGS.
    single = marker_names & _SINGLE_TRANSPORT_TAGS.keys()
    if single:
        transport = Transport[_SINGLE_TRANSPORT_TAGS[next(iter(single))]]
        metafunc.parametrize("ctx", [transport], ids=[transport.value], indirect=True)
        return

    # Admin scenarios are not AdCP tool surfaces (no a2a/mcp/rest/e2e_rest), but
    # they DO have two transports of their own, both declared in
    # BR-ADMIN-ACCOUNTS.feature's header and both implemented by AdminAccountEnv.
    # Parametrize over them here so the transport is chosen at collection time
    # rather than pinned inside the harness.
    if any(t.startswith(_ADMIN_TAG_PREFIX) for t in marker_names):
        from tests.harness.admin_accounts import AdminTransport

        _parametrize_ctx(metafunc, [AdminTransport.INTEGRATION], [AdminTransport.E2E])
        return

    # IMPL-only scenarios: harness has no transport wrappers for this path
    for uc_prefix, required_tag in _IMPL_ONLY:
        tag_prefix = f"T-{uc_prefix}-"
        if any(t.startswith(tag_prefix) for t in marker_names) and required_tag in marker_names:
            return

    # IMPL sunsetted: it adds no coverage the wire transports don't, and it has no
    # wire envelope (so it can't participate in error-envelope assertions). The six
    # truthful transports are a2a/mcp/rest in process, plus e2e_rest/e2e_mcp/e2e_a2a
    # over real HTTP (added below when enabled).
    transports = [Transport.A2A, Transport.MCP, Transport.REST]

    # EVERY tool is reachable on EVERY transport, so no scenario is withheld from one.
    # A per-UC exclusion for tools with no REST route, driven by a hand-maintained
    # tag-prefix tuple, would be a mistake in two ways at once.
    #
    # It cannot fire. A tool's reachability is the registry's answer, not a tag's: MCP
    # registration, the A2A card and the REST route are all generated from the ToolSpec
    # row, and all 14 rows carry a RestBinding. A tuple of tag prefixes restating that is
    # a second declaration of a fact one place already owns, free to drift from it.
    #
    # And dropping a transport at collection is the worst way to express even a true gap:
    # it is exactly as ungraded as an xfail, but INVISIBLE to both escape-hatch detectors
    # in test_architecture_e2e_rest_escape_hatches.py, which walk xfail conditions and
    # E2EUnsupportedSetup sites and never see a scenario that was never parametrized.
    # That is what tests/unit/test_e2e_rest_ssrf_blocked_scenario_collected.py pins.
    #
    # A tool that genuinely lost a wrapper is a PRODUCTION gap. Add the route.
    # e2e_mcp and e2e_a2a are OPT-IN, and that is a capacity decision rather than a
    # correctness one. Their dispatchers have existed since #1858 and nothing named them,
    # so turning them on for every scenario adds ~1600 in-network variants at once: the
    # bdd_e2e selection goes 2864 -> 8566 against ONE shared server and ONE /adcp database,
    # while tox -p runs the other suites alongside it. Measured consequence of doing that
    # unconditionally (run sa-47b58c6f): an xdist worker died with
    # `KeyError: <WorkerController gw20>`, the unit suite spent 25 minutes to run 714 tests
    # and ended INTERNALERROR, and suites that touch none of this — admin, e2e — failed
    # too. A saturated box manufactures failures that look like defects.
    #
    # So they are enabled per-run by BDD_E2E_TRANSPORTS=all, which is how the rollout
    # is meant to go: turn them on for one feature, classify what breaks
    # as harness gap versus real transport defect, and only then widen. Nothing about the
    # collection logic differs — the same six ids appear the moment the variable is set.
    e2e_members = [Transport.E2E_REST]
    if os.environ.get("BDD_E2E_TRANSPORTS") == "all":
        e2e_members += [Transport.E2E_MCP, Transport.E2E_A2A]
    _parametrize_ctx(metafunc, transports, e2e_members)


def _ssl_failure(exc: BaseException | None, depth: int = 0) -> ssl.SSLError | None:
    """The ``ssl.SSLError`` reachable from *exc*, walking the exception chain.

    httpx does not surface a certificate failure as an ``ssl`` exception: it
    raises ``httpx.ConnectError`` **wrapping** one, which is indistinguishable
    from "connection refused" by type alone. The chain is where the difference
    lives, so that is where the probe looks. Depth-bounded — a malformed chain
    must not hang the probe.
    """
    if exc is None or depth > 20:
        return None
    if isinstance(exc, ssl.SSLError):
        return exc
    return _ssl_failure(exc.__cause__ or exc.__context__, depth + 1)


def _probe_verify(base_url: str, ca_bundle: str | None) -> dict[str, object]:
    """``verify=`` kwargs for the health probe: the generated CA, when there is one.

    Only for an https base URL, and only when the bundle is really on disk — a
    missing file must reach the handshake and be reported as the TLS failure it
    is, not raise a ``FileNotFoundError`` from context construction that would
    read as a probe bug.
    """
    if not base_url.startswith("https://") or not ca_bundle or not Path(ca_bundle).is_file():
        return {}
    return {"verify": ssl.create_default_context(cafile=ca_bundle)}


@pytest.fixture(scope="session")
def e2e_stack():
    """Detect the live E2E stack; return an E2EConfig or None (never skips here).

    Reads E2E_BASE_URL / E2E_POSTGRES_URL (set by the in-network runner /
    run_all_tests via tox pass_env). Health-checks base_url so non-e2e transports
    still run when the stack is absent (returns None). For an e2e_* transport a
    None here is a hard ERROR (the ctx fixture raises) — never a skip, because
    e2e_* is only parametrized when BDD_E2E_ENABLED=true, so a missing stack means
    an explicitly-requested transport could not run. The RestE2EDispatcher reads
    config off the env, never the environment.
    """
    import httpx

    from tests.harness.transport import E2EConfig

    base_url = os.environ.get("E2E_BASE_URL")
    postgres_url = os.environ.get("E2E_POSTGRES_URL")

    # Phase B: per-worker e2e stacks. With E2E_PER_WORKER=1 under xdist, each
    # worker (PYTEST_XDIST_WORKER="gwN") targets its OWN server container
    # (network alias "server-gwN", port 8080) and its OWN database (adcp_gwN),
    # provisioned by run_all_tests.sh — so e2e_rest runs in parallel with no
    # shared-server/shared-DB contention. Falls back to the shared stack when off.
    ca_bundle = os.environ.get("E2E_CA_BUNDLE")
    tls_base_url = os.environ.get("E2E_TLS_BASE_URL")
    worker = os.environ.get("PYTEST_XDIST_WORKER")  # e.g. "gw3"
    if os.environ.get("E2E_PER_WORKER") == "1" and worker and worker.startswith("gw"):
        import re

        # Server containers are named "<project>-server-gwN" (globally-unique so
        # parallel worktrees don't collide) and reachable by that name on the
        # compose network. Hit the server directly on :8080 (SKIP_NGINX).
        proj = os.environ.get("COMPOSE_PROJECT_NAME", "")
        prefix = f"{proj}-" if proj else ""
        base_url = f"http://{prefix}server-{worker}:8080"
        # Each worker's TLS sidecar carries its own DOTTED CONTAINER NAME for the
        # same reason — `docker compose run` cannot give it a network alias.
        if tls_base_url:
            tls_base_url = f"https://{prefix}tls-{worker}.adcp.test:8443"
        if postgres_url:
            # swap the database name in the URL path -> adcp_<worker>
            postgres_url = re.sub(r"/[^/?]+(\?|$)", rf"/adcp_{worker}\1", postgres_url, count=1)

    if not base_url:
        return None

    probe_url = f"{base_url}/health"
    try:
        resp = httpx.get(probe_url, timeout=5, **_probe_verify(base_url, ca_bundle))
        resp.raise_for_status()
    except Exception as exc:
        # THREE outcomes, and collapsing any two of them is a defect:
        #   * a TLS/certificate failure is a BROKEN RIG -> raise. Reporting it as
        #     "absent" would hand back the plaintext config below and let an https
        #     scenario grade the http branch while reporting green — the exact
        #     vacuity #1291's TLS front exists to remove.
        #   * a transport/HTTP failure means nothing is listening -> None, so the
        #     in-process transports still run on a machine with no Docker stack.
        #   * anything else is a bug in this probe or in httpx -> propagate. A
        #     bare `except Exception: return None` classified those as "no stack".
        if _ssl_failure(exc) is not None:
            raise RuntimeError(
                f"TLS verification FAILED probing the e2e stack at {probe_url} "
                f"(E2E_CA_BUNDLE={ca_bundle!r}). A certificate failure is a broken test rig, not an "
                f"absent stack: reporting it as absent would silently fall back to the plaintext "
                f"config and grade an https scenario on the http branch."
            ) from exc
        if isinstance(exc, httpx.TransportError | httpx.HTTPStatusError):
            return None
        raise

    if not postgres_url:
        postgres_url = (
            f"postgresql://adcp_user:secure_password_change_me@localhost:{os.environ.get('POSTGRES_PORT', '5435')}/adcp"
        )
    return E2EConfig(
        base_url=base_url,
        postgres_url=postgres_url,
        tls_base_url=tls_base_url,
        ca_bundle=ca_bundle,
    )


# Every sequence OWNED BY a column of a public table, i.e. exactly the set
# ``TRUNCATE ... RESTART IDENTITY`` would have restarted. ``setval(seq, 1, false)``
# leaves is_called false, so the next ``nextval`` returns 1 -- identical end state.
_E2E_RESTART_IDENTITY_SQL = (
    "SELECT setval(s.oid::regclass, 1, false) "
    "FROM pg_class s "
    "JOIN pg_namespace n ON n.oid = s.relnamespace "
    "JOIN pg_depend d ON d.classid = 'pg_class'::regclass AND d.objid = s.oid "
    "  AND d.refclassid = 'pg_class'::regclass AND d.deptype = 'a' "
    "WHERE s.relkind = 'S' AND n.nspname = 'public'"
)


#: The tables the live server's concurrent writers take KEY SHARE row locks on, in the order
#: they take them, which the reset deletes from FIRST and in this same order. The delivery
#: scheduler's ``webhook_delivery_log`` INSERT checks its foreign keys in the order its
#: migration declared them (``1759f70fc76a``): tenants, then principals, then media_buys.
#: Two transactions that take the same rows in the same order cannot wait on each other in
#: a cycle, whichever one starts first.
_E2E_RESET_FIRST = ("tenants", "principals", "media_buys")


def _reset_e2e_db(e2e_config) -> None:
    """Flush the live server DB to a clean baseline before an e2e scenario.

    Live-server e2e shares ONE database and the server process commits
    independently, so the transaction-rollback isolation the in-process
    transports get (via the per-test integration_db) is impossible here. Instead
    empty every data table so each scenario's harness setup recreates exactly the
    rows it needs into a clean DB. The server reads the DB live, so it observes
    the reset immediately. alembic_version is preserved (schema stays).

    DELETE rather than TRUNCATE, and the lock mode is the whole point.
    TRUNCATE takes an AccessExclusiveLock on every table it names, one relation
    at a time, in whatever order ``pg_tables`` returned them. The server running
    against this same database sweeps it from background schedulers --
    ``delivery_webhook_scheduler`` every DELIVERY_WEBHOOK_INTERVAL (5s under
    run_all_tests.sh) and ``media_buy_status_scheduler`` every 60s -- and each
    sweep reads ``media_buys`` FIRST and a second table (webhook_delivery_log,
    creative_assignments, creatives) LATER in the SAME transaction, taking an
    AccessShareLock on each. AccessShareLock and AccessExclusiveLock conflict, the
    two orders are opposite, and neither side knows about the other: a textbook
    ABBA cycle. Postgres broke it by killing whichever party it picked, which
    surfaced as one rotating ``DeadlockDetected`` per full in-network run, always
    in scenario SETUP and never on an assertion (#2048).

    DELETE takes a RowExclusiveLock, which does not conflict with AccessShareLock
    at all, so that relation-lock cycle has nowhere to form. A ROW-lock cycle is
    left, with a concurrent writer rather than a reader. The delivery scheduler's
    ``webhook_delivery_log`` INSERT takes KEY SHARE locks on the rows its foreign
    keys name -- tenants, then principals, then media_buys. A reset that deleted in
    ``pg_tables`` order took principals long before tenants, so each side could hold
    the row the other waited on, and Postgres killed whichever side's deadlock check
    ran first -- the server's delivery-log write, or the reset in scenario SETUP
    (innet_291026_0008). That order is the catalog's physical order, which moves
    whenever a migration rewrites a table's ``pg_class`` row, so nothing held it
    still. The reset therefore deletes ``_E2E_RESET_FIRST`` before anything else, in
    the INSERT's order. If the INSERT holds tenants, the reset waits holding nothing
    the INSERT needs and deletes the committed log row with the rest; if the reset
    holds tenants first, the INSERT waits, then fails its foreign-key check, and
    ``ProtocolWebhookService._conclude`` swallows that without touching the scenario
    (since #2328 it does not trip the server's process-wide fail-fast in
    ``get_db_session`` either). Do NOT "fix" a recurrence by retrying or by
    serialising the suite -- both leave a cycle in place; a new writer that takes
    these rows in another order belongs in the ordering.

    Emptying every table makes the delete order irrelevant to the end state, so FK
    triggers are suppressed for the transaction (``session_replication_role =
    replica``, SET LOCAL so it reverts at COMMIT) instead of topologically sorting
    40+ tables -- that is the property ``CASCADE`` was supplying. It needs a superuser, which
    the e2e Postgres role already is (run_all_tests.sh calls pg_terminate_backend
    on other backends with it); if that ever stops being true this raises loudly
    rather than silently leaving rows behind.
    """
    from sqlalchemy import create_engine, text

    engine = create_engine(e2e_config.postgres_url)
    try:
        with engine.begin() as conn:
            tables = [
                row[0]
                for row in conn.execute(
                    text(
                        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
                    )
                )
            ]
            if tables:
                ordered = [t for t in _E2E_RESET_FIRST if t in tables]
                ordered += sorted(t for t in tables if t not in _E2E_RESET_FIRST)
                statements = ["SET LOCAL session_replication_role = replica"]
                statements += [f'DELETE FROM "{t}"' for t in ordered]
                conn.exec_driver_sql("; ".join(statements))
                conn.exec_driver_sql(_E2E_RESTART_IDENTITY_SQL)
    finally:
        engine.dispose()


@pytest.fixture()
def ctx(request: pytest.FixtureRequest, e2e_stack) -> Generator[dict, None, None]:
    """Per-scenario mutable context shared across Given/When/Then steps.

    When parametrized by pytest_generate_tests, ``request.param`` is a
    Transport enum injected as ctx["transport"]. Transport-specific
    scenarios (tagged @rest/@mcp/@a2a) are NOT parametrized and get
    an empty ctx (When steps handle dispatch explicitly).

    For an e2e_* transport, stash the live-stack E2EConfig in ctx so
    ``_harness_env`` passes it to the harness env (which then binds factories to
    the server's DB and dispatches over real HTTP). Skip if the stack is absent.
    """
    d: dict = {}
    if hasattr(request, "param"):
        d["transport"] = request.param
        t = request.param
        if hasattr(t, "value") and str(t.value).startswith("e2e_"):
            if e2e_stack is None:
                # e2e_* transports are only parametrized when BDD_E2E_ENABLED=true
                # (see pytest_generate_tests), so reaching here means e2e was
                # EXPLICITLY requested but the live stack could not be reached. That
                # is a hard ERROR, never a skip: a skipped e2e test masks the fact
                # that the transport never ran, turning a non-executed test into a
                # false green (No Quiet Failures / Test Integrity).
                base_url = os.environ.get("E2E_BASE_URL")
                cause = "E2E_BASE_URL is unset" if not base_url else f"{base_url}/health failed"
                raise RuntimeError(
                    f"BDD_E2E_ENABLED=true but the live E2E stack is unreachable ({cause}). "
                    "The e2e_rest transport cannot run. Start the in-network stack "
                    "(run_all_tests.sh) or unset BDD_E2E_ENABLED to run the in-process "
                    "transports only. Refusing to skip — a skipped e2e test is a false green."
                )
            d["e2e_config"] = e2e_stack
    try:
        yield d
    finally:
        # Stop any step-level patchers stashed on ctx (e.g. given_today_is patches
        # src.core.tools.media_buy_list.datetime; snapshot/adapter steps patch
        # get_adapter). These use patch().start() and are NOT tracked by the
        # harness's context-managed EXTERNAL_PATCHES, so without this teardown they
        # leak the module patch into later scenarios in the same worker — an
        # order-dependent contamination (masked today only by the wide factory
        # flight window). Stop in reverse (LIFO) and ignore already-stopped.
        for patcher in reversed(d.get("_patchers", [])):
            try:
                patcher.stop()
            except RuntimeError:
                pass  # already stopped


def _setup_existing_media_buy(ctx: dict, env: object, tenant: object, principal: object, product: object) -> None:
    """Create an existing media buy + package for UC-003 update scenarios.

    Seeds the database with a committed media buy and one package, then
    stores references in ctx so Given/When/Then steps can find them.
    Also registers the package label mapping for Gherkin "pkg_001".
    """
    from datetime import UTC, datetime, timedelta

    from tests.factories import MediaBuyFactory, MediaPackageFactory

    mb = MediaBuyFactory(
        tenant=tenant,
        principal=principal,
        status="pending_approval",
        currency="USD",
        start_time=datetime.now(UTC),
        end_time=datetime.now(UTC) + timedelta(days=30),
    )
    pkg = MediaPackageFactory(
        media_buy=mb,
        package_config={
            "package_id": "pkg_001",
            "product_id": product.product_id,
            "budget": 5000.0,
        },
    )
    env._commit_factory_data()
    ctx["existing_media_buy"] = mb
    ctx["existing_package"] = pkg
    # Register Gherkin label → real package_id mapping (see uc003 _register_package)
    from tests.bdd.steps.domain.uc003_update_media_buy import _register_package

    _register_package(ctx, "pkg_001", pkg)


@dataclass(frozen=True)
class EnvRoute:
    """One row of the declarative BDD env-routing registry.

    ``env_builder`` constructs the harness env (a ``BaseTestEnv`` context
    manager, not yet entered); ``seed`` — given the entered ``env`` — stashes
    ``ctx["env"]`` plus whatever tenant/principal/client/existing-data a
    scenario's steps need. ``xfail_reason``, when set, means this row is a
    placeholder: the generic consumer xfails immediately instead of building
    anything, so a UC can be registered ahead of a harness existing for it.

    The registry exists so authoring a new routing case is adding a row —
    there is no field for hand-rolling seeding or skipping DB scoping.

    ``when``, when set, is the row's ROUTING PREDICATE over the scenario's
    marker-name set. Rows carrying one are tried before the coarse ``uc``
    buckets. They live in rows, not as a hardcoded ``elif`` chain inside
    ``_harness_env``: such a chain is invisible to ``scripts/audit``'s join, which knows
    only about the buckets and would report every predicate-routed scenario as dormant.
    Rows are what let ONE resolver answer for both sides.

    ``uc`` is the coarse bucket this row serves, matched against
    ``storyboard_spec.detect_uc``. A row sets ``when`` or ``uc``, not both.
    """

    tag: str
    env_builder: Callable[[E2EConfig | None], AbstractContextManager[BaseTestEnv]]
    seed: Callable[[dict, BaseTestEnv], None] | None = None
    xfail_reason: str | None = None
    when: Callable[[frozenset[str]], bool] | None = None
    uc: str | None = None


def _seed_uc003_storyboard_generic_client(ctx: dict, env: object) -> None:
    """Seed ctx for the UC-003 storyboard scenarios that dispatch via AdCPTestClient.

    Demonstrator: dispatches through the transport-
    generic ``AdCPTestClient`` (``tests/harness/client.py``) instead of
    ``MediaBuyDualEnv``/``dispatch_request`` — see
    ``tests/bdd/steps/domain/uc003_storyboard_generic_client.py``. Background
    still seeds "mb_existing" (BR-UC-003-update-media-buy.feature:24-28 runs
    for this scenario too), so seeding reuses ``_setup_existing_media_buy``
    (the same named helper the ext-/targeting-overlay branch uses) instead of
    a hand-rolled ``MediaBuyFactory``/``_commit_factory_data`` block —
    ``given_buyer_owns_media_buy_by_id`` registers whatever real id the
    factory generates under the Gherkin "mb_existing" label, so the literal
    id is not required. ``BareIntegrationEnv`` has no product dependency
    chain, so a minimal ``Product`` row is created here purely to satisfy
    ``_setup_existing_media_buy``'s package_config.product_id.
    """
    from tests.factories import ProductFactory

    tenant, principal = env.setup_default_data()
    # And the ACCOUNT, with this principal's access to it. update-media-buy-request.json
    # lists ``account`` in /required, and the boundary now RESOLVES the reference rather
    # than accepting and dropping it, so an unseeded account comes back as
    # PERMISSION_DENIED before the scenario reaches what it grades.
    env.setup_default_account()
    product = ProductFactory(tenant=tenant)
    # ctx["client"] is built once by _run_env_route for every row (B8).
    ctx["tenant"] = tenant
    ctx["principal"] = principal
    _setup_existing_media_buy(ctx, env, tenant, principal, product)


def _build_uc003_storyboard_generic_client_env(e2e_config: object | None) -> AbstractContextManager:
    from tests.harness._base import BareIntegrationEnv

    return BareIntegrationEnv(e2e_config=e2e_config)


def _build_admin_env(e2e_config: object | None) -> AbstractContextManager:
    """Both transports BR-ADMIN-ACCOUNTS.feature declares, chosen at collection.

    ``pytest_generate_tests`` parametrizes ADMIN scenarios over
    ``AdminTransport.INTEGRATION`` plus ``AdminTransport.E2E`` (when
    ``BDD_E2E_ENABLED=true``), and the ``ctx`` fixture stashes ``e2e_config``
    for the ``e2e_``-prefixed one. ADMIN is never parametrized under ``e2e_rest``,
    but ``AdminTransport.E2E`` (``"e2e_admin"``) DOES carry the ``e2e_`` prefix the
    ``ctx`` fixture keys on, so ``e2e_config`` is not always ``None`` here: pinning
    ``mode="integration"`` would run the Flask test_client while the node id claims
    the live stack, silently grading 13 scenarios on one transport. The env is TOLD
    its transport and, over e2e, the per-worker address ``e2e_stack`` synthesised —
    it discovers neither.

    This is the ONE builder that passes ``base_url=`` instead of
    ``e2e_config=``, and the asymmetry is deliberate: the admin UI is an HTML
    form surface, not an AdCP tool surface, so the env needs the ADDRESS and
    nothing else from ``E2EConfig``. Handing it the whole object would pull an
    AdCP-shaped dependency into a surface that has no AdCP protocol — the same
    reason ``AdminTransport`` is not a member of the ``Transport`` enum (see its
    docstring). A census asking "does every builder here receive e2e_config?"
    will flag this line; that flag is expected. What actually must hold — no
    branch pins its own DB scope — is machine-checked by
    ``tests/unit/test_bdd_admin_transport_parametrization.py``
    ``::test_harness_env_never_pins_its_db_scope``, not by that heuristic.
    """
    from tests.harness.admin_accounts import AdminAccountEnv

    mode = "e2e" if e2e_config is not None else "integration"
    base_url = e2e_config.base_url if e2e_config is not None else None  # type: ignore[attr-defined]
    return AdminAccountEnv(mode=mode, base_url=base_url)


def _build_product_env(e2e_config: object | None) -> AbstractContextManager:
    """Shared by COMPAT and UC-GET-PRODUCTS — both are read-only product listing."""
    from tests.harness.product import ProductEnv

    return ProductEnv(e2e_config=e2e_config)


def _build_capabilities_env(e2e_config: object | None) -> AbstractContextManager:
    """UC-010 get_adcp_capabilities. Named rather than ``_env(...)``-generated so
    the parked/wired/not-wired rows below — and ``_uc010_dormancy_rows()`` above —
    all name the SAME builder.

    CapabilitiesEnv mocks only the adapter factory and the audit logger; the DB,
    TenantConfigUoW (publisher partners) and every transport wrapper are real.
    Capabilities is a pure read.
    """
    from tests.harness.capabilities import CapabilitiesEnv

    return CapabilitiesEnv(principal_id="buyer-001", e2e_config=e2e_config)


def _build_creative_formats_env(e2e_config: object | None) -> AbstractContextManager:
    from tests.harness.creative_formats import CreativeFormatsEnv

    return CreativeFormatsEnv(e2e_config=e2e_config)


def _build_media_buy_list_env(e2e_config: object | None) -> AbstractContextManager:
    """get_media_buys — MediaBuyListEnv runs the real _get_media_buys_impl and
    its A2A/MCP wrappers against a real DB (no adapter mock; list is a pure
    read). Genuine spec-production gaps stay xfailed via _UC019_XFAIL_TAGS /
    the selective blocks in the UC-002 branch above.
    """
    from tests.harness.media_buy_list import MediaBuyListEnv

    return MediaBuyListEnv(principal_id="buyer-001", e2e_config=e2e_config)


def _build_media_buy_create_list_env(e2e_config: object | None) -> AbstractContextManager:
    """UC-019 @post-create-poll — create_media_buy AND get_media_buys in ONE
    scenario on ONE identity; a factory-seeded buy would make the poll vacuous.
    MediaBuyCreateListEnv extends MediaBuyCreateEnv with the shared get_media_buys
    dispatch and routes a GetMediaBuysRequest to it (same shape as UC-003's
    MediaBuyDualEnv fork). Seeded with _seed_media_buy_chain, which runs
    setup_media_buy_data — the full create dependency chain (property tag, product,
    pricing option, authorized property).
    """
    from tests.harness.media_buy_create_list import MediaBuyCreateListEnv

    return MediaBuyCreateListEnv(principal_id="buyer-001", e2e_config=e2e_config)


def _seed_tenant_and_principal(ctx: dict, env: object) -> None:
    """``setup_default_data()``, stashed under the keys the steps read.

    Shared by UC-019 (its scenarios seed buys via factories under
    ``ctx["tenant"]`` / ``ctx["principal"]``, and principal "buyer-001" is the one
    its feature files name) and UC-010 (the capability Given steps write tenant
    declarations through ``ctx["tenant"]``). The two former branches held
    byte-identical bodies, which is the substituted-variable shape the DRY
    invariant treats as a defect, so it is one seed with two rows.
    """
    tenant, principal = env.setup_default_data()
    ctx["tenant"] = tenant
    ctx["principal"] = principal


# ── Seeds extracted from the former _harness_env elif chain ────
# Each was an inline body inside a marker-keyed branch. As rows they are visible
# to storyboard_spec.resolve_env_route, which is what lets scripts/audit resolve
# the SAME route instead of re-implementing a coarser lookup.


def _seed_default_tenant_and_principal(ctx: dict, env: object) -> None:
    """Tenant + principal only — the precondition an AUTHENTICATED dispatch needs.

    The two @egress sync routes below were the only EnvRoutes with no seed, so no
    ``Principal`` row was ever written for them. That was invisible while the harness
    handed identity a principal_id whether or not a row backed it; once
    ``BaseTestEnv.identity_for`` began nulling principal_id on a failed lookup — mirroring
    production's ``resolve_identity`` (salesagent-z9e0, pinned by
    tests/integration/test_harness_identity_for_db_lookup.py) — those dispatches started
    arriving unauthenticated and were refused with AUTH_MISSING before reaching the egress
    gate the scenarios exist to grade. The refusal under test is a BUYER's refusal, so the
    buyer has to exist.

    Deliberately NOT ``_seed_media_buy_chain``: these scenarios sync a creative, so a
    product and a pricing option would be unused setup implying a dependency that is not
    there.
    """
    tenant, principal = env.setup_default_data()
    ctx["tenant"] = tenant
    ctx["principal"] = principal


def _declare_seller_does_not_sign(ctx: dict, env: object) -> None:
    """Declare the egress seller a NON-signing seller — what these scenarios actually test.

    RequestSigningPosture.supported defaults to TRUE for a tenant that declared nothing
    (posture_from_declarations({}).supported is True), so an undeclared tenant is treated
    as signing-capable and _credentials_force_a_signature refuses any request carrying
    push_notification_config.authentication. A valid bearer does NOT exempt it — that is
    deliberate ("exempting authenticated callers would defeat it entirely") — so sending
    the credential is necessary but not sufficient; the posture must say so too.

    Truthful rather than a workaround: security.mdx @ v3.1.1 :1465, quoted by
    _credentials_force_a_signature itself, says sellers that do not support request
    signing "have no way to enforce this rule and fall back to the log-and-alarm posture",
    and the pinned signed-requests storyboard gates all 28 negative vectors on
    request_signing.supported: true alone — so a seller advertising false is OUTSIDE the
    rule rather than evading it (tests.helpers.signing.unsupported). Advertise and enforce
    remain one object (posture_for_tenant is the single reader), and the verifier keeps its
    own grading in tests/integration/test_request_signature_operations.py and the
    compliance vectors.

    Through env.declare_request_signing — the ONE writer for a declared posture — and NOT
    a hand-built dict on ctx["tenant"]. That writer attaches the derived
    identity.brand_json_url the pinned required_when trigger needs, gives the tenant a
    dotted virtual_host first (ensure_declarable_identity_host: a single-label host derives
    http:// and the whole declaration is REFUSED, silently, back into the supported bucket),
    and writes through the env's OWN session — which on the e2e_rest parametrization is
    bound to the LIVE server's database, the one that server's verifier reads.

    That last property is why the posture, and not SigningConfig.verifier_enabled, is the
    lever here: these scenarios run on e2e_rest too, and a config patch in the runner
    process cannot reach the server's verifier. The sibling in-process-only module
    tests/integration/test_webhook_hmac_credentials_ingest_refusal.py uses the config
    lever for the same reason inverted.
    """
    env.declare_request_signing(bucket="unsupported")


def _seed_egress_sync(ctx: dict, env: object) -> None:
    """Tenant + principal for the @egress sync legs, as a non-signing seller."""
    _seed_default_tenant_and_principal(ctx, env)
    _declare_seller_does_not_sign(ctx, env)


def _seed_egress_create(ctx: dict, env: object) -> None:
    """The create chain for the @egress create leg, as a non-signing seller."""
    _seed_media_buy_chain(ctx, env)
    _declare_seller_does_not_sign(ctx, env)


def _seed_egress_update(ctx: dict, env: object) -> None:
    """The update chain for the @egress update leg, as a non-signing seller."""
    _seed_update_with_existing_buy(ctx, env)
    _declare_seller_does_not_sign(ctx, env)


def _seed_media_buy_chain(ctx: dict, env: object) -> None:
    """Seed the full create dependency chain (tenant/principal/product/pricing)."""
    tenant, principal, product, pricing_option = env.setup_media_buy_data()
    ctx["tenant"] = tenant
    ctx["principal"] = principal
    ctx["default_product"] = product
    ctx["default_pricing_option"] = pricing_option


def _seed_media_buy_chain_create_dispatch(ctx: dict, env: object) -> None:
    """The chain, plus the flag telling the shared When step to dispatch a create."""
    _seed_media_buy_chain(ctx, env)
    ctx["dispatch_mode"] = "create"


def _seed_media_buy_chain_full_create(ctx: dict, env: object) -> None:
    """The chain, plus the manual-approval full-create flag (PR #1567)."""
    _seed_media_buy_chain(ctx, env)
    ctx["uc002_full_create"] = True


def _seed_update_account_ref(ctx: dict, env: object) -> None:
    """Name the seeded account as the one every update request will carry.

    update-media-buy-request.json lists ``account`` in ``/required`` (v3.1), and the
    boundary RESOLVES the reference, so an update scenario needs a row that exists and is
    reachable by the caller. Seeded HERE, before any Given runs, rather than in
    ``_ensure_update_defaults``: ``setup_default_account`` goes through
    ``setup_default_data``, which re-creates a missing principal, and
    @T-UC-003-ext-a-unknown deletes its principal on purpose — a Given cannot be allowed to
    be undone by a later Given's request default. ``ctx["account_ref"]`` is the key the
    rest of this tree already uses for "the account this request names".
    """
    ctx["account_ref"] = {"account_id": env.setup_default_account().account_id}


def _seed_update_with_existing_buy(ctx: dict, env: object) -> None:
    """The chain plus an existing media buy + package for UC-003 update scenarios."""
    _seed_media_buy_chain(ctx, env)
    _setup_existing_media_buy(ctx, env, ctx["tenant"], ctx["principal"], ctx["default_product"])
    env._seeded_media_buy_id = ctx["existing_media_buy"].media_buy_id
    _seed_update_account_ref(ctx, env)


def _seed_update_with_mb_existing(ctx: dict, env: object) -> None:
    """The chain plus a standalone MediaBuy carrying the literal Background id."""
    from tests.factories import MediaBuyFactory

    _seed_media_buy_chain(ctx, env)
    existing_media_buy = MediaBuyFactory(
        tenant=ctx["tenant"],
        principal=ctx["principal"],
        media_buy_id="mb_existing",
        status="active",
    )
    env._commit_factory_data()
    env._seeded_media_buy_id = "mb_existing"
    ctx["existing_media_buy"] = existing_media_buy
    _seed_update_account_ref(ctx, env)


def _seed_default_data(ctx: dict, env: object) -> None:
    """Envs whose setup is a bare ``setup_default_data()``."""
    env.setup_default_data()


def _seed_delivery_poll(ctx: dict, env: object) -> None:
    """UC-004 polling: stash the tenant/principal under the keys its steps read."""
    tenant, principal = env.setup_default_data()
    ctx["db_tenant"] = tenant
    ctx[f"db_principal_{env._principal_id}"] = principal


def _env(factory_path: str, **kwargs: object) -> Callable[[object | None], AbstractContextManager]:
    """Build an env_builder that imports its harness lazily, as the branches did."""

    def _builder(e2e_config: object | None) -> AbstractContextManager:
        import importlib

        module_name, _, class_name = factory_path.rpartition(".")
        env_cls = getattr(importlib.import_module(module_name), class_name)
        return env_cls(e2e_config=e2e_config, **kwargs)

    return _builder


def _uc(uc_name: str, predicate: Callable[[frozenset[str]], bool]) -> Callable[[frozenset[str]], bool]:
    """Scope a marker predicate to one UC bucket.

    The former chain tested ``uc == "UC-00N"`` FIRST and only then the markers,
    so a bare marker predicate would over-match across UCs (``account`` and
    ``BR-RULE-034`` are both carried by more than one use case).
    """
    return lambda markers: storyboard_spec.detect_uc(markers) == uc_name and predicate(markers)


@contextmanager
def _production_db_pointed_at(url: str) -> Generator[None, None, None]:
    """Point production's cached DB engine at ``url`` for the scenario duration.

    The e2e counterpart of ``integration_db``'s engine repoint: over e2e_rest
    the env's factories write to the live server DB (``e2e_config.postgres_url``),
    but the runner's ``DATABASE_URL`` targets the in-process test base (in-network:
    ``.../adcp_test``), so any in-process production call inside an e2e scenario
    (e.g. a TRANSPORT-BYPASS Given calling an ``_impl``) would read a different
    database than the one being seeded. Repoint DATABASE_URL + reset the cached
    engine on entry, restore both on exit (mirrors tests/conftest_db.py).
    """
    import src.core.context_manager as _context_manager_module
    from src.core.database.database_session import reset_engine

    original_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    reset_engine()
    _context_manager_module._context_manager_instance = None
    try:
        yield
    finally:
        if original_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = original_url
        reset_engine()
        _context_manager_module._context_manager_instance = None


def _db_scope_for(request: pytest.FixtureRequest, e2e_config: object | None) -> AbstractContextManager[None]:
    """Select the production-DB scope for an e2e-capable harness branch.

    In-process transports need the per-test database (``integration_db``).
    Over e2e_rest, ``integration_db`` would repoint production's cached engine
    at an empty per-test DB while the env's factories write to the live server
    DB — so any in-process production call inside an e2e scenario (raw
    ``get_db_session()`` read-backs in Then steps, TRANSPORT-BYPASS Givens
    calling an ``_impl``) would read the wrong database. Point production at
    the server DB instead for the scenario duration.
    """
    if e2e_config is None:
        request.getfixturevalue("integration_db")
        return nullcontext()
    return _production_db_pointed_at(e2e_config.postgres_url)  # type: ignore[attr-defined]


def _run_env_route(
    request: pytest.FixtureRequest, ctx: dict, route: EnvRoute, e2e_config: object | None
) -> Generator[None, None, None]:
    """The one generic ``ENV_ROUTES`` consumer.

    Enters ``_db_scope_for`` — the structural DB-scoping entry point — before
    the row's ``env_builder`` runs, so on the e2e_rest parametrization
    production's cached engine is pointed at the live server DB (not an empty
    per-test DB) before any factory writes happen. Stashes the entered env on
    ``ctx["env"]``, runs the row's ``seed`` callback if present, then yields
    control to the scenario. A row with ``xfail_reason`` set never builds an
    env at all.
    """
    from tests.harness.client import AdCPTestClient

    if route.xfail_reason is not None:
        pytest.xfail(route.xfail_reason)
    with _db_scope_for(request, e2e_config), route.env_builder(e2e_config) as env:
        ctx["env"] = env
        # Build the client ONCE, here, for every row. Constructing it inside a
        # hand-wired seed callback lets only that one row dispatch via the client,
        # and any new row wanting it has to remember to repeat the line.
        # Construction is cheap and
        # side-effect-free; a row that never dispatches via the client simply
        # does not read the key.
        ctx["client"] = AdCPTestClient(env)
        if route.seed is not None:
            route.seed(ctx, env)
        yield


_UC_BUCKET_ROUTES: dict[str, EnvRoute] = {
    "T-UC-003-storyboard-media-buy-not-found": EnvRoute(
        tag="T-UC-003-storyboard-media-buy-not-found",
        env_builder=_build_uc003_storyboard_generic_client_env,
        seed=_seed_uc003_storyboard_generic_client,
    ),
    # Same env + same seed as the row above: the re-cancel scenario also needs
    # the Background's "mb_existing" buy plus a client that sends the buyer's
    # literal payload (`canceled` must reach the
    # seller rather than being dropped by a harness flattener).
    "T-UC-003-storyboard-not-cancellable-on-recancel": EnvRoute(
        tag="T-UC-003-storyboard-not-cancellable-on-recancel",
        env_builder=_build_uc003_storyboard_generic_client_env,
        seed=_seed_uc003_storyboard_generic_client,
    ),
    # The five rows below are keyed by the coarse `uc` bucket (from
    # storyboard_spec.detect_uc), not a per-scenario tag: they are what a scenario in these
    # UCs falls back to when no predicate row above claims it. ADMIN, COMPAT,
    # UC-GET-PRODUCTS and UC-005 have no predicate rows at all — one env + one
    # seed serves every scenario. UC-019 does have one (@post-create-poll needs
    # create + list in a single scenario), so its bucket row is the remainder.
    "ADMIN": EnvRoute(tag="ADMIN", env_builder=_build_admin_env),
    "COMPAT": EnvRoute(tag="COMPAT", env_builder=_build_product_env),
    "UC-GET-PRODUCTS": EnvRoute(tag="UC-GET-PRODUCTS", env_builder=_build_product_env),
    # UC-005 seeds the tenant on EVERY transport, not e2e only. The e2e-only row said
    # "in-process the registry is mocked and the DB is per-test, so the in-process status
    # quo must stay unseeded" -- true of the registry, false of the SELLER. The resolver
    # reads the tenant out of the database on every transport, so with no row the Background
    # sentence "a Seller Agent is operational and accepting requests" was false and
    # list_creative_formats took its no-seller branch (``if tenant is None`` ->
    # ``ListCreativeFormatsResponse(formats=[])``): every filter scenario graded an empty
    # catalog. It looked transport-specific only because _run_a2a_handler's audit-FK
    # preamble (_seed_ambient_tenant -> _ensure_tenant_for_audit) creates the row behind
    # the scenario's back, so a2a alone had a seller and mcp/rest did not -- the same
    # scenario running two different worlds.
    "UC-005": EnvRoute(tag="UC-005", env_builder=_build_creative_formats_env, seed=_seed_default_data),
    "UC-019": EnvRoute(tag="UC-019", env_builder=_build_media_buy_list_env, seed=_seed_tenant_and_principal),
    # UC-026 was WRITTEN but never routed: 75 scenarios, a 2784-line step module and
    # its own xfail tag set, and every node xfailing at fixture setup with "No harness
    # wired for UC-026" -- 728 in-process nodes and 242 over e2e_rest in
    # innet_150926_0531, none of them passing. Nothing flagged it, because the use case
    # is absent from dormant_scenarios.txt too, so it read as ordinary xfail volume.
    #
    # The harness was never the missing piece. MediaBuyDualEnv's own first line says it
    # is "a composite environment for UC-026 and UC-003 BDD scenarios" and its class
    # docstring names UC-026 again; it was built for this and left unreferenced. UC-026
    # needs exactly what it provides, because its scenarios drive BOTH tools -- the
    # feature calls create_media_buy 94 times and update_media_buy 121 -- and
    # _is_update_request routes each dispatch to the right wrappers.
    #
    # _seed_media_buy_chain matches the Background sentence for sentence: a tenant, an
    # authenticated buyer, and a product with pricing options.
    "UC-026": EnvRoute(
        tag="UC-026",
        env_builder=_env("tests.harness.media_buy_dual.MediaBuyDualEnv"),
        seed=_seed_media_buy_chain,
    ),
}

# Tag sets the routing predicates below key on. They were inline `if` conditions
# in the former _harness_env elif chain; as named sets they are readable from the
# rows AND from scripts/audit, which now resolves through the same table.
_UC002_MANUAL_APPROVAL_ROW_TAGS = _UC002_MANUAL_APPROVAL_WIRED
_UC003_TARGETING_OVERLAY_TAGS = frozenset(
    {"T-UC-003-partition-targeting-overlay", "T-UC-003-boundary-targeting-overlay"}
)
_UC003_MANUAL_APPROVAL_TAGS = frozenset(
    {"T-UC-003-alt-manual", "T-UC-003-approval-tenant", "T-UC-003-approval-adapter"}
)
# The package-update root `not` scenarios (media-buy/package-update.json). Their Givens need
# exactly the uc003-ext seed -- an existing buy with pkg_001 -- and the refusal they grade is
# raised before any of it is read: by the request model, or for bidding beside bid_price by
# update_media_buy above the UoW (AMBIGUOUS_BIDDING_POLICY). They sat on the uc003-not-wired
# catch-all, which xfails at setup, so the outline added with the refusal had never run.
_UC003_PACKAGE_UPDATE_NOT_TAGS = frozenset(
    {"T-UC-003-package-update-immutable-keyword", "T-UC-003-package-update-bidding-with-bid-price"}
)
# The BR-RULE-215 revision scenarios. They were dormant for a reason that was not
# "the harness cannot reach them": they had NO step definitions at all, so the
# not-wired row was standing in for missing work rather than for a production gap.
# The steps exist now, and these grade the obligation the whole revision surface
# rests on — a mutating update advances the buyer's optimistic-concurrency token
# and REPORTS the advanced value. They need the same seeded existing buy as the
# manual-approval branch, so they share its row.
_UC003_REVISION_TAGS = frozenset(
    {
        "T-UC-003-revision-success-increments",
        "T-UC-003-revision-and-idempotency-independent",
        "T-UC-003-boundary-revision",
        "T-UC-003-partition-revision",
    }
)
_UC003_STORYBOARD_CLIENT_TAGS = frozenset(
    {"T-UC-003-storyboard-media-buy-not-found", "T-UC-003-storyboard-not-cancellable-on-recancel"}
)
_UC003_INLINE_CREATIVES_READBACK_TAGS = frozenset(
    {
        "T-UC-003-local-inline-creatives-replace",
        "T-UC-003-local-inline-creatives-keep-library",
        "T-UC-003-alt-creatives-inline",
        "T-UC-003-storyboard-creative-fate-after-cancellation",
    }
)

#: UC-006 scenarios CreativeSyncEnv provably serves, keyed by SCENARIO IDENTITY.
#:
#: Identity, not behavioural family, because the family tags do not partition along
#: the wired boundary: measured over all 617 catch-all scenarios, only 102 of the 338
#: that pass are selectable by a family tag whose every member passes -- @partition,
#: @boundary, @format-id and the rest each span passing, failing and step-less
#: scenarios at once, so wiring by family forces failures awake alongside passes.
#: By identity the same measurement selects 210 nodes across these 49 scenarios, every
#: one of which passes on every transport and every Examples row.
#:
#: MEASURED, not assumed: the catch-all row below and this row build
#: the SAME env, so "wiring" is not building a harness -- it is naming the scenarios the
#: existing one serves. Setting the catch-all's xfail_reason to None locally and running
#: the file against a real Postgres gives 410 passed / 209 failed / 85 xfailed; grouped
#: by identity, 49 scenarios pass on every node, 23 are step-less, 20 carry their own
#: xfail and 7 fail on production defects. These are the 49.
#:
#: The rest stay on the catch-all with their blockers named per bucket below. Adding
#: an entry here GROWS the executing surface; it exempts nothing from grading.
#: Empty: every UC-006 scenario parked here has either been corrected to the pin and
#: wired (assignment weight, per-creative status, the sandbox flag, tracker assets,
#: CREATIVE_REJECTED details) or deleted as ungrounded in the sync request's pin
#: (CreativeItem / CreativeVariable, the submitted envelope, CONFLICT, POLICY_VIOLATION).
_UC006_NO_STEP_DEFINITION: frozenset[str] = frozenset()

#: Empty: rule-094-inv2, the one scenario parked here, sent a provenance object with
#: fields core/provenance.json does not define; the Given is pin-shaped now.
_UC006_UNDECLARED_MALFORMATION: frozenset[str] = frozenset()

#: Empty: the one scenario parked here (rule-037-inv4) reads the AI-review executor
#: seam the env has carried since the local dry-run features needed it.
_UC006_MISSING_HARNESS_SEAM: frozenset[str] = frozenset()

#: Empty: rule-037-inv6 (no Slack without a webhook) was parked here because the env
#: replaced _send_creative_notifications wholesale and the guard lives inside it. The
#: env now runs the real function and mocks the Slack sender it reaches, so the
#: scenario reads the sender and grades live.
_UC006_UNVERIFIED_FAILURE: frozenset[str] = frozenset()

#: Empty: main-weight and sandbox-validation, the two scenarios parked here, are rows of
#: the boundary outlines that grade assignments[].weight and the sandbox flag now.
_UC006_OWN_XFAIL: frozenset[str] = frozenset()

_UC006_WIRED_SCENARIOS = frozenset(
    {
        # ext-a: the AUTH_MISSING refusal of a request with no principal, graded live
        # with ext-a-empty now that the stale "answers VALIDATION_ERROR" xfail is gone.
        "T-UC-006-ext-a",
        # The 15 Scenario Outlines whose Examples rows disagree: wired here so their
        # passing rows execute, with the non-passing rows parked per row in
        # _SELECTIVE_XFAIL. A route cannot express row-level disagreement.
        "T-UC-006-boundary-approval",
        "T-UC-006-boundary-assignment-weight",
        "T-UC-006-boundary-assignments-structure",
        "T-UC-006-boundary-generative",
        "T-UC-006-boundary-provenance",
        "T-UC-006-boundary-validation-mode",
        "T-UC-006-main-lenient-warnings",
        "T-UC-006-partition-assignment-pkg",
        "T-UC-006-partition-assignments-structure",
        # The per-creative status is omitted on failed/deleted actions: graded on the
        # unknown-format and full-library-replace rows through the entry selectors.
        "T-UC-006-partition-creative-status-terminal",
        "T-UC-006-partition-format-id",
        "T-UC-006-partition-generative",
        "T-UC-006-partition-idempotency-key",
        "T-UC-006-partition-provenance",
        "T-UC-006-boundary-assignment-format",
        "T-UC-006-boundary-assignment-package",
        "T-UC-006-boundary-creative-scope",
        "T-UC-006-boundary-media-buy",
        "T-UC-006-boundary-principal",
        "T-UC-006-ext-a-empty",
        "T-UC-006-ext-j",
        "T-UC-006-main",
        "T-UC-006-main-approval",
        "T-UC-006-main-assign",
        "T-UC-006-main-provenance-warning",
        "T-UC-006-main-update",
        "T-UC-006-main-warnings",
        "T-UC-006-partition-approval-mode",
        "T-UC-006-partition-auth",
        "T-UC-006-partition-creative-scope",
        "T-UC-006-partition-mb-status",
        # The four assignment-reference scenarios migrated from the retired creative-sync
        # integration file: wired because they pass on every in-process transport.
        "T-UC-006-local-assignment-unknown-creative",
        "T-UC-006-local-assignment-only-missing-package",
        "T-UC-006-local-assignment-only-existing-creative",
        "T-UC-006-local-failed-creative-assignment",
        "T-UC-006-local-dryrun-parity",
        # The format/validation error paths, corrected to the pinned enum (INVALID_REQUEST
        # for schema violations at the request, REFERENCE_NOT_FOUND / VALIDATION_ERROR on
        # the creative's entry, SERVICE_UNAVAILABLE for an agent that does not answer) and
        # to what production does; their ledger entries named codes the enum never had.
        "T-UC-006-boundary-format-id",
        "T-UC-006-ext-c",
        "T-UC-006-ext-d",
        "T-UC-006-ext-d-whitespace",
        "T-UC-006-ext-e",
        "T-UC-006-ext-f",
        "T-UC-006-ext-g",
        "T-UC-006-rule-035-inv2",
        # And the rest of that sweep: per-item CONFIGURATION_ERROR read on the entry
        # (ext-i), the creative on the transport's own served format so only the
        # product's declared set varies (partition-assignment-fmt, which also absorbed
        # ext-k and rule-039-inv2), strict PACKAGE_NOT_FOUND carrying which package
        # (rule-033-inv2), and two Then bodies that read the wrong mock argument or a
        # mock no env wires (rule-035-static, rule-037-inv4).
        "T-UC-006-ext-i",
        "T-UC-006-partition-assignment-fmt",
        "T-UC-006-rule-033-inv2",
        "T-UC-006-rule-035-static",
        "T-UC-006-rule-037-inv4",
        # Steps written for the delete_missing scope outline, the delete_missing +
        # creative_ids conflict (now refused by the request model, per the pin's own
        # "Invalid when creative_ids is provided") and the lenient format mismatch;
        # action "unchanged" reachable now that upsert fields are compared to the row.
        "T-UC-006-boundary-delete-missing",
        "T-UC-006-main-delete-missing-conflict",
        "T-UC-006-rule-039-inv5-lenient",
        "T-UC-006-main-unchanged",
        # Same env and the same Given as main-unchanged, with the agent_url respelled
        # between the two syncs. core/format-id.json makes canonicalizing agent_url a
        # MUST before two format references are treated as the same, and CreativeSyncEnv
        # already serves everything this needs -- so it executes rather than sitting on
        # the unclassified path. salesagent-jnp2s.
        "T-UC-006-unchanged-respelled",
        "T-UC-006-rule-033-inv1",
        "T-UC-006-rule-033-inv3",
        "T-UC-006-rule-033-inv4",
        "T-UC-006-rule-033-inv5",
        "T-UC-006-rule-036-inv1",
        "T-UC-006-rule-036-inv2",
        "T-UC-006-rule-036-inv3",
        "T-UC-006-rule-036-inv4",
        "T-UC-006-rule-036-inv5",
        "T-UC-006-rule-036-inv6",
        "T-UC-006-rule-037-inv1",
        "T-UC-006-rule-037-inv2",
        "T-UC-006-rule-037-inv3",
        "T-UC-006-rule-037-inv5",
        "T-UC-006-rule-038-inv1",
        "T-UC-006-rule-038-inv3",
        "T-UC-006-rule-038-inv4",
        "T-UC-006-rule-038-inv4-violated",
        "T-UC-006-rule-038-inv5",
        "T-UC-006-rule-039-inv1",
        "T-UC-006-rule-039-inv1b",
        "T-UC-006-rule-039-inv3",
        "T-UC-006-rule-039-inv6",
        "T-UC-006-rule-040-inv1",
        "T-UC-006-rule-040-inv2",
        "T-UC-006-rule-040-inv3",
        "T-UC-006-rule-040-inv4",
        # assignments[].weight is carried per entry now, so the two-creative INV-3 grades
        # each persisted weight; INV-1/INV-2 became boundary-assignment-weight rows.
        "T-UC-006-rule-093-inv3",
        # The env runs the real notification function and mocks the Slack sender it
        # reaches, so "no Slack without a webhook" is read off the sender.
        "T-UC-006-rule-037-inv6",
        "T-UC-006-rule-094-inv1",
        # A pin-shaped provenance object (digital_source_type, ai_tool, disclosure).
        "T-UC-006-rule-094-inv2",
        "T-UC-006-rule-094-inv3",
        "T-UC-006-rule-094-inv4",
        "T-UC-006-rule-094-inv5",
        # The sandbox flag on the success shape, absent for a production account and on
        # the errors shape: the request names the seeded account, production reads it.
        "T-UC-006-boundary-sandbox",
        # Tracker assets: accepted ones stored as sent, the pin's refused events and the
        # missing progress offset INVALID_REQUEST through the accepted shape's validator.
        "T-UC-006-partition-tracker-assets",
        # CREATIVE_REJECTED with reasons, driven through the no-preview / no-media_url
        # rejection production already makes.
        "T-UC-006-error-details-creative-rejected",
        # A canonical creative's format_kind and format_option_ref read back through
        # list_creatives after a sync and after a resync onto a different option. The env
        # serves both tools already; the readback uses the same client the format_id
        # roundtrip does.
        "T-UC-006-canonical-identity-roundtrip-on-sync",
        "T-UC-006-canonical-identity-resync-replaces-option",
        # The preview half of the same checklist item, and review on resync of a creative
        # identified by format_kind. CreativeSyncEnv serves all three: the preview answer
        # is the env's preview_creative seam in process and the pinned agent on e2e, and
        # the approval mode is the env's tenant writer.
        "T-UC-006-canonical-identity-through-preview",
        "T-UC-006-canonical-unchanged-resync-keeps-review",
        "T-UC-006-canonical-changed-resync-enters-review",
        # A rename on resync is metadata: "updated", review state kept.
        "T-UC-006-canonical-renamed-resync-keeps-review",
        # representation_selection on sync: the lineage verified against its item, stored,
        # listed back, and the execution-vs-revision review split. The same sync and
        # readback the canonical-identity rows above use, and the same tenant writer.
        "T-UC-006-representation-selection-readback",
        "T-UC-006-representation-selection-refused",
        "T-UC-006-representation-reselection-enters-review",
        "T-UC-006-representation-new-revision-keeps-review",
    }
)

ENV_ROUTES: list[EnvRoute] = [
    # ── @ctxecho (local context-echo-on-every-outcome feature) ──────────────
    # Same reason the @egress rows below are UNSCOPED `when` rows: these
    # scenarios carry T-CTXECHO-* identity tags, not T-UC-<n>, so
    # storyboard_spec.detect_uc returns None and no coarse bucket can claim
    # them. Two rows because the feature grades two tools on purpose —
    # get_products is auth-OPTIONAL (a token-less caller reaches the
    # implementation), so the auth-rejection scenarios need a tool whose
    # ToolSpec declares auth=required, and get_media_buys is the cheapest of
    # those (a pure read, no adapter).
    EnvRoute(
        tag="ctxecho-products",
        when=lambda m: "ctxecho-products" in m,
        env_builder=_build_product_env,
        # A tenant plus its principal, nothing else: get_products needs no
        # account (the field is optional on get-products-request.json) and no
        # Product row — an empty catalog is a valid SUCCESS, and the echo is
        # what these scenarios read off it. Without the seed there is no
        # Principal row for credential() to read a token from, so every scenario
        # would dispatch unauthenticated and grade the wrong refusal.
        seed=_seed_tenant_and_principal,
    ),
    EnvRoute(
        tag="ctxecho-media-buys",
        when=lambda m: "ctxecho-media-buys" in m,
        env_builder=_build_media_buy_list_env,
        seed=_seed_tenant_and_principal,
    ),
    # A third tool, for the ELEMENT-level echo. The two rows above grade the
    # ENVELOPE's context, which one seam reads and writes; AdCP declares the same
    # opaque object on models nested INSIDE a response (Package, and the same
    # field on MediaBuy, PackageUpdate, Results, MediaBuyDeliveryWebhookResult),
    # and no seam can reach those -- the boundary reads the request root and
    # writes the response root. create_media_buy is the cheapest tool with a real
    # request-element -> response-element pair: packages[] (PackageRequest) comes
    # back as packages[] (Package), both declaring `context`.
    EnvRoute(
        tag="ctxecho-packages",
        when=lambda m: "ctxecho-packages" in m,
        env_builder=_build_media_buy_create_list_env,
        seed=_seed_media_buy_chain,
    ),
    # ── @tenantid (local tenant-identification-routes feature) ──────────────
    # An UNSCOPED `when` row for the same reason as the rows above and below: the
    # scenarios carry a @tenantid tag rather than a T-UC-<n> identity, so
    # storyboard_spec.detect_uc returns None and no coarse bucket claims them.
    #
    # The capabilities env, because get_adcp_capabilities is what these dispatch: the
    # tool is chosen by the ENV, not by the step. It is a public read, so no credential
    # confuses the question of which tenant answered. The seed is the point rather than an
    # incidental: a tenant with its own virtual_host is exactly the state being
    # graded, and _seed_tenant_and_principal builds it through TenantFactory, which sets
    # one by default.
    # ── @agentcard (local agent-card-discovery feature) ─────────────────────
    # An UNSCOPED `when` row for the same reason as the rows around it: the scenarios
    # carry T-AGENTCARD-* identity tags rather than a T-UC-<n>, so detect_uc returns None
    # and no coarse bucket claims them.
    #
    # The capabilities env, for the one thing a card fetch needs from an env: the
    # in-process ASGI client (`get_rest_client`), which `fetch_agent_card` uses on the
    # three in-process transports. The card is NOT dispatched as a tool — it is a root
    # endpoint carrying no envelope — so which tool this env would dispatch is irrelevant.
    # The seed is the point: a tenant declaring its own virtual_host is the state being
    # graded, and _seed_tenant_and_principal builds it through TenantFactory.
    EnvRoute(
        tag="agentcard",
        when=lambda m: "agentcard" in m,
        env_builder=_build_capabilities_env,
        seed=_seed_tenant_and_principal,
    ),
    # ── @pubformats (local publisher-format-catalog feature, RFC S3) ────────
    # An UNSCOPED `when` row for the same reason as the rows around it: T-PUBFORMATS-* tags
    # carry no T-UC-<n>, so detect_uc returns None and no coarse bucket claims them.
    #
    # The product env, because one scenario dispatches get_products and reads the product
    # off the wire; the adagents.json fetches use `fetch_agent_card`, which every env has.
    # No seed: the Background's "a tenant is configured for product discovery" seeds the
    # tenant and principal, as for the canonical-format-filter rows.
    EnvRoute(
        tag="pubformats",
        when=lambda m: "pubformats" in m,
        env_builder=_build_product_env,
    ),
    # ── @pubauth (local publisher-authorization feature) ────────────────────
    # A `when` row because its scenarios carry T-ADMIN-PUBAUTH-* tags, which detect_uc
    # files under the ADMIN bucket -- whose env drives the accounts pages and serves no
    # publisher's file. The seed is the tenant whose agent_url the publisher names.
    EnvRoute(
        tag="pubauth",
        when=lambda m: "pubauth" in m,
        env_builder=_env("tests.harness.publisher_authorization.PublisherAuthorizationEnv"),
        seed=_seed_tenant_and_principal,
    ),
    EnvRoute(
        tag="tenantid",
        when=lambda m: "tenantid" in m,
        env_builder=_build_capabilities_env,
        seed=_seed_tenant_and_principal,
    ),
    # ── @predispatch (local pre-dispatch-refusals feature) ──────────────────
    # T-PREDISPATCH-* identity tags, so an UNSCOPED `when` row like the two above.
    # Every routable document addresses get_products, and a tenant plus its
    # principal is all a refusal made before any tool runs can need.
    EnvRoute(
        tag="predispatch",
        when=lambda m: "predispatch" in m,
        env_builder=_build_product_env,
        seed=_seed_tenant_and_principal,
    ),
    # ── @egress (local SSRF / webhook-credential refusal feature) ───────────
    # These scenarios carry T-EGRESS-* identity tags, NOT T-UC-<n>, so
    # storyboard_spec.detect_uc returns None for them and no coarse bucket can
    # claim them. They are UNSCOPED `when` rows (no _uc(...) wrapper) declared
    # FIRST, which is exactly how the former elif chain expressed them: the
    # egress tests checked before the shared UC branches and each borrowed one branch's
    # env. Two of them need an env that does NOT patch the surface under test —
    # a refusal manufactured by a mock proves nothing about the real egress seam.
    EnvRoute(
        tag="egress-sync",
        # sync_creatives leg: the buyer-supplied agent_url must be refused by the
        # REAL registry plus the REAL egress seam, so it takes the unpatched
        # registry variant rather than CreativeSyncEnv.
        when=lambda m: "egress_sync" in m,
        env_builder=_env("tests.harness.creative_sync.RealRegistryCreativeSyncEnv"),
        # sync_creatives is an AUTHENTICATED tool (`require_principal_id` in
        # src/core/tools/creatives/_sync.py) and its request carries a spec-required
        # `account`, resolved through `enrich_identity_with_account` — the first thing
        # that asks the identity for a principal. With no Principal row the dispatch
        # arrives UNAUTHENTICATED and production answers AUTH_MISSING before the egress
        # seam this scenario exists to grade is ever reached. The seed also declares the
        # seller's NON-SIGNING posture, for the same reason the @egress_create /
        # @egress_update rows below carry it: with inbound RFC 9421 verification in the
        # request boundary, an undeclared posture is a second way to be refused early.
        seed=_seed_egress_sync,
    ),
    EnvRoute(
        tag="egress-sync-creds",
        # The CREDENTIAL half of the registration is refused before the
        # per-creative loop is reached, so it wants the ordinary
        # (registry-mocked) sync env, not the real-registry variant above.
        when=lambda m: "egress_sync_creds" in m,
        env_builder=_env("tests.harness.creative_sync.CreativeSyncEnv"),
        # Authenticated and non-signing for the same reasons as the row above. The
        # typed transports refuse the credential half above `_impl`, so they never
        # needed a principal; A2A forwards the buyer's raw dict and reaches the account
        # enrichment first, so without this seed only the a2a leg died on AUTH_MISSING —
        # grading nothing about credentials on the one transport the scenario covers.
        seed=_seed_egress_sync,
    ),
    EnvRoute(
        tag="egress-update",
        # Dispatches a real update_media_buy carrying a push_notification_config,
        # so it needs the UC-003 ext branch: the update wrappers plus a seeded
        # existing media buy for the update to target.
        when=lambda m: "egress_update" in m,
        env_builder=_env("tests.harness.media_buy_dual.MediaBuyDualEnv"),
        seed=_seed_egress_update,
    ),
    EnvRoute(
        tag="egress-create",
        # Ingest-time refusal of a buyer webhook URL — dispatches a real
        # create_media_buy, so it needs the UC-004 "create" branch's env and the
        # full create dependency chain.
        when=lambda m: "egress_create" in m,
        env_builder=_env("tests.harness.media_buy_create.MediaBuyCreateEnv"),
        seed=_seed_egress_create,
    ),
    EnvRoute(
        tag="reporting-cadence-create",
        # local-reporting-cadence-refusal: a reporting_frequency the buy's products do
        # not advertise is refused at create ingest, so this dispatches a real
        # create_media_buy and takes the same env and dependency chain as its
        # @egress_create sibling above. It does NOT take that row's seed: the
        # non-signing-seller declaration there is an egress concern, and a cadence
        # refusal is decided before any signing posture matters.
        #
        # Its own identity tag means detect_uc returns None for these scenarios, so
        # WITHOUT this row all three auto-xfail at fixture setup with "No harness wired
        # for None" and grade nothing while reading green. The row is therefore added in
        # the same change as the feature AND pinned in
        # tests/unit/test_architecture_measurement_floors.py's EXPECTED_WIRED_ROUTES in
        # that same change -- salesagent-8qela.152 is that pin being forgotten for a
        # sibling row, which left the only scenarios grading canonical format filtering
        # deletable without anything reddening.
        when=lambda m: "reporting_cadence" in m,
        env_builder=_env("tests.harness.media_buy_create.MediaBuyCreateEnv"),
        seed=_seed_media_buy_chain,
    ),
    EnvRoute(
        tag="egress-get-products",
        # The remaining @egress scenarios dispatch get_products (and the A2A
        # message/send envelope pair). They share the UC-GET-PRODUCTS branch and
        # differ only in the env: the refusal must come from the REAL
        # resolve_property_list, so ProductEnv's patch is not applied.
        when=lambda m: "egress" in m,
        env_builder=_env("tests.harness.product.RealResolverProductEnv"),
    ),
    # ── Cross-cutting wire obligations (no T-UC-<n> identity tag) ───────────
    # Like the @egress rows above, these carry their own identity tags, so
    # storyboard_spec.detect_uc returns None and no coarse bucket can claim them.
    # They are UNSCOPED `when` rows naming the harness each one needs.
    EnvRoute(
        tag="get-products-pricing-options",
        # BR-UC-GET-PRODUCTS pricing announcement: stores real PricingOption rows and
        # reads them back off get_products, so it takes the UC-GET-PRODUCTS branch. It
        # carries its own identity tag rather than @inventory_profile, so detect_uc
        # returns None for it and no coarse bucket claims it.
        when=lambda m: "pricing_option_announcement" in m,
        env_builder=_build_product_env,
    ),
    EnvRoute(
        tag="get-products-format-options",
        # BR-UC-GET-PRODUCTS format-option announcement: stores real products.format_options
        # documents and reads them back off get_products, so it takes the UC-GET-PRODUCTS
        # branch, exactly like its pricing sibling above. It carries its own identity tag, so
        # detect_uc returns None for it and no coarse bucket claims it -- which is why the row
        # is needed at all: without it the scenarios auto-xfail at fixture setup with
        # "No harness wired for None" and grade nothing while reading green.
        when=lambda m: "format_option_announcement" in m,
        env_builder=_build_product_env,
    ),
    EnvRoute(
        tag="get-products-format-id-filter",
        # BR-UC-GET-PRODUCTS format_id filtering: stores real products.format_ids documents
        # under two declaring agents and reads which products get_products announces, so it
        # takes the UC-GET-PRODUCTS branch like its two siblings above. Its own identity tag
        # means detect_uc returns None, so without this row the scenario would auto-xfail at
        # fixture setup with "No harness wired for None" and grade nothing while reading green.
        when=lambda m: "format_id_filter" in m,
        env_builder=_build_product_env,
    ),
    EnvRoute(
        tag="get-products-canonical-format-filters",
        # BR-UC-GET-PRODUCTS canonical format filtering: stores real canonical-only
        # products.format_options documents and reads which products get_products
        # announces, so it takes the UC-GET-PRODUCTS branch like its three siblings above.
        # Its own identity tag means detect_uc returns None, so WITHOUT this row the
        # scenarios auto-xfail at fixture setup with "No harness wired for None" and grade
        # nothing while reading green -- which is the hole salesagent-edkpt describes one
        # layer up, and the reason this row is added in the same change as the feature.
        when=lambda m: "canonical_format_filter" in m,
        env_builder=_build_product_env,
    ),
    EnvRoute(
        tag="format-satisfaction",
        # BR-UC-FORMAT-SATISFACTION dispatches create_media_buy AND update_media_buy against
        # a product carrying a real canonical declaration, so it takes MediaBuyDualEnv --
        # which subclasses MediaBuyCreateEnv and routes an update (discriminated by
        # media_buy_id) to the update wrappers. The create scenarios are unaffected by the
        # subclass; what it adds is the second of the three paths the bead grades.
        #
        # Its own identity tag means detect_uc returns None, so WITHOUT this row every
        # scenario auto-xfails at fixture setup with "No harness wired for None" and grades
        # nothing while reading green. Added in the same change as the feature, and pinned in
        # tests/unit/test_architecture_measurement_floors.py in that same change --
        # salesagent-8qela.152 is this pin being forgotten for a sibling row.
        when=lambda m: "format_satisfaction" in m,
        env_builder=_env("tests.harness.media_buy_dual.MediaBuyDualEnv"),
        seed=_seed_media_buy_chain,
    ),
    EnvRoute(
        tag="format-satisfaction-sync",
        # The THIRD path the bead grades: the sync_creatives assignment gate. A different
        # tool and so a different env, which is why these scenarios carry their own tag
        # rather than sharing @format_satisfaction -- one row cannot build two envs. Seeded
        # by the scenarios' own Givens, because CreativeSyncEnv's default seed does not know
        # which format arms this feature's product needs to declare.
        #
        # Pinned alongside its sibling for the same reason: its tag is its own, so no coarse
        # uc bucket claims it and deleting this row makes all four scenarios auto-xfail.
        #
        # ``_seed_default_tenant_and_principal`` and not ``_seed_media_buy_chain``: a buyer
        # dispatching sync_creatives has to EXIST (``identity_for`` nulls principal_id on a
        # failed lookup, so an unseeded principal arrives unauthenticated and is refused
        # AUTH_MISSING before reaching the gate), but the product these scenarios need
        # declares format arms the shared chain knows nothing about -- so the product, the
        # buy and the package are the scenarios' own Givens.
        when=lambda m: "format_satisfaction_sync" in m,
        env_builder=_env("tests.harness.creative_sync.CreativeSyncEnv"),
        seed=_seed_default_tenant_and_principal,
    ),
    EnvRoute(
        tag="creative-library-upsert",
        # The LIBRARY UPSERT path, and the ONE route in this feature that leaves the
        # creative-agent registry unpatched. ``RealRegistryCreativeSyncEnv`` says why that
        # is worth having -- "a refusal produced by a mock proves nothing about
        # production".
        #
        # THE CATALOGUE IS NO LONGER THE REASON THIS ROW EXISTS, and the two measurements
        # that used to be written here ("``CreativeSyncEnv`` sets
        # ``list_all_formats.return_value = []``, so ``all_formats`` arrives EMPTY",
        # "the parent still blanks the fetch, measured at len=0") WERE TRUE AND ARE NOW
        # FALSE: 1883dedc4 installs production's own ``ReferenceFormatsRegistry`` on the
        # parent and deleted the ``run_async`` patch, so BOTH envs serve the committed
        # capture with nothing arranged. They are recorded as retired rather
        # than deleted because they are what made the Given for these two scenarios reach
        # for ``mock["registry"]`` -- a key this env DELETES -- and died there with
        # ``KeyError`` on all four transports from the day the scenarios were written
        # (``KeyError: 'run_async'`` in box run innet_171026_2038,
        # ``KeyError: 'registry'`` in innet_171026_2156). Nothing here needs the
        # catalogue arranged; see ``_sync_legacy_claim``.
        #
        # THE CATALOGUE IS HERMETIC HERE; THE PREVIEW IS NOT, and the paragraph this
        # replaces claimed the env "never dials" on the strength of the catalogue alone.
        # Resolution is hermetic: under ADCP_TESTING the unpatched registry is
        # ``ReferenceFormatsRegistry``, which answers every format from the checked-in
        # capture, ``display_300x250_image`` projecting to ``image``. The PREVIEW is a
        # real request: with the Given fixed and the gate reached, production's own
        # ``preview_creative`` runs for a static agent-backed format, and the projectable
        # scenario was measured dialling ``https://creative.adcontextprotocol.org`` for
        # real -- three failed MCP handshakes, then a live session answering
        # ``variants=1`` -- on every in-process transport. The GRADE is unaffected (the
        # canonical kind comes from the capture, not from the agent), but the row's green
        # mark now depends on a third party being up. That is **salesagent-1bc7l**, which
        # owns the missing ``preview_creative``/``build_creative`` capture; routing these
        # rows to the parent would trade the dial for an ``AsyncMock`` in the BDD layer,
        # which is not a fix.
        #
        # ITS OWN TAG, and the tag deliberately does NOT contain "format_satisfaction":
        # these rows are PREDICATE-routed by substring, so a tag sharing a prefix with a
        # sibling would be claimed by whichever row is reached first. Derived against the
        # live table rather than assumed -- ``creative_library_upsert`` is claimed by no
        # other row, checked with the two existing tags as controls so a table that claimed
        # nothing could not read as agreement.
        #
        # Pinned in ``test_architecture_measurement_floors.py`` in this same change: an
        # own-identity tag makes ``detect_uc`` return None, so deleting this row would send
        # both scenarios to "No harness wired for None" -- an auto-xfail at fixture setup
        # that grades nothing while reading green.
        when=lambda m: "creative_library_upsert" in m,
        env_builder=_env("tests.harness.creative_sync.LibraryUpsertCreativeSyncEnv"),
        seed=_seed_default_tenant_and_principal,
    ),
    EnvRoute(
        tag="security-wire-error-safety",
        # BR-SECURITY-001 grades that an UNTYPED exception cannot leak internals to
        # the wire. It dispatches get_products, so it takes the UC-GET-PRODUCTS branch.
        when=lambda m: any(t.startswith("T-SECURITY-001") for t in m),
        env_builder=_build_product_env,
    ),
    EnvRoute(
        tag="security-tenant-isolation",
        # BR-SECURITY-002 grades that a credential resolves to exactly one tenant and one
        # principal. It dispatches get_products, so like BR-SECURITY-001 it takes the
        # UC-GET-PRODUCTS branch -- but it seeds its OWN two tenants rather than relying on
        # the branch's default one, because a single-tenant database cannot exhibit the leak
        # it is looking for.
        when=lambda m: any(t.startswith("T-SECURITY-002") for t in m),
        env_builder=_build_product_env,
    ),
    EnvRoute(
        tag="database-fail-fast",
        # The process-wide fail-fast in get_db_session must refuse every tenant only when the
        # database cannot be reached. The claim is about the OTHER tenant, so it reuses
        # BR-SECURITY-002's two-tenant seeding and dispatches get_products like it does.
        when=lambda m: any(t.startswith("T-DBFAILFAST") for t in m),
        env_builder=_build_product_env,
    ),
    EnvRoute(
        tag="protocol-version-negotiation",
        # BR-PROTOCOL-001 grades that the BOUNDARY refuses a version pin this seller cannot
        # serve, on a tool that is not get_adcp_capabilities — the tool whose own negotiation
        # was the only one that ever ran. It dispatches get_products, so like BR-SECURITY-001
        # it takes the UC-GET-PRODUCTS branch.
        when=lambda m: any(t.startswith("T-PROTOCOL-001") for t in m),
        env_builder=_build_product_env,
    ),
    EnvRoute(
        tag="codes-declared-code-reaches-buyer",
        # BR-CODES-001 (a declared error code reaches the buyer unrewritten) and
        # BR-CODES-002's bare-raise scenario both exercise their obligation through a
        # FULL create_media_buy — it is the cheapest bare, non-auth raise site already
        # wired to every transport — so they need the UC-002 full-create branch rather
        # than a harness of their own.
        when=lambda m: bool(m & _UC002_FULL_CREATE_WIRED),
        env_builder=_env("tests.harness.media_buy_create.MediaBuyCreateEnv"),
        seed=_seed_media_buy_chain_full_create,
    ),
    # ── UC-002 ──────────────────────────────────────────────────────────────
    EnvRoute(
        tag="uc002-account",
        when=_uc("UC-002", lambda m: "account" in m),
        env_builder=_env("tests.harness.media_buy_create.MediaBuyCreateEnv"),
        seed=_seed_media_buy_chain,
    ),
    EnvRoute(
        tag="uc002-ext",
        when=_uc(
            "UC-002",
            lambda m: (
                any(t.startswith("T-UC-002-ext-") for t in m)
                # T-UC-002-main, the auto-approved main flow, shares this row rather than
                # getting its own: it needs EXACTLY what the ext scenarios need — the create
                # chain plus dispatch_mode="create", so the When dispatches the request the
                # Givens built instead of rebuilding it. It sat on the uc002-not-wired
                # catch-all until now, which xfailed it at fixture setup, so it dispatched
                # nothing and its two unique Thens had never executed anywhere in the corpus
                # The sibling already on this row,
                # @T-UC-002-ext-dual-emit, has the byte-identical Given block and passes on
                # a2a/mcp/rest/e2e_rest, which is why this is a row-share and not new wiring.
                or "T-UC-002-main" in m
                or "nfr-highvalue" in m
                or "T-UC-002-nfr-001-enforcement" in m
                # The two daily-spend-cap outlines join for the same reason, and the same
                # way. Their Givens need exactly this row's seed and nothing else: a tenant
                # with the auto-seeded USD CurrencyLimit whose ``max_daily_package_spend``
                # ``_set_daily_spend_cap`` mutates (it reads the row with ``.one()``, so the
                # row has to exist), ``ctx["tenant"]`` to find it by, request defaults to put
                # the package budget and flight dates on, and dispatch_mode="create" so the
                # shared When dispatches what the Givens built.
                #
                # On the catch-all they dispatched nothing, and that concealed a live
                # production defect for the whole life of the marker. The scenarios demand
                # BUDGET_EXCEEDED on the wire and the Then asserts it through
                # ``assert_wire_error``, so a run would have caught the buyer receiving
                # INTERNAL_ERROR / transient / 500 instead — the failure builder raising while
                # rendering a dict ``details``. Two markers covered them: this routing, and a
                # narrower ``_UC002_VALIDATION_XFAIL`` entry whose reason ("production raises
                # plain ValueError") had expired. Neither reason was the reason.
                or "T-UC-002-partition-daily-spend-cap" in m
                or "T-UC-002-boundary-daily-spend-cap" in m
                # The two creative-asset outlines join on the same reading. Their Givens need
                # this row's seed and nothing else: the creative Givens
                # (given_media_buy.py:2112, :2187) build fixtures off ``ctx["tenant"]`` and
                # ``ctx["principal"]`` through CreativeFactory, read ``ctx["default_product"]``
                # for the format a mismatch has to mismatch, and hang creative_ids / inline
                # creatives on the request defaults — then dispatch_mode="create" makes the
                # shared When dispatch exactly what they built. All 54 of their items PASS on
                # this row now. Nine of them used to carry a row-keyed strict xfail for the
                # seeded-format fixture gap; that gap is reconciled and the nine graduated —
                # the record is with the _UC002_VALIDATION_XFAIL block.
                or "T-UC-002-partition-creative-asset" in m
                or "T-UC-002-boundary-creative-asset" in m
            ),
        ),
        env_builder=_env("tests.harness.media_buy_create.MediaBuyCreateEnv"),
        seed=_seed_media_buy_chain_create_dispatch,
    ),
    EnvRoute(
        tag="uc002-manual-approval",
        # Also claims the v3.1 sync-success envelope scenario: it needs exactly the
        # full create this branch already runs, so it shares the row rather than
        # duplicating the seed. The former chain expressed the same thing by OR-ing
        # _UC002_V31_SUCCESS_WIRED into the manual-approval full-create flag.
        when=_uc("UC-002", lambda m: bool(m & (_UC002_MANUAL_APPROVAL_ROW_TAGS | _UC002_V31_SUCCESS_WIRED))),
        env_builder=_env("tests.harness.media_buy_create.MediaBuyCreateEnv"),
        seed=_seed_media_buy_chain_full_create,
    ),
    EnvRoute(
        tag="uc002-idempotency",
        when=_uc(
            "UC-002",
            lambda m: bool(m & _UC002_IDEMPOTENCY_WIRED) or storyboard_spec.is_brand_shorthand_media_buy(m),
        ),
        env_builder=_env("tests.harness.media_buy_create.MediaBuyCreateEnv"),
        seed=_seed_media_buy_chain,
    ),
    # Wired, not parked: the scenario needs what the uc002-ext rows need -- the create chain
    # plus dispatch_mode="create", so the shared When dispatches the request its Given broke
    # -- and its refusal is the transport boundary's, which every transport reaches.
    EnvRoute(
        tag="uc002-inv-015-6",
        when=_uc("UC-002", lambda m: "T-UC-002-inv-015-6" in m),
        env_builder=_env("tests.harness.media_buy_create.MediaBuyCreateEnv"),
        seed=_seed_media_buy_chain_create_dispatch,
    ),
    # The sandbox-isolation pair (@T-UC-002-sandbox-no-live-ad-server and its
    # instrument check @T-UC-002-live-ad-server-reached). A row of its own, ABOVE the
    # catch-all, because the catch-all xfails at fixture setup — a scenario that lands
    # there dispatches nothing and grades nothing, which is exactly how BR-RULE-209
    # INV-2 came to be unenforced while a scenario claimed to cover it
    # (salesagent-kf5mn).
    #
    # The same seed as the uc002-ext row: these scenarios need the full create chain
    # plus dispatch_mode="create", so the When dispatches the request the Givens built
    # rather than rebuilding it. Not a row-share with uc002-ext because the predicate
    # there enumerates tags and this one keys on a capability the Given needs
    # (@live-ad-server), which is the thing a future scenario would reuse.
    EnvRoute(
        tag="uc002-live-ad-server",
        when=_uc("UC-002", lambda m: "live-ad-server" in m),
        env_builder=_env("tests.harness.media_buy_create.MediaBuyCreateEnv"),
        seed=_seed_media_buy_chain_create_dispatch,
    ),
    # The inline-creatives create reads the created package back with get_media_buys, so it
    # needs the env that dispatches both: MediaBuyCreateListEnv routes a GetMediaBuysRequest
    # to the list tool and everything else to the create. The seed is the catch-all's, which
    # it used before the readback was added.
    EnvRoute(
        tag="uc002-inline-creatives-readback",
        when=_uc("UC-002", lambda m: "T-UC-002-alt-creatives" in m),
        env_builder=_env("tests.harness.media_buy_create_list.MediaBuyCreateListEnv"),
        seed=_seed_media_buy_chain_create_dispatch,
    ),
    # The pending_creatives_to_start lifecycle creates a buy and then updates it, so it needs
    # the env that dispatches both: MediaBuyDualEnv subclasses MediaBuyCreateEnv and routes a
    # request carrying media_buy_id to update_media_buy. The seed is the uc002-ext one, so the
    # first When dispatches the create its Givens built.
    EnvRoute(
        tag="uc002-create-then-update",
        when=_uc("UC-002", lambda m: "T-UC-002-storyboard-pending-creatives-state-transition" in m),
        env_builder=_env("tests.harness.media_buy_dual.MediaBuyDualEnv"),
        seed=_seed_media_buy_chain_create_dispatch,
    ),
    # The remainder of UC-002. It seeds what the uc002-ext row seeds, for the same reason:
    # its Background claims "a tenant exists with completed setup checklist", and
    # validate_setup_complete reads the rows setup_media_buy_data writes on every
    # transport. Unseeded, a box run with the xfail lifted refused 452 of these nodeids
    # CONFIGURATION_ERROR before reaching what they grade.
    #
    # No xfail_reason any more: every scenario this row claims runs. What fails is named in
    # _UC002_UC003_LEDGER, one entry per measured cause (measure_innet_291026_1202), and what
    # passes without grading anything is parked in _UC002_UC003_VACUOUS_PARKS. The tag keeps
    # its old name because the census and the docs know the row by it.
    EnvRoute(
        tag="uc002-not-wired",
        when=_uc("UC-002", lambda m: True),
        env_builder=_env("tests.harness.media_buy_create.MediaBuyCreateEnv"),
        seed=_seed_media_buy_chain_create_dispatch,
    ),
    # ── UC-003 ──────────────────────────────────────────────────────────────
    # The scenarios that read a package's creatives back the way a buyer does (the
    # inline-creatives uploads and the release on cancel): the uc003-ext seed, on the env
    # that also routes get_media_buys.
    EnvRoute(
        tag="uc003-inline-creatives-readback",
        when=_uc("UC-003", lambda m: bool(m & _UC003_INLINE_CREATIVES_READBACK_TAGS)),
        env_builder=_env("tests.harness.media_buy_create_update_list.MediaBuyCreateUpdateListEnv"),
        seed=_seed_update_with_existing_buy,
    ),
    EnvRoute(
        tag="uc003-ext",
        # The two creative-state outlines share this row rather than getting one of their
        # own: their Givens need exactly this seed — the update chain plus an existing media
        # buy and its package, which is what ``_seed_update_with_existing_buy`` builds — and
        # nothing else. ONE marker held them: the uc003-not-wired catch-all, which xfails at
        # fixture setup, so neither outline had ever dispatched. Verified to be the only one
        # — neither tag appears in _XFAIL_TAGS, in e2e_rest_known_failures.txt, or in
        # dormant_scenarios.txt — so unlike the UC-002 creative-asset pair there is no second
        # marker to remove alongside this.
        when=_uc(
            "UC-003",
            lambda m: (
                any(t.startswith("T-UC-003-ext-") for t in m)
                or bool(m & _UC003_TARGETING_OVERLAY_TAGS)
                or bool(m & _UC003_PACKAGE_UPDATE_NOT_TAGS)
                or "T-UC-003-partition-creative-state" in m
                or "T-UC-003-boundary-creative-state" in m
            ),
        ),
        env_builder=_env("tests.harness.media_buy_dual.MediaBuyDualEnv"),
        seed=_seed_update_with_existing_buy,
    ),
    EnvRoute(
        tag="uc003-manual-approval",
        when=_uc("UC-003", lambda m: bool(m & (_UC003_MANUAL_APPROVAL_TAGS | _UC003_REVISION_TAGS))),
        env_builder=_env("tests.harness.media_buy_dual.MediaBuyDualEnv"),
        seed=_seed_update_with_mb_existing,
    ),
    EnvRoute(
        tag="uc003-storyboard-generic-client",
        when=_uc("UC-003", lambda m: bool(m & _UC003_STORYBOARD_CLIENT_TAGS)),
        env_builder=_build_uc003_storyboard_generic_client_env,
        seed=_seed_uc003_storyboard_generic_client,
    ),
    # The catch-all seeds what the feature's Background states for every UC-003 scenario
    # -- an existing media buy the buyer owns, with a package, and the account the update
    # names -- which is the uc003-ext seed. Without it, a measurement with the xfail off
    # (measure_innet_281026_1501) failed every one of the 1524 nodes this row claims on the
    # Background's ownership Given ("No existing_media_buy in ctx"), so the row's xfail hid
    # a missing seed rather than the per-scenario gaps it names.
    #
    # The reason is dropped, so each scenario is graded on its own steps. What fails is named
    # in _UC002_UC003_LEDGER and what passes vacuously in _UC002_UC003_VACUOUS_PARKS, both
    # measured on measure_innet_291026_1202.
    EnvRoute(
        tag="uc003-not-wired",
        when=_uc("UC-003", lambda m: True),
        env_builder=_env("tests.harness.media_buy_dual.MediaBuyDualEnv"),
        seed=_seed_update_with_existing_buy,
    ),
    # ── UC-006 ──────────────────────────────────────────────────────────────
    # @request-signing scenarios (salesagent-n78j0.1.3) grade the INBOUND RFC 9421
    # enforcement ladder — the composition rule and the webhook-credential
    # escalation — on the same sync_creatives dispatch. They need nothing from this
    # env beyond a real wire on every transport and a push_notification_config it
    # already forwards; the posture, the key and the verification oracle are
    # BaseTestEnv's (env.declare_request_signing / enable_request_signing /
    # signature_verifications).
    EnvRoute(
        tag="uc006-creative-sync",
        when=_uc(
            "UC-006",
            lambda m: (
                bool(
                    m
                    & {
                        "account",
                        "creative-invariant",
                        "BR-RULE-034",
                        "webhook-ssrf",
                        # @request-signing: the inbound RFC 9421 enforcement ladder, per
                        # the note above this route.
                        "request-signing",
                        "uc006-storyboard-routing",
                        "uc006-idempotency",
                        # @creative-approval drives the approval_mode branches of
                        # _processing.py, whose ai-powered branch reaches the background
                        # AI-review executor — an effect that leaves the sync
                        # transaction. CreativeSyncEnv mocks that executor, which is what
                        # makes the effect observable rather than a race with a real
                        # background thread. This set is the ONLY thing standing between a
                        # UC-006 scenario and dormancy, so a scenario CreativeSyncEnv
                        # genuinely serves belongs in it — the entry grows the executing
                        # surface, it does not exempt anything from grading.
                        "creative-approval",
                    }
                )
                # Plus the scenarios named one by one, because the family tags do not
                # partition along the wired boundary -- see _UC006_WIRED_SCENARIOS.
                or bool(m & _UC006_WIRED_SCENARIOS)
            ),
        ),
        env_builder=_env("tests.harness.creative_sync.CreativeSyncEnv"),
    ),
    EnvRoute(
        tag="uc006-stepless",
        when=_uc("UC-006", lambda m, s=_UC006_NO_STEP_DEFINITION: bool(m & s)),
        env_builder=_env("tests.harness.creative_sync.CreativeSyncEnv"),
        xfail_reason=(
            "UC-006 not wired: no step definition for one of its steps, so it grades nothing (tests/bdd/dormant_scenarios.txt names the blocking sentence per scenario)"
        ),
    ),
    EnvRoute(
        tag="uc006-malformation",
        when=_uc("UC-006", lambda m, s=_UC006_UNDECLARED_MALFORMATION: bool(m & s)),
        env_builder=_env("tests.harness.creative_sync.CreativeSyncEnv"),
        xfail_reason=(
            "UC-006 not wired: the dispatched payload is refused by the malformation gate and not declared. Fix the payload or declare it with the code the buyer must receive -- an xfail here would hide exactly what that gate exists to show"
        ),
    ),
    EnvRoute(
        tag="uc006-harness-seam",
        when=_uc("UC-006", lambda m, s=_UC006_MISSING_HARNESS_SEAM: bool(m & s)),
        env_builder=_env("tests.harness.creative_sync.CreativeSyncEnv"),
        xfail_reason=(
            "UC-006 not wired: CreativeSyncEnv lacks the seam this scenario asserts on. A harness gap, not a production one"
        ),
    ),
    EnvRoute(
        tag="uc006-unverified",
        when=_uc("UC-006", lambda m, s=_UC006_UNVERIFIED_FAILURE: bool(m & s)),
        env_builder=_env("tests.harness.creative_sync.CreativeSyncEnv"),
        xfail_reason=(
            "UC-006 not wired: fails for a reason that is none of the named test-side blockers and has NOT been individually diagnosed. Not filed as a production defect on a classifier's say-so"
        ),
    ),
    EnvRoute(
        tag="uc006-own-xfail",
        when=_uc("UC-006", lambda m, s=_UC006_OWN_XFAIL: bool(m & s)),
        env_builder=_env("tests.harness.creative_sync.CreativeSyncEnv"),
        xfail_reason=(
            "UC-006 not wired: already carries its own xfail for a named reason; this row keeps it off the unclassified path and adds nothing"
        ),
    ),
    EnvRoute(
        tag="uc006-unclassified",
        when=_uc("UC-006", lambda m: True),
        env_builder=_env("tests.harness.creative_sync.CreativeSyncEnv"),
        xfail_reason=(
            "UC-006 UNCLASSIFIED: no row names this scenario, so nobody has decided what "
            "blocks it. Wire it, or give it a row naming its blocker -- do not leave it here"
        ),
    ),
    # ── UC-018 ──────────────────────────────────────────────────────────────
    EnvRoute(
        tag="uc018-list",
        # The three T-UC-018-* outlines (#1721 lane D) carry the rows the
        # _handle_list_creatives_skill -> shared build_*_request conversion can
        # silently delete, so they must EXECUTE rather than xfail fast:
        #  - partition-filters: singular media_buy_id + plural media_buy_ids
        #    merge/dedup (a bare select_request_fields(ListCreativesRequest, bag)
        #    drops both keys — they live on CreativeFilters, not the request model);
        #  - partition-field-selector: include_assignments, one of the projection
        #    flags the A2A hand-list carries today;
        #  - boundary-pagination: the sort_by/sort_order coercions the builder
        #    performs on the FLAT path.
        # Rows in these outlines that grade behavior the lane does not implement are
        # parked per-row in _SELECTIVE_XFAIL, not per-scenario.
        when=_uc(
            "UC-018",
            lambda m: bool(
                m
                & {
                    "list-after-sync",
                    "concept-id",
                    "BR-RULE-034",
                    "T-UC-018-partition-filters",
                    "T-UC-018-partition-field-selector",
                    "T-UC-018-boundary-pagination",
                }
            ),
        ),
        env_builder=_env("tests.harness.creative_list.CreativeListEnv"),
    ),
    # The uc018-ext-c ROW IS GONE, not merely un-parked. #1652's "validation harness
    # wiring" was the missing When bindings, not a production gap: every row of that
    # outline sends a payload violating a constraint the pinned request schema declares,
    # and the DTO refuses it at the boundary on every transport, so the rows grade the
    # refusal for real. With its xfail_reason removed the row named the same env as the
    # UC-018 catch-all below and did nothing else, and a row that selects a subset in
    # order to give it identical treatment is a routing decision with no consequence.
    # When the dormant all-fields boundary scenarios are wired, their Then must
    # assert value-when-present, not key-presence-of-13: list_creatives drops a
    # corrupt tags/assets blob to absent and collapses an empty stored tags list
    # to omission (both conformant at 3.1.1) -- see the #1508 reconciliation note
    # in test_uc018_list_creatives.py's module docstring.
    EnvRoute(
        # Not a catch-all park any more: this row builds the SAME env as uc018-list and
        # carries no seed either, so the scenarios it matches were parked by a reason
        # string rather than by a missing harness. The reason is gone and they execute.
        tag="uc018-not-wired",
        when=_uc("UC-018", lambda m: True),
        env_builder=_env("tests.harness.creative_list.CreativeListEnv"),
    ),
    # ── UC-011 ──────────────────────────────────────────────────────────────
    EnvRoute(
        tag="uc011-list",
        when=_uc("UC-011", lambda m: storyboard_spec.uc011_harness(m) == "list"),
        env_builder=_env("tests.harness.account_list.AccountListEnv"),
    ),
    EnvRoute(
        tag="uc011-sync",
        when=_uc("UC-011", lambda m: storyboard_spec.uc011_harness(m) == "sync"),
        env_builder=_env("tests.harness.account_sync.AccountSyncEnv"),
    ),
    EnvRoute(
        # Same env and same absent seed as uc011-sync, so the reason string was the
        # only thing stopping these scenarios: it is gone and they execute.
        tag="uc011-not-wired",
        when=_uc("UC-011", lambda m: True),
        env_builder=_env("tests.harness.account_sync.AccountSyncEnv"),
    ),
    # ── UC-004 ──────────────────────────────────────────────────────────────
    EnvRoute(
        tag="uc004-create",
        when=_uc("UC-004", lambda m: storyboard_spec.uc004_harness(m) == "create"),
        env_builder=_env("tests.harness.media_buy_create.MediaBuyCreateEnv"),
        seed=_seed_media_buy_chain,
    ),
    EnvRoute(
        tag="uc004-circuit-breaker",
        when=_uc("UC-004", lambda m: storyboard_spec.uc004_harness(m) == "circuit-breaker"),
        env_builder=_env("tests.harness.delivery_circuit_breaker.CircuitBreakerEnv"),
        seed=_seed_default_data,
    ),
    EnvRoute(
        tag="uc004-poll",
        when=_uc("UC-004", lambda m: storyboard_spec.uc004_harness(m) == "poll"),
        env_builder=_env("tests.harness.delivery_poll.DeliveryPollEnv", principal_id="buyer-001"),
        seed=_seed_delivery_poll,
    ),
    # ── UC-010 ──────────────────────────────────────────────────────────────
    EnvRoute(
        tag="uc010-capabilities",
        when=_uc("UC-010", lambda m: bool(m & _uc010_wired_tags())),
        env_builder=_build_capabilities_env,
        seed=_seed_tenant_and_principal,
    ),
    # ── UC-019 ──────────────────────────────────────────────────────────────
    EnvRoute(
        tag="uc019-post-create-poll",
        when=_uc("UC-019", lambda m: "post-create-poll" in m),
        env_builder=_build_media_buy_create_list_env,
        seed=_seed_media_buy_chain,
    ),
]

# The dormant UC-010 tags, one row each so every one keeps its own tracking
# citation — including the PARKED ones, whose row carries the text naming what is
# missing instead of the generic dormancy sentence. The park's ROW-level half
# (_UC010_PARKED_ROWS) is keyed on the nodeid, which a `when` predicate cannot see,
# so it stays in pytest_collection_modifyitems.
#
# They come AFTER the wired row above: a tag that is both wired and still listed as
# dormant resolves to the wired row, and the stale entry is caught by
# tests/unit/test_architecture_uc010_dormancy_citations.py. A second UC-010 block
# stood here after the merge — its own parked rows, a duplicate wired row reading a
# duplicate wired set, and a catch-all that hardcoded one issue number for every
# dormant tag. Being earlier in the table, that catch-all matched first and swallowed
# every row below, so each dormant tag xfailed with the one shared citation the
# per-tag map exists to replace.
ENV_ROUTES += _uc010_dormancy_rows()


def _refuse_unrouted(uc: str | None, marker_names: frozenset[str]) -> NoReturn:
    """Refuse a scenario no ``ENV_ROUTES`` row claims, instead of xfailing it.

    THIS USED TO BE ``pytest.xfail("No harness wired for ...")``, raised from
    ``_harness_env`` at FIXTURE SETUP -- before a single step ran. That made it the one
    routing mechanism with no baseline and no ledger, twenty lines from the dormancy
    converter that refuses a scenario absent from ``dormant_scenarios.txt``. The cost
    was not theoretical: it made a ``scenarios()`` call look like binding a feature.
    Add the call, delete the row from ``UNBOUND_ALLOWLIST`` in
    ``tests/unit/test_architecture_feature_file_bound.py``, and every scenario in the
    file xfails here at setup -- so that guard goes GREEN while the feature grades
    exactly nothing, recorded in no ledger, no xfail census and no dormancy baseline.
    "Unbound and counted" became "bound and counted by nobody", which reads as progress
    on every artifact we have.

    A ledger was considered and is the wrong shape HERE, because the population is
    EMPTY. Measured on run innet_091026_2116 (both BDD suites, both reconciled
    collected == total, both exit 0): ZERO of 11569 scenario items reached this branch
    -- 8657 in bdd_inprocess, 2912 in bdd_e2e. Every bound-but-unwired scenario today
    is claimed by a row that names itself (``uc002-not-wired`` 1236 items,
    ``uc003-not-wired`` 1161, the UC-010 dormancy rows 105). A shrink-only baseline
    seeded with the empty set IS this refusal, and a zero-entry file would only add
    something to grow.

    So refusing costs nothing today and keeps costing nothing for as long as every
    scenario is routed. If you are here because you added a ``scenarios()`` call: that
    is the point. Binding a feature means giving it an ``ENV_ROUTES`` row AND step
    definitions, and this is where you find out you did neither. Do not add a
    ``<uc>-not-wired`` row to get past it -- that is an xfail wearing a route, and it
    puts the scenario back in the population this refusal exists to keep at zero.

    Graded by ``tests/unit/test_architecture_bdd_unrouted_is_refused.py``, which drives
    this fixture for real and carries a routed control, because a mechanism whose
    population is empty is otherwise indistinguishable from one that stopped working.
    """
    pytest.fail(
        f"UNROUTED SCENARIO: no ENV_ROUTES row claims {uc} "
        f"(markers: {sorted(marker_names)}).\n"
        "  It reached _harness_env, so it IS collected -- it just has no environment, "
        "and would otherwise have xfailed at setup before running a single step.\n"
        "  Give the UC an ENV_ROUTES row that builds a real env, or revert the "
        "scenarios() call that bound its feature. A <uc>-not-wired row is not a fix.\n"
        "  If detect_uc returned None, the tag is unrecognised: widen detect_uc.",
        pytrace=False,
    )


def _uc_bucket_rows() -> list[EnvRoute]:
    """The coarse uc-bucket rows, each re-keyed to the bucket it routes.

    A function, not a module-level comprehension, for the same reason
    ``_uc010_dormancy_rows`` is one: ``dataclasses.replace`` is spelled exactly
    like ``Path.replace`` (a rename), so the import-time filesystem-I/O guard —
    which matches by attribute NAME, and says so in its DETECTOR NOTE — reports
    it at module scope. Nothing here needs to be computed during collection, so
    the deferral is free; the call below still runs at import, and ``ENV_ROUTES``
    is unchanged for every consumer that imports it as a module attribute.
    """
    return [dataclasses.replace(route, uc=key) for key, route in _UC_BUCKET_ROUTES.items() if not key.startswith("T-")]


# The coarse uc-bucket rows come last: a predicate row above always wins, which
# preserves the former chain's order (it matched the bucket first only for UCs
# that had NO predicate branches). Tag-keyed rows are already represented above
# as predicate rows, so only the bucket keys are appended.
ENV_ROUTES += _uc_bucket_rows()


@pytest.fixture(autouse=True)
def _harness_env(request: pytest.FixtureRequest, ctx: dict) -> Generator[None, None, None]:
    """Provide the appropriate harness for each BDD scenario.

    - A ``uc`` bucket with no marker_names-based sub-branching (ADMIN, COMPAT,
      UC-GET-PRODUCTS, UC-005, UC-019) is a row in ``ENV_ROUTES`` and goes
      through the one generic ``_run_env_route`` consumer.
    - UC-004 @polling → DeliveryPollEnv
    - UC-004 @webhook → WebhookEnv (unit variant, no DB needed)
    - UC-004 @webhook-reliability → CircuitBreakerEnv (unit variant)
    - A UC recognized by ``storyboard_spec.detect_uc`` but not claimed by any row is
      REFUSED by ``_refuse_unrouted`` below -- it is not xfailed. See that function.
    - A tag ``storyboard_spec.detect_uc`` does not recognize at all reaches the same
      refusal with ``uc=None``. That is a bug, not intended behavior: widen
      ``detect_uc`` instead of relying on this fixture to paper over it.
    """
    # ONE derivation, ONE routing call. The marker set comes from the
    # shared accessor so the conftest and the liveness plugin cannot narrow it
    # differently, and the route comes from the shared resolver so scripts/audit
    # answers the same question the same way.
    marker_names = derive_marker_names(request.node)
    uc = storyboard_spec.detect_uc(marker_names)
    e2e_config = ctx.get("e2e_config")

    # E2E shares one live DB across all scenarios; flush it to a clean baseline so
    # this scenario's harness setup starts fresh (no cross-scenario tenant_id
    # collisions). No-op for the in-process transports (they use per-test DBs).
    if e2e_config is not None:
        _reset_e2e_db(e2e_config)

    route = storyboard_spec.resolve_env_route(marker_names, ENV_ROUTES)
    if route is None:
        _refuse_unrouted(uc, marker_names)
    yield from _run_env_route(request, ctx, route, e2e_config)
