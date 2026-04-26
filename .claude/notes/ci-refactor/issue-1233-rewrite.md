> **Status:** Mostly **subsumed by #1234** (12 of 15 D-items absorbed by #1234 PRs 3 + 4). Three items remain in this issue's scope: D9 (informational), D11 (`requires_server` decision), D13 (GAM live tests → nightly-cron follow-up). Close this issue when #1234 merges + the 3 remaining items are resolved.
> **Read order:** Status table → Remaining scope → expand `<details>` for the original 15-divergence catalog and historical context.

## TL;DR

Two entire test suites — `tests/admin/` and `tests/bdd/` — were never run in CI. The audit found 15 CI-vs-local divergences (D1-D15). **#1234's CI/pre-commit refactor absorbed 12 of them** (PR 3 adds the missing jobs as part of the new 14-frozen-check-name structure; PR 4 deletes D15's dead hook). 3 items are decisions/follow-ups that need explicit resolution.

The original framing — "CI green doesn't mean all tests pass" — is fixed by #1234. This issue closes when #1234 lands AND the 3 remaining items are decided.

## Status of each divergence (post-#1234 planning)

| ID | Divergence | Status | Closed by |
|---|---|---|---|
| **D1** | Admin + BDD suites never run in CI | ✅ Absorbed | #1234 PR 3 commit 3 — `Admin UI Tests` + `BDD Tests` are 2 of the 14 frozen check names |
| **D2** | Integration marker matrix fragile; `architecture` not routed | ✅ Absorbed | #1234 PR 3 commit 3 — matrix collapsed to single xdist job (`--dist=loadscope` per D33) |
| **D3** | `tests/harness/` collected locally, ignored by CI | ✅ Absorbed | #1234 PR 3 — `tox -e unit` runs `tests/unit/ tests/harness/` (verified at tox.ini:40) |
| **D4** | Coverage collected from unit only; never combined | ✅ Absorbed | #1234 PR 3 commit 6 — `.coverage-baseline=53.5` hard-gate + combine job (D11 of #1234) |
| **D5** | `make quality` pre-commit hooks don't re-run in CI | ✅ Absorbed | #1234 PR 3 commit 3 — `Quality Gate` job runs `pre-commit run --all-files` |
| **D6** | Ruff linting advisory-only in CI (`\|\| true`) | ✅ Absorbed | #1234 PR 3 commit 7 — removes `\|\| true` and `continue-on-error: true` |
| **D7** | Ratcheting duplication baseline not enforced in CI | ✅ Absorbed | Folded into D5 (quality-gate runs all pre-commit hooks including duplication check) |
| **D8** | No alembic downgrade roundtrip in CI | ✅ Absorbed | #1234 PR 3 — `Migration Roundtrip` is one of the 14 frozen check names (upgrade → downgrade → upgrade) |
| **D9** | Parallelism / isolation asymmetry (informational) | ⚠️ **Open — document** | Add to `docs/development/ci-pipeline.md` as known asymmetry; no code fix needed |
| **D10** | Schema-alignment test silent-skips on network failure | ✅ Absorbed | #1234 PR 3 commit 10 — fail-hard on network errors, no `pytest.skip` |
| **D11** | `requires_server` tests never run (in CI or locally) | ⚠️ **Open — decide** | Delete the 22 dead-marked tests OR add a CI job that reuses the e2e Docker stack. Not in #1234's scope. |
| **D12** | Creative agent only started for `creative` shard | ✅ Absorbed | #1234 PR 3 commit 9 (per D32 + D39) — full creative-agent bootstrap unconditional in integration job |
| **D13** | GAM tests gated on credentials never run in CI | ⚠️ **Open — follow-up filed** | Accepted asymmetry per #1234 D-discussion. Nightly-cron follow-up issue planned to give `requires_gam` tests a CI home on a different cadence (not per-PR). |
| **D14** | E2E uses fixed port 8080 | ✅ Absorbed | #1234 PR 3 commit 8 — dynamic port from conftest |
| **D15** | Dead pre-commit hook `no-skip-integration-v2` | ✅ Absorbed | #1234 PR 4 commit 7 — included in the 16 hook deletions |

**Summary: 12 absorbed by #1234, 3 remaining (D9, D11, D13).**

## What remains in this issue's scope

### D9 — Parallelism / isolation asymmetry (informational)

