> **Status:** Tier 1 (restore the gate) + most of Tiers 2 / 4 / 5 are **absorbed by #1234**. Tier 3 (permanent-amnesty cleanup) and a handful of follow-ups remain. Close this issue when #1234 merges + the residual items are resolved (see "Closure plan" below).
> **Read order:** Status table → Residual scope → expand `<details>` for the original 30-finding catalog and historical context.

## TL;DR

CI looks strict; it isn't. Three independent leaks ("Soft Gate") allow stated quality invariants (DRY, No Quiet Failures, Test Integrity, type ratcheting, structural guards) to drift without enforcement. Downstream of that softness, ratcheting baselines have drifted, hook environments diverged from the runtime, and dead config is being cargo-culted through files.

**#1234's CI/pre-commit refactor closes the soft-gate root cause and ~14 of the 30 findings here.** The remaining ~16 are mostly:
- **Tier 3 architectural-debate items** — the "fix incrementally / never gets fixed" ruff ignores + mypy lenient flags + 301-entry obligation allowlist
- **A few misc cleanup items** that fell outside #1234's scope (orphan `.mypy_baseline`, `google_ad_manager_original` phantom refs, `.type-ignore-baseline` rebaseline, uv cache key)
- **One critical residual P0** that #1234 partially addresses but doesn't fully close: `release-please publish-docker` still doesn't `needs: [test-summary]` — red main can ship images.

## Status of each finding (post-#1234 planning)

### Cluster A — Soft Gate (meta root cause)

| # | Finding | Status | Closed by |
|---|---|---|---|
| A1 | `test-summary` omits `smoke-tests` from failure aggregation | ✅ Absorbed | #1234 PR 3 commit 3 — new `Summary` job's `needs:` covers all 14 frozen checks including `Smoke Tests` |
| A2 | Ruff lint CI-disabled (`\|\| true` + `continue-on-error: true`) | ✅ Absorbed | #1234 PR 3 commit 7 (also closes #1233 D6) |
| A3 | Zero of 27 pre-commit hooks run in CI | ✅ Absorbed | #1234 PR 3 commit 3 — `Quality Gate` job runs `pre-commit run --all-files` (also closes #1233 D5) |
| **A4** | `release-please publish-docker` ships without waiting for tests | ⚠️ **Open — P0** | #1234 PR 6 commit 2 EXTENDS publish-docker (cosign, SBOM, Trivy, SOURCE_DATE_EPOCH) but **does NOT add `needs: [test-summary]` / `needs: [ci]`**. Red main can still ship. Fix in PR 6 or a tiny pre-PR-6 commit. |
| A5 | 5 jobs missing `timeout-minutes` | 🟡 Partial | #1234 PR 3 sets timeouts on Schema Contract / Unit / Integration / E2E / Admin / BDD; **Round 11 R11E-04 caught that Quality Gate / Type Check / Migration Roundtrip / Coverage / Summary still inherit the 360-min default**. Add explicit `timeout-minutes` per spec in PR 3 commit. |
| A6 | Coverage floor 30% only in tox, not CI | ✅ Absorbed | #1234 PR 3 commit 6 — `.coverage-baseline=53.5` hard-gated via `--fail-under` in CI (per #1234 D11) |

### Cluster B — Ratcheting baselines drifting

| # | Finding | Status | Closed by |
|---|---|---|---|
| **B1** | `.type-ignore-baseline=42` vs actual 54 (+12 drift) | ⚠️ **Open** | Rebaseline to actual; reuse the same ratchet pattern. Not in #1234 scope. |
| B2-B5 | mypy hook deps unpinned/stale (`sqlalchemy`, `fastmcp`, `alembic`, `types-*`) | ✅ Absorbed | #1234 PR 2 — replaces `mirrors-mypy` with local `uv run mypy` hook (`language: system`); eliminates `additional_dependencies` entirely; deps come from `uv.lock`. The whole class of pin-drift is structurally fixed. |
| B6 | black rev `25.1.0` missing CVE fix (GHSA-3936-cmfr-pm3m) | ✅ Absorbed | #1234 PR 2 — replaces `psf/black` with local `uv run black` hook (uses `uv.lock` resolved version) |
| B7 | `.duplication-baseline` drift (`{src: 44, tests: 109}`) | 🟡 Audit | #1234 PR 4 commit 7 deletes the `check-code-duplication` pre-commit hook (per #1234 D17); CI absorbs the check via `Quality Gate`. The baseline itself wasn't audited; verify post-#1234 that the count holds. |

