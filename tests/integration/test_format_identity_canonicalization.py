"""One canonical format-identity comparison across all entry tools (#1172).

The creative-vs-product format check exists at four sites (create-time package
check, update_media_buy, sync_creatives assignments, creative_helpers). All four
must agree on ONE canonicalization: SDK canonical form (lowercased host, dropped
default ports) AND transport-suffix tolerance (/mcp, /a2a,
/.well-known/adcp/sales). The same buyer input must never be accepted by one
entry tool and rejected by another.

Reproduction for review finding S1 (#1172 review round): create-time keys on
``format_id_identity`` (canonical, NOT suffix-tolerant, one-directional /mcp
append) while the three sibling sites key on ``normalize_agent_url``
(suffix-tolerant, NOT canonical).
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from src.core.schemas import CreateMediaBuyRequest, CreateMediaBuySuccess
from tests.harness.media_buy_create import MediaBuyCreateEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_TENANT_ID = "formatidenttenant"
_PRINCIPAL_ID = "formatidentprincipal"


def _env(**overrides: Any) -> MediaBuyCreateEnv:
    overrides.setdefault("tenant_id", _TENANT_ID)
    overrides.setdefault("principal_id", _PRINCIPAL_ID)
    overrides.setdefault("human_review_required", False)
    return MediaBuyCreateEnv(**overrides)


def _future(days: int) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).isoformat()


def _make_request(requested_agent_url: str) -> CreateMediaBuyRequest:
    return CreateMediaBuyRequest(
        brand={"domain": "testbrand.com"},
        start_time=_future(1),
        end_time=_future(8),
        idempotency_key=f"int-key-{uuid.uuid4().hex}",
        packages=[
            {
                "product_id": "prod_1",
                "budget": 5000.0,
                "pricing_option_id": "cpm_usd_fixed",
                "format_ids": [{"agent_url": requested_agent_url, "id": "display_300x250"}],
            }
        ],
    )


class TestCreateTimeSuffixTolerance:
    """Create-time package-format check accepts the same URL variants the
    creative-validation sites accept (update_media_buy, sync_creatives,
    creative_helpers all strip /mcp, /a2a and trailing slashes)."""

    @pytest.mark.parametrize(
        ("product_agent_url", "requested_agent_url"),
        [
            # /a2a-suffixed stored URL vs bare requested URL: accepted by
            # creative-assignment validation, must be accepted at create time.
            ("https://creative.adcontextprotocol.org/a2a", "https://creative.adcontextprotocol.org"),
            # bare stored URL vs /a2a-suffixed requested URL (reverse direction).
            ("https://creative.adcontextprotocol.org", "https://creative.adcontextprotocol.org/a2a"),
            # request carrying /mcp vs bare stored URL: the old fallback only
            # appended /mcp to the REQUESTED key, so this direction never matched.
            ("https://creative.adcontextprotocol.org", "https://creative.adcontextprotocol.org/mcp"),
            # host-case variant: SDK canonicalization lowercases the host.
            ("https://CREATIVE.ADCONTEXTPROTOCOL.ORG", "https://creative.adcontextprotocol.org"),
        ],
    )
    def test_url_variant_accepted_at_create_time(self, integration_db, product_agent_url, requested_agent_url):
        req = _make_request(requested_agent_url)
        with _env() as env:
            tenant, _principal = env.setup_default_data()
            env.setup_product_chain(
                tenant,
                format_ids=[{"agent_url": product_agent_url, "id": "display_300x250"}],
            )
            result = env.call_impl(req=req)
        assert isinstance(result.response, CreateMediaBuySuccess), (
            f"create-time format check rejected a matching URL variant: {result.response}"
        )
