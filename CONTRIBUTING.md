# Contributing to Prebid Sales Agent

Thanks for your interest in contributing! Full contributor workflow lives at:
**[`docs/development/contributing.md`](docs/development/contributing.md)** (canonical).

## Quick start

1. Fork and clone the repo.
2. Install dev dependencies: `uv sync --group dev`
3. Install both pre-commit hook stages:
   ```bash
   pre-commit install --hook-type pre-commit --hook-type pre-push
   ```
4. See `docs/development/contributing.md` for branch naming, testing, PR review process.

## PR title format (Conventional Commits)

PR titles MUST use one of these prefixes (release-please uses them to generate changelogs):

- `feat:` — new functionality (Features section)
- `fix:` — bug fix (Bug Fixes section)
- `refactor:` — code refactoring (Code Refactoring section)
- `docs:` — documentation only
- `chore:` — maintenance / dependencies (hidden from changelog)
- `perf:` — performance improvements

Without a recognized prefix, the change ships but won't appear in release notes.

## Reporting security issues

See [SECURITY.md](SECURITY.md) — please use private vulnerability reporting, NOT public issues.
