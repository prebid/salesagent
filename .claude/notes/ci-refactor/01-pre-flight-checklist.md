# Pre-flight Checklist

Everything that must be true before PR 1 is authored. Mix of admin-only actions (only `@chrishuie` can do) and agent-runnable preparation steps.

## Admin-only actions (cannot be delegated to agents)

These require admin scope on the GitHub repo. Run them yourself; paste outputs into [03-decision-log.md](03-decision-log.md) as a "Decision X — captured state" entry where appropriate.

### A1 — Capture current branch protection state

```bash
gh api repos/prebid/salesagent/branches/main/protection \
  -H "Accept: application/vnd.github+json" \
  > .claude/notes/ci-refactor/branch-protection-snapshot.json
```

If the API returns `404 Branch not protected`, no rule exists yet — start from blank. Either way, this snapshot is the rollback target for PR 3 Phase B.

**Token requirement:** classic PAT with `repo` scope, OR fine-grained PAT with `Administration: read` permission. A default `gh auth login` token does NOT include this.

### A2 — Capture current required-checks list

```bash
gh api repos/prebid/salesagent/branches/main/protection/required_status_checks \
  --jq '.checks[].context' > .claude/notes/ci-refactor/required-checks-current.txt
```

`.checks[].context` is the canonical field per GitHub's branch-protection API; the deprecated `.contexts[]` form was scrubbed in 2026-04-25 Round 8 sweep.

This is the list PR 3 Phase B will atomically replace with the **14** frozen names from D17 amended by D30 (Round 10 sweep added Smoke Tests, Security Audit, Quickstart).

### A3 — Verify GitHub private vulnerability reporting is ON

UI path: **Settings → Code security → Private vulnerability reporting → Enable**.

Confirm via:
```bash
gh api repos/prebid/salesagent --jq '.security_and_analysis.private_vulnerability_reporting.status'
```
Expected: `"enabled"`. If disabled, enable it; SECURITY.md (PR 1) will reference the resulting `/security/advisories/new` URL.

### A4 — Verify Dependabot is enabled

UI path: **Settings → Code security → Dependabot alerts → Enable** + **Dependabot security updates → Enable** (the auto-PR-on-CVE feature, which is separate from version updates and OK to enable; D5 only forbids auto-MERGE, not auto-PR).

Confirm:
```bash
gh api repos/prebid/salesagent --jq '.security_and_analysis.dependabot_security_updates.status'
```
Expected: `"enabled"`.

### A5 — Confirm CodeQL repo-level setup mode

UI path: **Settings → Code security → CodeQL analysis**. Must be set to **"Advanced"** (so PR 1's custom `codeql.yml` workflow takes precedence over GitHub's default scan).

If currently "Default", flip to "Advanced" — there's a one-time confirmation that disables the default scan.

### A6 — Decide PR #1217 fate before starting

PR #1217 (`feature/adcp-3.12-migration`) is open and CONFLICTING. Before authoring PR 1, decide:

- **Merge it** — bumps `pyproject.toml:10` `adcp>=3.10.0` → `adcp>=3.12.0`. Strengthens PR 2's PD1 evidence (3-major-version drift instead of 7-minor) but doesn't change the fix.
- **Close it** — work has been superseded by something else.
- **Land around it** — author PR 2 to tolerate the merge happening mid-review.

**Default: land around it** (PR 2 spec is written for this).

### A7 — Capture coverage baseline

```bash
cd /Users/quantum/Documents/ComputedChaos/salesagent
test -f coverage.json && python -c "import json; d=json.load(open('coverage.json')); print(f\"current: {d['totals']['percent_covered']:.2f}%\")" \
  || (./run_all_tests.sh && python -c "import json; d=json.load(open('coverage.json')); print(f\"current: {d['totals']['percent_covered']:.2f}%\")")
```

Most recent measurement (2026-04, from agent inspection): **55.56%**. PR 3's `.coverage-baseline` will be set to `53.5` (current minus 2pp safety margin) per D11.

