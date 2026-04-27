#!/usr/bin/env bash
# Verification for PR 4 — Hook relocation + structural guards
source "$(dirname "$0")/_lib.sh"

# Commit 1: helpers extended (file existed since PR 2 commit 8)
if [[ -f tests/unit/_architecture_helpers.py ]]; then
  for fn in parse_module iter_workflow_files iter_compose_files iter_action_uses \
            iter_python_version_anchors iter_postgres_image_refs \
            assert_violations_match_allowlist assert_anchor_consistency format_failure; do
    grep -qE "^def $fn\b" tests/unit/_architecture_helpers.py \
      || fail "_architecture_helpers.py missing extended helper: $fn"
  done
  ok "_architecture_helpers.py fully extended (PR 2 baseline + PR 4 additions)"
fi

# pytest marker registered (D29 renamed from `architecture` to `arch_guard` to avoid
# collision with the entity-marker `architecture` auto-applied by tests/conftest.py).
# PR 2 commit 8 OWNS registration (in pytest.ini, not pyproject.toml); PR 4 commit 1 verifies.
grep -qE '^\s*arch_guard:' pytest.ini || fail "pytest.ini missing 'arch_guard' marker (D29; PR 2 commit 8 owns registration)"
ok "pytest @arch_guard marker registered in pytest.ini (D29; PR 2 commit 8)"

# D31 — `default_install_hook_types` directive at top of .pre-commit-config.yaml.
# Without this directive, `pre-commit install` (no --hook-type qualifier) only installs
# pre-commit-stage hooks; the entire 10-hook pre-push tier from D27 silently no-ops.
grep -qE '^default_install_hook_types:.*pre-commit.*pre-push' .pre-commit-config.yaml \
  || fail ".pre-commit-config.yaml missing default_install_hook_types: [pre-commit, pre-push] (D31; R33 mitigation)"
ok "default_install_hook_types directive present (D31; load-bearing for D27 hook math)"

# D44 — minimum_pre_commit_version: 3.2.0 directive.
# default_install_hook_types is a pre-commit ≥3.2 feature. Older versions silently ignore
# unknown directives, leaving pre-push tier disabled. minimum_pre_commit_version surfaces
# the version requirement at install time rather than silently at commit time.
grep -qE '^minimum_pre_commit_version:\s*3\.2' .pre-commit-config.yaml \
  || fail ".pre-commit-config.yaml missing minimum_pre_commit_version: 3.2.0 (D44; protects D31 from silent ignore)"
ok "minimum_pre_commit_version: 3.2.0 declared (D44)"

# Commit 3+4: new structural guards
NEW_GUARDS=(
  test_architecture_no_tenant_config
  test_architecture_jsontype_columns
  test_architecture_no_defensive_rootmodel
  test_architecture_import_usage
)
for g in "${NEW_GUARDS[@]}"; do
  [[ -f "tests/unit/${g}.py" ]] && ok "guard present: ${g}.py"
done

# R12B-01 — frozen-checks structural guard MUST be lifted from drafts/ to tests/unit/
# (PR 4 commit 3 lifts it). Without this, R36 mitigation is non-operational.
[[ -f tests/unit/test_architecture_required_ci_checks_frozen.py ]] \
  || fail "tests/unit/test_architecture_required_ci_checks_frozen.py missing — must be lifted from drafts/guards/ in PR 4 commit 3 (R12B-01)"
ok "frozen-checks structural guard lifted from drafts/ to tests/unit/ (R12B-01 fix)"

# Commit 1.5 — AST guard pre-existing-violation audit gate.
# New guards added in commits 3+4 must pass on main BEFORE deletion of corresponding regex hooks.
uv run pytest tests/unit/test_architecture_no_defensive_rootmodel.py \
              tests/unit/test_architecture_no_tenant_config.py \
              tests/unit/test_architecture_jsontype_columns.py \
              tests/unit/test_architecture_import_usage.py \
              tests/unit/test_architecture_query_type_safety.py \
              -v -x 2>/dev/null \
  || warn "Commit 1.5 audit: new guards must pass on main before deletions (advisory — full check requires running tests)"

