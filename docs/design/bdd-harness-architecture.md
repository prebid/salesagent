# BDD harness architecture

One Gherkin scenario runs on every transport this seller speaks, and passes on all of
them. This page describes how that works: what a scenario names, what the harness derives,
and which layer owns each decision.

[tests/CLAUDE.md](../../tests/CLAUDE.md) § "Transport dispatching" is the short version you
need before authoring a test. Read this page when you change the harness itself, add a
transport, or want to know why a step does not do the obvious thing.

This page is the test-side companion to
[building-tools.md](../development/building-tools.md), which describes the production side.
Production derives MCP registration, the A2A card, and the REST route from one registry row.
The harness derives its addresses from those same three live registrations, so no file under
`tests/` holds a second list of what a tool accepts or where it lives.

## The rule

**A scenario names a state, a tool, and an outcome. Everything else is derived.**

- The transport is a parametrized fixture value, never a sentence. Scenario text that
  names a transport is the declared exception, and it costs that scenario its
  parametrization.
- The tool's address on each transport comes from live registration
  (`tests/harness/address_table.py:1-34`).
- The credential is a headers dict. No transport carries an identity object; the production
  resolver builds one from the headers on every dispatch (`tests/harness/_base.py:511`).
- The outcome is graded against the bytes the buyer received — the wire body on success,
  the two-layer error envelope on failure. There is no harness-side reconstruction to
  grade instead.

## One scenario, several transports

`Transport` has six members and every one of them dispatches over a real wire
(`tests/harness/transport.py:142-157`). The three protocol members take their values from
production's own `TransportProtocol`, so a transport is spelled once:

| Member | Wire |
|---|---|
| `A2A` | in-process `AdCPRequestHandler.on_message_send` → `serve` → Task/Artifact framing |
| `MCP` | in-process FastMCP client → `RegistryTool.run` → `serve` |
| `REST` | in-process Starlette `TestClient` → generated `/api/v1` route → `serve` |
| `E2E_REST` | real HTTP through nginx to the live Docker stack |
| `E2E_MCP` | real MCP over HTTP to the same stack |
| `E2E_A2A` | real A2A over HTTP to the same stack |

`pytest_generate_tests` parametrizes the `ctx` fixture indirectly over those members
(`tests/bdd/conftest.py:4344`), and the `ctx` fixture hands each step a fresh dict with
`ctx["transport"]` set to the enum member (`:4624`). Which members are in play:

- **`A2A`, `MCP`, `REST` always.** Every tool is reachable on every transport, because
  reachability is the registry's answer and all 14 rows carry a REST binding. No scenario
  is withheld from a transport.
- **`E2E_REST` when `BDD_E2E_ENABLED=true`** — the in-network job. `_parametrize_ctx`
  appends the e2e members only then (`:4104`).
- **`E2E_MCP` and `E2E_A2A` when `BDD_E2E_TRANSPORTS=all`.** Opt-in per run, and a capacity
  decision rather than a correctness one. Turning them on for every scenario takes the
  in-network selection from ~2.9k to ~8.6k variants against one shared server and one
  database, and a saturated box manufactures failures that read as defects. Roll them out
  one feature at a time. The comment at `:4415-4438` carries the measured cost.

The pytest ids are **derived** from the enum values, never passed as a parallel list
(`:4126-4135`). `tox.ini`'s `-k "e2e_rest or ..."` selector matches on those ids, so a
hand-written id that disagrees collects a transport no environment selects — a scenario
that dies dormant while the run stays green.

### The two exceptions, both visible at collection

**Transport-pinned scenarios.** A scenario carrying `@rest`, `@mcp`, or `@a2a` skips
parametrization entirely and runs once, on whatever transport its When step names
(`_TRANSPORT_SPECIFIC_TAGS`, `tests/bdd/conftest.py:4015`). This is the declared exception
to transport independence, not a loophole in it: the tag exists so a scenario whose subject
IS one transport's own frame can say so. Three such scenarios live in collected feature
files — `T-UC-011-ext-c-a2a`, and the `T-UC-026-main-mcp` / `T-UC-026-main-rest` twins
(measured 2026-09-15 by scanning `tests/bdd/features/*.feature` against the feature names
referenced from `tests/bdd/test_*.py`).

