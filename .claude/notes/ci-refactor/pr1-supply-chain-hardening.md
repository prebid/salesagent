# PR 1 — Supply-chain hardening

**Drift items closed:** PD3, PD4, PD5, PD6, PD7, PD13, PD14, PD15, PD23, PD24
**Estimated effort:** 2.5 days (Path C CodeQL — D10) / 3 days (Path A) / 5.5 days (Path B)
**Depends on:** pre-flight checklist (01-pre-flight-checklist.md) complete
**Blocks:** PR 2, PR 3
**Decisions referenced:** D1, D2, D5, D10, D12, D15, D16, D17, D21

## Scope

Pure defense, mostly additive. Establishes the governance baseline (CODEOWNERS, dependabot, SECURITY.md, CONTRIBUTING.md), the security-scanning layer (zizmor, CodeQL, pip-audit), and SHA-pins all external pre-commit hooks. Zero behavior change for existing tests; main is mergeable on landing.

Per D10 (Path C), CodeQL is **advisory** for 2 weeks then flips to gating. zizmor is gating from day 1 (its findings are RCE-class).

## Out of scope

- Mypy / black local-hook migration → PR 2
- CI workflow restructure → PR 3
- Pre-commit hook architecture → PR 4
- Version anchor consolidation → PR 5
- CSRF middleware (`Flask-WTF CSRFProtect`) → deferred to v2.0 phases (the v2.0 branch already adds `src/admin/csrf.py`)
- Any change under `src/` (production code untouched)
- `harden-runner` adoption (Fortune-50 pattern, file as PR 6 follow-up per D25; v2.16.0+ pin floor)

> **Note on ADR location.** ADR-001, ADR-002, and ADR-003 are embedded inline in this PR 1 spec (authored by commits 7 and 11 — see §Embedded ADR-002 below and the inline ADR-001 / ADR-003 bodies in their respective commit sections). They are NOT staged as standalone draft files in `drafts/` because their text in this spec is the canonical source until extraction at commit time. ADR-004 onward exist as standalone drafts because they were authored in earlier planning rounds. See `drafts/README.md` for the inventory split.

> **Pre-flight P5 (new) — empirical check.** Before authoring any commit, verify `.github/` directory exists and `pyproject.toml` has the expected sections. Round 9 verification found 6 cases where plan assumptions about current code state were wrong; Rule 19 of the executor prompt now requires this empirical check.

## Internal commit sequence

ORDER IS LOAD-BEARING. Bisect-friendly: each commit is a self-contained unit reviewers can revert independently.

### Commit 1 — `docs: add SECURITY.md, [project.urls], description`

Files:
- `SECURITY.md` (new, ~80 lines)
- `pyproject.toml` (modify L4 description; add `[project.urls]` block)

Pure docs/metadata. Closes PD5, PD23.

**`[project.urls]` block to add** (5 keys; matches `scripts/verify-pr1.sh` expectations):

```toml
[project.urls]
Homepage = "https://github.com/prebid/salesagent"
Repository = "https://github.com/prebid/salesagent"
Issues = "https://github.com/prebid/salesagent/issues"
Documentation = "https://github.com/prebid/salesagent/tree/main/docs"
Changelog = "https://github.com/prebid/salesagent/blob/main/CHANGELOG.md"
```

Replace the placeholder description (`"Add your description here"`) with: `"Prebid Sales Agent — AdCP-compliant MCP/A2A/REST server for ad inventory orchestration."`

Verification:
```bash
test -s SECURITY.md
[[ $(wc -l < SECURITY.md) -ge 30 ]]
grep -qiE 'private vulnerability|security advisory' SECURITY.md
grep -qE '\[project\.urls\]' pyproject.toml
for k in Homepage Repository Issues Documentation Changelog; do
  grep -qE "^${k} = \"https" pyproject.toml || { echo "missing [project.urls].${k}"; exit 1; }
done
! grep -qE 'description = "Add your description here"' pyproject.toml
```

### Commit 2 — `docs: rewrite root CONTRIBUTING.md as thin pointer (D21 revised P0 sweep)`

Files:
- `CONTRIBUTING.md` (rewrite from 20 lines → **~30-line thin pointer**)
- `docs/development/contributing.md` (594 lines — **KEEP UNCHANGED as canonical contributor guide**)

Closes PD7. Per **D21 (revised 2026-04-25 P0 sweep):** `docs/development/contributing.md` (594 lines) is canonical content; root `CONTRIBUTING.md` is a thin pointer (~30 lines: 6 conventional-commit prefixes inline + "See `docs/development/contributing.md` for full contributor workflow." + `pre-commit install --hook-type pre-commit --hook-type pre-push` instruction). Earlier framing ("root canonical, ~120 lines, delete docs/development version") was reversed in the P0 sweep after disk-truth audit found docs/development/contributing.md was substantive (594 lines), not a thin duplicate.

**Thin pointer body** (verbatim — author this exactly):

```markdown
# Contributing to Prebid Sales Agent

Thanks for your interest in contributing! Full contributor workflow lives at:
**[`docs/development/contributing.md`](docs/development/contributing.md)** (canonical).

## Quick start

1. Fork and clone the repo.
2. Install dev dependencies: `uv sync --group dev`
3. Install both pre-commit hook stages:
   ```bash
   pre-commit install --hook-type pre-commit --hook-type pre-push
   ```
4. See `docs/development/contributing.md` for branch naming, testing, PR review process.

## PR title format (Conventional Commits)

PR titles MUST use one of these prefixes (release-please uses them to generate changelogs):

- `feat:` — new functionality (Features section)
- `fix:` — bug fix (Bug Fixes section)
- `refactor:` — code refactoring (Code Refactoring section)
- `docs:` — documentation only
- `chore:` — maintenance / dependencies (hidden from changelog)
- `perf:` — performance improvements

Without a recognized prefix, the change ships but won't appear in release notes.

## Reporting security issues

See [SECURITY.md](SECURITY.md) — please use private vulnerability reporting, NOT public issues.
```

**Note:** this is NOT an authoring task — it is a near-verbatim lift of the block above. Budget 5-10 minutes. Do NOT expand `docs/development/contributing.md`; it stays at its current 594 lines.

Verification:
```bash
# Root pointer is short (was 20, becomes ~30)
[[ $(wc -l < CONTRIBUTING.md) -ge 20 && $(wc -l < CONTRIBUTING.md) -le 60 ]]
grep -q 'docs/development/contributing.md' CONTRIBUTING.md
grep -q 'pre-commit install --hook-type pre-commit --hook-type pre-push' CONTRIBUTING.md
grep -qE '(feat|fix|refactor|docs|chore|perf):' CONTRIBUTING.md
# Canonical docs/development/contributing.md is preserved at full size
[[ -f docs/development/contributing.md ]]
[[ $(wc -l < docs/development/contributing.md) -ge 500 ]]
```

