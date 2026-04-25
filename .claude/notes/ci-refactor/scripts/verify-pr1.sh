#!/usr/bin/env bash
# Verification for PR 1 — Supply-chain hardening
# Run after EACH commit. Exits 0 if PR 1 acceptance criteria are met (so far).
# Pre-existing missing artifacts (e.g., .action-shas.txt before commit 9) are non-fatal early.
set -uo pipefail

fail() { echo "FAIL: $*" >&2; exit 1; }
ok()   { echo "  ok: $*"; }

# Commit 1: pyproject [project.urls]
if grep -qE '^\[project\.urls\]' pyproject.toml; then
  for k in Homepage Repository Issues Documentation Changelog; do
    grep -qE "^${k} = \"https" pyproject.toml || fail "[project.urls].${k} missing"
  done
  ok "[project.urls] complete"
fi

# Commit 2: CONTRIBUTING.md authoritative
if [[ -f CONTRIBUTING.md ]]; then
  [[ $(wc -l <CONTRIBUTING.md) -ge 80 ]] || fail "CONTRIBUTING.md < 80 lines (looks stub)"
  grep -q '^## ' CONTRIBUTING.md || fail "CONTRIBUTING.md has no H2 sections"
  ok "CONTRIBUTING.md present and structured"
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

  # Per Round 5+6 P0 sweep: ipr-agreement.yml and pr-title-check.yml use pull_request_target
  # without checkout (zizmor allowlist via ADR-003). Their lack of persist-credentials is
  # benign because they don't checkout. The PERSIST_FALSE check above only applies to files
  # with checkout calls; verify the non-checkout workflows still have top-level permissions.
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

# Commit 10: MOVED to PR 3 per 2026-04-25 P0 sweep (D15/PD24 Gemini fallback fix is now
# applied as a new commit in PR 3's commit sequence, since PR 3 rewrites test.yml wholesale
# into ci.yml + composite. Verification of the Gemini mock lives in scripts/verify-pr3.sh.)

# Commit 11: zizmor
if [[ -f .github/zizmor.yml ]]; then
  ok ".github/zizmor.yml present"
fi

# ADRs
for adr in adr-001-single-source-pre-commit-deps adr-002-solo-maintainer-bypass adr-003-pull-request-target-trust; do
  if [[ -f docs/decisions/${adr}.md ]]; then
    grep -qE '^## Status' docs/decisions/${adr}.md || fail "${adr}.md lacks ## Status section"
    ok "ADR ${adr} present with ## Status"
  fi
done

echo "PR 1 verification: complete (commits implemented so far passed)"
