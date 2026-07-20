"""Behavioral tests for create_media_buy transport boundary serialization.

Covers the push_notification_config serialization obligations: both MCP and A2A
wrappers must use model_dump(mode='json') so that Pydantic v2 AnyUrl fields and
enum instances are converted to plain Python strings before reaching _impl and
SQLAlchemy String columns.

Also covers brand propagation (Change 5): _brand_str_to_ref() must convert
plain brand strings to AdCP BrandRef-shaped dicts (bare hostname, no scheme/path).

Also covers media_buy_brand propagation (Bug 4 fix): _create_media_buy_impl must
pass req.brand as media_buy_brand to process_and_upload_package_creatives so
adapters can read brand.domain from stored creative data.

Obligation IDs:
  UC-002-TRANSPORT-PNC-SERIALIZATION-01  (MCP wrapper)
  UC-002-TRANSPORT-PNC-SERIALIZATION-02  (A2A wrapper)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.core.helpers.creative_helpers import _brand_str_to_ref
from src.core.schemas import CreateMediaBuyRequest
from tests.factories import PrincipalFactory
from tests.helpers.create_media_buy_capture import capture_a2a_forwarded_pnc, capture_mcp_forwarded_pnc
from tests.unit._media_buy_mock_helpers import future as _future, mock_pricing_option

# ---------------------------------------------------------------------------
# Shared helpers for TestMediaBuyBrandPropagation
# ---------------------------------------------------------------------------


def _make_request(**overrides) -> CreateMediaBuyRequest:
    """Build a minimal valid CreateMediaBuyRequest with one inline-creative package."""
    defaults = {
        "brand": {"domain": "acme.com"},
        "start_time": _future(1),
        "end_time": _future(8),
        "idempotency_key": "test-idempotency-key-0001",
        "packages": [
            {
                "product_id": "prod_1",
                "budget": 5000.0,
                "pricing_option_id": "cpm_usd_fixed",
                "creatives": [
                    {
                        "creative_id": "inline_1",
                        "name": "Test Ad",
                        "format_id": {
                            "agent_url": "https://creative.example.com/",
                            "id": "display_300x250_image",
                        },
                        "assets": {"banner_image": {"url": "https://example.com/ad.png"}},
                        "variants": [],
                    }
                ],
            }
        ],
    }
    defaults.update(overrides)
    return CreateMediaBuyRequest(**defaults)


def _mock_product(product_id: str = "prod_1", currency: str = "USD") -> MagicMock:
    """Create a mock DB Product with a single pricing option."""
    product = MagicMock()
    product.product_id = product_id
    product.pricing_options = [mock_pricing_option(currency)]
    return product


def _make_identity():
    """Build a minimal ResolvedIdentity for unit tests via PrincipalFactory."""
    return PrincipalFactory.make_identity(
        principal_id="p_test",
        tenant_id="t_test",
        tenant={
            "tenant_id": "t_test",
            "name": "Test Tenant",
            "subdomain": "test",
            "approval_mode": "auto-approve",
        },
    )


class TestMCPWrapperPncJsonSerialization:
    """MCP wrapper must serialize PushNotificationConfig with mode='json'.

    Regression: plain model_dump() preserves AnyUrl objects that SQLAlchemy
    String columns cannot coerce, raising StatementError at flush.
    """

    @pytest.mark.asyncio
    async def test_mcp_wrapper_url_is_plain_str_not_anyurl(self):
        """Covers: UC-002-TRANSPORT-PNC-SERIALIZATION-01

        When the MCP wrapper serializes PushNotificationConfig to a dict,
        the url field must be a plain str (not a Pydantic AnyUrl object) so
        that SQLAlchemy String columns can persist it without StatementError.
        """
        from adcp import PushNotificationConfig

        pnc = PushNotificationConfig(
            url="https://buyer.example.com/webhook",
            authentication={"credentials": "a" * 32, "schemes": ["Bearer"]},
        )
        forwarded = await capture_mcp_forwarded_pnc(pnc)

        assert forwarded is not None, "MCP wrapper did not forward push_notification_config to _impl"
        assert isinstance(forwarded, dict), f"push_notification_config must be a dict, got {type(forwarded).__name__}"

        url = forwarded.get("url")
        assert isinstance(url, str), (
            f"url must be a plain str after model_dump(mode='json'), got {type(url).__name__!r}. "
            "This indicates model_dump() was used instead of model_dump(mode='json'), "
            "which preserves AnyUrl objects and causes SQLAlchemy StatementError."
        )
        assert url == "https://buyer.example.com/webhook", f"url value mismatch: {url!r}"

    @pytest.mark.asyncio
    async def test_mcp_wrapper_enum_schemes_are_plain_strings(self):
        """Covers: UC-002-TRANSPORT-PNC-SERIALIZATION-01

        When the MCP wrapper serializes PushNotificationConfig, enum fields
        such as authentication.schemes must be plain strings, not enum instances,
        so SQLAlchemy can persist them without coercion errors.
        """
        from adcp import PushNotificationConfig

        pnc = PushNotificationConfig(
            url="https://buyer.example.com/webhook",
            authentication={"credentials": "a" * 32, "schemes": ["Bearer"]},
        )
        forwarded = await capture_mcp_forwarded_pnc(pnc)
        assert forwarded is not None

        auth = forwarded.get("authentication", {})
        schemes = auth.get("schemes", [])
        for scheme in schemes:
            assert isinstance(scheme, str), (
                f"authentication.schemes entries must be plain str after model_dump(mode='json'), "
                f"got {type(scheme).__name__!r} — enum instances cause SQLAlchemy coercion errors."
            )


class TestA2AWrapperPncJsonSerialization:
    """A2A wrapper must serialize PushNotificationConfig with mode='json'.

    Regression: plain model_dump() preserves AnyUrl objects that SQLAlchemy
    String columns cannot coerce, raising StatementError at flush.
    """

    @pytest.mark.asyncio
    async def test_a2a_wrapper_url_is_plain_str_not_anyurl(self):
        """Covers: UC-002-TRANSPORT-PNC-SERIALIZATION-02

        When the A2A wrapper (create_media_buy_raw) receives a PushNotificationConfig
        model instance and serializes it to a dict, the url field must be a plain str
        (not a Pydantic AnyUrl object) so SQLAlchemy String columns can persist it.
        """
        from adcp import PushNotificationConfig

        pnc = PushNotificationConfig(
            url="https://buyer.example.com/webhook",
            authentication={"credentials": "a" * 32, "schemes": ["Bearer"]},
        )
        forwarded = await capture_a2a_forwarded_pnc(pnc)

        assert forwarded is not None, "A2A wrapper did not forward push_notification_config to _impl"
        assert isinstance(forwarded, dict), f"push_notification_config must be a dict, got {type(forwarded).__name__}"

        url = forwarded.get("url")
        assert isinstance(url, str), (
            f"url must be a plain str after model_dump(mode='json'), got {type(url).__name__!r}. "
            "This indicates model_dump() was used instead of model_dump(mode='json'), "
            "which preserves AnyUrl objects and causes SQLAlchemy StatementError."
        )
        assert url == "https://buyer.example.com/webhook", f"url value mismatch: {url!r}"

    @pytest.mark.asyncio
    async def test_a2a_wrapper_passthrough_dict_unchanged(self):
        """Covers: UC-002-TRANSPORT-PNC-SERIALIZATION-02

        When the A2A wrapper receives push_notification_config already as a plain
        dict (the normal A2A JSON path), it must pass it through unchanged without
        re-serializing it.
        """
        pnc_dict = {
            "url": "https://buyer.example.com/webhook",
            "authentication": {"credentials": "a" * 32, "schemes": ["Bearer"]},
        }
        forwarded = await capture_a2a_forwarded_pnc(pnc_dict)

        assert forwarded is not None
        assert isinstance(forwarded, dict)
        assert forwarded["url"] == "https://buyer.example.com/webhook"
        assert forwarded["authentication"]["schemes"] == ["Bearer"]

    @pytest.mark.asyncio
    async def test_a2a_wrapper_enum_schemes_are_plain_strings(self):
        """Covers: UC-002-TRANSPORT-PNC-SERIALIZATION-02

        When the A2A wrapper serializes a PushNotificationConfig model, enum
        fields such as authentication.schemes must be plain strings.
        """
        from adcp import PushNotificationConfig

        pnc = PushNotificationConfig(
            url="https://buyer.example.com/webhook",
            authentication={"credentials": "a" * 32, "schemes": ["Bearer"]},
        )
        forwarded = await capture_a2a_forwarded_pnc(pnc)
        assert forwarded is not None

        auth = forwarded.get("authentication", {})
        schemes = auth.get("schemes", [])
        for scheme in schemes:
            assert isinstance(scheme, str), (
                f"authentication.schemes entries must be plain str after model_dump(mode='json'), "
                f"got {type(scheme).__name__!r} — enum instances cause SQLAlchemy coercion errors."
            )


class TestBrandStrToRef:
    """_brand_str_to_ref converts plain brand strings to typed BrandReference (Change 5).

    AdCP 3.1 BrandReference.domain requires a bare hostname (no scheme, no path).
    The helper must strip URL scheme and path components so adapters can read
    ``brand.domain`` from stored creative data.

    Returns a typed BrandReference — not a loose dict — so the brand stays typed
    end-to-end inside the application (serialization to dict happens only at the
    DB/SDK boundary).
    """

    def test_plain_domain_unchanged(self):
        """A bare domain string is returned as-is in the domain field."""
        result = _brand_str_to_ref("example.com")
        assert result.domain == "example.com"

    def test_https_scheme_stripped(self):
        """https:// scheme is stripped, leaving only the hostname."""
        result = _brand_str_to_ref("https://example.com")
        assert result.domain == "example.com"

    def test_http_scheme_stripped(self):
        """http:// scheme is stripped, leaving only the hostname."""
        result = _brand_str_to_ref("http://example.com")
        assert result.domain == "example.com"

    def test_path_stripped(self):
        """URL path is stripped — only the hostname is kept."""
        result = _brand_str_to_ref("https://example.com/path/to/page")
        assert result.domain == "example.com"

    def test_query_string_stripped(self):
        """Query string is stripped — only the hostname is kept."""
        result = _brand_str_to_ref("https://example.com/path?q=1&foo=bar")
        assert result.domain == "example.com"

    def test_fragment_stripped(self):
        """URL fragment is stripped — only the hostname is kept."""
        result = _brand_str_to_ref("https://example.com/page#section")
        assert result.domain == "example.com"

    def test_full_url_all_components_stripped(self):
        """Full URL with scheme, path, query, and fragment → bare hostname."""
        result = _brand_str_to_ref("https://example.com/path?q=1#anchor")
        assert result.domain == "example.com"

    def test_result_is_brand_reference(self):
        """Result is always a typed BrandReference, not a loose dict."""
        from adcp.types import BrandReference

        result = _brand_str_to_ref("https://example.com")
        assert isinstance(result, BrandReference)
        assert result.domain == "example.com"

    def test_domain_is_lowercase(self):
        """Domain is lowercased for consistent comparison."""
        result = _brand_str_to_ref("https://Example.COM/Path")
        assert result.domain == "example.com"

    def test_subdomain_preserved(self):
        """Subdomains are preserved in the domain field."""
        result = _brand_str_to_ref("https://ads.example.com/campaign")
        assert result.domain == "ads.example.com"


