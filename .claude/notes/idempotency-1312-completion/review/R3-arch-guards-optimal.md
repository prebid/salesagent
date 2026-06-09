# R3 — Architecture / Guards / "Concise & Optimal" Adversarial Review

**Reviewer role:** refute the plan's guard-green / DRY / optimal / atomic / sequencing claims, and find over-/under-build. Read-only, evidence-cited.
**Target:** `PLAN-REBUILD.md` + `SYNTHESIS.md` (β success-caching rebuild).
**Branch:** `feature/b6-idempotency-replay-table`. All file:line OBSERVED on this branch unless tagged INFERRED.

---

## LEAD — the items that change the plan

1. **A4 — "atomic success-cache write in the MediaBuy's own transaction" is NOT real today and requires real restructuring (HIGH).** `_create_media_buy_impl` opens **22 separate `MediaBuyUoW` blocks** (`grep -c` = 22), each its own commit. The auto-approved buy create is its OWN UoW (`media_buy_create.py:3436-3452`) that commits, then packages commit in a *different* UoW (`:3474`), then creatives in *another* (`:3574`); the success envelope is built at `:3874` and returned at `:4005` **outside every UoW**. The current idempotency probe is yet another isolated read UoW (`:1738`). "Same txn as the buy" is achievable only by threading ONE UoW across create+cache — which the present code does not do for create+packages either. The plan asserts atomicity as a settled fact (PLAN-REBUILD.md:17,30-34); it is a restructuring task, not a given.

2. **A4-corollary — the atomicity claim cannot cover the ad-server booking, so the "crash window" β claims to close is narrower than advertised.** In the auto-approved path the adapter call (`_execute_adapter_media_buy_creation`, `:3350`) happens **before any DB write**. A crash after booking but before the buy-row commit already leaves an orphan ad-server order (the code says so: `:2560` "An orphan adapter-side order may exist"). Putting the success-cache write in the buy's transaction closes the buy↔cache gap but NOT the booking↔buy gap. The headline "closes the crash-between-success-and-cache window the PgBackend docstring flags" (PLAN-REBUILD.md:17) overstates the win.

3. **A3 — keeping BOTH `MediaBuy.idempotency_key` unique index AND an `idempotency_attempts` success-cache is defensible for create_media_buy but the plan does not justify the *envelope storage* as minimal (HIGH).** The two mechanisms are not redundant for the same job (see A3 below), but storing the full serialized response envelope in `idempotency_attempts` collides with `no_model_dump_in_impl` and re-introduces the verbatim-vs-rebuild tension the plan claims to resolve. There is a leaner faithful option (store the hash on `media_buys`, re-derive the envelope from the persisted buy + frozen advisory) that the plan rejected on "verbatim" grounds without confronting the serialization-in-impl guard cost.

4. **A2 — the plan's own pseudocode `success.serialize()` does not exist; producing the verbatim envelope dict means `model_dump()`, which is GUARD-BANNED inside `_create_media_buy_impl`.** No `.serialize()` method exists on `CreateMediaBuyResult`/`CreateMediaBuySuccess` (`grep "def serialize" src/core/schemas/_base.py` → none). The verbatim wire dict is produced by `result.model_dump(mode="json")`. `test_architecture_no_model_dump_in_impl.py` scans every function ending in `_impl` (`:85`) for `.model_dump`/`.model_dump_internal` (`:23`), and `media_buy_create.py` has **zero** entries in `KNOWN_VIOLATIONS` (`:33-64`). A `model_dump()` added inside the impl to serialize the success for storage = an immediate new violation. The dump MUST be pushed into a repository method (mirroring `create_from_request`'s `req.model_dump` at `media_buy.py:311`). The plan does not specify this; step 4 as written trips the guard.

5. **A1 — flipping `idempotency_key` back to REQUIRED leaves STALE comments (not stale *allowlist*) in `test_architecture_schema_inheritance.py`, and the plan's "VALIDATION_ERROR via existing schema validation" claim (D2) is UNVERIFIED against the actual override.** The override entries at `:229` (`CreateMediaBuyRequest`) and `:234` (`UpdateMediaBuyRequest`) carry the comment "optional override (generated at boundary)". The guard has **no stale-entry detection for KNOWN_OVERRIDES** (only a membership check at `:262`), so removing them is not forced and leaving them does not fail CI — but the comments become false, a P-pattern hygiene flag. More importantly, the schema relaxation is `idempotency_key: str | None = None` (`_base.py:1446`); flipping to required means the field is inherited from the library (required) and the override line is *deleted*, at which point the allowlist entry IS stale-but-harmless. The real open question (D2/V2) — does deleting the override make a missing key emit `VALIDATION_ERROR` uniformly across MCP/A2A/REST? — is asserted, not verified.

