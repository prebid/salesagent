"""Regression: a UC-004 partition cell must route by the request field's DECLARED type.

The dispatch verification bar, expected side. The scenario's Examples cell is a
string; the request field ``include_package_daily_breakdown`` is declared ``bool | None``.
Today ``_dispatch_partition`` tries ``json.loads`` and, on failure, dispatches the RAW
STRING — so the cell ``True`` reaches the wire as ``"True"`` — and even a successful
``json.loads`` is never checked against the declared type, so the cell ``"true"`` reaches
it as the str ``"true"``. Both then read back through one untyped channel, where
``requested is True`` at the daily-breakdown oracle is ``False`` and the else-arm asserts
the OPPOSITE contract ("this package must carry no daily_breakdown") — green, against a
production that is honouring the flag correctly. Today's cells happen to be ``true`` /
``false``, so the branch is correct by luck, not by type.

The target: parse the cell with ``TypeAdapter(<declared annotation>).validate_python(cell,
strict=True)``. Strict rejects ``"true"`` / ``"True"`` / ``1`` and accepts ``True``, which
splits the cells into two STRUCTURALLY distinct channels — a well-formed request model
under ``ctx["dispatched_request"]``, or a malformed body under ``ctx["dispatched_malformed"]``
that only production may judge. One accessor, one type: ``dispatched_request(ctx)`` returns
the request model and raises loudly on the malformed channel instead of handing an oracle
a raw string it will silently mis-grade.

Both tests dispatch through a real transport with a real DB and read the channel the real
When step recorded.
"""

from __future__ import annotations

import copy
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import pytest

from src.core.schemas.delivery import GetMediaBuyDeliveryRequest
from tests.bdd.steps.domain import uc004_delivery
from tests.harness.transport import Transport

WIRE_TRANSPORTS = (Transport.A2A, Transport.MCP, Transport.REST)

FIELD = "include_package_daily_breakdown"

#: The cell the boundary Outline grades as invalid (feature line 781), and the one the
#: ticket names as the second half of its mutation bar.
STRING_TRUE_CELL = '"true"'


@dataclass(frozen=True)
class CellCase:
    """One Examples cell, and the channel its declared type puts it on."""

    id: str
    cell: str
    #: The value the request model must carry. ``...`` means the field must be UNSET
    #: (omitted, not defaulted). ``None`` means the cell is malformed: no request model.
    expected: bool | None | Any

    @property
    def well_formed(self) -> bool:
        return self.expected is not None


CASES = (
    # Valid rows of the partition/boundary Outlines.
    CellCase("json-true", "true", True),
    CellCase("json-false", "false", False),
    CellCase("field-absent", "(field absent)", ...),
    # Rows the Outlines grade invalid, plus the ticket's `True`-spelled mutation. None of
    # these is a bool, so none may become a request model the expected side can read.
    CellCase("quoted-true", STRING_TRUE_CELL, None),
    CellCase("quoted-yes", '"yes"', None),
    CellCase("python-spelled-True", "True", None),
    CellCase("int-one", "1", None),
)


@contextmanager
def _seeded_env(tenant_id: str) -> Iterator[Any]:
    """A DeliveryPollEnv holding one active media buy with adapter delivery data."""
    from tests.factories import MediaBuyFactory, PrincipalFactory, TenantFactory
    from tests.harness import DeliveryPollEnv

    with DeliveryPollEnv(tenant_id=tenant_id, principal_id="p1") as env:
        tenant = TenantFactory(tenant_id=tenant_id)
        principal = PrincipalFactory(tenant=tenant, principal_id="p1")
        buy = MediaBuyFactory(tenant=tenant, principal=principal, status="active")
        env.set_adapter_response(buy.media_buy_id, impressions=5000)
        yield env


def _dispatch_cell(env, transport: Transport, cell: str) -> dict:
    """Run the real UC-004 When step for one Examples cell and return its ctx."""
    ctx: dict = {"env": env, "transport": transport}
    uc004_delivery.when_partition_daily_breakdown(ctx, cell)
    return ctx


def _dispatched_request_accessor():
    """The single expected-side accessor, or a failure naming what has not landed."""
    from tests.bdd.steps import _outcome_helpers

    accessor = getattr(_outcome_helpers, "dispatched_request", None)
    assert accessor is not None, (
        "tests/bdd/steps/_outcome_helpers.py exposes no dispatched_request(ctx): the "
        "expected side is still read through dispatched_kwargs/dispatched_field, which "
        "return a dict for a flat dispatch and a Pydantic model for a req= dispatch — "
        "two types from one accessor."
    )
    return accessor


