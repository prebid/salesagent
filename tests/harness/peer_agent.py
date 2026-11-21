"""A real peer creative agent, reachable from any env whose code under test dials one.

A creative agent the tenant configured is dialled for its formats -- on the listing path
and on a single-format lookup alike -- and for a preview of each static creative synced in
one of its formats, so a test that names such an agent has to stand one up that really
answers. In process that is this worker's shared peer agent
(:mod:`tests.helpers.peer_creative_agent`), on loopback over TLS; on e2e it is the compose
stack's webhook-capture service. ``serve_peer_tool`` is the one way an env reaches either,
so every env that needs a peer gets the same agent and the same reachability.

``seed_own_creative_agent`` is the one way an env says "the seller's catalog is exactly
these formats": serve them from the peer, point the tenant's one ``creative_agents`` row at
it, and stop federating the standard creative agent. Every env whose subject is a seeded
catalog -- listing it, or resolving a creative against it -- goes through it.
``host_reference_format`` is the one way an env says "the tenant's own agent also
publishes this capture format and answers its previews", with the standard agent still
federated.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

from src.core.config import get_settings
from src.core.schemas import Format
from tests.e2e.webhook_capture_service import PEER_CATALOG_TOOL, PEER_PREVIEW_TOOL
from tests.harness._realize import realize_e2e
from tests.harness.egress import EgressHatchMixin
from tests.helpers.peer_creative_agent import (
    catalog_entry,
    new_peer_key,
    peer_agent_url,
    peer_tool_calls,
    rehomed_reference_format,
    serve_peer_tool,
    single_preview_document,
)
from tests.helpers.tls_material import trust_test_ca

#: A creative agent that does not answer: the shared TLS front's own name, so the egress
#: gate resolves it and lets the dial through, at a port nothing listens on. A made-up host
#: fails DNS inside the gate instead, which is a VALIDATION_ERROR on the buyer's agent_url
#: -- a different outcome from an agent that is down. Not the operator agent (another
#: port), so a format named here is a counterparty reference the seller has to dial.
UNREACHABLE_AGENT_URL = "https://creative-agent.adcp.test:8444/api/creative-agent"
#: Where that name resolves in process: a public-range address, as the e2e network's TLS
#: front sits at one (``E2E_NETWORK_SUBNET`` in docker-compose.e2e.yml), so the egress gate
#: admits it with no private-range hatch. The TCP connect to it is refused, so nothing is
#: ever sent there.
_UNREACHABLE_AGENT_ADDRESS = "223.255.255.62"


def _unreachable_agent_on_the_live_stack(env: PeerCreativeAgentMixin) -> str:
    """On the live stack nothing listens at the URL, so the server's own dial fails."""
    return UNREACHABLE_AGENT_URL


def _serve_peer_tool_on_the_live_stack(
    env: PeerCreativeAgentMixin, key: str, tool: str, document: dict[str, Any]
) -> str:
    """E2E realization of the peer agent: the compose stack's capture service serves *document*.

    The live server cannot reach a socket in the runner, so the peer it dials is the
    webhook-capture service, which already sits behind the shared TLS front and answers a
    creative agent's tools over MCP for a key a test programs. Returns the URL the server
    dials, which the tenant's ``creative_agents`` row then names; the factory writes that
    row to the live server's own database, as every e2e factory row is written.
    """
    from tests.e2e import _webhook_capture

    _webhook_capture.serve_peer_tool(key, tool, document)
    return _webhook_capture.peer_agent_url(key)


def _peer_url_on_the_live_stack(env: PeerCreativeAgentMixin, key: str) -> str:
    """The URL the live server dials for the capture service's peer under *key*."""
    from tests.e2e import _webhook_capture

    return _webhook_capture.peer_agent_url(key)


def _peer_tool_calls_on_the_live_stack(env: PeerCreativeAgentMixin, key: str, tool: str) -> list[dict[str, Any]]:
    """The *tool* calls the live server made of the capture service's peer under *key*."""
    from tests.e2e import _webhook_capture

    return _webhook_capture.peer_tool_calls(key, tool)


