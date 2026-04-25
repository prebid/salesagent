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

This is the list PR 3 Phase B will atomically replace with the 11 frozen names from D17.

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

**Risk:** silent governance bypass. Any PR merged to `develop` escapes the 11 frozen required checks.

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
- [ ] P1 — drift evidence re-verified (or noted as still-current)
- [ ] P2 — pydantic.mypy baseline captured (`.mypy-baseline.txt`)
- [ ] P3 — zizmor pre-flight captured (`.zizmor-preflight.txt`)
- [ ] P4 — PR #1221 overlap matrix verified
- [ ] P5 — guards-on-disk snapshot saved
- [ ] P6 — ui-tests usage verified
