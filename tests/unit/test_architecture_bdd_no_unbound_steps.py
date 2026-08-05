"""Guard: every registered BDD step definition is bound by >=1 feature step.

A step definition no scenario reaches is the step-level twin of the
dormant-scenario anti-pattern: it reads as coverage in the step module and
grades nothing (GH #1751 found two such steps in uc011_accounts.py that also
carried a presence-guard defect — they looked like real graders under two
separate reviews).

Detection approach: behavioral, not source-text. We import every step module
in the universe and read the real ``pytestbdd_stepdef_*`` fixtures pytest-bdd
created at import time; each carries a ``_pytest_bdd_step_context``
(``StepFunctionContext``) holding the actual parser object. We then render the
CONCRETE step corpus from every ``tests/bdd/features/*.feature`` — every
``ScenarioTemplate`` rendered with EVERY Examples context (``examples`` is a
LIST of Examples tables; contexts are the union of ``as_contexts()`` over all
tables, else ``[{}]``; background steps are included by ``render()``). Full
Examples rendering is the correctness trap: outline example CELLS are step
text after rendering, and a renderer that mishandles the Examples list
produces ~235 false unbound instead of the true count. A registration is
UNBOUND iff no corpus step satisfies pytest-bdd 8.1.0's own matching
semantics (``find_fixturedefs_for_step``):
``(ctx.type is None or ctx.type == step.type) and
ctx.parser.is_matching(step.name)``. Using the real parser objects means
``parsers.parse``/``parsers.re`` patterns are resolved, never compared as
literal strings.

Universe: the ``pytest_plugins`` modules from ``tests/bdd/conftest.py`` plus
``tests.bdd.steps.domain.uc019_query_media_buys`` (live via ``from … import *``
in ``tests/bdd/test_uc019_query_media_buys.py`` — intentionally-local, see the
reachability guard's allowlist reason B). EXCLUDED:
``tests/bdd/test_uc018_list_creatives.py``, which defines stepdefs inline but
cannot be imported outside a pytest session (its ``scenarios()`` call raises
``IndexError`` via ``CONFIG_STACK[-1]`` on an empty stack), injects 109
``PytestUnknownMarkWarning`` when imported inside one, and contributes ZERO
unbound registrations (measured: census is 102 with and without it).

Known blind spot — contested steps: "bound" here is weaker than "actually
runs". When 2+ registrations match one rendered step, pytest-bdd sorts the
fixturedefs and only ONE wins (``inject_fixturedefs_for_step``); the losers
execute nothing. Measured on this branch: 68 of 1130 bound registrations only
ever match contested steps, i.e. they may be de facto dormant while this guard
calls them reachable. Reachable-but-shadowed is the jurisdiction of
``test_architecture_bdd_no_shadowed_steps`` (which catches identical
fixture-NAME collisions, not cross-parser shadowing); the winner-resolution
extension is recorded on the dormant-step backlog issue.

Shrinking contract — wire by scenario unit, or delete: an allowlist entry is
removed ONLY by (a) wiring the step's whole scenario unit — authoring or
regenerating the scenario that needs it, which is protocol-behavior work
requiring a spec citation first (spec-grounding gate) — or (b) deleting the
registration when the BDD source hierarchy (generated BR-*.feature, storyboard,
spec) demands no such scenario. Deleting one step of a scenario unit while its
siblings stay allowlisted just orphans half a scenario. The allowlist may only
shrink; the stale-entry check enforces removal once a step becomes bound.

Note on corpus scope: matching runs against ALL feature files, including the
ones no ``scenarios()`` call loads (the dormant-feature class tracked by the
reachability guard). A step bound only by an unloaded feature still grades
nothing today, but it belongs to that other guard's class — folding it in here
would churn this allowlist when those features get wired (measured cost of the
stricter definition: 2 extra entries).
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from tests.unit._architecture_helpers import assert_violations_match_allowlist, bdd_registered_step_plugins

_FEATURES_DIR = Path(__file__).resolve().parents[1] / "bdd" / "features"
_STEPDEF_PREFIX = "pytestbdd_stepdef_"

# Step modules registered OUTSIDE conftest's pytest_plugins but still live:
# uc019 loads via `from … import *` in tests/bdd/test_uc019_query_media_buys.py
# (intentionally-local — reachability guard allowlist reason B).
_LOCAL_STEP_MODULES = ("tests.bdd.steps.domain.uc019_query_media_buys",)

# Unbound step registrations. RATCHETING baseline — may only shrink, never
# grow. Entry key: (defining module dotted name, function name, parser
# pattern). Remove an entry only per the wire-by-scenario-unit-or-delete
# contract in the module docstring.
# FIXME(#1801): wire each step's scenario unit (spec citation first) or
# delete the registration, then remove its entry.
_UNBOUND_STEPS: set[tuple[str, str, str]] = {
    (
        "tests.bdd.steps.domain.uc002_create_media_buy",
        "given_account_other_agent",
        "the account exists but is accessible only to a different agent",
    ),
    (
        "tests.bdd.steps.domain.uc002_create_media_buy",
        "given_natural_key_other_agent",
        "the natural key resolves to an account accessible only to a different agent",
    ),
    (
        "tests.bdd.steps.domain.uc002_create_media_buy",
        "given_package_positive_budget",
        "the package has a positive budget meeting minimum spend",
    ),
    (
        "tests.bdd.steps.domain.uc002_create_media_buy",
        "given_request_includes_packages",
        "the request includes {count:d} package with a valid product_id",
    ),
    (
        "tests.bdd.steps.domain.uc002_create_media_buy",
        "given_sandbox_account_other_agent",
        "the sandbox account exists but is accessible only to a different agent",
    ),
    (
        "tests.bdd.steps.domain.uc002_create_media_buy",
        "given_valid_request_with_table",
        "a valid create_media_buy request with:\n{datatable}",
    ),
    ("tests.bdd.steps.domain.uc002_create_media_buy", "then_remember_field", 'I remember the "{field}" as "{alias}"'),
    (
        "tests.bdd.steps.domain.uc002_create_media_buy",
        "then_response_equals_remembered",
        'the response "{field}" should equal the remembered "{alias}"',
    ),
    (
        "tests.bdd.steps.domain.uc002_create_media_buy",
        "then_response_not_equals_remembered",
        'the response "{field}" should NOT equal the remembered "{alias}"',
    ),
    ("tests.bdd.steps.domain.uc002_nfr", "given_budget_below_minimum", "But the package budget is below the minimum"),
    (
        "tests.bdd.steps.domain.uc003_ext_error_scenarios",
        "given_adapter_error_during_update",
        "the ad server adapter returns an error during update",
    ),
    (
        "tests.bdd.steps.domain.uc003_ext_error_scenarios",
        "then_no_db_records_modified",
        "no database records should be modified",
    ),
    (
        "tests.bdd.steps.domain.uc003_update_media_buy",
        "given_buyer_owns_media_buy",
        "the Buyer owns an existing media buy",
    ),
    (
        "tests.bdd.steps.domain.uc003_update_media_buy",
        "given_frequency_cap_config",
        "the package targeting_overlay includes frequency_cap: {freq_cap_config}",
    ),
    (
        "tests.bdd.steps.domain.uc003_update_media_buy",
        "given_media_buy_exists_with_status",
        'the media buy exists with status "{status}"',
    ),
    (
        "tests.bdd.steps.domain.uc003_update_media_buy",
        "given_media_buy_owned_by_principal",
        'the media buy is owned by principal "{owner_id}"',
    ),
    (
        "tests.bdd.steps.domain.uc003_update_media_buy",
        "given_media_buy_with_owner",
        "the media buy exists with owner {owner}",
    ),
    (
        "tests.bdd.steps.domain.uc003_update_media_buy",
        "given_update_request_no_table",
        "a valid update_media_buy request",
    ),
    (
        "tests.bdd.steps.domain.uc003_update_media_buy",
        "then_affected_packages_present",
        "the response should contain affected_packages",
    ),
    (
        "tests.bdd.steps.domain.uc003_update_media_buy",
        "then_implementation_date_null",
        "the response should contain implementation_date that is null",
    ),
    (
        "tests.bdd.steps.domain.uc006_sync_creatives",
        "when_sync_creative",
        "the Buyer Agent syncs the creative via the MCP tool",
    ),
    (
        "tests.bdd.steps.domain.uc006_sync_creatives",
        "when_sync_creative",
        "the Buyer Agent syncs the creative via the REST/A2A endpoint",
    ),
    (
        "tests.bdd.steps.domain.uc011_accounts",
        "given_agent_b_accounts_same_tenant",
        'agent "{name}" has {count:d} accessible accounts in the same tenant',
    ),
    (
        "tests.bdd.steps.domain.uc011_accounts",
        "given_agent_created_account",
        'agent "{name}" created account for brand domain "{domain}"',
    ),
    (
        "tests.bdd.steps.domain.uc011_accounts",
        "given_agent_granted_access",
        'agent "{a}" was granted access to the account for brand domain "{domain}"',
    ),
    (
        "tests.bdd.steps.domain.uc011_accounts",
        "given_agent_with_n_accounts",
        'agent "{name}" has an authenticated connection with {count:d} accessible accounts',
    ),
    (
        "tests.bdd.steps.domain.uc011_accounts",
        "given_connection_no_principal",
        "the Buyer Agent has a connection with tenant resolved but no principal_id",
    ),
    ("tests.bdd.steps.domain.uc011_accounts", "given_db_failure", "the database is experiencing a transient failure"),
    (
        "tests.bdd.steps.domain.uc011_accounts",
        "given_existing_account_all_fields",
        'an account for brand domain "{domain}" exists with billing "{billing}", payment_terms "{pt}", and governance_agents',
    ),
    (
        "tests.bdd.steps.domain.uc011_accounts",
        "given_existing_account_billing_and_pt",
        'an account for brand domain "{domain}" already exists with billing "{billing}" and payment_terms "{pt}"',
    ),
    (
        "tests.bdd.steps.domain.uc011_accounts",
        "given_existing_account_payment_terms",
        'an account for brand domain "{domain}" already exists with payment_terms "{pt}"',
    ),
    (
        "tests.bdd.steps.domain.uc011_accounts",
        "given_existing_account_with_governance",
        'an account for brand domain "{domain}" already exists with governance_agents',
    ),
    (
        "tests.bdd.steps.domain.uc011_accounts",
        "given_unauthenticated",
        "the Buyer Agent has an unauthenticated connection via {transport}",
    ),
    (
        "tests.bdd.steps.domain.uc011_accounts",
        "then_accounts_from_first_page",
        "the response returns accounts starting from the first page",
    ),
    (
        "tests.bdd.steps.domain.uc011_accounts",
        "then_db_field_unchanged",
        "the account {field} in the database is unchanged from the original",
    ),
    (
        "tests.bdd.steps.domain.uc011_accounts",
        "then_governance_agents_stored",
        'the governance_agents are stored for brand domain "{domain}"',
    ),
    (
        "tests.bdd.steps.domain.uc011_accounts",
        "then_list_includes_domain",
        'the list includes an account with brand domain "{domain}"',
    ),
    (
        "tests.bdd.steps.domain.uc011_accounts",
        "then_no_modifications_for_domain",
        'no accounts were actually modified for brand domain "{domain}"',
    ),
    (
        "tests.bdd.steps.domain.uc011_accounts",
        "then_no_result_for_domain",
        'the response does not include a result for brand domain "{domain}"',
    ),
    (
        "tests.bdd.steps.domain.uc011_accounts",
        "then_none_belong_to_agent",
        'none of the returned accounts belong to agent "{name}"',
    ),
    (
        "tests.bdd.steps.domain.uc011_accounts",
        "then_one_access_grant",
        'the agent has exactly one access grant for brand domain "{domain}"',
    ),
    (
        "tests.bdd.steps.domain.uc011_accounts",
        "when_agent_list_accounts",
        'agent "{name}" sends a list_accounts request',
    ),
    (
        "tests.bdd.steps.domain.uc011_accounts",
        "when_list_accounts_no_principal",
        "the Buyer Agent sends a list_accounts request with no principal_id",
    ),
    (
        "tests.bdd.steps.domain.uc011_accounts",
        "when_list_accounts_via_transport",
        "the Buyer Agent sends a list_accounts request via {transport}",
    ),
    (
        "tests.bdd.steps.domain.uc011_accounts",
        "when_list_accounts_with_explicit_cursor",
        'the Buyer Agent sends a list_accounts request with cursor "{cursor}"',
    ),
    (
        "tests.bdd.steps.domain.uc011_accounts",
        "when_named_agent_sync_delete_missing",
        'agent "{name}" sends a sync_accounts request with delete_missing true and:',
    ),
    (
        "tests.bdd.steps.domain.uc011_accounts",
        "when_resync_identical_all_fields",
        'the Buyer Agent re-syncs with identical billing, payment_terms, and governance_agents for brand "{domain}"',
    ),
    (
        "tests.bdd.steps.domain.uc011_accounts",
        "when_resync_identical_governance",
        'the Buyer Agent re-syncs with identical governance_agents for brand "{domain}"',
    ),
    (
        "tests.bdd.steps.domain.uc011_accounts",
        "when_sync_different_governance",
        'the Buyer Agent sends a sync with different governance_agents for brand "{domain}"',
    ),
    (
        "tests.bdd.steps.domain.uc011_accounts",
        "when_sync_dryrun_and_delete_missing",
        "the Buyer Agent sends a sync_accounts request with dry_run true and delete_missing true and:",
    ),
    (
        "tests.bdd.steps.domain.uc011_accounts",
        "when_sync_no_principal",
        "the Buyer Agent sends a sync_accounts request with no principal_id and:",
    ),
    (
        "tests.bdd.steps.domain.uc011_accounts",
        "when_sync_with_governance_agents",
        'the Buyer Agent sends a sync_accounts request with governance_agents for brand "{domain}"',
    ),
    (
        "tests.bdd.steps.domain.uc019_query_media_buys",
        "then_empty_buys_with_error",
        'empty media_buys with error "{code}"',
    ),
    (
        "tests.bdd.steps.domain.uc019_query_media_buys",
        "then_empty_with_error",
        'the response should include an empty media_buys array with error "{code}"',
    ),
    (
        "tests.bdd.steps.domain.uc019_query_media_buys",
        "when_query_a2a_no_filters",
        "the Buyer Agent sends a get_media_buys request via A2A with no filters",
    ),
    (
        "tests.bdd.steps.domain.uc019_query_media_buys",
        "when_query_mcp_no_filters",
        "the Buyer Agent invokes the get_media_buys MCP tool with no filters",
    ),
    (
        "tests.bdd.steps.generic.given_auth",
        "given_buyer_has_tenant_context_mcp",
        "the Buyer has tenant context via MCP session",
    ),
    (
        "tests.bdd.steps.generic.given_config",
        "given_registry_three_formats_inline",
        'the registry has formats: "{name_a}" ({type_a}), "{name_b}" ({type_b}), "{name_c}" ({type_c})',
    ),
    (
        "tests.bdd.steps.generic.given_config",
        "given_registry_two_formats_inline",
        'the registry has formats: "{name_a}" ({type_a}), "{name_b}" ({type_b})',
    ),
    (
        "tests.bdd.steps.generic.given_media_buy",
        "given_ad_server_rejects_creative_upload",
        "But the ad server rejects the creative upload",
    ),
    ("tests.bdd.steps.generic.given_media_buy", "given_adapter_error", "But the ad server adapter returns an error"),
    (
        "tests.bdd.steps.generic.given_media_buy",
        "given_auction_no_bid_price",
        "But a package selects an auction pricing option but provides no bid_price",
    ),
    (
        "tests.bdd.steps.generic.given_media_buy",
        "given_bid_below_floor",
        "But a package has bid_price {bid:g} but floor_price is {floor:g}",
    ),
    (
        "tests.bdd.steps.generic.given_media_buy",
        "given_both_fixed_and_floor",
        "But a package pricing option has both fixed_price and floor_price set",
    ),
    (
        "tests.bdd.steps.generic.given_media_buy",
        "given_creative_format_mismatch",
        "But a creative's format_id does not match any of the product's supported format_ids",
    ),
    (
        "tests.bdd.steps.generic.given_media_buy",
        "given_creative_format_not_generative",
        "And the creative format is not generative",
    ),
    (
        "tests.bdd.steps.generic.given_media_buy",
        "given_duplicate_product",
        'But both packages reference the same product_id "{product_id}"',
    ),
    ("tests.bdd.steps.generic.given_media_buy", "given_end_before_start", "But end_time is before start_time"),
    (
        "tests.bdd.steps.generic.given_media_buy",
        "given_geo_overlap",
        'But a package targeting_overlay includes "{value}" in both geo_countries and geo_countries_exclude',
    ),
    (
        "tests.bdd.steps.generic.given_media_buy",
        "given_high_daily_spend",
        "But a package has budget {budget:d} over a {days:d}-day flight (daily = {daily:d})",
    ),
    (
        "tests.bdd.steps.generic.given_media_buy",
        "given_inline_creative_missing_url",
        "But a creative is missing the required URL in assets",
    ),
    (
        "tests.bdd.steps.generic.given_media_buy",
        "given_managed_targeting_dimension",
        "But a package targeting_overlay sets a managed-only dimension",
    ),
    (
        "tests.bdd.steps.generic.given_media_buy",
        "given_neither_fixed_nor_floor",
        "But a package pricing option has neither fixed_price nor floor_price",
    ),
    (
        "tests.bdd.steps.generic.given_media_buy",
        "given_neither_fixed_nor_floor",
        "a package pricing option has neither fixed_price nor floor_price",
    ),
    (
        "tests.bdd.steps.generic.given_media_buy",
        "given_nonexistent_pricing_option",
        'But a package references pricing_option_id "{po_id}" not found on the product',
    ),
    (
        "tests.bdd.steps.generic.given_media_buy",
        "given_nonexistent_product",
        'But a package references product_id "{product_id}" which does not exist',
    ),
    (
        "tests.bdd.steps.generic.given_media_buy",
        "given_package_references_missing_creative",
        'But a package creative_assignment references creative_id "{creative_id}"',
    ),
    ("tests.bdd.steps.generic.given_media_buy", "given_past_start_time", 'But start_time is "{value}" (in the past)'),
    (
        "tests.bdd.steps.generic.given_media_buy",
        "given_proposal_budget_guidance_min",
        "But the proposal's total_budget_guidance.min is {amount:d}",
    ),
    (
        "tests.bdd.steps.generic.given_media_buy",
        "given_proposal_not_exists",
        'But proposal "{proposal_id}" does not exist or has expired',
    ),
    (
        "tests.bdd.steps.generic.given_media_buy",
        "given_request_inline_creatives_valid",
        "Given a valid create_media_buy request with inline creatives that passes all validation",
    ),
    (
        "tests.bdd.steps.generic.given_media_buy",
        "given_unknown_targeting_field",
        'But a package targeting_overlay contains unknown field "{field_name}"',
    ),
    (
        "tests.bdd.steps.generic.given_media_buy",
        "given_unsupported_currency",
        'But the packages use currency "{currency}" which is not in the tenant\'s CurrencyLimit table',
    ),
    ("tests.bdd.steps.generic.given_media_buy", "given_zero_budget", "But all package budgets sum to 0"),
    ("tests.bdd.steps.generic.then_payload", "then_returned_type", 'the returned format type should be "{fmt_type}"'),
    ("tests.bdd.steps.generic.when_request", "when_call_mcp", "the Buyer Agent calls list_creative_formats MCP tool"),
    (
        "tests.bdd.steps.generic.when_request",
        "when_call_mcp_no_filters",
        "the Buyer Agent calls list_creative_formats MCP tool with no filters",
    ),
    (
        "tests.bdd.steps.generic.when_request",
        "when_call_mcp_type",
        'the Buyer Agent calls list_creative_formats MCP tool with type "{type_value}"',
    ),
    (
        "tests.bdd.steps.generic.when_request",
        "when_request_asset_types_and_name_search",
        'the Buyer Agent requests formats with asset_types {asset_types} and name_search "{name_search}"',
    ),
    (
        "tests.bdd.steps.generic.when_request",
        "when_send_a2a",
        "the Buyer Agent sends a list_creative_formats task via A2A",
    ),
    (
        "tests.bdd.steps.generic.when_request",
        "when_send_a2a_no_filters",
        "the Buyer Agent sends a list_creative_formats task via A2A with no filters",
    ),
    (
        "tests.bdd.steps.generic.when_request",
        "when_send_a2a_type_filter",
        'the Buyer Agent sends a list_creative_formats task via A2A with type filter "{type_filter}"',
    ),
    (
        "tests.bdd.steps.generic.when_request",
        "when_send_a2a_type_value",
        'the Buyer Agent sends a list_creative_formats task via A2A with type "{type_value}"',
    ),
}


def _step_registrations() -> list[tuple[str, str, str, object]]:
    """Collect deduped stepdef registrations from the module universe.

    Returns (defining module, function name, parser pattern, StepFunctionContext)
    per registration. Deduped by (code filename, first line, parser pattern) —
    NOT by function alone: a dual-decorated function registers one fixture per
    decorator, and one can be bound while its sibling is dormant.
    """
    seen: dict[tuple[str, int, str], tuple[str, str, str, object]] = {}
    for dotted in [*bdd_registered_step_plugins(), *_LOCAL_STEP_MODULES]:
        module = importlib.import_module(dotted)
        for attr, value in vars(module).items():
            if not attr.startswith(_STEPDEF_PREFIX):
                continue
            ctx = getattr(value, "_pytest_bdd_step_context", None)
            if ctx is None:
                continue
            code = ctx.step_func.__code__
            key = (code.co_filename, code.co_firstlineno, ctx.parser.name)
            seen.setdefault(key, (ctx.step_func.__module__, ctx.step_func.__name__, ctx.parser.name, ctx))
    return list(seen.values())


def _rendered_feature_steps() -> set[tuple[str, str]]:
    """Every concrete (type, text) step across all feature files.

    Each ScenarioTemplate is rendered with EVERY Examples context (union over
    the ``examples`` list of tables, else ``[{}]``); ``render()`` prepends
    background steps. Rendering with each context is what turns
    ``<placeholders>`` into the literal cell values that step parsers match.
    """
    from pytest_bdd.feature import get_feature

    corpus: set[tuple[str, str]] = set()
    for path in sorted(_FEATURES_DIR.glob("*.feature")):
        feature = get_feature(str(_FEATURES_DIR), path.name)
        for template in feature.scenarios.values():
            contexts = [context for examples in template.examples for context in examples.as_contexts()] or [{}]
            for context in contexts:
                for step in template.render(context).steps:
                    corpus.add((step.type, step.name))
    return corpus


def _scan_unbound_registrations() -> set[tuple[str, str, str]]:
    """Registrations no concrete feature step reaches (pytest-bdd semantics)."""
    corpus = sorted(_rendered_feature_steps())
    unbound: set[tuple[str, str, str]] = set()
    for module, func_name, pattern, ctx in _step_registrations():
        bound = any(
            (ctx.type is None or ctx.type == step_type) and ctx.parser.is_matching(step_name)
            for step_type, step_name in corpus
        )
        if not bound:
            unbound.add((module, func_name, pattern))
    return unbound


class TestBddNoUnboundSteps:
    """Structural guard: no registered step definition is unreachable."""

    @pytest.mark.arch_guard
    def test_unbound_step_registrations_match_allowlist(self) -> None:
        """Every stepdef must be bound by a rendered feature step or allowlisted.

        An unbound stepdef grades nothing — it reads as coverage in the step
        module while no scenario can ever execute it.
        """
        found = _scan_unbound_registrations()
        assert_violations_match_allowlist(
            found,
            _UNBOUND_STEPS,
            fix_hint=(
                "Wire the step's whole scenario unit (spec citation first — "
                "spec-grounding gate) or delete the registration; then remove "
                "its entry from _UNBOUND_STEPS. Never add new entries."
            ),
        )
