# Session handoff — PR #1430 execute batch + review + merges + CI in-network (2026-07-04 → 07-09)

Branch `feature/e2e-harness-wiring`, PR **#1430** (ready-for-review, reviewer ChrisHuie).
Head at write time: `6781ca52a`. All work below is committed and pushed.

## What landed (chronological)

**Ledger plan items 1–6 (molecular execution of 10 beads, all closed):**
- `7e8fdf46e` pawr — wrong-DB class closed structurally: all 14 e2e-capable
  `_harness_env` branches route through `_db_scope_for` (integration_db in-process,
  `_production_db_pointed_at` over e2e).
- `21461693e` 927n — uc011 agent identities carry `auth_token`; agent-sync Givens
  fail loudly (3 duplicate bodies → one helper); new guard
  `test_architecture_bdd_no_swallowed_dispatch_errors`.
- `8249689ed` mchp — `_seed_auto_approval` extracted (was inlined 3×), ext-o/ext-p
  seed it so e2e takes the auto create path.
- `3ebba4a7f` 7c5g — roas/cpa/media_buy_count Then steps; scenario became strict
  tag-declared gap; ledger 21→20.
- `f7910a5d1` o9b1 — 8 entries graduated on gate `innet_050726_2030` (ledger →12).
- tr1x — PR body+title rewritten (312→11 chronology at the time).

**ChrisHuie review (all findings addressed):**
- `17db1a026` kk15+39n0 — pending-approval create path runs shared creative
  validation (`_pre_validate_package_creatives`) before persisting; CREATIVE_REJECTED
  parity; REST wire-envelope repro tests + per-branch call-site guard.
- `f1ae8799b` pdje — delivery aggregated_totals: conversions/conversion_value/
  roas/cost_per_acquisition, omit-on-zero; strict tag graduated.
- `6b5c0575a` w2mx — fail_on_upload DB injection (shared `_raise_injected_failure`),
  catalog-real ext-q format, **real prod bug**: `_get_format_spec_sync` bare
  `asyncio.run()` always failed in async transports → `run_async_in_sync_context`.
- `555069ffe` hpjq — P0 cross-principal FK-500/leak: `get_creative_by_id`
  principal-scoped (composite PK); multi-principal repro observed live pre-fix;
  guard `test_architecture_creative_lookup_principal_scoped`.
- `eb5bba06e` dzmf — typed transient AdCPErrors propagate from format fetch
  (were swallowed → terminal CREATIVE_REJECTED).
- `e76061fad` tx41 — dormant BR-RULE-034 scenarios wired (2 latent step bugs fixed).
- `4fbbe8b58` t4or — local hand-authored cross-principal ASSIGNMENT feature
  (+ traceability entry). Upstream adcp-req reconciliation still open on the bead.
