"""Pricing option schemas extending the adcp library's discriminated union.

The SDK's ``PricingOption`` RootModel wraps a nine-member discriminated union
(discriminator ``pricing_model``). Its members carry ``extra="allow"``, which
this seller must not expose: unknown inbound fields would be silently echoed
back onto the wire, and pre-V3 keywords such as ``rate=`` would be accepted
instead of rejected. The RootModel wrapper itself cannot carry an ``extra``
policy (pydantic forbids ``extra`` on RootModel — see
adcontextprotocol/adcp-client-python#1077), but the members can: each one is a
plain BaseModel and subclasses cleanly.

This module therefore defines:

- Nine local subclasses of the SDK union members. Each inherits its full field
  set from the library type (Pattern #1 — never copy fields) and mixes in a
  derived ``is_fixed`` property. The mixin also applies this project's ``extra``
  policy (``forbid`` outside production), replacing the SDK's ``allow``.
- A ``PricingOption`` wrapper subclassing the SDK's RootModel with its root
  narrowed to OUR union, so ``option.root`` and proxied attribute access keep
  working at call sites.

Naming note: this wrapper is intentionally NOT star-exported from
``src.core.schemas`` — the package-level ``PricingOption`` name still refers to
the legacy flat model in ``_base.py``, which remains only because ledgered
schema-validation tests (T-UC-001-boundary-pricing-xor) still grade it. Import
the wrapper explicitly via ``from src.core.schemas.pricing import
PricingOption``. The nine member subclasses ARE star-exported and shadow the
SDK names at package level, so ``from src.core.schemas import
CpmPricingOption`` resolves to the local subclass (Pattern #7 applies).
"""

from typing import Annotated

from adcp.types import CpaPricingOption as LibraryCpaPricingOption
from adcp.types import CpcPricingOption as LibraryCpcPricingOption
from adcp.types import CpcvPricingOption as LibraryCpcvPricingOption
from adcp.types import CpmPricingOption as LibraryCpmPricingOption
from adcp.types import CppPricingOption as LibraryCppPricingOption
from adcp.types import CpvPricingOption as LibraryCpvPricingOption
from adcp.types import FlatRatePricingOption as LibraryFlatRatePricingOption
from adcp.types import TimeBasedPricingOption as LibraryTimeBasedPricingOption
from adcp.types import VcpmPricingOption as LibraryVcpmPricingOption

# The SDK's RootModel wrapper. NOT importable as adcp.types.PricingOption —
# that public name is a plain Union alias over the members, not the wrapper.
# Underscore-prefixed (not the usual Library* alias) on purpose: the
# schema-inheritance guard maps every Library* alias against the PACKAGE-level
# class of the same bare name, and src.core.schemas.PricingOption is still the
# legacy flat model (see the naming note above), not this wrapper's subclass.
from adcp.types.generated_poc.core.pricing_option import (
    PricingOption as _LibraryPricingOption,
)
from pydantic import BaseModel, ConfigDict, Field

from src.core.config import get_pydantic_extra_mode

__all__ = [
    "AdCPPricingOption",
    "CpaPricingOption",
    "CpcPricingOption",
    "CpcvPricingOption",
    "CpmPricingOption",
    "CppPricingOption",
    "CpvPricingOption",
    "FlatRatePricingOption",
    "TimeBasedPricingOption",
    "VcpmPricingOption",
]


class _AdapterSupportAnnotations(BaseModel):
    """What every local pricing member adds to its library parent: nothing on the wire.

    The ``extra`` policy declared here replaces the SDK members'
    ``extra="allow"``. For the mixin's config to win pydantic's base-config
    merge, subclasses must list the library parent FIRST and this mixin LAST
    (pydantic applies base configs in ``__bases__`` order, last one wins).

    ``is_fixed`` is DERIVED, not stored: AdCP V3 decides fixed-rate versus auction by
    which price field is present, so a property reads it off the member and there is
    no field to keep off the wire.
    """

    model_config = ConfigDict(extra=get_pydantic_extra_mode())

    @property
    def is_fixed(self) -> bool:
        return getattr(self, "fixed_price", None) is not None


# Base order matters in every subclass below: (Library*, _AdapterSupportAnnotations)
# keeps the mixin last so its extra policy overrides the SDK's extra="allow".


