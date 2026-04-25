# Verification scripts

Each `verify-pr<N>.sh` is a stand-alone verification script lifted from the
corresponding `pr<N>-<slug>.md` spec's per-commit verification blocks. The
executor agent runs the matching script after every commit (per
`templates/executor-prompt.md` §Verification).

The scripts are advisory: when `make quality` passes but the script doesn't,
the most likely cause is a missing artifact (e.g., `.action-shas.txt`,
`.coverage-baseline`) from an earlier commit. Re-read the spec section.

| Script | Spec | Purpose |
|---|---|---|
| `verify-pr1.sh` | pr1-supply-chain-hardening.md | governance, SHA-pin, persist-credentials, zizmor |
| `verify-pr2.sh` | pr2-uvlock-single-source.md | local hooks, mypy plugin, pyproject migration |
| `verify-pr3.sh` | pr3-ci-authoritative.md | CI workflow, frozen check names, reusable workflow |
| `verify-pr4.sh` | pr4-hook-relocation.md | hook count ≤12, structural guards, CLAUDE.md table |
| `verify-pr5.sh` | pr5-version-consolidation.md | version anchors, py312 reformat |
| `verify-pr6.sh` | pr6-image-supply-chain.md | harden-runner, cosign, SBOM |
| `flip-branch-protection.sh` | pr3-ci-authoritative.md §Phase B | admin-only `gh api -X PATCH` (DO NOT RUN as agent) |
| `capture-rendered-names.sh` | pr3-ci-authoritative.md §Phase B Step 1b | pre-flip rendered-name probe |

All scripts are idempotent and safe to re-run. Most exit non-zero on the
first failure with a one-line message naming the failing assertion.

## verify-pr*.sh shared helpers

All `verify-pr*.sh` scripts source `_lib.sh` for common assertions:

| Helper | Purpose |
|---|---|
| `fail` / `ok` / `warn` / `section` | Output formatting |
| `check_sha_pinned` | Assert action `uses:` is SHA-pinned with `# v<tag>` comment |
| `check_persist_credentials_false` | Assert `actions/checkout` includes `persist-credentials: false` |
| `check_workflow_permissions` | Assert top-level `permissions:` block present |
| `check_workflow_concurrency` | Assert top-level `concurrency:` block present |
| `check_adr_status` | Assert ADR Status field matches expected value |
| `check_harden_runner_cve_fix` | Assert harden-runner uses CVE-2025-32955 mitigation |
| `check_yaml_lints` | Run yamllint (relaxed mode) |
| `check_actionlint` | Run actionlint |

Usage in per-PR scripts:
```bash
#!/bin/bash
source "$(dirname "$0")/_lib.sh"

section "PR N — Pre-condition checks"
check_workflow_permissions ".github/workflows/ci.yml"
check_persist_credentials_false ".github/workflows/release-please.yml"
```
