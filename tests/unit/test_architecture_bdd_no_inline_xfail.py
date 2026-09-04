"""Exact-set lock for the FOURTH escape-hatch route: an inline xfail in a step body.

``test_architecture_e2e_rest_escape_hatches.py`` enumerates three routes that
turn a failing scenario into a non-blocking xfail and gives each an exact-set
AST lock:

1. the nodeid ledger (``test_e2e_rest_ledger_state.py``);
2. an ``is_e2e_rest``-gated xfail route in the BDD conftest's
   ``pytest_collection_modifyitems``;
3. an env-level ``E2EUnsupportedSetup`` declaration in ``tests/harness/``.

Route 4 — a bare ``pytest.xfail(...)`` called from inside a step definition (or
a private helper a step calls) — had no lock. It is the route with the *least*
review surface of the four: it registers nothing, it is keyed to no scenario
tag, no ledger equality test can see it, and because ``pytest.xfail()`` raises
immediately it also ABORTS the scenario, silently killing every later
assertion in the same scenario as dead code.

Core invariant (#1858 round-2): **a "known gap" is registered
exactly ONE way in this repo — a scenario/Examples-row tag in the ratcheted
ledger — never as a per-assertion escape hatch inside a step body.**

Why this guard is NOT hosted in ``test_architecture_bdd_assertion_strength.py``:
that module scans via ``tests/unit/_bdd_guard_helpers.iter_then_functions``,
which yields ONLY ``@then``-decorated functions and SKIPS files whose name
starts with ``_``. It is therefore structurally blind to a private module
helper such as ``_response_or_xfail`` — the exact shape this guard outlaws.
This module follows ``test_architecture_bdd_no_swallowed_dispatch_errors.py``
instead: every ``FunctionDef``/``AsyncFunctionDef`` in every ``.py`` under
``tests/bdd/steps``, plus module scope.

Detection is AST (``ast.Call`` resolving to the name ``xfail``), never grep:
prose in a docstring that mentions ``pytest.xfail(...)`` is not a call and must
not count, and a count "achieved" by editing prose must not turn the guard
green.

Both detectors are exercised by meta-tests below against known-bad and
known-good synthetic sources, so a detector regression cannot silently blind
the lock (repo precedent: #1498).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.unit._architecture_helpers import (
    assert_detector_catches_ast_snippets,
    assert_violations_match_allowlist,
)

_BDD_STEPS_DIR = Path(__file__).resolve().parents[1] / "bdd" / "steps"

#: Triple identifying one inline-xfail site: (path relative to tests/bdd/steps,
#: enclosing function name or "<module>", number of xfail calls in that scope).
XfailSite = tuple[str, str, int]

MODULE_SCOPE = "<module>"


def _is_xfail_call(node: ast.Call) -> bool:
    """Match a call whose callee resolves to the name ``xfail``.

    Covers the bare ``xfail(...)`` (``from pytest import xfail``), the
    attribute ``pytest.xfail(...)``, and ``pytest.mark.xfail(...)`` used
    inside a step module — all three are the same route: a step-local xfail
    that no ledger equality test can observe.
    """
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == "xfail"
    return isinstance(func, ast.Attribute) and func.attr == "xfail"


def find_inline_xfail_calls(tree: ast.Module) -> list[tuple[int, str]]:
    """Return ``(lineno, enclosing function name)`` for every inline xfail call.

    The walk tracks the enclosing scope explicitly so a call inside a nested
    helper attributes to that helper, and a call at module level attributes to
    ``"<module>"``. A docstring or comment mentioning ``pytest.xfail`` is not
    an ``ast.Call`` and is therefore never reported.
    """
    found: list[tuple[int, str]] = []

    def _visit(node: ast.AST, scope: str) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scope = node.name
        if isinstance(node, ast.Call) and _is_xfail_call(node):
            found.append((node.lineno, scope))
        for child in ast.iter_child_nodes(node):
            _visit(child, scope)

    _visit(tree, MODULE_SCOPE)
    return found


def collect_xfail_sites(tree: ast.Module, relative_path: str) -> set[XfailSite]:
    """Return the ``(relative_path, function_name, xfail_call_count)`` triples.

    The COUNT element is load-bearing: ``(path, function)`` pairs alone would
    let a hatch be deleted in one branch of an already-listed function and
    re-added in another with no failure.
    """
    counts: dict[str, int] = {}
    for _lineno, scope in find_inline_xfail_calls(tree):
        counts[scope] = counts.get(scope, 0) + 1
    return {(relative_path, scope, count) for scope, count in counts.items()}


def step_module_paths() -> list[Path]:
    """Every module under tests/bdd/steps — helpers and ``_``-prefixed files INCLUDED."""
    return sorted(_BDD_STEPS_DIR.rglob("*.py"))


def _scan_bdd_steps() -> set[XfailSite]:
    found: set[XfailSite] = set()
    for py_file in step_module_paths():
        tree = ast.parse(py_file.read_text(), filename=str(py_file))
        relative = str(py_file.relative_to(_BDD_STEPS_DIR))
        found |= collect_xfail_sites(tree, relative)
    return found


# ---------------------------------------------------------------------------
# The pin. Allowlists can only SHRINK — never add a new triple, fix it instead.
# ---------------------------------------------------------------------------
# FIXME(#1858): 101 pre-existing inline-xfail sites (135 calls across 10 files)
# predate that Core Invariant. They are PINNED here, not swept: each must
# migrate to a ledger tag (tests/bdd/conftest.py's *_XFAIL_TAGS maps, or
# tests/bdd/e2e_rest_known_failures.txt) as its use case is next touched.
# One reference for the whole set — deliberately NOT 101 annotated call sites.
#
# tests/bdd/steps/domain/uc006_storyboard_creative_sync.py must appear ZERO
# times: any triple bearing that path is a NEW violation, not an allowlisted one.
_ALLOWLIST: set[XfailSite] = {
    ("domain/uc002_create_media_buy.py", "_assert_pipeline_routing", 1),
    ("domain/uc002_create_media_buy.py", "then_webhook_notification", 1),
    ("domain/uc002_nfr.py", "then_response_within_sla", 1),
    ("domain/uc002_task_query.py", "_xfail_on_unsupported_param", 1),
    ("domain/uc003_ext_error_scenarios.py", "given_seller_minimum_budget", 1),
    ("domain/uc004_delivery.py", "_assert_placements_sorted_by", 2),
    ("domain/uc004_delivery.py", "then_attribution_default", 1),
    ("domain/uc004_delivery.py", "then_geo_system", 1),
    ("domain/uc004_delivery.py", "then_packages_include_breakdown", 1),
    ("domain/uc006_sync_creatives.py", "_assert_generative_build", 1),
    ("domain/uc006_sync_creatives.py", "_assert_standard_processing", 1),
    ("domain/uc006_sync_creatives.py", "_xfail_if_e2e", 1),
    ("domain/uc006_sync_creatives.py", "given_principal_no_associated_tenant", 1),
    ("domain/uc006_sync_creatives.py", "given_product_format_ids_using_format_id_key", 1),
    ("domain/uc006_sync_creatives.py", "then_asset_has_provenance_not_inherited", 3),
    ("domain/uc006_sync_creatives.py", "then_assignment_created_as_paused", 1),
    ("domain/uc006_sync_creatives.py", "then_assignment_created_as_paused_no_delivery", 1),
    ("domain/uc006_sync_creatives.py", "then_assignment_created_with_specified_weight", 1),
    ("domain/uc006_sync_creatives.py", "then_assignment_created_with_weight", 1),
    ("domain/uc006_sync_creatives.py", "then_assignment_errors_contain_package_id", 2),
    ("domain/uc006_sync_creatives.py", "then_assignment_includes_placement", 1),
    ("domain/uc006_sync_creatives.py", "then_assignment_results_list_assigned_packages", 1),
    ("domain/uc006_sync_creatives.py", "then_assignment_skipped_with_warning", 2),
    ("domain/uc006_sync_creatives.py", "then_compatible_package_assignment_created", 1),
    ("domain/uc006_sync_creatives.py", "then_creative_a_more_delivery_than_b", 2),
    ("domain/uc006_sync_creatives.py", "then_creative_action_created", 1),
    ("domain/uc006_sync_creatives.py", "then_creative_action_created_or_updated", 1),
    ("domain/uc006_sync_creatives.py", "then_creative_action_failed", 1),
    ("domain/uc006_sync_creatives.py", "then_creative_associated_with_principal", 1),
    ("domain/uc006_sync_creatives.py", "then_creative_has_generated_content", 2),
    ("domain/uc006_sync_creatives.py", "then_creative_validated_by_agent", 1),
    ("domain/uc006_sync_creatives.py", "then_error_assignment_creative_id_required", 1),
    ("domain/uc006_sync_creatives.py", "then_error_assignment_package_id_required", 1),
    ("domain/uc006_sync_creatives.py", "then_error_code_with_suggestion", 2),
    ("domain/uc006_sync_creatives.py", "then_existing_assignment_updated_not_duplicated", 1),
    ("domain/uc006_sync_creatives.py", "then_existing_creative_updated_by_triple_key", 1),
    ("domain/uc006_sync_creatives.py", "then_formats_match_after_url_normalization", 2),
    ("domain/uc006_sync_creatives.py", "then_formats_match_using_format_id_key", 2),
    ("domain/uc006_sync_creatives.py", "then_generative_build_skipped", 1),
    ("domain/uc006_sync_creatives.py", "then_generative_build_uses_prompt", 1),
    ("domain/uc006_sync_creatives.py", "then_invalid_creative_action", 1),
    ("domain/uc006_sync_creatives.py", "then_invoke_generative_with_asset_prompt", 1),
    ("domain/uc006_sync_creatives.py", "then_media_buy_status_should_remain", 1),
    ("domain/uc006_sync_creatives.py", "then_new_creative_created_for_principal", 1),
    ("domain/uc006_sync_creatives.py", "then_no_field_level_merging", 2),
    ("domain/uc006_sync_creatives.py", "then_nonexistent_package_reported_as_warning", 1),
    ("domain/uc006_sync_creatives.py", "then_operation_fails_with_assignment_error", 7),
    ("domain/uc006_sync_creatives.py", "then_operation_should_abort_package_not_found", 2),
    ("domain/uc006_sync_creatives.py", "then_preview_urls_generated", 1),
    ("domain/uc006_sync_creatives.py", "then_proceed_with_resolved_account", 1),
    ("domain/uc006_sync_creatives.py", "then_processed_as_generative", 1),
    ("domain/uc006_sync_creatives.py", "then_processed_without_external_validation", 1),
    ("domain/uc006_sync_creatives.py", "then_processing_continues_normally", 1),
    ("domain/uc006_sync_creatives.py", "then_response_includes_assignment_errors", 2),
    ("domain/uc006_sync_creatives.py", "then_response_includes_assignment_errors_for_nonexistent", 2),
    ("domain/uc006_sync_creatives.py", "then_response_includes_creative_with_assignment_results", 1),
    ("domain/uc006_sync_creatives.py", "then_response_includes_one_creative_with_action", 1),
    ("domain/uc006_sync_creatives.py", "then_review_workflow_with_ai", 1),
    ("domain/uc006_sync_creatives.py", "then_second_is_idempotent_upsert", 1),
    ("domain/uc006_sync_creatives.py", "then_system_should_reject_validation_error", 4),
    ("domain/uc006_sync_creatives.py", "then_two_assignments_created_successfully", 1),
    ("domain/uc006_sync_creatives.py", "then_uc006_result_should_be", 2),
    ("domain/uc006_sync_creatives.py", "then_user_assets_preserved", 1),
    ("domain/uc006_sync_creatives.py", "then_user_assets_priority_over_generated", 2),
    ("domain/uc006_sync_creatives.py", "then_valid_assignment_created", 3),
    ("domain/uc006_sync_creatives.py", "then_valid_creative_action", 1),
    ("domain/uc006_sync_creatives.py", "then_valid_not_affected_by_invalid", 1),
    ("domain/uc011_accounts.py", "then_account_transitions", 1),
    ("domain/uc011_accounts.py", "then_push_sent", 1),
    ("domain/uc011_accounts.py", "then_response_includes_context", 1),
    ("domain/uc011_accounts.py", "then_webhook_registered", 1),
    ("domain/uc019_query_media_buys.py", "_assert_flight_dates_present", 1),
    ("domain/uc019_query_media_buys.py", "given_creative_status_extra", 1),
    ("domain/uc019_query_media_buys.py", "given_creative_status_simple", 1),
    ("domain/uc019_query_media_buys.py", "given_creative_status_with_reason", 1),
    ("domain/uc019_query_media_buys.py", "given_no_creative_exists", 1),
    ("domain/uc019_query_media_buys.py", "given_package_creative_assignment", 1),
    ("domain/uc019_query_media_buys.py", "given_package_creative_ref_nonexistent", 1),
    ("domain/uc019_query_media_buys.py", "then_package_details", 1),
    ("domain/uc026_package_media_buy.py", "then_catalogs_unchanged", 1),
    ("domain/uc026_package_media_buy.py", "then_goals_unchanged", 1),
    ("domain/uc026_package_media_buy.py", "then_keyword_bid_ceiling", 1),
    ("domain/uc026_package_media_buy.py", "then_keyword_updated_bid", 2),
    ("domain/uc026_package_media_buy.py", "then_keyword_with_match_type", 1),
    ("domain/uc026_package_media_buy.py", "then_negative_keyword", 1),
    ("domain/uc026_package_media_buy.py", "then_no_delivery", 1),
    ("domain/uc026_package_media_buy.py", "then_old_audience_absent", 1),
    ("domain/uc026_package_media_buy.py", "then_old_catalog_absent", 1),
    ("domain/uc026_package_media_buy.py", "then_package_default_formats", 1),
    ("domain/uc026_package_media_buy.py", "then_paused_unchanged", 2),
    ("domain/uc026_package_media_buy.py", "then_pkg_goals", 1),
    ("domain/uc026_package_media_buy.py", "then_pkg_has_keyword", 2),
    ("domain/uc026_package_media_buy.py", "then_pricing_defaults", 1),
    ("domain/uc026_package_media_buy.py", "then_resume_delivery", 1),
    ("domain/uc026_package_media_buy.py", "then_targeting_audience", 2),
    ("domain/uc026_package_media_buy.py", "then_updated_keyword_and_negative", 2),
    ("generic/given_media_buy.py", "given_product_minimum_spend", 1),
    ("generic/given_media_buy.py", "given_proposal_budget_guidance_min", 1),
    ("generic/given_media_buy.py", "given_proposal_not_exists", 1),
}

_FIX_HINT = (
    "Register the gap the ONE sanctioned way: a scenario/Examples-row tag in the "
    "ratcheted ledger (a *_XFAIL_TAGS map in tests/bdd/conftest.py, or a nodeid in "
    "tests/bdd/e2e_rest_known_failures.txt), and make the Then step assert the "
    "obligation unconditionally. An inline pytest.xfail registers nothing, is keyed "
    "to no scenario, and aborts every later assertion in the scenario as dead code."
)


class TestBddNoInlineXfail:
    """Structural guard: no inline xfail call in any tests/bdd/steps module."""

    @pytest.mark.arch_guard
    def test_no_new_inline_xfail(self):
        found = _scan_bdd_steps()
        new = found - _ALLOWLIST
        assert not new, (
            f"Found {len(new)} inline-xfail site(s) in tests/bdd/steps that are not "
            "in the pinned allowlist:\n"
            + "\n".join(f"  {path}::{func} ({count} call(s))" for path, func, count in sorted(new))
            + "\n\n"
            + _FIX_HINT
        )

    @pytest.mark.arch_guard
    def test_allowlist_matches_tree_exactly(self):
        """Exact-set equality: a NEW site and a STALE entry both fail here.

        Equality in both directions is what makes the pin a ratchet — deleting
        an inline xfail without removing its triple fails just as loudly as
        adding one, so the count can only go down and every move is reviewed
        in the same change.
        """
        assert_violations_match_allowlist(
            _scan_bdd_steps(),
            _ALLOWLIST,
            fix_hint=(
                "Removed an inline xfail? Delete its triple from _ALLOWLIST in the same change "
                "(allowlists only shrink). Added one? " + _FIX_HINT
            ),
        )


class TestDetectorMetaTests:
    """Meta-tests: the LIVE detector catches known-bad and passes known-good shapes."""

    @pytest.mark.arch_guard
    def test_detector_catches_known_bad(self):
        assert_detector_catches_ast_snippets(
            lambda tree: [line for line, _ in find_inline_xfail_calls(tree)],
            snippets={
                "inline-xfail-in-then-body": (
                    "@then('the result is graded')\n"
                    "def then_result(ctx):\n"
                    "    if ctx['response'].status is None:\n"
                    '        pytest.xfail("SPEC-PRODUCTION GAP: status never populated")\n'
                ),
                "inline-xfail-in-private-helper": (
                    "def _response_or_xfail(ctx, expectation):\n"
                    "    resp = ctx.get('response')\n"
                    "    if resp is not None:\n"
                    "        return resp\n"
                    '    pytest.xfail(f"SPEC-PRODUCTION GAP: {expectation}")\n'
                ),
                "bare-imported-xfail": ("from pytest import xfail\n\ndef then_result(ctx):\n    xfail('gap')\n"),
                "xfail-at-module-scope": ("import pytest\n\npytest.xfail('whole module is a gap')\n"),
                "pytest-mark-xfail-in-step-module": (
                    "def then_result(ctx):\n    item.add_marker(pytest.mark.xfail(reason='gap', strict=False))\n"
                ),
            },
        )

    @pytest.mark.arch_guard
    def test_detector_passes_known_good(self):
        good_snippets = {
            # C5: the target file's module docstring ADVERTISES the practice in prose.
            # An AST detector must not count it — and a prose edit must not be able to
            # turn the guard green while calls remain.
            "docstring-prose-mentioning-xfail": (
                '"""Steps that honestly ``pytest.xfail("SPEC-PRODUCTION GAP: ...")`` when\n'
                'production diverges from the pinned spec."""\n'
                "\n"
                "def then_result(ctx):\n"
                "    assert ctx['response'].status == 'approved'\n"
            ),
            "comment-mentioning-xfail": (
                "def then_result(ctx):\n"
                "    # do NOT pytest.xfail() here — ledger the scenario tag instead\n"
                "    assert ctx['response'].status == 'approved'\n"
            ),
            "string-literal-mentioning-xfail": (
                "def then_result(ctx):\n"
                "    reason = 'pytest.xfail is banned in step bodies'\n"
                "    assert reason in ctx['docs']\n"
            ),
            "pytest-fail-is-a-hard-failure": (
                "def then_result(ctx):\n    if ctx['response'] is None:\n        pytest.fail('dispatch failed')\n"
            ),
            "pytest-skip-is-a-different-route": (
                "def then_result(ctx):\n    if ctx['transport'] == 'e2e_rest':\n        pytest.skip('no surface')\n"
            ),
            "name-containing-xfail-is-not-a-call": (
                "def then_result(ctx):\n    xfail_reason = ctx.get('xfail_reason')\n    assert xfail_reason is None\n"
            ),
        }
        for label, source in good_snippets.items():
            tree = ast.parse(source, filename=f"<known-good:{label}>")
            assert not find_inline_xfail_calls(tree), f"False positive on known-good: {label}"

    @pytest.mark.arch_guard
    def test_triples_carry_per_function_call_counts(self):
        """The COUNT element closes the same-function swap.

        ``(path, function)`` pairs alone would let one xfail be deleted and
        another added inside an already-listed function with no failure — the
        hole that matters most in uc006_sync_creatives.py, which carries 87
        calls across only 59 functions.
        """
        source = (
            "def then_two_hatches(ctx):\n"
            "    if ctx['a'] is None:\n"
            "        pytest.xfail('gap a')\n"
            "    if ctx['b'] is None:\n"
            "        pytest.xfail('gap b')\n"
            "\n"
            "def then_one_hatch(ctx):\n"
            "    pytest.xfail('gap c')\n"
            "\n"
            "pytest.xfail('module scope')\n"
        )
        tree = ast.parse(source, filename="<counts>")
        assert collect_xfail_sites(tree, "domain/fake.py") == {
            ("domain/fake.py", "then_two_hatches", 2),
            ("domain/fake.py", "then_one_hatch", 1),
            ("domain/fake.py", MODULE_SCOPE, 1),
        }

    @pytest.mark.arch_guard
    def test_scan_covers_underscore_prefixed_modules(self):
        """The scan domain must include ``_``-prefixed step modules.

        ``iter_then_functions`` (the assertion-strength host) skips them, which
        is precisely why this guard does not live there.
        """
        from tests.unit._bdd_guard_helpers import iter_then_functions

        scanned = {p.name for p in step_module_paths()}
        underscore_modules = {p.name for p in _BDD_STEPS_DIR.rglob("_*.py")}
        assert underscore_modules, "expected at least one _-prefixed module under tests/bdd/steps"
        assert underscore_modules <= scanned, (
            f"scan domain misses _-prefixed modules: {sorted(underscore_modules - scanned)}"
        )

        # And the host this guard deliberately does NOT use cannot see them.
        then_host_files = {key.split(":")[0].rsplit("/", 1)[-1] for key, _func in iter_then_functions()}
        assert not (underscore_modules & then_host_files), (
            "iter_then_functions unexpectedly yields _-prefixed modules — re-evaluate the host choice"
        )
