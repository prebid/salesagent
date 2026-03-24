---
name: inspect-bdd-steps
description: >
  Two-pass BDD step correctness inspector for all step types (Given/When/Then).
  Pass 1 (Sonnet): triage all steps — FLAG or PASS.
  Pass 2 (Opus): deep trace flagged steps with full production context,
  producing architectural judgment on what the correct behavior should be.
  Use after writing or modifying BDD step definitions to catch:
  - Then: assertion mismatches (claims to verify X but only checks existence)
  - Given: setup mismatches (claims to set up X but uses wrong data/params)
  - When: dispatch mismatches (claims to send X but swallows errors or calls wrong function)
args: "[--pass1-only] [--then-only] [--files FILE...] [--steps-dir PATH] [--output PATH] [--json]"
---

# BDD Step Correctness Inspector

Inspects every BDD step function for semantic correctness: does the function
body actually implement what the step text claims?

## Usage

```
/inspect-bdd-steps                              # All step types, all files
/inspect-bdd-steps --pass1-only                  # Fast triage only
/inspect-bdd-steps --then-only                   # Then steps only (legacy mode)
/inspect-bdd-steps --files tests/bdd/steps/domain/uc002_create_media_buy.py  # Specific files
```

## What It Does

1. **AST scan**: Extracts all `@given`/`@when`/`@then` decorated functions
   from `tests/bdd/steps/`
2. **Pass 1 (Sonnet triage)**: For each step, type-aware triage:
   - **Then**: Does the function assert what the step text claims?
   - **Given**: Does the function set up what the step text describes?
   - **When**: Does the function dispatch the operation and capture outcomes?
   - PASS: Function plausibly implements its claim
   - FLAG: Function likely doesn't
3. **Pass 2 (Opus deep trace)**: For each FLAG, collects production context
   and asks Opus to make an architectural judgment.
4. **Report**: Writes `.claude/reports/bdd-step-audit-<date>.md` with
   findings grouped by severity (MISSING > WEAK > COSMETIC).

## Protocol

Run the inspection script:

```bash
python3 .claude/scripts/inspect_bdd_steps.py
```

Options:
- `--pass1-only` — Skip Pass 2 deep trace (fast triage only)
- `--then-only` — Only inspect Then steps (default: false)
- `--files FILE...` — Scope to specific step files
- `--json` — Also write machine-readable JSON alongside markdown
- `--steps-dir PATH` — Override step definitions directory
- `--output PATH` — Override report output path

Review the generated report and work findings systematically via beads tasks.

## When to Use

- After writing new BDD step definitions
- After modifying existing steps
- As a periodic audit (monthly or per-epic)
- Before closing BDD-related PRs
- Integrated into bdd-pipeline.sh INSPECT phase (per-task)
