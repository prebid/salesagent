# Engineering standards

How to engineer a change correctly in this codebase: where logic belongs,
what a test must prove, and what a change to protocol behavior owes the
spec. Read it before you start writing, and run the
[verification list](#verify-the-change) before you consider a change
complete. These standards are also the criteria every pull request here is
reviewed against — but each one stands on its own as correct engineering,
not as review compliance.

Two properties bind them together:

- **Every standard binds equally.** A small violation of a standard is
  still a violation; none of them is optional polish.
- **Symptoms share roots.** Fifteen copy-pastes, a hand-assembled
  response, and a mock-heavy test are usually one design fault — a
  missing helper, a bypassed boundary — expressed three ways. Fix the
  design choice, and the symptoms never appear.

## Contents

- [Solve the problem the issue posed](#solve-the-problem-the-issue-posed)
- [Put logic in its layer](#put-logic-in-its-layer)
- [Say each piece of logic once](#say-each-piece-of-logic-once)
- [Follow the codebase's own conventions](#follow-the-codebases-own-conventions)
- [Write tests that can fail](#write-tests-that-can-fail)
- [Assert on the wire](#assert-on-the-wire)
- [Ground protocol behavior in the spec](#ground-protocol-behavior-in-the-spec)
- [Prove behavior with live BDD scenarios](#prove-behavior-with-live-bdd-scenarios)
- [Keep every allowlist shrinking](#keep-every-allowlist-shrinking)
- [Write current Python](#write-current-python)
- [Verify the change](#verify-the-change) — the checklist to run before you call a change complete

## Solve the problem the issue posed

A change exists to close one or more issues, and the issue states the
requirement — the outcome, not the mechanism. Engineer against that:

- **State the goal in the issue's own terms** in the pull request
  description, and say where the solution lives. A change whose own
  purpose is vague is not finished.
- **Do not narrow the problem.** A technically clean implementation of a
  smaller problem than the issue described leaves the issue open.
- **Do not special-case a transport.** A2A, MCP, and REST are surfaces
  over one core; a per-transport code path, error code, or test means
  behavior leaked out of the shared core, and the leak is a defect by
  construction — see
  [architecture-principles.md](architecture-principles.md).
- **Do not patch a symptom with another guard or special case.** When a
  solution starts accumulating patches and exceptions, the design
  underneath is wrong; reconsider the approach instead of adding the
  next patch.

## Put logic in its layer

Each layer does one job, and logic in the wrong layer runs for some
callers and not others. The placement of a function's callees matters as
much as its own: a clean wrapper calling a helper full of business logic
has the same defect one level down. The following table maps the layers
to this repository.

| Layer | Where it lives | Allowed | Forbidden |
|---|---|---|---|
| Transport wrappers | `src/routes/api_v1.py`, tool wrappers in `src/core/main.py`, `src/a2a_server/` | Identity resolution, error-format translation, protocol framing, forwarding every parameter to `_impl` | Business logic, validation, data transformation, database access, external calls |
| Business logic | `_impl` functions in `src/core/tools/` | Orchestration, validation, calling repositories and services, raising `AdCPError` subclasses, audit logging | Transport imports, protocol types, direct session management, direct external API calls |
| Repositories | `src/core/database/repositories/` | ORM queries, tenant-scoped access, model factory methods | Business logic, validation beyond data integrity, external calls, transport awareness |
| Adapters | `src/adapters/` | External API calls, protocol translation, retry logic | Database access, business-rule enforcement |
| Services | `src/services/` | Policy, targeting, webhooks, coordination between repositories and adapters | Transport awareness, direct HTTP handling |
| Admin UI | `src/admin/` | Routes, templates, sessions, calling business logic | Duplicating business logic, direct ORM model construction |

Before you place a change, consult the [placement
table](request-lifecycle.md#where-does-my-change-go) in the request
lifecycle. The admin UI deserves extra care — it is the most common home
of duplicated business logic. An `if adapter_type == "x":` branch in
business logic is an adapter leak.

Two boundary rules follow from the layering:

- **Never serialize by hand.** `_impl` returns the declared response
  model and stops; the boundary serializes it once. A hand-assembled
  response dict, or a `model_dump()` inside `_impl`, forks the wire
  contract per call site
  (enforced by `ruff-serialization.toml` and
  `.ast-grep/rules/serialize-only-at-the-edges.yml`).
- **Raise typed errors, never build envelopes.** `_impl` raises an
  `AdCPError` subclass from `src/core/exceptions.py`; the boundary
  translator builds the buyer-facing envelope. Never raise a bare
  `ValueError`, and never return an error object from `_impl` — the
  full rule set is in
  [architecture-principles.md](architecture-principles.md).

## Say each piece of logic once

Duplication is semantic, not textual: two blocks are duplicates when
they solve the same problem, a single parameterized function could
replace both, and a bug fix in one would need replicating in the other.
That replication is the harm — the next person who fixes one copy misses
the other. Syntactic variation does not change the diagnosis.

Where duplication concentrates, and what to do instead:

- **Transport wrappers** — keep them thin pass-throughs; shared
  validation or request construction belongs in `_impl` or a shared
  helper.
- **Query patterns** — the same `select(Model).filter_by(...)` in two
  places becomes a repository method
  (canonical example: `src/core/database/repositories/media_buy.py`).
- **Validation shared by create and update paths** — extract one
  validator both call; [patterns-reference.md](patterns-reference.md)
  documents the anti-pattern.
- **Error-handling blocks, response construction, test setup** — the
  same failure mode handled with slightly different messages, or the
  same mock scaffolding per test, is duplication; use a helper or a
  harness environment.

Intentional repetition is fine: test parametrization,
protocol-required boilerplate, and framework conventions repeat by
design. The mechanical backstop is `check_code_duplication.py` in
`make quality` — the `.duplication-baseline` count may not rise.

## Follow the codebase's own conventions

The standard is internal consistency, not external style: the codebase
establishes a convention wherever a dominant usage exists, and the
minority usage is the defect. A concept named two ways breaks grepping;
an error message formatted three ways confuses API consumers. Match the
majority pattern:

- **Names**: `_impl` suffix for business logic, `_raw` for A2A wrappers,
  no suffix for primary wrappers; `*Request`, `*Response`, `*Repository`
  class names; one name per concept across modules.
- **Errors**: codes from the shared vocabulary, message formats matching
  the neighbors, user-facing wording for API errors.
- **Logging and config**: consistent levels, no secrets in logs, config
  read the way sibling modules read it.
- **None handling**: match the file's convention for
  `if x is None:` versus `if not x:` on the same concept.

One trap: the dominant pattern in a legacy file is not the convention.
[patterns-reference.md](patterns-reference.md) names the canonical file
per pattern and lists the legacy files whose surrounding code you must
not imitate.

## Write tests that can fail

Ask one question of every test: if the production code this test covers
were broken, would the test catch it? A test that cannot fail is worse
than no test — it manufactures confidence. Write tests through the
harness and factories — [tests/CLAUDE.md](../../tests/CLAUDE.md) is the
authoritative recipe — and avoid the shapes that cannot fail:

- **Mock echo** — mocking the function under test, then asserting the
  mock's return value came back, verifies the mock framework. Assert on
  production output (hard rule 5 in tests/CLAUDE.md).
- **Assertion-free or truthiness-only tests** — `assert result` and
  `assert x is not None` verify nothing; compare values, and set
  distinctive inputs (`impressions=5000`, not a factory default) so the
  assertion can only pass if production carried the value through.
- **Split mock assertions** — write
  `mock.assert_called_once_with(x=expected)`, never
  `assert_called_once()` plus a separate `call_args` check: the count
  check passes even when the arguments are wrong (guard:
  `tests/unit/test_architecture_weak_mock_assertions.py`).
- **Over-mocking** — more than five `patch()` targets on one test, or
  mock setup longer than the test, means the harness environment should
  own the wiring; never hand-patch a dependency the environment already
  manages.
- **Integration tests without integration** — a test in
  `tests/integration/` that mocks the database covers nothing a unit
  test does not.
- **Testing the framework** — Pydantic validation, ORM behavior, and
  language builtins are not your code.
- **Happy path only** — every changed behavior needs its error paths
  proven too.

Setup rules the guards enforce: factory-boy factories from
`tests/factories/` for all data, no `get_db_session()` or
`session.add()` in test bodies, identity from
`PrincipalFactory.make_identity(...)`.

## Assert on the wire

What a test asserts must be what the buyer received, not an in-memory
reconstruction — a value that never crossed the transport boundary
proves nothing about the wire contract.

- **Error paths**: assert with `assert_envelope_shape()` from
  `tests/helpers/envelope_assertions.py` on
  `result.wire_error_envelope`, with the required `recovery` argument.
  In BDD steps, use `assert_wire_rejection(ctx, code, recovery=...,
  field=...)` — both delegate to `TransportResult.assert_wire_error`,
  the single shape authority. Never assert on the reconstructed
  exception (`isinstance`, `.error_code`) — reconstruction is lossy —
  and never hand-extract `envelope["errors"][0]["code"]`.
- **Success paths**: `result.payload` for values;
  `result.require_wire()` when the assertion is about serialization
  itself. In BDD steps, `wire_field(ctx, ...)` / `wire_dict(ctx)`. A
  `model_dump()` round-trip proves model self-consistency, not what
  crossed the wire.

The full policy, including per-transport authenticity of the captured
envelope, is in [tests/CLAUDE.md](../../tests/CLAUDE.md) § "Error
verification policy".

## Ground protocol behavior in the spec

Any change to AdCP behavior — a request/response contract, error
emission, idempotency, governance, or a capability — must be grounded in
the spec before you write code. Grounding a protocol feature in a
downstream artifact — an SDK error code, an internal doc — has produced
a feature built inverse to the spec. Pure internal refactors are exempt.

- **Cite before coding.** Record the spec section, the pinned version,
  and the graded storyboard step (or note "ungraded") in the pull
  request description or planning note — the spec-grounding gate in
  [CLAUDE.md](../../CLAUDE.md).
- **The pinned version is the authority.** Confirm it in
  [docs/adcp-spec-version.md](../adcp-spec-version.md) and the `adcp`
  pin in `pyproject.toml`; when a version bump is in flight, the target
  version is the pin.
- **The SDK is a cross-check, never the authority.** The spec is the
  contract; everything else — including the installed `adcp` SDK — is
  derived and can diverge.
- **Wire values must be on-wire values.** Every status, error code, and
  enum value in code and tests must exist in the pinned spec's enum
  definitions.
- **Keep the pin guarded.** A change to the `adcp` pin must update
  [docs/adcp-spec-version.md](../adcp-spec-version.md) and
  `tests/unit/test_adcp_spec_version.py` together.

## Prove behavior with live BDD scenarios

A protocol-behavior change is complete only when a wired, **executing**
BDD scenario proves it. Mock-heavy `_impl` unit tests are not proof, and
neither is a scenario that exists but never runs — a dormant scenario
claims a thoroughness that never executes.

- **The scenario must be live.** Not auto-xfailed, not in the
  `tests/bdd/conftest.py` xfail registries or
  `tests/bdd/e2e_rest_known_failures.txt`, its steps bound, its feature
  registered, its step not shadowed by a generic parser. Liveness is
  measured, not inferred: `tests/bdd/scenario_liveness.py` emits a
  per-scenario record from a real run, and
  `scripts/audit/scenario_liveness_join.py` joins it into the
  storyboard-checks pipeline.
- **The scenario must be transport-blind by construction.**
  Given/When/Then never name a transport or touch wire shapes; the
  harness parametrizes one scenario across A2A, MCP, and REST, plus
  `e2e_rest` in-network. Coverage on one transport only is a gap — the
  unified scenario would fail on the uncovered transports, and that red
  is the defect the single-transport coverage hides. A single-transport
  scenario is legitimate only for a pure transport mechanic with the
  reason stated at the scenario.
- **Setup and dispatch go through the harness.** Given steps use env
  methods and factories; `dispatch_request` performs the dispatch and is
  the only writer of the context keys it stores. Reach what it produced
  through the accessors in `tests/bdd/steps/_outcome_helpers.py`:
  `require_payload` and `wire_field` for values, `assert_wire_rejection`
  for refusals. They fail loudly when the dispatch is missing or errored,
  so a step cannot quietly assert nothing, and they read as the behavior
  being graded rather than as plumbing.

  What makes a step wrong is not which context key it names but what it
  does with it. Building the error test-side fabricates a result the
  transport never produced. Asserting on the reconstructed exception alone
  grades the reconstruction rather than the wire.
  `test_architecture_bdd_wire_discipline.py` catches both.
  `ctx["result"].assert_wire_error(...)` reads the real wire and is
  correct; the accessor is preferred because it is guarded.
- **Cite schema divergence.** Generated `BR-*.feature` files can be
  edited locally (generation merges semantically); where a scenario is
  corrected against the pinned schema, add a comment citing the exact
  AdCP version and file.

## Keep every allowlist shrinking

Guard allowlists, `.duplication-baseline`, and the xfail and
known-failure registries track pre-existing debt. Debt can only be paid
down; growing a ratchet converts a visible defect into an invisible,
permanent one. The invariant is binary — a ratchet either grew or it did
not:

- **Never add an allowlist entry to make a new violation pass.** Fix the
  violation in the same change.
- **Never defer a new violation with a FIXME.** Only pre-existing debt
  is allowlisted, and every allowlisted entry's `# FIXME(#<n>)` must
  cite a GitHub issue or pull request number, never a beads id.
- **Never copy an allowlisted shape.** "The existing code does it this
  way" fails when the existing code is tracked debt — follow the
  canonical pattern instead.
- **Remove entries you fix.** A stale entry is its own defect.
- **Never treat xfail-set growth as a fix.** When a Then step asserts
  on in-process state and fails on e2e, re-express the assertion on
  transport-observable signals; registering the tag in a skip set hides
  exactly the gap the live run exists to expose.

The guard inventory and design rationale are in
[structural-guards.md](structural-guards.md).

## Write current Python

Beyond what the linters and mypy already enforce, the semantic
standards:

- **SQLAlchemy 2.0**: `select()` + `scalars()`, never `session.query()`;
  `Mapped[]` annotations on new columns; `| None`, not `Optional[]`.
- **Pydantic v2**: `model_dump()` / `model_validate()` /
  `field_validator`, never the v1 APIs.
- **Async correctness**: no unawaited coroutines, no `asyncio.run()`
  inside a running loop; in tests, never `side_effect=lambda:
  async_func()` — the lambda hides the coroutine from
  `iscoroutinefunction`.
- **Types**: typed models or TypedDicts instead of `dict[str, Any]`;
  wrappers and endpoints return typed models, not raw dicts; no
  framework types (`Request`, `Context`) in business logic.
- **Errors and resources**: no bare `except:`, no silent
  `except Exception: pass`; sessions, files, and connections in context
  managers.
- **Logging**: lazy arguments — `logger.info("msg %s", val)`, not an
  f-string.

## Verify the change

Run through this list to check that the work is correct:

1. `make quality` passes — format, lint, mypy, unit tests, and every
   structural guard.
2. All outbound HTTP goes through
   `src/core/security/outbound_http.py` (`send` / `asend`) — the
   egress bans in `ruff-egress.toml` run in `make quality-ci`.
3. `git diff main...HEAD -- 'tests/unit/test_architecture_*.py'
   .duplication-baseline tests/bdd/conftest.py
   tests/bdd/e2e_rest_known_failures.txt` shows no added allowlist,
   baseline, or xfail entries — and shows removals for every violation
   you fixed.
4. Every changed error path is asserted with `assert_envelope_shape()`
   (or `assert_wire_rejection` in BDD steps), `recovery` included; no
   new assertion reads `result.error`, `isinstance`, or `.error_code`.
5. Every protocol-behavior change carries a spec citation — section,
   pinned version, storyboard step or "ungraded" — in the pull request
   description.
6. Every changed behavior is proven by a BDD scenario that executes on
   all transports; no new per-transport scenario without a stated
   transport-mechanic reason.
7. New tests use the harness environments and `tests/factories/`
   factories — no `get_db_session()` or `session.add()` in test bodies,
   no hand-rolled mock scaffolding for dependencies an environment
   manages.
8. No new logic sits outside its layer: wrappers forward, `_impl`
   decides, repositories query, boundaries serialize and translate
   errors.
9. The pull request title uses a Conventional Commits prefix
   (`.github/workflows/pr-title-check.yml` enforces it), and the
   description states the goal in the closed issue's terms.
10. For refactors, `tox -e integration` passes; for protocol or schema
    changes, `./run_all_tests.sh` passes.