### Cluster C — External dependency resilience

| # | Finding | Status | Closed by |
|---|---|---|---|
| C1 | E2E pulls schema URL with no cache/retry/offline | ✅ Absorbed | #1234 PR 3 commit 10 (also closes #1233 D10). #1213 is the deeper root-cause fix. |
| C2 | Creative integration rebuilds adcp monolith every run | 🟡 Tracked elsewhere | #1234 PR 3 commit 9 (per D32+D39) keeps the build-from-tarball pattern (matches `test.yml:180-223` disk truth). **#1189 owns the caching/registry-pull migration.** Out of #1234's scope. |
| C3 | Postgres v15/v16/v17 drift across workflows | ✅ Absorbed | #1234 PR 5 commit 1 — unify to `postgres:17-alpine` everywhere (per #1234 D34); `test_architecture_uv_version_anchor` analog catches future drift |
| C4 | `test.yml:187` uses bare `docker build` (no GHA cache) while `release-please` uses cached `build-push-action@v5` | ✅ Absorbed | #1234 PR 6 commit 2 — `build-push-action@v7.1.0` with `cache-from: type=gha` + `cache-to: type=gha,mode=max` |
| **C5** | uv cache key hashes only `uv.lock`; `pyproject.toml` changes → stale cache | ⚠️ **Open** | Trivial fix (`hashFiles('**/uv.lock', '**/pyproject.toml')`). Could fold into #1234 PR 3 setup-env composite or ship as a tiny standalone. |
| C6 | No `.github/dependabot.yml` | ✅ Absorbed | #1234 PR 1 commit 4 (per D5/D16) — pip / pre-commit / gh-actions / docker, weekly grouped, **no auto-merge** |
| **C7** | Persistent `Security Audit` failures (pillow, pytest, python-multipart) — no tracking issue | ⚠️ **Open** | File a separate sub-issue tracking each `--ignore-vulns` allowlist entry with a "remove when fixed upstream" condition. #1234 PR 3 preserves the `Security Audit` job (per D30) but doesn't audit the allowlist. |

### Cluster D — Supply chain exposure

| # | Finding | Status | Closed by |
|---|---|---|---|
| D1 | Every third-party Action tag-pinned, never SHA | ✅ Absorbed | #1234 PR 1 commit 9 — `pre-commit autoupdate --freeze` + SHA-freeze of all 23 `uses:` refs in workflows; `.github/.action-shas.txt` artifact captures resolved SHAs |
| **D2** | `ipr-agreement.yml` `pull_request_target` + broad write perms + tag-pinned third-party action | 🟡 Partial — needs verification | #1234 PR 1 commit 11 narrows ipr-agreement.yml permissions per Round 10 SF-10. Confirm the narrow per-job perms actually land before closing this finding. |

### Cluster E — Dead / phantom config (cargo cult)

| # | Finding | Status | Action |
|---|---|---|---|
| **E1** | `google_ad_manager_original.py` excluded in `mypy.ini:54-56` + `.pre-commit-config.yaml:305` but file doesn't exist | ⚠️ **Open** | Trivial; can fold into #1234 PR 2 (which rewrites mirrors-mypy block) or ship standalone. |
| **E2** | `.mypy_baseline` orphan file (zero readers since `806769b2`) | ⚠️ **Open** | `git rm .mypy_baseline`. Standalone PR; ~30s of work. |

### Cluster F — Permanent amnesty disguised as "incremental"

| # | Finding | Status | Decision needed |
|---|---|---|---|
| **F1** | 14 ruff rules ignored with stale violation counts (`C901: 216`, `PLR0911-15`, `PLR2004: 158`, `B904: 47`, `F403: 8`) | ⚠️ **Open — architectural** | Per ignored rule: (a) introduce a violation-count baseline file + ratcheting hook, OR (b) strip "fix incrementally" language and accept the exemption permanently. Tier 3 of the original plan; not in #1234 scope. |
| **F2** | `mypy.ini:9-12` TODO block (`check_untyped_defs=True (431 errors)`, etc.) — last touched 2025-12-08 (4.5 months static) | ⚠️ **Open — architectural** | Same decision tree: ratchet or accept. |

