#!/usr/bin/env bash
# Verification for PR 6 — Image supply-chain (cosign + harden-runner + SBOM)
set -uo pipefail

# Source shared helpers (fail/ok/warn/section + common checks live in _lib.sh)
source "$(dirname "$0")/_lib.sh"

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

  # Version floor: v2.19.0+ (current pin as of 2026-04-26).
  # GHSA floor history: v2.12.0 patched CVE-2025-32955 (sudo bypass);
  # v2.16.0 patched DoH/DNS-over-TCP bypass GHSAs (GHSA-46g3-37rh-v698 + GHSA-g699-3x6g-wm3g);
  # v2.19.0 captures incremental hardening above v2.16 baseline.
  # SHA-pinned refs in PR 1's .action-shas.txt; verify the trailing comment shows v2.19+.
  HR_REFS=$(grep -RhoE 'uses: step-security/harden-runner@[a-f0-9]{40}\s*#\s*v[0-9.]+' .github/workflows/ | grep -oE 'v[0-9.]+' | sort -u)
  if [[ -n "$HR_REFS" ]]; then
    while IFS= read -r tag; do
      MAJOR=$(echo "$tag" | tr -d v | cut -d. -f1)
      MINOR=$(echo "$tag" | tr -d v | cut -d. -f2)
      if [[ "$MAJOR" -lt 2 ]] || { [[ "$MAJOR" -eq 2 ]] && [[ "$MINOR" -lt 19 ]]; }; then
        fail "harden-runner pinned to $tag — must be ≥v2.19.0 (current pin per D34; v2.16 is the GHSA-floor minimum but v2.19 is the documented current)"
      fi
    done <<< "$HR_REFS"
    ok "harden-runner ≥v2.19.0 (current pin; v2.16 GHSA floor satisfied)"
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

  # D47 gate prerequisite — release-please outputs.sha declared
  grep -qE '^\s+sha:\s+\$\{\{\s*github\.sha\s*\}\}' .github/workflows/release-please.yml \
    || fail "release-please.yml release-please job missing 'sha: \${{ github.sha }}' in outputs (D47 gate broken without it)"
  ok "release-please.outputs.sha declared (D47 gate operational)"

  # R29 mitigation — TWO publish jobs, sign-and-attest depends on build-and-push
  grep -qE '^\s+build-and-push:' .github/workflows/release-please.yml \
    || fail "release-please.yml missing build-and-push job (R29 mitigation requires split from monolithic publish-docker)"
  grep -qE '^\s+sign-and-attest:' .github/workflows/release-please.yml \
    || fail "release-please.yml missing sign-and-attest job (R29 mitigation requires split from build-and-push)"
  grep -A2 'sign-and-attest:' .github/workflows/release-please.yml | grep -q 'needs:.*build-and-push' \
    || fail "sign-and-attest must depend on build-and-push (needs: build-and-push) — R29 mitigation"
  ok "publish split into build-and-push + sign-and-attest (R29 mitigation applied)"

  # D47 polling loop — required for 5-30s eventual-consistency tolerance
  grep -B1 -A20 'Require CI green on release commit' .github/workflows/release-please.yml | grep -qE 'for attempt in.*MAX_ATTEMPTS' \
    || fail "D47 gate missing polling loop (would false-negative on 5-30s eventual-consistency lag)"
  ok "D47 gate uses polling loop (eventual-consistency tolerant)"

  # New supply-chain hardening present
  # --bundle is required in Cosign v3+ (sigstore/cosign-installer@v4.1.1 installs Cosign v3+)
  grep -q 'cosign sign --yes --bundle' .github/workflows/release-please.yml \
    || fail "release-please.yml missing 'cosign sign --yes --bundle' (--bundle required in Cosign v3+)"
  grep -q 'actions/attest-build-provenance' .github/workflows/release-please.yml || fail "release-please.yml missing attest-build-provenance"
  grep -q 'sbom: true' .github/workflows/release-please.yml || fail "release-please.yml missing 'sbom: true'"
  grep -q 'provenance: mode=max' .github/workflows/release-please.yml || fail "release-please.yml missing 'provenance: mode=max'"
  grep -q 'id-token: write' .github/workflows/release-please.yml || fail "release-please.yml missing id-token: write (cosign keyless requires)"

  # CVE-2025-32955 in publish-docker job
  grep -q 'disable-sudo-and-containers: true' .github/workflows/release-please.yml \
    || fail "release-please.yml missing disable-sudo-and-containers (CVE-2025-32955)"

  ok "release-please.yml: cosign + attest + SBOM + provenance:max + multi-arch + Docker Hub + R29 split + D47 polling preserved"
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

