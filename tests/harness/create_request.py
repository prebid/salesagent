"""The create_media_buy request payload, built from the DTO-bound factories.

WHY THIS LIVES IN THE HARNESS. It used to live in
``tests/bdd/steps/generic/_create_request.py``, and the layer was wrong in a way that
had consequences rather than being untidy. The dependency direction here is
``tests/factories`` <- ``tests/harness`` <- ``tests/bdd/steps``: the harness imports
factories and never imports bdd (measured -- the only ``tests.harness`` -> ``tests.bdd``
imports in the tree are inside ``test_outcome_helpers_wire_contract.py``, a test OF a bdd
helper). Request construction is cross-transport SETUP, which is the harness's job, so
parking it in the step layer meant:

  - the harness could not call it without inverting the dependency;
  - non-BDD tests reached into the step layer anyway
    (``tests/integration/test_bdd_create_request_builder.py``,
    ``tests/integration/test_minimum_spend_validation.py``, ``integration/conftest.py``,
    ``tests/unit/test_product_schema_obligations.py``);
  - and, sitting a layer away from ``tests/factories``, it drifted into hand-writing the
    package dict instead of using the factory that owns it.

That last one is the defect this module exists to end. ``packages`` is built ONLY by
``PackageRequestFactory``, which binds ``src.core.schemas.PackageRequest``, so the fields
a package carries are the ones the accepted model declares. A literal is bound to nothing:
it carries whatever its author thought of, and a field the pin adds tomorrow lands in no
copy of it. Before this module there were three copies of that shape -- the builder's own
literal, ``given_request_2_packages``, and ``tests/factories/media_buy.request_package``
(the only one derived from the model).

VALID vs DELIBERATELY-INVALID. A conformant package is the BARE factory call. A package
that is meant to be wrong is the factory call plus a NAMED override, so the deviation is
visible at the site instead of baked into a literal where nothing can tell whether it is
intentional. ``tests/factories/malformed.py`` is the declaration vocabulary for the
stronger form, gated at dispatch by ``assert_declared_malformations``.

``ctx`` is deliberately absent from this module's signatures -- it is a BDD concept. The
step layer keeps a thin ctx-reading wrapper over these functions.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from tests.factories.mint import mint
from tests.factories.request import PackageRequestFactory


def pricing_option_id(pricing_option: Any) -> str:
    """The id a ``PricingOption`` row carries — the same one get_products announces.

    Read off the row rather than rebuilt from its other columns. Rebuilding was a sixth
    copy of a grammar the readers did not agree on, and it could only ever produce the
    DEFAULT id, so a row whose publisher chose its own id would be named wrongly here.
    """
    return pricing_option.pricing_option_id


def build_request_packages(
    packages: list[tuple[str, str]],
    *,
    budget: float = 5000.0,
) -> list[dict[str, Any]]:
    """The ``packages`` array of a create request, one entry per ``(product_id, option_id)``.

    Every entry is ``PackageRequestFactory.payload(...)`` — the DTO-bound owner of the
    package shape — with only the three fields the model REQUIRES supplied. The factory's
    other 31 optional fields stay unset, because a baseline that filled them would be
    stating decisions the buyer did not make.

    BUDGETS DIFFER PER PACKAGE, scaled down by index. That is deliberate and it is what
    makes the multi-package obligations falsifiable: with equal budgets, a seller that
    collapses N packages into one or misallocates budget across them still satisfies every
    allocation assertion. It is not decoration — it is the difference between the Thens
    grading and the Thens passing.

    ``package_id`` is NOT set, here or anywhere a REQUEST is built: the buyer does not send
    one. The seller mints it and ``MediaBuyRepository.create_from_request`` injects it into
    the persisted ``raw_request`` — which is why ``tests/factories/media_buy.request_package``
    (an ORM-fixture helper for that PERSISTED shape) does inject it and this does not. The
    two are different shapes with the same field names, and conflating them is how a
    ``MediaPackage`` row and its request entry end up with identities that join to nothing.
    """
    return [
        PackageRequestFactory.payload(
            product_id=product_id,
            budget=budget / (index + 1),
            pricing_option_id=option_id,
        )
        for index, (product_id, option_id) in enumerate(packages)
    ]


def build_create_request(
    packages: list[tuple[str, str]],
    *,
    po_number: str | None = None,
    budget: float = 5000.0,
) -> dict[str, Any]:
    """A valid create_media_buy request dict over the given ``(product_id, option_id)`` pairs.

    The single base-request literal, minus the package shape it used to duplicate.

    ``po_number`` is OMITTED when None rather than written as ``None``: the A2A wrapper no
    longer mints one when the caller omits it (it stays None for idempotency-hash +
    cross-transport parity), so a caller that hashes the canonical payload supplies its own
    exactly as a real buyer does — and a caller that does not want one should produce a dict
    without the key, not a dict with a null in it.

    Deliberately KEY-FREE: no idempotency_key. Minting one is the caller's decision and
    there are two different right answers — a fresh key per call (independent buys) and one
    stable key per scenario (replay). A third minting site here would silently pick one of
    them for everybody.
    """
    assert packages, "A create request needs at least one package."
    now = datetime.now(UTC)
    kwargs: dict[str, Any] = {
        "brand": {"domain": "testbrand.com"},
        # Clock-derived and therefore different on every run. ``mint`` records it as
        # GENERATED so ``compare_payloads`` interns it instead of reporting every
        # media-buy scenario as CHANGED; the pinned literals beside it (the brand
        # domain, the PO number) are untouched and still diffed verbatim.
        "start_time": mint((now + timedelta(days=1)).isoformat()),
        "end_time": mint((now + timedelta(days=30)).isoformat()),
        "packages": build_request_packages(packages, budget=budget),
    }
    if po_number is not None:
        kwargs["po_number"] = po_number
    return kwargs
