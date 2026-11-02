# salesagent-eii8n re-derived: what actually blocks the unwired UC-006 scenarios

Measured 2026-09-10 on branch `exec/itsii-1-drop-impl`. Nothing was re-run; every
number below comes either from a **collection-time probe** that renders each scenario
and asks pytest-bdd's own binder whether each step has a definition, or from the
committed artifacts of run `test-results/innet_090926_1728/`.

## How the numbers were produced

**Instrument.** A pytest plugin running in `pytest_collection_modifyitems` over
`tests/bdd/`. For every collected pytest-bdd item it:

1. takes the `ScenarioTemplate` off `item.function.__scenario__` and calls
   `templated_scenario.render(item.callspec.params["_pytest_bdd_example"])` — the same
   substitution pytest-bdd performs at run time, so the sentences examined are the ones
   the binder actually looks up, not the `<placeholder>` template text;
2. calls `pytest_bdd.scenario.find_fixturedefs_for_step(step, fixturemanager, node)`
   against the real `FixtureManager` and the real node — pytest-bdd's own binder, not a
   re-implementation of parser matching;
3. resolves the route with `storyboard_spec.resolve_env_route(derive_marker_names(node),
   ENV_ROUTES)` — the same call `_harness_env` makes.

**Validation of the instrument.** The probe's UC-006 node set is *identical* to the UC-006
node set of the last full in-network run: 708 nodeids in
`test-results/innet_090926_1728/bdd_inprocess.json`, 708 in the probe, zero on either side
of the difference. Every measured claim below is also cross-checked against that run's
outcomes.

**Two units are reported throughout**, because they differ by up to 2×:

- **RENDERED sentence** — what pytest-bdd looks up, one per Examples row. This is the unit
  of *binding*.
- **TEMPLATE line** — what a human edits in the `.feature` file. Six identities share the
  single template line `Then <expected>`, which renders to 20 distinct sentences.

**Denominator warning.** `bdd` runs 4 transports in-network (`a2a`, `mcp`, `rest`,
`e2e_rest`) split across two tox envs. The ticket's node counts are **3-transport
(in-process) only**. Both are given below; neither is wrong, but only one is the run.

## The corrections

| | ticket claimed | measured | why they differ |
|---|---|---|---|
| scenario identities blocked by a missing step definition | 23 | **31** | The ticket enumerated only the identities the `uc006-stepless` ENV route names, and then dropped 4 of even those. `_UC006_NO_STEP_DEFINITION` holds **27**; a further **4** identities have unbound steps on some Examples rows while routing to the *wired* row. |
| identities on the `uc006-stepless` route | (23) | **27** | The 4 the ticket omitted are `rule-039-inv5-lenient`, `rule-093-inv1`, `rule-093-inv3`, `sandbox-happy`. Two of them (`rule-039-inv5-lenient`, `sandbox-happy`) additionally carry a `strict=True` xfail marker claiming a SPEC-PRODUCTION GAP, which is what makes them awkward to list — see "Two mislabelled xfails" below. |
| nodes | 111 | **119** on the stepless route in-process, **160** with `e2e_rest`; **155 / 210** counting all 31 identities; **128 / 174** nodes that actually carry an unbound step | The ticket's 111 is exactly the in-process node total of its own 23 — arithmetically correct for the set it chose, and 3-transport. |
| "~30 sentences" | ~30 | **49 template lines / 69 rendered sentences** for the ticket's own 23; **54 / 74** for the 27; **63 / 85** for all 31 | The ticket listed *one* blocking sentence per scenario — the first step that fails to bind. A scenario stops at its first missing step, so the rest were invisible to whatever produced that list. `T-UC-006-vast-tracker-asset` is listed with one sentence and has four. |

Per-scenario node counts in the ticket were spot-checked and are correct as
3-transport figures (e.g. `boundary-creative-status` 12 = 4 Examples rows × 3
transports; it is 16 in the full run).

## Two gates, reported separately

A scenario grades something only if **(a)** every rendered step binds *and* **(b)** its
`ENV_ROUTES` row carries no `xfail_reason`.

