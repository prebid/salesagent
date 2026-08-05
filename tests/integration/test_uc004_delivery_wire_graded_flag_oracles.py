"""Regression: UC-004 boolean-flag oracles must grade the wire, not the coerced payload.

salesagent-9csh's verification bar. On a2a/mcp/rest the harness hands Then-steps a
typed payload that was *reconstructed from* the wire, so Pydantic has already coerced
every field to its declared type. A boolean serialized as the JSON string ``"true"``
reconstructs to ``True``: an oracle that reads the typed payload passes on a wire the
buyer would reject, and no amount of ``is True`` strictness at the read site helps.
Only reading the serialized body catches it.

Each case dispatches a real ``get_media_buy_delivery`` through a real transport, takes
the real captured wire, and applies one mutation — a boolean flag re-serialized as its
JSON string form — to both the wire body and the payload the harness would rebuild
from it. It then asserts the production model really does coerce the flag back (the
blind spot is demonstrated, not assumed) and that the UC-004 oracle for that flag
rejects the response anyway.

Two of the four cases are already-migrated controls (``then_field_true`` /
``then_field_false`` read ``_wire_packages``) and pass today; they are here so a future
revert to the typed payload cannot land silently. The ``sandbox`` cases are MIGRATE
rows 33 and 37 of the ticket's disposition table — still on ``getattr(resp, ...)`` —
and fail until the migration lands.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import pytest

from tests.bdd.steps.domain import uc004_delivery
from tests.bdd.steps.generic import then_success
from tests.harness.transport import Transport

WIRE_TRANSPORTS = (Transport.A2A, Transport.MCP, Transport.REST)


@dataclass(frozen=True)
class FlagCase:
    """One boolean flag, where it lives on the wire, and the oracle that grades it."""

    id: str
    field: str
    value: bool
    package_level: bool
    oracle: Callable[[dict], None]

    @property
    def wire_value(self) -> str:
        """The flag as a non-conformant seller would serialize it: a JSON string."""
        return "true" if self.value else "false"

    def apply(self, body: dict[str, Any]) -> None:
        """Write the string-serialized flag into a response body (wire or payload dump)."""
        if not self.package_level:
            body[self.field] = self.wire_value
            return
        packages = [pkg for d in body.get("media_buy_deliveries") or [] for pkg in d.get("by_package") or []]
        assert packages, "response carries no packages — the package-level mutation would be a no-op"
        for pkg in packages:
            pkg[self.field] = self.wire_value

    def typed_values(self, response: Any) -> list[Any]:
        """Read the flag off the reconstructed typed payload, the way the old oracles do."""
        if not self.package_level:
            return [getattr(response, self.field, None)]
        return [getattr(pkg, self.field, None) for d in response.media_buy_deliveries for pkg in d.by_package]


CASES = (
    FlagCase(
        id="by_geo_truncated-wire-graded",
        field="by_geo_truncated",
        value=True,
        package_level=True,
        oracle=lambda ctx: uc004_delivery.then_field_true(ctx, "by_geo_truncated"),
    ),
    FlagCase(
        id="by_device_type_truncated-wire-graded",
        field="by_device_type_truncated",
        value=False,
        package_level=True,
        oracle=lambda ctx: uc004_delivery.then_field_false(ctx, "by_device_type_truncated"),
    ),
    FlagCase(
        id="sandbox-uc004-oracle",
        field="sandbox",
        value=True,
        package_level=False,
        oracle=uc004_delivery.then_no_billing,
    ),
    FlagCase(
        id="sandbox-generic-oracle",
        field="sandbox",
        value=True,
        package_level=False,
        oracle=then_success.then_sandbox_true,
    ),
)


@pytest.mark.requires_db
@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
@pytest.mark.parametrize("transport", WIRE_TRANSPORTS, ids=lambda t: t.value)
def test_string_serialized_boolean_flag_is_rejected_by_the_oracle(
    transport: Transport, case: FlagCase, integration_db
) -> None:
    """A flag emitted as ``"true"``/``"false"`` must fail the oracle that grades it."""
    from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory
    from tests.harness import DeliveryPollEnv

    tenant_id = f"t-{uuid4().hex[:8]}"
    with DeliveryPollEnv(tenant_id=tenant_id, principal_id="p1") as env:
        tenant = TenantFactory(tenant_id=tenant_id)
        principal = PrincipalFactory(tenant=tenant, principal_id="p1")
        buy = MediaBuyFactory(tenant=tenant, principal=principal, status="active")
        env.set_adapter_response(buy.media_buy_id, impressions=5000)

        result = env.call_via(transport, media_buy_ids=[buy.media_buy_id])

    assert result.is_success, f"{transport}: delivery dispatch failed: {result.error!r}"
    assert isinstance(result.wire_response, dict), f"{transport}: no success-path wire body was captured"

    wire = copy.deepcopy(result.wire_response)
    case.apply(wire)

    payload_dump = result.payload.model_dump(mode="json")
    case.apply(payload_dump)
    reconstructed = type(result.payload).model_validate(payload_dump)

    coerced = case.typed_values(reconstructed)
    assert coerced and all(v is case.value for v in coerced), (
        f"{transport}: premise broken — {case.field} serialized as {case.wire_value!r} did not "
        f"reconstruct to {case.value!r} on the typed payload (got {coerced!r}), so this case no "
        "longer demonstrates the coercion blind spot it exists to demonstrate."
    )

    ctx = {
        "env": env,
        "transport": transport,
        "result": result,
        "response": reconstructed,
        "wire_response": wire,
    }

    with pytest.raises(AssertionError):
        case.oracle(ctx)
