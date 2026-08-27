# PR #1721 review round 2 — triage + wire-assertion recurrence root cause

Source: pr-review-queue run `050826_2116` (artifacts in
`~/.local/state/pr-review-queue/prebid-salesagent/queue/050826_2116/pr1721/`, note the
findings doc is `full-findings.md`, not `FINDINGS.md`). All 10 findings re-verified
adversarially against the live source at HEAD `951a570c1` (both worktrees are at the same
commit; verification done in `/Users/konst/projects/salesagent-1210`). Companion to (not a
duplicate of) `.claude/notes/pr1721-architecture-diagnosis.md` (round 1, D1–D5/M1–M5) —
round 1's remedies are all landed at this head; this round is the residue plus the
recurrence question.

---

## 1. Triage of the 10 Should-fix findings

Verification method: every cited file/line opened or grepped directly; spec claims
re-checked against `git -C ~/projects/adcp show v3.1.1:...`; GH issue state re-pulled via
`gh issue view`. One fix tier per review policy — no severity downgrades were warranted.

| # | Finding | Verdict | Evidence / annotation |
|---|---------|---------|----------------------|
| 1 | NotificationProofService proves nothing | **KEEP** | `src/services/notification_proof_service.py:91-106` — POST body is `{type, account_id, subscriber_id}` only; `proven = 200 <= status < 300`, body never read. Spec re-checked: `v3.1.1:dist/schemas/3.1.1/core/webhook-challenge.json` requires `[type, challenge, account_id, subscriber_id, seller_agent_url, delivery_auth, event_types]`, challenge 32–255 chars, echo required. Docstring claims "endpoint control … is proven" — it is not. Strongest finding in the batch. |
| 2 | MCP wrappers skip `adcp_validation_boundary`; new guard allowlists the gap | **KEEP** | `grep adcp_validation_boundary src/core/tools/accounts.py` → zero hits; REST has it (`api_v1.py:510,521`). `test_architecture_request_construction_boundary.py:49-56` allowlists exactly these two sites + `properties.py`, citing `salesagent-2ari`. Textbook "convention introduced then violated in the same diff". |
| 3 | Repo-pattern/session guards scoped by directory list; two fresh violations in unscanned dirs | **KEEP** | `src/admin/blueprints/accounts.py:86-97` raw `Account(...)` kwargs; `src/services/order_approval_service.py:340-342` opens `get_db_session()` for one repository read. Guard confirms `_IMPL_DISCOVERY_DIRS = ("src/core/tools", "src/core/helpers")` (`test_architecture_repository_pattern.py:36`). Identical root to round 1's D2, in the two dirs M2 didn't reach. |
| 4 | Strengthened Then-step still reads through the `list_accounts` transport-bypass | **KEEP** | `uc011_accounts.py:3130-3151` (new non-vacuous body, reads `_require_response(ctx)` — in-process) paired with `when_list_sandbox_filter` at :494 / `# TRANSPORT-BYPASS` at :507. **Additional fact the review missed:** the bypass is *allowlisted* in `test_architecture_bdd_no_direct_call_impl.py:31-36` with `FIXME(salesagent-ec0)` — a fourth beads-id citation site (see F9) and proof this bypass is a sanctioned, guard-blessed hole (see §2). |
| 5 | Three levels of type erasure on the tenant value + two new subsystems | **KEEP** | All verified: `capabilities.py:111,136` bare `Mapping`; `billing_policy.py:20` same; `adapter_helpers.py:88,101,162,200,231,267` `tenant: Any`; `capability_declarations.py:120-121` `Iterable[Any]`; `accounts.py:1309` `_proof_tuple(config: object) -> tuple`; `mypy.ini:80-85` strictness covers only accounts/capabilities. |
| 6 | Two steps reimplement wire-assertion logic fixed elsewhere in the same file | **KEEP, one annotation** | Site A verified: `then_error_with_code` (`uc011_accounts.py:3017-3021`) asserts only `getattr(_get_error(ctx), "error_code", None)` — reconstructed; sibling `then_error_code` (:1803) is wire-first with a docstring citing the policy. Site B (`uc010_capabilities.py:2081-2092`): **annotation — this is a DRY/consistency defect, not a wire-provenance defect.** `then_rejection_names` *does* read `ctx["wire_error_envelope"]`; the sin is hand-rolling the extraction (plus an undocumented `synthesized_error_envelope` fallback) instead of `assert_wire_error(..., message_substr=)`, which `transport.py`'s docstring explicitly forbids ("step definitions must not hand-roll envelope parsing"). Same fix as written; the framing matters for §2. |
| 7 | Redundant `_normalize_advisory_errors` alias | **KEEP** | `media_buy_delivery.py:153` alias, one call site :661. Trivial but real; single-tier policy applies. |
| 8 | `error-code.json` fixture pin diverges from `_refresh.py` | **KEEP** | Fixture `$id` = `/schemas/3.1.1/...`; sibling `billing-party.json` = unversioned; `_refresh.py:31,61` still lists the file under `PINNED_SHA=04f59d2d5...`. Running the documented refresh regresses the fixture (caught only after the fact by the SHA-pin test). |
| 9 | Beads-id deferral citations in three new registries | **KEEP + widen** | All three verified (`salesagent-2ari` ×3 in the new guard; `conftest.py:1298` hzlp; `:2502` cyzy). **Undercounted:** the same diff-window also carries `salesagent-ec0` (no_direct_call_impl allowlist), `salesagent-5yik`/`p99d` (guard header), `salesagent-mkso` (then_error_code docstring), `salesagent-0njj` (admin blueprint comment). Repo-wide: **138 live `FIXME(salesagent-/bd-)` citations** (18 in `src/`, 120 in `tests/` — 48 in `tests/bdd/conftest.py` alone). Fixing three sites is whack-a-mole; the real fix is proposal (c) in §3. |
| 10 | Four deferral-tracking issues open but unassigned | **KEEP** | Re-pulled 2026-08-05: #1855/#1856/#1857/#1871 all `OPEN, assignees=[]`; #1291 assigned. Process-level, but the project's deferral discipline treats unowned deferrals as real findings (review-no-sweep). One `gh issue edit --add-assignee` sweep. |

