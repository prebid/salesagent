# RCA synthesis: why remediation keeps finding more

Five independent investigations, 2026-08-19, branch `feature/spec-gaps-1210`, pin `adcp==6.6.0` / AdCP 3.1.1.
Sources: `rca-error-path.md`, `rca-dto-boundary.md`, `rca-model-behavior.md`, `rca-guard-blindness.md`,
`rca-layering-slices.md`. Every number below is from an AST scan, a `git log`, or an executed probe.

---

## The finding

**Every primitive this codebase needs already exists, and not one of them is on the only path.**

The correct thing is present — some shipped by the SDK, some written in-house, some documented as *the*
design — and in every case the incorrect alternative still compiles. The architecture is not missing.
It is *optional*.

| The correct thing | Exists at | Used | Bypass that still compiles |
|---|---|---|---|
| `internal_detail=` for third-party text | `exceptions.py:464` | 12 sites | `message: str = ""` accepts any f-string → 25 laundering sites |
| `get_media_buys_raw` transport seam | exported | **0 callers** | A2A imports the model and calls `_impl` directly (`adcp_a2a_server.py:2150-2160`) |
| `select_request_fields` | exists | 4 of ~30 edges | hand-maintained field lists |
| `SyncCreativesRequest` | `creative.py:326` (declared + documented) | **never constructed** | `_sync_creatives_impl` takes 9 loose kwargs |
| `ProtocolEnvelope` | declared, documented as the design | **never used** | per-transport hand assembly |
| `adcp.canonical_formats` (4 fns, one docstring says callers **MUST** use it) | in the wheel | **0 imports** | 3 hand-rolled rules + 2 sites with no rule |
| `adcp.types.projections` (`to_account_response`) | in the wheel | **0 imports** | `_scrub_business_entity` (`accounts.py:399`) |
| 92 canonical `suggestion` strings, `enumMetadata`, 92/92 codes | `adcp/_schemas/3.1/enums/error-code.json` **inside the wheel** | unread | 74 hand-written `suggestion=` args |
| join-or-own UoW shape | `_assignments.py:88-91` (correct exemplar) | 1 site | `BaseUoW.__enter__` has no nesting check → 5 confirmed nested sites |
| `MediaBuyReadinessService` | exists | — | `media_buy_status_scheduler.py:164` re-decides a subset |
| `GAMPricingCompatibility` | correctly designed, correctly placed | — | `orders.py:801-853, :1134-1140` re-decides |
| the `after_commit` / `savepoint` / `is_preview` effect boundary | used correctly throughout `sync_creatives` | slice 1 only | `create_media_buy`: **zero occurrences**, 12 transactions |
| `CreativeStatus` — a closed **`StrEnum`**, 6 members, shipped by the SDK | `adcp.types.CreativeStatus` | read path only | column is `Mapped[str]`, so every use is `.value` (`_validation.py:65`, `_workflow.py:58-63`) and every write is a bare literal (`_sync.py:273, :372`, `_assignments.py:317`) |

The last row is the thesis proven inside one codebase: two slices, same primitives available, one uses them
and one uses none — and every duplication in the second is downstream of that.

**21 of 34 local `*Request` models are never constructed anywhere in `src/`.**

## Why it is optional: a permissive type at every seam

| Seam | The permissive type | Consequence |
|---|---|---|
| error message | `message: str = ""` (`exceptions.py:437`) | the type cannot distinguish seller prose from `googleads` output → a **1116-line guard with a 16-row allowlist** exists to recover a distinction the signature erased |
| request models | `class SalesAgentBaseModel(LibraryAdCPBaseModel)` (`_base.py:292`) | a permissive **root** base: a local `GetMediaBuysRequest` (`_base.py:2797`) is "AdCP-shaped" by construction with **no type link** to `adcp.types.GetMediaBuysRequest`. Only a docstring connected them, and it is now false |
| A2A handlers | `parameters: dict` | a dict where a type belongs; free import of a request model into the transport layer |
| tenant | `dict[str, Any]` | every consumer re-reads keys by string |
| creative approval | `Creative.status` typed `Mapped[str]`, not `Mapped[CreativeStatus]` | the closed SDK `StrEnum` is imported, then immediately `.value`-d back to `str` at every use; writes are bare literals. Any line may set it at any point to any string; **6 pieces of state** say "needs approval", 4 writers |
| BDD step context | `ctx: dict` | raw envelope keys stay reachable beside the guarded reader |
| transactions | `BaseUoW.__enter__` (`uow.py:110-115`) — no check | see below |
| **all of the above** | `mypy.ini`: `check_untyped_defs = False`, `disallow_untyped_defs = False` | **most of `src/` is not type-checked at all.** `.mypy-untyped-defs-baseline = 212` permanently permits the errors that would appear if it were on, and `mypy.ini:9-11` says outright the flag "stays False here so day-to-day mypy is unchanged" |

