"""Unit tests for Creative Agent Registry.

``TestCreativeAgentRegistry`` (build/fetch against a mocked ``adcp.ADCPMultiAgentClient``)
was retired by salesagent-4n88: the OPERATOR agent path no longer constructs
that SDK client at all — it dials through the guarded MCP seam
(``src.core.utils.mcp_client.call_mcp_tool``, reached through
``src.core.utils.operator_mcp.call_operator_mcp_tool``) instead, via
``_fetch_formats_operator``. ``_build_adcp_client``, ``_fetch_formats_from_agent``,
``_as_format_dict``, ``build_adcp_multi_agent_client`` and ``signed_agent_call``
no longer exist, so every test that mocked them tested a mechanism rather than a
behaviour. Their contracts are re-expressed against a real local origin — the
project's stated preference over mocking the thing under test's own dependency:

* auth propagation (custom ``auth_header``, absent header, per-agent routing) —
  ``tests/integration/test_auth_header_propagation.py``
  ``::TestAuthConfigForwardedToGuardedSeam``
* connection-alias routing — ``tests/unit/test_creative_agent_connection_alias.py``
* RFC 9421 request signing (#1291 C3). This module's only signing surface was
  ``mock_agent_client.signing = None`` — hygiene against the SDK's now-deleted
  auto-signing. The obligation is now "the dial carries the tenant's per-attempt
  ``sign=`` callback, resolved by
  ``src.core.helpers.adapter_helpers.request_signer_for_tenant``", graded by
  ``tests/integration/test_auth_header_propagation.py``
  ``::TestRequestSignerForwardedToGuardedSeam``
* error taxonomy (auth / timeout / connection → typed ``AdCPError``) —
  ``tests/integration/test_operator_agent_mcp_seam_egress.py``
  ``::TestOperatorAgentFailureIsClassifiedTerminalByCode`` and
  ``tests/integration/test_format_fetch_transient_errors.py``. A dial-time
  refusal is terminal, never retried or laundered, per
  ``tests/integration/test_creative_agent_dial_refusal_recovery.py``
* no quiet failure on an anomalous response. The old "``submitted`` status must
  raise, not return ``[]``" is now "a payload with no ``formats`` key raises
  ``AdCPConfigurationError``", graded by
  ``tests/unit/test_silent_empty_format_bug.py``
* format parsing, including library-``Format``-to-local-``Format`` conversion →
  ``_validate_formats_tolerant``, graded by
  ``tests/integration/test_operator_probe_agent.py`` and the schema-invalid
  payload cases in ``test_operator_agent_mcp_seam_egress.py``

The message-provenance pairs the signing branch added to the retired class (the
buyer-facing ``message`` is the CODE_TABLE sentence, the upstream SDK text
reaches only ``internal_detail``, per AdCP 3.1.1 transport-errors.mdx
§ Security Considerations) survive one layer out and one notch stronger: they
are graded on the wire envelope, not on a reconstructed exception, by
``tests/integration/test_creative_agent_egress.py::TestTaxonomy::test_a_delivery_failure_does_not_echo_the_operators_endpoint``
— which asserts the operator's host, port AND the origin's response body are all
absent from the serialized envelope.

Genuinely retired with no successor: nothing behavioral. The three
``_build_adcp_client`` construction tests asserted only ``client is not None`` /
``hasattr(client, "agent")`` about a class the registry no longer instantiates;
the auth mapping they were reaching for is the propagation obligation above.

What remains here is the one contract that is still a pure unit: ``_cache_key``
must accept a Pydantic ``AnyUrl``.
"""

import pytest
from pydantic import AnyUrl

from src.core.creative_agent_registry import CreativeAgentRegistry


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
        # The canonical form renders an empty path as "/" (the algorithm's step 5), which
        # is what makes "https://x.org" and "https://x.org/" one cache entry rather than two.
        assert result == "https://creative.adcontextprotocol.org/"

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
