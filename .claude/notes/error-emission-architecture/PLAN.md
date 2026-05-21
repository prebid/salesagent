# Error Emission Architecture — Implementation Plan

**Status**: PR 1 substrate landed and review-incorporated (PR #1306). Awaiting CI re-run + merge.
**Tracking**: GitHub architecture issue (body drafted, not yet posted) + companion async/submitted issue (body drafted, not yet posted).
**Scope**: 3 sequential PRs over 5-8 weeks. Completes closed-#1078's deferred response-shaping scope.

---

## POST-REVIEW ADDENDA (PR #1306 review round 1)

The review surfaced 2 blockers + 7 important items that landed in the PR. Decisions worth pinning so PR 2/3 authors don't re-derive them:

### D9 — Additive guard, not inversion (overrules PLAN section 5 step 10)

**Decision**: Keep `test_architecture_error_code_compliance.py` unchanged AND add `test_architecture_no_error_construction_in_impl.py` as a separate guard.

**Rationale**: The two guards enforce complementary invariants:
- `test_architecture_error_code_compliance` — every `Error(code=...)` literal uses a STANDARD or INTERNAL code. Still useful for PR 3's success-envelope `errors[]` composition (advisory non-fatal errors are a legitimate use of `Error(code=...)`).
- `test_architecture_no_error_construction_in_impl` — Pattern A: `_impl` must not construct `Error(code=...)` at all. Stricter superset for `_impl` scope only.

PLAN section 5 said "invert" the older guard; in practice the additive form is cleaner. PR 2 reviewers should not second-guess.

### D10 — INTERNAL_CODES translation as defense-in-depth (B1)

**Decision**: Add `NOT_FOUND → INVALID_REQUEST`, `INTERNAL_ERROR → SERVICE_UNAVAILABLE`, `CONFIGURATION_ERROR → SERVICE_UNAVAILABLE` to `ERROR_CODE_MAPPING`. Overlap with `INTERNAL_CODES` is intentional and re-tested.

**Rationale**: 9 production raise sites today use these base-class codes. The substrate's stated job is wire-safe codes; without translation the new envelope serializes the non-standard codes verbatim. PR 2 cleanup will migrate the 6 `AdCPNotFoundError` sites to specific subclasses (`MEDIA_BUY_NOT_FOUND`, `PACKAGE_NOT_FOUND`, etc.); the 3 `AdCPConfigurationError` sites in `models.py` (encrypted-secret decryption failure) probably stay — those are genuine admin-required configuration faults that `SERVICE_UNAVAILABLE` represents accurately to the buyer.

**Test**: `test_internal_codes_translated_to_wire_safe_codes` in `test_adcp_exceptions.py` hard-asserts each INTERNAL→STANDARD translation. `test_internal_codes_overlap_with_mapping_have_wire_safe_targets` in `test_error_code_mapping.py` enforces that any future INTERNAL/MAPPING overlap must translate to a STANDARD target.

### D11 — A2A fallthrough envelope coverage (B5)

**Decision**: A2A `ValueError` / `PermissionError` / `Exception` paths now wrap in synthetic `AdCPValidationError` / `AdCPAuthorizationError` / `AdCPError` and call `_adcp_to_a2a_error()` so the envelope is uniform across all error paths.

**Rationale**: Mirrors MCP's `_translate_to_tool_error` behavior. Storyboard runner can read `errors[0].code` and `adcp_error.code` from every A2A error path. Closes a hole the substrate would have otherwise left open.

### D12 — Recovery deferral on AdCPAuthenticationError + AdCPAuthorizationError (B2/F5)

**Decision**: Keep `recovery="terminal"` on both. Spec 3.0.4 reclassified `AUTH_REQUIRED` to `correctable`; we follow the installed SDK (4.3) which still says `terminal`.

**Rationale**: Buyer-facing wire output must match what the SDK declares. Re-classification happens when the project upgrades to a SDK version that ships the new vocabulary. Documented in both class docstrings.

### D13 — `media_buy_list.py:256` cleaned up in PR 1 (F1)

`raise ToolError(...)` at this site was a bypass of the new envelope path. Migrated to `raise AdCPValidationError(...)` in this PR so the boundary translator runs.

### D14 — FIXME comments at allowlisted sites deferred (F2)

**Decision**: Defer until the user posts the architecture issue and provides the issue number.

**Rationale**: Per user convention (`feedback_no_notes_for_github_issues`), the user posts issues manually. FIXME format `FIXME(error-emission-architecture-#N)` needs the real N. PR 2 cleanup migrates these sites anyway; FIXMEs at allowlisted-but-not-yet-migrated sites are nice-to-have, not load-bearing.

### D15 — Storyboard smoke test deferred to post-PR-2 (review Q1)

**Decision**: Section 11's storyboard smoke test cannot fully pass until PR 2 lands the return-path Pattern A migration. The raise-path is fixed in PR 1; the return-path (early `return UpdateMediaBuyError(...)` inside `_impl`) is not.

**Rationale**: PR 1's structural ratchet documents the 82 return-path sites as capped, not eliminated. Mark the storyboard smoke test as "deferred verification" in the PR description and re-run after PR 2 lands.

### D16 — `_handle_tool_error` kept defensively (review Q3)

**Decision**: Keep the REST `_handle_tool_error` helper despite it being effectively dead today.

**Rationale**: Routes catch `ToolError` defensively against future refactors that might re-introduce MCP/REST cross-pollination. The helper now correctly produces the envelope (B3 latent bug fixed). Removal is PR 2 follow-up if confirmed unused after the cleanup sweep.

### Open in PR 2:

- 7 broken context-missing Pattern A sites in `media_buy_update.py` (D6 — context echo on those raises).
- Pattern A migration sweep across 82 sites; allowlist drains to zero.
- ValueError migration sweep across 126 sites.
- FIXME comments at remaining allowlisted sites referencing posted architecture issue.
- Storyboard smoke test re-run + green check.

---

## NEXT SESSION — Resume After Compact

Cold-start steps (in order; do NOT skip):

1. **Read this file top to bottom** before any Read/Edit/Write of code. It is the durable record of every decision.

2. **Cut the feature branch FIRST**, before any code changes:
   ```bash
   git -C /Users/quantum/Documents/ComputedChaos/salesagent checkout main
   git -C /Users/quantum/Documents/ComputedChaos/salesagent pull --rebase   # optional, only if branch is stale
   git -C /Users/quantum/Documents/ComputedChaos/salesagent checkout -b feature/error-emission-architecture-pr1
   ```
   Per user convention (see user memory `feedback_feature_branch_first`), the branch cut is the first action of implementation. Never start on main.

3. **Re-verify file:line citations** against HEAD. Line numbers in section 3 (Interaction Surfaces) were captured during planning and may have shifted slightly. The structural decisions remain valid; only line offsets need refresh.

4. **Verify the GitHub issue is posted** before adding FIXME comments at allowlisted sites. FIXME format: `# FIXME(error-emission-architecture-#N): migrate to raise AdCPError subclass` where #N is the architecture issue number.

5. **Follow the TDD sequence in section 5** for PR 1 commits.

6. **Run quality gates per section 8** before opening the PR.

7. **User pushes git themselves** (see user memory `feedback_user_owns_git_push`). Do not run `git push`, `gh pr create`, or remote-mutating operations without explicit instruction.

---

---

## 1. What this is, in one paragraph

Salesagent's `_impl` functions construct typed Pydantic error response variants (`CreateMediaBuyError(errors=[Error(code=...)])`) inline and early-return, shifting wire-shape decisions into business logic and violating Pattern #5 (transport boundary). This produces the storyboard symptom where `MEDIA_BUY_NOT_FOUND` surfaces on the wire as `"MCP_ERROR"`, plus three independent symptoms (#1041 partial-success, #1224 cross-tenant enumeration, #1286 lowercase code) — all sharing one root cause. The fix: a salesagent-owned two-layer envelope serializer + typed `AdCPError` subclass expansion + boundary translator refactor + structural guards. Reuses the stable `adcp_error()` SDK helper (already in adcp 4.3, used at 3 salesagent sites) for the payload half; constructs the envelope half ourselves; rejects coupling to newer SDK helpers (`build_mcp_error_result()` from 5.5) to avoid private-API churn.

---

## 2. Decision Log — every decision with rationale

### D1 — Option A vs C (SDK helper vs own serializer)
**Decision**: Option C. Build our own envelope serializer in salesagent code.
**Rationale**: `build_mcp_error_result()` (adcp 5.5.0) is private-adjacent API (uses `_serialize_details_for_mcp`, `_extract_structured_fields`). Salesagent owns its wire contract. SDK churn (3.12→4.3→5.5 in months) should not force architectural rework. `adcp_error()` (the payload-only helper) is stable across versions and acceptable to depend on.
**Trade-off**: ~30 extra lines of code we own (envelope construction). In exchange: full control, debuggability, salesagent-specific extensions, no SDK upgrade prerequisite.

### D2 — PR count: 3 PRs, sequential
**Decision**: Substrate → Cleanup sweep → Async/submitted. Each depends on the prior.
**Rationale**: Investigation expanded scope from initial 5-6 PR plan. Natural compression: Stages 2-4 cleanup share the same mechanical transformation (Pattern A → typed raise), can ship in one ~1800-2500 line sweep PR after the substrate lands. Async/submitted is feature work (different review surface) and stays separate.
**Trade-off**: PR 2 is large but mechanical. Easier to review uniform diff than mixed PRs. Single revert point per stage.

### D3 — Spec compliance target
**Decision**: AdCP 3.0.6 (currently pinned). Storyboard runner: `@adcp/sdk@6.11.0` (currently pinned in PR #1274's CI). Do NOT bundle adcp 4.3→5.5 SDK upgrade with this work.
**Rationale**: User explicitly chose stable 4.3 features. Two-layer model is normative since spec 3.0.6 (CHANGELOG entry `91b6e2c`). Documentation arrived in 3.0.7 (`error-handling.mdx`) but the rule predates the doc. SDK 6.11.0 runner is identical to 7.3.0 for our scenarios.
**Trade-off**: We track spec changes ourselves (integration tests + smoke runs). No automated drift detection — rejected as scope creep.

### D4 — `fail_step` location AND signature
**Decision**: `ContextManager.fail_step(step_id: str, *, exc: AdCPError, error_message: str | None = None) -> None`.

**Location rationale**: `ctx_manager.update_workflow_step` triggers `_send_push_notifications` (verified at `context_manager.py:332`, with private method at line 621). If `fail_step` lived on `WorkflowRepository` and called `update_status` directly (verified at `workflow.py:266-296`), webhooks would silently stop firing. ContextManager owns push-notification side effects; repository stays pure data access.

**Signature rationale**: Accepts the exception (`exc: AdCPError`) not a response model. Internally calls `build_two_layer_error_envelope(exc)` to produce `response_data` — the SAME builder the boundary translator uses for the wire response. Single source of truth: the persisted `workflow_step.response_data` (read by `get_task` and webhook delivery) and the immediate wire response are byte-identical by construction. `_impl` never builds wire shape itself.

**Internal flow**:
```
ctx_manager.fail_step(step.step_id, exc=exc)
  → build_two_layer_error_envelope(exc)  # ours; wraps adcp_error() helper
  → self.update_workflow_step(step_id, status="failed", response_data=envelope, error_message=...)
  → existing _send_push_notifications fires on status="failed"
```

**Trade-off**: Slight asymmetry — most data access is via repositories, but webhook integration crosses the layer. Pattern #3 violation in `update_workflow_step` (raw `select(WorkflowStep)`) is pre-existing tech debt, allowlisted, NOT introduced or worsened by `fail_step`.

**Clarification on naming**: `AdCPError` is salesagent's exception hierarchy (`src/core/exceptions.py:105-310`, established in PR #1066). It is NOT from the adcp SDK. The SDK provides `Error` (Pydantic model), `adcp_error()` (helper), `ErrorCode` enum, and `ContextObject`. Salesagent owns the exception hierarchy + the envelope builder; the SDK provides the payload-shape helper we wrap.

### D5 — AdCPError.context: optional with default None
**Decision**: `__init__(message, *, context: ContextObject | None = None, ...)`. No enforcement guard yet.
**Rationale**: Backward compatible. Some `_impl` paths (helpers, validators) don't have access to `req.context`. Adding a soft lint later is data-driven after PR 2 cleanup reveals actual usage patterns.
**Trade-off**: Some raise sites will not pass context, creating partial spec-compliance for those error paths. Acceptable: PR 2 cleanup migrates the 24 known sites; remaining sites are helpers without context access.

### D6 — 7 broken context-missing Pattern A sites: defer to PR 2
**Decision**: Lines 458, 527, 598, 1081, 1099, 1242, 1274 in `media_buy_update.py` get `context=req.context` added in PR 2 (along with the rest of the Pattern A migration), not as drive-by in PR 1.
**Rationale**: They're already in the Pattern A allowlist. PR 2 rewrites them to typed raises with context. Touching them in PR 1 risks merge conflict with PR 2.
**Trade-off**: They remain silently broken for one PR cycle. Pre-existing regression, not a recent one.

### D7 — Async/submitted gap: separate companion issue (PR 3)
**Decision**: Out of scope for PR 1 + 2. Companion issue body drafted, becomes PR 3.
**Rationale**: Async/submitted is *success envelope* shape (Response3 with `task_id`), not error envelope shape. Different feature surface. Closes #1247 gap #12 (`pending_creatives` → `pending_start`) and the `media_buy_delivery` terminal-error mis-shape.

### D8 — adcp 4.3→5.5 SDK upgrade: NOT a prerequisite
**Decision**: Independent decision on its own merits. Not bundled.
**Rationale**: We reject `build_mcp_error_result()` adoption (per D1), so 5.5 doesn't unlock anything we need. If someone wants to upgrade for unrelated reasons (e.g., adcp library bug fixes), that's a separate workstream.

---

## 3. Interaction Surfaces — the blast radius map

### Files that change (per PR)

#### PR 1 — Architecture substrate (~1000 lines)

| File | Change type | Notes |
|---|---|---|
| `src/core/exceptions.py` | Modify | Add 7 typed subclasses; add `context` param to `AdCPError.__init__`; update `to_dict()`, `to_adcp_error()` to include context; fix `ERROR_CODE_MAPPING` gaps (NOT_FOUND, INTERNAL_ERROR, CONFIGURATION_ERROR); sync `STANDARD_ERROR_CODES` with 45-code canonical enum; audit `CREATIVES_NOT_FOUND` mapping |
| `src/core/error_envelope.py` (or in exceptions.py) | New file (optional) | `build_two_layer_error_envelope(exc: AdCPError) -> dict` — ~40-60 lines |
| `src/core/tool_error_logging.py` | Modify | `_translate_to_tool_error` calls envelope serializer; produces `CallToolResult(isError=True, structuredContent={...})` directly; bypasses FastMCP `_make_error_result(str(e))` |
| `src/a2a_server/adcp_a2a_server.py` | Modify | `_adcp_to_a2a_error` builds two-layer envelope into artifact body; line 1397-1400 adds `_log_a2a_operation(success=False)` before re-raise |
| `src/app.py` | Modify | `adcp_error_handler` (lines 96-109) produces two-layer body instead of flat `error_code` |
| `src/routes/api_v1.py` | Modify | Add `except AdCPError` route-level block (lines 50-60 currently only catch ToolError) |
| `src/core/context_manager.py` | Modify | Add `fail_step(step_id, *, response_model, error_message)` method mirroring `update_workflow_step` |
| `tests/unit/test_architecture_no_error_construction_in_impl.py` | New | AST guard, allowlist ~84 Pattern A sites |
| `tests/unit/test_architecture_error_envelope_two_layer.py` | New | Boundary translator guard |
| `tests/unit/test_architecture_no_value_error_in_impl.py` | New | ValueError guard, allowlist ~26 sites |
| `tests/unit/test_architecture_error_code_compliance.py` | Modify | Invert: fail on construction in `_impl` |
| `tests/unit/test_adcp_exceptions.py` | Modify | ~16 sites update flat `body["error_code"]` to two-layer envelope |
| `tests/unit/test_error_boundary_translation.py` | Modify | ~23 sites update ToolError `args[0/1/2]` to result-object inspection |
| `tests/harness/_base.py` | Modify | `_unwrap_mcp_tool_error` and `_unwrap_a2a_server_error` parse new envelope shape |
| `tests/integration/test_a2a_error_responses.py` | Modify | ~10 sites extended to assert envelope alongside payload |
| `tests/integration/test_error_envelope_two_layer_per_transport.py` | New | Per-transport, per-subclass envelope verification |

#### PR 2 — Pattern A cleanup sweep (~1800-2500 lines, mechanical)

| File | Pattern A sites | ValueError sites | Workaround sites |
|---|---|---|---|
| `src/core/tools/media_buy_update.py` | 21 (lines 260, 376, 433, 542, 576, 626, 646, 672, 851, 882, 910, 930, 953, 976, 984, 1082, 1100, 1149, 1167, 1243, 1275) + 7 context-missing | 5 | 0 |
| `src/core/tools/media_buy_create.py` | 4 + catchall narrowed | 26 | 18-19 `details["error_code"]` |
| `src/core/tools/media_buy_delivery.py` | 5 | 0 | 0 |
| `src/core/tools/signals.py` | 2 + 1 multi-line | 0 | 0 |
| `src/adapters/google_ad_manager.py` | 8 + 14 multi-line | 0 | 0 |
| `src/adapters/broadstreet/adapter.py` | 2 + 11 multi-line | 0 | 0 |
| `src/adapters/kevel.py` | 4 + 1 multi-line | 0 | 0 |
| `src/adapters/triton_digital.py` | 4 + 1 multi-line | 0 | 0 |
| `src/adapters/mock_ad_server.py` | (verify) | 0 | 0 |
| `src/adapters/xandr.py` | (verify) | 0 | 0 |
| `src/core/tools/accounts.py` | +2 multi-line | 0 | 0 |
| `src/core/tools/creative_formats.py` | +1 multi-line | 0 | 0 |
| `src/core/tools/creatives/_processing.py` | +1 multi-line | 0 | 0 |
| `src/core/creative_agent_registry.py` | +1 multi-line | 0 | 0 |

PR 2 also drains:
- `test_architecture_no_model_dump_in_impl.py` allowlist (21 of 24 entries drain via `ContextManager.fail_step` adoption)
- All 4 new structural guard allowlists from PR 1

#### PR 3 — Async/submitted + lifecycle (~600-1000 lines, feature)

| File | Change |
|---|---|
| `src/core/tools/media_buy_create.py:2418-2428, 2577-2586` | Switch to Response3 shape with `task_id` |
| `src/core/tools/media_buy_update.py:321-351` | Manual approval branch returns Response3 |
| `src/core/tools/media_buy_delivery.py:104, 125, 159` | Terminal errors → error variant (success envelope today) |
| `src/core/database/models.py` (workflow_steps) | Possibly extend for `task_id` field — open question on reuse vs new |
| `src/core/tools/task_management.py` | `get_task` mapping to whichever ID strategy is chosen |
| `src/adapters/google_ad_manager.py:893` | Submitted status audit |
| Storyboard-specific test for `pending_creatives_to_start.yaml` | New |

### Cross-PR dependencies

- **PR 1 → PR 2**: PR 2 needs PR 1's typed subclasses + boundary translators + `ContextManager.fail_step`. Cannot start PR 2 until PR 1 lands.
- **PR 2 → PR 3**: PR 3 ideally builds on PR 2's clean baseline (no Pattern A in touched files). Can start in parallel if needed but coordination required.
- **PRs and in-flight work**: PR #1274 can merge anytime (zero conflict). PR #1276 can merge before/after (mild Pattern A conflict, handled via allowlist update). PR #1262 (in draft) handled by user separately.

### Test infrastructure interaction

- Harness unwrappers (`tests/harness/_base.py:100-190`) already absorb wire-shape variations. Update them in PR 1; most BDD and integration tests continue passing.
- 80% of test breakage concentrated in 2 files: `test_error_boundary_translation.py` + `test_adcp_exceptions.py`. Both updated in PR 1.
- 10 A2A artifact tests need extension (add envelope assertion alongside existing payload assertion) — not rewrites.

### Audit/Slack behavior change

After PR 1: `raise AdCPError(...)` → `with_error_logging` decorator → calls `audit_logger.log_operation(success=False)` → audit_logger's rules trigger Slack for sensitive ops (`update_media_buy` is on the list).

**Today**: Pattern A `return UpdateMediaBuyError(...)` silently skips audit + Slack.
**After PR 1**: Slack notifications fire on previously-silent failures. **Behavior change. Document in PR description.**

A2A boundary has a pre-existing gap: `adcp_a2a_server.py:1397-1400` re-raises `AdCPError` without calling `_log_a2a_operation(success=False)`. Fixed in PR 1 (~3 lines).

---

## 4. Spec & Storyboard Compliance Mapping

### Spec text → invariant → implementation

| Spec source | Verbatim text | Invariant | Implementation |
|---|---|---|---|
| `error-code.json` GOVERNANCE_DENIED (3.0.6) | "populate `errors[].code` AND `adcp_error.code` per the two-layer model... HTTP 4xx, MCP `isError: true`, A2A `failed`" | Both layers populated, transport marker flipped | PR 1 envelope serializer + boundary translators |
| `error-handling.mdx` (3.0.7+) | "A fatal task failure SHOULD populate both layers" | Same | Same |
| `error-handling.mdx` (3.0.7+) | "Non-fatal errors populate only the payload... MUST NOT populate `adcp_error`" | Advisory `errors[]` on success envelope, never on error envelope | Structural guard discriminator carves out success envelopes |
| `error.json` | `code: string, min 1 max 64` | Open string accepted; UPPER_SNAKE convention | Vocabulary guard already enforces |
| `core/error.json` | `recovery: enum {transient, correctable, terminal}` | Recovery populated per canonical mapping | Auto-populated by `adcp_error()` helper |
| Storyboard `invalid_transitions.yaml` | `check: error_code, value: "MEDIA_BUY_NOT_FOUND"` | Real code visible on wire | PR 1 fixes raise path; PR 2 fixes return path |
| Storyboard `invalid_transitions.yaml` | `field_value context.correlation_id == "..."` | Context echoed on errors | PR 1 adds context to AdCPError; boundary translators thread it |
| Spec 3.0.4 (`78b1dc4`) | AUTH_REQUIRED reclassified terminal→correctable | Verify `AdCPAuthenticationError` recovery default | PR 1 audit |

### Storyboard scenarios unblocked

| Scenario | When unblocked | What it verifies |
|---|---|---|
| `media_buy_seller/invalid_transitions` (3 error codes) | PR 1 (raise path) + PR 2 (return path) | MEDIA_BUY_NOT_FOUND, PACKAGE_NOT_FOUND, NOT_CANCELLABLE all surface correctly with context echo |
| `media_buy_seller/refine_products` | Already passing (PR #1274) | Validates buying_mode contract |
| `media_buy_seller/inventory_list_targeting` | Out of scope (#1302 follow-up) | Adapter passthrough work |
| `media_buy_seller/inventory_list_no_match` | Out of scope (#1303 follow-up) | Zero-inventory pre-validation |
| `media_buy_seller/pending_creatives_to_start` | PR 3 (companion issue) | Lifecycle transition |
| `media_buy_seller/creative_fate_after_cancellation` | PR #1262 (in flight) | Cancel work |

### Storyboard runner version

Pin: `@adcp/sdk@6.11.0` (PR #1274 CI). Latest is 7.3.0; runner algorithm is byte-identical for our scenarios. No bump required for this work.

---

## 5. .claude Rules & Workflows Compliance

### 7 Critical Patterns (CLAUDE.md)

| Pattern | Compliance | Notes |
|---|---|---|
| #1 AdCP Schema (extend library) | ✓ No schema changes in PR 1 |
| #2 Flask route conflicts | ✓ No new routes |
| #3 Repository pattern (ORM-first) | ✓ `ContextManager.fail_step` delegates to existing repository methods |
| #4 Pydantic nested serialization | ✓ No new response types |
| #5 Transport boundary | ✓✓ **This is what we're enforcing** |
| #6 JavaScript script_root | ✓ No JS |
| #7 Schema validation environment-based | ✓ No validation mode changes |
| #8 Factory-based test fixtures | ✓ New integration tests use factories per CLAUDE.md |

### Structural Guard Interactions

23 existing guards + 4 new = 27 after PR 1.

- `test_no_toolerror_in_impl` — Reinforced (eliminate last `raise ToolError` at `media_buy_list.py:256`)
- `test_transport_agnostic_impl` — Reinforced
- `test_architecture_no_model_dump_in_impl` — Drains 21 entries via `ContextManager.fail_step` (PR 2)
- `test_architecture_error_code_compliance` — **Inverted** (PR 1) to fail on construction in `_impl`
- `test_architecture_repository_pattern` — Verify allowlist doesn't grow

### DRY Compliance (non-negotiable per CLAUDE.md)

PR 1 is a **DRY consolidation by design**: 3 transport boundary translators currently duplicate error-construction logic; PR 1 centralizes into one shared serializer. Net `.duplication-baseline` should drop or stay flat.

Risk: 4 new structural guards may share AST-walking helper code. Extract a shared `_iter_impl_functions(tree) -> Iterator[FunctionDef]` helper to avoid duplication.

### Test Integrity Policy (ZERO TOLERANCE)

If any existing test breaks due to wire-shape changes (MCP test pinning `MCP_ERROR`, REST test pinning flat `error_code`), **update the test** to assert the new spec-compliant shape. Do NOT use `--ignore`, `-k "not ..."`, `--deselect`, `pytest.mark.skip`, or `pytest.mark.xfail`. Migration cost estimated at 4-8 hours mechanical work.

### Quality Gates Before Commit

```bash
make quality                  # Format, lint, typecheck, unit tests
./run_all_tests.sh            # Full suite via tox -p (Docker stack)
```

### TDD Approach (Red-Green-Refactor per .claude/rules/workflows/tdd-workflow.md)

PR 1 commit sequence:
1. Red — write the two-layer envelope integration test (fails — function doesn't exist)
2. Red — write the 4 structural guards (fail — Pattern A sites present)
3. Green — add envelope serializer; integration test passes
4. Green — seed guard allowlists; guards pass
5. Green — add 7 new typed subclasses + tests
6. Green — wire 3 boundary translators
7. Green — add `ContextManager.fail_step` + tests
8. Green — fix ERROR_CODE_MAPPING gaps + STANDARD_ERROR_CODES sync
9. Refactor — extract shared AST helper for guards (DRY)
10. Refactor — invert `test_architecture_error_code_compliance.py`
11. Quality gates pass; storyboard smoke test passes

### Conventional Commits

PR titles: `refactor:` prefix. Specifically:
- PR 1: `refactor: complete error-emission architecture — substrate + structural guards`
- PR 2: `refactor: migrate all _impl Pattern A sites to typed AdCPError raises`
- PR 3: `feat: spec-conformant async/submitted response envelopes` (or `feat:` since it's user-visible new behavior)

---

## 6. Coordination with In-Flight PRs

| PR | Status | Coordination |
|---|---|---|
| **#1274** buying_mode/refine | All CI green, ready | Merge anytime. Zero conflict with this work. |
| **#1276** property/collection lists | 3 blocking review fixes outstanding | Merge before or after PR 1. Mild +1 Pattern A site — added to allowlist if it lands first. |
| **#1262** cancel | Moved to draft by user | User handling cleanup of scope creep separately. After it merges, its 4 Pattern A sites (NOT_CANCELLABLE, INVALID_STATE) added to PR 2's cleanup scope. |

**Critical**: if any of #1274/#1276/#1262 merge between PR 1 and PR 2, update the Pattern A allowlist to include their new sites. The only-shrink rule applies to PR 2 cleanup, not to in-flight PR merges.

---

## 7. Risk Register

| Risk | PR | Mitigation |
|---|---|---|
| `_translate_to_tool_error` change breaks pinned MCP tests | 1 | Update harness unwrappers first; identified 23 sites in `test_error_boundary_translation.py` to update |
| REST flat `error_code` has external consumers | 1 | Search admin UI + dashboards. Open Question 4 — feature-flag during transition, or atomic migrate? Decide before opening PR |
| `ContextManager.fail_step` webhook integration drift | 1 | Mirror existing `update_workflow_step` API surface; verify `_send_push_notifications` fires |
| Slack notification volume jumps on PR 1 merge | 1 | Document in PR description. Operations team forewarned. |
| Structural guards too strict, CI breaks | 1 | Allowlists pinned at current state; only-shrink rule |
| PR 2 too large to review (~2500 lines) | 2 | Uniform mechanical diff. Reviewer treats it as "spot-check N sites" not "review every line." |
| In-flight PRs merge between PR 1 and PR 2 | 2 | Allowlist update protocol documented above |
| `Error.issues[]` field adoption for `details["error_code"]` workarounds | 2 | Open Question 5 — typed subclasses vs generic `AdCPExtensionError(code)` |
| Async/submitted `task_id` mapping (reuse step_id vs new table) | 3 | Open Question — implementer decision based on data shape |

---

## 8. Definition of Done (per PR)

### PR 1 — Architecture substrate

- [ ] All Stage 1 deliverables landed
- [ ] 4 new structural guards green with allowlists pinned (84 Pattern A + 26 ValueError + boundary sites)
- [ ] `test_architecture_error_code_compliance.py` inverted
- [ ] All existing 23 structural guards still green
- [ ] `make quality` clean
- [ ] `./run_all_tests.sh` — all 5 suites pass
- [ ] Coverage doesn't decrease
- [ ] `.duplication-baseline` unchanged or shrunk
- [ ] `.type-ignore-baseline` unchanged (still 60)
- [ ] Storyboard smoke test: `invalid_transitions.yaml` produces real codes (MEDIA_BUY_NOT_FOUND, PACKAGE_NOT_FOUND, NOT_CANCELLABLE), no `MCP_ERROR` synthesis
- [ ] PR title: `refactor:` prefix
- [ ] PR description includes: link to architecture issue, allowlist counts, migration safety section, Slack notification behavior change call-out, coordination note for in-flight PRs
- [ ] FIXME comments at every allowlisted site referencing architecture issue #

### PR 2 — Pattern A cleanup sweep

- [ ] All ~84 Pattern A sites converted to typed raises
- [ ] All 26 `raise ValueError` replaced with typed subclasses
- [ ] All 18-19 `details["error_code"]` workarounds resolved (lifted to subclasses or generic `AdCPExtensionError`)
- [ ] All 4 structural guard allowlists drained to zero
- [ ] 21 of 24 `model_dump()` allowlist entries drained (via `ContextManager.fail_step` adoption)
- [ ] Catchall at `media_buy_create.py:1924-1936` narrowed to backstop
- [ ] 7 context-missing sites in `media_buy_update.py` fixed
- [ ] All existing tests pass (no wire shape changes since PR 1 already produces spec shape)
- [ ] Coverage maintained
- [ ] PR description includes: site count summary, before/after wire trace examples

### PR 3 — Async/submitted

- See companion issue body for detailed contract.

---

## 9. Open Implementation-Time Questions (resolve during implementation)

These are normal engineering judgment calls. Don't block implementation start.

1. Typed factory naming: `AdCPError.to_response(cls)` vs `cls.from_adcp_error(exc)` vs both?
2. `_impl` return signature for tuple-returning functions: tuple stays with error variant, or tuple becomes plain Success and wrapper builds union?
3. Catchall at `media_buy_create.py:1924-1936`: keep as `AdCPError` wrapper, or remove entirely with a guard requiring every code path to either return success or raise typed?
4. REST `app.py:97-109` backward compat: atomic migrate or feature-flag during transition?
5. `details["error_code"]` workarounds: ~7 specific subclasses or generic `AdCPExtensionError(code: str)`?
6. Async/submitted advisory guard discriminator: confirm enclosing-envelope-class-name approach covers all cases.
7. PR #1262's cancel pattern: migrate to raise + context, or keep return-with-context for the §292 warnings case?
8. AdCPError.context enforcement: when to add a soft lint guard requiring `context=req.context`?

---

## 10. Out of Scope — separate follow-up issues to file

1. **Adapter targeting compilation for `property_list`/`collection_list`** — tracked: #1302 draft
2. **Pre-flight zero-inventory check** — tracked: #1303 draft
3. **`Product.publisher_properties`/`Product.collections` persistence** — tracked: #1295
4. **Async/submitted shape + lifecycle** — covered by PR 3 / companion issue
5. **Idempotency replay markers** (`replayed: true`, `IDEMPOTENCY_CONFLICT`, `IDEMPOTENCY_EXPIRED`, TTL) — no issue drafted yet
6. **REST `UpdateMediaBuyBody.context` field** — sibling to PR 1 context work, small follow-up
7. **Storyboard CI matrix extension** beyond `refine_products` — CI quality concern, coordinate with #1228
8. **adcp Python SDK major-version upgrade** (4.x → 5.x) — independent decision

---

## 11. Storyboard Smoke Test (run before PR 1 ready-for-review)

```bash
docker compose up -d
# Wait for stack ready

npx -y @adcp/sdk@6.11.0 storyboard run mcp http://localhost:8000/mcp/ \
    --auth test-token \
    --scenario media_buy_seller/invalid_transitions \
    --json > /tmp/storyboard-result.json

# Verify: every error_code assertion produces the real code, not MCP_ERROR
grep -c '"code":"MCP_ERROR"' /tmp/storyboard-result.json   # MUST be 0
grep -c '"code":"MEDIA_BUY_NOT_FOUND"' /tmp/storyboard-result.json   # MUST be ≥1
grep -c '"code":"PACKAGE_NOT_FOUND"' /tmp/storyboard-result.json   # MUST be ≥1
grep -c '"code":"NOT_CANCELLABLE"' /tmp/storyboard-result.json   # MUST be ≥1
```

---

## 12. Reasoning Summary (for explaining to team members)

**Why this matters**: Today, salesagent's error path produces wire output that storyboard runners erase into `"MCP_ERROR"` on the exception path, and Pattern A's accidentally-passing-the-runner shape violates spec by missing the envelope-level `adcp_error.code` and transport-level failure marker. Three observable bugs (#1041, #1224, #1286) trace to one architectural cause: `_impl` functions own wire-shape decisions they shouldn't.

**Why this approach (Option C, salesagent-owned)**: We rejected coupling to `build_mcp_error_result()` from adcp 5.5.0 because it's private-adjacent API in a fast-moving SDK. Salesagent owns its wire contract; we use the stable `adcp_error()` payload helper (~3 years old, byte-identical between 4.3 and 5.5) and write ~40-60 lines of envelope construction we control.

**Why 3 PRs**: Substrate establishes architecture; cleanup applies it; async/submitted is feature work (different review concern). Each is independently revertable. Lower review burden than one mega-PR; less context-switching than 5-6 PRs.

**Why structural guards in PR 1, cleanup in PR 2**: Salesagent precedent (PR #1212 closing #1078, PR #1066 establishing AdCPError). Guards land with allowlists pinned at current state; PR 2 drains them. The combination prevents regression while making the migration mechanical.

**Why now**: Currently, every new PR risks adding Pattern A (PR #1262 has 4 new sites; PR #1276 has 1). Without architecture, the gap grows. Without guards, future SDK upgrades will normalize the anti-pattern again (the recurring failure mode of past 4 migrations).

---

## 13. Posting Sequence

When ready to file:

1. Post the **architecture issue** (body in chat — long form, all 9 root causes, 3-PR sequence)
2. Post the **async/submitted companion issue** (body in chat — covers PR 3 scope + #1247 gap #12 + media_buy_delivery shape fix)
3. Begin PR 1 implementation on a feature branch
4. PR 1 description references architecture issue #
5. FIXME comments at all allowlisted sites reference architecture issue #

---

## 14. Compact-survival checklist

If we compact context after this:
- This file is the durable record of the planning phase
- Architecture issue body is in chat — re-paste if needed
- Companion async/submitted issue body is in chat — re-paste if needed
- All 12+ deep-dive investigations are stored as agent results in conversation history (may be lost on compact)
- File:line citations should be re-verified against HEAD before opening PR 1 (line numbers may shift)

If picking up cold: read this file top-to-bottom, then read the architecture issue body. That's complete context.
