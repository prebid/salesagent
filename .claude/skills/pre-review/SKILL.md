---
name: pre-review
description: >
  Pre-review a salesagent PR before human review. Checks for the recurring
  patterns that ChrisHuie and KonstantinMirin consistently flag across
  merged PRs: hoisting, type refinement, vacuous assertions, create/update
  parity, raw dicts to typed fields, dead code, test DRY violations, and
  redundant DB queries. Saves review rounds by catching these before submission.
args: <pr-number>
---

# Pre-Review Skill

Run before opening a PR or requesting review. Catches the patterns that
ChrisHuie and KonstantinMirin consistently flag in code review, derived
from analysis of 102 comments across 14 merged PRs.

Does NOT replace `make quality` — run that first. This catches semantic
patterns that ruff, mypy, and unit tests miss.

## Usage

```
/pre-review 1276
```

## Data Fetching

**Use `curl` for ALL GitHub API calls.** Do NOT use `gh api` or WebFetch —
they can paraphrase or summarize content, losing raw JSON structure. `curl`
returns exact API responses.

```bash
# Auth header for all curl calls:
# -H "Authorization: token $GITHUB_TOKEN"
# Or if gh is authed: -H "Authorization: token $(gh auth token)"
```

## Protocol

### Step 0: Fetch PR context (fetch once — do not re-fetch in later steps)

```bash
# PR metadata
curl -s -H "Authorization: token $(gh auth token)" \
  "https://api.github.com/repos/prebid/salesagent/pulls/<pr-number>" | jq '{title, body, additions, deletions, changed_files}'

# File list
curl -s -H "Authorization: token $(gh auth token)" \
  "https://api.github.com/repos/prebid/salesagent/pulls/<pr-number>/files" | jq '[.[] | {filename, status, additions, deletions}]'

# Full diff
curl -s -H "Authorization: token $(gh auth token)" \
  -H "Accept: application/vnd.github.v3.diff" \
  "https://api.github.com/repos/prebid/salesagent/pulls/<pr-number>"
```

Read the diff. Understand what the PR is trying to do before checking anything.

### Step 1: Gather existing review signal (skip bokelley — agent-generated)

```bash
# Existing reviews
curl -s -H "Authorization: token $(gh auth token)" \
  "https://api.github.com/repos/prebid/salesagent/pulls/<pr-number>/reviews" | \
  jq '[.[] | select(.user.login == "ChrisHuie" or .user.login == "KonstantinMirin") | {user: .user.login, state, body}]'

# Inline comments
curl -s -H "Authorization: token $(gh auth token)" \
  "https://api.github.com/repos/prebid/salesagent/pulls/<pr-number>/comments" | \
  jq '[.[] | select(.user.login == "ChrisHuie" or .user.login == "KonstantinMirin") | {user: .user.login, path, line, body}]'
```

Note what's already been flagged. Do not re-flag resolved issues.

### Step 2: Run the checklist

Work through every check below against the PR diff. For each finding,
record: file path, line number, pattern name, and one-sentence description
of the specific violation.

---

## Checklist

### A. Hoisting — Inline imports without circular-dep justification

> Chris's named pattern. Konstantin flags it most often (6 threads across 14 PRs).

For every `import` statement inside a function or method body in the diff:

1. Check whether the imported module has a circular dependency with the
   enclosing module. Use:
   ```bash
   grep -r "from src.<enclosing_module>" <imported_module_path>
   ```
2. If no circular dep exists → **flag it**. Move to module-level imports.
3. If a circular dep exists → acceptable, note it as intentional.

**Exception:** `TYPE_CHECKING` guards are fine.

---

### B. Type refinement — `Any` where a concrete type is known

> Konstantin's named pattern. Appears in 5 threads; often triggers follow-on
> raw-dict bugs in later rounds.

Scan changed Python files for:

```bash
# In the diff, look for:
grep -n ": Any\b\|-> Any\b\|list\[Any\]\|dict\[str, Any\]" <changed_files>
```

For each hit, ask: does the function only ever receive or return one specific
type? If yes:
- `product: Any` that only accesses `.product_id` → `Product | None`
- `list[Any]` that only ever contains `Error` objects → `list[Error]`
- `-> Any` on a function that always returns a `BaseModel` subclass → type it

**Note:** If the concrete type would cause a circular import, use
`TYPE_CHECKING` guard + string annotation.

---

### C. Vacuous / tautological test assertions

> Both reviewers flag this. 7 threads. Konstantin tracks it systematically.

In changed test files, scan for:

1. **OR short-circuit**: `assert X or Y` where X is the success/happy-path
   condition. When X is True, Y never evaluates. Example:
   ```python
   # BAD — the all(...) never runs on success
   assert not isinstance(response, CreateMediaBuyError) or all(...)
   ```
   Fix: split into two asserts.

2. **hasattr on concrete object**: `assert hasattr(obj, "method")` where
   `obj` is not Optional and the method is on the class unconditionally.
   This passes on main with zero changes to the code under test.

3. **Happy path with zero assertions**: a test that calls the impl and
   returns without asserting anything about the response. Even one
   `assert not isinstance(response, ErrorType)` is required.

4. **Always-true inequality**: comparing a Pydantic model instance to a dict
   with `!=` — always True because model != dict in Python even if contents
   match. Use `.model_dump(mode="json")` on both sides or compare model-to-model.

---

### D. Create/update path parity

> Konstantin's most systematic pattern. Flagged across every round of PR #1276.
> Causes exploitable validation bypasses.

