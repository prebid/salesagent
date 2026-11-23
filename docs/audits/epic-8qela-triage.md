# Epic salesagent-8qela: triage of the open children

Triage bead: **salesagent-4174v**. Produced 2026-10-14. Closes nothing, changes no code.

## Trees and artifacts every evidence line is taken against

| role | path | ref |
|---|---|---|
| application (authoritative) | `~/projects/sa-land-324` | `land/one-tree-8qela` @ `2d86c7586` |
| SDK fork | `~/projects/acp-integrate` | `integrate/8qela` @ `3c3aa04dc`; published wheel `9.0.0b3+sa.8qela.bf0228b68` |
| upstream SDK | `~/projects/upstream-acp/repo` | `fix/derive-open-asset-unions` @ `629c1b54f` over main `47084e83a` |
| instrument | `~/projects/acp-branch-runs` | now a git repo: 21 commits, 544 tracked files |

Runs, all noetis-b, read with `uv run python -m scripts.audit.run_report <dir> --baseline LABEL=<dir>`:

| label | dir | tree | unit exit | unit reconcile | unit skips |
|---|---|---|---|---|---|
| BASE | `test-results/innet_141026_1250` | `dea30eab1`, old wheel | 0 | 6695+40+31 = 6766 | 40 |
| HEAD | `test-results/innet_141026_0335` | `c20b25b95`, new wheel | 1 | 6693+2+40+31 = 6766 | 40 |
| CONFIRM | `test-results/innet_141026_2314` | `5299d33f2` | 1 | 6725+1+9+31 = 6766 | 9 |

No unit leg in any of the three is truncated and none exits 3. Every count above reconciles against `collected`.

## Counts per disposition

| disposition | count |
|---|---|
| CLOSE-DONE | 27 |
| CLOSE-SUPERSEDED | 2 |
| CLOSE-NOT-OURS | 3 |
| MERGE-INTO | 5 |
| KEEP-RFC | 4 |
| KEEP-BLOCKING | 19 |
| KEEP-BACKLOG | 76 |
| **total open children** | **136** |

32 of the 136 are closeable (27 CLOSE-DONE, 2 CLOSE-SUPERSEDED, 3 CLOSE-NOT-OURS); 5 more disappear into a survivor; 99 stay open (4 RFC, 19 blocking, 76 backlog).

## CLOSE-DONE (27)

