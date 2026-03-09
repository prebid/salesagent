---
name: obligation-team
description: >
  Spawn a team of agents to write per-obligation behavioral tests in parallel.
  Each agent researches, writes, runs, and fixes their test independently.
  Leader consolidates verified tests, runs quality gates, and commits.
arguments:
  - name: obligations
    description: >
      Obligation IDs or prefix with --count. Examples:
      UC-004 --count 10
      UC-004-ALT-WEBHOOK-PUSH-REPORTING-03 UC-004-ALT-WEBHOOK-PUSH-REPORTING-04
    required: true
---

# Team-Based Obligation Test Generation: $ARGUMENTS

Spawn one agent per obligation. Each agent researches production code, writes a
test to a temp file, runs it, fixes it until green, and reports back verified
working code. Leader consolidates into the final test file and commits.

## Architecture

- **Agents**: Research + write temp test file + run pytest + fix until green (parallel)
- **Team lead**: Consolidates verified tests into final file, quality gates, commit

Agents are full team members with shell access. They each write to their own
temp file (`tests/unit/_tmp_test_{OID_SUFFIX}.py`) so there are no conflicts.
The leader appends verified code to the shared test file after all agents report.

## Protocol

### Step 0: Resolve obligation IDs

Parse `$ARGUMENTS` to extract obligation IDs.

**If prefix mode** (args look like `UC-004 --count 10` — no trailing sequence number):

```bash
python3 -c "
import json
al = json.loads(open('tests/unit/obligation_coverage_allowlist.json').read())
prefix = '$PREFIX'
matches = sorted(oid for oid in al if oid.startswith(prefix))
count = $COUNT  # default 10 if --count not specified
selected = matches[:count]
print('Selected obligations:')
for oid in selected:
    print(f'  {oid}')
print(f'Total matching: {len(matches)}, selected: {len(selected)}')
"
```

**If direct IDs** (args are full obligation IDs separated by spaces):
Verify each ID exists in `docs/test-obligations/` or the allowlist.

Store the resolved list as `OBLIGATION_IDS`.

### Step 1: Gather shared context

Read these once — they'll be included in every agent's prompt:

1. **Test file header** (imports + helpers):
   ```bash
   # Identify the test file from the use case prefix
   # UC-004 → tests/unit/test_delivery_behavioral.py
   head -110 tests/unit/test_delivery_behavioral.py
   ```

2. **Obligation scenarios** for all resolved IDs:
   ```bash
   for OID in $OBLIGATION_IDS; do
     grep -A 30 "$OID" docs/test-obligations/*.md
   done
   ```

3. **Production files to read** (list for the agent prompt):
   - `src/core/tools/media_buy_delivery.py`
   - `src/core/schemas/delivery.py`
   - `src/core/webhook_delivery.py`
   - `src/core/webhook_authenticator.py`

### Step 2: Create team

```
TeamCreate:
  team_name: "ob-{prefix}-{timestamp}"
  description: "Obligation tests: {N} obligations from {prefix}"
```

### Step 3: Create tasks (one per obligation)

For each `OID` in `OBLIGATION_IDS`:
```
TaskCreate:
  subject: "Research + write + verify test for {OID}"
  description: "Agent researches obligation, writes test, runs it, fixes until green"
  activeForm: "Building test for {OID}"
```

### Step 4: Spawn N agents (parallel, single message)

Launch ALL agents simultaneously using `Agent` tool calls in one message.
Each agent is `subagent_type: "general-purpose"` with **`model: "opus"`**.

**MANDATORY: model must be "opus"** — do not use sonnet or haiku for team agents.

---

**BEGIN AGENT PROMPT TEMPLATE** (substitute {OID}, {OID_SUFFIX}, {SCENARIO}, {TEST_HEADER}, {TARGET_TEST_FILE}):

