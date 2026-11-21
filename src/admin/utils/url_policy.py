"""Ingest-time egress policy for admin write handlers.

Admin write paths accept URLs that are STORED now and fetched later — a signals
agent endpoint, a Slack webhook, a principal's push-notification target. The
refusal an operator can act on therefore has to happen here, at ingest, long
before there is a request to attach it to. That is exactly what
:func:`src.core.security.outbound_http.validate_url` exists for: it applies the
seam's egress policy and connects to nothing, so no handler in this package
needs address policy of its own. (See the ``outbound_http`` module docstring:
this application implements no SSRF protection, ever — the policy belongs to
``adcp.signing``, reached through the one seam.)

The admin surface answers in two shapes — a form post that flashes and
redirects, and a JSON API that returns 400 — so there are two functions here
over one refusal decision. There were previously nine copies of that decision
spread across five modules, which is how a call site ends up with a policy the
others do not have.

``label`` is deliberately NOT passed to the seam as ``error.field``. The two look
alike and are not: ``label`` is an HTML form label an operator reads ("Webhook
URL"), while ``error.field`` is an AdCP JSONPath-lite path into a buyer's request
payload. Nothing on this surface builds an AdCP envelope, so there is no payload
for a path to point into, and handing a form label to that field would put a
non-AdCP namespace in it.

The message handed to the operator never interpolates the reason. The reason
names the resolved IP and distinguishes "cannot resolve" from "reserved range";
handed back to whoever supplied the URL, the pair is an internal host and port
scanner (``building/by-layer/L1/security.mdx``, point 6: do not
echo fetch errors back to the party that supplied the URL). The real reason is
not lost — ``outbound_http`` logs it at WARNING before these functions return,
and the log line below pins it to the URL and the field it came from.
"""

from __future__ import annotations

import logging
from typing import Any

from flask import Response, flash, jsonify, redirect
from werkzeug.wrappers import Response as WerkzeugResponse

from src.core.security.outbound_http import OutboundRequestBlocked, validate_url

logger = logging.getLogger(__name__)

# One wording for every refusal, whatever the real cause. ``label`` names the
# field the operator filled in, which is the only part they need to act on.
_REJECTION_MESSAGE = "{label} is not allowed by outbound egress policy."


def _is_allowed(url: Any, label: str) -> bool:
    """Whether *url* may be stored for a later outbound fetch.

    Anything that is not a non-empty string is refused without consulting the
    seam: these handlers store what they are given, there is no such thing as a
    safe absent destination, and a JSON API field can arrive as any type at all.
    Answering both here keeps every call site free of its own shape check.

    The WARNING carries what the seam's own refusal cannot say — which form field
    and which URL — so the two lines together are the whole story. The seam's half
    is ``AdCPBlockedUrlError.REFUSAL_MESSAGE``, the one buyer-facing sentence, and
    it names neither. (This cited ``outbound_http._blocked``, a symbol with zero
    occurrences in ``src/``.)
    """
    if not isinstance(url, str) or not url:
        logger.warning("[SECURITY] Rejected %s at ingest: not a usable URL", label)
        return False
    try:
        validate_url(url)
    except OutboundRequestBlocked:
        logger.warning("[SECURITY] Rejected %s at ingest: %r", label, url)
        return False
    return True


def redirect_if_url_blocked(url: Any, label: str, destination: str) -> WerkzeugResponse | None:
    """Flash an error and redirect to *destination* when *url* fails egress policy.

    Returns ``None`` when the URL is allowed, so a handler reads as::

        if blocked := redirect_if_url_blocked(url, "Webhook URL", destination):
            return blocked
    """
    if _is_allowed(url, label):
        return None
    flash(_REJECTION_MESSAGE.format(label=label), "error")
    return redirect(destination)


def json_error_if_url_blocked(url: Any, label: str, **payload_extra: Any) -> tuple[Response, int] | None:
    """Return a 400 JSON refusal when *url* fails egress policy, else ``None``.

    ``payload_extra`` carries the envelope keys a particular endpoint already
    promises its callers (for example ``success=False``); ``error`` is always
    the opaque message and is never overridden by it.
    """
    if _is_allowed(url, label):
        return None
    return jsonify({**payload_extra, "error": _REJECTION_MESSAGE.format(label=label)}), 400
