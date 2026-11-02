# Fixture-layer audit

Why the same failure keeps arriving in different costumes, and what one shared
fixture layer would have to own.

## The measurement that settles it

| Suite | `session.add()` calls | local `make_*`/`build_*` helpers | failures on 2026-09-15 |
|---|---|---|---|
| bdd | **0** | 18 | **0** |
| unit | 34 | 106 | 42, all fixture-caused |
| integration | **547** | 44 | **212** |
| e2e | 9 | 8 | 0 |

BDD went through a fixture consolidation. It seeds exclusively through factories
bound to the harness session, dispatches exclusively through one seam, and has
**no** raw `session.add` anywhere. It is also the only large suite at zero
failures — 4162 scenarios in process, 1208 over e2e_rest.

Integration did not get that consolidation. It carries 547 raw `session.add`
calls across 92 files, and it produced 212 failures. Of those, essentially none
were production defects.

The correlation is the argument. The suites are testing the same system.

## What today's failures actually were

Every cluster diagnosed on 2026-09-15 reduced to the fixture layer:

| Cluster | Real cause |
|---|---|
| 35 `NotNullViolation: token_hash` | a constructor sweep that did not know which `Principal` was in scope |
| 13 `Tenant has no attribute admin_token` | a column deleted by design; fixtures still set it |
| 11 `AdCPAuthRequiredError` | `_call_get_products` accepted `tenant_overrides` and dropped it |
| 8 `'completed' == 'submitted'` | a constructor kwarg reached the identity, never the row |
| 7 wrong identity type | parent handed where the child is declared |
| 6 `create_step() missing persistent_context` | a seeding helper behind its repository |
| 5 `DID NOT RAISE` | `pytest.raises` on the wrong exception class |
| 5 `CREATIVE_REJECTED` expected | production correct against a pinned MUST |
| 3 `LimitSettings` | a test driving production through an env var nothing reads |

Two genuine production defects surfaced all day, and both were found while
fixing fixtures: a nested unit-of-work losing a write on the approval path, and
`platform_creative_id` storing our own id instead of the ad server's.

## The duplication, counted

One concern, many private re-implementations:

| Helper | Times defined |
|---|---|
| `_make_identity` | 16 |
| `build_rest_body` | 14 |
| `_make_request` | 11 |
| `_make_format` | 9 |
| `make_format_id` | 6 |
| `_build_account_block` | 5 |

`_make_identity` is defined in 14 files across unit AND integration, while
`PrincipalFactory.make_identity` exists and is the documented single source.

## Coverage of the model surface

39 ORM models. 58 factories across 19 files. **15 models have no factory**:

`AuditLog`, `Context`, `CreativeReview`, `GAMLineItem`, `GAMOrder`,
`IdempotencyAttempt`, `ObjectWorkflowMapping`, `ProductInventoryMapping`,
`Strategy`, `StrategyState`, `SyncJob`, `TenantManagementConfig`,
`WebhookDeliveryLog`, `WebhookDeliveryRecord`, `WorkflowStep`.

A test needing one of those has no option but `session.add`, which is most of
the 547.

## Four worked examples of the same disease

**One value, four schemes.** A tenant id is decided by `TenantFactory`
(`Sequence`), overridden by `BaseTestEnv` (fixed `test_tenant`), overridden
again by `MediaBuyCreateEnv` (`uuid4` mint), and again by a local `_env()`
wrapper (a hand-picked hyphen-free constant). Three of the four solve nothing:
`integration_db` creates a uuid-named database PER TEST, so ids cannot collide
across workers.

**A derivation that manufactured its own constraint.** `subdomain` is an
independent unique column, and the server resolves a tenant by indexed lookup
(`tenant_id_for(subdomain=...)`), never by parsing a tenant id. The factory
derives `f"pub-{tenant_id}".replace("_", "-")` anyway, which invents a shape
constraint on `tenant_id`, which then needed normalization, a "MUST be the only
derivation" rule, and three workarounds. The bug it was introduced to fix was a
MISMATCH BETWEEN TWO DERIVATIONS of one value.

**A wrapper that downgraded the thing it wrapped.** `_env()` pinned a fixed id
over an env that minted a unique one, reinstating the hazard the env avoided,
and its `**overrides` bag silently swallowed 14 callers' intent.

**The right seeder in the wrong place.** `seed_account_with_access` is the one
helper that gets account seeding right — the `Account` row AND the
`AgentAccountAccess` join, because resolution is access-scoped. It lived inside
`tests/bdd/steps/generic/`, so only BDD could reach it; three integration
modules imported across that boundary anyway. Ten or more others hand-roll the
pair with a different signature.

## The one cause under all of it: the env half-seeds

`IntegrationEnv.__enter__` does not seed a tenant or principal in process. Its
`_seed_e2e_identity` (`tests/harness/_base.py:1196`) is called at `:1332` in e2e
mode only, and the docstring gives the reason:

> "in-process they don't need one (identity is a mock)"

That was true when it was written. It is no longer. `call_impl` on these envs
dispatches through `invoke_tool`, so the REAL resolver loads the tenant from its
ROW. In-process now needs rows, and the env still only seeds for e2e.

Every symptom in this document follows from that single gap:

- **The hardcoded literals.** 165 occurrences of `"test_tenant"` across 20 files
  and 45 of `"test_principal"` across 7 would break if the env's default id
  changed, because ~150 tests seed the tenant themselves and the id is a
  hardcoded copy of the constructor default. They need to know the id only
  because the env did not create the row.
- **The fabricated identities.** `make_identity` is documented "without DB
  persistence" — correct for the era when in-process identity was a mock, and a
  lie once the resolver started reading rows. The 19 fabricating sites are
  leftovers of that assumption.
- **The four id schemes.** A test that must seed its own tenant must NAME it, so
  the id cannot be generated. Hence a fixed default, then per-env mints to escape
  the fixed default, then a per-file constant to escape the mint.
- **The eight status tests.** `MediaBuyCreateEnv(human_review_required=True)`
  reached the env-built identity while the resolver read the unseeded row's
  default, and production's correct answer was reported as a defect.

So the first change is not a new id scheme and not a rename. It is: **seed in
process through the same idempotent `setup_default_data` path already used for
e2e.** Then ~150 tests stop hand-seeding, stop needing to know the id, the
default can be generated, and the fabricating identity path has no remaining
caller.

## What a shared layer has to own

1. **One factory per model**, generating every field it owns so nothing
   downstream re-decides one. Including the 15 with none today.
2. **One seeding layer** for multi-row facts that must agree — an account plus
   its access join, a media buy plus its packages plus its account grant, a
   tenant plus a principal plus a credential whose hash matches.
3. **One identity derivation**, from seeded rows, so an identity cannot exist
   without its referents and cannot be the wrong type for the `_impl` it is
   handed to.
4. **One request-payload builder per tool**, shared across suites. This is the
   `build_rest_body` ×14 and `_make_request` ×11 case: the shape is genuinely
   common to unit, integration and BDD, and it is written per file.
5. **No raw `session.add` in a test body** — a rule that already exists and is
   ratcheted, with 547 outstanding violations.

## The ordering that follows from the data

Factories first (they unblock the 547), then the seeding layer, then identity,
then payloads. Each step deletes workarounds rather than adding a layer: the
test of whether a step is right is that code disappears.
