# Account Management — Risk Mitigations & Known Gaps

Preparation document for the account management implementation.
Created during planning to ensure nothing is forgotten during execution.

## Risk 1: Gherkin spec assumes capabilities we don't have

### brand.json resolution (BR-RULE-058)
**Decision**: Echo brand as-is from the request. We do NOT resolve `/.well-known/brand.json`.
The steps "echoes brand domain" just verify the response includes what was sent — this works
without brand.json resolution. If full resolution is needed later, it's additive.

### governance_agents
**Decision**: Store as JSON, pass through. No governance logic implemented.
Not referenced in any step definitions — safe to ignore.

### sandbox mode
**Decision**: Implement the flag (it's just a boolean on Account + natural key). The 3 sandbox
scenarios in UC-011 are simple filter/provisioning tests. Low effort, high coverage value.

### seller internal failure
**Decision**: Simulate via mock in the harness (mock the DB to raise). The step "the seller
system is experiencing an internal failure" → mock repo to raise `OperationalError`.

## Risk 2: resolve_account is cross-cutting — no BDD coverage in UC-011

**What UC-011 covers**: Managing accounts (list, sync, create, update).
**What UC-011 does NOT cover**: Using accounts in other operations (create_media_buy, sync_creatives).

### Cross-cutting tests exist in upstream adcp-req:
- **UC-002 (create_media_buy)**: 5 scenarios with account resolution (account not found,
  ambiguous natural key, account field absent, sandbox account, production account)
- **UC-006 (sync_creatives)**: Account partition tests (explicit_account_id, natural_key)
- **UC-001 (discover_inventory)**: Sandbox account filtering

### Mitigation:
After UC-011 slices are green, copy the account-related scenarios from UC-002 and UC-006
feature files into our BDD suite. These test `resolve_account()` in the context of each
tool. File as a separate beads task: "BDD: Account resolution in create_media_buy (from UC-002)"
and "BDD: Account resolution in sync_creatives (from UC-006)".

**DO NOT call the feature complete until these cross-cutting tests pass.**

## Risk 3: Step definition ambiguity

### "the account was actually created on the seller"
**Meaning**: DB row exists with status != "failed". Verify via `AccountRepository.get_by_natural_key()`.
NOT: adapter was called (adapters are not involved in account management).

### "no accounts were actually created or modified on the seller"
**Meaning**: DB state unchanged. For dry_run: verify DB before and after are identical.
For error: verify no new rows or status changes.

### "the accounts are only those accessible to the authenticated agent"
**Meaning**: response.accounts contains only accounts linked via AgentAccountAccess
to the authenticated principal_id. Verify by creating accounts for another agent
and confirming they don't appear.

## Risk 4: Admin UI has no BDD coverage

**Status**: Admin UI is tested via integration tests (Flask TestClient), not BDD scenarios.
This is consistent with how the existing admin UI is tested (see `tests/integration/test_admin_*.py`).

**Mitigation**: Add integration tests for each admin route in `test_account_admin_integration.py`.
These are part of the architecture task `salesagent-7kn` (Admin UI), not the BDD slices.

**Checklist** (verify manually before calling feature complete):
- [ ] Admin: List accounts page loads
- [ ] Admin: Approve pending account → status changes to active
- [ ] Admin: Reject pending account → status changes to rejected
- [ ] Admin: Edit platform_mappings → GAM advertiser ID saved
- [ ] Admin: Account detail shows correct data

## Risk 5: Context window limits during execution

**Mitigation**: Use `mol-execute` with `task-single` formula for each slice.
Each slice is 4-15 scenarios — manageable in one agent context window.
The molecular workflow (atoms) provides crash recovery if context compacts.

Run slices sequentially (not parallel) since they share files.

## Execution Checklist

Before calling the feature COMPLETE, verify:

1. [ ] All 7 BDD slices green (55 scenarios × 4 transports = 220 test runs)
2. [ ] Cross-cutting account resolution tests from UC-002 (5 scenarios)
3. [ ] Cross-cutting account resolution tests from UC-006 (3 scenarios)
4. [ ] Admin UI integration tests pass
5. [ ] `account` field is REQUIRED again on CreateMediaBuyRequest (override reverted)
6. [ ] `account` field is REQUIRED again on SyncCreativesRequest (override reverted)
7. [ ] `./run_all_tests.sh` passes (all suites, including existing tests)
8. [ ] Alembic migrations run cleanly (up and down)
9. [ ] Docker build succeeds
10. [ ] Manual smoke test: sync_accounts → list_accounts → create_media_buy with account
