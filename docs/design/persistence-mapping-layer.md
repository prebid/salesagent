# The persistence mapping layer

**Status:** design, not implemented. Proven on `Creative` first (salesagent-3zmsd).

A domain model is the pinned SDK model the seller extends. A row is what Postgres holds.
Today those are the same object: a repository returns the ORM class, so every caller above it
reads ORM attributes and the domain model carries whatever persistence needed. This document
defines the layer that separates them, for every aggregate rather than for one.

## What is actually wrong today

Measured at `b9138b19c`, adcp `9.0.0b3+sa.8qela.f33d4fc71`:

- **Repositories return ORM rows.** `CreativeRepository.get_by_id(...) -> Creative` is the
  ORM class, not a domain model. There is no `to_domain` anywhere in the repository layer, so
  the row IS the value that flows upward into `_impl` functions and adapters.
- **26 repository classes across 24 modules share no base class.** `BaseUoW` has 12 domain
  subclasses; the repositories below it have nothing in common. So each one hand-rolls its
  own mapping, and there is no single place a rule about mapping could live.
- **Mapping already exists, unnamed and partial.** `repositories/creative.py:49`
  `_identity_columns(identity) -> dict` is a hand-written mapper whose docstring explains that
  `create` and `set_identity` must share it, "which is what happened while the identity was
  three partial columns and each path assigned them itself". That is this layer, for four
  columns of one aggregate, discovered by a bug.
- **16 fields are marked `Field(exclude=True)` on a wire model.** `exclude=True` strips a
  field from the wire *and* persistence *and* the idempotency hash, so each of those fields
  cannot persist where it is declared. They are on the nested domain objects — `Format` (4),
  `Product` (3), `PackageRequest` (2), `AffectedPackage` (2), `SignalDeployment` (2),
  `SyncCreativeResult` (2), `Creative` (1) — and 0 of 14 request DTOs and 0 of 14 response
  models carry one, so the coupling is one level below the tool boundary.
- **Twelve pinned `CreativeAsset` fields have no column** and live inside the `data` JSONType
  blob: `assets`, `component_assets`, `industry_identifiers`, `inputs`, `placement_ids`,
  `placement_refs`, `provenance`, `representation_selection`, `rights`, `tags`, `weight`,
  plus `format_id` (accounted for by `legacy_format_id`). Callers know that layout: the read
  side queries `Creative.data["assets"]` and `jsonb_exists(Creative.data["tags"], tag)`
  directly (salesagent-dufcl).

None of this is a missing patch on one model. It is a missing layer.

## The three models, named

| | what it is | who owns it | may it change? |
|---|---|---|---|
| **domain model** | the pinned SDK class the seller extends, plus the wire serializer | `src/core/schemas/` | the seller MAY extend it; it declares no field that exists only to persist |
| **row** | the SQLAlchemy ORM class | `src/core/database/models.py` | freely — it is private to persistence |
| **mapper** | the one translation between them | `src/core/database/mapping/` (new) | per aggregate |

The domain model stays extendable. That is deliberate and is not what this layer restricts:
a seller may declare a field the pin does not have. What the layer removes is the need to
declare a field *for persistence's sake*, and the `exclude=True` trap that follows from it.

## The interface

```python
class RowMapper(Protocol[DomainT, RowT]):
    """The one translation between a domain model and its row."""

    domain: type[DomainT]
    row: type[RowT]

    #: Columns persistence owns outright: the caller never supplies them and the domain
    #: model never sees them. Derived coverage (below) requires each to be named here.
    repository_owned: frozenset[str]

    def to_row(self, obj: DomainT, /, **owned: Any) -> RowT: ...
    def to_domain(self, row: RowT, /) -> DomainT: ...
```

Three rules make it an interface rather than a convention:

**1. Column coverage is DERIVED, not declared.** At mapper construction, every column in
`row.__table__.columns` must be accounted for by exactly one of: a field of the same name on
the domain model, an explicit per-column override, a member of `repository_owned`, or a
column the database defaults. A column that is none of those raises `TypeError` at import
time, naming the column.

This is the rule that earns the layer. A by-name row copy that omits silently has already
caused a defect in this repo once, and in a shape that could widen auth. The omission set is
computed from `__table__.columns` so a new migration that adds a column breaks the mapper
loudly instead of writing `NULL` quietly. It refuses at construction rather than in a test,
per the epic's directive: write the code so the incorrect representation is impossible.

**2. The blob is the mapper's business, not the caller's.** A pinned field with no column is
mapped into `data` BY THE MAPPER, and read back out by it. `Creative.data["assets"]` stops
appearing in tool code. Where a blob-held field needs to be *queried*, that is a repository
method over a JSON path — the repository still owns queries — but the shape is stated once,
in the mapper, instead of at each call site.

**3. One direction per obligation, and both are explicit.** `to_row` takes the domain object
plus the repository-owned values as keyword arguments, so a caller cannot forget `tenant_id`;
it is a required argument, not a field someone might leave unset. `to_domain` takes a row and
returns a domain model, validated — so a row that cannot produce a valid domain model is a
loud failure at the read, not a malformed response three layers up.

## How it composes with what already exists

Nothing about the Unit of Work changes. It still opens the session, commits on a clean exit,
rolls back on a raise, and repositories hang off it. Nothing about the repository's ownership
changes either: it owns queries, tenant scoping and the business rules about which rows a
caller may see.

**THE REPOSITORY'S RETURN TYPE DOES NOT CHANGE.** It keeps returning ORM rows, callers keep
mutating them, and the Unit of Work keeps flushing — which is what SQLAlchemy's identity map
and unit of work are for. An earlier version of this document said the opposite, that
repositories must return domain models, and that rule was wrong. It was not solving a problem:

- it required a carrier (`StoredCreative`) because a `CreativeAsset` alone does not hold
  `created_at`/`updated_at`, which the response needs;
- frozen, that carrier broke every write path, because `_update_existing_creative` uses the
  row as its working object (`.updated_at`, `.name`, `.status` across three branches) and
  nothing calls save — the row is session-attached and the UoW flushes the assignments;
- unfrozen, it would have been worse: the assignments would succeed against a detached copy
  and silently never reach the database;
- and it put row -> carrier -> row transformations in the path for no gain.

So the layer does one thing: it owns the translation between a row and the pinned shape, at
the edges that need one. Nothing is required to stop using the ORM as an ORM.

## The problem it actually solves

Measured at `ea3a3e54a`: **21 sites outside the repositories read the storage layout
directly** — `creative.data["rejection_reason"]`, `creative.data.get("tags", [])`,
`creative.data["media_buy_id"]`, `creative.data["ai_review"]`. Each one has to know that the
value lives inside a JSON blob and under that exact key.

That is the coupling worth removing, and it is how `tags` came to have four readers and no
writer (`salesagent-gxudh`): no single place owned the key, so the write path simply never
wrote it while three readers and two pinned filters read it.

Routing those 21 sites through the mapper names the layout once. It changes no return type,
no write path and no wire output.


### Where a repository-owned column lives

`Creative`'s columns split cleanly, which is why it is the pilot:

- **domain**: `creative_id`, `name`, `format_kind`, `legacy_format_id`, `legacy_format_key`,
  `format_option_ref` (via `CreativeFormatIdentity` — the existing `_identity_columns`
  becomes the mapper's identity override), and the twelve blob-held pinned fields.
- **repository-owned**: `tenant_id`, `principal_id`, `status`, `group_id`, `strategy_id`,
  `created_at`, `updated_at`, `approved_at`, `approved_by`.

`Creative.principal_id` is currently declared on the DOMAIN model as an added field
(1 of the 51) and is simultaneously an ORM column. Under this layer it is repository-owned
and leaves the domain model — which is the shape the whole exercise is for.

## What this must not become

- **A second representation that drifts.** The point is fewer values, not three. The domain
  model remains the value; the row is a projection; there is no DTO-of-a-row.
- **A generic ORM-to-pydantic bridge.** Per-aggregate mappers with explicit overrides, not a
  framework that infers. `from_attributes=True` already handles the trivial direction; the
  mapper exists for the parts that are not trivial, and those are exactly the parts a
  framework would get wrong silently.
- **A reason to resurrect `model_dump()` in business logic.** The mapper is a named edge like
  the wire and the idempotency hash. A tool still hands the model through.
- **A data migration.** No column moves in the first cut. The mapper describes the schema
  that exists; changing the schema is a separate, later decision with its own gate.

## Status: steps 1 and 2 are landed, and that is where this stops

1. `src/core/database/mapping/_base.py` — the `RowMapper` base and the derived coverage check
   that refuses at class definition. **Landed** (`194d8f06f`), fire-probed seven ways in both
   directions against the real 16-column `creatives` table, including: append a column to the
   live table and the unchanged mapper refuses, which is what proves the omission set is
   derived and not a snapshot.
2. `src/core/database/mapping/creative.py` — the `CreativeAsset` ↔ `Creative` mapper,
   absorbing `_identity_columns`. **Landed** (`0e1433115`). Accounting: 2 domain + 8
   repository_owned + 6 override = 16 columns, zero unaccounted.

**Step 3 is CANCELLED. Step 4 is NOT** — an earlier version of this section said both were,
and that was wrong.

Step 3 was "the repository returns domain models", which is the return-type rule this document
used to state and the owner overruled. With the rule gone it answers no question.

Step 4 was "delete `Creative.principal_id` from the domain model, now repository-owned", and
that survives on its own: removing a seller-state field from a wire model does not require the
repository to change what it returns. It is also larger than the one field it named. Measured
at `2e09d1063`, TEN fields are declared `Field(exclude=True)` on a wire model and absent from
the pin:

    Creative.principal_id          Product.allowed_principal_ids
    Format.is_standard             Product.countries
    Format.platform_config         Product.implementation_config
    Format.requirements            SyncCreativeResult.internal_status
    PackageRequest.creative_ids    PackageRequest.impressions

`exclude=True` strips a field from the wire AND persistence AND the idempotency hash, so each
of these cannot do the job it was added for. The owner's rule: "if we need it for persistence
then it becomes the persistence model." That is step 4's real scope. Three beads existed
only to resolve problems they created and are closed as refuted: `salesagent-eusx2` (what
carries the repository-owned columns on a read), `salesagent-6aa6y` (whether `to_domain`
should refuse a row that `list_creatives` deliberately degrades), `salesagent-pyvcb` (how a
frozen carrier expresses the upsert's mutations).

**What remains worth doing, and it is small:** route the 21 blob-reading sites through the
mapper so the layout is named once. No return type changes, no write path changes, no wire
output changes.

**Graded behaviourally.** The creative BDD scenarios on a2a, mcp, rest and e2e_rest must pass
exactly as before, measured on a box run against a named baseline, judged per nodeid. A nodeid
that passed before and fails after is a defect in the mapper; one that was xfailed before and
passes after needs explaining before it is believed.

No schema change, no data change, no wire change. If the wire moves, the mapper is wrong.

## What this document got wrong, kept so it is not repeated

The first version made a rule ("repositories return domain models") out of something that was
never a problem, and the rule generated all of the following before anything measured it: a
carrier type, a frozen-versus-unfrozen question, a tolerance conflict with `list_creatives`,
a mutation problem with the sync upsert, and a scope of ~31 call sites across six directories
in place of the ~18 in two that it claimed. The problem it was reaching for — 21 sites knowing
the JSON blob layout — needed none of that.