Re-measure if the value has shifted >1pp since 2026-04.

### A8 — Capture pre-commit warm latency baseline

```bash
pre-commit run --all-files >/dev/null 2>&1 || true   # warm cache
{ time pre-commit run --all-files >/dev/null; } 2>&1 | grep real
```

Issue claims 18-30s warm. Capture the actual number for the rollout's "before" metric.

### A9 — Capture OpenSSF Scorecard baseline

```bash
docker run --rm -e GITHUB_AUTH_TOKEN=$(gh auth token) \
  gcr.io/openssf/scorecard:stable \
  --repo=github.com/prebid/salesagent \
  --format=json | jq '{score, checks: [.checks[] | select(.score < 7) | {name, score, reason}]}'
```

Captures the current score and the failing-or-weak checks. Target post-rollout: ≥7.5/10. Capture the "before" number to validate progress.

### A10 — Confirm the CSRF advisory window plan

Re-read [03-decision-log.md](03-decision-log.md) D10. Confirm you accept Path C (advisory CodeQL for 2 weeks, flip to gating in Week 5).

If you change your mind: PR 1 spec has a one-line toggle for gating-vs-advisory in the codeql.yml workflow.

### A11 — Audit GitHub repo `allow_auto_merge` toggle (R30 mitigation)

```bash
gh api repos/prebid/salesagent --jq '.allow_auto_merge'
```

Expected: `false`. If `true`, this is the silent bypass attack chain: combined with R20 (compromised bypass actor), an attacker who phishes @chrishuie can click the "Enable auto-merge" button on a malicious Dependabot PR.

Disable via:
- UI path: **Settings → General → Pull Requests → Disable "Allow auto-merge"**, OR
- API: `gh api -X PATCH /repos/prebid/salesagent -f allow_auto_merge=false`

Confirm: `gh api repos/prebid/salesagent --jq '.allow_auto_merge'` returns `false`.

### A12 — Drain existing Dependabot PR queue before PR 1 lands (R9 + D5)

```bash
gh pr list --author "app/dependabot" --state open --json number,title --jq '.[] | "\(.number) \(.title)"'
```

Plan: clear the queue to ≤2 open Dependabot PRs before authoring PR 1, so the first post-PR-1 cron cycle doesn't compound a backlog onto a fresh deluge. If queue is ≥5, the D5 sustainability tripwire fires; pause PR 1 authoring until cleared.

### A13 — Snapshot `mypy.ini [mypy.plugins]` block (R2/D13 mitigation)

```bash
grep -A 1 '^plugins' mypy.ini > .claude/notes/ci-refactor/mypy-plugins-baseline.txt
```

Captures the current dead-plugin state (`pydantic.mypy` listed but dependency missing from mirrors-mypy hook). PR 2 makes the plugin live; this snapshot proves it was dead pre-PR-2.

### A15 — Decide `develop` branch fate (Round 8 — silent-bypass mitigation)

`origin/develop` exists and is 35 commits ahead / 2 behind `main` (Round 8 drift-verified). Both `test.yml` (current) and `ci.yml` (PR 3 introduces) trigger on `branches: [main, develop]`. Branch protection is configured on `main` only — `develop` is unprotected.

**Risk:** silent governance bypass. Any PR merged to `develop` escapes the 14 frozen required checks (D17 amended by D30).

**Decision required before PR 1 launches** — pick one:

**Option A — delete `develop`** (preferred for cleanest governance):
```bash
# Verify nothing in flight targets develop (admin only)
gh pr list --base develop --state open
# If empty:
gh api -X DELETE /repos/prebid/salesagent/git/refs/heads/develop
# Update ci.yml triggers to drop `develop` (in PR 3 spec)
```

**Option B — apply branch protection symmetrically to `develop`** (if `develop` must remain — e.g., contributor forks track it):
```bash
gh api -X PUT /repos/prebid/salesagent/branches/develop/protection \
  --input .claude/notes/ci-refactor/branch-protection-snapshot.json
# Document in ADR-002 that `develop` mirrors `main` protection
```

