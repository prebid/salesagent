"""The one property-list resolver behaviour the egress seam cannot own: the Bearer header.

Most of this module was deleted by prebid/salesagent#1802 and re-expressed in
``tests/integration/test_property_list_resolver.py``. That deletion was right:
every class here patched ``httpx.AsyncClient`` or
``src.core.security.url_validator``, and ``src/core/property_list_resolver.py``
touches neither any more — it fetches through
``src/core/security/outbound_http.py``, which owns scheme policy, address
validation, IP pinning, redirect refusal, the response-size cap and retry
classification and grades all of them once in
``tests/integration/test_outbound_http.py``. Those cases asserted the mock's own
configuration. Their replacements grade the resolver's **cache** against a real
local origin (``origin.hits == 1`` beats a mock's ``call_count``) and the wire
class of a **refusal** (VALIDATION_ERROR / correctable, naming
``property_list.agent_url``) on the envelope through ``get_products``.

One fact survived that move ungraded, so it is graded here.

``PropertyListReference.auth_token`` is the credential a buyer hands the seller
to read a PROTECTED property list, and the resolver is the only code that turns
it into a request header::

    if ref.auth_token:
        headers["Authorization"] = f"Bearer {ref.auth_token}"

That is a pure input mapping, not an egress decision: the seam is handed a
``headers`` mapping and forwards it, so it can neither supply the header nor
notice its absence. Nothing else in the tree asserts it — the integration
module's ``origin_ref`` accepts an ``auth_token`` but no case passes one, and
its origin never inspects ``Authorization``. Dropping the two lines above would
leave every other test green while every authenticated property list started
resolving as a 401, so the mapping keeps a test.

Mocking is legitimate for exactly this shape and no other: ``asend`` is
substituted to CAPTURE the call the resolver composed, and the assertion is on
arguments the resolver computed from its own input — not on a response the test
chose. Anything that depends on what comes BACK from the network belongs in the
integration module, against a real origin.
"""

from unittest.mock import AsyncMock, patch

import pytest
from adcp.types import PropertyListReference

from src.core.property_list_resolver import _DEFAULT_TIMEOUT, _REFUSED_FIELD_PATH
from src.core.security.egress.response import OutboundResult
from src.core.security.outbound_http import CounterpartyUrl


@pytest.fixture(autouse=True)
def _clear_cache():
    """Clear the module-level cache around each test.

    The cache outlives a test function, so a previous entry would answer this
    module's call before ``asend`` was ever reached and the captured-call
    assertion would fail on zero calls.
    """
    from src.core.property_list_resolver import clear_cache

    clear_cache()
    yield
    clear_cache()


def _empty_list_result() -> OutboundResult:
    """A real ``OutboundResult`` carrying the smallest valid list response.

    Constructed rather than mocked: ``OutboundResult`` is a frozen dataclass
    closed over its five fields (``tests/unit/test_outbound_result_is_closed.py``),
    so building one keeps this double honest — a field added to or removed from
    the seam's return type breaks this line instead of being silently absorbed
    by a ``MagicMock``.
    """
    return OutboundResult(
        http_status=200,
        headers={"content-type": "application/json"},
        content=b'{"list": {"list_id": "list-1", "name": "Test List"}}',
        attempts=1,
        duration_seconds=0.0,
    )


def _make_ref(auth_token: str | None) -> PropertyListReference:
    return PropertyListReference(
        agent_url="https://agent.example.com",
        list_id="list-1",
        auth_token=auth_token,
    )


class TestAuthTokenBecomesABearerHeader:
    """``ref.auth_token`` -> ``Authorization: Bearer <token>``, and nothing when absent."""

    @pytest.mark.asyncio
    async def test_auth_token_is_sent_as_a_bearer_header(self):
        """A present ``auth_token`` reaches the agent service as a Bearer credential."""
        from src.core.property_list_resolver import resolve_property_list

        with patch(
            "src.core.property_list_resolver.asend",
            new_callable=AsyncMock,
            return_value=_empty_list_result(),
        ) as mock_asend:
            await resolve_property_list(_make_ref(auth_token="my-secret-token"))

        # Atomic rather than split (assert_called_once() + call_args): the whole
        # composed call is the claim. ``timeout`` and ``provenance`` are read from
        # the module's own constants — this test does not grade them, and restating
        # their values would pin production against a second copy of itself.
        mock_asend.assert_called_once_with(
            "https://agent.example.com/lists/list-1",
            method="GET",
            # ast-grep-ignore: test-credential-header-single-producer - asserts the header PRODUCTION sent outbound, not one we build
            headers={"Authorization": "Bearer my-secret-token"},
            timeout=_DEFAULT_TIMEOUT,
            provenance=CounterpartyUrl(field=_REFUSED_FIELD_PATH),
        )

    @pytest.mark.asyncio
    async def test_no_authorization_header_when_the_ref_carries_no_token(self):
        """An absent ``auth_token`` sends NO ``Authorization`` header at all.

        Not an empty one: ``Authorization: Bearer `` is a malformed credential a
        strict agent service answers 401 to, which is a worse failure than an
        unauthenticated request to a public list.
        """
        from src.core.property_list_resolver import resolve_property_list

        with patch(
            "src.core.property_list_resolver.asend",
            new_callable=AsyncMock,
            return_value=_empty_list_result(),
        ) as mock_asend:
            await resolve_property_list(_make_ref(auth_token=None))

        mock_asend.assert_called_once_with(
            "https://agent.example.com/lists/list-1",
            method="GET",
            headers={},
            timeout=_DEFAULT_TIMEOUT,
            provenance=CounterpartyUrl(field=_REFUSED_FIELD_PATH),
        )
