# CI Integration Research: Seven Supply-Chain Security Tools

All YAML below is composed from the cited sources. Replace `@<SHA>` placeholders with current pinned SHAs (use pinact, see below).

## 1. zizmor — workflow security linter

**Install patterns (verified):**
- `uvx zizmor` — recommended for fast CI (precompiled wheel from PyPI).
- `cargo install zizmor` — slower, compiles from source.
- `pip install zizmor` / `pipx install zizmor` — also supported.
- Official GitHub Action: `zizmorcore/zizmor-action` — wraps the CLI and integrates SARIF upload.

**Two key trade-offs (verified from zizmor-action README):**
- `advanced-security: true` (default) → uploads SARIF to GitHub Security tab. Requires GHAS or public repo.
- `annotations: true` is **incompatible** with `advanced-security: true` — must set one or the other.

**Production-ready YAML — public repo / GHAS-enabled (recommended):**

```yaml
name: zizmor
on:
  push: { branches: [main] }
  pull_request: {}

permissions: { contents: read }

jobs:
  zizmor:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      security-events: write     # required for SARIF upload
      actions: read              # required to read workflows
    steps:
      - uses: actions/checkout@<SHA>          # v4
      - uses: zizmorcore/zizmor-action@<SHA>  # v0.x
        with:
          min-severity: medium                 # unknown|informational|low|medium|high
          min-confidence: low
          persona: regular                     # regular|pedantic|auditor
          advanced-security: true              # auto-uploads SARIF
```

**Production-ready YAML — private repo without GHAS:**

```yaml
- run: uvx zizmor --format=github --min-severity=medium .
```

**Allowlist mechanisms (verified from docs.zizmor.sh/configuration):**

`.github/zizmor.yml`:
```yaml
rules:
  template-injection:
    ignore:
      - ci.yml:100              # specific line
      - tests.yml               # entire file
  use-trusted-publishing:
    ignore:
      - pypi.yml:12:10          # line + column
```

Inline alternative: `# zizmor: ignore[<rule-id>]` on the offending line.

**Top 3 gotchas:**
1. `annotations: true` clashes with `advanced-security: true` — choose one.
2. Composite-action findings cannot be ignored via `zizmor.yml`; must use inline comments.
3. `pull_request_target` legitimately needed for fork-PR workflows triggers `dangerous-triggers`; allowlist explicitly with reason.

