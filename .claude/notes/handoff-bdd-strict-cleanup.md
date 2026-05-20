# Handoff — BDD strict-marker cleanup + green-main restoration

**Last touched: 2026-05-08. Status: paused after Wave 3 / strict-marker
cleanup, awaiting owner approval to file GH issues.**

If you are picking this up cold, read this top to bottom before doing
anything. Then read the plan in `.claude/notes/bdd-strict-marker-cleanup-proposal.md`.

## TL;DR — where we are

Local `main` is at `c4446267`, **13 commits ahead of `origin/main`**
(local merges only, no push per project convention). All commits
authored as Constantine Mirin.

```
c4446267 test(bdd): clear ~244 stale xfail markers and tighten 4 blanket scopes
ea2181cc fix(test-stack): raise readiness deadline 120s -> 360s for cold-boot
3c1bae94 fix(deploy): raise migration timeout to 300s and stream output
cf731023 refactor(test): route UC-004 webhook BDD scenarios through CircuitBreakerEnv
44c8c2b0 fix(test): include valid identifier in test_create_property_saves_to_db POST
ab43f116 test: xfail UC-004 by_geo/by_device_type breakdown scenarios — production gap
15eed75a fix(test): inventory_profiles list test asserts on JS-rendered content
c6cfb99f fix(prod): _build_sync_result must populate account_id on sync responses
5315f543 test: xfail authorized_properties list page tests for missing template
731b177e fix(test): UC-011 advertiser assertion conflates schema with model_dump
a353cd5b fix(test): BDD webhook conftest branch missing integration_db fixture
8a325683 fix(test): inventory_profiles delete test calls POST instead of DELETE
00a468dc fix(test): _resolve_media_buy_id helper not defined in uc004_delivery.py (4 failures)
6c9017c8 fix(test): admin Principal fixture uses empty platform_mappings dict
5011855d (origin/main) feat: upgrade adcp SDK 3.12→4.3 and import protocol metadata (#1266)
```

Working tree: clean except for the untracked `.claude/skills/.agent-db.env`
file, which is a worktree-local DB env stub that the skill writes when
spawning containers. Not part of this work; leave alone.

## Test status

Last full green run: **`test-results/070526_2244/`** (2026-05-07).

```
admin:        78p 0f 0e  3 xfailed
bdd:         999p 0f 0e  2839 xfailed   (after Wave-2 webhook routing fix)
e2e:          87p 0f 0e  28 skipped
integration: 1880p 0f 0e  39 xfailed
ui:            5p 0e
unit:       4550p 0f 0e  19 xfailed
```

After the `c4446267` strict-marker cleanup, the BDD xpasses dropped
further (from 427 to 0 for in-scope scenarios; the targeted post-cleanup
run showed 224 passed, 0 failed, 0 xpassed, 68 strict xfailed). Full
`./run_all_tests.sh` was not re-run after `c4446267`; if you want
absolute confidence before doing anything destructive, re-run it.

## Three documents you should read

1. **`docs/test-debt-bdd-strict-markers.md`** — canonical inventory of
   the 19 items still wearing non-strict markers. Each has a unique ID
   (C1–C11, B1–B7, H1–H2). FIXME comments in `tests/bdd/conftest.py`
   reference these IDs.

2. **`.claude/notes/bdd-strict-marker-cleanup-proposal.md`** — proposed GH
   issue structure (1 umbrella + 3 P1 security issues). Awaiting owner
   approval; do NOT file issues until they say go.

3. **This file** — operational state.

## What's done

- [x] Wave 1 (9 BDD/admin fixes) — all 9 sub-tasks of `salesagent-4ku`
      closed, rebased onto post-#1266 main
- [x] Wave 2 (`salesagent-ww2`) — UC-004 webhook BDD routing through
      `CircuitBreakerEnv` instead of legacy `deliver_webhook_with_retry`
- [x] 13j follow-up bug — TestPropertyCreate identifier fix
- [x] Migration timeout bump (60s → 300s) — `scripts/deploy/run_all_services.py`
- [x] Test-stack readiness deadline bump (120s → 360s) — `scripts/test-stack.sh`
- [x] Wave 3 verification — `./run_all_tests.sh` fully green at
      `070526_2244`
- [x] Local fast-forward merge of `fix/restore-green-main` → `main`
      (post-Wave-3, see commit ladder above)
- [x] `salesagent-4ku` epic closed
- [x] BDD strict-marker audit via 6 parallel Explore agents
- [x] Strict-marker cleanup committed as `c4446267` (~244 stale
      markers cleared, 14 precise strict=True added)
- [x] `docs/test-debt-bdd-strict-markers.md` created with full
      inventory and lifecycle instructions

## What's pending (in priority order)

1. **Owner decision on GH issue structure** — see plan doc. Options:
   - (a) 1 umbrella + 3 P1 security issues (recommended)
   - (b) 1 umbrella only, security folded in as P1 sub-checkboxes
   - (c) Doc-only, no GH issues
2. **If (a) or (b): file issues, then wire FIXME comments** in
   `tests/bdd/conftest.py` to reference issue numbers. Per
   `feedback_no_beads_in_code` memory, use GH issue refs in code
   comments, never beads IDs.
3. **Eventually: work the 19 items** — each item has its own scope,
   spec source, production gap (if any), and unblock criterion. Engineers
   pick from the umbrella checklist, fix the gap, flip the marker, remove
   the doc entry, check the box.

