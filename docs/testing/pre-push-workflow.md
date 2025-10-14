# Pre-Push Testing Workflow

## Overview

The pre-push git hook automatically runs tests before allowing code to be pushed to the remote repository. This ensures that all code pushed to GitHub has been validated locally first.

## Current Configuration

**Mode**: CI (Full test suite with PostgreSQL)

The hook runs:
```bash
./run_all_tests.sh ci
```

This includes:
- ✅ Unit tests (fast, no external dependencies)
- ✅ Integration tests (database required)
- ✅ E2E tests (Docker + PostgreSQL)

**Runtime**: ~3-5 minutes

## Why CI Mode?

**Previous configuration** ran "quick" mode which skipped E2E tests. This led to:
- ❌ E2E test failures only caught in GitHub Actions
- ❌ Slow feedback loop (10+ minutes to discover issues)
- ❌ Wasted CI resources

**Current configuration** runs full CI suite locally:
- ✅ Catches E2E issues before push
- ✅ Faster feedback (local failures < 5min vs CI failures > 10min)
- ✅ Reduces GitHub Actions usage
- ✅ Prevents broken commits from reaching remote

## Development Workflow

### During Active Development

For rapid iteration, run quick tests manually:
```bash
./run_all_tests.sh quick  # ~40 seconds
```

### Before Pushing

The pre-push hook automatically runs:
```bash
./run_all_tests.sh ci     # ~3-5 minutes
```

**If tests fail**, you have two options:

1. **Fix the issues** (recommended):
   ```bash
   # Fix the code, then try again
   git push origin your-branch
   ```

2. **Skip validation** (not recommended):
   ```bash
   git push --no-verify origin your-branch
   ```

## Bypass Hook (Emergency Only)

If you must push without running tests (e.g., CI is broken, documentation-only change):

```bash
git push --no-verify origin your-branch
```

**⚠️ Use sparingly**: This bypasses ALL validations, including schema sync checks.

## Updating the Hook

The pre-push hook is in `.git/hooks/pre-push` (not tracked by git).

To update for all developers:
1. Modify the hook in `.git/hooks/pre-push`
2. Document changes in this file
3. Notify team to run: `./scripts/setup/setup_hooks.sh`

## Related Configuration

- **Pre-commit hooks**: `.pre-commit-config.yaml`
- **Test runner**: `./run_all_tests.sh`
- **CI config**: `.github/workflows/test.yml`

## Troubleshooting

### Tests are slow

**Problem**: CI mode tests take 3-5 minutes.

**Solutions**:
- Use `quick` mode during development
- Pre-push hook runs CI automatically (only when pushing)
- Consider upgrading hardware (Docker needs resources)

### PostgreSQL container issues

**Problem**: Tests fail with "Connection refused" to PostgreSQL.

**Solutions**:
```bash
# Clean up Docker state
docker-compose down -v
docker system prune -f

# Restart Docker Desktop
# Then try tests again
./run_all_tests.sh ci
```

### Want to skip E2E tests temporarily

**Not recommended**, but for local iteration:
```bash
# During development only
./run_all_tests.sh quick

# Pre-push still runs full CI (as designed)
```

## History

- **2025-10-13**: Changed from "quick" to "ci" mode
  - Reason: E2E test failures were only caught in GitHub Actions
  - Impact: Catches schema mismatches and integration issues before push
  - Trade-off: Slower push (~3-5min) but faster overall feedback
