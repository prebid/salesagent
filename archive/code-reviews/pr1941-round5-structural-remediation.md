# PR #1941 round-5 remediation — structural design

Design only; nothing here is implemented. Inputs: the round-5 review (4 BLOCKER / 25
SHOULD-FIX / 6 NIT, 31 tracked findings + a nit tail), the PR description, and the tree at
`fix/1900-grade-adcp-envelope-status-requiredness-for-real` head. Every `file:line` below was
read in this tree, not taken from the review's prose.

**Governing rule:** structure over guards. A remedy is acceptable only if it cannot itself
become a round-6 finding — which disqualifies most new AST scanners, allowlist entries, and
rationale comments. Prefer a signature, a type, or a deletion. Count net lines; a good
remediation is negative.

---

## Root-cause analysis

The 31 findings collapse into six design faults. Each names the wrong *shape* that made its
cluster the path of least resistance — not a category of symptom.

| RC | Design fault | Findings closed |
|----|--------------|-----------------|
| RC1 | Media-buy approval has three orchestrators and no owner | R5-25, R5-26, R5-28, R5-30, R5-31 |
| RC2 | The list pipeline validates rows twice because `_MediaBuyData` carries raw fields, not resolved facts | R5-23, R5-21, R5-35, R5-17, R5-33 (ground), R5-37 |
| RC3 | Review remedies delivered as prose claims and bulk sweeps instead of code | R5-3, R5-36, R5-2, R5-6, R5-24, R5-27, R5-34, R5-32 + nit tail |
| RC4 | AST guards shipped without a non-vacuity obligation | R5-1, R5-4, R5-5, R5-16 |
| RC5 | Oracles that never observe the thing they claim to grade | R5-7, R5-8, R5-18, R5-19, R5-20 |
| RC6 | Optimistic-concurrency token accepted but not implemented | R5-9, R5-13 (R5-10 filed) |

---

## RC1 — Approval orchestration has no owner

**The fault.** `execute_approved_media_buy` (`src/core/tools/media_buy_create.py:721`) does
the adapter work, then **unconditionally** writes `ACTIVE` at `:1241-1243`. Because it does
not own the final status, all three admin routes bolt their own orchestration around it, and
the three copies disagree:

- `workflows.py:194-272` — creative gate via a raw, **tenant-unscoped** select (`:210`,
  R5-30); post-execute resolver write at `:255-267` that reads a **detached** ORM row and
  500s (R5-25: the nested `MediaBuyUoW` enters `get_db_session()` — the same
  thread-scoped session (`database_session.py:151-152`, `uow.py:88-91`) — commits, closes
  it, and `expire_on_commit` default-True makes the route's `:255` attribute read raise
  `DetachedInstanceError` after the order exists in the ad server).
- `operations.py:395-460` — creative gate (tenant-scoped, correct), resolver write **before**
  the adapter call (`:433-452`), so the adapter-failure branch inherits a stamped, write-once
  `confirmed_at` (R5-28) and the success branch's resolved value is overwritten by `:1241`'s
  `ACTIVE` (R5-26 — the resolver adoption is inert). It also executes the adapter even when
  creatives are unapproved, which `workflows.py:228` refuses.
- `creatives.py:621-645` — the only correct lifecycle shape (execute, then fresh UoW,
  re-fetch, resolve, write), which proves the pattern but as a *convention* each route must
  re-discover.

Plus three byte-similar resolver blocks with three identical 5-line rationale comments and
identical assert messages — a DRY defect the review found twice independently.

**The structural fix — one post-adapter writer.**

1. `execute_approved_media_buy` becomes the sole owner of the media buy's state transition:
   - Signature: `execute_approved_media_buy(media_buy_id, tenant_id, *, approved_by: str, approved_at: datetime) -> ApprovalResult`, where `ApprovalResult` is a small frozen
     dataclass/StrEnum-carrier: `EXECUTED | HELD_PENDING_CREATIVES | FAILED` + optional
     `error_msg`. The `tuple[bool, str | None]` contract is deleted — a typed result makes
     "route decides flash/redirect, callee decides state" unrepresentable to get wrong.
   - Inside its own UoW, before the adapter call: creative gate via a new tenant-scoped
     repository method (below). Unapproved → write `PENDING_CREATIVES` (+ approval stamps),
     return `HELD_PENDING_CREATIVES`. This unifies the gate `operations.py` currently skips.
   - After adapter success: `resolve_flight_window_status(buy, now=…, creatives_approved=True)`
     replaces the hard-coded `ACTIVE` at `:1241-1243`; write it with `approved_at/approved_by`
     in the same `update_status` call (one write, one revision bump).
   - After adapter failure: write `FAILED` here (today only `operations.py:466` does), return
     `FAILED`. Because no committed status was written before the adapter ran, `confirmed_at`
     stays NULL on this branch — R5-28 closes as a *consequence of ordering*, not a guard.
