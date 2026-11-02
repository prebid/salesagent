# Retired: the BDD harness migration plan

This file was the plan for the seeding-and-assertion migration in `tests/bdd`
and `tests/harness` — four seeding items, a separate track for checks that grade
nothing, and the supporting work behind them. The migration ran. The plan's
inventory (site counts, "no request-side convergence today", `ctx["response"]`,
transport de-pinning listed as out of scope) no longer describes the tree, so
the plan text is gone. This note records what landed, what landed in another
form, and what is still open.

## Landed

- **BDD seeding has no hand-built creative or pricing payloads left.**
  `uv run python scripts/audit/creative_literal_sites.py --scope bdd` reports 0
  creative literals and 0 rejected payloads. The census script is still
  committed, states its definition in its docstring, and is how you re-measure.
- **A step declares deliberate malformation, and the dispatch grades it.**
  A step that means to send something invalid says so with `malformed(...)`,
  naming the code the buyer must receive, and
  `assert_declared_malformations` (`tests/factories/malformed.py:471`) fails the
  scenario when a payload and its declaration disagree with the pinned model.
  It runs from `gate_and_record` (`tests/bdd/steps/generic/_dispatch.py:178`),
  one function at the one place every dispatch passes, before `json_safe`
  rebuilds the dicts and drops the marker.
- **The gate captures the request-as-dispatched.**
  `record_dispatched_request` (`tests/bdd/payload_capture.py:168`) records each
  payload against the running test from the same gate, and
  `scripts/audit/compare_payloads.py` compares them across transports. That is
  the request-side check the plan reported missing.
- **`ctx["response"]` is gone**, with a guard: check E of
  `tests/unit/test_architecture_bdd_wire_discipline.py` (around line 992) refuses
  both subscript and `.get` access to it, with an empty allowlist. Steps read
  `ctx["result"].payload` through the named accessors instead.
- **The `env.mock` gap is declared rather than ambient.**
  `EXPECTED_ENV_MOCK_REACHES` in
  `tests/unit/test_architecture_e2e_rest_escape_hatches.py` pins 42 (step file,
  function) pairs and an AST detector fires on any pair the tuple does not list,
  including a reach in an undecorated helper. The tuple can shrink; it cannot
  grow silently.
- **Transport de-pinning**, which this plan listed under "Not in scope", has
  happened. Every scenario runs on `a2a`, `mcp`, and `rest` — plus `e2e_rest`
  when the in-network stack enables it — and `_IMPL_ONLY` is an empty set
  (`tests/bdd/conftest.py:4052`).

## Landed in a different form

Item 1 proposed one sentinel vocabulary replacing the three families. The
families are still three: `OMIT` (`tests/factories/request.py:99`),
`OMIT_IDEMPOTENCY_KEY` / `OMIT_ACCOUNT`
(`tests/harness/media_buy_create.py:32,38`), and the deliberate local clone
(`tests/bdd/steps/domain/uc011_accounts.py:56`). What replaced the unification is
the declared-malformation gate above, which grades a payload against the pin
instead of asking every omission to spell itself the same way.

## Still open

**The non-BDD literals.** `creative_literal_sites.py --scope tests` reports 147
hand-built creative literals outside BDD, 32 of them rejected by the pinned
model and 8 carrying pre-3.1.1 fields. The plan's disposition for these was
deletion, with any real obligation moving to `tests/bdd` as a scenario. Nothing
here says that stopped being the right call.

## Where the live descriptions are

Read `tests/CLAUDE.md` for authoring with the harness (environments, factories,
wire assertions, the ledgers), `tests/bdd/CLAUDE.md` for the step helpers, and
`docs/design/bdd-harness-architecture.md` for the vocabulary target it measured.
`scripts/audit/compare_runs.py` and `python3 -m scripts.audit.run_report` are the
before-and-after instruments the plan's Compare lines referred to.
