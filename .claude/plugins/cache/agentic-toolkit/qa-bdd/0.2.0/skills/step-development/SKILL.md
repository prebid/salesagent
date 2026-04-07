---
name: step-development
description: >
  Write BDD step definitions with mandatory scenario context analysis.
  Hard gate: before writing ANY Then step, the agent must complete a
  "scenario brief" that identifies all scenarios using the step, determines
  their intent, reads harness capabilities, and defines what "strong" means.
  This prevents weak assertions that check existence instead of correctness.
args: <feature-file-or-step-text> [--steps-file PATH]
---

# BDD Step Development (Scenario-Brief Gated)

Write BDD step definitions that are semantically correct from the start.
The key insight: a step's implementation depends on the SCENARIOS that use it,
not just the step text. The same step text means different things in different
scenarios.

## Args

```
/step-development tests/bdd/features/BR-UC-003-update-media-buy.feature
/step-development "the response should contain affected_packages"
/step-development tests/bdd/features/BR-UC-011-manage-accounts.feature --steps-file tests/bdd/steps/domain/uc011_accounts.py
```

## Protocol

### Step 0: Identify missing steps

Parse the feature file for step texts that lack implementations:

```bash
# Run pytest --collect-only to find missing steps
uv run pytest tests/bdd/ -k "$(basename FEATURE .feature)" --collect-only 2>&1 | grep "NOTIMPLEMENTED\|StepImplementationNotFound\|fixture.*not found"
```

Or use ast-grep to inventory existing steps and diff against feature file:

```bash
# List all implemented step texts
ast-grep --pattern '@given("$TEXT")' tests/bdd/steps/ 2>/dev/null
ast-grep --pattern '@when("$TEXT")' tests/bdd/steps/ 2>/dev/null
ast-grep --pattern '@then("$TEXT")' tests/bdd/steps/ 2>/dev/null
```

Categorize each missing step as Given, When, or Then.

### Step 1: Given and When steps (standard flow)

Given and When steps do not require the full scenario brief. Implement them
following these rules:

**Given steps:**
- Set up state in the test context (`ctx`)
- Use factories from `tests/factories/` (never raw dicts or inline `session.add()`)
- Read `.agent-index/factories.pyi` for available factory classes
- Store created objects in ctx with descriptive keys for later Then steps

**When steps:**
- Dispatch the operation through the harness or production code
- Capture BOTH success responses AND errors in ctx
- Never swallow exceptions silently -- catch and store them
- Read `.agent-index/api/impl.pyi` for _impl function signatures

### Step 2: Then steps -- MANDATORY SCENARIO BRIEF (hard gate)

**You MUST complete the scenario brief before writing ANY Then step implementation.
This is not optional. Do not skip it. Do not abbreviate it.**

For EACH Then step to implement, complete all 5 phases:

---

#### Phase 1: Find all scenarios using this step

Search the features directory for every scenario that references this step text:

```bash
grep -rn "STEP_TEXT" tests/bdd/features/ --include="*.feature"
```

For each match, extract:
- Scenario name (the `Scenario:` or `Scenario Outline:` line above)
- Tags (e.g., `@partition`, `@boundary`, `@error`, `@happy_path`)
- The full Given/When/Then chain for that scenario

List them explicitly. Example output:

```
Scenarios using "the response should contain affected_packages":
  1. "Pause active media buy" (@happy_path, @post_s2)
     Given: media buy "mb_existing" is active with package "pkg_001"
     When: buyer pauses the media buy
     Then: response contains affected_packages
  2. "Budget update with package changes" (@mutation)
     Given: media buy "mb_existing" with budget $5000, package "pkg_001"
     When: buyer updates budget to $3000
     Then: response contains affected_packages
```

#### Phase 2: Determine scenario intent

For each scenario found in Phase 1, answer: **What does this scenario INTEND
to prove?**

