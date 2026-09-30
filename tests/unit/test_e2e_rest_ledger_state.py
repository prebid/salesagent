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

from tests.helpers.ledger import load_ledger_nodeids

# The 13 e2e_rest nodeids remaining: 7 genuine gaps + 2 parallel-e2e_rest
# mock-injection artifacts (owner-approved, added on the adcp-6.6 /
# perf/parallelize-test-suite work — see the block comment inside the set)
# + the adcp#7338 catalog row (2026-09-13) + 3 UC-010 targeting-shape rows
# (2026-09-15), both annotated at their entries below.
# 5 of that mock-injection block graduated 2026-09-15 (4 UC-004 delivery rows and
# the UC-005 format-id roundtrip row): the block's stated mechanism had gone stale,
# XPASS in-network innet_150926_0531, evidence at their former entries below.
# A SECOND #7338 row (@T-UC-005-main-referrals) was considered on 2026-09-15 and
# NOT added: it would have validated a document byte-identical to the row below
# while denying POST-S4 the only transport where the seller's real federation path
# runs. The redundant compliance Then was deleted from that scenario instead. The
# ledger file's #7338 block carries the reasoning and the measured scope.
# The 3 uc018 rows of that block graduated 2026-08-31: their Givens seed through
# the factories into the live server's own database, so the block's
# "injected cross-principal creatives" clause no longer describes them. XPASS in
# innet_270826_0338, innet_270826_1824 and innet_310826_1248; full evidence in
# the ledger file's block comment.
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
        # salesagent-3cs7o.54 — asset-level provenance absent from the stored
        # creative over e2e_rest only (passes on a2a/mcp/rest). The pin mandates
        # it and every source-readable hop is cleared by measurement; the next
        # step is a live-stack probe, so it is ledgered rather than guessed at.
        "tests/bdd/test_uc006_sync_creatives.py::test_inv5__assetlevel_provenance_replaces_creativelevel_entirely[e2e_rest]",
        # All four date-range invalid rows graduated: boundary rows 2026-07-09
        # (#1270 tripwires fired on the first in-network CI run — live server
        # validates start>=end now), partition twins at the origin/pr-1417 merge
        # (d4af23095, strict-xfail XPASS in-network).
        # Account valid rows graduated at the #1417 merge (jr5b seeded-account
        # Given; XPASS in-network innet_140726_1516) — see ledger note.
        "tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_include_package_daily_breakdown_boundary__boundary_point[e2e_rest-string 'true' (non-boolean type)-\"true\"-invalid]",
        'tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_reporting_dimensions_boundary__boundary_point[e2e_rest-geo with geo_level=metro but no system (behavioral gap)-{"geo": {"geo_level": "metro"}}-invalid]',
        "tests/bdd/test_uc004_deliver_media_buy_metrics.py::test_seller_ignores_attribution_request__returns_platform_default[e2e_rest]",
        "tests/bdd/test_uc011_manage_accounts.py::test_push_notification_for_async_status_changes__with_push_notification[e2e_rest]",
        # Added 2026-07-09 on the adcp-6.6 branch (owner-approved) when
        # perf/parallelize-test-suite enabled parallel e2e_rest (E2E_PER_WORKER):
        # mock-injection-incompatible artifacts, not regressions — UC-005
        # set_registry_formats is invisible to the separate HTTP server.
        #
        # The 4 UC-004 delivery rows and the UC-005 format-id roundtrip row graduated
        # 2026-09-15 (XPASS in-network, innet_150926_0531). The clause that routed the
        # UC-004 four named the wrong mechanism: #1418 gave set_adapter_response an e2e
        # realization that persists a DeliverySimulationConfig row the deployed server's
        # MockAdServer reads, so nothing is injected and nothing is invisible. The
        # roundtrip row was never a registry-injection case at all. Evidence per row is
        # in the ledger file's block comment.
        #
        # The third-party-agent row below stays: it xpasses VACUOUSLY (its one
        # discriminating assertion compares a raw agent_url against canonicalized
        # identities, so an id-only filter regression would still pass) and graduating
        # it would delete coverage rather than record it.
        "tests/bdd/test_uc005_discover_creative_formats.py::test_baseline_list_creative_formats_response_carries_format_id_objects_with_agent_url_and_id[e2e_rest]",
        "tests/bdd/test_uc005_discover_creative_formats.py::test_format_id_with_agent_url_pointing_at_a_thirdparty_creative_agent_is_reported_as_observation_not_failure[e2e_rest]",
        # Added 2026-09-13: @T-UC-005-main's Given moved from minted fmt_N ids to
        # reference-catalog formats, which retired its tag-level xfail on every
        # transport; over e2e_rest the live catalog carries pixel_tracker assets the
        # pinned Format.assets union does not admit (adcp#7338), so the compliance
        # Then fails there for the same reason as the three UC-005 rows above.
        # Upstream's own release is inconsistent with itself, which is why no change here
        # can graduate this row: the reference catalog captured at pin 467fd93d7711 -- the
        # same commit the feature files cite for the pinned schemas -- publishes
        # pixel_tracker on 45 of its 57 formats, and the adcp SDK from that release already
        # carries an UnknownFormatAsset fallback arm for exactly this ("AdCP enums grow
        # additively by design", strict on emit, lenient on parse). The JSON schema is the
        # half that did not get the arm. The one local "fix" -- stripping assets the pin
        # omits from the catalog this seller serves -- would tell a buyer a format needs
        # fewer assets than it does.
        "tests/bdd/test_uc005_discover_creative_formats.py::test_discover_full_format_catalog[e2e_rest]",
        # Added 2026-09-15: three UC-010 rows that configure a targeting SHAPE per
        # tenant (which metro systems, which country-keyed postal systems) and read
        # it back from get-adcp-capabilities-response.json
        # #/properties/media_buy/properties/execution/properties/targeting.
        # Production builds both blocks correctly -- the in-process rows prove it --
        # but there is no per-tenant surface to configure the shape over HTTP, and
        # adding one is refused twice over: TargetingCapabilities is adapter-CLASS
        # level by documented design (AdServerAdapter.get_targeting_capabilities is a
        # @staticmethod so discovery can read it without a Principal-bound instance,
        # INV-4 / salesagent-dn2s), and the declaration store carries no field for a
        # posture production cannot back ("the absence is the enforcement",
        # src/core/schemas/capability_declarations.py). The third option is the
        # test_behavior override read in src/core/ that a1b79d22d deleted and
        # prebid/salesagent#1891 questions. Their sibling channel and degradation rows
        # needed none of this: channels now come from Product.channels via
        # channel_helpers, and "adapter unavailable" is a real unresolvable
        # adapter_type. Full reasoning and the graduation condition are in the ledger
        # file's own block.
        "tests/bdd/test_uc010_discover_seller_capabilities.py::test_targeting_capability_configurations__partition[e2e_rest-nested_absent no nested sub-properties declared-no nested sub-properties true-geo_metros and geo_postal_areas absent from targeting]",
        'tests/bdd/test_uc010_discover_seller_capabilities.py::test_targeting_capability_configurations__partition[e2e_rest-nested_populated native postal map and metros-geo_metros.nielsen_dma=true, geo_postal_areas US=["zip"]-geo_metros equals {nielsen_dma: true} and geo_postal_areas has US containing "zip"]',
        'tests/bdd/test_uc010_discover_seller_capabilities.py::test_targeting_capability_configurations__partition[e2e_rest-postal_areas_native DE/CH/AT via native country-keyed map-geo_postal_areas DE=["plz"] CH=["plz"] AT=["plz"]-geo_postal_areas equals {DE: [plz], CH: [plz], AT: [plz]}]',
        # The 3 uc018 rows of this block GRADUATED 2026-08-31 on main (#1858,
        # f9e82da6a): their Givens seed through the factories into the live server's
        # own database, so the block's "injected cross-principal creatives" clause
        # stopped describing them. XPASS in innet_270826_0338, innet_270826_1824,
        # innet_310826_1248. Never re-add them: this ledger only shrinks.
        # Bug-triage epic salesagent-jl20 (2026-07-16) surfaced 2 genuine e2e-only
        # gaps by un-xfailing dn2s/mkso's scenarios; both graduated before landing
        # here, so neither is listed: uc010 auth-data-identity at salesagent-zna9
        # (_resolve_auth_dep now resolves tenant from headers regardless of
        # credential presence), and uc003 ext-a-unknown at salesagent-z9e0 (the harness
        # presents no token when no Principal row exists, so production's identity
        # resolver answers the failed lookup itself — all transports agree now).
    }
)

