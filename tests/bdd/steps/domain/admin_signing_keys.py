"""BDD step definitions for BR-ADMIN-SIGNING-KEYS (#2288).

The env (``AdminSigningKeysEnv``) seeds the key and fetches the two pages; these steps
read what the pages rendered. Expected texts come from the feature file, never from the
production mapping, so a change to the advice has to change the scenario too.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pytest_bdd import given, parsers, then, when

from tests.bdd.steps._outcome_helpers import _require
from tests.harness.admin_signing_keys import (
    KeyState,
    checklist_task_details,
    status_cell,
    warning_runbook_link,
    warning_text,
)

if TYPE_CHECKING:
    from tests.harness.admin_signing_keys import AdminSigningKeysEnv

_RUNBOOK = "https://github.com/prebid/salesagent/blob/main/docs/operations/signing-key-runbook.md"


def _env(ctx: dict) -> AdminSigningKeysEnv:
    return ctx["env"]


def _expected(ctx: dict, text: str) -> str:
    """*text* with ``{kid}`` and ``{revoked_at}`` replaced by the seeded key's values."""
    env = _env(ctx)
    assert env.kid is not None, "no signing key was seeded"
    return text.replace("{kid}", env.kid).replace("{revoked_at}", str(env.revoked_at))


@given(parsers.parse("the tenant's only signing key {state}"))
def given_signing_key_in_state(ctx: dict, state: str) -> None:
    _env(ctx).seed_key(KeyState(state))


@when("the operator opens the signing-keys page and the setup checklist")
def when_operator_opens_pages(ctx: dict) -> None:
    env = _env(ctx)
    ctx["signing_keys_page"] = env.signing_keys_page()
    ctx["setup_checklist_page"] = env.setup_checklist_page()


@then(parsers.parse('the key\'s Status cell reads "{status}"'))
def then_status_cell_reads(ctx: dict, status: str) -> None:
    page = _require(ctx, "signing_keys_page")
    assert status_cell(page, _env(ctx).kid or "") == _expected(ctx, status)


@then(parsers.parse('the signing-keys page warns "{warning}"'))
def then_signing_keys_page_warns(ctx: dict, warning: str) -> None:
    actual = warning_text(_require(ctx, "signing_keys_page"))
    expected = None if warning == "nothing" else _expected(ctx, warning)
    assert actual == expected


@then(parsers.parse('the Message Signing Key task reads "{checklist}"'))
def then_signing_key_task_reads(ctx: dict, checklist: str) -> None:
    page = _require(ctx, "setup_checklist_page")
    assert checklist_task_details(page, "Message Signing Key") == _expected(ctx, checklist)


@then(parsers.parse('the signing-keys warning links the runbook section "{section}"'))
def then_warning_links_runbook_section(ctx: dict, section: str) -> None:
    assert warning_runbook_link(_require(ctx, "signing_keys_page")) == f"{_RUNBOOK}#{section}"
