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
