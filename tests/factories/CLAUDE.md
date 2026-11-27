# tests/factories — pointer

Every piece of test data comes from a factory here. The rule and the correct/wrong examples
are in [tests/CLAUDE.md](../CLAUDE.md) § Factory system; this file says what belongs in the
directory and what the gaps cost.

## The two kinds, and the one that is imported by mistake

- **ORM factories** create rows: `TenantFactory`, `PrincipalFactory`, `MediaBuyFactory`.
  `IntegrationEnv.__enter__()` binds the session, so a test inside a `with env:` block manages
  none.
- **Pydantic factories** build non-ORM models: `FormatFactory`, `FormatIdFactory`.
- **`tests/fixtures/` has namesakes that return plain dicts.** `from tests.fixtures import
  TenantFactory` is the wrong one. Import from `tests.factories`.

## A factory owns every field it decides

- **Generate ids here, once.** `TenantFactory` does it with a `Sequence`. Four competing
  schemes once existed across the harness and per-file wrappers because each layer re-decided
  an id the layer above had already decided; `integration_db` creates a uuid-named database
  per test, so there was never a collision to solve.
- **Derive nothing that is an independent column.** `subdomain` is its own unique column and
  the server resolves a tenant by indexed lookup, never by parsing a tenant id. A factory
  that derives one from the other invents a shape constraint on the id, which then needs
  normalization, a rule, and workarounds — and the bug that started it was two derivations of
  one value disagreeing.
- **`mint()` RECORDS a value as test-generated; it does not generate one.** `mint_id(prefix)`
  does both. The BDD steps, the harness and the factories take every `<prefix>_<hex>` id from
  it, so an id stays unique in a database that outlives the test, such as the e2e stack's.
- **Build a parent only when no id names it.** factory_boy evaluates every parent
  `SubFactory`, `Meta.exclude` included, so a caller that passes `tenant_id=` still gets the
  factory's own tenant row. Subclass `ParentRowsFactory` and declare each parent once, as
  `ParentRow(keys, SubFactory(...))`, both in `core.py`: the parent is built only when the
  caller passes none of its keys, and the base fills the keys from the parent it built or was
  given.

## Multi-row facts belong in a seeder, not in each caller

Some state is only true when several rows agree: an `Account` plus its `AgentAccountAccess`
join, because resolution is access-scoped; a tenant plus a principal plus a credential whose
hash matches; the rows a setup gate grades. Those live in one helper —
[tests/helpers/account_seeding.py](../helpers) for the account pair,
[tests/utils/tenant_setup.py](../utils) for the checklist rows, `seed_gam_tenant` and the GAM
onboarding rows (`onboard_gam`) — and
every suite calls the same one. Ten hand-rolled copies of a pair, each with its own
signature, is how one of them comes to seed the row without the join.

## The gap that forces `session.add()`

12 of 39 ORM models have no factory: `AuditLog`, `Context`, `CreativeReview`, `GAMLineItem`,
`GAMOrder`, `IdempotencyAttempt`, `ObjectWorkflowMapping`, `ProductInventoryMapping`,
`Strategy`, `StrategyState`,
`WebhookDeliveryRecord`, `WorkflowStep`. A test needing one has no option but a raw
`session.add()`, which is most of the 547 in `tests/integration/`. **Adding the missing
factory is the fix; the `session.add()` is the symptom.**
`docs/development/fixture-layer-audit.md` has the measurement.
