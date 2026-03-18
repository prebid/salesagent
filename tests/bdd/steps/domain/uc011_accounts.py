"""Domain step definitions for UC-011: Manage Accounts.

Given steps: set up accounts, agent access, seller config
When steps: send list_accounts / sync_accounts requests
Then steps: verify account results, actions, status, errors

All steps operate on ctx dict (shared across Given/When/Then).
ctx["env"] is the harness environment (AccountSyncEnv or AccountListEnv).
ctx["response"] is the response object after When.
ctx["error"] is any exception raised.
"""

from __future__ import annotations

# TODO: Implement step definitions once production code exists.
# This file is a placeholder for the UC-011 account management BDD steps.
# Steps will be added incrementally as the account feature is built.
#
# Step categories needed:
#   Given: agent accounts setup, seller config (billing, approval, sandbox)
#   When: list_accounts requests, sync_accounts requests
#   Then: account verification (action, status, billing, setup, pagination, errors)