### Cluster G — Duplicated / conflicting tooling

| # | Finding | Status | Closed by |
|---|---|---|---|
| G1 | Two Python formatters active (`black==25.1.0` in pre-commit + `black>=26.3.1` runtime + `ruff format` in `make quality`) | 🟡 Partial | #1234 PR 2 makes black local (resolves the version mismatch). Single-formatter migration to `ruff format` is in **ADR-008 follow-up** per #1234 D28. |
| **G2** | Obligation coverage allowlist has 301 entries; no decrement mechanism | ⚠️ **Open — architectural** | Largest stagnation risk in the codebase. Same decision as F1/F2: ratchet or accept. Not in #1234 scope. |

### Cluster H — Minor

| # | Finding | Status | Closed by |
|---|---|---|---|
| H1 | `architecture` entity marker not in integration matrix groups | ✅ Absorbed | #1234 PR 3 commit 3 — matrix collapsed to single xdist job; #1234 D29 renamed structural-guard marker to `arch_guard` to disambiguate from entity-marker |
| **H2** | `test_architecture_bdd_no_duplicate_steps.py` missing `test_allowlist_entries_still_exist` paired check | ⚠️ **Open** | Trivial structural-guard addition; ~30 min. Not in #1234 scope but could fold into PR 4. |
| H3 | `docker compose down -v 2>/dev/null \|\| true` cleanups mask diagnostic errors | 🟡 Partial | #1234 PR 3 commit 3 cleanup steps are explicit; the silent suppression class largely goes away with Phase C deletion of `test.yml`. |
| H4 | `find . -type d -name __pycache__ -exec rm -rf {} +` workaround; root cause undiagnosed | 🟡 Open — informational | Round 10 GAP-CI-17 noted this; deferred. Not in #1234 scope. |

**Summary: ~14 absorbed, ~16 remaining. Of the 16 remaining, 5 are architectural decisions (F1, F2, G2 + portions of E1/E2), 4 are mechanical follow-ups (B1, C5, C7, E1, E2), 1 is a critical P0 that #1234 partially addresses (A4 — release-please gate), 6 are misc (A5, B7, D2, H2, H3, H4).**

## Residual scope (closing scope of #1228 after #1234 lands)

### Critical — must close before claiming the gate is restored

- **A4 — `release-please publish-docker` `needs: [ci]` gate.** #1234 PR 6 commit 2 extends publish-docker with cosign + SBOM + Trivy but doesn't gate on test results. **Add `needs: [ci]` (or equivalent) so red main can't ship images.** This is a one-line change; could fold into #1234 PR 6 commit 2 or ship as a follow-up immediately after #1234 closes.

- **A5 residual — `timeout-minutes` on the 5 jobs Round 11 caught.** Quality Gate (10), Type Check (10), Migration Roundtrip (10), Coverage (10), Summary (5). Spec into PR 3 or follow-up.

### Mechanical (low effort, high value)

- **B1** — Rebaseline `.type-ignore-baseline` 42 → 54.
- **C5** — uv cache key `hashFiles('**/uv.lock', '**/pyproject.toml')`.
- **C7** — File sub-issues for each `Security Audit` `--ignore-vulns` allowlist entry (pillow GHSA-whj4-6x5x-4v2j, pytest GHSA-6w46-j5rx-g56g, python-multipart GHSA-mj87-hwqh-73pj).
- **E1** — Delete `google_ad_manager_original` references from `mypy.ini:54-56` + `.pre-commit-config.yaml:305`.
- **E2** — `git rm .mypy_baseline` (orphan since `806769b2`).
- **D2** — Verify `ipr-agreement.yml` permissions actually narrow per-job in #1234 PR 1 commit 11.
- **H2** — Add `test_allowlist_entries_still_exist` to `test_architecture_bdd_no_duplicate_steps.py`.
- **B7** — Audit `.duplication-baseline` post-#1234.

