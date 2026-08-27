"""Lock tests for the e2e_rest xfail ledgers (#1418, Wave 3).

Two ledgers, one discipline. The nodeid ledger below is the original; the
``_UC004_E2E_WEBHOOK_INTERNAL_TAGS`` tag set in ``tests/bdd/conftest.py`` is the
second — a blanket ``is_e2e_rest`` xfail route whose CONTENTS were protected only
by a prose comment, which is why the set grew. Both are pinned by exact-set
equality in BOTH directions here.

The ledger (``tests/bdd/e2e_rest_known_failures.txt``) is a shrinking work-list of
e2e_rest BDD scenarios that fail over real HTTP. Wave 3 graduated every scenario
that now passes in-network and moved every format-injection-only scenario to an
env-level ``E2EUnsupportedSetup`` declaration (surfaced as xfail by the conftest
report hook, NOT listed in the ledger). What remains are genuine production /
harness gaps, enumerated below.

This test pins that end state so the ledger cannot silently drift:

* a removed entry that creeps back (a graduation regression) fails here;
* a genuine-gap entry deleted without landing the underlying fix fails here;
* the conftest loader must still read the same file the BDD suite xfails against.

When a gap is genuinely fixed (its scenario now passes in-network) or moved to an
env declaration, remove it from BOTH the ledger file and ``EXPECTED_LEDGER`` below
in the same change.
"""

from __future__ import annotations

import ast
from pathlib import Path

