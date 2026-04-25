### C3 — Two Dependabot PRs both modify `uv.lock` and merge within 5 min


**Trigger**: Dependabot opens 3+ PRs simultaneously; reviewer merges two within minutes; the second merge has a stale `uv.lock` — its CI passed against an older base.
**Severity**: P2.
**Detection time**: minutes (post-merge `make quality` on a fresh checkout fails).
**Affected PR(s)**: any post-PR-1.

**Symptoms**
- Main is unmergeable for new PRs: `uv.lock` references a version main's `pyproject.toml` no longer pins exactly.
- `uv sync --frozen` fails on main checkouts.

**Verification**
```bash
git checkout main && git pull
uv sync --frozen --group dev   # Should error with "lock out of date"
```

**Immediate response (first 15 min)**
1. **Stop merging** any further Dependabot PRs until lock is fresh.
2. On a fresh branch from main:
   ```bash
   uv lock
   git add uv.lock
   git commit -m "chore: refresh uv.lock after concurrent dependabot merges"
   ```
3. Open a fast-track PR. Force-rerun CI. Merge once green.

**Stabilization (next 1-4 hours)**
1. Resume Dependabot review queue, but **serialize merges** for the rest of the day — wait for CI green on main before merging the next.

**Recovery (longer-term)**
- Consider `dependabot.yml` `groups:` directive to coalesce same-day bumps into one PR (already in PR 1's config).

**Post-incident**
- File no issue unless this recurs.
- Risk register: this is a sub-case of R9 (Dependabot deluge).

**Why this happens (root cause)**
Each Dependabot PR's CI ran against the base SHA at PR-open time. Merging two in quick succession means the second's lock was generated against the pre-first-merge base. GitHub's branch protection requires CI green but doesn't enforce up-to-date-with-base by default.

**Related scenarios**
- See also: C4, R9 (deluge), D5 (auto-merge would amplify this — explicitly forbidden).

---