### Architectural (Tier 3 of the original plan — needs decision)

- **F1** — 14 ruff ignores: per rule, ratchet or accept-permanently.
- **F2** — `mypy.ini` lenient flags (`check_untyped_defs`, `disallow_incomplete_defs`, `warn_return_any`): ratchet or accept.
- **G2** — `obligation_coverage_allowlist.json` (301 entries): introduce decrement mechanism or accept the inventory.

These are "Tier 3" in the original plan. They're independent of #1234 — could close this issue with the architectural items deferred to follow-up issues, or hold this issue open until they're decided.

### Tracked elsewhere (not closed by this issue)

- **C2** — Creative-agent monolith caching → **#1189**.
- **C1 root cause** — schema URL caching → **#1213**.

## Closure plan

Close this issue when ALL of:

1. **#1234 has fully merged** (PRs 1-6); A1, A2, A3, A6, B2-B5, B6, C1, C3, C4, C6, D1, G1 partial, H1 verified absorbed via the verification commands below.
2. **A4 fixed** (release-please publish-docker gates on test results) — either in #1234 PR 6 or a tiny follow-up.
3. **A5 timeouts added** to the 5 jobs Round 11 R11E-04 flagged.
4. **Mechanical follow-ups** B1, C5, C7, D2, E1, E2, B7, H2 closed.
5. **Architectural decisions** F1, F2, G2 made (ratchet vs. accept; capture in ADRs).
6. **C2** tracked under #1189; **C1 root** under #1213; both with explicit "owned by" links here.

## Verification (post-#1234 + residual fixes)

```bash
# A1 — summary aggregates all 14 frozen checks
yq '.jobs.summary.needs' .github/workflows/ci.yml | grep -c -E 'smoke-tests|security-audit|quickstart' # expect 3

# A2 — no advisory ruff
! grep -E "\\|\\| true|continue-on-error: true" .github/workflows/*.yml

# A3 — pre-commit runs in CI
grep "pre-commit run" .github/workflows/ci.yml  # in Quality Gate job

# A4 — release-please publish-docker gates on tests
yq '.jobs."publish-docker".needs' .github/workflows/release-please.yml | grep -E '(ci|test-summary|tests)'

# A5 — every job has explicit timeout-minutes
yq '.jobs | to_entries | .[] | select(.value | has("timeout-minutes") | not) | .key' .github/workflows/ci.yml
# expect: empty

# A6 — coverage hard-gate from CI
grep -- '--fail-under' .github/workflows/ci.yml

# B2-B5 — additional_dependencies eliminated for project libs
grep -c 'additional_dependencies:' .pre-commit-config.yaml  # 0

# B6 — black at lockfile-resolved version
grep 'black' uv.lock | head

# C3 — single Postgres major
grep -rh 'postgres:1[5-9]' docker-compose*.yml .github/workflows/*.yml | sort -u  # one line

# C5 — uv cache key includes pyproject.toml
grep "hashFiles.*pyproject" .github/actions/setup-env/action.yml

# C6 — dependabot config exists
test -f .github/dependabot.yml

# D1 — every third-party action SHA-pinned
! grep -E '^\s+uses:.*@v[0-9]' .github/workflows/*.yml

# E1, E2 — dead config removed
! grep 'google_ad_manager_original' mypy.ini .pre-commit-config.yaml
! test -f .mypy_baseline
```

## Related

- **#1234** — CI and pre-commit refactor (the umbrella issue that closes Tier 1 + most of Tier 2/4/5). 12 audit rounds applied; 6-PR rollout; D1-D46 locked decisions; ~19.5-23.5 engineer-days. Planning corpus on branch `docs/ci-refactor-planning` at `.claude/notes/ci-refactor/`.
- **#1233** — CI vs local test-tooling divergences (D1-D15). Now mostly subsumed by #1234; only D9, D11, D13 remain in scope.
- **#1189** — Integration (creative) rebuilds adcp monolith every run (subset of Cluster C2).
- **#1213** — `_preload_schema_references` silently swallows `SchemaDownloadError` (root cause of Cluster C1 + a "No Quiet Failures" violation).
- **#1220** — test harness migration for property discovery (orthogonal).

