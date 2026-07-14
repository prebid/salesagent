# Handoff — PR #1420 review follow-up (`ci/in-network-test-runner`)

**PR:** prebid/salesagent#1420 — "ci: in-network Docker test runner + recover e2e_rest BDD 5th transport"
**Branch:** `ci/in-network-test-runner` · **Head:** `d21a6c14b` · **Base:** `main` · **State:** OPEN, ready for review
**Author:** KonstantinMirin · **Reviewer:** ChrisHuie
**Branch worktree:** `/Users/konst/projects/salesagent-innet-runner` (the branch is checked out there; this handoff was authored from a detached checkout of `d21a6c14b` in `salesagent-mbvr`). **Do the actual fixes in the `salesagent-innet-runner` worktree** so they land on the branch.

## Review state (3 conversation comments, 0 inline, 0 formal reviews)
1. ChrisHuie review @ `c3d8e60c` (06-11) — 5 findings, no blockers.
2. KonstantinMirin response @ `d21a6c14b` (06-12) — addressed items 1–4.
3. **ChrisHuie re-review @ `d21a6c14b` (06-13) — NOT yet responded to.** Verdict: all 5 prior items + nits **Fixed/over-delivered**; **no blockers**; CI green (29 checks); `make quality` 4797 passed. Raises **2 new should-fix** + nits (below).

Net: PR is mergeable as-is per the reviewer. The items below are quality/correctness improvements + a reply to the reviewer.

---

## Should-fix #A — e2e_rest reports a server crash as a correct rejection
**Verified locations (head d21a6c14b):**
- `tests/harness/dispatchers.py:240-253` — `RestE2EDispatcher`: on a ≥400 *non-JSON* response it wraps the body as `AdCPError(f"HTTP {status}: {body}", details={status_code, raw_body})` — **no `error_code`**.
- `tests/bdd/steps/domain/uc004_delivery.py:2611-2614` — the `"invalid"` Then-branch asserts only `isinstance(error, (AdCPError, ValidationError))` (no mock, no code check). A genuine 5xx crash / nginx HTML error therefore satisfies "correctly rejected invalid input."
- Contrast: the narrower `error "<CODE>"` branch (`uc004_delivery.py:2624-2625`) **does** check `error.error_code`, so only the loose `"invalid"` branch on a non-JSON response is fooled.

**Why it matters:** these same `-invalid` scenarios are on the e2e_rest ledger today (server *accepts* invalid input → 200). When #1270 / ownership land and they graduate, a later 5xx regression would false-pass the loose step (and could fire a strict tripwire as a false xpass).

**Fix:** require a non-empty `error_code` in the `"invalid"` step (or return a distinguishable error type the step rejects). If the broader reclassification is deferred to the stacked harness PR, add an in-tree marker comment at `dispatchers.py:243` so the deferral is discoverable (nothing records it today).
**Verify:** a non-JSON 5xx in an `[e2e_rest]-…-invalid` scenario must NOT satisfy the step.

## Should-fix #B — `e2e_host()` introduced but bypassed at 7 sites (partial extraction)
**Helper:** `tests/e2e/conftest.py:25` (`def e2e_host() -> str: return os.getenv("ADCP_TEST_HOST", "localhost")`), adopted 6× in-module.
**7 inline copies the PR also touched (verified):**
1. `tests/ui/conftest.py:24`
2. `tests/harness/admin_accounts.py:149`
3. `tests/e2e/test_landing_pages.py:34`
4. `tests/e2e/test_a2a_regression_prevention.py:26`
5. `tests/e2e/test_a2a_endpoints_working.py:26`
6. `tests/e2e/test_admin_bdd_e2e.py:75`
7. `tests/e2e/test_a2a_adcp_compliance.py:262`

**Fix:** import `e2e_host` at the 7 sites (conftest helpers are already importable here — `find_free_port` is imported cross-module), or move host/URL helpers to the importable `tests/e2e/utils.py` (already in this PR). Watch the 4 f-string-embedded URL sites.
**Verify:** `grep -rn 'ADCP_TEST_HOST' tests/ src/` shows only `e2e_host()` + docstrings.

---