---

## A1 — Guard coverage  →  **NEEDS-ADJUSTMENT**

The plan names guards but (a) misses two that the change touches, (b) the headline DRY/duplication guard is the real risk, and (c) one allowlist-comment goes stale.

### Guards the plan correctly identifies as staying green (verified)
- `test_architecture_no_error_construction_in_impl.py` — cap `media_buy_create.py: 1` is the principal-not-found `Error(code="AUTH_REQUIRED")` at `media_buy_create.py:1713` (`:50`). The removed `_cache_rejection_*` helpers build NO `Error(code=)`, so the count stays 1. BUT `test_caps_only_shrink` (`:114-122`) requires sites == cap EXACTLY: the new code must not add any `Error(code=)`. The plan's "missing key → VALIDATION_ERROR via schema" keeps it raise/validation-based, so OK. **CONFIRMED green.**
- `test_architecture_no_value_error_in_impl.py` — cap 2 (`:56`, lines 286/821). Removed rejection helpers hold no `raise ValueError`. **CONFIRMED green.**
- `test_architecture_no_model_dump_in_impl.py` — green ONLY IF the success-cache serialization is done in a repository, not the impl (see LEAD #4). **CONDITIONAL.**
- `test_architecture_repository_pattern.py` — `IMPL_SESSION_ALLOWLIST` is empty `set()` (`:47`); the two idempotency entries at `:250-257` are *test fixtures* in `test_media_buy_repository.py` (the success-side `find_by_idempotency_key` tests), which the plan KEEPs. No delta. **CONFIRMED.** (New integration tests must use factories, not `session.add()`.)
- `test_architecture_no_silent_except.py` — `_cache_rejection_envelope`'s `except Exception: logger.exception(...)` (`media_buy_create.py:1558-1566`) is NOT a violation (it logs); `_KNOWN_VIOLATIONS` is empty (`:46`). Removing it changes nothing. **CONFIRMED.**
- `test_architecture_single_migration_head.py` — `alembic heads` = `1d9b1402eacb (head)` only. Single head confirmed (the plan's migration topology claim is accurate — see A6). **CONFIRMED, re-verify post-rebase.**
- `test_architecture_migration_completeness.py` — see A6. **CONFIRMED for a well-formed V1.**

### Guards the plan OMITS that the change touches
- **`test_architecture_wrapper_typed_params.py`** (`:31` create_media_buy, `:47` create_media_buy_raw) and **`test_architecture_wrapper_field_descriptions.py`** — the plan rewrites the `idempotency_key` MCP/A2A wrapper docstrings/descriptions (`media_buy_create.py:4115-4124`, `:4230`) and may change the field type when flipping to required. The wrapper param stays `str | None` Annotated with a `Field(description=...)`, so `field_descriptions` stays green only if the rewritten description is preserved as a `Field(description=...)`. If the plan makes the wrapper param non-optional (`str`) the typed-params guard still passes (str is scalar) but the description guard requires the `Annotated[..., Field(description=...)]` wrapper to remain. **NEEDS-ADJUSTMENT: enumerate these two guards in the plan's hygiene list.**
- **`test_error_format_consistency.py:765`** — `IDEMPOTENCY_EXPIRED` sits in `CANONICAL_ERROR_CODES` (`:741-771`). `test_all_exception_error_codes_are_canonical` (`:773`) checks each live class's code IS-IN the set (forward only); there is **no reverse stale check**, so leaving `IDEMPOTENCY_EXPIRED` after deleting the class does NOT fail CI. Still, it is dead vocabulary a reviewer flags. The plan should remove it. **Hygiene, not a blocker.**

### The real A1 risk: the DRY / duplication ratchet
- `.duplication-baseline` = `{"src": 37, "tests": 100}` (count-based). Deleting 3 near-identical rejection helpers should LOWER `src`; the plan says regenerate downward (PLAN-REBUILD.md:88) — correct. **But** β stores the success envelope at TWO create paths (pending `:2536`, auto-approved `:3436`) AND on TOCTOU recovery — three structurally-identical "build success result + record_success in same UoW" blocks. If written inline, this ADDS duplicate blocks and could raise `src` back up, *failing the ratchet* and violating the CLAUDE.md DRY invariant. See A2.

### Stale-allowlist verdict
- No guard allowlist goes *stale-and-failing* from this change. The schema-inheritance override comments go stale-but-passing (LEAD #5). EXPIRED removal touches `test_adcp_exceptions.py:112-119,130-137` (delete the two expired tests) + `test_error_format_consistency.py:765` (remove entry) — the plan covers the exceptions test but should add the format-consistency line.

---

## A2 — DRY (CLAUDE.md non-negotiable)  →  **NEEDS-ADJUSTMENT (a shared helper is REQUIRED)**

OBSERVED — β records the success envelope at three structurally-identical sites:
1. pending-approval create (after `create_from_request`, `media_buy_create.py:2536-2553`),
2. auto-approved create (after `create_from_request`, `:3436-3452`),
3. both TOCTOU `IntegrityError` recovery branches (`:2563`, `:3462`) re-read → replay.

INFERRED — "compute hash + record_success(scope, tool, key, hash, envelope) in the same UoW that created the buy" is the **same logical operation with parameter substitution** at sites 1 and 2. Per the CLAUDE.md DRY invariant ("If you write a block of logic that is structurally similar to an existing block … you MUST extract a shared helper"), this REQUIRES a single helper (e.g. `_persist_buy_and_cache_success(uow, req, identity, status, ...)`), not copy-paste. If the plan inlines it, (a) the duplication ratchet may fire (A1), and (b) it violates the invariant directly. **The plan does not name this helper.** Add it to the change-set.

OBSERVED — removing `_build_idempotency_hit_result` + the 3 rejection helpers IS a real net simplification (4 functions + 4 call sites gone). But the plan replaces `_build_idempotency_hit_result` with "verbatim replay" — that replay logic (re-read attempt row → reconstruct `CreateMediaBuyResult` from stored envelope → set `replayed=True`) is itself new shared logic used by both the happy-path probe and the TOCTOU recovery. So logic does not vanish; it MOVES from "re-derive from MediaBuy" to "re-hydrate from stored envelope." Net: roughly neutral function count, with a new serialization concern (LEAD #4).

**Verdict: the success-store is the same operation twice → a shared helper is mandatory under the invariant; the plan must specify it. The deletion is genuine simplification.**

---

## A3 — Concise / Optimal: one mechanism or two?  →  **NEEDS-ADJUSTMENT** (two is defensible, but the plan's *envelope storage* is not the minimal faithful option)

### Are `MediaBuy.idempotency_key` (unique index) and the `idempotency_attempts` success-cache redundant?
**No — they answer different questions for create_media_buy:**
- `MediaBuy.idempotency_key` partial unique index (`models.py:967-974`, `tenant_id, principal_id, idempotency_key WHERE NOT NULL`) prevents a **duplicate ad-server booking** at COMMIT time (the TOCTOU backstop — two concurrent misses, one wins, the loser gets `IntegrityError` → replay). It is the *write-side* dup-prevention.
- The `idempotency_attempts` row carries **`payload_hash`** (`models.py:1013`) — the ONLY place a stored hash exists for conflict detection, AND the verbatim response envelope. The MediaBuy row stores `raw_request` (`models.py:922`) but **no canonical hash**, so MediaBuy alone cannot distinguish a true replay from an `IDEMPOTENCY_CONFLICT` without recomputing the hash from `raw_request` on every replay.

So conflict detection and verbatim replay need a stored hash + stored envelope; dup-booking prevention needs the unique index. They are NOT the same mechanism. **Two mechanisms is the correct minimum FOR create_media_buy with verbatim semantics.**

### BUT — is storing the full envelope optimal?
Three leaner faithful options the plan does not weigh:
- **(i) Hash-on-media_buys, re-derive envelope.** Add `payload_hash` to `media_buys` (the prior PLAN's "F8"), drop the separate table for create_media_buy, re-derive the success envelope from the persisted buy + a *frozen* advisory. Leanest; ONE table; ONE write in the buy's txn (truly atomic, no second repo). Cost: the advisory must be frozen (persisted), not re-derived live — which is exactly the byte-for-byte fix the plan wants anyway. The plan rejected α on "re-derivation risks verbatim" (SYNTHESIS.md:34) but β's own verbatim requirement is satisfiable by persisting the advisory, not the whole envelope.
- **(ii) Envelope-in-attempts (the plan's β).** Faithful, generalizes to other tools — but forces `model_dump()` serialization (guard tension, LEAD #4) and a second repository write that must share the buy's UoW (atomicity work, A4).
- **(iii) Hash-only-in-attempts.** Store hash for conflict + a pointer to the MediaBuy; re-derive envelope from the buy. Middle ground.

**MINIMAL OPTIMAL DESIGN recommendation:** For the *graded scope* (create_media_buy only at 3.0.1), **option (i)** is the most concise and the most genuinely atomic — one row, one write, one transaction, no `model_dump`-in-impl, no second repo to thread through the UoW. The plan's β (envelope-in-attempts) is justified ONLY by the "generalize to other mutating tools (fast-follow)" goal (PLAN-REBUILD.md:8) — which is a real future value but is **scope the plan defers anyway**. Building the generic envelope cache now, wiring only create_media_buy, and eating the serialization-guard + atomicity-restructuring cost, is **over-build relative to the graded contract**. If the generic cache is wanted, that is a principled scope expansion — but the plan should SAY that's why it accepts the heavier design, not present β as the minimal correct choice. As written it over-builds for 3.0.1.

(If the team commits to the generic cache: store the envelope but produce it via a repository `record_success(result)` method that dumps at the DB boundary, and thread ONE UoW across buy-create + cache — see A4.)

---

## A4 — Atomicity claim  →  **REFUTED as stated; NEEDS-RESTRUCTURING to become true**

### What the architecture supports
- `MediaBuyUoW` (`uow.py:121-149`) wires `media_buys` AND `idempotency_attempts` on the **same `_session`** (`:140`, `:143`). So a SINGLE UoW block CAN write both atomically — the substrate exists. **CONFIRMED the capability.**

### What the code actually does today
- The create paths each use their OWN, SEPARATE `MediaBuyUoW`:
  - pending: buy create `:2536-2553` (commits) → packages `:2609` (separate UoW) → …
  - auto-approved: buy create `:3436-3452` (commits) → packages `:3474` (separate UoW) → creatives `:3574` (separate UoW).
- The idempotency *read* probe is its own isolated UoW (`:1738`, `_IdempotencyUoW`), opened/closed long before the create UoW.
- The success envelope (`adcp_response`, `:3874`) is built and returned (`:4005`) **outside any UoW**, after all create transactions have committed.
- `grep -c "with MediaBuyUoW\|_IdempotencyUoW\|_CacheUoW"` = **22** separate transaction scopes in this one function.

### Therefore
"The success-cache write commits in the MediaBuy's own transaction" (PLAN-REBUILD.md:17,30-34) is **not the current shape and is non-trivial to achieve**: today the buy row commits in one UoW and the response is assembled later. To make the cache write atomic with the buy create you must either (a) move the `record_success` call INTO the buy-create UoW block (`:3436` / `:2536`) — but at that point the *full success envelope is not yet built* (packages/advisories are assembled afterward at `:3814-3881`), so you'd be caching a partial/placeholder; or (b) restructure so the entire create→response-build happens in ONE UoW that also writes the cache — a substantial reordering touching package + creative persistence. **Neither is "just add a line."**

### Detached-ORM hazard (memory: feedback_uow_detached_after_exit)
- `BaseUoW.__exit__` (`uow.py:95-112`) has `expire_on_commit` default + closes the session. Any ORM object returned from a UoW block is detached after the block. The plan's "re-read attempt → replay verbatim" on TOCTOU (PLAN-REBUILD.md:35) must read the row's `response_envelope`/`payload_hash` **inside** the recovery UoW block and copy to locals before exit — the current `_build_idempotency_hit_result` already does its work inside the `with` (`:1609-1643`), so mirror that. If verbatim replay reads `cached.response_envelope` after the block, it will raise `DetachedInstanceError`. **Flag explicitly in step 4.**

**Verdict: atomicity is REAL only after restructuring; "same txn" as presented is INFERRED-not-OBSERVED and the plan treats it as settled. Downgrade to an explicit work item with the partial-envelope ordering problem called out.**

---

## A5 — Sequencing safety  →  **NEEDS-ADJUSTMENT** (step 1 breaks the suite before step 6 lands; not atomic-per-commit)

The plan claims each commit is `make quality`-green (PLAN-REBUILD.md:97). Hazards:

- **Step 1 (restore required `idempotency_key`) is NOT self-contained.** Flipping the field to required immediately breaks:
  - The existing `test_idempotency_replay.py` wire tests (`TestWirePathReplay`, `:341-368`) and the mock tests in `test_media_buy.py` (`test_idempotency_absent_proceeds_normally` `:1249`, etc.) which exercise *absent-key* and *rejection* behavior — those are not ported until **step 6**.
  - Any harness/test/caller that creates a media buy WITHOUT a key (the plan's own V2 says "enumerate blast radius" — PLAN-REBUILD.md:93). The pre-existing BDD steps (`uc002_create_media_buy.py:594-655`, per grounding) describe "absent key proceeds without protection" and will runtime-fail (not xfail) once required, violating the BDD pass-or-xfail meta-rule.
  - The schema-inheritance override deletion is in step 1 but the comments/entries interplay (A1/LEAD #5) is in the same commit — fine, but the BDD + replay-test breakage spans steps 1→6.
  **=> Step 1 cannot be green alone.** Either (a) make the required-key flip and ALL its test/BDD/caller updates one atomic commit, or (b) sequence the test port (step 6) and BDD authoring BEFORE or WITH the schema flip. The current 1→2→…→6 order guarantees a red window.

- **Step 4 (rip out rejection machinery + wire success cache) before step 6 (port tests)** means the integration suite (`test_idempotency_replay.py`, `test_idempotency_attempt_repository.py`) references deleted symbols (`record_rejection`, `_raise_idempotency_rejection_replay`, `seed_rejection`, `assert_replayed_rejection`) and fails to import/collect between step 4 and step 6. Import-collection failure fails the whole `tox -e integration` run. **=> steps 4, 5, 6 must be ONE commit, or the test port must precede the deletion.**

- **Step 3 (`record_rejection`→`record_success` rename) before step 4 (callers)** — between 3 and 4, the production caller `media_buy_create.py:1544` still calls `record_rejection`, which no longer exists → import/attribute break. **=> 3 and 4 are coupled.**

**Verdict: the per-commit-green claim is REFUTED for steps 1, 3, 4 in isolation. The realistic atomic units are {1 + test/BDD updates} and {3+4+5+6}. Re-sequence or merge.**

---

## A6 — Migration hygiene  →  **CONFIRMED safe for a well-formed V1, with two caveats**

- Topology verified: `1d9b1402eacb` (head, adds `payload_hash`) → `ee84c805a0b1` (merge) → `097b909c7b5f` (creates `idempotency_attempts`) → `b4e2bffdd4f8`. Single head (`alembic heads`). The plan's description (PLAN-REBUILD.md:73) is accurate.
- A V1 migration adding `account_id` + reworking the unique index is migration-safe IF:
  - **Caveat 1 (downgrade table coverage):** `test_architecture_migration_completeness.py::test_downgrade_covers_upgrade_tables` (`:142`) extracts the FIRST string arg of each `op.*` call (`_extract_table_names`, `:61-75`). An upgrade doing `op.add_column("idempotency_attempts", …)` + `op.drop_index(old, table_name="idempotency_attempts")` + `op.create_index(new, "idempotency_attempts", …)` must have a downgrade that references `idempotency_attempts` in `op.*` calls. **Hazard:** `op.drop_index("idx_name", table_name="idempotency_attempts")` passes the *index name* as the first positional arg, so `_extract_table_names` records `"idx_name"`, NOT the table. If the downgrade only does `op.drop_column` + index rename via `drop_index/create_index`, the extracted up-table-set vs down-table-set could mismatch and trip the coverage check. Write the migration so both up and down reference `"idempotency_attempts"` via `add_column`/`drop_column` (first-arg = table). **Verify the extracted sets match by running the guard.**
  - **Caveat 2 (unique index on existing rows):** reworking the unique index to include `account_id` on a table that already has rows is safe only because `idempotency_attempts` is freshly created on this same branch (no production data) — but on an environment that already ran `097b909c7b5f`, adding `account_id NULL` then a unique index over `(tenant, principal, account_id, tool, key)` is fine (NULLs don't collide in Postgres unique indexes by default, matching the existing `WHERE NOT NULL` partial pattern on media_buys). No data backfill needed since the column is new. **OK.**
- Re-verify single head post-rebase (zero-tolerance guard) — the plan says this (PLAN-REBUILD.md:75). **CONFIRMED.**

**Verdict: CONFIRMED, provided the V1 up/down both reference the table by name in `add_column`/`drop_column` (not only via index-name args) — run the completeness guard to confirm.**

---

## A7 — Under-built / gold-standard gaps  →  **multiple real gaps**

### GOLD-STANDARD GAPS (ordered by impact)
1. **Unbounded growth is WORSE under β, and no cleanup job is in scope.** `idempotency_attempt.py:112-123` (`expire_old`) explicitly states "No production caller is wired yet — the table will grow unbounded." Under rejection-caching only *errors* were stored; under β **every successful create_media_buy** (and, fast-follow, every mutating call) writes a row that never gets reaped. The read path filters on `expires_at` (`:69`) so correctness is fine, but storage grows monotonically. **The plan does not put a cleanup job in scope** (PLAN-REBUILD.md is silent; the table is KEEP/repurpose). At minimum: wire `expire_old` to a periodic task, OR document the deferral with a tracked follow-up AND keep `expires_at` enforced. A gold-standard success-cache has eviction. **UNDER-BUILT.**
2. **No observability on replay / conflict.** The spec capability advertises idempotency; operators need metrics/logs to see replay-hit rate and conflict rate (cache effectiveness, key-collision attacks). Current code logs at INFO (`:1743`, `:1771`) but the plan adds no counter/metric. A gold-standard implementation emits a replay-hit / conflict / store metric. **UNDER-BUILT (advisory).**
3. **`replayed` placement on `CreateMediaBuyResult` collides with the error-shaped success return.** `CreateMediaBuyResult.response` is `CreateMediaBuySuccess | CreateMediaBuyError` (`_base.py:294`), and the principal-not-found path RETURNS a `CreateMediaBuyResult(response=CreateMediaBuyError(...), status="failed")` at `media_buy_create.py:1711-1717` (a success-envelope-shaped error return, NOT a raise). The plan injects `replayed` in `_serialize` (`_base.py:296-300`). That is the right single choke point — but the plan must ensure `replayed` is only ever True on a genuine *replayed success*, never leaking onto the error-return branch. Default `False` + only the replay producer sets True handles it, but the plan should state the invariant given the union. **Specify it.**
4. **Concurrency under the atomic model is under-specified.** With the cache write moved into the buy-create UoW, two concurrent misses both: book the ad server (`:3350`), then race to commit. The `MediaBuy.idempotency_key` unique index makes one lose with `IntegrityError` → recovery replay (`:3462`). But the loser ALSO booked an ad-server order (`:2560`/`:3458` orphan warning). The plan inherits this orphan-on-race behavior unchanged — acceptable, but the success-cache does not improve it, and the plan's "atomicity closes the crash window" framing (LEAD #2) should not be read as fixing the orphan-booking race. **Clarify scope.**

### Konstantine P-pattern application (from reference catalog)
- **P-ship-with-caller (substrate needs a production caller):** β's `record_success` MUST be called from production in the same PR (it is — both create paths). Good, IF the generic-cache framing is honored (the method should be generic, but only create_media_buy wires it now — that's the documented fast-follow). Ensure the KEPT `idempotency_attempts` repo has ≥1 live production caller after the rejection callers are deleted; otherwise the whole table+repo becomes dead code a reviewer flags (the rejection callers at `:1544`, `:1764` are the ONLY current callers — `grep` confirms — so if `record_success` wiring slips, the table is orphaned).
- **P34 (`error_code=` bypass):** the conflict raise (`media_buy_create.py:1772`) correctly uses the typed `AdCPIdempotencyConflictError` with no `error_code=` kwarg — preserve that shape when relocating. The missing-key path (D2) must NOT do `AdCPValidationError(error_code="INVALID_REQUEST")` (that's the P34 smell, and grounding-C E2 flags it); if VALIDATION_ERROR is acceptable per the storyboard (PLAN-REBUILD.md:9), raising the typed `AdCPValidationError` as-is (code VALIDATION_ERROR) is clean. **Confirm the missing-key path uses a typed raise, not a synthesized code.**
- **Wire-envelope assertions (wire_envelope_policy):** the plan's test matrix (PLAN-REBUILD.md:78-83) correctly uses `assert_envelope_shape(wire_error_envelope, …)` for conflict/missing-key and a NEW success-replay assertion for replay. Good. The success assertion must read `result.payload` + top-level `replayed` AND the harness must `data.pop("replayed", …)` in `parse_rest_response` (grounding-C S6, `tests/harness/media_buy_create.py:328`) or `extra="forbid"` will raise on reconstruction. **Plan references this; keep it.**
- **Cross-transport parity (P36):** `replayed` injected once in `_serialize` reaches MCP/A2A/REST uniformly (grounding-C verified all three dump `CreateMediaBuyResult`). **OK, but V3 (top-level placement per transport) is asserted-not-verified** — the plan flags it.

---

## UNCERTAIN (could not fully verify; flag before building)

- **[INFERRED] Whether moving `record_success` into the buy-create UoW forces caching a partial envelope.** The full envelope (packages from adapter response, advisories) is assembled at `:3814-3881`, AFTER the buy-create UoW (`:3436`) closes. I did not trace whether all envelope inputs are available at `:3436` time. If not, "atomic cache write" requires either re-ordering envelope construction before the buy commit, or accepting a two-phase write (buy in txn A, cache in txn B) — which is exactly what the plan claims to avoid. **Verify the data-availability ordering before committing to single-txn.**
- **[OBSERVED-partial] D2 missing-key → VALIDATION_ERROR uniformity.** I confirmed the override is `idempotency_key: str | None = None` (`_base.py:1446`) and that deleting it inherits the library's required field. I did NOT run the three transports to confirm a missing required field surfaces as `VALIDATION_ERROR` (vs a 422/transport-specific shape) uniformly. The plan tags this V2; it remains unverified. **Run the missing-key wire matrix early.**
- **[INFERRED] Duplication ratchet direction after the net change.** Deleting 3 helpers lowers `src`; adding a shared success-store helper is one block; if inlined at 2-3 sites it could RAISE `src`. I did not run `check_code_duplication.py` against a hypothetical diff. **The net `src` delta depends on whether A2's shared helper is extracted — run the duplication check on the real diff.**
- **[OBSERVED] `record_success` name collision** with `webhook_delivery_service.py:92` (circuit breaker). Different class, no functional conflict — purely a grep-noise/readability note. Not a blocker.
- **[UNVERIFIED] Capability `replay_ttl_seconds=86400` with EXPIRED out of scope.** Two sites (`capabilities.py:92,265`). Grounding-B flags whether advertising a TTL window for an unimplemented EXPIRED eviction is a conformance problem. The plan KEEPs both with a DRY note (one constant). I did not check the adcp 4.3 `Idempotency` type's required fields. **Verify the SDK type permits/requires `replay_ttl_seconds` and that advertising it without active eviction is honest (the read path DOES honor TTL via `expires_at`, so the window is real even without a reaper).**

---

## Verdict table

| # | Assumption | Verdict | Core evidence |
|---|---|---|---|
| A1 | Plan's named guards stay green; no stale allowlist | **NEEDS-ADJUSTMENT** | misses `wrapper_typed_params`/`wrapper_field_descriptions`; `model_dump`-in-impl conditional (LEAD #4); duplication ratchet is the real risk; schema-override comments go stale-but-passing; `test_error_format_consistency.py:765` EXPIRED entry |
| A2 | β stores success at 2 paths + TOCTOU; DRY ok | **NEEDS-ADJUSTMENT** | 3 structurally-identical store sites (`:2536`,`:3436`,recovery) → shared helper REQUIRED per CLAUDE.md invariant; deletion is real simplification |
| A3 | Keep BOTH idempotency_key index + success cache | **NEEDS-ADJUSTMENT** | two mechanisms NOT redundant for verbatim create_media_buy; but envelope-storage is over-build vs option (i) hash-on-media_buys for the graded 3.0.1 scope |
| A4 | Success-cache write atomic in buy's txn | **REFUTED (needs restructuring)** | 22 separate UoW blocks; buy create (`:3436`) commits before envelope built (`:3874`/`:4005`); detached-ORM hazard on replay |
| A5 | Each commit make-quality-green | **NEEDS-ADJUSTMENT** | step 1 (required key) + step 3/4 (rename/delete) break suite/imports/BDD before step 6 ports tests; merge into atomic units |
| A6 | V1 migration single-head + completeness safe | **CONFIRMED (w/ caveat)** | topology + single head verified; up/down must reference table via add/drop_column, not only index-name args |
| A7 | Plan is complete (no gold-standard gaps) | **UNDER-BUILT** | unbounded growth WORSE under β + no cleanup wired; no replay/conflict metrics; `replayed`-on-error-union invariant unstated; orphan-booking race unimproved |