class PeerCreativeAgentMixin(EgressHatchMixin):
    """Mixes a real peer creative agent into an env whose code under test dials a configured one.

    Host env must be an ``IntegrationEnv`` (relies on ``_guard`` to release the trusted CA
    and the hatch posture on exit, and on ``ensure_tenant`` / ``_commit_factory_data`` to
    write the tenant's ``creative_agents`` row).
    """

    #: Whether this env trusts the test CA and opened the private-range hatch yet.
    _peer_reachable: bool = False
    #: The tenant's one peer ``creative_agents`` row, created on first use.
    _peer_agent_row: Any = None
    #: The URL that row names: where the seller dials its peer, and what it refers buyers to.
    _peer_agent_url: str | None = None
    #: Which configuration owns that row: the ``owner`` its first configuration named.
    _peer_agent_owner: str | None = None
    #: The key :meth:`host_reference_format` serves its agent under, once it has one.
    _hosted_key: str | None = None
    #: The catalog entries that agent publishes, by format id.
    _hosted_formats: dict[str, dict[str, Any]] | None = None

    if TYPE_CHECKING:
        # Declared, not implemented: this mixin is only composed with IntegrationEnv,
        # which owns the tenant row and the factory session (same convention as
        # EgressHatchMixin._guard).
        def ensure_tenant(self, **tenant_kwargs: Any) -> Any: ...

        def _commit_factory_data(self) -> None: ...

    def _enter_pre(self) -> None:
        """In process, the operator creative agent is dialled at a stand-in on this worker's peer.

        A static creative in a format of the operator agent (the standard agent the reference
        capture lists) is previewed by dialling that agent at CREATIVE_AGENT_URL. The e2e
        server reaches the stack's own creative agent there; an in-process env would reach
        the live public host, which no test may depend on. So the env points
        CREATIVE_AGENT_URL, the deployment's own setting, at a peer under a key of its own
        that answers ``preview_creative`` with the pinned single preview. The operator
        agent's catalog is unaffected: the testing registry answers it from the capture.
        """
        super()._enter_pre()  # type: ignore[misc]
        if self.is_e2e:  # type: ignore[attr-defined]
            return
        key = new_peer_key()
        endpoint = patch.object(
            get_settings().integrations,
            "creative_agent_url",
            self.serve_peer_tool(key, PEER_PREVIEW_TOOL, single_preview_document(key)),
        )
        endpoint.start()
        self._guard("operator creative agent stand-in", endpoint.stop)

    @realize_e2e(_serve_peer_tool_on_the_live_stack)
    def serve_peer_tool(self, key: str, tool: str, document: dict[str, Any]) -> str:
        """Serve *document* as the peer's answer to *tool* under *key*; return the URL a seller dials.

        In process the peer is this worker's shared agent, on loopback over TLS. The seller
        dials it through the egress seam, so the env trusts the shared test CA and opens the
        private-range hatch -- once, for the env's lifetime. The live stack's posture is
        already open.
        """
        if not self._peer_reachable:
            trust_test_ca(self._guard)
            self.set_egress_hatches(private=True)
            self._peer_reachable = True
        return serve_peer_tool(key, tool, document)

    @realize_e2e(_peer_tool_calls_on_the_live_stack)
    def peer_tool_calls(self, key: str, tool: str) -> list[dict[str, Any]]:
        """The arguments of every *tool* call a seller made of the peer under *key*, oldest first.

        The peer records each call it answers, on either side of the process boundary, so
        what the seller asked of its agent is read off the agent rather than off a recorder
        inside the seller -- which over e2e_rest is in another process.
        """
        return peer_tool_calls(key, tool)

    def serve_peer_catalog(self, key: str, document: dict[str, Any]) -> str:
        """Serve *document* as the peer's ``list_creative_formats`` under *key*; return the URL a seller dials."""
        return self.serve_peer_tool(key, PEER_CATALOG_TOOL, document)

    @realize_e2e(_peer_url_on_the_live_stack)
    def peer_url(self, key: str) -> str:
        """The URL a seller dials for the peer under *key*, known before anything is served there.

        A catalog whose formats name their own agent has to carry this URL inside the
        document that is then served under it.
        """
        return peer_agent_url(key)

    def publish_formats(self, formats: Sequence[Format], *, rehome: bool) -> str:
        """Serve *formats* from the peer under a fresh key; return the URL a seller dials.

        Each call mints a new key, so a re-seed is never answered from a catalog a registry
        cached for an earlier URL. With *rehome*, every entry's ``format_id.agent_url`` is the
        peer's own URL, as an agent states for a format it publishes: a creative that names
        one of them is then resolved by dialling this peer, because a single-format lookup
        routes by that URL and a URL naming the standard agent is answered from the
        reference capture instead. Without it, the entries are served verbatim
        (:func:`~tests.helpers.peer_creative_agent.catalog_entry`).

        The peer also answers ``preview_creative`` with the pinned single preview, as
        :meth:`host_reference_format`'s agent does: a seller previews every static creative
        synced in a format whose agent it dials, so a catalog that could resolve a creative
        and not preview it would refuse every accepted sync as ``no_previews``.
        """
        key = new_peer_key()
        self.serve_peer_tool(key, PEER_PREVIEW_TOOL, single_preview_document(key))
        agent_url = self.peer_url(key) if rehome else None
        return self.serve_peer_catalog(key, {"formats": [catalog_entry(fmt, agent_url) for fmt in formats]})

    def seed_own_creative_agent(self, formats: Sequence[Format], *, rehome: bool) -> str:
        """The seller's catalog is exactly *formats*, on every transport; return the peer's URL.

        Serves *formats* from the peer (:meth:`publish_formats`), points the tenant's one
        peer ``creative_agents`` row at it, and turns off the tenant's federation of the
        standard creative agent (``use_default_creative_agent``), so the seller lists and
        resolves these formats and nothing else, through production's own registry.

        A format the seller's own parse refuses (``PeerFormat``) is discarded and reported,
        as it would be from any real peer: the caller has to build a conforming format,
        which is the point of serving it for real.
        """
        url = self.publish_formats(formats, rehome=rehome)
        self.configure_peer_agent(url, owner="seed_own_creative_agent", use_default_creative_agent=False)
        return url

    def configure_peer_agent(self, agent_url: str, *, owner: str, **tenant_columns: Any) -> None:
        """The tenant's one peer creative agent is *agent_url* -- one row, both worlds.

        The row is created on the first call and re-pointed after, so a scenario never
        configures two peers by re-seeding. *tenant_columns* go through ``ensure_tenant``,
        which applies them to the tenant row the test or the e2e auto-seed already created
        and seeds nothing else: this configures the seller, not a buyer.

        ONE CONFIGURATION OWNS THE ROW. *owner* names the configuration the call belongs
        to, and only that owner may re-point the row: a re-seed of the same configuration
        supersedes it by design (a catalog Given re-serves the whole grown catalog under a
        fresh key). A DIFFERENT owner is refused, because the tenant has one peer row and
        the two would otherwise leave whichever ran last -- a seeded catalog with the
        standard agent turned off, or a hosted reference format with it federated -- and
        silently lose the other's formats (salesagent-ol033.20.30.4).
        """
        from tests.factories import CreativeAgentFactory

        if self._peer_agent_owner not in (None, owner):
            raise ValueError(
                f"the tenant's one peer creative agent is already configured by "
                f"{self._peer_agent_owner}; {owner} would re-point it at {agent_url} and "
                f"discard that configuration's catalog. A scenario uses one of them."
            )
        tenant = self.ensure_tenant(**tenant_columns)
        if self._peer_agent_row is None:
            self._peer_agent_row = CreativeAgentFactory(tenant=tenant, agent_url=agent_url, name="Peer Creative Agent")
        else:
            self._peer_agent_row.agent_url = agent_url
        self._commit_factory_data()
        self._peer_agent_url = agent_url
        self._peer_agent_owner = owner

    def peer_agent_url(self) -> str:
        """The URL of the tenant's peer creative agent, as its ``creative_agents`` row names it."""
        assert self._peer_agent_url is not None, "no peer creative agent is configured on this tenant"
        return self._peer_agent_url

    def host_reference_format(self, format_id: str) -> str:
        """The tenant's own creative agent publishes the reference capture's *format_id*; return its URL.

        One agent per env, created on the first call: a peer under a fresh key that answers
        ``preview_creative`` with a pinned single preview, and the tenant's ``creative_agents``
        row pointed at it. Each format asked for is added to its catalog as the capture's
        entry with ``format_id.agent_url`` set to the agent's own URL, so a creative naming
        that identity is looked up from this agent and previewed by it -- the seller dials
        the agent a format's identity names. The standard creative agent stays federated.

        Every Given runs before the seller first lists this agent, so a format added by a
        later call is in the catalog the seller reads; the URL never changes within an env.
        """
        if self._hosted_key is None:
            key = new_peer_key()
            self.serve_peer_tool(key, PEER_PREVIEW_TOOL, single_preview_document(key))
            self.configure_peer_agent(self.peer_url(key), owner="host_reference_format")
            self._hosted_key, self._hosted_formats = key, {}
        assert self._hosted_formats is not None
        if format_id not in self._hosted_formats:
            self._hosted_formats[format_id] = rehomed_reference_format(format_id, self.peer_url(self._hosted_key))
            self.serve_peer_catalog(self._hosted_key, {"formats": list(self._hosted_formats.values())})
        return self.peer_url(self._hosted_key)

    def _require_hosted_key(self) -> str:
        """The key :meth:`host_reference_format` serves its agent under; refuses before there is one."""
        assert self._hosted_key is not None, (
            "the tenant hosts no creative agent yet; host_reference_format() must publish a format first"
        )
        return self._hosted_key

    def serve_hosted_tool(self, tool: str, document: dict[str, Any]) -> None:
        """The tenant's hosted agent answers *tool* with *document* from now on."""
        self.serve_peer_tool(self._require_hosted_key(), tool, document)

    def hosted_agent_calls(self, tool: str) -> list[dict[str, Any]]:
        """The arguments of every *tool* call the seller made of its hosted agent, oldest first.

        Refuses rather than answering an empty list when no agent is hosted: "the seller
        asked nothing" read off an agent nobody configured is true of every scenario.
        """
        return self.peer_tool_calls(self._require_hosted_key(), tool)

    @realize_e2e(_unreachable_agent_on_the_live_stack)
    def unreachable_creative_agent_url(self) -> str:
        """The URL of a creative agent that does not answer, for the lifetime of this env.

        On e2e the live server dials it and nothing listens on that port. In process the
        same thing happens one layer down, at the calls the seller makes into the system
        and does not own: the resolver answers the agent's host with a public-range
        address, and a TCP connect to that address is refused, as a closed port refuses
        it -- the async connect httpcore opens through anyio and the sync one it opens
        through ``socket.create_connection`` alike, so nothing is ever sent there. Every
        dial to this agent fails at the socket -- the egress seam's one-shot ``asend`` (a
        counterparty format lookup) and the MCP session ``call_operator_mcp_tool`` opens
        (the listing) -- and every other host and address is resolved and connected as
        before. Everything above the socket -- the egress gate, the seam's retries and
        backoff, the MCP client's ``/mcp`` fallback, the error mapping and each tool's
        handling of an undelivered request -- is production's on both paths.
        """
        import errno
        import socket
        from unittest.mock import patch
        from urllib.parse import urlsplit

        import anyio

        host = urlsplit(UNREACHABLE_AGENT_URL).hostname
        resolve, connect, open_connection = socket.getaddrinfo, anyio.connect_tcp, socket.create_connection
        answer = (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "")

        def refuse(address: Any) -> None:
            if str(address) == _UNREACHABLE_AGENT_ADDRESS:
                raise ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused")

        def getaddrinfo(name: Any, port: Any, *args: Any, **kwargs: Any) -> Any:
            if name == host:
                return [(*answer, (_UNREACHABLE_AGENT_ADDRESS, port or 0))]
            return resolve(name, port, *args, **kwargs)

        async def connect_tcp(remote_host: Any, remote_port: int, **kwargs: Any) -> Any:
            refuse(remote_host)
            return await connect(remote_host, remote_port, **kwargs)

        def create_connection(address: tuple[Any, ...], *args: Any, **kwargs: Any) -> Any:
            refuse(address[0])
            return open_connection(address, *args, **kwargs)

        for target, replacement in ((socket, getaddrinfo), (anyio, connect_tcp), (socket, create_connection)):
            patcher = patch.object(target, replacement.__name__, replacement)
            patcher.start()
            self._guard("unreachable creative agent", patcher.stop)
        return UNREACHABLE_AGENT_URL