### Commit 3 — `chore: add CODEOWNERS`

Files:
- `.github/CODEOWNERS` (new, ~30 lines)

Activates review-routing on subsequent commits. Closes PD4.

Verification:
```bash
test -s .github/CODEOWNERS
grep -qE '^\*\s+@chrishuie' .github/CODEOWNERS
grep -qE '^/\.pre-commit-config\.yaml\s+@chrishuie' .github/CODEOWNERS
grep -qE '^/\.github/.*@chrishuie' .github/CODEOWNERS
grep -qE '^/SECURITY\.md\s+@chrishuie' .github/CODEOWNERS
# Test-infra & ratchet-baseline scope (representative samples — full list in spec)
grep -qE '^/Makefile\s+@chrishuie' .github/CODEOWNERS
grep -qE '^/tests/conftest\.py\s+@chrishuie' .github/CODEOWNERS
grep -qE '^/\.duplication-baseline\s+@chrishuie' .github/CODEOWNERS
```

### Commit 4 — `ci: add dependabot.yml (no auto-merge)`

Files:
- `.github/dependabot.yml` (new, ~80 lines)

Closes PD6. Includes `ignore: adcp` per D16.

Verification:
```bash
yamllint -d relaxed .github/dependabot.yml || pip install yamllint && yamllint -d relaxed .github/dependabot.yml
for eco in pip pre-commit github-actions docker; do
  grep -qE "package-ecosystem: \"?${eco}\"?" .github/dependabot.yml
done
grep -qE 'dependency-name: "?adcp"?' .github/dependabot.yml
grep -qE 'dependency-name: "?googleads"?' .github/dependabot.yml
! grep -qE 'auto-?merge' .github/dependabot.yml
```

### Commit 5 — `ci: add security.yml (zizmor + pip-audit)`

Files:
- `.github/workflows/security.yml` (new)

Closes PD13.

Verification:
```bash
test -f .github/workflows/security.yml
yamllint -d relaxed .github/workflows/security.yml
grep -qE '^permissions:\s*\{?\s*\}?' .github/workflows/security.yml
grep -q 'zizmor' .github/workflows/security.yml
grep -q 'pip-audit' .github/workflows/security.yml
```

### Commit 6 — `ci: add codeql.yml (advisory)`

Files:
- `.github/workflows/codeql.yml` (new)
- `.github/codeql/codeql-config.yml` (new, optional but recommended)

Closes PD14. Per D10 (Path C), this workflow runs but does NOT gate merges for the first 2 weeks.

Verification:
```bash
test -f .github/workflows/codeql.yml
yamllint -d relaxed .github/workflows/codeql.yml
grep -qE '^permissions:' .github/workflows/codeql.yml
grep -qE 'security-events:\s+write' .github/workflows/codeql.yml
grep -qE 'language: python' .github/workflows/codeql.yml
grep -qE 'security-extended' .github/workflows/codeql.yml
```

### Commit 7 — `docs: add ADR-001 (single-source pre-commit deps) and ADR-002 (solo-maintainer bypass)`

Files:
- `docs/decisions/adr-001-single-source-pre-commit-deps.md` (new — embed body below)
- `docs/decisions/adr-002-solo-maintainer-bypass.md` (new, full text in §Embedded ADR-002 below)
- `docs/decisions/.placeholder` removed if it exists (`mkdir -p docs/decisions` creates the directory implicitly)

ADR-001 is referenced by PR 2 but committed here so the directory and pattern exist before PR 2.

**Embedded ADR-001 body** (lift verbatim into `docs/decisions/adr-001-single-source-pre-commit-deps.md`):

```markdown
# ADR-001 — uv.lock as single source of truth for pre-commit deps

## Status
Accepted (2026-04 — finalized PR 2 of issue #1234)

## Context
`.pre-commit-config.yaml` previously declared external project dependencies
under each hook's `additional_dependencies` block (e.g., `factory-boy`,
`sqlalchemy[mypy]`). This produces a second source of dependency truth that
drifts from `pyproject.toml` + `uv.lock` whenever either side updates without
the other being mirrored.

## Decision
1. `uv.lock` (with `pyproject.toml`'s `[dependency-groups].dev`) is the single
   source of truth for ALL Python dev-time dependencies.
2. `.pre-commit-config.yaml` external repos (e.g., `psf/black`, `mirrors-mypy`)
   are replaced by `local` hooks that invoke `uv run <tool>` at
   `language: system`. The hooks resolve against the project venv.
3. The `additional_dependencies` field is permitted ONLY for `types-*` stub
   packages (e.g., `types-PyYAML`) that pre-commit's isolated env genuinely
   needs and that don't belong in dev deps.
4. A structural guard (`tests/unit/test_architecture_pre_commit_no_additional_deps.py`)
   enforces this: any `additional_dependencies` value that resolves to a
   project-level dependency name fails the build.

## Consequences
- Pre-commit and CI/local-make-quality see identical dependency versions.
- Hook execution speed is slightly slower (cold venv hit) but warm runs are
  comparable.
- `mirrors-mypy` migration: the underlying motivation is NOT deprecation
  (mirrors-mypy is actively maintained) but isolated-env import resolution —
  see [Jared Khan's analysis](https://jaredkhan.com/blog/mypy-pre-commit) and
  [mypy#13916](https://github.com/python/mypy/issues/13916).

## Tripwire
If a future hook genuinely needs a project-version dep that's also in
pyproject.toml AND isolation is required, the only acceptable answer is
to extract the hook's logic into a script under `.pre-commit-hooks/` invoked
via `language: system` — never re-add `additional_dependencies` for project
libs.
```

Verification:
```bash
test -f docs/decisions/adr-001-single-source-pre-commit-deps.md
test -f docs/decisions/adr-002-solo-maintainer-bypass.md
grep -q '## Status' docs/decisions/adr-001-single-source-pre-commit-deps.md
grep -q '## Status' docs/decisions/adr-002-solo-maintainer-bypass.md
```

### Commit 8 — `chore: pre-commit autoupdate --freeze (SHA-pin all external hooks)`

Files:
- `.pre-commit-config.yaml` (modify lines 262, 275, 281, 289)

Closes PD3. Per D12, bumps each hook to its latest tag and rewrites `rev:` to a 40-char SHA with `# frozen: v<tag>` trailing comment.

