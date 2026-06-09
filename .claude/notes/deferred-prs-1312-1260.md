# Deferred reconciliations: #1312 (idempotency) + #1260 (version-compat)

Both are merge-with-main reconciliations that turned out to be DESIGN-level (main
re-implemented/generalized the PR's feature), not mechanical. Deferred during the
2026-06-01 merge sweep. Verified facts captured here so they don't need re-deriving.

---

## #1312 — feature/b6-idempotency-replay-table (idempotency rejection replay)

**Conflict:** 3 files — `media_buy_create.py` (imports + the ~2325 error-path logic),
`adcp_a2a_server.py` (NL handler), `routes/api_v1.py`.

**Verified facts (origin/main):**
- main HAS success-replay idempotency (`find_by_idempotency_key`, `req.idempotency_key`
  checks ~1620) but NOT rejection-replay: `IdempotencyAttempt`, `record_rejection`,
  `_cache_and_return_rejection`, `_build_idempotency_rejection_replay` are all **0 in main**.
  `idempotency_attempt.py` repo is #1312's NEW file.
- main's `create_media_buy_raw` signature does NOT take `account`/`idempotency_key` kwargs;
  it routes idempotency via the `CreateMediaBuyRequest` model (`req.account`, `req.idempotency_key`).
  BUT the *merged* `create_media_buy_raw` (auto-merge) DID keep #1312's `account`/`idempotency_key`
  kwargs and threads them into the request (4250-4251) + main's `enrich_identity_with_account`.
  So #1312's REST/A2A idempotency-param enhancement survives the merge intact.
- main's early-validation error path: `except (AdCPError, ValueError, PermissionError):
  audit_step_failure_if_present(step,e); raise` — main's stated reason: the old
  return-CreateMediaBuyResult path **mis-tagged PermissionError as VALIDATION_ERROR**.
- #1312's path: `except (ValueError, PermissionError): _cache_and_return_rejection(...)` which
  builds `CreateMediaBuyError`, caches `response.model_dump()` to IdempotencyAttempt, RETURNS it.
- `_build_idempotency_rejection_replay` RETURNS `CreateMediaBuyResult(CreateMediaBuyError, failed)`.
  4 tests + `tests/harness/assertions.py::assert_replayed_rejection` pin this return-based contract.
- main's error-handling is MIXED: some paths raise (early-validation), some RETURN error-status
  Results (AUTH_REQUIRED ~1722). Could NOT locate the single point that converts an error-status
  Result → two-layer envelope, so could not verify return-based replay is wire-equivalent to a raise.

**The design fork (owner decision — it changes the buyer idempotency wire contract):**
- (A) Raise-based: live-rejection caches envelope + raises; replay reconstructs AdCPError from the
  cached envelope + raises → both go through the boundary → identical wire shape (two-layer envelope).
  Requires rewriting `_build_idempotency_rejection_replay` (return→raise) + a cached-envelope→AdCPError
  reconstruction helper (none exists) + updating ~4 tests + `assert_replayed_rejection`. Gold-standard
  but it's a feature re-architecture.
- (B) Return-based (keep #1312's design): preserves feature + tests, but must (1) fix the mis-tagging
  main flagged (build cached error via `normalize_to_adcp_error(e)` not the validation helper), and
  (2) confirm a returned error-status Result serializes to the SAME two-layer wire shape as a raise.
  Open question (2) is the blocker — unverified.
- Depends on #1307's completed taxonomy (Phase 0): reconstruction/raising needs the typed subclasses.
  Do #1307 taxonomy first.

To resume: `git checkout feature/b6-idempotency-replay-table; git merge origin/main` (re-create the
3 conflicts). Imports = union (keep AccountReference). a2a NL handler = take main's raise
(`AdCPValidationError`) but ensure account/idempotency_key still thread to `core_create_media_buy_tool`.
api_v1 = main's no-try/except form + keep #1312's `account=`/`idempotency_key=` (raw accepts them).
The ~2325 logic = the design fork above.

---

## #1260 — fix/issue-1246-pricing-helper-v3 (pricing_option v3 + get_products v2 back-compat)

**Conflict:** 8 files incl. `products.py`, `adcp_a2a_server.py` (3 hunks), 2 test files, baselines.

**Verified facts:**
- #1260 adds `add_get_products_v2_compat(response_dict, adcp_version)` (function-specific), used in
  products.py (~848) + a2a (~1517). main GENERALIZED this into `apply_version_compat("get_products",
  ...)` (generic dispatcher) used at the a2a boundary.
- The AUTO-MERGED `version_compat.py` kept #1260's `add_get_products_v2_compat` (+`_add_v2_compat_keys`)
  and did NOT contain main's `apply_version_compat` — so the merged tree is INTERNALLY INCONSISTENT:
  main's a2a side references `apply_version_compat` (would be undefined), #1260's side references
  `add_get_products_v2_compat` (exists).
- main applies version-compat at the a2a boundary but NOT in products.py MCP path (just model_dump).

**The fork:** keep #1260's specific `add_get_products_v2_compat` everywhere (consistent with the
auto-merged version_compat.py; take HEAD's side for products.py + a2a) — OR restore main's generalized
`apply_version_compat` across version_compat.py + all callers (more "correct" if main generalized
intentionally; more work). Verify which `version_compat.py` is canonical for the merged state.
Baselines (.type-ignore, .duplication) = recompute via --update-baseline. .pre-commit-config = cosmetic.