# The 17 e2e_rest nodeids remaining: 7 genuine gaps + 10 parallel-e2e_rest
# mock-injection artifacts (owner-approved, added on the adcp-6.6 /
# perf/parallelize-test-suite work — see the block comment inside the set).
# Graduated on the way here: the 2 date-range boundary rows (2026-07-09, first
# in-network CI run), the 2 date-range partition twins (origin/pr-1417 merge,
# d4af23095 — strict-xfail XPASS in-network), and the 2 uc004 account valid rows
# (#1417 merge, jr5b seeded-account Given, XPASS innet_140726_1516).
# (47 after Wave 3 triage; jdy1
# graduated M3 6 get_products tenant-duplicate, M1 6 uc004 REST-422 wire-shape,
# M4 4 uc004 webhook-observability entries [now tag-declared in conftest]; the
# uc004 attribution campaign-interval boundary graduated at the main merge after
# upstream re-pointed its expected cell at error "VALIDATION_ERROR"; 12 uc006
# account billing-state entries graduated at the #1417 merge — its account
# resolution wiring makes them pass, xpass confirmed innet_040726_0013; 3 uc002
# creative extension entries imported at the #1417 merge — newly wired there,
# confirmed still failing in-network post-merge, innet_040726_0013; the uc004
# roas/cpa entry retired at #1430 item 4 — its Then steps now exist and the
# scenario is tag-declared T-UC-004-aggregated-roas-and-cpa on ALL transports;
# #1430 items 1-3 graduated the 6 uc011 read-back entries [_db_scope_for repoint
# + agent auth_token fix] and 2 uc002 ext-o/ext-p entries [auto-approval seeding],
# all 8 xpassed in-network, innet_050726_2030; the uc002 ext-q upload entry
# graduated after the fail_on_upload mock-fidelity + catalog-format +
# run_async_in_sync_context format-resolution fixes, verified in-network).
# Grouped by gap in the ledger file's section comments; flat here for exact-set
# comparison.
EXPECTED_LEDGER: frozenset[str] = frozenset(
    {
        # All four date-range invalid rows graduated: boundary rows 2026-07-09
        # (#1270 tripwires fired on the first in-network CI run — live server
        # validates start>=end now), partition twins at the origin/pr-1417 merge
        # (d4af23095, strict-xfail XPASS in-network).
        # Account valid rows graduated at the #1417 merge (jr5b seeded-account
        # Given; XPASS in-network innet_140726_1516) — see ledger note.
        "tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_include_package_daily_breakdown_boundary__boundary_point[e2e_rest-string 'true' (non-boolean type)-\"true\"-invalid]",
        "tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_principal_ownership_boundary__boundary_point[e2e_rest-principal differs from owner-invalid]",
        "tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_principal_ownership_partition__partition[e2e_rest-owner_mismatch-invalid]",
        'tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_reporting_dimensions_boundary__boundary_point[e2e_rest-geo with geo_level=metro but no system (behavioral gap)-{"geo": {"geo_level": "metro"}}-invalid]',
        "tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_sampling_method_boundary__boundary_point[e2e_rest-Unknown string not in enum-systematic-invalid]",
        "tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_seller_ignores_attribution_request__returns_platform_default[e2e_rest]",
        "tests/bdd/test_uc011_manage_accounts.py::test_push_notification_for_async_status_changes__with_push_notification[e2e_rest]",
        # Added 2026-07-09 on the adcp-6.6 branch (owner-approved) when
        # perf/parallelize-test-suite enabled parallel e2e_rest (E2E_PER_WORKER):
        # mock-injection-incompatible artifacts, not regressions — UC-004
        # set_adapter_response (delivery), UC-005 set_registry_formats, UC-018
        # injected cross-principal creatives are invisible to the separate HTTP
        # server. Preserved through the main merge.
        "tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_breakdown_complete_not_truncated__truncation_flag_set_false[e2e_rest]",
        "tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_breakdown_truncated_by_limit__truncation_flag_set_true[e2e_rest]",
        "tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_buyer_requests_supported_dimension__seller_returns_breakdown[e2e_rest]",
        "tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_multiple_dimensions_requested_simultaneously[e2e_rest]",
        "tests/bdd/test_uc005_discover_creative_formats.py::test_baseline_list_creative_formats_response_carries_format_id_objects_with_agent_url_and_id[e2e_rest]",
        "tests/bdd/test_uc005_discover_creative_formats.py::test_format_id_roundtrip__list_creative_formats_returns_the_same_format_object_that_get_products_advertised[e2e_rest]",
        "tests/bdd/test_uc005_discover_creative_formats.py::test_format_id_with_agent_url_pointing_at_a_thirdparty_creative_agent_is_reported_as_observation_not_failure[e2e_rest]",
        "tests/bdd/test_uc018_list_creatives.py::test_brrule034_inv1_counter__crossprincipal_creatives_never_visible[e2e_rest]",
        "tests/bdd/test_uc018_list_creatives.py::test_brrule034_inv1_holds__query_always_scoped_by_principal[e2e_rest]",
        "tests/bdd/test_uc018_list_creatives.py::test_list_creatives_filtered_by_concept_ids_returns_only_creatives_in_that_concept_carrying_concept_id_and_concept_name[e2e_rest]",
        # Added by bug-triage epic salesagent-jl20 (2026-07-16): 2 genuine e2e-only
        # gaps surfaced by un-xfailing dn2s/mkso's scenarios — see ledger file
        # section comments for full root-cause analysis of each.
        # uc010 auth-data-identity graduated at salesagent-zna9 (_resolve_auth_dep
        # now resolves tenant from headers regardless of credential presence).
        # uc003 ext-a-unknown graduated at salesagent-z9e0 (harness identity_for()
        # now nulls principal_id on a failed token->principal DB lookup, mirroring
        # production's resolve_identity() — all transports agree now).
    }
)

_LEDGER_PATH = Path(__file__).parent.parent / "bdd" / "e2e_rest_known_failures.txt"


