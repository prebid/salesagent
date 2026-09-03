"""An adapter's pricing set is declared once, and what it reports is what it enforces.

Two fields used to declare the same fact independently:

* ``AdapterCapabilities.supported_pricing_models`` — the field the capabilities route
  ``GET /api/adapters/<type>/capabilities`` would serve
  (``src/admin/blueprints/adapters.py``, ``jsonify(asdict(schemas.capabilities))``).
* ``AdServerAdapter.supported_pricing_models`` — the frozenset
  ``validate_media_buy_request`` enforces.

They had already drifted. GAM declared ``None`` on the capabilities side — a value any
reader of that surface can only take as "all pricing models supported" — while refusing
everything outside ``{cpm, vcpm, cpc, flat_rate}``. The capabilities field is now derived
from the frozenset by ``AdServerAdapter.__init_subclass__``, leaving one declaration per
adapter.

What is graded here is that derived ``capabilities.supported_pricing_models`` value
itself. Nothing in this module issues a request or exercises a reader of it.

GAM is the anchor of these tests deliberately. The mock declares the same seven models on
both sides under either design, so a mock-only test passes on the drifted code and binds
nothing — which is exactly how the drift went unnoticed.
"""

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
from adcp.types import PricingModel

from src.adapters import ADAPTER_REGISTRY, get_adapter_schemas
from src.adapters.base import AdServerAdapter
from src.adapters.google_ad_manager import GoogleAdManager
from src.core.schemas import Principal

pytestmark = pytest.mark.unit


def served_pricing_models(adapter_type: str) -> list[str] | None:
    """Return the pricing models the capabilities route would serve for ``adapter_type``.

    Mirrors the body of ``get_adapter_capabilities`` in
    ``src/admin/blueprints/adapters.py`` — it does not drive the route, so this helper
    grades the derived value, never the route's own behaviour. The two shapes have
    already diverged: on an adapter that declares no capabilities the route returns
    ``jsonify({})``, so the key is absent from the payload entirely, while this helper
    returns ``None``, standing for a key that is present with a null value. Read a
    ``None`` from here as "this adapter declares no capabilities", not as the payload
    the route would produce.
    """
    schemas = get_adapter_schemas(adapter_type)
    assert schemas is not None, f"{adapter_type} is not in ADAPTER_REGISTRY"
    if schemas.capabilities is None:
        return None
    return asdict(schemas.capabilities)["supported_pricing_models"]


@pytest.fixture
def gam_adapter() -> GoogleAdManager:
    """A GAM adapter instance — dry-run, so no GAM network traffic."""
    principal = Mock(spec=Principal)
    principal.principal_id = "test_principal"
    principal.platform_mappings = {}
    return GoogleAdManager(
        config={"network_code": "123456", "refresh_token": "test_token", "enabled": True},
        principal=principal,
        network_code="123456",
        advertiser_id="789",
        trafficker_id="101112",
        dry_run=True,
        tenant_id="test_tenant",
    )


def spec_pricing_models() -> set[str]:
    """Every pricing model AdCP defines — the canonical universe for these tests.

    Taken from the spec enum rather than from another adapter's declaration: an
    adapter-sourced universe makes GAM's contract move whenever that adapter's set
    moves, and re-spelling the members as a literal here would reproduce the
    two-declarations defect this file exists to close.
    """
    return {model.value for model in PricingModel}


def _validate_pricing(adapter: AdServerAdapter, pricing_model: str) -> list[str]:
    """Run the adapter's real pre-validation for a single package's pricing model.

    ``request``/``packages`` are placeholders: the pricing branch of
    ``AdServerAdapter.validate_media_buy_request`` reads only ``package_pricing_info``
    and the adapter's own supported set, and GAM does not override the method.
    """
    now = datetime.now(UTC)
    return adapter.validate_media_buy_request(
        request=None,  # type: ignore[arg-type]
        packages=[],
        start_time=now,
        end_time=now + timedelta(days=1),
        package_pricing_info={"pkg_1": {"pricing_model": pricing_model}},
    )


class TestGamReportsWhatItEnforces:
    """GAM: the disagreeing adapter. These fail on the two-declaration design."""

    def test_gam_is_a_constrained_adapter(self):
        """GAM must report a real, strictly-narrower set — never 'all models'.

        This is the assertion the old code failed: GAM's capabilities carried ``None``,
        which reads as every pricing model supported.
        """
        served = served_pricing_models("google_ad_manager")
        assert served is not None, "GAM's capabilities report 'all pricing models supported'"
        assert set(served) < spec_pricing_models(), f"GAM must report fewer models than AdCP defines; got {served}"

    def test_gam_enforces_exactly_the_models_it_is_configured_for(self):
        """Pin GAM's content, not only its relations.

        Every other assertion here compares GAM against itself: the served set is derived
        from the enforced frozenset, so narrowing that frozenset moves both sides together
        and reddens nothing. This is the assertion that notices a change to what GAM
        actually sells.
        """
        assert GoogleAdManager.supported_pricing_models == frozenset({"cpm", "vcpm", "cpc", "flat_rate"})

    def test_served_set_equals_enforced_set(self, gam_adapter):
        """What the capabilities surface reports is what the validator enforces."""
        assert served_pricing_models("google_ad_manager") == sorted(gam_adapter.get_supported_pricing_models())

    def test_validator_accepts_every_model_gam_reports(self, gam_adapter):
        """Every model the capabilities surface reports is actually buyable."""
        served = served_pricing_models("google_ad_manager")
        assert served, "nothing served — see test_gam_is_a_constrained_adapter"
        for pricing_model in served:
            assert _validate_pricing(gam_adapter, pricing_model) == [], (
                f"GAM reports '{pricing_model}' but its validator rejects it"
            )

    def test_validator_refuses_every_model_gam_omits(self, gam_adapter):
        """Every model the capabilities surface omits is actually refused.

        Pairs with the test above: together they pin the served list to the validator's
        answer in both directions, so a list that is merely non-empty cannot pass. The
        omitted set is measured against the whole spec, so every model GAM withholds is
        exercised — not just the ones some other adapter happens to declare.
        """
        served = served_pricing_models("google_ad_manager")
        omitted = spec_pricing_models() - set(served or [])
        assert omitted, "no omitted models to check — GAM should not support every model AdCP defines"
        for pricing_model in sorted(omitted):
            errors = _validate_pricing(gam_adapter, pricing_model)
            assert errors, f"GAM omits '{pricing_model}' from its capabilities but accepts it"


@pytest.mark.parametrize("adapter_type", sorted(ADAPTER_REGISTRY))
def test_every_registered_adapter_reports_what_it_enforces(adapter_type):
    """No adapter in the registry may report a pricing set it does not enforce."""
    adapter_class = ADAPTER_REGISTRY[adapter_type]
    served = served_pricing_models(adapter_type)

    if not issubclass(adapter_class, AdServerAdapter):
        # Not an ad server (creative_engine) — declares no capabilities, serves nothing.
        assert served is None
        return

    assert served == sorted(adapter_class.supported_pricing_models)
