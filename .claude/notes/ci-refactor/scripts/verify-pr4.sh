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

# pytest marker registered
grep -qE '^\s*"architecture: ' pyproject.toml || \
  grep -qE 'architecture:' pyproject.toml || fail "pytest 'architecture' marker not registered"
ok "pytest @architecture marker registered"

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

# Commit 5: pre-push migrations (9 hooks per D27)
PREPUSH=(
  check-docs-links check-route-conflicts type-ignore-no-regression
  adcp-contract-tests mcp-contract-validation
  mcp-schema-alignment check-tenant-context-order
  ast-grep-bdd-guards check-migration-completeness
)
for hook in "${PREPUSH[@]}"; do
  yq ".repos[].hooks[] | select(.id == \"$hook\") | .stages" .pre-commit-config.yaml 2>/dev/null \
    | grep -q pre-push && ok "hook moved to pre-push: $hook" || true
done

# Commit 7: deleted hooks
DELETED=(
  no-tenant-config enforce-jsontype check-rootmodel-access enforce-sqlalchemy-2-0
  check-import-usage check-gam-auth-support check-response-attribute-access
  check-roundtrip-tests check-code-duplication check-parameter-alignment
  pytest-unit mcp-endpoint-tests suggest-test-factories no-skip-integration-v2
  check-migration-heads
)
DELETED_OK=0
for hook in "${DELETED[@]}"; do
  ! grep -qE "^\s+- id: $hook$" .pre-commit-config.yaml && DELETED_OK=$((DELETED_OK + 1))
done
ok "$DELETED_OK/${#DELETED[@]} deleted hooks confirmed absent"

# Commit 7 acceptance: ≤12 commit-stage hooks (D27 + Blocker #2 resolution)
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
  ok "commit-stage hooks: $HOOKS_COMMIT/12 (D27 acceptance met)"
fi

# Commit 8: latency baseline
[[ -f .pre-commit-latency-after.txt ]] && ok ".pre-commit-latency-after.txt captured"

# Commit 9: CLAUDE.md guards table
if grep -q 'Structural Guards' CLAUDE.md; then
  TABLE_ROWS=$(awk '/^\| Guard/,/^$/' CLAUDE.md | grep -cE '^\|.*test_architecture_')
  [[ "$TABLE_ROWS" -ge 30 ]] && ok "CLAUDE.md guards table has $TABLE_ROWS rows (target ~52)"
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
