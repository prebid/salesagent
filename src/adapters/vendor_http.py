"""Construction proves an adapter can dial its vendor.

``AdServerAdapter`` used to declare ``base_url: str`` / ``headers: dict[str, str]``
as bare annotations — a promise to mypy that ``self._api(...)`` could type-check
on every subclass, kept only by the two (Kevel, Triton) that happened to assign
both. GAM, mock, and Broadstreet never did, so the same call site type-checked
and then raised ``AttributeError`` at runtime; Kevel/Triton assigned ``headers``
only in their credentialed branch, so a dry-run adapter type-checked a call it
could never actually make.

``VendorHttpClient`` replaces the promise with a fact: an adapter holds one only
when its construction already proved ``base_url`` and ``headers`` exist
together. :func:`require_vendor` is the one place "no client" becomes a typed
error, so no adapter writes its own copy of that guard.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from pydantic import JsonValue

from src.core.errors.details import ConfigurationDetails
from src.core.exceptions import AdCPConfigurationError
from src.core.security.outbound_http import OutboundResult, QueryParams, send


@dataclass(frozen=True, slots=True)
class VendorHttpClient:
    """A vendor API endpoint, dialled through the egress seam.

    ``base_url``, ``headers``, ``params`` and ``timeout`` are set together at
    construction and never after — the same invariant :class:`OperatorEndpoint`
    enforces for a label, applied here to a vendor's dial coordinates.

    ``params`` is how a vendor that authenticates by QUERY STRING holds its
    credential, exactly as ``headers`` holds one for a vendor that
    authenticates by header. Without it such a client had to rebuild its URL at
    every call site, which is both a second place to state the destination and
    a place the no-destination-rewrite guard cannot see.
    """

    base_url: str
    headers: Mapping[str, str]
    params: QueryParams = MappingProxyType({})
    timeout: float = 30.0

    def call(
        self, method: str, path: str, *, json: JsonValue | None = None, params: QueryParams | None = None
    ) -> OutboundResult:
        """One vendor call through the egress seam. Returns the OutboundResult.

        Deliberately does NOT parse and does NOT map errors — callers whose
        vendor response never carries a body would otherwise trip
        ``OutboundResult.json()``'s ``json.JSONDecodeError``, which the seam
        places outside its ``OutboundError`` contract; and call sites vary in
        error policy (raise, degrade to a failed status, degrade to unknown),
        so mapping here would flatten them.

        ``params`` merges the client's own mapping with *params*; overlapping
        keys raise rather than resolving to a winner — see
        :meth:`_merged_params`.

        ``max_attempts=1``: a single request, not a retry policy this method
        decides — vendor mutations (campaign/flight/creative creation) are not
        idempotent, so silently turning one failed create into three is exactly
        what routing through here must not do. ``dict(self.headers)`` copies
        the frozen mapping so a caller's mutation of the returned dict cannot
        reach back into this client's own headers.
        """
        return send(
            f"{self.base_url}{path}",
            method=method,
            headers=dict(self.headers),
            json=json,
            params=self._merged_params(params),
            timeout=self.timeout,
            max_attempts=1,
        )

    def _merged_params(self, per_call: QueryParams | None) -> QueryParams:
        """This client's own params, plus *per_call*. Overlapping keys are an error.

        The client-level mapping is where a vendor that authenticates by query
        string holds its credential, so a caller passing the same key is either
        forging a credential or shadowing a dial coordinate — a defect either
        way, and never something to resolve silently by picking a winner.

        Raises ``AdCPConfigurationError`` — the same class :func:`require_vendor`
        raises one function below, for the same reason: the deployment's own
        wiring is wrong and no buyer can act on it. It is a 500, NOT a
        validation error, because nothing about the buyer's request is invalid.
        It is deliberately not an ``OutboundError``: every adapter catches that
        family and translates it into a vendor failure status, which would
        report a defect in our own code as the vendor being down.

        The clashing keys are the structured fact, so they go in
        ``ConfigurationDetails.rejected_value``. Buyer-facing text is a function
        of the code (ADR-010) and is not authored here.
        """
        if not per_call:
            return dict(self.params)
        clash = sorted(self.params.keys() & per_call.keys())
        if clash:
            raise AdCPConfigurationError(details=ConfigurationDetails(rejected_value=clash))
        return {**self.params, **per_call}


def require_vendor(client: VendorHttpClient | None, *, vendor: str) -> VendorHttpClient:
    """Return *client*, or raise a typed, vendor-named configuration error.

    The one place "no client" becomes an ``AdCPConfigurationError`` — every
    adapter with a ``VendorHttpClient | None`` calls this instead of writing
    its own ``if self._vendor is None: raise ...``, so the typed details and
    the exception class cannot drift per adapter.

    The vendor name is the structured fact and goes in
    ``ConfigurationDetails.provider``, the same slot every other named-provider
    misconfiguration uses. Naming a third-party vendor in buyer-facing text is
    exactly what AdCP 3.1.1 ``transport-errors.mdx`` § Security Considerations
    forbids, and buyer-facing text is a function of the code anyway (ADR-010),
    so there is nothing to author.
    """
    if client is None:
        raise AdCPConfigurationError(details=ConfigurationDetails(provider=vendor))
    return client
