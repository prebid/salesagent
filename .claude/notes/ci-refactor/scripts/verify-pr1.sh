#!/usr/bin/env bash
# Verification for PR 1 — Supply-chain hardening
# Run after EACH commit. Exits 0 if PR 1 acceptance criteria are met (so far).
# Pre-existing missing artifacts (e.g., .action-shas.txt before commit 9) are non-fatal early.
set -uo pipefail

# Source shared helpers (fail/ok/warn/section + common checks live in _lib.sh)
source "$(dirname "$0")/_lib.sh"

# Commit 1: pyproject [project.urls]
if grep -qE '^\[project\.urls\]' pyproject.toml; then
  for k in Homepage Repository Issues Documentation Changelog; do
    grep -qE "^${k} = \"https" pyproject.toml || fail "[project.urls].${k} missing"
  done
  ok "[project.urls] complete"
fi

# Commit 2: CONTRIBUTING.md is a thin pointer; docs/development/contributing.md is canonical (per D21)
if [[ -f CONTRIBUTING.md ]]; then
  ROOT_LINES=$(wc -l <CONTRIBUTING.md)
  [[ "$ROOT_LINES" -ge 20 && "$ROOT_LINES" -le 60 ]] || fail "CONTRIBUTING.md root pointer should be 20-60 lines (got $ROOT_LINES); per D21 root is thin pointer"
  grep -q 'docs/development/contributing.md' CONTRIBUTING.md \
    || fail "CONTRIBUTING.md missing pointer to docs/development/contributing.md (D21)"
  grep -q 'pre-commit install --hook-type pre-commit --hook-type pre-push' CONTRIBUTING.md \
    || fail "CONTRIBUTING.md missing both-stages pre-commit install instruction"
  grep -qE '(feat|fix|refactor|docs|chore|perf):' CONTRIBUTING.md \
    || fail "CONTRIBUTING.md missing Conventional Commit prefix list"
  ok "CONTRIBUTING.md root pointer ($ROOT_LINES lines, D21-compliant)"

  # Canonical docs/development/contributing.md must remain at full size
  [[ -f docs/development/contributing.md ]] \
    || fail "docs/development/contributing.md missing — D21 says KEEP this as canonical (594 lines)"
  DOCS_LINES=$(wc -l <docs/development/contributing.md)
  [[ "$DOCS_LINES" -ge 500 ]] \
    || fail "docs/development/contributing.md ($DOCS_LINES lines) was truncated; D21 says KEEP at ~594"
  ok "docs/development/contributing.md preserved at $DOCS_LINES lines (D21-canonical)"
fi

# Commit 3: SECURITY.md
if [[ -f SECURITY.md ]]; then
  grep -qE 'reporting|disclosure|security@' SECURITY.md || fail "SECURITY.md lacks reporting policy"
  ok "SECURITY.md present"
fi

# Commit 4: CODEOWNERS
if [[ -f CODEOWNERS ]] || [[ -f .github/CODEOWNERS ]]; then
  cf=$([[ -f .github/CODEOWNERS ]] && echo .github/CODEOWNERS || echo CODEOWNERS)
  grep -qE '^\* @' "$cf" || fail "$cf has no global owner"
  ok "CODEOWNERS present at $cf"
  # v2.0 forward-compat glob (per master-index line 54 mandate; v2.0 phase PRs add 27 files
  # to /tests/unit/architecture/ per D20 collision list)
  if [[ -f .github/CODEOWNERS ]]; then
    grep -qE '^/tests/unit/architecture/\s+@chrishuie' .github/CODEOWNERS \
      || fail "CODEOWNERS missing v2.0 forward-compat glob /tests/unit/architecture/ @chrishuie"
    ok "CODEOWNERS includes v2.0 forward-compat glob /tests/unit/architecture/"
  fi
fi

# Commit 5: dependabot
if [[ -f .github/dependabot.yml ]]; then
  grep -qE '^version: 2' .github/dependabot.yml || fail "dependabot.yml missing 'version: 2'"
  for eco in pip pre-commit github-actions docker; do
    grep -q "package-ecosystem: \"$eco\"" .github/dependabot.yml || fail "dependabot.yml missing ecosystem: $eco"
  done
  ok "dependabot.yml covers 4 ecosystems"
fi

# Commit 8: pre-commit autoupdate --freeze
# pre-commit emits: rev: <40-char-sha>  # frozen: <tag>  (single line)
# Tag format varies: pre-commit-hooks ships `v6.0.0`, black ships `25.1.0` (no `v` prefix),
# ruff-pre-commit ships `v0.14.10`. Regex relaxed to `# frozen: \S+`.
# Note: black is held at 25.1.0 per ADR-008 (target-version bump deferred); the autoupdate
# in PR 1 commit 8 explicitly omits psf/black via individual --repo flags.
if grep -qE '# frozen: \S+' .pre-commit-config.yaml; then
  BAD=$(grep -E '^\s+rev:' .pre-commit-config.yaml | grep -vcE 'rev: [a-f0-9]{40}\s+# frozen: \S+')
  [[ "$BAD" == "0" ]] || fail "$BAD external rev: line(s) not in '<40-sha>  # frozen: <tag>' format"
  ok "pre-commit external hooks SHA-pinned with '# frozen: <tag>' comment"
  # Verify black held at 25.1.0 per ADR-008
  if grep -qE '^\s+rev:\s+25\.1\.0\s*$' .pre-commit-config.yaml; then
    ok "psf/black held at 25.1.0 (ADR-008 deferral)"
  fi
