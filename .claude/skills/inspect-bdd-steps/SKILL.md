---
name: inspect-bdd-steps
description: >
  Two-pass BDD step assertion completeness inspector.
  Pass 1 (Sonnet): triage all Then steps — FLAG or PASS.
  Pass 2 (Opus): deep trace flagged steps with full production context,
  producing architectural judgment on what the correct assertion should be.
  Use after writing or modifying BDD step definitions to catch assertion
  mismatches (steps that claim to verify X but actually only check existence).
args: "[--pass1-only] [--then-only] [--steps-dir PATH] [--output PATH]"
---

# BDD Step Assertion Completeness Inspector

Inspects every BDD step function for semantic completeness: does the function
body actually implement what the step text claims?

## Usage

```
/inspect-bdd-steps
/inspect-bdd-steps --pass1-only
```

## What It Does

1. **AST scan**: Extracts all `@given`/`@when`/`@then` decorated functions
   from `tests/bdd/steps/`
2. **Pass 1 (Sonnet triage)**: For each Then step, asks: "Is there a HIGH
   chance this function does NOT implement what the step text claims?"
   - PASS: Function plausibly implements its claim
   - FLAG: Function likely doesn't (pass body, truthiness-only check, etc.)
3. **Pass 2 (Opus deep trace)**: For each FLAG, collects production context
   (schemas, error classes, harness code) and asks Opus to make an
   architectural judgment about what the correct assertion should be.
4. **Report**: Writes `.claude/reports/bdd-step-audit-<date>.md` with
   findings grouped by severity (MISSING > WEAK > COSMETIC).

## Protocol

Run the inspection script:

```bash
python3 .claude/scripts/inspect_bdd_steps.py
```

Options:
- `--pass1-only` — Skip Pass 2 deep trace (fast triage only)
- `--steps-dir PATH` — Override step definitions directory
- `--output PATH` — Override report output path
- `--then-only` — Only inspect Then steps (default: true)

Review the generated report and use findings to inform beads-58u
(assertion mismatch fixes) or create new tasks.

## When to Use

- After writing new BDD step definitions
- After modifying existing step assertions
- As a periodic audit (monthly or per-epic)
- Before closing BDD-related PRs