Joining the probe against `bdd_inprocess.json` (708 UC-006 nodes: 455 passed,
253 xfailed, 0 failed):

| route row | route xfails? | has an unbound step? | in-process nodes | run outcome |
|---|---|---|---|---|
| `uc006-creative-sync` | no | no | 455 | passed |
| `uc006-creative-sync` | no | no | 63 | xfailed (own marker) |
| `uc006-creative-sync` | no | **yes** | **9** | xfailed |
| `uc006-stepless` | **yes** | **yes** | **119** | xfailed |
| `uc006-graduation` | yes | no | 11 | xfailed |
| `uc006-malformation` | yes | no | 15 | xfailed |
| `uc006-own-xfail` | yes | no | 24 | xfailed |
| `uc006-unverified` | yes | no | 9 | xfailed |
| `uc006-harness-seam` | yes | no | 3 | xfailed |

**Which would still be dormant after wiring alone: all 27 on the `uc006-stepless` route
(119 in-process nodes / 160 with `e2e_rest`).** That row's predicate is
`bool(m & _UC006_NO_STEP_DEFINITION)` and it carries an `xfail_reason`, so it xfails at
fixture setup before any step runs. Worse, simply *deleting* an identity from
`_UC006_NO_STEP_DEFINITION` does not wake it: the row above it keys off
`_UC006_WIRED_SCENARIOS`, and anything matching neither falls through to
`uc006-unclassified`, which also xfails. **Each of the 27 needs both a step definition and
a move from `_UC006_NO_STEP_DEFINITION` to `_UC006_WIRED_SCENARIOS`** (`tests/bdd/conftest.py`
lines 5349 and 5435).

The remaining **9 nodes across 4 identities** need step definitions only — their route
already permits execution. They are parked one row at a time instead:

- `T-UC-006-boundary-assignments-structure` (2 nodes) and `T-UC-006-boundary-validation-mode`
  (1 node) are parked by `_SELECTIVE_XFAIL` rows whose reason already reads
  "no step definition for one of its steps" (`conftest.py:922`, `:946`). Both dispatch the
  request — the a2a POST is in the run's stderr — and then die at the `Then`, so they grade
  the wire not at all while looking like partially-live scenarios.
- `T-UC-006-storyboard-creative-reception-stateful-render` (3 nodes) and
  `T-UC-006-storyboard-provenance-claim-contradicted` (3 nodes) are recorded in
  `tests/bdd/dormant_scenarios.txt:262-266`.

### Two mislabelled xfails

`T-UC-006-rule-039-inv5-lenient` and `T-UC-006-sandbox-happy` carry
`pytest.mark.xfail(..., strict=True)` with reasons that begin "SPEC-PRODUCTION GAP"
(`conftest.py:2378`, `:2384`) — and neither scenario binds its `Then` steps, so neither has
ever graded production. `_classify_strict_xfail_dormancy` exists precisely to fail this
pattern, but it never fires here: the `uc006-stepless` route xfails at *fixture setup*, so
the call phase never runs and the tripwire never sees the report. When these two are wired,
the marker must be re-decided, not carried forward.

## The sentence list

**Bucket "defined only in a DARK module" is empty.** Every module under `tests/bdd/steps/`
was imported and every `@given/@when/@then` parser in it — 1753 step definitions across 33
modules (29 registered in `pytest_plugins` / 1438 defs; 4 unregistered / 315 defs:
`uc019_query_media_buys` 158, `uc026_package_media_buy` 121, `then_media_buy` 25,
`uc002_task_query` 11) — was matched against all 85 unbound rendered sentences. **Zero
matches.** No UC-006 sentence is waiting on a re-export or a dark module; all 85 are
genuinely undefined. (`then_media_buy` is the RE-EXPORTED tier —
`tests/bdd/steps/generic/then_success.py:13` imports `then_no_media_buy_persisted` from it
and re-declares it — and `uc019_query_media_buys` is live via a star-import in
`tests/bdd/test_uc019_query_media_buys.py:14`. Neither contributes a UC-006 sentence.)

