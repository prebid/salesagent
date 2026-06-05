"""Faithful intersection of products with a buyer's property_list.

Replaces the legacy per-product filter in ``src/core/tools/products.py`` that
silently dropped products whose only ``publisher_properties`` selector was
``by_tag``. The faithful version resolves all three AdCP selector variants to
the concrete *identifier values* (e.g. domains) the covered properties expose,
then intersects those against the buyer's resolved property_list values:

- ``selection_type="all"``: the product covers every property under
  ``publisher_domain`` → product is unbounded; always include.
- ``selection_type="by_id"``: ``property_ids`` are AuthorizedProperty IDs
  (slugs matching ``^[a-z0-9_]+$``), NOT identifier values — they are resolved
  via ``AuthorizedPropertyRepository.list_by_ids`` to the rows' identifier
  values.
- ``selection_type="by_tag"``: tags are resolved via
  ``AuthorizedPropertyRepository.list_by_tags`` to the matching rows, then to
  their identifier values.

Both sides are compared in the identifier-value namespace (e.g. ``espn.com``),
normalized for parity (lowercase, ``www.``/``m.``/``mobile.`` stripped). This is
the crux of the resolution: the buyer's property_list resolves to identifier
values, while an AdCP ``PropertyId`` is a slug that only maps to a domain
through its AuthorizedProperty row — the two never overlap without it.

Strict mode preserves the existing semantic: when
``product.property_targeting_allowed == False``, the buyer must accept the
entire product (every covered identifier value must be in the allowed set);
otherwise any non-empty intersection is enough.

Returns ``IntersectionResult`` carrying both the kept products and the
``DroppedProduct`` reasons so callers (e.g. ``_create_media_buy_impl``) can
log advisories on zero-match per the inventory-targeting plan's SD2
(accept-with-context, not reject).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from src.core.database.repositories.authorized_property import AuthorizedPropertyRepository
from src.services.property_discovery_service import _normalize_domain


class DropReason(StrEnum):
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
        authorized_property_repo: Tenant-scoped repo used to resolve ``by_id``
            and ``by_tag`` selectors to AuthorizedProperty rows (and thence to
            their identifier values).
    """

    def __init__(self, authorized_property_repo: AuthorizedPropertyRepository) -> None:
        self._repo = authorized_property_repo

    def filter_products(self, products: list[Any], allowed_properties: set[str]) -> IntersectionResult:
        """Apply the intersection across a product list.

        ``allowed_properties`` are the buyer's resolved property_list identifier
        values; they are normalized here so comparison with the products'
        (also-normalized) covered identifier values is apples-to-apples.
        """
        allowed_normalized = {_normalize_domain(value) for value in allowed_properties if value}
        kept: list[Any] = []
        dropped: list[DroppedProduct] = []
        for product in products:
            outcome = self._evaluate(product, allowed_normalized)
            if outcome is None:
                kept.append(product)
            else:
                dropped.append(DroppedProduct(product=product, reason=outcome))
        return IntersectionResult(kept_products=kept, dropped_products=dropped)

    def _evaluate(self, product: Any, allowed_properties: set[str]) -> DropReason | None:
        """Return ``None`` to keep, or the drop reason to exclude.

        ``allowed_properties`` is already normalized by ``filter_products``.
        """
        covered = self._resolve_covered_identifier_values(product)
        if covered is None:
            # selection_type='all' on at least one selector — product is unbounded.
            return None
        if not covered:
            return DropReason.NO_RESOLVABLE_PROPERTIES
        if not covered & allowed_properties:
            return DropReason.NO_PROPERTY_OVERLAP
        if not getattr(product, "property_targeting_allowed", False):
            # Strict: every covered identifier value must be in the buyer's list.
            if not covered.issubset(allowed_properties):
                return DropReason.STRICT_MODE_VIOLATION
        return None

    def _resolve_covered_identifier_values(self, product: Any) -> set[str] | None:
        """Resolve a product's publisher_properties to the covered identifier values.

        Returns ``None`` when at least one selector is ``selection_type='all'``
        (the product is unbounded). Returns an empty set when the product has no
        selectors, or only selectors that resolve to no AuthorizedProperty rows
        (unknown ``by_id`` IDs, or ``by_tag`` tags that match nothing).

        ``by_id`` ``property_ids`` are AuthorizedProperty IDs (slugs) and
        ``by_tag`` resolves tags to rows; both are mapped to their rows'
        normalized identifier values — the namespace the buyer's property_list
        is expressed in.
        """
        selectors = getattr(product, "publisher_properties", None) or []
        if not selectors:
            return set()

        by_id_ids: set[str] = set()
        rows: list[Any] = []
        for selector in selectors:
            inner = getattr(selector, "root", selector)
            selection_type = getattr(inner, "selection_type", None)
            if selection_type == "all":
                # Any "all" selector makes the whole product unbounded.
                return None
            if selection_type == "by_id":
                # ``PropertyId`` is a ``RootModel[str]`` carrying an
                # AuthorizedProperty ID (slug); resolved to identifier values below.
                by_id_ids.update(pid.root for pid in inner.property_ids)
            elif selection_type == "by_tag":
                # ``PropertyTag`` is a ``RootModel[str]`` — direct ``.root`` access.
                tags = [tag.root for tag in inner.property_tags]
                rows.extend(self._repo.list_by_tags(inner.publisher_domain, tags))

        if by_id_ids:
            rows.extend(self._repo.list_by_ids(sorted(by_id_ids)))

        values: set[str] = set()
        for row in rows:
            values.update(self._identifier_values(row))
        return values

    @staticmethod
    def _identifier_values(row: Any) -> set[str]:
        """The normalized identifier values an AuthorizedProperty row exposes."""
        values: set[str] = set()
        for ident in getattr(row, "identifiers", None) or []:
            value = ident.get("value") if isinstance(ident, dict) else getattr(ident, "value", None)
            if value:
                values.add(_normalize_domain(value))
        return values
