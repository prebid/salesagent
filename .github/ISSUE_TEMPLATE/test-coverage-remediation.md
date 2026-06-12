---
name: Test Coverage Remediation
about: Track progress on fixing test coverage gaps and over-mocking issues
title: 'Test Coverage: Fix over-mocking and add missing A2A skill tests'
labels: testing, tech-debt, priority-high
assignees: ''
---

## Problem

Our integration tests have two critical issues that allowed production bugs to slip through:

1. **Over-mocking anti-pattern**: Tests mock internal handlers instead of testing real code
2. **Incomplete coverage**: 15 of 18 AdCP skills have no A2A integration tests (17% coverage)

### Production Impact

- **Bug**: A2A authentication failed in production for `create_media_buy`
- **Root cause**: `get_principal_from_context()` didn't handle `ToolContext` objects
- **Why tests missed it**: Test mocked `_handle_create_media_buy_skill()` - the function containing the bug

**Full analysis**: [docs/testing/coverage-analysis.md](../../docs/testing/coverage-analysis.md)

## Current State

Run: `uv run python scripts/analyze_test_coverage.py`

```
📊 COVERAGE SUMMARY
   Implemented skills: 18
   Tested skills:      3 (17%)
   Coverage:           17%

❌ UNTESTED SKILLS (15)
   • get_media_buy_delivery  ← CAUSED PRODUCTION BUG
   • update_media_buy
   • update_performance_index
   • list_creative_formats
   • list_authorized_properties
   • sync_creatives
   • list_creatives
   • add_creative_assets
   • approve_creative
   • assign_creative
   • create_creative
   • get_creatives
   • get_media_buy_status
   • optimize_media_buy
   • search_signals

⚠️  OVER-MOCKING VIOLATIONS (6)
   File: tests/integration/test_a2a_skill_invocation.py
   Lines: 265, 317, 369, 408, 462, 463
   Issue: Mock internal handlers instead of external dependencies
```

## Remediation Plan

### Phase 1: Fix Over-Mocking (Week 1) 🔥 HIGH PRIORITY

**File**: `tests/integration/test_a2a_skill_invocation.py`

Fix 6 violations by changing:
```python
# ❌ BEFORE: Mocks internal handler
with patch.object(handler, "_handle_get_products_skill"):
    mock.return_value = {...}

# ✅ AFTER: Mock only external dependencies
with patch("src.adapters.get_adapter"), \
     patch("src.core.database.get_db_session"):
    # Real handler code runs - would catch bugs!
```

**Lines to fix**: 265, 317, 369, 408, 462, 463

**Test template**: [docs/testing/preventing-over-mocking.md#integration-test-template](../../docs/testing/preventing-over-mocking.md#integration-test-template)

### Phase 2: Add Missing Tests (Week 2)

**Priority 1** (Caused production bugs):
- [ ] `test_get_media_buy_delivery_spec_compliant()` - Accept plural `media_buy_ids`
- [ ] `test_get_media_buy_delivery_backward_compatible()` - Support singular for legacy

**Priority 2** (Core AdCP endpoints):
- [ ] `test_update_media_buy_integration()`
- [ ] `test_update_performance_index_integration()`
- [ ] `test_list_creative_formats_integration()`
- [ ] `test_list_authorized_properties_integration()`

**Priority 3** (Creative management):
- [ ] `test_sync_creatives_integration()`
- [ ] `test_list_creatives_integration()`
- [ ] `test_add_creative_assets_integration()`
- [ ] `test_create_creative_integration()`
- [ ] `test_get_creatives_integration()`
- [ ] `test_assign_creative_integration()`

**Priority 4** (Complete coverage):
- [ ] `test_approve_creative_integration()`
- [ ] `test_get_media_buy_status_integration()`
- [ ] `test_optimize_media_buy_integration()`
- [ ] `test_search_signals_integration()`

### Phase 3: CI Enforcement (Week 3)

- [ ] Add coverage analysis to GitHub Actions
- [ ] Require 100% A2A skill coverage for new PRs
- [ ] Block merges with over-mocking violations
- [ ] Add test coverage badge to README

## Success Criteria

- ✅ Zero over-mocking violations in `test_a2a_skill_invocation.py`
- ✅ 100% of AdCP skills have integration tests (18/18)
- ✅ All tests use spec-compliant parameter names
- ✅ Coverage analysis runs automatically in CI
- ✅ Pre-commit hook prevents new violations

## Tools & Documentation

### Analysis Tools
```bash
# Check current coverage
uv run python scripts/analyze_test_coverage.py

# Check for anti-patterns
uv run python scripts/detect_test_antipatterns.py tests/integration/test_a2a_skill_invocation.py

# Pre-commit hook runs automatically
git commit  # Detects violations
```

### Documentation
- **[docs/testing/preventing-over-mocking.md](../../docs/testing/preventing-over-mocking.md)** - How to write proper tests
- **[docs/testing/coverage-analysis.md](../../docs/testing/coverage-analysis.md)** - Why tests missed bugs
- **[docs/testing/remediation-plan.md](../../docs/testing/remediation-plan.md)** - Detailed plan

### Pre-Commit Hook
Already configured in `.pre-commit-config.yaml`:
```yaml
- id: detect-test-antipatterns
  entry: uv run python scripts/detect_test_antipatterns.py
  files: '^tests/.*\.py$'
```

## Testing Philosophy

From our `CLAUDE.md`:

> **1. Less Mocking ≠ Worse Tests**
> Over-mocking hides real bugs. Mock external I/O, not internal logic.

> **2. Integration Tests Matter**
> HTTP-level behavior can't be unit tested. Test full request → response.

> **3. Test What You Import**
> If you import it, test that it works. Don't mock it away.

## Acceptance Criteria

This issue is complete when:

1. **All over-mocking violations fixed** (6/6)
   ```bash
   uv run python scripts/detect_test_antipatterns.py tests/integration/test_a2a_skill_invocation.py
   # Output: ✅ No anti-patterns detected
   ```

2. **All skills have tests** (18/18)
   ```bash
   uv run python scripts/analyze_test_coverage.py
   # Output: Coverage: 100%
   ```

3. **CI enforces standards**
   - Coverage analysis runs on every PR
   - PRs fail if coverage drops below 100%
   - New violations blocked by pre-commit hook

4. **Documentation updated**
   - Test examples use correct patterns
   - Remediation plan marked complete
   - README shows 100% coverage

## Timeline

- **Week 1**: Fix 6 over-mocking violations + add Priority 1 tests
- **Week 2**: Add Priority 2-3 tests (10 tests)
- **Week 3**: Add Priority 4 tests + CI enforcement (4 tests)

Total: ~3 weeks for complete remediation

## Questions?

- Review [docs/testing/preventing-over-mocking.md](../../docs/testing/preventing-over-mocking.md)
- Run analysis tools to see what needs work
- Ask in #engineering Slack channel

---

**Labels**: `testing`, `tech-debt`, `priority-high`
**Milestone**: Q1 2025
**Estimated effort**: 3 weeks
