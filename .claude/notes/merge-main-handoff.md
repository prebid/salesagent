# Merge Main Handoff — May 3, 2026

## Task
Merge `main` into `feature/media-buy-refactoring`. Main is 1 commit ahead (5ef141aa).

## CRITICAL: Merge Conflict Resolution Rules

1. **NEVER blindly accept "ours" or "theirs".** Each conflict must be resolved by understanding BOTH sides.
2. **NEVER resolve .beads/ conflicts.** If .beads/ has conflicts, stop and ask.
3. **Read both versions of each conflicting hunk** before choosing.
4. **Both sides may be correct** — the merge resolution often needs to combine changes from both.

## What main changed (5ef141aa)

A large refactor: "move billing policy and approval mode to tenant configuration (#1184)".

Key changes on main:
- **src/core/resolved_identity.py**: REMOVED `supported_billing` and `account_approval_mode` fields (dead fields, reads go through identity.tenant)
- **src/core/tenant_context.py**: ADDED `account_approval_mode` field, REMOVED `supported_billing` (moved to DB column)
- **src/core/tools/accounts.py**: Changed `_check_billing_policy` to read from `identity.tenant.get('supported_billing')`, simplified dual-key lookup
- **src/core/database/models.py**: Added `supported_billing` and `account_approval_mode` columns to Tenant
- **tests/harness/account_sync.py**: Added `set_billing_policy()` and `set_approval_mode()` that write to DB
- **tests/integration/test_sync_accounts.py**: Added 24 transport-matrix tests
- **tests/bdd/conftest.py**: Removed 5 UC-011 MCP xfails
- **tests/bdd/steps/domain/uc011_accounts.py**: Changed step helpers to use harness methods
- **pyproject.toml + uv.lock**: Bumped deps (pillow, pytest 9.0.3, python-multipart)

## What our branch changed in the SAME files

- **src/core/resolved_identity.py**: We ADDED `supported_billing` and `account_approval_mode` back (commit 65ae565b). Main REMOVED them. **Main is correct — these were moved to tenant config. Drop our additions.**
- **src/core/tenant_context.py**: We REMOVED `supported_billing` and `approval_mode`. Main ADDED `account_approval_mode` as a separate field. **Main is correct — take main's version.**
- **src/core/tools/accounts.py**: We changed auth error to `AdCPAuthRequiredError`. Main simplified billing lookup. **Both changes are needed — merge carefully.**
- **tests/harness/account_sync.py**: We removed the executor's broken `build_rest_body` override. Main added `set_billing_policy()` and `set_approval_mode()`. **Both changes are needed — our removal + main's additions.**
- **tests/bdd/conftest.py**: Both sides modified xfails extensively. **Most complex conflict — resolve per-section.**
- **tests/bdd/steps/domain/uc011_accounts.py**: Both sides changed step functions. **Merge carefully.**
- **pyproject.toml + uv.lock**: Both bumped deps. **Take the newer versions.**

## Merge strategy

```bash
git fetch origin main
git merge main --no-commit   # Merge without auto-committing
# Resolve each conflict manually
# After resolving:
make quality                  # Must pass
scripts/run-test.sh tests/bdd/ -k "uc011" --no-header -q  # UC-011 is the overlap area
./run_all_tests.sh            # Full suite
git commit -m "merge: integrate main (#1184 billing/approval tenant config)"
```

## Post-merge verification

1. `make quality` — 0 failed
2. `uv run python3 scripts/enumerate_bdd_issues.py test-results/latest/bdd.json` — 0 action items
3. `./run_all_tests.sh` — 0 failed across all suites
4. `uv run python3 scripts/detect_misrouted_transport.py test-results/latest/bdd.json` — no new misroutes

## Current branch state (pre-merge)

- 0 failed, 0 xpassed across all suites
- 5,200 BDD passed, 3,384 xfailed (all production gaps)
- Integration: 0 failed (was 48 at worst point, all fixed)