class CpmPricingOption(LibraryCpmPricingOption, _AdapterSupportAnnotations):
    """CPM pricing option — library fields plus internal adapter annotations."""


class VcpmPricingOption(LibraryVcpmPricingOption, _AdapterSupportAnnotations):
    """vCPM pricing option — library fields plus internal adapter annotations."""


class CpcPricingOption(LibraryCpcPricingOption, _AdapterSupportAnnotations):
    """CPC pricing option — library fields plus internal adapter annotations."""


class CpcvPricingOption(LibraryCpcvPricingOption, _AdapterSupportAnnotations):
    """CPCV pricing option — library fields plus internal adapter annotations."""


class CpvPricingOption(LibraryCpvPricingOption, _AdapterSupportAnnotations):
    """CPV pricing option — library fields plus internal adapter annotations."""


class CppPricingOption(LibraryCppPricingOption, _AdapterSupportAnnotations):
    """CPP pricing option — library fields plus internal adapter annotations."""


class CpaPricingOption(LibraryCpaPricingOption, _AdapterSupportAnnotations):
    """CPA pricing option — library fields plus internal adapter annotations."""


class FlatRatePricingOption(LibraryFlatRatePricingOption, _AdapterSupportAnnotations):
    """Flat-rate pricing option — library fields plus internal adapter annotations."""


class TimeBasedPricingOption(LibraryTimeBasedPricingOption, _AdapterSupportAnnotations):
    """Time-based pricing option — library fields plus internal adapter annotations."""


_MEMBER_TYPES: tuple[type[BaseModel], ...] = (
    CpmPricingOption,
    VcpmPricingOption,
    CpcPricingOption,
    CpcvPricingOption,
    CpvPricingOption,
    CppPricingOption,
    CpaPricingOption,
    FlatRatePricingOption,
    TimeBasedPricingOption,
)

# Union of all nine AdCP pricing option types (the pinned spec's
# pricing-option.json oneOf), in local-subclass form. Also the root type of the
# PricingOption wrapper below.
AdCPPricingOption = (
    CpmPricingOption
    | VcpmPricingOption
    | CpcPricingOption
    | CpcvPricingOption
    | CpvPricingOption
    | CppPricingOption
    | CpaPricingOption
    | FlatRatePricingOption
    | TimeBasedPricingOption
)


class PricingOption(_LibraryPricingOption):
    """The SDK's RootModel wrapper, narrowed to OUR union members.

    ``Product.pricing_options`` is typed with this wrapper so every element
    carries the local members' extra policy and internal annotations while the
    call-site contract (``option.root``, proxied attribute access via the
    inherited ``__getattr__``) stays identical to the SDK wrapper it replaces.
    Every local member subclasses its SDK counterpart, so this root type is a
    strict narrowing of the parent's.
    """

    root: Annotated[
        AdCPPricingOption,
        Field(
            description=(
                "A pricing model option offered by a publisher for a product. Discriminated by pricing_model field."
            ),
            discriminator="pricing_model",
            title="Pricing Option",
        ),
    ]

    # NO SDK-COERCING VALIDATOR. A ``mode="before"`` validator used to accept an SDK-typed
    # member (or the SDK's own wrapper) by serializing it to a plain dict and revalidating
    # that dict against the local members. That is the one spelling critical pattern 4 names
    # twice: a pydantic
    # validator on a wire model may not serialize, and a receiving model adopts a sibling
    # generated class by reading its attributes, never by a dump. It survived only because
    # this file carried a per-file exemption in ruff-serialization.toml and in the ast-grep
    # rule; both entries are gone with it, so the ban now covers this file.
    #
    # It was deleted rather than rewritten to the attribute-reading form because nothing
    # needs it. No production path produces an SDK-typed pricing option: every construction
    # site under src/ imports the local subclass (product_conversion.py, whose comment says
    # exactly that; dynamic_pricing_service.py; the Xandr adapter), and the single direct SDK
    # pricing import in src/ is TimeParameters, a nested sub-object rather than a union
    # member. Only tests ever built an SDK member.
    #
    # Its docstring claimed the dump was what kept an undeclared field from leaking off an
    # ``extra="allow"`` SDK instance. That was backwards: the dump is what ACCEPTED the SDK
    # instance in the first place, and re-applying the extra policy was a side effect of
    # that acceptance. With no coercion an SDK instance is refused outright, under either
    # extra mode, so there is nothing left for a field to leak through.
