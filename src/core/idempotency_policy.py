"""Idempotency capability posture: the ``adcp.idempotency`` declaration this
seller advertises on ``get_adcp_capabilities``.

Distinct from :mod:`src.services.idempotency_policy`, which is the cache
ADMISSION policy (insert-rate/storage-ceiling enforcement on the idempotency
cache itself) -- this module is the DECLARED POSTURE, the typed value
``capabilities.py``'s ``Adcp.idempotency`` union derives from. Mirrors
``billing_policy.py``'s shape: a pure module, zero ORM imports, so business
logic can import it without pulling in the repository layer (#1721
M2 -- was previously defined inside ``repositories/idempotency_attempt.py``,
importing SDK protocol *response* types into the repository layer even though
this same posture-vs-repository split already existed as a working template
here).
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from pydantic import BaseModel

from src.core.errors.details import ConfigurationDetails

if TYPE_CHECKING:
    from adcp.types.generated_poc.protocol.get_adcp_capabilities_response import Idempotency, Idempotency3

# Matches GetAdcpCapabilitiesResponse.adcp.idempotency.replay_ttl_seconds (86400 = 24h).
DEFAULT_REPLAY_TTL = timedelta(seconds=86400)

# get-adcp-capabilities-response.json#/properties/adcp/properties/idempotency/oneOf/0:
# replay_ttl_seconds bounds (schema-enforced integer minimum/maximum).
_MIN_REPLAY_TTL_SECONDS = 3600
_MAX_REPLAY_TTL_SECONDS = 604800


class IdempotencyPosture(BaseModel):
    """The seller's declared adcp.idempotency posture, pre-wire-serialization.

    Mirrors resolve_supported_billing()'s shape: one typed value both
    response-construction sites in capabilities.py derive from, instead of a
    literal Idempotency(...) duplicated at each site. ``check_bounds()``
    enforces the schema minimum/maximum and the cross-field
    in_flight_max_seconds <= replay_ttl_seconds rule the JSON Schema cannot
    express, raising AdCPConfigurationError (terminal — a seller-side
    deployment fault the buyer cannot fix) rather than silently clamping or
    emitting a non-conformant response.
    """

    supported: bool
    replay_ttl_seconds: int | None = None
    in_flight_max_seconds: int | None = None
    account_id_is_opaque: bool = False

    def check_bounds(self) -> None:
        if not self.supported:
            return
        from src.core.exceptions import AdCPConfigurationError

        if self.replay_ttl_seconds is None or not (
            _MIN_REPLAY_TTL_SECONDS <= self.replay_ttl_seconds <= _MAX_REPLAY_TTL_SECONDS
        ):
            raise AdCPConfigurationError(
                details=ConfigurationDetails(
                    replay_ttl_seconds=self.replay_ttl_seconds,
                    min_replay_ttl_seconds=_MIN_REPLAY_TTL_SECONDS,
                    max_replay_ttl_seconds=_MAX_REPLAY_TTL_SECONDS,
                ),
            )
        if self.in_flight_max_seconds is not None and self.in_flight_max_seconds > self.replay_ttl_seconds:
            raise AdCPConfigurationError(
                details=ConfigurationDetails(
                    in_flight_max_seconds=self.in_flight_max_seconds,
                    replay_ttl_seconds=self.replay_ttl_seconds,
                ),
            )

    def to_sdk_union(self) -> Idempotency | Idempotency3:
        """The SDK's discriminated Adcp.idempotency union. ``Idempotency3`` is a
        generator artifact name for the ``supported: Literal[False]`` branch --
        no friendlier alias exists in adcp.types (mirrors the ``Targeting``
        false-cognate comment, capabilities.py:33-37)."""
        from adcp.types.generated_poc.protocol.get_adcp_capabilities_response import Idempotency, Idempotency3

        if not self.supported:
            return Idempotency3(supported=False)
        return Idempotency(
            supported=True,
            replay_ttl_seconds=self.replay_ttl_seconds,
            in_flight_max_seconds=self.in_flight_max_seconds,
            account_id_is_opaque=self.account_id_is_opaque,
        )


def get_idempotency_posture(tenant: object = None) -> IdempotencyPosture:  # noqa: ARG001 -- tenant reserved for future per-tenant config (#1592 C4 Q3, follow-up)
    """Single source for the adcp.idempotency posture on get_adcp_capabilities.

    Currently returns the same fixed posture for every tenant (matches
    pre-existing hardcoded behavior exactly) -- per-tenant posture
    persistence is deferred (salesagent-rldj research Q3), not a behavior
    change here. Tests override this via CapabilitiesEnv.set_idempotency_posture
    (in-process monkeypatch of this module's function).
    """
    return IdempotencyPosture(supported=True, replay_ttl_seconds=int(DEFAULT_REPLAY_TTL.total_seconds()))