**Option C — leave `develop` unprotected and remove from triggers** (intermediate — develop becomes a personal feature-branch convention without CI gating).

Default recommendation: Option A (delete) unless contributor workflow concretely depends on `develop`.

### A16 — xdist soak (per PR 3 spec Pre-flight 3a)

Before PR 3 Phase A merge, run integration suite under xdist on current main:
```bash
tox -e integration -- -n 4   # ≥3 successful runs
tox -e integration -- -n auto # ≥1 successful run
```
Capture timings; record in PR 3 description. If flakes appear, fix infrastructure FIRST (likely candidates: `mcp_server` port TOCTOU, `factory.Sequence` collisions, module-global engine mutations in `tests/conftest_db.py:478-486`).

### A17 — AST guard pre-existing-violation audit (per PR 4 spec Commit 1.5)

Before PR 4 Commit 7 deletes legacy grep hooks, run each new AST guard against current main:
```bash
pytest tests/unit/test_architecture_no_defensive_rootmodel.py -v -x
pytest tests/unit/test_architecture_no_tenant_config.py -v -x
pytest tests/unit/test_architecture_jsontype_columns.py -v -x
# ... etc per PR 4 spec
```
If any guard surfaces violations: choose Option A (remediate) or Option B (expand `ALLOWED_FILES` allowlist). Document the choice. Hard gate.

### A18 — mypy plugin canary (per PR 2 spec)

Before PR 2 commit 8 lands, verify the pydantic.mypy plugin is actually loaded:
```bash
uv run mypy tests/unit/_pydantic_mypy_canary.py 2>&1 | grep -q "Incompatible default"
```
If the canary doesn't trigger the expected mypy error, the plugin failed to load — escalate. D13's ">200 errors" tripwire is uninstrumented without this canary.

### A14 — Confirm @chrishuie can be added to bypass list (D2 mitigation)

The Prebid.org GitHub org may require a higher-tier admin to add @chrishuie to the branch-protection bypass list. Confirm before authoring PR 1 (which depends on the bypass for the solo-maintainer model). Test:

```bash
gh api repos/prebid/salesagent/branches/main/protection/bypass_pull_request_allowances --jq '.users[].login'
```

If @chrishuie is not in the list and current GH role doesn't have org-admin to add, escalate to org admin before PR 1 lands.

### A20 — Snapshot in-flight fork PRs before Phase B flip (Round 11 R11C-07)

Phase B Step 2.5's in-flight PR drain fails for fork PRs because `gh workflow run --ref refs/pull/<n>/head` returns 403 (no write access on contributor fork). Capture the fork-PR list pre-flight so the maintainer can post coordination comments before the flip:

```bash
gh pr list --state open --search "is:pr -author:@me draft:false" --json number,headRepository,headRefName,author \
  --jq '.[] | select(.headRepository.owner.login != "prebid")' \
  > .claude/notes/ci-refactor/inflight-fork-prs-snapshot.json
wc -l .claude/notes/ci-refactor/inflight-fork-prs-snapshot.json
```

Before Phase B Step 2 PATCH, post a coordination comment on each fork PR: "Branch-protection-rename Phase B is happening at <time>. After the flip, please push a no-op commit (e.g., `git commit --allow-empty -m 'chore: refresh CI'`) to refresh status checks. Without this, your PR will show 'expected — waiting for status' indefinitely."

### A21 — Validate CODEOWNERS + dependabot.yml syntax post-PR-1-merge (Round 11 R41)

GitHub silently disables CODEOWNERS routing on syntax error (no failure surface). Dependabot stops opening PRs entirely on dependabot.yml syntax error. Both can be detected via API:

```bash
# CODEOWNERS validator (returns errors[] array; should be empty)
gh api repos/prebid/salesagent/codeowners/errors --jq '.errors | length'   # expect: 0
# If non-zero, fix-forward in a follow-up PR before any other PR merges (R41 mitigation).

# Dependabot config validator (no native API; check by Dependabot opening at least one PR within 1 week)
# After PR 1 lands, watch:
gh pr list --author "app/dependabot" --state open --json number --jq 'length'   # expect: ≥1 within 7 days
```

