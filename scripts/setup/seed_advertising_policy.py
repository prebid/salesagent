"""The advertising policy a seeded tenant saves.

A tenant with no saved policy serves no products: ``get_products`` answers
``CONFIGURATION_ERROR`` with ``missing_tasks: ["advertising_policy"]`` until the operator
saves one (#2698). The tenants these scripts seed are operators' stand-ins, so they save
one explicitly, the way an operator would on the Policy page. One function, because the
CI tenants and the storyboard tenant must not come to disagree about it.
"""

from __future__ import annotations

from src.core.schemas.advertising_policy import AdvertisingPolicy


def unchecked_advertising_policy() -> AdvertisingPolicy:
    """The smallest policy an operator can save: accept every brief without checking it.

    Checks off, because checks on also need an AI configuration of the seller's own, which
    no seeded tenant has.
    """
    return AdvertisingPolicy(
        enabled=False,
        require_manual_review=False,
        prohibited_advertisers=[],
        prohibited_categories=[],
        prohibited_tactics=[],
        default_prohibited_categories=[],
        default_prohibited_tactics=[],
        description=None,
    )
