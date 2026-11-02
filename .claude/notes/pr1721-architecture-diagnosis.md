# PR #1721 review round 1 — architecture-level diagnosis

Epic: salesagent-c0ia (R1-1..R1-9). Source: pr-review-queue run 050826_0859, all findings
re-verified at PR head 8297ea85f. This note is the etiology and the coherent remedy; the
beads carry the site-level detail. Every implementer reads this BEFORE touching an R1-x bead.

Verdict up front: the 9 findings are not 9 diseases. They are **five disease classes**, and
four of the five share one root: *the enforcement layer grades a narrower surface than the
prose rules claim to govern, and agents (human or AI) optimize to the graded surface.* Every
violation cluster sits exactly where no guard, no mypy strictness, and no ledger check was
looking. The remedy is therefore paired in every case: fix the sites **and** widen the graded
surface so the fixed shape is the new path of least resistance.

---

## 1. Disease classes and their root causes

### D1. The advisory-error lane has no owned, capable combinator (R1-3, R1-4, R1-9's site)

The RAISED-error lane is fully architected: typed `AdCPSalesAgentError` → boundary translation →
`WIRE_STANDARD_CODES` → envelope, guard-enforced end to end. The ADVISORY lane (`errors[]`
inside a success response) has only a half-built primitive:

- `normalize_advisory_errors()` (src/core/exceptions.py:254) **drops `field`, `suggestion`,
  and `details`** — it rebuilds each Error with only code/message/recovery. Its three current
  callers (capabilities, media_buy_delivery, creatives/listing) only ever build code+message
  advisories, so the loss is latent there — but accounts.py's advisories carry pinned
  `field`/`suggestion` content (scenarios assert them), so the shared primitive **could not
  carry what accounts.py needed**.
