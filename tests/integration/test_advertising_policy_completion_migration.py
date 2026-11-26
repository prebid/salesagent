"""Revision c3ebb9a41337 completes every stored advertising policy, and refuses to guess.

``tenants.advertising_policy`` is ``JSONType(model=AdvertisingPolicy)`` from this revision on
(GH #2696), and the model has no defaults, so a row loads only when it holds every key. The
rows stored before it are partial: the Settings form never writes ``require_manual_review``
or ``description``. The revision adds each missing key with the value the old readers gave
its absence. This module is the revision's only grading: the integration suite builds its
schema with ``Base.metadata.create_all``, so nothing else executes a migration's SQL.

The assertion that matters is that a completed row loads through the ORM: ``JSONType``
validates on read, so a row the revision left partial raises there.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.core.database.models import Tenant
from src.core.schemas.advertising_policy import AdvertisingPolicy
from tests.integration.migration_helpers import (
    reset_to_revision,
    run_alembic_downgrade,
    run_alembic_upgrade,
    stored_column,
)

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

_REVISION = "c3ebb9a41337"
_PREVIOUS = "b9b8e637b56c"
_TENANT = "mig_adpol_tenant"

#: The value each key takes when a stored row lacks it.
_BLANK: dict[str, Any] = {
    "enabled": False,
    "require_manual_review": False,
    "prohibited_advertisers": [],
    "prohibited_categories": [],
    "prohibited_tactics": [],
    "default_prohibited_categories": [],
    "default_prohibited_tactics": [],
    "description": None,
}


@pytest.fixture
def at_previous(migration_db):
    """The module-scoped database at the revision before this one, with one tenant and no policy."""
    return reset_to_revision(
        migration_db,
        revision=_PREVIOUS,
        tenant_id=_TENANT,
        tenant_name="Migration advertising policy",
        subdomain="mig-adpol",
    )


def _store(engine, policy: Any) -> None:
    """Write the tenant's policy as raw JSONB, the way the schema before this revision held it."""
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE tenants SET advertising_policy = CAST(:policy AS jsonb) WHERE tenant_id = :tid"),
            {"policy": None if policy is None else json.dumps(policy), "tid": _TENANT},
        )


def _stored(engine) -> Any:
    return stored_column(engine, "tenants", "advertising_policy", tenant_id=_TENANT)


def _load_through_orm(engine) -> AdvertisingPolicy | None:
    with Session(engine) as session:
        tenant = session.get(Tenant, _TENANT)
        assert tenant is not None
        return tenant.advertising_policy


#: (stored before the revision, stored after it).
_COMPLETIONS = [
    pytest.param({"enabled": True}, {**_BLANK, "enabled": True}, id="the-test-seed"),
    pytest.param({}, _BLANK, id="empty-object"),
    pytest.param(
        {"description": "No adult content"}, {**_BLANK, "description": "No adult content"}, id="description-only"
    ),
    pytest.param(
        {
            "enabled": True,
            "prohibited_advertisers": ["rival.example"],
            "prohibited_categories": [],
            "prohibited_tactics": ["fake urgency"],
            "default_prohibited_categories": ["hate"],
            "default_prohibited_tactics": [],
        },
        {
            **_BLANK,
            "enabled": True,
            "prohibited_advertisers": ["rival.example"],
            "prohibited_tactics": ["fake urgency"],
            "default_prohibited_categories": ["hate"],
        },
        id="the-settings-form",
    ),
    pytest.param(
        {"enabled": True, "prohibited_tactics": None, "require_manual_review": True},
        {**_BLANK, "enabled": True, "require_manual_review": True},
        id="a-json-null-list-is-missing",
    ),
]


class TestTheUpgradeCompletesEveryRow:
    @pytest.mark.parametrize(("before", "after"), _COMPLETIONS)
    def test_a_partial_row_is_completed_and_loads(self, at_previous, before, after):
        engine, db_url = at_previous
        _store(engine, before)

        run_alembic_upgrade(db_url, _REVISION)

        assert _stored(engine) == after
        assert _load_through_orm(engine) == AdvertisingPolicy.model_validate(after)

    def test_a_tenant_with_no_policy_keeps_none(self, at_previous):
        engine, db_url = at_previous
        _store(engine, None)

        run_alembic_upgrade(db_url, _REVISION)

        assert _stored(engine) is None
        assert _load_through_orm(engine) is None


class TestTheUpgradeRefusesToGuess:
    @pytest.mark.parametrize(
        "policy",
        [
            pytest.param({"enabled": True, "custom_rules": {}}, id="undeclared-key"),
            pytest.param({"enabled": "yes"}, id="flag-not-boolean"),
            pytest.param({"prohibited_tactics": "spam"}, id="list-not-array"),
            pytest.param({"prohibited_tactics": [1]}, id="list-entry-not-string"),
            pytest.param(["enabled"], id="not-an-object"),
        ],
    )
    def test_a_row_that_cannot_be_completed_aborts_and_is_left_alone(self, at_previous, policy):
        engine, db_url = at_previous
        _store(engine, policy)

        with pytest.raises(RuntimeError) as excinfo:
            run_alembic_upgrade(db_url, _REVISION)

        message = str(excinfo.value)
        assert f"tenant={_TENANT}" in message, f"the abort must name the row: {message}"
        assert "1 stored advertising policy row(s)" in message, f"the headline must count the rows: {message}"
        assert _stored(engine) == policy, "an aborted upgrade must rewrite nothing"


class TestTheDowngradeRestoresThePartialShape:
    @pytest.mark.parametrize(("before", "after"), _COMPLETIONS[:4])
    def test_a_completed_row_returns_to_what_was_stored(self, at_previous, before, after):
        """Each key the upgrade added holds its completion value, so dropping those keys restores the row.

        The JSON-null case is left out on purpose: the upgrade drops a null list, which the
        readers before this revision could not use, and the downgrade does not put it back.
        """
        engine, db_url = at_previous
        _store(engine, before)
        run_alembic_upgrade(db_url, _REVISION)

        run_alembic_downgrade(db_url, _PREVIOUS)

        expected = {key: value for key, value in before.items() if _BLANK.get(key, object()) != value}
        assert _stored(engine) == expected
