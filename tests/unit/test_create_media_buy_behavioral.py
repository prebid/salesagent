"""Behavioral tests for create_media_buy transport boundary serialization.

Covers the push_notification_config serialization obligations: both MCP and A2A
wrappers must use model_dump(mode='json') so that Pydantic v2 AnyUrl fields and
enum instances are converted to plain Python strings before reaching _impl and
SQLAlchemy String columns.

Also covers brand propagation (Change 5): to_brand_reference() must convert
plain brand strings to AdCP BrandRef-shaped dicts (bare hostname, no scheme/path).

The media_buy_brand propagation obligation (that _create_media_buy_impl forwards
req.brand to process_and_upload_package_creatives) lives in the integration
sibling, tests/integration/test_create_media_buy_behavioral.py, where the
MediaBuyCreateEnv harness drives the real pipeline instead of hand-rolled mocks.

Obligation IDs:
  UC-002-TRANSPORT-PNC-SERIALIZATION-01  (MCP wrapper)
  UC-002-TRANSPORT-PNC-SERIALIZATION-02  (A2A wrapper)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from adcp.types import BrandReference

from src.core.schema_helpers import to_brand_reference
from tests.helpers.create_media_buy_capture import capture_a2a_forwarded_pnc, capture_mcp_forwarded_pnc


class TestMCPWrapperPncJsonSerialization:
    """The typed model reaches ``_impl``, and the values it becomes are plain.

    gh-#1377 is the regression these obligations exist for: a pydantic ``AnyUrl``
    (or an ``AuthenticationScheme`` enum) reaching a SQLAlchemy ``String`` column
    raises ``StatementError`` at flush. The RISK is unchanged. What Epic D lane
    C3 changed is WHERE it is prevented.

    Before: each transport wrapper did ``model_dump(mode="json")`` and handed
    ``_impl`` a dict, so the conversion was a step every wrapper had to remember —
    and the A2A wrapper's ``else`` branch forgot it entirely, forwarding whatever
    raw dict the buyer sent.
    After: the wrapper forwards the TYPED model, and
    ``ValidatedWebhookRegistration`` performs the conversion once, at the one
    boundary where wire types become stored primitives. So these cases now assert
    the model arrives typed AND that what persistence receives is plain — which is
    the actual obligation, stated at the layer that now owns it.
    """

    @pytest.mark.asyncio
    async def test_mcp_wrapper_url_is_plain_str_not_anyurl(self):
        """Covers: UC-002-TRANSPORT-PNC-SERIALIZATION-01"""
        from adcp import PushNotificationConfig

        from src.core.webhooks.registration import accept_push_notification_config

        pnc = PushNotificationConfig(
            url="https://buyer.example.com/webhook",
            authentication={"credentials": "a" * 32, "schemes": ["Bearer"]},
        )
        forwarded = await capture_mcp_forwarded_pnc(pnc)

        assert forwarded is not None, "MCP wrapper did not forward push_notification_config to _impl"
        assert isinstance(forwarded, PushNotificationConfig), (
            f"_impl must receive the typed model, got {type(forwarded).__name__}"
        )

        url = accept_push_notification_config(forwarded).to_columns()["url"]
        assert type(url) is str, (
            f"the url written to a SQLAlchemy String column must be a PLAIN str, got "
            f"{type(url).__name__!r} — a pydantic AnyUrl here is gh-#1377 at flush time"
        )
        assert url == "https://buyer.example.com/webhook", f"url value mismatch: {url!r}"

    @pytest.mark.asyncio
    async def test_mcp_wrapper_enum_schemes_are_plain_strings(self):
        """Covers: UC-002-TRANSPORT-PNC-SERIALIZATION-01"""
        from adcp import PushNotificationConfig

        from src.core.webhooks.registration import accept_push_notification_config

        pnc = PushNotificationConfig(
            url="https://buyer.example.com/webhook",
            authentication={"credentials": "a" * 32, "schemes": ["Bearer"]},
        )
        forwarded = await capture_mcp_forwarded_pnc(pnc)
        assert forwarded is not None

        columns = accept_push_notification_config(forwarded).to_columns()
        scheme = columns["authentication_type"]
        assert type(scheme) is str, (
            f"authentication_type must be a PLAIN str, got {type(scheme).__name__!r} — "
            f"AuthenticationScheme is a str SUBCLASS, so it persists but leaks an enum "
            f"into the DB and JSONB layers"
        )
        assert scheme == "Bearer", f"scheme value mismatch: {scheme!r}"


class TestA2AWrapperPncJsonSerialization:
    """Same obligation on the A2A path, which additionally COERCES a raw dict.

    The A2A wrapper used to pass a raw dict straight through — the untyped seam
    Epic D lanes 1-3 traced. It now coerces through the pinned model, so a
    document the schema forbids is refused instead of stored.
    """

    @pytest.mark.asyncio
    async def test_a2a_wrapper_url_is_plain_str_not_anyurl(self):
        """Covers: UC-002-TRANSPORT-PNC-SERIALIZATION-02"""
        from adcp import PushNotificationConfig

        from src.core.webhooks.registration import accept_push_notification_config

        pnc = PushNotificationConfig(
            url="https://buyer.example.com/webhook",
            authentication={"credentials": "a" * 32, "schemes": ["Bearer"]},
        )
        forwarded = await capture_a2a_forwarded_pnc(pnc)

        assert forwarded is not None, "A2A wrapper did not forward push_notification_config to _impl"
        assert isinstance(forwarded, PushNotificationConfig), (
            f"_impl must receive the typed model, got {type(forwarded).__name__}"
        )

        url = accept_push_notification_config(forwarded).to_columns()["url"]
        assert type(url) is str, (
            f"the url written to a SQLAlchemy String column must be a PLAIN str, got {type(url).__name__!r} — gh-#1377"
        )
        assert url == "https://buyer.example.com/webhook", f"url value mismatch: {url!r}"

    @pytest.mark.asyncio
    async def test_a2a_wrapper_coerces_a_raw_dict_to_the_typed_model(self):
        """Covers: UC-002-TRANSPORT-PNC-SERIALIZATION-02

        This case INVERTED in Epic D lane C3, deliberately. It previously asserted
        the A2A wrapper passes a raw dict through UNCHANGED — which is precisely
        the untyped hole that let a schema-invalid registration reach ``_impl``,
        be stored, and then never deliver. The wrapper now coerces, so the buyer's
        dict becomes the pinned model or is refused by name.
        """
        pnc_dict = {
            "url": "https://buyer.example.com/webhook",
            "authentication": {"credentials": "a" * 32, "schemes": ["Bearer"]},
        }
        forwarded = await capture_a2a_forwarded_pnc(pnc_dict)

        from adcp import PushNotificationConfig

        assert forwarded is not None
        assert isinstance(forwarded, PushNotificationConfig), (
            f"the A2A wrapper must COERCE a raw dict, not forward it — got {type(forwarded).__name__}"
        )
        assert str(forwarded.url) == "https://buyer.example.com/webhook"
        assert [str(s) for s in forwarded.authentication.schemes] == ["Bearer"]

    @pytest.mark.asyncio
    async def test_a2a_wrapper_enum_schemes_are_plain_strings(self):
        """Covers: UC-002-TRANSPORT-PNC-SERIALIZATION-02"""
        from adcp import PushNotificationConfig

        from src.core.webhooks.registration import accept_push_notification_config

        pnc = PushNotificationConfig(
            url="https://buyer.example.com/webhook",
            authentication={"credentials": "a" * 32, "schemes": ["Bearer"]},
        )
        forwarded = await capture_a2a_forwarded_pnc(pnc)
        assert forwarded is not None

        scheme = accept_push_notification_config(forwarded).to_columns()["authentication_type"]
        assert type(scheme) is str, f"authentication_type must be a PLAIN str, got {type(scheme).__name__!r}"
        assert scheme == "Bearer", f"scheme value mismatch: {scheme!r}"


class TestToBrandReference:
    """``to_brand_reference`` is the ONE str/dict/model → BrandReference converter.

    One home for the converter's contract, because there is one converter: the
    creative-build path and ``create_media_buy``'s request builder both route
    through it (``media_buy_create._build_create_media_buy_request`` no longer
    constructs ``BrandReference(domain=brand)`` raw), so scheme-bearing/uppercase
    shorthand is accepted identically on both.

    ``brand-ref.json @ 3.1.1`` requires ``domain`` to be a bare hostname — no
    scheme, no path, no query, no fragment — so the converter strips every URL
    component and lowercases the host. It returns a TYPED ``BrandReference``, not
    a loose dict: the brand stays typed end-to-end inside the application and is
    serialized only at the DB/SDK boundary.
    """

    @pytest.mark.parametrize(
        "raw,expected_domain",
        [
            pytest.param("example.com", "example.com", id="bare-domain"),
            pytest.param("https://example.com", "example.com", id="https-scheme"),
            pytest.param("http://example.com", "example.com", id="http-scheme"),
            pytest.param("https://example.com/path/to/page", "example.com", id="path"),
            pytest.param("https://example.com/path?q=1&foo=bar", "example.com", id="query"),
            pytest.param("https://example.com/page#section", "example.com", id="fragment"),
            pytest.param("https://example.com/path?q=1#anchor", "example.com", id="all-components"),
            pytest.param("https://Example.COM/Path", "example.com", id="uppercase-host"),
            pytest.param("https://ads.example.com/campaign", "ads.example.com", id="subdomain-preserved"),
            pytest.param({"domain": "acme.com"}, "acme.com", id="dict-input"),
            pytest.param(BrandReference(domain="acme.com"), "acme.com", id="model-input"),
        ],
    )
    def test_normalizes_to_bare_lowercase_domain(self, raw, expected_domain):
        result = to_brand_reference(raw)

        assert isinstance(result, BrandReference), "the converter returns a typed BrandReference, not a dict"
        assert result.domain == expected_domain

    def test_invalid_dict_raises_typed_correctable_error(self):
        """A malformed dict brand raises AdCPValidationError (correctable), not a raw
        pydantic ValidationError crash.
        """
        from src.core.exceptions import AdCPValidationError

        with pytest.raises(AdCPValidationError) as exc_info:
            to_brand_reference({"domain": 12345})  # wrong type — not coercible to str

        assert exc_info.value.recovery == "correctable"

    def test_media_buy_create_raw_construction_uses_same_converter(self):
        """media_buy_create._build_create_media_buy_request routes brand through
        to_brand_reference(), matching the creative-build path's normalization —
        pins the "one converter" invariant against regressing to a raw
        BrandReference(domain=brand) construction.
        """
        from src.core.tools.media_buy_create import _build_create_media_buy_request

        req = _build_create_media_buy_request(
            brand="https://Example.COM/path",
            packages=None,
            start_time="asap",
            end_time=(datetime.now(UTC) + timedelta(days=30)).isoformat(),
            po_number=None,
            reporting_webhook=None,
            context=None,
            ext=None,
            account=None,
            idempotency_key="test-idempotency-key-0001",
            paused=None,
        )
        assert req.brand is not None
        assert req.brand.domain == "example.com"