**Headline: 10/10 survive. 0 rejected, 0 downgraded.** Two annotations: F6 site B is
mislabeled (DRY, not wire-provenance), and F9 is systemic (138 sites), which changes the
right fix from "edit three strings" to "add the ratchet". Overlap note: F2 and F9 share
the guard-allowlist site (fixing F2 removes 2 of F9's citation sites); F4 and F6a are
distinct sites of the same disease — correctly kept separate.

---

## 2. Root cause: why the "assert on the wire" antipattern keeps recurring

Short version: **the guidance is not missing, not unread, and not new-this-month — the
guard that claims to enforce it structurally cannot see the most common spelling of the
violation, and pytest-bdd's text-matching lets new scenarios silently inherit ungraded
legacy steps.** This is the same etiology round 1's diagnosis note named for src/:
"the enforcement layer grades a narrower surface than the prose rules claim to govern,
and agents optimize to the graded surface." It holds for tests/bdd/ too.

### 2.1 The guidance exists and is good

- `tests/CLAUDE.md` §"Error Verification Policy" (line 370) — wire envelope is the
  authority; names `assert_envelope_shape` / `ctx["result"].assert_wire_error`; lists
  the exact forbidden patterns (`error.error_code == ...` etc.).
- `tests/CLAUDE.md` §"BDD authoring discipline (the five rules)" (line 120, commit
  `0c442ddbd`, **2026-07-15**) — rule 2: `dispatch_request` is the ONE writer of
  `ctx["result"]`/wire keys; rule 3: "Assert on the wire, through the guarded helpers —
  never hand-rolled", naming every correct helper.
- The PR author *knew* this: `then_error_code`'s new docstring (`uc011_accounts.py:1805`)
  cites the policy by name. So "prose unread" is not the explanation.

### 2.2 The graded surface is narrower than the rule — three specific holes

1. **`test_architecture_bdd_wire_discipline.py` Check B detects two helper *names*, not
   the pattern.** It flags an error-Then only if it calls `_get_error_code` or
   `_get_error_dict` (the `generic/then_error.py` helpers) with no wire reference
   (line 141, `_RECONSTRUCTED_ASSERTION_ALLOWLIST = set()`). `uc011_accounts.py` has its
   own **local** `_get_error` (line 1709) and asserts via raw
   `getattr(error, "error_code", None)` — same antipattern, different spelling,
   invisible. The empty allowlist reflects a narrow detector, not a clean estate.
   This is why F6a shipped: `then_error_with_code` passes every guard.
2. **`test_architecture_bdd_no_trivial_assertions.py` grades assertion FORM, not
   SOURCE.** `assert actual == code` is a meaningful `ast.Compare` regardless of whether
   `actual` came from the wire envelope or a lossy reconstruction. By design it cannot
   catch this failure mode — and it's the guard people think covers it.
3. **`test_architecture_bdd_no_direct_call_impl.py` sanctions the bypass that makes weak
   assertions the only working option.** A `# TRANSPORT-BYPASS` comment (or an allowlist
   entry — exactly the two uc011 When-steps behind F4, `FIXME(salesagent-ec0)`) exempts a
   When from wire dispatch, with **no compensating control**: the scenario stays
   parametrized across a2a/mcp/rest/e2e_rest and runs byte-identical in-process code four
   times. Downstream, `ctx["result"].assert_wire_error` *correctly raises* ("no
   wire_error_envelope was captured") when dispatch didn't go through the wire — so the
   honest helper is the one that breaks, and the reconstructed `ctx["error"]` path is
   the one that works. **Weak Then-steps are downstream of missing env dispatch; the
   escape hatch is cheaper than wiring the env.**

### 2.3 The binding mechanism launders old debt into new scenarios

pytest-bdd binds scenario lines to steps by *text*. The PR's new AUTH_MISSING scenario
line ("the response is an error variant with AUTH_MISSING",
`BR-UC-011-manage-accounts.feature:1007`) silently bound to `then_error_with_code` — a
step from `ccf91eb93` (**2026-04-01**, #1170), three months before the policy. The
policy's own Migration path (tests/CLAUDE.md:432-440) explicitly grandfathers
"~660 call sites, ~80 BDD steps" as not-broken with boy-scout migration — and does not
say whether *wiring a new scenario through an old weak step* counts as a "new error
test". Under that ambiguity, the path of least resistance is: write the feature line,
watch it go green, never open the step body.

### 2.4 A reachable skill actively teaches the antipattern

`qa-bdd:step-development` (plugin, `~/.claude/plugins/cache/agentic-toolkit/qa-bdd/0.3.0/`)
is the only available "write new BDD steps" guide — and its When-step REQUIRED column is
literally `except Exception as exc: ctx["error"] = exc` (SKILL.md:124), with no mention
of `dispatch_request`, `assert_wire_error`, or wire envelopes. It's a generic pytest-bdd
skill that predates/ignores this repo's harness; any agent that loads it is taught the
hand-stash pattern rule 2 forbids. Meanwhile none of the seven **project** skills
(`derive-tests`, `inspect-bdd-steps`, `verify-spec`, `surface`, `integrate`, `guard`,
`audit`) covers BDD step authoring at all — verified by reading each SKILL.md:
`derive-tests` targets the *obligation* harness (`env.call_via`, different envelope
helpers), `inspect-bdd-steps` is post-hoc grading only, `verify-spec` grades unit-suite
docstrings **and still cites the stale `3.0.0-beta.3` schema path**, and the other four
have zero BDD/harness content.

### 2.5 Classification and the load-bearing fixes

Classification: **present-but-not-graded-for-this-failure-mode** (guards), compounded by
**text-binding inheritance of a grandfathered estate** (policy migration path) and one
**actively contradictory generic skill**. Not a documentation gap.

Fixes that actually bind (in priority order):

1. **Broaden wire-discipline Check B** from the two helper names to the pattern: any
   error `@then` step that (a) calls any `_get_error*`-named callable, (b) reads
   `ctx["error"]` directly, or (c) accesses `.error_code`/`.recovery` on a non-wire
   object — with none of `_WIRE_REFERENCES` present — is a violation. Seed the
   allowlist with the legacy estate (shrink-only, per-site entries). This one change
   catches F6a at write time and converts the grandfathered estate into a visible,
   shrinking ledger instead of an invisible one.
2. **Compensate TRANSPORT-BYPASS**: extend `no_direct_call_impl` so any allowlisted/
   commented bypass forces the scenarios reaching it to be single-transport-tagged (or
   xfail-routed on the wire transports with a wiring-gap reason). Four green transport
   IDs over one in-process call is a false conformance signal — worse than one honest
   xfail.
3. The skill/CLAUDE.md/hook items in §3.

---

## 3. The three proposals — concrete recommendations

### (a) New skill: YES — net-new, none of the existing skills is 80% of it

Confirmed by reading all seven project SKILL.mds (see §2.4): capabilities
(a) scenario-vs-pinned-spec, (b) wireability, (c) step authoring with the correct
helpers, (d) domain-env wiring are respectively PARTIAL-wrong-target, NONE, NONE-or-
wrong-harness, NONE. `derive-tests` is the right *structural template* (gate → read env
API → gold examples → verify) but for the other harness. Do NOT extend it — the two
harnesses' helper vocabularies (`assert_envelope` vs `assert_wire_error`) would blur.

**New project skill `.claude/skills/author-bdd-steps/SKILL.md`** — a wiring guide with
symbol refs (per the established skill-design convention), normative text stays in
tests/CLAUDE.md. Section outline:

1. **Authority gate.** Locate the scenario in the generated `BR-UC-*.feature`; the
   generated feature + pinned schema/storyboard are the authority. Spec-cite before
   coding: `git -C ~/projects/adcp show v3.1.1:dist/schemas/3.1.1/<path>` (v3.1.1 = the
   pin; confirm no in-flight bump). Cites CLAUDE.md's spec-grounding gate.
2. **Wireability gate (HARD STOP).** Which domain env serves this scenario? Does its
   `dispatch_request` route (`tests/bdd/steps/generic/_dispatch.py`) carry this tool?
   If not: wire the env FIRST, or single-transport-tag + xfail-route with a wiring-gap
   reason. **NEVER add a `TRANSPORT-BYPASS` in new code** — cite F4/`salesagent-ec0` as
   the cautionary precedent.
3. **Binding audit.** Before writing a step, check whether the scenario's wording
   already binds to an existing step (grep the step text). If it does, READ that step's
   body and grade it against rule 3; if weak, upgrading it is part of THIS change (the
   policy's boy-scout rule is mandatory, not optional, when a new scenario rides an old
   step). This closes the §2.3 laundering channel procedurally.
4. **Authoring table.** Given → env-owned setup via env methods/factories (never
   hand-stash wire data in ctx); When → `dispatch_request` only (the ONE writer of
   `ctx["result"]`/`ctx["wire_response"]`/`ctx["wire_error_envelope"]`); Then-error →
   `ctx["result"].assert_wire_error(code, recovery=..., message_substr=..., field=...)`;
   Then-success → `wire_field(ctx, ...)` / `wire_dict(ctx)`
   (`tests/bdd/steps/_outcome_helpers.py`). Explicit FORBIDDEN column: local
   `_get_error*` helpers, `ctx["error"]` reads, hand-rolled envelope `.get()` chains,
   `model_dump()` round-trips.
5. **Dormancy proof.** Run the touched slice serial with `-rxX`; sub-second wall time /
   "No harness wired" / `StepDefinitionNotFoundError` are the tells (rule 5).
6. **Antipattern ↔ guard map.** Each antipattern names the guard that grades it and the
   escalation when a guard is silent (extend the guard, don't celebrate the pass).

Also (small, same theme): fix `verify-spec`'s stale `3.0.0-beta.3` paths, and either
uninstall/supersede `qa-bdd:step-development` for this project or note in the new skill
that it must not be followed here (its When-step rules contradict rule 2).

### (b) CLAUDE.md placement: keep tests/CLAUDE.md normative; add a thin tests/bdd/CLAUDE.md

tests/CLAUDE.md already contains the right normative content (five rules + Error
Verification Policy) — do not move or duplicate it (drift risk). But tests/bdd/ is a
large distinct subtree, agents working in it load the nearest CLAUDE.md, and today the
BDD-specific conventions are split across the root guard table, tests/CLAUDE.md, and
`xpass-graduation.md`. Add a **pointer-only `tests/bdd/CLAUDE.md` (~30-40 lines)**:

- Authority hierarchy (generated BR-UC-*.feature ← pinned schema/storyboard), one line.
- The five rules by reference (link tests/CLAUDE.md §120), not restated.
- The ONE thing that exists nowhere today: a **"which helper for which situation"
  table** — error Then / success Then / Given setup / When dispatch → exact symbol +
  file, plus the FORBIDDEN spellings (local `_get_error*`, `ctx["error"]`, hand-rolled
  envelope parsing).
- Where domain-env wiring lives (`tests/harness/` env classes, `_dispatch.py` routes,
  `_realize.py` for e2e) — pointers.
- Pointer to `xpass-graduation.md` and the dormancy check.

### (c) Pre-commit hook for beads-id citations: yes, as a pre-push count-ratchet — NOT in check_repo_invariants.py, NOT commit-stage

Facts that constrain the design (all verified):

- Live violations: **138** `FIXME(salesagent-|bd-)` in `src/`+`tests/` (18 in src/,
  120 in tests/; `tests/bdd/conftest.py` alone has 48). Any binary hook fails today.
- Commit-stage hook count is **exactly 12 = the D27 ceiling**
  (`test_architecture_pre_commit_hook_count.py`, `COMMIT_STAGE_MAX = 12`). A new
  commit-stage hook is blocked outright.
- `check_repo_invariants.py` is binary pass/fail with no baseline flow — wrong shape
  for a 138-violation estate; adding it there would either fail every commit or need a
  bolted-on baseline that its structure doesn't support.
- The four existing count-ratchets (`type-ignore`, `ruff-complexity`,
  `mypy-untyped-defs`, `admin-raw-session`) all run at **`stages: [pre-push]`** and
  share `.pre-commit-hooks/count_ratchet.py` (create / `--update-baseline` / compare /
  auto-lower). Pre-push hooks don't count toward D27.

**Design — `.pre-commit-hooks/check_fixme_citation_count.py`, mirroring
`check_admin_raw_session_count.py` line-for-line:**

- Pattern: `re.compile(r"(?:FIXME|TODO)\(\s*(?:salesagent|bd)-")` over `*.py` under
  `src/` and `tests/` (include TODO — same disease, 1 extra hit today).
- Baseline: `.fixme-citation-baseline`, JSON via `count_ratchet.json_baseline_io`, with
  **per-tree keys** `{"src_fixme_beads": <n>, "tests_fixme_beads": <n>}` seeded from the
  live counts at implementation time (verify then; ~18/~120). Per-tree keys narrow the
  count-ratchet's inherent swap-masking (a new violation in src/ can't hide behind a
  removal in tests/).
- Behavior: fail on any key increase; auto-lower on decrease; `origin/main` soft-land
  rule for keys main doesn't carry yet — all inherited from `count_ratchet`.
- Registration: `.pre-commit-config.yaml`, `stages: [pre-push]`, `files:` pattern
  matching the four siblings (source trees + configs + own baseline), placed in the
  existing "Count ratchets" block. **Commit-stage count stays 12; D27 untouched.**
- Message on failure: quote the CLAUDE.md rule ("GH issue/PR number, never a local
  beads id — beads ids don't resolve for outside contributors") and the fix (file the
  GH issue, cite `FIXME(#NNNN)`).

Known residual (state it, don't hide it): a count-ratchet is swap-blind *within* a tree
(remove one old + add one new = net zero). The site-anchored layer already exists for
the registries that matter most — this PR's own escape-hatch guard asserts citations
never match `salesagent-` (round 1 M5.4); finding 9's fix extends that to the
request-construction guard's allowlist and conftest xfail reasons. Ratchet for the
long tail, site-anchored guard assertions for the registries: that division matches how
the project already splits `check_code_duplication` (count) vs guard allowlists (sites).

If a stronger-than-count enforcement is wanted later, the project-native upgrade is a
pytest arch-guard (`test_architecture_fixme_citations.py`) with a per-site allowlist and
stale-entry detection — zero hooks, runs in `make quality` — but that's an upgrade path,
not a blocker for shipping the ratchet now.