**Procedure (run on a scratch branch first to review the diff before committing):**

```bash
git checkout -b chore/sha-freeze-preview
# Pin individual hooks, NOT all. Hold psf/black at 25.1.0 — autoupdate would jump
# to 26.3.0 (2026-style) and trigger a global reformat. The 26.x reformat is
# deferred to ADR-008 (post-#1234 follow-up).
uv run pre-commit autoupdate --freeze \
  --repo https://github.com/pre-commit/pre-commit-hooks \
  --repo https://github.com/astral-sh/ruff-pre-commit \
  --repo https://github.com/pre-commit/mirrors-mypy
# psf/black NOT in the --repo list — held at 25.1.0 per ADR-008 deferral.
git diff .pre-commit-config.yaml > /tmp/sha-freeze.diff
cat /tmp/sha-freeze.diff   # review the 3 hook bumps (black excluded)
# If any bump breaks pre-commit run --all-files, hold individual hooks at previous version
uv run pre-commit run --all-files
# If clean, cherry-pick or replay onto PR 1 branch
```

Verification:
```bash
# SHA-freeze regex: relaxed to `# frozen: \S+` since black ships `25.1.0` (no `v` prefix)
# while pre-commit-hooks ships `v6.0.0`. Strict regex `# frozen: v<tag>` would fail
# on correctly-frozen black entries.
[[ $(grep -E '^\s+rev:' .pre-commit-config.yaml | grep -vcE 'rev: [a-f0-9]{40}\s+# frozen: \S+') == "0" ]]
# Black is held at 25.1.0 (ADR-008 deferral): verify it's still on the original tag-pin
grep -qE '^\s+rev:\s+25\.1\.0\s*$' .pre-commit-config.yaml
uv run pre-commit run --all-files
```

### Commit 9 — `ci: pin GitHub Actions to SHAs, add permissions, and persist-credentials:false`

Files:
- `.github/workflows/test.yml` (modify all `uses:` lines, add `permissions: {}` top-level, set `persist-credentials: false` on all `actions/checkout` steps)
- `.github/workflows/pr-title-check.yml` (same)
- `.github/workflows/release-please.yml` (same)
- `.github/workflows/ipr-agreement.yml` (same)

Addresses zizmor's `unpinned-uses` and `excessive-permissions` findings (~32 expected from pre-flight P3). Also closes the OSSF Scorecard `Token-Permissions` gap by ensuring no `actions/checkout` invocation persists credentials in `.git/config` (default behavior leaks the GITHUB_TOKEN to subsequent steps and any artifact they upload — see [actions/checkout#2312](https://github.com/actions/checkout/issues/2312)). Closes PD15a (SHA-pin scope) and PD15b (workflow permissions remainder).

**Reference count** (verified 2026-04-25): 23 total `uses:` references across 4 workflows (release-please.yml=6, test.yml=15, pr-title-check.yml=1, ipr-agreement.yml=1) — not 24 as estimated in `research/empirical-baseline.md` (one fewer site to pin).

For each `uses: actions/<name>@v<X>` reference, replace with `uses: actions/<name>@<40-char-sha>  # v<X>`. Persist the resolved SHAs as a committed artifact at `.github/.action-shas.txt` so PR 3 commit 5 can reuse them without re-running the loop.

Mechanical operation — generate the SHAs in batch:

```bash
# For each unique action ref, fetch its SHA at the pinned tag
: > .github/.action-shas.txt
for ref in $(grep -RhoE 'uses: [^ ]+' .github/workflows/ | sort -u | sed 's/uses: //'); do
  case "$ref" in
    *@v*)
      tool=${ref%@*}
      tag=${ref#*@}
      sha=$(gh api repos/$tool/git/refs/tags/$tag --jq '.object.sha')
      # If it's a tag object, dereference to commit
      if [[ $(gh api repos/$tool/git/tags/$sha --jq '.object.type' 2>/dev/null) == "commit" ]]; then
        sha=$(gh api repos/$tool/git/tags/$sha --jq '.object.sha')
      fi
      printf '%s\t%s\t%s\n' "$ref" "$sha" "$tag" >> .github/.action-shas.txt
      ;;
  esac
done
sort -o .github/.action-shas.txt .github/.action-shas.txt
```

Apply via `sed` or manual edits. PR 3 commit 5 reads `.github/.action-shas.txt` instead of re-running this loop.

**Add `persist-credentials: false` to every `actions/checkout` step:**

```yaml
# Before
- uses: actions/checkout@<SHA>  # v4

# After
- uses: actions/checkout@<SHA>  # v4
  with:
    persist-credentials: false
```

If the existing `with:` block has other keys (e.g., `fetch-depth`), append `persist-credentials: false` alongside.

Verification:
```bash
# Every uses: line is SHA-pinned
[[ $(grep -RhoE 'uses: [^ ]+@[a-f0-9]{40}' .github/workflows/ | wc -l) == \
   $(grep -RhoE 'uses: [^ ]+@[^ ]+' .github/workflows/ | grep -vE 'uses: \./' | wc -l) ]]
# Every workflow has top-level permissions
for f in .github/workflows/*.yml; do
  grep -qE '^permissions:' "$f" || { echo "missing top-level perms: $f"; exit 1; }
done
# Every actions/checkout call disables credential persistence
CHECKOUT_USES=$(grep -RhoE 'uses: actions/checkout@' .github/workflows/ | wc -l)
PERSIST_FALSE=$(grep -RnA5 'uses: actions/checkout@' .github/workflows/ | grep -c 'persist-credentials: false')
[[ "$CHECKOUT_USES" -le "$PERSIST_FALSE" ]] || \
  { echo "missing persist-credentials:false on $((CHECKOUT_USES - PERSIST_FALSE)) checkout(s)"; exit 1; }
# SHA artifact is committed and referenced by PR 3
test -s .github/.action-shas.txt
```

### Commit 10 — MOVED to PR 3

The Gemini-fallback unconditional-mock fix (D15, PD24) has been **moved to PR 3** in the
2026-04-25 P0 sweep. Rationale: PR 3 rewrites `test.yml` wholesale (the file is replaced by
`ci.yml` + reusable composite); applying the Gemini fix on `test.yml` in PR 1 just to have
PR 3 rewrite the same region is wasted work. The fix lands as a new commit in PR 3's commit
sequence, applied directly to the new composite `_pytest/action.yml`.

PR 1 commit numbering remains 1-11 with this slot vacant; commit 11 (zizmor) is the next
real commit.