```
You are writing ONE behavioral test for obligation {OID}.
You have full shell access. You will research, write, run, and fix your test
until it passes before reporting back.

## Obligation Scenario

{SCENARIO — exact Given/When/Then from docs/test-obligations/}

## Production Code

Read these files and trace the behavior described in the obligation:
- src/core/tools/media_buy_delivery.py (start at _get_media_buy_delivery_impl)
- src/core/schemas/delivery.py
- src/core/webhook_delivery.py
- src/core/webhook_authenticator.py

Find the SPECIFIC LINES that implement (or would implement) the Given/When/Then.

## Existing Test Context

Your test will eventually be appended to {TARGET_TEST_FILE}.
Here are the imports and helpers already available in that file
(DO NOT redefine these in your final test class):

{TEST_HEADER — first ~110 lines of the test file}

## Your Workflow

### Phase 1: Research
1. Read the production code files listed above
2. Trace the exact code path for this obligation's Given/When/Then
3. Identify: which function to call, what to mock, what to assert

### Phase 2: Write temp test file
Write your test to a TEMPORARY file:
```
tests/unit/_tmp_test_{OID_SUFFIX}.py
```

This temp file MUST be self-contained — copy the necessary imports and helpers
from the test header into it so pytest can run it standalone. Include:
- All imports from the test header that your test needs
- The helper functions (_make_identity, _make_buy, _make_adapter_response, _PATCH)
- Your test class

### Phase 3: Run and fix
```bash
uv run pytest tests/unit/_tmp_test_{OID_SUFFIX}.py -x -v
```

If the test fails:
- **ImportError/NameError/TypeError**: Fix the test code (wrong types, wrong method, etc.)
- **AssertionError where your assertion is correct per the obligation scenario**:
  This means production doesn't implement the designed behavior. Mark `@pytest.mark.xfail`
  with a reason describing WHAT is missing and WHERE it would go.
- **AssertionError where your assumption was wrong**: Fix the test to match
  actual production behavior. Re-read the production code more carefully.

Iterate until the test PASSES or is correctly marked XFAIL.

### Phase 4: Clean up temp file
```bash
rm tests/unit/_tmp_test_{OID_SUFFIX}.py
```

### Phase 5: Answer Right Questions and report

## 6 Hard Rules

Your test MUST satisfy ALL of these:

| # | Rule | Check |
|---|------|-------|
| 1 | MUST import from `src.` | Has `from src.` import (may use existing imports) |
| 2 | MUST call production function | Test body calls `_impl`, repo method, or schema method |
| 3 | MUST assert production output | Assertion checks a value from the production call |
| 4 | MUST have `Covers: {OID}` tag | Docstring contains exactly `Covers: {OID}` |
| 5 | MUST use helpers | Use _make_buy, _make_identity, _make_adapter_response, etc. |
| 6 | MUST NOT be mock-echo only | Does more than verify mock.called |

## IMPORTANT FORMAT RULE
The Covers tag MUST be on its own indented line in the docstring:
```python
class TestSomething:
    """Description of test.

    Covers: {OID}
    """