- Its allow-set lags the pinned enum: `BILLING_NOT_SUPPORTED` is a real v3.1.1 wire code but
  absent from `WIRE_STANDARD_CODES` (#1602), so the normalizer would rewrite it to
  `UNSUPPORTED_FEATURE`.

Given a shared primitive that both *loses required content* and *corrupts a required code*,
hand-rolling was the rational local move — hence 6 duplicated gate blocks, ~9 raw `Error()`
sites, a silent normalizer bypass, and the drift the duplication then produced
(`_check_domain_validity` missing `recovery=`; `_validate_notification_configs` naming).
R1-4 (capability_declarations' duplicated claimed-minus-backed check) is the same root at
config-validation time: a recognized combinator used twice with no extraction, because no
DRY pressure exists below the R0801 textual threshold for semantically-identical blocks.

**Correction to the review's fix line:** "route accounts.py through
`normalize_advisory_errors` like its siblings" is NOT viable as written — it would strip the
pinned `field`/`suggestion`/`details` from every accounts advisory. The normalizer must be
extended first (see M1). The review's two options collapse into one once this is seen.

### D2. Layer boundaries are enforced by file *list*, not by layer (R1-5, R1-6)

`test_architecture_repository_pattern.py` scans a hand-maintained `IMPL_FILES` list of 17
files. Consequences found in this PR:

- `src/core/tools/accounts.py` — the largest new tools module — **is not on the list at
  all**. Nothing graded it.
- `src/core/helpers/` is not scanned, so moving `get_db_session()` one call frame into a
  helper makes the violation invisible. `_read_mock_test_behavior`'s docstring *documents
  the loophole as the design* — the guard taught the workaround. Four copies of the identical
  session-open + `AdapterConfigRepository(...).find_by_tenant()` block now exist there.
- There is **no reverse-direction rule**: nothing forbids a repository file from hosting
  business logic. `IdempotencyPosture`/`check_bounds()`/`to_sdk_union()` landed in
  `repositories/idempotency_attempt.py` (importing SDK protocol *response* types into the
  repository layer) even though this same PR built the correct template
  (`src/core/billing_policy.py`, 34 lines, zero ORM imports) and the class docstring *cites
  that template*. The wrong placement type-checked, guard-passed, and shipped.
- Same root for `_new_account_row`: `AccountRepository` exposes no factory (`create_from_*`
  / `build_row`), so natural-key + field assembly accreted in the tools layer — the exact
  shape CLAUDE.md Pattern #3 names, in a file no guard reads.

### D3. Type erasure is invisible under the current mypy posture (R1-7)

`mypy.ini` sets `disallow_untyped_defs = False` globally and nothing restricts explicit
`Any`. So ~20 `entry/existing/repo: Any` parameters — written by getattr-driven duck code
under wave pressure — were *cheaper to write than the correct types* and produced zero
signal, even though the correct union is already spelled in the same file
(`list[SyncAccountInput | SettingsUpdateAccountInput]`, accounts.py:1875). The one bug class
`_FIELD_POLICY` exists to prevent (handing one arm's function the other arm's entry) is
exactly the class `Any` re-opens. `_build_geo_postal_areas(targeting_caps: object | None)`
+ `getattr(..., False)` is the same erasure making a `_POSTAL_AREA_TABLE` typo silently
`False` instead of a mypy error.

### D4. Dormancy and xfail bookkeeping are hand-maintained strings with no classifier (R1-2)

The strict-xfail ledger records a *reason string* at write time and nothing ever re-grades
whether the reason is still (or ever was) true. `T-UC-010-main`'s entry claims a production
gap; the actual failure is a missing Given step — a **test-wiring** gap. The `@T-UC-010-v31-
account-sandbox` outline is dormant (its Given has no bound step anywhere) yet carries an
auto-copied citation to the *wrong issue* (#1855 instead of #1856). Root: the pipeline can
express only one failure category ("production gap") and nothing distinguishes
missing-step-definition dormancy from a real graded gap, so mask-type errors survive six
reviewer passes' worth of waves. This is the residual of the same disease the earlier
stale-citation guard and pass-count gate partially treated.

### D5. Provenance pointers are unverified strings (R1-1, R1-8)

Fixture provenance (`@04f59d2d5`), suggestion texts, and allowlist ticket citations are all
prose with no verifier. The fixture self-describes as "the pinned enum" while diverging from
the tag it claims (4+ drifted suggestion strings); the escape-hatch registry's new entries
cite beads ids an outside contributor cannot resolve, against the registry's own established
convention (#1418 precedent / no-ticket-for-structural precedent). Same class, two surfaces:
a claim of ground truth with no hash, and a claim of tracking with no resolvable target.

(R1-9, the eager f-string, is not a disease of its own — it is D1's module written without
the repo's logging idiom and with no lint rule selected for it; ruff `G` rules are not in
`select` and there are ~1,206 pre-existing eager-f-string log sites.)

---

## 2. The coherent remedy — T1, must land in this PR

Six measures. Each fixes the root with bounded scope, and each carries its enforcement
change so regression is graded, not requested.

### M1 — One advisory-error lane (governs R1-3, R1-4, R1-9's file)

1. **Extend `normalize_advisory_errors()`** (src/core/exceptions.py) to preserve `field`,
   `suggestion`, `details` verbatim, and to *fill* `recovery` via `advisory_recovery_for`
   only when unset — never clobber an explicitly-set recovery. (This automatically repairs
   `_check_domain_validity`'s missing `recovery=` at assembly time; still set it at the
   source for honesty.)
2. **Promote `BILLING_NOT_SUPPORTED` into `_SPEC_SUPPLEMENT_CODES`** with a pinned-enum
   citation (verify recovery classification via
   `git -C ~/projects/adcp show v3.1.1:dist/schemas/3.1.1/enums/error-code.json`). This
   closes the #1602 half and disarms the silent-rewrite trap. `UNSUPPORTED_PROVISIONING` is
   already promoted.
3. **Route accounts.py through the extended normalizer** at its result-assembly choke points
   (the `errors=` argument of `_build_failed_result` / advisory assembly), exactly like its
   three siblings. Delete the "universal, except accounts.py" ambiguity instead of
   documenting it.
4. **Extract the gate-runner** in accounts.py:
   `_first_gate_failure(gates: Iterable[Callable[[], list[Error] | None]]) -> list[Error] | None`
   — both arms build their gate list and call it once; the six check→build→short-circuit
   blocks collapse. Rename `_validate_notification_configs` → `_check_notification_configs`
   (drift closed by construction, not convention).
5. **capability_declarations.py**: extract
   `_reject_unbacked(claimed, backed, *, field, noun, tracked_by=None)` and call it for
   protocols and specialisms; the roll-up-coherence block stays hand-written (genuinely
   different shape — graded against the *emitted* set).
6. Fix `_record_degradation`'s f-string → `logger.warning("Could not get %s: %s", what, exc)`
   (R1-9) while in the file.

### M2 — Boundary redrawn around the layer, not a file list (governs R1-5, R1-6)

1. **One session-owning read in the repository layer**: add a module-level
   `read_adapter_config(tenant_id: str) -> AdapterConfig | None` to
   `src/core/database/repositories/adapter_config.py` (infrastructure layer — the guard's
   legitimate session home) that opens exactly one session and returns the row detached/
   plucked as needed. All four `adapter_helpers.py` sites call it;
   `adapter_helpers.py` ends with **zero** `get_db_session` imports and its docstrings stop
   describing the loophole.
2. **Relocate the idempotency policy family** — `IdempotencyPosture`, `check_bounds()`,
   `to_sdk_union()`, `get_idempotency_posture()`, and the TTL bound constants — to a new
   `src/core/idempotency_policy.py` on the `billing_policy.py` template. The repository file
   keeps only CRUD and imports `DEFAULT_REPLAY_TTL` *from* the policy module (policy never
   imports the repository). Update the two capabilities.py import sites.
3. **`AccountRepository.build_row(...)`** — a pure, non-persisting factory owning
   natural-key assembly and the `_FIELD_POLICY`-derived field copy now in
   `_new_account_row`; both call sites (live create, dry-run preview) go through it. The
   existing single-construction-site guard test retargets to the repository method.
4. **Guard hardening (same PR, same guard file)**:
   - Replace the hand-maintained `IMPL_FILES` list with a discovery glob over
     `src/core/tools/**/*.py` + `src/core/helpers/**/*.py` (after 1–3 the
     `IMPL_SESSION_ALLOWLIST` stays empty — no allowlist growth).
   - Add a reverse-layer check in the same file: modules under
     `src/core/database/repositories/` may not import from `adcp.types.*` protocol/response
     modules (AST import scan; crisp, zero false positives today after M2.2).

### M3 — Types restored, and strictness that keeps them (governs R1-7)

1. `SyncEntry = SyncAccountInput | SettingsUpdateAccountInput` module alias; every `entry`
   param typed with it; `existing`/`db_account: DBAccount | None`; `repo: AccountRepository`;
   the legitimately-polymorphic serializers typed honestly
   (`BusinessEntity | Mapping[str, Any] | None`), not `Any`.
2. `_build_geo_postal_areas(targeting_caps: TargetingCapabilities | None)` with direct
   attribute access — a `_POSTAL_AREA_TABLE` typo becomes a mypy error.
3. **Enforcement**: per-module mypy strictness in `mypy.ini`:
   `[mypy-src.core.tools.accounts]` and `[mypy-src.core.tools.capabilities]` with
   `disallow_any_explicit = True`. Binding, zero new test code, scoped to the modules this
   PR owns (repo-wide strictness is T3).

### M4 — Unmask the acceptance mechanism, and classify dormancy vs gap (governs R1-2)

1. Add the Given to `T-UC-010-main` via `env.configure_tenant_field("account_sandbox",
   False)` (mechanism already in-diff at uc011_accounts.py:3202).
2. Bind `Given the tenant account is configured for {boundary_point}` and wire
   `@T-UC-010-v31-account-sandbox` into `_UC010_WIRED_TAGS` with accurate reasons; verify
   each row fails on the *real* gap before recording any xfail.
3. Correct the #1855 → #1856 citation; remove the `T-UC-010-main` and `sandbox_disabled`
   xfail entries once green for the right reason (shrink-only). Expected honest stopping
   point: `reporting_delivery_methods` xfail pending #1291.
4. **Enforcement**: extend the conftest strict-xfail tripwire to *classify* the failure it
   is excusing — a strict-xfail whose underlying failure is a pytest-bdd
   missing-step-definition (or a Given-side setup error) is a MISCLASSIFIED entry
   (dormancy, not production gap) and must fail loud with that message. This is the check
   that would have caught R1-2 at write time and it is a bounded conftest function, not a
   new guard file.

### M5 — Provenance gets a verifier (governs R1-1, R1-8)

1. Re-vendor `tests/fixtures/adcp_schemas_pinned/enums/error-code.json` **verbatim** from
   `v3.1.1`; fix both `@04f59d2d5` citations in `tests/harness/transport.py` to `v3.1.1`
   (matching the exceptions.py correction already in this diff).
2. Add a completeness pin: a test asserting the SHA-256 of the vendored fixture equals a
   recorded constant taken from the v3.1.1 blob (the same pattern we shipped upstream in
   adcp-client-python #980). Drift becomes a red test, not a review find.
3. Escape-hatch registry: file the GH issue for `set_adapter_channels` (draft first — post
   only on explicit "GH approved"; dedup-search GH before drafting), cite it; drop the
   beads refs on the three structural entries per the registry's no-ticket precedent; fix
   the duplicated comments in `tests/harness/capabilities.py`.
4. **Enforcement**: a small assertion in the escape-hatch guard (it already parses these
   entries) that citation strings never match `salesagent-` — the CLAUDE.md rule
   ("GH issue/PR number, never a local beads id") becomes graded.

### M6 — R1-9 site fix

Folded into M1.6. No standalone commit.

Suggested execution order: M1 → M2 → M3 (same files, least churn if sequenced) → M4 →
M5. Full gate (`saci run` from the worktree, then `./run_all_tests.sh` as final authority)
after M3 and again at the end; pass-count comparison against the pre-fix baseline per the
semantic-merge layer-5 discipline.

---

## 3. T2 — named follow-ups (legitimate under the "fully addressed" bar)

- **ruff G004 as an ADR-009 count-ratchet.** 1,206 pre-existing eager-f-string log sites
  make repo-wide enablement a churn bomb; the ratchet machinery
  (`.ruff-complexity-baseline`) already exists for exactly this shape. Adding G004 to the
  ratchet set freezes the count and lets it shrink. Out-of-PR because it touches the
  baseline machinery and 60+ unrelated files' worth of debt accounting — a genuinely
  separable, unconnected mechanism change. (The in-PR obligation — the one *new* violation
  — is fixed by M1.6.)
- **Per-tenant idempotency posture persistence** — already deferred with a reasoned note
  (salesagent-rldj Q3); M2.2 moves the seam to the right layer without changing the
  deferral.
- **#1602 remainder** — M1.2 closes the `BILLING_NOT_SUPPORTED` half; whatever #1602 still
  tracks beyond it stays in that issue.

## 4. T3 — considered and rejected (rabbit-hole)

- **Repo-wide f-string logging sweep** — 1,206 sites, zero behavior change, poisons the PR
  diff; the ratchet (T2) achieves containment.
- **Generalizing the gate-runner into a cross-tool framework** — accounts.py is today's
  only multi-gate emitter; a framework for one consumer is speculative abstraction, the
  inverse failure mode of D1.
- **Import-following (call-graph) analysis in the repository guard** — heavy machinery;
  the discovery glob over tools/+helpers/ closes the actual loophole for a fraction of the
  cost and complexity.
- **Full UoW/dependency-injection of sessions through capabilities `_impl` signatures** —
  would ripple every transport wrapper for no behavioral gain now; the admin UoW epic
  (salesagent-ctmz, GH #1853) owns the broader migration and this PR must not annex it.
- **Repo-wide `disallow_any_explicit`** — thousands of legacy hits; per-module scoping (M3)
  covers where the disease actually presented, and the module list can grow file-by-file.
- **Auto-generating xfail reasons from failure introspection** — over-engineering; the M4.4
  classifier (dormancy vs gap) is the load-bearing 20% of that idea.

## 5. Enforcement story per T1 measure

| Measure | Regression graded by | New or extended? |
|---|---|---|
| M1 normalizer + lane | existing advisory structural-guard comments + `test_architecture_no_error_flattening`; add one assertion that response `errors=` assemblies in tools route through `normalize_advisory_errors` | extended (existing guard file) |
| M1 gate-runner / `_reject_unbacked` | R0801 duplication ratchet (count drops; baseline shrink-only) | existing |
| M2 session reads | repository-pattern guard with **discovery glob** (tools/ + helpers/), allowlist stays empty | extended |
| M2 policy relocation | new reverse-layer check: repositories/ must not import adcp protocol types | extended (same file) |
| M2 build_row | existing single-construction-site guard retargeted to the repo method | existing, retargeted |
| M3 typing | `disallow_any_explicit` per-module in mypy.ini (runs in `make quality`) | config, binding |
| M4 BDD | strict-xfail tripwire classifier (dormancy ≠ production gap) + existing stale-citation guard + shrink-only ledger rule | extended (conftest) |
| M5 fixture | SHA-256 completeness pin test | new (tiny) |
| M5 citations | escape-hatch guard rejects `salesagent-` in citations | extended |

No new pre-commit hooks anywhere — everything rides existing pytest guard files, conftest,
or mypy/ruff config, so the D27 hook ceiling is untouched, and no allowlist grows.

## 6. Process countermeasure (the meta-question)

Why did our own executors write `Any`-typed params, session-opening helpers, and raw ORM
kwargs with CLAUDE.md forbidding all three? Because **prose does not bind agents; gates
do** — and every one of these violations sits in a zone the gates did not grade: accounts.py
absent from `IMPL_FILES`, helpers/ unscanned, repositories/ unchecked in reverse, `Any`
legal under lenient mypy, and the bead directives (deliberately) front-loaded BDD/spec
discipline, which is where the *graded* obligations were. One executor even wrote the
loophole into a docstring as design intent — the honest signal that the guard, not the
author, defined the boundary. The review gate did catch all of it, but at end-of-branch
cost instead of write time.

The cheapest countermeasure that actually binds is therefore **the guard/config changes
already inside M2/M3/M4** — they convert the three violated prose rules into graded rules
precisely where the violations clustered, at near-zero ongoing cost. One durable process
line is worth adding to the bead-directive template on top: *"If your change creates a
module in `src/core/`, it is guard-covered by default (discovery); weakening or opting out
of a guard requires editing the guard file itself, which the diff must show."* That makes
evasion visible in review instead of silent. No new instructions beyond that — adding more
prose to CLAUDE.md would repeat the failure mode this PR just demonstrated.