If `codeowners/errors` is non-empty: do NOT merge any other PR until fixed; routing is silently broken.

### A22 — Phase B day-of-week + holiday guard (Round 11 D45)

Per **D45** (Round 11 sweep), Phase B atomic flip is FORBIDDEN on Fri/Sat/Sun + holiday eve. Pre-flight check before executing Phase B:

```bash
# Day-of-week check (1=Mon, 7=Sun)
DOW=$(date +%u)
[[ "$DOW" -ge 1 && "$DOW" -le 4 ]] || { echo "ABORT: Phase B is forbidden on Fri-Sun per D45. Today is dow=$DOW. Reschedule to Mon-Thu."; exit 1; }

# Holiday-eve check (extend with org calendar; minimal version: US federal next-day holidays)
NEXT_DAY=$(date -d "+1 day" +%Y-%m-%d 2>/dev/null || date -v+1d +%Y-%m-%d)
# WARNING: HOLIDAYS_2026 below is hard-coded for calendar year 2026 (Round 14 deep-verify Gap 3 bonus B3).
# If the rollout extends past Jan 1, 2027, rename the variable and refresh the date list:
# US federal holidays: https://www.opm.gov/policy-data-oversight/pay-leave/federal-holidays/
HOLIDAYS_2026="2026-01-01 2026-01-19 2026-02-16 2026-05-25 2026-06-19 2026-07-03 2026-07-04 2026-09-07 2026-10-12 2026-11-11 2026-11-26 2026-12-24 2026-12-25 2026-12-31"
for h in $HOLIDAYS_2026; do
  [[ "$NEXT_DAY" == "$h" ]] && { echo "ABORT: Phase B forbidden on holiday eve ($NEXT_DAY is a US federal holiday). Reschedule."; exit 1; }
done
echo "Phase B day-of-week + holiday checks passed."
```

If a Phase-B-class operation is unavoidable on a Fri/weekend (e.g., security incident), require a second admin temporarily added to the bypass list with a known-revoke time — document the exception in an explicit ADR before executing.

### A23 — Creative-agent commit pin freshness check (Round 11 D32 tripwire)

D32 requires the pinned creative-agent commit `ca70dd1e2a6c` to be <3 months old at PR-3 author time. Verify:

```bash
# Resolve commit timestamp
COMMIT_TS=$(curl -sH "Accept: application/vnd.github+json" \
  https://api.github.com/repos/adcontextprotocol/adcp/commits/ca70dd1e2a6c \
  | jq -r '.commit.committer.date')
COMMIT_AGE_DAYS=$(( ($(date +%s) - $(date -d "$COMMIT_TS" +%s 2>/dev/null || date -j -f "%Y-%m-%dT%H:%M:%SZ" "$COMMIT_TS" +%s)) / 86400 ))
echo "creative-agent pin commit age: $COMMIT_AGE_DAYS days"
[[ "$COMMIT_AGE_DAYS" -le 90 ]] || { echo "WARNING: pin >3 months old; bump to current main and re-verify env-var schema before authoring PR 3 commit 9."; exit 0; }
```

If the pin is stale, bump to a current commit AND verify the 10 env vars in D32 still match upstream's expected schema:

```bash
curl -sL https://github.com/adcontextprotocol/adcp/archive/<NEW_SHA>.tar.gz | tar xz -C /tmp/adcp-pinned --strip-components=1
grep -RhE 'process\.env\.[A-Z_]+' /tmp/adcp-pinned/src/ | sort -u
```

If new required env vars surfaced, file an issue and update D32 before authoring PR 3 commit 9.

### A24 — Phase B dry-run on sandbox repo (Round 13 addition; BLOCKER for PR 3 Phase B)

