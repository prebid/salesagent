# tests/integration — pointer

**Integration is the second choice, and only when both halves hold.** Ask, in order:

1. Can BDD express this? A behavior a buyer can observe — a response field, an error code, a
   status, a webhook — is BDD's, and one scenario grades it on a2a, mcp, rest and e2e_rest
   against real production. Only when there is no wire, no transport, or the subject is a
   repository or migration contract does it land here.
2. **Is it a testable behavior at all?** A test that patches one layer so it can watch
   another layer's defensive code run is testing the patch. If the state the layer reads
   cannot occur in production, the fix is to make it unrepresentable in the design, not to
   fabricate it with a mock. This is the most common reason a file here should not exist.

The full order, and why a lacking harness is not a licence to monkey-patch, is in
[tests/CLAUDE.md](../CLAUDE.md) § Which kind of test.

## The rules that bind a file here

- **PostgreSQL is real.** Every environment, including this one. `integration_db` is
  function-scoped and creates a fresh uuid-named database per test, so fixed ids cannot
  collide across xdist workers — which means there is no reason to invent an id scheme.
- **Factories, never `session.add()`.** The factory-boy factories in
  [tests/factories](../factories/CLAUDE.md) bind to the harness session automatically.
  `tests/unit/test_architecture_repository_pattern.py` fails a new `get_db_session()` or
  `session.add()` in a test body at `make quality`, and its allowlist only shrinks. 275
  files live here; many predate the rule and are allowlisted debt. Do not copy them.
- **Seed the state the production path reads.** A create_media_buy test needs a tenant that
  is genuinely set up, because `validate_setup_complete` runs for every caller now — the
  `dry_run` channel that used to skip it is gone. [tests/utils/tenant_setup.py](../utils)
  seeds the rows nothing else wants; `seed_gam_tenant` seeds a whole GAM tenant.
- **Assert on the wire envelope for errors**, through
  `result.assert_wire_error(code, recovery=...)`. The reconstruction that used to let a test
  assert on a rebuilt exception is deleted. [tests/CLAUDE.md](../CLAUDE.md) § Error
  verification policy is the policy.

## Where the debt is written down

`docs/development/fixture-layer-audit.md` measures this directory against BDD: 547 raw
`session.add()` calls across 92 files here, versus zero in BDD, and the failure counts track
that difference. It also names the duplication — `_make_identity` defined 16 times while
`PrincipalFactory.make_identity` exists — and the 15 ORM models with no factory, which is
what forces the `session.add()`.