_LEDGER_PATH = Path(__file__).parent.parent / "bdd" / "e2e_rest_known_failures.txt"


def _load_ledger_nodeids() -> frozenset[str]:
    """Parse the ledger the way the conftest loader does (drop comments/blanks)."""
    return load_ledger_nodeids(_LEDGER_PATH)


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

# The 5 UC-004 webhook tags blanket-xfailed on e2e_rest. This set is a work-list,
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
#
# Graduated on the #1721 branch, landing here at this merge: the SIX rows
# T-UC-004-webhook-notification-type, -no-aggregated, -retry-success, -retry-5xx,
# -no-retry-4xx and -sequence (#2098 / #1873). The routing reason had gone stale for
# them — the in-process local origin is no longer the e2e_rest endpoint; the compose
# stack's long-lived webhook-capture service is, and LocalOriginMixin's realize_e2e
# accessors read the delivery back off it, so the POST body IS observable through the
# Docker HTTP path. Measured, not read off the green mark: four mutations in
# src/services/webhook_delivery_service.py (run innet_080926_0627, baselines
# innet_070926_1424 -> _1642) flipped each of the six XPASS -> XFAIL with the message
# of its OWN assertion; exactly 8 of 2856 nodes changed outcome. Verified un-routed in
# innet_080926_0638 — all six a plain PASS, failure count unchanged at 123. Full
# evidence lives in the conftest comment above the set.
#
# MERGE NOTE (#1721 x signing epic), and it CORRECTS the paragraph above for two of
# those six. The two branches graduated disjoint rows off the same 11-row base — six on
# #1721, bearer/hmac on the signing epic — so the naive resolution is the intersection,
# three rows. That is wrong here, because a graduation is not a fact about a tag: it is a
# claim about the tree it was measured on, and the merge changed the tree under four of
# #1721's six.
#
# The #1721 six were measured where `_call_webhook_service` ended in `env.call_send(...)`
# — an in-process WebhookDeliveryService on EVERY transport, e2e_rest included — which is
# what made the POST observable and the mutations attributable. The signing epic replaced
# that seam (salesagent-n78j0.1.4): `_call_webhook_service` now ends in
# `env.deliver_webhook(...)`, which is `@realize_e2e(_deliver_via_live_server)`, so over
# e2e_rest the delivery is made by the DEPLOYED server via
# /admin/.../trigger-delivery-webhook. Neither parent ever ran that seam against #1721's
# graduations. Two of the six are re-parked here because the live path provably cannot
# satisfy their Thens, each for a reason recorded at its entry below; the other four
# (-retry-success, -sequence, -retry-5xx, -no-retry-4xx) stay graduated and are owed a
# re-grade that only a bdd-in-network run can settle, as the conftest comment records.
#
# This is a pin GROWING against the intersection, which the contract above allows only as
# a reviewed edit stating why the scenario cannot grade the live delivery path — that is
# what the two entries below do. It is still a SHRINK against every measured predecessor:
# 11 at the merge base, 9 on the signing epic (the only parent that carries this lock), 5
# here.
EXPECTED_WEBHOOK_INTERNAL_TAGS: frozenset[str] = frozenset(
    {
        # RE-PARKED at the merge (see MERGE NOTE): the e2e_rest realization of
        # `deliver_webhook` drives /admin/.../trigger-delivery-webhook, and that route has
        # no server-side selector for the notification type —
        # `_deliver_via_live_server` (tests/harness/_mixins.py) accepts the argument and
        # ignores it, and DeliveryWebhookScheduler emits `scheduled` unconditionally. So
        # the `final` / `delayed` / `adjusted` Examples rows cannot pass over e2e_rest
        # whatever the harness does. Unparks when production selects the type.
        "T-UC-004-webhook-notification-type",
        # RE-PARKED at the merge (see MERGE NOTE) because it is a GENUINE FAILURE over
        # e2e_rest, not a vacuous pass and not an ungraded obligation.
        #
        # The Then is NOT top-level-only on this tree. #1721 strengthened it and the merge
        # kept that: uc004_delivery.py:2211 reads `_get_last_webhook_result(ctx)` — the
        # inner `result` — where BASE and the incoming branch both read the whole payload.
        # So the assertion looks exactly where the obligation lives.
        #
        # The in-process transports pass because webhook_delivery_service.py:363 builds
        # `delivery_result` as the inner `result` and it carries no aggregated_totals. Over
        # e2e_rest the delivery is made by the DEPLOYED server, whose `result` is a
        # serialized GetMediaBuyDeliveryResponse, and that always sets aggregated_totals
        # (media_buy_delivery.py:596) — which is precisely what 3.1.1 L3/webhooks.mdx :253
        # forbids in a reporting webhook result payload. The row is therefore parked on a
        # real production violation (GH #2058), and it unparks when production stops
        # emitting the field, NOT when the Then is changed.
        "T-UC-004-webhook-no-aggregated",
        # DEFERRED to prebid/salesagent#2060, which owns both halves of the breaker's
        # missing coverage: these Thens read CircuitBreaker state in the test process,
        # which the deployed server never touches. The routing stays until the
        # scenario actually grades the live server — un-routed, the leg reports a
        # plain PASS, which reads as real coverage and is strictly worse than an
        # XPASS, which at least records that nothing is being graded.
        "T-UC-004-webhook-circuit-open",
        "T-UC-004-webhook-circuit-recovery",
        # jdy1-M4: retry observability — this Then asserts on env.mock['post'] call
        # counts / args, not visible over the Docker HTTP path. Its retry siblings
        # graduated when their assertions moved onto the capture service; this one
        # still reads the in-process mock, so it cannot.
        "T-UC-004-webhook-retry-network",
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