def _load_ledger_nodeids() -> frozenset[str]:
    """Parse the ledger the way the conftest loader does (drop comments/blanks)."""
    return frozenset(
        line.strip()
        for line in _LEDGER_PATH.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


def test_ledger_matches_expected_genuine_gaps() -> None:
    """The ledger file contains exactly the pinned genuine-gap nodeids."""
    actual = _load_ledger_nodeids()
    crept_back = actual - EXPECTED_LEDGER
    disappeared = EXPECTED_LEDGER - actual
    assert actual == EXPECTED_LEDGER, (
        "e2e_rest ledger drifted from its pinned Wave-3 end state.\n"
        f"Entries that crept back in (un-graduate them or update EXPECTED_LEDGER): {sorted(crept_back)}\n"
        f"Entries removed without updating this test: {sorted(disappeared)}"
    )


def test_ledger_entries_are_e2e_rest_bdd_nodeids() -> None:
    """Every ledger entry is a tests/bdd e2e_rest scenario nodeid."""
    for nodeid in _load_ledger_nodeids():
        assert nodeid.startswith("tests/bdd/"), f"non-bdd ledger entry: {nodeid}"
        assert "::" in nodeid, f"ledger entry is not a nodeid: {nodeid}"
        assert "e2e_rest" in nodeid, f"ledger entry is not an e2e_rest variant: {nodeid}"


def test_conftest_loader_reads_this_ledger() -> None:
    """The BDD conftest loads the same ledger this test pins.

    Guards against the loader being deleted or pointed elsewhere while the file
    still exists — that would silently stop xfailing these known failures.
    """
    from tests.bdd.conftest import _E2E_REST_KNOWN_FAILURES

    assert _E2E_REST_KNOWN_FAILURES == EXPECTED_LEDGER


# ---------------------------------------------------------------------------
# Second ledger: the UC-004 webhook blanket-xfail tag set in the BDD conftest
# ---------------------------------------------------------------------------

_BDD_CONFTEST = Path(__file__).resolve().parents[1] / "bdd" / "conftest.py"
_WEBHOOK_TAGS_NAME = "_UC004_E2E_WEBHOOK_INTERNAL_TAGS"

# The 11 UC-004 webhook tags blanket-xfailed on e2e_rest. This set is a work-list,
# not a config: every entry is a scenario that does NOT grade the live delivery
# path. It is pinned by EXACT SET EQUALITY, in both directions, on purpose:
#
#   * an ADDITION fails here even when it is legitimate — parking a scenario has
#     to be a deliberate edit to this constant with a stated reason, which is
#     precisely the review step the prose comment in the conftest could not force
#     (the set grew under it);
#   * a REMOVAL fails here too — an un-graduated deletion silently un-xfails a
#     scenario whose Thens can no longer observe anything, which is the failure
#     mode salesagent-n78j0.1.4 nearly shipped. Removals follow
#     .claude/rules/workflows/xpass-graduation.md, one scenario at a time, and
#     update this constant in the same change.
#
# Graduated on the way here: T-UC-004-webhook-9421 (salesagent-n78j0.1.4 — the
# delivery ACTION moved into env.deliver_webhook(), which drives the live server's
# own trigger route over e2e; see the conftest comment for the mutation evidence).
# Graduated on the way here: T-UC-004-webhook-hmac (salesagent-n78j0.13 — traced
# independently of the 9421 sibling rather than assumed to ride along with it. Its
# three Thens read env.last_delivery() and the last RECOMPUTES the digest over the
# received bytes; production reaches the HMAC arm because _send_report_for_media_buy
# looks the registration up in DBPushNotificationConfig, which overrides the
# auth-less raw_request the harness writes. Mutation evidence in the conftest
# comment; this lock is what caught the removal before the pin was updated, which
# is the review step it exists to force).
# Graduated on the way here: T-UC-004-webhook-bearer (salesagent-n78j0.13 — traced
# independently of the hmac row again, not carried by it. This one is structurally
# weaker than hmac (ONE Then, no recompute-over-received-bytes), so the inspection
# turned on a single question: does that Then grade the token's VALUE or merely the
# header's PRESENCE? It grades the value — expected comes from the test's own ctx,
# actual off the wire via env.last_delivery() — and the mutation that proves it is a
# WRONG-BUT-PRESENT token, deliberately not a removed header, since a removed header
# would only re-prove presence. Mutation evidence in the conftest comment).
EXPECTED_WEBHOOK_INTERNAL_TAGS: frozenset[str] = frozenset(
    {
        # Server-side gap, not a harness bypass: the live delivery path emits
        # NotificationType.scheduled unconditionally (delivery_webhook_scheduler.py),
        # so the final/delayed/adjusted Examples rows cannot pass over e2e_rest.
        "T-UC-004-webhook-notification-type",
        "T-UC-004-webhook-no-aggregated",
        "T-UC-004-webhook-circuit-open",
        "T-UC-004-webhook-circuit-recovery",
        "T-UC-004-webhook-retry-success",
        # jdy1-M4: retry/sequence observability — assert on env.mock['post'] call
        # counts / args, not visible over the Docker HTTP path.
        "T-UC-004-webhook-retry-5xx",
        "T-UC-004-webhook-retry-network",
        "T-UC-004-webhook-no-retry-4xx",
        "T-UC-004-webhook-sequence",
    }
)


def find_webhook_internal_tags(tree: ast.Module) -> frozenset[str]:
    """Extract the ``_UC004_E2E_WEBHOOK_INTERNAL_TAGS`` literal from the conftest AST.

    The set is a local inside ``pytest_collection_modifyitems``, so it cannot be
    imported; it is read structurally instead (same technique as
    ``test_architecture_e2e_rest_escape_hatches.find_e2e_rest_xfail_conditions``).

    Raises if the binding is missing or is no longer a set of string literals —
    deleting or dynamically building the set must fail this lock loudly rather
    than reduce it to an empty set that compares unequal for the wrong reason.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name, value = node.target.id, node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name, value = node.targets[0].id, node.value
        else:
            continue
        if name != _WEBHOOK_TAGS_NAME:
            continue
        if not isinstance(value, ast.Set) or not all(
            isinstance(elt, ast.Constant) and isinstance(elt.value, str) for elt in value.elts
        ):
            raise AssertionError(
                f"{_WEBHOOK_TAGS_NAME} is no longer a literal set of strings. This lock reads "
                "it structurally; a computed set cannot be pinned and would silently disable "
                "the ledger discipline."
            )
        return frozenset(elt.value for elt in value.elts)
    raise AssertionError(
        f"{_WEBHOOK_TAGS_NAME} not found in {_BDD_CONFTEST}. If the blanket e2e_rest webhook "
        "xfail route was retired, delete EXPECTED_WEBHOOK_INTERNAL_TAGS and this lock in the "
        "same change (and drop the route from EXPECTED_XFAIL_ROUTES)."
    )


def test_webhook_internal_tags_match_pin() -> None:
    """The conftest's UC-004 webhook xfail tag set is exactly the pinned set."""
    actual = find_webhook_internal_tags(ast.parse(_BDD_CONFTEST.read_text()))
    added = actual - EXPECTED_WEBHOOK_INTERNAL_TAGS
    removed = EXPECTED_WEBHOOK_INTERNAL_TAGS - actual
    assert actual == EXPECTED_WEBHOOK_INTERNAL_TAGS, (
        f"{_WEBHOOK_TAGS_NAME} drifted from its pin.\n"
        f"Parked without a deliberate pin update: {sorted(added)}\n"
        f"Un-parked without a graduation: {sorted(removed)}\n"
        "Growth is not forbidden, but it must be a reviewed edit to "
        "EXPECTED_WEBHOOK_INTERNAL_TAGS stating why the scenario cannot grade the live "
        "delivery path. Removal follows .claude/rules/workflows/xpass-graduation.md."
    )


def test_webhook_tag_extractor_rejects_a_computed_set() -> None:
    """Meta-test: a non-literal set fails loudly instead of pinning nothing."""
    src = f"def hook():\n    {_WEBHOOK_TAGS_NAME} = set(SOME_OTHER_TAGS)\n"
    try:
        find_webhook_internal_tags(ast.parse(src))
    except AssertionError as exc:
        assert "no longer a literal set" in str(exc)
    else:
        raise AssertionError("extractor accepted a computed set")


def test_webhook_tag_extractor_rejects_a_missing_binding() -> None:
    """Meta-test: deleting the set fails the lock instead of passing vacuously."""
    try:
        find_webhook_internal_tags(ast.parse("def hook():\n    pass\n"))
    except AssertionError as exc:
        assert "not found" in str(exc)
    else:
        raise AssertionError("extractor accepted a missing binding")
