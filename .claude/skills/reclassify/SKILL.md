---
description: Reclassify obligation layers (behavioral vs schema) to shrink the test obligation allowlist without writing integration tests. Uses deterministic keyword scoring plus LLM judgment for borderline cases.
---

# /reclassify — Obligation Layer Reclassification

Reclassify obligation items from `behavioral` to `schema` where appropriate, shrinking the obligation coverage allowlist.

## Context

Every obligation tagged `**Layer** behavioral` in `docs/test-obligations/` requires an integration test (or allowlist entry). Many obligations are pure schema checks — field presence, enum validation, serialization — that can be tested with unit tests and mocked data. Reclassifying these as `schema` removes them from the behavioral obligation set.

## Protocol

### Step 1: Run deterministic classifier report

```bash
uv run python scripts/reclassify_obligations.py --report
```

Review the output:
- **Schema** obligations (negative score): high-confidence reclassifications
- **Behavioral** obligations (positive score): correctly classified, no action needed
- **Borderline** obligations (score = 0): need LLM review in Step 2

### Step 2: Review borderline obligations

For each borderline obligation (score = 0) that has **both** behavioral and schema signals, read the full scenario text and apply this rubric:

> "Does testing this obligation require rows created/updated/deleted in a real database, external service calls, or multi-step state transitions? If yes → behavioral. If it can be tested with mocked data and schema assertions alone → schema."

For borderline obligations you decide to reclassify:
1. Open the source doc file
2. Find the `**Layer** behavioral` line after the obligation's `**Obligation ID**` line
3. Change `behavioral` to `schema`

### Step 3: Apply deterministic classifications

```bash
uv run python scripts/reclassify_obligations.py --apply --regenerate-allowlist
```

This updates:
- `**Layer**` tags in all obligation docs
- `tests/unit/obligation_coverage_allowlist.json` (regenerated from behavioral-only set)

### Step 4: Verify guard passes

```bash
make quality
```

All obligation coverage guard tests must pass. If they fail:
- Check `test_obligation_count_documented` — allowlist size must match uncovered behavioral count
- Check `test_known_uncovered_are_still_obligations` — no phantom IDs in allowlist

### Step 5: Commit

```bash
git add docs/test-obligations/ tests/unit/obligation_coverage_allowlist.json
git commit -m "refactor: reclassify schema obligations to reduce allowlist"
```

## Keyword Scoring Reference

The classifier in `scripts/reclassify_obligations.py` uses scored keyword matching:

| Direction | Signal | Example Keywords |
|-----------|--------|-----------------|
| +behavioral | State mutation | creates, persists, stores, deletes, updates |
| +behavioral | Workflow | approval, pending, status transitions |
| +behavioral | External I/O | adapter, webhook, LLM, retry |
| +behavioral | Auth/access | denied, unauthorized, AUTH_REQUIRED |
| +behavioral | Transactions | rollback, atomic, no partial state |
| -schema | Field presence | field is present, response includes, is preserved |
| -schema | Serialization | serialized to AdCP, model_dump, JSON output |
| -schema | Enum/type | enum, valid values, oneOf, Enum: |
| -schema | Constraints | exactly one of, minItems, minLength, pattern |
| -schema | Validation | schema validation, validated against, defaults to |

Score > 0 → behavioral. Score ≤ 0 with schema signals → schema. No signals → behavioral (safe default).

## Files

| File | Role |
|------|------|
| `scripts/reclassify_obligations.py` | Deterministic classifier script |
| `docs/test-obligations/*.md` | Obligation docs with `**Layer**` tags |
| `tests/unit/obligation_coverage_allowlist.json` | Behavioral-only allowlist |
| `tests/unit/test_architecture_obligation_coverage.py` | Guard that validates coverage |