**Single-transport scenarios** get a real one-element parametrization rather than being
dropped (`_SINGLE_TRANSPORT_TAGS`, `:4039`). Dropping a transport at collection is exactly
as ungraded as an xfail and invisible to both escape-hatch detectors, so a genuine
one-transport obligation keeps a real `[a2a]` test id that `--collect-only` shows.

`_IMPL_ONLY` — the set of (use case, tag) pairs that skipped wire parametrization — is an
empty set (`:4052`). No scenario bypasses the wire at collection.

### Two refusals that keep e2e honest

- Reaching the `ctx` fixture with an `e2e_*` transport and no reachable stack raises
  `RuntimeError` naming the cause (`:4638-4658`). A skipped e2e test turns a
  non-executed test into a false green, so it refuses to skip.
- `BDD_E2E_ENABLED=true` under xdist without `E2E_PER_WORKER=1` is a `pytest.UsageError`
  (`:337-360`). A worker's `pytest_generate_tests` silently drops the e2e transport at
  collection, so without that refusal the suite passes without ever having run it.

## Who owns what

| Layer | Owns | Where |
|---|---|---|
| Feature file | the state, the tool, the outcome — and never the transport | `tests/bdd/features/` |
| `ctx` fixture | which transport this run dispatches on; the e2e config | `tests/bdd/conftest.py:4624` |
| `ENV_ROUTES` | which env builds and seeds this scenario | `tests/bdd/conftest.py:5281` |
| Env (`BaseTestEnv` and subclasses) | mocks, factories, the DB session, the credential, and how its own wire is typed | `tests/harness/_base.py` |
| Dispatcher | one per transport: call the env, wrap the outcome in a `TransportResult` | `tests/harness/dispatchers.py:312` |
| `AdCPTestClient` / `_dispatch_core` | address, wrap, deliver, unwrap — the one dispatch implementation | `tests/harness/client.py:656` |
| `ADDRESS_TABLE` | where a tool lives on each transport, read from live registration | `tests/harness/address_table.py:232` |
| Step dispatch entries | gate and record the payload, then publish one ctx contract | `tests/bdd/steps/generic/_dispatch.py` |
| `TransportResult` + `_outcome_helpers` | the guarded reads and the assertions | `tests/harness/transport.py:276`, `tests/bdd/steps/_outcome_helpers.py` |

## The dispatch path

Every dispatch is **address → wrap → deliver → unwrap**, and `_dispatch_core` is the one
implementation (`tests/harness/client.py:656`).

- **ADDRESS** resolves `(tool, transport)` through `ADDRESS_TABLE`, built from the three
  objects production routes real traffic with: `mcp.list_tools()`, `create_agent_card()`,
  and `app.routes` (`tests/harness/address_table.py:1-34`). A tool not registered on a
  transport raises `NoAddressForTransport` (`:53`) instead of being quietly unreachable. A
  REST handler name that is not a known tool name raises `UnresolvedRestHandlerName` at
  table-build time (`:66`) rather than registering under the wrong name.
- **WRAP** and **UNWRAP** are per transport *family*: the same function object serves a
  transport and its E2E sibling, because the wire format is identical either way
  (`client.py:173`, `:855`, `:864`).
- **DELIVER** is the only per-transport half (`:454`). The in-process transports reuse the
  env primitives that already own the factory-commit and middleware plumbing; the three e2e
  transports are real HTTP.

An env reaches that core rather than reimplementing it. `call_via` presents the env's
credential unless the caller passed one, then routes to `DISPATCHERS[transport]`
(`tests/harness/_base.py:674`). `deliver_mcp` / `deliver_a2a` are the override point and
delegate through `_deliver_via_client`, which calls `_dispatch_core` and then re-parses the
payload with the env's own `response_parser` — the core owns the dispatch, the env owns only
how its wire is typed (`:762`, `:729`, `:780`). The base defines `call_mcp` / `call_a2a`
once as `deliver_*(**kwargs).payload`, and nothing overrides them (`:826`, `:746`).