**Why:** Phase B is irreversible without snapshot rollback. R39 (snapshot SPOF) mitigation requires that the rollback procedure works. Plan currently has documentation but no evidence of execution. Comprehensive review (Round 13) flagged this as a blocker.

**What:**
1. Create a throwaway GitHub repo (`<your-org>/salesagent-phase-b-sandbox` or fork)
2. Set up branch protection on `main` with 3-5 dummy required check names
3. Run `bash scripts/flip-branch-protection.sh --target sandbox-repo --dry-run` (extend script if needed for sandbox targeting)
4. Capture pre-flip snapshot
5. Execute actual PATCH against sandbox
6. Verify protection mutated as expected (run `gh api repos/<org>/<repo>/branches/main/protection`)
7. Execute rollback PATCH using snapshot
8. Verify protection restored
9. Record execution evidence in `escalations/phase-b-dry-run-evidence.md` (sandbox URL + before/after JSON)

**Status:** [ ] Complete (admin) — record date + sandbox repo URL

**Blocks:** PR 3 Phase B execution. Without A24 complete, do not proceed.

**Setup prerequisite — `phase-b-in-progress` label (R23 mutex; Round 14 deep-verify Gap 3 bonus B6):**
The `flip-branch-protection.sh` script (lines 36-50) opens a GitHub Issue with label `phase-b-in-progress` to enforce a mutex during Phase B execution. The label must exist one-time before any Phase B run (also referenced in `pr3-phase-b-checklist.md`):

```bash
# Create the label (one-time setup):
gh label create phase-b-in-progress --color FFA500 --description="Phase B atomic-flip in progress; do not merge or admin-action" 2>/dev/null || true

# Verify:
gh label list | grep -q phase-b-in-progress && echo "label exists" || echo "MISSING"
```

The script will create an Issue using this label at flip time; the issue auto-closes on success. If you see an unexpected issue with this label appear in your inbox during Phase B execution, this is the mutex doing its job — do not close it manually until the script completes.

**Status:** [ ] Label exists in repo

### A25 — Confirm hardware MFA on @chrishuie + document SPOF acceptance (BLOCKER for PR 3 Phase B)

**Why:** R20 + R30 are both CRITICAL severity, both depend on @chrishuie not being compromised AND not being unavailable for 5 weeks. Single human in the bypass path is single point of failure for both governance AND incident response. Under the solo+agents execution model, recruiting a second maintainer is not an available path (agents are not maintainers); the only mitigation is hardware MFA + a documented SPOF-acceptance runbook for the unavailability case. Comprehensive review (Round 13) elevated this from "out of scope (organizational)" to in-scope blocker.

**Action:**
- Confirm @chrishuie's GitHub account has hardware MFA enabled (FIDO2 / hardware key).
- Document: model, registration date, recovery procedure.
- Document SPOF acceptance in ADR-002 (already drafted; lifts in PR 1).

**Recovery for the unavailability case:** see `runbooks/E4-account-lockout-recovery.md` (Round 14 M7 — covers the "what if @chrishuie unavailable" case: rollout pause, escalation contact, who has authority to revert).

**Status:** [ ] Complete (admin) — record evidence

**Blocks:** PR 3 Phase B execution.

### A19 — Clean stale `tests/migration/__pycache__/` bytecode (Round 10 sweep)

Round 10 audit surfaced misleading `.pyc` files at `tests/migration/__pycache__/` for `test_a2a_agent_card_snapshot`, `test_mcp_tool_inventory_frozen`, `test_openapi_byte_stability`. These are leftover from a prior local checkout of the v2.0 branch (`feat/v2.0.0-flask-to-fastapi`) — the `.py` source files exist on that branch (commits `a2d3b350`, `c736f6c5`, `def4a4ea`), NOT on main or HEAD.

Verification:

```bash
git ls-tree main -- tests/migration/    # expect: empty (directory not tracked)
git ls-tree HEAD -- tests/migration/    # expect: empty
ls tests/migration/__pycache__/ 2>/dev/null   # may show stale .pyc files
```

Cleanup (one-time, on every contributor machine that has touched the v2.0 branch locally):

