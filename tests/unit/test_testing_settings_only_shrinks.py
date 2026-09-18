"""``TestingSettings`` is a work list, and it may only get shorter.

Every field on that class says A SUITE IS RUNNING. A production path that consults one
serves a different seller under test than in deployment, so the suite's verdict becomes
conditional on the suite being what ran it — CLAUDE.md pattern 11. The class therefore has
no legitimate growth: a new field is a new instance of the defect.

This pins the exact field set. Removing a field passes once you also delete it here, in the
same change, which is the discipline the repo's other shrink-only pins carry. ADDING one
fails, and the message says what to do instead.

Not a duplicate of ``test_test_flag_properties_only_shrink.py``: that pins the seven
``Settings`` PROPERTIES that still fork on ``adcp_testing`` (GH #2255). This pins the
FIELDS that exist to be forked on. The properties are the readers; these are the switches.

``ProvisioningSettings`` is deliberately not covered. Its three fields — seed a demo
tenant, seed sample data, skip migrations — are facts an operator asks of a fresh
deployment, read once at init, and none of them asks whether a suite is running. They lived
on ``TestingSettings`` and that was the confusion worth removing: a class name implying
everything inside is a test artifact hides which fields are actually defects.
"""

from __future__ import annotations

from src.core.config import ProvisioningSettings, TestingSettings

#: The field set as it stands, each with what has to land before it can go.
EXPECTED_TESTING_FIELDS: frozenset[str] = frozenset(
    {
        # One decision: webhook_validator.py:196 loosens EgressPolicy.check_registration's
        # loopback check while a suite runs. Goes when the test environments' loopback
        # origins are reachable without it — the same work ADCP_OUTBOUND_ALLOW_PRIVATE needs.
        "adcp_testing",
        # SEVEN FIELDS WERE REMOVED HERE, which is the direction this pin exists to allow.
        # adcp_auth_test_mode decided whether create_app COMPOSED a test-credential login
        # blueprint, and its six test_* credentials were that blueprint's password table.
        # The blueprint is deleted: a test that needs an admin session signs one
        # (tests/helpers/admin_session), and a deployment reaches its first admin through
        # its identity provider, with per-tenant Setup Mode covering the interval before SSO
        # is enabled.
    }
)


def test_no_field_is_added() -> None:
    """A new test-mode switch is a new defect, so there is no way to add one here."""
    added = sorted(set(TestingSettings.model_fields) - EXPECTED_TESTING_FIELDS)
    assert not added, (
        "these fields were added to TestingSettings:\n  "
        + "\n  ".join(added)
        + "\n\nEvery field on this class makes production ask whether a suite is running, "
        "which CLAUDE.md pattern 11 forbids. Give the behaviour a real input a deployment "
        "can set — a tenant column, an AdapterConfig row, an ordinary settings field — and "
        "let the test seed it like any other state. If the fact is genuinely a provisioning "
        "decision an operator makes, it belongs on ProvisioningSettings."
    )


def test_a_removed_field_is_removed_here_too() -> None:
    """The pin cannot fall behind the class: a deletion updates both or fails."""
    stale = sorted(EXPECTED_TESTING_FIELDS - set(TestingSettings.model_fields))
    assert not stale, (
        "these fields are pinned here but gone from TestingSettings:\n  "
        + "\n  ".join(stale)
        + "\n\nGood — that is the direction. Delete them from EXPECTED_TESTING_FIELDS in the "
        "same change, and say in the commit what replaced them."
    )


def test_provisioning_is_not_a_test_flag() -> None:
    """The split holds: nothing that merely provisions a deployment sits on the test class."""
    assert set(ProvisioningSettings.model_fields) == {
        "create_demo_tenant",
        "create_sample_data",
        "skip_migrations",
    }
    assert not set(ProvisioningSettings.model_fields) & set(TestingSettings.model_fields), (
        "a field is on both classes; one of them is wrong about what it is"
    )


# There is no test asserting that each field carries a reason. The one written here checked
# that every field NAME appeared twice in this module, which grades text repetition rather
# than substance -- padding the file would satisfy it, and grouping six credentials under one
# honest comment failed it. Whether an entry's reason is any good is a review property, not a
# machine-checkable one, and a guard that grades the spelling of a comment is the shape this
# repo already has notes about avoiding.
