"""BDD binding for the locally-added UC-006 dry_run preview-parity feature.

Grades the content of a preview against the live run it claims to predict: the same
payload dispatched twice against one tenant, dry_run first, and the live creatives[]
is the oracle for the previewed one. BR-UC-006-sync-creatives.feature has no dry_run
scenario and the other local dry_run feature grades out-of-transaction effects, not
what the preview says. Retire this file together with the local feature once adcp-req
grows a dry_run partition.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/local-uc006-dry-run-preview-parity.feature")