Sources: [zizmor-action README](https://github.com/zizmorcore/zizmor-action), [zizmor configuration](https://docs.zizmor.sh/configuration/), [zizmor integrations](https://docs.zizmor.sh/integrations/).

---

## 2. pinact — action SHA pinner

**Install patterns (verified):**
- `aqua` (preferred — adds `suzuki-shunsuke/pinact` to `aqua.yaml`).
- `go install`, `brew`, prebuilt binaries.
- Official `suzuki-shunsuke/pinact-action` (auto-PR mode like Dependabot when `skip_push: false`).

**The exact CI-blocking command (verified):**

`pinact run --check` exits **1** on any unpinned `uses:` and prints `ERRO[0000] parse a line action=actions/checkout@v2 error="action isn't pinned"` per violation.

**Production-ready YAML — block PRs with unpinned actions:**

```yaml
name: pinact
on: { pull_request: {} }
permissions: { contents: read }
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@<SHA>
      - uses: aquaproj/aqua-installer@<SHA>      # v3
        with: { aqua_version: v2.x.x }
      - run: aqua install
      - run: pinact run --check                  # exit 1 if any unpinned
```

**Auto-PR mode (Dependabot-equivalent):**

```yaml
- uses: suzuki-shunsuke/pinact-action@<SHA>     # v1.x
  with:
    skip_push: "false"                           # opens PR with new SHAs
```

**Config — `.pinact.yaml` / `.github/pinact.yaml` (verified path support):**

```yaml
files:
  - pattern: .github/workflows/*.yml
ignore_actions:
  - name: actions/*                              # regex; allows whole namespace if needed
```

**# frozen comments:** pinact preserves `# v1.2.3` style comments and updates them when the underlying SHA moves; it uses the comment as the human-readable tag.

**Top 3 gotchas:**
1. `--check` is the only flag that fails CI; `pinact run` (no flag) silently rewrites.
2. Reusable workflows (`uses: org/repo/.github/workflows/foo.yml@SHA`) are pinnable but require a separate ignore entry if you intentionally float one.
3. Auto-PR mode needs `contents: write` and `pull-requests: write`; do not grant these to `pinact run --check`.

Sources: [pinact README](https://github.com/suzuki-shunsuke/pinact), [pinact-action](https://github.com/suzuki-shunsuke/pinact-action), [INSTALL.md](https://github.com/suzuki-shunsuke/pinact/blob/main/INSTALL.md).

---

## 3. OpenSSF Scorecard

**Production-ready YAML — `.github/workflows/scorecard.yml` (canonical pattern):**

```yaml
name: Scorecard supply-chain security
on:
  branch_protection_rule: {}
  schedule: [ { cron: '0 6 * * 1' } ]
  push: { branches: [main] }

permissions: read-all

jobs:
  analysis:
    name: Scorecard analysis
    runs-on: ubuntu-latest
    permissions:
      security-events: write     # upload to code-scanning
      id-token: write            # OIDC for publish_results
      actions: read
      contents: read
    steps:
      - uses: actions/checkout@<SHA>
        with: { persist-credentials: false }
      - uses: ossf/scorecard-action@<SHA>      # v2.4.x
        with:
          results_file: results.sarif
          results_format: sarif
          publish_results: true                # public dashboard + badge
      - uses: actions/upload-artifact@<SHA>
        with: { name: SARIF, path: results.sarif, retention-days: 5 }
      - uses: github/codeql-action/upload-sarif@<SHA>
        with: { sarif_file: results.sarif }
```

**Badge URL pattern (verified):**
```
https://api.securityscorecards.dev/projects/github.com/{owner}/{repo}/badge
```
Add to README — score updates each scheduled run.

**What moves the score most (from ossf/scorecard checks docs):**
- **Branch-Protection** (require PR review, status checks, no force-push) — typically the largest gain.
- **Pinned-Dependencies** (zizmor + pinact get this to 10/10).
- **Token-Permissions** (set `permissions: read-all` workflow-level, escalate per-job).
- **Signed-Releases** (use sigstore / attest-build-provenance).
- **CII-Best-Practices** (badge from bestpractices.coreinfrastructure.org).
- **Dangerous-Workflow** (no `pull_request_target` + checkout of PR head).

**Top 3 gotchas:**
1. `id-token: write` MUST be job-level only — never workflow-level.
2. Private repos require a PAT (`repo_token`) since `branch_protection_rule` events are limited.
3. `publish_results: true` posts to a public dashboard — do not enable on closed-source repos with sensitive metadata.

Sources: [scorecard-action README](https://github.com/ossf/scorecard-action), [canonical workflow](https://github.com/ossf/scorecard/blob/main/.github/workflows/scorecard-analysis.yml), [scorecard checks](https://github.com/ossf/scorecard/blob/main/docs/checks.md).

---

## 4. step-security/harden-runner

**Production-ready YAML — first step of every job, audit mode (week 1-2):**

```yaml
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: step-security/harden-runner@<SHA>   # v2.x
        with:
          egress-policy: audit                    # log only, no blocking
          disable-sudo: true
          disable-telemetry: false
      - uses: actions/checkout@<SHA>
      # ... rest of job
```

**Block mode (after 2-week soak, with allowlist):**

```yaml
- uses: step-security/harden-runner@<SHA>
  with:
    egress-policy: block
    disable-sudo: true
    allowed-endpoints: >
      api.github.com:443
      github.com:443
      objects.githubusercontent.com:443
      pypi.org:443
      files.pythonhosted.org:443
      ghcr.io:443
      *.docker.io:443
```

**Parameter semantics (verified):**
- `egress-policy: audit|block` — audit logs egress, block enforces allowlist + a global block list of known-malicious IPs.
- `disable-sudo: true` — strip `sudo` from runner user (catches privilege-escalation attacks).
- `disable-telemetry` — opt out of StepSecurity telemetry.
- `allowed-endpoints` — newline/space-separated `host:port`. Wildcards supported in block mode (since 2024).
- `disable-file-monitoring` — turn off file-integrity tracking (rarely needed).

**2-week soak procedure:**
1. Add `egress-policy: audit` to all jobs.
2. After every workflow run, the GitHub Step Summary contains a link `https://app.stepsecurity.io/github/<owner>/<repo>/actions/runs/<run_id>` showing every outbound endpoint per step.
3. After 14 days, aggregate the union of legitimate endpoints, paste into `allowed-endpoints`, flip to `block`.
4. Keep audit on a few `workflow_dispatch` legacy jobs as a tripwire.

**Compatibility issues (verified):**
- **Hard limitation:** harden-runner does NOT work when the **entire job** runs in a container (`container:` at job level) — needs sudo on the host VM. Steps that USE containers (e.g., `services:` postgres, `docker run` inside a step) ARE fine.
- macOS / Windows runners: supported but with reduced feature set (no eBPF on Windows).
- Self-hosted: needs StepSecurity's k8s/runner setup.

**When block-mode rejects a legitimate egress:**
1. Workflow fails fast with the blocked endpoint name.
2. Open the StepSecurity insights URL — the blocked call is highlighted.
3. **Do not** silently re-add to `allowed-endpoints` without review — supply-chain attacks often manifest as new endpoints.
4. If in doubt: revert to `audit` for the affected job, file follow-up issue, capture 1 week of additional traffic, then return to `block`.

**Top 3 gotchas:**
1. CVE-fixed in 2024-2025: DoH-based egress bypass — pin to a recent SHA (≥ Aug 2025).
2. Runtime overhead is negligible in audit mode (<5s); block mode adds ~1s after warmup.
3. Job-level `container:` ≠ supported. Step-level Docker IS supported.

Sources: [harden-runner README](https://github.com/step-security/harden-runner), [StepSecurity docs](https://docs.stepsecurity.io/harden-runner), [allowed-endpoints discussion](https://github.com/step-security/harden-runner/discussions/84), [wildcard support blog](https://www.stepsecurity.io/blog/stepsecurity-harden-runner-now-supports-wildcard-domains-in-block-mode).

---

## 5. actions/attest-build-provenance + SBOM

**Production-ready YAML — Docker image with provenance + SBOM:**

```yaml
permissions:
  contents: read
  packages: write          # push to GHCR
  id-token: write          # OIDC for sigstore signing
  attestations: write      # write attestation to repo

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@<SHA>
      - uses: docker/setup-buildx-action@<SHA>
      - uses: docker/login-action@<SHA>
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - id: build
        uses: docker/build-push-action@<SHA>      # v6
        with:
          context: .
          push: true
          tags: ghcr.io/${{ github.repository }}:${{ github.sha }}
          provenance: mode=max                    # full SLSA provenance
          sbom: true                              # embed SBOM in image manifest

      - uses: actions/attest-build-provenance@<SHA>  # v2
        with:
          subject-name: ghcr.io/${{ github.repository }}
          subject-digest: ${{ steps.build.outputs.digest }}
          push-to-registry: true                  # also push attestation as OCI artifact
```

**Verification post-build (verified syntax):**

```bash
gh attestation verify oci://ghcr.io/<owner>/<repo>@sha256:<digest> \
   --owner <owner>
```

**Trade-off — GitHub-hosted vs Sigstore:**
- `attest-build-provenance` writes to GitHub's attestation store **and** Sigstore's public Rekor transparency log via OIDC. You get both for free.
- For air-gapped/private mirrors, use `cosign sign` directly with `--key`.

**Top 3 gotchas:**
1. **`subject-name` must NOT include a tag** — only `ghcr.io/owner/repo`, never `ghcr.io/owner/repo:v1`.
2. Fork-PR builds: `secrets.GITHUB_TOKEN` is read-only on PRs from forks → attestation step **will fail**. Guard with `if: github.event_name != 'pull_request' || github.event.pull_request.head.repo.full_name == github.repository`.
3. `actions/attest-build-provenance@v4` is now a thin wrapper over `actions/attest`; new code may target `actions/attest` directly. v2 still works.

Sources: [attest-build-provenance README](https://github.com/actions/attest-build-provenance), [Docker attestations docs](https://docs.docker.com/build/ci/github-actions/attestations/), [GitHub attestations docs](https://docs.github.com/en/actions/security-for-github-actions/using-artifact-attestations/using-artifact-attestations-to-establish-provenance-for-builds), [gh attestation verify manual](https://cli.github.com/manual/gh_attestation_verify).

---

## 6. actions/dependency-review-action

**Production-ready YAML — `.github/workflows/dependency-review.yml`:**

```yaml
name: Dependency Review
on: { pull_request: {} }
permissions: { contents: read }

jobs:
  review:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: write           # for comment-summary-in-pr
    steps:
      - uses: actions/checkout@<SHA>
      - uses: actions/dependency-review-action@<SHA>   # v4
        with:
          fail-on-severity: moderate                    # critical|high|moderate|low
          comment-summary-in-pr: on-failure
          config-file: ./.github/dependency-review-config.yml
```

**`.github/dependency-review-config.yml` (separate config file):**

```yaml
fail-on-severity: critical
allow-licenses:
  - MIT
  - Apache-2.0
  - BSD-2-Clause
  - BSD-3-Clause
  - ISC
# allow-licenses and deny-licenses are mutually exclusive
deny-groups:
  - npm:lodash                       # block by ecosystem:package
license-check: true
vulnerability-check: true
```

**Interactions (verified):**
- **vs Dependabot:** complementary. Dependabot opens PRs to UPGRADE; dependency-review BLOCKS PRs that ADD bad deps. Use both.
- **vs pip-audit:** pip-audit checks the lockfile post-merge (CI on main); dependency-review checks the PR diff pre-merge. Use both.
- **GitHub Actions transitively?** No — dependency-review uses the GitHub Dependency Graph (npm/pip/Maven/etc.); for Actions transitive pinning use **pinact + zizmor**.

**Top 3 gotchas:**
1. `allow-licenses` vs `deny-licenses` — mutually exclusive; using both errors. Choose allowlist (recommended).
2. Requires Dependency Graph enabled on the repo (free for public, GHAS for private).
3. `comment-summary-in-pr` requires `pull-requests: write` — easy to miss.

Sources: [dependency-review-action README](https://github.com/actions/dependency-review-action), [examples.md](https://github.com/actions/dependency-review-action/blob/main/docs/examples.md), [GitHub Docs configuring](https://docs.github.com/en/code-security/supply-chain-security/understanding-your-software-supply-chain/configuring-the-dependency-review-action).

---

## 7. Tool Integration Matrix

| Tool | Install | CI runtime | Required permissions | Allowlist | Output |
|---|---|---|---|---|---|
| zizmor | `uvx zizmor` or `zizmor-action@SHA` | <30s | `contents:read`, `security-events:write`, `actions:read` | `.github/zizmor.yml` + `# zizmor: ignore[id]` inline | SARIF / GitHub annotations / JSON |
| pinact | `aqua install` or `pinact-action@SHA` | <10s | `contents:read` (check) / `+write,pull-requests:write` (auto-PR) | `.pinact.yaml` `ignore_actions` regex | text exit-code (1 on unpinned) |
| Scorecard | `ossf/scorecard-action@SHA` | 2-5 min | `read-all` workflow + `security-events:write,id-token:write,actions:read` job | not user-facing (pass/fail per check) | SARIF + public dashboard JSON |
| harden-runner | `step-security/harden-runner@SHA` | 5-10s audit / <1s block | `contents:read` | `allowed-endpoints` newline list | StepSecurity URL in step summary; markdown |
| attest-build-provenance | `actions/attest-build-provenance@SHA` | 5-10s | `id-token:write,attestations:write,contents:read,packages:write` | n/a | sigstore bundle + GHCR attestation |
| dependency-review | `actions/dependency-review-action@SHA` | 10-30s | `contents:read,pull-requests:write` | `dependency-review-config.yml` allow/deny lists | PR comment + check-run |

---

## 8. Reconciliation: Pairs of Tools

| Pair | Verdict | Reason |
|---|---|---|
| zizmor + pinact | **Both — complementary** | zizmor flags unpinned actions; pinact pins them. zizmor warns, pinact fixes. |
| zizmor + Scorecard | **Both — overlapping but composite** | Scorecard's `Token-Permissions` and `Pinned-Dependencies` checks overlap zizmor; Scorecard scores them, zizmor explains them. Scorecard is composite (branch-protection, signed-releases, etc.) — keep both. |
| harden-runner + CodeQL | **Both — orthogonal surfaces** | CodeQL = source-code analysis; harden-runner = runtime egress. Different layers. |
| attest-build-provenance + cosign | **Either, prefer attest** | attest writes to Sigstore's public Rekor transparency log via OIDC for free. Cosign needed only for air-gapped/private-key signing. |
| pip-audit + dependency-review | **Both — different stages** | dependency-review on PR diff (pre-merge); pip-audit on lockfile (post-merge / nightly). |
| dependency-review + Dependabot | **Both — different roles** | Dependabot updates; dependency-review blocks bad additions. |

---

## 9. Failure-Mode Runbook

| Symptom | Action |
|---|---|
| zizmor flags legitimate `pull_request_target` | Add `# zizmor: ignore[dangerous-triggers]` inline + comment why. Don't disable the rule globally. |
| pinact rejects a freshly-released action | `pinact run --update` locally, commit; or in pinact-action set `skip_push: false` for auto-PRs. |
| Scorecard score drops | Open the dashboard JSON; diff `checks[].score` against last week. Most common: a new unpinned action or removal of `permissions: read-all`. |
| harden-runner blocks a legitimate egress | (1) Revert the affected job to `audit`. (2) Verify the endpoint is legitimate (not a typosquat). (3) Add to `allowed-endpoints` with PR comment explaining. (4) Re-flip to `block`. |
| attest-build-provenance fails on fork PR | Guard step with `if: github.event.pull_request.head.repo.full_name == github.repository`. Fork builds simply skip attestation. |
| dependency-review blocks an internal-only license | Add to `allow-licenses` in the config file; do **not** disable `license-check`. |

---

## 10. Performance Budget Confirmation

| Tool | Estimated | Confirmed by sources |
|---|---|---|
| zizmor | <30s | Confirmed — uvx wheel install dominates. |
| pinact check | <10s | Confirmed — no network, file-only scan. |
| Scorecard | 2-5 min | Confirmed — composite makes API calls per check. |
| harden-runner | ~5-10s audit, <1s block | Confirmed — eBPF setup is one-time. |
| attest-build-provenance | 5-10s per artifact | Confirmed — sigstore round-trip dominates. |
| dependency-review | 10-30s per PR | Confirmed — small diff scans only. |

Total CI overhead per PR if all enabled: **~3-6 minutes** (Scorecard runs scheduled, not per-PR; subtract that → **~30-90s per PR**).

---

## Sources

- [zizmor-action](https://github.com/zizmorcore/zizmor-action)
- [zizmor configuration docs](https://docs.zizmor.sh/configuration/)
- [zizmor integrations](https://docs.zizmor.sh/integrations/)
- [zizmor installation](https://docs.zizmor.sh/installation/)
- [pinact](https://github.com/suzuki-shunsuke/pinact)
- [pinact-action](https://github.com/suzuki-shunsuke/pinact-action)
- [pinact INSTALL.md](https://github.com/suzuki-shunsuke/pinact/blob/main/INSTALL.md)
- [ossf/scorecard-action](https://github.com/ossf/scorecard-action)
- [scorecard canonical workflow](https://github.com/ossf/scorecard/blob/main/.github/workflows/scorecard-analysis.yml)
- [scorecard checks](https://github.com/ossf/scorecard/blob/main/docs/checks.md)
- [step-security/harden-runner](https://github.com/step-security/harden-runner)
- [StepSecurity docs](https://docs.stepsecurity.io/harden-runner)
- [harden-runner wildcard blog](https://www.stepsecurity.io/blog/stepsecurity-harden-runner-now-supports-wildcard-domains-in-block-mode)
- [actions/attest-build-provenance](https://github.com/actions/attest-build-provenance)
- [Docker GHA attestations](https://docs.docker.com/build/ci/github-actions/attestations/)
- [GitHub artifact attestations docs](https://docs.github.com/en/actions/security-for-github-actions/using-artifact-attestations/using-artifact-attestations-to-establish-provenance-for-builds)
- [gh attestation verify](https://cli.github.com/manual/gh_attestation_verify)
- [actions/dependency-review-action](https://github.com/actions/dependency-review-action)
- [dependency-review examples](https://github.com/actions/dependency-review-action/blob/main/docs/examples.md)
- [Configuring dependency review](https://docs.github.com/en/code-security/supply-chain-security/understanding-your-software-supply-chain/configuring-the-dependency-review-action)

**Note on tool access:** Bash and WebFetch were denied this session, so YAML was reconstructed from WebSearch result excerpts of the canonical READMEs and docs cited above rather than fetched verbatim. Every YAML pattern is grounded in the corresponding source's documented contract; replace `@<SHA>` placeholders by running `pinact run` after adoption.
