# Deferred structural changes 2 through 6

Parked 2026-08-21 while change 1 (the error code table, epic `salesagent-3dawm`)
runs long. Change 1's measured outcome is the calibration data at the end of this
note: use it to size the rest.

Source analysis: `rca-synthesis.md`, `rca-error-path.md`, `rca-layering-slices.md`,
`rca-guard-mortality.md`, `pr1721-structural-plan.md`.

---

## 2. A second unit of work cannot open on an open session

**Correctness first, cleanup second.** `media_buy_update.py:462` commits and
detaches the transaction on every `update_media_buy`. Mutations after that point
are dropped with no exception and no log.

### The framing that matters: two paths, not one

`BaseUoW.__enter__` refusing to nest catches the minority of sites.

| Path | Route | Caught by a check in `__enter__`? |
|---|---|---|
| A — a second `BaseUoW` | `__enter__` -> `get_db_session()` -> `scoped()` | Yes |
| B — `ContextManager` | `database_session.py:305` calls `get_scoped_session()` directly, never enters `get_db_session()`, never calls `begin_effects` | **No** |

Path B is 10 of the roughly 13 sites, including the worst one:

    media_buy_update.py:390   with MediaBuyUoW(...) as uow:
                      :446      ctx_manager.get_or_create_context(...)
                      :462      ctx_manager.create_workflow_step(...)   <- commits + closes
                      :593, :712, :756, :781, :849, :1417   ctx_manager.audit_*

`ContextManager` calls `session.close()` in nine places on a session an outer
unit still holds.

So the check belongs at the acquisition point. `get_scoped_session()` has four
callers, all inside `database_session.py`. One file.

### Separate two things currently conflated

- **The effect scope** (`begin_effects`, carries `preview=`) is a unit-of-work
  concept. Leave it in the unit of work.
- **An ownership counter** — who holds the thread-scoped session — belongs at
  acquisition. A raw acquisition has no `preview` flag, so `begin_effects`
  cannot simply move down.

### Refuse, not refcount

Refcounting is tempting: about 15 lines, only the outermost commits and closes,
every site fixed with no call-site edits. It fails on a concrete case.

`update_media_buy` calls `_sync_creatives_impl`, and both take `dry_run`. Under
refcounting, whose `preview` wins? The outer opened with `preview=False`; the
inner wants `True`. Refcounting picks one silently. Under refusal plus explicit
threading the question cannot arise: one unit, one flag.

Second problem: under refcounting, `after_commit` drains only at the outermost
exit, so an inner unit that logically completed defers its effects into a scope
it does not know exists.

Take refusal plus an optional `uow` parameter, generalizing the exemplar at
`_assignments.py:88-91`:

    if uow is None:
        uow = stack.enter_context(CreativeUoW(tenant_id))

### `ContextManager`'s destination already exists

It must not acquire a session. `prkv.16` gave `CreativeUoW` a workflow
repository so workflow-step writes join the caller's transaction.
`ContextManager`'s step operations fold into that repository. The 10-site fix
lands on a seam this PR already built.

### Sequencing

Flipping the check turns silent corruption into loud exceptions, so fix the
sites first. Land the counter in warn mode — log and count, do not raise — for
one full-suite run, because a static census cannot see dynamically dispatched
paths, admin background jobs, or the scheduler. Read the count, fix what it
found, then flip to raise.

That fits the `predict` atom: predict warn-mode count equals zero, verify, flip.

### What this deletes (measured, and smaller than first estimated)

| Artifact | Lines | Allowlist |
|---|---:|---:|
| `test_architecture_nested_unit_of_work.py` | 753 | 5 |
| `test_architecture_production_session_add.py` (already a stub, 0 tests) | 5 | 0 |

Corrections to the first estimate, both verified:

- `test_architecture_uow_effect_boundary.py` (393 lines) **survives**. It
  enforces that escaping effects route through `repo.after_commit()` or
  `repo.outbound()`. That is effect routing, not nesting.
- `test_architecture_repository_pattern.py` (833 allowlist) and
  `test_architecture_no_raw_select.py` (270) **survive**. They need Track A,
  below, not this change.

`effects.py`'s scope stack collapses to a single slot, and its docstring stops
documenting nesting as supported. One test file mentions the stack API, in a
docstring, so the collapse is a production simplification with no test win.

`BaseUoW` carries a `@session.setter` whose docstring reads "Deprecated setter —
only used by tests that mock uow.session". It has 25 users: 21 in
`test_media_buy.py`, 2 in `test_gam_placement_targeting.py`, 1 each in
`test_update_media_buy_transport_wrappers.py` and `test_dry_run_no_persistence.py`.
Those tests migrate to a real unit of work through factories rather than delete.
The production setter goes, which is the win.

### Why it is worth doing anyway

Once nothing nests and `ContextManager` stops self-acquiring,
`get_scoped_session()` has one reachable path, and Track A becomes tractable.
Track A holds 1,103 allowlist entries. Change 2 is its prerequisite.

**Scope:** its own epic. It touches `ContextManager`, admin blueprints, and the
scheduler, none of which the error epic goes near.

---

## Track A (not numbered, larger than 2 through 6 combined)

Make `get_db_session` unreachable by name.