One delivery has one channel. `DeliverResult(payload, wire_response)` carries both the
parsed payload and its wire bytes as the return value
(`tests/harness/transport.py:250-269`). The per-env `_last_wire_response` stash that
dispatchers used to read across the object boundary does not exist, which is what makes a
second writer — and a stale wire — structurally impossible.

### The credential

`env.credential()` returns the headers this env's buyer presents: the env principal's token
plus `x-adcp-tenant` (`tests/harness/_base.py:511`). Four spellings cover every auth
scenario:

- `env.credential()` presents the env principal with a valid token.
- `env.credential(token=INVALID_TOKEN)` presents a token that resolves to no principal.
- `env.credential(token=None)` presents no token, with the tenant still addressed.
- `credential={}` on a dispatch presents no headers at all.

Each transport injects that dict where it reads headers. `NO_IDENTITY_OVERRIDE`
(`tests/harness/transport.py:186`) is the one shared sentinel distinguishing "the caller
passed no `credential=`" from an explicit empty one. Every site imports it and none
re-declares it, so two sentinels cannot share one name.

## What a step may publish, and what it may read

Three step-level entries reach a transport, and all three publish through one writer,
`_populate_ctx_from_result` (`tests/bdd/steps/generic/_dispatch.py:71`):

- `dispatch_request` (`:202`) takes the ordinary case — a keyword bag through
  `env.call_via`.
- `dispatch_via_client` (`:296`) takes scenarios wired onto `AdCPTestClient` directly.
- `dispatch_raw_document` (`:281`) takes a document no request model accepts, through
  `tests/harness/raw_wire.py`.

The single writer sets `ctx["result"]` always, then `ctx["error"]` plus
`ctx["wire_error_envelope"]` on the error branch, or `ctx["wire_response"]` on the success
branch. Both branches go through it: unifying only the error branch leaves the success
branch with two writers.

**The ctx contract is typed.** `WireCtx` is a `TypedDict(total=False)` naming exactly what a
dispatch may publish and with what type (`:27`). `ctx["error"]` is typed as the `WireError`
carrier, so `ctx["error"] = AdCPValidationError(...)` is a mypy error rather than a
convention someone must remember. There is deliberately no `response` key: a
provenance-stripped copy of the payload cannot tell a Then whether it holds a wire fact or
an in-process reconstruction.

**One function normalizes `ctx["transport"]`.** `_as_transport` accepts the enum or a
case-insensitive string and raises when the key is unset (`:138`) — a missing transport is a
wiring bug, never a silent bypass of the wire.

**Every dispatch gates and records before bytes move.** `gate_and_record` (`:178`) first
calls `assert_declared_malformations` (`tests/factories/malformed.py:471`), which grades the
payload against the pinned model. It then calls `record_dispatched_request`
(`tests/bdd/payload_capture.py:168`), which makes the request-as-dispatched a measured
artifact that `scripts/audit/compare_payloads.py` compares across transports. The order
matters in both directions. The gate must precede `json_safe`, which rebuilds every dict and
drops the marker the declaration rides on. The capture must precede the transport, so the
same scenario's payload is comparable across a2a/mcp/rest.

**No entry wraps its call in a blanket `except Exception`.** The "nothing crossed a wire"
outcome travels on the return value: each in-process dispatcher wraps its own delivery and
hands back an error `TransportResult` with `has_wire=False` and
`envelope["status"] == "transport_fault"` (`tests/harness/transport.py:234`). Whatever still
escapes `call_via` is harness wiring — a missing address, a missing tool name, an absent e2e
config — and must fail loudly. The blanket clause came out only after a full in-process run
without it produced zero outcome and zero traceback changes across 8164 common nodeids.

### Read a success

