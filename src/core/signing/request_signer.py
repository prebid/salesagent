"""The RFC 9421 REQUEST-signing profile, as one callable an egress seam can hold.

The layer already owns *which key* signs a tenant's outbound traffic
(:func:`src.core.signing.provider.resolve_signing_material` ->
:func:`~src.core.signing.provider.signing_config_from_material`, which yields the
SDK's ``adcp.signing.SigningConfig`` bundle of private key, ``kid``, ``alg`` and
``tag``). What it did not own was a way to hand that decision to a transport as a
*callback*: every existing consumer passed the bundle to an SDK client that dialled
for itself, which is precisely what put an un-pinned dialer beside the SSRF-guarded
egress seam.

This module closes that gap and nothing else. It is the REQUEST-signing twin of
``adcp.webhook_auth.JwkSignerStrategy`` — the webhook-profile strategy
:mod:`src.core.security.webhook_egress` hands to ``send``/``asend`` as their
``sign=`` hook — and it is deliberately the same shape of object: a frozen dataclass
whose single method turns one outgoing request into the headers that authenticate it.
The bound method satisfies :class:`src.core.utils.mcp_client.SignMcpAttempt`, NOT
:class:`src.core.security.outbound_http.SignAttempt`: it takes one parameter more,
for the reason the ``headers`` paragraph below gives. The two protocols are twins with
one deliberate difference, not one protocol somebody widened.

**Zero crypto lives here.** :func:`adcp.signing.sign_request` builds the signature
base and signs it; this module only binds the already-resolved key material to it and
pins the two profile choices:

* ``tag`` comes from the :class:`~adcp.signing.SigningConfig` (``adcp/request-signing/v1``),
  NOT the webhook tag — a webhook-tagged signature on a protocol request is one a
  conformant verifier rejects, and reusing ``JwkSignerStrategy`` here would emit exactly
  that. This is the whole reason a second strategy exists rather than a second caller of
  the first.
* ``cover_content_digest=True`` always, matching the SDK's own choice in
  :func:`adcp.signing.install_signing_event_hook`: with ``covers_content_digest``
  unset or ``"either"``, the stricter body-bound option is the one a seller's verifier
  can never reject for a missing optional component.

**``headers`` is a parameter, and that is not a widening for its own sake.** The
webhook profile's strategy hard-codes ``{"Content-Type": "application/json"}`` because a
webhook delivery is always one POST of one JSON document. A signed MCP *session* is not:
it is a POST per JSON-RPC message plus, at the transport's discretion, a GET for the
event stream and a DELETE to terminate. ``adcp.signing.verifier`` requires that a
``content-type`` header PRESENT on the request be covered (``_check_components``), and
building the base from a header that did not ship produces a signature no verifier can
reconstruct. Signing over the request's real headers is the only spelling that is
correct for every message of a session; it is also exactly what the SDK's own httpx
hook (:func:`adcp.signing.install_signing_event_hook`) does with ``dict(request.headers)``.
The keyword names mirror :func:`adcp.signing.sign_request`'s own so a seam can hold
:meth:`RequestSignerStrategy.build_signed_headers` with no adapter in between.

Spec grounding: pinned AdCP 3.1.1, ``docs/building/by-layer/L1/security.mdx``
§Request signing — the ``adcp/request-signing/v1`` profile over RFC 9421, whose
``nonce`` a conformant verifier MUST reject on replay (which is why the seam invokes
this per attempt rather than once per call). ``adcp==6.6.0``'s
``adcp.signing.signer``/``adcp.signing.verifier`` are the cross-check confirming the
reading, not the authority.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from adcp.signing import SigningConfig, sign_request


@dataclass(frozen=True, slots=True)
class RequestSignerStrategy:
    """Signs ONE outgoing request under ``adcp/request-signing/v1``.

    Holds the :class:`~adcp.signing.SigningConfig` the layer already resolved rather
    than the key, ``kid`` and ``alg`` as three loose fields: those three are chosen
    together by :func:`~src.core.signing.provider.signing_config_from_material`, and
    re-spelling them here would be a second place a rotation has to land. The SDK
    type redacts its own private key in ``repr``, so this dataclass does not have to.

    Stateless and re-entrant: every call mints a fresh ``created``/``expires`` window
    and a fresh ``nonce`` inside :func:`adcp.signing.sign_request`. That is what makes
    it safe — and required — to hand this to a seam that retries.
    """

    signing: SigningConfig

    def build_signed_headers(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes,
    ) -> dict[str, str]:
        """The ``Signature-Input`` / ``Signature`` / ``Content-Digest`` headers for one request.

        *url* must be the post-parameters target URI and *body* the exact bytes the
        transport will transmit — the caller's obligation, because a signature over a
        re-serialization of a payload is the defect class this whole seam exists to
        make unconstructible (GH #1441). *headers* must be the request's real headers,
        as they will ship.
        """
        return sign_request(
            method=method,
            url=url,
            headers=headers,
            body=body,
            private_key=self.signing.private_key,
            key_id=self.signing.key_id,
            alg=self.signing.alg,
            cover_content_digest=True,
            tag=self.signing.tag,
        ).as_dict()