### Commit 11 — `ci: zizmor pre-flight findings — fix or allowlist`

Files:
- `.github/workflows/pr-title-check.yml` (add `# zizmor: ignore[dangerous-triggers]` comment with cite to ADR-003)
- `.github/workflows/ipr-agreement.yml` (same)
- `docs/decisions/adr-003-pull-request-target-trust.md` (new, ~50 lines, explaining why these workflows legitimately use the dangerous trigger)
- `.github/zizmor.yml` (new — embed below)

Per pre-flight P3 (`.zizmor-preflight.txt`), fix all medium+ findings except the legitimate `pull_request_target` workflows.

**`.github/zizmor.yml` content** (lift verbatim — keep `online_audits: true`; do NOT enable both `--annotations` and `--advanced-security` simultaneously per the GHAS exclusivity constraint):

```yaml
# zizmor configuration — workflow security audit
# Sources: https://zizmorcore.github.io/zizmor/configuration/
#
# Dangerous-trigger findings are allowlisted on the two workflows that
# legitimately need pull_request_target (PR-title enforcement, IPR-agreement
# CLA gate). See docs/decisions/adr-003-pull-request-target-trust.md.
rules:
  dangerous-triggers:
    ignore:
      - pr-title-check.yml
      - ipr-agreement.yml
  # Reserve room for forward additions; default-on for everything else.
  unpinned-uses:
    config:
      policies:
        # All actions/* and docker/* must be 40-char SHA-pinned.
        "actions/*": ref-is-pinned
        "docker/*": ref-is-pinned
        "github/*": ref-is-pinned

# Online audits hit GitHub for upstream metadata (license, deprecated state).
online_audits: true
```

Verification:
```bash
uvx zizmor .github/workflows/ --min-severity medium --config .github/zizmor.yml
# Expected exit code: 0 (or 1 with only allowlisted findings)
test -f docs/decisions/adr-003-pull-request-target-trust.md
grep -q '## Status' docs/decisions/adr-003-pull-request-target-trust.md
test -f .github/zizmor.yml
yamllint -d relaxed .github/zizmor.yml
grep -q 'dangerous-triggers' .github/zizmor.yml
```

## Acceptance criteria

From issue #1234 §Acceptance criteria, scoped to PR 1:

- [ ] `.github/CODEOWNERS` exists with `@chrishuie` + critical-path coverage
- [ ] `.github/dependabot.yml` exists; no auto-merge configured
- [ ] `SECURITY.md` exists with GitHub private vuln reporting link + scope
- [ ] `CONTRIBUTING.md` rewritten (>80 lines); references layered model
- [ ] Every external `rev:` in `.pre-commit-config.yaml` is a full SHA with `# frozen: v<tag>` comment
- [ ] `.github/workflows/codeql.yml` exists and runs on PR (advisory per D10)
- [ ] `.github/workflows/security.yml` exists with pip-audit + zizmor
- [ ] `pyproject.toml` has `[project.urls]` and no placeholder description

Plus agent-derived:

- [ ] All workflows have top-level `permissions:` block
- [ ] Every `uses:` action ref is SHA-pinned with a `# v<tag>` comment
- [ ] zizmor reports zero unallowlisted medium+ findings
- [ ] ADR-001, ADR-002, ADR-003 exist and pass `## Status` grep
- [ ] `pre-commit run --all-files` passes
- [ ] `make quality` passes

## Verification (full PR-level)

```bash
bash .claude/notes/ci-refactor/scripts/verify-pr1.sh
```

Inline:

```bash
#!/usr/bin/env bash
set -euo pipefail

# 1. SHA-freeze
echo "[1/8] SHA-freeze..."
[[ $(grep -E '^\s+rev:' .pre-commit-config.yaml | grep -vcE 'rev: [a-f0-9]{40}\s+# frozen: v') == "0" ]]

# 2. CODEOWNERS
echo "[2/8] CODEOWNERS..."
test -s .github/CODEOWNERS
grep -qE '^\*\s+@chrishuie' .github/CODEOWNERS
# Test-infra & ratchet-baseline scope (representative samples; see ADR-004 shrink-only contract)
grep -qE '^/Makefile\s+@chrishuie' .github/CODEOWNERS || { echo "missing /Makefile owner"; exit 1; }
grep -qE '^/tests/conftest\.py\s+@chrishuie' .github/CODEOWNERS || { echo "missing /tests/conftest.py owner"; exit 1; }
grep -qE '^/\.duplication-baseline\s+@chrishuie' .github/CODEOWNERS || { echo "missing /.duplication-baseline owner"; exit 1; }

# 3. SECURITY.md
echo "[3/8] SECURITY.md..."
[[ $(wc -l < SECURITY.md) -ge 30 ]]
grep -qiE 'private vulnerability|security advisory' SECURITY.md

# 4. dependabot
echo "[4/8] dependabot.yml..."
yamllint -d relaxed .github/dependabot.yml
for eco in pip pre-commit github-actions docker; do
  grep -qE "package-ecosystem: \"?${eco}\"?" .github/dependabot.yml
done
grep -qE 'dependency-name: "?adcp"?' .github/dependabot.yml

# 5. Workflows perms
echo "[5/8] workflow perms..."
for f in .github/workflows/*.yml; do
  grep -qE '^permissions:' "$f" || { echo "missing perms: $f"; exit 1; }
done

# 6. SHA-pinned actions
echo "[6/8] SHA-pinned actions..."
total=$(grep -RhoE 'uses: [^ ]+@[^ ]+' .github/workflows/ | grep -vcE 'uses: \./')
sha_pinned=$(grep -RhoE 'uses: [^ ]+@[a-f0-9]{40}' .github/workflows/ | wc -l)
[[ "$total" == "$sha_pinned" ]] || { echo "$((total - sha_pinned)) actions still tag-pinned"; exit 1; }

# 7. ADRs
echo "[7/8] ADRs..."
for adr in adr-001-single-source-pre-commit-deps adr-002-solo-maintainer-bypass adr-003-pull-request-target-trust; do
  test -f "docs/decisions/${adr}.md"
  grep -q '## Status' "docs/decisions/${adr}.md"
done

# 8. zizmor
echo "[8/8] zizmor..."
uvx zizmor .github/workflows/ --min-severity medium

echo "PR 1 verification PASSED"
```

## Risks (scoped to PR 1)

- **R3 — CodeQL findings explode**: mitigation — D10 Path C; advisory not gating
- **R4 — zizmor finds > 50 issues**: mitigation — pre-flight P3 captures count; PR 1 fixes only load-bearing findings; rest goes to follow-up issue
- **R9 — Dependabot deluge week 1**: mitigation — `open-pull-requests-limit: 5`; land on Friday for weekend triage runway

