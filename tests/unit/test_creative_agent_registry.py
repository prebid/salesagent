"""Unit tests for Creative Agent Registry.

``TestCreativeAgentRegistry`` (build/fetch against a mocked ``adcp.ADCPMultiAgentClient``)
was retired by salesagent-4n88: the OPERATOR agent path no longer constructs
that SDK client at all — it dials through the guarded MCP seam
(``src.core.utils.mcp_client.call_mcp_tool``, reached through
``src.core.utils.operator_mcp.call_operator_mcp_tool``) instead, via
``_fetch_formats_operator``. Its behavioral contracts (auth propagation,
connection-alias routing, error taxonomy) are re-expressed as integration
tests against a real local origin in
``tests/integration/test_creative_agent_operator_seam.py`` — the project's
stated preference over mocking the thing under test's own dependency.
"""

from unittest.mock import patch

import pytest
from pydantic import AnyUrl

from src.core.creative_agent_registry import (
    _KNOWN_ASSET_TYPES,
    CreativeAgentRegistry,
    GenerativeBuildResult,
)
from src.core.exceptions import (
    AdCPServiceUnavailableError,
    AdCPValidationError,
)
from src.core.schemas import FormatId
from tests.factories.creative_asset import build_assets, image_spec

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _seam(*, result: object = None, side_effect: BaseException | None = None, capture: list | None = None):
    """Patch the ONE guarded MCP seam ``build_creative`` dials through.

    ``call_operator_mcp_tool`` (src.core.utils.operator_mcp) is where the request
    leaves this process: IP-pinned, redirect-refusing, and the owner of both
    error mappings. Patching THERE — rather than an SDK client the registry no
    longer constructs — is what keeps these tests pointed at the real call path
    (#1802 retired ``ADCPMultiAgentClient`` for outbound work: adcp 6.6.0 exposes
    no transport injection point, so no egress policy of ours could reach it).

    Args:
        result: the payload the seam returns (a dict, as an MCP tool result is).
        side_effect: raise this instead of returning — the seam's own mapped
            ``AdCPError``, as production would see it.
        capture: if given, each call's ``(agent_url, tool, arguments)`` is
            appended to it.
    """

    async def _call(agent_url, tool, arguments, *, label, **_kwargs):
        if capture is not None:
            capture.append((agent_url, tool, arguments))
        if side_effect is not None:
            raise side_effect
        return result if result is not None else {"status": "draft"}

    return patch("src.core.creative_agent_registry.call_operator_mcp_tool", side_effect=_call)


GENERATIVE_FORMAT = FormatId(agent_url="https://creative.example.com", id="display_300x250_generative")


@pytest.fixture
def dials_the_agent(monkeypatch):
    """Turn OFF testing mode so build_creative actually reaches the seam.

    ``ADCP_TESTING=true`` (set for the whole test session by ``tests/conftest.py``)
    makes every registry method serve checked-in/derived data instead of dialling
    — which is the point in CI, and exactly what the tests below must NOT
    exercise: they grade the request the registry BUILDS and the errors the seam
    translates. Without this they would pass against a branch that never
    constructs a request at all.
    """
    monkeypatch.setenv("ADCP_TESTING", "false")


class TestCacheKeyAcceptsAnyUrl:
    """Regression tests for #1106: _cache_key must accept Pydantic AnyUrl.

    FormatId.agent_url is AnyUrl (not a str subclass in Pydantic v2).
    When GAM line item creation resolves formats, the AnyUrl flows through
    format_resolver → creative_agent_registry._cache_key → yarl.URL().
    yarl.URL() rejects non-str input with TypeError.
    """

    def test_cache_key_accepts_pydantic_anyurl(self):
        """_cache_key must not crash when given AnyUrl instead of str."""
        registry = CreativeAgentRegistry()
        agent_url = AnyUrl("https://creative.adcontextprotocol.org/")
        result = registry._cache_key(agent_url)
        assert result == "https://creative.adcontextprotocol.org"

    def test_cache_key_normalizes_anyurl_same_as_str(self):
        """AnyUrl and equivalent str must produce the same cache key."""
        registry = CreativeAgentRegistry()
        str_key = registry._cache_key("https://creative.adcontextprotocol.org/")
        anyurl_key = registry._cache_key(AnyUrl("https://creative.adcontextprotocol.org/"))
        assert str_key == anyurl_key

    @pytest.mark.asyncio
    async def test_get_format_accepts_anyurl_agent_url(self, monkeypatch):
        """get_format must not crash when agent_url is AnyUrl (GAM line item path)."""
        monkeypatch.delenv("ADCP_TESTING", raising=False)
        registry = CreativeAgentRegistry()

        # Patch _fetch to avoid real HTTP — we only test the cache_key path
        async def mock_fetch(*args, **kwargs):
            return []

        monkeypatch.setattr(registry, "_fetch_formats_operator", mock_fetch)

        result = await registry.get_format(AnyUrl("https://creative.adcontextprotocol.org/"), "display_300x250_image")
        assert result is None  # Not found, but no TypeError


