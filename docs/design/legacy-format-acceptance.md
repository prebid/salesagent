# Legacy format acceptance: call the SDK's comparison, keep ours for declarations

Why a product restricting itself to 300x250 accepts a 728x90 creative, and the smallest change
that fixes it.

**The version, derived rather than assumed.** `adcp.get_adcp_spec_version()` returns **3.2.3**
on this tree, from distribution `9.0.0b3+sa.8qela.bf0228b68`, read with
this worktree's own `.venv/bin/python` — so every clause below is quoted from
`adcp/_schemas/3.2`. Both halves are stated because both are traps. The venv carries
`_schemas/{3.0,3.1,3.1.15,3.2}`, so the wrong bundle is readable and answers differently
without erroring; and `get_adcp_spec_version()` reads a file packaged with the WHEEL, so it
moves with the SDK — `adcp 6.6.0`, which the shared checkout pins and which a leaked
`$VIRTUAL_ENV` resolves to, reports 3.1.1 instead. CLAUDE.md's "targets 3.1.1 via
adcp==6.6.0" describes main, not this branch. A sibling lane built a design on the 3.1.1 read
twice in one day, on a sentence that exists at 3.1.1 and was deliberately removed at 3.2.3.

Beads: salesagent-4764o (the acceptance defect), salesagent-59z0a (the broad-claim half).

## What is wrong

The accepted set a product is compared against is built from template keys —
`product_format_identities` → `format_id_identity` → `canonical_agent_url(agent_url) + \x1f + id`.
Measured:

```
product arm [display_image @ 300x250] -> {'https://creative.adcontextprotocol.org/\x1fdisplay_image'}
product arm [display_image @ 728x90]  -> {'https://creative.adcontextprotocol.org/\x1fdisplay_image'}
SAME KEY? True
```

**Two products with different restrictions produce the identical accepted set.** The product's
restriction is not merely unread, it is unrepresentable. `_legacy_lane`
(`format_satisfaction.py:779`) then decides acceptance with `claimed in legacy_keys`.

`core/format-id.json`'s `id` description names what the key loses: "When used alone, references
a **template** format. When combined with dimension/duration fields, creates a parameterized
format ID for a **specific variant**." The key reduces a variant to its template and then
accepts any other variant of the template.

## The fix: the SDK already implements this comparison, correctly

`adcp.canonical_formats.format_is_supported(requested, supported)` answers exactly this
question. Measured against a product restricting to `display_image` @ 300x250:

| requested | `format_is_supported` | `formats_are_equivalent` |
|---|---|---|
| `display_image` @ 300x250 | True | True |
| `display_image` @ 728x90 | **False** | False |
| `display_image` (bare template) | **False** | True |
| `video_hosted` @ 300x250 | False | False |

and against a BROAD product (bare `display_image`): every variant True, a different id False.

Row 2 is salesagent-4764o. Row 3 is salesagent-59z0a's legacy half — the pin's "a broad request
with no dimensions or duration does not satisfy a fixed-size or fixed-duration product". The
fourth column is the matching-vs-satisfaction distinction the normative clause is titled after:
a bare template and a 300x250 variant ARE the same canonical family and do NOT satisfy each
other.

The mechanism is minimal and is the pin's rule rather than a coincidence:

```python
req = _upgrade_legacy_format_id(requested); sup = _upgrade_legacy_format_id(supported)
if not formats_are_equivalent(req, sup): return False
for field in ("width", "height", "duration_ms"):
    if getattr(sup, field) is None: continue      # a broad product permits
    if getattr(req, field) != getattr(sup, field): return False   # silence FAILS
return True
```

Both sides are upgraded first, so a name-encoded id is normalized before comparison — which is
the clause's "legacy named formats MUST be normalized to canonical declarations before
comparison". `agent_url` is canonicalized (verified: a trailing slash still matches).

**So the change is small.** `_legacy_lane`'s `claimed in legacy_keys` becomes a
`format_is_supported` comparison against the product's legacy references, which means the
product's arm must be carried as REFERENCES rather than reduced to template keys.
`format_id_identity` is NOT touched — it is the graded resolution key and dropping params is
correct there (`format_resolver.py:758-760`: a parameterized reference must resolve to the
template it parameterizes).

## One upstream gap to cover locally

`core/format-id.json` declares four parameterizing fields. `format_is_supported` compares
three, and the loop is a literal — quoted rather than described, from
`9.0.0b3+sa.8qela.bf0228b68`:

```python
for field in ("width", "height", "duration_ms"):
```

So `pixel_ratio` is not merely unenforced on silence; it is **never read**, and a contradicting
value passes exactly as silence does. All three rows measured against a product fixing
`pixel_ratio=2` (a 600x500 asset for a 300x250 render):

```
creative states pixel_ratio=2 -> True
creative states pixel_ratio=1 -> True     <-- should be False
creative states NOTHING       -> True     <-- should be False
```

**The 1x row is the correction.** An earlier version of this table listed only the silence case
while the paragraph below it asserted "a product requiring a 2x asset currently accepts a 1x
one" — true, and not shown. A table that lists one of two failing cases stops the next reader
from checking the other, and the local supplement has to refuse both, so both are now measured
here and graded by a scenario each.

The pin calls the field "**Required** intrinsic-pixel density for a parameterized visual
format". This is the same defect shape this repo already fixed in itself —
`schemas/_base.py`'s `_FORMAT_ID_PARAMETERS` exists because "three of the four parameters were
copied by name, so a creative synced with a pixel ratio came back as a different format than
the buyer sent". Two independent implementations, one hand-written list each, the same field
missing from both.

