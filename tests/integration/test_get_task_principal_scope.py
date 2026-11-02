"""One principal's task is not readable, listable or completable by another, inside a tenant.

#1808. protocol/get-task-status-request.json @ AdCP 3.1.1 attaches the
obligation to ``account``: "Sellers MUST return REFERENCE_NOT_FOUND for a task_id that
exists only under a different account or principal." Two storyboard steps in
dist/compliance/3.1.1/domains/media-buy/scenarios/get_products_async.yaml grade it:

  * ``get_products_task_status_wrong_account`` — poll the same task_id from a different
    account, expect error_code REFERENCE_NOT_FOUND, "Sellers MUST NOT reveal whether the
    task exists under another account or principal."
  * ``list_products_task_wrong_account`` — list the same task_id from a different account,
    expect total_matching 0, "Sellers MUST scope task reconciliation to the authenticated
    account + principal pair."

GRADED SHORT OF THAT STEP, deliberately and recorded here rather than left to be inferred:
the listing assertions below check that the id is absent from the intruder's body and
present in the owner's, NOT that a count reads zero. list_tasks' response does not satisfy
its own pinned schema — it omits the required ``query_summary`` and ``pagination``
(FIXME(#2201)) — so ``total_matching`` does not exist to assert on, and the count field
that does exist is unsettled. The disclosure half of the obligation is graded now because
it can be stated without naming a field; the volume half is graded when the response
conforms.

WorkflowRepository.get_by_step_id and list_by_tenant/count_by_tenant filtered
``DBContext.tenant_id`` and nothing else, so inside one tenant every authenticated
principal could read, list and COMPLETE every other principal's tasks. Nothing covered it,
and the tenant-isolation guard was green while naming the leaking method as exemplary.

ON THE WIRE, not on an exception class. tests/CLAUDE.md: the harness no longer reconstructs
AdCPSalesAgentError from a wire response, and a test asserting on a rebuilt object grades
the reconstruction rather than the buyer-facing envelope. A refusal that is correct in
Python and wrong on the wire is exactly the failure this obligation is about. Asserted
through ``result.assert_wire_error`` rather than the bare envelope primitive, which adds
the CODE_TABLE emittability check and fails loudly when no envelope was captured at all --
the way a "passing" error test can grade nothing.

INTEGRATION, against real Postgres: the defect and the fix are both a WHERE clause. A
mocked repository returns whatever the test told it to and would pass identically before
and after.
"""

import uuid
from typing import Any

import pytest

from tests.factories import PrincipalFactory, TenantFactory
from tests.harness.task_management import TaskManagementEnv
from tests.harness.transport import Transport

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

OWNER_ID = "principal_owner"
INTRUDER_ID = "principal_intruder"


@pytest.fixture
def tenant_id(integration_db) -> str:
    """A tenant of this test's own.

    The integration database is shared and is not rolled back between tests, so a fixed
    tenant id collides the moment a second test seeds it — which is what surfaced once the
    tests ahead of these started passing and reaching their own seeding. Per-test isolation
    is this suite's existing idiom (see test_idempotency_race) and removes the whole class
    rather than making one get-or-create smarter.
    """
    return f"task_scope_{uuid.uuid4().hex[:8]}"


class _GetTaskEnv(TaskManagementEnv):
    """TaskManagementEnv aimed at get_task_status rather than list_tasks.

    Subclassed HERE rather than added to tests/harness: the base already dispatches any
    registered MCP tool by name, so this needs the name and nothing else.
    """

    MCP_TOOL = "get_task_status"


class _CompleteTaskEnv(TaskManagementEnv):
    """The write half. Same reasoning as :class:`_GetTaskEnv`."""

    MCP_TOOL = "complete_task"


