from abc import ABC, abstractmethod

from src.core.schemas import Creative, CreativeApprovalStatus


class CreativeEngineAdapter(ABC):
    """Abstract base class for creative engine adapters."""

    @abstractmethod
    def process_creatives(self, creatives: list[Creative]) -> list[CreativeApprovalStatus]:
        """Processes creative assets, returning their status."""
        pass