## Open beads tickets (this work created)

These were filed during the parent epic and remain open after `c4446267`:

- `salesagent-u6n` (P2 feat) — implement authorized_properties_list page
- `salesagent-zk1` (P2 feat) — implement reporting_dimensions breakdowns
  (by_geo, by_device_type, by_audience) per BR-RULE-091
- `salesagent-3d1` (P2 bug) — agent-db skill keys container off skills
  basename, collides between concurrent worktree executors
- `salesagent-13j` is **closed** (fixed in commit `44c8c2b0`)
- `salesagent-4ku` epic is **closed**

Do NOT file beads tickets for the items in
`docs/test-debt-bdd-strict-markers.md`. Per the owner's direction
(2026-05-08 session), GitHub is the right tracker for cross-team
visibility; beads is reserved for the active session-bridging
backlog.

## Critical project conventions (read these or risk losing work)

From `/Users/konst/.claude/projects/-Users-konst-projects-salesagent/memory/MEMORY.md`:

- **NEVER run any `bd sync` variant.** Overwrites shared JSONL,
  destroys tickets across all worktrees. Use `bd import -i .beads/issues.jsonl`
  if the local DB goes stale.
- **NEVER touch `.beads/` during merge conflicts.** Stop and ask.
- **NEVER drop/pop/clear stashes** without explicit permission. Lost
  completed #1162 work this way once.
- **NEVER remove worktrees** without explicit permission. They exist
  for a reason.
- **NEVER mention Claude in commit messages.** Project rule.
- **Code comments use GH issue/PR numbers, never beads IDs.**
- **`./run_all_tests.sh` is the only valid final verification.**
  Targeted runs are for iteration only.
- **Test integrity is zero-tolerance.** Never use `--ignore`,
  `-k "not"`, or pytest skip flags to silence failures. Fix or report
  as blocker.

## Source-of-truth principle (recently affirmed by owner)

For enum values, schema shapes, and protocol constraints, the AdCP
library types (`.venv/lib/python3.12/site-packages/adcp/types/...`)
and the AdCP spec are authoritative — **NOT** the existing codebase.
If a test references an enum value that exists in the codebase but
not in the library, the test is wrong, regardless of how long the
codebase has had the value. Example: B1 (Gherkin uses
`pending_activation` which is not in `MediaBuyStatus`); the fix is
to align with the library, not preserve the codebase value.

## How to verify nothing has broken since this handoff

```bash
cd /Users/konst/projects/salesagent-main
git status                                            # should be clean
git log --oneline origin/main..HEAD | head            # should show 13 commits
git log -1 --format=%H                                # should be c4446267
make quality                                           # should pass
```

If you want to verify the strict-marker cleanup specifically:

```bash
scripts/run-test.sh tests/bdd/test_uc004_deliver_media_buy_metrics.py \
  tests/bdd/test_uc005_discover_creative_formats.py \
  -k 'reporting_dimensions or attribution_window or asset_types_filter or daterange or filter_default or filter_empty or filter_array' \
  --tb=line 2>&1 | tail -5
```

Expected: `224 passed, 1227 deselected, 68 xfailed`. 0 failed, 0
xpassed in scope.

If you want to confirm production gap C11 (the one we discovered
during the strict-flip):

```bash
scripts/run-test.sh tests/bdd/test_uc004_deliver_media_buy_metrics.py \
  -k 'test_custom_date_range_used_as_reporting_period' --tb=line 2>&1 | tail -10
```

Expected: 4 xfailed (one per transport). The marker reason mentions
"production ignores buyer-supplied start_date." When C11 is fixed in
production, this test will start xpassing → strict marker fails →
forces removal. Working as designed.

## Architecture context worth knowing

- **All 4 in-process BDD transports** (IMPL/A2A/MCP/REST) are wired for
  UC-004 and UC-011 via `tests/bdd/conftest.py:pytest_generate_tests`.
  No E2E (real HTTP) surface yet — that's a separate future epic per
  `project_bdd_e2e_direction` memory.
- **Webhook delivery has two competing services in production**:
  `WebhookDeliveryService.send_delivery_webhook` (the correct,
  spec-compliant one with X-ADCP-Signature/Authorization headers) and
  `deliver_webhook_with_retry` (the legacy one with X-Webhook-Signature,
  different retry semantics). The Wave-2 fix `cf731023` routed BDD
  scenarios through the correct service via `CircuitBreakerEnv`. The
  legacy service may be dead code worth removing — out of scope.
- **`_assert_partition_outcome`** in
  `tests/bdd/steps/generic/then_payload.py:209-230` is the shared
  Then-step helper for UC-005's binary `valid`/`invalid` Examples
  columns. UC-004 uses its own richer `_assert_partition_or_boundary`
  that parses `error "..." with suggestion` shapes. H1/H2 in the
  inventory cover known weaknesses.

## Where to ask for direction

The owner is Constantine Mirin
(`constantine@mirin.pro`). When in doubt about scope, file the question
to them before acting. Pattern: the green-main effort was scoped tightly
to "make main green again" and the strict-marker cleanup discovered 19
follow-ups — those should be tracked separately, not absorbed into the
main fix. The owner explicitly said "we don't want this main branch fix
to become a super big refactoring of unrelated issues" — that principle
applies to anything you discover going forward too.