**Bucket "defined but the scenario is xfail-routed regardless" is also empty** in the strict
sense: none of the 31 identities has all its steps bound. The 27-identity analogue of that
bucket is the routing gate documented above — wiring their steps changes nothing until the
route row moves.

So: **all 63 template lines / 85 rendered sentences are genuinely undefined anywhere**, and
the full list follows, grouped by scenario identity. `route` is the ENV row that claims it;
`in-proc` / `4-tr` are node counts.

### T-UC-006-boundary-creative-status
route `uc006-stepless` — 12 in-proc / 16 4-tr; 2 template lines, 8 rendered sentences

- `GIVEN a creative whose sync resolves to a non-terminal per-creative action "<action>" carrying advisory status <status_value>` → 4 rows: action `created`/`unchanged`/`updated` × status `"deleted"`/`"processing"`/`"approved"`/`"archived"`
- `THEN <expected>` → `the per-creative result should be rejected as schema-invalid`; `... should carry advisory status "approved"` / `"archived"` / `"processing"`

### T-UC-006-boundary-creative-status-response
route `uc006-stepless` — 6 / 8; 2 template lines, 4 rendered

- `GIVEN a creative whose sync resolves to <result_shape>` → `per-creative action "deleted" with no status field`; `per-creative action "failed" carrying status "rejected"`
- `THEN <expected>` → `the per-creative result shape should be rejected as schema-invalid`; `the per-creative result should be accepted with the status omitted`

### T-UC-006-boundary-delete-missing
route `uc006-stepless` — 15 / 20; 2 template lines, 8 rendered

- `GIVEN a sync request whose scope is <scope_setup>` → 5 rows: `a creative_ids filter and no delete_missing flag`; `delete_missing false and no creative_ids filter`; `delete_missing true and no creative_ids filter`; `delete_missing true together with a creative_ids filter`; `neither delete_missing nor creative_ids provided`
- `THEN <expected>` → `the request should proceed and leave absent creatives unchanged`; `... as a full-library replace`; `... scoped to the filtered subset`

### T-UC-006-boundary-sandbox
route `uc006-stepless` — 18 / 24; 3 template lines, 5 rendered

- `WHEN the Buyer Agent sends a <response_shape>` → `a sync_creatives request that completes synchronously`; `... that fails operation validation`; `... that is queued as submitted`
- `GIVEN <account_kind>` → `the request targets an explicit production account`
- `THEN <expected>` → `the response sandbox field, if present, should be false`

### T-UC-006-creative-item-missing-content
route `uc006-stepless` — 3 / 4; 3 template lines

- `GIVEN a creative whose assets contain a CreativeItem with asset_kind "media" but no content_uri`
- `THEN the error should be a schema validation error`
- `THEN the error should identify the missing content_uri field on the CreativeItem`

### T-UC-006-creative-item-multi-asset
route `uc006-stepless` — 3 / 4; 4 template lines

- `GIVEN a creative with a known format_id that requires a multi-asset composition`
- `GIVEN the creative's assets include CreativeItems with asset_kind "media" and asset_kind "text"`
- `GIVEN each CreativeItem carries asset_type, asset_id, and the discriminator-required content field`
- `THEN every CreativeItem should be persisted under the parent creative`

### T-UC-006-creative-item-text-array
route `uc006-stepless` — 3 / 4; 2 template lines

- `GIVEN a CreativeItem with asset_kind "text" whose content is an array of strings`
- `THEN all text variants should be retained on the CreativeItem`

### T-UC-006-creative-variable-declared
route `uc006-stepless` — 3 / 4; 2 template lines

- `GIVEN the creative declares CreativeVariables with variable_id, name, and variable_type set`
- `THEN every declared CreativeVariable should be persisted on the creative`

### T-UC-006-creative-variable-invalid-type
route `uc006-stepless` — 3 / 4; 3 template lines

- `GIVEN a creative that declares a CreativeVariable whose variable_type is not in the v3.1 enum`
- `THEN the error should be a schema validation error`
- `THEN the error should identify the offending variable_type value`