```bash
rm -rf tests/migration/__pycache__/
# Followed by, if the empty parent dir bothers you:
rmdir tests/migration/ 2>/dev/null || true
```

The contract-snapshot tests themselves arrive on main when v2.0 phase PRs land — they are NOT this rollout's responsibility per D20 (Path 1 sequencing). Cleanup is a one-time admin step to silence false-positive on-disk artifacts; not part of any PR.

## Agent-runnable preparation steps

These can be delegated to the executor agent at the start of PR 1's session.

### P1 — Re-verify drift catalog evidence

Issue #1234 was written 2026-04-23/24. If a substantial period has passed, re-verify line numbers:

```bash
# Drift evidence baseline
grep -n 'adcp==3.2.0' .pre-commit-config.yaml          # expect: line 301
grep -n 'rev: 25.1.0' .pre-commit-config.yaml          # expect: line 276 (psf/black)
grep -n '"adcp>=' pyproject.toml                       # expect: line 10
grep -n 'UV_VERSION:' .github/workflows/test.yml       # expect: line 12
grep -n 'postgres:15' .github/workflows/test.yml       # expect: line 135
grep -n 'postgres:16' .github/workflows/test.yml       # expect: line 196
grep -n 'GEMINI_API_KEY' .github/workflows/test.yml    # expect: line 342 the fallback
```

If any line numbers have drifted, update the relevant per-PR spec evidence pointers before authoring.

### P2 — Capture pydantic.mypy error baseline

**Mandatory before PR 2 is authored** per D13:

```bash
uv run mypy src/ --config-file=mypy.ini > .mypy-baseline.txt 2>&1
echo "errors: $(grep -c 'error:' .mypy-baseline.txt)"
grep -oE 'error: \[[a-z-]+\]' .mypy-baseline.txt | sort | uniq -c | sort -rn | head -10
```

**Today's (effective) baseline:** the pre-commit mypy hook runs without `pydantic.mypy` (silently disabled). PR 2 makes it active. Capturing now lets PR 2 prove it didn't add errors. If `.mypy-baseline.txt` shows `error: 0`, no fixes needed; if non-zero, fix in PR 2 (D13).

### P3 — Capture zizmor pre-flight

**Mandatory before PR 1 is authored** per D10:

```bash
uvx zizmor .github/workflows/ --min-severity medium > .zizmor-preflight.txt 2>&1 || true
cat .zizmor-preflight.txt
```

Expected ~35 findings: 2 dangerous-triggers (pull_request_target on pr-title-check.yml + ipr-agreement.yml — legitimate, allowlist), 2 excessive-permissions (test.yml + pr-title-check.yml), 30 unpinned-uses (every action ref is tag-pinned not SHA-pinned), 0-3 template-injection.

PR 1 must address each finding via fix or documented allowlist.

### P4 — Verify PR #1221 file-overlap matrix

```bash
gh api repos/prebid/salesagent/pulls/1221/files --paginate \
  --jq '.[] | select(.filename | test("pyproject.toml|pre-commit|github/workflows|CLAUDE.md|Dockerfile|docker-compose")) | .filename'
```

Cross-reference against [00-MASTER-INDEX.md](00-MASTER-INDEX.md) §"File overlap matrix" expectations. If new overlap surfaces (e.g., v2.0 starts modifying `.pre-commit-hooks/` files PR 4 was going to touch), update PR 4 spec.

### P5 — Snapshot disk-truth for guards

```bash
ls tests/unit/test_architecture_*.py | sort > .claude/notes/ci-refactor/guards-on-disk.txt
ls tests/unit/test_no_toolerror_in_impl.py tests/unit/test_transport_agnostic_impl.py tests/unit/test_impl_resolved_identity.py 2>&1 >> .claude/notes/ci-refactor/guards-on-disk.txt
wc -l .claude/notes/ci-refactor/guards-on-disk.txt
```

Confirms D18's count. Should show 26-27 entries (depending on whether the `test_get_media_buys_architecture.py` and similar one-offs are filtered).