| id | pri | disposition | evidence (one line, re-checkable) |
|---|---|---|---|
| `salesagent-8qela.167` | P0 | CLOSE-DONE | `git merge-base --is-ancestor 47c9bfc8b bf0228b68` (SDK half in the pinned wheel) + app half 3b74016b1/be677463e; zero `request_signature_capabilities_unreachable` in the FAILURES section of `uv run python -m scripts.audit.run_report test-results/innet_141026_2314` |
| `salesagent-mx7a8` | P0 | CLOSE-DONE | b3eb7951d; `uv run python -c "import src.core.tools.media_buy_update as m; print(len(m._UNAPPLIED_PACKAGE_FIELDS))"` -> 14; run report AGAINST BASELINE BASE reports FIXED 12, all tests/bdd/test_uc026_package_media_buy.py |
| `salesagent-8qela.114` | P1 | CLOSE-DONE | premise refuted: `cd ~/projects/acp-branch-runs && git rev-parse --is-inside-work-tree` -> true, 21 commits, 544 tracked files including suite_delta.py, classify_gate.py, forbid_extras.py, required_sweep.py |
| `salesagent-8qela.147` | P1 | CLOSE-DONE | `git show 2d86c7586:pyproject.toml | sed -n 217p` -> published release URL sa-sdk-9.0.0b3-8qela-bf0228b68; `git show 2d86c7586:uv.lock | grep -A2 '^name = "adcp"'` -> 9.0.0b3+sa.8qela.bf0228b68; re-measured in test-results/innet_141026_2314 @5299d33f2 |
| `salesagent-8qela.15` | P1 | CLOSE-DONE | its stated closing condition is salesagent-be41n, which the lead has closed (`bd show salesagent-be41n` -> closed, 16/16 deps closed) |
| `salesagent-8qela.157` | P1 | CLOSE-DONE | the measurement is complete and its recommendation adopted: all three retained runs are noetis-b and all three reconcile 6766/6766 (`python3 -c` over test-results/*/unit.json summaries) |
| `salesagent-8qela.158` | P1 | CLOSE-DONE | 653827956 'fix: raise the CI-box memory floor to the range it was measured green at' |
| `salesagent-8qela.16` | P1 | CLOSE-DONE | `df -h /` -> 199Gi avail (was 868 MiB); `git worktree list | wc -l` -> 49 (was 273 stale) |
| `salesagent-8qela.160` | P1 | CLOSE-DONE | `git show 2d86c7586:tests/storyboard/runner/package.json` pins @adcp/sdk 15.8.1; run report STORYBOARD line reads `sdk=15.8.1 adcp=3.2.3`, matching EXPECTED_SPEC_VERSION=3.2.3 |
| `salesagent-8qela.170` | P1 | CLOSE-DONE | d5fb86429 + merge 567339fc1; zero UC-004 e2e_rest delivery failures in test-results/innet_141026_2314 (bdd_e2e's 9 are the uc002 creative/sandbox set) |
| `salesagent-8qela.173` | P1 | CLOSE-DONE | both double-fixes are merged with one side kept each: be677463e (cluster A, keeping the sdk9 line's SSE decline) and 19fcb3a74 (.160's runner pin) |
| `salesagent-8qela.176` | P1 | CLOSE-DONE | 5c91ed4d2 + merge dea30eab1; `git ls-tree 2d86c7586 tests/utils/derived_population.py` exists. Residues = v7gtv, kdkbo (open) |
| `salesagent-8qela.180` | P1 | CLOSE-DONE | acp-integrate 5ca25b531 'chore(release): cut 9.0.0b3+sa.8qela.bf0228b68' and the app pins that wheel. CAVEAT: two open CHILDREN (.180.1, .180.2) outlive it |
| `salesagent-8qela.181` | P1 | CLOSE-DONE | decision taken and landed: b9e094ab2/42045831b retire _ACCEPTED_DIVERGENCE and pin 15.8.1; run report reads `sdk=15.8.1 adcp=3.2.3` |
| `salesagent-8qela.183` | P1 | CLOSE-DONE | `gh pr list --repo adcontextprotocol/adcp-client-python --search 'asset union' --state all` -> #1501 OPEN from KonstantinMirin:fix/derive-open-asset-unions; local branch at 629c1b54f over upstream main 47084e83a. Three open CHILDREN are findings |
| `salesagent-8qela.4` | P1 | CLOSE-DONE | `git show 2d86c7586:tests/bdd/steps/domain/uc026_package_media_buy.py | sed -n '2268,2271p'` -- the success branch now calls `_assert_requested_package_effects(ctx)`, which derives the assertion from the REQUEST |
| `salesagent-8qela.8` | P1 | CLOSE-DONE | the four keyword fields are in the derived refusal set: `uv run python -c "import src.core.tools.media_buy_update as m; print(sorted(m._UNAPPLIED_PACKAGE_FIELDS))"` lists keyword_targets_add/_remove and negative_keywords_add/_remove; refused with AdCPCapabilityNotSupportedError before the UoW opens |
| `salesagent-wsum9` | P1 | CLOSE-DONE | a27344d18; `git grep -c CATEGORY_MAP 2d86c7586 -- tests/bdd/steps/generic/given_entities.py` -> rc 1 (no match) |
| `salesagent-8qela.12` | P2 | CLOSE-DONE | `git show 2d86c7586:tests/bdd/steps/generic/then_payload.py | sed -n '559,567p'` -- the `field == "type"` branch is deleted and the deletion's reason recorded in its place (cd8277a74) |
| `salesagent-8qela.129` | P2 | CLOSE-DONE | cd8277a74 deletes both outlines; its own notes narrow the population to those two tags (the third, agent-asset, was correctly graduated). `git grep -c 'T-UC-005-partition-agent-type' 2d86c7586 -- tests/` -> no match |
| `salesagent-8qela.134` | P2 | CLOSE-DONE | b2fadb137 'fix(agent-db): hand out 127.0.0.1, not localhost, for an IPv4-only container' |
| `salesagent-8qela.149` | P2 | CLOSE-DONE | cd8277a74 'test(bdd): delete the two creative-agent type outlines -- the seller has no type filter' |
| `salesagent-8qela.152` | P2 | CLOSE-DONE | df4f5d33c; `git grep -c canonical-format-filters 2d86c7586 -- tests/unit/test_architecture_measurement_floors.py` -> 2; unit leg's only failure in test-results/innet_141026_2314 is the GATED-storyboard pin, not the route pin |
| `salesagent-8qela.163` | P2 | CLOSE-DONE | 1b97119bd; `git grep -n products 2d86c7586 -- tests/unit/test_architecture_local_schema_imports.py` -> empty (allowlist row gone); .152's route pin landed in the same line |
| `salesagent-8qela.165` | P2 | CLOSE-DONE | 404ba1d1e/d1db30c7d rewrote the block; `git show 2d86c7586:pyproject.toml | sed -n '157,162p'` now describes a published URL and cites salesagent-8qela.165. NEW residue filed as salesagent-kmobj |
| `salesagent-8qela.169` | P2 | CLOSE-DONE | fixed in the fork at 47c9bfc8b 'fix(signing): a task-group-wrapped capabilities failure names its sub-exception'; `git merge-base --is-ancestor 47c9bfc8b bf0228b68` -> rc 0, so it is in the wheel the app pins |
| `salesagent-8qela.171` | P2 | CLOSE-DONE | 3b80a6f31 'feat(webhooks): refuse a reporting cadence the products do not advertise' + merge 1f7eb8ace. Residue = .177 (open) |

## CLOSE-SUPERSEDED (2)

| id | pri | disposition | evidence (one line, re-checkable) |
|---|---|---|---|
| `salesagent-8qela.166` | P0 | CLOSE-SUPERSEDED | its three clusters are now separately owned and measured in test-results/innet_141026_2314: A -> .167 (closed, signature absent from the failure set), B -> .170 (closed, e2e 127 passed / 0 failed in all three runs), C -> the runner pin .160/.181. The residual 9 bdd_e2e + 1 integration belong to gzi17, 9imdq/f89xk and klpm2. CAVEAT in the unknowns section |
| `salesagent-8qela.2` | P0 | CLOSE-SUPERSEDED | the box it describes is retired. All three noetis-b runs reconcile exactly (6695+40+31, 6693+40+31+2, 6725+9+31+1 = 6766 collected) with unit exitcode 0/1/1, never 3. CONFIRMS the lead's suspicion; .157 carries the hetzner2-vm attribution |

## CLOSE-NOT-OURS (3)

| id | pri | disposition | evidence (one line, re-checkable) |
|---|---|---|---|
| `salesagent-8qela.191` | P2 | CLOSE-NOT-OURS | upstream suite health at their main 47084e83a. salesagent-8qela.183's notes independently measure the same shared failures (7 platform-bound, identical nodeid set on both trees), so the integration already accounts for them |
| `salesagent-8qela.188` | P3 | CLOSE-NOT-OURS | the defect is in upstream's scripts/generate_types.py and the artifact is upstream's SCHEMA_DELTAS.md; we do not maintain their regeneration record. The fork-side instance is salesagent-8qela.143, which stays open |
| `salesagent-8qela.189` | P3 | CLOSE-NOT-OURS | `make pre-push` / `make lint-all` is upstream's contributor gate at their main 47084e83a (9 black files, 35 ruff errors in tests/, CI runs only `ruff check src/`). Nothing in this epic consumes it; our fork's own gate is salesagent-8qela.184/.187 |

## MERGE-INTO (5)

| id | pri | disposition | evidence (one line, re-checkable) |
|---|---|---|---|
| `salesagent-f89xk` | P1 | MERGE-INTO salesagent-9imdq | same three nodeids: `test_sandbox_account_on_a_liveadserver_tenant_is_never_routed_to_the_live_ad_server[a2a|mcp|rest]`, both beads naming them verbatim |
| `salesagent-fm02z` | P2 | MERGE-INTO salesagent-klpm2 | same single nodeid: tests/integration/test_get_products_behavioral.py::TestProductConversionNegativeCardinality::test_zero_format_ids_raises_value_error, the one integration failure in all three runs |
| `salesagent-raqvy` | P2 | MERGE-INTO salesagent-8qela.183 | same defect (Format.assets routes 14 of 21); raqvy's ask was to file upstream, and issue #1486 plus PR #1501 both exist |
| `salesagent-8qela.190` | P3 | MERGE-INTO salesagent-8qela.183.2 | salesagent-8qela.183.2 is the same finding filed under the PR bead ('upstream public <Type>FormatAsset aliases name 14 of 21 slot classes'). The survivor is itself an upstream API decision, not ours |
| `salesagent-8qela.72` | P3 | MERGE-INTO salesagent-8qela.128 | one cause, stated in .128: suite_delta.py self_check()'s three round-trip cases at :309-311 carry no IPv6 literal. One case with '::' closes both |

## KEEP-RFC (4)

| id | pri | disposition | evidence (one line, re-checkable) |
|---|---|---|---|
| `salesagent-8qela.148` | P1 | KEEP-RFC | the authoring inversion: `git grep -c format_kinds 2d86c7586 -- src/` and the pin citations core/product.json 'New products MUST author canonical format_options[]' |
| `salesagent-8qela.150` | P1 | KEEP-RFC | the canonical half's thin shell: the seller calls two legacy-comparison helpers and emits none of the three pinned projection codes |
| `salesagent-8qela.3` | P1 | KEEP-RFC | `git grep -E 'def [a-z_]*satisf' 2d86c7586 -- src/` -> nothing, so .3.4's one satisfaction function exists nowhere; five open children |
| `salesagent-8qela.154` | P2 | KEEP-RFC | `git grep -n format_ids 2d86c7586 -- src/core/database/models.py` -- InventoryProfile carries format_ids NOT NULL and no format_options column, so a profile-linked product cannot be canonically authored |

## KEEP-BLOCKING (19)

| id | pri | disposition | evidence (one line, re-checkable) |
|---|---|---|---|
| `salesagent-8qela.126` | P0 | KEEP-BLOCKING | blocks the epic's exit: `git rev-list --count main..2d86c7586` -> 295, `2d86c7586..main` -> 0; and `git branch -a --contains 0144ad734` names only work/epic-8qela-pipeline, so .3.4's only work is still droppable |
| `salesagent-14ffd` | P1 | KEEP-BLOCKING | blocks the buyer capabilities mx7a8 now refuses: 14 declared fields answer UNSUPPORTED_FEATURE, `packages[].canceled` among them |
| `salesagent-8qela.127` | P1 | KEEP-BLOCKING | blocks .8's acceptance. `targeting_overlay` is in _APPLIED_PACKAGE_FIELDS so the nested keyword_targets is NOT refused, and `git grep keyword_targets 2d86c7586 -- src/` -> 2 hits, both comments: no production handler |
| `salesagent-8qela.14` | P1 | KEEP-BLOCKING | blocks .9's and .4's acceptance (both graded by rows in the uc002/uc003-not-wired 2,397). Its ratchet is also unlanded -- see salesagent-837bd |
| `salesagent-8qela.156` | P1 | KEEP-BLOCKING | CONFIRMED with a number: 31 unit items move skip->pass between innet_141026_0335 (40 skips) and test-results/innet_141026_2314 (9), all tests/unit/test_architecture_storyboard_*/measurement_floors; no refusal landed (`_storyboard_guard_env.py:68` still returns (None, None)). Blocks any premeasure in a fresh worktree |
| `salesagent-8qela.9` | P1 | KEEP-BLOCKING | blocks the create path's apply-exactly-or-reject MUST; gated on .14 per its own notes. No refusal on the create side: `git grep keyword_targets 2d86c7586 -- src/core/tools/` -> comments only |
| `salesagent-9imdq` | P1 | KEEP-BLOCKING | still failing in all three runs, 4 nodeids (a2a/mcp/rest in bdd_inprocess + e2e_rest), CONFIGURATION_ERROR. Blocks: on a credentialed deployment a sandbox account would proceed toward real orders |
| `salesagent-dxb3j` | P1 | KEEP-BLOCKING | blocks every cross-run comparison in this epic -- the three runs' shas come from the lead's prompt, not from any artifact: `ls test-results/innet_141026_2314/` carries no meta.json |
| `salesagent-zbmkz` | P1 | KEEP-BLOCKING | declared blocker of .14 (`bd show salesagent-8qela.14` DEPENDS ON) and the cause behind .155's 2553 unobserved items |
| `salesagent-7sp1i` | P2 | KEEP-BLOCKING | `git grep -c _UC026_REST_VACUOUS 2d86c7586 -- tests/bdd/conftest.py` -> still defined at :4466 and used on ~87 rows whose rationale mx7a8 falsified; likely grader is salesagent-zrqpw |
| `salesagent-8qela.11` | P2 | KEEP-BLOCKING | blocks the ledger-fitness storyboard check (salesagent-zrqpw): xpassed across both BDD legs is 105 in test-results/innet_141026_2314, up from 97 at BASE |
| `salesagent-8qela.159` | P2 | KEEP-BLOCKING | its fix 1514c2e52 is on land/sdk9-lanes-five and `git merge-base --is-ancestor 1514c2e52 2d86c7586` -> rc 1. Blocks any graduation population derived from bdd_scenario_liveness.json |
| `salesagent-8qela.164` | P2 | KEEP-BLOCKING | its census module is NOT in the authoritative tree: `git ls-tree -r --name-only 2d86c7586 tests/unit/ | grep route_xfail` -> empty. Blocks .14's ratchet and .151's _CEILINGS. See salesagent-837bd |
| `salesagent-8qela.182` | P2 | KEEP-BLOCKING | blocks the next pin move; the bead carries a measured recommendation (prefer upstream's two commits over landing pin/spec-3.2.3) and the decision is the lead's |
| `salesagent-8qela.184` | P2 | KEEP-BLOCKING | blocks a green fork gate: `make typecheck-all` -> `test-type-checks` is a required step of `make ci-local` and `make pre-push`, and the error set is byte-identical at 81027b9a8 and at the 3.2.3 pin |
| `salesagent-8qela.187` | P2 | KEEP-BLOCKING | blocks a green cut by construction: test_worktree_version_matches_normalized_release_manifest fails on every stamped commit (bf0228b68 green, 5ca25b531 carrying the one failure) |
| `salesagent-b6oyh` | P2 | KEEP-BLOCKING | `git grep -n 'T-UC-026-inv-198-4' 2d86c7586 -- tests/bdd/conftest.py` -> :4310, still in _UC026_XFAIL_TAGS and xpassing on all three transports |
| `salesagent-gzi17` | P2 | KEEP-BLOCKING | its 6 uc002 e2e_rest creative rows are 6 of the 9 bdd_e2e failures in test-results/innet_141026_2314 (run report FAILURES: 'Expected creative validation to pass but got error: wire error VALIDATION_ERROR') |
| `salesagent-klpm2` | P2 | KEEP-BLOCKING | the one integration failure in all three runs: tests/integration/test_get_products_behavioral.py::...::test_zero_format_ids_raises_value_error, 'Regex pattern did not match' |

## KEEP-BACKLOG (76)

| id | pri | disposition | evidence (one line, re-checkable) |
|---|---|---|---|
| `salesagent-8qela.10` | P1 | KEEP-BACKLOG | three unbound features; 155 of 276 are Creative-Agent tasks this seller does not serve, so only BR-UC-001's 121 are real and .13 owns them |
| `salesagent-8qela.109` | P1 | KEEP-BACKLOG | dispatch.py:1440, .28's handed-over residue; the bead's own note warns a pattern grep on the shifted site returns a false green |
| `salesagent-8qela.140` | P1 | KEEP-BACKLOG | an ordering constraint on .108, not work of its own; `git grep -rn CreativeManifest 2d86c7586 -- src/` -> zero, so .108's CreativeManifest half is irrelevant here |
| `salesagent-8qela.141` | P1 | KEEP-BACKLOG | SDK ProtocolEnvelope.status optional where core/protocol-envelope requires it; 2f047548f is not an ancestor of the cut tag, so the shipped wheel has no _ROOT_REQUIRED at all |
| `salesagent-8qela.161` | P1 | KEEP-BACKLOG | the git-pathspec rule; nothing landed. Hazard confirmed independently in this triage (`git ls-tree -r --name-only ... | grep` used instead) |
| `salesagent-8qela.23` | P1 | KEEP-BACKLOG | the ask is a refusal, not a repair; `uv run` now WARNS on a mismatched VIRTUAL_ENV (seen in this triage) but `uv pip install` still follows it |
| `salesagent-8qela.34` | P1 | KEEP-BACKLOG | SDK message projection; shares _OPAQUE_TYPES with .94 and should land with it |
| `salesagent-8qela.36` | P1 | KEEP-BACKLOG | ungraded by construction -- .python-version pins 3.12 and tests/test_native_ci.py:142 asserts the matrix is 3.10-3.13 by exact equality |
| `salesagent-8qela.44` | P1 | KEEP-BACKLOG | the digest recipe is now versioned (see .114) but the platformdirs/filelock pin-back residue stands and filelock is load-bearing on behaviour |
| `salesagent-8qela.58` | P1 | KEEP-BACKLOG | a process rule, not code; followed throughout this triage (bd note only, never bd update --notes) |
| `salesagent-8qela.65` | P1 | KEEP-BACKLOG | the triage rule separating a real defect from one manufactured by an open schema node; the flip is now re-derivable (see .114) and needs re-running at the current tip |
| `salesagent-8qela.73` | P1 | KEEP-BACKLOG | `tests/conformance/test_schema_model_parity.py` 86/86 passes with the defect re-armed; the companion guard grades 7 of 8 derived schemas |
| `salesagent-8qela.74` | P1 | KEEP-BACKLOG | _NESTED_PYTEST_MODULES drift; needs the AST walk .96's fix used, and the marker is inert outside tox.ini's unit env |
| `salesagent-8qela.91` | P1 | KEEP-BACKLOG | scope corrected by its own notes to bare `uv run`/`uv sync` WITHOUT --locked; UV_FROZEN=1 with tox-uv fails loudly instead |
| `salesagent-kdkbo` | P1 | KEEP-BACKLOG | the third and fourth spellings of an emptying derived population; .176 covered two |
| `salesagent-nhyxg` | P1 | KEEP-BACKLOG | a generated subclass's root validator shadows its base's; the guard whose title reads as its grader selects on `_branch_models` in cls.__dict__, a disjoint population, so 29 pairs are unmeasured |
| `salesagent-1qnvq` | P2 | KEEP-BACKLOG | FIRST CLAUSE REFUTED (forbid_extras.py and required_sweep.py are now tracked in acp-branch-runs, see .114); the 'no gate can grade it' clause stands and is .65/.66's grading gap |
| `salesagent-5nr4i` | P2 | KEEP-BACKLOG | the four package_config blobs and targeting_overlay still absent from the affected_packages snapshot mx7a8 built |
| `salesagent-8qela.107` | P2 | KEEP-BACKLOG | 5 of 19 split-root bases; serialize_as_any=True suppresses the only symptom a test could see, and the assertion shaped like its grader is unfalsifiable |
| `salesagent-8qela.108` | P2 | KEEP-BACKLOG | the application's insulation from .116 per .140; the gates are byte-identical at the cut tag and at 2aeef29c2, so the pinned wheel has it |
| `salesagent-8qela.111` | P2 | KEEP-BACKLOG | the reporting_rows branch of _delivery_rows has no currency case; the bead is right about the defect and wrong about the fix per the deleting commit's own docstring |
| `salesagent-8qela.113` | P2 | KEEP-BACKLOG | Spotlight indexing confounds a run's load average; remedies listed, none landed |
| `salesagent-8qela.115` | P2 | KEEP-BACKLOG | a question to settle between structural-change.yaml's mandated REMOTE_TEST_HOST export and .98's rule; the file is in the orchestrator's tree |
| `salesagent-8qela.128` | P2 | KEEP-BACKLOG | the write-direction mangling for 158 IPv6 nodeids across eight modules; survivor of the merge with .72 |
| `salesagent-8qela.132` | P2 | KEEP-BACKLOG | `git grep -c 127.0.0.2 2d86c7586 -- tests/` -> 4 matches in one file, all in TestTwoDestinationsOneService; one site, platform-bound |
| `salesagent-8qela.136` | P2 | KEEP-BACKLOG | oracle-vs-pydantic granularity; no assertion compares the two lanes' values, so a green suite is silence. Groups with .81 and .142 |
| `salesagent-8qela.138` | P2 | KEEP-BACKLOG | the merged root carries the branches' CONST values; strictly larger than .80's requiredness fix because Status22 has lost the nine-value enum |
| `salesagent-8qela.151` | P2 | KEEP-BACKLOG | a BDD population count taken without BDD_E2E_ENABLED under-reports by a quarter; its _CEILINGS citation does not resolve in the authoritative tree (see salesagent-837bd) |
| `salesagent-8qela.155` | P2 | KEEP-BACKLOG | an XPASS is recorded as 'passed', so a graduation population cannot be derived from the artifact; the obvious substitute over-counts 852 vs 117 |
| `salesagent-8qela.175` | P2 | KEEP-BACKLOG | nothing in the release preflight compares the cut sha to the graded sha; .187 is the concrete instance that makes it bite |
| `salesagent-8qela.186` | P2 | KEEP-BACKLOG | the carried spec version is declared in a hand-editable file; the derived form (src/adcp/_compliance/bundle-manifest.json) is already packaged |
| `salesagent-8qela.37` | P2 | KEEP-BACKLOG | a maintainer packaging call; its cheaper-looking option (b) turns tests/test_native_ci.py:142 red, which the bead does not price |
| `salesagent-8qela.42` | P2 | KEEP-BACKLOG | 3.14-only; `suite.*.meta.json` records CPython 3.12.9, so no graded interpreter reaches it. .45 carries the leading candidate cause |
| `salesagent-8qela.43` | P2 | KEEP-BACKLOG | no rootdocs artifact exists at any commit in the current lineage, so the bound is unmeasured; closure needs a measurement, not a commit |
| `salesagent-8qela.46` | P2 | KEEP-BACKLOG | a genuine union at a FIELD; .28's sweep exercised split-root bases only, so its zero says nothing here |
| `salesagent-8qela.48` | P2 | KEEP-BACKLOG | CI installs with `uv pip install -e` and caches on pyproject.toml; measured at 18 install steps, not ~30, and the exposure is CI-only |
| `salesagent-8qela.5` | P2 | KEEP-BACKLOG | 27 of 76 park-table entries, zero re-triaged; `git grep -n _UC010_PARKED_TAGS 2d86c7586 -- tests/bdd/conftest.py` still at :1447 |
| `salesagent-8qela.54` | P2 | KEEP-BACKLOG | blocks .56; the recovery=None sub-observation lost its evidence when .19 and .49 closed and must be re-derived |
| `salesagent-8qela.59` | P2 | KEEP-BACKLOG | the compiled index in a conjunct's message; the predicate is correct and only the number is wrong, so no acceptance assertion can move |
| `salesagent-8qela.61` | P2 | KEEP-BACKLOG | authored validators for rules the compiled constraints derive; ordering is .62 before .61, anchored on the class rather than the file |
| `salesagent-8qela.62` | P2 | KEEP-BACKLOG | the bundled capabilities clone carries zero derived root constraints; query by __pydantic_decorators__.model_validators on both classes, not by file grep |
| `salesagent-8qela.64` | P2 | KEEP-BACKLOG | blocked on .59; the fix is a REUSE of the per-subpackage _diagnostics module the package already has |
| `salesagent-8qela.66` | P2 | KEEP-BACKLOG | 3 models, not the 75 first reported; the defect class is invisible to a green suite by construction (an under-required field causes a MISSING failure) |
| `salesagent-8qela.67` | P2 | KEEP-BACKLOG | no schema-file-to-class artifact; scripts/task_message_lattice.py:135 already has the shape, scoped to task messages |
| `salesagent-8qela.81` | P2 | KEEP-BACKLOG | in_progress; the lanes' field values are never compared, so the zero-failure tree is silence. Groups with .136 and .142 |
| `salesagent-8qela.89` | P2 | KEEP-BACKLOG | the blocker is an INTERFACE, not a rule -- narrow_union_errors never receives the arm models; one change with .84 |
| `salesagent-8qela.90` | P2 | KEEP-BACKLOG | a split root's generated schema drops allOf and states anyOf where the pin says oneOf; nothing compares a generated schema to its pinned document |
| `salesagent-8qela.94` | P2 | KEEP-BACKLOG | the remaining 80 bare-ValueError sites in 26 modules; one projection with .34 on two fields |
| `salesagent-8qela.98` | P2 | KEEP-BACKLOG | the run-queue design error; both box qualifiers were already durable and the one non-durable thing (the rule) is contradicted by structural-change.yaml |
| `salesagent-demae` | P2 | KEEP-BACKLOG | Cond class identity depends on sys.path; latency condition holds (no scripts/__init__.py, no root conftest, no pythonpath) so exactly one spelling is live by accident |
| `salesagent-z1zqn` | P2 | KEEP-BACKLOG | aliases.py patches two generated classes; three TestPydanticInternalApiDriftSentinel tests PIN the pydantic internal API the defect depends on and must be deleted with the fix |
| `salesagent-5beb4` | P3 | KEEP-BACKLOG | a DECISION bead (delete or keep); the documentation half has landed, and it is FOUR dead by external reference, not three |
| `salesagent-8qela.104` | P3 | KEEP-BACKLOG | brand_discovery.py's extra='allow' is ungraded; .96's floor is a liveness check on the detector, not a completeness check on the population |
| `salesagent-8qela.105` | P3 | KEEP-BACKLOG | nothing grades docs/schema-modifiers-and-class-checks.md; two of its quantitative claims are already falsified with zero failures |
| `salesagent-8qela.112` | P3 | KEEP-BACKLOG | three identical pointer expressions in negotiation.py; one shared helper closes all three, which is the DRY reading |
| `salesagent-8qela.13` | P3 | KEEP-BACKLOG | BR-UC-001's 121 scenarios are a genuine obligation (get_products is Required and served) but unblock nothing in this epic |
| `salesagent-8qela.130` | P3 | KEEP-BACKLOG | four .81 citations that mean .82; the module has been touched since and the citations were not corrected, refuting the bead's own prediction |
| `salesagent-8qela.133` | P3 | KEEP-BACKLOG | blocked on the owner, not stalled: the pin leaves no legal value for an adopter-supplied exception's keyword |
| `salesagent-8qela.142` | P3 | KEEP-BACKLOG | the lanes still disagree by one entry; a single cross-lane equality assertion over a complete payload grades .81, .136 and .142 at once |
| `salesagent-8qela.143` | P3 | KEEP-BACKLOG | SCHEMA_DELTAS.md reports five already-existing bases as added; the fork-side instance of .188 (closed NOT-OURS) |
| `salesagent-8qela.146` | P3 | KEEP-BACKLOG | two reparented webhook servers; shares start_new_session territory with .55 and .68, worth checking for one cause first |
| `salesagent-8qela.174` | P3 | KEEP-BACKLOG | supports_webhooks=False advertised while the scheduler delivers; settle together with salesagent-3cs7o.21's backfill rather than patching the default |
| `salesagent-8qela.177` | P3 | KEEP-BACKLOG | pre-existing non-daily registrations; closeable by a read-only query (keyed on reporting_frequency, NOT frequency) that may return zero rows |
| `salesagent-8qela.32` | P3 | KEEP-BACKLOG | _set_class_extra_allow's silent 'missing'; 266 anonymous / 0 NAMED x-adcp-open-payload carries forward, so the branch remains unreachable rather than fixed |
| `salesagent-8qela.38` | P3 | KEEP-BACKLOG | a perf bead; the emitter window it was waiting for has passed, so the regeneration happened without the CSE |
| `salesagent-8qela.45` | P3 | KEEP-BACKLOG | 15 unrestored sys.modules writes; the conversion target is something the same file already does. Leading candidate cause for .42 |
| `salesagent-8qela.56` | P3 | KEEP-BACKLOG | blocked on .54's precedence rule; a sweep will not answer it |
| `salesagent-8qela.68` | P3 | KEEP-BACKLOG | the only caller of execute() is SKIPPED 4/4 in the graded run, so no suite outcome can demonstrate or refute a fix |
| `salesagent-8qela.71` | P3 | KEEP-BACKLOG | one I001 plus two siblings no bead carries, all auto-fixable; the drift is explained by .92's absent .git/hooks/pre-commit |
| `salesagent-8qela.78` | P3 | KEEP-BACKLOG | a design question with nothing to fix first; the two existing mirrors are each independently checked, so a third would be the only unchecked one |
| `salesagent-8qela.82` | P3 | KEEP-BACKLOG | five split response roots declare no merged shape; their ten skips are the only non-passing items in the 19307-item green run besides one xfail |
| `salesagent-8qela.84` | P3 | KEEP-BACKLOG | union membership decided by SPELLING; the graded tree carries the workaround as a permanent fixture rename. One change with .89 |
| `salesagent-8qela.92` | P3 | KEEP-BACKLOG | the attribution needs redoing over the black 24.10.0 set; 11 pinned-only files have no owner, and black 26 in a lane venv is an authoring hazard |
| `salesagent-9mn5x` | P3 | KEEP-BACKLOG | 43 rescued patches, 4 touching production; not urgent while the trees remain, urgent the moment someone reclaims them |
| `salesagent-mp20e` | P3 | KEEP-BACKLOG | the spec-vs-SDK version ambiguity that made two beads read 92 correct citations as fabricated |
| `salesagent-v7gtv` | P3 | KEEP-BACKLOG | 56 at-risk derived populations in 29 files after .176; carries the enumeration and the triage rule so neither is re-derived |

## The MERGE pairs, and what the loser carries

Copy the named facts onto the survivor BEFORE closing the loser. Each loser carries
something its survivor does not.

### `salesagent-f89xk` → **`salesagent-9imdq`**

Same three nodeids, both beads naming them verbatim:
`test_sandbox_account_on_a_liveadserver_tenant_is_never_routed_to_the_live_ad_server[a2a|mcp|rest]`.

Survivor chosen because 9imdq is older (2026-10-12), carries the root cause
(CONFIGURATION_ERROR from an absent GAM credential), the introducing commit
(`e7342c089`, with the scenario absent at its parent `6fbba07af`), and the resolved
verdict "the seller is wrong".

f89xk carries two facts 9imdq lacks:
- **the routing gap** — the tag `@T-UC-002-sandbox-no-live-ad-server` appears nowhere in
  `tests/bdd/conftest.py` and nowhere in `tests/bdd/e2e_rest_known_failures.txt`, so the
  failure is unrouted and unledgered. That is why it shows as a hard failure rather than an
  xfail, and it is what the ledger-fitness check (`salesagent-zrqpw`) may be reading.
- **the attribution** — both the base `dea30eab1` run and the mx7a8 commit's run show F at
  the same three positions in a byte-identical outcome stream for that module, so mx7a8 did
  not cause it.

Also worth carrying to the survivor: the CONFIRM run shows a FOURTH row,
`[e2e_rest]`, failing the same way (`WireError('wire error CONFIGURATION_ERROR')`), so the
population is 4, not 3.

### `salesagent-fm02z` → **`salesagent-klpm2`**

Same single nodeid — the one integration failure in all three runs:
`tests/integration/test_get_products_behavioral.py::TestProductConversionNegativeCardinality::test_zero_format_ids_raises_value_error`.

Survivor chosen because klpm2 is newest (2026-10-14) and carries the fix shape: derive the
product stub's attribute set from the model the way
`tests/helpers/media_buy_row_stubs.stub_package_row` derives from
`MediaPackage.__table__.columns` (which also shrank the mock cap 103 → 88).

fm02z carries two facts klpm2 lacks:
- **the bisect** — fails at `4899604d7`, passes at its parent `4eedcb1de`.
- **the mechanism** — `4899604d7` ("feat(products): let a canonically-authored product exist
  and be served") replaced the empty-`format_ids` `ValueError` with a refusal on the root
  `anyOf` at `product_conversion.py:429`. So the refusal the test asserts was *removed by
  design*, which means the fix is not only "make the fixture reach the guard" — someone has
  to decide whether the guard should still exist.

These two claims are not the same and both are true: the fixture cannot reach the guard
(klpm2) **and** the guard it would reach has been replaced (fm02z). Closing klpm2 without
fm02z's half risks repairing the fixture to assert a refusal production no longer owes.

### `salesagent-8qela.72` → **`salesagent-8qela.128`**

One cause, stated in .128: `suite_delta.py`'s `self_check()` round-trip cases at `:309-311`
carry no IPv6 literal, so the check that exists to stop the two directions disagreeing has
no case that can see a disagreement. One case with `::` in a parametrize id closes both.

Survivor chosen because .128 carries the corrected numbers (158 nodeids across **eight**
modules, not .72's 127 across two) and .128 was filed expressly to correct .72's verdict.

.72 carries two facts .128 lacks:
- **the read direction** — `nodeid_of` relocates `.py` into the middle of the name. .128 is
  only about the write direction (`junit_key`).
- **the correction to .72's own transcript** — the quoted demonstration does not reproduce;
  the versioned code produces a full-length wrong key. Anyone re-deriving from .72's
  transcript will be chasing a different defect.

**.72's verdict is the thing being retired**: "cosmetic, `main()` is safe" is wrong, which is
why a note on .72 is not enough and the survivor has to be .128.

### `salesagent-raqvy` → **`salesagent-8qela.183`**

Same defect: the forward-compat asset union narrows `Format.assets` from the 21 types the
bundled schema declares to 14 (12 in a repeatable group). raqvy's remaining ask was to file
it upstream, and both artifacts now exist: issue
[#1486](https://github.com/adcontextprotocol/adcp-client-python/issues/1486) OPEN and PR
**#1501** OPEN from `KonstantinMirin:fix/derive-open-asset-unions`.

raqvy carries three porting facts .183 lacks, all measured:
- `0b8b194ab` does **not** cherry-pick onto `e5744dcf7` (8 conflicting files, and
  `src/adcp/types/_split_root.py` is absent upstream).
- the `+288` generator pass in `post_generate_fixes.py` references none of our branch-class
  machinery, so the fix is separable.
- it needs `_ensure_module_import` carried from our `6c2cf3d0d` (17 lines, self-contained),
  and the three generated files **regenerated rather than ported**.
- the drafted PR body is held at
  `~/projects/acp-branch-runs/repro/PR_BODY_asset_union.md` — now inside a git
  repo, tracked at `40ac054`.

Note `salesagent-cjm3j` is already closed with the title "replaces PR #1501", which reads as
if #1501 were retired. It is OPEN. Whoever reconciles these three should settle which PR is
the live one before closing anything.

### `salesagent-8qela.190` → **`salesagent-8qela.183.2`**

.183.2 ("Finding: upstream public `<Type>FormatAsset` aliases name 14 of 21 slot classes") is
the same finding already filed under the PR bead. The loser names the absent aliases
explicitly and that list is worth copying: `CardFormatAsset`, `ZipFormatAsset`,
`DisplayTagFormatAsset`, `PixelTrackerFormatAsset`, `VastTrackerFormatAsset`,
`DaastTrackerFormatAsset`, `PublishedPostFormatAsset`, plus the group counterparts and
`BriefFormatGroupAsset` / `CatalogFormatGroupAsset`.

The survivor is itself **upstream's API decision, not ours** — .190's own text says "adding
public names is an API decision, not a bug fix". Routing it to .183.2 keeps it with the PR
that would carry it; it does not make it our work.

---

## Beads where I could not reach a confident disposition

Four. Each names the one question that would settle it.

### 1. `salesagent-8qela.166` — I called it CLOSE-SUPERSEDED and cannot prove the headline number

Its claim is "15 tests that PASS on main fail on the integration branch, per nodeid on the
same box". I decomposed it by cluster and every cluster is accounted for:

- cluster A (7 signing) → `.167`, and `request_signature_capabilities_unreachable` appears in
  no failure in any of the three runs;
- cluster B (6 UC-004 e2e_rest delivery) → `.170`, and `e2e` is 127 passed / 0 failed in all
  three runs with no UC-004 delivery failure in `bdd_e2e`;
- cluster C (storyboard) → the runner pin, `.160`/`.181`, now 15.8.1 / 3.2.3.

**What I cannot do is the comparison the bead is phrased as.** No main-baseline run is
retained in `sa-land-324/test-results/` — there are exactly three run dirs and all three are
integration-branch trees. A per-nodeid main-vs-HEAD delta is the only thing that closes .166
on its own terms.

**Question that settles it:** is there a retained main run anywhere (the bead cites
`sa-f1623b33` and `/srv/sa-runs/`), or does .166 need one in-network run on `main` before it
can be closed? If a main run exists, `scripts/audit/compare_runs.py` answers it in one
command.

### 2. `salesagent-8qela.126` — KEEP-BLOCKING, but what it blocks may be a decision, not work

The premise holds exactly: `git rev-list --count main..2d86c7586` → 295,
`2d86c7586..main` → 0, and `main` IS an ancestor, so the land tree is a clean fast-forward.
The data-loss half also still holds: `git branch -a --contains 0144ad734` names only
`work/epic-8qela-pipeline`, and `git merge-base --is-ancestor 0144ad734 2d86c7586` → rc 1, so
`.3.4`'s only existing work is still reachable from one branch and nothing else.

**Question that settles it:** does this bead close when the work lands on main, or is its
subject the *mechanism* (nothing notices when a lane's commit is reachable from one branch
only)? The first is a scheduling step the lead owns; the second is a guard and a different
bead. The bead's description does not say which, and I am not going to pick the lead's scope.

### 3. `salesagent-8qela.114` — I called it CLOSE-DONE on the stated premise and left its scope question open

Its title premise is now false by measurement: `acp-branch-runs` IS a git repository (21
commits, 544 tracked files, `suite_delta.py`, `classify_gate.py`, `forbid_extras.py`,
`required_sweep.py` all tracked). Its own notes anticipated exactly this and declined to
decide: "Whether this bead now closes on the repository being created, or stays open for the
broader 'the instrument is outside the trees it measures' shape with 1qnvq as its first
subtask, is a judgement about this bead's intended scope that its description does not
settle."

I did not overrule that. I closed it on the title and left `salesagent-1qnvq` open carrying
the half that survives (nothing goes red if the condition returns).

**Question that settles it:** is "the instrument lives outside the trees it measures" in
scope for .114, or a new bead? If it is in scope, .114 reopens and 1qnvq becomes its subtask.

### 4. `salesagent-8qela.15` — CLOSE-DONE on its own gate, with one live defect inside it that has no bead

The bead names `salesagent-be41n` as its closing condition and be41n is closed with all 16
dependencies closed. That is the stated gate and it is met.

But its last note records something that does not close with it, and this is the part I am
unsure how to route: `test_the_two_response_branches_the_rule_cannot_name_today` is a
**docstring-only record of a live defect** ("The emitter nonetheless mints a NEW class for it,
which is the defect") with **no bead id and no FIXME**, and it names two upstream blockers
recorded nowhere else — `get-content-standards-response.json` `oneOf[0]` needs an upstream
title, and `get-creative-features-response.json` `oneOf[0]` is a bare `$ref`.

**Question that settles it:** should that become its own bead (and the two upstream blockers
an upstream issue), or does .15 stay open as its home? A defect recorded only in a passing
test's docstring is invisible to every sweep this epic runs, which argues for a bead.

---

## Confirmations and refutations of the lead's stated suspicions

| suspicion | verdict | the measurement |
|---|---|---|
| `.2` is about a box we no longer use | **CONFIRMED** | all three noetis-b runs reconcile 6766/6766; unit exitcodes 0/1/1, never 3; `.157` attributes every truncation in 167 retained runs to hetzner2-vm @ 74 GB |
| `.16` disk reclaimed | **CONFIRMED** | `df -h /` → 199 Gi avail; `git worktree list \| wc -l` → 49 |
| `.147` repin done | **CONFIRMED** | pin is the published release URL for `bf0228b68`; `uv.lock` carries `9.0.0b3+sa.8qela.bf0228b68`; re-measured at `5299d33f2` |
| `.180` pin move done | **CONFIRMED** | fork cut `5ca25b531` published and pinned. Two open children survive it |
| `.181` runner line decided | **CONFIRMED** | `tests/storyboard/runner/package.json` → `@adcp/sdk 15.8.1`; run report reads `sdk=15.8.1 adcp=3.2.3` |
| `.183` upstream PR done | **CONFIRMED, and it is OPEN** | PR **#1501** is OPEN upstream, not merely developed. `salesagent-cjm3j` (closed) says it "replaces PR #1501" — reconcile that before closing |
| `.160` runner version | **CONFIRMED** | was `14.0.0-rc.42`, now `15.8.1`, which declares the pinned 3.2.3 exactly |
| `.166`, `.167` landed at `dea30eab1` | **`.167` CONFIRMED; `.166` PARTLY** | `.167`'s signature is absent from every failure. `.166`'s headline comparison cannot be re-measured — see unknown #1 |
| `.169`, `.170`, `.171`, `.176` landed | **ALL FOUR CONFIRMED** | `.169` in the SDK at `47c9bfc8b` (ancestor of `bf0228b68`); `.170` `d5fb86429`; `.171` `3b80a6f31`; `.176` `5c91ed4d2` + `tests/utils/derived_population.py` |
| `.165`'s complaint no longer stands | **CONFIRMED for the complaint, REFUTED for the file** | the 43-line block is gone and the rewrite cites the bead. The rewrite then re-introduced the same staleness at two clauses — filed as `salesagent-kmobj` |
| one fix does not cover `.8`/`.9`/`.127` | **CONFIRMED, exactly as the lead warned** | `targeting_overlay` is in `_APPLIED_PACKAGE_FIELDS`, so the derived refusal covers `keyword_targets_add/_remove` (`.8`, closed) and **not** `packages[].targeting_overlay.keyword_targets` (`.127`, open) nor the create path (`.9`, open) |
| `.156` is real and measured | **CONFIRMED, with the number** | 31 unit items move skip→pass between `innet_141026_0335` (40 skips) and `innet_141026_2314` (9), all in `tests/unit/test_architecture_storyboard_*` / `measurement_floors`; no refusal has landed |
| the four upstream beads are CLOSE-NOT-OURS | **3 of 4** | `.188`, `.189`, `.191` → CLOSE-NOT-OURS. `.190` is a **duplicate of `.183.2`** and goes there, where it remains upstream's call |

---

## Found and not previously filed — five beads, filed under the epic

### `salesagent-837bd` (P1) — five commits never reached the authoritative tree

**This is the most consequential thing in the triage.**
`git merge-base --is-ancestor 541334ba3 2d86c7586` → **rc 1**. `land/one-tree-8qela`
descends from `f24d0b5bb`, an *earlier* point on `land/sdk9-lanes-five`, so the last five
commits of that line — the whole `lane/route-xfail-census` landing — are absent:
`541334ba3`, `3ca702958`, `1514c2e52`, `dc027281f`, `17033061a` (8 files, 577 insertions).

Consequences, each checkable at `2d86c7586`:
`git ls-tree -r --name-only 2d86c7586 tests/unit/ | grep route_xfail` → **empty**;
`git grep -c _CEILINGS 2d86c7586 -- tests/` → **nothing**. So `.164`'s census module does not
exist here (although its notes record "THE CONSOLIDATION IS COMPLETE: SEVEN OF SEVEN" — six
of seven are in), `.14`'s ratchet over the 2,532 setup-phase xfails does not exist, `.151`'s
and `.149`'s `_CEILINGS` citations do not resolve, and `.159`'s fix (`1514c2e52`) is unlanded
so the 7% transport corruption stands in every run taken from this tree.

It was invisible because every bead note is *correct about `541334ba3`* and silent about
which tree the application was then assembled from. The fix is a merge, not a
re-implementation — `541334ba3` is a descendant of `f24d0b5bb`. Pre-check with
`git merge-tree --merge-base=f24d0b5bb`.

### `salesagent-c7dz1` (P1) — a closed bead's graded check has never gone green

`salesagent-8qela.1` is CLOSED. The check it exists for,
`security_transport/signed_requests/negative-profile-3.2/negative/001-base64url-sf-binary`,
fails **identically in all three runs on both protocols**: `expected 401 with
error="request_signature_header_malformed", got 200 (error="(none)")`. The seller accepts a
request the 3.2 profile requires it to reject.

`.160`'s "a mis-pinned runner grades the wrong contract" objection no longer applies: the
CONFIRM run's runner declares 3.2.3 and `EXPECTED_SPEC_VERSION` is 3.2.3. This is the shape
this epic's invariant 5 exists to catch — the instrument was never shown able to produce the
passing outcome before the bead was closed.

### `salesagent-1lum4` (P2) — the epic's own storyboard baseline has no bead

The epic description records "runner 30 passing / **4 failing** / 475 skipped per protocol".
Those four failing checks are named nowhere in the tracker: `bd search notification_config`
returns five issues, all from other epics; `bd search billing_gate` returns one, a different
subject. All four are `sync_accounts` checks (three `notification_config_*`, one
`billing_gate_dispatch`), identical on mcp and a2a, with `DISPARITY between protocols = 0`,
and stable across all three runs (`checks fixed 0, newly failing 0`). They are **not** inside
the `comply_test_controller` scope boundary the epic excludes — they execute and fail on
their assertions, unlike the 334 `missing_test_controller` skips.

### `salesagent-zrqpw` (P2) — the ledger-fitness check fails in all three runs, unowned

`tests/storyboard/test_storyboard_conformance.py::test_storyboard_check[ledger::fitness::ledger_fitness::stale_entries]`
is FAILED in BASE, HEAD and CONFIRM alike. `bd search ledger_fitness` and `bd search
pass_floor` both return nothing, so neither it nor the two `_runner::pass_floor::floor_met`
failures beside it has a bead.

Three open beads say the ledger *is* stale: `7sp1i` (~87 rows whose rationale mx7a8
falsified; `_UC026_REST_VACUOUS` still live at `tests/bdd/conftest.py:4466`), `b6oyh`
(`T-UC-026-inv-198-4` still in `_UC026_XFAIL_TAGS` at `:4310` while xpassing on all three
transports), and `.11`. xpassed across both BDD legs went **97 → 105** between BASE and
CONFIRM — the ledger falling further behind production. So this check is plausibly the
*grader* for those three rather than separate work, which is why they should be scheduled
together. Not established: whether `stale_entries` names those entries specifically.

### `salesagent-kmobj` (P3) — the rewrite re-introduced the staleness it records

`.165` asked for the pin comment to be rewritten, and it was. Two clauses inside the rewrite
are now stale against the same file: `pyproject.toml:170` says the local segment is
`+sa.8qela.2aeef29c2` and `:179` says "`EXPECTED_SPEC_VERSION` … 3.2.1 here", while `:217`
pins `…bf0228b68…` and `tests/helpers/adcp_pin.py:20` reads
`EXPECTED_SPEC_VERSION = "3.2.3"`. The same block gets it right 23 lines later at `:193-197`,
so one comment states both values and a reader meets the old one first.

The durable half: `.165`'s own notes predicted it — "no guard reads prose for agreement with
the line it annotates, so this kind of staleness is caught by nothing" — and the repin commit
`d1db30c7d` proved the prediction on the very block that records it.

---

## Two method notes for whoever re-reads this

**A landing recorded against a lane branch is not a landing in the measured tree.**
`salesagent-837bd` was found by checking `git merge-base --is-ancestor` for every sha a bead
note claimed as landed, rather than trusting the note. Six of the seven landings `.164`
records were real; the seventh was not, and the note is not wrong about anything it says.

**A closed bead is not evidence its check passes.** `salesagent-c7dz1` was found by reading
the failure set of the authoritative run and asking which failures had no owner, rather than
by reading the open list. Two of the five storyboard failures turned out to belong to a
*closed* bead and to no bead at all respectively.
