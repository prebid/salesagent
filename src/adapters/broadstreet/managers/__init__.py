"""Broadstreet adapter managers.

Managers handle specific operations for the Broadstreet adapter,
following the modular pattern from GAM.
"""

from .advertisements import AdvertisementInfo, BroadstreetAdvertisementManager
from .campaigns import BroadstreetCampaignManager
from .inventory import BroadstreetInventoryManager, ZoneInfo
from .placements import BroadstreetPlacementManager, PlacementInfo
from .workflow import BroadstreetWorkflowManager

__all__ = [
    "AdvertisementInfo",
    "BroadstreetAdvertisementManager",
    "BroadstreetCampaignManager",
    "BroadstreetInventoryManager",
    "BroadstreetPlacementManager",
    "BroadstreetWorkflowManager",
    "PlacementInfo",
    "ZoneInfo",
]