class TestToBrandReferenceNormalization:
    """to_brand_reference() is the single str/dict/model → BrandReference converter.

    Routes create_media_buy's raw ``BrandReference(domain=brand)`` construction
    (media_buy_create.py) through the same normalizer the creative-build path
    uses, so scheme-bearing/uppercase shorthand is accepted on both paths
    instead of raising an unhandled ValidationError on this one.
    """

    def test_scheme_bearing_string_normalized(self):
        """A scheme-bearing string ("https://Example.COM/path") no longer raises —
        it is normalized to a bare lowercase hostname like the creative path.
        """
        from src.core.schema_helpers import to_brand_reference

        result = to_brand_reference("https://Example.COM/path")
        assert result is not None
        assert result.domain == "example.com"

    def test_bare_domain_string_passthrough(self):
        """A bare domain string is accepted unchanged (already spec-compliant)."""
        from src.core.schema_helpers import to_brand_reference

        result = to_brand_reference("acme.com")
        assert result is not None
        assert result.domain == "acme.com"

    def test_dict_input_still_validated(self):
        """A dict brand is still routed through BrandReference validation."""
        from src.core.schema_helpers import to_brand_reference

        result = to_brand_reference({"domain": "acme.com"})
        assert result is not None
        assert result.domain == "acme.com"

    def test_invalid_dict_raises_typed_correctable_error(self):
        """A malformed dict brand raises AdCPValidationError (correctable), not a raw
        pydantic ValidationError crash.
        """
        from src.core.exceptions import AdCPValidationError
        from src.core.schema_helpers import to_brand_reference

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
        )
        assert req.brand is not None
        assert req.brand.domain == "example.com"