### P6 — Verify ui-tests is still actively used

```bash
grep -rn 'ui-tests\|tests/ui' tox.ini Makefile run_all_tests.sh scripts/ 2>&1 | head
ls tests/ui/
```

D14 assumes `tests/ui/` is live. If the directory has been removed since 2026-04, demote D14 to "delete the extras block" instead of "migrate."

### P7 — Post-PR-4 verify `default_install_hook_types` directive (D31 / R33 detection)

After PR 4 lands, before merging any subsequent PR, verify the pre-push hook tier is actually installed on contributor machines:

```bash
# 1. Verify .pre-commit-config.yaml has the directive (D31)
grep -E '^default_install_hook_types:.*pre-commit.*pre-push' .pre-commit-config.yaml

# 2. Simulate a fresh contributor clone — does `pre-commit install` (no flags) auto-install both?
TMPDIR=$(mktemp -d)
git clone . "$TMPDIR/scratch"
cd "$TMPDIR/scratch"
uv run pre-commit install  # no --hook-type flag
ls .git/hooks/ | grep -E '^(pre-commit|pre-push)$' | wc -l   # expect: 2
cd - && rm -rf "$TMPDIR"
```

Mitigates R33 (Critical, High probability — pre-push tier silently disabled). If the directive is missing or contributors aren't getting both hooks, file a P0 follow-up before authoring PR 5.

### P8 — Mypy warm-time pre-flight measurement (PR 4 fallback gate)

Per PR 4 spec, mypy moves to pre-push (D27) as the 10th hook ONLY if warm wall-clock is ≤20s. Measure before authoring PR 4 commit 5:

```bash
uv sync --group dev   # warm cache
uv run mypy src/ --config-file=mypy.ini > /dev/null 2>&1   # warm
T1=$(date +%s%N)
uv run mypy src/ --config-file=mypy.ini > /dev/null 2>&1
T2=$(date +%s%N)
echo "Warm mypy wall-clock: $(( (T2 - T1) / 1000000 ))ms"
```

If >20000ms, do NOT include mypy in the 10 pre-push moves; instead activate the P8 fallback in PR 4 spec (move `no-hardcoded-urls` to pre-push as the 10th swap). Document the decision in the PR 4 PR description.

### P9 — Stale-string drift guard (Round 12 D46)

Each sweep round adds new content to per-PR specs. Historically the propagation across non-spec surfaces (verify scripts, briefings, executor template, admin scripts, architecture.md) trails by 1-2 rounds, leading to stale strings like "11 frozen", "D1-D28", "R1-R10" misleading executors and reviewers. Per **D46**, run before declaring a sweep round complete:

```bash
bash .claude/notes/ci-refactor/scripts/check-stale-strings.sh
```

Exit 0 = corpus is clean of propagation drift across production-facing surfaces (scripts, briefings, templates, per-PR specs). Exit 1 = stale strings found; fix before declaring the sweep complete. Allowlist (files explicitly tagged as audit-trail / history-marker) is documented in the script:

- `RESUME-HERE.md` (sweep audit-trail sections)
- `architecture.md` (banner declares stale; forwards to D30)
- `REFACTOR-RUNBOOK.md` (superseded; kept as audit trail)
- `research/` (read-only audit trail)
- `03-decision-log.md` (decision history may cite older counts in change-log entries)

If a script outside the allowlist contains a stale string, the next sweep MUST update it. New patterns are added to the script's `PATTERNS` array as decisions evolve (e.g., when a 15th frozen name is proposed, "14 frozen" becomes a stale-string pattern).

**Round 13 patterns extension:** P9 patterns extended in Round 13 to cover '11 check names', '11 required checks', '0.11.6', '33 effective', '9 to pre-push', '73-row', D1-D40 through D1-D47, R1-R37 through R1-R44.

### P10 — Capture pre-commit hook count baseline (Round 14 M5)