**The keystone is the last row.** "Make it unwritable with types" currently has no engine behind it.
Every type-level fix below is inert until `check_untyped_defs` is on.

## Why more guards cannot work — measured

- **143 guard files. ~2959 individually-permitted violations**: 1212 across 13 numeric ratchets, 1446
  allowlist rows in 41 files, 301 obligation rows.
- **Net movement across the last 40 commits: −5.** Nine of thirteen ratchets did not move at all.
- `.type-ignore-baseline`: 42 (Feb) → 69 (Jun) → 63 (Aug). **Net +21 over six months** — it has grown.
- `.duplication-baseline` `src` = 35, unchanged for **36 days**. `C901` = 183, flat for 20 days; at its own
  best observed rate, zero in **854 days**.
- **49% of guards (70 of 143) have no mutation self-test.** One (`test_architecture_production_session_add.py`)
  is a docstring with zero test functions and still counts toward the corpus.
- Only **13 of 143** route allowlists through the staleness-checking helper; the other 28 hand-roll a
  set-difference, so fixed violations rot in the list.

And the decisive one, from the four post-mortems:

> Of the four defects, only one involved a *wrong predicate*. The other three involved guards whose
> predicates were **correct and green** — they were not looking at the subject.
> **Mutation-testing all 143 guards would not have found any of the three.**

A guard is what you build when the right way is optional and you want to notice the wrong way. The
ratchet numbers say the noticing has not converted into removal, at any rate, on any horizon.

## Corrected severity: the UoW nesting is not one site

`db4ci` describes one call site. The census found **five confirmed nesting sites**, and a probe against
this venv's actual `scoped_session` proved the consequence:

```
row in outer session after inner close: False
after outer commit, id=1 value: outer-uncommitted     # the post-close mutation was silently lost
```

An inner unit **commits the outer's pending writes, closes the outer's session, and detaches every ORM
object it loaded** — after which mutations to them are dropped with no exception, no log, no `UPDATE`.

The worst is not `db4ci`. It is `media_buy_update.py:462` — `ctx_manager.create_workflow_step(...)` inside
the `MediaBuyUoW` opened at `:390`, and `ContextManager` commits and closes the same scoped session
(`context_manager.py:235,246`). **Every `update_media_buy` request begins by committing and detaching its
own unit of work.** Eight such `ctx_manager` sites in that file, two more in `media_buy_create.py`, two in
`src/admin/blueprints/`. `media_buy_update.py:356` documents the opposite: *"Uses a single MediaBuyUoW for
the entire operation — one session, one transaction."*

`effects.py:91-101` *documents* that nesting happens and makes the effect queue survive it. Nothing makes
the transaction survive it.

## What the numbers say about the models

- 154 schema classes, 104 methods — **55 are serialization**, 21 are Pydantic validators.
- **3 predicates across all 194 model classes.** None on `Creative`, `MediaBuy`, `Package`, `Product`,
  `Format`, `FormatId`.
- 127 comparisons of a model attribute against a literal, scattered across `src/`.

> The models are serialization contracts, not domain objects — so a call site that needs to know something
> about an object has no method to call, and writes a comparison instead.

## What the numbers say about the wire

- **315 places can author a buyer-facing message** (292 `AdCP*Error` constructions with a message arg — 210
  of them non-literal — plus 12 advisory, plus 7 rewriting functions).
- **~65 places can decide a code**, of which ~25 are runtime rewrites.
- Against that: the SDK ships a message default per code, nine code-bound subclasses, a boundary translator
  for both transports, a details sanitizer, an envelope validator with the spec's caps — and 92/92 codes
  with `{recovery, suggestion}`. **We import two names from all of it.**
- `ERROR_CODE_MAPPING` (42 entries) and `INTERNAL_CODES` (16) exist only because ~14 subclasses declare a
  `_default_error_code` that is not a wire code. Under a raise-only design that internal taxonomy is
  `internal_detail`, not `error_code`.
- **26 DTO departure points**, of which **4 have no reason at all** — including three A2A handlers that
  construct a validated request model and then re-read the raw dict anyway.

## The redesign, ordered by findings closed

