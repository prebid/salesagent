"""BDD binding for the locally-added feature on what the ad server receives per creative kind.

Grades the typed creative the seller hands its ad server on the synchronous create, the
seller's approval of a held buy and the retroactive push, for each creative kind (#2547,
#2390, #2545). Graded on a2a, mcp, rest and e2e_rest. Its steps live in
``tests/bdd/steps/domain/uc002_creative_kind.py``, registered in ``tests/bdd/conftest.py``.
"""

from __future__ import annotations

from pytest_bdd import scenarios

scenarios("features/local-uc002-creative-kind-reaches-adapter.feature")
