"""Harness for what the operator is told about the tenant's active signing key (#2288).

Two admin pages say whether the seller signs and what to do when it cannot: the
signing-keys page (the Status cell and the warning above the table) and the setup
checklist (the Message Signing Key task). Both read ``signing_key_backed``, and this env
puts a key in each state they distinguish and reads both pages back as rendered.

Every state is a SEEDING fact, so a scenario runs unchanged against a live server. The
deployment KEK is never changed: this process holds the KEK docker-compose.e2e.yml gives
the live server (:func:`tests.helpers.signing.hold_stack_kek`), and a key is encrypted
under it, under a different passphrase, or published beside a JWK its private half does
not derive (``SigningKeyFactory``'s ``passphrase=`` and ``material_changed``).

The admin transport follows the env's own, through the shared
:class:`tests.harness.admin_client.AdminClient`: a Flask test client in process, a
``requests`` session against ``e2e_config.base_url`` over e2e. The env adds no
``__enter__`` (``tests/harness/test_harness_base.py`` keeps that list closed).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import uuid4

from bs4 import BeautifulSoup, Tag

from tests.harness._base import BareIntegrationEnv
from tests.harness.admin_client import AdminOperatorMixin

#: A KEK the live server and this process never hold.
_FOREIGN_KEK = b"a-kek-this-deployment-has-never-held"


class KeyState(StrEnum):
    """The states of the tenant's one signing key that the two pages tell apart."""

    HEALTHY = "signs under the deployment KEK"
    KEK_WRONG = "was provisioned under a KEK this deployment does not hold"
    MATERIAL_CHANGED = "publishes a public key its private half does not match"
    REVOKED_IN_GRACE = "was revoked moments ago"
    NOT_YET_ACTIVE = "becomes active tomorrow"


class AdminSigningKeysEnv(AdminOperatorMixin, BareIntegrationEnv):
    """A tenant with one signing key in a chosen state, and the operator's two pages."""

    #: The kid of the key :meth:`seed_key` created, read back by the Then steps.
    kid: str | None = None
    #: When the seeded key was revoked, as the database returns it; ``None`` unless revoked.
    revoked_at: datetime | None = None

    def seed_key(self, state: KeyState) -> str:
        """Give the tenant one signing key in *state*; return its kid.

        The kid is random: the signing-material cache is per ``(tenant, kid)`` and
        process-wide (the live server's too), so a fixed kid would let one example
        read another's cached resolution.
        """
        from tests.factories import SigningKeyFactory
        from tests.helpers.signing import hold_stack_kek

        deployment_kek = hold_stack_kek(self._guard)
        tenant = self.ensure_tenant()
        now = datetime.now(UTC).replace(microsecond=0)
        overrides: dict[str, Any] = {"passphrase": deployment_kek}
        if state is KeyState.KEK_WRONG:
            overrides["passphrase"] = _FOREIGN_KEK
        elif state is KeyState.MATERIAL_CHANGED:
            overrides["material_changed"] = True
        elif state is KeyState.REVOKED_IN_GRACE:
            overrides["revoked_at"] = now - timedelta(seconds=5)
        elif state is KeyState.NOT_YET_ACTIVE:
            overrides["not_before"] = now + timedelta(days=1)
        row = SigningKeyFactory(tenant=tenant, kid=f"adcp-{uuid4().hex[:12]}", **overrides)
        self._commit_factory_data()
        self.kid = row.kid
        self.revoked_at = row.revoked_at
        return row.kid

    # ── pages ──────────────────────────────────────────────────────────────

    def signing_keys_page(self) -> BeautifulSoup:
        """The signing-keys page as rendered for the operator."""
        return self._page("signing-keys/")

    def setup_checklist_page(self) -> BeautifulSoup:
        """The setup checklist page as rendered for the operator."""
        return self._page("setup-checklist")

    def _page(self, path: str) -> BeautifulSoup:
        response = self.admin_request(self._tenant_id, "get", path)
        assert response.status_code == 200, f"/tenant/{self._tenant_id}/{path} answered {response.status_code}"
        return BeautifulSoup(response.data, "html.parser")


def status_cell(page: BeautifulSoup, kid: str) -> str:
    """The text of the Status cell on *kid*'s row of the signing-keys table."""
    for row in page.select("table.signing-table tbody tr"):
        cells = row.find_all("td")
        if cells and _rendered_text(cells[0]) == kid:
            return _rendered_text(cells[3])
    raise AssertionError(f"the signing-keys page lists no row for {kid!r}")


def _rendered_text(tag: Tag) -> str:
    """*tag*'s text as the operator reads it: every run of whitespace collapsed to one space.

    The one normalisation every reader here uses, so the signing-keys page and the
    checklist are compared under the same rule and the feature can show they agree.
    """
    return " ".join(tag.get_text().split())


def _warning(page: BeautifulSoup) -> Tag | None:
    """The warning above the signing-keys table, or ``None`` when there is none."""
    return page.select_one(".kek-warning")


def warning_text(page: BeautifulSoup) -> str | None:
    """The text of the warning above the signing-keys table, or ``None`` when there is none."""
    banner = _warning(page)
    return None if banner is None else _rendered_text(banner)


def warning_runbook_link(page: BeautifulSoup) -> str | None:
    """The runbook link in the warning, or ``None`` when there is no warning."""
    banner = _warning(page)
    link = None if banner is None else banner.find("a")
    return None if link is None else str(link.get("href"))


def checklist_task_details(page: BeautifulSoup, task_name: str) -> str:
    """The details line of the checklist task named *task_name*."""
    for item in page.select(".task-item"):
        name = item.select_one(".task-name")
        if name is not None and _rendered_text(name) == task_name:
            details = item.select_one(".task-details")
            assert details is not None, f"the {task_name!r} task renders no details"
            return _rendered_text(details)
    raise AssertionError(f"the setup checklist has no {task_name!r} task")
