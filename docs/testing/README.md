# 🧪 Testing Guide

Quick reference for running tests and maintaining quality.

## 🚀 Quick Commands

```bash
# Run all tests
uv run pytest

# By category
uv run pytest tests/unit/           # Fast, isolated
uv run pytest tests/integration/    # Database + services
uv run pytest tests/e2e/            # Full system

# With coverage
uv run pytest --cov=. --cov-report=html

# Check test quality
uv run python scripts/analyze_test_coverage.py       # What's not tested
uv run python scripts/detect_test_antipatterns.py    # Anti-patterns

# Pre-commit hooks (runs automatically)
pre-commit run --all-files
```

## 📊 Test Coverage Tracking

**Check current state**:
```bash
uv run python scripts/analyze_test_coverage.py
```

Shows:
- Coverage percentage for A2A skills
- List of untested functionality
- Over-mocking violations

**Track progress**: See GitHub issue #248

## 🛡️ Pre-Commit Hooks

Automatically enforced on every commit:

| Hook | What It Catches |
|------|----------------|
| `no-excessive-mocking` | More than 10 mocks in one test file |
| `detect-test-antipatterns` | Mocking internal handlers/implementations |
| `adcp-contract-tests` | Missing AdCP compliance tests |
| `mcp-contract-validation` | MCP tool parameter validation |

## 📚 Detailed Guides

### Protocol Compliance
- **[adcp-compliance.md](adcp-compliance.md)** - AdCP spec compliance testing (mandatory)
- **[link-validation.md](link-validation.md)** - Automatic link validation in integration tests (NEW ✨)

### Postmortems
- **[postmortems/2025-10-04-test-agent-auth-bug.md](postmortems/2025-10-04-test-agent-auth-bug.md)** - Authentication bug incident

### Tools
- **[tools/README.md](tools/README.md)** - Coverage and anti-pattern detection tools

## 🐛 Troubleshooting

**"Connection refused"**
```bash
docker-compose up -d
curl http://localhost:8166/health  # MCP server
curl http://localhost:8091/        # A2A server
```

**"Tests skipping"**
- Tests with `@pytest.mark.skip_ci` are skipped in CI
- Check markers with: `pytest --markers`

**"Schema validation errors"**
- E2E tests validate exact AdCP spec compliance
- Check field names, types, and required fields

## 📂 Test Organization

```
tests/
├── unit/          # Fast, isolated (mock external deps only)
├── integration/   # Database + services (real DB, mock external APIs)
├── e2e/          # Full system tests
└── ui/           # Admin UI tests

scripts/
├── analyze_test_coverage.py      # Coverage analysis
└── detect_test_antipatterns.py   # Anti-pattern detection
```

## 🎯 What to Mock

✅ **Always mock**:
- External APIs (Google Ad Manager, payment processors)
- Time-dependent functions (`datetime.now()`)
- Network calls (`requests.post()`)

❌ **Never mock**:
- Database operations (use real test DB)
- Internal business logic
- Internal function calls (tests won't catch bugs!)

## 🔍 Need More Help?

1. Check the scripts: They're self-documenting
2. Look at existing tests: Patterns are established
3. Pre-commit hooks: They'll tell you what's wrong
4. GitHub issue #248: Tracks ongoing test coverage work