| Helper | Grades |
|---|---|
| `TransportResult.require_wire()` (`transport.py:496`) | the body the buyer received, or a loud failure naming which of two things went wrong |
| `wire_field(ctx, path)` (`_outcome_helpers.py:124`) | present AND non-null at a dotted path |
| `wire_absent(ctx, path)` (`:295`) | the strict complement — a serialized `null` counts as present and fails |
| `wire_dict(ctx, path=None)` (`:146`) | the whole body, or the object at a path |
| `wire_lookup(ctx, path)` (`:107`) | the shared resolver; asserts nothing, for genuinely tri-state checks only |
| `require_payload(ctx)` / `payload_or_none(ctx)` (`:447`, `:428`) | the typed payload, with its provenance |

`_wire_body` delegates to `require_wire()` and has no serializer fallback (`:32`): a success
result with no stashed wire means the dispatch bypassed the real pipeline, which is a defect
in the env rather than a case to serialize around.

### Read a failure

`TransportResult.assert_wire_error(code, ...)` is the one way to verify an error
(`transport.py:568`). It reads the captured envelope and refuses a code absent from
`CODE_TABLE` as unemittable. It defaults `recovery` from that table, so the assertion is
non-vacuous without per-scenario duplication, then forwards to the single
`assert_envelope_shape` locator. `assert_wire_rejection` (`_outcome_helpers.py:389`) is the
thin shared body behind every "rejected with CODE naming field f" step.

`wire_error_envelope` holds the transport's own bytes or nothing: REST's ≥400 body, MCP's
`ToolError` JSON, A2A's failed-Task artifact DataPart. `_wire_envelope_from_exception`
returns only the real stash (`transport.py:97`); `None` is the honest answer when nothing
crossed a wire, and the synthesizing fallback that used to sit there is gone —
pinned by `tests/unit/test_harness_mcp_never_synthesizes.py`.

The carrier is `WireError` (`tests/harness/_base.py:100`), deliberately not an
`AdCPSalesAgentError` subclass and holding no code-to-class knowledge. An `isinstance` check
against a production class fails loudly instead of passing quietly.

There is one attribute-access site for the envelope in the whole step tree,
`_real_wire_error_envelope` (`_outcome_helpers.py:307`), with three named accessors over it:
`error_envelope_or_none` (`:326`), `wire_error_envelope_or_none` (`:348`), and the loud
`wire_error_dict` (`:361`). Everywhere else calls an accessor.

## Env construction and seeding

Routing a scenario to an env is a declarative registry, not a chain of conditionals. An
`EnvRoute` row (`tests/bdd/conftest.py:4711`) carries `env_builder`, `seed`, an optional
`xfail_reason`, and either a marker-set predicate (`when`) or a coarse `uc` bucket — one or
the other, never both. `_run_env_route` is the single consumer (`:5009`). It enters
`_db_scope_for` (`:4992`) before the builder runs, so on an e2e parametrization production's
cached engine points at the live server DB before any factory writes happen. It then stashes
`ctx["env"]`, builds `ctx["client"]` for every row, and seeds. The predicates live in rows
rather than inside the env fixture so that the conftest and the audit join resolve a route
from the same data.

A Given means the same thing against a live stack through one decorator. `realize_e2e`
(`tests/harness/_realize.py:60`) keeps the in-process body and calls the e2e realization
with the same normalized arguments when `env.is_e2e` is true (`_base.py:435`) — one seam,
no per-method branching in the steps. When the live stack gives no way to realize an intent,
declare that at the env method with `e2e_unsupported(reason)` (`:94`), which raises
`E2EUnsupportedSetup` (`:38`). The BDD report hook turns that into a non-strict xfail
carrying the reason (`tests/bdd/conftest.py:297`, the `E2EUnsupportedSetup` branch), so the
in-process transports of the same scenario still run. The declaration lives at the method,
not in a nodeid ledger.

## What the suite measures about itself

A transport-independent suite is only worth its claim if you can tell a scenario that
graded something from one that was xfailed out of existence in fixture setup. Three
instruments answer that.

**Liveness is measured, not read off a reason string.**
`tests/bdd/scenario_liveness.py` records `steps_executed` from
`pytest_bdd_before_step_call` (`:307`) — the hook pytest-bdd fires immediately before
calling a step function — and `harness_wired` is an AND over that fact (`:106`,
`:150-190`). A run that stops in fixture setup records `False` whatever its reason text
says. The module measures `steps_bound` by walking every step of the scenario through
pytest-bdd's own `get_step_function` before any body runs (`:276`), so a scenario whose
fourth step is unbound reports that step by name. Membership in the measurement is the
`@T-*` identity tag. The module records the provenance tag as data, because that tag used
to be a collection filter, where a retag silently deleted a scenario from the population.

