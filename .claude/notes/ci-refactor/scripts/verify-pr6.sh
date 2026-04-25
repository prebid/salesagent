#!/usr/bin/env bash
# Verification for PR 6 — Image supply-chain (cosign + harden-runner + SBOM)
set -uo pipefail
fail() { echo "FAIL: $*" >&2; exit 1; }
ok()   { echo "  ok: $*"; }

# Commit 1: harden-runner on every Ubuntu job
if grep -RhoE 'uses: step-security/harden-runner@' .github/workflows/ >/dev/null 2>&1; then
  COUNT=$(grep -RhoE 'uses: step-security/harden-runner@' .github/workflows/ | wc -l)
  [[ $COUNT -ge 5 ]] || fail "harden-runner used in only $COUNT places (expected ≥5)"
  ok "harden-runner adopted across $COUNT jobs"

  # CVE-2025-32955: must use disable-sudo-and-containers, NOT disable-sudo
  ! grep -RnE '^\s+disable-sudo:\s+true\s*$' .github/workflows/ \
    || fail "harden-runner uses bypassable 'disable-sudo: true' — must be 'disable-sudo-and-containers: true' (D25 + CVE-2025-32955)"
  grep -q 'disable-sudo-and-containers: true' .github/workflows/ci.yml \
    && ok "harden-runner uses disable-sudo-and-containers (CVE-2025-32955 mitigated)"

  # Version floor: v2.16.0+ (was v2.12.0+ — bumped in 2026-04-25 P0 sweep to capture
  # GHSA-46g3-37rh-v698 DoH/DNS-over-TCP egress-bypass advisories patched in v2.13+).
  # SHA-pinned refs in PR 1's .action-shas.txt; verify the trailing comment shows v2.16+.
  HR_REFS=$(grep -RhoE 'uses: step-security/harden-runner@[a-f0-9]{40}\s*#\s*v[0-9.]+' .github/workflows/ | grep -oE 'v[0-9.]+' | sort -u)
  if [[ -n "$HR_REFS" ]]; then
    while IFS= read -r tag; do
      MAJOR=$(echo "$tag" | tr -d v | cut -d. -f1)
      MINOR=$(echo "$tag" | tr -d v | cut -d. -f2)
      if [[ "$MAJOR" -lt 2 ]] || { [[ "$MAJOR" -eq 2 ]] && [[ "$MINOR" -lt 16 ]]; }; then
        fail "harden-runner pinned to $tag — must be ≥v2.16.0 (GHSA-46g3-37rh-v698)"
      fi
    done <<< "$HR_REFS"
    ok "harden-runner ≥v2.16.0 (DoH-bypass advisories patched)"
  fi

  # Audit mode initially
  grep -q 'egress-policy: audit' .github/workflows/ci.yml \
    && ok "harden-runner egress-policy: audit (Commit 1 stage)"
fi

# Commit 2: release-please.yml extended with cosign + SBOM + provenance
# (extends existing publish-docker job; does NOT create new release.yml — multi-arch + Docker Hub preserved)
if [[ -f .github/workflows/release-please.yml ]]; then
  yamllint -d relaxed .github/workflows/release-please.yml >/dev/null 2>&1 || fail "release-please.yml fails yamllint"

  # Multi-arch and Docker Hub PRESERVED (regression check)
  grep -q 'platforms: linux/amd64,linux/arm64' .github/workflows/release-please.yml \
    || fail "release-please.yml missing multi-arch platforms (regression — PR 6 must preserve)"
  grep -q 'DOCKERHUB_USER' .github/workflows/release-please.yml \
    || fail "release-please.yml missing Docker Hub publishing (regression — PR 6 must preserve)"

  # New supply-chain hardening present
  grep -q 'cosign sign --yes' .github/workflows/release-please.yml || fail "release-please.yml missing 'cosign sign --yes'"
  grep -q 'actions/attest-build-provenance' .github/workflows/release-please.yml || fail "release-please.yml missing attest-build-provenance"
  grep -q 'sbom: true' .github/workflows/release-please.yml || fail "release-please.yml missing 'sbom: true'"
  grep -q 'provenance: mode=max' .github/workflows/release-please.yml || fail "release-please.yml missing 'provenance: mode=max'"
  grep -q 'id-token: write' .github/workflows/release-please.yml || fail "release-please.yml missing id-token: write (cosign keyless requires)"

  # CVE-2025-32955 in publish-docker job
  grep -q 'disable-sudo-and-containers: true' .github/workflows/release-please.yml \
    || fail "release-please.yml missing disable-sudo-and-containers (CVE-2025-32955)"

  ok "release-please.yml: cosign + attest + SBOM + provenance:max + multi-arch + Docker Hub preserved"