def _make_brand_propagation_env(product: MagicMock) -> tuple:
    """Build the shared mock scaffolding for TestMediaBuyBrandPropagation tests.

    Returns ``(mock_uow, mock_principal, mock_ctx_manager)`` — the three objects
    that callers need to configure session-level side-effects or make assertions.
    The UoW, product repo, currency repo, principal, and context manager are wired
    together so that ``_create_media_buy_impl`` can reach the
    ``process_and_upload_package_creatives`` call without hitting real I/O.
    """
    mock_product_repo = MagicMock()
    mock_product_repo.get_by_ids.return_value = [product]
    mock_product_repo.get_by_id.return_value = product

    mock_currency_repo = MagicMock()
    mock_currency_repo.get_by_currency.return_value = None

    mock_uow = MagicMock()
    mock_uow.__enter__ = MagicMock(return_value=mock_uow)
    mock_uow.__exit__ = MagicMock(return_value=False)
    mock_uow.products = mock_product_repo
    mock_uow.currency_limits = mock_currency_repo

    mock_principal = MagicMock()
    mock_principal.principal_id = "p_test"
    mock_principal.name = "Test Principal"

    mock_ctx_manager = MagicMock()
    mock_ctx_manager.create_context.return_value = MagicMock(context_id="ctx_test")
    mock_ctx_manager.create_workflow_step.return_value = MagicMock(step_id="step_test")

    return mock_uow, mock_principal, mock_ctx_manager


