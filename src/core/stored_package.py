"""What an adapter's ``update_media_buy`` reads of a stored package, handed over by the core.

A leaf both sides import: ``MediaBuyRepository.get_stored_packages`` builds the carrier from
the rows it owns, and the adapters read it. The adapter never reads a row itself: inside the
core's open unit of work, the first ``get_db_session()`` closes that unit's session (#1644,
#2647).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from src.core.errors.details import EntityRefDetails


@dataclass(frozen=True)
class StoredPackage:
    """One stored package of the buy, exactly as the adapters read it.

    An adapter that needs another stored value adds it here and to
    ``stored_package_of`` in the media-buy repository, the one reader of the row.
    """

    package_id: str
    #: The GAM line item booked for the package (``stored_line_item_id``).
    platform_line_item_id: str | None
    #: The pricing model the package was bought on, lower-cased (``pricing_info``).
    pricing_model: str
    #: The currency the package was bought in (``pricing_info``).
    currency: str
    #: The pause state the seller recorded.
    paused: bool
    product_id: str | None
    #: The budget the seller recorded: the ``budget`` column.
    budget: float | None
    #: The Broadstreet advertisements trafficked for the package.
    broadstreet_advertisement_ids: tuple[str, ...] = field(default=())


def stored_package(packages: Sequence[StoredPackage], media_buy_id: str, package_id: str) -> StoredPackage:
    """The package *package_id* among *packages*, or ``AdCPPackageNotFoundError`` when the buy holds none."""
    from src.core.exceptions import AdCPPackageNotFoundError

    for package in packages:
        if package.package_id == package_id:
            return package
    raise AdCPPackageNotFoundError(details=EntityRefDetails(package_id=package_id, media_buy_id=media_buy_id))
