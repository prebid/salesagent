"""Envs that carry a registration from INGEST to DELIVERY, over a real origin.

Epic D lane C2 turns the persistence and stash boundaries
into value-takers. Every one of those boundaries is INVISIBLE from either end
in isolation: a repository test proves what a row holds, and a sender test
proves what a sender does with a row, but neither can see a registration whose
credential half was dropped SOMEWHERE BETWEEN the two. The only observation
that spans the gap is the one the buyer makes — did the POST that finally
arrived carry a signature it can verify.

So both envs here are the same shape: a real local origin, the real production
path from the buyer's registration call through to the send, and the origin's
captured request as the sole authority. Nothing about the intermediate
representation is asserted; the graders would survive the raw dict, the wire
dump and the value alike, which is what makes them a regression guard for a
refactor whose whole point is to change that representation.

``LocalOriginMixin`` supplies the origin (real TLS, private-range hatch open
for its lifetime); the domain envs supply production. Neither is re-derived.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from tests.factories.mint import mint
from tests.factories.webhook import PushNotificationConfigRequestFactory
from tests.harness._mixins import LocalOriginMixin
from tests.harness.media_buy_dual import MediaBuyDualEnv
from tests.helpers.adcp_factories import create_test_media_buy_request_dict

if TYPE_CHECKING:
    pass


def _drop_registered_auth(stashed: Any) -> Any:
    """Return *stashed* with its credential half removed, whatever shape it is.

    Deliberately shape-agnostic. The mutation this powers is the reverse-TDD
    control for the A2A grader: it must express "the auth fields did not
    survive into the stash" against the RAW PROTOBUF stashed today and against
    the ``ValidatedWebhookRegistration`` stashed after lane C2, or the control
    would silently stop mutating anything the day the representation changes —
    and a control that mutates nothing reports a vacuous grader as a real one.
    """
    from src.core.webhooks.registration import ValidatedWebhookRegistration

    if isinstance(stashed, ValidatedWebhookRegistration):
        from src.core.schema_helpers import to_push_notification_config

        # Rebuild from the value's own wire dump minus the auth block, rather than
        # re-listing its fields: the value HOLDS the library PushNotificationConfig,
        # so a field added there (operation_id, token, ...) keeps surviving this
        # mutation instead of being dropped by a stale field list here.
        document = {key: value for key, value in stashed.to_stash().items() if key != "authentication"}
        coerced = to_push_notification_config(document)
        assert coerced is not None
        return ValidatedWebhookRegistration(config=coerced)

    stripped = type(stashed)()
    stripped.CopyFrom(stashed)
    stripped.ClearField("authentication")
    return stripped


class _AuthDroppingStash(dict):
    """A ``_task_push_configs`` that loses the credential half on the way in."""

    def __setitem__(self, key: Any, value: Any) -> None:
        super().__setitem__(key, _drop_registered_auth(value))


class MediaBuyPushRegistrationEnv(LocalOriginMixin, MediaBuyDualEnv):
    """``create_media_buy`` / ``update_media_buy`` register; the WORKFLOW STEP delivers.

    Both tools stash the registration on the workflow step they create, and
    ``ContextManager._send_push_notifications`` reads that stash back when the
    step's status changes. Production's own ``update_workflow_step`` calls are
    mocked out by the media-buy envs (so a create does not fire a webhook
    mid-test), which is why the status change is driven here, explicitly,
    through the REAL context manager — the same entry point the admin approval
    flow and the adapter completion path both use.
    """

    def register_delivery_target(self, count: int = 1) -> Any:
        """Store *count* active ``PushNotificationConfig`` rows pointing at the origin.

        The rows stand in for the earlier ``create_media_buy`` calls that
        registered them, which is the only way a real buyer's
        ``update_media_buy`` webhook is ever delivered — update never upserts one
        itself. What a row HOLDS is not what gets delivered to: the config the
        sender receives is rebuilt from the workflow step's stash.

        THE ROW COUNT IS NOT THE DELIVERY COUNT, and this used to take no
        argument BECAUSE it was: "``_send_push_notifications`` sends once per
        active row per mapping, so the row count is the delivery count — one row
        keeps ``delivery_attempts == 1`` a statement about signing rather than
        about fan-out." That described a BUG and then arranged for it not to
        show. The production loop it described read no part of the row it
        iterated; it sent the step's own stashed registration once per row, so a
        principal with three configs got three copies of one event (measured on
        the storyboard tenant: the same payload to the same URL three times
        inside 17ms, failing AdCP 3.1.1
        ``webhook_emission::expect_no_duplicate_webhook_on_replay``). Seeding
        exactly one row, with no way to seed two, is what kept every test here
        green through it.

        The loop is gone. Delivery is once per MAPPED OBJECT, and the stored rows
        are not consulted on this path at all. ``count`` exists so a test can say
        that out loud: seed several and the answer must still be one delivery.

        Returns the single row at ``count=1`` (every existing caller's shape), and
        the list otherwise.
        """
        from tests.factories import PushNotificationConfigFactory

        tenant, principal = self.setup_default_data()
        rows = [
            PushNotificationConfigFactory(
                tenant=tenant,
                principal=principal,
                url=self.webhook_url,
                is_active=True,
            )
            for _ in range(count)
        ]
        return rows[0] if count == 1 else rows

    def push_step(self, tool_name: str) -> Any:
        """The workflow step ``tool_name`` created, read fresh from the database.

        ``expire_all`` first: production wrote the row through its own session,
        so the env-bound session would otherwise answer from its identity map.
        """
        from src.core.database.models import WorkflowStep

        self.get_session().expire_all()
        steps = [step for step in self.get_workflow_steps() if step.tool_name == tool_name]
        assert len(steps) == 1, (
            f"expected exactly one {tool_name} workflow step to grade, found {len(steps)} — "
            f"a second step would make the delivery count ambiguous"
        )
        step: WorkflowStep = steps[0]
        return step

    def stashed_push_config(self, step: Any) -> dict[str, Any]:
        """What the tool actually stashed under ``push_notification_config``."""
        stashed = (step.request_data or {}).get("push_notification_config")
        assert isinstance(stashed, dict), (
            f"the workflow step stashed {stashed!r} under push_notification_config — "
            f"there is nothing for the delivery path to read back"
        )
        return stashed

    def drop_stashed_credential_half(self, step: Any) -> None:
        """Remove the auth block from the stash — the reverse-TDD mutation.

        Keyed on the ``authentication`` block that every producer of this key
        writes today (``model_dump(mode="json")`` of the library
        ``PushNotificationConfig``), so the mutation means the same thing before
        and after lane C2 changes who writes it. Asserts the block was there:
        a mutation that removed nothing would make the control vacuous.
        """
        stashed = self.stashed_push_config(step)
        assert "authentication" in stashed, (
            "nothing to drop — the stash already carries no authentication block, so this control would grade nothing"
        )
        request_data = dict(step.request_data)
        request_data["push_notification_config"] = {
            key: value for key, value in stashed.items() if key != "authentication"
        }
        step.request_data = request_data
        self.get_session().add(step)
        self.get_session().commit()

    def poison_stashed_registration(self, step: Any) -> None:
        """Rewrite the stash into a shape the INGEST GATE refuses.

        HMAC-SHA256 with the credential half removed — the one document the
        registration gate rejects outright, so ``from_stash`` raises
        ``AdCPValidationError`` at delivery time. Distinct from
        :meth:`drop_stashed_credential_half`, which produces a stash the gate
        ACCEPTS (as unauthenticated) and therefore delivers unsigned: that one
        grades signing, this one grades what happens when rehydration REFUSES.
        """
        stashed = self.stashed_push_config(step)
        request_data = dict(step.request_data)
        request_data["push_notification_config"] = PushNotificationConfigRequestFactory.payload(
            url=stashed["url"], authentication={"schemes": ["HMAC-SHA256"]}
        )
        step.request_data = request_data
        self.get_session().add(step)
        self.get_session().commit()

    def minimal_create_kwargs(self, product: Any, pricing_option: Any, **overrides: Any) -> dict[str, Any]:
        """The minimal valid ``create_media_buy`` kwargs against this env's seeded chain.

        Lives on the env rather than in each grader because every registration
        case needs the identical valid request and differs only in what it hangs
        off ``push_notification_config`` — a per-file copy is the shape the DRY
        invariant forbids, and it is also how two graders drift into dispatching
        against subtly different buys. ``idempotency_key`` is fresh per call, so
        a case that registers TWICE (row-identity) is two executions rather than
        one execution and one cached replay.

        ``pricing_option_id`` is derived here rather than imported: the ORM row
        has no id column of its own, so the request-side identifier is built from
        (model, currency, fixed-vs-auction). The same derivation exists in
        ``tests/bdd/steps/generic/given_media_buy.py``, and it is deliberately
        NOT imported from there — a BDD step module is not an API, and importing
        from one would make this harness a downstream of the step registry.
        """
        return create_test_media_buy_request_dict(
            product_ids=[product.product_id],
            pricing_option_id=(
                f"{pricing_option.pricing_model}_{pricing_option.currency.lower()}_"
                + ("fixed" if pricing_option.is_fixed else "auction")
            ),
            total_budget=5000.0,
            # Spread, not keywords: an ``overrides`` carrying ``start_time`` must
            # OVERRIDE it, not raise "got multiple values for keyword argument".
            **{
                "start_time": mint((datetime.now(UTC) + timedelta(days=1)).isoformat()),
                "end_time": mint((datetime.now(UTC) + timedelta(days=30)).isoformat()),
                "idempotency_key": mint(f"fo99-{uuid.uuid4().hex}"),
                **overrides,
            },
        )

    def restash_authentication(self, step: Any, authentication: dict[str, Any]) -> None:
        """Rewrite the stash's ``authentication`` block wholesale — a LEGACY row.

        One place knows how a stashed registration is rewritten and committed. A
        row whose block names an unrecognised scheme, or holds a credential under
        the pinned ``minLength: 32``, is manufactured with this — both are shapes
        the untyped A2A path really wrote (it forwards the buyer's raw dict), and
        both are what Epic D lane C4 stops delivering.

        Asserts the stash already carried a block: rewriting one that was never
        there would grade a document no producer writes.
        """
        stashed = self.stashed_push_config(step)
        assert isinstance(stashed.get("authentication"), dict), (
            f"the stash carries authentication={stashed.get('authentication')!r} — there is no "
            f"registered block to rewrite, so this helper would manufacture a row no producer wrote"
        )
        request_data = dict(step.request_data)
        request_data["push_notification_config"] = {**stashed, "authentication": dict(authentication)}
        step.request_data = request_data
        self.get_session().add(step)
        self.get_session().commit()

    def persisted_config_rows(self) -> list[Any]:
        """Every push-notification row this env's tenant currently holds."""
        from sqlalchemy import select

        from src.core.database.models import PushNotificationConfig

        self.get_session().expire_all()
        return list(self.get_session().scalars(select(PushNotificationConfig)).all())

    def step_status(self, step: Any) -> str:
        """The step's status, read fresh — production wrote it in another session."""
        self.get_session().expire_all()
        self.get_session().refresh(step)
        return step.status

    def complete_step(self, step: Any) -> None:
        """Drive the step to ``completed`` through the REAL context manager.

        This is the call that fires ``_send_push_notifications``. It runs with
        no event loop running, so production takes its ``asyncio.run`` branch
        and the delivery has finished by the time this returns — the assertions
        need no polling.
        """
        from src.core.context_manager import get_context_manager

        self._commit_factory_data()
        get_context_manager().update_workflow_step(step_id=step.step_id, status="completed")
