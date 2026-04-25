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
# pre-commit emits: rev: <40-char-sha>  # frozen: v<tag>  (single line)
if grep -q 'frozen: v' .pre-commit-config.yaml; then
  BAD=$(grep -E '^\s+rev:' .pre-commit-config.yaml | grep -vcE 'rev: [a-f0-9]{40}\s+# frozen: v')
  [[ "$BAD" == "0" ]] || fail "$BAD external rev: line(s) not in '<40-sha>  # frozen: v<tag>' format"
  ok "pre-commit external hooks SHA-pinned with '# frozen: v<tag>' comment"
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
fi

# Commit 10: Gemini key
if grep -q 'GEMINI_API_KEY' .github/workflows/test.yml 2>/dev/null; then
  ! grep -q 'secrets.GEMINI_API_KEY' .github/workflows/test.yml || fail "secrets.GEMINI_API_KEY still referenced"
  grep -q "GEMINI_API_KEY: test_key_for_mocking" .github/workflows/test.yml || fail "Gemini mock missing"
  ok "Gemini key is unconditional mock"
fi

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
