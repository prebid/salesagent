"""``create_media_buy`` re-dials a stored creative's ``agent_url`` with no adapter-format guard.

``_validate_creatives_before_adapter_call`` (``src/core/tools/media_buy_create.py``)
fetches a format spec for every referenced creative via ``_get_format_spec_sync``
(:405, and its ``_build_adapter_asset_from_creative`` sibling at :677), unlike the
creative-sync INGEST gate (``src/core/tools/creatives/_validation.py:138``,
``is_dialled_agent_url``), which skips external validation for an adapter-provided
pseudo-``agent_url`` like ``broadstreet://<tenant_id>`` — those formats are served
by the adapter IN-PROCESS (``src/core/tools/creative_formats.py``,
``src/adapters/broadstreet/adapter.py``), so there is no request to make and no
format-fetch that could ever resolve it.

The bug: neither call site in ``media_buy_create.py`` applies that same predicate.
A creative stored with ``agent_url="broadstreet://<tenant_id>"`` and
``format="broadstreet_<template>"`` reaches ``_get_format_spec_sync`` unconditionally,
which cannot resolve a format the adapter serves itself — the fetch returns ``None``
(no dialled agent advertises it) — and ``_validate_creatives_before_adapter_call``
treats an unresolved format spec as a rejection: "Creative ... has unknown format
... Format must be registered with the creative agent." A media buy carrying a
creative in the exact format the SELLER advertises therefore fails.

Residual left open by GH #1802's ingest gate: that gate is INGEST-time only
and gates ``sync_creatives``, not this RE-DIAL at ``create_media_buy`` time.

Conformance storyboard: ungraded (no scenario in ``dist/compliance/3.1.1/``
exercises an adapter-provided creative format on the create_media_buy path).
"""

from __future__ import annotations

import pytest

from tests.factories import AuthorizedPropertyFactory, CreativeFactory
from tests.harness.media_buy_create import MediaBuyCreateEnv
from tests.harness.transport import Transport
from tests.integration.media_buy_helpers import _make_create_request

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_ALL_TRANSPORTS = [Transport.IMPL, Transport.A2A, Transport.REST, Transport.MCP]

_BROADSTREET_FORMAT_ID = "broadstreet_display"


class TestAdapterFormatCreativeSurvivesTheReDial:
    """A creative in an adapter-served format must not be refused for lacking a
    format spec no dialled agent could ever have advertised.

    Today this fails: ``_get_format_spec_sync`` is called with no
    ``is_dialled_agent_url``-style guard, so the fetch is attempted, resolves
    to ``None`` (nothing in the reference/agent catalog knows a
    ``broadstreet_*`` id), and the creative is rejected as "unknown format".
    """

    @pytest.mark.parametrize("transport", _ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_broadstreet_format_creative_is_accepted(self, integration_db, transport):
        with MediaBuyCreateEnv() as env:
            tenant, principal = env.setup_default_data(human_review_required=False)
            AuthorizedPropertyFactory(tenant=tenant)
            agent_url = f"broadstreet://{tenant.tenant_id}"
            product, _pricing_option = env.setup_product_chain(
                tenant,
                format_ids=[{"agent_url": agent_url, "id": _BROADSTREET_FORMAT_ID}],
            )
            CreativeFactory(
                tenant=tenant,
                principal=principal,
                creative_id="c_broadstreet",
                format=_BROADSTREET_FORMAT_ID,
                agent_url=agent_url,
                data={"url": "https://example.com/ad.jpg", "width": 300, "height": 250},
            )

            result = env.call_via(
                transport,
                req=_make_create_request(
                    packages=[
                        {
                            "product_id": product.product_id,
                            "budget": 5000.0,
                            "pricing_option_id": "cpm_usd_fixed",
                            "creative_ids": ["c_broadstreet"],
                        }
                    ]
                ),
            )

            assert not result.is_error, (
                "a creative whose agent_url is an adapter-served pseudo-URL must be served "
                "in-process, not judged as a fetchable creative-agent format. Got: "
                f"{result.error_envelope_or_none()!r}"
            )


class TestReDialOfBuyerProvenanceUrlIsNotOperatorMisclassified:
    """A stored (buyer-provenance) THIRD-PARTY agent_url refused at re-dial time must
    surface as a buyer-correctable error, not a seller CONFIGURATION_ERROR.

    ``_get_format_spec_sync`` (media_buy_create.py) previously called
    ``fetch_format_spec`` with no ``field``, so ``get_formats_for_agent`` always
    took the OPERATOR path for a re-dial — CONFIGURATION_ERROR/terminal — even
    for a url the buyer chose at their own prior sync_creatives call, which
    GH #1802 already classifies INVALID_REQUEST/correctable at ingest for
    the identical url. Passing ``field=`` routes a refusal through the seam's
    counterparty-aware path instead, UNLESS the url is also the tenant's actual
    operator agent (verified unaffected: ``_is_operator_agent`` still wins).

    Uses the REAL registry/seam (only ``_get_format_spec_sync``'s mock
    ``side_effect`` is swapped for the real ``fetch_format_spec`` — everything
    downstream of it, including ``call_mcp_tool``'s ``validate_url``, is
    real) so the refusal is genuine, not injected.
    """

    @pytest.mark.parametrize("transport", _ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_refused_third_party_agent_url_is_buyer_correctable(self, integration_db, transport):
        from src.core.format_resolver import fetch_format_spec

        third_party_agent_url = "https://169.254.169.254"

        with MediaBuyCreateEnv() as env:
            tenant, principal = env.setup_default_data(human_review_required=False)
            AuthorizedPropertyFactory(tenant=tenant)
            product, _pricing_option = env.setup_product_chain(
                tenant,
                format_ids=[{"agent_url": third_party_agent_url, "id": "display_300x250_image"}],
            )
            CreativeFactory(
                tenant=tenant,
                principal=principal,
                creative_id="c_third_party",
                format="display_300x250_image",
                agent_url=third_party_agent_url,
                data={"url": "https://example.com/ad.jpg", "width": 300, "height": 250},
            )

            # Restore the REAL fetch path (only the mock indirection is bypassed;
            # this still hits the real CreativeAgentRegistry and the real seam).
            env.mock["format_spec"].side_effect = fetch_format_spec

            result = env.call_via(
                transport,
                req=_make_create_request(
                    packages=[
                        {
                            "product_id": product.product_id,
                            "budget": 5000.0,
                            "pricing_option_id": "cpm_usd_fixed",
                            "creative_ids": ["c_third_party"],
                        }
                    ]
                ),
            )

            assert result.is_error, (
                f"a refused third-party creative-agent url must fail create_media_buy: {result.payload!r}"
            )
            envelope = result.error_envelope()
            assert envelope is not None, f"expected an error envelope, got {result!r}"
            code = envelope.get("adcp_error", envelope).get("code")
            recovery = envelope.get("adcp_error", envelope).get("recovery")
            assert code != "CONFIGURATION_ERROR", (
                f"errors[0].code={code!r} — a url the BUYER chose (at their own prior sync_creatives "
                "call) is not a SELLER misconfiguration; this is the operator misclassification the "
                f"ticket names. Full envelope: {envelope!r}"
            )
            assert recovery != "terminal", (
                f"errors[0].recovery={recovery!r} — the buyer can fix this by syncing a different "
                f"agent_url; terminal wrongly tells them nothing they send can help. Envelope: {envelope!r}"
            )