- `a39ce36a4`/`391d9cd8c`/`8f9118a93`/`dd5f09079` — ledger ratchet binds at
  len(EXPECTED_LEDGER); **CI job "BDD In-Network (e2e_rest)"** added (frozen-checks
  registered; branch-protection required-list is the owner's step); e2e engine
  dispose; nits batch (GH refs in comments, ADCP_TESTING-gated sim read,
  per-branch guard counting, spec citation for quotients).
- Review reply posted: PR comment 4918901719.

**Semantic merges (per-file agent workflows, user-mandated method):**
- `60ff7fb58` — updated PR #1417 head `385388920` (23 commits, 68 files, 3 conflicts).
  Key: kept our flag-based `_raise_injected_failure` with #1417's suggestion-promotion.
- `5ff726a23` — origin/main 4 commits (#1545 completed_views rename + status
  taxonomy; 47 files, 5 conflicts). Workflow agent caught main's new UC-019 branch
  on the pre-`_db_scope_for` pattern and corrected it.
- `95a8da9f1` — post-merge fallout: update wire now date-refines via shared
  `_compute_status` (scheduled→pending_start honest again; `_adcp_status_and_actions`
  takes the buy row); dual-emit Given makes "scheduled" pre-flight (clears stale
  start_time — start_time OUTRANKS start_date in the resolver); 3rd stale
  persisted-authoritative test class aligned.
- `370d84bbd` — first in-network CI run fallout (44 e2e_rest-only failures):
  (a) e2e_rest no longer appended for `_NO_REST` UCs (UC-019 has no REST route —
  live 404 by design); (b) `"rest-…"` xfail substrings anchored `"[rest-…"` (were
  matching `e2e_rest-` nodeids — 112 rows); (c) #1270 date-range tripwires FIRED
  (live server validates start>=end now) → removed, 2 boundary ledger entries
  graduated, 2 merged-upstream account rows ledgered (net ledger 11).
- `6781ca52a` — in-network job skips the host uvx audit (RUN_ALL_SKIP_AUDIT=1;
  no _setup-env in that job; dedicated Security Audit check owns the scan).

## CI state at handoff
Latest push running; earlier snapshot: 10 pass / 18 pending / **1 fail: pip-audit**
(35m, job 86119460452 — NOT yet diagnosed; possibly the same fresh uv advisory
GHSA-4gg8-gxpx-9rph that hit uv-secure's tool check, or its own thing. NEXT ACTION:
read that job's log). Watch "BDD In-Network (e2e_rest)" — should be green after
`370d84bbd`+`6781ca52a`; its non-strict xpass count needs the usual inspection.

## THE headline decision (owner, 2026-07-09)
**e2e without MCP is a flawed design — 90% of buyers use MCP.** The `e2e_mcp`
transport belongs **in PR #1430** ("the current PR made actually useful"), not a
follow-up. Bead: **salesagent-8fhz** (P1) carries the full design: an MCP client
that genuinely speaks AdCP (fastmcp Client + StreamableHttpTransport →
`proxy:8000/mcp/`, x-adcp-auth, same tool kwargs as in-process `_run_mcp_client`,
wire capture via structured_content / AdCPToolError envelopes; `uvx adcp` CLI as
framing reference), Transport.E2E_MCP parametrization incl. UC-019 (which today has
ZERO live coverage), first-run triage wave + ledger mechanics, CI timeout/split.
**This is the next major work item.**

## Open beads (this branch's orbit)
- salesagent-8fhz — e2e_mcp driver (P1, in-PR; see above).
- salesagent-jwhs — seed referenced accounts so uc004 account valid rows genuinely
  pass (in-process xfail rows + 2 e2e ledger rows reference it).
- salesagent-tpr3 — default-format reseed (84-file seam; full plan in bead).
- salesagent-t4or — upstream adcp-req storyboard scenario (local half done).
- salesagent-e2aw — 13 uc004 webhook tag-family e2e xpasses (pawr unlock) retirement.
- salesagent-8efn — obsolete buyer_refs boundary xpass retirement.
- salesagent-om5y — consolidate duplicate post-adapter creative checks (auto path).
- salesagent-94ij CI-required-check + branch protection = owner steps.

## Infrastructure learnings (verified this session)
- The "random" kills of local gates = **host memory saturation** (48 GB host at
  47 GB used/20 GB compressor; concurrent worktree sessions + dev stacks). Docker
  VM is 15.6 GiB. Concurrent in-net gates are BY DESIGN fine (owner) — it's host
  RAM that reaps runner processes. GitHub CI is the reliable gate executor now.
- `deploy-langfuse-1` was crash-looping (stopped on owner request); budibase gone.
- pytest gotchas that cost time here: piping `make quality` to `tail` masks
  failures (use pipefail); background `pgrep` watchers self-match (use docker-ps
  probes); `"rest-"` substrings match `e2e_rest-` nodeids.

## Test-escape root-cause (kept for the PR narrative)
The cross-principal P0 survived because: storyboard has no cross-principal
ASSIGNMENT case (ungraded), the adjacent BR-RULE-034 scenarios were dormant
(wired-but-never-run), and the suite's default topology is single-principal.
Fixed at all three layers (guard + wiring + local feature + upstream bead).