```

## 5 Right Questions (MANDATORY — answer ALL before reporting)

### RQ-1: Am I calling the ACTUAL production function?

Trace the call chain in your test:
- What production function does your test call?
- Does that function contain the LOGIC described in the obligation?
- Or does it just pass through to something else?

FAIL if: Test only constructs a schema and calls model_dump().
         That's a schema test, not a behavioral test.
FAIL if: Test calls deliver_webhook_with_retry for an obligation about
         webhook SCHEDULING (the scheduler doesn't exist yet — wrong layer).
PASS if: Test calls _get_media_buy_delivery_impl, _get_target_media_buys,
         deliver_webhook_with_retry, WebhookAuthenticator.sign_payload,
         or another function that contains actual business logic for
         the obligation's scenario.

For xfail tests: the function you call should be WHERE the behavior
WOULD be implemented. Not just any production function.

### RQ-2: Am I importing production helpers, or reimplementing them?

Check every function/lambda defined in your test code (excluding test_ and _make_):
For each: search production code for the same function.
If production has this function → IMPORT it, don't redefine it.
If it doesn't exist → verify it's truly test-only.

FAIL example: Defining _to_internal(status) when it exists in production.
PASS example: Importing from src.core.tools.media_buy_delivery import _to_internal

### RQ-3: Would my assertion FAIL if production behavior changed?

Read your primary assertion. Imagine the production code changes to
return a WRONG value. Would your assertion catch it?

FAIL: assert result is not None         → passes for ANY non-None value
FAIL: assert len(result) > 0            → passes for any non-empty list
PASS: assert result.notification_type == "scheduled"  → catches wrong type
PASS: assert returned_ids == ["mb_completed"]          → catches wrong IDs
PASS: assert headers["X-Webhook-Signature"].startswith("sha256=")

### RQ-4: For xfail — WHERE would this behavior be implemented?

If marking @pytest.mark.xfail, you MUST answer:
a) WHAT is missing? (specific function/parameter/logic)
b) WHERE would it go? (which file:function)
c) Does your test CALL that location?

FAIL: "Behavior would be in a webhook scheduler" but test calls
      deliver_webhook_with_retry (wrong layer).
PASS: "notification_type would be set in _get_media_buy_delivery_impl
      around the response construction" and test calls that function.

If NOT xfail, answer "N/A — test is expected to pass."

### RQ-5: Are all my mocks exercised?

For each @patch in your test:
1. Does the production code path actually call this patched target?
2. If you removed the patch, would the test behave differently?

FAIL: @patch("...get_db_session") but the code path never enters DB logic.
PASS: @patch("...requests.post") — the delivery function calls requests.post.

Remove dead patches.

## Output Format

Report back with EXACTLY these 5 sections:

### 1. RESEARCH
Three subsections:
- **Understanding**: What the obligation requires (1-2 sentences)
- **Production code**: Which function/lines implement this (with file:line refs)
- **Test strategy**: Unit/integration, which mocks, key assertion

### 2. TEST CODE
The test class ONLY (no imports/helpers — those are already in the target file).
Must be ready to append to {TARGET_TEST_FILE}. Include:
- A separator comment with the OID
- The test class with docstring containing Covers: {OID}
- Any NEW imports needed that aren't already in the file header

### 3. TEST RESULT
- PASS or XFAIL
- Paste the pytest output line showing the result
- If you had to fix issues during iteration, briefly describe what you fixed and why

### 4. RIGHT QUESTIONS
Your answer to each RQ-1 through RQ-5. Format:
- RQ-1: PASS/FAIL — [explanation]
- RQ-2: PASS/FAIL — [explanation]
- RQ-3: PASS/FAIL — [explanation]
- RQ-4: PASS/FAIL or N/A — [explanation]
- RQ-5: PASS/FAIL — [explanation]

### 5. XFAIL
yes/no — If yes, the specific reason (what's missing in production code).
```

**END AGENT PROMPT TEMPLATE**

---

### Step 5: Collect and review results

As each agent reports back:

1. **Check TEST RESULT section** — agent must report PASS or XFAIL with pytest output
   - If agent reports ERROR or unresolved failure, send back for revision

2. **Check all 5 Right Question answers are present**
   - If any RQ answer is "FAIL", send a message back to the agent requesting
     revision with specific guidance on what to fix.

3. **Check test code follows all 6 hard rules**
   - Verify `from src.` import present
   - Verify `Covers: {OID}` tag present and on its own line
   - Verify production function call in test body
   - Verify assertion checks production output

4. **Collect approved test code** for Step 6.

### Step 6: Write tests (leader only)

Once all agents have reported approved, verified test code:

1. **Determine new imports** needed (not already in the test file):
   ```bash
   # Compare agent-reported imports against existing
   head -41 tests/unit/test_delivery_behavioral.py
   ```

2. **Append all test classes** to the test file:
   - Add new imports at the top if needed (after existing imports)
   - Add separator comments + test classes at the end of the file
   - Maintain the existing file structure

