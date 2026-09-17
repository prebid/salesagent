"""A2A 'success' field: one derivation, and it reports FAILURE (#1868 review, then #2167).

Two obligations meet in one predicate here.

DRY (#1868). Three sites independently stamped response_data["success"] onto the
A2A wire: _serialize_for_a2a (the declared "single serialization point"),
_handle_get_products_skill, and _get_products (the natural-language handler) --
the latter two duplicated the stamp inline WITHOUT any derivation, unconditionally
forcing success=True even when the response carried populated `errors`. That is a
DRY violation (CLAUDE.md non-negotiable invariant) that hid a real behavioral
divergence between the paths. All three now route through
_stamp_a2a_protocol_fields, so every test below is repeated on all three.

WHAT THE MARKER MEANS (#2167). The single derivation then answered the wrong
question: `success = not bool(errors)`, i.e. "is errors[] non-empty". The pinned
core/protocol-envelope.json says those are different questions -- on `adcp_error`:
"a fatal task failure SHOULD populate both this envelope-level field AND the
payload's `errors[]` array ... Non-fatal warnings populate ONLY `payload.errors[]`
with `severity: warning` -- the envelope MUST NOT carry `adcp_error` for
non-failures." Its two examples carry exactly that contrast: severity "warning" on
a completed response, severity "error" on status "failed".

So a tenant with an advisory -- get_products' unranked-ranking notice is the live
one -- reported success=false on EVERY call over A2A while returning a complete,
usable product list. An entry that has declared itself `severity: "warning"` no
longer flips the marker; an entry that has NOT is still a failure, which is why
every unmarked-error case below keeps its original expectation.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from adcp.types import Error

from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
from src.core.schemas import GetProductsResponse
from tests.factories.principal import PrincipalFactory

_MOCK_IDENTITY = PrincipalFactory.make_identity(
    principal_id="test_principal", tenant_id="test_tenant", tenant={"tenant_id": "test_tenant"}, protocol="a2a"
)

# The shape production emits: src.core.tools.products._unranked_products_advisory.
# Built here as a real adcp Error so `severity` travels as the extra field it is,
# through the same model_dump the stamper reads.
_ADVISORY = Error(
    code="CONFIGURATION_ERROR",
    message="PRODUCT_RANKING_UNAVAILABLE: products returned unranked",
    recovery="terminal",
    severity="warning",
    field="products[]",
)

# An errors[] entry with no severity marker -- every fatal errors[] this codebase
# emits (UpdateMediaBuyError, get_media_buys' AUTH_REQUIRED degradation).
_UNMARKED_ERROR = {"code": "X", "message": "y"}


def _make_get_products_response(errors=None, adcp_error=None) -> GetProductsResponse:
    """A real GetProductsResponse -- no MagicMock standing in for the response object.

    A MagicMock's canned .model_dump()/__str__ return values round-trip regardless of
    what _stamp_a2a_protocol_fields actually does with a real Pydantic model, so it
    cannot observe a regression in that behavior. This constructs the same response
    type production actually returns from core_get_products_tool.
    """
    kwargs = {"products": []}
    if errors is not None:
        kwargs["errors"] = errors
    if adcp_error is not None:
        kwargs["adcp_error"] = adcp_error
    return GetProductsResponse(**kwargs)


class TestSerializeForA2ADerivesSuccessFromErrors:
    """Baseline: the declared single serialization point already gets this right."""

    def test_success_true_when_no_errors(self):
        handler = AdCPRequestHandler()
        result = handler._serialize_for_a2a(_make_get_products_response())
        assert result["success"] is True

    def test_success_false_when_errors_present(self):
        handler = AdCPRequestHandler()
        result = handler._serialize_for_a2a(_make_get_products_response(errors=[_UNMARKED_ERROR]))
        assert result["success"] is False

    def test_success_true_when_the_only_entry_is_an_advisory_warning(self):
        """A complete product list plus a non-fatal notice is not a failed task."""
        handler = AdCPRequestHandler()
        result = handler._serialize_for_a2a(_make_get_products_response(errors=[_ADVISORY]))
        assert result["success"] is True, (
            "an errors[] entry marked severity='warning' is by the pinned envelope's own "
            "definition non-fatal and must not flip the A2A success marker"
        )
        # The advisory still reaches the buyer -- success is corrected, not the payload.
        assert result["errors"][0]["code"] == "CONFIGURATION_ERROR"
        assert result["errors"][0]["severity"] == "warning"

    def test_success_false_when_a_warning_rides_alongside_an_unmarked_error(self):
        """One real error is enough; a warning next to it does not launder it."""
        handler = AdCPRequestHandler()
        result = handler._serialize_for_a2a(_make_get_products_response(errors=[_ADVISORY, _UNMARKED_ERROR]))
        assert result["success"] is False

    def test_success_false_when_the_envelope_carries_adcp_error(self):
        """`adcp_error` is the envelope's own fatal-failure signal, warnings or not.

        Pinned core/protocol-envelope.json: it is the "transport-envelope error signal
        for fatal task failures", and the envelope "MUST NOT carry `adcp_error` for
        non-failures" -- so its presence settles the question by itself.
        """
        handler = AdCPRequestHandler()
        response = _make_get_products_response(
            errors=[_ADVISORY], adcp_error=Error(code="INVALID_REQUEST", message="bad brief")
        )
        assert handler._serialize_for_a2a(response)["success"] is False


class TestHandleGetProductsSkillDerivesSuccessFromErrors:
    """_handle_get_products_skill duplicated the stamp without errors-derivation."""

    @pytest.mark.asyncio
    async def test_success_false_when_errors_present(self):
        handler = AdCPRequestHandler()
        with patch("src.a2a_server.adcp_a2a_server.core_get_products_tool") as mock_core_tool:
            mock_core_tool.return_value = _make_get_products_response(errors=[_UNMARKED_ERROR])
            result = await handler._handle_get_products_skill({"brief": "test"}, _MOCK_IDENTITY)

        assert result["success"] is False, (
            "get_products A2A response with populated errors must report success=False, "
            "matching _serialize_for_a2a's derivation -- not unconditionally True"
        )

    @pytest.mark.asyncio
    async def test_success_true_when_no_errors(self):
        handler = AdCPRequestHandler()
        with patch("src.a2a_server.adcp_a2a_server.core_get_products_tool") as mock_core_tool:
            mock_core_tool.return_value = _make_get_products_response()
            result = await handler._handle_get_products_skill({"brief": "test"}, _MOCK_IDENTITY)

        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_success_true_when_the_only_entry_is_an_advisory_warning(self):
        """The live case: a seller with AI ranking configured but unavailable.

        Every get_products call from that seller carries the advisory, so before this
        was corrected every one of them reported success=false over A2A while returning
        a complete product list.
        """
        handler = AdCPRequestHandler()
        with patch("src.a2a_server.adcp_a2a_server.core_get_products_tool") as mock_core_tool:
            mock_core_tool.return_value = _make_get_products_response(errors=[_ADVISORY])
            result = await handler._handle_get_products_skill({"brief": "test"}, _MOCK_IDENTITY)

        assert result["success"] is True
        assert result["errors"][0]["severity"] == "warning"


class TestNaturalLanguageGetProductsDerivesSuccessFromErrors:
    """_get_products (NL handler) duplicated the stamp without errors-derivation."""

    @pytest.mark.asyncio
    async def test_success_false_when_errors_present(self):
        handler = AdCPRequestHandler()
        with patch("src.a2a_server.adcp_a2a_server.core_get_products_tool") as mock_core_tool:
            mock_core_tool.return_value = _make_get_products_response(errors=[_UNMARKED_ERROR])
            result = await handler._get_products("test query", _MOCK_IDENTITY)

        assert result["success"] is False, (
            "get_products A2A response (NL path) with populated errors must report "
            "success=False, matching _serialize_for_a2a's derivation"
        )

    @pytest.mark.asyncio
    async def test_success_true_when_no_errors(self):
        handler = AdCPRequestHandler()
        with patch("src.a2a_server.adcp_a2a_server.core_get_products_tool") as mock_core_tool:
            mock_core_tool.return_value = _make_get_products_response()
            result = await handler._get_products("test query", _MOCK_IDENTITY)

        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_success_true_when_the_only_entry_is_an_advisory_warning(self):
        handler = AdCPRequestHandler()
        with patch("src.a2a_server.adcp_a2a_server.core_get_products_tool") as mock_core_tool:
            mock_core_tool.return_value = _make_get_products_response(errors=[_ADVISORY])
            result = await handler._get_products("test query", _MOCK_IDENTITY)

        assert result["success"] is True
        assert result["errors"][0]["severity"] == "warning"