### T-UC-006-creative-variable-required-flag
route `uc006-stepless` — 3 / 4; 2 template lines

- `GIVEN a creative that declares a CreativeVariable with required true and a default_value`
- `THEN the persisted CreativeVariable should retain its required flag and default_value`

### T-UC-006-daast-tracker-asset
route `uc006-stepless` — 3 / 4; 4 template lines

- `GIVEN an audio creative with a known format_id`
- `GIVEN the creative's assets include a DAAST tracker with daast_event "start" and a tracker URL`
- `GIVEN the creative's assets include a DAAST tracker with daast_event "complete" and a tracker URL`
- `THEN every DAAST tracker asset should be persisted with its daast_event and url`

### T-UC-006-daast-tracker-no-non-linear-target
route `uc006-stepless` — 3 / 4; 4 template lines

- `GIVEN an audio creative with a known format_id`
- `GIVEN a DAAST tracker asset whose target is "non_linear"`
- `THEN the error should be a schema validation error`
- `THEN the error should explain that DAAST has no non_linear element`

### T-UC-006-error-details-conflict
route `uc006-stepless` — 3 / 4; 2 template lines

- `GIVEN a creative whose creative_id collides with a concurrently-updated server-side creative`
- `THEN the error should include a suggestion to re-read the resource and retry`

### T-UC-006-error-details-creative-rejected
route `uc006-stepless` — 3 / 4; 1 template line

- `GIVEN a creative that is rejected by the Seller's review workflow`

### T-UC-006-error-details-policy-violation
route `uc006-stepless` — 3 / 4; 1 template line

- `GIVEN a creative whose content breaches a referenced governance policy`

### T-UC-006-main-async-submitted
route `uc006-stepless` — 3 / 4; 4 template lines

- `GIVEN a batch sync that the Seller cannot confirm within the request window` *(shared with `sandbox-submitted-no-flag`)*
- `THEN the response should have status "submitted" with a task_id` *(shared with `sandbox-submitted-no-flag`)*
- `THEN the response should not include a creatives array`
- `THEN the Buyer can poll tasks/get with the task_id to retrieve per-item results`

### T-UC-006-main-delete-missing-conflict
route `uc006-stepless` — 3 / 4; 3 template lines

- `GIVEN a sync request with both creative_ids filter and delete_missing set to true`
- `THEN the operation should fail with INVALID_REQUEST`
- `THEN the error should explain that delete_missing applies to the entire library scope, not a filtered subset`

### T-UC-006-partition-creative-status-terminal
route `uc006-stepless` — 6 / 8; 2 template lines, 3 rendered

- `GIVEN a creative whose sync resolves to per-creative action "<action>"` → `"deleted"`; `"failed"`
- `THEN the per-creative result should omit the status field`

### T-UC-006-rule-039-inv5-lenient
route `uc006-stepless` — 1 / 2; 2 template lines. **Also carries a strict SPEC-PRODUCTION-GAP xfail.**

- `THEN the incompatible package should be reported in assignment_errors`
- `THEN processing should continue without aborting`

### T-UC-006-rule-093-inv1
route `uc006-stepless` — 3 / 4; 1 template line

- `THEN the creative should be assigned but paused (no delivery)`

### T-UC-006-rule-093-inv3
route `uc006-stepless` — 3 / 4; 1 template line

- `THEN the delivery ratio should reflect the weight ratio (80:20)`

### T-UC-006-sandbox-errors-no-flag
route `uc006-stepless` — 3 / 4; 1 template line

- `GIVEN a sync request that fails operation-level validation`

### T-UC-006-sandbox-happy
route `uc006-stepless` — 1 / 2; 1 template line. **Also carries a strict SPEC-PRODUCTION-GAP xfail.**

- `THEN no real ad platform creative uploads should have been made`

### T-UC-006-sandbox-submitted-no-flag
route `uc006-stepless` — 3 / 4; 2 template lines

- `GIVEN a batch sync that the Seller cannot confirm within the request window` *(shared)*
- `THEN the response should have status "submitted" with a task_id` *(shared)*