3. **Update the allowlist** — remove OIDs for passing tests:
   ```bash
   python3 -c "
   import json
   path = 'tests/unit/obligation_coverage_allowlist.json'
   al = json.loads(open(path).read())
   passing_oids = [<list of OIDs with passing (non-xfail) tests>]
   for oid in passing_oids:
       if oid in al:
           al.remove(oid)
   open(path, 'w').write(json.dumps(sorted(al), indent=2) + '\n')
   print(f'Removed {len(passing_oids)} OIDs from allowlist')
   "
   ```

4. **Format and lint**:
   ```bash
   uv run ruff format tests/unit/test_delivery_behavioral.py
   uv run ruff check tests/unit/test_delivery_behavioral.py --fix
   ```

5. **Run the tests**:
   ```bash
   uv run pytest tests/unit/test_delivery_behavioral.py -x -v 2>&1 | tail -40
   ```

6. **DECISION GATE — if any test fails after consolidation**:

   The agents already verified their tests pass in isolation. If a test fails
   after consolidation, the DEFAULT is that the test is correct and production
   is wrong. Tests derive from obligation scenarios which derive from
   requirements. Requirements are the source of truth, not production code.

   **Only TWO legitimate reasons to modify a test:**

   - **Mechanical integration issue**: Import conflict, name collision,
     missing helper that existed in the temp file but not the shared file.
     These are consolidation artifacts, not behavioral differences.
     Fix the plumbing, don't touch the assertion.

   - **Ruff/formatting**: Lint or format changes that don't affect behavior.

   **Everything else → xfail:**

   If the test assertion fails because production behaves differently from
   what the obligation scenario specifies, mark `@pytest.mark.xfail(reason="...")`
   with a specific reason. Do NOT change the assertion. Do NOT "fix" the test
   to match production. The test describes designed behavior. Production is
   what needs to change eventually.

   **There is no "ambiguous" case.** The obligation scenario is the spec.
   If production disagrees with the spec, that's an xfail, not a test bug.

7. **Run quality gates**:
   ```bash
   make quality 2>&1 | tail -30
   ```

8. **Run obligation guard**:
   ```bash
   uv run pytest tests/unit/test_architecture_obligation_coverage.py -x -v 2>&1 | tail -20
   ```

### Step 7: Commit

```bash
git add tests/unit/test_delivery_behavioral.py
git add tests/unit/obligation_coverage_allowlist.json
git add tests/unit/test_architecture_obligation_coverage.py  # if _UNIT_ENTITY_FILES updated
git commit -m "feat: add UC-004 obligation tests ({N} obligations)"
```

### Step 8: Shutdown team

1. Send shutdown requests to all agents:
   ```
   SendMessage: type="shutdown_request", recipient="<agent-name>"
   ```

2. After all agents confirm shutdown:
   ```
   TeamDelete
   ```

3. Report summary:
   ```
   ## Summary
   - Obligations covered: N
   - Tests passing: X
   - Tests xfail: Y
   - Allowlist reduced by: Z
   - Quality gates: PASS/FAIL
   ```

## Test File Selection

| Use Case | File |
|----------|------|
| UC-002 | `test_create_media_buy_behavioral.py` |
| UC-003 | `test_update_media_buy_behavioral.py` |
| UC-004 | `test_delivery_behavioral.py` |
| UC-006 | `test_creative_behavioral.py` |
| Other | `test_{use_case}_behavioral.py` |

## Anti-Patterns

- **Don't let agents write to the shared test file** — they write temp files only. Leader consolidates.
- **Don't skip RQ review** — if an agent reports FAIL on any RQ, require revision.
- **Don't batch-append without formatting** — always run ruff after appending.
- **Don't commit before quality gates pass** — fix issues first.
- **Don't exceed 10 agents** — diminishing returns, context pressure on leader.
- **Don't use sonnet/haiku for agents** — obligation tests require opus-level reasoning.
- **NEVER change an assertion to match production** — if the scenario says X and production does Y, xfail. The test derives from requirements. Requirements are the source of truth. Period.

## See Also

- `/obligation-test` — Sequential per-obligation formula (single agent, beads-tracked)
- `/surface` — Map complete test surface for an entity
- `/remediate` — Fill existing test stubs