def _seed(env, tenant_id: str, principal_id: str) -> str:
    """Seed a task owned by *principal_id*, and both principals if not already there.

    IDEMPOTENT on the tenant and principals, because a test that seeds a task for each of
    the two principals calls this twice against one env. Both principal rows exist either
    way: an intruder with no row has no access_token, and ``_become`` would then fall back
    to a mocked identity instead of the real header -> token -> DB chain — which is how the
    first version of this file ended up never authenticating as anyone but the owner.

    Written through PRODUCTION's creators — ``build_context`` and
    ``WorkflowRepository.create_step`` — not constructed inline: the repository-pattern
    guard forbids new inline session writes, and using the real path means the ownership
    this test asserts on is established the way production establishes it.
    """
    from sqlalchemy import select

    from src.core.database.models import Principal, Tenant
    from src.core.database.repositories.workflow import WorkflowRepository, build_context

    session = env.get_session()
    tenant = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
    if tenant is None:
        tenant = TenantFactory(tenant_id=tenant_id)
    for pid in (OWNER_ID, INTRUDER_ID):
        existing = session.scalars(select(Principal).filter_by(tenant_id=tenant_id, principal_id=pid)).first()
        if existing is None:
            PrincipalFactory(tenant=tenant, principal_id=pid)

    context = build_context(session, tenant_id=tenant_id, principal_id=principal_id)
    step = WorkflowRepository(session, tenant_id).create_step(
        persistent_context=context,
        step_type="tool_call",
        owner="principal",
        status="requires_approval",
        tool_name="create_media_buy",
        request_data={"secret_brief": "the other buyer's brief"},
    )
    session.commit()
    return step.step_id


def _blind_echoed_ids(envelope: Any) -> Any:
    """The envelope with every echoed ``step_id`` replaced by a constant.

    ``details.step_id`` is the id the CALLER sent, so of course it differs between two
    lookups of different ids — it is the one field that legitimately cannot match and is
    not a disclosure, because the buyer already knows what they asked for. Everything else
    must be identical. Blinding it here rather than comparing field-by-field is what keeps
    the comparison total: a field added to this envelope later is compared automatically
    instead of being silently omitted from a hand-written list.
    """
    if isinstance(envelope, dict):
        return {k: ("<echoed>" if k == "step_id" else _blind_echoed_ids(v)) for k, v in envelope.items()}
    if isinstance(envelope, list):
        return [_blind_echoed_ids(item) for item in envelope]
    return envelope


def _body_mentions(body: Any, task_id: str) -> bool:
    """Whether *task_id* appears ANYWHERE in *body* — at any depth, in any field.

    The list obligation is a DISCLOSURE claim, so it is asserted the way disclosure
    actually works: either the buyer can see the id somewhere in what came back, or they
    cannot. Stated that way it names no field, so it survives the response being reshaped
    — which matters here specifically, because list_tasks' body does not satisfy its own
    pinned schema (FIXME(#2201)) and every field name in it is subject to change. A
    ``payload["tasks"]``/``payload["total"]`` assertion would have to be rewritten by
    whoever fixes that, and rewritten assertions are where obligations quietly weaken.

    Substring, not equality: an id leaked inside a message ("task step_x belongs to
    another principal") is the same disclosure as an id leaked in a field. Keys are
    searched as well as values, since an id can be a map key.

    Same technique as :func:`_blind_echoed_ids` — compare what the buyer can observe,
    structurally, instead of field by field.
    """
    if isinstance(body, dict):
        return any(task_id in str(key) or _body_mentions(value, task_id) for key, value in body.items())
    if isinstance(body, list):
        return any(_body_mentions(item, task_id) for item in body)
    return isinstance(body, str) and task_id in body


def _assert_two_distinct_principals(env) -> None:
    """Prove the two principals authenticate as DIFFERENT callers before asserting a refusal.

    The precondition the previous version of this file silently lacked. A cross-principal
    test that authenticates as one principal twice cannot fail, and passes loudest on a
    build that leaks. So the tokens are compared here: they come from two separate Principal
    rows and the production chain resolves each to its own identity.
    """
    owner_token = _become(env, OWNER_ID)
    intruder_token = _become(env, INTRUDER_ID)
    assert owner_token != intruder_token, (
        "both principals presented the SAME credential, so every call below authenticates "
        "as one caller and the cross-principal assertions cannot fail"
    )


