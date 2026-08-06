"""Guard: uc011_accounts.py's 68 account oracles must read the wire, not the
harness-reconstructed TYPED payload.

Sibling to ``test_architecture_bdd_no_typed_sandbox_or_array_oracle.py`` /
``test_architecture_bdd_no_typed_status_oracle.py`` — reuses ``find_typed_field_violations``
directly (no re-implementation of the scan loop). Filed for salesagent-oyiv.8: the
account-list/sync oracles in ``tests/bdd/steps/domain/uc011_accounts.py`` read a field VALUE
off the harness-reconstructed typed payload (``ctx["response"]`` / ``_require_response(ctx)``)
— or a value CACHED from it, ``ctx["last_account"]``, populated by an earlier producer
``@then`` step — instead of ``wire_dict(ctx)`` / ``wire_field(ctx, ...)`` / ``wire_list(ctx,
key)`` (``tests/bdd/steps/_outcome_helpers.py``). On a2a/mcp/rest the typed payload is
reconstructed FROM the wire with fields already coerced to their declared types, so a
wire-format regression (a bool serialized as the string ``"true"``, an enum sent with the
wrong casing, a required key silently dropped) can pass green against a non-conformant wire.

## Detector gaps fixed here (both required — see salesagent-oyiv.8's design notes)

The 68 sites split across 3 detection shapes, 2 of which the base
``find_typed_field_violations``/``_reads_typed_field`` machinery cannot see without the
extensions below (both added to the shared scan function in
``test_architecture_bdd_no_typed_sandbox_or_array_oracle.py``, opt-in via keyword args so the
2 pre-existing sibling guards are unaffected — reverified green in the same change):

1. **Direct read off ``ctx["response"]``/``_require_response(ctx)``** (Groups A/B/C/G, 39
   sites) — already visible to the base detector; no extension needed.
2. **Read cached from the typed payload under ``ctx["last_account"]``** (Groups E/F, 20
   sites) — a producer ``@then`` step stores a typed ``Account`` object in
   ``ctx["last_account"]``; every consumer then does
   ``acct = ctx.get("last_account") or _require_response(ctx).accounts[0]`` (Group E) or the
   bare ``acct = ctx.get("last_account")`` (Group F, no fallback — same disease, no
   ``_require_response`` call in the function's own body at all). Neither shape is visible to
   the base detector's ``_typed_source_var_names``, which only recognizes a local bound
   directly to ``ctx["response"]``/``_require_response(ctx)``. Fixed via
   ``typed_cache_keys=frozenset({"last_account"})``.
3. **Read reached transitively through the module's own ``_find_account_by_brand(resp, ...)``
   helper** (Group D, 8 sites) — ``resp`` is typed, but the field read
   (``acct.action``/``acct.brand.domain``/...) happens on the helper's RETURN value, not on
   ``resp`` directly, so the base detector's single-function syntactic walk cannot see the
   taint. Fixed via ``typed_walker_helpers=frozenset({"_find_account_by_brand"})``, mirroring
   ``test_architecture_bdd_wire_discipline.py``'s own ``_typed_walker_names``/
   ``_walks_typed_shape`` mechanism (Check C's solution to the identical indirection problem).

## Group G: 2 of 3 sites are migrate-blind (documented, not hidden)

All three Group G steps (``then_webhook_registered``, ``then_account_transitions``,
``then_push_sent``) live in exactly ONE scenario
(``features/BR-UC-011-manage-accounts.feature`` L362-364).
``then_webhook_registered`` ends in ``pytest.xfail(...)`` which raises immediately, so
``then_account_transitions`` and ``then_push_sent`` never execute in the corpus today —
their disposition is static (this AST guard), never runtime-verified via a passing scenario.
They are named as guard targets rather than silently omitted (both currently read the typed
payload directly and are correctly flagged below), matching the established dormancy
precedent from salesagent-oyiv.1/.9: the migration itself is still correct even though no
live scenario exercises it, and the guard is what proves the migration happened, not a test
run.

All entries are empty by design — every one of the 68 targets is a defect being eliminated,
not an accepted/tracked exception. No allowlist entry should ever be added for these 68.
"""

from __future__ import annotations