@pytest.mark.requires_db
@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
@pytest.mark.parametrize("transport", WIRE_TRANSPORTS, ids=lambda t: t.value)
def test_partition_cell_routes_to_the_channel_its_declared_type_allows(
    transport: Transport, case: CellCase, integration_db
) -> None:
    """A cell that is not a ``bool`` must not become a request model an oracle can read."""
    with _seeded_env(f"t-{uuid4().hex[:8]}") as env:
        ctx = _dispatch_cell(env, transport, case.cell)

    if not case.well_formed:
        assert ctx.get("dispatched_request") is None, (
            f"{transport}/{case.cell!r}: a non-boolean cell was recorded on the well-formed "
            f"channel as {ctx.get('dispatched_request')!r}. Strict validation against "
            f"{FIELD}'s declared type must route it to the malformed channel."
        )
        malformed = ctx.get("dispatched_malformed")
        assert malformed is not None, (
            f"{transport}/{case.cell!r}: nothing was recorded on ctx['dispatched_malformed'] — "
            "the negative path is not a distinct channel, so 'the buyer sent garbage' is "
            "indistinguishable from 'the buyer sent nothing'."
        )
        assert FIELD in malformed and not isinstance(malformed[FIELD], bool), (
            f"{transport}/{case.cell!r}: the malformed channel must carry the raw, uncoerced "
            f"value so PRODUCTION rejects it on the wire, got {malformed!r}"
        )
        dispatched_request = _dispatched_request_accessor()
        with pytest.raises(Exception) as exc_info:  # noqa: B017 - the accessor's contract is "loud", not a type
            dispatched_request(ctx)
        assert "malformed" in str(exc_info.value).lower(), (
            f"{transport}/{case.cell!r}: dispatched_request() must fail loudly and NAME the "
            f"malformed channel; got {exc_info.value!r}"
        )
        return

    assert ctx.get("dispatched_malformed") is None, (
        f"{transport}/{case.cell!r}: a well-formed boolean cell was routed to the malformed channel"
    )
    req = _dispatched_request_accessor()(ctx)
    assert isinstance(req, GetMediaBuyDeliveryRequest), (
        f"{transport}/{case.cell!r}: dispatched_request() returned {type(req).__name__}, not the "
        "tool's AdCP request model — the accessor is still multi-typed."
    )
    if case.expected is ...:
        assert FIELD not in req.model_fields_set, (
            f"{transport}/{case.cell!r}: the omitted cell fabricated {FIELD} into the request. "
            "Omitted and explicit-false are separate rows of the Outline; the expected side "
            "must keep them distinguishable."
        )
        return
    assert getattr(req, FIELD) is case.expected, (
        f"{transport}/{case.cell!r}: request model carries {getattr(req, FIELD)!r} for {FIELD}, "
        f"expected the parsed bool {case.expected!r}"
    )


@pytest.mark.requires_db
@pytest.mark.parametrize("transport", WIRE_TRANSPORTS, ids=lambda t: t.value)
def test_string_true_cell_cannot_invert_the_daily_breakdown_obligation(transport: Transport, integration_db) -> None:
    """The ``"true"`` cell must make the daily-breakdown oracle fail loudly, not flip contracts.

    The oracle branches on what the scenario REQUESTED. Given the str ``"true"``,
    ``requested is True`` is ``False`` and the else-arm asserts that no package carries a
    ``daily_breakdown`` — the opposite of what the row means. Against a wire whose packages
    carry none, that inversion passes green. Once the cell is strictly typed, the request is
    on the malformed channel and the oracle cannot read an expected value at all.
    """
    with _seeded_env(f"t-{uuid4().hex[:8]}") as env:
        valid_ctx = _dispatch_cell(env, transport, "true")
        malformed_ctx = _dispatch_cell(env, transport, STRING_TRUE_CELL)

    wire = valid_ctx.get("wire_response")
    assert isinstance(wire, dict), (
        f"{transport}: the well-formed 'true' dispatch stashed no success-path wire body "
        f"({valid_ctx.get('error')!r}); there is nothing to grade the oracle against."
    )
    wire = copy.deepcopy(wire)
    packages = [pkg for d in wire.get("media_buy_deliveries") or [] for pkg in d.get("by_package") or []]
    assert packages, "response carries no packages — the oracle's loop would be vacuous either way"
    for pkg in packages:
        pkg.pop("daily_breakdown", None)

    # Graft the real success wire onto the malformed dispatch: the actual side is a
    # response the oracle can read, so the only thing left to decide the outcome is what
    # the expected side reports the scenario requested.
    graded_ctx = dict(malformed_ctx)
    graded_ctx["wire_response"] = wire
    graded_ctx.pop("error", None)

    with pytest.raises(Exception) as exc_info:  # noqa: B017 - contract is "loud", not a specific type
        uc004_delivery._assert_valid_content(graded_ctx, FIELD)
    assert "malformed" in str(exc_info.value).lower(), (
        f"{transport}: the {STRING_TRUE_CELL} cell must make the daily-breakdown obligation fail "
        f"loudly and name the malformed channel; got {exc_info.value!r}"
    )