The same step text means different things depending on scenario context:
- In an error-handling scenario: checking for a specific error code
- In a feature-works scenario: checking for specific output values
- In an isolation scenario: checking exclusion of other tenants' data
- In a boundary scenario: checking exact boundary behavior (not just "works")
- In a partition scenario: checking which CODE PATH was taken

Write a one-sentence intent for each scenario. Example:

```
Intent:
  1. Pause scenario: buyer must see WHICH packages were paused (not just "some packages")
  2. Budget update: buyer must see WHICH packages had budget adjusted
```

#### Phase 3: Read harness capabilities

Determine what the test harness provides for this domain:

1. **Read the .agent-index stubs** for the relevant harness env:
   ```
   .agent-index/harness/envs.pyi   -- domain env classes with methods
   .agent-index/harness/base.pyi   -- BaseTestEnv interface
   ```

2. **Check what ctx provides**: Read the Given/When steps that precede this
   Then step. What keys do they set in ctx? What objects are stored?

3. **Check mock return types**: What does the harness mock return? What
   response type does the _impl function produce?

4. **Read the error hierarchy** if the step involves errors:
   ```
   .agent-index/errors.pyi
   ```

Document what's available. Example:

```
Harness: MediaBuyUpdateEnv
  ctx["existing_media_buy"] -> MediaBuy with media_buy_id="mb_existing"
  ctx["existing_package"] -> MediaPackage with package_id="pkg_001"
  ctx["response"] -> UpdateMediaBuyResponse (has .affected_packages: list[str])
  ctx["error"] -> AdCPError | None
```

#### Phase 4: Define "strong" for this step

Based on Phases 1-3, write a one-liner that defines what a STRONG assertion
looks like for this specific step. This is the contract your implementation
must fulfill.

Format: `Strong assertion: <what specifically must be verified>`

Examples:
- `Strong assertion: affected_packages contains "pkg_001" from ctx["existing_package"], not just non-empty list`
- `Strong assertion: error.error_code == "BUDGET_TOO_LOW", not just error is not None`
- `Strong assertion: assignments match specific (creative_id, package_id) pairs from request, not just len > 0`
- `Strong assertion: response.media_buy_id == ctx["expected_media_buy_id"], not just is not None`
- `Strong assertion: returned account IDs are disjoint from decoy IDs in ctx["other_agent_accounts"]`
- `Strong assertion: retry count == 4 (1 initial + 3 retries), not 2 <= count <= 4`

#### Phase 5: Write the implementation

Now -- and ONLY now -- write the step function. The implementation MUST match
the "strong" definition from Phase 4.

```python
@then(parsers.parse('the response should contain affected_packages'))
def then_affected_packages_present(ctx):
    """Verify affected_packages lists the specific packages that were modified."""
    response = ctx["response"]
    expected_pkg_id = ctx["existing_package"].package_id

    assert hasattr(response, "affected_packages"), "Response missing affected_packages"
    assert isinstance(response.affected_packages, list), "affected_packages is not a list"
    assert len(response.affected_packages) > 0, "affected_packages is empty"

    # Strong: verify the SPECIFIC package from the scenario, not just any package
    pkg_ids = [p if isinstance(p, str) else p.package_id for p in response.affected_packages]
    assert expected_pkg_id in pkg_ids, (
        f"Expected package {expected_pkg_id} in affected_packages, got {pkg_ids}"
    )
```

### Step 3: Self-check

After writing each Then step, verify:

1. **Does the assertion match the Phase 4 "strong" definition?**
   If not, rewrite.

2. **Would this step pass with WRONG data?**
   Mentally substitute wrong values. If the step would still pass
   (e.g., any non-None value, any non-empty list, any error), it's WEAK.

3. **Does the step re-assert Given preconditions?**
   If you're checking ctx values that the Given step set up, you're
   testing the test, not the production code. Remove those checks.

### Step 4: Run and verify

```bash
uv run pytest tests/bdd/ -k "scenario_name" -x -v
```