Per **Round 14 M5** (deep-dive analysis), PR 4's hook math `36 − 13 − 10 − 1 = 12` has zero headroom. A contributor adding a hook between PR 1 author start and PR 4 merge causes silent math overflow. Git log of `.pre-commit-config.yaml` shows ~1 mod every ~6 weeks → 5-week rollout window has ~70% drift probability.

**Mandatory before PR 1 authoring:**

```bash
bash .claude/notes/ci-refactor/scripts/capture-hook-baseline.sh
```

Writes `.claude/notes/ci-refactor/.hook-baseline.txt`. `verify-pr4.sh` reads this file to fail noisily if the baseline shifted. If drift detected mid-rollout, see `runbooks/PR4-partial-deletion-recovery.md` for the decision tree.

**Status:** [ ] Complete — `.hook-baseline.txt` exists with `effective_commit_stage: 36`

## Sign-off

Before PR 1 is authored, this file should be marked complete:

- [ ] A1 — branch protection snapshot saved
- [ ] A2 — required-checks list captured
- [ ] A3 — private vulnerability reporting enabled
- [ ] A4 — Dependabot enabled
- [ ] A5 — CodeQL set to "Advanced"
- [ ] A6 — PR #1217 fate decided
- [ ] A7 — coverage baseline captured
- [ ] A8 — pre-commit latency baseline captured
- [ ] A9 — OpenSSF Scorecard baseline captured
- [ ] A10 — CSRF advisory plan confirmed
- [ ] A11 — `allow_auto_merge` toggle audited (must be `false`)
- [ ] A12 — Dependabot PR queue drained to ≤2 open
- [ ] A13 — mypy.ini plugin block snapshotted
- [ ] A14 — @chrishuie bypass-list addition feasibility confirmed
- [ ] A15 — `develop` branch fate decided (delete / protect symmetrically / drop-from-triggers)
- [ ] A16 — xdist soak completed (≥3 `-n 4` runs + ≥1 `-n auto` run, timings captured)
- [ ] A17 — AST guard pre-existing-violation audit run (each new guard executed; remediate-or-allowlist choice documented)
- [ ] A18 — mypy plugin canary verified (`tests/unit/_pydantic_mypy_canary.py` triggers expected error)
- [ ] A19 — `tests/migration/__pycache__/` cleaned (Round 10; one-time stale-bytecode removal)
- [ ] A20 — In-flight fork PR snapshot captured + coordination comments posted (Round 11; before Phase B)
- [ ] A21 — CODEOWNERS + dependabot.yml syntax validated post-PR-1-merge (Round 11; R41 mitigation)
- [ ] A22 — Phase B day-of-week + holiday-eve guard checked (Round 11; D45 enforcement)
- [ ] A23 — Creative-agent commit pin freshness verified (<3 months old) before authoring PR 3 (Round 11; D32 tripwire)
- [ ] A24 — Phase B dry-run on sandbox repo executed; evidence recorded (Round 13; BLOCKER for PR 3 Phase B)
- [ ] A25 — Hardware MFA confirmed on @chrishuie + SPOF acceptance documented (Round 13/14; BLOCKER for PR 3 Phase B)
- [ ] P7 — `default_install_hook_types` directive verified post-PR-4 (D31 / R33 detection — only relevant after PR 4 lands)
- [ ] P8 — mypy warm-time measured before PR 4 commit 5 (gate for pre-push migration vs fallback)
- [ ] P9 — `check-stale-strings.sh` exit 0 (Round 12 D46 — propagation discipline; run before any sweep round closes)
- [ ] P10 — Hook count baseline captured (`.hook-baseline.txt` shows 36 effective; Round 14 M5)
- [ ] P1 — drift evidence re-verified (or noted as still-current)
- [ ] P2 — pydantic.mypy baseline captured (`.mypy-baseline.txt`)
- [ ] P3 — zizmor pre-flight captured (`.zizmor-preflight.txt`)
- [ ] P4 — PR #1221 overlap matrix verified
- [ ] P5 — guards-on-disk snapshot saved
- [ ] P6 — ui-tests usage verified