import tests.unit.test_architecture_bdd_no_typed_sandbox_or_array_oracle as sibling_guard
import tests.unit.test_architecture_bdd_wire_discipline as wire_discipline
from tests.unit._architecture_helpers import assert_violations_match_allowlist

_TESTS_ROOT = wire_discipline._TESTS_ROOT

_TYPED_CACHE_KEYS: frozenset[str] = frozenset({"last_account"})
_TYPED_WALKER_HELPERS: frozenset[str] = frozenset({"_find_account_by_brand"})

_TARGET_FUNCS: dict[str, tuple[str, ...]] = {
    "bdd/steps/domain/uc011_accounts.py": (
        # ── Group A — top-level accounts array/list content (31 sites) ──
        "then_accounts_array_count",
        "then_accounts_have_fields",
        "then_accounts_are_agent_scoped",
        "then_only_status",
        "then_other_statuses_excluded",
        "then_empty_accounts",
        "then_n_accounts",
        "then_n_more_accounts",
        "then_accounts_from_first_page",
        "then_all_statuses_present",
        "then_result_set_identical",
        "then_response_outcome",
        "then_success_with_accounts",
        "then_n_account_results",
        "then_all_accounts_echo_brand",
        "then_has_accounts_array",
        "then_no_operation_errors",
        "then_response_is_success_variant",
        "then_all_accounts_action",
        "then_no_operation_level_errors",
        "then_dry_run_true",
        "then_no_dry_run_include",
        "then_account_in_db",
        "then_only_included_processed",
        "then_account_sandbox_true",  # also a Group-E producer (ctx["last_account"] = acct)
        "then_all_accounts_sandbox_true",
        "then_no_production_accounts",
        "then_none_belong_to_agent",
        "then_none_have_brand_domain",
        "then_list_includes_domain",
        "then_no_result_for_domain",
        # ── Group B — pagination metadata (2 sites) ──
        "then_pagination_has_more_with_cursor",
        "then_pagination_has_more",
        # ── Group C — context echo field (3 sites) ──
        "then_response_includes_context",
        "then_context_identical",
        "then_no_context",
        # ── Group D — brand-lookup indirection via _find_account_by_brand (8 sites) ──
        "then_account_action_with_brand_id",  # producer
        "then_account_action",  # producer
        "then_brand_echoed",
        "then_per_account_brand_echo",
        "then_account_shows_action",  # producer
        "then_deactivation_result",
        "then_account_unchanged_or_updated",  # producer
        "then_account_processed_normally",  # producer
        # ── Group E — ctx["last_account"] idiom consumers, with the
        #    _require_response(ctx).accounts[0] fallback (14 sites) ──
        "then_account_has_id",
        "then_account_status",
        "then_account_action_generic",
        "then_account_operator",
        "then_account_billing",
        "then_has_setup",
        "then_setup_has_message",
        "then_setup_message_present",
        "then_setup_has_url",
        "then_setup_has_expires",
        "then_setup_no_url",
        "then_no_setup",
        "then_sandbox_account_has_id",
        "then_no_real_platform_account",
        # ── Group F — ctx["last_account"]-ONLY consumers, per-account ERROR content,
        #    no fallback expression in the function's own body (6 sites) ──
        "then_error_has_suggestion",
        "then_failed_has_errors",
        "then_per_account_error_code",
        "then_billing_error_message",
        "then_failed_status_with_error",
        "then_db_field_unchanged",
        # ── Group G — dormant/xfail trio; then_webhook_registered is the one
        #    e2e_rest-ledgered, live-executing site, the other two are migrate-blind
        #    (never execute — the scenario xfails on then_webhook_registered first) (3 sites) ──
        "then_webhook_registered",
        "then_account_transitions",
        "then_push_sent",
        # ── Scope-leak: same disease, a `when_` step (not `@then`), folded in per the
        #    architect review's "cheapest correct fix" (1 site) ──
        "when_list_accounts_with_cursor",
    ),
}

# Empty by design — see module docstring. No entry is legitimate for any of these 68 sites;
# the migration (salesagent-oyiv.8's implementation atom) is what clears every one of them.
_ALLOWLIST: set[str] = set()


def _find_violations() -> set[str]:
    return sibling_guard.find_typed_field_violations(
        _TARGET_FUNCS,
        typed_cache_keys=_TYPED_CACHE_KEYS,
        typed_walker_helpers=_TYPED_WALKER_HELPERS,
    )