### T-UC-006-vast-tracker-asset
route `uc006-stepless` — 3 / 4; 4 template lines

- `GIVEN a video creative with a known format_id` *(shared across the 3 VAST identities)*
- `GIVEN the creative's assets include a VAST tracker with vast_event "start" and a tracker URL`
- `GIVEN the creative's assets include a VAST tracker with vast_event "complete" and a tracker URL`
- `THEN every VAST tracker asset should be persisted with its vast_event and url`

### T-UC-006-vast-tracker-forbidden-event
route `uc006-stepless` — 3 / 4; 4 template lines

- `GIVEN a video creative with a known format_id` *(shared)*
- `GIVEN a VAST tracker asset whose vast_event is "impression"`
- `THEN the error should be a schema validation error` *(shared with 4 other identities)*
- `THEN the error should explain that impression URLs belong on a url asset with url_type "tracker_pixel"`

### T-UC-006-vast-tracker-progress-requires-offset
route `uc006-stepless` — 3 / 4; 4 template lines

- `GIVEN a video creative with a known format_id` *(shared)*
- `GIVEN a VAST tracker asset with vast_event "progress" but no offset field`
- `THEN the error should be a schema validation error` *(shared)*
- `THEN the error should identify the missing offset field`

### Not on the stepless route — 9 in-process nodes the ticket does not mention

### T-UC-006-boundary-assignments-structure
route `uc006-creative-sync` (executes) — 2 of 20 in-proc nodes unbound; parked per row by `_SELECTIVE_XFAIL`

- `THEN <expected>` → `the error should be INVALID_REQUEST`

### T-UC-006-boundary-validation-mode
route `uc006-creative-sync` (executes) — 1 of 10 in-proc nodes unbound; parked per row by `_SELECTIVE_XFAIL`

- `THEN <expected>` → `the system should reject with INVALID_REQUEST`

### T-UC-006-storyboard-creative-reception-stateful-render
route `uc006-creative-sync` (executes) — 3 / 4 nodes unbound; in `dormant_scenarios.txt`

- `GIVEN the Buyer Agent pushes creative assets to a stateful sales agent`
- `THEN the seller should validate the creatives against its format specifications`
- `THEN the per-creative result should carry a status drawn from creative-status enum`
- `THEN the per-creative status may be "approved", "pending_review", or "rejected"`
- `THEN platform-assigned IDs should be returned when applicable`

### T-UC-006-storyboard-provenance-claim-contradicted
route `uc006-creative-sync` (executes) — 3 / 4 nodes unbound; in `dormant_scenarios.txt`

- `GIVEN the Buyer Agent submits a creative claiming digital_source_type "digital_capture"`
- `GIVEN the on-list verifier responds with ai_generated true at confidence at least 0.9`
- `WHEN the seller invokes the verifier against the creative manifest`
- `THEN the error details should NOT carry detail_url or verifier extension fields`

### Sentences shared across identities

Wiring these six template lines once discharges part of several scenarios at a time:

| template line | identities |
|---|---|
| `THEN the error should be a schema validation error` | 5 (`creative-item-missing-content`, `creative-variable-invalid-type`, `daast-tracker-no-non-linear-target`, `vast-tracker-forbidden-event`, `vast-tracker-progress-requires-offset`) |
| `GIVEN a video creative with a known format_id` | 3 (the VAST trio) |
| `GIVEN an audio creative with a known format_id` | 2 (the DAAST pair) |
| `GIVEN a batch sync that the Seller cannot confirm within the request window` | 2 (`main-async-submitted`, `sandbox-submitted-no-flag`) |
| `THEN the response should have status "submitted" with a task_id` | 2 (same pair) |
| `THEN <expected>` | 6 — but it renders to 20 distinct sentences, so it is one *edit site*, not one *binding* |

## The 22 `bdd_e2e` failures in `test_uc006_sync_creatives.py`

`test-results/innet_090926_1728/bdd_e2e.json`: 2877 nodes, 26 failed, of which **22 are
`test_uc006_sync_creatives.py`** — the largest single failure cluster in the tree.