## Anti-Patterns -- FORBIDDEN

The following patterns produce weak assertions. The skill MUST NOT use them.

### hasattr() on Pydantic models

```python
# FORBIDDEN: hasattr() is ALWAYS True on Pydantic model fields
assert hasattr(response, "affected_packages")  # Always True, even if None

# CORRECT: check the actual value
assert response.affected_packages is not None
assert len(response.affected_packages) > 0
```

### getattr(..., None) is not None as sole assertion

```python
# FORBIDDEN: proves attribute exists, not that it has correct value
assert getattr(response, "media_buy_id", None) is not None

# CORRECT: compare against expected value from scenario context
assert response.media_buy_id == ctx["expected_media_buy_id"]
```

### ctx["error"] as substitute for response inspection

```python
# FORBIDDEN: checks test harness state, not production response
if ctx.get("error"):
    return  # "error exists, good enough"

# CORRECT: inspect the actual error object for specifics
error = ctx["error"]
assert isinstance(error, AdCPValidationError)
assert error.error_code == "BUDGET_TOO_LOW"
```

### Accepting any error as proof of specific validation

```python
# FORBIDDEN: auth failure, network error, or serialization bug all pass
if ctx.get("error"):
    pass  # "some error happened, close enough"

# CORRECT: verify the SPECIFIC error type and code
error = ctx["error"]
assert isinstance(error, AdCPValidationError), f"Expected validation error, got {type(error)}"
assert "budget" in str(error).lower(), f"Error not budget-related: {error}"
```

### len(items) > 0 as sole collection check

```python
# FORBIDDEN: any non-empty list passes, even with wrong items
assert len(response.packages) > 0

# CORRECT: verify specific items from scenario context
expected_ids = {ctx["pkg_001"].package_id, ctx["pkg_002"].package_id}
actual_ids = {p.package_id for p in response.packages}
assert expected_ids <= actual_ids, f"Missing packages: {expected_ids - actual_ids}"
```

### Re-asserting Given preconditions from ctx

```python
# FORBIDDEN: validates the test setup, not production behavior
webhook_config = ctx["webhook_config"]
assert not webhook_config["active"]  # This is what the Given step set!

# CORRECT: verify production behavior (no HTTP POST was made)
assert not any(
    mb_id in str(call) for call in mock_post.call_args_list
), f"POST was made for {mb_id} despite no webhook configured"
```

### Absence-of-failure instead of presence-of-success

```python
# FORBIDDEN: empty context (no validation ran) also passes
assert "error" not in ctx

# CORRECT: verify positive acceptance signal
response = ctx["response"]
assert response is not None
assert response.status == "accepted"  # or whatever the success indicator is
```

## Relationship to Other Quality Gates

This skill is **Level 2** in the assertion quality pipeline:

| Level | Mechanism | What it catches |
|-------|-----------|-----------------|
| 1 | Structural guard (AST) | Deterministic patterns: hasattr on Pydantic, len>0 as sole check |
| **2** | **This skill (scenario brief)** | **Context-dependent weakness: assertions that ignore scenario intent** |
| 3 | Pre-commit inspect gate | LLM-powered check on changed Then steps |
| 4 | Periodic full inspection | Comprehensive audit of all steps |

The scenario brief prevents the root cause (information asymmetry) rather than
catching symptoms after the fact.

## References

- `.agent-index/harness/envs.pyi` -- domain env classes with methods
- `.agent-index/harness/base.pyi` -- BaseTestEnv interface
- `.agent-index/factories.pyi` -- factory_boy factories
- `.agent-index/api/impl.pyi` -- _impl function signatures
- `.agent-index/errors.pyi` -- AdCPError hierarchy
- `.agent-index/schemas/core.pyi` -- request/response models
- `tests/bdd/features/` -- Gherkin feature files (scenario context)
- `tests/bdd/steps/domain/` -- existing step implementations
- `.claude/reports/bdd-step-audit-*.md` -- inspection reports