else
  echo "  skipped: PR 1 commit 8 (pre-commit autoupdate --freeze) not yet applied"
fi

# Commit 9: SHA-pinned actions + persist-credentials + permissions
if [[ -f .github/.action-shas.txt ]]; then
  [[ -s .github/.action-shas.txt ]] || fail ".action-shas.txt empty"
  ok ".github/.action-shas.txt populated ($(wc -l <.github/.action-shas.txt) refs)"

  USES_PINNED=$(grep -RhoE 'uses: [^ ]+@[a-f0-9]{40}' .github/workflows/ | wc -l)
  USES_TOTAL=$(grep -RhoE 'uses: [^ ]+@[^ ]+' .github/workflows/ | grep -vE 'uses: \./' | wc -l)
  [[ "$USES_PINNED" == "$USES_TOTAL" ]] || fail "$((USES_TOTAL - USES_PINNED)) action(s) not SHA-pinned"
  ok "$USES_TOTAL/$USES_TOTAL action refs SHA-pinned"

  for f in .github/workflows/*.yml; do
    grep -qE '^permissions:' "$f" || fail "missing top-level permissions: $f"
  done
  ok "all workflows declare top-level permissions"

  CHECKOUT_USES=$(grep -RhoE 'uses: actions/checkout@' .github/workflows/ | wc -l)
  PERSIST_FALSE=$(grep -RnA5 'uses: actions/checkout@' .github/workflows/ | grep -c 'persist-credentials: false')
  [[ "$CHECKOUT_USES" -le "$PERSIST_FALSE" ]] || fail "$((CHECKOUT_USES - PERSIST_FALSE)) checkout(s) missing persist-credentials:false"
  ok "all $CHECKOUT_USES actions/checkout calls set persist-credentials:false"

  # ipr-agreement.yml and pr-title-check.yml use pull_request_target without checkout
  # (zizmor allowlist via ADR-003). Their lack of persist-credentials is benign because
  # they don't checkout. The PERSIST_FALSE check above only applies to files with
  # checkout calls; verify the non-checkout workflows still have top-level permissions.
  for f in .github/workflows/ipr-agreement.yml .github/workflows/pr-title-check.yml; do
    if [[ -f "$f" ]]; then
      grep -qE '^permissions:' "$f" || fail "$f missing top-level permissions (pull_request_target requires)"
    fi
  done

  # peter-evans/create-pull-request appears in dependabot pre-commit fallback workflow;
  # if present, ensure SHA-pinned
  if grep -RqE 'uses: peter-evans/create-pull-request@' .github/workflows/; then
    grep -RhoE 'uses: peter-evans/create-pull-request@[a-f0-9]{40}' .github/workflows/ \
      || fail "peter-evans/create-pull-request not SHA-pinned"
    ok "peter-evans/create-pull-request SHA-pinned"
  fi
else
  echo "  skipped: PR 1 commit 9 (.action-shas.txt artifact) not yet applied"
fi

# Commit 10: MOVED to PR 3 (D15/PD24 Gemini fallback fix is now applied as a new commit
# in PR 3's commit sequence, since PR 3 rewrites test.yml wholesale into ci.yml + composite.
# Verification of the Gemini mock lives in scripts/verify-pr3.sh.)

# Commit 11: zizmor
if [[ -f .github/zizmor.yml ]]; then
  ok ".github/zizmor.yml present"
fi

# Commit 5: security.yml has pinact + actionlint jobs
if [[ -f .github/workflows/security.yml ]]; then
  grep -qE '^\s+pinact:' .github/workflows/security.yml \
    || fail "security.yml missing 'pinact' job (belt-and-suspenders to zizmor unpinned-uses)"
  grep -qE 'pinact run --check' .github/workflows/security.yml \
    || fail "security.yml pinact job missing 'pinact run --check' invocation"
  grep -qE '^\s+actionlint:' .github/workflows/security.yml \
    || fail "security.yml missing 'actionlint' job (\${{ }} expression lint)"
  ok "security.yml: pinact + actionlint jobs present"
fi

# Commit 5 (per D35): gitleaks adopted as both pre-commit hook AND security.yml job
if [[ -f .pre-commit-config.yaml ]]; then
  grep -q 'id: gitleaks' .pre-commit-config.yaml \
    || fail ".pre-commit-config.yaml missing gitleaks hook (D35)"
  ok ".pre-commit-config.yaml has gitleaks hook (D35)"
fi
if [[ -f .github/workflows/security.yml ]]; then
  grep -q 'gitleaks' .github/workflows/security.yml \
    || fail "security.yml missing gitleaks job (D35)"
  ok "security.yml has gitleaks job (D35)"
fi

# ADRs
for adr in adr-001-single-source-pre-commit-deps adr-002-solo-maintainer-bypass adr-003-pull-request-target-trust; do
  if [[ -f docs/decisions/${adr}.md ]]; then
    grep -qE '^## Status' docs/decisions/${adr}.md || fail "${adr}.md lacks ## Status section"
    ok "ADR ${adr} present with ## Status"
  fi
done

echo "PR 1 verification: complete (commits implemented so far passed)"
