"""BDD binding for the locally-added UC-006 post-commit effect-ordering feature.

The live-branch half of the escaping-effect seam. Its sibling
(test_uc006_dry_run_out_of_transaction_effects.py) grades that a preview fires
no effect its rollback cannot reach; this one grades that when the effect DOES
fire, it fires after the transaction that owns it has committed — the only
point at which the job's own session can read what the sync decided.

Retire together with the sibling once adcp-req grows a dry_run x approval_mode
partition and an ordering obligation to compile from.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/local-uc006-post-commit-effect-ordering.feature")
