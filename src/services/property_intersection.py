"""Faithful intersection of products with a buyer's property_list.

Replaces the legacy per-product filter in ``src/core/tools/products.py`` that
silently dropped products whose only ``publisher_properties`` selector was
``by_tag``. The faithful version resolves all three AdCP selector variants to
the concrete AuthorizedProperty rows the product covers, then matches each
covered property against the buyer's resolved property_list identifiers:

- ``selection_type="all"``: the product covers every property under
  ``publisher_domain`` → product is unbounded; always include.
- ``selection_type="by_id"``: ``property_ids`` are AuthorizedProperty IDs
  (slugs matching ``^[a-z0-9_]+$``) scoped to the selector's
  ``publisher_domain`` — the spec makes ``publisher_domain`` required on the
  by_id variant precisely because slugs are only publisher-unique. Resolved via
  ``AuthorizedPropertyRepository.list_by_ids(publisher_domain, ids)``.
- ``selection_type="by_tag"``: tags are resolved via
  ``AuthorizedPropertyRepository.list_by_tags`` to the matching rows.

Matching is per covered PROPERTY, type-aware, and honors the spec
``Identifier.value`` grammar via the SDK matchers (see
``src/services/identifier_matching.py``): the buyer's identifiers are the
pattern side, so a buyer's ``*.espn.com`` selects a property identified by
``sports.espn.com``, and ``ios_bundle:com.foo`` never collides with
``domain:com.foo``.

Strict mode preserves the existing semantic: when
``product.property_targeting_allowed == False``, the buyer must accept the
entire product — EVERY covered property must match the buyer's list; otherwise
any covered property matching is enough.

Returns ``IntersectionResult`` carrying both the kept products and the
``DroppedProduct`` reasons so callers can surface them to the buyer
(``GetProductsResponse.errors`` advisories) or log them
(``_create_media_buy_impl``'s zero-match advisory per the inventory-targeting
plan's accept-with-context decision).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from adcp.types import Identifier

from src.core.database.models import AuthorizedProperty
from src.core.database.repositories.authorized_property import AuthorizedPropertyRepository
from src.core.schemas import Error
from src.services.identifier_matching import identifier_dicts, property_matches_buyer_list

logger = logging.getLogger(__name__)


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
    """Outcome of intersecting products with a buyer's property_list.

    Attributes:
        kept_products: Products that pass the intersection.
        dropped_products: Products excluded, paired with the reason. Callers
            surface these as buyer-visible advisories (zero-match is not a
            rejection condition) or in operator logs.
    """

    kept_products: tuple[Any, ...] = ()
    dropped_products: tuple[DroppedProduct, ...] = ()

    @property
    def zero_match(self) -> bool:
        """True when no products survived the intersection — drives the advisory paths."""
        return len(self.kept_products) == 0


class PropertyIntersection:
    """Filter products against a buyer's property_list with faithful resolution.

    Args:
        authorized_property_repo: Tenant-scoped repo used to resolve ``by_id``
            and ``by_tag`` selectors to AuthorizedProperty rows.
    """

    def __init__(self, authorized_property_repo: AuthorizedPropertyRepository) -> None:
        self._repo = authorized_property_repo

    # ``Any`` products are deliberate: the intersection reads only
    # ``publisher_properties``/``property_targeting_allowed`` duck-typed, so it
    # accepts schema Products, converted ORM rows, and unit-test doubles alike.
    def filter_products(self, products: list[Any], buyer_identifiers: list[Identifier]) -> IntersectionResult:
        """Apply the intersection across a product list.

        ``buyer_identifiers`` are the buyer's resolved property_list
        identifiers, typed — ``.type`` participates in matching (an
        ``ios_bundle`` value never matches a ``domain`` value) and
        ``domain``-type values keep their spec grammar (wildcards, bare-domain
        www/m selection).
        """
        buyer_dicts = identifier_dicts(buyer_identifiers)
        kept: list[Any] = []
        dropped: list[DroppedProduct] = []
        for product in products:
            outcome = self._evaluate(product, buyer_dicts)
            if outcome is None:
                kept.append(product)
            else:
                dropped.append(DroppedProduct(product=product, reason=outcome))
                # Single source of the [INTERSECTION-ADVISORY] operator marker: emitting it
                # here means both get_products and the create-side advisory builder inherit
                # consistent observability for a buyer property_list drop.
                logger.warning(
                    "[INTERSECTION-ADVISORY] product %s excluded by buyer property_list (reason=%s)",
                    getattr(product, "product_id", "?"),
                    outcome.value,
                )
        return IntersectionResult(kept_products=tuple(kept), dropped_products=tuple(dropped))

    def _evaluate(self, product: Any, buyer_dicts: list[dict[str, str]]) -> DropReason | None:
        """Return ``None`` to keep, or the drop reason to exclude.

        The unit of matching is the covered PROPERTY: a property is selected by
        the buyer's list when any of its identifiers matches any buyer
        identifier (the SDK's ``identifiers_match`` semantic). Strict mode
        requires every covered property to be selected.
        """
        rows = self._resolve_covered_rows(product)
        if rows is None:
            # selection_type='all' on at least one selector — product is unbounded.
            return None
        if not rows:
            return DropReason.NO_RESOLVABLE_PROPERTIES
        row_matches = [property_matches_buyer_list(row.identifiers, buyer_dicts) for row in rows]
        if not any(row_matches):
            return DropReason.NO_PROPERTY_OVERLAP
        if not getattr(product, "property_targeting_allowed", False):
            # Strict: every covered property must be in the buyer's list.
            if not all(row_matches):
                return DropReason.STRICT_MODE_VIOLATION
        return None

    def _resolve_covered_rows(self, product: Any) -> list[AuthorizedProperty] | None:
        """Resolve a product's publisher_properties to covered AuthorizedProperty rows.

        Returns ``None`` when at least one selector is ``selection_type='all'``
        (the product is unbounded). Returns an empty list when the product has
        no selectors, or only selectors that resolve to no AuthorizedProperty
        rows (unknown ``by_id`` IDs, or ``by_tag`` tags that match nothing).

        ``by_id`` lookups are scoped to the selector's ``publisher_domain``:
        ``PropertyId`` slugs are only unique per publisher, so an unscoped
        lookup could resolve a slug authored for pub-a against pub-b's row.
        """
        selectors = getattr(product, "publisher_properties", None) or []
        if not selectors:
            return []

        by_id_groups: dict[str, set[str]] = {}
        rows: list[AuthorizedProperty] = []
        for selector in selectors:
            inner = getattr(selector, "root", selector)
            selection_type = getattr(inner, "selection_type", None)
            if selection_type == "all":
                # Any "all" selector makes the whole product unbounded.
                return None
            if selection_type == "by_id":
                # ``PropertyId`` is a ``RootModel[str]`` carrying an
                # AuthorizedProperty ID (slug), publisher-scoped by the selector.
                by_id_groups.setdefault(inner.publisher_domain, set()).update(pid.root for pid in inner.property_ids)
            elif selection_type == "by_tag":
                # ``PropertyTag`` is a ``RootModel[str]`` — direct ``.root`` access.
                tags = [tag.root for tag in inner.property_tags]
                rows.extend(self._repo.list_by_tags(inner.publisher_domain, tags))

        for publisher_domain in sorted(by_id_groups):
            rows.extend(self._repo.list_by_ids(publisher_domain, sorted(by_id_groups[publisher_domain])))

        return rows


# Sentinel: omitted kwargs leave their key out of ``details`` entirely, while
# explicitly-passed values (including None) appear.
_UNSET: Any = object()


def property_list_drop_advisory(
    *,
    message: str,
    field: str,
    reason: Any = _UNSET,
    product_id: Any = _UNSET,
    list_id: Any = _UNSET,
    additional_dropped: Any = _UNSET,
    suggestion: str | None = None,
) -> Error:
    """Single builder for buyer-facing property_list drop advisories.

    Every advisory for "a product/package was excluded (or zero-matched) by the
    buyer's property_list" is constructed here, so the error code and the
    ``details`` key vocabulary are decided once — call sites supply only the
    prose, the JSONPath-lite ``field``, and whichever detail keys legitimately
    apply (``reason`` accepts a :class:`DropReason` or its string value).
    """
    details: dict[str, Any] = {}
    for key, value in (
        ("product_id", product_id),
        ("reason", reason),
        ("list_id", list_id),
        ("additional_dropped", additional_dropped),
    ):
        if value is not _UNSET:
            details[key] = value.value if isinstance(value, DropReason) else value
    return Error(  # structural-guard: advisory: property_list drops are accept-with-context — they ride the success envelope, never a raise
        code="PRODUCT_UNAVAILABLE",
        message=message,
        field=field,
        suggestion=suggestion,
        details=details or None,
        # Explicit recovery: a zero-overlap/dropped-product advisory is
        # buyer-correctable (fix the list or pick another product) — without
        # it, the spec's forward-compat rule tells receivers to assume
        # transient (retry), the wrong instruction for this condition.
        recovery="correctable",
        # severity is prose-defined for warnings-in-errors[] but absent from
        # the Error schema at 3.0.1 — legal via additionalProperties, and it
        # lets schema-aware buyers separate advisories from failures.
        severity="warning",
    )
