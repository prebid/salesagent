---
name: verify-spec
description: >
  Verify every test expectation in entity test suites against the authoritative
  adcp spec and library sources. Adds spec permalinks to each test for
  traceability. Flags discrepancies where our tests assume behavior the spec
  doesn't define. Run this after /surface and before /remediate.
args: <entity-name-1> [entity-name-2] ...
---

# Spec Verification

Verify that every test in entity surface suites reflects actual protocol
behavior as defined by the authoritative sources. Adds traceability links
and flags discrepancies before we commit to changing production code.

## Args

```
/verify-spec <entity-name-1> [entity-name-2] ...
```

Entity names (space-separated). Each must already have a test suite from
`/surface`. If the suite doesn't exist, the formula STOPS at pre-check.

## Why This Step Exists

The pipeline from spec to test has multiple translation layers:

```
adcp spec repo (JSON schemas, OpenAPI)
    ↓ interpreted into
adcp-client-python (Pydantic models)
    ↓ extracted into
docs/test-obligations/ (requirements artifacts)
    ↓ mapped into
tests/unit/test_{entity}.py (surface tests + stubs)
```

Each arrow can introduce errors. Before `/remediate` changes production code
to make tests pass, we must confirm the tests expect the RIGHT behavior.

## Sources of Truth (Priority Order)

1. **`adcontextprotocol/adcp`** — The protocol spec. JSON schemas at
   `dist/schemas/3.0.0-beta.3/core/`, OpenAPI at `static/openapi/`.
   Local clone: `/Users/konst/projects/adcp` (commit-pinned).
   GitHub: `https://github.com/adcontextprotocol/adcp`

2. **`adcontextprotocol/adcp-client-python`** — The Python implementation.
   Pydantic models at `adcp/types/generated_poc/`.
   Local clone: `/Users/konst/projects/adcp-client-python` (upstream remote).
   GitHub: `https://github.com/adcontextprotocol/adcp-client-python`

3. **`adcp-req`** (derivative, NOT authoritative) — Requirements artifacts at
   `/Users/konst/projects/adcp-req/docs/requirements/`. Useful as an INDEX
   with source links back to (1) and (2). Follow those links to confirm
   faster than researching from scratch. Never treat adcp-req as the
   source of truth itself.

## Entity to Spec File Mapping

| Entity | JSON Schemas (adcp repo) | Python Types (adcp-client-python) | adcp-req Index |
|--------|-------------------------|----------------------------------|----------------|
| creative | creative-manifest.json, creative-asset.json, creative-variant.json, creative-filters.json, creative-assignment.json, creative-policy.json, format.json, format-id.json | media_buy/sync_creatives_*, media_buy/list_creatives_* | BR-UC-006/ |
| media-buy | media-buy.json, package.json, pricing-option.json, targeting.json, brand-ref.json | media_buy/create_media_buy_*, media_buy/update_media_buy_* | UC-002/, UC-003/ |
| delivery | delivery-metrics.json, reporting-webhook.json, reporting-capabilities.json | media_buy/get_media_buy_delivery_* | UC-004/ |

## What You Get

For each entity, the test file is updated with:

1. **Spec permalink in each test docstring**:
   ```python
   def test_create_media_buy_requires_brand(self):
       """UC-002-S01: CreateMediaBuyRequest requires brand (BrandReference).

       Spec: https://github.com/adcontextprotocol/adcp/blob/{COMMIT}/dist/schemas/3.0.0-beta.3/core/media-buy.json#L42
       Library: https://github.com/adcontextprotocol/adcp-client-python/blob/{COMMIT}/adcp/types/generated_poc/media_buy/create_media_buy_request.py#L15
       """
   ```

2. **Discrepancy report** — tests flagged where expectation doesn't match spec:
   - `CONFIRMED` — spec supports this test expectation
   - `UNSPECIFIED` — spec doesn't explicitly address this behavior (implementation-defined)
   - `CONTRADICTS` — spec says something different than what the test expects
   - `SPEC_AMBIGUOUS` — spec is unclear, needs interpretation

3. **Verification summary** at the top of the test file:
   ```python
   """Entity test suite: creative

   Spec verification: 2026-02-26
   adcp spec commit: 975402d5
   adcp-client-python commit: a08805d
   Verified: 89/95 CONFIRMED, 4 UNSPECIFIED, 2 CONTRADICTS
   """
   ```

## Protocol

### Step 1: Cook the molecule

```bash
python3 .claude/scripts/cook_formula.py \
  --formula .claude/formulas/verify-spec.yaml \
  --var "ENTITY_NAMES={all_args}" \
  --epic-title "Verify spec: {all_args}"
```

**Dry run first** (recommended):
```bash
python3 .claude/scripts/cook_formula.py \
  --formula .claude/formulas/verify-spec.yaml \
  --var "ENTITY_NAMES={all_args}" \
  --epic-title "Verify spec: {all_args}" \
  --dry-run
```

### Step 2: Walk the molecule

```
bd ready -> bd show <atom-id> -> execute -> bd close <atom-id> -> repeat
```

Each entity goes through: pre-check -> pin-commits -> extract-expectations ->
verify-against-spec -> annotate -> review -> commit.

### Step 3: Done when all atoms closed

All entity suites annotated with spec links and verified. Discrepancy
report generated.

## Verification Strategy

For each test, the verification atom should:

1. **Read the test docstring** to understand what behavior it asserts
2. **Check adcp-req first** (as an index) — follow the source link it provides
3. **Confirm at the source** — read the actual JSON schema or Python type
4. **For stubs**: verify the EXPECTED behavior described in the skip reason
5. **For real tests**: verify the assertions match what the spec defines

### Shortcut via adcp-req

adcp-req artifacts list their source in each requirement:
```markdown
**Source**: adcp/dist/schemas/3.0.0-beta.3/core/media-buy.json, property `brand`
```

Follow this link directly — it's faster than searching the spec from scratch.
But always CONFIRM at the actual source file, don't just trust the adcp-req
interpretation.

## Batching

Each entity is one batch (unlike /remediate which batches within an entity).
For an entity with 130+ tests, the verify atom will be the longest — but it's
read-only analysis, not code changes, so it's safe to do in one pass.

## Pipeline Position

```
/surface  ->  /verify-spec  ->  /remediate
  (map)        (confirm)        (implement)
```

## See Also

- `/surface` — Create the entity test suite (prerequisite)
- `/remediate` — Fill verified stubs (runs after this)
- `/guard` — Structural guards (orthogonal)
