> **Status:** Scope **unchanged by #1234**. The CI/pre-commit refactor preserves the bare `docker build` pattern (per #1234 D32 + D39, the build-from-source-tarball flow at `test.yml:180-223` is carried forward verbatim into ci.yml's `integration-tests` job). #1228 Cluster C2 explicitly tracks creative-agent caching under this issue. **#1234 contributes a reference implementation for Solution 1**: PR 6 commit 2 uses `docker/build-push-action@v7.1.0` with `cache-from/to: type=gha` for the salesagent image — the same pattern can apply to the creative-agent build.
> **Recommended landing:** small follow-up PR after #1234 PR 3 merges. The integration-tests job in `ci.yml` is the new home for the build step.

## TL;DR

`Integration (creative)` builds the entire [adcontextprotocol/adcp](https://github.com/adcontextprotocol/adcp) monolith from source on every run — ~1,640 npm packages, TypeScript compile, 27 git submodule clones — with **zero Docker layer caching and no retry**. A single transient `ECONNRESET` from the npm registry kills the entire job. Build time: ~2 min/run; with caching this drops to seconds on cache hit.

**Two changes ship 95% of the value in <1 day of work**: (1) `docker/build-push-action` + GHA cache, (2) retry loop. Solutions 3 (self-hosted ghcr.io pre-build) and 4 (upstream publishes) are real but heavier — pursue if Solutions 1+2 prove insufficient.

## Problem

### What the creative job does differently

The `creative` integration matrix group is the only one that builds an external Docker image. The step at [test.yml:180-237](https://github.com/prebid/salesagent/blob/main/.github/workflows/test.yml#L180) does:

1. Downloads upstream `adcontextprotocol/adcp` at pinned commit `ca70dd1e2a6c`
2. Runs `docker build` — a 3-stage Node.js multi-stage build:
   - **Stage 1 (builder):** `npm ci --ignore-scripts` (1,640 packages, ~41s) → TypeScript compile (~37s)
   - **Stage 2 (repos):** Shallow-clones 27 external git repositories for the "Addie" search feature (~14s)
   - **Stage 3 (production):** `npm ci --omit=dev` + `npm rebuild sharp` (~25s)
3. Creates a Docker network, starts a Postgres sidecar, boots the monolith at port 9999
4. Health-checks `/api/creative-agent/health` (up to 60 retries)

**Total build overhead: ~2 minutes per run**, making creative the CI bottleneck at ~6.5 min vs 1.5–5 min for other integration shards.

### Post-#1234 location of the same code

After **#1234 PR 3** lands (Phase C deletes `test.yml`), this build step lives in `ci.yml`'s `integration-tests` job as a `docker run` script-step pattern (D32+D39 — services-block design was rejected in Round 11 because GHA service containers can't cross-resolve hostnames). The pinned commit, the network, the env vars, and the health check are all preserved verbatim from `test.yml:180-223`. **The fragility moves with the code; #1234 does not address it.**

### Why it's fragile

| Fragility | Impact |
|---|---|
| **No Docker layer caching** — bare `docker build` with no `--cache-from`, no buildx, no GHA cache | Full rebuild every run even though the pinned commit `ca70dd1e2a6c` never changes between runs |
| **No retry on build failure** — single `ECONNRESET` during any of 1,640 npm package downloads kills the job | Transient network flakes cause complete job failure (PR #1188 was the first observed failure in 30 runs — latent fragility) |
| **Full monolith for one endpoint** — tests only call `list_creative_formats` (one MCP tool) + a health check, but we build the entire server (27 cloned repos, WorkOS auth, Stripe, full DB migrations) | ~90% of the build work is wasted |
| **Pinned to a specific commit** — `ca70dd1e2a6c` is hardcoded because upstream HEAD has broken migrations (`community_points FK violation`) | Pin drifts behind upstream; manual updates; commit-level pin makes caching reasoning harder. #1234 D32 tripwire + pre-flight A23 watch for staleness (>3 months old). |
| **No pre-built image available** — upstream doesn't publish Docker images to ghcr.io or Docker Hub | Building from source is currently the only option |
| **Inline Docker commands** — entire infra (network, postgres sidecar, container, health check) is raw shell in workflow YAML | Can't test locally, hard to maintain |
| **Inconsistent with the rest of the pipeline** — #1228 Cluster C4 noted that `release-please.yml` uses cached `build-push-action` while creative-agent uses bare `docker build`. After #1234 PR 6 commit 2, the salesagent image build uses `build-push-action@v7.1.0` + `cache-from/to: type=gha` — yet the creative-agent step a few jobs over still doesn't | Two patterns coexist in the same workflow corpus; the cached one is right there to copy |

### What the tests actually need

Only **1 test file** (`tests/integration/test_creative_agent_live.py`, 19 tests) requires the live creative agent. It tests:
- Format discovery via MCP protocol (`list_creative_formats` tool)
- Format field validation (format_id, name, type)
- URL normalization (trailing slash handling)
- Cache behavior and format resolver integration

All other ~80+ creative-tagged tests use the harness (`CreativeFormatsEnv`, `CreativeSyncEnv`, `CreativeListEnv`) which mocks the creative agent. They only need PostgreSQL.

This means the build cost is paid for 19 tests, not the ~80+ creative-tagged total.

### Failure frequency

The npm `ECONNRESET` failure on PR #1188 was the first infrastructure-caused creative failure in the last 30 runs. **Latent fragility** — doesn't fail often, but when it does it's not actionable (just re-run). The cost shifted to: per-run wall-clock (every PR pays ~2 minutes), runner-minute quota burn, and PR-author confusion when the job fails for non-code reasons.

## Proposed solutions (priority order)

### 1. Add Docker layer caching (quick win)

Replace bare `docker build` with `docker/build-push-action` + GitHub Actions cache. **#1234 PR 6 commit 2 already adopts this pattern for the salesagent image; copy verbatim:**

```yaml
- uses: docker/setup-buildx-action@<SHA>  # v4.0.0 — same SHA as PR 6 uses

- uses: docker/build-push-action@<SHA>    # v7.1.0 — same SHA as PR 6 uses
  with:
    context: /tmp/adcp-server
    load: true
    tags: adcp-creative-agent
    cache-from: type=gha
    cache-to: type=gha,mode=max
```

**Impact:** After the first run, all layers cached (the pinned commit `ca70dd1e2a6c` never changes between runs). Subsequent builds take seconds instead of ~2 min. npm downloads only happen on cache miss.

**Effort:** ~15 lines of YAML. Same-day PR after #1234 PR 3 lands (the build step moves to `ci.yml` integration-tests).

**Trade-off:** GHA cache is 10 GB per repo. The adcp image is large (27 cloned repos). If cache evicts, falls back to full rebuild. Could use `type=registry` cache with ghcr.io for persistence (links to Solution 3).

**Coordination with #1234:**
- Land **after** PR 3 (Phase C deletion of `test.yml`) so the patch goes into `ci.yml` directly.
- SHA-pin per #1234 PR 1 commit 9's convention (`# frozen: <version>` comment) — reuse `.github/.action-shas.txt` resolved SHAs.

### 2. Add a retry loop around the Docker build (safety net)

```bash
for attempt in 1 2 3; do
  docker build -t adcp-creative-agent /tmp/adcp-server && break
  echo "Attempt $attempt failed, retrying in 10s..."
  sleep 10
done
```

**Impact:** Handles transient npm/network errors automatically. Worst case adds 4-6 min to a flaky run.

**Effort:** 3 lines of bash. Trivial.

**Trade-off:** Retries the full build from scratch. Wasteful but effective as a safety net alongside caching. With Solution 1 in place, the retry only re-runs cache-miss layers.

**Better option (combined with Solution 1):** `build-push-action`'s built-in retry via `step-security/retry`-style wrapping. Either approach acceptable.

### 3. Pre-build and publish the image to ghcr.io (medium term)

Build the adcp creative agent image once (manually or via a dispatch workflow) and push to `ghcr.io/prebid/salesagent/adcp-creative-agent:<pinned-sha>`. CI then does `docker pull` instead of `docker build`.

```yaml
# In CI:
- run: docker pull ghcr.io/prebid/salesagent/adcp-creative-agent:ca70dd1e

# Separate workflow (manual dispatch) to rebuild when pin changes:
- uses: docker/build-push-action@<SHA>
  with:
    push: true
    tags: ghcr.io/prebid/salesagent/adcp-creative-agent:ca70dd1e
```

**Impact:** Eliminates npm/build entirely from CI. `docker pull` is fast and reliable.

**Effort:** 1-2 PRs — one for the build+push workflow, one to update CI to pull.

**Trade-off:** Requires `packages: write` permission. Must rebuild when updating the pinned SHA (could be automated). Adds registry storage.

**Coordination with #1234:**
- The publish workflow should reuse #1234 PR 6's pattern (cosign keyless, harden-runner, `permissions: {}`-then-elevate). The creative-agent image is a build-time-only test fixture; cosign is overkill but harden-runner audit-mode is cheap insurance.
- Tag immutability (PR 6 commit 7 admin step) only applies to release tags, NOT this internal-test-fixture image — verify before publishing.

### 4. Request upstream publish Docker images (long term)

File an issue on `adcontextprotocol/adcp` requesting they publish container images to ghcr.io on releases. If accepted, switch from self-hosted (Solution 3) to upstream-published.

**Impact:** Best long-term solution — zero build overhead, maintained by upstream.

**Effort:** Upstream would add ~20 lines using `docker/build-push-action`.

**Trade-off:** Depends on upstream willingness and timeline. The current pin `ca70dd1e2a6c` is held because upstream HEAD has broken migrations — this signals the upstream's release discipline isn't yet at the maturity needed for "auto-pull latest stable" anyway. Solutions 1-3 are the practical path.

## Recommended approach

**Solutions 1 + 2 immediately**, after #1234 PR 3 merges. ~20 lines of YAML changes, eliminates most flakiness, cuts the creative shard's build time from ~2 min to ~5s on cache hits. Ship as a tiny follow-up to #1234.

Then pursue **Solution 3** (self-hosted pre-built image) if Solutions 1+2 don't fully eliminate the flakiness — making CI completely independent of npm registry availability.

**Solution 4** (upstream publishes) — file the upstream issue at any time as a tracking ticket; not blocking.

## Coordination with #1234

- **Wait for #1234 PR 3 to merge** before opening the fix PR. PR 3 Phase C deletes `test.yml` and moves the creative-agent build step into `ci.yml`'s `integration-tests` job. Patching `test.yml` would conflict.
- **Reuse #1234 PR 6 commit 2's BPA SHAs**. PR 6 establishes `docker/setup-buildx-action@<SHA>` (v4.0.0) and `docker/build-push-action@<SHA>` (v7.1.0) — pin to the same SHAs to keep dependabot updates consolidated.
- **Add to `_setup-env` composite or stand-alone step** — the integration-tests job already does `uses: ./.github/actions/setup-env`, which doesn't include buildx. Add buildx as a separate step in the integration-tests job (creative-agent-specific), not in the shared composite (other jobs don't need buildx).
- **Update the structural-guard expected list** — none of the existing #1234 guards (`test_architecture_required_ci_checks_frozen.py`, etc.) reference creative-agent build infra; this PR doesn't need a new guard.

## Acceptance criteria

A PR closing this issue must satisfy ALL of:

- [ ] `Integration Tests` job uses `docker/build-push-action@<SHA>` (NOT bare `docker build`) for the creative-agent step
- [ ] `cache-from: type=gha` + `cache-to: type=gha,mode=max` present on the build step
- [ ] Retry loop wraps the build step (3 attempts, 10s delay) — OR build-push-action's built-in retry semantics enabled
- [ ] On a cache-hit run, build-step wall-clock <30s (vs. ~2min today)
- [ ] On a cold run after cache eviction, build-step wall-clock <2min30s (slight increase from today's ~2min due to BPA overhead is acceptable)
- [ ] No `npm ECONNRESET` failures cause job termination (retry catches; cache-hit avoids npm entirely)
- [ ] SHAs match #1234 PR 1 commit 9's convention (`# frozen: v<tag>` comment)

## Verification

```bash
# 1. BPA replaces bare docker build
grep -A 6 'creative-agent' .github/workflows/ci.yml | grep -q 'docker/build-push-action'
! grep -E '^\s+- run:\s+docker build' .github/workflows/ci.yml

# 2. GHA cache
grep 'cache-from: type=gha' .github/workflows/ci.yml
grep 'cache-to: type=gha,mode=max' .github/workflows/ci.yml

# 3. Retry loop (or BPA retry)
grep -E 'for attempt in 1 2 3|retry-attempts' .github/workflows/ci.yml

# 4. SHA pin
grep -E 'docker/build-push-action@[a-f0-9]{40}' .github/workflows/ci.yml

# 5. Cache-hit timing (run twice; second run should be fast)
# Manual: open the PR, watch CI; first run ~2min, second run <30s
```

## Related

- **#1234** — CI and pre-commit refactor. Carries the build step forward unchanged (D32+D39); does NOT address fragility. PR 6 commit 2 establishes the `build-push-action@v7.1.0 + GHA cache` pattern for the salesagent image — copy that pattern here. Land THIS issue's fix PR after #1234 PR 3 merges.
- **#1228 Cluster C2** — explicitly tracks creative-agent caching under this issue. Closure of #1228 partly depends on #1189 closing (or being explicitly accepted as a non-blocker).
- **PR #1188** — E2E port allocation fix; first observed `ECONNRESET` flake in the creative job.
- **`test.yml:183` comment** — "upstream HEAD has broken migrations (community_points FK violation)" — the reason for the commit pin. Carried forward into `ci.yml` per #1234 D32; pre-flight A23 monitors pin freshness.
- **`adcontextprotocol/adcp`** — upstream repo. Solution 4 = file an upstream issue for ghcr.io image publishing.

## Sign-off

A successful resolution of this issue means:

1. The creative-agent build step uses Docker layer caching (cache-hit ~5s, cache-miss ~2min).
2. Transient npm `ECONNRESET` flakes do not kill the job (retry catches them).
3. PR-author confusion from "the build failed but my code is fine" is reduced to near-zero.
4. The pattern matches #1234's salesagent-image build (consistent tooling across the workflow corpus, closing #1228 Cluster C4).
5. Solution 3 (ghcr.io pre-build) remains available as a follow-up if Solutions 1+2 prove insufficient.

---

**Labels:** `ci` · `tech-debt` · `infrastructure` · `P3` (latent fragility; raise to P2 if a second `ECONNRESET` failure occurs in a 30-day window)
