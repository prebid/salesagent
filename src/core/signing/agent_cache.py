"""The process-level counterparty resolution cache, owned by neither reader.

Two modules read this dict, and each is the other's importer if it holds the dict:
``verifier.py`` fills it from the discovery walk and reads it back on the next request,
and ``revocation.py`` reads it at checklist step 9 to find the JWK a revocation list was
signed with. Putting it in either one forces the other into a function-local import,
which no import-time check can see — a name that drifts there fails nowhere at import and
surfaces only on a signed request from a counterparty that publishes a revocation list.

So the dict lives here, in a module that imports neither reader, and both import it at
module level.
"""

from adcp.signing.agent_resolver import AgentResolution

#: Process-level ``{agent_url: AgentResolution}``. The WHOLE resolution is kept, not just
#: the JWKS, because the JWKS LOCATION is checked against the agent's own origin before the
#: resolution is admitted (``verifier._jwks_is_well_known``). Entries expire by
#: ``agent_resolution_ttl_seconds`` against ``AgentResolution.fetched_at``.
AGENT_RESOLUTION_CACHE: dict[str, AgentResolution] = {}