Local `tox -p` runs all envs against one shared Postgres; CI matrix shards each get fresh Postgres. After #1234 PR 3 collapses the integration matrix into a single xdist job, this asymmetry shrinks but doesn't fully close — local `tox -e integration` still uses the host's running Postgres, while CI starts a fresh `postgres:17-alpine` service.

Cross-test isolation bugs that `tests/admin/conftest.py:35` warns about (process-wide factory binding) can reproduce locally but not in CI.

**Resolution:** add a section to `docs/development/ci-pipeline.md` documenting the asymmetry and the diagnostic procedure (run `tox -p` locally to reproduce; expected behavior; what to inspect). No code fix.

**Effort:** S (~30 minutes — doc edit).

### D11 — `requires_server` tests never run (in CI or locally)

22 tests tagged `@pytest.mark.requires_server` (including `tests/integration/test_mcp_endpoints_comprehensive.py:144-311`). `tox.ini:50` filters them out (`-m "not requires_server and not skip_ci"`); `tests/conftest.py:811-814` skips them when port 8100 isn't reachable. Effectively dead code — they ship as "tests" but never execute.

**Two options (decision needed):**

- **(a) Delete** — these tests were originally written for a since-deleted server-on-port-8100 setup. If the coverage they intended is now provided by `tests/e2e/` and `tests/integration/test_mcp_*` (which DO run), delete the 22 tests + the marker.
- **(b) Resurrect** — add a CI job that reuses the e2e Docker stack and runs `pytest -m "requires_server"`. Aligns with #1234's "CI is authoritative" principle.

**Recommendation:** option (a) unless audit shows they cover a gap not exercised by `tests/e2e/`. Audit before deleting:

```bash
# What do requires_server tests actually exercise?
git grep -l '@pytest.mark.requires_server' tests/ | while read f; do
  echo "=== $f ==="
  grep -A 10 'def test_' "$f" | head -40
done | less

# Cross-reference: is the same area covered by tests/e2e/ or tests/integration/ (without requires_server)?
# Look for overlap in API path / function-under-test.
```

**Effort:** M (~2-4 hours for audit + decision + delete OR ~1 day for resurrect).

### D13 — GAM tests gated on credentials never run in CI

10 tests marked `@pytest.mark.requires_gam` (e.g., `tests/e2e/test_gam_lifecycle.py:504,591`) skip when env missing. The `tests/e2e/conftest.py:578` fixture `pytest.skip(...)` when `GAM_SERVICE_ACCOUNT_JSON` / `GAM_SERVICE_ACCOUNT_KEY_FILE` are absent.