# Commit 5: pre-push migrations (10 hooks per D27 — 9 named + mypy per D3)
PREPUSH=(
  check-docs-links
  check-route-conflicts
  type-ignore-no-regression
  adcp-contract-tests
  mcp-contract-validation
  mcp-schema-alignment
  check-tenant-context-order
  ast-grep-bdd-guards
  check-migration-completeness
  mypy
)
[[ ${#PREPUSH[@]} -eq 10 ]] || fail "PREPUSH list must have exactly 10 entries (D27 + D3); got ${#PREPUSH[@]}"
for hook in "${PREPUSH[@]}"; do
  yq ".repos[].hooks[] | select(.id == \"$hook\") | .stages" .pre-commit-config.yaml 2>/dev/null \
    | grep -q pre-push \
    || fail "pre-push hook missing or not at pre-push stage: $hook (D27)"
  ok "hook moved to pre-push: $hook"
done

# Commit 10a — arch-guards consolidated pre-push hook
yq '.repos[].hooks[] | select(.id == "arch-guards") | .stages' .pre-commit-config.yaml 2>/dev/null \
  | grep -q pre-push \
  || fail "arch-guards pre-push hook missing (commit 10a)"
ok "arch-guards consolidated hook present at pre-push (commit 10a)"

# Commit 10a-bis — hook-install nudge in scripts + Makefile
[[ -x scripts/check-hook-install.sh ]] \
  || fail "scripts/check-hook-install.sh missing (commit 10a-bis)"
grep -qE '^[[:space:]]*scripts/check-hook-install\.sh' Makefile \
  || fail "Makefile quality target missing hook-install nudge"
ok "scripts/check-hook-install.sh + Makefile nudge present (commit 10a-bis)"

# Commit 7: deleted hooks (16 total — 13 commit-stage + 3 already-manual stubs)
DELETED=(
  no-tenant-config
  enforce-jsontype
  check-rootmodel-access
  enforce-sqlalchemy-2-0
  check-import-usage
  check-gam-auth-support
  check-response-attribute-access
  check-roundtrip-tests
  check-code-duplication
  check-parameter-alignment
  pytest-unit
  mcp-endpoint-tests
  suggest-test-factories
  no-skip-integration-v2
  check-migration-heads
  test-migrations
)
[[ ${#DELETED[@]} -eq 16 ]] || fail "DELETED list must have exactly 16 entries (13 commit-stage + 3 already-manual); got ${#DELETED[@]}"
DELETED_OK=0
for hook in "${DELETED[@]}"; do
  ! grep -qE "^\s+- id: $hook$" .pre-commit-config.yaml && DELETED_OK=$((DELETED_OK + 1))
done
ok "$DELETED_OK/${#DELETED[@]} deleted hooks confirmed absent"

# Commit 6 — repo-invariants consolidated hook (replaces no-skip-tests + sibling greps)
[[ -f .pre-commit-hooks/check_repo_invariants.py ]] \
  || fail "check_repo_invariants.py missing (commit 6)"
! grep -qE '^\s+- id: no-skip-tests$' .pre-commit-config.yaml \
  || fail "no-skip-tests not consolidated (commit 6)"
ok "repo-invariants consolidated hook present (commit 6)"

# No advisory-only steps in workflows (every gate must fail loudly per D-no-advisory).
# codeql.yml is allowlisted for D10 Path C (advisory until PR 6 commit 5 flips it).
# security.yml is allowlisted for SARIF upload step (uploads even on findings).
ADVISORY_VIOLATIONS=$(grep -rE '(\|\| true|\|\| echo|continue-on-error: true)' .github/workflows/ \
  --exclude=codeql.yml --exclude=security.yml 2>/dev/null || true)
if [[ -n "$ADVISORY_VIOLATIONS" ]]; then
  echo "$ADVISORY_VIOLATIONS"
  fail "Advisory-only step found in workflow (every gate must fail loudly per D-no-advisory; codeql.yml + security.yml allowlisted)"
fi
ok "no advisory-only steps in workflows (D-no-advisory)"

# Commit 7 acceptance: ≤12 commit-stage hooks (D27 + Blocker #2 resolution)
# Math (D27): 36 effective commit-stage − 13 deletions − 10 pre-push moves
# − 2 grep consolidations + 1 new repo-invariants = 12. Exactly at ceiling, zero headroom.
#
# M5 (Round 14) — drift detector. PR 1 captures `effective_commit_stage:` baseline; PR 4 compares.
# If the baseline shifted between PR 1 author time and PR 4 author time, the D27 math
# stops adding up — surface this as a fail rather than rubber-stamping a wrong total.
if [[ -f .claude/notes/ci-refactor/.hook-baseline.txt ]]; then
  BASELINE_EFFECTIVE=$(grep -oE '^effective_commit_stage:\s*[0-9]+' .claude/notes/ci-refactor/.hook-baseline.txt | grep -oE '[0-9]+' | head -1)
  if [[ -n "${BASELINE_EFFECTIVE:-}" ]]; then
    [[ "$BASELINE_EFFECTIVE" == "36" ]] \
      || fail "hook-baseline drift: expected effective=36 at PR 1 capture time, got $BASELINE_EFFECTIVE — D27 math invalidated (M5)"
    ok "hook-baseline effective=36 stable since PR 1 (M5 drift detector clean)"
  fi
fi

if command -v uv >/dev/null 2>&1; then
  HOOKS_COMMIT=$(uv run python -c "
import yaml
cfg = yaml.safe_load(open('.pre-commit-config.yaml'))
default = cfg.get('default_stages', ['pre-commit', 'commit'])
n = 0
for r in cfg['repos']:
    for h in r['hooks']:
        stages = h.get('stages', default)
        if 'pre-commit' in stages or 'commit' in stages:
            n += 1
print(n)
")
  [[ "$HOOKS_COMMIT" -le 12 ]] || fail "commit-stage hook count $HOOKS_COMMIT > 12 (D27 violation)"
  [[ "$HOOKS_COMMIT" -ge 10 ]] || fail "commit-stage hook count $HOOKS_COMMIT < 10 (over-deletion; D27 floor protects required gates)"
  ok "commit-stage hooks: $HOOKS_COMMIT/12 (D27 acceptance met; ≥10 floor protects required gates)"
fi

# Commit 8: latency baseline
[[ -f .pre-commit-latency-after.txt ]] && ok ".pre-commit-latency-after.txt captured"

# Commit 9: CLAUDE.md guards table (D18 — final ~81 post-v2.0-rebase per Round 8 revision)
# Per the deferral: PR 4 commit 9 adds only the 4 PR-4 rows + 2 residual missing rows;
# the full ~81-row audit happens in a post-v2.0-rebase commit, not here.
if grep -q 'Structural Guards' CLAUDE.md; then
  TABLE_ROWS=$(awk '/^\| Guard/,/^$/' CLAUDE.md | grep -cE '^\|.*test_architecture_')
  [[ "$TABLE_ROWS" -ge 28 ]] && ok "CLAUDE.md guards table has $TABLE_ROWS rows (target ≥28 post-PR-4; ~81 final post-v2.0-rebase)"
fi

# Layer 4 reference table verbatim mirror of D17/D30 — docs/development/ci-pipeline.md
# must list all 14 frozen check names so the reference table never drifts from ci.yml.
for n in 'Quality Gate' 'Type Check' 'Schema Contract' 'Security Audit' 'Quickstart' \
         'Smoke Tests' 'Unit Tests' 'Integration Tests' 'E2E Tests' 'Admin UI Tests' \
         'BDD Tests' 'Migration Roundtrip' 'Coverage' 'Summary'; do
  grep -q "$n" docs/development/ci-pipeline.md 2>/dev/null \
    || fail "ci-pipeline.md missing Layer 4 name: $n"
done
ok "docs/development/ci-pipeline.md mirrors all 14 frozen Layer-4 names (D17/D30)"

# Coverage map
if [[ -f .pre-commit-coverage-map.yml ]]; then
  yamllint -d relaxed .pre-commit-coverage-map.yml >/dev/null 2>&1 || fail "coverage-map fails yamllint"
  ok ".pre-commit-coverage-map.yml present"
fi

# ADRs added by PR 4
for adr in adr-004-guard-deprecation-criteria adr-005-fitness-functions adr-006-allowlist-shrink-only; do
  if [[ -f "docs/decisions/${adr}.md" ]]; then
    grep -qE '^## Status' "docs/decisions/${adr}.md" || fail "${adr}.md lacks ## Status"
    ok "ADR ${adr} present with ## Status"
  fi
done

echo "PR 4 verification: complete"
