# Retired: the `impl` drop from the BDD transport set

This file was the design for removing the `impl` pseudo-transport from the BDD
parametrization. That happened, and it went further than the plan allowed: the
plan's Non-goals section said "do NOT delete the impl machinery —
`Transport.IMPL`, `ImplDispatcher`, the private `_synthesized_error_envelope`
STAY". All three are deleted. The plan tells you to keep machinery that no
longer exists, so the plan text is gone and this note replaces it.

## What the tree carries

- `Transport` has six members and every one dispatches over a real wire: `A2A`,
  `REST`, `MCP`, and the three `E2E_*` siblings
  (`tests/harness/transport.py:142-157`). A `TransportResult` can therefore only
  come from a wire.
- `DISPATCHERS` maps those six to `A2ADispatcher`, `RestDispatcher`,
  `McpDispatcher`, and the three E2E dispatchers
  (`tests/harness/dispatchers.py:312-322`). There is no impl dispatcher.
- `_IMPL_ONLY` — the set of (use case, tag) pairs that skipped wire
  parametrization — is an empty set (`tests/bdd/conftest.py:4052`), so no
  scenario bypasses the wire at collection.
- The `call_impl` fallbacks the dispatch seams used when `ctx["transport"]` was
  unset are removed. Both seams name that removal in the error they raise
  instead (`tests/bdd/steps/generic/_dispatch.py:163`,
  `tests/bdd/steps/generic/when_request.py:37`).
- `env.call_impl(...)` survives and is not a transport
  (`tests/harness/_base.py:696`). Use it when the obligation is about what
  `_impl` returns or raises, and assert against the returned DTO or, with
  `pytest.raises`, the error class.

## Where the live description is

`tests/CLAUDE.md` § "Transport dispatching" states the rule and the reason,
under the paragraph headed **There is no IMPL transport**.
[bdd-harness-architecture.md](bdd-harness-architecture.md) records the coverage
measurement that decided it.

## Residue worth knowing about

`tests/bdd/conftest.py` still defines `is_impl` (line 1452) and still carries
xfail-ledger row substrings beginning `impl-` (for example lines 2488, 2514-2518,
2616). With the transport gone, no nodeid can match them, so they select
nothing: they are dead ledger entries rather than live markers.
