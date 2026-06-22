"""Unit tests for the idempotency-race IntegrityError detection seam.

``_is_idempotency_backstop_violation`` is the single home for the
"is this the idempotency race?" decision that used to be a substring match
duplicated across both booking paths. It prefers the driver's STRUCTURED
constraint name and only falls back to scanning the message text — so an
unrelated IntegrityError (a different unique/foreign-key violation) is NOT
mistaken for the idempotency backstop, and the detection no longer hinges on the
wording of a Postgres message in two places.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from src.core.tools.media_buy_create import (
    _is_idempotency_backstop_violation,
    _resolve_idempotency_race_or_raise,
)


class _FakeDiag:
    def __init__(self, constraint_name: str | None) -> None:
        self.constraint_name = constraint_name


class _FakeOrig(Exception):
    """Stand-in for the DBAPI error (a real BaseException, as ``orig`` must be):
    optional ``.diag.constraint_name`` + a message."""

    def __init__(self, message: str, *, constraint_name: str | None = None, with_diag: bool = True) -> None:
        super().__init__(message)
        self._message = message
        if with_diag:
            self.diag = _FakeDiag(constraint_name)

    def __str__(self) -> str:
        return self._message


def _integrity_error(message: str, *, constraint_name: str | None = None, with_diag: bool = True) -> IntegrityError:
    return IntegrityError(
        "INSERT INTO media_buys ...", {}, _FakeOrig(message, constraint_name=constraint_name, with_diag=with_diag)
    )


class TestIsIdempotencyBackstopViolation:
    """Structured-name primary, message-substring fallback, unrelated -> False."""

    def test_exact_backstop_index_name_via_structured_diag(self):
        exc = _integrity_error("duplicate key", constraint_name="idx_media_buys_idempotency_key")
        assert _is_idempotency_backstop_violation(exc) is True

    def test_transient_build_index_name_matches_by_prefix(self):
        # The CONCURRENTLY build-swap variants (…_acct / …_noacct) share the
        # backstop index PREFIX — detection must survive them.
        exc = _integrity_error("duplicate key", constraint_name="idx_media_buys_idempotency_key_acct")
        assert _is_idempotency_backstop_violation(exc) is True

    def test_unrelated_constraint_is_not_the_backstop(self):
        # A different unique/FK violation must NOT be swallowed as an idempotency race.
        exc = _integrity_error("duplicate key", constraint_name="media_buys_pkey")
        assert _is_idempotency_backstop_violation(exc) is False

    def test_constraint_merely_containing_the_token_is_not_the_backstop(self):
        # A DIFFERENT constraint that merely CONTAINS the column token (e.g. a
        # unique index on another table) must NOT be misread as the media_buys
        # backstop. The prefix match — not a bare substring — is what excludes it;
        # the old `"idempotency_key" in constraint` disjunct would have false-matched.
        exc = _integrity_error("duplicate key", constraint_name="orders_idempotency_key_uniq")
        assert _is_idempotency_backstop_violation(exc) is False

    def test_message_fallback_when_no_structured_diag(self):
        exc = _integrity_error(
            'duplicate key value violates unique constraint "idx_media_buys_idempotency_key"',
            with_diag=False,
        )
        assert _is_idempotency_backstop_violation(exc) is True

    def test_unrelated_message_without_diag_is_not_the_backstop(self):
        exc = _integrity_error("null value in column violates not-null constraint", with_diag=False)
        assert _is_idempotency_backstop_violation(exc) is False


class TestResolveIdempotencyRaceOrRaise:
    """The shared handler re-raises anything that is not the idempotency backstop."""

    def test_unrelated_integrity_error_re_raises_unchanged(self):
        exc = _integrity_error("foreign key violation", constraint_name="media_buys_principal_id_fkey")
        with pytest.raises(IntegrityError) as caught:
            _resolve_idempotency_race_or_raise(
                exc,
                "tenant_x",
                idempotency_key="key-1234567890123456",
                principal_id="prin_x",
                account_id=None,
                request_hash="hash",
            )
        # Re-raised verbatim — not translated, not swallowed into a replay.
        assert caught.value is exc


class TestBackstopIndexConstantPinnedToModel:
    """The detection constant must name a real index on the MediaBuy table.

    Semantic-SSOT guard: ``_IDEMPOTENCY_BACKSTOP_INDEX`` is a literal independent
    of the model's ``Index(...)`` declaration and the migration. A rename in one
    place would silently desync detection — the prefix match would stop matching
    and the message fallback also keys on the column token. This is the failing
    oracle for that drift.
    """

    def test_constant_names_a_real_media_buys_index(self):
        from src.core.database.models import MediaBuy
        from src.core.tools.media_buy_create import _IDEMPOTENCY_BACKSTOP_INDEX

        index_names = {ix.name for ix in MediaBuy.__table__.indexes}
        assert _IDEMPOTENCY_BACKSTOP_INDEX in index_names, (
            f"{_IDEMPOTENCY_BACKSTOP_INDEX!r} is not a declared index on MediaBuy "
            f"(have {sorted(index_names)}) — the detection constant has drifted from the model."
        )