**The artifact records its own scope.** `test-results/bdd_scenario_liveness.json` carries a
`run` block — collected, selection, markers, deselected, workers, exit status, failures —
beside the scenarios (`:460`), so a narrowed `-k` run is visible in the file instead of
reading as hundreds of dormant scenarios. A collect-only session writes nothing at all.
Under xdist a worker ships its shard on `config.workeroutput` and only the controller
writes, with each merged field's combine rule named (`:494-534`).

**Three ledgers record what the suite does not grade, and all three may only shrink.**

| Ledger | Holds | Enforced by |
|---|---|---|
| `tests/bdd/dormant_scenarios.txt` | 149 scenarios that grade nothing because a step has no binding, keyed `<tag> :: <step text>` | per-test, in `_record_dormancy` (`conftest.py:184`); `tests/unit/test_bdd_dormancy_baseline.py` |
| `tests/bdd/e2e_rest_known_failures.txt` | 19 nodeids that fail over real HTTP for a production or harness reason | loaded at `conftest.py:44`; pinned exactly by `tests/unit/test_e2e_rest_ledger_state.py` |
| `tests/storyboard/known_failures.txt` | nothing, by decision — no conformance check is routed to xfail, so the job's failure count IS the gap count | a stale entry resolving to no collected check fails the session |

Dormancy enforcement is per test rather than a count, which is what lets it fire on a
subset run: a scenario that goes dormant and is not already listed fails immediately, and
the list may only shrink. Mock-injection-only e2e entries live in env-level
`E2EUnsupportedSetup` declarations rather than in the e2e ledger.

### Measured counts

These counts come from the in-network box run `test-results/innet_150926_0801/`:

| Selection | Collected | Passed | Xfailed | Failed |
|---|---|---|---|---|
| `bdd_inprocess` | 8326 | 3755 | 4565 | 0 |
| `bdd_e2e` (the `e2e_rest` transport) | 2855 | 1013 | 1817 | 0 |

State the denominator when citing this. "Zero failed" is over a population that is 55%
in-process and 64% e2e xfailed, and the three ledgers above are what bound that xfail set.
Storyboard conformance in the same run scores 30 passed / 21 failed / 249 skipped per
protocol, identically on mcp and a2a (`storyboard/summary_mcp.json`,
`storyboard/summary_a2a.json`, adcp 3.1.1, SDK runner 14.0.0-rc.35).

## Enforcement

Guards in the unit suite hold the invariants above, so `make quality` fails on a regression
rather than a reviewer catching it:

| Guard | Refuses |
|---|---|
| `tests/unit/test_architecture_bdd_wire_discipline.py` | six checks: test-side error construction, reconstructed-only assertions, hand-rolled envelope parsing, private circuit-breaker state in a step, `ctx["response"]`, and hand-rolled `wire_error_envelope` access |
| `tests/unit/test_architecture_bdd_dispatch_entries.py` | a step file reaching a transport without `gate_and_record`; two allowlisted direct sites, shrink-only |
| `tests/unit/test_architecture_harness_single_dispatch.py` | per-env `call_mcp`/`call_a2a`, a `deliver_*` override outside the shrink-only allowlist, and any `_last_wire_response` attribute |
| `tests/unit/test_harness_mcp_never_synthesizes.py` | a synthesized error envelope under any name |
| `tests/unit/test_bdd_dormancy_baseline.py`, `test_e2e_rest_ledger_state.py` | a ledger that grew, and a stale row that no longer resolves |

Two more sit on the authoring side: `test_architecture_bdd_no_pass_steps.py` and
`test_architecture_bdd_no_trivial_assertions.py` refuse a Then that can return without
asserting. The full list is `ls tests/unit/test_architecture_bdd_*.py`.

## What this replaced

Each row names a mechanism that existed before the rework and no longer exists.

