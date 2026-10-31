# RFC: Re-engineer the creative format model on canonical format kinds

**Status:** Request for comment. Implementation is partial; the per-obligation status is
[`docs/audits/creative-rfc-status.html`](../audits/creative-rfc-status.html).
**Scope:** the AdCP version the installed `adcp` SDK reports. This document states no version;
[`docs/adcp-spec-version.md`](../adcp-spec-version.md) says how to read the pinned one.
**Decision proposed:** Author products canonically on `format_options[]`, which the pin
requires. Canonicalize creative format identity internally on `format_kind`. Treat `format_id`
as a retained buyer assertion that business logic never keys on, and project to it outbound only
from a seller-asserted `v1_format_ref`. Call the SDK's projection and advisory machinery rather
than reimplementing it.

**Revision note.** Parts 1 through 5 were written against AdCP 3.1.1 and argued the canonical
direction from the resolution asymmetry. The pin now states that direction outright, so
[Part 10](#part-10-the-canonical-authoring-contract) carries the obligation text and the
authoring contract that Parts 4 and 5 leave out. Part 6 is superseded, and
[Part 11](#part-11-what-grades-each-obligation) names what grades each obligation. Read
Part 10 first if you are implementing.

**Second revision, 2026-10-28.** Parts 10 and 11 were restated after the SDK pin moved to the
released `adcp` 9.3.0 from PyPI. Every obligation in the status audit was re-graded against the
pinned spec, each grade re-derived by a second, independent check, and the result replaces the
measured-state table in Part 10 and the grading surface in Part 11. Two decisions bind the
restatement: `format_kind` is the canonical identifier, and `comply_test_controller` is not part
of this seller, so the `canonical_formats` storyboard, which requires it, grades nothing here.
Parts 1 to 9 are kept as the record of the argument; where they name a version, it is the version
they were written against, not the current pin.

**Third revision, 2026-10-30.** Parts 10 and 11 now describe the design as built on
`integrate/wave1` at `813ee8dff`, graded by box run `innet_301026_1301`: authoring on
`format_options[]` and the data migration (S1), the derived legacy projection and its divergence
advisory (F4, E1), resolution in the registry's order and the reference-origin rule for legacy
ids (L1, L2), `list_creative_formats` projected from product declarations (S5), and the two
legacy ceilings the owner accepted because legacy-only products go obsolete on 2026-11-25 (L4).
The seller derives `format_ids` itself instead of calling the SDK's `project_product_to_v1`, so
the proposal above to call the SDK's projection machinery applies to inbound resolution only.

## Summary

The sales agent implements the creative format model that AdCP replaced in 3.1. It stores
format identity as a name across three columns, resolves that name against a creative-agent
registry, and compares it without canonicalization in four places. It accepts the 3.1+ identity
at the request boundary and implements nothing behind that boundary.

A compatibility shim cannot close this gap, because the two models resolve in opposite
directions. AdCP defines a normative six-step order for projecting a legacy `format_id` onto a
canonical kind, and forbids deriving a `format_id` from a kind. Four canonical kinds carry
`v1_translatable: false` and can never have a legacy form. A subsystem keyed on `format_id`
therefore has no way to represent a spec-legal 3.1+ creative. Reversing which side is canonical
is the only change that gives it one.

**The format model is the stable part of the specification.** Across every published release,
`canonical-format-kind.json` holds the same 13 values from 3.1.1 through 3.1.21, and
`creative-asset.json` holds the same two branches with the same required fields. 3.2.0-rc.1
changes the model additively: three new kinds, identical branches, identical requirements. The
open-enum promotion mechanism behaved as the schema documented. Building on `format_kind` is a
safe bet today, and it is the one representation both directions share.

**The defects sit in the schemas around the model.** For six patch releases,
`list-creatives-response` could not report a creative that `sync_creatives` accepted.
`list-creative-formats-response` still enumerates 16 of 21 asset types. Both are drift between
artifacts rather than flaws in the identity design.

**Most of the breakage belongs to this repository, and it is long-standing.** 82 open issues
here touch creative formats. They are not 82 independent bugs. They are a handful of root
causes emitting symptoms, and the largest cause is that this codebase never adopted the 3.1
model.

## The version position

The pinned spec version is whatever the installed `adcp` SDK reports
(`adcp.get_adcp_spec_version()`), and the canonical kinds and error codes are read from that
SDK's own schema tree. This section records history, not the current pin.

The 3.1.1 pin this RFC was written against trailed the published spec by 20 patch releases, and
the `list_creatives` defect it names was fixed upstream out of reach of that pin. Both are
closed: the pin has since moved past the 3.1 line, which is why Part 6 is superseded.

The format model survived the minor-version move exactly as the schema said it would.
`canonical-format-kind` grew from 13 values to 16 by promotion, `creative-asset` holds the same
two branches, and `product-format-declaration` still requires `format_kind` and `params`. What
3.2 added is the authoring direction, stated normatively, plus the placement and locale rules
Part 10 covers.

[Upgrade adcp SDK from 6.6.0 to 7.0.2](https://github.com/prebid/salesagent/issues/2138)
describes `format_ids` → `format_options` and `measurement` → `delivery_measurement` as
breaking schema renames. The schemas disagree. `core/product.json` carries both `format_ids`
and `format_options` in 3.1.1 and in 3.1.21, marks neither deprecated, and joins them with a
root `anyOf` requiring at least one. It carries `delivery_measurement` as deprecated in both.
The rename applies to the SDK's generated class names rather than to the wire contract. Plan
the bump against the schemas.

## Part 1: What the specification requires

### Identity is an object, and the two identities resolve asymmetrically

`core/format-id.json` requires `agent_url` and `id`, with optional `width`, `height`, and
`duration_ms` that pin a template to a variant. The schema states the rule outright: "A JSON
object — never a plain string… Using a plain string here is a schema violation." Callers
comparing two values MUST canonicalize `agent_url` first.

`core/creative-asset.json` is a root `oneOf` over two branches:

| Branch | Requires | The schema's own title |
|---|---|---|
| 1 | `format_id` | Legacy creative (named-format reference), "remains supported through 4.x" |
| 2 | `format_kind` | 3.1+ creative (canonical format kind), introduced by RFC #3305 |

`format_id` on an upload is therefore the legacy branch of 3.1.1 itself, rather than a
pre-3.1.1 artifact. Refusing it refuses a spec-legal request.

The asymmetry between the branches carries the argument for the proposed direction:

- **Legacy resolves to canonical.** `registries/v1-canonical-mapping.json` defines a six-step
  normative order: the seller's `v1_format_ref`, then an explicit `canonical` field, then a
  registry glob, then a structural match, then an ambiguous family, then fail closed. Step 4
  yields a family, which is what a `format_kind` is.
- **Canonical does not resolve to legacy.** The registry is "authoritative for v1 → v2
  projection only", and SDKs "MUST NOT synthesize a v1 `format_id` from the registry by
  inverting structural matches." All seven registry entries published at 3.1 are
  pure-structural, so nothing inverts even on a best-effort basis. Four kinds
  (`image_carousel`, `sponsored_placement`, `responsive_creative`, and `agent_placement`) carry
  `v1_translatable: false`, and the schema calls that unreachability structural.

One direction resolves. The other is forbidden, and for four kinds impossible. The canonical
kind is therefore the one representation every downstream consumer can share. This asymmetry,
rather than any preference about style, is the technical core of the proposal.

### Why the model changed

The pin names the failure mode of the old model directly, in the AAO-convention paragraph of
`v1_format_ref`:

> Without this convention, every publisher's 300x250 ships with a different `v1_format_ref`
> (theirs vs nytimes.example vs cnn.example vs …) and the v1 wire fragments into per-publisher
> namespaces — exactly what canonical-formats was designed to eliminate.

A `format_id` names something someone owns, so the same IAB MREC carried N identities and a
buyer needed per-vendor catalog integration to learn they were the same thing. `format_kind`
replaces a pointer with a description: a closed set of shapes a buyer reasons about
generically, plus open machine-readable parameters per product. Identity survives the change
with a different scope. `format_option_id` remains stable, namespaced to the product instead of
to an agent's global namespace. Shape is global and closed; identity is local and open.

### Stability of the model

| Artifact | 3.1.1 | 3.1.21 | 3.2.1 (pinned when written) |
|---|---|---|---|
| `canonical-format-kind` values | 13 | 13, identical | 16, adding `audio_vast`, `coordinated_placements`, and `seller_rendered_stateful_display` |
| `creative-asset` branches | `[format_id]`, `[format_kind]` | identical | identical |
| `product-format-declaration` required | `format_kind`, `params` | identical | identical, plus 5 optional properties |

The identity contract held across 20 patch releases and one minor version. The producer-side
enum stays closed, consumers MUST retain unknown values, and new kinds arrive by promotion in a
minor release, which the schema calls "non-breaking by design" and which 3.2.0-rc.1
demonstrates.

### The three-part 3.1+ identity

| Field | Answers | Scope |
|---|---|---|
| `format_kind` | Which canonical shape | Global, closed at 12 kinds plus `custom` |
| `format_option_ref` | Which concrete option of the product realizes it | `{scope: "publisher", publisher_domain, format_option_id}` or `{scope: "product", format_option_id}` |
| `format_id` | Which named format in an agent's namespace | Legacy, global name |

A seller defines a shape outside the enum in two ways: today, as `custom` plus `format_shape`
(a recognized pattern from `core/format-shape-vocabulary.json`) plus `format_schema` (a URI and
digest reference to a fetchable schema); later, by promotion into the enum in a minor release.
The payoff the specification claims for the fetchable schema is that "buyer agents fetch the
schema, validate manifests structurally, and reason about manifests without per-seller
integration code."

### Assets is a slot map by design

Both `core/creative-asset.json` and `core/creative-manifest.json` key `assets` by slot. On the
legacy path each key matches an `asset_id` from the format's `assets` array, such as
`banner_image` or `vast_tag`. On the 3.1+ path each key matches an `asset_group_id` from the
format's `slots` declaration, drawn from the canonical vocabulary registry, such as
`images_landscape`, `video`, `published_post`, or `landing_page_url`. A value is a single asset
object, or an array for slots whose `min` or `max` exceeds 1, such as carousel `cards` or
responsive `headlines`. Each asset carries an `asset_type` discriminator selecting its schema.

### Genuine upstream defects

A bounded list. All are filed, and none touches the identity model.

| Issue | Defect | State |
|---|---|---|
| [list_creatives cannot report a format_kind creative](https://github.com/adcontextprotocol/adcp/issues/7402) | `list-creatives-response` required `format_id` unconditionally, making the `format_kind` branch unroundtrippable | **Fixed in 3.1.7**, invisible at the 3.1.1 pin |
| [list-creative-formats-response asset oneOf omits 5 asset types](https://github.com/adcontextprotocol/adcp/issues/7338) | Triage found a second drift site at `format.json`'s `repeatable_group.assets`, short at 13 of 21 | Open, ready-to-implement, P1 |
| [format_kind typing in the Python SDK](https://github.com/adcontextprotocol/adcp-client-python/issues/1140) | The SDK closes the `format_kind` enum where the specification mandates openness, and admits `custom` without the required `format_shape` and `format_schema` | Open, ready-to-implement |
| SDK, unreported | `adcp.types.CreativeAsset` resolves to `CreativeAsset1`, the legacy branch, at runtime, while mypy resolves the alias to the union | Recorded in code |

All four share one pattern: two artifacts describing the same thing disagree. None invalidates
the identity design, and each makes implementing against it harder.

## Part 2: What this codebase does

### The thirteen gaps

| # | Gap |
|---|---|
| G1 | The boundary admits the `format_kind` branch and the code behind it fails. `CreativeAssetRequest._exactly_one_format_identifier` accepts the branch, then `Creative(**schema_data)` raises `ValidationError` on `format_id: None`. A spec-legal upload clears validation and then fails internally, which is worse than an outright refusal |
| G2 | No resolution machinery exists in either direction. A grep over `src/` finds `format_kind` only inside the validator that admits it and in comments; `v1_format_ref`, `format_options`, `canonical_formats_only`, and `format_option_ref` appear nowhere |
| G3 | `list_creatives` cannot report a `format_kind` creative. Upstream, fixed in 3.1.7 |
| G4 | The ORM has no column for the 3.1+ identity. One wire object, `FormatId {agent_url, id, width, height, duration_ms}`, maps to three columns (`format: String(100)`, `agent_url`, `format_parameters: JSONB`), and none of them holds a kind or an option reference |
| G5 | No creative manifest exists. `core/creative-manifest.json` is the specification's adapter-facing shape, and a hand-rolled dict stands in for it, carrying `{creative_id, package_assignments, width, height, url, click_url, asset_type, name}` |
| G6 | Three non-GAM adapters index `asset["format"]`, a key the live builder never sets, so they raise `KeyError` on contact. GAM, the mature adapter, reads only `creative_id`, `package_assignments`, `snippet`, and `snippet_type` |
| G7 | The SDK closes the `format_kind` enum, and admits `custom` without the fields the specification requires alongside it |
| G8 | `Creative` extends the listing response model and serves three roles: response type, database rehydration, and internal validation vehicle |
| G9 | `assets` is retyped to `dict[str, Any]`, which discards the SDK parent's constrained slot map and its discriminated `asset_type` union. Every coercion branch in the listing code is the bill for that |
| G10 | The request DTO extends the legacy branch by accident, because `adcp.types.CreativeAsset` resolves to `CreativeAsset1` at runtime while mypy resolves it to the union |
| G11 | `Creative.format` is a non-spec alias with three production readers, and it exists only because the database column is named `format` |
| G12 | A request round-trips through a response model so the code can read one field back |
| G13 | The live adapter path supports exactly one impression tracker. The deleted dead converter built a multi-URL list, and its tests were the only record of that capability |

### The 82 open issues, clustered by root cause

82 open issues in this repository touch creative formats. They resolve to seven root causes.

**A. Format identity behaves like a string in a structured model.**
[FormatId does not own its identity](https://github.com/prebid/salesagent/issues/2093) records
four `agent_url` normalizers and no owner.
[MCP idempotency canonicalization crashes on structured format_id](https://github.com/prebid/salesagent/issues/1768)
and [internal CanonicalizationError surfaces as buyer VALIDATION](https://github.com/prebid/salesagent/issues/1679)
are the same defect reaching the buyer.
[list_creatives filter by format_id object](https://github.com/prebid/salesagent/issues/1406)
cannot filter on the object form. The specification mandates canonicalizing `agent_url` before
comparison, and a helper that does so already exists in `src/core/schemas/_base.py`.

**B. The 3.1+ selectors reach the code and the code drops them.**
[3.1+ format selectors accepted then silently ignored](https://github.com/prebid/salesagent/issues/1789)
is G1 on the media-buy path: a package carries three format selectors, the boundary accepts all
three because they arrive inherited from the SDK's `LibraryPackageRequest`, and the code reads
only the legacy one. Together with G1, both write paths, `sync_creatives` and
`create_media_buy`, discard a spec-legal 3.1+ selector without telling the buyer.

**C. `Creative` occupies a request position and a response position at once.**
[Creative occupies both a response and a request position](https://github.com/prebid/salesagent/issues/2121)
records that neither pin restores while it serves both.
[Widened Creative.assets makes the long-video suggestion silently dead](https://github.com/prebid/salesagent/issues/2118)
and [Provenance.verification is the unswept sibling](https://github.com/prebid/salesagent/issues/1910)
are consequences. G8 and G9 state the same cause on the schema side.

**D. Products do not drive format selection.**
[Inventory bundles should drive creative-format selection](https://github.com/prebid/salesagent/issues/1347)
and [Inventory bundles should inherit formats from canonical placements](https://github.com/prebid/salesagent/issues/1348)
describe the intended shape.
[Product edit: format changes silently fail to persist](https://github.com/prebid/salesagent/issues/1238)
and [saving the GAM edit-product form with no edits rebinds format agent_url](https://github.com/prebid/salesagent/issues/1796)
are the write path failing.
[Should format override path silently swallow format-not-found errors?](https://github.com/prebid/salesagent/issues/1091)
asks the resulting question. Under the 3.1 model, a product's `format_options[]` is where
identity resolves. In this codebase the product holds a list of names and resolution happens by
dialling a registry.

**E. The read path cannot report or filter what the write path accepts.**
G3 upstream, plus
[structured CreativeFilters reported but not applied to the query](https://github.com/prebid/salesagent/issues/1502),
[cap concept_ids/statuses/format_ids filter length](https://github.com/prebid/salesagent/issues/1505),
and [harden remaining untyped-blob readers](https://github.com/prebid/salesagent/issues/1779).

**F. Adapters read a shape that nothing produces.**
G5 and G6, plus
[Repeatable Group Assets Not Supported](https://github.com/prebid/salesagent/issues/947) and
[GAM native creatives ignore configured native_style_id](https://github.com/prebid/salesagent/issues/1516).

**G. The format catalog is non-conformant and fragile.**
[Reference creative-format catalog emits pixel_tracker assets the pinned schema does not define](https://github.com/prebid/salesagent/issues/1998)
mirrors upstream #7338.
[Per-format resilient ingestion](https://github.com/prebid/salesagent/issues/1333) records that
one non-conforming format invalidates the whole ingest.
[CI dials the public creative agent](https://github.com/prebid/salesagent/issues/2172) and
[allowlisted silent per-item loop failures](https://github.com/prebid/salesagent/issues/1566)
complete the cluster.

## Part 3: Root causes

Four statements. Each explains a cluster, and patching a call site fixes none of them.

**1. The codebase canonicalizes on the identity that the specification forbids deriving.**
Every downstream consumer keys on `format_id`. Nothing can produce a `format_id` from a
canonical kind, and four kinds never have one, so no correct implementation of the 3.1+ branch
keeps `format_id` as the internal identity. This explains cluster B and G2.

**2. One wire object persists as three columns.** Nothing holds a `format_kind` or a
`format_option_ref`, so even a correctly resolved value has nowhere to live. Every serializer
straddling row and schema pays for this, and naming the column `format` is why the non-spec
`Creative.format` alias exists. This explains cluster A, G4, and G11.

**3. One class serves as request DTO, response model, and persistence shape.** Because it
extends the listing response model, tightening it for one role breaks another. That is why
`assets` widened to `dict[str, Any]`, and that widening is what makes the coercion branches and
the silently dead `isinstance` path necessary. This explains cluster C, G8, G9, and G12.

**4. No adapter-facing contract exists.** The specification publishes one,
`core/creative-manifest.json`. This codebase emits a hand-rolled dict from a single builder,
and three adapters read a key that builder never sets. This explains cluster F, G5, G6, and
G13.

**Why the breakage persists:** each symptom is small enough to patch where it surfaces, so each
one got patched there. A subsystem accumulates 82 open issues that way without any single issue
justifying re-engineering. The work therefore belongs together, as one change rather than an
82-item backlog.

## Part 4: Proposed architecture

### Canonicalize on `format_kind`

`format_kind` becomes the internal identity. `format_id` becomes a retained buyer assertion:
stored verbatim, echoed back on the legacy wire, and never the value that business logic keys
on. The legacy branch survives at the edge, where the code projects an inbound `format_id` onto
a kind through the six-step order.

The resolution asymmetry drives this choice. It also removes `isinstance` branching everywhere
except that edge.

### Resolve at the product declaration

Under the 3.1 model, a product's `format_options[]` declares which canonical kinds the product
accepts and with which parameters. Resolution becomes a lookup against declarations the seller
already published, with no outbound call. The `fetch_format_spec` dial-out belongs to the
legacy branch alone, and its typed failures (429 to `RATE_LIMITED`, 5xx to
`SERVICE_UNAVAILABLE`) stay scoped there.

**This section understates `format_options[]`, and the understatement is the gap this RFC was
reopened to close.** A lookup target for an inbound legacy id is the smaller half of its job.
Under the pin, `format_options[]` is the catalog the seller AUTHORS, and `format_ids` is the
projection a seller may publish beside it. Part 10 carries that obligation and the contract a
declaration has to satisfy.

### A callable discriminator

The stated reason for flattening the `oneOf` is that a failing branch puts a codegen class name
(`CreativeAsset1`) into `issues[].pointer`, where `core/error.json` requires a field the buyer
sent. Pydantic's callable `Discriminator` dissolves that objection, because it selects a branch
by presence and the author chooses the `Tag` names:

```python
def which_branch(v):
    if v.get("format_id") is not None:   return "format_id"
    if v.get("format_kind") is not None: return "format_kind"

Asset = Annotated[Union[Annotated[ByFormatId,   Tag("format_id")],
                        Annotated[ByFormatKind, Tag("format_kind")]],
                  Discriminator(which_branch)]
```

Behaviour against the `RootModel` union it replaces:

| Input | Result |
|---|---|
| Legacy branch | Resolves to `ByFormatId`, same as the union |
| 3.1+ branch | Resolves to `ByFormatKind`, same as the union |
| Missing `name` on the 3.1+ branch | `loc = ('format_kind', 'name')` instead of `('CreativeAsset1', 'name')` |
| Neither identifier | `loc = ()` with `union_tag_not_found`, instead of two branch-shaped errors |

The extra pointer segment becomes a closed two-value set named after real specification fields,
which the pointer builder drops deterministically. A payload carrying neither identifier lands
at the root, which is where a `oneOf` violation belongs.

### One accessor and one identity helper

Downstream code asks the request for its format identity once instead of branching.
`CreativeAssetRequest` has no such accessor, and `Creative` has three ad-hoc ones (`format`,
`format_id_str`, and `format_agent_url`). Build on the existing `format_id_identity()` in
`src/core/schemas/_base.py`, which returns the canonicalized `(agent_url, id)` pair that the
pin mandates for comparison. Adding a second helper beside it recreates cluster A, which exists
because four already compete.

### Split the roles of `Creative`

Three classes take the three jobs: a request DTO built on the discriminated union, a
persistence model, and a response model that keeps the SDK's typed `assets` slot map. The
parent already types it as `dict[Annotated[str, StringConstraints(pattern='^[a-z0-9_]+$')],
AssetVariant | Assets] | None`. Restoring that deletes the coercion branches, which exist only
because the type was discarded.

### Implement the manifest

`core/creative-manifest.json` becomes the adapter-facing contract and replaces the hand-rolled
dict. Adapters consume a typed manifest carrying `assets` plus whichever identity applies.

## Part 5: Work required

### Data model and migration

| Change | Note |
|---|---|
| Add `format_kind` and `format_option_ref` to `creatives` | Nothing holds the 3.1+ identity (G4) |
| Decide between additional columns and one structured identity column replacing `format`, `agent_url`, and `format_parameters` | Open question, see Part 8 |
| Backfill existing rows by projecting stored `format_id` values through the six-step order | Rows that fail to project are the interesting ones. Fail closed and report them rather than guessing |
| Retire the `format` column name | It is the sole reason the non-spec `Creative.format` alias exists (G11) |
| Persist a product's `format_options[]` as declarations rather than as a list of names | Cluster D |

Migrations follow the repository rules: name the schema explicitly in foreign keys, supply a
non-empty `upgrade()` and `downgrade()`, and never modify a migration after commit.

### Tools

- `sync_creatives` accepts both branches, projects the legacy one, and persists the canonical
  kind (G1).
- `create_media_buy` and `update_media_buy` read `format_option_refs` and `format_kind` with
  `params` instead of discarding them
  ([#1789](https://github.com/prebid/salesagent/issues/1789)).
- `list_creatives` reports the canonical identity and applies structured filters to the query
  (G3, [#1502](https://github.com/prebid/salesagent/issues/1502)).
- `list_creative_formats` emits only the asset types the pinned schema admits
  ([#1998](https://github.com/prebid/salesagent/issues/1998)).

### Adapters

GAM is the mature adapter, and it reads only `creative_id`, `package_assignments`, `snippet`,
and `snippet_type`. It never touches format, so the manifest change reaches it cleanly.

Kevel, Triton, and mock index a key the live builder never sets (G6), so they already break on
contact. The choice is between fixing three adapters against a contract that does not exist yet
and declaring them unsupported until the manifest lands. Declaring them unsupported needs a
carve-out for mock, because the whole test harness and every BDD scenario run on mock.

### Error taxonomy

Nothing needs wiring. `src/core/errors/codes.py` builds `CODE_TABLE` by loading the published codes from
`enums/error-code.json` inside the installed wheel and unioning this seller's own
`AppErrorCode` values. It is derived rather than transcribed, so a published code is present by
construction. On the pin, 92 published codes plus 8 platform codes give a table of
100, `FORMAT_NOT_SUPPORTED` is in it with `recovery: correctable`, and all 92 published codes
carry `enumMetadata`, so no `recovery` value needs choosing. `FORMAT_NOT_SUPPORTED` even
carries an authored suggestion naming the migration window.

The corollary matters for triage. A spec code missing from `CODE_TABLE` is a gap in the SDK's
schema bundle and belongs upstream, never a task in this repository.

The real defect in this area is the opposite one.
[Map FORMAT_NOT_FOUND to REFERENCE_NOT_FOUND](https://github.com/prebid/salesagent/issues/1847)
is a conformance bug rather than a mapping preference: `FORMAT_NOT_FOUND` is absent from the
pinned enum entirely, so raising it emits a code the specification does not publish.
`REFERENCE_NOT_FOUND` is published, carries `recovery: correctable`, and its suggestion covers
the case. Related:
[creative-sync discards the validation error type](https://github.com/prebid/salesagent/issues/1977)
and
[sync_creatives reports an agent_url failure as CONFIGURATION_ERROR](https://github.com/prebid/salesagent/issues/1790).

## Part 6: Superseded — the SDK migration landed

This part asked whether to migrate off `adcp==6.6.0` separately or inside this feature, and
recommended folding the schema half in while shipping the transport fix separately. The question
is closed: the pin moved past the 3.1 line, past both the 3.1.7 `list_creatives` fix and the
7.x shape changes this part planned for. The reasoning is kept below because it records
why the two concerns were split, which is still the right split to describe even though the
timing question it served has expired.

What the move changes for the rest of this RFC, beyond the version table:

- **The six-step resolver is no longer the seller's to build.** `adcp.canonical_formats` ships
  it as `project_v1_format_to_declaration`, which walks the documented resolution order, plus
  `project_product_to_v1` for the outbound direction and `check_narrows` for the narrowing
  check. Part 7's sequencing question assumed the seller would write that resolver, and that
  assumption is what made the question hard.
- **Producer-side `custom` validation has an answer.** Open question 5 asked whether it belongs
  at the seller's boundary or in the SDK. The SDK ships `validate_format_kind_in_options` and
  `FormatKindNotInClosedSetError`, so it is the SDK's, and the seller's job is to call it.
- **The advisory channel exists.** `make_sdk_advisory` builds the `errors[]` entry the
  projection rules mandate, setting `source=sdk` and `sdk_id` itself.

The original reasoning follows.

Coupling unrelated work carries a cost, worth paying here for one reason: the schema half of the bump edits the same models this feature rewrites. `Creative`,
`CreativeAsset`, and `PackageRequest` all change shape under 7.x, and all three are the classes
Part 4 splits and re-types. Migrating them first means porting the old creative model onto 7.x
and then discarding that port. That is the patching a separate migration produces, and while
patching does keep each step small, here it means paying twice for the same files.

The bump carries two independent concerns, and the proposal splits by concern rather than by
timing:

| Concern | What it covers | Proposal |
|---|---|---|
| Transport | mcp 2.x streamable HTTP replacing SSE-first, and `protocolBinding: "JSONRPC"` on the A2A agent card | Ship separately and immediately. It has nothing to do with creatives, and any buyer on adcp 7.x cannot reach this agent at all today, which the client sees as "SSE stream ended without a response" |
| Schemas and models | The generated model changes, including the `format_kind` enum closure | Fold into this feature |

`7.0.2` is the stable SDK and carries spec 3.1.15, which contains the 3.1.7 fix. The `8.0.0bN`
line carries 3.2 and stays out of scope while it is beta. Reaching 3.1.21 exactly is not
required; reaching 3.1.7 or later is.

## Part 7: Sequencing

One decision settles before the work starts, because it determines the first lane either way:
what happens to the legacy branch while somebody builds the resolver.

| Option | First lane | Cost |
|---|---|---|
| **A** | Flip the identity, accept both branches, and build the six-step resolver immediately | The resolver up front, including catalog reads. Its refusal is the specification's own fail-closed step rather than an invention |
| **B** | Flip the identity, accept only `format_kind`, and refuse the legacy branch with `FORMAT_NOT_SUPPORTED` until the resolver lands | One refusal, built once, pointing where the design goes. It refuses spec-legal traffic explicitly and visibly |

Taking B first and A as a later lane avoids building a refusal and then deleting it. It also
refuses spec-legal 3.1.1 traffic for the duration, which makes it a product decision rather
than an engineering one. **A and B are the first item in Part 8.**

Refusing `format_kind`, which is the behaviour today, is correct only while `format_id` holds
the identity. The refusal flips when the identity flips, so doing both in the stated order
produces throwaway work.

### The A-or-B question is closed, and the answer is neither

Both options priced the seller building the six-step resolver. It does not: the SDK ships it
(Part 6). That removes B's whole justification, which was to defer resolver work behind one
refusal.

B is also refused by the pin independently. `core/product.json` requires that "Products MUST
carry `format_ids`, `format_options`, or both during the 3.x migration window", and
`format_ids` is deprecated in 3.2 and removed in 4.0 rather than removed now. A seller that
refuses the legacy branch with `FORMAT_NOT_SUPPORTED` refuses conformant traffic for the whole
3.x window, so B is not available at this pin regardless of sequencing preference.

The first lane is therefore: author canonically, accept both branches inbound, and call the
SDK's projection. Part 10 states what that lane has to produce.

## Part 8: Open questions

Five of the seven below are settled by the current pin or by the SDK that ships with it. They are
kept with their answers, because a reader who finds only the open ones cannot tell whether the
rest were decided or forgotten.

1. ~~**A or B in Part 7.**~~ **Closed: neither.** The SDK ships the resolver, and the pin
   requires the legacy branch to keep working through the 3.x window. See Part 7.
2. **Columns or one structured identity column.** Does `creatives` gain `format_kind` and
   `format_option_ref` alongside the three columns it has, or does one structured column
   replace all of them? **Still open**, and Part 10 adds a constraint on the answer: whichever
   shape wins, the comparable identity it yields must be the kind rather than the legacy pair.
3. **Non-GAM adapters.** Ratify or overrule declaring Kevel and Triton unsupported, with the
   mock carve-out.
4. ~~**SDK coupling.**~~ **Closed.** The pin moved to the 9.x SDK line and the transport concern
   shipped separately, which is the split this part recommended.
5. ~~**Where producer-side `custom` validation belongs.**~~ **Closed: the SDK.** It ships
   `validate_format_kind_in_options`, raising `FormatKindNotInClosedSetError` when a kind a
   buyer's manifest targets is absent from the product's published `format_options[]`. The
   seller calls it; it does not write its own closed-set check.
6. ~~**`list_creatives` reporting for the four unreachable kinds.**~~ **Closed, and the
   synthetic-identifier option is refused.** `registries/v1-canonical-mapping.json` states that
   SDKs "MUST NOT synthesize a plausible-but-arbitrary v1 `format_id`" and must instead surface
   `FORMAT_DECLARATION_V1_AMBIGUOUS` through `errors[]`. A canonical carrying
   `v1_translatable: false` "never has any v1 form regardless of registry coverage", so there is
   nothing to mint. The reporting path is the advisory, not an invented id.
7. ~~**3.2 timing.**~~ **Closed: the pin has moved past the 3.1 line.** The format model did survive the move
   additively, as this part predicted. The product-surface restructure it worried about is
   separate work and stays a non-goal.
8. **How a seller authors `v1_format_ref`.** New, and the one genuinely open design question
   Part 10 raises. The pin makes `v1_format_ref` the only sanctioned v2 to v1 link and makes it
   seller-asserted, so a seller that wants legacy reach has to record the pairing somewhere an
   operator can edit. Nothing in the product model holds it today.
9. **Whether to publish `placements[]`.** The pin ties the answer to a hard constraint: a
   product spanning multiple publishers that omits `placements[]` must narrow its
   `format_options[]` to the common intersection across every selected publisher scope. A
   seller wanting the union MUST publish `placements[]` with publisher-scoped identities. This
   is a product decision with a schema consequence, not an engineering one.

## Part 9: Non-goals

- **Fixing the specification.** Upstream defects are filed and tracked, and this work consumes
  their outcomes.
- **The 3.2 product-surface migration.** Separate work, gated on 3.2 shipping.
- **Generative and build paths.**
  [Generative refinement round-trip is unwired](https://github.com/prebid/salesagent/issues/2143),
  [A2A CreativeAsset construction loses generative-build data](https://github.com/prebid/salesagent/issues/2011),
  and [generative build and update paths have drifted apart](https://github.com/prebid/salesagent/issues/1746)
  touch creatives without touching format identity.
- **A backlog sweep.** The 82 issues are evidence for the root causes rather than a work list.
  Most close as a consequence of the four fixes in Part 3. Re-triage the rest afterwards.

## Part 10: The canonical authoring contract

Parts 1 through 5 argue that the canonical kind is the representation every consumer can share,
and they argue it from the resolution asymmetry. The pin states that direction outright. This
part quotes the obligation, then describes how the seller meets it: how products are authored,
how the deprecated `format_ids` arm is derived, how a legacy reference is resolved, what
`list_creative_formats` lists, and which legacy cases are accepted ceilings. Part 11 says what
grades each rule.

### The obligation, quoted

`core/product.json`, `properties.format_ids.description`, at the pin:

> Deprecated in AdCP 3.2; removed in AdCP 4.0. Legacy named-format compatibility path. Products
> MUST carry `format_ids`, `format_options`, or both during the 3.x migration window. New
> products MUST author canonical `format_options[]`; sellers MAY additionally project those
> declarations to `format_ids` for legacy buyers. When both fields are present they MUST
> describe the same underlying formats, and buyers MUST prefer `format_options`. Do not author a
> new product from `format_ids` alone.

`properties.format_options.description`, same file:

> New 3.2 products MUST carry format_options; a seller MAY additionally project the same
> declarations to deprecated format_ids for older 3.x peers.

And the root `anyOf` names the two branches, with `deprecated: true` on exactly one:

| Branch | Title | Requires | Deprecated |
|---|---|---|---|
| `anyOf[0]` | Named-format product | `format_ids` | Yes |
| `anyOf[1]` | Canonical-format product | `format_options` | No |

`docs/creative/canonical-formats.mdx` § Deprecated named-format compatibility projection
(normative) adds the producer rules: "Canonical `format_options` are the source of truth.
Compatibility `format_ids` MUST be derived from them rather than maintained as a second authored
catalog", and "A producer that cannot guarantee agreement MUST omit the legacy compatibility
projection."

### What a declaration has to say

`core/product-format-declaration.json` requires `format_kind` and `params`, and offers 15
properties. Five carry obligations an authoring path has to honour:

| Property | Obligation |
|---|---|
| `format_kind` | Required. One of the 16 canonical kinds, or `custom`. |
| `params` | Required. The narrowing of that kind this product accepts. |
| `format_option_id` | "REQUIRED when a product contains multiple declarations with the same format_kind and SHOULD be set on every entry." Buyers "MUST NOT fall back to deprecated format_ids merely because this optional ID is absent." |
| `publisher_domain` | Pairs with `format_option_id` for a publisher-backed option; omitted for a product-local one. |
| `v1_format_ref` | The authoritative v2 to v1 link, an array of `format_id` values. "The v2 declaration's `params` MUST narrow (be compatible with) each referenced v1 format's `requirements`." |

`locale_policy` carries a coupled constraint rather than a standalone one: a declaration
carrying it "MUST set `canonical_formats_only` to true and MUST NOT carry `v1_format_ref`",
because "legacy format_ids cannot preserve this constraint". A seller that wants locale
eligibility therefore gives up legacy reach for that option, by construction and not by choice.

### Every authoring path writes the canonical arm (S1)

The seller authors products on `format_options[]` only:

- The admin product create and edit forms author declarations. Saving declarations on an
  existing row clears its stored `format_ids`. A product linked to an inventory profile that has
  a canonical arm doesn't need declarations of its own, because the profile supplies them.
- Inventory profiles carry their own `format_options` column, and the admin profile forms author
  it. Their `format_ids` column is nullable.
- Every seed path authors declarations that assert a `v1_format_ref`: `CREATE_SAMPLE_DATA`,
  `scripts/setup/seed_products.py`, `scripts/setup/init_database.py` and
  `scripts/setup/storyboard_signing.py`. The mock adapter's product-config route writes no
  `format_ids`.

Existing rows are moved by a data migration, not by code that reads the legacy arm at request
time. `alembic/versions/ddb20e636cee_move_legacy_format_ids_into_format_options.py` projects
each legacy id with `adcp.canonical_formats.project_legacy_format_id` and moves profiles before
the products that link them. It fails closed per row: if any id on a row can't be projected
(for example, a custom agent's format), the row stays on its legacy arm and the migration log
names it. A product linked to a profile that stays legacy-only also stays where it is.

### The legacy projection is derived from the declarations (F4, E1)

`Product.format_contract` (`src/core/database/models.py`) is the one place that answers what a
product's format contract is. It returns one of two types from `src/core/format_resolver.py`:

| Type | When | `format_ids` |
|---|---|---|
| `CanonicalFormats` | The product, or the profile it links, has declarations | Computed from the declarations' `v1_format_ref` entries, deduplicated on the canonical identity, in declared order. The stored `format_ids` column is never read. |
| `NamedFormats` | No declarations anywhere | The authored legacy arm, served as stored. This is the deprecated "Named-format product" branch, which the 3.x window still admits for a row the migration couldn't move. |

`linked_format_contract` decides between the product and its profile: the profile supplies the
contract, except that a legacy-only profile yields to a product that has declarations, because
the pin makes `format_options` the source of truth.

Three rules govern the derivation:

1. **Nothing is synthesized.** `registries/v1-canonical-mapping.json` makes the registry
   "authoritative for v1 → v2 projection only", and the v2 to v1 direction "MUST rely on
   `v1_format_ref`". So a `format_id` is never computed from a `format_kind` or a structural
   match. A product whose declarations assert no `v1_format_ref` is served with no `format_ids`
   key at all, which declines the pin's MAY for that product.
2. **A reference the declaration doesn't narrow is omitted and reported.** Before a
   `v1_format_ref` reaches `format_ids`, `format_satisfaction.declaration_narrows` checks that the
   declaration's `params` narrow the referenced format. It is the same `_narrows` rule the legacy
   selector route applies, so a reference the product would not project is one the route would
   not select. A reference that fails is left out ("A producer that cannot guarantee agreement
   MUST omit the legacy compatibility projection"). `format_declaration_divergence` reports it as
   a `FORMAT_DECLARATION_DIVERGENT` advisory on `errors[]` of the populated `get_products`
   success, with `source: "producer"`, `field: "products[i]"` and the details the enum
   description names (`product_id`, `format_ids`, `format_options_summary`,
   `divergence_reason`). Every advisory for that code is built in one place in
   `src/core/tools/products.py`.
3. **The projection goes to every peer.** canonical-formats.mdx says a producer "MAY derive
   deprecated `format_ids` from those declarations only for a negotiated older-3.x
   compatibility response". The owner decided on 2026-10-28 to emit the derived `format_ids` to
   all peers without gating on the negotiated version. That deliberately goes beyond the MAY,
   and is recorded here so it isn't read as an oversight.

Every reader that needs a product's formats reads `Product.format_contract`, never the stored
columns: the `get_products` converter, the manual-approval path of `create_media_buy`, the GAM
sizing readers (`src/services/gam_inventory_service.py`, `src/services/dynamic_pricing_service.py`)
and `list_creative_formats`. The mock, Kevel, Triton and Broadstreet adapters read neither arm.

### Which advisories belong to the SDK

`registries/v1-canonical-mapping.json` assigns three advisory codes to the SDK. Each is in the
pin's error-code enum, so `src/core/errors/codes.py` already carries them.

| Code | Raised when | Actor, per the registry text |
|---|---|---|
| `FORMAT_DECLARATION_DIVERGENT` | The narrowing check between a declaration's `params` and its referenced v1 `requirements` conflicts | "SDKs SHOULD run the *narrows* check … surface `FORMAT_DECLARATION_DIVERGENT` on the `get_products` response `errors[]`" |
| `FORMAT_DECLARATION_V1_AMBIGUOUS` | A v2 declaration carries no `v1_format_ref` and a v1-only buyer queries the product | "SDKs MUST surface … via `errors[]` augmentation rather than synthesize a plausible-but-arbitrary v1 `format_id`" |
| `FORMAT_PROJECTION_FAILED` | Resolution fails closed | "SDKs MUST augment the response's `errors[]` array with an entry carrying `source: "sdk"`, `sdk_id: "<package>@<version>"` … `field: "products[N].format_ids[K]"`" |

The seller is the producer. It emits `FORMAT_DECLARATION_DIVERGENT` itself, as a
self-detecting producer, which the enum description permits ("a SELLER may self-detect on
emit"). It never needs `FORMAT_DECLARATION_V1_AMBIGUOUS`, because it doesn't project a
declaration that has no `v1_format_ref`. It never needs `FORMAT_PROJECTION_FAILED` either: when
an inbound legacy reference can't be resolved, the seller refuses it (see the next section).

The SDK's side of the narrowing SHOULD is not met by the pinned SDK. Its projection helpers
don't run the check on a seller-asserted `v1_format_ref`, and `check_narrows` misses three of
the pinned `narrowing_vectors` (pixel density, range containment, exact value). This is filed as
[adcp-client-python#1749](https://github.com/adcontextprotocol/adcp-client-python/issues/1749).
That gap belongs to the consumer side. The seller's own derivation runs its own check, so the
gap doesn't affect what this seller publishes.

From `adcp.canonical_formats`, the seller calls the following:

| Helper | Where |
|---|---|
| `project_v1_format_to_declaration` | `canonical_kind_for`, for steps 2 to 6 of the resolution order |
| `upgrade_legacy_format_id` | Before the order runs, once, so a member id such as `display_300x250_image` reaches its template |
| `project_legacy_format_id` | The data migration, and the package route in `format_satisfaction.py` |
| `format_is_supported` | Matching a legacy-only product (the first L4 ceiling below) |
| `formats_are_equivalent` | Canonical equivalence between two declarations |
| `FormatKindNotInClosedSetError` | The per-product closed set (V2). `validate_format_kind_in_options` itself is not called because of a typing mismatch (salesagent-ol033.17); the membership test is two lines with the same behaviour. |

It doesn't call `project_product_to_v1`. That helper is additive and doesn't omit a reference
that fails to narrow, so the seller derives `format_ids` itself.

### A legacy reference resolves in the registry's order, at the reference agent's origin (L1, L2)

Before two legacy references are compared, each `agent_url` is canonicalized under the AdCP URL
canonicalization rules, as `core/format-id.json` requires ("Callers comparing two `format-id`
values MUST canonicalize `agent_url`"). `CreativeAgentRegistry.get_format` matches the
canonical `(agent_url, id)` pair. `registry_addressed` and `upgraded_reference` address a
reference canonically before it is projected.

A legacy `format_id` then resolves to a canonical kind in the order
`registries/v1-canonical-mapping.json` makes normative:

1. **`v1_format_ref`.** On the package route, `_legacy_route` selects the declaration whose
   `v1_format_ref` names the reference.
2. **Steps 2 to 6.** `canonical_kind_for` hands the SDK's `project_v1_format_to_declaration` the
   upgraded id, together with the declaration the agent serves when there is one. That walks
   the `canonical` annotation, then the registry glob, then the structural match, then the
   ambiguous family, and fails closed.
3. **Fail closed.** An unprojectable reference is refused with `UNSUPPORTED_FEATURE`, as
   `media-buy/package-request.json` requires, on the package route and on the creative write
   (`creative_identity_of` raises `AdCPCapabilityNotSupportedError`). Nothing is stored.

**The registry maps an id only at the reference agent's origin.** The registry's
`format_id_glob` entries and the SDK's size upgrade use the id namespace of the AdCP reference
creative agent, `https://creative.adcontextprotocol.org`. The SDK matches a glob on the id
alone, without reading `agent_url`. The pin points the same way: sellers SHOULD point each
`v1_format_ref[].agent_url` for an IAB-standard format at that agent and use "the
registry-published id". A seller-bespoke format's id is "seller-namespaced", and such formats
"won't appear in `v1-canonical-mapping.json`'s registry; they're seller-asserted only"
(`core/product-format-declaration.json`, `v1_format_ref`). So:

- A legacy id maps through the registry only when its `agent_url` names the reference agent.
  `names_reference_agent` compares by origin, so the path the agent is mounted at doesn't make
  it another agent.
- Another agent's id belongs to that agent. It maps only through the `canonical` annotation on
  the declaration that agent serves. A foreign declaration that carries no `canonical` doesn't
  fall through to the registry glob or the structural match. The reference is unprojectable
  and is refused `UNSUPPORTED_FEATURE` (owner decision, 2026-10-30).
- An `agent_url` that canonicalization refuses names no agent, so it has no projection.
- `declaration_narrows` reads a `v1_format_ref` with no projection as stating no constraint.

The operator's own creative agent is one case to note. Its catalogue is answered by the agent
that stands in for it (`CREATIVE_AGENT_URL`, or the checked-in reference capture in a test
deployment), so an operator lookup also accepts that agent's entries, compared by origin.
`CreativeAgentRegistry.lists_format` is the one rule for "is this catalogue entry the format
that reference names", and both `get_format` and `list_creative_formats` use it.

Once projected, legacy references are compared on the canonical shape, never on the raw key
(`core/product-format-declaration.json`, Format matching vs satisfaction: "do not exact-match
raw `(agent_url, id)` pairs once a `format_id` has been projected"). `canonical_projection`
yields the kind plus `width`, `height` and `duration_ms_exact`. The `get_products` and
`list_creatives` `format_ids` filters match on that shape. `sync_creatives` and
`create_media_buy` accept a reference the registry projects, whether or not the agent's
deprecated catalogue lists the id (`registry_projects`), because template-format-ids.mdx says
not to "treat a deprecated `list_creative_formats` response as a source of truth".

A canonical kind is never turned back into a legacy id. `format_ids_to_provide` on a package
snapshot is read from `v1_format_ref` only, and a creative's legacy echo is the buyer's own
assertion, returned verbatim (L3).

### `list_creative_formats` is a projection of product declarations (S5)

The pin deprecates the sales-agent task ("sales-agent `list_creative_formats` is deprecated in
3.2") and allows a compatibility response only as a derivation: "Compatibility
`list_creative_formats` responses MAY be projected from canonical declarations for older 3.x
peers, but MUST NOT be independently authored" (`docs/media-buy/specification.mdx` § Canonical
creative-format discovery).

The seller serves it that way:

- `_declared_formats` (`src/core/tools/creative_formats.py`) collects the named formats the
  tenant's products declare. It reads each product's `Product.format_contract.format_ids`, so
  the set is exactly what the same products carry on `get_products`.
- A format is listed only when a product declares it, with the definition its creative agent,
  or the in-process adapter, publishes. A format an agent publishes that no product declares is
  left off the list. A declared reference that no agent defines is omitted, not invented.
- The MCP tool and the A2A skill advertise the pinned request schema's description, which marks
  the task deprecated, instead of the SDK's "Returns format_ids needed for sync_creatives"
  (`ToolSpec.advertised_description` in `src/core/tools/registry.py`). Nothing declares the task
  required. The buy path enforces `format_options[]`, never this list.

Known ceiling: the list reads every product of the tenant, with no principal or account
scoping, so a format that only a hidden product declares is listed to every caller. The pin lets
a seller with one shared list ignore the request's `account`. The upgrade path is the principal
filter that `get_products` applies, read from the same rows.

### Accepted ceilings: legacy-only products and pixel density (L4)

Two legacy cases are not matched on the full canonical shape:

1. **A legacy-only product** (`NamedFormats`) is matched through the SDK's
   `format_is_supported`, which upgrades only `display_WxH` ids. A `video_30s` selector or
   filter therefore doesn't match a product that lists `video_pre_roll_30s`.
2. **Density.** An id-only projection of the reference agent's `display_*_image_2x` carries no
   `pixel_ratios`, because `_PROJECTED_SHAPE_PARAMS` compares only `width`, `height` and
   `duration_ms_exact`. The deprecated `format_ids` filters treat it as the 1x id, and a `_2x`
   selector narrows to a 1x 300x250 declaration. The pin says "An omitted legacy ratio is 1x",
   so that selector should be refused. The ceiling and its upgrade path are recorded at
   `_PROJECTED_SHAPE_PARAMS`.

The owner accepted both on 2026-10-30 as documented ceilings, with no further work, because
legacy-only products and v1 ids go obsolete on 2026-11-25. Fixing either case would need the
served declaration's projection at those readers.

### Placements layer, and an unresolved reference fails closed

`format_options.description` carries the layering rule. When placements are published,
product-level `format_options` are "the union of formats deliverable somewhere in the product
and the upper bound for every placement", and a placement's effective accepted set is the
intersection of three layers:

1. The product's `format_options`.
2. The product placement's inline `format_options`, when present.
3. For kind `publisher_ref`, the named publisher's `adagents.json` catalog narrowing.

Two rules govern the edges, and both are refusals rather than defaults. "An omitted optional
layer is unconstrained, but an unresolved publisher placement or format-option reference MUST
fail closed." And "A product- or placement-level declaration MUST NOT introduce a format outside
the product upper bound." A publisher-referenced placement without inline product
`format_options` therefore "does NOT inherit the full product union" when the publisher catalog
narrows further.

Matching across layers is specified rather than left to equality:

- Publisher-declared options match by `{publisher_domain, format_option_id}`.
- Product-local options match by `format_option_id` when `publisher_domain` is omitted.
- Otherwise, declarations match when they share a `format_kind` and the narrower `params`
  satisfy the broader declaration.

That third rule is the satisfaction relation, and `src/core/format_satisfaction.py` implements
it.

Which constraints a claim has to STATE is named by the pin rather than left to the shape of the
constraint. `docs/creative/canonical-formats.mdx` § Format matching vs product satisfaction:
"When the product declares a fixed `width`, `height`, `duration_ms_exact`, or
`duration_ms_range`, the requested creative or package selector MUST declare and match that
constraint." Every other constraint a claim omits is inherited — the same document's normative
"Narrows" definition: "when v2.params omits a parameter that v1 specified, v2 inherits v1's
value (no narrowing constraint added)" — and a constraint the claim does state must still be
met. The `canonical_formats` storyboard depends on the split: its dual-emission positive sends a
direct route of `{width, height}` against a declaration that also fixes `max_file_size_kb` and
`image_formats`, and grades it accepted. `src/core/format_satisfaction.py` keys silence on that
named set (`_MUST_BE_STATED`), and `BR-UC-FORMAT-SATISFACTION.feature` grades both halves.

Locale policy follows the same intersection, under RFC 4647 Basic Filtering: with no product
policy a placement may introduce any concrete policy as a narrowing, and with both present
"every placement range must be contained by a product range".

### Creative coverage across placements (F3)

One more rule, which bears on assignment rather than on discovery: for a product or package with
multiple included placements, a single creative meant for all of them "MUST lie in the
intersection of every selected placement's effective set". Per-placement creatives MAY use the
union, "but the selected creative set MUST cover every included placement; uncovered inventory
MUST be rejected or refined, never silently omitted".

The seller rejects and never refines, which the pin allows. Package selectors and the assigned
creative set are checked against every included placement on `create_media_buy`,
`update_media_buy` and `sync_creatives` assignment. A multi-publisher product that publishes no
`placements[]` is checked per publisher slice (`format_satisfaction.publisher_slice_layers`), so
a package or creative set that leaves a slice uncovered is refused.

`core/product.json` also binds the seller as the product's author: such a product's
`format_options` "MUST therefore be the common intersection accepted across every selected
publisher/property scope". The admin product and inventory-profile saves don't refuse a
publisher-scoped option on that product yet (salesagent-ol033.20.8.2). The owner decided on
2026-10-30 that the save is refused and existing rows get a data migration.

### Where `create_media_buy` and `update_media_buy` disagree

Creatives reach a media buy through two functions that were written separately and still
disagree in places. One known case: `update_media_buy` saves inline creatives to the library
and assigns them, but doesn't upload them to the ad server, while `create_media_buy` does. The
rule for every such divergence is the same. It isn't patched in one of the two functions.
Instead, a BDD scenario states the shared behaviour and is marked xfail against
[prebid/salesagent#2483](https://github.com/prebid/salesagent/issues/2483), the refactor that
moves both tools onto shared services.

## Part 11: What grades each obligation

Grading is visible system behaviour through the BDD layer, not guards and not type
introspection. An obligation counts as graded only when a scenario under `tests/bdd/features`
asserts it as the pinned spec states it, its steps assert values on the wire, and it passes on
all four transports: a2a, mcp, rest and e2e_rest.

**The `canonical_formats` storyboard grades nothing on this seller.** At the pin it declares
`requires: [controller]`, and `comply_test_controller` is not part of this seller, so the runner
skips it. Every claim elsewhere in the tree that an obligation is "graded by the
`canonical_formats` storyboard" means, here, graded by the BDD scenario that mirrors the
storyboard step, or not graded.

The table describes `integrate/wave1` at `813ee8dff`. Each status is the per-obligation grade
recorded in `docs/audits/creative-rfc-status.html`. The transport column reads the per-nodeid
outcomes of box run `innet_301026_1301`, which ran that commit on a clean tree against the pin
its `meta.json` records. In that run, every scenario the beads below name passed on a2a, mcp,
rest and e2e_rest. The two values mean:

- "yes": every clause the seller implements is asserted by such a scenario.
- "partly": a clause the seller implements has no scenario, or a clause isn't built. The gap
  list after the table names which.

Each bead carries the obligation's pin text, code evidence, scenarios and gaps.

| # | Obligation | Status | Graded on all four transports | Bead |
|---|---|---|---|---|
| S1 | Move each product's creative contract into format_options[] | implemented | partly | `salesagent-ol033.20.1` |
| S2 | A canonical-only product is servable: the product root anyOf admits format_options without format_ids | implemented | yes | `salesagent-ol033.20.2` |
| S3 | Publish reusable publisher options in /.well-known/adagents.json and reference them by {publisher_domain, format_option_id} | implemented | yes | `salesagent-ol033.20.3` |
| S4 | Return the applicable options from get_products | implemented | partly | `salesagent-ol033.20.4` |
| S5 | list_creative_formats is a deprecated compatibility surface, projected from product declarations and never authored | implemented | yes | `salesagent-ol033.20.5` |
| F1 | Canonical compatibility is directional and exact: exact dimensions/durations match; a requested range must be contained, overlap insufficient; required slots present with compatible asset types; an exact publisher claim matches both halves | implemented | yes | `salesagent-ol033.20.6` |
| F2 | Equal format_kind alone does not prove compatibility | implemented | yes | `salesagent-ol033.20.7` |
| F3 | For a multi-placement product the selected format set must cover every placement; reject or refine, never silently remove inventory | partial | partly | `salesagent-ol033.20.8` |
| F4 | The compatibility projection must be DERIVED from canonical data and must not become a second authored catalog | implemented | yes | `salesagent-ol033.20.9` |
| P1 | Honour the canonical product filters filters.format_kinds and filters.format_option_refs | implemented | yes | `salesagent-ol033.20.10` |
| P2 | Honour package-level canonical selectors format_kind / format_option_refs on create_media_buy and update_media_buy | implemented | yes | `salesagent-ol033.20.11` |
| M1 | Manifest identity is format_kind plus optional format_option_ref; new manifests use the canonical branch | implemented | yes | `salesagent-ol033.20.12` |
| M2 | format_option_ref is required when the target product carries multiple format_options sharing a format_kind | implemented | yes | `salesagent-ol033.20.13` |
| M3 | Validate the manifest against the selected declaration: resolve the ref, confirm required slots, validate each asset's type and technical constraints, check fixed dimensions/durations/codecs/file sizes, reject uncovered selections without relaxing the target | partial | partly | `salesagent-ol033.20.14` |
| M4 | A manifest must not mix format_id and format_kind (root oneOf, each arm with a not) | implemented | yes | `salesagent-ol033.20.15` |
| M5 | Preserve format_kind and format_option_ref through preview, sync, library and delivery paths | implemented | yes | `salesagent-ol033.20.16` |
| M6 | Carry representation_selection when the manifest was resolved from equivalent trafficking representations | implemented | yes | `salesagent-ol033.20.17` |
| C1 | Declare creative.has_creative_library: true when the agent exposes reusable library operations such as sync_creatives and list_creatives | not applicable | — | `salesagent-ol033.20.18` |
| C2 | Declare media_buy.inline_creative_management when package-scoped creative bodies are accepted on create/update | partial | partly | `salesagent-ol033.20.19` |
| C3 | Declare creative.supports_revisions and preserve immutable buyer revision_id values through sync, review, readback and delivery | not applicable | — | `salesagent-ol033.20.20` |
| C4 | Declare creative in supported_protocols and advertise creative.supported_formats[] if the endpoint can build, validate or preview | not applicable | — | — |
| E1 | Flag divergent dual emission with a non-fatal FORMAT_DECLARATION_DIVERGENT advisory in errors[], source: "producer", keeping the get_products response a populated success | implemented | yes | `salesagent-ol033.20.21` |
| E2 | Reject disagreeing canonical and legacy selectors, and conflicting canonical routes, with CONFLICTING_SELECTORS; report an unprojectable legacy route as UNSUPPORTED_FEATURE before attempting equivalence | implemented | yes | `salesagent-ol033.20.22` |
| G1 | build_creative accepts target_capability_id and returns a canonical manifest | not applicable | — | — |
| G2 | preview_creative / creative delivery features | not applicable | — | — |
| L1 | Canonicalize agent_url before treating two format references as the same (MUST) | implemented | yes | `salesagent-ol033.20.23` |
| L2 | Legacy → canonical projection follows the registry's normative order and fails closed; canonical → legacy must not be synthesized | implemented | partly | `salesagent-ol033.20.24` |
| L3 | Retain and echo the buyer's own format_id verbatim on the legacy arm | implemented | yes | `salesagent-ol033.20.25` |
| L4 | Do not author new template/parameterized format_id values; normalize legacy ones immediately and match on the canonical shape | partial, ceilings accepted | partly | `salesagent-ol033.20.26` |
| V1 | The seller's accepted format_kind vocabulary is the seller's, not the library's | no longer an obligation | — | — |
| V2 | Refuse a format_kind absent from the target product's format_options[] (the per-product closed set, enforced through the SDK's FormatKindNotInClosedSetError) | implemented | yes | `salesagent-ol033.20.27` |

Totals: 21 implemented, 4 partial (F3, M3, C2, L4), 5 not applicable, and V1 is no longer an
obligation at the pin. C4, G1 and G2 are creative-agent obligations. C1 and C3 are not applicable
by the owner's decision of 2026-10-29: this seller doesn't claim the creative protocol, and the
pinned capabilities response carries the creative block only when creative is in
`supported_protocols`. 18 obligations are graded "yes" and 7 "partly".

The remaining gaps:

- **Authoring has one graded path (S1).** `@T-UC-GET-PRODUCTS-format-options-operator-authored`
  grades the admin product form. Inventory-profile authoring and the seed paths have no
  scenario, and the rows the data migration leaves on the legacy arm are named only in its log.
- **Placement-level options on the get_products wire (S4).** A placement's inline
  `format_options` are not graded on the response.
- **Author-side intersection (F3).** At `813ee8dff` the admin saves accept a publisher-scoped
  option on a multi-publisher product without `placements[]` (salesagent-ol033.20.8.2). On
  `integrate/wave1` at `ff5795e1a` (wave 11, not yet box-run) the product save refuses it and data
  migration `7a74ba340f11` narrows the stored rows; a product whose intersection is empty is
  left as stored and logged. Graded by
  `@T-UC-GET-PRODUCTS-format-options-operator-common-intersection-accepted` and `-refused`.
- **Manifest validation beyond what is graded (M3).** At `813ee8dff` a legacy manifest's assets
  are not graded against its named format, and `cta_values`, card media constraints,
  `responsive_creative`'s video duration, logo aspect ratios and file-size caps, and
  `image_carousel`'s card bounds are not read. On `ff5795e1a` (wave 11, not yet box-run) all of these
  are read and have scenarios, a missing click destination is refused
  `CREATIVE_MISSING_CLICK_URL` and a `cta` outside `cta_values` `CREATIVE_VALUE_NOT_ALLOWED`.
  `om_sdk_required` still cannot be graded on a manifest
  ([adcp#8627](https://github.com/adcontextprotocol/adcp/issues/8627)).
- **Inline creatives on update (C2).** `update_media_buy` doesn't upload inline creatives to the
  ad server, while `create_media_buy` does. At `813ee8dff` no scenario stated this. On `ff5795e1a`
  (wave 11), following the rule in Part 10,
  `@T-UC-003-local-inline-creatives-ad-server-push`
  (`tests/bdd/features/local-uc003-create-update-parity.feature`) states it on a2a, mcp, rest
  and e2e_rest, strict-xfailed against #2483. `@T-UC-003-approval-tenant` is xfailed the same
  way: `update_media_buy` reads only the adapter's approval flag, not the tenant's. The
  capability flag is hardcoded in `src/services/seller_capabilities.py`.
- **A foreign declaration without `canonical` (L2).** At `813ee8dff` such a declaration still
  reaches the registry glob through the SDK. On `ff5795e1a` (wave 11, not yet box-run) the
  reference-origin rule in Part 10 is built: a peer's id projects only through its own
  `canonical` or the structural entries, VAST and DAAST version constraints included, and
  otherwise fails closed (`@T-UC-FORMAT-SATISFACTION-upsert-peer-id-skips-glob`,
  `-upsert-structural`, `-upsert-structural-versioned`). The SDK's own structural match ignores
  those versions ([adcp-client-python#1750](https://github.com/adcontextprotocol/adcp-client-python/issues/1750)).
  The `list_creatives` fail-closed Given also runs in process: `CreativeListEnv` cannot serve an
  unprojectable format, so fail-closed on the wire is graded only on the creative write
  (`@T-UC-FORMAT-SATISFACTION-upsert-unprojectable`).
- **Legacy-only products and density (L4).** These are the two accepted ceilings in Part 10, and
  are not graded.
- **SDK-side narrowing.** This is not a seller obligation:
  [adcp-client-python#1749](https://github.com/adcontextprotocol/adcp-client-python/issues/1749).

Creative format identity underpins creative, media buy, packages and delivery, so a change to
what business logic keys on is graded in all four areas, not only in the creative features.
Grading it only on the creative side is how a regression reaches a media buy.

## Sources

| Claim | Where to check it |
|---|---|
| Pinned schema text, every Part 10 quotation | The installed SDK's schema tree, or the spec repository at the tag the SDK reports (`docs/adcp-spec-version.md`): `core/product.json`, `core/product-format-declaration.json`, `registries/v1-canonical-mapping.json`, `enums/error-code.json`, `docs/creative/canonical-formats.mdx`, `docs/media-buy/specification.mdx`, `docs/creative/template-format-ids.mdx` |
| The SDK helpers Part 10 names | `adcp.canonical_formats`, in the pinned SDK |
| The SDK's narrowing check against the pinned vectors | [adcp-client-python#1749](https://github.com/adcontextprotocol/adcp-client-python/issues/1749), and `narrowing_vectors` in `static/test-vectors/v1-canonical-mapping.json` at the pinned tag |
| The derived projection, the resolution order and the reference-origin rule | `src/core/format_resolver.py` (`CanonicalFormats`, `NamedFormats`, `linked_format_contract`, `canonical_kind_for`, `canonical_projection`, `names_reference_agent`), `src/core/format_satisfaction.py` (`declaration_narrows`, `_legacy_route`, `publisher_slice_layers`) |
| The `list_creative_formats` projection | `src/core/tools/creative_formats.py` (`_declared_formats`), `CreativeAgentRegistry.lists_format`, `ToolSpec.advertised_description` |
| The data migration | `alembic/versions/ddb20e636cee_move_legacy_format_ids_into_format_options.py` |
| Per-nodeid outcomes behind Part 11 | Box run `innet_301026_1301` (`bdd_inprocess.json`, `bdd_e2e.json`), which ran `813ee8dff` on a clean tree |
| The per-obligation status in Part 11 | `docs/audits/creative-rfc-status.html`, and the beads under `salesagent-ol033.20` |
| The grading surface in Part 11 | The scenarios each obligation's bead names, under `tests/bdd/features`, and their routing in `tests/bdd/conftest.py` |
| 3.1.1 schema text | The vendored `_schemas/3.1` set inside `adcp==6.6.0` |
| 3.1.21 and 3.2.0-rc.1 schema text | `dist/schemas/3.1.21/` and `dist/schemas/3.2.0-rc.1/` in the AdCP repository |
| Published spec and SDK versions | `dist/schemas/latest.json`, and the `adcp` project on PyPI |
| `CODE_TABLE` contents | `src/core/errors/codes.py`, which loads `enums/error-code.json` from the installed wheel |
| The behaviour of every gap in Part 2 | The file and symbol named in the row |
