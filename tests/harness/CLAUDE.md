# tests/harness — pointer

The harness is what makes one scenario run on four transports against real production. When
a test cannot express the state it needs, **this directory is what changes** — not the
production code under test. [tests/CLAUDE.md](../CLAUDE.md) § Which kind of test carries the
rule; this file says what belongs in an env.

## `EXTERNAL_PATCHES` means EXTERNAL

The name is the contract. A patch target qualifies only if it is a system **we do not own and
a test cannot call**: the ad server, an external creative agent, Slack, an LLM. Determinism
controls (`time.sleep`, `random.uniform`) are not behavior and are fine.

Our own code never qualifies, and neither does our own database. Measured across the nine
BDD-routed envs: 20 patches, of which 5 are genuinely external, 2 are determinism, and 13 are
ours — audit loggers, the context manager, the policy service, the format resolver, the
property-list resolver, sync internals. Each one of those is a scenario not grading what its
name says. Do not add the fourteenth.

**A production GATE is never a patch target.** `validate_setup_complete` was in
`MediaBuyCreateEnv.EXTERNAL_PATCHES` stubbed to `return None`, while the live e2e_rest server
enforced it — one scenario, two productions, which BDD rule 1 forbids by construction. It
came out and the rows the gate grades went in; all 180 UC-002 scenarios pass with the gate
real on every transport. The patch had been standing in for a single missing seeded row.

## What an env owns instead

- **Seeding, through the factories**, bound to the env's session on `__enter__`. Seed every
  row the production path READS, not just the ones the scenario mentions — a gate that reads
  three rows needs three, and a tenant column belongs to whoever creates the tenant.
- **Typed setup methods** the Givens call: `set_adapter_response(...)`,
  `set_registry_formats([...])`, `set_http_status(...)`, `set_egress_hatches(...)`. A Given
  never reaches into `env.mock[...]` for something an env method should own.
- **One dispatch path per transport**, each observing real wire bytes. There is no IMPL
  transport: a direct in-process call is not a transport, and modelling it as one gave every
  "assert on the wire" rule an escape hatch.
- **`realize_e2e`** (`_realize.py`) for the e2e branch of a setup intent — a DB row on the
  live server, or driving the real API.

## Ids need no scheme

`integration_db` is function-scoped and creates a uuid-named database per test, so fixed ids
cannot collide across xdist workers. `TenantFactory` owns id generation through a `Sequence`.
Four competing id schemes once existed here because each layer tried to solve a collision
that the per-test database had already solved; `docs/development/fixture-layer-audit.md`
records how that happened.
