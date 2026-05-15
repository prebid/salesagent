# PR 2 — Pattern A Cleanup Sweep — Implementation Plan

**Status**: PR 1 (#1306) approved; ready to start PR 2 when #1306 merges to main.
**Tracking**: GitHub architecture issue (body still drafted in chat, not yet posted) → PR 2 references it in commit FIXMEs.
**Scope**: PR 2 of the 3-PR sequence. Drains the 4 structural-guard allowlists landed in PR 1.

This plan is a follow-on to [`PLAN.md`](./PLAN.md) (the master architecture plan). PR 1's
decision log (D1-D16) and architecture context still apply; only the PR-2-specific
additions are repeated here. Cold-start agents should read `PLAN.md` first.

---

## NEXT SESSION — Resume After Compact

Cold-start steps (in order; do NOT skip):

1. **Read this file top to bottom** and then `PLAN.md` for architectural context. This file is the
   durable PR 2 execution plan.

2. **Confirm PR #1306 is merged to main** before cutting the PR 2 branch. If not yet merged,
   the substrate isn't on main and PR 2 has no foundation:
   ```bash
   git -C /Users/quantum/Documents/ComputedChaos/salesagent fetch origin
   git -C /Users/quantum/Documents/ComputedChaos/salesagent log --oneline origin/main..HEAD
   gh pr view 1306 --json state,mergedAt --jq '{state, mergedAt}'
   ```

3. **Cut the feature branch FIRST**, before any code changes:
   ```bash
   git checkout main
   git pull --rebase
   git checkout -b feature/error-emission-architecture-pr2
   ```
   Per user convention (`feedback_feature_branch_first`).

4. **Check PR #1262 status** (cancel work) — its merge state determines coordination:
   ```bash
   gh pr view 1262 --json state,isDraft --jq '{state, isDraft}'
   ```
   - If merged: rebase incorporates its 4 Pattern A sites; add them to PR 2 migration list (§5).
   - If still draft: PR 2 proceeds without it; #1262's sites stay capped until that PR lands.
   - If merged after PR 2 starts: hold the sub-batch that touches `media_buy_update.py` until
     it lands, then rebase.

5. **Verify the architecture issue is posted** (if user posted it). Use its number for FIXME
   comments at any new caps PR 2 introduces (unlikely; the goal is drain to zero).

6. **Follow the sub-batch sequence in §7** — each sub-batch is its own commit. Five sub-batches
   total. Do not collapse into a single commit; reviewers asked for batched diffs.

7. **Run quality gates per §11 before each sub-batch commit**, not just at PR ready-for-review.

8. **User pushes git themselves** (`feedback_user_owns_git_push`). Do not run `git push` or
   `gh pr create` without explicit instruction.

---

## 1. What this is, in one paragraph

PR 1 (#1306) landed the error-emission **substrate**: typed `AdCPError` subclasses, the
`build_two_layer_error_envelope()` serializer wired into all 3 transport boundaries, the
`ContextManager.fail_step()` ingress, and 4 structural guards that froze the current Pattern A
site counts as a per-file shrink-only ratchet. PR 2 is the **cleanup sweep** that drains those
caps to zero. The work is mechanical: 82 `Error(code="...")` Pattern A sites become typed
`AdCPError` subclass raises; 32 boundary-facing `raise ValueError` sites in tools/ become typed
raises (12 internal-helper ValueErrors stay); the `media_buy_create.py:1924` catchall narrows
to AdCPError-only; the 7 context-missing sites in `media_buy_update.py` deferred from PR 1 get
`context=req.context`; 21 of 24 `model_dump()` allowlist entries drain via
`ContextManager.fail_step()` adoption. Once green, the storyboard smoke test deferred in PR 1
(`media_buy_seller/invalid_transitions`) is re-run end-to-end and must produce real error codes
on both the raise-path AND the return-path that PR 1 couldn't fully fix.

---

## 2. Decision Log — extending PR 1's D1-D16

### D17 — ValueError migration scope: boundary-only

**Decision**: Migrate `raise ValueError` sites that escape to a transport boundary; leave
internal-helper ValueError raises that enforce programmer contracts unchanged. Of the 44
ValueError sites in `src/core/tools/`, 32 are boundary-facing and migrate; 12 are internal
contracts and stay.

**Rationale**: ValueError at a helper that's only called from `_impl` (e.g., "session is
required for `_validate_creatives_before_adapter_call`") is a programmer-error invariant — the
caller is salesagent code, not a buyer agent. Converting to `AdCPValidationError` would be
worse: buyers would see `VALIDATION_ERROR` for what is in fact a code bug. Boundary sites where
the raise escapes through `_impl` to MCP/A2A/REST → buyer must migrate so the envelope
serializes correctly.

**Adapter ValueError disposition**: 82 sites in adapters (per the cap dict) follow the same
rule. Plan: audit per-file inside sub-batch 4. Adapters that re-export internal validation
helpers (e.g., GAM auth, targeting compilers) typically stay ValueError; adapters that raise at
the operation boundary (a buyer-visible failure) migrate.

**Trade-off**: A future contributor might call an "internal" helper from a new code path
without the assertion-protection mindset. Acceptable: structural guards still catch new
ValueError additions; a future audit can re-classify a specific site if it gains a boundary
caller.

### D18 — `details["error_code"]` workaround: typed subclasses, not generic `AdCPExtensionError`

**Decision**: No `AdCPExtensionError(code: str)` generic class. Where the legacy code emitted
`Error(code="X", details={"error_code": "Y"})` to smuggle a more-specific code through a
generic exception, PR 2 introduces a specific subclass for "Y" if missing, or maps "X" to a
typed raise if "Y" is just metadata.

**Rationale**: Per PR 1 D2's intent, the typed hierarchy is the user-facing vocabulary. A
generic `AdCPExtensionError` would re-introduce the Pattern A anti-pattern at a different
layer (caller decides the code at raise time, not the class). The enumeration found 0
`details["error_code"]` workarounds in tools and adapters — the pattern that PR 1 PLAN section
3 worried about doesn't actually exist in production code today. (PR 1's typed-raise
migrations in `_impl` may have eliminated it.)

**Trade-off**: If PR 2 finds a site we missed (rare, structural guards would catch new ones),
add a specific subclass. The typed-subclass cost is ~6 lines per new class.

### D19 — Sub-batch ordering: surfaces-first, hot-path-last

**Decision**: Five sub-batches in this order (see §7 for full detail):

1. **Tools tools/ low-traffic** (8 sites across 5 files): accounts, creative_formats,
   creatives/_processing, media_buy_delivery, signals.
2. **Tools media_buy_create.py** (4 Pattern A + 18 ValueError + catchall narrowing): single
   most-complex file in PR 2, separated for review focus.
3. **Tools media_buy_update.py** (21 Pattern A + 1 ValueError + 7 context-missing): the bulk
   of `_impl` Pattern A; coordinates with PR #1262 if it merges mid-flight.
4. **Adapters Pattern A** (45 sites across 4 adapters): mostly `AdCPAdapterError`, mechanical.
5. **Adapters ValueError audit + ContextManager.fail_step adoption** (~31 boundary sites,
   model_dump allowlist drain).

**Rationale**: Surfaces-first lets the structural guards confirm the migration pattern works on
low-traffic sites before touching `media_buy_update.py`. The model_dump drain at the end means
`ContextManager.fail_step` adoption is concentrated, easier to review uniformly. Each sub-batch
is its own commit; reviewers can ack one sub-batch at a time.

**Trade-off**: 5 sub-batches in 1 PR means a longer PR. If reviewers ask, split into 2 PRs at
the sub-batch 3/4 boundary (tools complete vs adapters); the natural break.

### D20 — PR #1262 coordination protocol

**Decision**:
- **#1262 merges before PR 2 cut**: rebase main, add its 4 Pattern A sites to migration list
  (§5 dynamic; site list updates from the cap dict on branch cut).
- **#1262 merges during PR 2 (after sub-batch 3 starts)**: hold sub-batch 3, let #1262 land,
  rebase, restart sub-batch 3 with its sites included.
- **#1262 still draft at PR 2 merge**: PR 2 proceeds without it; its sites stay in the cap (cap
  dict is current-actual at PR 2 commit time; doesn't grow). When #1262 eventually merges, its
  raises must use typed subclasses (the structural guard will block plain `Error(code=...)`).

**Rationale**: Aligns with the only-shrink invariant of the structural guards. #1262 is the
user's iteration; not the right call to block on it.

### D21 — `ContextManager.fail_step` adoption strategy

**Decision**: Adopt `ctx_manager.fail_step(step.step_id, exc=exc)` at every Pattern A
return-shape site that currently calls `ctx_manager.update_workflow_step(step.step_id,
status="failed", response_data=<inline-built dict>, ...)`. PR 1 added `fail_step` precisely so
the wire-response and persisted `workflow_step.response_data` stay byte-identical; PR 2 makes
it the actual ingress.

**Implementation**: Migration is per-site. Each call to
`update_workflow_step(..., status="failed", response_data={...})` becomes
`fail_step(step_id, exc=exc)`. The `model_dump()` allowlist sites named in PR 1 (21 of 24)
correspond 1:1 to these adoptions.

**Trade-off**: `fail_step` requires the typed `AdCPError` instance, which is exactly what PR 2
already produces. No additional cost.

### D22 — Catchall narrowing at `media_buy_create.py:1924-1936`

**Decision**: Narrow `except (ValueError, PermissionError) as e: return CreateMediaBuyError(...)`
to `except AdCPError as e: ...`. Since sub-batch 2 migrates all 26 internal ValueErrors in
`media_buy_create.py` (18 boundary → typed AdCPError, 8 internal → keep ValueError), the
catchall doesn't need to swallow ValueError anymore — typed AdCPError reaches it directly. The
8 internal-helper ValueErrors are programmer bugs that should crash with a stack trace, not be
caught and wire-shaped.

**Rationale**: The current catchall is a workaround for the Pattern A return pattern. With
typed raises, `except AdCPError` is the right granularity. PermissionError is rarely raised in
this path; if it ever fires, it should propagate to the boundary translator's PermissionError
handler.

**Trade-off**: If sub-batch 2 misses an internal ValueError site that turns out to be boundary
in production, an uncaught ValueError reaches the boundary. Boundary translator wraps it in
`AdCPValidationError` (per PR 1's `_translate_to_tool_error`). Acceptable safety net.

---

## 3. Scope — What's In, What's Out

### In Scope

- **Pattern A migration**: 82 sites across 11 files, all to typed `AdCPError` subclasses.
- **ValueError migration in tools/**: 32 boundary-facing sites of 44 total.
- **ValueError audit in adapters/**: 82 sites; boundary-facing migrate, internal-helper stay.
- **Catchall narrowing**: `media_buy_create.py:1924-1936` → `except AdCPError`.
- **Context-missing fixes**: 7 sites in `media_buy_update.py` get `context=req.context` (D6
  from PR 1, deferred).
- **`ContextManager.fail_step` adoption**: 21+ sites currently doing inline `update_workflow_step`
  with status=failed migrate to fail_step.
- **Cap-dict shrink**: all 4 PR 1 structural-guard allowlists drain to zero.
- **`model_dump()` allowlist drain**: 21 of 24 entries (the ones that drain via fail_step
  adoption; the 3 remaining are out-of-scope helpers).
- **Storyboard smoke test re-run**: `media_buy_seller/invalid_transitions` (3 codes) must
  produce real codes on raise-path AND return-path. PR 1 only fixed raise-path.
- **FIXME removal**: any FIXME comments PR 1 added at allowlisted sites get removed as the
  sites migrate.

### Out of Scope

- **PR 3 work**: async/submitted lifecycle, Response3 envelope, `task_id` population, status
  transitions. Separate companion-issue.
- **adcp SDK 4.3 → 5.5 upgrade**: independent decision, not bundled (D8).
- **New typed subclasses**: enumeration confirmed 0 needed (§5). If a corner case turns up,
  add it inline.
- **3 `AdCPConfigurationError` raises in `src/core/database/models.py`** (encrypted secret
  decryption failure): per PR 1 D10, these stay. They're admin-required server-config faults;
  `SERVICE_UNAVAILABLE` (the wire-translated code) is the right buyer-facing facade.
- **Adapter ValueError sites that are internal helpers** (per D17): keep as ValueError.
- **Storyboard CI matrix extension** beyond `invalid_transitions`: out of scope (PR 1 D3 also
  rejected this; tracked in #1228).

---

## 4. Blast Radius — Files Touched

| File | Sub-batch | Pattern A sites | ValueError sites | Other |
|---|---|---|---|---|
| `src/core/tools/accounts.py` | 1 | 2 | 0 | |
| `src/core/tools/creative_formats.py` | 1 | 1 | 0 | |
| `src/core/tools/creatives/_processing.py` | 1 | 1 | 2 (both → AdCPConfigurationError) | |
| `src/core/tools/creatives/_validation.py` | 1 | 0 | 5 (all boundary) | |
| `src/core/tools/media_buy_delivery.py` | 1 | 5 | 0 | |
| `src/core/tools/signals.py` | 1 | 3 (cap; agent found 2 — 1 multi-line) | 0 | |
| `src/core/tools/media_buy_create.py` | 2 | 4 | 26 (18 boundary, 8 internal) | catchall narrowing line 1924 |
| `src/core/tools/media_buy_update.py` | 3 | 21 | 5 (1 boundary, 4 internal) | 7 context-missing fixes |
| `src/core/tools/task_management.py` | 1 | 0 | 4 (all boundary) | |
| `src/core/tools/performance.py` | 1 | 0 | 1 (internal — keep) | |
| `src/core/tools/products.py` | 1 | 0 | 1 (boundary) | |
| `src/adapters/broadstreet/adapter.py` | 4 | 13 | 3 (audit) | |
| `src/adapters/google_ad_manager.py` | 4 | 22 | 8 (audit) | |
| `src/adapters/kevel.py` | 4 | 5 | 2 (audit) | |
| `src/adapters/triton_digital.py` | 4 | 5 | 2 (audit) | |
| `src/adapters/__init__.py`, `base.py`, `gam/*` | 5 (audit) | 0 | ~58 (mostly internal — keep) | |
| `src/adapters/broadstreet/config_schema.py`, `gam_implementation_config_schema.py` | 5 (audit) | 0 | 8 (internal helpers — keep) | |
| `src/adapters/mock_ad_server.py`, `xandr.py` | 5 (audit) | 0 | 12 (mix; audit) | |
| `src/core/context_manager.py` | 5 | 0 | 0 | `fail_step` callsite adoption |
| `tests/unit/test_architecture_no_error_construction_in_impl.py` | each sub-batch | 0 | 0 | cap dict drains to 0 |
| `tests/unit/test_architecture_no_value_error_in_impl.py` | each sub-batch | 0 | 0 | cap dict drains to ~58 (internal helpers) |
| `tests/unit/test_architecture_no_model_dump_in_impl.py` | 5 | 0 | 0 | drain 21 of 24 entries |
| `tests/integration/*` | each sub-batch | 0 | 0 | per-tool integration tests verify migration |

---

## 5. Per-File Pattern A Mapping

**Critical finding**: All 82 Pattern A sites map to existing subclasses. **Zero new subclasses
required**.

### Tools — 37 sites

#### `src/core/tools/accounts.py` — 2 sites

| Line | Code | What | Replacement |
|---|---|---|---|
| 387 | `VALIDATION_ERROR` | Reserved-TLD domain in account onboarding | `AdCPValidationError` |
| 418 | `UNSUPPORTED_FEATURE` | Billing model not in tenant's supported list | `AdCPCapabilityNotSupportedError` |

**Shape note**: Both return inside `SyncAccountsResponse(errors=list[Error])`. These ARE
advisory-errors on a success envelope (multiple accounts can be checked; each gets pass/fail).
**This is the success-envelope `errors[]` pattern that should NOT migrate to typed raises** —
they're per-item results, not whole-operation failures. Confirm with caller; if confirmed,
mark as allowlist-permanent and `# noqa: structural-guard` instead of migrating.

#### `src/core/tools/creative_formats.py` — 1 site

| Line | Code | What | Replacement |
|---|---|---|---|
| 146 | `SERVICE_UNAVAILABLE` | Creative agent registry init failed | `AdCPServiceUnavailableError` |

**Shape**: `return ListCreativeFormatsResponse(errors=[...])`. Similar to accounts.py — verify
this is operation-level failure (then migrate to raise) vs per-format advisory (then allowlist).

#### `src/core/tools/creatives/_processing.py` — 1 site

| Line | Code | What | Replacement |
|---|---|---|---|
| 34 | `SERVICE_UNAVAILABLE` | Creative agent unreachable during sync_creatives | `AdCPServiceUnavailableError` |

**Shape**: `return SyncCreativeResult(errors=[...])`. Per-creative advisory — likely
allowlist-permanent.

#### `src/core/tools/media_buy_create.py` — 4 sites

| Line | Code | What | Replacement |
|---|---|---|---|
| 1383 | `AUTH_REQUIRED` | Principal not found in DB | `AdCPAuthenticationError` |
| 1932 | `VALIDATION_ERROR` | Catchall for validation+permission errors | **DELETED** by D22 catchall narrowing |
| 2492 | `VALIDATION_ERROR` | GAM config validation errors (list comprehension) | `AdCPValidationError` |
| 2870 | `VALIDATION_ERROR` | start_time/end_time missing | `AdCPValidationError` |

**Note**: Line 1932 is the catchall return; D22 narrows to `except AdCPError` so the inline
`Error(code=...)` build disappears.

#### `src/core/tools/media_buy_delivery.py` — 5 sites

| Line | Code | What | Replacement |
|---|---|---|---|
| 104 | `AUTH_REQUIRED` | Principal ID missing from context | `AdCPAuthenticationError` |
| 125 | `AUTH_REQUIRED` | Principal not found in DB | `AdCPAuthenticationError` |
| 159 | `VALIDATION_ERROR` | start_date >= end_date | `AdCPValidationError` |
| 189 | `MEDIA_BUY_NOT_FOUND` | Requested media buy doesn't exist | `AdCPMediaBuyNotFoundError` |
| 321 | `SERVICE_UNAVAILABLE` | Adapter call failed | `AdCPAdapterError` |

**Shape**: All return inside `GetMediaBuyDeliveryResponse(errors=[...])`. **Line 189 is the
per-buy advisory pattern** (one request can ask for delivery on multiple buys; missing buys
are reported per-buy). Verify; if per-item, allowlist line 189. Lines 104, 125, 159, 321 are
operation-level failures and migrate to `raise`.

#### `src/core/tools/media_buy_update.py` — 21 sites

| Line | Code | Replacement |
|---|---|---|
| 260 | `AUTH_REQUIRED` | `AdCPAuthenticationError` |
| 376 | `UNSUPPORTED_FEATURE` | `AdCPCapabilityNotSupportedError` |
| 433 | `BUDGET_EXCEEDED` | `AdCPBudgetExceededError` |
| 542 | `VALIDATION_ERROR` | `AdCPValidationError` |
| 576 | `BUDGET_TOO_LOW` | `AdCPBudgetTooLowError` |
| 626 | `VALIDATION_ERROR` | `AdCPValidationError` |
| 646 | `MEDIA_BUY_NOT_FOUND` | `AdCPMediaBuyNotFoundError` |
| 672 | `CREATIVE_REJECTED` | `AdCPCreativeRejectedError` |
| 851 | `VALIDATION_ERROR` | `AdCPValidationError` |
| 882 | `SERVICE_UNAVAILABLE` | `AdCPAdapterError` |
| 910 | `VALIDATION_ERROR` | `AdCPValidationError` |
| 930 | `MEDIA_BUY_NOT_FOUND` | `AdCPMediaBuyNotFoundError` |
| 953 | `PACKAGE_NOT_FOUND` | `AdCPPackageNotFoundError` |
| 976 | `VALIDATION_ERROR` | `AdCPValidationError` |
| 984 | `UNSUPPORTED_FEATURE` | `AdCPCapabilityNotSupportedError` |
| 1082 | `VALIDATION_ERROR` | `AdCPValidationError` |
| 1100 | `PACKAGE_NOT_FOUND` | `AdCPPackageNotFoundError` |
| 1149 | `VALIDATION_ERROR` | `AdCPValidationError` |
| 1167 | `BUDGET_EXCEEDED` | `AdCPBudgetExceededError` |
| 1243 | `MEDIA_BUY_NOT_FOUND` | `AdCPMediaBuyNotFoundError` |
| 1275 | `VALIDATION_ERROR` | `AdCPValidationError` |

**Context-missing sites (D6 from PR 1)**: lines 458, 527, 598, 1081, 1099, 1242, 1274. These
are NOT in the above table because they're at line numbers shifted slightly during PR 1; the
list above is from the enumeration agent. Cross-check at branch-cut time against the cap dict
in `test_architecture_no_error_construction_in_impl.py`.

**For ALL 21 sites**: each raise gets `context=req.context` to echo correlation_id. The
boundary translator's `build_two_layer_error_envelope` propagates context into the wire
envelope.

#### `src/core/tools/signals.py` — 3 sites (per cap; agent found 2 distinct + 1 multi-line)

| Line | Code | What | Replacement |
|---|---|---|---|
| 288 | `SERVICE_UNAVAILABLE` | Signal provider unavailable | `AdCPServiceUnavailableError` |
| 303 | `SERVICE_UNAVAILABLE` | Generic exception during signal activation | `AdCPAdapterError` |
| (3rd site, multi-line) | — | Cross-check at branch-cut time | likely `AdCPServiceUnavailableError` |

### Adapters — 45 sites

All adapter sites use the `return SomeResponse(errors=[Error(code=...)])` pattern. After
migration, they `raise` typed AdCPError and the caller `_impl` propagates to the boundary.

#### `src/adapters/broadstreet/adapter.py` — 13 sites

| Line | Code | Replacement |
|---|---|---|
| 336 | `VALIDATION_ERROR` | `AdCPValidationError` |
| 632 | `UNSUPPORTED_FEATURE` | `AdCPCapabilityNotSupportedError` |
| 656 | `PACKAGE_NOT_FOUND` | `AdCPPackageNotFoundError` |
| 682 | `SERVICE_UNAVAILABLE` | `AdCPAdapterError` |
| 708 | `VALIDATION_ERROR` | `AdCPValidationError` |
| 725 | `PACKAGE_NOT_FOUND` | `AdCPPackageNotFoundError` |
| 744 | `API_UPDATE_FAILED` (INTERNAL_CODES) | `AdCPAdapterError` |
| 769 | `VALIDATION_ERROR` | `AdCPValidationError` |
| 779 | `VALIDATION_ERROR` | `AdCPValidationError` |
| 793 | `PACKAGE_NOT_FOUND` | `AdCPPackageNotFoundError` |
| 817 | `VALIDATION_ERROR` | `AdCPValidationError` |
| 827 | `VALIDATION_ERROR` | `AdCPValidationError` |
| 841 | `PACKAGE_NOT_FOUND` | `AdCPPackageNotFoundError` |

#### `src/adapters/google_ad_manager.py` — 22 sites

| Line | Code | Replacement |
|---|---|---|
| 405 | `UNSUPPORTED_FEATURE` | `AdCPCapabilityNotSupportedError` |
| 425 | `CONFIGURATION_ERROR` (INTERNAL_CODES) | `AdCPConfigurationError` |
| 526 | `PRODUCT_UNAVAILABLE` | `AdCPProductUnavailableError` |
| 549 | `PRODUCT_UNAVAILABLE` | `AdCPProductUnavailableError` |
| 564 | `UNSUPPORTED_FEATURE` | `AdCPCapabilityNotSupportedError` |
| 598 | `WORKFLOW_CREATION_FAILED` (INTERNAL_CODES) | `AdCPAdapterError` |
| 768 | `LINE_ITEM_CREATION_FAILED` (INTERNAL_CODES) | `AdCPAdapterError` |
| 1318 | `AUTH_REQUIRED` | `AdCPAuthorizationError` (admin-only-action; not auth-missing) |
| 1339 | `WORKFLOW_CREATION_FAILED` (INTERNAL_CODES) | `AdCPAdapterError` |
| 1368 | `ACTIVATION_WORKFLOW_FAILED` (INTERNAL_CODES) | `AdCPAdapterError` |
| 1388 | `VALIDATION_ERROR` | `AdCPValidationError` |
| 1408 | `PACKAGE_NOT_FOUND` | `AdCPPackageNotFoundError` |
| 1427 | `BUDGET_EXCEEDED` | `AdCPBudgetExceededError` |
| 1445 | `VALIDATION_ERROR` | `AdCPValidationError` |
| 1471 | `GAM_UPDATE_FAILED` (INTERNAL_CODES) | `AdCPAdapterError` |
| 1512 | `VALIDATION_ERROR` | `AdCPValidationError` |
| 1527 | `PACKAGE_NOT_FOUND` | `AdCPPackageNotFoundError` |
| 1540 | `VALIDATION_ERROR` | `AdCPValidationError` |
| 1558 | `GAM_UPDATE_FAILED` (INTERNAL_CODES) | `AdCPAdapterError` |
| 1591 | `PACKAGE_NOT_FOUND` | `AdCPPackageNotFoundError` |
| 1621 | `SERVICE_UNAVAILABLE` | `AdCPServiceUnavailableError` |
| 1659 | `UNSUPPORTED_FEATURE` | `AdCPCapabilityNotSupportedError` |

**Note on line 1318**: `AUTH_REQUIRED` for an admin-permission gate is `AdCPAuthorizationError`
(auth ok, permission denied), not `AdCPAuthenticationError` (auth missing). Both pin
`AUTH_REQUIRED` as `error_code`, so the wire output is identical; the semantic distinction is
the Python type.

#### `src/adapters/kevel.py` — 5 sites

| Line | Code | Replacement |
|---|---|---|
| 229 | `UNSUPPORTED_FEATURE` | `AdCPCapabilityNotSupportedError` |
| 616 | `UNSUPPORTED_FEATURE` | `AdCPCapabilityNotSupportedError` |
| 708 | `FLIGHT_NOT_FOUND` (INTERNAL_CODES) | `AdCPPackageNotFoundError` |
| 750 | `FLIGHT_NOT_FOUND` (INTERNAL_CODES) | `AdCPPackageNotFoundError` |
| 778 | `API_ERROR` (INTERNAL_CODES) | `AdCPAdapterError` |

#### `src/adapters/triton_digital.py` — 5 sites

| Line | Code | Replacement |
|---|---|---|
| 157 | `UNSUPPORTED_FEATURE` | `AdCPCapabilityNotSupportedError` |
| 552 | `UNSUPPORTED_FEATURE` | `AdCPCapabilityNotSupportedError` |
| 643 | `FLIGHT_NOT_FOUND` (INTERNAL_CODES) | `AdCPPackageNotFoundError` |
| 685 | `FLIGHT_NOT_FOUND` (INTERNAL_CODES) | `AdCPPackageNotFoundError` |
| 712 | `API_ERROR` (INTERNAL_CODES) | `AdCPAdapterError` |

### Subclass usage summary (across all 82 sites)

| Subclass | Sites | Notes |
|---|---|---|
| `AdCPValidationError` | ~26 | The most common pattern |
| `AdCPAdapterError` | ~11 | All `API_ERROR`, `*_UPDATE_FAILED`, `WORKFLOW_*` INTERNAL_CODES converge here |
| `AdCPPackageNotFoundError` | ~10 | `PACKAGE_NOT_FOUND`, `FLIGHT_NOT_FOUND` (kevel/triton's term for package) |
| `AdCPCapabilityNotSupportedError` | ~8 | `UNSUPPORTED_FEATURE` |
| `AdCPAuthenticationError` | ~5 | Auth missing |
| `AdCPMediaBuyNotFoundError` | ~5 | `MEDIA_BUY_NOT_FOUND` |
| `AdCPServiceUnavailableError` | ~4 | Service unavailable to buyer (separate from adapter failure) |
| `AdCPBudgetExceededError` | ~3 | Over ceiling |
| `AdCPProductUnavailableError` | ~2 | `PRODUCT_UNAVAILABLE` |
| `AdCPBudgetTooLowError` | ~1 | Below minimum |
| `AdCPCreativeRejectedError` | ~1 | Policy/format rejection |
| `AdCPConfigurationError` | ~1 | Missing required server config |
| `AdCPAuthorizationError` | ~1 | Admin-only action |

Total: ~78 mapped; ~4 sites are advisory-on-success-envelope (accounts.py:387, 418;
creative_formats.py:146; creatives/_processing.py:34; media_buy_delivery.py:189) and need
caller-verification to confirm allowlist-permanent disposition.

---

## 6. ValueError Migration

### Rule (D17)

A `raise ValueError(...)` site migrates to typed `AdCPError` **if and only if** it can escape
to a transport boundary. Use this test:
- Is the function `_impl`-suffixed, or directly callable from an MCP/A2A/REST entry point?
  → **boundary** → migrate.
- Is the function called only from other salesagent internal code (a helper, validator,
  contract enforcer)? → **internal** → keep as ValueError.

The boundary translator's `_translate_to_tool_error` wraps any unwrapped ValueError as a
synthetic `AdCPValidationError` (PR 1 D17). So an "internal" ValueError that accidentally
escapes still produces a valid envelope, just with reduced semantic precision. The rule
optimizes for buyer-facing clarity, not crash-resistance.

### Tools — 32 boundary sites of 44 total

#### `src/core/tools/creatives/_processing.py` — 2 of 2 boundary

| Line | What | Replacement |
|---|---|---|
| 199 | `GEMINI_API_KEY` missing during generative-creative update | `AdCPConfigurationError` |
| 526 | `GEMINI_API_KEY` missing during generative-creative build | `AdCPConfigurationError` |

#### `src/core/tools/creatives/_validation.py` — 5 of 5 boundary

| Line | What | Replacement |
|---|---|---|
| 95 | Creative name empty | `AdCPValidationError` |
| 98 | Creative format missing | `AdCPValidationError` |
| 104 | Format `id` unresolvable | `AdCPValidationError` |
| 133 | Creative agent unreachable | `AdCPValidationError` (or `AdCPAdapterError`) |
| 140 | Unknown format from agent | `AdCPValidationError` |

#### `src/core/tools/media_buy_create.py` — 18 of 26 boundary

| Line | What | Replacement |
|---|---|---|
| 264 | `session` param missing | **INTERNAL** — keep (programmer contract) |
| 678 | `agent_url` not HTTPS | **INTERNAL** — keep (format approval helper) |
| 1511 | Budget not positive | `AdCPValidationError` (not `AdCPBudgetExceededError` — that's ceiling) |
| 1519 | `start_time` required | `AdCPValidationError` |
| 1536 | Unexpected `start_time` type | **INTERNAL** — keep (programmer error) |
| 1542 | `start_time` in past | `AdCPValidationError` |
| 1547 | `end_time` required | `AdCPValidationError` |
| 1556 | `end_time` ≤ `start_time` | `AdCPValidationError` |
| 1575 | At least one product required | `AdCPValidationError` |
| 1582 | Package missing `product_id` | `AdCPValidationError` |
| 1593 | Duplicate `product_id` in packages | `AdCPConflictError` |
| 1626 | Product IDs not found | `AdCPProductUnavailableError` |
| 1715 | Currency unsupported | `AdCPCapabilityNotSupportedError` |
| 1735 | Currency unsupported (supported list) | `AdCPCapabilityNotSupportedError` |
| 1761 | Caught AdCPError re-raised as ValueError | **REMOVE** — `raise` original; defeats type-preservation |
| 1840 | Min package budget violation | `AdCPBudgetTooLowError` |
| 1856 | Min package budget (legacy) | `AdCPBudgetTooLowError` |
| 1884 | Max daily package spend exceeded | `AdCPBudgetExceededError` |
| 1898 | Max daily package (legacy) | `AdCPBudgetExceededError` |
| 1922 | Targeting validation failed | `AdCPValidationError` |
| 2610 | Package missing `product_id` field | **INTERNAL** — keep (sanity check) |
| 2620 | Package references unknown `product_id` | `AdCPProductUnavailableError` |
| 2700 | Product doesn't support format | `AdCPCapabilityNotSupportedError` |
| 3049 | Adapter didn't return `package_id` | **INTERNAL** — keep (adapter contract) |
| 3257 | Same | **INTERNAL** — keep |
| 3477 | Same | **INTERNAL** — keep |

#### `src/core/tools/media_buy_update.py` — 1 of 5 boundary

| Line | What | Replacement |
|---|---|---|
| 121 | Media buy not found | `AdCPMediaBuyNotFoundError` |
| 164 | Identity required for update_media_buy | **INTERNAL** — keep (auth contract) |
| 169 | `principal_id` required | **INTERNAL** — keep (auth contract) |
| 187 | `media_buy_id` required | **INTERNAL** — keep (required-field contract) |
| 243 | Context creation failed | **INTERNAL** — keep (programmer error) |

#### `src/core/tools/task_management.py` — 4 of 4 boundary

| Line | What | Replacement |
|---|---|---|
| 154 | Task not found | `AdCPNotFoundError` |
| 222 | Invalid status string | `AdCPValidationError` |
| 230 | Task not found | `AdCPNotFoundError` |
| 233 | Task in terminal state, cannot transition | `AdCPConflictError` |

#### `src/core/tools/performance.py` — 0 of 1 boundary

| Line | What | Replacement |
|---|---|---|
| 60 | Identity required | **INTERNAL** — keep (auth contract) |

#### `src/core/tools/products.py` — 1 of 1 boundary

| Line | What | Replacement |
|---|---|---|
| 366 | Product object → schema conversion failure | `AdCPAdapterError` (data integrity, not buyer-fault) |

### Adapters — Audit rule, not enumeration

For each of the ~82 ValueError sites in `src/adapters/`, apply the boundary-vs-internal rule
from D17. Most adapter ValueErrors are internal validators (e.g.,
`gam/managers/targeting.py`'s 22 sites are all internal targeting-spec validators called from
the adapter's `create_media_buy`); these stay. Adapter sites that raise at the
operation-method boundary (e.g., `adapter.create_media_buy()` validating its inputs) migrate.

Estimate based on file roles: ~24 of 82 are boundary-facing (migrate); ~58 internal (keep).
Final count is determined per-file during sub-batch 5.

### After PR 2: ValueError cap drains to ~58 sites (internal helpers only)

The ValueError cap dict in `test_architecture_no_value_error_in_impl.py` updates to reflect
the surviving internal-only sites. The shrink-only invariant means future additions of
boundary ValueErrors must migrate to typed AdCPError immediately.

---

## 7. Sub-batch Order (D19)

Each sub-batch is its own commit. Reviewers can ack one at a time.

### Sub-batch 1: Low-traffic tools surfaces (~12 sites, ~250 lines)

**Files**: `accounts.py`, `creative_formats.py`, `creatives/_processing.py`,
`creatives/_validation.py`, `media_buy_delivery.py`, `signals.py`, `performance.py`,
`products.py`, `task_management.py` (where applicable).

**Work**:
- Migrate Pattern A in these 6 files (12 sites: 2+1+1+5+3, plus the 4 task_management
  ValueErrors and 1 products ValueError).
- Verify per-item advisory sites (accounts.py:387/418, creative_formats.py:146,
  creatives/_processing.py:34, media_buy_delivery.py:189) with caller code; allowlist-permanent
  if confirmed per-item.
- Drop Pattern A cap entries for these files.

**Commit**: `refactor(errors): migrate low-traffic tools/ Pattern A + ValueError to typed AdCPError raises`

### Sub-batch 2: media_buy_create.py + catchall narrowing (~45 sites, ~600 lines)

**Files**: `src/core/tools/media_buy_create.py` only.

**Work**:
- Migrate 4 Pattern A sites.
- Migrate 18 boundary ValueErrors to typed AdCPError.
- Delete the line 1761 "re-raise AdCPError as ValueError" anti-pattern; pass through original
  exception.
- Narrow catchall at line 1924 from `except (ValueError, PermissionError)` to `except AdCPError`.
- 8 internal ValueErrors stay; structural guard cap drops by 18.

**Commit**: `refactor(errors): migrate media_buy_create.py Pattern A + boundary ValueError; narrow catchall`

### Sub-batch 3: media_buy_update.py + context-missing fix (~22 sites + 7 context, ~500 lines)

**Files**: `src/core/tools/media_buy_update.py` only.

**Work**:
- Migrate 21 Pattern A sites with `context=req.context` echo.
- Add `context=req.context` to the 7 context-missing sites identified in PR 1 D6.
- Migrate 1 boundary ValueError (line 121 → AdCPMediaBuyNotFoundError); keep 4 internal.
- Adopt `ContextManager.fail_step` at every return-shape site (replaces inline
  `update_workflow_step(status="failed", response_data=...)`).
- Coordinate with PR #1262 per D20.

**Commit**: `refactor(errors): migrate media_buy_update.py Pattern A + context echo + fail_step adoption`

### Sub-batch 4: Adapters Pattern A (~45 sites, ~700 lines)

**Files**: `broadstreet/adapter.py`, `google_ad_manager.py`, `kevel.py`, `triton_digital.py`.

**Work**:
- Migrate 13+22+5+5 = 45 Pattern A sites to typed AdCPError raises.
- Adapters now `raise AdCPAdapterError` (etc.) instead of returning `Error(code="API_ERROR")`.
- Caller `_impl` propagates; boundary translator builds envelope with `SERVICE_UNAVAILABLE`
  wire code (from `AdCPAdapterError.wire_error_code`).
- Update per-adapter integration tests if they assert old `Error(code=...)` return shape.

**Commit**: `refactor(errors): migrate adapter Pattern A return shape to typed raises`

### Sub-batch 5: Adapter ValueError audit + model_dump drain (~30 sites, ~400 lines)

**Files**: All `src/adapters/` files; `src/core/context_manager.py` callsites in
sub-batch 3; `test_architecture_no_model_dump_in_impl.py` allowlist.

**Work**:
- For each of ~82 adapter ValueError sites: apply D17 rule. Migrate boundary; keep internal.
- Drain the `model_dump()` allowlist by 21 entries (covered by `fail_step` adoption in
  sub-batch 3; this sub-batch updates the allowlist file).
- Final cap-dict updates so all 4 PR 1 structural guards drain to their final values:
  - Pattern A cap: 0 (or near-0 if advisory-on-success sites are allowlist-permanent)
  - ValueError cap: ~58 (internal helpers only)
  - Boundary-envelope guard: no changes (just confirms still green)
  - Code compliance guard: still green

**Commit**: `refactor(errors): adapter ValueError audit, model_dump drain, final cap updates`

---

## 8. Coordination Protocols

### With PR #1306 (PR 1)

- PR 1 must be merged to main before PR 2 branch cut. Confirm at cold-start step 2.

### With PR #1262 (cancel; currently draft)

Per D20 above. Detection:
```bash
gh pr view 1262 --json mergedAt --jq .mergedAt
```
- `null` → still draft/open; PR 2 proceeds.
- timestamp → merged; rebase PR 2 onto main; add its 4 Pattern A sites to sub-batch 3.

### With PR 3 (async/submitted, future)

PR 3 starts after PR 2 merges. The catchall narrowing in sub-batch 2 + the `fail_step`
adoption in sub-batch 3 set up PR 3's success-shape Response3 work cleanly. No overlap.

### With unrelated in-flight PRs

Pattern A is enforced shrink-only. Any new PR touching `_impl` that adds `Error(code=...)`
will hit the structural guard immediately. The PR 1 substrate is in main; any merge conflict
should be a simple "use typed raise" mechanical resolution for the conflicting PR.

---

## 9. .claude Rules & Workflows Compliance

### 7 Critical Patterns (CLAUDE.md)

| Pattern | Compliance | Notes |
|---|---|---|
| #1 AdCP Schema (extend library) | ✓ No schema changes |
| #2 Flask route conflicts | ✓ No new routes |
| #3 Repository pattern (ORM-first) | ✓ `fail_step` already uses `update_workflow_step` (Pattern #3 violation in `update_workflow_step` is pre-existing tech debt, allowlisted) |
| #4 Pydantic nested serialization | ✓ No new response types |
| #5 Transport boundary | ✓✓ **This is what we're enforcing** |
| #6 JavaScript script_root | ✓ No JS |
| #7 Schema validation environment-based | ✓ No validation mode changes |
| #8 Factory-based test fixtures | ✓ New integration tests use factories |

### Structural Guard Interactions

PR 2 drains 4 cap-dict guards from PR 1 plus the model_dump guard:

- `test_architecture_no_error_construction_in_impl` — Pattern A cap → drain to 0 (or ~4 if
  advisory-on-success sites are allowlist-permanent).
- `test_architecture_no_value_error_in_impl` — ValueError cap → drain to ~58 (internal-only).
- `test_architecture_error_envelope_two_layer` — no change (still verifies boundary calls
  envelope builder; PR 2 doesn't touch boundaries).
- `test_architecture_error_code_compliance` — still green (PR 2 doesn't add Error(code=...)).
- `test_architecture_no_model_dump_in_impl` — drain 21 of 24 allowlist entries.

### DRY Compliance

PR 2 is a mechanical replacement; each Pattern A site becomes a typed raise. No new
abstractions. `.duplication-baseline` should stay flat or shrink as Pattern A's repeated
inline construction (each `Error(code="X", message="Y")` literal) collapses to one-line
raises.

### Test Integrity Policy

ZERO TOLERANCE applies. Each sub-batch's test changes update assertions to match the new wire
codes; never skip. If a per-tool integration test breaks because it asserted the old return
shape (e.g., `result.errors[0].code == "X"`), update it to assert the typed-raise pattern
(e.g., `pytest.raises(AdCPXError)`) at the harness boundary.

### Quality Gates Before Each Sub-batch Commit

```bash
make quality                            # Per sub-batch
tox -e integration -- -k <touched-file> # Targeted integration
```

Before PR ready-for-review:
```bash
./run_all_tests.sh                      # Full suite
# Storyboard smoke (see §13)
```

### Conventional Commits

Each sub-batch commit uses `refactor:` prefix and a tight subject. Sub-batches 1-5 are listed
in §7 with their full commit subjects.

---

## 10. Risk Register

| Risk | Sub-batch | Mitigation |
|---|---|---|
| Advisory-on-success sites mis-classified as Pattern A | 1 | Verify caller context; allowlist-permanent with `# noqa: structural-guard` |
| 7 context-missing sites in media_buy_update.py have shifted line numbers | 3 | Cross-check against cap dict at branch cut; the file paths are stable, line numbers refresh |
| `ContextManager.fail_step` adoption breaks workflow_step persistence integration tests | 3 | The wire response and persisted response_data are byte-identical by construction; tests assert envelope shape from both sides |
| Per-adapter integration tests asserted old `Error(code=...)` return shape | 4 | Update assertions to `pytest.raises(AdCPAdapterError)` etc. |
| PR #1262 merges mid-flight, conflicts with sub-batch 3 | 3 | D20 protocol: hold sub-batch 3, rebase, restart |
| Adapter ValueError audit misclassifies a boundary site as internal | 5 | Boundary translator wraps escaped ValueError as synthetic AdCPValidationError (PR 1 safety net); incident → re-classify in follow-up |
| Catchall narrowing accidentally drops a real production error path | 2 | Run targeted integration tests on `media_buy_create`; any uncaught exception still hits boundary translator's catchall |
| Sub-batch 4 adapter sites trigger Slack notification volume jump | 4 | Document in PR description per PR 1's pattern; ops team forewarned |
| Storyboard smoke still red after PR 2 lands | 13 | Investigate per-scenario; storyboard runner expectations may have evolved |

---

## 11. Definition of Done — per sub-batch

### Sub-batch 1 (low-traffic tools)

- [ ] 12 Pattern A sites migrated OR allowlist-permanent with `# noqa` and comment.
- [ ] 5 boundary ValueErrors in `creatives/_validation.py` migrated to `AdCPValidationError`.
- [ ] 4 ValueErrors in `task_management.py` migrated.
- [ ] 1 ValueError in `products.py` migrated to `AdCPAdapterError`.
- [ ] Pattern A cap dict updated (12 entries removed or marked permanent).
- [ ] ValueError cap dict updated (10 entries removed).
- [ ] Per-file integration tests pass.
- [ ] `make quality` clean.
- [ ] Commit message: `refactor(errors): migrate low-traffic tools/ Pattern A + ValueError to typed AdCPError raises`.

### Sub-batch 2 (media_buy_create.py)

- [ ] 4 Pattern A sites migrated.
- [ ] 18 boundary ValueErrors migrated.
- [ ] Line 1761 ValueError-rewrap deleted; original exception passes through.
- [ ] Catchall at line 1924 narrowed to `except AdCPError`.
- [ ] 8 internal ValueErrors stay (commented as "internal contract").
- [ ] Pattern A cap dict entry for `media_buy_create.py` removed.
- [ ] ValueError cap dict drops by 18.
- [ ] Per-file integration tests pass; cross-tenant security tests still green.
- [ ] `make quality` clean.

### Sub-batch 3 (media_buy_update.py)

- [ ] 21 Pattern A sites migrated with `context=req.context`.
- [ ] 7 context-missing sites get context echo.
- [ ] 1 boundary ValueError migrated to `AdCPMediaBuyNotFoundError`.
- [ ] `ContextManager.fail_step` adopted at every status="failed" persistence site.
- [ ] Pattern A cap entry for `media_buy_update.py` removed.
- [ ] ValueError cap drops by 1.
- [ ] `model_dump()` allowlist drained by ~21 entries.
- [ ] PR #1262 coordinated per D20.
- [ ] Cross-tenant integration tests green.
- [ ] `make quality` clean.

### Sub-batch 4 (adapters Pattern A)

- [ ] 45 adapter Pattern A sites migrated to typed raises.
- [ ] Per-adapter integration tests assert `pytest.raises(AdCPXError)`.
- [ ] Pattern A cap entries for all 4 adapters removed.
- [ ] Slack notification volume change documented in PR description.
- [ ] `make quality` clean.

### Sub-batch 5 (adapter ValueError + drains)

- [ ] Adapter ValueError audit complete; ~24 boundary sites migrated, ~58 internal documented.
- [ ] `model_dump()` allowlist drained to ≤3 entries.
- [ ] ValueError cap dict ends at ~58 (internal helpers only).
- [ ] All 4 PR 1 structural-guard cap dicts at their final shrunk state.
- [ ] `./run_all_tests.sh` clean.
- [ ] Storyboard smoke test green (see §13).
- [ ] PR description summarizes total sites migrated, before/after envelope-on-wire traces.

---

## 12. Open Implementation-Time Questions

These are normal engineering judgment calls. Don't block PR 2 start.

1. **Per-item advisory sites** (accounts.py:387/418, creative_formats.py:146,
   creatives/_processing.py:34, media_buy_delivery.py:189): Migrate to raise, or
   allowlist-permanent as legitimate per-item results? Verify caller before sub-batch 1.

2. **GAM 1318 admin check**: `AdCPAuthorizationError` (auth ok, admin-only) vs
   `AdCPAuthenticationError` (admin-credential missing)? Both pin `AUTH_REQUIRED` so the wire
   output is identical; pick the semantically clearer Python type.

3. **`AdCPNotFoundError` vs `AdCPProductUnavailableError`**: `media_buy_create.py:1626` and
   `2620` say "Product not found"; `PRODUCT_UNAVAILABLE` is more spec-accurate (product may
   exist but be unavailable). Default to `AdCPProductUnavailableError`; revisit if tests
   expect `NOT_FOUND` wire code (unlikely).

4. **GAM workflow-creation INTERNAL_CODES** (lines 598, 768, 1339, 1368, 1471, 1558): All
   map to `AdCPAdapterError`. The current codes (`WORKFLOW_CREATION_FAILED`, etc.) are
   buyer-opaque; `SERVICE_UNAVAILABLE` wire code is correct. Keep `details={...}` for the
   specific internal code if debugging needs it.

5. **`task_management.py:233`** ("Task already in terminal state"): `AdCPConflictError` (409)
   vs `AdCPValidationError` (400)? Conflict is more precise; default to that.

6. **Re-raise pattern at `media_buy_create.py:1761`**: Confirmed it's catching `AdCPError`
   from a helper and re-wrapping as `ValueError(str(e))`. Definitively delete and pass through
   the original.

7. **Idempotency codes** (`IDEMPOTENCY_CONFLICT`, `IDEMPOTENCY_EXPIRED` in
   STANDARD_ERROR_CODES): not raised anywhere today; out of scope. Future PR may add
   `AdCPIdempotencyConflictError` / `AdCPIdempotencyExpiredError` when idempotency replay is
   implemented (see PR 1 §10 follow-ups).

8. **Storyboard smoke test return-path coverage**: PR 1 fixed raise-path; PR 2's sub-batch 3
   fixes return-path for `media_buy_update.py`. Confirm `invalid_transitions.yaml` green
   after sub-batch 3 lands; full green requires sub-batch 4 (adapter return-path) too.

---

## 13. Storyboard Smoke Test (final acceptance — re-run from PR 1)

Run before PR 2 ready-for-review. This is the test PR 1 couldn't fully green because the
return-path Pattern A sites were capped but not yet migrated.

```bash
docker compose up -d
# Wait for stack ready

npx -y @adcp/sdk@6.11.0 storyboard run mcp http://localhost:8000/mcp/ \
    --auth test-token \
    --scenario media_buy_seller/invalid_transitions \
    --json > /tmp/storyboard-pr2.json

# Verify: every error_code assertion produces the real code, not MCP_ERROR or wire-translated fallback
grep -c '"code":"MCP_ERROR"' /tmp/storyboard-pr2.json   # MUST be 0
grep -c '"code":"INVALID_REQUEST"' /tmp/storyboard-pr2.json  # MUST be 0 (no NOT_FOUND fallback)
grep -c '"code":"MEDIA_BUY_NOT_FOUND"' /tmp/storyboard-pr2.json   # MUST be ≥1
grep -c '"code":"PACKAGE_NOT_FOUND"' /tmp/storyboard-pr2.json   # MUST be ≥1
grep -c '"code":"NOT_CANCELLABLE"' /tmp/storyboard-pr2.json   # depends on PR #1262 merge; if landed, MUST be ≥1
```

Additional scenarios that PR 2 unblocks (not required for PR 2 acceptance; can verify):
- `media_buy_seller/refine_products` — already green from PR #1274; should stay green.
- `media_buy_seller/pending_creatives_to_start` — PR 3 work; will still fail.

---

## 14. Compact-Survival Checklist

If we compact context during PR 2:

- This file is the durable record. Read top-to-bottom on resume.
- `PLAN.md` (PR 1's plan) is still relevant for architecture context.
- File:line citations in §5/§6 may have shifted; cross-check against cap dicts at resume.
- Sub-batch ordering is fixed (§7); don't re-derive.
- Decision log D17-D22 is binding; don't second-guess.
- Open questions §12 are normal judgment calls; resolve inline during implementation.

If picking up cold: read this file, then `PLAN.md`. That's complete context.

---

## 15. Reasoning Summary (for explaining to teammates)

**Why this PR exists**: PR 1 (#1306) made the architecture but explicitly didn't touch the 82
Pattern A return-path sites. The structural guards froze them as caps. PR 2 drains the caps.
After PR 2, the codebase has zero Pattern A; PR 1's substrate carries the load uniformly across
every raise/return.

**Why 5 sub-batches**: The work is mechanical (replace literal-constructed Error with typed
raise) but spans 11 source files with ~82 + ~32 + ~24 = ~138 individual edits. Reviewers
asked for review-able units. The 5-batch split lets each batch be reviewed independently;
batches 1-3 are tools, 4-5 are adapters.

**Why this approach (typed subclasses, not generic AdCPExtensionError)**: PR 1 D1 chose
typed-class hierarchy as the user-facing vocabulary. PR 2 sticks with that; no generic
extension class. The enumeration confirmed no new subclasses are needed.

**Why ValueError migration only at boundaries**: A ValueError raised inside an internal
validator is a programmer-error invariant ("you passed me garbage; that's a bug"). Converting
all of them to typed AdCPError would mis-classify these as buyer-facing failures. The
boundary-only rule (D17) preserves the distinction between "your request is bad" and "my code
is buggy".

**Why now**: PR 1's structural ratchet is frozen; until PR 2 drains it, every new PR that
touches `_impl` must use typed raises (the guard blocks Pattern A). Drain the cap quickly so
the guard becomes an invariant ("Pattern A is forbidden") rather than a ratchet ("Pattern A
must shrink").