# Commit 7b: self-hosted Scorecard workflow file exists
if [[ -f .github/workflows/scorecard.yml ]]; then
  yamllint -d relaxed .github/workflows/scorecard.yml >/dev/null 2>&1 || fail "scorecard.yml fails yamllint"
  grep -q 'ossf/scorecard-action' .github/workflows/scorecard.yml \
    || fail "scorecard.yml missing ossf/scorecard-action"
  grep -qE 'publish_results:\s*true' .github/workflows/scorecard.yml \
    || fail "scorecard.yml missing publish_results: true (badge auto-update)"
  grep -q 'branch_protection_rule' .github/workflows/scorecard.yml \
    && ok "scorecard.yml: ossf/scorecard-action + publish_results + branch_protection_rule"
fi

# Stale-string guard: no `release.yml` references; everything goes through release-please.yml
if grep -RnE 'release\.yml' .claude/notes/ci-refactor/scripts/ .claude/notes/ci-refactor/*.md 2>/dev/null \
    | grep -vE 'release-please\.yml|# (extends|does NOT|MOVED)' >/dev/null; then
  fail "stale 'release.yml' references found in plan corpus — must use 'release-please.yml' (per D5)"
fi

# Commit 4-7 are admin/operator actions — out of scope for the agent verifier

# ADR-007
if [[ -f docs/decisions/adr-007-build-provenance.md ]]; then
  grep -qE '^## Status' docs/decisions/adr-007-build-provenance.md || fail "ADR-007 lacks ## Status"
  grep -qiE 'cosign|attest-build-provenance' docs/decisions/adr-007-build-provenance.md \
    || fail "ADR-007 should reconcile cosign + attest-build-provenance overlap"
  ok "ADR-007 present with ## Status and reconciliation rationale"
fi

# D47 / R44 — publish-docker MUST gate on CI green
if [[ -f .github/workflows/release-please.yml ]]; then
  grep -q 'Require CI green on release commit' .github/workflows/release-please.yml \
    || fail "release-please.yml publish-docker missing CI-green gate (D47 — closes #1228 Cluster A4; without it red main can ship signed-but-broken images per R44)"
  grep -q 'workflows/ci.yml/runs' .github/workflows/release-please.yml \
    || fail "release-please.yml publish-docker missing gh api ci.yml workflow lookup (D47)"
  ok "publish-docker gates on CI green via gh api (D47/R44 mitigation)"
fi

# D34 + R11A-02 — Trivy OS-layer scan + SOURCE_DATE_EPOCH reproducible build
if [[ -f .github/workflows/release-please.yml ]]; then
  grep -q 'aquasecurity/trivy-action' .github/workflows/release-please.yml \
    || fail "release-please.yml missing aquasecurity/trivy-action (D34 / R12B-04)"
  grep -qE "severity:\s*['\"]?CRITICAL,HIGH" .github/workflows/release-please.yml \
    || fail "release-please.yml Trivy scan missing severity: 'CRITICAL,HIGH' (D34)"
  grep -qE "category:\s*trivy-os-layer" .github/workflows/release-please.yml \
    || fail "release-please.yml Trivy SARIF upload missing category: trivy-os-layer (D34)"
  # vuln-type: 'os,library' — REQUIRED to enable OS-level CVE detection (default is library-only)
  grep -q "vuln-type: 'os,library'" .github/workflows/release-please.yml \
    || fail "Trivy missing vuln-type: 'os,library' (OS-level CVE detection disabled — default is library-only)"
  ok "Trivy OS-layer scan present with CRITICAL/HIGH gating + os,library vuln-type + SARIF upload (D34)"

  # D34 / R11A-02 — SOURCE_DATE_EPOCH for reproducible builds
  grep -q 'SOURCE_DATE_EPOCH=' .github/workflows/release-please.yml \
    || fail "release-please.yml missing SOURCE_DATE_EPOCH build-arg (D34, R11A-02)"
  grep -q 'rewrite-timestamp=true' .github/workflows/release-please.yml \
    || fail "release-please.yml missing rewrite-timestamp=true output (D34)"
  ok "SOURCE_DATE_EPOCH reproducible build flags present (D34, R11A-02)"
fi

# SF-15 — dep-review config extracted to .github/dependency-review-config.yml
if [[ -f .github/dependency-review-config.yml ]]; then
  grep -qE '^fail-on-severity:\s+moderate' .github/dependency-review-config.yml \
    || fail ".github/dependency-review-config.yml missing fail-on-severity: moderate"
  grep -qE 'GPL-3\.0' .github/dependency-review-config.yml \
    || fail ".github/dependency-review-config.yml missing GPL-3.0 in deny-licenses"
  ok "dep-review config extracted to .github/dependency-review-config.yml (per SF-15)"
fi

# R36 — frozen-checks structural guard updated for 'Security / Dependency Review'
if [[ -f tests/unit/test_architecture_required_ci_checks_frozen.py ]]; then
  grep -q '"Security / Dependency Review"' tests/unit/test_architecture_required_ci_checks_frozen.py \
    || fail "test_architecture_required_ci_checks_frozen.py expected list missing 'Security / Dependency Review' (R36)"
  ok "frozen-checks structural guard knows about 'Security / Dependency Review' (R36 mitigation)"
fi

echo "PR 6 verification: complete"
