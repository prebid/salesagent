"""BDD binding for the generated UC-001 discovery feature, one scenario at a time.

The feature carries 121 scenarios and most have no step definitions yet, so this module
binds by NAME the ones that run, rather than collecting the whole file with
``scenarios()``: a collected scenario with a missing step reads as dormant coverage. Bind
the next scenario here when its steps exist.

The bound scenarios are BR-RULE-002 (advertising-policy check, including the policy the admin
Policy page saves) and BR-RULE-005 (AI ranking), both on the seller's own AI configuration, the
no-saved-policy refusal, and the channels filter partitions.
Steps: ``tests/bdd/steps/domain/uc001_discovery.py``. Spec grounding: neither rule is in
AdCP 3.1.1, so no storyboard grades them; ``POLICY_VIOLATION`` is in the pinned 3.1
error-code enum. ``filters.channels`` is ``core/product-filters.json#/properties/channels``
at the pinned 3.2.7, graded upstream by the ``media_buy_seller/product_filter_behavior``
storyboard.
"""

from __future__ import annotations

from pytest_bdd import scenario

FEATURE = "features/BR-UC-001-discover-available-inventory.feature"


@scenario(FEATURE, "BR-RULE-002 INV-1 violated - policy enabled and brief BLOCKED")
def test_policy_blocks_the_brief() -> None:
    """The policy check runs on the seller's own AI configuration, wherever it is stored."""


@scenario(FEATURE, "BR-RULE-002 INV-3 holds - policy disabled, check skipped")
def test_policy_check_skipped() -> None:
    """Checks off: the brief is not checked, whatever the model would answer."""


@scenario(FEATURE, "BR-RULE-002 INV-3 violated - policy enabled with no usable seller AI, CONFIGURATION_ERROR")
def test_policy_check_without_seller_ai_refused() -> None:
    """Checks on with no AI configuration of the seller's own that can run them: setup is incomplete."""


@scenario(FEATURE, "BR-RULE-002 INV-4 holds - policy service unavailable, SERVICE_UNAVAILABLE")
def test_policy_model_unavailable_refused() -> None:
    """The seller's policy model fails, and the request is refused rather than served unchecked."""


@scenario(FEATURE, "Extension - the tenant has saved no advertising policy, <buying_mode> mode")
def test_no_saved_policy_serves_no_products() -> None:
    """No saved policy: CONFIGURATION_ERROR naming the advertising_policy task, in every buying mode."""


@scenario(FEATURE, "BR-RULE-002 INV-5 holds - policy evaluation returns ALLOWED")
def test_policy_allows_the_brief() -> None:
    """The seller's policy model allows the brief, and the request proceeds."""


@scenario(FEATURE, "BR-RULE-002 - the saved prohibitions reach the policy model")
def test_policy_prompt_names_the_saved_prohibitions() -> None:
    """Every list the seller saved reaches the policy model's prompt."""


@scenario(FEATURE, "BR-RULE-002 - get_products enforces the policy saved on the Policy page")
def test_policy_page_save_is_enforced() -> None:
    """The operator's save on the admin Policy page reaches the check get_products runs (#2697)."""


@scenario(FEATURE, "BR-RULE-002 - get_products skips the check when the Policy page saved it off")
def test_policy_page_save_turns_the_check_off() -> None:
    """Checks saved off on the Policy page skip the check, though the model would block the brief (#2697)."""


@scenario(FEATURE, "BR-RULE-005 INV-1 violated - product below 0.1 threshold excluded")
def test_ranking_excludes_below_threshold() -> None:
    """Ranking runs on the seller's own AI configuration and drops a low score."""


@scenario(FEATURE, "BR-RULE-005 INV-2 holds - product at or above 0.1 threshold included")
def test_ranking_orders_by_score() -> None:
    """Ranking orders products by score, which differs from catalog order."""


@scenario(FEATURE, "BR-RULE-005 INV-4 holds - ranking cannot run, products returned unranked")
def test_ranking_falls_back_to_catalog_order() -> None:
    """A failing model or an unusable seller configuration returns catalog order."""


@scenario(FEATURE, "channels partition validation - <partition>")
def test_channels_filter_partitions() -> None:
    """A product matches on the channels it stores; one storing none, on the tenant ad server's defaults."""
