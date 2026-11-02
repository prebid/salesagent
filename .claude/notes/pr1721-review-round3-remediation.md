All load-bearing claims I spot-checked hold in the tree (reader at `transport.py:31`, `T-UC-010-main` xfailed at `conftest.py:433` and absent from the e2e ledger, `_UNBACKED_BLOCKS` at `capability_declarations.py:56`, sandbox literals, UC-018 gate at `conftest.py:3944`, the harness declarations, `BaseUoW.__exit__` at `uow.py:95`, admin mint at `blueprints/accounts.py:79`, #1547's `pinned_error_code_metadata()` at its `pinned_schema.py:33`). The plan follows.

---

# PR #1721 remediation plan

**Branch:** `feature/spec-gaps-1210` @ `acdf61ab3`. **Claims:** Closes #1210, #1521, #1592. **Reality:** the deliverables are substantially there; the PR also built a second write path, five hand-maintained enforcement registries seeded from its own head, ~35 guards of uneven quality, and several claims (in prose and on the wire) that its own code does not back. The remediation below is net-negative in lines everywhere except one migration test, a handful of BDD step definitions, and a few scenario rows. Nothing here inflates the PR; most of it deletes.

## 0. The gating decision: #1547 lands first

Ratified. #1547 owns the substrate both PRs contend on — the auth exception classes, the A2A `on_message_send` failure arms, the boundary sanitization policy, the harness synthesized-envelope contract, and the pinned-fixture provenance mechanics — and on all seven contested concerns its shape is the one this PR's own remediation is being told to adopt anyway. Landing #1721 first would bake in the repurposed auth classes (every pre-existing `raise AdCPAuthenticationError` silently becomes terminal `AUTH_INVALID`), the raw `str(exc)` in JSON-RPC InternalError messages, the synthesized-envelope fallback on wire transports, and a fixture tree whose provenance claims are false — all of which #1547 would then revert file-by-file.

Practical flow: **phases 1–3 below proceed now** (they live in hunks #1547 does not own; the one shared file, `src/core/exceptions.py`, is touched in different functions and rebases mechanically). **Phase 4 is the rebase**, done when #1547 merges and before #1721 merges. Section 4.4 lists exactly what is deferred to it, plus the fallback if #1547 stalls.

## 1. Rulings

Where agents disagreed with the findings, or findings disagreed with each other:

1. **Finding 8 — prescription rejected, honesty fixes kept.** The read-path `CONFIGURATION_ERROR` raise (`capabilities.py:457` → terminal) is spec-correct per v3.1.1's error-code definition and matches the recorded owner STRICT decision. Routing it through `_record_degradation` would emit `SERVICE_UNAVAILABLE` (transient, retry-with-backoff) for a fault retrying can never fix — a quiet failure this codebase bans. Production stands. What changes: the two scenario titles claiming "rejected at config time" (`local-uc010-declaration-backing.feature:39,64`), the PR-description sentence claiming a config-load gate exists, and one comment on `from_tenant` binding the future #1856 write surface to the same classmethod.

2. **Finding 13 — the original finding's rooting prescription is inverted; the agent's correction stands.** The graded compliance contract pins **entry-relative** pointers (`notification-config-event-scope.yaml` grades `accounts[0].errors[0].field == "notification_configs[0].event_types[0]"`). Prefixing `accounts[{i}].` as the finding directed would fail the graded contract. The two request-rooted **new** gates (`accounts.py:714`, `:1004`) are the deviants and drop their prefixes. The code disjunction ("INVALID_REQUEST or VALIDATION_ERROR") is the spec's own delegation to the seller — the fix is production picking one code (VALIDATION_ERROR for all value-class failures, per the PR's own rule at `src/app.py:242-250`) and the scenarios pinning it, not the harness or-step.

3. **Finding 3 — replace, do not revert.** Reverting dry_run restores two shipped bugs (settings-update persisting under `dry_run=true`; pre-write billing echo — the latter still live in #1547 at its `accounts.py:549-563`, which is independent evidence the shadow-state disease is real). The behavior is spec-required (`sync-accounts-request.json#/properties/dry_run`); the spec is silent on mechanism; rollback-only UoW satisfies parity by construction. This is the single largest simplification available (~330 lines and four guard files deleted) and it must happen before the typing work in the same file, so we delete code rather than type it.

4. **Finding 2 vs Finding 14e — one fix, owned by finding 2's package.** The `T-UC-010-main` split is done once; 14e's entry defers to it.

5. **Finding 5 vs Finding 6 on list_creatives pagination — behavior change deferred, false record fixed now.** Converting A2A/REST to accept spec `pagination`/`sort` is a buyer-visible behavior change that would then obligate wiring the dormant UC-018 pagination rows — real work on a non-titled surface. Defer both to one ticket. But the parity-guard allowlist reason ("needs a pinned-spec decision") is factually false — the pin decided; `page`/`limit`/`sort_by`/`sort_order` do not exist in 3.1.1 — so the entry text is corrected now and cites the ticket. The rows this PR **did** change behavior on (media_buy_ids merge, projection flags) get wired now (finding 6).

6. **Finding 7 — sub-claim inverted, hazard stands.** The two readers do **not** disagree on no-tenant today (`_check_sandbox_capability` rejects; capabilities omits the block). They agree by accident of two independently written literals; the resolver fix stands, the drama doesn't.

7. **Finding 14c — split the declarations by cost.** `set_supported_versions` and `set_idempotency_posture` cover surfaces this PR ships (#1592's capabilities), and the write-through pattern exists 40 lines above (`declare_capabilities` → `configure_tenant_field`, `tests/harness/capabilities.py:101-120`); `get_idempotency_posture(tenant)` already reserves the parameter (`idempotency_policy.py:93`). Implement those two reads if they fit the declarations store without joining `_UNBACKED_BLOCKS` (they should — they are genuine seller declarations); if either needs its own persistence design, keep the declaration with an owned GH issue in the reason string. `set_adapter_channels` mirrors `_realize_targeting_capabilities` ten lines up — do it (that is #1871's ask). `break_tenant_config_db` (DB fault injection) and `set_build_version` (process metadata) keep declarations with issue citations.

8. **Finding 6's honest boundary accepted.** UC-001/UC-007 have no test module, no harness, and in UC-001's case no scenario row — building two harnesses to grade opportunistic adjacent fixes is exactly the inflation to refuse. The fixes stay; the PR description records them as **ungraded** per the spec-grounding gate; two tickets carry the harnesses.

9. **Finding 16's count quibble (16 vs 14/30 files) is immaterial**; the substance — identifier joins, re-rolled scaffolding, a tautological meta-test, two prefixes — is fully verified.

10. **Finding 4's framing correction accepted**: `uc011_accounts.py` is modified, not new; the 46 pre-existing `_require_response` Thens and the pre-existing list-bypass are **not** this PR's to convert. They land as shrink-only allowlist entries under the widened guard, tracked by #1880.

11. **The comparison's auth finding is real and uncovered by the 16.** #1721 repurposed `AdCPAuthenticationError` to terminal `AUTH_INVALID` (`exceptions.py:603-621`) with hand-written suggestion text unregistered in the suggestion-conformance guard — the exact drift class that guard bans, in the same PR that made the fixture claim v3.1.1 authority. Resolution is #1547's classifier-by-header-presence shape; remap at rebase (section 4.4).

## 2. The design faults under the sixteen findings

**R1 — dry_run modeled as a business-logic branch instead of a transaction scope.** Introduced. Symptom: finding 3 (shadow identity map, two byte-identical write arms, four guards pinning copies together, a creatives twin, and a guard-excuse falsified in the same diff). Fix: `dry_run` keyword on `BaseUoW` (`uow.py:50`, decision point already at `__exit__:95`); one write path; rollback instead of commit.

**R2 — policy and spec data re-encoded per consumer instead of resolved from one module.** Introduced — and the PR itself built the correct pattern (`billing_policy.py`: one resolver read by both the claim and the enforcement) and then didn't use it for the next three values. Symptoms: finding 7 (four sandbox `True` literals), finding 9 (recovery classification encoded three ways, disagreeing on 21 codes), finding 2 (the webhook-reporting decision answered "no" by `_UNBACKED_BLOCKS` and "yes" by the live scheduler), and finding 10's duplications (adapter-resolve preamble ×4, declaration ternaries ×5, sorted-union ×2, degradation try/except ×5). Fix: one resolver per policy value; pin-parity tests where the value is spec data; a resolve-once `AdapterContext`.

**R3 — transport edges re-enumerate the request model; the parity guard grades the wrong invariant.** The per-transport hand-enumeration disease is pre-existing; this PR built the cure (`select_request_fields`, `schema_helpers.py:276`), applied it at 5 of N sites, then hand-enumerated in the very handlers it was editing, and shipped a guard that compares transports to each other (union) instead of to `RequestModel.model_fields`, with a naming-convention join that already misses one Body class. Symptoms: findings 5, 6, and the 18-name `A2A_WIRE_INTEGER_FIELDS` hand-list. Fix: apply the PR's own seam at every touched edge; guard against the model; derive the integer set from SDK model fields.

**R4 — the test harness has no mandatory single-owner seams, so grading downgrades silently.** Pre-existing dual-stash design, aggravated here: three envelope regions with no sanctioned accessor, a module-level `_error_details` invisible to the PR's own discipline guard, a read-back leg that bypasses the transport entirely, a pinned-enum reader living in the harness instead of `pinned_schema.py`. Symptoms: findings 4 and 1, plus the synthesized-envelope lane #1547 removes. Fix: one guarded primitive per region; one reader module; guard blind spots closed.

**R5 — enforcement machinery authored ad hoc: identifier joins, re-rolled scaffolding, head-seeded ratchets, one-direction registries.** Introduced at scale (35 guard files in one pass). Symptoms: finding 16 wholesale, finding 14a/b's seed mechanics (a baseline hook that grandfathers the same PR's new debt), the dormancy guard checking only map→feature and never feature→map, the tautological meta-test. Fix: `_architecture_helpers` as the mandatory floor plus a scaffolding meta-guard; artifact joins; merge-base seeding.

**R6 — BDD behavior knobs as in-process monkeypatches, e2e legs amputated by registry.** The monkeypatch-seam pattern is pre-existing; the registries that codify the amputation (`_NO_E2E_REST_TAGS`, five new `EXPECTED_UNSUPPORTED_DECLARATIONS`, the `T-UC-005-main` strict=False route, 13 new `_XFAIL_TAGS`) are this PR's. Symptoms: finding 14, finding 2's xfail gate, finding 16's unmapped dormant tags. Fix: write-through `realize_e2e` via tenant config (the pattern already proven twice in the same file); transport-observable Thens; per-row selective xfail with citations instead of family-wide parking.

**R7 — the PR's new mechanisms have no type contracts, and the repository is typed in storage payloads.** Introduced. Symptoms: findings 12, 11, 13 — one contract-hardening campaign in `accounts.py`/`account.py` (stringly modes, magic-string kinds, `dict[str, object]` payloads with three downstream `cast()`s, anonymous 4-tuples ×3, `hasattr` union discrimination ×4, seven gates each privately choosing code/rooting/wording, an admin route hand-building rows the repository factory owns). Fix: `Literal` axes, `NaturalKey`, `ResolvedFields`, `GateFailure` + one code table, repository-owned serialization and id minting.

**R8 — claims exceed implementation.** Introduced. Symptoms: "Closes #1592" with its named grading gate strict-xfailed (finding 2); `sandbox` defaulted true with zero suppression semantics (finding 7); "rejected at config load" with no write path (finding 8); the fixture tree still claiming `@04f59d2d5` after a v3.1.1 byte swap (comparison item 1); "73 test_architecture" in CLAUDE.md after adding 35 guards under two prefixes (finding 16). Fix: make every claim true or delete it.

**R9 — the PR's own migration-test standard applied to two of its three migrations.** Finding 15, standalone: #1521's migration has no test and a downgrade that silently destroys spec-valid rows, contradicting the survey-and-abort owner decision the sibling migration in the same PR encodes.

## 3. PR shape

### 3.1 Keep — the actual deliverable and the seams that must survive

- `src/core/tools/capabilities.py`, `src/core/schemas/capability_declarations.py`, `src/core/billing_policy.py` — the #1592/#1210 surface (with R2 fixes applied).
- #1521: the billing CHECK widening + migration `e381618812f1` (gaining its test and a corrected downgrade), the `_FIELD_POLICY` dispositions, natural-key upsert machinery, `AccountRepository.create()` + `NaturalKeyConflict`, the notification-config gates.
- The UC-010/UC-011 feature corpus, their transport wiring, the graduations, the `_SELECTIVE_XFAIL` per-row mechanism (it is the good pattern; 14's fix propagates it to UC-018).
- The migration test harness (`tests/integration/migration_helpers.py`, `migration_db` fixture) and both existing migration tests.
- `select_request_fields` (`schema_helpers.py:276`), `restore_a2a_integer_types` (set derivation fixed), the `field:` kwarg on `assert_wire_error`, the INV-4 discovery fix in `_resolve_auth_dep` — the comparison's explicit survive-the-merge inventory.
- The five adjacent wire fixes' behavior (A2A list_creatives `media_buy_ids` + projection flags; REST get_products `property_list`/`context`).
- dry_run **behavior** (spec-required) — mechanism replaced per R1.
- The guard corpus that survives R5's sweep, the mypy per-module strictening (`mypy.ini:81-92`), the wire-discipline guard's intent, the factory-based Givens.

### 3.2 Delete from this PR

- The dry-run shadow write path: `previewed_by_key` (`accounts.py:1640`), `_preview_state_from` (`:1457-1509`), `_settings_update_preview_state` (`:1106-1144`), both setattr loops (`:1273`, `:1760`), the duplicated dry-arm `build_row`/`_build_sync_result` calls (`:1808/:1822` vs `:1839/:1874`), the creatives twin (`PriorCreativeState`/`previewed`, `creatives/_sync.py:133,223-250,447`), and four guard files (`test_guards_dry_run_write_domination.py`, `test_guards_sync_accounts_row_builder.py`, `test_guards_sync_accounts_update_result_builder.py`, `test_guards_sync_creatives_update_result_builder.py`).
- `_NO_E2E_REST_TAGS` (`conftest.py:3276`) and its consumer (`:3420`); the `T-UC-005-main` strict=False route (`conftest.py:1279-1301` + `test_architecture_e2e_rest_escape_hatches.py:87` entry) replaced by explicit skip + owned issue; three-to-four of the five new `EXPECTED_UNSUPPORTED_DECLARATIONS`.
- `_error_details` (`uc010_capabilities.py:95-102`) and every `or ctx.get("synthesized_error_envelope")` step branch this PR added; the uc026 `wire_error_envelope is not None` conjunct (`:1929`) and the suggestion→recovery fallback (`:1953-1957`); `then_per_account_error_code_or` (`uc011_accounts.py:3781`) and the two disjunctive feature lines (`BR-UC-011:719,:735`).
- `_iter_adcp_error_subclasses` (`exceptions.py:249-259`) and the 10-field `Error` re-list in `normalize_advisory_errors` (`:296-305`).
- `A2A_WIRE_INTEGER_FIELDS` as a hand-list (`adcp_a2a_server.py:202-224`) — derived instead.
- The two request-rooted pointer spellings (`accounts.py:714`, `:1004`) and the hard-coded settings-update rejection wording; the four sandbox `True` literals (`tenant_context.py:56,:121`, `capabilities.py:161`, `accounts.py:997`) plus the true defaults in `models.py:82-84` and unmerged migration `846006a30d9f`.
- The admin inline mint + raw `Account(...)` (`blueprints/accounts.py:79-96`) — routed through `build_row`.
- The two beads-id FIXMEs (`conftest.py:1298,:2502`), the 86 bare beads ids this diff added under `src/` (convert to GH refs where an issue exists, otherwise delete the id — provenance belongs in commits), baseline re-seeded at 118.
- `transport.py`'s `_PINNED_ERROR_ENUM`/`_pinned_error_metadata` (`:25-38`) — re-homed.
- The false claims: "Closes #1592", "rejected at config load", the "config time" scenario titles, the "satisfied vacuously" comment at `capabilities.py:76-81`, CLAUDE.md's "73", and the stale `@04f59d2d5` docstrings where they describe the swapped enum file (`tests/helpers/pinned_schema.py:6`, `test_pydantic_schema_alignment.py:54`) — corrected to state the mixed provenance until the rebase adopts #1547's manifest.

### 3.3 The honest claim

`Closes #1210, Closes #1521` stand. **`Closes #1592` becomes `Advances #1592`**: two of its three named sections (account block, supported_pricing_models) are delivered and — after the T-UC-010-main split — graded un-xfailed; the third (`media_buy.reporting_delivery_methods`) is undeliverable without RFC 9421 webhook signing and is re-filed against #1291, with the dedicated strict-xfail scenario citing it. The PR description additionally records: the UC-001/UC-007 adjacent fixes as **ungraded** (spec-grounding gate) with their ticket numbers, and the corrected description of the declaration validator as a read-path backstop with #1856 as the write gate.

### 3.4 Split out (tickets, filed in phase 0)

1. **#1291 rider** — `reporting_delivery_policy` module (billing_policy pattern), scheduler emission gating, honest `["webhook"]` + `webhook_signing.supported=true` declaration, and the converse scenario (seller emits reporting webhook ⇒ declaration includes webhook + signing true). Gating live pushes off before signing is a product decision for the owner — this PR must not smuggle it in.
2. **#1856 rider** — tenant-config write surface calling `CapabilityDeclarations.from_tenant` at the write seam; write-time refusal scenario; sandbox execution semantics + its behavior scenario; the `account_degraded` shape.
3. **UC-001 harness + property_list/context scenarios** (graders for the REST get_products fixes; includes authoring the missing property_list row).
4. **UC-007 harness + binding module** (graders for list_authorized_properties/formats fixes).
5. **list_creatives structured `pagination`/`sort` on A2A/REST** + moving the MCP structured→flat coercion into `_build_list_creatives_request` + wiring the dormant UC-018 pagination rows.
6. **Seam conversion for the ~15 untouched A2A handlers / REST bodies** — tracked by the re-pointed parity guard's shrink-only allowlist.
7. **#1880** — the 46 pre-existing `_require_response` Thens in uc011, tracked by the widened wire-discipline guard's allowlist.
8. **Admin `resolve_or_write` constraint-name layering** (~17 sites) — against the salesagent-ctmz / #1878 admin-UoW epic.
9. **UC-005 live-catalog format seeding** (replaces the explicit skip left by 14b) — only if the interim skip is taken.
10. **Five `v31-*` dormant UC-010 tags** — mapped into `_UC010_DORMANT_TRACKING` with these issues now; wired there.

## 4. Work order

Phases 1–3 run now, in six parallel lanes; phase 4 is the #1547 rebase. Within each lane the order is load-bearing.

### Phase 0 — decisions and claims (maintainer, before code)

Ratify #1547-first. Rewrite the PR description per 3.3. File the ten tickets (3.4) — several later edits cite their numbers. Confirm with the owner that reporting-webhook emission is **not** being gated in this PR (plan assumes honesty-only).

### Phase 1 — six lanes

**Lane A — accounts machinery (strict order: delete, then type, then contract, then seam).**
1. *UoW dry_run (F3).* Add `dry_run: bool = False` to `BaseUoW.__init__`; in `__exit__` (`uow.py:95`) rollback instead of commit when set. Thread from `_sync_accounts_impl` (`accounts.py:1651`) and delete in dependency order: `previewed_by_key` → provisioning-arm seeding (`:1727-1749`) → `_preview_state_from` → `_settings_update_preview_state` + its params on `_process_settings_update_entry` → both setattr loops → the dry "created" arm (`:1799-1836`, unifying the duplicated `build_row`/`_build_sync_result` pairs) → the `if not dry_run` inside delete_missing. Repeat for creatives (`_sync.py`/`_assignments.py`). Delete the four guards. Keep `_resolve_activation_proofs`' dry_run (pre-transaction HTTP genuinely branches). **Graded by:** the existing UC-011 dry-run partitions (`@T-UC-011-ext-e-preview` `:909`, `-preview-settings-update` `:921`, `-ext-e-normal` `:937`) + `local-uc011-dry-run-preview-parity.feature` across MCP/A2A/REST/e2e-REST (all execute today), + `TestDryRunPreviewMatchesLiveRun` (`test_sync_accounts.py:1229`, `test_creative_sync_behavioral.py:868`) as the live-run oracle. **Author one Then:** the settings-update preview scenario (`:921-935`) additionally asserts the wire result echoes `payment_terms "net_45"` — the pre-write-echo defect class, currently unpinned on that arm; wire-graded via Lane C's primitives.
2. *Minimal types (F12).* `EntryMode`/`DispositionKind` Literals; frozen `_Disposition`; `_FieldPolicy.for_mode()` replacing both `getattr(policy, mode)` spellings (`:661`, `:702`); mode threaded through `_resolve_entry_changes`/`_rejected_field_errors`.
3. *Gate contract (F13).* `FailureClass` + `GateFailure` (entry-relative `field`) + `_FAILURE_CLASS_TO_CODE` next to `_FIELD_POLICY`; convert the seven `Error(...)` sites (`:707`, `:889`, `:999`, `:1045`, `:1060`, `:1078`, `:1338`); single conversion in `_first_gate_failure`; drop the index params; rejection message moves onto the disposition row. Feature edits: disjunctions → `"VALIDATION_ERROR"` (`:719`, `:735`); `accounts[0].sandbox` → `sandbox` (`:812`, `:1246`); delete the or-step. **Graded by:** the notif/sandbox gate scenarios (`:713`, `:729`, `:745`, `:805`) — executing on all four transports today; they get updated, then re-run.
4. *Remaining types (F12).* `NaturalKey` frozen dataclass with `from_row/from_reference/from_parts` (replacing `:1130/:1144/:1726`), `ResolvedFields` TypedDict, `_as_json_dict` replacing four `hasattr` sites, `_lookup_existing_for_entry` delegating to `_resolve_settings_update_target`, plain attribute access in `_apply_list_account_filters`, `MockTestBehavior` as a validated model, `model_copy` protocol filtering + enum-derived section names in capabilities, import-time key check for `_EXPERIMENTAL_FEATURE_BY_BLOCK`, annotations on `_restore_a2a_wire_integers`/`_wrapped`, `disallow_untyped_defs` added to the existing mypy per-module entries.
5. *Repository seam (F11).* `_resolve_*` helpers return typed models; serialization moves inside `update_fields`/`build_row` (making `account_serialization.py` repository-private and `_persisted_value` a repository concern); repository-owned id mint; admin create routes through `build_row` + `create()`, catching `NaturalKeyConflict`; the no-model_dump guard's traversal follows calls with repositories as the sanctioned sink. If the admin form is to share `_check_domain_validity`/`_check_billing_policy`, **author** one BR-ADMIN-ACCOUNTS scenario (reserved-TLD domain refused on the form; integration Flask client + e2e Docker — admin is correctly not on the four AdCP transports); if gate-sharing is deliberate divergence, assert it in a comment like `blueprints/accounts.py:150-165` does for billing, and the scenario travels with the ticket.

**Lane B — capabilities and policy.**
1. *Crash + posture fixes (F10).* `_build_account_block` returns `None` when `resolve_supported_billing` is empty (spec: account optional, `supported_billing` minItems 1); move `adapter.get_targeting_capabilities()` (`capabilities.py:420-421`) inside the try its comment claims; five bare asserts → typed `AdCPSalesAgentError` raises; `capabilities.py:247` assert → explicit raise. **Author two scenarios:** an `empty_billing_policy → absent` row in the executing `T-UC-010-degradation-account` outline (`BR-UC-010:380-398`, rides the existing step at `uc010_capabilities.py:847`; MCP/A2A/REST + e2e-REST — the ledger entries there are marked RESOLVED, so the new row must pass); and a BR-UC-011 scenario for settings-update against a persisted brand-less account → typed wire error via `assert_envelope_shape`, four transports.
2. *Sandbox (F7).* `resolve_account_sandbox(tenant) -> bool` in `billing_policy.py` (False on no-tenant); both readers (`capabilities.py:161`, `accounts.py:997`) call it; default flips to False in `models.py:82-84`, unmerged migration `846006a30d9f`, `tenant_context.py:56` (drop the `:121` coalesce — NOT NULL column); parity guard extended so `_build_account_block` kwargs must derive from the policy module, and the self-test that blesses the inline read inverted. **Graded by:** `@T-UC-010-v31-account-sandbox` true/false rows (executing, four transports — must stay green; they set the column explicitly), "absent" row stays xfailed to #1856; UC-011 gate scenarios (`uc011_accounts.py:2759`, `:3205-3228`) unaffected and re-run.
3. *Recovery authority (F9).* `_SPEC_RECOVERY_OVERRIDES` (~7 entries, each citing the pin) folded into `WIRE_STANDARD_CODES`; `advisory_recovery_for` becomes a table lookup raising on unknown codes; delete the subclass walk; `normalize_advisory_errors` via `model_copy(update=...)`; **author** `tests/unit/test_error_recovery_pin_parity.py` (table values, subclass `_default_recovery`, no duplicate-code disagreement — shape of `test_billing_party_parity.py`) and add the recovery assertion `test_wire_standard_codes_conformance.py`'s docstring already promises. The now-redundant hand-pinned `recovery="correctable"` kwargs in accounts.py drop with Lane A step 3. **Graded by:** the executing UC-011 advisory scenarios (`BR-UC-011:429-444`, `:515-544`) on all four transports, plus the new unit parity tests.
4. *Assembly seams (F10 follow-through).* `resolve_adapter_context()` (one session, replacing the ×4 preamble in `adapter_helpers.py`); `_resolve_or_degrade()` wrapping all five degradation blocks; `from_tenant` returns an empty instance (deleting five ternaries); `_union_sorted` collapsing `emitted_specialisms`/`emitted_supported_protocols`. Behavior-preserving; graded by existing UC-010 suites staying green.
5. *Honesty (F2 + F8).* Split `T-UC-010-main`: move the single reporting_delivery_methods assert (`BR-UC-010:101`) into its own `@T-UC-010-main-reporting-delivery` scenario carrying strict xfail + #1291 citation; delete the `_XFAIL_TAGS` entry (`conftest.py:433`); retarget `TestUC010MainReasonAccuracy` (`test_architecture_bdd_stale_xfail_reason_text.py:77-105`) at the new tag. Rewrite `capabilities.py:76-81` to state the truth (validator-layer satisfaction only; production pushes unsigned reporting webhooks today; honest declaration blocked on #1291). Rename the two "config time" scenario titles; comment on `from_tenant` binding #1856's write seam. **Effect on grading:** the account/pricing/features/geo/portfolio asserts of T-UC-010-main run un-xfailed on all four transports for the first time (tag absent from `e2e_rest_known_failures.txt`, verified — so the e2e leg grades too).

**Lane C — harness and oracles (before Lane A/B's authored Thens land, since they use these primitives).**
1. *Pinned reader (F1).* Add `pinned_error_code_metadata() -> dict[str, dict[str, str]]` (lru_cache, actionable `_refresh.py` failure message) and export `PINNED_SCHEMA_DIR` from `tests/helpers/pinned_schema.py` — **byte-identical to #1547's name and signature** (`salesagent-pr1547/tests/helpers/pinned_schema.py:33`). Delete `_PINNED_ERROR_ENUM`/`_pinned_error_metadata` from `transport.py`; `is_pinned_error_code` stays as a one-line wrapper. Re-point the five hand-derived paths (`test_guards_error_code_fixture_pin.py:24`, both enum-conformance guards, `scripts/verify_feature_error_codes.py:40`) and the sixth dir constant (`test_pydantic_schema_alignment.py:55`) at the exports. Correct the stale `@04f59d2d5` claims to state the enum file's v3.1.1 provenance (interim honesty until the rebase adopts the manifest).
2. *Wire oracles (F4).* `details=` support on `assert_envelope_shape` (per `core/error.json`); delete `_error_details` + both fallbacks, re-express the four version-negotiation Thens and the `:2355` pick through the primitive; drop the uc026 conjunct and the suggestion→recovery conflation; `wire_entry`/`wire_entry_errors` in `_outcome_helpers.py` for per-entry errors inside success envelopes; `list_accounts` dispatch verb on `AccountSyncEnv` (sharing `AccountListEnv`'s transport methods), deleting the `_list_accounts_impl` bypasses (`uc011:409`, `:506`, `:3437`); convert the ~10 new typed-payload Thens and re-point the two acceptance oracles (`then_account_billing:1707`, `then_deactivation_result:2615`) at `wire_dict`. Fix the guard's two blind spots (resolve one call level into module-local helpers; `ctx["result"]` subscript instead of the bare name `result`) and regenerate its allowlist from empty — pre-existing typed reads land as FIXME(#1880) entries, shrink-only. **Effect:** #1521's acceptance oracle and the dry-run preview oracle grade the buyer-received wire; the listed-account/subscriber e2e_rest rows grade a real transport for the first time.

**Lane D — transport seam (F5, then F6).**
1. Convert the handlers this diff opened to `build_X_request(**select_request_fields(XRequest, bag))`: `_handle_list_creatives_skill` (`adcp_a2a_server.py:1898-1936`), `_handle_get_adcp_capabilities_skill` (`:2027-2033`), `_handle_list_creative_formats_skill` (`:2038-2066`), `get_products`/`post_capabilities` (`api_v1.py:258-268`, `:278-293`). Add `ext` to the MCP wrapper, `get_adcp_capabilities_raw`, and the REST body; rename `GetCapabilitiesBody` → `GetAdcpCapabilitiesBody` (fixes the drop and the `_body_name` blind spot in one move). Re-point `collect_divergences()` at `RequestModel.model_fields`. Delete the dead `update_performance_index` webhook_url param and `update_media_buy_raw`'s dropped params, deleting their three allowlist entries; correct the two list_creatives allowlist reasons to cite ticket 5 (the pin decided). Extend `test_builder_kwargs_match_their_request_models` to every `build_*_request`. Derive the A2A integer set from SDK `*Response` model fields (or add the ~15-line set-vs-schema check to `test_guards_a2a_integer_restoration.py`). **Author:** a UC-010 ext-acceptance scenario (vendor-namespaced `ext` sent → accepted, normal response; success asserted on `wire_response`), four transports — this PR authored the entire capabilities request path, so this is its scenario to write.
2. *Wire the changed-behavior UC-018 rows (F6).* Add the partition tags (`@T-UC-018-partition-filters`, `@T-UC-018-partition-field-selector`) to the wired-marker set at `conftest.py:3944` routing through the existing `CreativeListEnv`; author the three step defs (factory-seeded creatives under two media buys; dispatch with flat singular+plural ids, and separately `include_assignments=false`; assert exact creative-id sets / assignment absence via `wire_dict(ctx)`); park the outline's remaining rows in a per-row selective-xfail dict with GH citations (copy the UC-010 `_SELECTIVE_XFAIL` pattern, `conftest.py:3968+`). **Red/green check:** `singular_to_plural_merge` (`BR-UC-018:253`) must fail on [a2a] with the handler hunk reverted and pass with it applied. Transports: a2a/mcp/rest in-process + e2e_rest.

**Lane E — registries and ratchets (F14).**
1. Two FIXMEs → GH issues; baseline re-seeded at 118; the citation hook (`check_fixme_citation_count.py`) gains a bare-id pattern (`\b(?:salesagent|bd)-[0-9a-z.]+`) for src/ and compares against the **merge base** when origin/main lacks the baseline file — closing the grandfather hole for every future hook; the 86 bare ids converted or deleted.
2. Write-throughs per ruling 7: `set_supported_versions` and `set_idempotency_posture` become `realize_e2e` tenant-config writes (implementing the reserved read in `idempotency_policy.py:93`) **iff** they fit the declarations store cleanly — the check that settles it is whether the field lands without joining `_UNBACKED_BLOCKS`; otherwise owned-issue citations. `set_adapter_channels` via `AdapterConfig.test_behavior` (mirror `_realize_targeting_capabilities`, `capabilities.py:139`). `break_tenant_config_db`/`set_build_version` keep declarations with issue citations. **Effect:** the UC-010 version-negotiation and idempotency scenarios gain their e2e leg — the only leg matching the graded contract's real-protocol posture (`version-negotiation.yaml`, `idempotency.yaml`).
3. SSRF scenario (F14d): rewrite the two Thens (`uc004_delivery.py:1777-1801`) onto transport-observables — the capture/webhook server received zero requests; the circuit-breaker internal-state assert moves to an integration test — parametrize e2e_rest, delete `_NO_E2E_REST_TAGS` and its consumer, close #1892 as not-needed. If the e2e capture plumbing can't assert absence-of-request, the fallback is an explicit declared skip + owned issue — never a registry.
4. `T-UC-005-main` (F14b): strict=False route → explicit skip citing ticket 9 (UC-005 is not this PR's surface; the false-green capability is this PR's to remove either way); guard entry count returns to 18.
5. `_SELECTIVE_XFAIL` reconciliation (F14e): the `account_degraded`/`no_tenant` rows' expectations are marked NOT-IN-SPEC in the feature itself — spec-silent means production is authoritative; fix the Examples rows to match production and drop the xfail rows. `T-UC-010-main` is Lane B step 5.

**Lane F — migration (F15, standalone).**
`tests/integration/test_billing_check_widening_migration.py` (~70 lines on the existing helpers): reset to `823974a5553e`, assert `billing='advertiser'` violates the narrow CHECK, upgrade to `e381618812f1`, assert advertiser/operator/agent admitted and non-enum still refused. Rewrite `downgrade()` (`e381618812f1:45`) to survey-and-abort (RuntimeError listing affected account_ids — the migration is unmerged, so editing it is allowed; matches the 2026-07-27 owner decision the natural-key migration encodes), extracting the shared `abort_downgrade_if_rows` helper rather than hand-rolling a second copy; pin both downgrade arms with tests. Wire behavior already graded by `T-UC-011-sync-billing-advertiser` (executing, four transports).

### Phase 2 — guard sweep (F16; after lanes A–D settle which guards still exist)

Six predicate fixes (parity join from route-handler annotations; wire-discipline `uses_wire`/`:303` fixes — Lane C did these; dormancy guard gains the missing feature→map direction: feature tags minus `_UC010_WIRED_TAGS` ⊆ `_UC010_DORMANT_TRACKING`, each with an open GH issue). Rewrite the tautological meta-test (`test_architecture_uc010_dormancy_citations.py:159-162`) to drive the real check on reverted source like its three siblings. Route the 14 helper-bypassing guards through `_architecture_helpers` (including replacing the hand-rolled allowlist pair at `transport_field_parity.py:190-215` with `assert_violations_match_allowlist`). Add the ~30-line scaffolding meta-guard (no bare `ast.parse`/`parents[2]` outside `_architecture_helpers.py`, seeded empty for new files). `git mv` the surviving `test_guards_*` files to `test_architecture_*` and fix `CLAUDE.md:85`. Map the 8 unmapped dormant UC-010 tags into `_UC010_DORMANT_TRACKING` with filed issues (the three on this PR's surfaces — channel-mapping, targeting-boundaries, degradation-creative — flagged as first candidates for wiring in their follow-ups). Collapse the six uc011 step clusters (`uc011_accounts.py:293-313`, `:331-356`) into two parametrized steps.

### Phase 3 — verification

Affected BDD modules serial on the box (`saci test bdd <files>` — files, never a directory), then full suite via `saci run --detach` + `saci status`. The T-UC-010-main split and the Lane C/E changes add e2e_rest-graded scenarios, so the bdd-in-network job is required at the finish line. Diagnose any red from `test-results/<newest>/*.json` before rerunning.

### Phase 4 — rebase onto #1547 (do not build parallel versions now)

- Fixture provenance: re-express the v3.1.1 `error-code.json` as a declared SUPPLEMENT or a full `_refresh.py` re-vendor at the new pin, regenerating `_manifest.py`; delete `test_guards_error_code_fixture_pin.py` (the manifest digests replace it). Content decision (92 codes vs 64+2) is made here; the branch carries the salesagent-44c8 reconcile, so full re-vendor at v3.1.1 is the likely answer — decide explicitly, per code, not by default.
- Auth: remap raise sites onto `classify_auth_credentials_error` / `AdCPAuthMissingError` / `AdCPAuthInvalidError`; delete `AUTH_MISSING_SUGGESTION`/`AUTH_INVALID_SUGGESTION` in favor of pinned-text defaults. **If #1547 stalls**, the minimum this PR must do before merging: register its suggestion constants in `_CANONICAL_SUGGESTION_CONSTANTS` and fix the fallback branch (`auth_context.py:126-129`) that emits AUTH_MISSING for a presented credential — the spec's MUST is keyed on header presence.
- A2A failure arms: re-land the webhook-notification and artifact work inside #1547's typed-arm (failed Task returned) / untyped-arm (forget + sanitized InternalError) split; route `_internal_error_for`/`_build_error_envelope` through `safe_adcp_error`/`_sanitized_envelope`; never interpolate `str(exc)` into a wire message.
- Harness: re-land the `field:` kwarg on #1547's both-layer `require_suggestion` body; the dispatcher-level synthesized-envelope deletion arrives with #1547 (Lane C already removed the step-level reads); recompute the three `uc002_nfr` allowlist line numbers in `test_architecture_bdd_no_request_in_then.py` and take #1547's presented-credential version of `then_auth_before_business_logic`.
- Adopt `tests/utils/a2a_helpers.py`'s failed-Task oracle family as the construction pattern for any further wire accessors.

## 5. The non-negotiable ledger

### 5.1 Every behavior this PR changes, and what grades it

- **Capabilities account block, pricing models, features, geo, portfolio, last_updated** — `T-UC-010-main` (post-split), MCP/A2A/REST/e2e-REST. Does not execute today (whole scenario strict-xfailed); executes after Lane B step 5.
- **reporting_delivery_methods** — new `@T-UC-010-main-reporting-delivery`, strict-xfail citing #1291, by design. The converse emission scenario travels with ticket 1.
- **account.sandbox emission** — `@T-UC-010-v31-account-sandbox` true/false rows, four transports, executing today; stay green after the default flip. "Absent" row xfailed to #1856.
- **Sandbox provisioning gate** — UC-011 declare/does-not-declare scenarios, four transports, executing.
- **billing='advertiser'** — `T-UC-011-sync-billing-advertiser` + partition outline, four transports, executing; the migration SQL itself gets the new integration test (authored, Lane F).
- **dry_run preview parity (accounts + creatives)** — UC-011 ext-e partitions + local parity feature + `TestDryRunPreviewMatchesLiveRun`, four transports, executing; plus the authored payment_terms-echo Then.
- **Per-account gate codes and pointers** — the four notif/sandbox scenarios, four transports, executing; updated to one code and one rooting, then re-run.
- **Advisory recovery values** — UC-011 advisory scenarios (executing, four transports) + authored unit pin-parity test.
- **Empty billing → account block absent** — authored outline row, four transports including e2e-REST. Does not exist today.
- **Brand-less account settings-update → typed error** — authored BR-UC-011 scenario, four transports. Does not exist today (currently a 500 through the A2A generic lane).
- **Unbacked declaration → CONFIGURATION_ERROR** — local-uc010 scenarios, four transports, executing (rename only).
- **A2A list_creatives media_buy_ids merge + projection flags** — UC-018 `singular_to_plural_merge` + field-selector rows: exist, **dormant today**; wired in Lane D with authored steps, four transports, with the revert-the-hunk red check.
- **capabilities request `ext` accepted** — authored UC-010 scenario, four transports. Does not exist today.
- **REST get_products property_list/context; A2A list_authorized_properties/formats fields** — **ungraded**; recorded as such in the PR description; tickets 3–4 carry the graders.
- **Version-negotiation / idempotency-posture scenarios** — executing on three in-process transports today; gain the e2e leg via Lane E write-throughs.
- **SSRF-blocked webhook** — rewritten Thens, e2e_rest parametrized (or explicit skip + issue if capture plumbing is missing — that check settles it).
- **AUTH_MISSING/AUTH_INVALID split** — graded at rebase by #1547's `auth_contract.py` oracle + presented-credential seam; until then the split is not independently graded, which is one more reason #1547 goes first.

### 5.2 Every duplication this PR introduced, and the abstraction that collapses it

All collapsed in-PR; the only duplication left tracked-not-fixed is pre-existing debt outside this diff (untouched handlers, the 46 old Thens), held by shrink-only allowlists with tickets.

- Dry-arm/live-arm `build_row` ×2, `_build_sync_result` ×2, setattr loops ×2, preview seeding ×2, creatives twin → **deleted** by the UoW dry_run flag (one write path).
- Sandbox default ×4 → `resolve_account_sandbox`.
- Recovery classification ×3 (+ 10-field Error re-list) → pin-parity-guarded `WIRE_STANDARD_CODES` lookup + `model_copy`.
- Webhook-reporting decision ×2 (declaration vs scheduler) → `reporting_delivery_policy` in ticket 1; interim honesty in comments/claims.
- Fixture path ×5, dir constant ×6, metadata reader ×2 → `PINNED_SCHEMA_DIR` + `pinned_error_code_metadata` in one module.
- Request field lists ×3–4 per tool at touched edges → `select_request_fields` + builders; integer hand-list → SDK model-field derivation.
- Adapter-resolve preamble ×4 → `resolve_adapter_context`; degradation try/except ×5 → `_resolve_or_degrade`; declaration ternaries ×5 → empty-instance `from_tenant`; sorted-union ×2 → `_union_sorted`.
- Mode lookup ×2 → `for_mode`; natural-key tuple ×3 → `NaturalKey`; `_lookup_existing_for_entry` vs `_resolve_settings_update_target` → delegation; `hasattr(model_dump)` ×4 → `_as_json_dict`; section-name tuple vs protocol enum → derive.
- Gate `Error(...)` assembly ×7 → `GateFailure` + one code table; or-step body duplicate → deleted.
- Account id mint ×2 and row assembly ×2 (admin vs repository) → repository-owned mint + `build_row`.
- Envelope parsing (`_error_details` + `:2355`) → `assert_envelope_shape(details=)`; list read-back ×2 → `AccountSyncEnv.list_accounts`.
- Guard scaffolding ×14 → `_architecture_helpers` + scaffolding meta-guard; hand-rolled allowlist pair → `assert_violations_match_allowlist`; uc011 step clusters 6 → 2; harness patcher boilerplate ×3 → dissolved by the write-through conversions.
- Survey-and-abort downgrade logic → one shared `abort_downgrade_if_rows` helper, not a second copy.

## 6. Right — do not touch

`billing_policy.py`'s claim-and-enforcement-read-one-source pattern (it is the template the fixes copy). `AccountRepository.create()` + `NaturalKeyConflict`. The migration harness and both existing migration tests. The `_FIELD_POLICY` disposition content and its spec citations. `select_request_fields`, `restore_a2a_integer_types`' concept, the `field:` kwarg, the INV-4 discovery fix. The five wire fixes' behavior. The UC-010/UC-011 scenario corpus, graduations, and the `_SELECTIVE_XFAIL` per-row mechanism. The read-path CONFIGURATION_ERROR raise and its terminal recovery. Entry-relative pointers in the notification gates (they match the graded contract; the request-rooted ones are the deviants). Presence-not-equality suggestion semantics. `TestDryRunPreviewMatchesLiveRun` — it is the oracle that survives the UoW refactor unchanged. `_resolve_activation_proofs`' dry_run branch. `declare_capabilities`' write-through and `_realize_targeting_capabilities` — the patterns Lane E propagates. The mypy per-module strictening. `account_serialization.py`'s content (it moves behind the repository; it is not deleted).

## 7. Uncertainties and what settles them

1. **SSRF e2e assertion** — whether the e2e webhook capture server can assert absence-of-request. Check the UC-004 e2e sink; if missing, explicit skip + issue, never a registry.
2. **Idempotency-posture persistence** — whether it fits `capability_declarations` without joining `_UNBACKED_BLOCKS`. If it needs its own design, keep the declaration + owned issue; do not force it.
3. **Fixture content at rebase** (92 vs 64+2 codes) — decided when adopting #1547's manifest; the 44c8 reconcile on this branch argues for full re-vendor at v3.1.1, but it must be an explicit per-decision, not a default.
4. **Reporting-webhook emission gating pre-#1291** — owner product call; plan assumes no. If the owner wants pushes stopped now, that lands as its own reviewed change with the converse scenario, not as a rider here.
5. **T-UC-005 live-catalog seeding size** — if small, do it and drop the skip; if large, ticket 9 stands.
---

# ALTERATIONS — 2026-08-06 (post-#1868 discovery + #1547 salvage triage)

Written after the plan above. Where the two disagree, **this section wins**.

## A0. The gating decision in §0 is REVERSED

§0 said "#1547 lands first". **Dead.** #1547's 2026-08-06 review returned
*Drifted on scope, Partial on thesis* — 173 files, eight independent contracts, no
closing issue, and the thesis misses on `tasks/get`/`tasks/cancel` (the v0.3 compat
adapter strips `error.data`, so the buyer gets `-32603` + `data: null`). Its own
review prescribes splitting the branch. It is not a gate; do not wait on it.

**#1868 is the gate instead** — and it is ours. Evidence: #1868 deletes 243 vendored
schema files (18,464 lines) and resolves `tests/helpers/pinned_schema.py` from the
installed SDK tree, keeping exactly one frozen file (`enums/error-code.json`, 64
codes) for the one field that genuinely diverges — `suggestion`, on exactly 4 codes
(`CREDENTIAL_IN_ARGS`, `MEDIA_BUY_NOT_FOUND`, `PACKAGE_NOT_FOUND`, `REQUOTE_REQUIRED`).
`recovery` was measured identical across all 64 shared codes, which is why #1868
already migrated `tests/harness/transport.py`, the recovery-conformance guard, and
`scripts/verify_feature_error_codes.py` onto the SDK tree.

**Stack topology (measured, not assumed).** A2-fetch and A1-uc004 are NOT stacked on
#1721 — both branch from `d912a7ee9` (= the main merge-base). Only A3-signing is, and
off `7f37e5252`, 8 commits behind this head. So #1721 gates exactly one downstream PR.

    main
     |- A2-fetch            independent of both
     `- 1868 (gate)
         |- A1-uc004        (151 of its files touch the pinned tree)
         `- 1721
             `- A3-signing  (54-file overlap incl. capabilities/accounts)

## A1. Phase 4 retargets: rebase onto #1868, not #1547

Replaces §4 Phase 4 wholesale. The fixture-provenance work there (adopt #1547's
`_manifest.py`, `SUPPLEMENT`, `test_pinned_schema_provenance.py`) is **cancelled** —
#1868 removes the tree those artifacts would describe.

## A2. The fixture hand-edit deletion is now a hard conflict, not tidiness

§3.2 listed dropping the `error-code.json` hand-edit under "false claims". Stronger:
this head carries **92 codes**; #1868 deliberately freezes the file at **64**
*because* the suggestion text must not move until #1883 reconciles it (main is 64;
#1547 is 66). Same file, incompatible intents, both ours. Whichever lands second
silently undoes the other's reasoning, and ours is the one making the provenance
claim false. **Delete the hand-edit in the rebase. Non-negotiable.**

## A3. Lane C step 1 is mostly CANCELLED

§4 Lane C step 1 said to add `pinned_error_code_metadata()` "byte-identical to
#1547's name and signature". #1868 already did this and already deleted
`_PINNED_ERROR_ENUM`/`_pinned_error_metadata` from `transport.py`.

**Remains after the rebase:** verify the five hand-derived fixture paths and the
sixth dir constant are all repointed (#1868 migrated three of them — confirm the
rest), and drop `test_guards_error_code_fixture_pin.py` if #1868's approach makes it
vacuous. **Do not re-add a reader.**

## A4. #1547 salvage triage — 10 "better" items, dispositioned

Only two are code ports. The rest are shapes this plan already prescribes, where
#1547 is a *worked example*, not a donor. #1547 is live (author committed 9 days
ago) — take shapes, not files, to avoid conflicting their split.

| # | Concern | Disposition |
|---|---|---|
| 1 | Fixture provenance manifest/SUPPLEMENT | **Cancelled** — #1868 supersedes (A1) |
| 2 | Home of the error-code reader | **Cancelled** — #1868 did it (A3) |
| 3 | `require_suggestion` both-layer check | **PORT** — new Lane C step 3 |
| 6 | Never interpolate `str(exc)` on the wire | **Fix ours** — new Lane G |
| 8 | Synthesized-envelope fallback | **PORT our half only** — Lane C step 2 |
| 7 | Recovery via `WIRE_STANDARD_CODES` | Shape only — already Lane B step 3 |
| 9 | Failed-Task accessor oracle family | Construction discipline — Lane C step 2 |
| 11 | Guards join on imported artifacts | Shape only — already Phase 2 |
| 5 | A2A two-arm failure split | **Leave in #1547** — its thesis, not our scope |
| 10 | Presented-credential auth seam | **Not now** — textual collision + out of scope |

### NEW Lane C step 3 — both-layer suggestion assertion (#1547 item 3)

**Do:** replace `assert_wire_error`'s either-layer check
(`errors[0].get("suggestion") or adcp_error.get("suggestion")`) with an explicit loop
requiring a non-empty suggestion on **both** mirrored layers by name. The `or` lets an
emitter that populates one layer pass every call site, so a one-layer regression is
invisible.
**Where:** `tests/harness/transport.py` (~line 196). Our `field:` kwarg composes —
it touches the `assert_envelope_shape` call, not the suggestion block.
**Take from:** `salesagent-pr1547:tests/harness/transport.py:181-186` (both-layer
presence loop + the spec-cited presence-not-equality comment).
**Acceptance:** an emitter populating only `adcp_error.suggestion` (or only
`errors[0].suggestion`) fails the assertion — prove it by mutation, not by reading.
Existing wire-error scenarios stay green on all four transports.
**Sequencing:** AFTER the #1868 rebase — #1868 rewrites this file.

### NEW Lane G — `str(exc)` reaches the buyer-facing wire (#1547 item 6) — SECURITY

**Do:** stop interpolating raw exception text into the JSON-RPC message. Delete the
`f"{operation} failed: {exc}"` construction and the docstring that blesses it as
"the canonical prefix" — a documented convention is worse than a stray site.
**Where:** `src/a2a_server/adcp_a2a_server.py:300` (construction) and `:287`
(docstring canonising it).
**Take from:** the principle only. #1547's `safe_adcp_error`
(`src/core/exceptions.py:1587+`) separates the semantic decision from the
message-trust decision and is the right end state, but it lives in a file #1547
rewrites wholesale — do not port the module.
**Spec:** AdCP 3.1.1 `dist/docs/3.1.1/building/operating/transport-errors.mdx`,
Security Considerations — a MUST-NOT list (credentials, SQL, hostnames, stack
traces, upstream responses). Error text also flows into LLM context.
**Acceptance:** a BDD scenario where an untyped exception is raised inside a
dispatched skill asserts, on the wire envelope, that the message contains no
exception text — graded on a2a + mcp + rest + e2e-REST via `assert_envelope_shape`.
Not a unit test on the formatter.
**Scope note:** this is ours — #1721 authored both lines.

### Lane C step 2 ADDITIONS

- **Delete the synthesized-envelope branches THIS PR added** (#1547 item 8): the
  `_error_details` helper (`uc010_capabilities.py:95-102`) and every
  `or ctx.get("synthesized_error_envelope")` step branch in the diff. 23 references
  across 9 files exist in total — including 7 in
  `test_architecture_bdd_wire_discipline.py`, i.e. a guard currently encodes the
  fallback as legitimate.
  **Boundary:** the pre-existing `McpDispatcher` fallback and the A2A
  `_wire_envelope_from_exception` fallback are NOT ours — they are #1547's change or
  their own leaf PR. Delete what we added; do not annex the rest.
  **Acceptance:** on a wire transport, an error assertion cannot pass without real
  wire bytes — prove by deleting the wire capture and watching the scenario redden.
  **Free of #1868:** `dispatchers.py` and `then_error.py` are untouched by it.
- **Construction discipline for the new wire primitives** (#1547 item 9): build
  `wire_error_details` / per-entry accessors as a private locator + one strict reader
  shared by all public entries, delegating to `assert_envelope_shape`, with
  required-not-optional returns. Reference:
  `salesagent-pr1547:tests/utils/a2a_helpers.py`.

### Lane B step 3 / Phase 2 — reference implementations

- Recovery table read: `salesagent-pr1547:src/core/exceptions.py:1479-1488`
  (`_canonical_recovery_for`) — a data lookup, no `__subclasses__()` walk, no
  `stack.pop()` ordering hazard.
- Guard-by-import: `salesagent-pr1547:tests/unit/test_architecture_rest_body_completeness.py:66-90`
  (`_PAIRS` pairs each Body class with its *imported* raw wrapper). Read this before
  fixing our `_body_name()` derivation, which is why `GetCapabilitiesBody` never
  entered the comparison and the dropped `ext` stayed invisible.

## A5. Auth — §4.4's fallback becomes the PRIMARY path

§4.4 said "remap onto #1547's `classify_auth_credentials_error`", with a fallback if
#1547 stalled. #1547 has stalled, and its auth seam is itself only half-applied:
hand-rolled `AdCPAuthInvalidError` raises sit beside the classifier
(`adcp_a2a_server.py:269-271`, `auth_context.py:124`), and `require_principal_id` is
used at none of the four transport boundaries it was written for.

**Do (minimum, ours):** register this PR's `AUTH_MISSING_SUGGESTION` /
`AUTH_INVALID_SUGGESTION` in `_CANONICAL_SUGGESTION_CONSTANTS`, and fix the branch at
`src/core/auth_context.py:126-129` that emits `AUTH_MISSING` for a *presented*
credential — the spec's MUST is keyed on header presence.
**Acceptance:** a scenario presenting a malformed credential gets `AUTH_INVALID`, and
one presenting none gets `AUTH_MISSING`, asserted on the wire envelope across all four
transports.
**Do NOT** build the boundary finalizer here. It is a real leaf PR (the shape both
reviews independently prescribe) and belongs beside #1868, not inside #1721.

---

# ALTERATIONS 2 — 2026-08-11 (owner review of the plan; verified against SDK + v3.1.1 spec)

Written after ALTERATIONS. Where they disagree, **this section wins.** Every claim
below was checked against the installed `adcp` SDK and the pinned spec tag, not inferred.

## B0. Error-code fixture — the authoritative set is DERIVED (SDK ∪ app), not a hand count

Owner ruling: the code set must be a **union of the SDK-provided codes and application-
specific ones**, imported and augmented, and *that* is authoritative — never a hand-
maintained file. Verified facts that ground it:

- `adcp.types.ErrorCode` = **92** (the spec vocabulary). `adcp.server.helpers.STANDARD_ERROR_CODES` = **38** (the wire-standard subset).
- **Both #1721 and #1868 already `from adcp.server.helpers import STANDARD_ERROR_CODES` and union it with `_SPEC_SUPPLEMENT_CODES`** (BILLING_NOT_SUPPORTED, UNSUPPORTED_PROVISIONING). So the *runtime code set* is already SDK-derived + app-augmented in both branches. The owner principle is, for the code set, **already satisfied** — do not rebuild it.
- The `92 vs 64` in A2 is **only the vendored `error-code.json` test fixture** (the suggestion/recovery *enumMetadata*), NOT the authoritative code set. #1868 resolves that metadata from the SDK tree and freezes only the 4 codes whose `suggestion` genuinely diverges — which **is** the import-and-augment shape the owner mandates.

**Correction to A2:** the conflict is not "incompatible code counts, pick one." #1721's
error is that it **hand-vendored 92 codes' metadata (with drifted suggestion text)**;
#1868's mechanism (SDK-resolve + 4-code suggestion override) is correct. prkv.1 adopts
**#1868's mechanism**, and it is **lossless** — no codes are dropped, because the code
set comes from the SDK import in both, and the 28-metadata-code difference is exactly the
hand-vendoring #1868 replaces with SDK resolution. Delete #1721's hand-edit; do not
re-add a vendored reader; let the metadata resolve from the SDK with the owned override
set. The §7.3 "decide 92 vs 64+2 per-code at rebase" uncertainty is **closed**: derive
from SDK, override only the divergent few.

## B1. reporting_delivery_methods — omission is SPEC-MANDATED, not a choice (must_equal_when)

The `must_equal_when` rule in `v3.1.1:dist/schemas/3.1.1/protocol/get-adcp-capabilities-
response.json` constrains **`webhook_signing.supported`**: when
`reporting_delivery_methods` contains `"webhook"`, `webhook_signing.supported` MUST be
`true` (rationale: "emitting state-changing webhooks unsigned is a downgrade vector that
lets an on-path attacker forge delivery callbacks").

`webhook_signing` means **RFC 9421** signing specifically, which is genuinely
unimplemented (`_WEBHOOK_SIGNING_UNSUPPORTED`, #1291). Therefore **declaring
`reporting_delivery_methods: ["webhook"]` while signing is off is SPEC-FORBIDDEN.**
Omitting it is the mandatory-honest choice, and the capabilities block is already
correct.

**Correction to Lane C step 1 (§4) and §5.2:** the fix is NOT "make the honesty claim
true by declaring the method" — that would introduce a `must_equal_when` violation. Two
concrete edits:
- The comment to be written at `capabilities.py:76-81` must say the seller pushes
  **legacy-HMAC-signed** reporting webhooks (via `get_adcp_signed_headers_for_webhook`,
  `protocol_webhook_service.py:207`), **not "unsigned"** — the plan's "pushes unsigned
  reporting webhooks today" is factually wrong. The honest statement: the seller does
  HMAC-signed webhook reporting delivery, but cannot *advertise* it because `webhook_signing`
  (RFC 9421) is unsupported and `must_equal_when` gates the declaration on it.
- `Closes #1592 → Advances #1592` stands, **for this reason**: #1592's final contract
  field (`reporting_delivery_methods`) is spec-gated on `webhook_signing`, which is gated
  on RFC 9421 (#1291). **#1592 fully closes when #1291 lands** — a real spec dependency,
  not a defect in this PR. T-UC-010-main's reporting-delivery assert honestly xfails on
  #1291; that is correct, not a masked gap.

## B2. HMAC / scheduler gating is OUT OF SCOPE for #1721 (owner ruling)

Whether HMAC-only reporting delivery is acceptable pending RFC 9421 — and whether the
`delivery_webhook_scheduler` should be gated off until signing lands — is **the
subsequent signing PR's concern (A3-signing / #1291 rider / #1605-#1441 lineage), not
#1721's.** #1721 touches neither the scheduler nor HMAC. Ticket 1 (§3.4) already carries
the `reporting_delivery_policy` module + scheduler gating; leave it there, do not pull it
forward. The only #1721 change in this area is the corrected comment/claim in B1 — a
prose fix, no behavior change.
