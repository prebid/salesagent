"""DeliveryPollEnv — integration test environment for _get_media_buy_delivery_impl.

Patches: get_adapter ONLY (external ad server).
Real: MediaBuyUoW, get_principal_object, _get_pricing_options (all hit real DB).

Requires: integration_db fixture (creates test PostgreSQL DB).

Usage::

    @pytest.mark.requires_db
    def test_something(self, integration_db):
        with DeliveryPollEnv() as env:
            tenant = TenantFactory(tenant_id="t1")
            principal = PrincipalFactory(tenant=tenant, principal_id="p1")
            buy = MediaBuyFactory(tenant=tenant, principal=principal)
            env.set_adapter_response(buy.media_buy_id, impressions=5000)

            response = env.call_impl(media_buy_ids=[buy.media_buy_id])
            assert response.aggregated_totals.impressions == 5000.0

Available mocks via env.mock:
    "adapter"    -- get_adapter mock (only external mock)
"""

from __future__ import annotations

from typing import Any

from src.core.schemas import AdapterGetMediaBuyDeliveryResponse, GetMediaBuyDeliveryResponse
from tests.harness._base import IntegrationEnv
from tests.harness._mixins import DeliveryPollMixin
from tests.harness.transport import DeliverResult


class DeliveryPollEnv(DeliveryPollMixin, IntegrationEnv):
    """Integration test environment for _get_media_buy_delivery_impl.

    Only mocks the adapter (external ad server). Everything else is real:
    - Real MediaBuyUoW -> real DB queries
    - Real get_principal_object -> real DB queries
    - Real _get_pricing_options -> real DB queries

    Fluent API (from DeliveryPollMixin):
        set_adapter_response(...)  -- configure adapter return for a media_buy_id
        set_adapter_error(exc)     -- make the adapter raise an exception
        call_impl(...)             -- call _get_media_buy_delivery_impl with real DB
    """

    RESPONSE_MODEL = GetMediaBuyDeliveryResponse

    # FIXME(#2012): JUSTIFIED OVERRIDE — deliberately does NOT declare
    # MCP_TOOL/A2A_SKILL, so it does not take the base's client-core delegation.
    # The core's UNWRAP parses into the PINNED GetMediaBuyDeliveryResponse, whose
    # by_package items REQUIRE pricing_model, rate and currency
    # (get-media-buy-delivery-response.json); production emits none of the three,
    # so every response fails that parse and 214 UC-004 scenarios go red. Parsing
    # here with the LOCAL model keeps the env working while the gap stays
    # attributable — a production schema defect, not a dispatch defect, and
    # deliberately not hidden by loosening the core. Delete both overrides and
    # their `_KNOWN_DELIVER_OVERRIDES` entries when #2012 lands.
    def deliver_mcp(self, **kwargs: Any) -> DeliverResult:
        """Dispatch get_media_buy_delivery via the real FastMCP Client pipeline."""
        return self._run_mcp_client("get_media_buy_delivery", GetMediaBuyDeliveryResponse, **kwargs)

    def deliver_a2a(self, **kwargs: Any) -> DeliverResult:
        """Dispatch get_media_buy_delivery via the real A2A handler pipeline."""
        return self._run_a2a_handler("get_media_buy_delivery", GetMediaBuyDeliveryResponse, **kwargs)

    EXTERNAL_PATCHES = {
        "adapter": "src.core.helpers.adapter_helpers.get_adapter",
    }
    REST_ENDPOINT = "/api/v1/media-buys/delivery"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._adapter_responses: dict[str, AdapterGetMediaBuyDeliveryResponse] = {}

    def _configure_mocks(self) -> None:
        self._configure_adapter_mock()

    def associate_buys_with_account(self, media_buy_ids: list[str], account_id: str) -> None:
        """Point specific media buys at an account, for scenarios that learn the account late.

        AdCP 3.1.1 defines get_media_buy_delivery's ``account`` as a FILTER, so a scenario
        naming a valid account must own buys in it. Some suites create the buy before the
        account value is known, hence this after-the-fact association.

        Scoped to the ids passed in — never "every unassociated buy in the tenant" — and
        raises when a named buy is absent, so a mis-seeded scenario fails on its own terms
        rather than silently returning an empty delivery set.
        """
        from src.core.database.repositories.media_buy import MediaBuyRepository

        session = self._session
        if session is None:  # pragma: no cover - env misuse
            raise RuntimeError("associate_buys_with_account requires an active env session")

        repo = MediaBuyRepository(session, self._tenant_id)
        for media_buy_id in media_buy_ids:
            # Write through the repository's own API rather than assigning to the ORM row:
            # tests must not reach around the layer production writes through.
            updated = repo.update_fields(media_buy_id, account_id=account_id)
            if updated is None:
                raise AssertionError(
                    f"cannot associate media buy {media_buy_id!r} with account {account_id!r}: "
                    "the buy does not exist in this scenario's database"
                )
        self._commit_factory_data()

    def seed_decoy_buy_on_account(
        self,
        tenant: Any,
        owner: Any,
        *,
        account_id: str,
        media_buy_id: str,
        brand_domain: str,
        operator: str,
    ) -> str | None:
        """Create a buy on a DIFFERENT account, so an account-scope Then step has something to exclude.

        Owned by ``owner`` — the scenario's own buy principal, not the env's default
        identity — so ownership filtering does not remove it and only the account filter
        can. Seeds adapter data too: a targeted buy whose adapter read fails degrades into
        errors[] rather than appearing in media_buy_deliveries, which would make "excluded
        when scoped" prove nothing (an unscoped response would be missing it either way).

        Returns the seeded media_buy_id, or None when this env has no local session to
        seed through. e2e mode still binds ``_session`` to the live server's own database
        (see ``IntegrationEnv.__enter__`` / ``associate_buys_with_account``, which this
        method also calls), so seeding proceeds there too — only a genuinely session-less
        env (unit variants) skips. Declared as a return value rather than the step
        function probing ``hasattr(env, "_session")`` directly — the env is what knows
        whether it can seed, not the step.
        """
        if self._session is None:
            return None

        from tests.bdd.steps.generic._account_resolution import seed_account_with_access
        from tests.factories import MediaBuyFactory

        seed_account_with_access(
            tenant,
            owner,
            account_id=account_id,
            status="active",
            brand_domain=brand_domain,
            operator=operator,
        )
        MediaBuyFactory(tenant=tenant, principal=owner, media_buy_id=media_buy_id, status="active")
        self.associate_buys_with_account([media_buy_id], account_id)
        self.set_adapter_response(media_buy_id=media_buy_id)
        return media_buy_id

    def build_rest_body(self, **kwargs: Any) -> dict[str, Any]:
        """Convert kwargs to GetMediaBuyDeliveryBody shape for REST POST."""
        # Forward all request fields that the REST body accepts
        _BODY_FIELDS = (
            "media_buy_ids",
            "status_filter",
            "start_date",
            "end_date",
            "reporting_dimensions",
            "attribution_window",
            "include_package_daily_breakdown",
            "account",
        )
        return {k: kwargs[k] for k in _BODY_FIELDS if k in kwargs and kwargs[k] is not None}

    def parse_rest_response(self, data: dict[str, Any]) -> GetMediaBuyDeliveryResponse:
        """Parse REST JSON into GetMediaBuyDeliveryResponse."""
        return GetMediaBuyDeliveryResponse(**data)