## Rollback plan

PR 1 is mostly additive (8 new files, 4 modified). Revert is atomic:

```bash
git revert -m 1 <PR1-merge-sha>
# admin: pushes via UI; agent does NOT run this command
```

Plus admin actions if needed:
- Disable `.github/workflows/codeql.yml` and `security.yml` runs (UI: Actions → workflow → Disable). They run advisory by default; revert removes the files anyway.
- Close any in-flight Dependabot PRs created during the brief enabled window:
  ```bash
  gh pr list --search "author:app/dependabot is:open" --json number --jq '.[].number' | xargs -I{} gh pr close {}
  ```

Recovery: < 5 minutes for code revert; ≤ 30 minutes including admin cleanup.

## Merge tolerance

- **PR #1217 (adcp 3.12)**: tolerated. PR 1 doesn't touch `adcp` references.
- **v2.0 phase PR landing on `pyproject.toml`**: rebase if it adds/removes `[project.urls]`. Mechanical.
- **v2.0 phase PR landing on `.pre-commit-config.yaml`**: rebase carefully — the `rev:` SHAs from autoupdate-freeze should be preserved; v2.0's hook-list edits apply on top.
- **v2.0 phase PR landing on `.github/workflows/test.yml`**: blocks if v2.0 also rewrites the workflow significantly. Coordinate; otherwise mechanical.

## Coordination notes for the maintainer

These are NOT part of the diff but are required for PR 1's effect:

1. **Before merging**: confirm pre-flight checklist (01-pre-flight-checklist.md) is complete.
2. **After merging, before week 5**: monitor `.zizmor-preflight.txt` follow-ups and CodeQL findings count. End of Week 4: decide gating-vs-advisory per D10 tripwire.
3. **After merging**: configure branch protection bypass for `@chrishuie`. UI path:
   - **Settings → Branches → main** (or "Branch protection rules" → edit `main`)
   - "Require a pull request before merging" → check **"Allow specified actors to bypass required pull requests"**
   - Add `chrishuie`
   - Save
4. **First Dependabot run**: lands within 24h of `dependabot.yml` being merged. Expect 5-13 PRs. Triage over the weekend.

---

# Embedded drafts

The executor agent should lift these into the indicated final locations. Content here is production-ready; only the date stamps need updating.

## Embedded SECURITY.md (commit to repo root)

```markdown
# Security policy

## Supported versions

We support the `main` branch and the most recent tagged release. Older releases
do not receive backported fixes; please upgrade to receive security updates.

| Version        | Supported |
|----------------|-----------|
| `main`         | Yes       |
| Latest release | Yes       |
| Older releases | No        |

## Reporting a vulnerability

Please report vulnerabilities through GitHub's private security advisory channel:

> https://github.com/prebid/salesagent/security/advisories/new

Do not file public issues for suspected vulnerabilities. Public PRs that fix
non-trivial security issues should also be coordinated through the advisory
channel before opening.

A useful report includes:

- A description of the issue and which component is affected (admin UI, MCP
  server, A2A server, GAM adapter, multi-tenant boundary, etc.)
- Reproduction steps or a proof-of-concept
- Impact assessment (data exposure, privilege escalation, denial of service)
- Suggested mitigations if you have them

## Triage SLA

- **Acknowledgement:** within 5 business days of submission.
- **Initial triage:** within 10 business days (severity assessment, scope
  confirmation, owner assigned).
- **Fix timeline:** case-by-case based on severity and scope. Critical issues
  affecting tenant isolation or authentication are prioritized over lower-impact
  findings.

## Scope

In scope:

- Admin UI authentication, session handling, CSRF, SSRF
- MCP server (`/mcp/`) authentication and authorization
- A2A server (`/a2a`) authentication and authorization
- GAM adapter — credential handling, OAuth flows, network isolation
- Mock adapter — only when used in non-test environments by mistake
- Multi-tenant isolation — tenant boundary enforcement, cross-tenant data
  access, subdomain routing
- Creative agent integration — webhook handling, push-notification handlers
- CI and supply-chain — `.pre-commit-config.yaml`, `.github/workflows/`,
  `pyproject.toml`, `uv.lock`, `Dockerfile`, `docker-compose*.yml`,
  `.python-version`

Out of scope:

- Vulnerabilities in third-party dependencies — please report directly to the
  upstream maintainers. We track and update dependencies via Dependabot.
- Theoretical issues without a reproduction or proof-of-concept.
- Findings that require an already-compromised maintainer machine, leaked
  credentials, or other prerequisites equivalent to administrative access.

## CI and hook modification policy

Files that influence what runs on contributor and maintainer machines, or what
gates the merge process, are CODEOWNERS-protected. Changes to any of the
following must be reviewed by `@chrishuie` and discussed for supply-chain
implications before merge:

- `.pre-commit-config.yaml`
- `.github/workflows/`
- `pyproject.toml`, `uv.lock`
- `Dockerfile`, `docker-compose*.yml`
- `.python-version`

External hook references and GitHub Actions are SHA-pinned. PRs that switch a
SHA to a tag, or downgrade SHA pinning to a less-strict form, will be rejected.

## Disclosure timeline

The default coordinated disclosure window is 90 days from the date of the
acknowledgement. We are willing to negotiate this case-by-case based on fix
complexity and the reporter's needs. We do not require a CVE to be assigned
before publishing a fix.
```

## Embedded CODEOWNERS (commit to `.github/CODEOWNERS`)

