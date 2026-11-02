"""Factory_boy factories for MediaBuy and MediaPackage models.

Also holds the Pydantic factory for the ``get_media_buys`` RESPONSE item
(``GetMediaBuysMediaBuyFactory``) — the wire-shaped sibling of the ORM
``MediaBuyFactory`` above it.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import factory
from adcp.types import MediaBuyStatus
from factory import LazyAttribute, Sequence, SubFactory

from src.core.database.models import MediaBuy, MediaPackage, PricingOption, is_media_buy_seller_confirmed
from src.core.helpers.pricing_helpers import pricing_info_for
from src.core.schemas import GetMediaBuysMediaBuy
from tests.factories.account import DEFAULT_TEST_ACCOUNT_ID, seed_default_account
from tests.factories.core import TenantFactory
from tests.factories.principal import PrincipalFactory
from tests.factories.product import DEFAULT_PRICING_OPTION_ID, PricingOptionFactory
from tests.factories.request import PackageRequestFactory

#: The product a fixture package points at. Held here rather than taken from the request
#: factory's ``prod-1`` because the ORM fixtures JOIN on it: seeding hooks create
#: ``ProductFactory(product_id="prod_001")`` so format validation resolves the product
#: instead of silently skipping.
FIXTURE_PRODUCT_ID = "prod_001"


def request_package(index: int = 0, **overrides: Any) -> dict[str, Any]:
    """One ``raw_request`` package: the DTO-bound payload with *overrides* applied.

    Built by ``PackageRequestFactory``, which binds ``PackageRequest`` — so the fields a
    fixture package carries are the ones the accepted model declares, not a list someone
    typed. ``pricing_option_id`` is there because the model REQUIRES it, and it is what
    ``get_media_buy_delivery`` resolves the ``pricing_model``/``rate``/``currency`` from
    that ``get-media-buy-delivery-response.json`` requires on every ``by_package`` entry.

    Two fields are set on the payload rather than taken from the model, each deliberately:

    - ``product_id`` — the model's ``prod-1`` names no fixture product. The ORM fixtures
      JOIN on ``prod_001``: seeding hooks create ``ProductFactory(product_id="prod_001")``
      so format validation resolves the product instead of silently skipping.
    - ``package_id`` — NOT a ``PackageRequest`` field, because the buyer does not send one.
      The seller mints it and ``MediaBuyRepository.create_from_request`` injects it into
      the persisted request from ``package_id_map``; injecting it here is the same move,
      and it is what lets a ``MediaPackage`` row and its request entry share one identity
      instead of minting two that join to nothing.
    """
    return PackageRequestFactory.payload(
        **{"product_id": FIXTURE_PRODUCT_ID, "package_id": f"pkg_{index + 1:03d}", **overrides}
    )


def default_request_packages() -> list[dict[str, Any]]:
    """The ``packages`` a conformant create request leaves on ``MediaBuy.raw_request``."""
    return [request_package()]


def pricing_option_for(pricing_option_id: str) -> PricingOption | None:
    """The unpersisted ``PricingOption`` row a package naming *pricing_option_id* selects.

    ``pricing_option_id`` is a stored column, so a row can carry any id its publisher
    chose and this cannot invert an arbitrary one. What it inverts is the DEFAULT id
    ``PricingOption.default_option_id`` assigns, which is what every fixture row carries:
    the parts are read out of the id, a candidate row is built from them, and it is kept
    only if the model agrees that row would be given that id back. An id outside the
    default grammar returns ``None`` — a fixture wanting a custom id builds the row
    itself and reads the id off it.
    """
    parts = pricing_option_id.rsplit("_", 2)
    if len(parts) != 3:
        return None
    pricing_model, currency, fixed = parts
    option = PricingOptionFactory.build(
        pricing_model=pricing_model, currency=currency.upper(), is_fixed=fixed == "fixed"
    )
    return option if option.pricing_option_id == pricing_option_id else None


def pricing_options_for(pricing_option_ids: Iterable[str]) -> dict[str, PricingOption]:
    """The lookup result ``_get_pricing_options`` returns for *pricing_option_ids*.

    For tests that mock that function: the mock answers about the ids production actually
    asked for, so a fixture package and its pricing option cannot drift apart.
    """
    resolved = ((option_id, pricing_option_for(option_id)) for option_id in pricing_option_ids)
    return {option_id: option for option_id, option in resolved if option is not None}


def pricing_options_named(
    pricing_model: str = "cpm",
    rate: str | Decimal = "5.00",
    currency: str = "USD",
    is_fixed: bool = True,
) -> dict[str, PricingOption]:
    """``_get_pricing_options``'s result for ONE option with a caller-chosen *rate*.

    The sibling of :func:`pricing_options_for`, for the tests that grade what a delivery
    report says the rate WAS. ``rate`` is the one term the synthetic id does not encode —
    the grammar is ``{model}_{currency}_{fixed|auction}`` — so it cannot be recovered by
    inverting an id, and a test asserting a cpc buy billed 0.50 has to state it.

    The KEY is READ OFF THE ROW, not computed here. ``pricing_option_id`` is a stored
    column now, and the row the factory builds already carries the id
    ``PricingOption.default_option_id`` assigned it — so this asks the row what it is
    named instead of spelling the grammar a second time. That is the whole difference
    from the helper this replaces, which took an arbitrary id (``"po_cpc"``) and
    fabricated an option for it, so a package could name an id no reader resolves and
    the fixture would still answer.
    """
    option = PricingOptionFactory.build(
        pricing_model=pricing_model,
        rate=Decimal(str(rate)),
        currency=currency,
        is_fixed=is_fixed,
    )
    return {option.pricing_option_id: option}


def package_config_for(package: dict[str, Any]) -> dict[str, Any]:
    """The ``MediaPackage.package_config`` a persisted request *package* carries.

    ``pricing_info`` is built by ``pricing_info_for`` — the SAME projection
    ``_validate_pricing_model_selection`` writes with, so the fixture cannot drift from
    what the seller persists; it is a call, not a copy. ``bid_price`` travels with the
    package because it is what that package bid, and the GAM order manager reads a
    package's rate from it (``src/adapters/utils/pricing.py``).

    ``get_media_buy_delivery`` resolves the pricing its response REQUIRES from this first,
    falling back to the ``PricingOption`` the package names. A row with neither makes the
    delivery report unbuildable, which is what ``_package_pricing`` refuses to paper over.
    """
    option_id = package.get("pricing_option_id")
    option = pricing_option_for(option_id) if option_id else None
    if option is None:
        # A package with no ``pricing_option_id``, or one naming an id outside the
        # synthetic grammar, names no option any reader can resolve — so the row this
        # would build is one production cannot produce. ``pricing_option_id`` is REQUIRED
        # on ``PackageRequest``, so a package that reaches here did not come from
        # ``PackageRequestFactory``; completing it silently is how a fixture that is
        # simply wrong buys a green.
        raise ValueError(
            f"package pricing option {option_id!r} resolves to no option — "
            "build packages through PackageRequestFactory / request_package()"
        )
    return {**package, "pricing_info": pricing_info_for(option, bid_price=package.get("bid_price"))}


class MediaBuyFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = MediaBuy
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"

    # ``MediaBuy.__init__`` refuses ``confirmed_at``/``revision`` outright, so a row
    # cannot be born already committed without passing the repository's stamp. The
    # factory still needs to seed those columns — a test grading listing or concurrency
    # has to start from a row in a state production reaches — so it takes the route the
    # repository takes: construct without them, then assign. Both doors are covered,
    # because ``.build()`` and ``.create()`` reach the model through different hooks.
    #
    # This deliberately adds NO escape hatch to the model. An exemption keyed on "the
    # caller is a factory" would have to enumerate its callers, and the guard this
    # replaces failed precisely because enumeration always misses a spelling.
    @staticmethod
    def _seed_seam_fields(instance, seam):
        """Apply repository-managed columns by assignment, the way the repository does."""
        for field, value in seam.items():
            setattr(instance, field, value)
        return instance

    @classmethod
    def _split_seam_kwargs(cls, kwargs):
        return {field: kwargs.pop(field) for field in MediaBuy._SEAM_MANAGED_FIELDS if field in kwargs}

    @classmethod
    def _build(cls, model_class, *args, **kwargs):
        seam = cls._split_seam_kwargs(kwargs)
        return cls._seed_seam_fields(super()._build(model_class, *args, **kwargs), seam)

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        seam = cls._split_seam_kwargs(kwargs)
        instance = cls._seed_seam_fields(super()._create(model_class, *args, **kwargs), seam)
        if seam and cls._meta.sqlalchemy_session is not None:
            cls._meta.sqlalchemy_session.flush()
        return instance

    tenant = SubFactory(TenantFactory)
    principal = SubFactory(PrincipalFactory, tenant=factory.SelfAttribute("..tenant"))

    media_buy_id = Sequence(lambda n: f"mb_{n:04d}")
    tenant_id = LazyAttribute(lambda o: o.tenant.tenant_id)
    principal_id = LazyAttribute(lambda o: o.principal.principal_id)
    order_name = LazyAttribute(lambda o: f"Order {o.media_buy_id}")
    advertiser_name = LazyAttribute(lambda o: o.principal.name)
    budget = Decimal("10000.00")
    currency = "USD"
    start_date = date(2025, 1, 1)
    end_date = date(2027, 12, 31)
    status = "pending_approval"
    # This factory persists STRAIGHT to the session (sqlalchemy_session_persistence
    # = "commit"), bypassing MediaBuyRepository — so it must reproduce the writer's
    # confirmation stamp itself. The repository writes confirmed_at the instant a buy
    # reaches a seller-committed status (_stamp_confirmation_if_needed), and the
    # pinned get-media-buys-response item schema forbids status "active" with a null
    # confirmed_at. Without this, every factory-seeded confirmed buy is a row
    # production cannot produce, and the wire documents built from it validate only
    # while a read-time fallback fabricates the missing value.
    #
    # The writer NO LONGER shares this predicate. It takes an explicit
    # ``seller_committed`` flag from the caller and does not consult status at all,
    # because ``pending_creatives`` names two states — an auto-approved buy with
    # nothing supplied yet (committed) and a buy held on creative review (not) — and
    # no status-keyed rule can be right about both. This factory keeps a
    # status-derived DEFAULT because a fixture needs one, and the predicate is still
    # the best available approximation for every status except that one; it is a
    # convenience default, not a mirror of the writer.
    #
    # For the ambiguous member, pass ``confirmed_at`` explicitly: a held
    # ``pending_creatives`` row is the default (None), and the auto-approved variant
    # is seeded with an explicit timestamp.
    #
    # Two properties are deliberate and must survive edits:
    #   - CONDITIONAL rather than re-listing statuses — a second listing drifts, and
    #     an unconditional stamp would manufacture committed instants on
    #     draft/rejected/failed rows that no production path can produce.
    #   - a FRESH clock reading, like the writer's, never derived from another column
    #     (approved_at/created_at) — that derivation is the read-time fabricator, and
    #     reproducing it here would re-import it into every fixture.
    # Opt out with an explicit ``confirmed_at=None`` (an explicit kwarg beats a
    # LazyAttribute in factory_boy) when the test grades the repository's own stamp.
    confirmed_at = LazyAttribute(lambda o: datetime.now(UTC) if is_media_buy_seller_confirmed(o.status) else None)
    # A buy created by a conformant request carries an account: ``account`` is REQUIRED on
    # create-media-buy-request.json, and the transport boundary resolves it onto the row. A
    # factory buy without one is a shape production can no longer produce, and it makes the
    # idempotency backstop -- scoped by (principal, account, key) -- miss its own row.
    #
    # The Account ROW is created too, not just the id: media_buys carries a composite FK to
    # (tenant_id, account_id), and many tests mint an ad-hoc tenant with no account, so a
    # bare id default is a ForeignKeyViolation on every one of them.
    account_id = LazyAttribute(lambda o: _ensure_default_account(o.tenant_id))
    raw_request = LazyAttribute(
        lambda o: {
            "packages": default_request_packages(),
            "account": {"account_id": DEFAULT_TEST_ACCOUNT_ID},
        }
    )

    @factory.post_generation
    def grant_account_access(obj, create, extracted, **kwargs):  # noqa: N805 — factory_boy hook signature
        """Let the buy's own principal REACH the account the buy carries.

        ``account_id`` above seeds the Account ROW because the composite FK demands one,
        but a row is not access: resolution is gated on the ``AgentAccountAccess`` join
        (#1417), and ``account_lookup._by_id`` — the path anything rebuilding an identity
        from a stored ``account_id`` takes, including the approval executor's
        ``identity_of`` — is NOT access-scoped, so it calls ``_require_access``. Seeding
        the row and not the grant therefore produced a buy whose own principal was not
        permitted on its own account: a world production cannot reach, which surfaced as
        ``AttributeError: 'NoneType' object has no attribute 'account_id'`` or a bare
        ``AdCPAuthorizationError`` in tests whose subject was something else entirely.

        Granted HERE rather than at each call site because a factory that cannot produce a
        reachable buy without the caller remembering a second factory is the factory's
        defect — every future test would hit it, and three already did
        (``test_media_buy_v3``, ``test_creative_assignment_principal_id``,
        ``tests/helpers/media_buy_approval.seed_pending_buy``, which carried this line by
        hand).

        Grants exactly (this buy's principal, this buy's account) and nothing wider, so a
        refusal test is unaffected: an OWNERSHIP-mismatch case drives a DIFFERENT principal,
        which is still unpermitted, and an access-refusal case names its own account
        (``test_resolve_account::test_no_access_raises`` builds ``acc_noaccess`` through
        ``AccountFactory``, which this hook never touches).

        Pass ``grant_account_access=False`` for a case that needs the buy's own principal
        locked out of its own account.
        """
        if not create or extracted is False:
            return
        seed_default_account(obj.tenant_id, obj.principal_id)

    @factory.post_generation
    def persisted_packages(obj, create, extracted, **kwargs):  # noqa: N805 — factory_boy hook signature
        """Materialize the ``MediaPackage`` row each persisted request package names.

        A buy production created always has them: ``create_media_buy`` writes one row per
        package and injects that row's ``package_id`` back into ``raw_request``. A factory
        buy without them is a shape production cannot produce, and ``get_media_buy_delivery``
        cannot state the pricing its response REQUIRES for a package with no row and no
        ``PricingOption`` — the same reason ``account_id`` above seeds a real Account row.

        Only packages that NAME a ``package_id`` get a row, because that id is what the
        repository injects when it persists them; a caller who supplies a ``raw_request``
        whose packages carry none is describing a buy whose packages were never persisted,
        and inventing rows for it would invent identities too.

        Named ``persisted_packages`` rather than ``packages`` so the model's own
        relationship stays reachable as a constructor kwarg.
        """
        if not create or MediaBuyFactory._meta.sqlalchemy_session is None:
            return
        for package in (obj.raw_request or {}).get("packages", []):
            if package.get("package_id"):
                MediaPackageFactory(media_buy=obj, package_id=package["package_id"])


def _ensure_default_account(tenant_id: str) -> str:
    """The tenant's default Account row, created if absent, returning its id.

    Delegates to ``seed_default_account`` so there is ONE get-or-create for this row: the
    account grant hook above and the fixtures that seed a tenant by hand all go through
    it, and a second copy here is what let the grant half drift out of three fixtures.
    Called with no principal because the buy's ``principal_id`` is not resolved yet at
    LazyAttribute time; the ``grant_account_access`` post-generation hook adds the grant
    once the row exists.
    """
    return seed_default_account(tenant_id)


def _request_packages(media_buy) -> list[dict[str, Any]]:
    """The packages the buy's persisted request names."""
    return (getattr(media_buy, "raw_request", None) or {}).get("packages") or []


def _source_package(o) -> dict[str, Any]:
    """The ``raw_request`` package this row is the persisted form of.

    A ``MediaPackage`` row does not mint its own identity or terms: production writes the
    row and the ``raw_request`` entry from the same request package, and every reader —
    the delivery report above all — joins them by ``package_id``.
    """
    packages = _request_packages(o.media_buy)
    named = [pkg for pkg in packages if pkg.get("package_id") == o.package_id]
    return named[0] if named else (packages[0] if packages else {})


class MediaPackageFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = MediaPackage
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"

    # ``(media_buy_id, package_id)`` is the primary key, and ``MediaBuyFactory`` already
    # materializes a row for every package its ``raw_request`` names. A caller naming one
    # of those packages is asking for THAT row — inserting a second is an IntegrityError,
    # not a second package — so the row is updated in place with whatever the caller
    # states and returned.
    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        session = cls._meta.sqlalchemy_session
        existing = session.get(model_class, (kwargs["media_buy_id"], kwargs["package_id"])) if session else None
        if existing is None:
            return super()._create(model_class, *args, **kwargs)
        for field, value in kwargs.items():
            setattr(existing, field, value)
        session.commit()
        return existing

    media_buy = SubFactory(MediaBuyFactory)
    media_buy_id = LazyAttribute(lambda o: o.media_buy.media_buy_id)
    package_id = LazyAttribute(
        lambda o: next(
            (pkg["package_id"] for pkg in _request_packages(o.media_buy) if pkg.get("package_id")), "pkg_001"
        )
    )
    budget = LazyAttribute(lambda o: Decimal(str(_source_package(o).get("budget", "5000.00"))))
    pacing = "even"
    package_config = LazyAttribute(
        lambda o: package_config_for(
            {
                **_source_package(o),
                "package_id": o.package_id,
                "product_id": _source_package(o).get("product_id", FIXTURE_PRODUCT_ID),
                "budget": float(o.budget),
            }
        )
    )


class GetMediaBuysMediaBuyFactory(factory.Factory):
    """Pydantic factory for a ``get_media_buys`` response item.

    Not an ORM factory — this builds the wire-shaped item that
    ``GetMediaBuysResponse.media_buys`` carries, so tests that grade the
    response (serialization, the protocol ``message``) don't hand-roll it.

    ``confirmed_at`` and ``revision`` are spec-REQUIRED on ``media_buys[]`` at
    AdCP 3.1.1 and the model is grounded on the library item type, so both carry
    concrete defaults here rather than being left to the caller.
    """

    class Meta:
        model = GetMediaBuysMediaBuy

    media_buy_id = Sequence(lambda n: f"mb_{n:04d}")
    status = MediaBuyStatus.active
    currency = "USD"
    total_budget = 10000.0
    confirmed_at = datetime(2025, 1, 1, tzinfo=UTC)
    revision = 1
    # LazyFunction, not a bare ``[]``: a mutable class attribute would be the SAME
    # list object on every built item.
    packages = factory.LazyFunction(list)


def package_pricing_fields(
    pricing_model: str | None = None,
    rate: float | None = None,
    currency: str | None = None,
) -> dict[str, Any]:
    """The three pin-REQUIRED pricing kwargs, for a hand-built ``PackageDelivery``.

    ``get-media-buy-delivery-response.json`` lists ``pricing_model``, ``rate`` and
    ``currency`` in the ``required`` set of every ``by_package`` item and types all three
    non-nullable, so a fixture that omits them is not building the object the pin
    describes — it is building one the wire will reject.

    Defaults are READ OFF the option the default request package names rather than
    restated, so a fixture's hand-built delivery item cannot disagree with the package it
    reports on.
    """
    option = pricing_option_for(DEFAULT_PRICING_OPTION_ID)
    assert option is not None  # DEFAULT_PRICING_OPTION_ID is a default-grammar id by construction
    return {
        "pricing_model": pricing_model if pricing_model is not None else option.pricing_model,
        "rate": rate if rate is not None else float(option.rate),
        "currency": currency if currency is not None else option.currency,
    }


def seed_delivery_pricing(
    tenant: Any,
    product_id: str = FIXTURE_PRODUCT_ID,
    pricing_model: str = "cpm",
    rate: str | Decimal = "5.00",
    currency: str = "USD",
) -> str:
    """PERSIST the Product + PricingOption rows a delivery-reporting buy needs; return the id.

    The integration ``DeliveryPollEnv`` runs the REAL ``_get_pricing_options``, which reads
    the tenant's ``pricing_options`` rows and reconstructs each synthetic id, so a package
    naming an id no row produces resolves to nothing. The returned id is produced by
    the row's own ``pricing_option_id`` column, read back rather than recomputed.

    Pass ``tenant`` (the object, not the id: ``ProductFactory`` derives ``tenant_id`` from
    its ``tenant`` SubFactory, so an id kwarg alone leaves the row on a freshly-minted
    tenant).
    """
    from tests.factories.product import ProductFactory

    product = ProductFactory(tenant=tenant, product_id=product_id)
    option = PricingOptionFactory(
        product=product,
        pricing_model=pricing_model,
        rate=Decimal(str(rate)),
        currency=currency,
        is_fixed=True,
    )
    return option.pricing_option_id