class TestKnownAssetTypes:
    """_KNOWN_ASSET_TYPES includes 'url' (Change 4).

    AdCP 3.1 adds 'url' as a valid asset type for text_ad_search formats.
    The tolerant ingestion must not reject formats that use 'url' assets.
    """

    def test_url_in_known_asset_types(self):
        """'url' must be in _KNOWN_ASSET_TYPES after Change 4."""
        assert "url" in _KNOWN_ASSET_TYPES, (
            "'url' must be in _KNOWN_ASSET_TYPES so formats with url assets "
            "are not rejected by the tolerant ingestion path"
        )

    def test_known_asset_types_is_frozenset(self):
        """_KNOWN_ASSET_TYPES must be a frozenset (immutable, hashable)."""
        assert isinstance(_KNOWN_ASSET_TYPES, frozenset), (
            "_KNOWN_ASSET_TYPES must be a frozenset so it cannot be mutated at runtime"
        )

    def test_known_asset_types_covers_every_enum_member(self):
        """No AssetContentType member may be missing from _KNOWN_ASSET_TYPES.

        A member left out would make the tolerant-ingestion path treat formats
        using it as "unknown additive" and silently DROP them, even though the
        pinned SDK models them.

        The enum is iterated, never listed: an annotation-walk over Format.assets
        collects nothing under the Annotated[…, Discriminator] shape the SDK uses,
        so production derives from AssetContentType — and this test derives from it
        too, so neither needs an edit when AdCP adds an asset type. It still catches
        the regression that matters: replacing the derivation with a partial
        hand-written list.
        """
        from adcp.types import AssetContentType

        missing = {member.value for member in AssetContentType} - set(_KNOWN_ASSET_TYPES)
        assert not missing, (
            f"_KNOWN_ASSET_TYPES is missing AssetContentType member(s) {sorted(missing)} — "
            f"formats using them would be dropped as unknown-additive by _validate_formats_tolerant"
        )

    def test_zip_in_known_asset_types(self):
        """'zip' must be in _KNOWN_ASSET_TYPES.

        'zip' is a valid asset_type Literal on the SDK's individual-asset shapes
        (Assets32/Assets43 in the generated union) but is absent from the
        AssetContentType response enum, so deriving _KNOWN_ASSET_TYPES from the
        enum alone silently drops it.
        """
        assert "zip" in _KNOWN_ASSET_TYPES, (
            "'zip' must be in _KNOWN_ASSET_TYPES — it's a real SDK asset_type Literal "
            "not covered by the AssetContentType enum"
        )

    def test_card_in_known_asset_types(self):
        """'card' must be in _KNOWN_ASSET_TYPES.

        'card' is the asset_type discriminator for RepeatableAssetGroup member
        assets (CardAsset) but is absent from the AssetContentType response enum.
        """
        assert "card" in _KNOWN_ASSET_TYPES, (
            "'card' must be in _KNOWN_ASSET_TYPES — it's a real SDK asset_type Literal "
            "not covered by the AssetContentType enum"
        )

    def test_zip_and_card_union_is_still_necessary(self):
        """The explicit zip/card union must still be earning its place.

        Production unions ``{"zip", "card"}`` on top of the enum precisely because
        AssetContentType omits them. If a pin bump adds either to the enum, that
        union becomes dead code — fail here so it is removed rather than lingering
        as a stale special case.

        (Replaces a hardcoded 17-name snapshot of the whole set: the snapshot
        duplicated the enum listing in the creative schema-compliance obligations
        test, needed an edit on every pin bump, and its stated claim about the
        Format.assets union was wrong — the union discriminates
        ``repeatable_group`` on ``item_type``, not ``asset_type``.)
        """
        from adcp.types import AssetContentType

        enum_values = {member.value for member in AssetContentType}
        overlap = enum_values & {"zip", "card"}
        assert not overlap, (
            f"AssetContentType now includes {sorted(overlap)} — drop it from the explicit "
            f"union in _known_asset_types(), which exists only to cover the enum's omissions"
        )