```
# CODEOWNERS — auto-requests review from @chrishuie on PRs touching listed paths.
# Bypass is configured at branch-protection level (see ADR-002), not here.
#
# Order matters: last matching pattern wins (GitHub semantics).
# Globs use gitignore syntax; trailing slash matches directories recursively.

# ---- Default fallback: maintainer owns everything not otherwise specified ----
*                                       @chrishuie

# ---- CI / build infrastructure (highest review concern) ----
/.github/                               @chrishuie
/.pre-commit-config.yaml                @chrishuie
/.pre-commit-hooks/                     @chrishuie
/scripts/hooks/                         @chrishuie
/setup_hooks.sh                         @chrishuie

# ---- Dependency / runtime pinning ----
/pyproject.toml                         @chrishuie
/uv.lock                                @chrishuie
/.python-version                        @chrishuie
/Dockerfile                             @chrishuie
/docker-compose*.yml                    @chrishuie

# ---- Database migrations (irreversible once merged) ----
/alembic/                               @chrishuie
/alembic.ini                            @chrishuie

# ---- Auth + security surface ----
/src/core/auth*                         @chrishuie
/src/core/security/                     @chrishuie
/SECURITY.md                            @chrishuie
/IPR_POLICY.md                          @chrishuie

# ---- Architecture guards (prevent regressions in invariants) ----
/tests/unit/test_architecture_*.py      @chrishuie

# ---- Test infrastructure & ratchet baselines (shrink-only contract per ADR-004) ----
/Makefile                                @chrishuie
/tox.ini                                 @chrishuie
/mypy.ini                                @chrishuie
/pytest.ini                              @chrishuie
/tests/conftest.py                       @chrishuie
/tests/integration/conftest.py           @chrishuie
/tests/conftest_db.py                    @chrishuie
/tests/factories/                        @chrishuie
/.duplication-baseline                   @chrishuie
/.type-ignore-baseline                   @chrishuie
/.coverage-baseline                      @chrishuie
/.guard-baselines/                       @chrishuie
/tests/unit/.allowlist-*.json            @chrishuie
/tests/unit/obligation_coverage_allowlist.json         @chrishuie
/tests/unit/obligation_test_quality_allowlist.json     @chrishuie
# Note: .coverage-baseline and .guard-baselines/ may not exist yet; their inclusion
# is forward-looking for when PR 3 / PR 4 create them.
```

## Embedded dependabot.yml (commit to `.github/dependabot.yml`)

```yaml
# Issue #1234 rollout. Decisions: weekly grouped, no auto-merge, ignore adcp until #1217.
# All schedules in America/Los_Angeles per maintainer TZ.
version: 2

updates:
  # ──────────────────────────────────────────────────────────────────
  # Python runtime + dev deps (pyproject.toml, uv.lock)
  # ──────────────────────────────────────────────────────────────────
  - package-ecosystem: "pip"
    directory: "/"
    schedule:
      interval: "weekly"
      day: "monday"
      time: "06:00"
      timezone: "America/Los_Angeles"
    open-pull-requests-limit: 5
    labels:
      - "dependencies"
      - "supply-chain"
    commit-message:
      prefix: "chore(deps)"
      include: "scope"
    pull-request-branch-name:
      separator: "/"

    groups:
      python-runtime:
        applies-to: version-updates
        dependency-type: "production"
        update-types: ["minor", "patch"]
      python-dev:
        applies-to: version-updates
        dependency-type: "development"
        update-types: ["minor", "patch"]
      types:
        applies-to: version-updates
        patterns: ["types-*"]
      gcp-stack:
        applies-to: version-updates
        patterns: ["google-*", "grpcio*", "protobuf"]
      security-patches:
        applies-to: security-updates
        patterns: ["*"]

    ignore:
      # Pinned: library default sets GAM API version (pyproject.toml).
      - dependency-name: "googleads"
      # TODO(#1234): remove once #1217 merges — adcp is being manually migrated.
      - dependency-name: "adcp"

  # ──────────────────────────────────────────────────────────────────
  # GitHub Actions (workflow `uses:` SHAs) — supply chain
  # ──────────────────────────────────────────────────────────────────
  - package-ecosystem: "github-actions"
    directory: "/"
    schedule:
      interval: "weekly"
      day: "monday"
      time: "06:00"
      timezone: "America/Los_Angeles"
    open-pull-requests-limit: 5
    labels:
      - "dependencies"
      - "ci"
      - "supply-chain"
    commit-message:
      prefix: "chore(ci)"
    groups:
      actions:
        applies-to: version-updates
        patterns: ["*"]

  # ──────────────────────────────────────────────────────────────────
  # Pre-commit hooks (rev: pins in .pre-commit-config.yaml)
  # GitHub-native ecosystem since 2026-03-10:
  # https://github.blog/changelog/2026-03-10-dependabot-now-supports-pre-commit-hooks/
  # If validation fails on first run, fall back to scheduled
  # peter-evans/create-pull-request workflow (see PR 1 spec §Fallbacks).
  # ──────────────────────────────────────────────────────────────────
  - package-ecosystem: "pre-commit"
    directory: "/"
    schedule:
      interval: "weekly"
      day: "monday"
      time: "06:00"
      timezone: "America/Los_Angeles"
    open-pull-requests-limit: 3
    labels:
      - "dependencies"
      - "pre-commit"
    commit-message:
      prefix: "chore(pre-commit)"
    groups:
      hooks:
        applies-to: version-updates
        patterns: ["*"]

  # ──────────────────────────────────────────────────────────────────
  # Docker base images — monthly is enough; base churn is low.
  # ──────────────────────────────────────────────────────────────────
  - package-ecosystem: "docker"
    directory: "/"
    schedule:
      interval: "monthly"
      day: "monday"
      time: "06:00"
      timezone: "America/Los_Angeles"
    open-pull-requests-limit: 2
    labels:
      - "dependencies"
      - "docker"
      - "supply-chain"
    commit-message:
      prefix: "chore(docker)"
```

### Fallback for the pre-commit ecosystem