def test_no_typed_account_oracle() -> None:
    """All 68 uc011_accounts.py account oracles must read wire_dict(ctx)/wire_field(ctx,
    ...)/wire_list(ctx, key) (or a wire-dict-based per-account helper), never a value off
    ctx['response']/_require_response(ctx)/ctx['last_account'] (when it holds a typed
    object)."""
    assert_violations_match_allowlist(
        _find_violations(),
        _ALLOWLIST,
        fix_hint=(
            "One of the 68 uc011 account-oracle targets still reads a field value off the "
            "typed ctx['response'] payload, a value cached from it in ctx['last_account'], or "
            "a value reached transitively through _find_account_by_brand(resp, ...), instead "
            "of wire_dict(ctx)/wire_field(ctx, ...)/wire_list(ctx, key). Migrate the read onto "
            "the wire accessor (see salesagent-oyiv.8's design for the target helper shapes: "
            "_wire_accounts/_find_wire_account_by_brand/_last_wire_account), then remove the "
            "entry from _ALLOWLIST in this file — it can only shrink."
        ),
    )


def test_meta_scan_set_matches_sixty_eight_targets() -> None:
    """Pin the scan set at exactly the 68 functions this ticket's scope covers.

    An empty/misconfigured _TARGET_FUNCS would make the guard above vacuously pass — this
    catches that independent of the allowlist's own content. 68 = 31 (Group A) + 2 (Group B)
    + 3 (Group C) + 8 (Group D) + 14 (Group E) + 6 (Group F) + 3 (Group G) + 1 (scope-leak
    when_ step) — the full disposition table from salesagent-oyiv.8's research.
    """
    all_targets = {f"{rel} {name}" for rel, names in _TARGET_FUNCS.items() for name in names}
    assert len(all_targets) == 68, (
        f"Expected exactly 68 target functions, got {len(all_targets)}: {sorted(all_targets)}"
    )
    for rel in _TARGET_FUNCS:
        assert (_TESTS_ROOT / rel).is_file(), f"{rel} does not exist under {_TESTS_ROOT}"


# ── Detector meta-tests (positive/negative controls on the detection logic itself) ──
# Same rationale as the 2 sibling guards' own meta-tests: an AST-walk guard has no
# "regex-slip" failure mode, so a positive + a negative control on the underlying helpers is
# the applicable bar — proving the detector actually discriminates a typed-payload read
# (direct, cached-via-ctx, or reached via a named walker helper) from a wire read,
# independent of what the live tree currently contains.


def test_detector_flags_a_typed_payload_read_positive_control() -> None:
    """Positive control: a function binding ctx['response'] then reading a field off it via
    getattr must be flagged — the same base-shape disease the 2 sibling guards already pin,
    reused here to confirm this guard's own _find_violations wiring (target funcs +
    cache-key/walker-helper extensions) does not accidentally suppress the base case."""
    func = sibling_guard._parse_single_function(
        "def then_example(ctx):\n"
        "    resp = ctx.get('response')\n"
        "    value = getattr(resp, 'some_field', None)\n"
        "    assert value is not None\n"
    )
    typed_vars = sibling_guard._typed_source_var_names_extended(func, _TYPED_CACHE_KEYS)
    assert typed_vars == {"resp"}, f"Expected 'resp' recognized as a typed-response local, got {typed_vars}"
    assert sibling_guard._reads_typed_field(func, typed_vars), (
        "Detector failed to flag a function that reads a field value off a typed "
        "ctx['response'] local via getattr — this is exactly the disease pattern this guard "
        "eliminates; a detector that misses it provides no regression protection."
    )


def test_detector_flags_a_cached_last_account_read_positive_control() -> None:
    """Positive control for the Group E/F extension: a function reading
    ctx.get('last_account') (bare, Group F's shape — no _require_response fallback) and then
    dereferencing a field off it must be flagged."""
    func = sibling_guard._parse_single_function(
        "def then_example(ctx):\n"
        "    acct = ctx.get('last_account')\n"
        "    assert acct is not None\n"
        "    errors = acct.errors\n"
        "    assert errors\n"
    )
    typed_vars = sibling_guard._typed_source_var_names_extended(func, _TYPED_CACHE_KEYS)
    assert typed_vars == {"acct"}, f"Expected 'acct' recognized as a cached-typed local, got {typed_vars}"
    assert sibling_guard._reads_typed_field(func, typed_vars), (
        "Detector failed to flag a function that reads a field value off a value cached from "
        "the typed payload in ctx['last_account'] — Group F's exact disease shape."
    )


