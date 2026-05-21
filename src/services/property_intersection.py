"""Faithful intersection of products with a buyer's property_list.

Replaces the legacy shortcut in ``src/core/tools/products.py``
(``filter_products_by_property_list``) that silently dropped products whose
only ``publisher_properties`` selector was ``by_tag``. The faithful version
resolves all three AdCP selector variants against the buyer's allowed set:

- ``selection_type="all"``: the product covers every property under
  ``publisher_domain`` → product is unbounded; always include when buyer
  has any property in their list.
- ``selection_type="by_id"``: the product covers an explicit ``property_ids``
  list → intersect with allowed set directly.
- ``selection_type="by_tag"``: the product covers properties tagged with
  any of ``property_tags`` → resolve via ``AuthorizedPropertyRepository``
  to get concrete property IDs, then intersect.

Strict mode preserves the existing semantic: when
``product.property_targeting_allowed == False``, the buyer must accept the
entire product (i.e. every covered property must be in the allowed set);
otherwise any non-empty intersection is enough.

Returns ``IntersectionResult`` carrying both the kept products and the
``DroppedProduct`` reasons so callers (e.g. ``_create_media_buy_impl``) can
log advisories on zero-match per the inventory-targeting plan's SD2
(accept-with-context, not reject).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from src.core.database.repositories.authorized_property import AuthorizedPropertyRepository


class DropReason(str, Enum):
    """Why a product was excluded from the intersection."""

    NO_RESOLVABLE_PROPERTIES = "no_resolvable_properties"
    NO_PROPERTY_OVERLAP = "no_property_overlap"
    STRICT_MODE_VIOLATION = "strict_mode_violation"


@dataclass(frozen=True)
class DroppedProduct:
    """A product the intersection excluded, with a structured reason."""

    product: Any
    reason: DropReason


@dataclass(frozen=True)
class IntersectionResult:
    """Outcome of intersecting products with a buyer's allowed-property set.

    Attributes:
        kept_products: Products that pass the intersection.
        dropped_products: Products excluded, paired with the reason. Callers
            can log these as advisories (per SD2 — zero-match is not a
            rejection condition) or surface them in operator UI.
    """

    kept_products: list[Any] = field(default_factory=list)
    dropped_products: list[DroppedProduct] = field(default_factory=list)

    @property
    def zero_match(self) -> bool:
        """True when no products survived the intersection — useful for SD2 advisory logging."""
        return len(self.kept_products) == 0


class PropertyIntersection:
    """Filter products against a buyer's property_list with faithful resolution.

    Args:
        authorized_property_repo: Tenant-scoped repo for ``by_tag`` resolution.
    """

    def __init__(self, authorized_property_repo: AuthorizedPropertyRepository) -> None:
        self._repo = authorized_property_repo

    def filter_products(self, products: list[Any], allowed_properties: set[str]) -> IntersectionResult:
        """Apply the intersection across a product list."""
        kept: list[Any] = []
        dropped: list[DroppedProduct] = []
        for product in products:
            outcome = self._evaluate(product, allowed_properties)
            if outcome is None:
                kept.append(product)
            else:
                dropped.append(DroppedProduct(product=product, reason=outcome))
        return IntersectionResult(kept_products=kept, dropped_products=dropped)

    def _evaluate(self, product: Any, allowed_properties: set[str]) -> DropReason | None:
        """Return ``None`` to keep, or the drop reason to exclude."""
        covered = self._resolve_covered_property_ids(product)
        if covered is None:
            # selection_type='all' on at least one selector — product is unbounded.
            return None
        if not covered:
            return DropReason.NO_RESOLVABLE_PROPERTIES
        if not covered & allowed_properties:
            return DropReason.NO_PROPERTY_OVERLAP
        if not getattr(product, "property_targeting_allowed", False):
            # Strict: every covered property must be in the buyer's list.
            if not covered.issubset(allowed_properties):
                return DropReason.STRICT_MODE_VIOLATION
        return None

    def _resolve_covered_property_ids(self, product: Any) -> set[str] | None:
        """Resolve a product's publisher_properties to the set of property IDs covered.

        Returns ``None`` when at least one selector is ``selection_type='all'``
        (the product is unbounded). Returns an empty set when the product
        has no selectors or only unresolvable ones (e.g. ``by_tag`` selectors
        whose tags don't match any authorized property).
        """
        selectors = getattr(product, "publisher_properties", None) or []
        if not selectors:
            return set()

        property_ids: set[str] = set()
        for selector in selectors:
            inner = getattr(selector, "root", selector)
            selection_type = getattr(inner, "selection_type", None)
            if selection_type == "all":
                # Any "all" selector makes the whole product unbounded.
                return None
            if selection_type == "by_id":
                # ``PropertyId`` is a ``RootModel[str]`` — direct ``.root`` access.
                for pid in inner.property_ids:
                    property_ids.add(pid.root)
            elif selection_type == "by_tag":
                # ``PropertyTag`` is a ``RootModel[str]`` — direct ``.root`` access.
                tags = [tag.root for tag in inner.property_tags]
                resolved = self._repo.list_by_tags(inner.publisher_domain, tags)
                for prop in resolved:
                    property_ids.add(prop.property_id)
        return property_ids
