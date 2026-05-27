# BDD strict-marker debt — proposed GH issue structure

**Status: PROPOSED, awaiting owner approval. Do NOT execute without explicit go.**

## Context

After the 2026-05-08 cleanup (commit `c4446267`), `tests/bdd/conftest.py`
has zero blanket non-strict markers on scenarios where production has
caught up. Remaining non-strict and selective-strict markers all point
to one of 19 items catalogued in `docs/test-debt-bdd-strict-markers.md`.

Each item represents either a production gap (10) or a test rewrite (7)
or a test-helper improvement (2). Severity: 3× P1 (security/correctness),
7× P2 (real bugs/feature gaps), 9× P3 (cosmetic/refinement).

The doc is the canonical reference. FIXMEs in `conftest.py` reference it
by item ID (`C1`–`C11`, `B1`–`B7`, `H1`–`H2`).

## Recommendation

**File 4 GH issues, not 19.** Keep `docs/test-debt-bdd-strict-markers.md`
as the source of truth; use GH issues as work trackers for the items that
need cross-team visibility.

### Issue 1 — Umbrella (P2)

- **Title:** `Test debt: BDD strict-marker inventory tracking (16 items)`
- **Labels:** `test-debt`, `bdd`, `tracking`
- **Body template:**

  ```markdown
  Tracking the non-security items from
  `docs/test-debt-bdd-strict-markers.md`. The doc is the single source
  of truth; please update the doc when items resolve, then check the
  corresponding box here.

  Production gaps (P2–P3):
  - [ ] C4 — Pydantic ValidationError → AdCPError(INVALID_REQUEST, suggestion) translation
  - [ ] C5 — include_package_daily_breakdown no-op
  - [ ] C6 — date-range validation in success envelope vs raised
  - [ ] C7 — end-only date_range defaults to today-30d, not creation date
  - [ ] C8 — MCP wrapper missing output_format_ids / input_format_ids
  - [ ] C10 — description-only spec constraints (Duration campaign-unit, Geo metro/postal system)
  - [ ] C11 — start_date / end_date not echoed in response.reporting_period

  Test rewrites (P2–P3):
  - [ ] B1 — Gherkin uses pending_activation (not in MediaBuyStatus enum)
  - [ ] B2 — date_range partition/boundary _dispatch_partition wiring
  - [ ] B3 — resolution/ownership symbolic names + identity-swap rewrite
  - [ ] B4 — sampling_method scenarios on wrong feature (move to UC-024)
  - [ ] B5 — webhook_credentials mis-routed (rewire or delete)
  - [ ] B6 — disclosure_positions filter + all_positions step enum fix
  - [ ] B7 — UC-006 account_resolution step-layer fakes AdCPValidationError

  Test-helper improvements (P3):
  - [ ] H1 — _assert_partition_outcome invalid branch should isinstance-guard
  - [ ] H2 — _assert_partition_outcome valid branch should route by domain

  When resolving an item: remove its entry from
  `docs/test-debt-bdd-strict-markers.md`, remove the FIXME from
  `tests/bdd/conftest.py`, flip the marker to `strict=True` (or remove
  if no rows remain xfail), and check this box.
  ```

### Issues 2–4 — Three P1 security/correctness items (separate)

These get their own issues so they surface in security boards and
warrant their own threat-model write-ups.

#### Issue 2 — `bug(a2a): silently discards account parameter on get_media_buy_delivery (security)`

