# tests/bdd/steps — pointer

Step definitions EXECUTE a scenario; they never carry its logic. The scenario in the
generated `BR-UC-*.feature` file is the contract — the logic, the boundary conditions as
examples, the exact expected outcome. A step that decides an outcome the scenario does not
state has moved the contract out of the feature file.

**The five authoring rules are in [tests/CLAUDE.md](../../CLAUDE.md) § BDD authoring
discipline. Read them before writing a step.** [tests/bdd/CLAUDE.md](../CLAUDE.md) has the
helper-per-situation table. This file adds only what a step author gets wrong.

## `generic/` vs `domain/`

`generic/` (18 files) is reusable across use cases: auth, entity setup, wire assertions.
`domain/` (30 files) is one use case's own. A step that two use cases need belongs in
`generic/`, and a third copy of one is a DRY defect — `test_architecture_bdd_no_duplicate_steps.py`
fails on three step functions with identical bodies.

## Never mock in a Given

The only defensible patch target is a system **we do not own and a test cannot call**: the ad
server, an external creative agent, Slack, an LLM. Those are the harness's
`EXTERNAL_PATCHES`, set up by the env, not by a step.

Our own code is never a legitimate target, and neither is our own database. Two shapes to
recognise, both of which existed here:

- **A mock that manufactures an outcome production cannot reach.** A fixture branching the
  adapter so it raises an error no production path raises makes the scenario runnable without
  making the behavior real. What the run then grades is the boundary carrying an adapter's
  error to the wire — not the policy the scenario is named after. If the code has no raise
  site, the gap is the raise site.
- **A patch that removes a production step.** `validate_setup_complete` was patched out
  in-process while the live e2e_rest server enforced it: one scenario, two productions, which
  rule 1 forbids by construction. Removing it left 96 of 180 UC-002 scenarios red, and none
  was a production defect — the tenant was half-seeded. Seed the state the production path
  reads instead.

**If the harness cannot express the state a scenario needs, change the harness** — an env
method, a factory, a `realize_e2e` branch. That is not a bigger job than the patch; it is the
job.

## Three more that are guard-enforced

- **A Then asserts a VALUE, through the guarded helpers.** `result.assert_wire_error(code,
  recovery=..., field=...)` for errors; `wire_field(ctx, "x")` / `require_payload(ctx)` for
  success. Never `ctx["error"]`, never `.error_code` on a reconstruction, never a hand-rolled
  envelope index, and never the buyer-facing message — it is a function of the code through
  `CODE_TABLE`, so asserting both compares the table to itself.
- **A Then must not re-read the Given.** If deleting the When would still let the Then compute
  its actual value, the assertion cannot fail. Set a distinctive value in the Given and read
  it back off the dispatched result.
- **Prove the scenario ran.** A missing step definition or an unwired harness route xfails at
  fixture setup, silently, by design. Run the touched slice with `-rxX` and read it: sub-second
  wall time and "No harness wired" are the tells. Three markers once kept the same scenarios
  dormant — 48 items, 48 xfailed, 0 executed — and a live production defect with them.