### They share one cause, and it is a test-harness defect

**Evidence, in order of strength.**

1. **They are transport-specific.** All 22 fail only on `e2e_rest`. Every one of them
   **passes on all three in-process transports** in the same run (20 of 22 pass on all three;
   2 have an in-process sibling that is separately xfailed). Zero UC-006 nodes failed
   in-process.

2. **They are deterministic, not a shared-database race.** The failing nodeid set is
   *byte-identical* across three consecutive in-network runs — `innet_090926_1255`,
   `innet_090926_1510`, `innet_090926_1728` — all 22, no drift. (This falsifies the
   obvious hypothesis that `_reset_e2e_db`'s per-scenario `TRUNCATE` is racing xdist
   workers on the shared server DB. It is not.)

3. **The dispatched payload differs by transport for the same scenario.** From
   `test-results/innet_090926_1728/payloads/`, for
   `test_assignments_structure_boundary__boundary_point[…single entry (minItems boundary)…]`:

   | transport | creative `format_id` on the wire |
   |---|---|
   | `rest` (in-process) | `{"agent_url": "https://creative.test.example.com", "id": "display_300x250"}` |
   | `e2e_rest` | `{"agent_url": "https://creative-agent.adcp.test:8443/api/creative-agent", "id": "display_300x250_image"}` |

4. **The setup that must match it does not switch.**
   `tests/bdd/steps/domain/uc006_sync_creatives.py:74` defines `_format_payload`, which
   returns the live Docker creative-agent's format under `e2e_rest` and the mocked
   `env.DEFAULT_AGENT_URL` + `display_300x250` in-process. Line 119 defines
   `_product_format_entry`, whose docstring says it "must match the creative format_id
   returned by `_format_payload` so that format compatibility checks pass on all transports
   including e2e_rest". **`_product_format_entry` is used at 3 call sites. 25 other sites
   hard-code `format_ids=[{"agent_url": agent_url, "id": "display_300x250"}]` with
   `agent_url = env.DEFAULT_AGENT_URL`** — for example `given_assignment_with_ids`
   (line 1189), `given_assignment_with_weight_zero` (line 1258),
   `given_assignment_with_placement_ids` (line 1286). The creative-scope steps have the
   mirror-image bug: `_build_creative_scope_payload` (line 7080) hard-codes
   `{"id": "display_300x250", "agent_url": env.DEFAULT_AGENT_URL}` into the *creative*
   instead of calling `_format_payload`.

5. **Production then behaves exactly as specified.**
   `src/core/tools/creatives/_assignments.py:250-276`: when the creative's normalized
   agent_url + format_id is not among the product's `supported_formats`, strict mode raises
   `AdCPCreativeRejectedError`, which `src/core/errors/codes.py:389` maps to HTTP 422. The
   run's stderr confirms `POST /api/v1/creatives/sync "HTTP/1.1 422 Unprocessable Entity"`.

**One root cause, two presentations:**

- **16 nodes** — a *product* seeded with the in-process format while the creative carries the
  live e2e format ⇒ request-level `CREATIVE_REJECTED` (HTTP 422). Tags:
  `boundary-assignments-structure` (3), `boundary-media-buy` (2), `partition-assignment-pkg`
  (2), `partition-assignments-structure` (2), `partition-mb-status` (3), `rule-038-inv4`,
  `rule-040-inv1`, `rule-040-inv4`, and `rule-039-inv1`.
- **6 nodes** — a *creative* built with the in-process format that the live server's real
  registry cannot resolve ⇒ HTTP 200 with per-creative `action: "failed"`. Tags:
  `boundary-creative-scope` (3), `partition-creative-scope` (3).

One caveat, stated rather than glossed: `T-UC-006-rule-039-inv1` (URL-normalization) sets
both agent_urls explicitly and fails differently — `assigned_to` comes back empty with no
error. It is in the same family (a format the live registry cannot resolve) but its exact
mechanism was not traced to a line; treat it as one node needing individual diagnosis.

### Their relationship to the unwired scenarios: none, beyond a common parent