- **Labels:** `security`, `a2a`, `P1`
- **Body:**

  ```markdown
  ## Summary
  `src/a2a_server/adcp_a2a_server.py:1937-1980` — the A2A skill handler
  for `get_media_buy_delivery` does not forward the `account` parameter
  to `_raw()` and does not call `enrich_identity_with_account`.

  ## Impact
  Security gap. A buyer with a token scoped to one tenant could request
  delivery against another account, and the account scope is silently
  ignored. The REST transport correctly resolves account at
  `src/routes/api_v1.py:265-289`; A2A and IMPL skip the step.

  ## Reproduction
  BDD scenarios `T-UC-004-partition-account` and
  `T-UC-004-boundary-account`, error-code rows on the `[a2a]` transport,
  currently xpass on REST/MCP and xfail on IMPL/A2A.

  ## Threat model
  - Is the A2A endpoint exposed to untrusted callers in production?
    (yes — that's the protocol's purpose)
  - Does a tenant's token authorize them to query any account, or only
    accounts they own? (the latter — but the parameter that scopes that
    is being dropped)
  - Has this been exploited? (no known abuse; logs may not even surface
    the discard since the parameter is silently ignored at deserialize time)

  ## Fix
  Forward `account` to `_raw()` in `_handle_get_media_buy_delivery_skill`;
  call `enrich_identity_with_account` on the A2A path. Paired with C2
  (move the resolution into `_impl` so all transports share the step).

  Tracked: `docs/test-debt-bdd-strict-markers.md#c1`
  ```

#### Issue 3 — `bug(impl): _get_media_buy_delivery_impl does not resolve AccountReference`

- **Labels:** `correctness`, `core`, `P1`
- **Body:**

  ```markdown
  ## Summary
  `src/core/tools/media_buy_delivery.py` — IMPL accepts the `account`
  kwarg but never resolves it against the DB. Account resolution only
  happens at the REST wrapper boundary
  (`src/routes/api_v1.py:265-289`); A2A and MCP and direct-IMPL calls
  skip it.

  ## Impact
  Cross-transport contract is broken. Same buyer with same token gets
  different behavior depending on which transport they use.
  `account_not_found` rows pass through IMPL silently.

  ## Fix
  Move `enrich_identity_with_account` from the REST wrapper into
  `_impl` (or into a shared helper that every transport calls before
  `_impl`). Paired with the A2A fix (Issue 2).

  Tracked: `docs/test-debt-bdd-strict-markers.md#c2`
  ```

#### Issue 4 — `bug(repo): cross-principal media_buy access returns 200+empty instead of 403 (security)`

- **Labels:** `security`, `core`, `P1`
- **Body:**

  ```markdown
  ## Summary
  `src/core/database/repositories/media_buy.py:99-107`,
  `MediaBuyRepository.get_by_principal`, filters silently by
  `principal_id`. A request from a principal who does not own any of
  the requested media_buys receives an empty deliveries list, not an
  authorization error.

  ## Impact
  Security gap. A 200 OK with empty body is indistinguishable from
  "everything ran, nothing matched." A 403 (or 404 with suggestion)
  would correctly indicate the access was denied. The BDD ownership
  scenarios were the only place this could be observed and they do
  not actually swap identity (see B3 — separate test bug), so the gap
  is invisible to the suite today.

  ## Threat model
  - Allowed to enumerate other principals' media_buy_ids? (no — but
    a guess-and-check attacker could)
  - Caching layer might cache the empty response — would that leak?
    (probably not, but worth checking)

  ## Fix
  Raise `AdCPAuthorizationError` (or `AdCPNotFoundError` with
  suggestion text "media_buy not found or not owned by you") when the
  requesting principal does not own any of the requested media_buys.

  Tracked: `docs/test-debt-bdd-strict-markers.md#c3`
  ```

## FIXME wiring plan (after issues land)

For each FIXME comment in `tests/bdd/conftest.py` (currently pointing
at `docs/test-debt-bdd-strict-markers.md`), append the GH issue number
once the issues are filed:

- C1, C2 → reference Issue 2, Issue 3
- C3 → reference Issue 4
- C4, C5, C6, C7, C8, C10, C11 → reference Issue 1 (umbrella checkbox)
- B1, B2, B3, B4, B5, B6, B7 → reference Issue 1
- H1, H2 → reference Issue 1 (or leave doc-only)

Format (per `feedback_no_beads_in_code` memory):

```python
# FIXME(gh-#nnnn, see docs/test-debt-bdd-strict-markers.md#c4)
```

Beads IDs are never used in code per project convention.

## Verification after issues land

1. Open each issue body; spot-check at least one link to the doc
   resolves correctly.
2. `grep -n "FIXME" tests/bdd/conftest.py` — every FIXME has both an
   issue number and a doc-anchor reference.
3. `make quality` still passes.
4. No commit needs to add or remove a test marker; only the FIXME
   comment text changes.

## Open questions for the owner

1. **Single umbrella or split prod-gaps from test-rewrites into two
   umbrellas?** Umbrella stays manageable at 16 items; splitting adds
   another tracker without clear benefit.
2. **For the 3 P1 security issues, should those also reference the
   umbrella, or stand fully alone?** Recommendation: stand alone; the
   umbrella covers the non-security work.
3. **Labels:** project has its own label taxonomy
   (`bug`/`feature`/`task`/`tracking` per release-please convention).
   Verify before filing — `gh label list` will confirm.
4. **Assignee:** unset (let the team triage).
5. **Milestone:** umbrella → next adcp library upgrade milestone?
   Security issues → whatever the next hot-fix milestone is?

## Risks

- Filing 4 issues makes it look like a bigger initiative than it is.
  Mitigation: the umbrella body explicitly says "test debt tracking,"
  signalling this is a passive list, not active work.
- The 3 security issues might attract security review attention. That's
  desirable — they're real gaps and should be triaged independently.
- If the team prefers all-in-one tracking, drop to 1 umbrella with the
  security items as P1 sub-checkboxes labelled `security`. Less precise
  but simpler.
