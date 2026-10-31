"""Pricing option schemas extending the adcp library's discriminated union.

The SDK's ``PricingOption`` is a nine-member discriminated union (discriminator
``pricing_model``). Its members carry ``extra="allow"``, which this seller must
not expose: unknown inbound fields would be silently echoed back onto the wire,
and pre-V3 keywords such as ``rate=`` would be accepted instead of rejected.
The members can carry an ``extra`` policy: each one is a plain BaseModel and
subclasses cleanly.

At the pinned SDK the union also had a ``RootModel`` WRAPPER class, which this
module's ``PricingOption`` subclassed. the pinned SDK deletes that wrapper and ships
only the union — the fix for adcontextprotocol/adcp-client-python#1077.

This module therefore defines:

- Nine local subclasses of the SDK union members. Each inherits its full field
  set from the library type (Pattern #1 — never copy fields) and mixes in a
  derived ``is_fixed`` property. The mixin also applies this project's ``extra``
  policy (``forbid`` outside production), replacing the SDK's ``allow``.
- ``AdCPPricingOption``, the union of those nine, which is what
  ``Product.pricing_options`` holds.
- ``PricingOptionAdapter``, a ``TypeAdapter`` for a caller that must validate an
  arbitrary document against the discriminated union.

NO LOCAL ``RootModel`` WRAPPER, and the deletion is the point. One was kept here
after the SDK dropped its own, on the stated grounds that ``option.root`` and
proxied attribute access had to keep working at call sites. They did not:
``Product.pricing_options`` is typed with the union MEMBERS, so every element of
the only list the wrapper was written for is a member, ``.root`` raises
``AttributeError`` on it, and the wrapper itself never landed there. What the
``__getattr__`` proxy bought was that a member and a wrapper read alike, which
is exactly what let the mistyping go unnoticed. A union member is reached by
reading its field; where a branch is genuinely needed, discriminate on
``pricing_model``.

Naming note: the nine member subclasses are star-exported and shadow the SDK
names at package level, so ``from src.core.schemas import CpmPricingOption``
resolves to the local subclass (Pattern #7 applies). The package-level
``PricingOption`` name refers to the legacy flat model in ``_base.py``, which
remains only because ledgered schema-validation tests
(T-UC-001-boundary-pricing-xor) still grade it; it never named the deleted
wrapper and is unaffected by its removal.
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
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

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
# pricing-option.json oneOf), in local-subclass form. This is what
# Product.pricing_options holds, element for element.
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


#: The discriminated union as a VALIDATOR, for a caller that holds an arbitrary document
#: and needs the pin's verdict on it. One such caller exists: the runtime malformation gate
#: (``GATED_ITEMS``, ``tests/factories/malformed.py``), which validates every dispatched
#: item against the pinned item model for its key.
#:
#: A ``TypeAdapter`` RATHER THAN THE ROOTMODEL THIS REPLACES. The gate needs one thing a
#: bare ``UnionType`` cannot do -- validate a dict and raise ``ValidationError`` -- and
#: that was the only capability the deleted wrapper supplied that nothing else did. A
#: ``TypeAdapter`` supplies exactly it and nothing more: it is not a model, so it cannot
#: be mistyped onto a field, it adds no ``.root`` for a call site to read, and
#: ``validate_python`` returns the MEMBER rather than a wrapper around it.
PricingOptionAdapter: TypeAdapter[AdCPPricingOption] = TypeAdapter(
    Annotated[AdCPPricingOption, Field(discriminator="pricing_model")]
)