If the PR touches any `_create_*_impl` or `_update_*_impl` function:

1. Find all validator/helper calls in the create impl.
2. Find all validator/helper calls in the update impl.
3. List anything in create that's missing from update (and vice versa).
4. Flag any asymmetry that allows a buyer to bypass a validation by using
   the update path instead of create.

Also check: does the create path check `identity.principal_id` and
`identity.tenant_id`? If yes, does the corresponding list/update/sync path
do the same three-part guard?

---

### E. Raw dicts passed to typed model fields

> Both reviewers. 5 threads. Often introduced when a `list[Any]` field is
> tightened to `list[Error]` but call sites aren't updated.

In the diff, find call sites for any model whose field was changed from
`list[Any]` → `list[ConcreteType]`:

```bash
grep -n '{"code":\|{"message":\|{"success": False' <changed_files>
```

Each raw dict passed to a `list[Error]`, `list[SomeModel]`, or similar
typed field should be replaced with the typed constructor:
```python
# BAD
errors=[{"code": "FOO", "message": "bar"}]
# GOOD
errors=[Error(code="FOO", message="bar")]
```

Also check: are both sides of any comparison using the same representation?
DB-hydrated Pydantic models vs serialized dicts will always be unequal.

---

### F. Test DRY — helpers extracted but old callsites not updated

> Both reviewers. 8 threads. The most common pattern overall.

1. Check `tests/utils/` and `tests/integration/` for any helper that was
   added or modified in the diff.
2. Search for the same logic inline in other test files:
   ```bash
   grep -rn "<key_line_from_helper>" tests/
   ```
3. If the same regex, date helper, fixture setup, or factory call exists
   in 2+ places outside the shared module → flag as DRY violation.

Also check: does the PR add a new test factory or fixture? If so, verify
that existing tests in the same file aren't still hand-rolling the same setup.

```bash
# Common factories to check for:
grep -rn "PrincipalFactory\|TenantFactory\|MediaBuyFactory" tests/
```

---

### G. Dead / unreachable code

> Chris flags this most. 5 threads. Often involves exception branches.

1. **Unreachable exception branch**: `except SomeError` inside a `try` block
   where `SomeError` cannot be raised by any statement in the block.
   Common: catching `ToolError` around FastMCP TypeAdapter calls (which
   raise `pydantic.ValidationError`, never `ToolError`).

2. **No-op transformation**: `json.loads(json.dumps(x))` where `x` is
   already plain JSON types — the round-trip changes nothing. Retry logic
   that re-sends identical data cannot produce a different outcome.

3. **Import of renamed/deleted symbol**: any `from module import name` where
   `name` no longer exists in `module` (ruff catches at import time, but
   check scripts/ and tests/scripts/ which may not be in the ruff pass).

---

### H. Redundant DB queries on hot-path callsites

> Chris's pattern. 3 threads. Focus on `get_adapter()` and repository methods.

In changed repository or service files:

1. Look for any method that calls `self.get_by_tenant()` or
   `session.execute(select(...))` more than once with the same WHERE clause.
2. In `get_adapter()` and similar factory methods: check whether any called
   helper re-queries what the caller already loaded.
3. Look for `count_by_X()` immediately followed by `get_by_X()` with the
   same arguments — consolidate to a single query with `limit=2`.

---

### I. Weakened BDD/integration assertions

> Chris's pattern. Flags when OR replaces AND in Then-step assertions.

In changed BDD step files (`tests/bdd/steps/`):

1. Find any step that uses `or` in its assertion where the Gherkin text
   implies both conditions must hold (e.g., "contains error message AND
   field reference" → step must assert both, not either).
2. Find any step where a field-identity check was removed
   (`response_names == registered_names`) and replaced with a count check.
3. Find any step where a guard (`len(x) > 0` before asserting on contents)
   was removed.

---

### J. Project conventions (non-blocking but flag as notes)

Quick checks — these are low-friction but Chris/Konstantin both care:

- **No issue/beads/PR numbers in code comments** (convention: reference
  PR number in commit message, not inline code comment)
- **No `# noqa: F401`** on imports that are actually used (ruff catches,
  but double-check any added in the diff)
- **No `--link` in Docker commands** (deprecated; use named networks)
- **`subprocess.run()` result used without checking `returncode`** when
  the result drives a gate decision (Chris caught this disabling DRY
  enforcement entirely in PR #1107)

---

## Output Format

```
## Pre-Review: PR #<number> — <title>

**Diff summary:** <N> files, +<X>/-<Y> lines

### Findings

#### [BLOCKING] <Pattern Name>
`<file>:<line>` — <one sentence describing the specific violation>
<2-3 lines of context if needed>

#### [BLOCKING] ...

#### [NOTE] <Pattern Name>
`<file>:<line>` — <convention/non-blocking observation>

### Not Flagged
- Hoisting: no inline imports without circular-dep reason
- Create/update parity: N/A (no impl functions touched)
- <etc for each check that passed>

### Already Covered by make quality
Do not re-flag anything caught by ruff, mypy, or unit tests.
```

Use **BLOCKING** for anything Konstantin or Chris would require fixed before
approving. Use **NOTE** for conventions they mention but don't block on.

If a check doesn't apply to this PR (e.g., no impl functions → skip parity
check), explicitly note it as N/A so the reader knows it was considered.

## See Also

- `/qc-validator` — validates beads task completion (different scope: task
  acceptance criteria, not PR review patterns)
- `/guard` — creates permanent structural guards for HARNESS-ABLE patterns
  found here
