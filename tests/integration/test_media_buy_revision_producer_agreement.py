"""One buy, one revision — whichever producer the buyer asks.

AdCP 3.1.1 names ``revision`` as an interchangeable value across producers, not a
per-response opinion (upstream v3.1.1 tag):

  - ``dist/schemas/3.1.1/media-buy/update-media-buy-request.json`` -> ``revision``:
    "Expected current revision for optimistic concurrency ... Obtain from
    get_media_buys **or the most recent create/update response**."
  - ``dist/schemas/3.1.1/media-buy/update-media-buy-response.json`` -> ``revision``:
    "Revision number **after this update**. Use this value in subsequent
    update_media_buy requests intended to change state for optimistic
    concurrency."
  - ``dist/schemas/3.1.1/media-buy/get-media-buys-response.json`` -> ``revision``
    (REQUIRED on every ``media_buys[]`` item): "Current optimistic concurrency
    token ... Sellers increment it on mutating state changes/updates and reject
    stale tokens with CONFLICT."

So the buyer is entitled to take the token from EITHER source and hand it back on
the next update. Two producers that disagree hand out a token that the seller's own
comparison would reject — the concurrency contract inverts.

What this module grades:

  1. The producers agree, and the update response reports the revision AFTER its
     own write. A buy created, then updated twice, reports 2 then 3 on the two
     update responses and 3 from get_media_buys.
  2. A dry run reports the token the buy has NOW. It applies nothing, so it must
     report neither a bump nor the default.
  3. A success envelope is never published for a buy that is no longer there. If
     the row vanishes between the write and the response build, the tool raises
     the typed MEDIA_BUY_NOT_FOUND rather than falling back to the schema
     default — a fabricated concurrency token is worse than a loud failure
     (CLAUDE.md "No Quiet Failures"). Graded at BOTH response-build sites that
     re-read the row: the field/budget update site and the pause/resume site.
  4. A pause/resume success reports the same token, from its own construction
     site. It is a separate ``UpdateMediaBuySuccess`` build in the same tool
     (media_buy_update.py, the ``req.paused is not None`` branch), so the
     obligation above says nothing about it until it is driven directly.

Graded on the wire wherever a wire exists, not on the typed payload:
``UpdateMediaBuySuccess.revision`` is a declared field with a default, so a
typed-payload assertion cannot tell a real value from the default.
``wire_response`` is the artifact DataPart (A2A) and the ``structured_content``
(MCP) — the bytes the buyer receives. The dry-run case is the one exception and
says why in its own docstring.

GH #1941 (review finding F4)
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from functools import wraps
from typing import Any

import pytest

from src.core.database.repositories.media_buy import MediaBuyRepository
from src.core.schemas import UpdateMediaBuyRequest
from src.core.schemas._base import GetMediaBuysRequest
from tests.factories.request import fresh_idempotency_key
from tests.harness.media_buy_create_update_list import MediaBuyCreateUpdateListEnv
from tests.harness.transport import Transport

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

# Transport coverage is decided PER SCENARIO by what the scenario actually drives,
# not by one module-wide constant. A single list applied to everything silently
# under-grades whichever cases could reach further — and REST is where a wrapper most
# easily drops a field, so excluding it by default is the expensive direction to be
# wrong in.
#
# A2A (artifact DataPart) and MCP (structured_content) stash a REAL wire body.
#
# Scenarios that POLL get_media_buys stop here: that tool has no REST route, a
# pre-existing surface fact, so a REST parametrization would grade nothing.
_GET_MEDIA_BUYS_TRANSPORTS = [Transport.A2A, Transport.MCP]

# Scenarios that drive update_media_buy ONLY reach further: it routes
# PUT /api/v1/media-buys/{id} (proven by
# test_harness_rest_refusal.py::TestNonListRestRoutingIsPreserved), so REST is a real
# surface for them and is graded.
_UPDATE_ONLY_TRANSPORTS = [Transport.A2A, Transport.MCP, Transport.REST]

# A buy that has already been mutated several times. Distinctive on purpose: it is
# neither the schema default (1) nor adjacent to it, so the dry-run assertion below
# tells "reports the current value" apart from "reports the default" and from
# "reports a bump".
_SEEDED_REVISION = 7


def _create_kwargs(product: Any, *, domain: str) -> dict[str, Any]:
    """A minimal, fully-valid create for the seeded product chain."""
    now = datetime.now(UTC)
    return {
        "brand": {"domain": domain},
        "packages": [{"product_id": product.product_id, "budget": 5000.0, "pricing_option_id": "cpm_usd_fixed"}],
        "start_time": (now + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end_time": (now + timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "po_number": "REVISION-AGREEMENT-1",
    }


# The two campaign-level toggles the tool serves from ONE construction site (the
# ``req.paused is not None`` branch), with the persisted status each one is legal
# from (adcp.server.helpers.MEDIA_BUY_STATE_MACHINE: "active" -> pause, "paused"
# -> resume). Both are driven because the branch is entered by either and the
# state-machine gate in front of it differs per case.
_TOGGLE_CASES = [
    pytest.param("active", True, id="pause"),
    pytest.param("paused", False, id="resume"),
]


def _seed_buy(env: MediaBuyCreateUpdateListEnv, tenant: Any, principal: Any, *, status: str) -> Any:
    """A persisted buy in ``status``, already carrying a distinctive revision.

    Seeded rather than created through the tool because the toggle branch is
    gated on the persisted status (pause needs "active", resume needs "paused")
    and a freshly created buy is in neither. ``_SEEDED_REVISION`` is the same
    distinctive value the dry-run case uses, for the same reason: it is far from
    the ``UpdateMediaBuySuccess.revision`` schema default, so a reported default
    cannot be mistaken for a real read.
    """
    from tests.factories import MediaBuyFactory

    buy = MediaBuyFactory(tenant=tenant, principal=principal, status=status, revision=_SEEDED_REVISION)
    env._commit_factory_data()  # noqa: SLF001 — the harness's factory/session flush seam
    return buy


class _VanishingRow:
    """The buy's row disappears from the repository the moment this is armed.

    Every ``UpdateMediaBuySuccess`` site in the tool re-reads the buy AFTER
    applying its write, and every one of them must answer a re-read of ``None``
    with the typed MEDIA_BUY_NOT_FOUND instead of a success envelope carrying a
    fabricated token. The sites differ only in WHERE the disappearance can be
    armed — each writes through a different seam — so the patch and the verdict
    live here once instead of once per site (CLAUDE.md DRY invariant).
    """

    def __init__(self) -> None:
        self.armed = False

    def arming(self, seam: Callable[..., Any]) -> Callable[..., Any]:
        """``seam``, wrapped so the row vanishes immediately AFTER it runs.

        Arming after (not before) is what keeps the injection honest: every
        lookup the tool makes on its way in — ownership, state machine, currency
        — still resolves against the real row, exactly as a concurrent DELETE
        landing at that instant would leave it.
        """

        @wraps(seam)
        def _armed(*args: Any, **kwargs: Any) -> Any:
            result = seam(*args, **kwargs)
            self.armed = True
            return result

        return _armed

    @contextmanager
    def patched(self) -> Iterator[pytest.MonkeyPatch]:
        """Patch the repository lookup for the block, yielding the patcher for the seam."""
        real_get_by_id = MediaBuyRepository.get_by_id

        def get_by_id_unless_vanished(repo: MediaBuyRepository, media_buy_id: str) -> Any:
            return None if self.armed else real_get_by_id(repo, media_buy_id)

        with pytest.MonkeyPatch.context() as patcher:
            patcher.setattr(MediaBuyRepository, "get_by_id", get_by_id_unless_vanished)
            yield patcher

    def assert_reported_not_found(self, result: Any, transport: Transport) -> None:
        """The call answered the vanished row with the typed not-found, on the wire."""
        assert self.armed, (
            "the injection never armed — the seam it hangs on was not reached, so this test "
            "did not exercise the post-write re-read at all"
        )
        assert result.is_error, (
            f"{transport}: the update published a SUCCESS envelope for a buy whose row is gone: "
            f"{result.wire_response!r}. A success here carries a media_buy_id, a status and a "
            f"revision the seller cannot honour"
        )
        result.assert_wire_error("MEDIA_BUY_NOT_FOUND", recovery="correctable")


@pytest.mark.parametrize("transport", _GET_MEDIA_BUYS_TRANSPORTS)
def test_update_responses_and_get_media_buys_report_the_same_revision(integration_db, transport):
    """Two updates report 2 then 3; get_media_buys reports 3 for the same buy.

    The three assertions are one obligation: whichever producer the buyer asks,
    the token is the same. Today every update response reports 1 — the schema
    default on ``UpdateMediaBuySuccess.revision`` — because no construction site
    passes the row's value, so a buyer who reads the token from an update
    response and hands it back gets a stale-token CONFLICT.

    Back-to-back updates (not one) are what discriminate "reports the post-write
    value" from "reports any plausible constant": a producer stuck at the create
    value passes a single-update check whenever the buy was only written once.
    """
    with MediaBuyCreateUpdateListEnv() as env:
        _tenant, _principal, product, _pricing = env.setup_media_buy_data()

        created = env.call_via(transport, **_create_kwargs(product, domain="revision-agreement.example.com"))
        media_buy_id = created.require_wire()["media_buy_id"]

        first = env.call_via(
            transport,
            req=UpdateMediaBuyRequest(
                account={"account_id": "acct_test"},
                idempotency_key=fresh_idempotency_key(),
                media_buy_id=media_buy_id,
                end_time="2026-12-01T00:00:00Z",
            ),
        )
        second = env.call_via(
            transport,
            req=UpdateMediaBuyRequest(
                account={"account_id": "acct_test"},
                idempotency_key=fresh_idempotency_key(),
                media_buy_id=media_buy_id,
                end_time="2026-12-15T00:00:00Z",
            ),
        )
        listed = env.call_via(transport, req=GetMediaBuysRequest(media_buy_ids=[media_buy_id]))

        first_revision = first.require_wire()["revision"]
        second_revision = second.require_wire()["revision"]
        listed_buys = listed.require_wire()["media_buys"]

    assert (first_revision, second_revision) == (2, 3), (
        f"{transport}: the two update responses must report the revision AFTER each own write "
        f"(spec 3.1.1 update-media-buy-response.json: 'Revision number after this update'), "
        f"got {(first_revision, second_revision)} — a constant here is the schema default "
        f"UpdateMediaBuySuccess.revision = 1 reaching the wire because no site passes the row's value"
    )
    assert len(listed_buys) == 1, f"{transport}: expected exactly the buy under test, got {listed_buys}"
    assert listed_buys[0]["revision"] == second_revision, (
        f"{transport}: get_media_buys reports revision {listed_buys[0]['revision']} while the last "
        f"update response reported {second_revision}. The pin names the two as interchangeable sources "
        f"of the same token (update-media-buy-request.json: 'Obtain from get_media_buys or the most "
        f"recent create/update response'), so a buyer taking either must get the same value"
    )


@pytest.mark.parametrize("transport", _GET_MEDIA_BUYS_TRANSPORTS)
def test_create_response_reports_the_persisted_confirmed_at_and_revision(integration_db, transport):
    """The CREATE producer is the same producer, for both persisted fields.

    This module graded update, dry-run and pause/resume, and never the branch the buyer
    meets FIRST. That gap is why the create response could mint its own values for
    two years of review rounds: ``confirmed_at`` defaulted to ``datetime.now(UTC)``
    and ``revision`` to ``1``, neither read from the row the repository had just
    written one function earlier.

    ``confirmed_at`` is the sharper of the two. The pinned
    ``create-media-buy-response.json`` @ 3.1.1 types it ``["string", "null"]`` and
    calls it stable once set, and ``models.py`` stamps it only when the buy reaches a
    seller-COMMITTED status. A buy that is not committed therefore has a NULL column —
    and the old default reported an instant anyway, asserting on the wire that the
    seller had committed to a buy it had not. That is the same falsehood
    ``CreateMediaBuySubmitted``'s own docstring refuses.

    Both fields are compared against ``get_media_buys``, not against a literal, because
    the obligation is producer AGREEMENT: a literal would pin today's lifecycle rather
    than the invariant that the two producers read one row.
    """
    with MediaBuyCreateUpdateListEnv() as env:
        _tenant, _principal, product, _pricing = env.setup_media_buy_data()

        created = env.call_via(transport, **_create_kwargs(product, domain="create-agreement.example.com"))
        create_body = created.require_wire()
        media_buy_id = create_body["media_buy_id"]

        listed = env.call_via(transport, req=GetMediaBuysRequest(media_buy_ids=[media_buy_id]))
        listed_buys = listed.require_wire()["media_buys"]

    assert len(listed_buys) == 1, f"{transport}: expected exactly the buy under test, got {listed_buys}"
    listed_buy = listed_buys[0]

    assert "confirmed_at" in create_body, (
        f"{transport}: the create response dropped confirmed_at, which the pinned "
        f"create-media-buy-response.json lists in `required` (keys: {sorted(create_body)})"
    )
    assert create_body["confirmed_at"] == listed_buy["confirmed_at"], (
        f"{transport}: create reported confirmed_at={create_body['confirmed_at']!r} while "
        f"get_media_buys reports {listed_buy['confirmed_at']!r} for the same buy. Two producers "
        f"for a field the pin calls stable after it is set; the create branch must read the column, "
        f"not stamp its own clock"
    )
    assert create_body["revision"] == listed_buy["revision"], (
        f"{transport}: create reported revision={create_body['revision']!r} while get_media_buys "
        f"reports {listed_buy['revision']!r}. The pin names the two as interchangeable sources of "
        f"one optimistic-concurrency token"
    )


@pytest.mark.parametrize("transport", _UPDATE_ONLY_TRANSPORTS)
def test_update_raises_media_buy_not_found_when_the_row_vanishes_mid_transaction(integration_db, transport):
    """A row that disappears under the write must not yield a success envelope.

    The tool re-reads the buy AFTER applying the write to report the post-write
    revision and status. That re-read can return None — the row was deleted
    between the write and the response build. Reporting success then publishes a
    media_buy_id, a status and a concurrency token for a buy that no longer
    exists; the token in particular is fabricated (the schema default), and the
    buyer's next update carrying it is unanswerable.

    So the correct answer is the typed AdCPMediaBuyNotFoundError the tool already
    raises when the row is missing on entry (media_buy_update.py, via
    ``get_by_id_or_raise``) — the same buyer-facing code for the same fact.

    The disappearance is injected at the repository seam rather than simulated
    with a fake row: ``update_fields`` branches it, so the re-read that follows the
    write returns None exactly as a concurrent DELETE would, while every lookup
    BEFORE the write still resolves normally (ownership, currency).
    """
    vanish = _VanishingRow()

    with MediaBuyCreateUpdateListEnv() as env:
        _tenant, _principal, product, _pricing = env.setup_media_buy_data()

        created = env.call_via(transport, **_create_kwargs(product, domain="revision-vanish.example.com"))
        media_buy_id = created.require_wire()["media_buy_id"]

        with vanish.patched() as patcher:
            patcher.setattr(MediaBuyRepository, "update_fields", vanish.arming(MediaBuyRepository.update_fields))

            result = env.call_via(
                transport,
                req=UpdateMediaBuyRequest(
                    account={"account_id": "acct_test"},
                    idempotency_key=fresh_idempotency_key(),
                    media_buy_id=media_buy_id,
                    end_time="2026-12-01T00:00:00Z",
                ),
            )

    vanish.assert_reported_not_found(result, transport)


@pytest.mark.parametrize("transport", _GET_MEDIA_BUYS_TRANSPORTS)
@pytest.mark.parametrize(("seeded_status", "paused"), _TOGGLE_CASES)
def test_pause_resume_response_reports_the_rows_revision_and_agrees_with_get_media_buys(
    integration_db, transport, seeded_status, paused
):
    """A pause/resume success carries the buy's real token, and get_media_buys confirms it.

    Same obligation as the update/list agreement above, at the tool's OTHER
    success site: the ``req.paused is not None`` branch builds its own
    ``UpdateMediaBuySuccess``, so nothing the field-update site does constrains
    it. The buyer is entitled to take the token off "the most recent
    create/update response" (3.1.1 update-media-buy-request.json) — a pause
    response that reports a value the seller would not accept back inverts the
    concurrency contract just as surely as a budget update doing so.

    An ordinary budget update is driven FIRST so the token is not sitting on the
    value it was seeded with when the toggle reports it. That makes three
    distinct answers distinguishable in the toggle's own response: the schema
    default (1), the pre-update seed (``_SEEDED_REVISION``) and the buy's actual
    current token. The final equality against get_media_buys is the producer
    agreement itself and is asserted on the wire body of both producers.

    The expected number is not written as a literal: how many times an ordinary
    budget update moves the token is that path's business, and pinning it here
    would make this case fail for a reason it does not grade. What it pins is
    that the toggle reports the row — strictly past the seed, and equal to what
    the other producer reports for the same buy.
    """
    with MediaBuyCreateUpdateListEnv() as env:
        tenant, principal, _product, _pricing = env.setup_media_buy_data()
        buy = _seed_buy(env, tenant, principal, status=seeded_status)

        bumped = env.call_via(
            transport,
            req=UpdateMediaBuyRequest(
                account={"account_id": "acct_test"},
                idempotency_key=fresh_idempotency_key(),
                media_buy_id=buy.media_buy_id,
                end_time="2026-12-01T00:00:00Z",
            ),
        )
        toggled = env.call_via(
            transport,
            req=UpdateMediaBuyRequest(
                account={"account_id": "acct_test"},
                idempotency_key=fresh_idempotency_key(),
                media_buy_id=buy.media_buy_id,
                paused=paused,
            ),
        )
        listed = env.call_via(transport, req=GetMediaBuysRequest(media_buy_ids=[buy.media_buy_id]))

        bumped_revision = bumped.require_wire()["revision"]
        toggled_revision = toggled.require_wire()["revision"]
        listed_buys = listed.require_wire()["media_buys"]

    assert bumped_revision > _SEEDED_REVISION, (
        f"{transport}: the budget update that precedes the toggle reported {bumped_revision}, which is "
        f"not past the seeded {_SEEDED_REVISION} — the setup for this case did not apply a write, so "
        f"the toggle assertion below could no longer tell the row's token from the seeded one"
    )
    assert toggled_revision > _SEEDED_REVISION, (
        f"{transport}: the {'pause' if paused else 'resume'} response reported revision "
        f"{toggled_revision}, which is not past the pre-update seed {_SEEDED_REVISION} — it is "
        f"reporting a stale or fabricated token (the UpdateMediaBuySuccess schema default is 1) "
        f"rather than reading the buy's current revision at response-build time"
    )
    assert len(listed_buys) == 1, f"{transport}: expected exactly the buy under test, got {listed_buys}"
    assert listed_buys[0]["revision"] == toggled_revision, (
        f"{transport}: get_media_buys reports revision {listed_buys[0]['revision']} while the "
        f"{'pause' if paused else 'resume'} response reported {toggled_revision}. The pin names the two "
        f"as interchangeable sources of the same token (update-media-buy-request.json: 'Obtain from "
        f"get_media_buys or the most recent create/update response'), so a buyer taking either must "
        f"get the same value"
    )


@pytest.mark.parametrize("transport", _UPDATE_ONLY_TRANSPORTS)
@pytest.mark.parametrize(("seeded_status", "paused"), _TOGGLE_CASES)
def test_pause_resume_reports_media_buy_not_found_when_the_row_vanishes_mid_transaction(
    integration_db, transport, seeded_status, paused
):
    """The toggle site refuses to publish success for a row that is gone — same as the field site.

    The obligation is the one the field-update case above states, re-graded here
    because the toggle branch builds its envelope independently. It also pins the
    ORDER of the two helpers in that construction: ``_revision_or_raise`` runs
    BEFORE ``_adcp_status_and_actions``. Only the first tolerates a ``None`` row
    and converts it into the buyer-facing MEDIA_BUY_NOT_FOUND; the second reads
    ``buy.status`` unguarded, so calling it first turns a vanished row into an
    ``AttributeError`` — an untyped INTERNAL_ERROR on the wire for a condition the
    buyer can act on.

    The seam that branches the disappearance is the adapter call, not a repository
    write: the toggle branch delegates the state change to the ad server and
    writes no column of its own, so the adapter call IS its write. Arming there
    reproduces a DELETE landing between the ad server accepting the toggle and
    the response being built.
    """
    vanish = _VanishingRow()

    with MediaBuyCreateUpdateListEnv() as env:
        tenant, principal, _product, _pricing = env.setup_media_buy_data()
        buy = _seed_buy(env, tenant, principal, status=seeded_status)

        adapter = env.mock["update_adapter"].return_value
        adapter.update_media_buy.side_effect = vanish.arming(adapter.update_media_buy.side_effect)

        with vanish.patched():
            result = env.call_via(
                transport,
                req=UpdateMediaBuyRequest(
                    account={"account_id": "acct_test"},
                    idempotency_key=fresh_idempotency_key(),
                    media_buy_id=buy.media_buy_id,
                    paused=paused,
                ),
            )

    vanish.assert_reported_not_found(result, transport)