| Gone | Replacement |
|---|---|
| `Transport.IMPL` and `ImplDispatcher` — a pseudo-transport calling `_impl` directly, parametrized beside the real ones | six wire members, no impl dispatcher (`transport.py:142-157`, `dispatchers.py:312`). `env.call_impl` survives and is not a transport (`_base.py:696`) |
| `TransportResult.synthesized_error_envelope` — "what production would emit for this exception", built by the harness from the exception it had caught | `wire_error_envelope` alone, holding the transport's own bytes or `None` |
| `_envelope_to_adcp_error` and its wire-code-to-exception-class map (20 of 43 classes, and it always produced the first of two classes sharing a code) | `WireError`, carrying the envelope verbatim and no class knowledge |
| `env._run_mcp_wrapper`, which skipped the FastMCP middleware chain and captured no envelope | `_run_mcp_client` through the real FastMCP client |
| Per-env `call_mcp` / `call_a2a` / `build_rest_body` / `parse_rest_response` — the hand-written quartet on ~33 env classes | one pair on the base, delegating to `_dispatch_core`; `deliver_*` as the allowlisted override point |
| `env._last_wire_response` — one delivery with two channels | `DeliverResult(payload, wire_response)` |
| `ctx["response"]` — a provenance-stripped payload copy with three writers and three meanings | `require_payload(ctx)` / `payload_or_none(ctx)`, plus the explicitly named `ctx["self_dispatched_response"]` |
| The `model_dump()` fallback in the success-path readers | `require_wire()`, which raises |
| A blanket `except Exception: ctx["error"] = exc` at each dispatch entry | `has_wire=False` plus `status == "transport_fault"` on the return value |
| `AdCPTestClient.call(..., identity=...)` — an identity object on the test client | `credential=`, a headers dict |
| The `*_raw` transport wrappers, `src/core/transport_helpers.py`, and `ProtocolEnvelope.wrap`, which the harness mirrored per transport | `serve()` at the one production boundary seam; the harness mirrors nothing |

A measurement decided the impl drop, recorded here because
[bdd-drop-impl.md](bdd-drop-impl.md) points at this file for it. The measurement covered
3079 distinct (scenario, example) rows before the removal. Passing per transport was impl
402, a2a 375, mcp 377, and rest 358. Two rows passed on impl where a wire variant existed
and did not pass. Another 32 passed on impl with no wire variant collected at all, almost
all of them request-validation partitions where the harness's wire path dropped the
parameter before validation. So impl uniquely contributed a pass in ~34 rows, and each one
needed a disposition — a fixed wire path or an honest ledger row — before the transport went
away. Those numbers are historical: they describe the tree at the time of the drop.

## Still open: the Gherkin vocabulary

The harness converged; the Gherkin has not finished following it. This section is the
vocabulary target, measured at commit `c20672203` and kept because
[bdd-migration.md](bdd-migration.md) cites it. Treat the numbers as a dated snapshot; where
a figure has since moved, this section states the correction.

**The variety is wording, not parameters.** Across the corpus at that commit: 4917 Given
lines carrying 2470 distinct sentences, 2807 When lines carrying 1470. Normalizing values,
placeholders, and numbers collapsed only 20%, and 75–77% of distinct sentences appeared
exactly once.

**Vocabulary density tracked executability exactly** — 0.59 distinct sentences per line in
the never-loaded feature files against 0.43 in the loaded ones. Invented phrasing
concentrates precisely where nothing ever forced two sentences to meet the same
implementation. No test module loads 18 of the 52 feature files (measured 2026-09-15):
BR-UC-001, 008, 012–017, 020–025, 027, 028, 030, and 032. They are the inventory of
protocol this seller has not built. Wire each one as the functionality arrives, one at a
time.

**Given — roughly 32 state primitives.** Thirty covered 4479 of 4963 lines; two more absorb
most of the residue. These five are the largest:

| | lines | primitive |
|---|---|---|
| P01 | 533 | the Buyer is authenticated as principal "P" on tenant "T" |
| P06 | 500 | the tenant setting S is V |
| P10 | 423 | the principal "P" owns media buy "M" with status "S" |
| P08 | 356 | an account "A" exists for brand/operator "B" status "S" |
| P05 | 276 | a tenant "T" exists and is resolvable |