def _become(env, principal_id: str) -> str:
    """Re-authenticate the env AS *principal_id*, and return the token it will present.

    ``env.switch_principal`` is the public accessor for this: it clears the identity cache
    so the next access re-runs the auth-token lookup against the committed principal rows.

    THIS REPLACES A HELPER THAT DID NOT WORK AND COULD NOT FAIL. It was
    ``env.identity.model_copy(update={"principal_id": ...})`` — which changes the field the
    dispatcher never reads. ``_run_mcp_client`` pops ``identity`` and uses it ONLY to build
    credential headers (``_credential_headers`` reads ``auth_token`` and nothing else), then
    the production chain resolves header -> token -> DB -> ResolvedIdentity. Copying a
    different principal_id onto the OWNER's token meant every call authenticated as the
    owner: the "intruder" never existed, and the whole suite would have passed against a
    leaking build.
    """
    env.switch_principal(principal_id)
    token = env.credential().get("Authorization")
    assert token, (
        f"{principal_id} has no token in the database, so the dispatch would present no "
        "credential instead of running the real header -> token -> DB chain"
    )
    return token


class TestTaskIsScopedToItsPrincipal:
    """Read, list and complete, each graded on the envelope the buyer actually receives."""

    def test_the_dispatch_really_runs_as_the_intruder(self, tenant_id):
        """POSITIVE CONTROL: production must RESOLVE the caller as the intruder.

        Every other test here asserts a REFUSAL — and a refusal is also what you get when
        the intruder was never asked at all. That is not hypothetical: the first version of
        this file forged an identity by copying a different principal_id onto the owner's
        token, the dispatcher read only the token, and all five tests ran as the owner. Once
        the assertions were corrected they would have gone green against a build that leaks.

        So this one drives a SUCCESS and reads back who production thinks called.
        ``completed_by`` is written by complete_task from ``require_principal_id(identity)``
        — the identity production resolved for itself, not a claim the test made. If the
        credential does not survive the dispatch this fails loudly with the owner's id,
        instead of every refusal silently meaning nothing.
        """
        with _CompleteTaskEnv(tenant_id=tenant_id, principal_id=OWNER_ID) as env:
            intruders_own_task = _seed(env, tenant_id, INTRUDER_ID)
            _assert_two_distinct_principals(env)
            _become(env, INTRUDER_ID)
            result = env.call_via(Transport.MCP, task_id=intruders_own_task, status="completed")

        assert not result.is_error, (
            f"the intruder could not complete their OWN task, so this control cannot report who "
            f"production resolved and every refusal below is ungrounded: {result}"
        )
        assert result.payload["completed_by"] == INTRUDER_ID, (
            f"production resolved the caller as {result.payload['completed_by']!r}, not the intruder — "
            "the credential did not survive the dispatch, so every refusal in this file would "
            "be the owner being correctly allowed rather than the intruder being refused"
        )

    def test_reading_another_principals_task_is_refused(self, tenant_id):
        """REFERENCE_NOT_FOUND on the wire, which is the code the storyboard demands.

        The intruder reads their OWN task first. That success is the inline proof that this
        dispatch authenticates as the intruder, so the refusal below is a refusal and not an
        absence of the question.
        """
        with _GetTaskEnv(tenant_id=tenant_id, principal_id=OWNER_ID) as env:
            owners_task = _seed(env, tenant_id, OWNER_ID)
            intruders_task = _seed(env, tenant_id, INTRUDER_ID)
            _assert_two_distinct_principals(env)
            _become(env, INTRUDER_ID)
            own = env.call_via(Transport.MCP, task_id=intruders_task)
            result = env.call_via(Transport.MCP, task_id=owners_task)

        assert not own.is_error, (
            "the intruder could not read their OWN task, so this env is not authenticating "
            "as the intruder at all and the refusal below proves nothing"
        )
        assert own.payload["task_id"] == intruders_task

        assert result.is_error
        result.assert_wire_error("REFERENCE_NOT_FOUND")

    def test_the_two_refusals_are_indistinguishable(self, tenant_id):
        """ "Not yours" and "not there" must be the SAME envelope, field for field.

        This is the control, and the reason the test above means anything. The obligation
        is not merely that a cross-principal lookup fails — it is that the buyer cannot
        tell a task they may not see from one that does not exist. "Sellers MUST NOT reveal
        whether the task exists under another account or principal."

        Compared as WHOLE ENVELOPES, not as two error codes. A code-only assertion passes
        even when the refusals differ in message, suggestion, or a details dict that names
        the owning principal — and a leak in any of those is exactly the disclosure this
        forbids. The only field allowed to differ is the step_id the caller sent back to
        itself, which is blinded above with the reason stated.
        """
        with _GetTaskEnv(tenant_id=tenant_id, principal_id=OWNER_ID) as env:
            task_id = _seed(env, tenant_id, OWNER_ID)
            _become(env, INTRUDER_ID)
            not_yours = env.call_via(Transport.MCP, task_id=task_id)
            not_there = env.call_via(Transport.MCP, task_id="step_no_such_task")

        assert not_yours.is_error
        assert not_there.is_error
        not_there.assert_wire_error("REFERENCE_NOT_FOUND")

        assert _blind_echoed_ids(not_yours.wire_error_envelope) == _blind_echoed_ids(not_there.wire_error_envelope), (
            "the refusal for a task owned by another principal differs from the refusal for "
            "a task that does not exist — the difference is what tells a buyer the task is real"
        )

    def test_completing_another_principals_task_is_refused(self, tenant_id):
        """The WRITE half. The same unscoped lookup let A complete B's task."""
        with _CompleteTaskEnv(tenant_id=tenant_id, principal_id=OWNER_ID) as env:
            task_id = _seed(env, tenant_id, OWNER_ID)
            _become(env, INTRUDER_ID)
            result = env.call_via(Transport.MCP, task_id=task_id, status="completed")

        assert result.is_error
        result.assert_wire_error("REFERENCE_NOT_FOUND")

    def test_listing_does_not_show_another_principals_task(self, tenant_id):
        """list_tasks was the widest of the three: it listed the whole tenant.

        The owner lists FIRST, and their own id must be in their own body. Without that
        half, "the id is absent from the intruder's body" also passes when the body is
        empty for any reason at all — a broken list, a failed seed, a filter that matches
        nothing — and would report a leak as closed on a build where listing is simply
        dead. It is the same positive control as
        :meth:`test_the_dispatch_really_runs_as_the_intruder`, applied to the page.

        Volume-by-arithmetic — a caller-scoped page beside a tenant-wide total, which
        discloses how many tasks the others hold — is NOT graded here. It cannot be
        stated without naming count fields, and list_tasks' body does not conform to its
        pinned schema, so those names are unsettled (FIXME(#2201)). It is graded once the
        response conforms.
        """
        with TaskManagementEnv(tenant_id=tenant_id, principal_id=OWNER_ID) as env:
            owners_task = _seed(env, tenant_id, OWNER_ID)
            _assert_two_distinct_principals(env)
            _become(env, OWNER_ID)
            owners_view = env.call_via(Transport.MCP)
            _become(env, INTRUDER_ID)
            intruders_view = env.call_via(Transport.MCP)

        # Checked BEFORE reading the body: on an error the wire is None, and an absence
        # assertion against None passes for the wrong reason.
        assert not owners_view.is_error, f"the owner could not list their own tasks: {owners_view}"
        assert not intruders_view.is_error, f"list_tasks failed instead of returning a page: {intruders_view}"

        assert _body_mentions(owners_view.wire_response, owners_task), (
            "the owner's own task is missing from the owner's own listing, so listing returns "
            "nothing to anyone and the absence asserted below proves nothing"
        )
        assert not _body_mentions(intruders_view.wire_response, owners_task), (
            f"the owner's task id appears in the intruder's listing: {intruders_view.wire_response!r}"
        )

    def test_the_owner_still_reads_their_own(self, tenant_id):
        """The other half: scoping must not have locked the owner out.

        Asserted on the request payload, not merely on task_id, because a fix that
        returned the row stripped of its contents would satisfy an id check.

        Reading only. The listing half of this pair moved INTO
        :meth:`test_listing_does_not_show_another_principals_task`, where it does work as
        that test's positive control, rather than sitting here where a dead listing would
        fail one test and silently hollow out the other.
        """
        with _GetTaskEnv(tenant_id=tenant_id, principal_id=OWNER_ID) as env:
            task_id = _seed(env, tenant_id, OWNER_ID)
            read = env.call_via(Transport.MCP, task_id=task_id)

        assert not read.is_error, f"the owner could not read their own task: {read}"
        assert read.payload["task_id"] == task_id
        assert read.payload["request_data"] == {"secret_brief": "the other buyer's brief"}