## Nits (informational / cheap)
- **`wire_error_envelope` unset on e2e_rest** — `dispatchers.py:254` returns `TransportResult(... error=...)` without `wire_error_envelope`, unlike in-process `RestDispatcher` (`:161`). The mandated error authority `assert_envelope_shape(result.wire_error_envelope, …)` is unavailable on this transport. Populate it from `response.json()` in the JSON-error branch.
- **`E2E_MCP` / `E2E_A2A` are `NotImplementedError` placeholders** (`dispatchers.py:264,273`, registered `:297-298`) — honest (they complete the `DISPATCHERS` dict that lacked them on `main`). Add a `set(DISPATCHERS) == set(Transport)` test, or defer to the PR wiring those transports.
- **Ledger count drift** — live ledger = **308** (`tests/bdd/e2e_rest_known_failures.txt`), but the by-mechanism breakdown still totals **312** (`docs/test-redesign/e2e-rest-ledger-retirement.md:89,91-97`; header comment `e2e_rest_known_failures.txt:13` says "312 total"). Subtract the 4 pruned entries from the buckets.
- **`scripts/test-in-network.sh` referenced but does not exist** — named in `docker-compose.e2e.yml:94,188`, `docker-compose.e2e.ports.yml:4`, `Dockerfile.test:11`, `scripts/test-stack.sh:83` (the orchestration is inline in `run_all_tests.sh`). Fix the 5 stale refs.
- **Webhook receiver DRY** — `WebhookReceiver.do_POST` (`tests/e2e/test_adcp_reference_implementation.py:39`) and `DeliveryWebhookReceiver.do_POST` (`tests/e2e/test_delivery_webhooks_e2e.py:39`) are near-byte-identical; the extraction folded the server bootstrap (`tests/e2e/_webhook_capture.py`) but not these handlers. A shared default capture handler would fold them.
- **Six-suite list duplicated in 4 places** with no guard: `run_all_tests.sh:61`, `run_all_tests_host.sh:48`, `tox.ini:6`, `tests/unit/test_run_all_tests_contract.py:21` (two differ in element order). Derive `ALL_SUITES` from `tox -l`, or assert the script list == tox `env_list`.
- **Local `salesagent-*` ids** in 9 docstrings/the retirement doc don't resolve for outside readers; the public `#1423` is already used in the PR body.
- **Comment accuracy** `conftest.py:2388-2393` — lead comment says "make EVERY e2e_rest xfail non-strict," contradicting the code below it that now preserves `strict=True`.
- **Test hygiene** — `test_bdd_e2e_enabled_xdist_guard.py:28,41` parametrizes `numprocesses` as strings `"auto"/"logical"` (xdist resolves to int before `pytest_configure`); surplus. `_webhook_capture.py:40,44` binds `0.0.0.0` (was `127.0.0.1`) — harmless loopback relaxation, worth a note.

---

## How to execute
1. Switch to the branch worktree: `cd /Users/konst/projects/salesagent-innet-runner` (branch already there, head `d21a6c14b`). Do NOT branch-checkout `ci/in-network-test-runner` in `salesagent-mbvr` — it's locked to that worktree.
2. Address #A and #B first (the should-fix), then sweep nits. Each is small + local.
3. Gate: `make quality` (was 4797 passed). For e2e_rest behavior, the in-network stack is needed; the new unit tests (`test_run_all_tests_contract.py`, `test_bdd_e2e_rest_xfail_policy.py`, `test_bdd_e2e_enabled_xdist_guard.py`) are the CI-level proofs — extend them for #A/#B (e.g., a `set(DISPATCHERS)==set(Transport)` test, an `error_code`-required assertion test).
4. Reply to ChrisHuie's 06-13 re-review on #1420 itemizing #A/#B/nit dispositions (mirror the prior response style).

## Verification trail (this handoff)
At `d21a6c14b` (detached in `salesagent-mbvr`, clean tree): read `RestE2EDispatcher` ≥400 path (`dispatchers.py:230-261`) + `DISPATCHERS` map + the two E2E placeholders; read the `"invalid"` vs `error "<CODE>"` Then-branches (`uc004_delivery.py:2600-2630`); confirmed `e2e_host()` def + enumerated the 7 inline `ADCP_TEST_HOST` sites; confirmed live ledger = 308 vs breakdown 312; confirmed `scripts/test-in-network.sh` absent. Did NOT run the in-network Docker stack.