The two sets are **disjoint**. All 22 failures sit on identities inside
`_UC006_WIRED_SCENARIOS`; none is in `_UC006_NO_STEP_DEFINITION`, and none of the 31
step-less identities appears among them.

What they do share is a cause in *time*. Comparing UC-006's `e2e_rest` outcomes:

| run | uc006 e2e nodes | xfailed | passed | failed |
|---|---|---|---|---|
| `innet_080926_1734` | 270 | 247 | 23 | 0 |
| `innet_090926_1255` → `innet_090926_1728` | 270 | 167 | 81 | **22** |

The UC-006 route partition (salesagent-lqm79) — the same work that produced this ticket —
moved 80 `e2e_rest` nodes from xfail into execution, and 22 of them landed on a harness
defect that had been invisible while they were parked. **The 22 are the `e2e_rest` residue
of the wired half; the 31 are the not-wired half. Fixing either does not touch the other.**

## Prioritized work list

Node counts are **in-process / all-4-transport**. "Route" means an identity must also move
from `_UC006_NO_STEP_DEFINITION` (`conftest.py:5349`) into `_UC006_WIRED_SCENARIOS`
(`conftest.py:5435`), or a per-row park must be removed.

| # | item | identities | unblocks (in-proc / 4-tr) | template lines | needs |
|---|---|---|---|---|---|
| 1 | **Fix `_format_payload` / `_product_format_entry` divergence** — route the 25 hard-coded product seedings and `_build_creative_scope_payload` through the transport-aware helpers | — | turns **22 red `e2e_rest` nodes green** | — | test-harness fix only; no route, no step defs |
| 2 | **E. sandbox + async-submitted** | 5 | **28 / 38** | 9 | steps **and** route; `sandbox-happy` also needs its strict SPEC-GAP marker re-decided |
| 3 | **C. per-creative status/action outlines** (`boundary-creative-status`, `-response`, `partition-creative-status-terminal`) | 3 | **24 / 32** | 5 | steps **and** route |
| 4 | **D. `delete_missing` scope** (`boundary-delete-missing`, `main-delete-missing-conflict`) | 2 | **18 / 24** | 5 | steps **and** route |
| 5 | **B. CreativeItem / CreativeVariables** | 6 | **18 / 24** | 15 | steps **and** route |
| 6 | **A. VAST / DAAST tracker assets** | 5 | **15 / 20** | 15 | steps **and** route |
| 7 | **F. error details** (conflict / creative-rejected / policy-violation) | 3 | **9 / 12** | 4 | steps **and** route |
| 8 | **H. rows inside already-wired outlines** | 4 | **9 / 14** | 10 | **steps only** — plus deleting 2 `_SELECTIVE_XFAIL` rows and 2 `dormant_scenarios.txt` lines |
| 9 | **G. BR-RULE-093 / 039 invariants** | 3 | **7 / 10** | 4 | steps **and** route; `rule-039-inv5-lenient` also needs its strict marker re-decided |

Totals for items 2-9: **31 identities, 128 in-process nodes (174 across 4 transports),
63 template lines / 85 rendered sentences.**

**Ordering rationale.** Item 1 is first because it is the only red in the tree, it is a
harness fix with no protocol question attached, and it is a prerequisite for trusting any
`e2e_rest` result the later items produce — a newly wired scenario that seeds a product the
in-process way will join the same 22. Items 2-4 are next on nodes-per-sentence (E, C and D
unblock 70 in-process nodes for 19 template lines, because they are Scenario Outlines whose
`<expected>` column multiplies). Items 5-7 and 9 are one-row scenarios: real work per
sentence, low leverage. Item 8 is small but cheap and is the only group whose route already
permits execution.

**Before writing any step body**, note that every one of the 85 sentences asserts protocol
behavior — per-creative `status` enums, `delete_missing` semantics, VAST/DAAST tracker
shape, `CreativeVariables`, sandbox flags, error `suggestion` fields. CLAUDE.md's
spec-grounding gate applies: cite the AdCP 3.1.1 section and the conformance storyboard step
before implementing, because several of these scenarios' expectations (e.g. the
`sandbox`-flag ones, the `submitted`-status ones) are exactly the kind that turned out to be
over-specified elsewhere in this tree.

