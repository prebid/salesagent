"""Guard: the billing-party value-set has exactly ONE source of truth.

Regression guard for the ck_accounts_billing drift (#1521 / #1592): the DB CHECK
constraint on ``accounts.billing`` was hand-written as ``('operator', 'agent')``
while the AdCP 3.1.1 spec (and the SDK ``BillingParty`` enum derived from it)
defines three values — ``operator``, ``agent``, ``advertiser``. Two literal
copies of one value-set drift independently: a spec-valid ``billing="advertiser"``
passed request validation and then IntegrityError'd on INSERT (an opaque 500
instead of honest acceptance).

Single source of truth: ``src.core.billing_policy.BILLING_PARTY_VALUES`` derives
from the SDK ``BillingParty`` enum and is pinned to the spec here (guard a). The
ORM CHECK constraint must derive from that constant (guard b). The migration's
frozen 3-value literal and this file's spec-pin tuple are the only deliberate
literals — they exist precisely so everything else can derive.
"""

from src.core.database.models import Account
from tests.helpers.orm_constraints import check_constraint_values, quoted_values

# The authoritative spec value-set for billing-party, pinned by tag.
# @source repo=adcp ref=v3.1.1 path=dist/schemas/3.1.1/enums/billing-party.json pointer=/enum
_SPEC_BILLING_PARTIES = ("operator", "agent", "advertiser")


def test_billing_party_values_match_spec_pin():
    """(a) BILLING_PARTY_VALUES (SDK-derived) equals the v3.1.1 spec enum.

    The installed SDK is a cross-check, not the authority — this pin is what
    makes deriving from the SDK enum safe against SDK bumps. If the SDK gains
    or loses a value relative to the pinned spec, this fails and forces a
    deliberate spec-version decision instead of silent drift.
    """
    from src.core.billing_policy import BILLING_PARTY_VALUES

    assert BILLING_PARTY_VALUES == _SPEC_BILLING_PARTIES, (
        "src.core.billing_policy.BILLING_PARTY_VALUES has drifted from the pinned "
        "AdCP 3.1.1 billing-party enum. Reconcile against the @source citation above "
        "before changing either side."
    )


def test_orm_billing_check_derives_from_billing_party_values():
    """(b) The ck_accounts_billing ORM CHECK contains exactly BILLING_PARTY_VALUES.

    Core invariant: the DB CHECK is a DERIVED copy of the single source of
    truth, never an independent hand-rolled literal (the #1521 bug class —
    the CHECK froze at 2 values while the spec defines 3).
    """
    from src.core.billing_policy import BILLING_PARTY_VALUES

    check_values = check_constraint_values(Account, "ck_accounts_billing")
    assert check_values == BILLING_PARTY_VALUES, (
        f"ck_accounts_billing CHECK values {check_values!r} != BILLING_PARTY_VALUES "
        f"{BILLING_PARTY_VALUES!r}. The ORM constraint must derive from "
        "src.core.billing_policy.BILLING_PARTY_VALUES, not carry its own literal copy."
    )


class TestParserModelsTheForm:
    """Self-tests: the SQL-literal extractor reads a CHECK clause correctly."""

    def test_extracts_in_clause_values_in_order(self):
        sql = "billing IS NULL OR billing IN ('operator', 'agent', 'advertiser')"
        assert quoted_values(sql) == ("operator", "agent", "advertiser")

    def test_no_quoted_literals_yields_empty(self):
        assert quoted_values("billing IS NULL") == ()
