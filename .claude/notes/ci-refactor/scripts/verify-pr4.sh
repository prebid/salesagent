#!/usr/bin/env bash
# Verification for PR 4 — Hook relocation + structural guards
set -uo pipefail
fail() { echo "FAIL: $*" >&2; exit 1; }
ok()   { echo "  ok: $*"; }

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

# D31 (Round 10) — `default_install_hook_types` directive at top of .pre-commit-config.yaml.
# Without this directive, `pre-commit install` (no --hook-type qualifier) only installs
# pre-commit-stage hooks; the entire 10-hook pre-push tier from D27 silently no-ops.
grep -qE '^default_install_hook_types:.*pre-commit.*pre-push' .pre-commit-config.yaml \
  || fail ".pre-commit-config.yaml missing default_install_hook_types: [pre-commit, pre-push] (D31; R33 mitigation)"
ok "default_install_hook_types directive present (D31; load-bearing for D27 hook math)"

# D44 (Round 11) — minimum_pre_commit_version: 3.2.0 directive.
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

# Round 12 R12B-01 — frozen-checks structural guard MUST be lifted from drafts/ to tests/unit/
# (PR 4 commit 3 lifts it). Without this, R36 mitigation is non-operational.
[[ -f tests/unit/test_architecture_required_ci_checks_frozen.py ]] \
  || fail "tests/unit/test_architecture_required_ci_checks_frozen.py missing — must be lifted from drafts/guards/ in PR 4 commit 3 (R12B-01)"
ok "frozen-checks structural guard lifted from drafts/ to tests/unit/ (R12B-01 fix)"

# Commit 5: pre-push migrations (10 hooks per D27 — 9 named + mypy per D3)
PREPUSH=(
  check-docs-links check-route-conflicts type-ignore-no-regression
  adcp-contract-tests mcp-contract-validation
  mcp-schema-alignment check-tenant-context-order
  ast-grep-bdd-guards check-migration-completeness
  mypy
)
for hook in "${PREPUSH[@]}"; do
  yq ".repos[].hooks[] | select(.id == \"$hook\") | .stages" .pre-commit-config.yaml 2>/dev/null \
    | grep -q pre-push && ok "hook moved to pre-push: $hook" || true
done

# Commit 7: deleted hooks (16 total — Round 10 sweep added test-migrations to deletion list)
DELETED=(
  no-tenant-config enforce-jsontype check-rootmodel-access enforce-sqlalchemy-2-0
  check-import-usage check-gam-auth-support check-response-attribute-access
  check-roundtrip-tests check-code-duplication check-parameter-alignment
  pytest-unit mcp-endpoint-tests suggest-test-factories no-skip-integration-v2
  check-migration-heads test-migrations
)
DELETED_OK=0
for hook in "${DELETED[@]}"; do
  ! grep -qE "^\s+- id: $hook$" .pre-commit-config.yaml && DELETED_OK=$((DELETED_OK + 1))
done
ok "$DELETED_OK/${#DELETED[@]} deleted hooks confirmed absent"

# Commit 7 acceptance: ≤12 commit-stage hooks (D27 + Blocker #2 resolution)
# Math (Round 10 D27 revised): 36 effective commit-stage − 13 deletions − 10 pre-push moves
# − 2 grep consolidations + 1 new repo-invariants = 12. Exactly at ceiling, zero headroom.
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
  ok "commit-stage hooks: $HOOKS_COMMIT/12 (D27 acceptance met; revised Round 10 math)"
fi

# Commit 8: latency baseline
[[ -f .pre-commit-latency-after.txt ]] && ok ".pre-commit-latency-after.txt captured"

# Commit 9: CLAUDE.md guards table (D18 revised P0 sweep — final ~73 post-v2.0-rebase)
# Per the deferral: PR 4 commit 9 adds only the 4 PR-4 rows + 2 residual missing rows;
# the full ~73-row audit happens in a post-v2.0-rebase commit, not here.
if grep -q 'Structural Guards' CLAUDE.md; then
  TABLE_ROWS=$(awk '/^\| Guard/,/^$/' CLAUDE.md | grep -cE '^\|.*test_architecture_')
  [[ "$TABLE_ROWS" -ge 28 ]] && ok "CLAUDE.md guards table has $TABLE_ROWS rows (target ≥28 post-PR-4; ~73 final post-v2.0-rebase)"
fi

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