## Artifacts

- Probe source: `bind_probe.py` / `corpus_probe.py` (session scratchpad; re-runnable with
  `BIND_PROBE_UC=UC-006 BDD_E2E_ENABLED=true pytest tests/bdd/ -p bind_probe --collect-only -q`)
- Probe output: `uc006_bind_inproc.json` (708 records), `uc006_bind_e2e.json` (978 records)
- Run artifacts read: `test-results/innet_090926_1728/{bdd_inprocess,bdd_e2e}.json`,
  `test-results/innet_090926_1728/payloads/{bdd_inprocess,bdd_e2e}.json`, and
  `bdd_e2e.json` from `innet_090926_1255`, `innet_090926_1510`, `innet_080926_1734`

## Appendix: the probe, verbatim

Drop as `bind_probe.py` on `PYTHONPATH`, then
`BIND_PROBE_OUT=out.json BIND_PROBE_UC=UC-006 BDD_E2E_ENABLED=true pytest tests/bdd/ -p bind_probe --collect-only -q -p no:randomly`.
It touches no database and starts nothing; collection is all it needs.

```python
"""Collection-time probe: render every UC-006 scenario and ask pytest-bdd's own
binder whether each rendered step has a definition. Also record the ENV_ROUTES row."""

from __future__ import annotations

import json
import os

OUT = os.environ.get("BIND_PROBE_OUT", "/tmp/bind_probe.json")
UC_FILTER = os.environ.get("BIND_PROBE_UC", "")  # e.g. "UC-006"; empty = all


def pytest_collection_modifyitems(session, config, items):
    from pytest_bdd.scenario import find_fixturedefs_for_step

    from scripts.audit import storyboard_spec
    from tests.bdd.conftest import ENV_ROUTES
    from tests.helpers.marker_names import derive_marker_names

    fm = session._fixturemanager
    records = []
    for item in items:
        templated = getattr(getattr(item, "function", None), "__scenario__", None)
        if templated is None:
            continue
        markers = derive_marker_names(item)
        if UC_FILTER and not any(UC_FILTER in m for m in markers):
            continue

        callspec = getattr(item, "callspec", None)
        example = (callspec.params.get("_pytest_bdd_example") or {}) if callspec else {}
        scenario = templated.render(example)

        steps = []
        for i, step in enumerate(scenario.steps):
            defs = list(find_fixturedefs_for_step(step=step, fixturemanager=fm, node=item))
            mods = set()
            for d in defs:
                sfc = getattr(d.func, "_pytest_bdd_step_context", None)
                sf = getattr(sfc, "step_func", None)
                mods.add(getattr(sf, "__module__", "?"))
            steps.append(
                {
                    "type": step.type,
                    "rendered": step.name,
                    "template": templated.steps[i].name,
                    "line": step.line_number,
                    "bound": bool(defs),
                    "modules": sorted(mods),
                }
            )

        route = storyboard_spec.resolve_env_route(markers, ENV_ROUTES)
        records.append(
            {
                "nodeid": item.nodeid,
                "feature": scenario.feature.filename,
                "scenario": scenario.name,
                "markers": sorted(markers),
                "example": example,
                "route_tag": getattr(route, "tag", None) if route else None,
                "route_xfail": getattr(route, "xfail_reason", None) if route else None,
                "steps": steps,
            }
        )

    with open(OUT, "w") as fh:
        json.dump(records, fh, indent=1)
    print(f"\n[bind_probe] wrote {len(records)} records to {OUT}")
```

The corpus probe that proved the DARK bucket empty imports every module under
`tests/bdd/steps/` (`PYTHONPATH=. python …`), collects each `pytestbdd_stepdef_*`
attribute's `_pytest_bdd_step_context`, and calls `parser.is_matching(sentence)` for every
unbound sentence — so it asks the same question the binder asks, minus the node-scoping
that makes an unregistered module invisible.
