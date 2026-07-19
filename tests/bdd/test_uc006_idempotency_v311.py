"""BDD binding for the hand-authored AdCP 3.1.1 UC-006 key matrix.

The companion feature is intentionally outside the generated adcp-req file so
``scripts/compile_bdd.py --merge`` cannot restore the derivative repository's
stale optional/8-character examples over the pinned protocol contract.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/local-uc006-idempotency-v311.feature")