class TestBuildCreativeRequest:
    """build_creative BUILDS the pinned request and SENDS it through the guarded seam.

    Two separable properties, both required: the payload is the typed AdCP 3.1
    request (idempotency_key, structured target_format_id, validated manifest,
    typed brand), and the dial goes through ``call_operator_mcp_tool`` rather
    than an SDK client whose httpx stack no egress policy of ours can reach.
    """

    @pytest.mark.asyncio
    async def test_build_creative_no_gemini_api_key_param(self, dials_the_agent):
        """build_creative must NOT accept gemini_api_key parameter (Change 3)."""
        import inspect

        sig = inspect.signature(CreativeAgentRegistry().build_creative)
        assert "gemini_api_key" not in sig.parameters, (
            "build_creative must not accept gemini_api_key — generation is the creative agent's job"
        )

    @pytest.mark.asyncio
    async def test_build_creative_takes_domain_values_only(self, dials_the_agent):
        """The signature carries domain values, not pre-built wire objects or dead inputs.

        ``creative_manifest`` is rendered by the registry from ``format_id`` +
        ``assets`` (protocol framing belongs to this adapter), and the pre-3.1
        ``promoted_offerings`` / ``context_id`` arguments are gone: neither has a
        home in ``media-buy/build-creative-request.json @ 3.1.1``, and both were
        accepted-and-ignored, which silently dropped buyer input (#2143).
        """
        import inspect

        params = set(inspect.signature(CreativeAgentRegistry.build_creative).parameters)

        assert {"format_id", "message", "assets", "brand"} <= params
        assert not params & {"creative_manifest", "promoted_offerings", "context_id", "gemini_api_key"}, (
            "build_creative must not accept wire objects or parameters its body never reads"
        )

    @pytest.mark.asyncio
    async def test_request_is_the_pinned_typed_request(self, dials_the_agent):
        """The arguments are a serialized BuildCreativeRequest, not a hand-built dict."""
        calls: list = []
        with _seam(capture=calls):
            await CreativeAgentRegistry().build_creative(format_id=GENERATIVE_FORMAT, message="Build a banner ad")

        assert len(calls) == 1
        agent_url, tool, arguments = calls[0]
        assert tool == "build_creative"
        assert agent_url.startswith("https://creative.example.com")
        # Required on every AdCP 3.1 task request.
        assert arguments["idempotency_key"]
        assert arguments["message"] == "Build a banner ad"
        assert arguments["target_format_id"]["id"] == "display_300x250_generative"
        assert arguments["creative_manifest"]["assets"] == {}

    @pytest.mark.asyncio
    async def test_request_identity_and_manifest_identity_are_one_value(self, dials_the_agent):
        """``target_format_id`` and the manifest's ``format_id`` must be byte-identical.

        Both are rendered from the single ``format_id`` argument. A hand-built
        canonical string next to a pydantic-serialized ``AnyUrl`` (which adds the
        trailing slash for a path-less URL) put two spellings of one agent_url in
        one request — the drift ``core/format-id.json``'s canonicalization MUST
        exists to stop.
        """
        calls: list = []
        with _seam(capture=calls):
            await CreativeAgentRegistry().build_creative(format_id=GENERATIVE_FORMAT, message="Build a banner ad")

        _, _, arguments = calls[0]
        assert arguments["creative_manifest"]["format_id"] == arguments["target_format_id"]

    @pytest.mark.asyncio
    async def test_build_creative_brand_str_converted_to_ref(self, dials_the_agent):
        """A brand string is normalized to the BrandReference shape before the request."""
        calls: list = []
        with _seam(capture=calls):
            await CreativeAgentRegistry().build_creative(
                format_id=GENERATIVE_FORMAT,
                message="Build a banner ad",
                brand="https://advertiser.example.com/brand",
            )

        _, _, arguments = calls[0]
        assert arguments["brand"]["domain"] == "advertiser.example.com"

    @pytest.mark.asyncio
    async def test_build_creative_returns_typed_result(self, dials_the_agent):
        """build_creative returns a typed GenerativeBuildResult, not an untyped dict.

        Callers read ``result.status`` / ``result.creative_output`` — a dict would
        put them back on the stringly-typed ``.get()`` chains the typed migration
        exists to remove.
        """
        with _seam(result={"status": "draft", "context_id": "ctx-abc"}):
            result = await CreativeAgentRegistry().build_creative(
                format_id=GENERATIVE_FORMAT, message="Build a banner ad"
            )

        assert isinstance(result, GenerativeBuildResult)
        assert result.status == "draft"
        assert result.context_id == "ctx-abc"

    @pytest.mark.asyncio
    async def test_empty_response_is_none(self, dials_the_agent):
        """An agent that returns no payload yields None — "nothing to store", not a default build."""
        with _seam(result={}):
            result = await CreativeAgentRegistry().build_creative(
                format_id=GENERATIVE_FORMAT, message="Build a banner ad"
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_seam_error_propagates_with_its_own_classification(self, dials_the_agent):
        """The seam's mapped AdCPError reaches the caller intact.

        ``call_operator_mcp_tool`` owns both error mappings, so an outbound
        refusal or MCP failure already arrives as the internal typed taxonomy.
        Re-wrapping it here would discard the code and recovery the sync path's
        failure ladder reads off it.
        """
        with _seam(side_effect=AdCPServiceUnavailableError("creative agent unreachable")):
            with pytest.raises(AdCPServiceUnavailableError) as exc_info:
                await CreativeAgentRegistry().build_creative(format_id=GENERATIVE_FORMAT, message="Build a banner ad")

        assert exc_info.value.error_code == "SERVICE_UNAVAILABLE"
        assert exc_info.value.recovery == "transient"


class TestBuildCreativeManifestValidation:
    """The registry renders the manifest via model_validate (not model_construct).

    Realistic complete assets must reach the request as a typed manifest, and
    realistic partial/malformed assets must raise — typed, and before anything
    is dialled — rather than forwarding a broken manifest to the agent.
    """

    @pytest.mark.asyncio
    async def test_realistic_complete_assets_forwarded_as_typed_manifest(self, dials_the_agent):
        calls: list = []
        with _seam(capture=calls):
            await CreativeAgentRegistry().build_creative(
                format_id=FormatId(agent_url="https://creative.example.com", id="display_300x250"),
                message="Build a banner ad",
                assets=build_assets(image_spec("main_image")),
            )

        _, _, arguments = calls[0]
        assert arguments["creative_manifest"]["assets"]["main_image"]["asset_type"] == "image"

    @pytest.mark.asyncio
    async def test_realistic_partial_assets_raise(self, dials_the_agent):
        """A partial asset (image missing required width/height) raises rather than
        silently forwarding a broken manifest to the creative agent.

        The rejection is TYPED (``VALIDATION_ERROR`` / ``correctable``): the assets
        are buyer input, and a bare ``pydantic.ValidationError`` is not an
        ``AdCPError``, so the sync path would report it as "creative agent
        unreachable … retry recommended". It also happens BEFORE the dial — the
        seam is never reached.
        """
        calls: list = []
        with _seam(capture=calls):
            with pytest.raises(AdCPValidationError) as exc_info:
                await CreativeAgentRegistry().build_creative(
                    format_id=FormatId(agent_url="https://creative.example.com", id="display_300x250"),
                    message="Build a banner ad",
                    # Missing required width/height on the image asset.
                    assets={"main_image": {"asset_type": "image", "url": "https://example.com/img.png"}},
                )

        assert exc_info.value.error_code == "VALIDATION_ERROR"
        assert exc_info.value.recovery == "correctable"
        assert calls == [], "a malformed manifest must be rejected before anything is dialled"
