# BDD Audit Handoff — 2026-04-01

## What was accomplished this session

1. **Strengthened 92 weak BDD assertions** across 7 parallel agents (all UCs)
2. **Built 3-bucket audit framework** (FIX_NOW / XFAIL_IT / FEATURE_FIX)
3. **Fixed 331→0 BDD failures**: 17 strict xfails removed, 4 fixture gaps fixed, 310 production behaviors xfailed
4. **Full test suite green**: 8,492 tests passing, 0 failures (test-results/010426_1104/)
5. **Ran full step inspector**: 1,011 steps inspected (all types), 121 flagged

## Current test state

```
unit:        4,096 passed,  0 failed,   19 xfailed
integration: 1,822 passed,  0 failed,   39 xfailed
e2e:            91 passed,  0 failed,    2 xfailed
admin:          10 passed,  0 failed,    0 xfailed
bdd:         2,473 passed,  0 failed, 4,361 xfailed, 232 xpassed
```

Results: `test-results/010426_1104/`

## Inspector results (just completed)

Reports:
- `.claude/reports/bdd-step-audit-20260401_1356.md` — human-readable
- `.claude/reports/bdd-step-audit-20260401_1356.json` — machine-readable (121 entries)
- `.claude/reports/cross-reference-audit.md` — cross-referenced with test results

### Severity breakdown

| Severity | Count | What it means |
|----------|-------|---------------|
| MISSING | 21 | Step doesn't dispatch/setup what text claims — needs rewrite |
| WEAK | 91 | Assertion is weaker than step text claims — needs strengthening |
| COSMETIC | 9 | Minor issues, functionally acceptable |

### Risk by UC (flags on passing tests = hidden problems)

| UC | Flags | Risk |
|----|-------|------|
| GENERIC | 28 | HIGH (admin UI steps) |
| UC-002 | 10 | HIGH |
| UC-003 | 2 | HIGH |
| UC-004 | 24 | HIGH |
| UC-005 | 0 | OK — clean |
| UC-006 | 2 | HIGH |
| UC-011 | 17 | HIGH |
| UC-019 | 22 | HIGH |
| UC-026 | 16 | HIGH |

## What needs to happen next

### 1. File beads tasks from inspector results

Use the cross-reference report to create beads tasks grouped by UC and severity:

```bash
# View the inspector findings
cat .claude/reports/cross-reference-audit.md

# View detailed flags
cat .claude/reports/bdd-step-audit-20260401_1356.md
```

Suggested grouping (one beads task per UC × severity):
- P1: MISSING steps (21) — these steps don't do what they claim
- P2: WEAK steps (91) — assertions need strengthening
- P3: COSMETIC steps (9) — minor, do last

### 2. Fix the 21 MISSING steps (highest priority)

These are steps where the function body doesn't implement the operation at all.
Examples from the report:
- `when_query_agent_type` — doesn't dispatch ListCreativeFormatsRequest
- `when_boundary_agent_asset_types` — doesn't construct request with filtering
- `given_tenant_exists` — body is empty, tenant_id parameter ignored

### 3. Fix the 91 WEAK steps

Same pattern as the fix-92-flags team from this session. For each flagged step:
1. Read the inspector reason
2. Read the step source + linked scenario
3. Strengthen the assertion to match what the step text claims
4. Run targeted BDD test to verify

### 4. Re-run audit to confirm

After fixes:
```bash
# Re-run inspector (will resume from checkpoint if same output path)
python3 .claude/scripts/inspect_bdd_steps.py --json

# Cross-reference with latest test results
python3 .claude/scripts/cross_reference_audit.py \
  --inspector .claude/reports/bdd-step-audit-<NEW>.json \
  --results test-results/<LATEST>/bdd.json \
  --output .claude/reports/cross-reference-audit.md
```

## Key scripts

| Script | Purpose |
|--------|---------|
| `.claude/scripts/inspect_bdd_steps.py` | Two-pass BDD step inspector (Sonnet triage + Opus deep trace) |
| `.claude/scripts/cross_reference_audit.py` | Join inspector flags with test results |
| `.claude/scripts/bdd_full_audit.py` | 3-bucket audit (FIX_NOW / XFAIL_IT / FEATURE_FIX) |
| `.claude/scripts/audit_xfails.py` | Classify xfailed tests |

### Inspector features
- `--json` — machine-readable output alongside markdown
- `--pass1-only` — skip Opus deep trace (fast triage)
- `--then-only` — only inspect Then steps
- `--files FILE...` — scope to specific files
- **Resume**: checkpoint saved after each batch at `.checkpoint.json`
- **Truncation**: long functions truncated to 40 lines for API efficiency

## Beads state

Epic: `salesagent-2kv0` (BDD full audit)
- All FIX_NOW tasks closed (salesagent-nzik, salesagent-qgws, salesagent-mkwn, salesagent-7wan)
- `salesagent-d8sg` (strengthen 82 weak assertions) — closed, but inspector now shows 121 flags (re-evaluate)

## Important conventions

- **NEVER** use `--pass1-only` for final audit — always run full two-pass (memory: feedback_no_shortcuts_quality.md)
- Use `/team` with executor agents for parallel step fixes (slice by file)
- Agents 1-3 transports may not need xfail if impl passes
- conftest.py has `_SELECTIVE_XFAIL`, `_MCP_SELECTIVE_XFAIL`, and `_UC005_PARTIAL_TAGS` — understand all three before editing
- Production behaviors are XFAIL_IT (document, don't fix) — only fix wiring/fixtures/step code

## Git state

Branch: `feature/media-buy-refactoring`
Latest commit: `14699df4` (pushed)
All work committed and pushed.