1. **Turn on `check_untyped_defs`.** Everything else is inert without it. Baseline says 212 errors.
2. **`BaseUoW.__enter__` refuses to nest** (`uow.py:110-115`) — `effects.py` already tracks the scope stack;
   read it. Closes all 5 sites and makes the class unconstructible. Then `_sync_creatives_impl` takes an
   optional `uow`, and `create_media_buy` gets one `MediaBuyUoW`.
3. **A typed wire slot.** `message: SellerAuthoredText`, constructible only from a literal or a first-party
   template, with no path from `BaseException`. Then `AdCPAdapterError(f"...{e}")` is a *type error* and
   `internal_detail=e` is the only thing that compiles. Add `_default_message` + populate `_default_suggestion`
   for all 42 subclasses from the wheel's `enumMetadata`. The 1116-line guard, its allowlist, and three
   tickets become unnecessary.

   **AMENDED 2026-08-21 (owner decision, ADR-010).** This item as written authorised `message` and
   `_default_suggestion` only, and said nothing about `recovery`. That silence made the recovery half of
   every later step formally out-of-boundary, and the `salesagent-3dawm.8` design gate correctly returned
   NARROW on exactly that ground. The scope is now explicit and covers ALL THREE graded wire fields:

   > A graded wire field (`message`, `recovery`, `suggestion`) is a function of the error CODE. A raise
   > site cannot author one. The `recovery=` and `suggestion=` PARAMETERS are deleted, not defaulted —
   > the same move `message` already had. A different retry semantic is a different code, hence a
   > different error class. An advisory `errors[]` entry is constructible only from a typed exception.
   > Specifics travel as `details=` / `field=` / `internal_detail=`, never as authored prose.

   Rationale, consequences and the cost (including that a TEST HOOK is what currently keeps `recovery=`
   alive) are in `docs/decisions/adr-010-graded-wire-fields-are-functions-of-the-code.md`. Execution is
   `salesagent-3dawm.11` (recovery), `.12` (suggestion), `.14` (advisory; subsumes `.13`).
4. **MCP takes the request model as its parameter.** FastMCP derives `inputSchema` from the wrapper
   signature, which is why the request model is currently *downstream* of the wire contract. One parameter
   whose type is the SDK model inverts that. Then delete the `_raw` layer: one typed entry point per tool,
   and A2A/REST cannot name a request type at all. Kills the shadow models and 21 dead declarations.
5. **Delegate to the SDK where it already owns the answer**: `canonical_formats` (5 call sites),
   `projections` (`_scrub_business_entity`), the 92 suggestions.
6. **Give models their predicates** — the ~12 in the "model already holds every input" bucket. `Creative.status`
   becomes *derived* from one approval decision instead of assigned alongside it, which removes the second
   representation rather than guarding the disagreement.

## What did NOT survive scrutiny

The agents were told to disagree, and did:

- **`GAMPricingCompatibility` is correctly placed and correctly designed.** Putting `SPONSORSHIP`/`PRICE_PRIORITY`
  on an AdCP `Package` would leak adapter vocabulary into the protocol layer.
- **`Creative.status` as `String(50)` with no CHECK is deliberate** and documented (`models.py:698-702`): the
  spec enum widens and DDL would make a spec bump a boot-blocking migration. **But the agent's caveat
  conflated two layers and is wrong as stated** — see the `CreativeStatus` row in the optionality table.
  `String(50)` is correct *at the DDL layer*; the domain layer must still be the closed `StrEnum`.
- **The owner's thesis is false as literally stated.** "Use SDK DTOs and nothing else could enter or leave"
  fails on one structural fact: `main.py:351-360` registers each tool as a *Python function*, and FastMCP
  derives the published schema from that signature — so today the request model is downstream of the wire
  contract on MCP. The thesis becomes true after change 4, not before.
- **Two things remain irreducible**: `mcp_compat_middleware.py:67-77` deliberately strips unknown fields in
  production (a *smaller* shape can still get in, on purpose), and A2A's protobuf `Struct` is untyped by
  construction.
- **`C901`/model-validator caveat**: the past-start-time check depends on wall-clock and belongs behind an
  injected clock, not on a model validator.

## The answer to "why did the round-1 remediation not hold"

Round 1 diagnosed correctly and its §6 concluded: *"prose does not bind agents; gates do."* That sentence is
the defect. It accepts that the wrong shape stays writable and invests in noticing it. Every round since has
added noticers — 143 guards, 2959 permitted violations, net −5 in 40 commits. The writable surface never
shrank, so the discovery rate stayed a function of how hard anyone looked.

Not: *make the wrong thing detectable.* Instead: **make the right thing the only thing that compiles.**