2. New `CreativeRepository.unapproved_creative_ids(media_buy_id) -> list[str]` — tenant-scoped
   by construction (repository carries `tenant_id`). The three open-coded gates
   (`workflows.py:203-215`, `operations.py:397-420`, `creatives.py`'s variant) delegate to it
   or disappear entirely (workflows/operations lose theirs to the callee). R5-30's missing
   `tenant_id` filter becomes unrepresentable: no caller assembles the query anymore.
3. Routes never touch the media-buy row after calling execute. `workflows.py:255-272` (the
   detached read + write), `operations.py:427-452` + `:463-470`, `creatives.py:625-645` are
   deleted. Routes keep only: auth, workflow-step bookkeeping, webhook/flash on the returned
   `ApprovalResult`. R5-25 closes because the post-execute read *does not exist*, on any
   route, present or future.

**Guards/prose this deletes:** the three rationale comment blocks and asserts; the
`test_architecture_no_raw_select.py` allowlist entry
`("src/admin/blueprints/workflows.py", "approve_workflow_step")` (allowlist shrinks); the
`workflows.py` raw `CreativeModel`/`CreativeAssignment` selects.

**Tests (R5-31, folded here because this task owns the surface):**
- One integration test running `approve_workflow_step` with the **real**
  `execute_approved_media_buy` against Postgres (the un-mocked path that currently has zero
  coverage; today's tests patch `_EXECUTE_APPROVED_PATCH`, `test_workflows_blueprint.py:227`).
- Two cross-tenant tests modelled on `test_workflows_blueprint.py:384-422` for
  `creatives.py:491` and `operations.py:317`, asserting persisted state unchanged (not the
  status code — login redirects 200).
- One test asserting `confirmed_at IS NULL` after an adapter-failure approval.
- One two-tenant test: a colliding foreign `creative_id` does not block this tenant's
  approval.

**Why terminal.** A detached-row read cannot recur when no route holds a row across the
call. A divergent gate cannot recur when there is one gate. An inert write cannot recur when
there is one writer. The next reviewer probes a single function with a typed contract.

**Spec grounding:** no wire contract changes. The persisted-status vocabulary and the
resolver are unchanged; only *which code path* writes them moves. Graded by the existing
admin/webhook integration tests plus the new ones above; the AdCP surface is untouched
(ungraded by storyboard — admin UI is out of protocol scope).

**Net lines:** ≈ −70 in `src/` (three orchestration blocks deleted, one gained), ≈ +120 in
tests. **Surface:** entirely inside files this PR already rewrote (`workflows.py`,
`operations.py`, `creatives.py`, `media_buy_create.py`, repositories).

**Deliberately not done here:** fixing the scoped-session nesting hazard itself
(`uow.py:89` re-entering the caller's session). That is a real landmine for every other
nested-UoW caller, but it is engine-level new surface — **filed** (see non-goals).

---

## RC2 — `_MediaBuyData` carries raw fields, not resolved facts

**The fault.** `_fetch_target_media_buys` (`media_buy_list.py:487`) validates
status/revision inside the UoW and builds `_MediaBuyData`, but the dataclass carries the
*raw* persisted fields — so the build loop in `_get_media_buys_impl` re-derives everything:
`_compute_status(buy, today)` again at `:230` under a byte-identical copy of the
refuse-or-omit policy (`:229-238` vs `:521-530`, R5-23; the first copy's advisory branch is
dead by dataflow), and `_persisted_revision(buy)` again at `:348` with **no covering
handler** — a second call to a raising function on a value the fetch stage already proved
valid. The hand-copied `_PINNED_REVISION_MINIMUM = 1` literal (`:601`, R5-17) and the
misdescribing comment at `:339-347` live in the same region.

**The structural fix — resolve once, at the seam; the build loop is a pure projector.**

1. `_MediaBuyData` gains `wire_status: MediaBuyStatus` (and keeps `revision: int`). Both are
   populated exactly once, inside `_fetch_target_media_buys`, inside the single
   `except AdCPPersistedStateError` policy site. `_compute_status` is now *always* called at
   fetch (it is needed for the response anyway); the status filter is applied to the computed
   value. A defective row **cannot reach the build loop** — the type says so.
2. Delete the `:229-238` policy copy and the `:348` re-derivation: the build loop reads
   `buy.wire_status` and `buy.revision`. `_persisted_revision` drops to exactly one call
   site. The `:339-347` comment shrinks to a true sentence ("populated at the validated
   fetch seam").
3. `_PINNED_REVISION_MINIMUM` is read from the pin: a `revision_minimum()` accessor beside
   `required_nullable_fields()` in `_pinned_fields.py`, reading
   `media-buy/get-media-buys-response.json#/properties/media_buys/items/properties/revision`
   `["minimum"]`. The literal symbol is deleted (R5-17). The existing single-kill mutation
   coverage (R5-22's measurement) keeps grading the value.
4. R5-35 (`_base.py:2999` / `media_buy_list.py:315`): a legacy naive or date-only
   `package_config.start_time` currently fails the entire listing because
   `GetMediaBuysPackage(...)` at `:310` is constructed outside any handler. Route it through
   the module's own OPTIONAL-field branch, exactly like `targeting_overlay` at `:256-262`:
   coerce a naive ISO string to UTC (the repo's stated column convention,
   `_media_buy_transitions._aware`) since the primary writer persists
   `datetime.now(UTC).isoformat()` (`media_buy_create.py:2774` et al.) and naive values are
   legacy-only; anything unparseable renders `None` + advisory. Correct the `_base.py:2999`
   docstring ("the wire is unchanged" holds for 1 of 5 forms — say which).
   *Spec grounding:* pinned `media-buy/get-media-buys-response.json` packages items type
   `start_time`/`end_time` as date-time and do not list them in `required`, so `null` plus a
   non-fatal advisory is a legal degraded rendering; graded by the full-document UC-019 wire
   scenario (`BR-UC-019` schema-valid steps). The advisory uses
   `CONFIGURATION_ERROR`/`terminal` per R5-21's ruling below.
5. R5-21: the `TARGETING_REHYDRATION_FAILED` advisory at `:296-305` changes to
   `code="CONFIGURATION_ERROR", recovery="terminal"` — matching the module's own argument at
   `:466-477` (seller-side permanent corruption; `SERVICE_UNAVAILABLE`'s pinned recovery is
   `transient`, advice that can never succeed). Fix the now-false rationale at
   `creatives/_processing.py:42` in the same change (RC3 carries the sibling contradiction).
   *Spec grounding:* pinned `enums/error-code.json` `enumMetadata` — `CONFIGURATION_ERROR`
   recovery `terminal`; advisory `errors[]` items need only `code` + `message`
   (`core/error.json`). Graded by the BDD advisory step (see RC5 item R5-22: the advisory's
   `recovery` gains a `_pinned_recovery`-backed Gherkin line so the class is read from the
   pin on both halves, closing R5-22 with a **reused** helper, not a new one).
6. R5-37 (repository half of the same disease — a seam that drops an id): delete
   `MediaBuyRepository._validated_status` (`repositories/media_buy.py:59-73`) and call
   `PersistedMediaBuyStatus.parse(status, media_buy_id=…)` directly at all four doors
   (`:424`, `:470` already does, `:531`, `:566`). The id is in each door's own signature;
   the wrapper is what loses it. One spelling, every refusal names the row — the guard
   `test_architecture_one_status_coercion.py:104` already instructs exactly this.
7. R5-33: rewrite the `_bump_revision` docstring claim (`repositories/media_buy.py:487`) to
   the ground that is actually true: protection is bump→flush adjacency (6 of 7 call sites
   flush on the next line; the 7th is a terminal statement of a private helper whose callers
   flush), not `TypeError` — which only fires in boolean/int contexts (2 of 10 read shapes).

**Why terminal.** The policy exists at one site because only one site *can* apply it — the
build loop's input type contains no unvalidated field. A hand-copied pin literal cannot
drift when the symbol holding it is gone. A dropped id cannot recur when no wrapper exists
to drop it.

**Net lines:** ≈ −35 in `src/`. **Surface:** files this PR already changed
(`media_buy_list.py`, `_pinned_fields.py`, `repositories/media_buy.py`, `_base.py` docstring).

---

## RC3 — Remedies delivered as prose claims and bulk sweeps

**The fault.** Two shapes of the same disease. (a) Review responses were closed with
*sentences asserting facts* — "nothing reads the Success branch" (`_base.py:840`, false: two
`getattr(result, …)` reads at `media_buy_update.py:727-728`), "implemented three times"
(`_media_buy_transitions.py:8`, it is four and the fourth —
`admin/services/media_buy_readiness_service.py:270-305` — is what operators see), "raises
TypeError" (R5-33), "one domain owner" (R5-34), the `:667-668` sentence contradicting
`:655` in the same `exceptions.py` docstring (R5-24), a false clause in a chmod rationale
(`run_all_tests.sh:165`, R5-32). Prose is ungraded by anything; eight claims were traced
this round and eight were wrong. (b) The tracker-citation sweep `ab396a03a` was a
regex-driven bulk rewrite: it clipped a regex *literal* into a fail-open
(`test_guards_no_beads_ids.py:29`, R5-36), produced 75 bare `# FIXME:` + 6 `FIXME()` + 22
empty parentheticals + 4 mangled xfail reason strings and several dangling sentences
(R5-3), and the hook meant to lock the result (`check_repo_invariants.py:56`) has no
self-test for its new check, is blind to `.feature`/conftest/step files by `types: [python]`
+ its own glob, fails on 3 lines this PR added, and carries a comment claiming beads ids
are exempt while the regex bans them (R5-2).

**The structural fix — a claim either becomes code, or is deleted.**

1. Where the prose asserts something checkable, the preceding RCs already moved the fact
   into code (RC2 items 3, 6, 7). The rest are corrections/deletions, all enumerated:
   - `_base.py:840-842`: replace the false "nothing reads the Success branch" with the true
     narrow claim — name `media_buy_id`/`affected_packages` as the two `getattr` reads and
     why a placeholder `revision` cannot leak through them (neither reads `revision`; both
     land only in log/bookkeeping paths). **No new AST rule** — R5-6's suggested read-scanner
     is exactly the RC4 disease; three known call sites do not warrant a guard.
   - `exceptions.py:667-668`: delete the sentence (it contradicts `:655`; the message *does*
     reach the buyer in both envelope layers — measured).
   - `_media_buy_transitions.py:8-17` and `:1` docstring: correct the inventory to four
     copies, naming `media_buy_readiness_service.py:270-305` as the un-adopted display-side
     copy, and replace "one domain owner" with the true statement of the granularity split
     (write side takes instants; `resolve_canonical_status` takes a `date`, so sub-day cases
     diverge — R5-34's two-row table is the acceptance oracle for whoever unifies it).
     Also correct the stale holdout list at `src/core/utils/flight_time.py:12-15` (this PR
     converted two of the files it names). Converting the readiness service itself is
     **filed**, not folded — `src/admin/services/` is untouched surface and it changes what
     operators see.
   - `creatives/_processing.py:42` vs `exceptions.py:655`: keep the `exceptions.py`
     statement (measured true), rewrite `_processing.py:42`.
   - `run_all_tests.sh:165`: delete the false "already world-writable" clause; keep the mode
     with its real (measured) rationale. The `user:`-on-`adcp-server` alternative is filed.
   - `_base.py:520-521` (nit tail): the "confirmed_at is NULL because the seller has not
     committed" sentence contradicts `is_media_buy_seller_confirmed('pending_creatives') ==
     True` and this PR's own test. Correct the sentence to the actual rule; whether
     `pending_creatives` *should* stamp is a spec question — flagged, not resolved (risk
     register).
2. Sweep repair (all mechanical, each verified against the review's byte-exact evidence):
   - Restore `_EXEMPT = re.compile(r"#\s*noqa:\s*beads-id")` and its assertion message
     (R5-36) — one line each.
   - Repair the enumerated dangling fragments: `webhook_validator.py:62`,
     `creatives.py:217`, `operations.py:531`/`:629`, `repositories/media_buy.py:9`,
     `test_unified_auth_middleware.py:1`, `conftest.py:1886`, and the two xfail reason
     strings at `conftest.py:813`/`:831` (report-visible under `-rxX`).
   - Marker policy: `CLAUDE.md:119` binds **allowlisted violations** to `FIXME(#<gh-issue>)`.
     Apply it exactly there: the 4 surviving `FIXME(salesagent-…)`, the 6 `FIXME()`, the 3
     `FIXME(production-gap bead)` and the 22 empty `(, ` parentheticals get a GitHub number
     or lose the marker (a bare, self-contained `# FIXME: <description>` is legal where no
     allowlist entry is involved; do not invent 75 issues).
3. The hook (`check_no_unresolvable_citations`) is *kept* — its forward-lock is the thing
   that stops shape (b) recurring — but made honest and graded, per its own docstring
   ("its grade is the mutation"):
   - Fix the 3 lines it fails on at this head (`test_media_buy_revision_confirmation.py:27`,
     `:604`, `then_schema.py:9` — replace `R1-6`/`R1-9` with self-contained text or a PR
     number).
   - Add two mutation cases to the existing `tests/unit/test_architecture_repo_invariants.py`
     (one per banned form) so the check has an oracle, and one case pinning the `:79`
     `"tests" in parts` operand (drop it → the case reddens).
   - Reconcile the comment with the regex: the beads-id alternative *is* enforced for `.py`
     files; say so, and state the deliberate `.feature` blind spot (10 known beads ids there)
     instead of implying coverage. Extending scope to `.feature` files is not folded — it
     would require editing generated features, which is upstream (adcp-req) work.

**Why terminal.** Every correction replaces a claim with either code (RC2) or a sentence
that states a *measured* fact with its measurement. The sweep class cannot recur silently:
the hook now has a self-test, and the one regex it damaged is restored and pinned by a probe
case. Nothing in this RC adds a new scanner.

**Net lines:** ≈ −45 (prose deleted > prose corrected). **Surface:** all inside
already-changed files except `flight_time.py:12` and `webhook_validator.py:62` — two
comment-only touches, justified as corrections to sentences this PR made false.

---

## RC4 — AST guards without a non-vacuity obligation

**The fault.** Round-2..4 remedies answered design findings with AST scanners, and round 5
found the scanners do not grade: `test_architecture_one_wire_serializer_seat.py` exempts
`src/core/schemas/_base.py` whole while **all** `@model_serializer` in `src/` live there —
284 files scanned, 0 instances visible, the assertion cannot fail (R5-4);
`test_architecture_media_buy_write_seam.py` misses the spelling the repository itself uses
(`MediaBuy(**kwargs)`, `repositories/media_buy.py:441`) plus `update(MediaBuy).values(...)`
and aliased imports (R5-5); `required_nullable_fields()` returns EMPTY for the 73 pinned
schemas composing via `allOf`/`oneOf` while its docstring promises a hard failure (R5-16);
and the one guard that *did* grade — `test_architecture_schema_inheritance.py` — was
**deleted**, on a probe aimed outside its target set, while this PR introduced exactly the
three redeclarations it existed to catch (R5-1, restored-and-run: 1 failed at head).

**The structural fix — fewer guards, and none that cannot redden.**

1. **Restore** `test_architecture_schema_inheritance.py` from `origin/main` (R5-1 is a
   BLOCKER; the deletion basis is refuted). Widen the alias key to
   `alias.asname or alias.name` (the probe hole), and add the three head redeclarations as
   `KNOWN_OVERRIDES` entries carrying their Pattern-#4 nested-serialization reasons
   (`GetMediaBuysMediaBuy.packages` at `_base.py:3042`, `GetMediaBuysPackage.targeting_overlay`
   at `:3007`, `GetMediaBuysResponse.media_buys` at `:3107`). This is the reviewer's option 1;
   option 2 ("route the fields so no redeclaration is needed") founders on Pattern #4 —
   the redeclarations narrow element types so local `model_dump` overrides run; removing them
   changes re-validation semantics. `KNOWN_OVERRIDES` is the guard's designed documentation
   mechanism (entries carry reasons and are type-graded against the pin by the alignment
   suite), not a debt allowlist; it starts at 3 and the stale-entry check keeps it honest.
2. **Delete** `test_architecture_one_wire_serializer_seat.py` (−279 lines). Its entire
   target population lives in its own exemption; it measures nothing by construction, and
   every unmodelled shape measures 0 live instances. The invariant it gestures at ("wire
   serialization concentrates in `_base.py` mixins") is already *structurally* true — the
   mixins are the only serializer seat — and the alignment suite's model_dump-survival
   checks grade the wire outcome, which is the thing that matters. Recorded in the PR
   description with this rationale (the R5-1 lesson: a guard deletion must name what now
   goes unrecorded; here the answer is "nothing that was recorded before — the guard
   recorded nothing").
3. **Fix** `test_architecture_media_buy_write_seam.py` — the one new guard the review
   verified live (relocation mutation reds). Close the three shape gaps exactly as R5-5
   specifies: `**`-splat keywords (`kw.arg is None`) on the model name, an
   `update(MediaBuy).values(...)` branch, and `ImportFrom` asname resolution — each landed as a
   `_KNOWN_BAD_SNIPPETS` fixture row so the finder is driven red by its own table. Extract
   the base/callee-resolution helper it already does best (`_called_name`,
   `constructor_keyword_qualified`) into a shared module used by
   `test_architecture_one_status_coercion.py` (whose `ast.Name`-only matcher misses
   `models.PersistedMediaBuyStatus(...)`) — DRY across the guard family, and the fix lands
   once.
4. **Fix** `_pinned_fields.required_nullable_fields()` in-file (R5-16): after pointer
   resolution, if the subschema has no local `required`, walk `allOf` branches (merging
   `required` + `properties`) before concluding; if still nothing and the schema composes
   via `oneOf`/`anyOf`, **raise** — the docstring already promises it. Widen the nullability
   predicate to `anyOf`/`oneOf` null-branches. Its three adopters are measured correct today, so
   this is drift-proofing the reader, not changing any wire value.
5. **Practice, not meta-guard:** every AST guard must contain a fixture table whose rows
   drive its own finder red (the pattern `test_architecture_no_response_side_persisted_defaults.py`
   already carries). This goes in `docs/development/structural-guards.md` as a review
   checklist line — deliberately *not* a guard-that-scans-guards, which would be RC4
   recursion.

**Why terminal.** The deleted guard cannot be found defective again. The restored guard
reddens under the exact head it was deleted at — that is its non-vacuity proof. The
write-seam guard's own fixture table is the standing mutation for every shape it claims.
`_pinned_fields` fail-closes, so the next composed-root adopter gets an exception, not an
empty set.

**Net lines:** ≈ −70 in tests (−279 deleted, +≈180 restored guard, +≈60 fixture rows/helper,
−≈30 deduped resolution logic). **Surface:** test files this PR added or deleted.

---

## RC5 — Oracles that never observe what they claim to grade

**The fault.** Five oracles pass without their subject occurring: the concurrency test
never observes overlap (R5-7 — mutation + a delayed writer B passes silently); the
normalization migration's decisions never execute against data (R5-8 — the roundtrip seeds
nothing and the one seeding test stops one revision short of `9b2d4f6c1a37`); the PR's
principal envelope scenario survives only while its upstream twin exists (R5-18 — no
`@hand-edited` marker, classifier says LEGACY-DELETE); the CONFLICT rows' three-transport
scope hangs on `"scope=per-transport" in str(reason)` — an unguarded substring over free
text (R5-19); and `confirmed_at`'s only invalid-partition row cites a `Given` that exists
nowhere (R5-20).

**The fixes** (each is small and carries its own named mutation):

1. R5-7: the reviewer's two lines in `writer_b`
   (`test_media_buy_revision_confirmation.py:678` region) — time the block with
   `monotonic()` and assert `b_blocked >= _LOCK_HOLD_SECONDS / 2` with the message "the
   transactions did not overlap; this run graded nothing". The oracle now *observes* the
   overlap it grades; a timing slip becomes a loud failure instead of a silent pass.
2. R5-8: extend `test_confirmed_at_backfill_migration.py` (which already seeds the exact
   out-of-vocabulary row at `:253`) one revision further, to `9b2d4f6c1a37`, with three
   cases: `'ACTIVE'` lowercased; unmapped value raises naming the value and the id; the
   abort leaves data untouched. No new file, no new harness — the seeding test already owns
   this chain.
3. R5-18: add `@hand-edited` + the `# HAND-EDITED` note to `@T-UC-019-envelope-status`
   (`BR-UC-019-query-media-buys.feature:45`), mirroring `:1401`; author it upstream in
   adcp-req in the same change (as `05b6947f5` did) so the marker is belt and the upstream
   twin braces. Record UC-011's new upstream-only dependency in the PR description (its fix
   has no local fallback — a known, stated risk rather than a silent one).
4. R5-19 — the structural piece of this RC: replace the substring probe at
   `tests/bdd/conftest.py:2866` with a tiny typed parser: `XfailReason` (frozen dataclass:
   `cause`, `scope`, `ref`, free text) parsed from the reason string, **raising on an
   unknown `scope=`/`cause=` token**. The conftest branch tests
   `reason.scope == Scope.PER_TRANSPORT`. A one-site typo now fails collection instead of
   silently re-scoping 4 rows. The taxonomy is 4 of 65 markers today; unparsed markers pass
   through as free text (no sweep of the other 61).
5. R5-20: add the missing `Given` beside its sibling at
   `tests/bdd/steps/domain/uc019_query_media_buys.py:2737` — `'…with persisted store
   missing confirmed_at (defective seller)'` — wiring `confirmed_at`'s only
   invalid-partition grader; or, if the partition is unreachable by the same argument as the
   retired neighbour at `feature:940-945`, retire the row *with that stated reason*. Default
   to wiring: the sibling revision row proves the harness supports defective-store setup.
6. R5-22 (from RC2 item 5): one Gherkin line delegating to the existing `_pinned_recovery`
   (`uc019_query_media_buys.py:1616-1633`) for the advisory's code, so both halves of the
   defect read recovery from the pin.

**Why terminal.** Each oracle now contains an explicit observation of its subject (overlap
duration, seeded rows at the right revision, a parser that rejects unknown tokens, a step
that exists). None of the fixes is a comment or a count.

**Net lines:** ≈ +80 in tests. **Surface:** test files this PR added/changed;
`BR-UC-019` feature edit goes through the upstream-author path (R5-18's own mechanism).

---

## RC6 — The concurrency token is accepted but not implemented

**The fault.** Round 3 made REST and MCP *accept* `revision` on update_media_buy, but
`req.revision` is compared 0 times in `src/` — the seller advertises an
optimistic-concurrency surface it does not have. The round-4 declination ("enforce or drop
are the only options; both blocked") is refuted by the reviewer's validated third option.

**The structural fix (R5-9 — fold, as the reviewer's most-wanted reconsideration):**
until #1607 implements CONFLICT, a request carrying `revision` is **refused**:

```json
{"status": "failed",
 "errors": [{"code": "UNSUPPORTED_FEATURE",
             "message": "the seller does not implement optimistic-concurrency revision matching",
             "field": "revision", "recovery": "correctable"}]}
```

*Spec grounding (mandatory citation):* pinned AdCP 3.1.1,
`media-buy/update-media-buy-response.json` — arm1 `UpdateMediaBuyError` requires `errors`,
forbids `media_buy_id`/`affected_packages`/`sandbox` and `status == "submitted"` (not
`"failed"`); `enums/error-code.json` lists `UNSUPPORTED_FEATURE` with recovery
`correctable`. The reviewer validated this exact document against the pin in both
directions, and production already builds the same shape three times in the same file
(`media_buy_update.py:716`, `:785`, `:853`). Graded by: new BDD rows on mcp + rest
asserting the wire arm1 shape via `assert_envelope_shape`/wire-dict steps; the a2a row
stays xfailed `ref=#1885` (A2A drops the field before `_impl`); the existing #1607
strict-xfail CONFLICT rows are untouched — they remain red under the refusal (wrong code),
exactly as they are today, and graduate when #1607 lands by deleting the refusal.
Breaks nobody: before this PR the same request died as `INVALID_REQUEST` on REST
(`api_v1.py:117-128`), so no buyer relies on acceptance.

*Scope note:* the round-3 graduated scenarios that assert *acceptance* of `revision`
(`@T-UC-003-revision-*`) must be re-authored upstream to assert the refusal — that is
adcp-req work in the same change, using the R5-18 upstream-author path. If the team judges
#1607 imminent (check before starting), this task converts to a FILE with the citation
attached; the design's default is fold, per the reviewer's weighting.

**R5-13 (same seam, typing):** narrow `list_by_statuses(statuses: list[str])`
(`repositories/media_buy.py:293`) to `list[PersistedMediaBuyStatus]` like its already-narrowed
sibling `get_all_by_statuses`; the declination's premise is measured false — exactly two
callers (`products.py:2140`, `dashboard_service.py:74`) and all four literals are members.
A misspelled status becomes a type error instead of a silent `[]`.

**Net lines:** ≈ +40 `src/`, +BDD rows. **Surface:** `media_buy_update.py` (changed by this
PR), repositories (changed), upstream feature files.

---

## Explicit non-goals — filed, not folded

| Item | Why filed | Routed to |
|------|-----------|-----------|
| R5-29 [BLOCKER, pre-existing] — `@log_admin_action` outside auth at 55/80 admin routes; unauthenticated POST writes `success=True` audit rows | Reviewer's own instruction; 55 unreviewed reorderings across untouched surface. The fix is the seam he names: a decorator-order guard, then reorder behind it — its own PR | New GH issue (P1, security-adjacent) |
| R5-10 — five success-branch `errors=` sites emit pin-rejected documents; `success = not bool(errors)` makes A2A report `success: false` for a committed buy | Reviewer's own "file rather than fold"; pre-existing; ruling should follow R5-9's | New GH issue, cross-linked to the R5-9 task |
| R5-27's conversion of `media_buy_readiness_service.py:270-305` to `resolve_flight_window_status` | `src/admin/services/` is untouched surface and it changes what operators see; this PR corrects the *inventory prose* only (RC3) | New GH issue |
| R5-32's `user:`-on-`adcp-server` alternative to chmod 666 | Reviewer files it; runtime-user change on the e2e server, own risk profile. This PR deletes the false clause only | New GH issue |
| Scoped-session nesting hazard — `BaseUoW.__enter__` (`uow.py:89`) re-enters and then closes the caller's thread-scoped session (`database_session.py:151-152`) | Engine-level semantics affecting every nested caller; RC1 removes the only known victim, the general fix (own-session UoW or `expire_on_commit=False` decision) needs its own analysis | New GH issue, citing R5-25's mechanism |
| The 864 missing-step xfails (189 sentences) | Already filed (P1) in round 3; out of scope then and now | Existing issue |
| `.feature`-scope extension of the citation hook (10 beads ids in generated features) | Generated files; the fix is upstream in adcp-req templates | Folded into the R5-27/readiness issue? No — own small GH issue |
| `pending_creatives` stamping `confirmed_at` (surfaced by `_base.py:520` contradiction) | Spec question ("has the seller committed when it holds for creatives?") — needs a spec-grounded ruling, not a drive-by | New GH issue with the pin citations to check |

---

## Child-task breakdown (ordered by dependency)

Each acceptance criterion is measurable: a count, a symbol, or a named mutation that
reddens a named test.

1. **T1 — Pin accessors fail closed** (RC4.4 + RC2.3). `required_nullable_fields` walks
   `allOf` and raises on a composed root with no derivable `required`; nullability follows
   `anyOf`/`oneOf`; add `revision_minimum()`.
   *Accept:* `required_nullable_fields("account/sync-accounts-response.json")` no longer
   returns silently-empty (raises or derives via composition — measured);
   `_PINNED_REVISION_MINIMUM` symbol no longer exists; existing 3 adopters unchanged.
2. **T2 — `_MediaBuyData` resolved-fact seam** (RC2.1-2, RC2.4-5). Depends on T1.
   *Accept:* `_persisted_revision` has exactly 1 call site; `except AdCPPersistedStateError`
   appears exactly once in `media_buy_list.py`; a seeded naive `package_config.start_time`
   renders (advisory or coerced) instead of failing the listing (new test); advisory code
   multiset gains no `SERVICE_UNAVAILABLE`.
3. **T3 — Repository seam polish** (RC2.6-7, RC6/R5-13).
   *Accept:* `_validated_status` symbol gone; a bad status via `update_status` raises naming
   the media_buy_id (test asserts the id in the message); `list_by_statuses` rejects a
   non-member at the type level; mypy clean.
4. **T4 — Approval single-writer** (RC1). Depends on T3.
   *Accept:* `resolve_flight_window_status` has exactly 2 `src/` call sites
   (`media_buy_create.py`, `media_buy_status_scheduler.py`); zero post-`execute` media-buy
   writes in the three blueprints (grep); the un-mocked `approve_workflow_step` integration
   test passes against Postgres; adapter-failure test asserts `confirmed_at IS NULL`;
   two cross-tenant route tests + the colliding-creative_id test pass; the
   `no_raw_select` allowlist entry for `workflows.py` is removed.
5. **T5 — Guard triage** (RC4.1-3, 4.5).
   *Accept:* restored inheritance guard passes at head with exactly 3 `KNOWN_OVERRIDES` and
   reddens on a synthetic 4th redeclaration; `test_architecture_one_wire_serializer_seat.py`
   does not exist; each new `_KNOWN_BAD_SNIPPETS` row drives its finder red in-process;
   `test_architecture_one_status_coercion.py` catches `models.PersistedMediaBuyStatus(...)`.
6. **T6 — Sweep repair + hook oracle** (RC3.2-3).
   *Accept:* `_EXEMPT` regex byte-equal to `origin/main`; `check_repo_invariants.py` exits 0
   over its full scan set at head; two new mutation cases + the `:79`-operand case in
   `test_architecture_repo_invariants.py` each redden under their mutation; zero `FIXME()`
   / `(, ` / `FIXME(salesagent-…)` / `FIXME(production-gap` in `src/`+`tests/`; the nine
   enumerated dangling sentences repaired.
7. **T7 — Prose corrections batch** (RC3.1 + R5-32 clause + nit-tail comment items).
   *Accept:* the enumerated claim list (this doc, RC3.1) each replaced by a measured
   statement; `git grep 'implemented three times' src/` → 0; `exceptions.py` docstring
   self-consistent (no operator-only sentence).
8. **T8 — Oracle de-vacuization** (RC5).
   *Accept:* concurrency test fails with the "did not overlap" message when writer B is
   artificially delayed under the `_bump_revision` mutation; migration test reaches
   `9b2d4f6c1a37` with the 3 cases (each reddens when its migration branch is deleted);
   `@T-UC-019-envelope-status` classifier returns hand_edited=True and survives
   `merge_feature()`; the `scope=` typo mutation now fails collection; the
   `confirmed_at`-missing partition row executes (or is retired with a stated reason).
9. **T9 — Revision refusal (spec-grounded)** (RC6/R5-9). Gate: confirm #1607 is not
   imminent; carry the full citation block from RC6 into the PR description.
   *Accept:* wire arm1 refusal graded on mcp + rest by new BDD rows; a2a row xfailed
   `ref=#1885`; #1607 CONFLICT rows unchanged (still strict-xfail); removing the refusal
   reddens exactly the new rows.
10. **T10 — File the non-goals.** Eight GH issues per the table, each carrying the evidence
    lines from this doc. *Accept:* issue numbers recorded in the PR description; the R5-29
    and R5-10 threads answered with the links.

T1–T3 are independent of T4; T5–T8 are independent of everything except where noted; T9
last (upstream feature work). Nothing here grows any ratcheting allowlist; two allowlists
shrink (no_raw_select, and `KNOWN_OVERRIDES` is a new documented-override table starting at
its measured floor of 3 with a stale-entry check).

**Net line estimate:** `src/` ≈ −150; tests ≈ −70 (guard triage) + ≈ +200 (new
integration/BDD coverage) → repo net ≈ −20 to −100, with the `src/` delta firmly negative
and every addition an executable oracle, not prose.

---

## Risk register

| Risk | Cheapest check |
|------|----------------|
| R5-25's mechanism was never run end-to-end (reviewer: "Flask's request context is the one unknown") — T4's design assumes the detached read is real | Before T4: one throwaway integration run of `approve_workflow_step` with `_EXECUTE_APPROVED_PATCH` removed against agent-db Postgres; observe the 500/DetachedInstanceError. This becomes T4's red test |
| Moving the creative gate into `execute_approved_media_buy` changes `operations.py` behavior (today it executes the adapter with unapproved creatives) | Confirm intent with the deleted `origin/main` comment ("draft … displayed as needs_creatives") and the existing webhook test (`test_admin_media_buy_reject_webhook.py:404` asserts only final state); run the admin suite after T4 |
| `_MediaBuyData.wire_status` computed even when no filter — could change which rows raise for buyer-named requests | The refuse-vs-omit branch keys on `buyer_named_rows` exactly as today; the existing UC-019 named/unnamed BDD rows are the regression net — run the module before/after with node-id diff |
| The 3 `KNOWN_OVERRIDES` might be avoidable (no redeclaration needed) — restoring the guard with overrides would then be over-engineering | 10-minute spike: delete the `media_buys` redeclaration, run the nested-serialization + alignment tests; if green, prefer option 2 for that field |
| Deleting the serializer-seat guard may be read as the R5-1 shape (guard deletion hiding a live defect) | The deletion note must carry the measurement: 284 files / 0 instances visible / assertion cannot fail, and that the alignment suite grades the wire outcome. If the reviewer still objects, the fallback is his own fix list — keyed exemption + transitive-base resolution |
| T9 conflicts with an in-flight #1607, or the upstream scenario re-authoring stalls | Check #1607 status before starting T9; if in flight, T9 converts to FILE with the citation attached (pre-agreed in this design) |
| Naive-`start_time` coercion (T2/R5-35) misreads legacy data that was not UTC | Grep all writers of `package_config["start_time"]` (3 known, all `isoformat()` of aware UTC); if any historical writer stored local time, fall back to advisory-only (no coercion) |
| `approved_at`/`approved_by` moving into `execute_approved_media_buy` couples an admin concern into a core tool | It already lives half-in (the tool writes status; routes write stamps); if the coupling reads badly in implementation, the fallback is a thin `MediaBuyApprovalService` in `src/admin/services/` owning both — same single-writer property, different module |
| The R5-18 upstream authoring (and T9's) requires an adcp-req checkout the review box lacked | Verify checkout + `--merge` roundtrip early in T8; the local `@hand-edited` marker alone is the safe intermediate state |