The collapses were proven at the implementation rather than the wording: "the Buyer is
authenticated with a valid principal_id" (234 uses) and "the Buyer Agent has an
authenticated connection" (118) had byte-identical step bodies, and two delivery sentences
reading as opposites both called the same step. P01 is built and rolled out
(`tests/bdd/steps/generic/given_auth.py`).

**When — one primitive, two modifiers, three events.** 2548 of 2807 When lines (90.8%) are
one operation: dispatch tool T with payload P. Stripped of payload they collapsed to 397
verb+tool stems naming 49 tools, written with 28 different verbs. Only two things reach the
seam that are not payload — the credential override, and a verbatim body the local request
model rejects. `dry_run`, `idempotency_key`, and `adcp_version` are AdCP request fields
and belong in the payload; repetition and concurrency are the primitive invoked twice.
Three genuine non-dispatch events remain (25 lines): an admin UI action, a seller-initiated
outbound webhook, and clock advancement.

**Payload — a named baseline plus the fields the scenario overrides.** 48% of When lines
named no payload at all, 30% carried an inline scalar, 12% an outline placeholder, and 3% a
data table. Preceding Given steps build the payload, which is why the Given corpus is 1.75×
the When corpus — so the payload vocabulary is a Given problem, and that corpus already
builds baseline-plus-delta by hand. A full JSON body does not fit an `Examples` cell; a
baseline name plus one field and value does.

**Then — resolved, never named.** A scenario that names its response schema file states
which tool it exercises in a second place, free to drift from the When. The tool determines
its response schema, so the schema resolves from the tool that was actually called.

### The trap waiting for whoever de-pins a transport-tagged scenario

pytest-bdd stores scenarios in a dict keyed by NAME. There is no duplicate check and no
warning, so **stripping the transport phrase from two twins silently deletes one** — the
file still shows both and the suite grades one fewer than it appears to contain. That is
live in the tree: `BR-UC-026-package-media-buy.feature` carries "Create package via MCP —
all required fields provided" (`:29-36`) and its REST twin (`:50`), in a collected file.
Merge a twin-set into a single parametrized scenario; never rename it twice. Shard the work
by feature file, never by tag — a tag-sharded run hands the two halves of a twin to
different workers, and neither worker can see the collision it is half of.

## Retired alongside this page

`bdd-harness-transport-boundary.md` is **deleted**. It described the harness reaching
production through per-tool `*_raw` wrappers, `src/core/transport_helpers.py`,
`ProtocolEnvelope.wrap`, and a four-transport set including `impl` — none of which exists.
Its decisions instruct you to build the wrong thing. D5 prescribed an `UpdateMediaBuyResult`
wrapper carrying `{status, response}` with a `model_serializer` that overwrites the root
`status`. What shipped instead is a common root class whose branches inherit the envelope
and declare `status` as a field (`src/core/schemas/_base.py:919`), under a rule that allows
exactly one serializer class and no per-class hooks. The one part worth keeping — its
measurement of what `impl` uniquely graded — is in "What this replaced" above.

## Related

- [tests/CLAUDE.md](../../tests/CLAUDE.md) — authoring with the harness: environments,
  factories, wire assertions, the error verification policy
- [tests/bdd/CLAUDE.md](../../tests/bdd/CLAUDE.md) — the step helpers
- [building-tools.md](../development/building-tools.md) and
  [request-lifecycle.md](../development/request-lifecycle.md) — the production side of the
  same seam
- [a2a-mcp-agent-flows.md](../development/a2a-mcp-agent-flows.md) — the storyboard run
  compared protocol by protocol, and the framing that does differ per transport
- [bdd-drop-impl.md](bdd-drop-impl.md), [bdd-migration.md](bdd-migration.md) — retirement
  notes for the two plans that got the suite here
- [xpass-graduation.md](../../.claude/rules/workflows/xpass-graduation.md) — the protocol
  for retiring a ledger row