Disposition: closed upstream. The pinned SDK (`adcp==9.3.0`) compares all four fields, and
reads an omitted `pixel_ratio` on a sized reference as the 1x variant, as `core/format-id.json`
says ("Omit for the backward-compatible 1x variant"). The local supplement that compared the
field beside the SDK call (`_pixel_ratio_unsatisfied`) is deleted, and the deprecated lane's
acceptance is `format_is_supported` alone. It also stops refusing a silent claim against a
product fixing 1x, which the supplement did and the field's text does not.

Both failing rows stay graded — `@T-UC-FORMAT-SATISFACTION-sync-density-mismatched` and
`-sync-density-silent` — and now grade the SDK's comparison.

## What stays ours, and why

**The canonical declaration comparison.** `_unsatisfied_params`, `_constraint_unsatisfied` and
`_range_unsatisfied` implement every rule the normative clause states — an exact number MUST
match and silence does not satisfy it, `max_*`/`min_*` are permissions silence does satisfy,
range containment of both endpoints with overlap insufficient, `duration_ms_exact` >
`duration_ms_range` precedence applied first.

The SDK's `check_narrows` is **not** a substitute and must not be used as one. It grades only
`max_*`/`min_*` divergence, has no exact-match rule, no range containment, no duration
precedence, and opens with `if not v2 or not v1: return []` — so an empty-params claim narrows
anything. Substituting it would reintroduce the defect being fixed.

**Confirmed broken upstream:** `find_declaration_by_v1_format_id`'s docstring says it walks
`v1_format_ref[]`; its body reads `decl.legacy_format_refs`, which none of the 16 generated
declaration arms declare:

```
RAISES AttributeError: 'ProductFormatDeclaration1' object has no attribute 'legacy_format_refs'
```

So `_legacy_route`'s v1_format_ref rung stays hand-written. That module's existing note on this
is confirmed and understated — it says the function "matches nothing", and it raises.

**Working and worth adopting where we hand-roll the same thing:**
`validate_format_kind_in_options` (raises `FormatKindNotInClosedSetError` naming the accepted
set — the pin's closed-set rule, "structural, not negotiable") and `find_declaration_by_kind`
(verified against the pinned arms).

## Ingest normalization: still right, no longer urgent

The pin makes `format_ids` an outbound projection — "sellers MAY additionally project those
declarations to `format_ids` for legacy buyers", "buyers MUST prefer `format_options`", "**Do
not author a new product from `format_ids` alone**" — and states the canonical comparison as
"match declarations with the same `format_kind` whose narrower parameters satisfy the broader
declaration". So a product's legacy arm should be normalized to declarations once, at authoring
or ingest, with `project_v1_format_to_declaration`; the catalog supports it (64 of 71 pinned
catalog formats declare `canonical.kind`, and the specs carry `renders[].dimensions`).

That remains the correct direction and it is NOT required to fix this defect. Decoupling it is
deliberate: it changes what product authoring accepts and what the admin UI shows — the same
change stage 5's A4 item names — and it should not ride along with a comparison fix.

## Two designs written and withdrawn, recorded because the shape recurs

**Withdrawn #1: a comparison rung grading the product's legacy arm as a variant.** Justified by
a measurement that 32 of 71 catalog ids state dimensions in their name and project to no params,
so normalizing the product's arm would drop constraints and fail open. That number asked
`upgrade_legacy_format_id` — the registry — which sizes exactly the 7-member
`display_NNNxNNN_image` family. The format spec the agent serves carries both kind and
dimensions, so the widening never arises.

**Withdrawn #2: ingest normalization as the fix.** Correct direction, wrong urgency — adopted
before checking whether the SDK already answered the comparison. It did.

The common shape: a structure justified by a number, where the number came from the narrowest
primitive to hand and no one asked whether an existing implementation already answered the
question. Check for the instrument before building one.

## Not in this design, deliberately

- **No change to `format_id_identity`** — graded resolution key, dropping params is correct.
- **No `check_narrows` substitution** — not a conformant satisfaction check.
- **No upstream filing yet** — two issues are ready (`legacy_format_refs` field name,
  `pixel_ratio` omission) and filing is outward-facing, so it needs a decision.

## Implemented

`b125d6ce4` (the comparison) and `65fc152fa` (the scenarios), merged at `6102651ed`. What the
design did not predict, recorded because each changed the work:

- **A name-encoded id cannot express the defect.** `upgrade_legacy_format_id` rewrites a
  reference's parameters from its NAME, so `display_300x250_image @ 728x90` upgrades to
  `display_image @ 300x250` and `format_is_supported` answers True. The scenario had to use a
  TEMPLATE id plus parameters. A row built on the id this document uses as its own example
  would have passed before and after the fix.
- **The wire-level accept, which this document left derived.** Measured on all three
  in-process transports before the fix: `status=completed`, `assigned_to=['pkg_001']`,
  `assignment_errors=None`. A 300x250 product created an assignment for a 728x90 creative.
- **The refusal is `CREATIVE_SIZE_MISMATCH`, the code the canonical lane gives the same
  condition.** When the product takes the creative's template at other parameters,
  `_legacy_shortfalls` reports each parameter that fails (`format_id.width`,
  `format_id.pixel_ratio`, ...) and `creative_refusal` codes them as it codes a manifest's:
  a size or density is `CREATIVE_SIZE_MISMATCH`, a required click destination left out
  `CREATIVE_MISSING_CLICK_URL`, anything else
  `CREATIVE_VALIDATION_FAILED_GENERIC`. A format the product does not take at all is still
  `VALIDATION_ERROR`, raised by the assignment sites themselves, and lenient
  `sync_creatives` still skips both.
- **The refusal has to NAME the size.** A template-key accepted set could only ever render
  `<agent>/display_image` — the same string whether the product restricts to 300x250 or to
  nothing — so `_reference_display` renders the identity plus its parameters, and the Then
  grades the dimensions rather than only the code.
