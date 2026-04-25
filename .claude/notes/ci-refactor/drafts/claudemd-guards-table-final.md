# Artifact 7 — Full corrected CLAUDE.md guards table (post-rollout)

This replaces the existing table in `CLAUDE.md` under "Structural Guards". Section headers chosen to group related invariants; rows are alphabetical within each section. Final post-PR-6 + v2.0 state. Total: **52 rows**.

```markdown
### Structural Guards (Automated Architecture Enforcement)

AST-scanning tests enforce architecture invariants on every `make quality` run. New violations fail the build immediately. See [docs/development/structural-guards.md](docs/development/structural-guards.md) for full details.

#### Schema patterns

| Guard | Enforces | Test File |
|-------|----------|-----------|
| Explicit nested serialization | Parent models with nested-model fields override `model_dump()` | `test_architecture_explicit_nested_serialization.py` |
| Schema inheritance | Schemas extend adcp library base types | `test_architecture_schema_inheritance.py` |

#### Transport boundary

| Guard | Enforces | Test File |
|-------|----------|-----------|
| Boundary completeness | MCP/A2A wrappers pass all `_impl` parameters | `test_architecture_boundary_completeness.py` |
| No model_dump in _impl | `_impl` returns model objects, never calls `.model_dump()` | `test_architecture_no_model_dump_in_impl.py` |
| No ToolError in _impl | `_impl` raises `AdCPError`, never `ToolError` | `test_no_toolerror_in_impl.py` |
| ResolvedIdentity in _impl | `_impl` accepts `ResolvedIdentity`, not `Context` | `test_impl_resolved_identity.py` |
| Transport-agnostic _impl | `_impl` has zero transport imports | `test_transport_agnostic_impl.py` |

#### Database access

| Guard | Enforces | Test File |
|-------|----------|-----------|
| JSONType columns | JSON columns use `JSONType`, not `JSON` | `test_architecture_jsontype_columns.py` |
| Migration completeness | Every migration has non-empty `upgrade()` and `downgrade()` | `test_architecture_migration_completeness.py` |
| No defensive RootModel | No defensive `hasattr(x, "root")` outside a2a-sdk allowlist | `test_architecture_no_defensive_rootmodel.py` |
| No direct DB access | No `get_db_session()` / `session.add()` outside repositories | `test_architecture_repository_pattern.py` |
| No production session.add | Production code never calls `session.add()` | `test_architecture_production_session_add.py` |
| No raw MediaPackage select | All MediaPackage access goes through repository | `test_architecture_no_raw_media_package_select.py` |
| No raw select outside repos | All ORM queries via repositories, not raw `select()` | `test_architecture_no_raw_select.py` |
| No tenant_config column access | No `tenant.config["..."]` — use per-field columns | `test_architecture_no_tenant_config.py` |
| Query type safety | DB queries use types matching column definitions; no `session.query()` | `test_architecture_query_type_safety.py` |
| Single migration head | Alembic graph has exactly one head | `test_architecture_single_migration_head.py` |
| Workflow tenant isolation | `WorkflowRepository` queries join `DBContext` for tenant scoping | `test_architecture_workflow_tenant_isolation.py` |

#### BDD

| Guard | Enforces | Test File |
|-------|----------|-----------|
| BDD no dict registry | Given steps use factories, not raw dicts | `test_architecture_bdd_no_dict_registry.py` |
| BDD no direct call_impl | BDD steps dispatch via transport, not call `_impl` directly | `test_architecture_bdd_no_direct_call_impl.py` |
| BDD no duplicate steps | No 3+ step functions with identical bodies | `test_architecture_bdd_no_duplicate_steps.py` |
| BDD no-op Then steps | Then steps must assert, not delegate to `_pending()` | `test_architecture_bdd_no_pass_steps.py` |
| BDD no silent env | No `ctx.get("env")` / `hasattr(env, ...)` in step functions | `test_architecture_bdd_no_silent_env.py` |
| BDD obligation sync | BDD scenarios stay in sync with `docs/test-obligations/` | `test_architecture_bdd_obligation_sync.py` |
| BDD trivial assertions | Then steps compare values, not just check truthiness | `test_architecture_bdd_no_trivial_assertions.py` |

#### Test integrity

| Guard | Enforces | Test File |
|-------|----------|-----------|
| Code duplication (DRY) | Duplicate-block count in `src/` and `tests/` cannot increase | `check_code_duplication.py` (pre-commit + `make quality`) |
| Import usage | Every imported symbol is referenced in the same module | `test_architecture_import_usage.py` |
| No silent except | No `except Exception: pass`/`continue` without logging | `test_architecture_no_silent_except.py` |
| No split mock assertions | Tests use `assert_called_once_with()`, not split assertions | `test_architecture_weak_mock_assertions.py` |
| Obligation coverage | Behavioral obligations have matching tests | `test_architecture_obligation_coverage.py` |
| Obligation test quality | Obligation-tagged tests CALL production code, not just import it | `test_architecture_obligation_test_quality.py` |
| Test marker coverage | Every test file has the right entity marker | `test_architecture_test_marker_coverage.py` |

#### Governance / CI

| Guard | Enforces | Test File |
|-------|----------|-----------|
| ADRs exist | All referenced ADR files exist with required sections | `test_architecture_adrs_exist.py` |
| CLAUDE.md table complete | Every guard test file has a row in CLAUDE.md table | `test_architecture_claudemd_table_complete.py` |
| Governance files | `CODEOWNERS`, `SECURITY.md`, `CONTRIBUTING.md` exist with required content | `test_architecture_governance_files.py` |
| GitHub Actions SHA-pinned | All `uses:` refs in workflows are SHA-pinned | `test_architecture_actions_sha_pinned.py` |
| No advisory CI | No `\|\| true` / `continue-on-error: true` on lint/test steps | `test_architecture_no_advisory_ci.py` |
| Pre-commit coverage map | `.pre-commit-coverage-map.yml` shape correct | `test_architecture_precommit_coverage_map.py` |
| Pre-commit hook count | Commit-stage hook count ≤ 12 | `test_architecture_pre_commit_hook_count.py` |
| Pre-commit no additional_deps | No project libs in `additional_dependencies:` | `test_architecture_pre_commit_no_additional_deps.py` |
| Pre-commit SHA-frozen | All external `rev:` are 40-char SHAs with `# frozen:` comments | `test_architecture_precommit_sha_frozen.py` |
| Required CI checks frozen | `ci.yml` declares all 11 frozen check names per D17 | `test_architecture_required_ci_checks_frozen.py` |
| Workflow checkout persist-credentials | `actions/checkout` uses `persist-credentials: false` | `test_architecture_persist_credentials_false.py` |
| Workflow concurrency | Each workflow declares concurrency w/ PR cancel-in-progress | `test_architecture_workflow_concurrency.py` |
| Workflow permissions | Each workflow + job has restrictive `permissions:` | `test_architecture_workflow_permissions.py` |
| Workflow timeout-minutes | Each non-reusable job declares `timeout-minutes:` | `test_architecture_workflow_timeout_minutes.py` |

#### Cross-file anchor consistency

| Guard | Enforces | Test File |
|-------|----------|-----------|
| Architecture helpers | Shared `_architecture_helpers.py` API contract | `test_architecture_helpers.py` |
| Postgres version anchor | `postgres:<tag>` is identical across all CI + compose | `test_architecture_postgres_version_anchor.py` |
| Python version anchor | Python version is identical across `.python-version`, Dockerfile, mypy, ruff, black | `test_architecture_python_version_anchor.py` |
| uv version anchor | `UV_VERSION` matches between Dockerfile and `setup-env` action | `test_architecture_uv_version_anchor.py` |

**Rules for guards:**
- Allowlists can only shrink — never add new violations, fix them instead
- Every allowlisted violation has a `# FIXME(salesagent-xxxx)` comment at the source location
- When you fix a violation, remove it from the allowlist (the stale-entry test will catch you)
- Guard retirement follows ADR-004; choosing pytest-vs-tool follows ADR-005; allowlist mechanics follow ADR-006
```

---
