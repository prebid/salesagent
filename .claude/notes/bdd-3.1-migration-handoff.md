# BDD 3.1 Migration — Agent Handoff

## Branch

- **Branch**: `feature/bdd-3.1-migration`
- **HEAD**: `cad9a5bf8`
- **Base**: `af39e8739` (tip of `feature/strengthen-bdd-assertions` PR #1374)
- **Fork**: pushed to `KonstantinMirin/prebid-salesagent`
- **Depends on**: PR #1374 (BDD assertion strengthening) must merge first

## What happened

The adcp-req spec was upgraded from v3.0.1 to v3.1. `scripts/compile_bdd.py`
regenerated all 26 feature files from the new spec. The 7 wired UCs
(001, 002, 003, 004, 005, 006, 008) had their Gherkin LLM-merged to preserve
existing step wording where possible. 19 unwired UCs were mechanically
rendered (TARGET-WINS — no step bindings to preserve).

3 UCs are PARTIAL (UC-011, UC-019, UC-026) — their feature files were NOT
updated because they have unresolved step-text conflicts (NSMs). Those will
be done later.

## Scale of changes

| UC | New scenarios | Removed | New step lines |
|----|--------------|---------|----------------|
| UC-001 | +27 | -4 | +701 |
| UC-002 | +60 | -1 | +1018 |
| UC-003 | +23 | -9 | +692 |
| UC-004 | +43 | -1 | +823 |
| UC-005 | +14 | -2 | +260 |
| UC-006 | +26 | -0 | +433 |
| UC-008 | +9 | -1 | +145 |
| **Total** | **+202** | **-18** | **+4072** |

~931 new unique step text lines across the wired UCs. Most match existing
step parsers and auto-xfail when step definitions are missing (via conftest
`pytest_runtest_makereport` hook). Only steps that PARTIALLY match an existing
parser cause real failures.

## Current test results (on this branch)

```
9 failed, 1485 passed, 4 skipped, 531 deselected, 6793 xfailed, 5 xpassed
```

### 9 failures (3 distinct scenarios × transports)

**1. `aggregated_totalsreach...homogeneous` (1 failure, rest transport only)**
**2. `aggregated_totalsreach...heterogeneous` (4 failures, all transports)**

- **Root cause**: Given step parser mismatch. The new Gherkin says:
  `Given a media buy "mb-001" owned by "buyer-001" with status "active" and reach_unit "individuals"`
- The existing `@given` parser matches `with status "{status}"` and captures
  `active" and reach_unit "individuals"` as the entire status string.
- Status column is `varchar(20)`, so `active" and reach_unit "individuals"`
  (41 chars) causes `StringDataRightTruncation`.
- **Fix**: Add a new Given step pattern that handles `with status "{status}" and reach_unit "{reach_unit}"`,
  or modify the existing parser to be more specific (stop at `"` boundary).

**3. `polling_response...legacy_status_pending` (4 failures, all transports)**

- **Root cause**: `pending_start` is a new v3.1 status. The Given step creates
  a media buy with status `pending_start`, but either:
  - The DB column/enum doesn't accept `pending_start` yet, or
  - The harness maps it to `active` during setup
- Then step asserts `status == "pending_start"` but gets `active`.
- **Fix**: Add `pending_start` to the valid status values in the model/harness.
  This is a production code change (new status enum value) + step wiring.

### 5 xpassed (scenarios that now pass but were xfailed)

All 5 are A2A transport, UC-004:
- `test_sort_by_metric_not_available__seller_falls_back_to_spend[a2a]`
- `test_webhook_credentials_partition__partition[a2a-credentials_too_short-invalid]`
- `test_webhook_credentials_partition__partition[a2a-unknown_scheme-invalid]`
- `test_webhook_credentials_boundary__boundary_point[a2a-credentials = 31 chars (rejected)-invalid]`
- `test_webhook_credentials_boundary__boundary_point[a2a-Unknown auth scheme not in enum-invalid]`

These were previously xfailed because their step definitions or scenarios
didn't match. The 3.1 feature file update fixed the Gherkin to match
existing step implementations. These xfails should be graduated (removed
from the xfail lists in conftest.py).

### ~6793 xfailed

Most are expected — they are:
- New v3.1 scenarios with no step definitions yet (auto-xfailed by conftest)
- Pre-existing xfails for unwired UCs and production gaps
- The 202 new scenarios mostly land here (no step defs → StepDefinitionNotFoundError → xfail)

## What needs to happen next

### Phase 1: Fix the 9 failures (immediate)

1. **New Given step for `reach_unit`**: Add a parser that handles
   `with status "{status}" and reach_unit "{reach_unit}"`. Store reach_unit in ctx.
   File: `tests/bdd/steps/generic/given_entities.py` or `given_media_buy.py`

2. **New status `pending_start`**: Either add it as a valid status in the
   MediaBuy model or map it in the harness. Check the adcp 3.1 spec for the
   canonical status enum values. File: `src/core/database/models.py` or
   harness status mapping.

3. **Graduate 5 xpassed**: Find which xfail entries in `tests/bdd/conftest.py`
   correspond to the 5 xpassed tests and remove them.

### Phase 2: Wire new 3.1 step definitions (incremental)

The 202 new scenarios added ~931 unique step text lines. Most auto-xfail
because they reference concepts that don't have step definitions:

- `reach_unit`, `frequency`, `metric_aggregates`, `qualifier`
- `committed_at`, `is_final`, `measurement_window`
- `unavailable_count`, `expected_availability`
- `reporting_delayed` status
- `superseded` webhook semantics

These should be wired incrementally, one concept at a time, following the
same pattern as the assertion strengthening work:
1. Identify the new step texts that need definitions
2. Create beads tickets with deterministic context (Gherkin + spec + .agent-index)
3. Implement step definitions against real production code
4. Run tests, verify, close

### Phase 3: Partial UCs (UC-011, UC-019, UC-026)

These 3 UCs still have their pre-3.1 feature files. They need LLM-merge
to reconcile the new spec text with existing step bindings:
- UC-011: 54 non-step-matched lines
- UC-019: 15 non-step-matched lines
- UC-026: 39 non-step-matched lines

## Key files

- Feature files: `tests/bdd/features/BR-UC-*.feature`
- Step definitions: `tests/bdd/steps/domain/uc*.py`, `tests/bdd/steps/generic/*.py`
- Conftest xfails: `tests/bdd/conftest.py` (search `_UC00X_XFAIL_ADDITIONAL`)
- Compile script: `scripts/compile_bdd.py`
- adcp-req source: `/Users/konst/projects/adcp-req` (v3.1)

## DB and infra

- agent-db is on port 54770 (container `agent-pg-skills`)
- DATABASE_URL: `postgresql://adcp_user:secure_password_change_me@localhost:54770/adcp_test`
- Full Docker stack via `./run_all_tests.sh` for final verification

## PR status

- PR #1374 (assertion strengthening): ready to merge, CI green except flaky
  Postgres service on media-buy shard (re-triggered)
- This migration work should be a separate PR off #1374's branch

---

## Phase 1 COMPLETE (2026-06-03)

Baseline was actually **23 UC-004 failures**, not the 9 originally stated (the
handoff undercounted + missed two groups). Classified into 3 root causes; all
resolved with **0 failures, 0 regressions**. Final full UC-004:
`490 passed, 0 failed, 739 xfailed, 4 xpassed`.

Commits: `924907133` (reach_unit Given parser → 8 crashes become honest
BR-RULE-224 xfails), `ed9633fc5` (hcvb: `media_buy_ids provided` boundary),
`3003b2c35` (polling pending_start → strict xfail, spec-confirmed prod gap),
`a6db6e5ae` (graduate sort_by a2a xpass; document credential false-passes),
`2073994a9` (revert too-broad status_filter parser; xfail pending_* siblings —
supersedes the reverted `0a9612cc4`).

**jceq xpass verification result:** of the 5 a2a xpasses, only `sort_by-fallback`
was a real, strongly-asserted pass (graduated). The 4 webhook-credential xpasses
pass for the **wrong reason** (When sends an unsupported `credentials` field →
`extra_forbidden` satisfies a loose `isinstance(error, AdCPError)` check) — left
xfailed, rework bead filed.

**KEY GOTCHA:** the generic `@when("...with {request_params}")` step shadows the
specific partition steps (`with status_filter "X"`, `with resolution "X"`,
`with principal "X"`). It only parses `key=value`, so single space-separated
values are dropped and masked. Broadening its parser regressed 49 scenarios.
Tracked as a follow-up bead; proper fix graduates the whole partition family.

Follow-up beads: delivery-status-surface (pending_start), credential-test-rework,
step-shadowing fix. Phase 2 (wire new v3.1 concepts) and Phase 3 (UC-011/019/026
merges) still pending.
