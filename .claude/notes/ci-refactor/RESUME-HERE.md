# Resume Here — CI/Pre-commit Refactor

**Date snapshot:** 2026-04-27 (Round 14 reframe applied — agent-team execution model; 14 audit rounds total).

**Rollout status:** **PRE-FLIGHT GATES OPEN.** 14 audit rounds complete. Per Round 14 closure, the corpus is internally consistent, all known load-bearing technical defects are closed, and propagation drift has been swept. **Before PR 1 author handoff, the user must:**
1. Run admin pre-flight items A1-A14 + A15 (develop-branch fate)
2. Run agent pre-flight items P1-P5, P10 (P10 already captured)
3. Decide A25 path (hardware MFA on @chrishuie + ADR-010 SPOF acceptance)
4. Decide Path 1 vs Path 2 (D20 tripwire fires ~2026-05-04; consult `briefings/path-2-fallback.md` if v2.0 PR #1221 advances)
5. Run A24 sandbox dry-run on a throwaway repo before PR 3 Phase B

After these gates clear, the executor handoff is genuinely ready.

## 2026-04-27 Round 14 reframe applied (agent-team execution model)

User feedback: "It is me and agents reviewing the work" — strip 40-person / CTO / VP-Eng / boss-level / multi-team scaffolding per `feedback_agent_team_execution_model.md`. Round 13's human-team framing was theatrical for a solo+agents execution model.

**Files deleted:**
- `ONBOARDING-CHEAT-SHEET.md` (duplicated EXECUTIVE-SUMMARY orientation)
- `FAILURE-BROADCAST-PROTOCOL.md` (Slack/PR-comments pattern; for solo+agents the pattern is `escalations/<file>.md → STOP → wait for user`)

**Files retained (reframed for parallel-agent execution):**
- `COORDINATION.md` — parallel-agent PR-claiming registry
- `REBASE-PROTOCOL.md` — file-collision logic for sequential PR landing

**Pre-flight items reframed:**
- A25 simplified to hardware-MFA-only path (Option A "recruit second maintainer" was impossible — agents are not maintainers)
- A26 (notification routing for 40-person team) deleted entirely

**Other Round 14 closures (already applied):**
- B1: D40 per-branch defaults contradiction (production regression risk closed)
- B4: D44 rationale corrected (`default_install_hook_types` is pre-commit ≥2.11.0; floor 3.2.0 is for `pre-push` stage-name rename)
- M1: 25 verify-script patches across 5 PRs
- M2/M3/M6: propagation drift sweep + check-stale-strings.sh fixed broken regex + 18 new patterns
- M4: R11-R15 RESERVED documented
- M5: P10 hook-baseline pre-flight + .hook-baseline.txt evidence
- M7: runbooks/E4-account-lockout-recovery.md authored (closes A25's mandate)
- M8: PR 6 Commit 2.5 emergency-revert scratch test
- B3: briefings/path-2-fallback.md + D20 operational definition

## Round 13 audit-trail reference

13 rounds of opus-subagent research + integrity audit + Round 5+6 P0 sweep applied 2026-04-25 + Round 9 verification 2026-04-25 + Round 10 completeness audit sweep applied 2026-04-26 + Round 11 verification + extension sweep applied 2026-04-26 + Round 12 verification + sweep applied 2026-04-26 + Round 13 comprehensive review + parallel-agent scaffolding sweep applied 2026-04-26 + Round 14 reframe applied 2026-04-27. The 3 critical blockers (workflow naming, hook count, helpers collision) are fixed. Round 11 caught + fixed 5 P0s introduced by Round 10 (creative-agent services networking, SOURCE_DATE_EPOCH ARG missing, structural-guard list 11 vs 14). Round 12 caught + fixed 19 P0s introduced by Round 11 sweep (DB_POOL_SIZE app-side wiring, 11→14 in admin scripts/briefings/executor template, structural-guard lift commit, D44 minimum_pre_commit_version). Round 13 surfaced D48 production-deploy coupling, 3 LOAD-BEARING action-version/CVE-attribution/D47-race fixes, 48 internal contradictions. External technical corrections applied (mirrors-mypy reframed, harden-runner CVE-2025-32955 + DoH-bypass v2.16+ floor, persist-credentials propagation, rendered-name capture). D-pending-1..4 promoted to D22-D25; D26 + D27 + D28 added; D29 (marker rename); D30-D38 added in Round 10; D39-D45 added in Round 11; D46 added in Round 12; D47 added in post-issue-review; D48 added in Round 13. R19/R20/R23 promoted; R26-R32 added; R33-R37 added in Round 10; R38-R42 added in Round 11; R43 added in Round 12; R44 added in post-issue-review; R45-R47 added in Round 13.

## 2026-04-26 Round 13 sweep applied (comprehensive review + parallel-agent scaffolding)

6 parallel opus subagents applied: (A) PR 1+2 fixes; (B) PR 3 + Phase B + cross-PR scripts; (C) PR 4 internal-consistency cleanup; (D) PR 5 + PR 6 + action-version bumps; (E) comprehensive review + parallel-agent scaffolding; (F) range sweeps. **3 new D / 3 new R / 3 new A / 4 new docs / ~80 file edits applied as a single sweep commit.**

**Top comprehensive-review findings closed:**
- **D48 (production deploy coupling):** plan never mentioned Fly.io existed — corpus-wide silence on production. Fixed: EXECUTIVE-SUMMARY §"Production Deploy Coupling"; D48 added; rollback via fly deploy documented.
- **A25 (hardware MFA on critical path):** previously cited as "out of scope (organizational)" — now BLOCKER for PR 3 Phase B per comprehensive review.
- **A24 (Phase B dry-run on sandbox repo):** rollback was documented but never drilled — now mandatory pre-flight before PR 3 Phase B execution.

**Top LOAD-BEARING fixes:**
- `astral-sh/setup-uv@v4` was FOUR majors stale (current v8.1.0; v8.0.0 removed major/minor tag floating as security feature). Corpus-wide sweep to `# v8.x`.
- CVE-2025-32955 attribution wrong — was patched in v2.12.0, NOT v2.16.0. v2.16+ floor is correct but for GHSA-46g3-37rh-v698 + GHSA-g699-3x6g-wm3g (DoH/DNS bypasses, 2026-03-16). All attributions rewritten.
- D47 (release-please CI gate) was broken: `${{ needs.release-please.outputs.sha }}` referenced but `outputs:` block didn't declare `sha`. Fix: add `sha: ${{ github.sha }}` to release-please.yml + 6× polling loop for eventual-consistency tolerance.
- R29 split-job mitigation never applied — PR 6's publish-docker had cosign as inline step. Fix: split into `build-and-push` + `sign-and-attest needs: build-and-push` jobs per R29.
- PR 4 duplicate "Commit 9" headings + 13-vs-16 deletions math + Layer-1 reference table referenced non-existent hooks. Fixed.

**Parallel-agent scaffolding:**
- Created `COORDINATION.md` (PR-claiming registry)
- Created `REBASE-PROTOCOL.md` (mandatory order for `.pre-commit-config.yaml`, `pyproject.toml`, `release-please.yml`)
- Created `ONBOARDING-CHEAT-SHEET.md` (later removed in Round 14 reframe — duplicated EXECUTIVE-SUMMARY orientation)
- Created `FAILURE-BROADCAST-PROTOCOL.md` (later removed in Round 14 reframe — assumed human-team Slack/PR-comments; for solo+agents the pattern is `escalations/<file>.md → STOP → wait for user`)
- flip-branch-protection.sh: GitHub-issue-based mutex + D45 day-of-week guard

**Stale-string PATTERNS extended (D46 enforcement):**
- Added: `\b11 check names\b`, `\b11 required checks\b`, `\b11 names\b`, `\b33 effective\b`, `\b9 to pre-push\b`, `\b73-row\b`, `\b0\.11\.6\b`, `D1-D40` through `D1-D47`, `R1-R37` through `R1-R44`
- check-stale-strings.sh now detects propagation drift it previously missed

**Net effort delta from Round 13:** ~5-6 hours mechanical + comprehensive review. Total: **~20.25-24.5 engineer-days, ~6 calendar weeks part-time** (calendar slack absorbs without extension).

**Round 14 verification — NOT recommended.** Round 13 closed all known gaps including the structural propagation issue. Further rounds would yield diminishing returns. Next step: launch executor team.

## 2026-04-26 Round 10 completeness audit sweep applied

User caught a smoke-tests-class gap (Smoke Tests dropped from frozen-name list); 9 parallel opus subagents ran across 4 verification + 5 extension axes. Pattern of findings: load-bearing one-liners that look correct on paper but silently no-op in practice. 1 false claim retracted (CRIT-4 PR 3 commit 11 fictional Gemini diff — it was explicitly aspirational, not contradictory). 12 verified, 5 partial, 1 false out of 18 P0 claims.

**Top finding (load-bearing one-liner):** `default_install_hook_types: [pre-commit, pre-push]` was missing from `.pre-commit-config.yaml`. Without it, the 10-hook pre-push tier (D27, including mypy per D3) silently does not execute on any contributor machine. Hook math is mathematically correct but operationally ineffective. Fixed: D31 + PR 4 commit 1 add the directive.

**Top-level docs:**
- D30 added: frozen check names **11 → 14** (adds Smoke Tests, Security Audit, Quickstart). All three currently-running CI jobs in `test.yml` were silently dropped. D17 amended to forward to D30 for the canonical 14-name list.
- D31 added: `default_install_hook_types: [pre-commit, pre-push]` mandatory in PR 4.
- D32 added: creative-agent containerized service bootstrap fully spec'd in PR 3 commit 9 (43 lines, 10 env vars, pinned commit `ca70dd1e2a6c`, `creative-net` Docker network, second `postgres:16-alpine`).
- D33 added: `pytest-xdist≥3.6` + `pytest-randomly` to `[dependency-groups].dev` (PR 2 commit 4.5); `--dist=loadscope` in `_pytest/action.yml`.
- D34 added: container hardening — `@sha256:` digest pin + `USER` non-root in PR 5; `SOURCE_DATE_EPOCH` + Trivy OS-layer scanning in PR 6.
- D35 added: gitleaks adopted (pre-commit hook + workflow with SARIF upload) in PR 1.
- D36 added: ADR file location — drafts/ during planning; docs/decisions/ in production after PR 5. ADR-001/002/003 promoted from inline references in PR 1 spec to standalone draft files.
- D37 added: `workflow_dispatch` trigger preserved in `ci.yml` (matches `test.yml:8`).
- D38 added: `Schema Contract` job runs under `tox -e integration` env, not `unit` (which unsets DATABASE_URL).

**New risks:**
- R33 (Critical): pre-push tier silently disabled if `default_install_hook_types` missing.
- R34 (High): `ADCP_AUTH_TEST_MODE` module-level env mutation leaks across xdist workers.
- R35 (Med): Schema Contract job runs under wrong tox env.
- R36 (Med): PR 6 `Security / Dependency Review` breaks PR 3's frozen-checks structural guard if guard isn't updated.
- R37 (Med): new pre-commit hook added between PR 1 author and merge slips past SHA-freeze.

**v2.0 collision list expanded** (`00-MASTER-INDEX.md`): added `release-please.yml`, `.github/CODEOWNERS` glob, `pytest.ini`, `tests/conftest_db.py` to the warning block.

**PR scope additions:**
- **PR 1:** gitleaks pre-commit hook + workflow (per D35); ADR-001/002/003 inline-in-PR1-spec lifted directly to `docs/decisions/` at commit time (D36 — drafts/README.md is canonical on the inline-vs-standalone split); `ipr-agreement.yml` permissions narrowed per-job; structural guard `test_architecture_pre_commit_sha_pinned` confirmed in commit list (mitigates R37).
- **PR 2:** new commit 4.5 (between current 4 and 5) adds `pytest-xdist>=3.6` + `pytest-randomly` to `[dependency-groups].dev` (per D33); arch_guard registration ownership clarified (PR 2 commit 8 owns; PR 4 commit 1 verifies-only — closes Round 10 MF-1).
- **PR 3:** 14-name list per D30 (Smoke Tests + Security Audit + Quickstart added as new job declarations); commit 9 expanded to full creative-agent bootstrap per D32 (~50 lines YAML); `_pytest/action.yml` env block adds `ENCRYPTION_KEY=PEg0SNGQyvzi4Nft-ForSzK8AGXyhRtql1MgoUsfUHk=` + `DELIVERY_WEBHOOK_INTERVAL=5` + `CREATIVE_AGENT_URL=http://creative-agent:3000`; `--dist=loadscope` in pytest invocation (per D33); `workflow_dispatch:` preserved in ci.yml `on:` block (per D37); `Schema Contract` job invokes `tox -e integration` (per D38); uv default version bumped to `0.11.7` (Round 9 fix half-applied — completes here); `retention-days: 7` on all `actions/upload-artifact` invocations; concurrency `group:` formula extended with `${{ github.event.pull_request.number || github.sha }}`; conftest_db.py filelock+worker-id gate promoted from prose-only to a standalone commit.
- **PR 4:** Layer 4 reference table at `pr4-hook-relocation.md:872` rewritten to mirror D17 / D30 verbatim (was inventing `Lint`, `Format`, `Coverage Report`); `default_install_hook_types: [pre-commit, pre-push]` added to top of `.pre-commit-config.yaml` (per D31); P8 fallback commit pre-authored if mypy warm-time exceeds 20s (move `no-hardcoded-urls` to pre-push as the swap); TROUBLESHOOTING.md scoped section added alongside contributing.md update.
- **PR 5:** `Dockerfile:4,43` `FROM python:3.12-slim` → `FROM python:3.12-slim@sha256:<64hex>` (per D34); `RUN groupadd -r app && useradd -r -g app app` + `USER app:app` stanza (per D34); structural guard `test_architecture_dockerfile_digest_pinned`; ADR-008 (target-version deferral) copied from drafts/ to `docs/decisions/` (per D36); ADR-001/002/003 ALSO copied to docs/decisions/ at this PR.
- **PR 6:** Trivy OS-layer scan (`aquasecurity/trivy-action@<SHA>`, fail on CRITICAL/HIGH severity, `vuln-type: os,library`, `ignore-unfixed: true`) per D34; `release-please.yml` permissions narrowed (top-level `contents: read`, broader perms only on `release-please` job); dep-review config extracted to `.github/dependency-review-config.yml`; `dependency-review-action` pinned to specific minor (`v4.6.0+`); `SOURCE_DATE_EPOCH=$(git log -1 --format=%ct)` + BuildKit `--source-date-epoch` flag in publish-docker job (per D34); structural guard `test_architecture_required_ci_checks_frozen` expected-list updated to include `Security / Dependency Review` (mitigates R36); OpenSSF Best Practices Badge enrollment as admin-only step (post-PR-6).

**ADR file lifecycle (per D36):**
- ADR-001/002/003: their canonical text lives **inline in `pr1-supply-chain-hardening.md`** (sections "Embedded ADR-001" / "Embedded ADR-002" / "Embedded ADR-003"). PR 1 commit 7 + 11 lift this content directly to `docs/decisions/`. They are intentionally NOT staged in `drafts/` — see `drafts/README.md` for the inline-vs-standalone split. (Round 11 R11D-01 caught earlier rev's "promoted to standalone drafts" claim that contradicted disk truth — corrected here.)
- ADR-004 through ADR-009: standalone drafts at `.claude/notes/ci-refactor/drafts/adr-<NNN>-*.md` (already existed; planning artifacts ratified in earlier rounds). Each has a `## Status` block.
- ADR-008 (target-version deferral): in drafts/ AND copied to `docs/decisions/` by PR 5 commit 7b.

**Pre-flight additions:**
- A15: `rm -rf tests/migration/__pycache__/` (one-time cleanup — stale bytecode from prior v2.0 branch checkout; the .py source files for `test_a2a_agent_card_snapshot.py`, `test_mcp_tool_inventory_frozen.py`, `test_openapi_byte_stability.py` exist on `feat/v2.0.0-flask-to-fastapi`, NOT on main; arrive when v2.0 lands)
- P7: post-PR-4 verify `default_install_hook_types: [pre-commit, pre-push]` is in `.pre-commit-config.yaml` line 1-3 (mitigates R33 detection-window gap)

**False finding retracted:**
- CRIT-4 (Round 10 verification): "PR 3 commit 11 references nonexistent template content" — verification proved the "Before" block is explicitly labeled "if migrated literally from test.yml," aspirational/conditional, not a contradiction. Audit hallucinated. Do NOT remove or rewrite PR 3 commit 11; it's correct as-is.

**Re-characterization (NOT a regression):**
- `tests/migration/__pycache__/` retains stale `.pyc` files for three contract-snapshot tests (`test_a2a_agent_card_snapshot`, `test_mcp_tool_inventory_frozen`, `test_openapi_byte_stability`). The `.py` source files exist on `feat/v2.0.0-flask-to-fastapi` (commits `a2d3b350`, `c736f6c5`, `def4a4ea`), NOT on main or HEAD. `git ls-tree main -- tests/migration/` returns empty. **We did not cause regression coverage loss.** The tests arrive when v2.0 phase PRs land. Pre-flight A15 cleans up the misleading bytecode.

**Total effort delta from Round 10 sweep:** +3.5-4 days across the 6 PRs.
- PR 1: +0.5 day (gitleaks + ADR promotion + ipr-agreement perms)
- PR 2: +0.25 day (commit 4.5)
- PR 3: +1.5 day (3 new jobs + creative-agent bootstrap + env var passthrough + conftest_db.py filelock + concurrency formula + retention-days)
- PR 4: +0.5 day (Layer 4 rewrite + default_install_hook_types + P8 fallback + TROUBLESHOOTING.md)
- PR 5: +0.5 day (Dockerfile @sha256: + USER non-root + ADR copies)
- PR 6: +0.5 day (Trivy + dep-review fixes + SOURCE_DATE_EPOCH + frozen-checks guard update)

**New total:** **~19-23 engineer-days** (was 15-19), still ~6 calendar weeks part-time.

## 2026-04-26 Round 11 verification + extension sweep applied

5 parallel opus subagents covered: (A) drift detection over Round 10 sweep, (B) cross-environment parity matrix (Docker compose / CI / local-uv), (C) failure modes + disaster recovery completeness, (D) documentation lifecycle + reviewer artifacts, (E) resource budgets + observability. ~27 P0 findings + ~30 P1.

**Most critical findings:**

- **R11A-03 (severe — Round 10 self-introduced break):** Round 10's creative-agent `services:` block spec was technically broken in GHA. Service containers cannot resolve each other by hostname (each runs on its own bridge network with the runner host). The OLD `test.yml:180-223` pattern uses `docker network create creative-net` + `docker run` script steps for exactly this reason. **Round 11 fix:** PR 3 commit 3 + commit 9 reverted to the script-step pattern with disk-truth env values (`NODE_ENV=production`, port 9999→8080, path `/api/creative-agent`, 10 env vars matching test.yml verbatim).
- **R11A-04:** Health-check `curl` likely missing from Node creative-agent image. **Fix:** moved health probe from `services.creative-agent.options.--health-cmd` (inside container) to a script step on the host (where curl is preinstalled).
- **R11A-02:** PR 6's `SOURCE_DATE_EPOCH` build-arg silently no-ops because PR 5 Dockerfile didn't declare `ARG SOURCE_DATE_EPOCH`. **Fix:** PR 5 Dockerfile now declares the ARG.
- **R11A-01 / R11C-04 (deadlock risk):** `drafts/guards/test_architecture_required_ci_checks_frozen.py` still hardcoded 11 names. Without update, every PR fails the structural guard once ci.yml emits 14 names — including PR 4 itself. **Fix:** updated the draft to 14 names; PR 3 commit 3 must land guard with the new ci.yml jobs.
- **R11D-01 (false claim contradicting disk):** RESUME-HERE / 00-MASTER-INDEX / EXECUTIVE-SUMMARY all said "ADR-001/002/003 promoted to standalone drafts" but `drafts/README.md` correctly states they're inline in PR 1 spec. **Fix:** struck the false claim from the three top-level docs.
- **R11E-02:** Postgres `max_connections=100` default + 30 conn/worker × 4 xdist workers = 120 → flaky. GHA `services:` cannot pass postgres CLI args. **Fix:** `_pytest` composite sets `DB_POOL_SIZE=4` + `DB_MAX_OVERFLOW=8` env vars (peak 48 connections). Tripwire R31 monitors.
- **R11E-03:** Tox writes pytest-json-report to `{toxworkdir}/<env>.json` but composite uploaded `path: test-results/` → silently empty artifact on every job. **Fix:** composite path now globs both `test-results/` and `.tox/<env>.json`.
- **R11B (11 env-var divergences across 3 environments):** DATABASE_URL credential triple-mismatch (`secure_password_change_me` vs `test_password`); CREATIVE_AGENT_URL port/path mismatch (R10 follow-up — fixed); `ADCP_SALES_PORT` defaults vary across 4 sources; `POSTGRES_PORT` not set in CI causing UI tests to skip silently; `ENCRYPTION_KEY` missing from main `docker-compose.yml`; `SUPER_ADMIN_EMAILS` baseline differs. **Round 11 partial fix:** `_pytest` composite now sets `SUPER_ADMIN_EMAILS=test@example.com`. Remaining items are documented in D43 (canonical DATABASE_URL credentials) and require coordination across PR 3 + PR 5.
- **R11C-04 (cascading deadlock):** see R11A-01.
- **R11C-01 (snapshot SPOF):** Phase B rollback depends on `branch-protection-snapshot.json` being intact and admin token still valid. Single point of failure. New R39 captures.
- **R11C-02 (Phase B Friday lockout):** if Phase B fails Friday and admin is unavailable, main is unmergeable for the weekend. New D45 forbids Phase B on Fri/weekend/holiday eve.
- **R11C-09 + R11C-10 (cosign + tag-immutability cascade):** Sigstore Rekor outage during release leaves an unsigned but published tag, AND tag-immutability locks out fix-by-republish. New R40 captures the chain.

**Net-new decisions D39-D45:**
- D39: creative-agent integration uses script-step pattern (`docker network create + docker run`), NOT GHA `services:` blocks. Codifies R11A-03 fix.
- D40: Postgres `max_connections` cannot be set via GHA `services:` (no `command:` field); reduce app's `DB_POOL_SIZE` + `DB_MAX_OVERFLOW` in CI env instead. Codifies R11E-02 fix.
- D41: pytest-json-report path standardized at `{toxworkdir}/<env>.json`; upload-artifact globs both `test-results/` and `.tox/<env>.json`. Codifies R11E-03 fix.
- D42: `integration_db` test fixture should converge with Alembic schema (long-term — currently uses `Base.metadata.create_all`). Tripwire: post-PR-3, monitor for production drift; if drift detected, schedule a follow-up to route through `migrate.py`. Documents R11B-2.
- D43: DATABASE_URL canonical credentials (user/password/db) — `adcp_user`/`test_password`/`adcp_test` is the CI canonical; compose uses production-like `secure_password_change_me`/`adcp` for dev realism. Tests must NOT hardcode credentials; use the env var. Codifies R11B-1 framing (full unification deferred to a P1 cleanup PR).
- D44: `minimum_pre_commit_version: 3.2.0` directive in `.pre-commit-config.yaml` so `default_install_hook_types` (D31) actually fires. Codifies R11C-06 fix.
- D45: Phase B branch-protection flip is FORBIDDEN on Fri/Sat/Sun + holiday eve; pre-flight A22 enforces. Codifies R11C-02 mitigation.

**Net-new risks R38-R42:**
- R38: Frozen-checks structural guard 11→14 transition deadlock (R11C-04 / R11A-01).
- R39: Phase B snapshot file single point of failure (R11C-01).
- R40: Cosign + Rekor outage + tag immutability cascade (R11C-09 + R11C-10).
- R41: CODEOWNERS or dependabot.yml syntax error silently breaks routing or stops dep updates (R11C-16).
- R42: Phase A overlap window exhausts GHA runner-minutes / memory under double workflow load (R11C-08).

**PR scope additions / corrections:**
- **PR 3 (major correction):** integration-tests job rewritten to use docker-run script-step pattern for creative-agent + adcp-postgres on a custom `creative-net` network (matches test.yml:180-223 verbatim with disk-truth env values). Postgres anchor adds documentation explaining why max_connections can't be tuned via GHA services + the DB_POOL_SIZE workaround. Composite env block sets `SUPER_ADMIN_EMAILS`, `DB_POOL_SIZE=4`, `DB_MAX_OVERFLOW=8`. Composite upload-artifact path globs both `test-results/` and `.tox/<env>.json`.
- **PR 5 Dockerfile:** added `ARG SOURCE_DATE_EPOCH=0` (default 0 = epoch; PR 6's --build-arg overrides). Without ARG declaration, BuildKit silently ignored the build-arg → reproducible-build claim broken.
- **PR 4 (pending application):** add `minimum_pre_commit_version: 3.2.0` directive (D44); fix duplicate "Commit 10" headings; simplify contributor instructions (D31 cascade — remove `--hook-type pre-push` qualifier from CONTRIBUTING references); use `python3 scripts/ops/migrate.py` (matches disk) instead of `uv run python ...`.
- **Pre-flight (pending application):** A20 fork-PR snapshot before Phase B; A21 CODEOWNERS + dependabot.yml syntax validators; A22 Phase B day-of-week guard (no Fri/weekend/holiday eve); A23 creative-agent commit pin freshness check (<3 months old).

**Top-level docs corrected:**
- RESUME-HERE.md, 00-MASTER-INDEX.md, EXECUTIVE-SUMMARY.md no longer claim "ADR-001/002/003 promoted to standalone drafts" — they're inline in PR 1 per drafts/README.md. (R11D-01)
- D1-D28 / R1-R10 stale ranges updated to D1-D45 / R1-R42 (R11D-02).

**Documentation drift (P1 — pending later sweep, not blocking handoff):** architecture.md still has 7 stale "11 frozen" mentions despite §1 banner. PR 1 / PR 4 contributor instructions still say `pre-commit install --hook-type pre-commit --hook-type pre-push` despite D31 simplification.

**Public issue body** still cites "11 frozen check names" (user owns the edit). Reviewer who lands on the issue first gets the wrong canonical count; the planning corpus is correct. Recommend the user update the issue body before handoff.

**Round 11 audit reliability:** R11-A re-verified Round 10 P0 claims; found 5 NEW P0s introduced by Round 10 sweep. R11-B/C/D/E covered axes Round 10 didn't. R11A-03 (creative-agent services break) is the most critical finding — confirms the user's instinct that we keep finding things, AND validates the sweep approach (without R11 verification, the executor would have shipped a broken integration job).

**Net effort delta from Round 11:** +0.5 day across PRs 3/4/5; total now **~19.5-23.5 engineer-days, ~6 calendar weeks part-time** (calendar slack absorbs).

## 2026-04-26 Round 12 verification + sweep applied

3 parallel opus subagents covered: (A) drift detection over Round 11 sweep, (B) end-to-end PR 1→6 continuity, (C) reviewer cold-start simulation. ~19 P0 + ~30 P1 findings.

**Pattern observation:** the round-cadence catches less *architectural* drift each time, but *propagation* drift (stale strings across non-spec surfaces — verify scripts, briefings, executor template, admin scripts, architecture.md) keeps appearing because each sweep round adds new content and forgets some surfaces. Round 12's response is structural: D46 adds a pre-flight grep-guard (P9) to enforce propagation discipline. Without this, Round 13 would find ~10 more "stale strings in script X" gaps.

**Top finding (load-bearing miss, same pattern as smoke gap and `default_install_hook_types`):**
- **R12A-01**: D40's `DB_POOL_SIZE`/`DB_MAX_OVERFLOW` env vars set in `_pytest/action.yml` are NOT read by `src/core/database/database_session.py` (lines 108-109 + 124-125 hardcode the pool sizes as Python literals). Postgres connection-saturation mitigation silently no-opped. **Fix:** new commit added to PR 2 spec wiring `os.getenv("DB_POOL_SIZE", ...)` in app code; `verify-pr3.sh` greps the new code path; D40 amended to document the wiring contract.

**Other critical Round 11 self-introduced gaps:**
- **R12B-01**: `tests/unit/test_architecture_required_ci_checks_frozen.py` referenced as if-existing but no PR commit lifted it from `drafts/guards/`. PR 6 commit 4 verified existence. **Fix:** PR 4 commit 3 lift step added explicitly; verify-pr4.sh checks existence; the structural guard now lands.
- **R12B-02**: `scripts/flip-branch-protection.sh` + `scripts/capture-rendered-names.sh` + `scripts/verify-pr3.sh` hardcoded with the OLD 11-name list — Phase B atomic flip would 422 OR leave 3 names in "expected — waiting" state. **Fix:** all 3 scripts updated to 14 names.
- **R12B-05**: D44 says "PR 4 commit 1 adds `minimum_pre_commit_version: 3.2.0`" but the spec body had only `default_install_hook_types`. **Fix:** PR 4 commit 1 spec body extended; verify-pr4.sh checks both directives.
- **R12B-03/04/06**: verify-pr5.sh missing `USER`/`SOURCE_DATE_EPOCH`/`@sha256:` greps; verify-pr6.sh missing `aquasecurity/trivy-action`; verify-pr3.sh missing filelock check. **Fix:** all 4 verify scripts extended.
- **R12C-05**: `templates/executor-prompt.md` had stale "D1-D28", "11 frozen", "18 rules". **Fix:** corrected to D1-D45, 14 frozen, 19 rules; D31/D45 explicitly cited.
- **R12A-02**: D39 prose had port direction reversed (said `port 8080:9999 (host:container)` — disk truth is `-p 9999:8080`). **Fix:** corrected.
- **R12C-02**: `RESUME-HERE.md:34` still had the unstruck "ADR-001/002/003 promoted to standalone drafts" claim alongside the corrected version. **Fix:** struck.

**Net-new decisions D46 (Round 12):**
- D46: Pre-flight P9 grep-guard for stale-string drift. Before any sweep round closes, the executor MUST run `scripts/check-stale-strings.sh` to catch references like "11 frozen", "D1-D28", "R1-R10" outside explicit history-marker contexts (architecture.md banner, this file's audit-trail sections). Non-zero exit blocks the sweep from being declared complete. Codifies the propagation pattern that Round 11 R11D-02 + Round 12 R12-C kept catching.

**Net-new risks R43 (Round 12):**
- R43 (Med×High): Verify-script drift behind spec amendments. Each sweep adds new content to per-PR specs but historically the verify scripts trail by 1-2 rounds, leading to silent-skip gaps where the script reports SUCCESS even when D-mandated content is missing. Mitigation: every D-numbered spec change must include a parallel verify-script update (formalized in D46's pre-flight P9).

**P1 findings (mechanical sweep — applied in this commit):**
- EXECUTIVE-SUMMARY.md last-refresh, effort, R/D ranges updated to current state
- 00-MASTER-INDEX.md effort updated
- 02-risk-register.md header sentence + R19 mitigation + R31 mitigation note pointing at D40
- verify-pr1.sh gitleaks check added
- verify-pr4.sh marker `architecture` → `arch_guard`; deletion list 15 → 16 (test-migrations); pre-push list 9 → 10 (mypy per D3)
- PR 3 spec commit 3 subject "11 frozen" → "14 frozen"; PR 3 ci.yml internal references swept
- briefings/pr1-briefing.md, pr3-phase-a-briefing.md, point4-phase-b-flip.md: 11 → 14
- architecture.md: residual "11 frozen" mentions left with banner pointing at D30 (per file-status declaration as audit trail)

**Post-Round-12 invariants:**
- Verify scripts cover ALL P0 spec content (R12B-01 through R12B-06 closed)
- Admin scripts (flip-branch-protection.sh, capture-rendered-names.sh, add-required-check.sh) all on the 14-name list
- Executor template references current state (D1-D45, 14 frozen, 19 rules)
- D46/P9 prevents future propagation drift

**Round 13 verification** — NOT recommended unless a substantive content change lands. Round 12 closed the structural propagation gap; further rounds would yield diminishing returns.

## 2026-04-26 Round 12 post-issue-review findings

While rewriting the public issue bodies for #1234 / #1233 / #1228 / #1189, three genuine new findings surfaced that 12 audit rounds had all missed. Integrated into the corpus:

- **D47 + R44 (P0 — most critical)**: `release-please publish-docker` had only `needs: release-please` (no test gate). After PR 6's cosign + SBOM + Trivy + reproducible-builds extension, red main could ship **signed-and-attested-but-broken** images — the supply-chain trail makes the bad build look verified. Closed by adding a "Require CI green on release commit" step using `gh api` (cross-workflow gate; `needs:` doesn't span workflows). Closes #1228 Cluster A4. `verify-pr6.sh` extended with the check.
- **A5 residual** (Round 11 R11E-04 caught but didn't fully spec): 5 ci.yml jobs (Quality Gate, Type Check, Migration Roundtrip, Coverage, Summary) inherited the GHA 360-min default. PR 3 spec now sets explicit `timeout-minutes` (10/10/10/10/5). `verify-pr3.sh` extended with a YAML-walking check that ALL jobs have explicit timeouts.
- **C5** (from #1228 issue rewrite): uv cache key only hashed `uv.lock`. A `pyproject.toml` change without `uv lock` regen would silently get a stale cache hit. `_setup-env` composite's `cache-dependency-glob` now hashes both files. Closes #1228 Cluster C5.

**Acknowledged-but-deferred (out of #1234 scope):**
- #1189 creative-agent caching — small follow-up PR after PR 3 merges; reuses #1234 PR 6 commit 2's `build-push-action@v7.1.0 + cache-from/to: type=gha` reference pattern
- #1228 Tier 3 architectural items (F1 ruff ignores, F2 mypy lenient flags, G2 obligation allowlist 301 entries) — separate post-#1234 architectural-debate issues
- #1228 E1/E2 (`google_ad_manager_original` phantom refs + `.mypy_baseline` orphan) — low-priority cleanup
- #1233 D11 (`requires_server` tests delete-or-resurrect decision) — out of #1234 scope per #1233 closure plan
- #1233 D13 (GAM live tests) — separate nightly-cron follow-up issue

**Pattern note:** the post-issue-review surfaced findings the round-cadence audits missed because the audits focused on internal corpus consistency, not on the public issue artifacts that frame stakeholder expectations. Future sweeps should include a "grep the GitHub issue body against the corpus" pass — issue body claims that contradict the corpus are a third propagation-drift class beyond what D46/P9 catches.

**Effort delta from post-issue-review:** ~0.25 day. New total: **19.75-23.75 engineer-days, ~6 calendar weeks part-time.**



## 2026-04-25 P0 sweep applied (Round 5 + Round 6)

13 reviewer reports across 2 rounds surfaced ~30 P0 defects. Sweep applied:

**Top-level docs**: D11 reframed (drop "advisory" — hard gate from day 1); D18 rewritten (~73 final post-v2.0-rebase, was 42); D27 rewritten (real baseline 33 effective, math 33−13−9−1=10); D28 added (defer target-version bump per ADR-008); R19/R20/R23 promoted; R26-R30 added; calendar updated (no Week 7-8 flip step); v2.0 coordination updated with 341 files / 31 arch tests / 9 baseline JSONs.

**PR 1**: drop commit 10 (Gemini → PR 3); pin black at 25.1.0; codeql-action v4 (was v3); harden-runner v2.16+ (was v2.12+); CONTRIBUTING.md scope corrected (docs version canonical); SHA-freeze regex relaxed; skip-friendly verify messages; ipr-agreement.yml in permissions list; pinact + actionlint added to security.yml; zizmor org rename to zizmorcore.

**PR 2**: v2.0 mirrors-mypy three-way collision note; helpers baseline rationale; uv 0.11.7 bump.

**PR 3**: bare-name sweep (`'CI / Quality Gate'` → `'Quality Gate'`); `.contexts[]` → `.checks[].context` field fix; `_pytest.yml` reusable → `_pytest/action.yml` composite (eliminates 3-segment rendered-name); Gemini commit added (from PR 1); coverage hard-gate framing; develop branch trigger; --paginate + --app_id; flip script idempotent + dry-run.

**PR 4**: hook math revised (33 effective baseline); `no-hardcoded-urls` classified; CONTRIBUTING.md `pre-commit install --hook-type pre-push` instruction; architecture-guards pre-push hook install commit; CLAUDE.md table audit deferred to post-v2.0-rebase (residual 2 rows only).

**PR 5**: drop `--no-verify` carve-out; drop `ruff --fix --select UP`; drop docker-compose enumeration (already at postgres:17-alpine); reference D28; extend uv-anchor guard scope.

**PR 6**: `release.yml` → `release-please.yml` everywhere; drop "add multi-arch" framing (already exists); harden-runner v2.16+ pin; cosign --bundle docs; egress allowlist additions (codeload, sigstore, Docker Hub, GHCR pkg, blob.core.windows.net, StepSecurity); scorecard.yml workflow file added; ghcr.io tag immutability admin step.

**Drafts**: 2 helpers added (`iter_python_version_anchors`, `iter_postgres_image_refs`); `test_architecture_required_ci_checks_frozen.py` created; ADR-007 updated; ADR-008 created (defer target-version); ADR-009 created (Rulesets future); 5 governance guards adopt `assert_violations_match_allowlist`; issue refs stripped from draft docstrings.

**Templates**: executor-prompt Rules 16/17/18 added (skip beads workflow rules; ESCALATION terminal message; 6-suite consistency).

**New directories**: `escalations/` and `in-flight/` created with `.gitkeep` + README.

**Pre-flight**: A11-A14 added (allow_auto_merge audit; Dependabot drain; mypy plugin snapshot; admin tier confirmation).

**Runbooks**: G1 ADMIN-ONLY header added; PR4-partial-deletion-recovery.md created.

**Round 7 verification** is recommended before launching PR 1 to confirm no drift introduced by this sweep.

## 2026-04-25 Round 9 verification sweep applied

6th verification round (5 parallel Opus subagents on per-PR specs + 1 on drafts/scripts/templates) surfaced ~30 additional findings across cross-PR state handoffs, ecosystem drift, failure modes, layer-model completeness, and governance. P0 + P1 fixes applied to all 6 PR specs and supporting files.

**Net-new P0 fixes:**
- **D29** (marker rename): structural-guard marker `architecture` → `arch_guard` to avoid collision with entity-marker auto-tagged in `tests/conftest.py:25-45,146-153`. Registration target: `pytest.ini` (NOT `pyproject.toml`). PR 2 commit 8 + PR 4 commits 1-2 updated.
- **PR 3 commit 4b** (NEW): `integration_db` template-clone optimization — replaces per-test `CREATE DATABASE + metadata.create_all` (~400-900ms × 600 tests) with template-clone (~10-50× faster). Without this, xdist saturates Postgres connection pool.
- **PR 3 xdist-workers wiring**: composite input declared but not piped to pytest invocation; fixed.
- **PR 3 tox.ini coverage gate sync**: `--fail-under=30` updated to read from `.coverage-baseline` (resolves CI/local divergence).
- **PR 4 Commit 1.5** (NEW): AST guard pre-existing-violation audit — `check-rootmodel-access` AST equivalent surfaces ~18 pre-existing violations across `src/` + `tests/`. Hard gate before Commit 7 deletes legacy grep hooks.
- **PR 6 cosign sign --bundle**: required in cosign v3+ (was optional in v2). Without it, release CI errors on first tag push.
- **PR 6 zizmor --persona=auditor**: `secrets-outside-env` rule is auditor-persona-only in zizmor 1.24+; default invocation does not fire it.
- **PR 2 mypy plugin canary** (NEW): D13 tripwire ">200 errors" cannot distinguish "plugin loaded" from "plugin silently disabled". Canary `tests/unit/_pydantic_mypy_canary.py` with deliberate type error proves plugin loaded.

**P0 mechanical version corrections (PR 5 + PR 6):**
- `uv 0.11.6 → 0.11.7` (PR 5, 7 occurrences)
- `scorecard-action # v2.5.0+ → # v2.4.3+` (v2.5.0 doesn't exist)
- `cosign-installer # v3 → # v4.1.1`
- `attest-build-provenance # v2 → # v4.1.0`
- `harden-runner` SHA pin to v2.19.0 (floor stays v2.16+)
- Docker actions: setup-qemu/buildx → v4.0.0, login → v4.1.0, build-push → v7.1.0

**P1 sweep:**
- PR 3 YAML anchors for postgres `services:` block (5x → 1x).
- PR 3 setup-env composite gains `--frozen` and `--no-install-project` inputs.
- PR 3 worker-id-suffix tox json-report paths under xdist.
- PR 3 filelock + worker-id gate around `migrate.py` invocation.
- PR 3 py-cov-action env vars (MINIMUM_GREEN, ANNOTATE_MISSING_LINES).
- PR 3 Phase B Step 2.5: in-flight PR drain procedure.
- PR 4 ≤11 commit-stage warn band on hook count.
- PR 4 `scripts/check-hook-install.sh` pre-push install nudge.
- PR 4 mypy warm-time pre-flight measurement (Layer-2 budget verification).
- PR 4 canonical Hook → Stage Reference Table.
- PR 4 `test-migrations` added to deletion list (was delegated to v2.0).
- PR 6 harden-runner egress: `+registry.npmjs.org`, `+raw.githubusercontent.com`.
- PR 6 `harden-runner-emergency-revert.yml` workflow (P1 — manual-dispatch contributor-recoverable lockout).
- PR 1 CODEOWNERS scoped sections for ratchet baselines and test infrastructure.
- **R16 promoted** (Dependabot uv.lock corruption); **R31 added** (integration_db throughput); **R32 added** (Phase B in-flight PR race).
- **Templates added**: `templates/adr-template.md` (canonical ADR structure); executor-prompt **Rule 19** (empirical pre-flight).
- **Scripts added**: `scripts/_lib.sh` (shared verify-pr*.sh helpers).

**Refuted prior findings (do NOT re-apply):**
- `check_import_usage.py` is already AST-based (243 LOC of `ImportCollector`/`UsageCollector` visitors); the migration is scope expansion, NOT a technique change.
- `feedback_no_beads_workflow.md` user-memory is INACCURATE for this repo: `.beads/issues.jsonl` exists (~1.3MB, active). However, of 14 distinct `FIXME(salesagent-xxxx)` IDs in src+tests, only 2 have matching beads issues; 12 are dangling (real accountability gap, different cause).
- factory-boy `Sequence` collision risk under xdist is mitigated by per-test UUID DB pattern (still warrants `worker_id` mixing as defense-in-depth, but P2 not P0).
- `tests/conftest_db.py` mutations are at lines 478-486 (not 470-486 as prior audit said).

If you are a fresh agent picking this up cold, read this file first.

---

## What this rollout is

GitHub issue [#1234](https://github.com/prebid/salesagent/issues/1234) — refactor `.pre-commit-config.yaml` and `.github/workflows/` into a layered, supply-chain-hardened CI system. **6 PRs** (PR 1-5 core + PR 6 follow-up). Estimated **~15-19 engineer-days, ~6 calendar weeks part-time.**

Concurrent work: v2.0 (Flask-to-FastAPI) under [PR #1221](https://github.com/prebid/salesagent/pull/1221). **D20** chose Path 1 — issue #1234 lands first; v2.0 phase PRs rebase.

---

## Read order for cold-start (~14-20k tokens total)

1. **This file** (`RESUME-HERE.md`) — orientation
2. **[`EXECUTIVE-SUMMARY.md`](EXECUTIVE-SUMMARY.md)** — single-screen orientation; if you read no other research file, read this
3. [`00-MASTER-INDEX.md`](00-MASTER-INDEX.md) — status table, calendar, sequencing
4. [`03-decision-log.md`](03-decision-log.md) — every locked decision (D1-D48 as of Round 13)
5. [`02-risk-register.md`](02-risk-register.md) — R1-R47 (Round 10 added R33-R37; Round 11 added R38-R42; Round 12 added R43; post-issue-review added R44; Round 13 added R45-R47); **R11-R15 RESERVED** (Round 14 M4 — never formally defined); R17-R18, R21-R22, R24-R25 remain LOW info in `research/edge-case-stress-test.md`
6. [`01-pre-flight-checklist.md`](01-pre-flight-checklist.md) — admin actions A1-A25 + agent steps P1-P10 (Round 13 added A24-A25; A26 deleted in Round 14)
7. **The per-PR spec for the PR you're working on** (`pr1-supply-chain-hardening.md` … `pr6-image-supply-chain.md`)
8. [`templates/executor-prompt.md`](templates/executor-prompt.md) — agent operating contract (now embeds the 22 continuity-hygiene rules; rules 16/17/18 added in P0 sweep; rules 20/21/22 added in Round 13)
9. `CLAUDE.md` at repo root — codebase patterns; non-negotiable
10. [`COORDINATION.md`](COORDINATION.md) — claim a PR before starting work

---

## What changed in the 2026-04-25 cleanup pass

### Critical blockers — FIXED in specs

1. **Workflow naming bug** — 11 sites in `pr3-ci-authoritative.md` updated: job `name:` strings now bare (e.g., `'Quality Gate'`), workflow header stays `name: CI`. GitHub auto-prefix produces correct `CI / Quality Gate` rendering. New decision **D26** locks this convention.
2. **PR 4 hook count** — `pr4-hook-relocation.md` commit 5 now moves **10** hooks to pre-push (was 5; then 9; revised Round 8 to 10 — adds `mypy` as the 10th per D3). **Real math (revised 2026-04-25 Round 8):** 36 effective commit-stage − 13 deletions − 10 moves − 1 consolidation = **12** (exactly at ≤12 ceiling, zero headroom). The earlier "33 effective" framing was off by 3 (40 active − 4 manual = 36, not 33). New decision **D27** (revised) locks this.
3. **`_architecture_helpers.py` collision** — `pr2-uvlock-single-source.md` commit 8 creates baseline (~30 lines); `pr4-hook-relocation.md` commit 1 explicitly EXTENDS to ~221 lines (reconciled draft at `drafts/_architecture_helpers.py`).

### External technical corrections applied

4. **harden-runner** in `pr6-image-supply-chain.md` and `research/external-tool-yaml.md` updated to use `disable-sudo-and-containers: true` (was `disable-sudo: true`) per [CVE-2025-32955](https://www.sysdig.com/blog/security-mechanism-bypass-in-harden-runner-github-action). Pin requirement: v2.16.0+.
5. **mirrors-mypy migration** in `pr2-uvlock-single-source.md` reframed — mirrors-mypy is NOT deprecated; the migration is to fix isolated-env import resolution per [Jared Khan](https://jaredkhan.com/blog/mypy-pre-commit) and [mypy#13916](https://github.com/python/mypy/issues/13916).
6. **Phase B rendered-name capture** added to `pr3-ci-authoritative.md` Step 1b — `gh api commits/<sha>/check-runs` to confirm names match the PATCH body before flipping. Reusable workflow nesting can produce 3-segment names; verify before flip.
7. **`persist-credentials: false`** propagated to all `actions/checkout` calls in `pr1-supply-chain-hardening.md` commit 9 (was only on PR 6's release.yml). Closes Scorecard `Token-Permissions` gap and addresses [actions/checkout#2312](https://github.com/actions/checkout/issues/2312).
8. **OpenSSF Scorecard target** phased in `00-MASTER-INDEX.md`: ≥6.5 after PR 1, ≥7.5 after PR 6 (PR 1 alone won't satisfy `Signed-Releases`).
9. **Action-SHA artifact** — PR 1 commit 9 now persists resolved SHAs to `.github/.action-shas.txt` so PR 3 commit 5 reuses them (no shell-history dependency).

### Decisions promoted

- D-pending-1 → **D22** (zizmor placement: CI-only)
- D-pending-2 → **D23** (check-parameter-alignment: delete)
- D-pending-3 → **D24** (UV_VERSION anchor in setup-env)
- D-pending-4 → **D25** (harden-runner adoption: PR 6)
- New: **D26** (workflow naming convention)
- New: **D27** (hook reallocation: 9 to pre-push)
- D-pending-5 dangling reference removed (was never a real decision; inline acceptance criterion in PR 4)
- **PD15** disambiguated: PR 1 closes both **PD15a** (SHA-pin) and **PD15b** (workflow permissions)

### Cleanup applied

- Deleted: `EXECUTIVE-SUMMARY-DRAFT.md` (promoted to `EXECUTIVE-SUMMARY.md`)
- Deleted: `minimal-context-bundle.md` (subsumed by EXECUTIVE-SUMMARY)
- Deleted: `self-sufficiency-scores.md` (round-3 audit artifact, superseded)
- Deleted: `continuity-hygiene.md` (15 rules merged into `templates/executor-prompt.md`; rules 16/17/18 added in P0 sweep — total 18)
- Deleted: 3 hypothetical context-wipe briefings (`briefings/point2/3/5*.md`)
- Deleted: 2 thin Phase-B/C briefings (content already in PR 3 spec)
- Created: `scripts/` directory with 6 verify-pr scripts + Phase B helpers (`capture-rendered-names.sh`, `flip-branch-protection.sh`, `add-required-check.sh`)
- Created: `research/README.md` and `drafts/README.md` audit-trail markers
- Renamed: `drafts/adr-007-build-provenance-attestation.md` → `adr-007-build-provenance.md` (matches spec/script paths)

### Second-pass executor-readiness fixes (2026-04-25 final)

After the first pass landed, two opus subagents simulated cold-start and surfaced 13 more issues across PR 1 and PR 6. Fixed:

**PR 1:**
- Embedded concrete `[project.urls]` block (5 keys matching `verify-pr1.sh` expectations)
- Embedded ADR-001 body verbatim in spec (was a dangling reference)
- Embedded `.github/zizmor.yml` content with rules + dangerous-triggers allowlist
- Labeled CONTRIBUTING.md commit explicitly as authoring task (not a lift)
- Fixed `verify-pr1.sh` ADR-002 filename: `codeowners-bypass` → `solo-maintainer-bypass`
- Fixed `verify-pr1.sh` SHA-freeze regex to match `<sha>  # frozen: v<tag>` format
- Reconciled guard ownership: ALL 8 new guards owned by PR 4 (was split across PR 1/3/4); `drafts/README.md` and `REFACTOR-RUNBOOK.md` updated
- Rewrote `drafts/guards/test_architecture_required_ci_checks_frozen.py` to enforce D26 (bare job names + workflow `name: CI`); removed contradiction with the workflow naming convention

**PR 6:**
- **CRITICAL fix:** rewrote Commit 2 to EXTEND existing `release-please.yml publish-docker` job rather than create a new `release.yml` (would have raced and produced duplicate publishes). Multi-arch (`linux/amd64,linux/arm64`) and Docker Hub publishing PRESERVED.
- Added CVE-2025-32955 fix to Commit 2 release-job harden-runner block (was only on Commit 1)
- Added CVE-2025-32955 fix to Commit 4 dependency-review job
- Added StepSecurity dashboard URL extraction recipe to Commit 3 (allowlist guidance was vague)
- Tagged Commit 4 admin step as ADMIN-ONLY; created `scripts/add-required-check.sh` companion for adding new required checks
- Refreshed stale `briefings/pr6-briefing.md` (was 1 week, now ≥2 weeks; sub-PR A/B model)
- Refreshed stale `checklists/pr6-checklist.md` (split into Sub-PR A first commits + Sub-PR B audit→block flip)
- Expanded `scripts/verify-pr6.sh` to cover dep-review, CodeQL gating flip, multi-arch + Docker Hub regression checks, SHA-pinning enforcement

### Calendar

- Extended from 5 weeks to **6 weeks** part-time. Week 4 was over-packed (Phase B + C + PR 4 ≥48h soak each); PR 4 moved to Week 5; PR 6 lands Week 6.

---

## What's still open (recommend before launching executor)

These are nice-to-have improvements not blocking executor handoff:

- **R16-R25 partial integration:** R16, R19, R20, R23 promoted into `02-risk-register.md`; R17-R18, R21-R22, R24-R25 remain LOW-impact informational in `research/edge-case-stress-test.md`. R11-R15 are RESERVED (never minted; see `02-risk-register.md` numbering note added Round 14 M4).
- **CLAUDE.md guards table** in `drafts/claudemd-guards-table-final.md` not yet lifted to `CLAUDE.md` — that happens during PR 4 commit 9 execution, not pre-execution. Confirmed.
- **Aggressive briefings/runbooks cleanup:** the redundancy audit identified ~21 of 28 runbooks and ~6 of 13 briefings as low-value. Today's pass deleted 5 briefings; if the user wants further reduction, it's in `research/handoff-readiness-audit.md` recommendations.
- **PD15a/PD15b disambiguation** is in `00-MASTER-INDEX.md` PR 1 row but not yet reflected throughout PR 1 spec body. The text says "Closes PD15a + PD15b" in commit 9 — sufficient for executor.

---

## Critical blockers FIXED — was-vs-now (audit trail)

| Blocker | Was (pre-fix) | Now |
|---|---|---|
| #1 | `pr3-ci-authoritative.md:184` `name: CI` + 11 jobs `name: 'CI / X'` | All 11 jobs `name: 'X'`; D26 documents convention; Phase B has rendered-name capture |
| #2 | PR 4 acceptance ≤12 with 36→16 math (off by 4 vs ≤12) | 9 hooks to pre-push, real math 33−13−9−1=10 (D27 revised in 2026-04-25 P0 sweep); 4 added candidates: mcp-schema-alignment, check-tenant-context-order, ast-grep-bdd-guards, check-migration-completeness |
| #3 | Both PR 2 c8 + PR 4 c1 said "create" `_architecture_helpers.py` | PR 2 creates baseline (~30 lines); PR 4 EXTENDS to ~221 lines per `drafts/_architecture_helpers.py` |

---

## What you must NEVER do (any session)

- **Push to origin or open PRs** — user owns these (per `feedback_user_owns_git_push.md` memory)
- **Mutate branch protection** via `gh api -X PATCH branches/main` — admin-only; only the user runs `scripts/flip-branch-protection.sh`
- **Use `--no-verify`, `--ignore`, `-k "not …"`, `pytest.mark.skip`** to bypass failing tests — CLAUDE.md test-integrity policy is zero-tolerance
- **Bundle CSRF middleware into PR 1** — D10 chose Path C; v2.0's `src/admin/csrf.py` is expected to address the 99 missing-CSRF findings
- **Auto-merge Dependabot PRs** — D5 forbids absolutely
- **Touch files outside your PR's spec scope** — strict per-PR boundaries
- **Use `harden-runner`'s `disable-sudo: true`** — bypassable per CVE-2025-32955; use `disable-sudo-and-containers: true`
- **Frame mirrors-mypy migration as "deprecation"** — it isn't; reframe as isolated-env import-resolution fix
- **Amend `03-decision-log.md` mid-execution** (Round 13 decision-freeze; ambiguities → write `escalations/pr<N>-<topic>.md` and STOP)

---

## When to STOP and escalate

- A test fails you can't diagnose in 15 minutes → write `escalations/pr<N>-<topic>.md` and STOP
- Branch-protection action requested → admin only; ask user
- Mypy delta >200 in PR 2 → D13 tripwire; comment out `pydantic.mypy` plugin, file follow-up
- Phase A check fails on main → don't flip; investigate
- Rendered names diverge from D17 expected list → don't flip; either fix names or update PATCH body
- harden-runner block-mode locks out CI → revert to audit; capture more telemetry
- Dependabot backlog ≥5 open PRs → pause forward work, clear backlog (D5 sustainability tripwire)

---

## Next step — launch executor

The plan refactor is complete. To launch an executor on PR 1:

1. Read `EXECUTIVE-SUMMARY.md` (~3k tokens)
2. Read `pr1-supply-chain-hardening.md` (~38k tokens, but you only need it once)
3. Read `templates/executor-prompt.md` (~1.5k tokens)
4. Confirm pre-flight A1-A25 + P1-P10 are complete (some are admin-only — user runs)
5. Fill in the executor prompt template with PR 1 specifics from spec + briefing + checklist
6. Launch the executor in a fresh session

**Best practice:** launch one PR at a time. Do not run multiple PR executors in parallel — too much risk of file conflicts and merge ordering hazards.

---

## Disk inventory (post-cleanup)

```
.claude/notes/ci-refactor/
├── RESUME-HERE.md                  ← orientation (this file)
├── EXECUTIVE-SUMMARY.md            ← single-screen (post-cleanup, replaces -DRAFT)
├── REFACTOR-RUNBOOK.md             ← 11-step plan (now superseded — most steps applied 2026-04-25; kept as audit trail)
├── 00-MASTER-INDEX.md              ← status, calendar (6 weeks), 6 PRs
├── 01-pre-flight-checklist.md
├── 02-risk-register.md             ← R1-R47 (Round 10 added R33-R37; Round 11 added R38-R42; Round 12 added R43; post-issue-review added R44; Round 13 added R45-R47)
├── 03-decision-log.md              ← D1-D48 (Round 10 added D30-D38; Round 11 added D39-D45; Round 12 added D46; post-issue-review added D47; Round 13 added D48)
├── architecture.md                 ← current vs target
├── landing-schedule.md             ← 6-week dependency graph
├── preflight-ttl-guard.md          ← TTL bash block for per-PR checklists
├── COORDINATION.md                ← Round 13 / reframed Round 14: parallel-agent PR-claiming registry
├── REBASE-PROTOCOL.md             ← Round 13: rebase order for shared files
├── pr1-supply-chain-hardening.md   ← PR 1 spec (with persist-credentials, .action-shas.txt artifact)
├── pr2-uvlock-single-source.md     ← PR 2 spec (with mirrors-mypy reframe + helpers baseline note)
├── pr3-ci-authoritative.md         ← PR 3 spec (Blocker #1 fixed; rendered-name capture added)
├── pr4-hook-relocation.md          ← PR 4 spec (Blocker #2 + #3 fixed)
├── pr5-version-consolidation.md
├── pr6-image-supply-chain.md       ← PR 6 spec (CVE-2025-32955 mitigation)
├── briefings/                      ← 8 files (was 13 — deleted 5)
│   ├── point1-pre-pr1.md, point4-phase-b-flip.md
│   └── pr1-, pr2-, pr3-phase-a-, pr4-, pr5-, pr6-briefing.md
├── checklists/                     ← 8 per-PR checklists
├── drafts/                         ← 4 ADRs + helpers + 8 guards + table + hook + README
├── research/                       ← 6 audits + README (read-only audit trail)
├── runbooks/                       ← 28 operational playbooks (top 5 most-likely-needed: A4, B3, D1, G1, G2)
├── scripts/                        ← NEW: 6 verify-pr scripts + capture-rendered-names + flip-branch-protection + README
└── templates/
    ├── executor-prompt.md          ← rewritten 2026-04-25 with embedded continuity hygiene (22 rules as of Round 13)
    └── pr-description.md
```

Total: ~25 files at root + 5 subdirectories = lean enough for cold-start. Audit trail preserved in `research/` and `drafts/` per their READMEs.