def _configure_brand_propagation_session(mock_uow: MagicMock, product: MagicMock) -> None:
    """Wire session-level scalars so the pipeline can pass currency and adapter checks.

    Sets up ``mock_uow.session.scalars`` to return a permissive CurrencyLimit on the
    first call and ``None`` on the second (no AdapterConfig → no GAM currency restriction).
    """
    mock_currency_limit = MagicMock()
    mock_currency_limit.min_package_budget = None
    mock_currency_limit.max_daily_package_spend = None
    mock_currency_limit.currency_code = "USD"
    mock_uow.session = MagicMock()
    mock_uow.session.scalars.return_value.first.side_effect = [mock_currency_limit, None]
    mock_uow.session.scalars.return_value.all.return_value = [product]


class TestMediaBuyBrandPropagation:
    """Bug 4 fix: req.brand propagated as media_buy_brand to process_and_upload_package_creatives.

    The fix at media_buy_create.py passes ``req.brand`` (the Pydantic model) as
    the ``media_buy_brand`` kwarg to ``process_and_upload_package_creatives``,
    which serializes it to a plain dict and forwards it to ``_sync_creatives_impl``
    so adapters can read ``brand.domain`` from stored creative data.
    """

    @pytest.mark.asyncio
    async def test_process_and_upload_called_with_media_buy_brand(self):
        """When req.brand is set, process_and_upload_package_creatives receives media_buy_brand.

        Anchors: media_buy_create.py — ``media_buy_brand=req.brand``
        """
        from src.core.tools.media_buy_create import _create_media_buy_impl

        req = _make_request(brand={"domain": "acme.com"})
        identity = _make_identity()
        product = _mock_product("prod_1")
        mock_uow, mock_principal, mock_ctx_manager = _make_brand_propagation_env(product)

        with (
            patch("src.core.tools.media_buy_create.process_and_upload_package_creatives") as mock_upload,
            patch("src.core.tools.media_buy_create.get_adapter") as mock_adapter_fn,
            patch("src.core.tools.media_buy_create.get_slack_notifier"),
            patch("src.core.tools.media_buy_create.activity_feed"),
            patch("src.core.tools.media_buy_create.get_audit_logger"),
            patch("src.core.tools.media_buy_create.validate_setup_complete"),
            patch("src.core.tools.media_buy_create.resolve_principal_or_raise", return_value=mock_principal),
            patch("src.core.tools.media_buy_create.get_context_manager", return_value=mock_ctx_manager),
            patch("src.core.tools.media_buy_create._lookup_cached_replay", return_value=None),
            patch("src.core.database.repositories.MediaBuyUoW", return_value=mock_uow),
        ):
            mock_upload.return_value = (req.packages, {})
            mock_adapter = MagicMock()
            mock_adapter.manual_approval_required = True
            mock_adapter.manual_approval_operations = ["create_media_buy"]
            mock_adapter_fn.return_value = mock_adapter
            _configure_brand_propagation_session(mock_uow, product)

            try:
                await _create_media_buy_impl(req=req, identity=identity)
            except Exception:
                pass  # Downstream failures are fine — we only care about the upload call

        # Verify process_and_upload_package_creatives was called with media_buy_brand=req.brand.
        # media_buy_brand receives req.brand (the Pydantic model); serialization to dict
        # happens inside process_and_upload_package_creatives, not in _impl.
        assert mock_upload.called, (
            "process_and_upload_package_creatives was not called — "
            "_create_media_buy_impl may have exited before reaching the upload step"
        )
        call_kwargs = mock_upload.call_args.kwargs
        assert "media_buy_brand" in call_kwargs, (
            "process_and_upload_package_creatives must receive media_buy_brand kwarg"
        )
        assert call_kwargs["media_buy_brand"] == req.brand, (
            f"media_buy_brand must be req.brand (the Pydantic model), got {call_kwargs['media_buy_brand']!r}"
        )

    @pytest.mark.asyncio
    async def test_process_and_upload_called_with_none_brand_when_no_brand(self):
        """When req.brand is None, process_and_upload_package_creatives receives media_buy_brand=None.

        Anchors: media_buy_create.py — ``media_buy_brand=req.brand``
        """
        from src.core.tools.media_buy_create import _create_media_buy_impl

        req = _make_request()
        # Override brand to None after construction (schema may default it)
        object.__setattr__(req, "brand", None)
        identity = _make_identity()
        product = _mock_product("prod_1")
        mock_uow, mock_principal, mock_ctx_manager = _make_brand_propagation_env(product)

        with (
            patch("src.core.tools.media_buy_create.process_and_upload_package_creatives") as mock_upload,
            patch("src.core.tools.media_buy_create.get_adapter") as mock_adapter_fn,
            patch("src.core.tools.media_buy_create.get_slack_notifier"),
            patch("src.core.tools.media_buy_create.activity_feed"),
            patch("src.core.tools.media_buy_create.get_audit_logger"),
            patch("src.core.tools.media_buy_create.validate_setup_complete"),
            patch("src.core.tools.media_buy_create.resolve_principal_or_raise", return_value=mock_principal),
            patch("src.core.tools.media_buy_create.get_context_manager", return_value=mock_ctx_manager),
            patch("src.core.tools.media_buy_create._lookup_cached_replay", return_value=None),
            patch("src.core.database.repositories.MediaBuyUoW", return_value=mock_uow),
        ):
            mock_upload.return_value = (req.packages, {})
            mock_adapter = MagicMock()
            mock_adapter.manual_approval_required = True
            mock_adapter.manual_approval_operations = ["create_media_buy"]
            mock_adapter_fn.return_value = mock_adapter
            _configure_brand_propagation_session(mock_uow, product)

            try:
                await _create_media_buy_impl(req=req, identity=identity)
            except Exception:
                pass  # Downstream failures are fine — we only care about the upload call

        # Verify process_and_upload_package_creatives was called with media_buy_brand=None.
        assert mock_upload.called, (
            "process_and_upload_package_creatives was not called — "
            "_create_media_buy_impl may have exited before reaching the upload step"
        )
        call_kwargs = mock_upload.call_args.kwargs
        assert "media_buy_brand" in call_kwargs, (
            "process_and_upload_package_creatives must receive media_buy_brand kwarg even when brand is None"
        )
        assert call_kwargs["media_buy_brand"] is None, (
            f"media_buy_brand must be None when req.brand is None, got {call_kwargs['media_buy_brand']!r}"
        )
