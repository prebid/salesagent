# salesagent-qjooi re-derived: UC-002 triage

Re-measured 2026-09-10 on `exec/itsii-1-drop-impl` (8233 collected BDD nodes).
Every number in the ticket was treated as unverified and re-derived. Three of the
ticket's four headline claims are wrong; the fourth (the shared-cause conjecture)
is right and its cause is now named.

## Instrument

A collection-time sweep (`--collect-only`, no test run) that, for every collected
pytest-bdd node:

* renders the scenario the way pytest-bdd does at run time —
  `ScenarioTemplate.render(callspec.params["_pytest_bdd_example"])` — and asks
  pytest-bdd's own `find_fixturedefs_for_step(step, fixturemanager, node)`
  against the real `FixtureManager` and the real node whether each rendered step
  binds;
* resolves the ENV_ROUTES row through the shared resolver the conftest itself
  uses (`storyboard_spec.resolve_env_route(marker_names, ENV_ROUTES)`), so
  routing is not re-implemented;
* records the UC through `storyboard_spec.detect_uc`, the same function
  `_harness_env` calls.

The sweep's predictions were then **joined against the newest full box run**
(`test-results/innet_090926_1728/`, `bdd_inprocess.json` + `payloads/`). The join
is exact: see [Validation](#validation-the-static-sweep-matches-the-run-exactly).
No test run was launched for this triage.

Two units are reported everywhere, because they answer different questions:

* **RENDERED sentence** — what pytest-bdd looks up. One per Examples row.
* **TEMPLATE line** — what a human edits in the `.feature` file. One
  parameterized step definition can satisfy many rendered sentences.

## Denominators

| population | count |
|---|---|
| BDD nodes collected in-process (mcp/a2a/rest) | 8233 |
| nodes where `detect_uc == "UC-002"` | **1409** (a2a 491, mcp 459, rest 459) |
| distinct Gherkin scenario blocks behind them | **193** |
| feature files | `BR-UC-002-create-media-buy.feature` (183 scenario blocks), `-nfr-enforcement` (3), `local-constraint-relaxation-rejections` (3), `-account-access` (2), `-media-buy-status-dual-emit` (1), `-manual-overrides` (1) |

Every count below is out of those 1409 nodes / 193 scenarios unless stated.

## Ticket claimed X / measured Y

| # | ticket claim | measured | why they differ |
|---|---|---|---|
| 1 | "133 DISTINCT unbound sentences" | **337 rendered / 274 template**, of which **240 rendered / 228 template** are undefined anywhere | 133 is a *run* statistic, and a run collapses the number three times: `_execute_scenario` stops at the FIRST missing step, so every later gap in the same scenario is invisible; the conftest's `_normalize_step_text` then collapses Examples-row variation; and it counts only scenarios that got as far as step lookup. Applying the same two collapses to today's tree gives **150** first-missing normalized keys — the direct like-for-like successor to 133 (the residual is tree drift, see #5). Un-collapsing: 187 rendered first-missing → 337 rendered all-missing. |
| 2 | "the largest single set measured anywhere" | **false by rendered sentences, true by template lines** | UC-026 has 346 rendered missing sentences vs UC-002's 337. By template lines UC-002 (274) does lead, ahead of UC-018 (218) and UC-026 (208). See the [per-UC table](#per-uc-context). |
| 3 | "HARNESS SEEDING — a step reads a ctx key nothing writes … READ_NEVER_WRITTEN" | **refuted.** The writer exists: `_seed_media_buy_chain` (`tests/bdd/conftest.py:5115`) writes both `ctx["default_product"]` and `ctx["default_pricing_option"]`. It is simply not attached to the row the probe released — `uc002-not-wired` (`conftest.py:5670-5675`) declares `env_builder` and `xfail_reason` and **no `seed=`**. | The probe set `xfail_reason=None` on a row that never had a seed callback. `_run_env_route` (`conftest.py:5244-5270`) stashes `ctx["env"]`/`ctx["client"]` and then calls `route.seed` *only if it is not None*. So the env built and the ctx stayed empty. Every KeyError the ticket counted is an artifact of the probe method, not a defect in the harness. |
| 4 | "~474 failures that may share one cause … DO NOT file them as production defects until that is tested" | **the conjecture holds, and the cause is the same one as #3.** | Confirmed below by static attribution, not by inference. |
| 5 | baseline "146 passed / 1227 xfailed"; "108 of ~1195 wake and pass (9%), second-best after uc006" | today: **176 passed / 1230 xfailed / 3 skipped** of 1409; **185 nodes dispatched anything at all** (13.1%) | The tree moved after the probe: `T-UC-002-main`, `nfr-highvalue` and `T-UC-002-nfr-001-enforcement` were moved onto the `uc002-ext` row (see the comment at `conftest.py:5624-5641`). "Second-best after uc006" no longer holds either: by dispatch rate UC-002 is 11th of 13 buckets. |

## The two gates, reported separately

A UC-002 node executes only if (a) its steps BIND and (b) its ENV_ROUTES row has
no `xfail_reason`. Measured independently:

### Gate (b): routing

| ENV_ROUTES row | nodes | live? |
|---|---|---|
| `uc002-not-wired` (catch-all, `when=_uc("UC-002", lambda m: True)`) | **1219** | xfail at fixture setup |
| `uc002-ext` | 102 | live |
| `uc002-account` | 73 | live |
| `uc002-manual-approval` | 6 | live |
| `uc002-idempotency` | 6 | live |
| `uc002-inv-015-6` | 3 | xfail (`#1652`) |

**86.5% of UC-002 nodes never build a seeded env.** No scenario is routed
differently on different transports (0 mixed-routing scenarios).

### Gate (a): binding

| | nodes | scenarios |
|---|---|---|
| every rendered step binds | **837** | 94 |
| at least one rendered step does not bind | **572** | 99 |

### Both gates together

| | nodes | scenarios | what blocks it |
|---|---|---|---|
| fully bound **and** routed live | **187** | 49 | nothing — this is UC-002's entire executing surface |
| fully bound, route-xfailed | **650** | 45 | routing only |
| unbound only via unregistered modules, route-xfailed | **262** | 22 | module registration **and** routing |
| unbound via sentences undefined anywhere, route-xfailed | **310** | 77 | unwritten steps **and** routing |

Read the ordering off that table: **routing gates 100% of the 1222 blocked
nodes.** Writing a step definition for UC-002 today changes nothing on the wire
— the scenario still xfails at `_run_env_route` before `_execute_scenario` runs.
The ticket's instruction to fix seeding first and re-measure was directionally
right for the wrong reason: what must come first is the route row, and the seed
comes with it.

## Validation: the static sweep matches the run exactly

Joining the sweep against `test-results/innet_090926_1728` (outcomes from
`bdd_inprocess.json`, dispatch counts from `payloads/bdd_inprocess.json`):

| routing | binding | own xfail marker | run outcome | dispatched | nodes |
|---|---|---|---|---|---|
| live | bound | — | passed | yes | 176 |
| live | bound | yes | xfailed | yes | 9 |
| live | bound | yes | xfailed | **no** | 2 |
| route-xfail | bound | — | xfailed | no | 633 |
| route-xfail | bound | — | skipped | no | 3 |
| route-xfail | bound | yes | xfailed | no | 14 |
| route-xfail | unbound | — | xfailed | no | 561 |
| route-xfail | unbound | yes | xfailed | no | 11 |

Zero rows contradict the sweep. **Every one of the 1222 route-xfailed nodes has
`dispatches=0`** — none of them sent a request on any transport. UC-002's graded
surface is 185 dispatching nodes out of 1409.

## The missing-sentence list, split three ways

Full lists are in [Appendix A](#appendix-a-the-sentences). Summary:

### 1. Undefined anywhere — 240 rendered / 228 template, blocking 310 nodes across 77 scenarios

Nothing in the tree defines these, registered or not. They are concentrated in
features UC-002 has never implemented: proposal / insertion-order acceptance
(`the proposal's insertion_order requires_signature is true`, 18 nodes),
governance-decision payloads (3 sentences × 9 nodes), agent billing
(`a create_media_buy request with billing value "agent"`, 12 nodes), and
bid-based auction pricing. Zero of the 240 are shared with any other UC — the
sweep found **no** sentence overlap between UC-002's missing set and any other
bucket's, so there is no cross-UC leverage here. (The "25 sentences shared
between uc010 and uc011" that a prior instrument reported also intersect at zero
in this measurement.)

### 2. Defined only in a module that never loads — 97 rendered / 46 template, blocking 262 nodes across 22 scenarios

| module | rendered sentences | nodes | tier |
|---|---|---|---|
| `tests/bdd/steps/domain/uc002_task_query.py` | 77 (20 template lines) | 228 (10 scenarios) | DARK — in no `pytest_plugins`, imported by nothing |
| `tests/bdd/steps/generic/then_media_buy.py` | 20 (26 template lines) | 40 (12 scenarios) | unregistered helper; ONE of its functions is live by delegation (`then_success.py:13,22-25` imports `then_no_media_buy_persisted` and re-declares it under its own `@then`) |

Both are already classified in
`tests/unit/test_architecture_bdd_step_module_reachability.py:50-57` as reason
(A) "dead-pending-harness", FIXME(#2132). Neither should simply be registered —
see the work list.

### 3. Defined, bound, and xfail-routed anyway — 650 nodes across 45 scenarios

Every step binds; the route row xfails first. 30 of the 45 are the
`@partition`/`@boundary` validation outlines (optimization goals, targeting
overlay, currency consistency, pricing-option XOR, minimum spend, start/end time,
daily spend cap, budget amount, product uniqueness, format-id structure,
approval workflow, persistence timing), which account for ~600 of the 650 nodes.

14 of these nodes carry a **strict** `xfail` marker added by
`_UC002_VALIDATION_XFAIL` (`conftest.py:2139-2181`) naming a diagnosed
spec-production gap — e.g. "daily spend cap returns validation_error, not
BUDGET_TOO_LOW". Those markers are unverifiable today: the imperative
`pytest.xfail()` in `_run_env_route` fires at fixture setup, so the strict
marker never gets the chance to XPASS. Someone diagnosed these rows, wrote the
gap down, and the diagnosis has never been graded.

## The "~474 failures share one cause" claim: it holds

The ticket's four grading-disagreement buckets are all one wire code:

| ticket bucket | count |
|---|---|
| `adcp_error.code='PRODUCT_NOT_FOUND'` where scenario expects `INVALID_REQUEST` | 150 |
| "expected validation to pass but got wire error PRODUCT_NOT_FOUND" — optimization | 132 |
| … targeting | 126 |
| … creative | 66 |
| **total** | **474** |
| plus `KeyError: 'default_product'` | 102 |
| plus `KeyError: 'default_pricing_option'` | 60 |
| **grand total** | **636** |

The population that can produce those failures is exactly the fully-bound,
route-xfailed, no-own-xfail nodes: **636 today.** The split within it, attributed
statically by reading which bound step functions each node reaches:

| | nodes | mechanism |
|---|---|---|
| reaches a step that hard-indexes `ctx["default_product"]` / `ctx["default_pricing_option"]` | **153** | raises `KeyError` (ticket: 162) |
| reaches only steps that go through `_ensure_request_defaults` | **483** | falls back to the literal placeholder ids `"guaranteed_display"` / `"cpm_usd_fixed"` (`given_media_buy.py:86-97`), which are not rows in the per-test DB, so production correctly answers `PRODUCT_NOT_FOUND` (ticket: 474) |

636 = 636; 153 vs 162 and 483 vs 474 are within the tree drift between the probe
and today. The seven hard-indexing steps are all in
`tests/bdd/steps/generic/given_media_buy.py`: `given_product_configuration`,
`given_product_scenario`, `given_pricing_option_configuration`,
`given_currency_configuration`, `given_currency_scenario`,
`given_start_time_value`, `given_end_time_value`.

**Verdict.** One cause, and it is the probe's, not production's. Releasing
`uc002-not-wired`'s `xfail_reason` without attaching a `seed=` measured a harness
that had never been configured. Do not file any of the 474 as production defects.
`PRODUCT_NOT_FOUND` for a product that was never seeded is production behaving
correctly.

## The "ctx seeding gap": refuted

The ticket calls it "a seventh category … the step exists and its setup does
not." The setup does exist:

* `_seed_media_buy_chain` (`conftest.py:5115-5121`) calls
  `env.setup_media_buy_data()` and writes `tenant`, `principal`,
  `default_product`, `default_pricing_option`;
* five ENV_ROUTES rows use it or a wrapper of it
  (`_seed_media_buy_chain_create_dispatch`, `_seed_media_buy_chain_full_create`);
* the 187 live UC-002 nodes all get it, and 176 of them pass.

`uc002-not-wired` builds the *same* `MediaBuyCreateEnv` as the live rows — it
just has no `seed`. This is not a READ_NEVER_WRITTEN shape and does not need a
seventh blocker category. It is one missing keyword argument on one row.

## Per-UC context

Measured across the whole in-process corpus, so the UC-002 numbers have a
denominator to sit in:

| UC | nodes | routed live | fully bound | missing sentences (rendered) | missing (template) | dispatched | passed |
|---|---|---|---|---|---|---|---|
| UC-002 | 1409 | 187 | 837 | 337 | **274** | 185 | 176 |
| UC-003 | 1374 | 223 | 1112 | 97 | 74 | 223 | 171 |
| UC-004 | 1047 | 1047 | 603 | 282 | 199 | 519 | 565 |
| UC-019 | 753 | 753 | 330 | 227 | 116 | 375 | 267 |
| UC-026 | 728 | 0 | 0 | **346** | 208 | 0 | 0 |
| UC-006 | 708 | 527 | 580 | 85 | 77 | 517 | 455 |
| UC-005 | 626 | 626 | 487 | 88 | 32 | 488 | 444 |
| UC-010 | 611 | 464 | 464 | 106 | 66 | 464 | 302 |
| UC-018 | 501 | 63 | 49 | 274 | 218 | 43 | 36 |
| UC-011 | 337 | 328 | 328 | 10 | 10 | 328 | 319 |
| (untagged) | 108 | 108 | 108 | 0 | 0 | 108 | 108 |
| UC-GET-PRODUCTS | 18 | 18 | 18 | 0 | 0 | 18 | 18 |
| ADMIN | 13 | 13 | 13 | 0 | 0 | 0 | 13 |

`passed` can exceed `dispatched` (UC-004) where a scenario grades a non-dispatch
outcome; `dispatched` can exceed `passed` where a dispatching node still fails or
xfails.

### A blind spot this exposes

`tests/bdd/dormant_scenarios.txt` is the ratcheting ledger that exists to stop
missing bindings growing silently. It contains **zero** UC-002 entries — its 177
lines are UC-019 (78), UC-004 (60), UC-018 (22), UC-005 (15), UC-006 (2). The
ledger is populated by the `pytest_bdd_step_func_lookup_error` hook
(`conftest.py:131-137`), which only fires if the scenario reaches step lookup. A
scenario xfailed at the ENV_ROUTES gate never gets there. So UC-002's 572 dormant
nodes — and UC-026's 728, and UC-003's 262 — are invisible to the guard designed
to catch exactly them. The ledger measures dormancy only where routing already
works.

## Prioritized work list

Ordered by graded nodes unblocked per unit of work. Every item is gated on #1;
nothing below it changes a single wire byte until #1 lands.

### 1. Give the wired UC-002 scenarios a route row that carries a seed — unblocks up to 636 nodes / 45 scenarios

The whole of #1 is one new `EnvRoute` above the catch-all, `env_builder` identical
to the one already there, plus `seed=_seed_media_buy_chain_create_dispatch`, and a
frozenset naming the scenarios it claims. This is the `_UC006_*` pattern verbatim
(`conftest.py:5340-5348` documents the measurement procedure and why membership is
keyed by scenario IDENTITY, not by family tag: `@partition`/`@boundary` each span
passing, failing and step-less scenarios at once).

Do the membership measurement the way UC-006 did: set the catch-all's
`xfail_reason` to `None` locally **and add the seed**, run
`BR-UC-002-create-media-buy.feature` against a real Postgres, and group by
scenario identity. The probe recorded in the ticket did the first half only,
which is why it produced 636 failures instead of a membership list. Expect the
153 KeyError nodes and the 483 `PRODUCT_NOT_FOUND` nodes to both disappear;
what remains is the real signal.

The 14 nodes carrying `_UC002_VALIDATION_XFAIL` strict markers are the free
check on this work: once routed, those markers either fire (the gap is real) or
XPASS strictly (the gap closed and the entry is stale). Today they assert
nothing.

### 2. Rewrite or delete `uc002_task_query.py` — 228 nodes / 10 scenarios, near-zero grading value as it stands

Registering it is safe from shadowing — measured: its 11 patterns match **zero**
currently-bound step instances anywhere in the 8233-node corpus, so it steals
nothing. But registering it as written yields no grading:

* its When step does `from src.core.tools.task_management import list_tasks`
  (`uc002_task_query.py:193`). **That name does not exist** — the module exports
  `_list_tasks_impl(req, identity)`. The import raises `ImportError`, which the
  step's `except (AdCPSalesAgentError, TypeError, Exception)` swallows into
  `ctx["error"]`;
* `_xfail_on_unsupported_param` only fires on `TypeError`, so the scenarios would
  not even reach their documented xfail — they would fail on a stale import;
* the Then dispatcher `assert_task_query_outcome` documents itself as "called
  from the then_result_should_be dispatcher in uc002_create_media_buy.py"
  (`uc002_task_query.py:281`). Nothing in that registered module references it.
  The docstring is stale;
* the When step calls production in-process regardless of transport, so all
  three transports would grade the same code path.

These 10 scenarios are also about `list_tasks` while living in
`BR-UC-002-create-media-buy.feature`; UC-027 (`BR-UC-027-manage-async-tasks`) is
the use case that owns the subject. Decide their home before rewriting them.

### 3. Promote `then_media_buy.py` sentences one at a time — 40 nodes / 12 scenarios

Do **not** register this module. Registration was already measured and rejected
(salesagent-uokuq, quoted in the module docstring): it steals 127 already-bound
step instances across 5 modules. The documented path is promotion of individual
functions into a registered module as a scenario needs them, which is how two
UC-002 package Thens already moved. 20 rendered sentences over 26 template lines
remain; the affected scenarios are the `T-UC-002-inv-*` invariant blocks
(inv-006-1/2, inv-013-4, inv-017-1/2/3, inv-018-1/2, inv-020-1/2/3) at 3 nodes
each, plus `T-UC-002-alt-asap`.

### 4. Write the 228 undefined template lines — 310 nodes / 77 scenarios

Largest by sentence count, smallest by nodes-per-unit-of-work, and last in order
because these scenarios are blocked twice. Sub-order by cluster, since the
clusters are coherent features rather than scattered gaps:

| cluster | rendered | template lines | nodes touching it |
|---|---|---|---|
| proposal / insertion-order acceptance (`T-UC-002-v31-io-acceptance-*`, `-boundary-io-acceptance`, `alt-proposal`) | 28 | 20 | 49 |
| governance-decision payload (`the account has governance_agents configured`, …) | 23 | 26 | 45 |
| agent billing (`billing value "agent"`, `supported_billing capability includes "agent"`) | 23 | 22 | 39 |
| bid-based auction pricing (`max_bid`, `bid_price` ceiling semantics) | 4 | 4 | 3 |
| everything else | 162 | 156 | 229 |

Node counts overlap: one node blocked by sentences from two clusters is counted
in both, so the column sums to more than 310.

Note that `T-UC-002-alt-proposal`, `-ext-l` and `-ext-m` already carry xfail
entries in the tag map (`conftest.py:576-577,634`) declaring proposal-based
creation unimplemented in production. Writing steps for the proposal cluster
produces graded xfails, not passes — worth doing for the ledger, not for the
pass count. **Apply the spec-grounding gate before writing any of these**: each
sentence needs its AdCP 3.1.1 section and storyboard step cited first.

## Appendix A: the sentences

### A1. Sentences undefined anywhere — 240 rendered, grouped by owning scenario

| scenario tag | feature line | nodes | keyword | sentence |
|---|---|---|---|---|
| T-UC-002-v31-io-acceptance-incomplete | 2344 | 9 | given | `the proposal's insertion_order requires_signature is true` |
|  |  | 3 | given | `a create_media_buy request with proposal_id "prop-abc-2026" and an io_acceptance missing "io_id"` |
|  |  | 3 | then | `the error should reference the missing "io_id" member` |
|  |  | 3 | given | `a create_media_buy request with proposal_id "prop-abc-2026" and an io_acceptance missing "accepted_at"` |
|  |  | 3 | then | `the error should reference the missing "accepted_at" member` |
|  |  | 3 | given | `a create_media_buy request with proposal_id "prop-abc-2026" and an io_acceptance missing "signatory"` |
|  |  | 3 | then | `the error should reference the missing "signatory" member` |
| T-UC-002-boundary-io-acceptance | 2797 | 3 | given | `a proposal-based create_media_buy request matching "requires_signature false, io_acceptance absent (no gate)"` |
|  |  | 3 | given | `a proposal-based create_media_buy request matching "requires_signature true, io_acceptance present with all required members"` |
|  |  | 3 | given | `a proposal-based create_media_buy request matching "requires_signature true, io_acceptance present with signature_id also supplied"` |
|  |  | 3 | given | `a proposal-based create_media_buy request matching "requires_signature false, io_acceptance present (no-op)"` |
|  |  | 3 | given | `a proposal-based create_media_buy request matching "requires_signature true, io_acceptance absent (gate blocks)"` |
|  |  | 3 | given | `a proposal-based create_media_buy request matching "requires_signature true, io_acceptance present but io_id missing"` |
|  |  | 3 | given | `a proposal-based create_media_buy request matching "requires_signature true, io_acceptance present but accepted_at missing"` |
|  |  | 3 | given | `a proposal-based create_media_buy request matching "requires_signature true, io_acceptance present but signatory missing"` |
| T-UC-002-storyboard-async-submitted-envelope-task-id-roundtrip | 2845 | 3 | given | `a comply_test_controller directive registered force_create_media_buy_arm with branch "submitted" and task_id "task_async_signed_io_q2"` |
|  |  | 3 | given | `the directive is keyed to the caller's authenticated sandbox account` |
|  |  | 3 | when | `the Buyer Agent sends create_media_buy under the registered sandbox account` |
|  |  | 3 | then | `the response should carry status "submitted"` |
|  |  | 3 | then | `the response should carry task_id "task_async_signed_io_q2"` |
|  |  | 3 | then | `the response should NOT carry media_buy_id on the submitted envelope` |
|  |  | 3 | then | `the response should NOT carry packages on the submitted envelope` |
|  |  | 3 | then | `the task_id on the response should match the value registered by the controller directive` |
| T-UC-002-v31-idempotency-pattern-invalid | 2104 | 6 | then | `the error should reference idempotency_key constraint "pattern [A-Za-z0-9_.:-] violated"` |
|  |  | 3 | given | `a create_media_buy request with idempotency_key "short"` |
|  |  | 3 | then | `the error should reference idempotency_key constraint "minLength 16 violated"` |
|  |  | 3 | given | `a create_media_buy request with idempotency_key "key with spaces in it that is long enough"` |
|  |  | 3 | given | `a create_media_buy request with idempotency_key "key/with/slashes/that/is/also/long/enough"` |
|  |  | 3 | given | `a create_media_buy request with idempotency_key "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"` |
|  |  | 3 | then | `the error should reference idempotency_key constraint "maxLength 255 violated"` |
| T-UC-002-boundary-billing-eligibility | 2762 | 3 | given | `a create_media_buy request whose resolved billing reflects "billing resolved = operator; operator in supported_billing; agent permitted"` |
|  |  | 3 | given | `a create_media_buy request whose resolved billing reflects "billing resolved = advertiser; seller's supported_billing = [operator, agent]"` |
|  |  | 3 | given | `a create_media_buy request whose resolved billing reflects "billing resolved = operator; seller supports operator generally; no operator billing relationship on this account"` |
|  |  | 3 | given | `a create_media_buy request whose resolved billing reflects "billing resolved = agent; agent in supported_billing; calling agent is passthrough-only"` |
|  |  | 3 | given | `a create_media_buy request whose resolved billing reflects "billing resolved = agent; caller unauthenticated"` |
|  |  | 3 | given | `a create_media_buy request whose resolved billing reflects "invoice_recipient supplied = business entity not authorized for this account"` |
| T-UC-002-boundary-sandbox-response | 2744 | 3 | given | `a create_media_buy request whose account resolution matches "sandbox: true in response (sandbox account)"` |
|  |  | 3 | given | `a create_media_buy request whose account resolution matches "sandbox absent in response (production account)"` |
|  |  | 3 | given | `a create_media_buy request whose account resolution matches "sandbox: false in response (explicit production)"` |
|  |  | 3 | given | `a create_media_buy request whose account resolution matches "sandbox account with invalid budget (real validation error)"` |
|  |  | 3 | given | `a create_media_buy request whose account resolution matches "sandbox: true on create_media_buy synchronous success shape"` |
|  |  | 3 | given | `a create_media_buy request whose account resolution matches "sandbox present on create_media_buy terminal-failure (errors) shape"` |
| T-UC-002-inv-017-4 | 827 | 3 | given | `a create_media_buy request that requires manual approval` |
|  |  | 3 | given | `the response returned a submitted task envelope with a task_id` |
|  |  | 3 | when | `the approval task is paused awaiting the human decision` |
|  |  | 3 | then | `the task status should be "input-required"` |
|  |  | 3 | then | `on completion the task should resolve to "completed" with a media_buy_id on the completion artifact or to "rejected"` |
|  |  | 3 | then | `"pending_approval" should never be used as a MediaBuyStatus value` |
| T-UC-002-inv-018-8 | 888 | 3 | given | `a create_media_buy request that produces a response` |
|  |  | 3 | when | `the task fails fatally` |
|  |  | 3 | then | `both the protocol envelope "adcp_error" field and the payload "errors" array should be populated` |
|  |  | 3 | then | `when only a non-fatal warning occurs only the payload "errors" array should carry it with severity "warning"` |
|  |  | 3 | then | `a non-fatal warning should not set the envelope "adcp_error" field` |
|  |  | 3 | then | `the envelope should not emit the legacy "task_status" or "response_status" fields` |
| T-UC-002-storyboard-governance-approved | 2865 | 3 | then | `the response should carry the media_buy_id` |
|  |  | 3 | given | `the buyer attaches the governance_decision payload to the create_media_buy request` |
|  |  | 3 | when | `the Buyer Agent sends create_media_buy with the governance_decision payload` |
|  |  | 3 | then | `the response should carry status "active" or "pending_start"` |
|  |  | 3 | given | `the buyer's governance agent has returned decision "APPROVED" for the proposed buy` |
|  |  | 3 | then | `the response should echo the governance_decision with decision "APPROVED"` |
| T-UC-002-storyboard-governance-denied-recovery | 2912 | 3 | then | `the response should carry the media_buy_id` |
|  |  | 3 | then | `the response should carry status "active" or "pending_start"` |
|  |  | 3 | given | `a previous create_media_buy attempt failed with error code "GOVERNANCE_DENIED"` |
|  |  | 3 | given | `the buyer reduces the proposed buy to within the governance agent's spending authority` |
|  |  | 3 | given | `the buyer obtains a new "APPROVED" decision from the governance agent` |
|  |  | 3 | when | `the Buyer Agent sends a corrected create_media_buy with the new APPROVED governance_decision` |
| T-UC-002-storyboard-governance-with-conditions | 2880 | 3 | then | `the response should carry the media_buy_id` |
|  |  | 3 | given | `the buyer attaches the governance_decision payload to the create_media_buy request` |
|  |  | 3 | when | `the Buyer Agent sends create_media_buy with the governance_decision payload` |
|  |  | 3 | given | `the buyer's governance agent has returned decision "APPROVED_WITH_CONDITIONS" with a non-empty conditions array` |
|  |  | 3 | then | `the response should echo the governance_decision with decision "APPROVED_WITH_CONDITIONS"` |
|  |  | 3 | then | `the response should carry the conditions array attached to the persisted buy` |
| T-UC-002-v31-success-status-values | 2214 | 9 | then | `the response status field should not equal "submitted"` |
|  |  | 3 | given | `a valid create_media_buy request producing the "creatives missing for all packages" condition` |
|  |  | 3 | given | `a valid create_media_buy request producing the "creatives present, future flight" condition` |
|  |  | 3 | given | `a valid create_media_buy request producing the "creatives present, asap flight" condition` |
| T-UC-002-boundary-plan-id-governance | 2780 | 3 | given | `a create_media_buy request and account governance state matching "governance agents configured + plan_id present + plan resolves"` |
|  |  | 3 | given | `a create_media_buy request and account governance state matching "governance agents configured + plan_id absent"` |
|  |  | 3 | given | `a create_media_buy request and account governance state matching "governance agents configured + plan_id present + plan not found"` |
|  |  | 3 | given | `a create_media_buy request and account governance state matching "no governance agents + plan_id absent"` |
|  |  | 3 | given | `a create_media_buy request and account governance state matching "no governance agents + plan_id present"` |
| T-UC-002-inv-006-5 | 714 | 3 | given | `a package uses a bid-based auction pricing model with max_bid set to true` |
|  |  | 3 | given | `the buyer supplies a bid_price above the floor_price` |
|  |  | 3 | then | `the pricing option should be accepted` |
|  |  | 3 | then | `the buyer bid_price should be interpreted as a ceiling rather than an exact price` |
|  |  | 3 | then | `the seller may clear at any price between the floor_price and the bid_price` |
| T-UC-002-inv-214-8 | 2635 | 3 | given | `a create_media_buy request that carries an invoice_recipient overriding the account default billing entity` |
|  |  | 3 | given | `the invoice_recipient is not authorized for the account` |
|  |  | 3 | then | `the seller should validate the invoice_recipient authorization before the buy proceeds` |
|  |  | 3 | then | `the buy should be rejected at the buy-time billing-eligibility gate` |
|  |  | 3 | then | `no dedicated error code is defined in v3.1 for this rejection` |
| T-UC-002-storyboard-inventory-list-no-match | 2929 | 3 | given | `the buyer references a property_list whose entries do not match any seller inventory` |
|  |  | 3 | given | `the buyer references a collection_list whose entries do not match any seller inventory` |
|  |  | 3 | when | `the Buyer Agent sends create_media_buy with the no-match list references in targeting_overlay` |
|  |  | 3 | then | `the response should NOT be a silent success with normal forecast numbers` |
|  |  | 3 | then | `one of the following two outcomes should be observed:` |
| T-UC-002-storyboard-inventory-list-targeting-parity | 2949 | 3 | then | `the response should carry the media_buy_id` |
|  |  | 3 | given | `the buyer holds a property_list (agent_url, list_id) that matches seller inventory` |
|  |  | 3 | given | `the buyer holds a collection_list (agent_url, list_id) that matches seller inventory` |
|  |  | 3 | when | `the Buyer Agent sends create_media_buy with property_list and collection_list in package targeting_overlay` |
|  |  | 3 | then | `the persisted package targeting should reflect the property_list and collection_list references` |
| T-UC-002-v31-error-billing-not-permitted-for-agent-suggested | 2592 | 3 | given | `a create_media_buy request with billing value "agent"` |
|  |  | 3 | given | `the seller's supported_billing capability includes "agent" generally` |
|  |  | 3 | given | `the calling agent's commercial relationship with the seller is passthrough-only` |
|  |  | 3 | then | `the error details rejected_billing should be "agent"` |
|  |  | 3 | then | `the error details suggested_billing should be "operator"` |
| T-UC-002-v31-error-billing-not-supported-account | 2562 | 3 | given | `a create_media_buy request with billing value "agent"` |
|  |  | 3 | given | `the seller's supported_billing capability includes "agent" generally` |
|  |  | 3 | given | `the seller does not accept "agent" billing on the account "acc-001" relationship` |
|  |  | 3 | given | `the caller's agent identity is established` |
|  |  | 3 | then | `the error details scope should be "account"` |
| T-UC-002-v31-plan-id-resolves | 2250 | 3 | given | `the account has governance_agents configured` |
|  |  | 3 | then | `the request should proceed past the governance gate` |
|  |  | 3 | given | `a valid create_media_buy request with plan_id "plan-001"` |
|  |  | 3 | given | `the plan "plan-001" resolves and is accessible to the account` |
|  |  | 3 | then | `the plan_id should be forwarded to check_governance` |
| T-UC-002-inv-080-11 | 1001 | 3 | given | `the resolved account has a terminal lifecycle status of "rejected" or "closed"` |
|  |  | 3 | then | `the account should be treated as not operational and unable to create media buys` |
|  |  | 3 | then | `no dedicated error code exists in error-code.json for this state` |
|  |  | 3 | then | `the resolution outcome is unspecified by the protocol` |
| T-UC-002-storyboard-pending-creatives-state-transition | 2982 | 3 | given | `the buyer sends create_media_buy without inline creatives` |
|  |  | 3 | then | `the response should carry status "pending_creatives"` |
|  |  | 3 | when | `the buyer subsequently completes sync_creatives for all required creatives` |
|  |  | 3 | then | `the buy's status should transition to "pending_start"` |
| T-UC-002-v31-billing-eligible-proceeds | 2623 | 3 | given | `a valid create_media_buy request with billing value "operator"` |
|  |  | 3 | given | `the seller's supported_billing capability includes "operator"` |
|  |  | 3 | given | `the calling agent's commercial relationship permits "operator" billing` |
|  |  | 3 | then | `the request should proceed past the billing eligibility gate` |
| T-UC-002-v31-committed-metrics-standard-accepted | 2420 | 3 | given | `the request package includes committed_metrics with one entry of scope "standard"` |
|  |  | 3 | given | `the proposed standard metric_id is present in the product's available_metrics` |
|  |  | 3 | then | `the response package committed_metrics should echo the standard metric_id` |
|  |  | 3 | then | `the response package committed_metrics entry should include a "committed_at" timestamp` |
| T-UC-002-v31-error-billing-not-permitted-for-agent-terminal | 2608 | 3 | given | `a create_media_buy request with billing value "advertiser"` |
|  |  | 3 | given | `the calling agent has no retryable billing value for this seller's commercial relationship` |
|  |  | 3 | then | `the error details rejected_billing should be "advertiser"` |
|  |  | 3 | then | `the error details should NOT include a "suggested_billing" field` |
| T-UC-002-v31-error-billing-not-supported-capability | 2546 | 3 | given | `a create_media_buy request with billing value "agent"` |
|  |  | 3 | given | `the seller's supported_billing capability is ["operator", "advertiser"]` |
|  |  | 3 | then | `the error details scope should be "capability"` |
|  |  | 3 | then | `the error details supported_billing should equal ["operator", "advertiser"]` |
| T-UC-002-v31-error-conflict-details | 2513 | 3 | then | `the response should indicate a correctable failure` |
|  |  | 3 | given | `a create_media_buy request with idempotency_key "buy-2026-q1-conflict-001"` |
|  |  | 3 | given | `a prior media buy "mb-789" exists for the same (seller, idempotency_key) pair with revision 1` |
|  |  | 3 | given | `the new request payload diverges from the prior request payload` |
| T-UC-002-v31-invoice-recipient-echoed | 2290 | 3 | given | `a valid create_media_buy request with an invoice_recipient override` |
|  |  | 3 | given | `the invoice_recipient is authorized for the account` |
|  |  | 3 | then | `the response should include an "invoice_recipient" matching the request` |
|  |  | 3 | then | `the response invoice_recipient should not include any bank-detail fields` |
| T-UC-002-v31-io-acceptance-not-required | 2364 | 3 | given | `a valid create_media_buy request with proposal_id "prop-no-sig-2026" and the io_acceptance field omitted` |
|  |  | 3 | given | `the proposal's insertion_order requires_signature is false` |
|  |  | 3 | then | `the request should proceed to further validation` |
|  |  | 3 | then | `the io_acceptance field should not affect processing` |
| T-UC-002-v31-plan-id-not-found | 2263 | 3 | then | `the response should indicate a correctable failure` |
|  |  | 3 | given | `the account has governance_agents configured` |
|  |  | 3 | given | `a create_media_buy request with plan_id "plan-missing"` |
|  |  | 3 | given | `the plan "plan-missing" does not exist or is not accessible to the account` |
| T-UC-002-v31-plan-id-optional-without-governance | 2277 | 3 | then | `the request should proceed past the governance gate` |
|  |  | 3 | given | `a valid create_media_buy request with the plan_id field omitted` |
|  |  | 3 | given | `the account has no governance_agents configured` |
|  |  | 3 | then | `the governance path should be skipped` |
| T-UC-002-v31-submitted-envelope-message | 2178 | 3 | when | `the seller emits a submitted envelope with a human-readable message` |
|  |  | 3 | then | `the buyer SHOULD treat the message field as untrusted input` |
|  |  | 3 | then | `the buyer SHOULD escape the message before rendering to HTML` |
|  |  | 3 | then | `the buyer SHOULD sanitize the message before passing to an LLM prompt context` |
| T-UC-002-boundary-targeting-collection-list | 2831 | 3 | given | `a create_media_buy request with package targeting matching "collection_list with valid agent_url and list_id"` |
|  |  | 3 | given | `a create_media_buy request with package targeting matching "collection_list_exclude with valid agent_url and list_id"` |
|  |  | 3 | given | `a create_media_buy request with package targeting matching "both collection_list and collection_list_exclude set"` |
| T-UC-002-inv-020-5 | 946 | 3 | then | `the ad server adapter should never be invoked` |
|  |  | 3 | given | `the request is sent in dry-run mode` |
|  |  | 3 | then | `a simulated success should be returned` |
| T-UC-002-storyboard-governance-denied | 2896 | 3 | given | `the buyer attaches the governance_decision payload to the create_media_buy request` |
|  |  | 3 | when | `the Buyer Agent sends create_media_buy with the governance_decision payload` |
|  |  | 3 | given | `the buyer's governance agent has returned decision "DENIED" with a denial reason` |
| T-UC-002-storyboard-measurement-terms-rejected | 2965 | 3 | given | `the buyer attaches measurement_terms that the seller will not accept` |
|  |  | 3 | when | `the Buyer Agent sends create_media_buy with the unworkable measurement_terms` |
|  |  | 3 | then | `the error details should identify which measurement_terms are unworkable` |
| T-UC-002-v31-advertiser-industry-per-buy | 2390 | 3 | given | `the brand "acme.com" operates across industries ["healthcare.wellness", "cpg"]` |
|  |  | 3 | given | `a valid create_media_buy request with advertiser_industry "healthcare.wellness"` |
|  |  | 3 | then | `the seller should map advertiser_industry to its platform-native industry code` |
| T-UC-002-v31-committed-metrics-rejected | 2436 | 3 | given | `the request package includes committed_metrics with standard metric_id not in the product's available_metrics` |
|  |  | 3 | then | `the response should indicate an error` |
|  |  | 3 | then | `the error should reference the offending committed_metrics entry` |
| T-UC-002-v31-error-billing-not-supported-unauth-omits-scope | 2578 | 3 | given | `a create_media_buy request with billing value "agent"` |
|  |  | 3 | given | `the caller's agent identity is NOT established` |
|  |  | 3 | then | `the error details should NOT include a "scope" field` |
| T-UC-002-v31-idempotency-expired | 2137 | 3 | then | `the response should indicate a correctable failure` |
|  |  | 3 | given | `a create_media_buy request with idempotency_key "buy-2026-q1-expired-001"` |
|  |  | 3 | given | `the (seller, account, idempotency_key) pair was recorded but its cached response expired past replay_ttl_seconds` |
| T-UC-002-v31-io-acceptance-provided | 2322 | 3 | given | `the proposal's insertion_order requires_signature is true` |
|  |  | 3 | given | `the request includes io_acceptance with:` |
|  |  | 3 | given | `the io_acceptance.io_id matches the proposal's insertion_order.io_id` |
| T-UC-002-v31-io-acceptance-required | 2307 | 3 | given | `the proposal's insertion_order requires_signature is true` |
|  |  | 3 | then | `the response should indicate a correctable failure` |
|  |  | 3 | given | `the request does not include an io_acceptance field` |
| T-UC-002-v31-io-acceptance-signature-id | 2376 | 3 | given | `the proposal's insertion_order requires_signature is true` |
|  |  | 3 | then | `the IO acceptance gate should be satisfied` |
|  |  | 3 | then | `the signature_id should be recorded as a signing-service reference` |
| T-UC-002-v31-offering-referenced-via-package-catalog | 2649 | 3 | given | `a package targets a catalog whose items are brand offerings` |
|  |  | 3 | given | `the referenced offering "summer-sale" has structured asset groups including "headlines" and "images_landscape"` |
|  |  | 3 | then | `the package should be bound to the offering's asset groups for creative assembly` |
| T-UC-002-v31-package-paused-on-creation | 2405 | 3 | given | `a valid create_media_buy request with one package having paused set to true` |
|  |  | 3 | then | `the paused package should be created in a paused state` |
|  |  | 3 | then | `the paused package should not deliver impressions until resumed` |
| T-UC-002-v31-planned-delivery-with-governance | 2450 | 3 | given | `a valid create_media_buy request with plan_id "plan-2026-q1"` |
|  |  | 3 | given | `the account "acc-001" has governance_agents configured` |
|  |  | 3 | then | `the response should include a "planned_delivery" block` |
| T-UC-002-v31-price-shape-on-proposal-item | 2706 | 3 | given | `a proposal "prop-001" referenced by proposal_id` |
|  |  | 3 | given | `the proposal's product allocation item carries a price {amount: 1200, currency: "USD", period: "one_time"}` |
|  |  | 3 | given | `the total_budget covers the proposal allocations` |
| T-UC-002-boundary-creative-asset-discriminator | 2817 | 3 | given | `a create_media_buy request with an inline creative matching "asset slot value with missing asset_type discriminator"` |
|  |  | 3 | given | `a create_media_buy request with an inline creative matching "asset slot value with unknown asset_type value"` |
| T-UC-002-inv-010-3 | 749 | 3 | then | `the product-uniqueness check should have no applicable buyer input` |
|  |  | 3 | then | `the request should not be rejected on the product-uniqueness rule` |
| T-UC-002-inv-015-6 | 332 | 3 | given | `a create_media_buy request with an inline creative whose assets map value lacks an "asset_type" field` |
|  |  | 3 | then | `the error should reference the unresolvable asset_type discriminator` |
| T-UC-002-inv-020-4 | 934 | 3 | then | `the ad server adapter should never be invoked` |
|  |  | 3 | given | `pre-adapter validation fails on creative completeness or adapter pricing/budget constraints` |
| T-UC-002-inv-026-1 | 960 | 3 | given | `all referenced creatives exist in valid state with compatible formats` |
|  |  | 3 | then | `the creative assignment should proceed` |
| T-UC-002-v31-agency-estimate-number-buy-level | 2482 | 3 | given | `a valid create_media_buy request with agency_estimate_number "AE-2026-Q1-12345"` |
|  |  | 3 | then | `the agency_estimate_number should be persisted on the media buy` |
| T-UC-002-v31-artifact-webhook-config | 2465 | 3 | given | `the artifact_webhook authentication scheme is "Bearer"` |
|  |  | 3 | then | `the seller should accept the artifact_webhook configuration for governance content delivery` |
| T-UC-002-v31-catchment-isochrone | 2666 | 3 | given | `the targeting includes a store_catchments entry with catchment_id "drive", travel_time {value:15, unit:"min"}, and transport_mode "driving"` |
|  |  | 3 | then | `the catchment should be accepted as an isochrone definition` |
| T-UC-002-v31-catchment-radius | 2680 | 3 | given | `the targeting includes a store_catchments entry with catchment_id "local" and radius {value:5, unit:"km"}` |
|  |  | 3 | then | `the catchment should be accepted as a radius definition` |
| T-UC-002-v31-catchment-xor-violation | 2693 | 3 | given | `the targeting includes a store_catchments entry providing BOTH radius and geometry` |
|  |  | 3 | then | `the error should reference the catchment oneOf constraint` |
| T-UC-002-v31-frequency-cap-scope-unregistered | 2733 | 3 | given | `the targeting frequency_cap.scope is "campaign"` |
|  |  | 3 | then | `the error should reference the frequency_cap.scope enum` |
| T-UC-002-v31-idempotency-canonical-comparison | 2150 | 3 | given | `a media buy was already created for the same seller with idempotency_key "buy-2026-q1-canon-001"` |
|  |  | 3 | given | `a create_media_buy request with idempotency_key "buy-2026-q1-canon-001" whose payload differs only in field ordering and insignificant whitespace` |
| T-UC-002-v31-idempotency-in-flight | 2123 | 3 | given | `a create_media_buy request with idempotency_key "buy-2026-q1-inflight-001"` |
|  |  | 3 | given | `a prior request for the same (seller, account, idempotency_key) pair is still in flight` |
| T-UC-002-v31-plan-id-required-when-governance | 2236 | 3 | given | `the account has governance_agents configured` |
|  |  | 3 | given | `a create_media_buy request with the plan_id field omitted` |
| T-UC-002-v31-submitted-envelope-shape | 2163 | 3 | then | `the response should not include a media_buy_id at the envelope level` |
|  |  | 3 | then | `the response should not include a packages array at the envelope level` |
| T-UC-002-alt-creatives | 110 | 1 | given | `each creative has a valid format_id, name, and assets with URL and dimensions` |
|  |  | 1 | given | `the creative agent has the referenced formats registered` |
|  |  | 1 | then | `the system should upload the creatives to the creative library` |
|  |  | 1 | then | `the system should assign the uploaded creatives to packages` |
|  |  | 1 | then | `the response should include the created media buy with creative assignments` |
| T-UC-002-alt-proposal | 125 | 1 | given | `proposal "prop-2026-001" exists and has not expired` |
|  |  | 1 | given | `the proposal has 3 product allocations` |
|  |  | 1 | then | `the system should derive packages from proposal allocations` |
|  |  | 1 | then | `the total_budget should be distributed per allocation percentages` |
|  |  | 1 | then | `the response should include the created media buy with derived packages` |
| T-UC-002-alt-manual-reject | 86 | 3 | when | `the Seller rejects the media buy with reason "Budget too low for Q1 campaign"` |
| T-UC-002-alt-manual-reject-override | 24 | 3 | when | `the Seller rejects the media buy with reason "Budget too low for Q1 campaign"` |
| T-UC-002-boundary-start-time-package-scope | 1679 | 3 | given | `a package in packages[] carries its own start_time of asap` |
| T-UC-002-inv-012-5 | 762 | 3 | then | `the media buy total budget should be validated against the daily cap as a single daily figure` |
| T-UC-002-inv-018-4 | 859 | 3 | given | `the system returns a transient error (RATE_LIMITED)` |
| T-UC-002-inv-018-6 | 879 | 3 | given | `a create_media_buy request with account_id that does not exist` |
| T-UC-002-v31-error-policy-violation-details | 2530 | 3 | given | `a create_media_buy request that violates the seller's editorial policy` |
| T-UC-002-v31-frequency-cap-scope-package | 2721 | 3 | given | `the targeting frequency_cap.scope is "package"` |
| T-UC-002-inv-087-7 | 1038 | 1 | given | `a package has an event kind optimization goal with target kind "per_ad_spend"` |
|  |  | 1 | given | `no event_sources entry has value_field set` |
| T-UC-002-inv-026-2 | 969 | 1 | given | `a referenced creative is in "error" state` |
| T-UC-002-inv-026-4 | 979 | 1 | given | `a creative format is incompatible with the product's supported formats` |
| T-UC-002-inv-087-5 | 1014 | 1 | given | `a package has two optimization goals with the same priority value` |
| T-UC-002-inv-087-6 | 1026 | 1 | given | `a package has optimization_goals as an empty array` |

### A2. Sentences defined only in an unregistered module — 97 rendered

| module | keyword | sentence | nodes |
|---|---|---|---|
| uc002_task_query | when | `the Buyer Agent queries the task list` | 228 |
| then_media_buy | then | `the approval path should be manual` | 6 |
| then_media_buy | then | `the media buy should enter pending state` | 6 |
| then_media_buy | then | `the package records should be persisted` | 6 |
| then_media_buy | then | `the pricing validation should pass` | 6 |
| then_media_buy | then | `the response should include "rejection_reason" containing "Budget too low"` | 6 |
| then_media_buy | then | `the system should resolve start_time to current UTC` | 4 |
| then_media_buy | then | `each error should include "suggestion" field` | 3 |
| then_media_buy | then | `no package records should be persisted` | 3 |
| then_media_buy | then | `the approval path should be auto-approved` | 3 |
| then_media_buy | then | `the creative assignment records should be persisted` | 3 |
| then_media_buy | then | `the date validation should pass` | 3 |
| uc002_task_query | given | `the domain filter boundary is: analytics` | 3 |
| uc002_task_query | given | `the domain filter boundary is: creative` | 3 |
| uc002_task_query | given | `the domain filter boundary is: empty array` | 3 |
| uc002_task_query | given | `the domain filter boundary is: media-buy` | 3 |
| uc002_task_query | given | `the domain filter boundary is: media-buy+signals` | 3 |
| uc002_task_query | given | `the domain filter boundary is: omitted` | 3 |
| uc002_task_query | given | `the domain filter is creative` | 3 |
| uc002_task_query | given | `the domain filter is domain_array` | 3 |
| uc002_task_query | given | `the domain filter is empty_array` | 3 |
| uc002_task_query | given | `the domain filter is governance` | 3 |
| uc002_task_query | given | `the domain filter is media_buy` | 3 |
| uc002_task_query | given | `the domain filter is omitted` | 3 |
| uc002_task_query | given | `the domain filter is signals` | 3 |
| uc002_task_query | given | `the domain filter is unknown_value` | 3 |
| then_media_buy | then | `the media buy record should be persisted in the database` | 3 |
| then_media_buy | then | `the media buy record should be persisted with status "pending_approval"` | 3 |
| then_media_buy | then | `the media buy should proceed to adapter execution` | 3 |
| then_media_buy | then | `the response should NOT have an "errors" field` | 3 |
| then_media_buy | then | `the response should NOT have success fields (media_buy_id, packages)` | 3 |
| then_media_buy | then | `the response should have an "errors" array` | 3 |
| then_media_buy | then | `the response should have success fields` | 3 |
| uc002_task_query | given | `the sort direction boundary is: asc` | 3 |
| uc002_task_query | given | `the sort direction boundary is: ascending` | 3 |
| uc002_task_query | given | `the sort direction boundary is: desc` | 3 |
| uc002_task_query | given | `the sort direction boundary is: omitted` | 3 |
| uc002_task_query | given | `the sort direction is asc` | 3 |
| uc002_task_query | given | `the sort direction is desc` | 3 |
| uc002_task_query | given | `the sort direction is omitted` | 3 |
| uc002_task_query | given | `the sort direction is unknown_value` | 3 |
| uc002_task_query | given | `the task list sort field boundary is: created_at` | 3 |
| uc002_task_query | given | `the task list sort field boundary is: domain` | 3 |
| uc002_task_query | given | `the task list sort field boundary is: omitted` | 3 |
| uc002_task_query | given | `the task list sort field boundary is: priority` | 3 |
| uc002_task_query | given | `the task list sort field is created_at` | 3 |
| uc002_task_query | given | `the task list sort field is domain` | 3 |
| uc002_task_query | given | `the task list sort field is omitted` | 3 |
| uc002_task_query | given | `the task list sort field is status` | 3 |
| uc002_task_query | given | `the task list sort field is task_type` | 3 |
| uc002_task_query | given | `the task list sort field is unknown_value` | 3 |
| uc002_task_query | given | `the task list sort field is updated_at` | 3 |
| uc002_task_query | given | `the task status filter boundary is: empty array` | 3 |
| uc002_task_query | given | `the task status filter boundary is: omitted` | 3 |
| uc002_task_query | given | `the task status filter boundary is: pending` | 3 |
| uc002_task_query | given | `the task status filter boundary is: submitted` | 3 |
| uc002_task_query | given | `the task status filter boundary is: submitted+working+input-required` | 3 |
| uc002_task_query | given | `the task status filter boundary is: unknown` | 3 |
| uc002_task_query | given | `the task status filter is auth_required` | 3 |
| uc002_task_query | given | `the task status filter is canceled` | 3 |
| uc002_task_query | given | `the task status filter is completed` | 3 |
| uc002_task_query | given | `the task status filter is empty_array` | 3 |
| uc002_task_query | given | `the task status filter is failed` | 3 |
| uc002_task_query | given | `the task status filter is input_required` | 3 |
| uc002_task_query | given | `the task status filter is omitted` | 3 |
| uc002_task_query | given | `the task status filter is rejected` | 3 |
| uc002_task_query | given | `the task status filter is status_array` | 3 |
| uc002_task_query | given | `the task status filter is submitted` | 3 |
| uc002_task_query | given | `the task status filter is unknown_status` | 3 |
| uc002_task_query | given | `the task status filter is unknown_value` | 3 |
| uc002_task_query | given | `the task status filter is working` | 3 |
| uc002_task_query | given | `the task type filter boundary is: create_media_buy` | 3 |
| uc002_task_query | given | `the task type filter boundary is: create_media_buy+update_media_buy` | 3 |
| uc002_task_query | given | `the task type filter boundary is: delete_media_buy` | 3 |
| uc002_task_query | given | `the task type filter boundary is: empty array` | 3 |
| uc002_task_query | given | `the task type filter boundary is: log_event` | 3 |
| uc002_task_query | given | `the task type filter boundary is: omitted` | 3 |
| uc002_task_query | given | `the task type filter is activate_signal` | 3 |
| uc002_task_query | given | `the task type filter is create_media_buy` | 3 |
| uc002_task_query | given | `the task type filter is create_property_list` | 3 |
| uc002_task_query | given | `the task type filter is delete_property_list` | 3 |
| uc002_task_query | given | `the task type filter is empty_array` | 3 |
| uc002_task_query | given | `the task type filter is get_creative_delivery` | 3 |
| uc002_task_query | given | `the task type filter is get_property_list` | 3 |
| uc002_task_query | given | `the task type filter is get_signals` | 3 |
| uc002_task_query | given | `the task type filter is list_property_lists` | 3 |
| uc002_task_query | given | `the task type filter is log_event` | 3 |
| uc002_task_query | given | `the task type filter is omitted` | 3 |
| uc002_task_query | given | `the task type filter is sync_accounts` | 3 |
| uc002_task_query | given | `the task type filter is sync_creatives` | 3 |
| uc002_task_query | given | `the task type filter is sync_event_sources` | 3 |
| uc002_task_query | given | `the task type filter is task_type_array` | 3 |
| uc002_task_query | given | `the task type filter is unknown_value` | 3 |
| uc002_task_query | given | `the task type filter is update_media_buy` | 3 |
| uc002_task_query | given | `the task type filter is update_property_list` | 3 |
| then_media_buy | then | `the campaign should be immediately activating` | 1 |
| then_media_buy | then | `the response should include resolved start_time (not literal "asap")` | 1 |

### A3. The 45 scenarios blocked by routing alone — 650 nodes

Every rendered step binds; `uc002-not-wired` xfails them at fixture setup. Ordered by node count.

| nodes | feature line | tag | scenario |
|---|---|---|---|
| 69 | 1322 | `T-UC-002-partition-optimization-goals` | Optimization goals partition validation - single_metric_goal |
| 60 | 1799 | `T-UC-002-boundary-optimization-goals` | Optimization goals boundary validation - optimization_goals present wi |
| 57 | 1216 | `T-UC-002-partition-targeting-overlay` | Targeting overlay partition validation - absent_overlay |
| 48 | 1711 | `T-UC-002-boundary-targeting-overlay` | Targeting overlay boundary validation - absent overlay |
| 30 | 1832 | `T-UC-002-boundary-catalog-distinct-type` | Catalog distinct type boundary validation - 0 catalogs (field absent) |
| 22 | 1251 | `T-UC-002-partition-creative-asset` | Creative asset partition validation - no_creatives |
| 21 | 1090 | `T-UC-002-partition-currency-consistency` | Currency consistency partition validation - single_package |
| 21 | 1361 | `T-UC-002-partition-catalog-distinct-type` | Catalog distinct type partition validation - no_catalogs |
| 21 | 1589 | `T-UC-002-boundary-currency-consistency` | Currency consistency boundary validation - single package (trivially v |
| 18 | 1173 | `T-UC-002-partition-start-time` | Start time partition validation - asap_literal |
| 18 | 1384 | `T-UC-002-partition-format-id-structure` | Format ID structure partition validation - valid_format_id |
| 18 | 1570 | `T-UC-002-boundary-pricing-option-xor` | Pricing option XOR boundary validation - fixed_price only (valid fixed |
| 18 | 1740 | `T-UC-002-boundary-creative-asset` | Creative asset boundary validation - no creatives (valid) |
| 15 | 1069 | `T-UC-002-partition-pricing-option-xor` | Pricing option XOR partition validation - fixed_pricing |
| 15 | 1132 | `T-UC-002-partition-minimum-spend` | Minimum spend partition validation - budget_meets_product_min |
| 15 | 1626 | `T-UC-002-boundary-minimum-spend` | Minimum spend boundary validation - budget = product min_spend (exact  |
| 15 | 1661 | `T-UC-002-boundary-start-time` | Start time boundary validation - literal 'asap' |
| 15 | 1855 | `T-UC-002-boundary-format-id-structure` | Format ID structure boundary validation - valid object (registered age |
| 12 | 1195 | `T-UC-002-partition-end-time` | End time partition validation - after_start_time |
| 12 | 1609 | `T-UC-002-boundary-product-uniqueness` | Product uniqueness boundary validation - single package (trivially uni |
| 12 | 1694 | `T-UC-002-boundary-end-time` | End time boundary validation - end_time after start_time |
| 10 | 1153 | `T-UC-002-partition-daily-spend-cap` | Daily spend cap partition validation - below_cap |
| 10 | 1644 | `T-UC-002-boundary-daily-spend-cap` | Daily spend cap boundary validation - daily budget = cap (at limit) |
| 9 | 1050 | `T-UC-002-partition-budget-amount` | Budget amount partition validation - positive_amount |
| 9 | 1113 | `T-UC-002-partition-product-uniqueness` | Product uniqueness partition validation - single_package |
| 9 | 1277 | `T-UC-002-partition-approval-workflow` | Approval workflow partition validation - auto_approve |
| 9 | 1406 | `T-UC-002-partition-persistence-timing` | Persistence timing partition validation - auto_approve_adapter_success |
| 9 | 1554 | `T-UC-002-boundary-budget-amount` | Budget amount boundary validation - amount = 0 (rejected by rule) |
| 9 | 1761 | `T-UC-002-boundary-approval-workflow` | Approval workflow boundary validation - both flags false (auto-approve |
| 9 | 1873 | `T-UC-002-boundary-persistence-timing` | Persistence timing boundary validation - adapter returns success (auto |
| 3 | 729 | `T-UC-002-inv-008-1` | INV-1 holds -- total budget greater than zero |
| 3 | 737 | `T-UC-002-inv-008-2` | INV-2 violated -- total budget is zero or negative |
| 3 | 869 | `T-UC-002-inv-018-5` | INV-5 holds -- correctable error includes suggestion and field |
| 3 | 990 | `T-UC-002-inv-080-1` | INV-1 violated -- account field absent from request |
| 3 | 1989 | `T-UC-002-nfr-003` | Audit logging -- all steps are logged |
| 3 | 1999 | `T-UC-002-nfr-004` | Response latency -- within SLA |
| 3 | 2007 | `T-UC-002-nfr-006` | Minimum order size enforcement |
| 3 | 2031 | `T-UC-002-sandbox-production` | Production account media buy response does not include sandbox flag |
| 3 | 2497 | `T-UC-002-v31-error-budget-too-low-details` | v3.1 BUDGET_TOO_LOW error carries minimum_budget and currency in detai |
| 3 | 49 | `T-UC-002-nfr-006-enforcement` | Budget below minimum order size is rejected |
| 1 | 704 | `T-UC-002-inv-006-3` | INV-3 violated -- both fixed_price and floor_price set |
| 1 | 784 | `T-UC-002-inv-013-5` | INV-5 violated -- start_time is "ASAP" wrong case |
| 1 | 1979 | `T-UC-002-nfr-001` | Security hardening -- request validation and rate limiting |
| 1 | 2016 | `T-UC-002-sandbox-happy` | Sandbox account creates simulated media buy with sandbox flag |
| 1 | 2041 | `T-UC-002-sandbox-validation` | Sandbox account with invalid budget returns real validation error |

Total: 650 nodes.