---

<details>
<summary><b>Original meta root cause: "Soft Gate" (preserved for context)</b></summary>

The CI pipeline in `.github/workflows/test.yml` appeared to enforce strict quality gates (Test Summary, Security Audit, 5 integration shards, E2E, Lint, Smoke), but three independent leaks made the gate soft in practice. Downstream of that softness, ratcheting baselines had drifted, hook environments diverged from the runtime, and dead config was being cargo-culted through files.

**The cascade (4 orders of consequence):**
1. **1st:** visible bugs, flakes, drift
2. **2nd:** alert fatigue (persistent Security Audit red → ignored); "re-run the job" muscle memory; baselines stop ratcheting; local pre-commit becomes "the real gate"
3. **3rd:** devs bypass hooks with `--no-verify` because "CI will catch it" (CI won't); red main gets released; real CVEs hide in noise of ignored ones
4. **4th:** stated principles (No Quiet Failures, Test Integrity ZERO TOLERANCE, DRY invariant) drift from actual enforcement; reviewers can't tell "real red" from "flake"; the quality gate becomes theater

#1234's PR 3 + PR 4 close the 1st-order leaks. Tier 3 (F1, F2, G2) closes the 2nd-3rd-order issues at the architectural-debate level — without resolving those, the cascade can recur in a different form.

</details>

<details>
<summary><b>Original 5-tier remediation plan (preserved as audit trail)</b></summary>

### Tier 1 — Restore the gate
1. Remove `|| true` and `continue-on-error: true` from `test.yml:381-387` ✅ #1234 PR 3 commit 7
2. Add `pre-commit` CI job ✅ #1234 PR 3 commit 3 (Quality Gate)
3. `needs.smoke-tests.result` in summary ✅ #1234 PR 3 commit 3 (summary needs covers 14 checks)
4. `needs: [test-summary]` to `release-please publish-docker` ⚠️ **OPEN — A4**
5. `timeout-minutes:` on 5 jobs ⚠️ **PARTIAL — A5 residual on 5 new jobs**

### Tier 2 — Repair drifts
6. Sync `.pre-commit-config.yaml` mypy hook deps to `uv.lock` ✅ #1234 PR 2 (eliminates `additional_dependencies` entirely)
7. Bump black 25.1.0 → 26.3.1 ✅ #1234 PR 2 (local hook uses lockfile-resolved version)
8. Rebaseline `.type-ignore-baseline` 42 → 54 ⚠️ **OPEN — B1**
9. Delete orphan `.mypy_baseline` ⚠️ **OPEN — E2**
10. Delete `google_ad_manager_original` references ⚠️ **OPEN — E1**
11. Add `test_hook_pins_match_lock.py` guard ✅ #1234 PR 2 commit 8 (`test_architecture_pre_commit_no_additional_deps.py` is the structural equivalent — eliminates the class entirely rather than checking pin parity)
12. Stale-entry check in `test_architecture_bdd_no_duplicate_steps.py` ⚠️ **OPEN — H2**

### Tier 3 — Permanent amnesty → honest ratcheting
13. Ruff ignores: ratchet or accept ⚠️ **OPEN — F1**
14. Mypy lenient flags: ratchet or accept ⚠️ **OPEN — F2**

### Tier 4 — External dependency resilience
15. Close #1189 (creative agent image to ghcr.io) → **TRACKED IN #1189**
16. Close #1213 (strict validator + cache-complete guard) → **TRACKED IN #1213**
17. Unify Postgres ✅ #1234 PR 5 commit 1
18. uv cache key hashes pyproject too ⚠️ **OPEN — C5**
19. `.github/dependabot.yml` ✅ #1234 PR 1 commit 4
20. Tracking sub-issue for persistent `Security Audit` vulns ⚠️ **OPEN — C7**

### Tier 5 — Supply chain
21. SHA-pin every third-party Action ✅ #1234 PR 1 commit 9
22. Review `ipr-agreement.yml` write perms 🟡 **PARTIAL — D2 (verify the narrowing actually lands)**

</details>

<details>
<summary><b>Original Cluster A-H finding list with file:line evidence (audit trail)</b></summary>

Preserved for archaeology — see the status table at the top for current status. All file:line evidence below was verified against the repo state on 2026-04-23.

**Cluster A:** `test.yml:399,405` (summary needs/aggregation), `test.yml:381-387` (ruff `|| true`), `grep -rn "pre-commit" .github/workflows/` → 0 hits, `release-please.yml:27-29,40-51,57-59,67,75-76` (publish-docker no test gate), `test.yml:15, 34, 77, 361, 396` (jobs missing timeout-minutes), `tox.ini:94` + `test.yml:112` (coverage floor 30 only in tox).

**Cluster B:** `.type-ignore-baseline=42` (actual `grep -rE "#\s*type:\s*ignore" src/ | wc -l` = 54), `.pre-commit-config.yaml:295` (sqlalchemy[mypy]==2.0.36), `:302` (fastmcp unpinned), `:303` (alembic unpinned), `:296-300` (5 types-* unpinned), `:275-276` (black 25.1.0 vs pyproject `black>=26.3.1`), `.duplication-baseline` ({"src": 44, "tests": 109}).

**Cluster C:** #1213 (schema URL no cache), `test.yml:186-187` (creative monolith rebuild — bare curl + bare docker build, no cache), `test.yml:135` (PG15) + `:196` (PG16) + `docker-compose.e2e.yml:17` (PG17-alpine), `release-please.yml:67,75-76` vs `test.yml:187` (cached vs uncached docker build), `test.yml:57,100,167,313` (uv cache key hashFiles only uv.lock), `find .github -name "dependabot*"` → 0 hits, `test.yml:32` (`uv-secure --ignore-vulns` 3 GHSAs).

**Cluster D:** `release-please.yml`, `pr-title-check.yml`, `ipr-agreement.yml` (all third-party actions tag-pinned), `ipr-agreement.yml:6, 9-13, 21` (`pull_request_target` + `contents:write` + `pull-requests:write` + `actions:write` + `statuses:write` → tag-pinned `contributor-assistant/github-action@v2.6.1`).

**Cluster E:** `mypy.ini:54-56` + `.pre-commit-config.yaml:305` reference `google_ad_manager_original.py` (file doesn't exist; only `google_ad_manager.py` + `.md`), `.mypy_baseline` orphan (zero readers; orphan since `806769b2`).

**Cluster F:** `pyproject.toml:166-185` (14 ruff rules ignored; 8 with violation counts in comments), `mypy.ini:9-12` TODO block (last touched 2025-12-08 by Brian O'Kelley).

**Cluster G:** `.pre-commit-config.yaml:275-279` + `pyproject.toml:111, 115` + `Makefile:8-13` (two formatters), `tests/unit/obligation_coverage_allowlist.json` (301 entries, manual-edit only).

**Cluster H:** `test.yml:122-131` (architecture marker missing from matrix), `tests/unit/test_architecture_bdd_no_duplicate_steps.py` (no stale-entry pair), `test.yml:332, 333, 360` (silent docker compose down -v cleanups), `test.yml:172-173` (`find __pycache__ -exec rm` workaround).

</details>

## Sign-off

A successful resolution of this issue means:

1. **#1234 has fully merged** — Tier 1, Tier 4, Tier 5, and most of Tier 2 are closed.
2. **A4 (release-please test gate)** — `publish-docker` has `needs: [ci]` (or equivalent); red main can't ship images.
3. **A5 residual timeouts** — every CI job has explicit `timeout-minutes`.
4. **Mechanical cleanups (B1, C5, C7, D2, E1, E2, H2, B7)** all closed.
5. **Architectural decisions (F1, F2, G2)** made — each amnestied rule/lenient-flag/allowlist either has a machine-enforced ratchet or the exemption is explicit and permanent.
6. **C2 → #1189** and **C1 → #1213** linked and tracked in their own issues.
7. The phrase "CI green" is reliable shorthand for "all stated quality invariants enforced."
8. The 4-order cascade (visible bugs → alert fatigue → `--no-verify` muscle memory → quality theater) is broken by automation, not by exhortation.

---

**Labels:** `ci` · `quality` · `tech-debt` · `P2` (downgraded from "medium" once #1234 is in flight) · `2.0 Release`
