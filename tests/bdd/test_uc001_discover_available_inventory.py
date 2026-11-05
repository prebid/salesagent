"""BDD binding for the generated UC-001 discovery feature, one scenario at a time.

The feature carries 121 scenarios and most have no step definitions yet, so this module
binds by NAME the ones that run, rather than collecting the whole file with
``scenarios()``: a collected scenario with a missing step reads as dormant coverage. Bind
the next scenario here when its steps exist.

The bound scenarios are BR-RULE-002 (advertising-policy check) and BR-RULE-005 (AI
ranking), both on the seller's own AI configuration. Steps:
``tests/bdd/steps/domain/uc001_discovery.py``. Spec grounding: neither rule is in AdCP
3.1.1, so no storyboard grades them; ``POLICY_VIOLATION`` is in the pinned 3.1 error-code
enum.
"""

from __future__ import annotations

from pytest_bdd import scenario

FEATURE = "features/BR-UC-001-discover-available-inventory.feature"


@scenario(FEATURE, "BR-RULE-002 INV-1 violated - policy enabled and brief BLOCKED")
def test_policy_blocks_the_brief() -> None:
    """The policy check runs on the seller's own AI configuration, wherever it is stored."""


@scenario(FEATURE, "BR-RULE-002 INV-3 holds - policy disabled or no usable seller AI, check skipped")
def test_policy_check_skipped() -> None:
    """Disabled, or no AI configuration of the seller's own that can run the check."""


@scenario(FEATURE, "BR-RULE-002 INV-4 holds - policy service unavailable, fail-open")
def test_policy_fails_open() -> None:
    """The seller's policy model fails, and the request proceeds."""


@scenario(FEATURE, "BR-RULE-002 INV-5 holds - policy evaluation returns ALLOWED")
def test_policy_allows_the_brief() -> None:
    """The seller's policy model allows the brief, and the request proceeds."""


@scenario(FEATURE, "BR-RULE-005 INV-1 violated - product below 0.1 threshold excluded")
def test_ranking_excludes_below_threshold() -> None:
    """Ranking runs on the seller's own AI configuration and drops a low score."""


@scenario(FEATURE, "BR-RULE-005 INV-2 holds - product at or above 0.1 threshold included")
def test_ranking_orders_by_score() -> None:
    """Ranking orders products by score, which differs from catalog order."""


@scenario(FEATURE, "BR-RULE-005 INV-4 holds - ranking cannot run, products returned unranked")
def test_ranking_falls_back_to_catalog_order() -> None:
    """A failing model or an unusable seller configuration returns catalog order."""
