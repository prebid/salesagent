"""Guard: every column the Account natural-key lookups filter on is immutable.

salesagent-8sfr: ``AccountRepository.get_by_natural_key`` resolves a buyer's
``sync_accounts`` entry on (tenant_id, operator, brand.domain[, brand_id],
sandbox), but ``_IMMUTABLE_FIELDS`` listed only {tenant_id, account_id,
created_at}. ``operator`` and ``sandbox`` were therefore load-bearing identity
that any caller could overwrite through the generic ``update_fields(**kwargs)``
-- and the admin edit form did. The account got RE-KEYED, the buyer's next sync
carrying the original key stopped matching, and a DUPLICATE account was
provisioned for the same brand+operator.

The behavioral consequence is graded by
tests/integration/test_account_natural_key_immutability.py. This guard exists so
the CLASS cannot come back: it derives the key components from the lookup
methods' own signatures, so adding a component to a lookup without protecting it
fails here at the moment the coupling is introduced, rather than the next time
someone edits a form.

Signature-derived on purpose. A hardcoded {"operator", "sandbox"} would restate
today's answer and go stale exactly when the key changes -- which is the failure
mode being guarded against.

Scope: this guards the Account natural key specifically, not every repository.
``MediaBuyRepository.find_by_idempotency_key`` has the same shape (its
(principal_id, account_id, idempotency_key) tuple sits outside
``_MEDIA_BUY_IMMUTABLE_FIELDS``) but no caller writes those names today; that is
tracked separately rather than generalised here on one example.
"""

from __future__ import annotations

import inspect

from src.core.database.repositories.account import AccountRepository

#: Lookup parameters that are not themselves mutable Account columns.
#: ``tenant_id`` is constructor-scoped and never a kwarg; ``limit`` /
#: ``principal_id`` are query controls on the list/count variants.
_QUERY_PARAMS = {"self", "tenant_id", "limit", "principal_id"}

#: Lookup parameters that are read OUT OF a column rather than being one.
#: Both are extracted from the ``brand`` JSON column, so protecting ``brand``
#: is what protects them.
_COLUMN_FOR_PARAM = {"brand_domain": "brand", "brand_id": "brand"}


def _natural_key_lookups() -> dict[str, object]:
    """Every ``*_by_natural_key`` method on the repository.

    All of them are inspected, not just ``get_``: the sync ambiguity path uses
    ``list_by_natural_key`` / ``count_by_natural_key``, so a component added to
    those alone would otherwise slip past the guard that exists to catch it.
    """
    return {
        name: member
        for name, member in inspect.getmembers(AccountRepository, inspect.isfunction)
        if name.endswith("_by_natural_key")
    }


def _natural_key_columns() -> set[str]:
    """Account columns the natural-key lookups filter on, from their signatures."""
    columns: set[str] = set()
    for member in _natural_key_lookups().values():
        for param in inspect.signature(member).parameters:
            if param in _QUERY_PARAMS:
                continue
            columns.add(_COLUMN_FOR_PARAM.get(param, param))
    return columns


def test_natural_key_components_are_immutable():
    """Every natural-key column must be refused by ``update_fields``."""
    unprotected = _natural_key_columns() - AccountRepository._IMMUTABLE_FIELDS
    assert not unprotected, (
        f"natural-key columns are mutable through update_fields: {sorted(unprotected)}.\n"
        "The natural-key lookups filter on these, so writing one RE-KEYS the account: the buyer's "
        "next sync_accounts call carrying the original key stops matching and provisions a "
        "DUPLICATE, stranding the account_id they already hold (salesagent-8sfr).\n"
        "Add them to AccountRepository._IMMUTABLE_FIELDS. If a caller legitimately needs to move an "
        "account, that is an explicit, audited re-key operation — not a field write."
    )


def test_every_natural_key_lookup_is_inspected():
    """All three lookup variants are covered, not just ``get_``.

    Pins the aperture itself: if the guard silently narrowed to one method, a
    component added to the list/count variants would stop being checked.
    """
    assert set(_natural_key_lookups()) == {
        "get_by_natural_key",
        "list_by_natural_key",
        "count_by_natural_key",
    }


def test_brand_params_map_onto_the_protected_column():
    """``brand_domain``/``brand_id`` are read out of the ``brand`` column.

    They are not columns themselves, so the guard maps them onto ``brand`` and
    requires THAT to be protected. Stated as a mapping assertion rather than as
    "brand must not be immutable" — a guard that goes red when protection
    increases would be inverted.
    """
    params = set(inspect.signature(AccountRepository.get_by_natural_key).parameters)
    assert {"brand_domain", "brand_id"} <= params, (
        "get_by_natural_key no longer takes brand_domain/brand_id — the mapping in this guard "
        "encodes how brand participates in the key and must be re-derived."
    )
    assert "brand" in _natural_key_columns()
    assert "brand" in AccountRepository._IMMUTABLE_FIELDS


class TestMatcherModelsTheForm:
    """Self-tests: the reader finds real names and the guard can actually fail."""

    def test_reader_finds_every_key_component(self):
        """Not vacuously empty — an empty set would make the guard always pass."""
        assert _natural_key_columns() == {"operator", "sandbox", "brand"}

    def test_guard_would_fail_if_a_component_were_unprotected(self):
        """The assertion has teeth: removing protection produces a violation.

        Computed against a stand-in rather than by mutating the real frozenset,
        so the self-test cannot leave global state altered for other tests.
        """
        pretend_protected = frozenset({"tenant_id", "account_id", "created_at"})
        assert _natural_key_columns() - pretend_protected == {"operator", "sandbox", "brand"}
