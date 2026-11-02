"""BDD binding for the locally-added UC-006 dry_run out-of-transaction-effect feature.

Grades the one thing a rollback-based preview cannot grade by construction: an
effect that leaves the preview's transaction. On an ai-powered tenant the sync
path hands a job to a background executor which opens its OWN AdminCreativeUoW,
commits a review verdict, and then sends Slack and the push webhook — so
"the preview rolls back" is not an answer for it.

BR-UC-006-sync-creatives.feature has no dry_run scenario at all and all of its
ai-powered rows are live-branch, so nothing upstream covers this intersection.
Retire this file together with the local feature once adcp-req grows a
dry_run x approval_mode partition.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/local-uc006-dry-run-out-of-transaction-effects.feature")