def test_detector_flags_a_walker_helper_read_positive_control() -> None:
    """Positive control for the Group D extension: a function that resolves an account via
    the module's own _find_account_by_brand(resp, ...) helper, then reads a field off the
    returned value, must be flagged even though the field read never touches ``resp``
    directly."""
    func = sibling_guard._parse_single_function(
        "def then_example(ctx):\n"
        "    resp = ctx.get('response')\n"
        "    acct = _find_account_by_brand(resp, 'example.com')\n"
        "    actual = acct.action\n"
        "    assert actual == 'created'\n"
    )
    typed_vars = sibling_guard._typed_source_var_names_extended(func, _TYPED_CACHE_KEYS)
    typed_vars |= sibling_guard._vars_from_typed_walker_calls(func, typed_vars, _TYPED_WALKER_HELPERS)
    assert typed_vars == {"resp", "acct"}, f"Expected 'resp' and 'acct' recognized as typed locals, got {typed_vars}"
    assert sibling_guard._reads_typed_field(func, typed_vars), (
        "Detector failed to flag a function that reads a field value off the return value of "
        "a known typed-walker helper (_find_account_by_brand) — Group D's exact disease shape."
    )


def test_detector_does_not_flag_a_wire_read_negative_control() -> None:
    """Negative control: a function reading the same field via wire_dict(ctx) (no
    ctx['response']/_require_response/ctx['last_account'] source at all, and no call to a
    typed-walker helper) must NOT be flagged — proves the detector discriminates rather than
    matching on the mere presence of a dict/attribute read."""
    func = sibling_guard._parse_single_function(
        "def then_example(ctx):\n"
        "    wire = wire_dict(ctx)\n"
        "    value = wire.get('some_field')\n"
        "    assert value is not None\n"
    )
    typed_vars = sibling_guard._typed_source_var_names_extended(func, _TYPED_CACHE_KEYS)
    typed_vars |= sibling_guard._vars_from_typed_walker_calls(func, typed_vars, _TYPED_WALKER_HELPERS)
    assert typed_vars == set(), f"Expected no typed-response local bound, got {typed_vars}"
    assert not sibling_guard._reads_typed_field(func, typed_vars), (
        "Detector flagged a function that only reads wire_dict(ctx) — it has no typed source "
        "at all, so this is a false positive."
    )


def test_detector_does_not_flag_a_migrated_cache_var_dict_get_negative_control() -> None:
    """Negative control for the Group E/F migration itself: a cache-key-sourced var (``acct``,
    still bound from ``ctx.get('last_account')`` since the disease and its fix share the same
    identifier) that reads via ``.get(...)`` — the CORRECT post-migration wire-dict access —
    must NOT be flagged. Account/SyncResponseAccount are Pydantic models with no ``.get()``
    method, so this call shape can only ever be the fixed form, never a live violation. Without
    this exclusion, migrating ``acct.errors`` to ``acct.get('errors')`` still trips the detector
    on the unchanged ``ast.Attribute(value=Name('acct'), attr='get')`` shape — a real false
    positive hit during salesagent-oyiv.8's Commit 2 (Group F sites), fixed in
    ``_reads_typed_field``."""
    func = sibling_guard._parse_single_function(
        "def then_example(ctx):\n"
        "    acct = ctx.get('last_account')\n"
        "    errors = acct.get('errors')\n"
        "    assert errors\n"
    )
    typed_vars = sibling_guard._typed_source_var_names_extended(func, _TYPED_CACHE_KEYS)
    assert typed_vars == {"acct"}, f"Expected 'acct' recognized as a cached-typed local, got {typed_vars}"
    assert not sibling_guard._reads_typed_field(func, typed_vars), (
        "Detector flagged 'acct.get(...)' on a cache-key-sourced var as a typed-field read — "
        "Account/SyncResponseAccount have no .get() method, so this can only be the correct "
        "post-migration wire-dict access."
    )