**Resolution path (per #1234 conversation):** file a separate follow-up issue for **nightly-cron GAM regression coverage**. Reasoning:

- Live tests cost real GAM API quota and pollute a shared test network — can't run per-PR
- Credential blast radius (CVE-2025-30066-class threat) makes secret exposure risky for fork PRs
- Nightly cron on `main` with secrets-on-`main`-only access closes the divergence on a different cadence

**This issue's contribution:** state explicitly that D13 is NOT a permanent silent-skip; track the nightly-cron follow-up by issue number once filed.

**Effort:** M (separate follow-up issue; ~1-2 days when implemented).

## Closure plan

Close this issue when ALL of:

1. **#1234 has merged** (PRs 1-6 all on `main`); D1-D8, D10, D12, D14, D15 verified absorbed via the post-#1234 verification commands below.
2. **D9 documentation** added to `docs/development/ci-pipeline.md`.
3. **D11 decision** made and applied (delete or resurrect; document the call).
4. **D13 nightly-cron follow-up issue** filed and linked here.

## Verification (post-#1234)

```bash
# 1. All tox envs have a CI job
tox -l                                               # should show: unit, integration, e2e, admin, bdd, ui, coverage
grep -E "tox-env: (unit|integration|e2e|admin|bdd)" .github/workflows/ci.yml

# 2. No advisory-only linting (D6)
! grep -E "\\|\\| true|continue-on-error: true" .github/workflows/*.yml

# 3. Pre-commit hooks run in CI (D5)
grep "pre-commit run" .github/workflows/ci.yml       # in Quality Gate job

# 4. Migration roundtrip (D8)
grep "Migration Roundtrip" .github/workflows/ci.yml  # one of the 14 frozen names

# 5. tests/harness in unit (D3)
grep "tests/harness" tox.ini                         # tox.ini:40

# 6. Coverage combine (D4)
grep -A2 "coverage:" .github/workflows/ci.yml        # the Coverage job

# 7. Creative agent unconditional (D12)
grep "docker run -d --network creative-net" .github/workflows/ci.yml

# 8. Dead hook removed (D15)
! grep "no-skip-integration-v2" .pre-commit-config.yaml
```

All should return expected output.

## Related

- **#1234** — CI and pre-commit refactor (the umbrella issue that subsumes most of this). 12 audit rounds applied; 6-PR rollout; D1-D46 locked decisions; ~19.5-23.5 engineer-days. Planning corpus on branch `docs/ci-refactor-planning` at `.claude/notes/ci-refactor/`.
- **PR #1222** — the example that motivated this audit (4 runtime blockers in `tests/admin/` + `tests/bdd/` that shipped through green CI). After #1234 PR 3 lands, the same class of blocker would be caught at PR time.
- **PR #1175** — deleted `tests/unit/test_pydantic_schema_alignment.py` for "false failures when spec site updates fields ahead of library release." D10's structural fix (#1234 PR 3 commit 10) is the resolution that would have prevented PR #1175's pain.
- **#1233 birthplaces** (kept for archaeology):
  - `abbcfa9b` (2026-03-12) — `tests/ui/` → `tests/admin/` rename without updating workflow (D1 for admin)
  - PR #1146 / `7f0d45a4` (2026-03-19) — introduced `tests/bdd/` without updating workflow (D1 for bdd)
  - `9c656177` (2026-03-31) — introduced integration marker matrix (D2)

---

<details>
<summary><b>Original problem statement (preserved for context)</b></summary>

**CI-green no longer implies "all tests pass."** `.github/workflows/test.yml` ran:

```
pytest tests/smoke/         # line 67
pytest tests/unit/          # line 112
pytest tests/integration/   # line 236 (matrix of 5 groups)
pytest tests/e2e/           # line 355
```

It did not run `tests/admin/` or `tests/bdd/`. Beyond that, 13 smaller divergences silently weakened CI compared to the local `./run_all_tests.sh` + `make quality` flow.

History showed accidental drift, not deliberate exclusion:
- `.github/workflows/test.yml` has **never** contained a job for `tests/admin/`, `tests/bdd/`, or the predecessor `tests/ui/`. No commit adds or removes such a job.
- `abbcfa9b` (2026-03-12): renamed `tests/ui/` → `tests/admin/`, updated `tox.ini`, `run_all_tests.sh`, `Makefile`. Workflow file untouched for admin.
- `7f0d45a4` / `530cf251` (2026-03-19, PR #1146): introduced `tests/bdd/` + `tox -e bdd`. Workflow file untouched for bdd.
- No commit message contained a rationale like "exclude X from CI," "too slow for CI," "skip in CI," or "flaky in CI" for these suites.
- Recent commits showed the author actively converging BDD toward strict-xfail-clean state (`69b1afce`, `7fc7eda9`) — consistent with intent-to-run-in-CI.

**Conclusion:** the gap was accidental drift, not a deliberate cost/flakiness trade-off.

</details>

<details>
<summary><b>Full original divergence catalog (D1-D15) — historical reference</b></summary>

Original priorities and fix sketches preserved here for the post-mortem record. **All P0 + P1 items were absorbed into #1234 except D9, D11, D13** (see status table at the top).

### P0 — admin/bdd CI jobs + integration matrix + quality gate + ruff enforcement

**D1 · Admin + BDD suites never run in CI** — `tests/admin/` (3,000+ LOC) and `tests/bdd/` (8 modules × 4 transport variants) not invoked anywhere in `.github/workflows/*.yml`. PR #1222 shipped 4 runtime blockers + 5 high-severity assertion bugs through green CI because of this gap.

**D2 · Integration marker matrix fragile** — 5-shard matrix at `test.yml:120-131` includes 15 entity markers in positive form + negation for "other." `tests/conftest.py:27-45` registers entity markers; any addition without matrix update silently disappears from CI.

**D5 · `make quality` pre-commit hooks not in CI** — 30 hooks run on commit but never in CI. Most important: `adcp-contract-tests` (AdCP protocol compliance only enforced at commit time). Contributors using `git commit --no-verify` or GitHub web UI ship violations.

**D6 · Ruff linting advisory-only** — `test.yml:381-387` had `|| true` + `continue-on-error: true` swallowing violations. C90 (complexity), PLR (refactor), TID251 (banned APIs) violations merged silently.

### P1 — coverage, harness, downgrade, schema, requires_server, creative-agent

**D3 · `tests/harness/` ignored by CI** — local `pytest tests/unit/ tests/harness/`; CI was `pytest tests/unit/`.

**D4 · Coverage from unit only** — `coverage.json` reported only unit-test lines, artificially lowering numbers for MCP wrappers, A2A routes, admin views.

**D7 · Duplication baseline not in CI** — `.duplication-baseline = {"src": 44, "tests": 109}` ratchet only enforced at commit time.

**D8 · No alembic downgrade roundtrip** — `test.yml:178` ran upgrade only; broken `downgrade()` implementations would land silently.

**D10 · Schema-alignment test silent-skips on network failure** — `tests/unit/test_pydantic_schema_alignment.py:111-116` `pytest.skip()` on `httpx.HTTPError`. CI reported green even when no schema validation ran.

**D11 · `requires_server` tests effectively dead code** — 22 tests gated on `port 8100` reachable; tox filter excludes them; CI excludes them.

**D12 · Creative agent only for `creative` shard** — `test.yml:180-223` conditional on `matrix.group == 'creative'`. Tests using `CREATIVE_AGENT_URL` not tagged `@pytest.mark.creative` silently skip in other shards.

### P2 — parallelism, GAM, port, dead hook

**D9 · Parallelism asymmetry (informational)** — local `tox -p` shared Postgres vs CI matrix fresh Postgres.

**D13 · GAM tests gated on credentials** — 10 `@pytest.mark.requires_gam` tests skip when env missing.

**D14 · E2E fixed port 8080** — local uses dynamic `50000-60000`.

**D15 · Dead pre-commit hook** — `no-skip-integration-v2` greps removed `tests/integration_v2/` directory.

### Genuinely equivalent (not findings, kept for completeness)

- `security-audit` job ≡ `run_all_tests.sh:112`
- `e2e-tests` job ≡ `tox -e e2e` (modulo D14)
- `quickstart-test` — CI-only smoke; no local equivalent needed
- mypy enforcement — runs in both CI and `make quality`

</details>

<details>
<summary><b>Original verification + research methodology</b></summary>

Cross-referenced `.github/workflows/test.yml` against `tox.ini`, `Makefile`, `run_all_tests.sh`, `.pre-commit-config.yaml`, `pytest.ini`, `pyproject.toml`, and all `tests/*/conftest.py` files. Git archaeology on `.github/workflows/test.yml` (`git log -p`) looking for any historical admin/bdd/ui jobs. Searched commit history for "skip_ci", "ci:", "CI flaky", "timeout", "admin test", "bdd". Checked `docs/`, `.claude/rules/`, `CLAUDE.md` for ADRs or CI policy docs.

Audit conclusion verbatim:
> The gap is accidental drift, not an intentional cost/flakiness decision. The authors wired the suites into every other runner (tox, run_all_tests.sh, Makefile) at the same time they introduced them — they just didn't touch .github/workflows/test.yml. No ADR, no issue, no skip marker, no documented rationale. Closing the gap is low risk and likely intended.

This is what #1234 PR 3 acts on.

</details>

## Sign-off

A successful resolution of this issue means:

1. **#1234 has fully merged** (the umbrella refactor; 6 PRs; 12 audit rounds completed).
2. **D9 asymmetry documented** in `docs/development/ci-pipeline.md`.
3. **D11 decided** (delete or resurrect; rationale captured).
4. **D13 nightly-cron follow-up filed** as a separate issue.
5. PR #1222's class of blockers (4 runtime errors + 5 high-severity assertions) would now be caught by CI at PR time.
6. The phrase "CI green" is reliable shorthand for "all tests pass."
7. The phrase "run `./run_all_tests.sh` locally before merge" in `tests/CLAUDE.md` (the current workaround) can be removed.

---

**Labels:** `ci` · `testing` · `infrastructure` · `tech-debt` · `P1` (downgraded from P0 once #1234 is in flight) · `2.0 Release`