282 of about 290 imports are the identical line. Rename the real function
`_open_session`; add `src/core/database/legacy_session.py` re-exporting it, with
a docstring stating that every import of this module is a violation; point the
282 imports at it; delete both Python allowlists.

No call site changes. The allowlist becomes an import graph: new code cannot
reach a session without a visible one-line import of a module named
`legacy_session`. The ratchet becomes `grep -c`, monotonic because that module
has one export.

**Deletes** `test_architecture_repository_pattern.py` (1,478 lines, 833
allowlist) and `test_architecture_no_raw_select.py` (436 lines, 270).

The debt is inherited, not new: 247 of the 270 production entries are the
original author's, 185 land in October 2025, and 113 come from two commits —
"Stage 4: Admin UI SQLAlchemy 2.0 Migration" and "Stage 5". The migration that
converted `session.query()` to `select()` manufactured the debt five months
before a repository pattern existed to place it. 831 of the 833 repository
pattern entries are test files; the production allowlist has been empty since
the pattern landed.

---

## 3. `Mapped[CreativeStatus]`

The column stays `String(50)`. The documented reason at `models.py:698-702`
holds: the spec enum widens, and DDL would make a spec bump a boot-blocking
migration.

The domain type becomes the SDK's `CreativeStatus` StrEnum through a
`TypeDecorator`. `adcp.types.CreativeStatus` has six members and is already
imported — and every use immediately calls `.value` to drop back to `str`
because the column is typed `Mapped[str]`. Writes are bare literals at
`_sync.py:273`, `:372`, and `_assignments.py:317`.

**Closes** half of `salesagent-ka79t`: status stops being an arbitrary string.
The ordering half remains — status is written after the append decision that
reads it, so `Creative.status` must become derived from one approval decision
rather than assigned beside it. Six pieces of state currently say "needs
approval", written by four writers.

**Deletes** `test_architecture_creative_status_vocabulary.py` (241 lines).

---

## 4. Delegate account scrubbing to the SDK

`accounts.py:399-417` `_scrub_business_entity` hand-rolls what
`adcp/types/projections.py:139,182` (`BusinessEntityResponse`,
`to_account_response()`) does structurally. `src/` imports
`adcp.types.projections` zero times.

`accounts.py` is a file this PR grew by 1,440 lines, so this deletes hand-rolled
code from the PR's own diff.

---

## 5. One typed entry point for `capabilities` and `accounts`

`main.py:351-360` registers each tool by handing FastMCP a Python function, and
FastMCP derives the published `inputSchema` from that signature. The request
model is therefore downstream of the wire contract on MCP. One parameter whose
type is the SDK request model inverts that.

Do only the two tools this PR authored. Converting all eleven at once executes
new paths on every tool simultaneously, which is the pattern that produced a
711-file branch.

Then A2A and REST dispatch through the same typed entry point rather than naming
request types. `get_media_buys_raw` exists, is exported, and has zero callers;
the A2A handler imports the model and calls `_impl` directly.

**Deletes** roughly 5 guards, 1,191 lines, 6 allowlist entries, including
`transport_field_parity`, `request_construction_boundary`, and
`rest_body_completeness`. Also removes the shadow local `GetMediaBuysRequest`
(`salesagent-hg1lu`) and 21 of 34 local `*Request` models that `src/` never
constructs.

---

## 6. Extend the mypy per-module opt-in

`mypy.ini` already opts in `src.core.tools.accounts`,
`src.core.tools.capabilities`, and `src.core.helpers.adapter_helpers`. Add the
modules changes 2 through 5 touch. Not global: `.mypy-untyped-defs-baseline` is
212, and `mypy.ini:9-11` states the strictness flags stay off so day-to-day
mypy is unchanged.

Change 1 already extended this file (`mypy.ini` is +8 in the branch). Follow
that precedent per change rather than as a separate step.

---

## Calibration: what change 1 actually cost and returned

Measured 2026-08-21 across the 20 commits from `9585ace68` to `HEAD`.

| Measure | Value |
|---|---|
| Commits | 20 |
| Files touched | 300 |
| Diff | +4,534 / −12,255, **net −7,721** |
| `tests/unit` | net −6,462 |
| `tests/integration` | net −3,050 |
| `src/core` | net +94 |
| Guard files | 143 -> 132 (11 deleted) |
| Epic children | grew from 5 to 9 or more |

Guards deleted: `error_message_provenance` (1,116 lines), `error_code_compliance`,
`error_envelope_two_layer`, `error_recovery_enum_conformance`,
`error_suggestion_enum_conformance`, `no_error_code_kwarg_in_impl`,
`envelope_reconstruction`, `error_code_fixture_pin`, `no_raw_exception_message`,
`suggestion_details_emit`, `suggestion_details_read`.

Unit tests deleted alongside them: `test_error_boundary_translation.py` (1,114),
`test_adcp_exceptions.py` (946), `test_error_format_consistency.py` (882),
`test_error_envelope.py` (420), plus `test_a2a_error_responses.py` (1,092) and
`test_request_validation_suggestion_parity.py` (590) in integration. Every one
asserted on authored error text, which stops being testable at unit level once
the text is derived.

**Two lessons for changes 2 through 6:**

1. The deletion prediction held and then some — 11 guards against 8 predicted.
2. The epic grew from 5 children to 9 or more. A structural change surfaces
   adjacent work at roughly twice the planned step count. Size the remaining
   changes with that multiplier.