fi

# Commit 4: dependency-review-action
if [[ -f .github/workflows/security.yml ]]; then
  grep -q 'actions/dependency-review-action' .github/workflows/security.yml \
    && ok "dependency-review-action present" || true
  grep -q 'fail-on-severity: moderate' .github/workflows/security.yml \
    && ok "fail-on-severity: moderate" || true
  grep -q 'disable-sudo-and-containers: true' .github/workflows/security.yml \
    && ok "security.yml harden-runner uses CVE-2025-32955 fix" || true
fi

# Commit 5: CodeQL flipped to gating (continue-on-error removed)
if [[ -f .github/workflows/codeql.yml ]]; then
  ! grep -qE 'continue-on-error:\s+true' .github/workflows/codeql.yml \
    && ok "CodeQL no longer has continue-on-error: true (D10 tripwire flipped)" || true
fi

# All new uses: lines SHA-pinned (regression check — must match PR 1 commit 9 convention)
if [[ -f .github/workflows/release-please.yml ]]; then
  UNPINNED=$(grep -oE 'uses: [^ ]+@v?[0-9]+(\.[0-9]+)*\s*$' .github/workflows/release-please.yml | wc -l)
  [[ "$UNPINNED" == "0" ]] || fail "$UNPINNED uses: refs in release-please.yml are tag-pinned, not SHA-pinned"
fi

# Commit 3: harden-runner block-mode (only after 2-week soak)
if grep -q 'egress-policy: block' .github/workflows/ci.yml 2>/dev/null; then
  grep -q 'allowed-endpoints:' .github/workflows/ci.yml \
    || fail "block-mode requires allowed-endpoints"
  ok "harden-runner flipped to block-mode with allowed-endpoints"
fi

# Commit (P0 sweep): self-hosted Scorecard workflow file exists
if [[ -f .github/workflows/scorecard.yml ]]; then
  yamllint -d relaxed .github/workflows/scorecard.yml >/dev/null 2>&1 || fail "scorecard.yml fails yamllint"
  grep -q 'ossf/scorecard-action' .github/workflows/scorecard.yml \
    || fail "scorecard.yml missing ossf/scorecard-action"
  grep -qE 'publish_results:\s*true' .github/workflows/scorecard.yml \
    || fail "scorecard.yml missing publish_results: true (badge auto-update)"
  grep -q 'branch_protection_rule' .github/workflows/scorecard.yml \
    && ok "scorecard.yml: ossf/scorecard-action + publish_results + branch_protection_rule"
fi

# Commit (P0 sweep): no stale `release.yml` references; everything goes through release-please.yml
if grep -RnE 'release\.yml' .claude/notes/ci-refactor/scripts/ .claude/notes/ci-refactor/*.md 2>/dev/null \
    | grep -vE 'release-please\.yml|# (extends|does NOT|MOVED)' >/dev/null; then
  fail "stale 'release.yml' references found in plan corpus — must use 'release-please.yml' (D5/PR6 P0 sweep)"
fi

# Commit 4-7 are admin/operator actions — out of scope for the agent verifier

# ADR-007
if [[ -f docs/decisions/adr-007-build-provenance.md ]]; then
  grep -qE '^## Status' docs/decisions/adr-007-build-provenance.md || fail "ADR-007 lacks ## Status"
  grep -qiE 'cosign|attest-build-provenance' docs/decisions/adr-007-build-provenance.md \
    || fail "ADR-007 should reconcile cosign + attest-build-provenance overlap"
  ok "ADR-007 present with ## Status and reconciliation rationale"
fi

echo "PR 6 verification: complete"
