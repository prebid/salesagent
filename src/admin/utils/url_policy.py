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

A refused URL raises :class:`EgressUrlRefused`, and the admin error handler
answers it like any other refusal: JSON for a script or API caller, a flash on
the page it came from for a browser. No route chooses the format.

``label`` is deliberately NOT passed to the seam as ``error.field``. The two look
alike and are not: ``label`` is an HTML form label an operator reads ("Webhook
URL"), while ``error.field`` is an AdCP JSONPath-lite path into a buyer's request
payload. Nothing on this surface builds an AdCP envelope, so there is no payload
for a path to point into, and handing a form label to that field would put a
non-AdCP namespace in it.

The answer never carries the reason. The reason names the resolved IP and
distinguishes "cannot resolve" from "reserved range"; handed back to whoever
supplied the URL, the pair is an internal host and port scanner
(``building/by-layer/L1/security.mdx``, point 6: do not echo fetch errors back
to the party that supplied the URL). The real reason is not lost —
``outbound_http`` logs it at WARNING before the refusal is raised, and the log
line below pins it to the URL and the field it came from.
"""

from __future__ import annotations

import logging
from typing import Any

from src.core.operator_errors import OperatorRefusal
from src.core.security.outbound_http import OutboundRequestBlocked, validate_url
from src.core.webhook_validator import webhook_url_for_log

logger = logging.getLogger(__name__)


class EgressUrlRefused(OperatorRefusal):
    """A URL the operator entered is refused by outbound egress policy; *details* name its field.

    One wording for every refusal, whatever the real cause. The field's label is the only
    part the operator needs to act on.
    """

    MESSAGE = "A URL is not allowed by outbound egress policy"


def require_allowed_url(url: Any, label: str) -> None:
    """Raise :class:`EgressUrlRefused` unless *url* may be stored for a later outbound fetch.

    Anything that is not a non-empty string is refused without consulting the
    seam: these handlers store what they are given, there is no such thing as a
    safe absent destination, and a JSON API field can arrive as any type at all.
    Answering both here keeps every call site free of its own shape check.

    The WARNING carries what the seam's own refusal cannot say — which form field
    and which URL — so the two lines together are the whole story. The seam's half
    is ``AdCPBlockedUrlError.REFUSAL_MESSAGE``, the one buyer-facing sentence, and
    it names neither.
    """
    if not isinstance(url, str) or not url:
        logger.warning("[SECURITY] Rejected %s at ingest: not a usable URL", label)
    else:
        try:
            validate_url(url)
            return
        except OutboundRequestBlocked:
            # No path: a Slack webhook URL carries its token in the path
            # (hooks.slack.com/services/T000/B000/<token>), and a mistyped one is refused here.
            logger.warning("[SECURITY] Rejected %s at ingest: %s", label, webhook_url_for_log(url, keep_path=False))
    raise EgressUrlRefused(f"{label} refused at ingest", details=(label,))