If `package-ecosystem: "pre-commit"` is rejected on first run (the GitHub feature might not be GA in this org's plan), replace that block with a scheduled workflow:

```yaml
# .github/workflows/precommit-autoupdate.yml
name: Pre-commit autoupdate
on:
  schedule:
    - cron: '0 13 * * 1'  # Monday 06:00 PT
  workflow_dispatch:

permissions:
  contents: write
  pull-requests: write

jobs:
  autoupdate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@<SHA>  # v4
      - uses: astral-sh/setup-uv@<SHA>  # v4
      - run: uv run pre-commit autoupdate --freeze
      - uses: peter-evans/create-pull-request@<SHA>  # v6
        with:
          commit-message: 'chore(pre-commit): autoupdate frozen SHAs'
          title: 'chore(pre-commit): weekly autoupdate'
          body: 'Automated weekly pre-commit hook SHA updates.'
          branch: 'chore/precommit-autoupdate'
          labels: 'dependencies,pre-commit'
```

## Embedded ADR-002 (commit to `docs/decisions/adr-002-solo-maintainer-bypass.md`)

```markdown
# ADR-002: Solo-maintainer branch protection with bypass

## Status

Accepted (2026-04-25). Implemented in PR 1 of the CI/pre-commit refactor (issue
#1234). Tripwire: 6-month review, or earlier if a second maintainer joins.

## Context

GitHub branch protection on `main` is being introduced as part of issue #1234's
governance layer. The intended posture is: every PR receives CODEOWNERS review,
no force-pushes, no direct commits to main, all required CI checks must pass.

GitHub's branch protection has two structural facts that interact poorly with a
solo maintainer:

1. A PR author cannot approve their own PR. The "approval" review type is
   permanently unavailable to the author.
2. Setting `required_approving_review_count: 0` while requiring CODEOWNERS
   review is a configuration conflict. CODEOWNERS-required-review only takes
   effect when `required_approving_review_count >= 1`.

`@chrishuie` is the sole CODEOWNERS entry per decision 1 of issue #1234. With
naive branch protection the maintainer cannot merge their own PRs. Adding a
second reviewer is explicitly out of scope for this work.

## Decision

Branch protection on `main` is configured as:

- `required_approving_review_count: 1`
- CODEOWNERS review required
- All required CI checks must pass
- No force-pushes, no direct commits, no deletions
- **Bypass list:** `@chrishuie` is granted bypass via "Allow specified actors to
  bypass required pull requests"

The bypass permission lets `@chrishuie` merge a PR they authored once CI is green,
without a second human approval. Other actors — including any future
contributors — remain bound by the standard rules.

## Options considered

**0 reviews + CODEOWNERS required.** Impossible. GitHub rejects this combination
as a settings conflict; CODEOWNERS enforcement requires a non-zero review count.

**1 review + maintainer bypass.** Chosen. Captures the intent (CODEOWNERS still
auto-requests review on every PR, the trail is documented) while permitting the
solo maintainer to ship.

**1 review + second-reviewer requirement.** Rejected. Blocks every PR until a
second maintainer is recruited. That recruitment is intentionally out of scope
for this work and should not gate routine development.

## Consequences

**Positive.**
- Solo maintainer can ship.
- CODEOWNERS still auto-requests review, which documents intent and provides a
  clear handoff path when a second maintainer joins (remove the bypass entry,
  no other config change).
- All other branch-protection invariants — required CI checks, no force-push,
  no direct commits — apply to the maintainer too. The bypass is scoped strictly
  to the review requirement.

**Negative.**
- The bypass partially defeats the CODEOWNERS guarantee for `@chrishuie`'s own
  PRs. Acknowledged. The remaining guarantees (CI, no force-push, public PR
  trail, signed audit log) are the load-bearing controls in a solo-maintainer
  posture.
- The configuration is a transitional posture, not a steady state. It must be
  revisited when a second maintainer is added.

## Tripwire

When a second maintainer is added to CODEOWNERS:

1. Remove `@chrishuie` from the bypass list.
2. Verify `required_approving_review_count: 1` still applies and now requires a
   non-author reviewer.
3. Update this ADR's status to `Superseded by ADR-NNN` and link to the new
   decision.

Independent of that, this decision is reviewed at the 6-month mark (2026-10) to
confirm the solo-maintainer model is still the operating reality.
```

## Embedded ADR-003 (commit to `docs/decisions/adr-003-pull-request-target-trust.md`)

```markdown
# ADR-003: pull_request_target trust boundary for CLA and PR-title workflows

## Status

Accepted (2026-04-25). Implemented in PR 1 of the CI/pre-commit refactor.

## Context

zizmor flags `pull_request_target:` triggers as HIGH severity (`dangerous-triggers`)
because the workflow runs with write-scoped `GITHUB_TOKEN` and reads code from
the PR branch — including from forks. Two of our workflows legitimately use this
trigger:

- `.github/workflows/pr-title-check.yml` — must validate PR titles on fork PRs
  (a feature that fails under `pull_request:` because forks lack write scope to
  post check statuses)
- `.github/workflows/ipr-agreement.yml` — Prebid's IPR check, which posts a
  status check to fork PRs

We cannot drop `pull_request_target:` without breaking fork-PR support, which
is the primary external-contributor flow.

## Decision

Both workflows keep `pull_request_target:` AND adhere to the safe-trigger rules:

1. **No checkout of the PR head branch.** These workflows do NOT `actions/checkout`
   the PR's code; they only read repository metadata (`github.event.pull_request.title`).
2. **No untrusted command execution.** No `${{ github.event.* }}` interpolation
   into shell commands.
3. **Minimum permissions.** Each workflow declares only the permissions it needs.
4. **No reusable-workflow calls** to scripts in the PR branch.

The zizmor allowlist comment is added at the top of each workflow:

```yaml
# zizmor: ignore[dangerous-triggers]
# Justification: ADR-003 — these workflows legitimately use pull_request_target
# for fork-PR support and adhere to the safe-trigger rules.
```

## Options considered

**Drop `pull_request_target:` entirely.** Rejected. Fork PRs would lose CLA and
title validation; that's a larger user-experience regression than the
exfiltration risk this trigger introduces.

**Replace with `pull_request:`.** Rejected. Fork PRs run with read-only token by
default; status posts fail.

**Replace with workflow_run trigger.** Rejected. workflow_run has its own
footguns and adds an indirection that doesn't reduce risk in our use case.

## Consequences

**Positive.**
- Fork-PR support continues to work.
- Trust boundary is documented; zizmor allowlist has a justification.

**Negative.**
- These two workflows are ongoing review surface for the maintainer. Any change
  that adds a checkout step, a shell-interpolation, or a permissions broadening
  must be reviewed for `pull_request_target:` safety.

## Tripwire

If we ever need to add `actions/checkout` to either of these workflows, we MUST:

1. Switch to `pull_request:` trigger (and accept the fork-PR limitations), OR
2. Use `actions/checkout` with an explicit `ref:` that does NOT trust the PR HEAD,
   AND audit all subsequent steps for shell interpolation.

This is non-trivial; consult the GitHub Actions Security Hardening docs before
making any change.
```

## Embedded CONTRIBUTING.md outline — DEFERRED per D21 P0 sweep

**This section is preserved as audit-trail / source-material for a future docs/development/contributing.md content refresh.** The 120-line full-rewrite-of-root-CONTRIBUTING.md plan was reversed in the 2026-04-25 P0 sweep when disk-truth audit found `docs/development/contributing.md` was already 594 lines of substantive content (not a thin duplicate as originally assumed). PR 1 commit 2 now produces a ~30-line thin pointer at the root (see Commit 2 above). The bullets below remain useful if a future PR wants to refresh `docs/development/contributing.md` with this expanded outline; do not lift them into root `CONTRIBUTING.md`.

<details>
<summary>(audit trail — outline, do not lift to root)</summary>

### 1. Welcome & project context
- One paragraph: salesagent is the Prebid Sales Agent (Prebid.org). Multi-tenant Python service: MCP server, A2A server, Admin UI.
- "Read [README.md](README.md) for the project overview before contributing."
- Link to [IPR_POLICY.md](IPR_POLICY.md) — agreement required.

### 2. Development setup
- Prereq: `uv` (link to install), Python 3.12, Docker (for integration/e2e).
- `uv sync --group dev` — installs dev dependencies (PEP 735 group).
- `pre-commit install --hook-type pre-commit --hook-type pre-push` — installs both stages.
- Optional: `cp .env.secrets.example .env.secrets` and fill in.

### 3. Local development workflow
- `make quality` — fast local check: ruff format, ruff lint, mypy, unit tests, structural guards. Run before every commit.
- `tox -e integration` — when refactoring imports/shared code (pre-commit can't catch import errors).
- `./run_all_tests.sh` — full suite: Docker up + 6 tox envs in parallel + Docker down. Run before PRs that touch protocols/schemas/critical patterns.
- Test results land in `test-results/<ddmmyy_HHmm>/` as JSON.

### 4. PR process
- Branch from `main` (`git checkout -b feat/short-description`). Never push directly to main.
- PR title must use a Conventional Commit prefix (`feat:`, `fix:`, `docs:`, `refactor:`, `perf:`, `chore:`). Enforced by `.github/workflows/pr-title-check.yml`.
- Required CI checks (the 11 frozen names per D17 + D26):
  - `CI / Quality Gate`, `CI / Type Check`, `CI / Schema Contract`, `CI / Unit Tests`, `CI / Integration Tests`, `CI / E2E Tests`, `CI / Admin UI Tests`, `CI / BDD Tests`, `CI / Migration Roundtrip`, `CI / Coverage`, `CI / Summary`
- Reviewer is auto-requested via `.github/CODEOWNERS`.

### 5. Layered hook model
- Layer 1: pre-commit stage (formatters, hygiene, fast AST checks) ~1-2s
- Layer 2: pre-push stage (medium checks, scoped pytest) ~10-20s
- Layer 3: structural guards (in tox -e unit, run via make quality) ~5-10s
- Layer 4: CI required checks (authoritative) ~5-15min
- Layer 5: manual / on-demand (full e2e, security audits) varies
- "CI is authoritative." If a check exists in pre-commit and CI, CI is the source of truth.

### 6-10. Other sections (modification policy, dependency policy, testing requirements, security reporting, optional tooling)
- Captured in the original outline (see git history of this file pre-P0-sweep).

</details>

## Embedded `.github/workflows/security.yml` skeleton

```yaml
name: Security

on:
  pull_request:
  push:
    branches: [main]
  schedule:
    - cron: '0 13 * * 1'  # Monday 06:00 PT

permissions: {}

jobs:
  pip-audit:
    name: pip-audit
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@<SHA>  # v4
      - uses: astral-sh/setup-uv@<SHA>  # v4
      - run: uv export --no-hashes --format requirements-txt > /tmp/requirements.txt
      - run: uvx pip-audit -r /tmp/requirements.txt

  zizmor:
    name: zizmor (workflow security lint)
    runs-on: ubuntu-latest
    permissions:
      contents: read
      security-events: write   # for SARIF upload
    steps:
      - uses: actions/checkout@<SHA>  # v4
      - uses: astral-sh/setup-uv@<SHA>  # v4
      - run: uvx zizmor --format sarif .github/workflows/ > zizmor.sarif
        continue-on-error: true   # SARIF still uploads even on findings
      - uses: github/codeql-action/upload-sarif@<SHA>  # v4 — pin v4 (v3 deprecates Dec 2026)
        with:
          sarif_file: zizmor.sarif
      - run: uvx zizmor --min-severity medium .github/workflows/  # this gates

  pinact:
    name: pinact (action SHA-pin enforcement)
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@<SHA>  # v4
        with:
          persist-credentials: false
      # Belt-and-suspenders to zizmor's `unpinned-uses` rule: pinact
      # purpose-built, zero-config. Catches new tag-pinned actions sneaking
      # past the one-time SHA-freeze in PR 1 commit 9.
      - run: |
          curl -sSfL https://raw.githubusercontent.com/suzuki-shunsuke/pinact/main/scripts/install.sh | sh -s -- -b /usr/local/bin
          pinact run --check  # exits non-zero if any uses: ref is unpinned

  actionlint:
    name: actionlint (workflow expression lint)
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@<SHA>  # v4
        with:
          persist-credentials: false
      # Orthogonal coverage to zizmor — actionlint catches `${{ }}` syntax
      # errors, runner-label typos, shellcheck-on-`run:` issues that zizmor
      # doesn't. <5s runtime.
      - run: |
          curl -sSfL https://raw.githubusercontent.com/rhysd/actionlint/main/scripts/download-actionlint.bash | bash -s -- 1.7.7
          ./actionlint -color
```

## Embedded `.github/workflows/codeql.yml` skeleton

```yaml
name: CodeQL

on:
  pull_request:
  push:
    branches: [main]
  schedule:
    - cron: '0 13 * * 1'  # Monday 06:00 PT

permissions: {}

jobs:
  analyze:
    name: CodeQL
    runs-on: ubuntu-latest
    permissions:
      contents: read
      security-events: write
    strategy:
      fail-fast: false
      matrix:
        language: [python]
    steps:
      - uses: actions/checkout@<SHA>  # v4
      - uses: github/codeql-action/init@<SHA>  # v4 — pin v4 (v3 deprecates Dec 2026)
        with:
          languages: ${{ matrix.language }}
          queries: security-extended
          config-file: ./.github/codeql/codeql-config.yml
      - uses: github/codeql-action/autobuild@<SHA>  # v4 — pin v4 (v3 deprecates Dec 2026)
      - uses: github/codeql-action/analyze@<SHA>  # v4 — pin v4 (v3 deprecates Dec 2026)
        with:
          category: '/language:${{ matrix.language }}'
        continue-on-error: true   # PER D10 PATH C — advisory until 2026-05-30
```

After Week 4 (per D10 tripwire), remove `continue-on-error: true` to flip to gating.

## Embedded `.github/codeql/codeql-config.yml` skeleton

```yaml
name: salesagent CodeQL config
queries:
  - uses: security-extended
paths:
  - src/
  - scripts/
paths-ignore:
  - tests/
  - alembic/versions/
  - '**/*.test.py'
  - '**/test_*.py'
# query-filters intentionally empty — Path C handles noise via continue-on-error
# rather than suppression. If specific findings need permanent suppression after
# the advisory window, add them here with a # justification comment.
```
