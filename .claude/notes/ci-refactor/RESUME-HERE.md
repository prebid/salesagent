# Resume Here — CI/Pre-commit Refactor

**Date snapshot:** 2026-04-25 (post-integrity-audit + Round 5+6 P0 sweep applied, blockers fixed, executor-handoff ready).

**Rollout status:** **READY FOR EXECUTOR HANDOFF (after Round 7 verification).** 4 rounds of opus-subagent research + integrity audit + Round 5+6 P0 sweep applied 2026-04-25. The 3 critical blockers (workflow naming, hook count, helpers collision) are fixed in the per-PR specs. External technical corrections applied (mirrors-mypy reframed, harden-runner CVE-2025-32955 + DoH-bypass v2.16+ floor, persist-credentials propagation, rendered-name capture). D-pending-1..4 promoted to D22-D25; D26 + D27 + D28 added; R19/R20/R23 promoted from edge-case-stress-test; R26-R30 added.

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
- `feedback_no_beads_workflow.md` user-memory is INACCURATE for this repo: `.beads/issues.jsonl` exists (107KB, active). However, of 14 distinct `FIXME(salesagent-xxxx)` IDs in src+tests, only 2 have matching beads issues; 12 are dangling (real accountability gap, different cause).
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
4. [`03-decision-log.md`](03-decision-log.md) — every locked decision (D1-D28)
5. [`02-risk-register.md`](02-risk-register.md) — R1-R10 + R19/R20/R23 promoted + R26-R30 added; R11-R18, R24-R25 remain LOW info in `research/edge-case-stress-test.md`
6. [`01-pre-flight-checklist.md`](01-pre-flight-checklist.md) — admin actions A1-A14 + agent steps P1-P6
7. **The per-PR spec for the PR you're working on** (`pr1-supply-chain-hardening.md` … `pr6-image-supply-chain.md`)
8. [`templates/executor-prompt.md`](templates/executor-prompt.md) — agent operating contract (now embeds the 18 continuity-hygiene rules; rules 16/17/18 added in P0 sweep)
9. `CLAUDE.md` at repo root — codebase patterns; non-negotiable

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

- **R11-R25 integration:** the 15 edge-case risks in `research/edge-case-stress-test.md` are not yet in `02-risk-register.md`. Append when bandwidth allows.
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
4. Confirm pre-flight A1-A14 + P1-P6 are complete (some are admin-only — user runs)
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
├── 02-risk-register.md             ← R1-R10 + R19/R20/R23 promoted + R26-R30 added in P0 sweep
├── 03-decision-log.md              ← D1-D28
├── architecture.md                 ← current vs target
├── landing-schedule.md             ← 6-week dependency graph
├── preflight-ttl-guard.md          ← TTL bash block for per-PR checklists
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
    ├── executor-prompt.md          ← rewritten 2026-04-25 with embedded 18-rule continuity hygiene
    └── pr-description.md
```

Total: ~25 files at root + 5 subdirectories = lean enough for cold-start. Audit trail preserved in `research/` and `drafts/` per their READMEs.
