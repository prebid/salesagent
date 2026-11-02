"""``_is_valid_email`` accepts an address shape and refuses a malformed one.

A pure function, so it belongs in this directory: input in, bool out, no database,
no transport, no identity.

WHAT THIS IS NOT A TEST FOR, because the first version of this file got it wrong.
The helper replaced ``re.match(r"[^@]+@[^@]+\\.[^@]+", email)``, which CodeQL
reports as ``py/polynomial-redos`` (alert 194). That report is a FALSE POSITIVE and
it was measured, not argued: every adversarial shape -- ``"a"*n + "@"``,
``"a@" + "b"*n``, ``"a@"*n``, ``"a"*n + "@" + "b"*n`` -- runs in under 0.0001s at
n=80,000. ``[^@]`` excludes the delimiter the pattern splits on, so each quantifier
is bounded to a single segment and there is no overlapping alternation to backtrack
across. The pattern is linear.

So this file carries NO timing assertion. One was written, and it would have passed
against the old regex just as it passes against the new code -- an instrument whose
silence is not evidence, because it could not have spoken. A source-inspection
assertion was written too, and ``ruff-boundary``'s TID251 ban on
``inspect.getsource`` in tests refused it for the right reason: it grades a spelling
rather than a behaviour.

What survives is the contract the helper actually owes its one caller, including the
one place it is deliberately stricter than the regex it replaced.
"""

from __future__ import annotations

import pytest

from src.admin.blueprints.users import _is_valid_email


@pytest.mark.parametrize(
    "address",
    [
        "a@b.c",
        "user@example.com",
        "first.last@sub.example.co.uk",
        "user+tag@example.org",
        "user@example.com trailing",  # re.match anchored only at the start; so does this
    ],
)
def test_accepts_an_ordinary_address(address: str) -> None:
    assert _is_valid_email(address) is True


@pytest.mark.parametrize(
    ("address", "why"),
    [
        ("", "empty"),
        ("plainstring", "no @"),
        ("@example.com", "no local part"),
        ("user@", "no domain"),
        ("user@example", "domain carries no dot"),
        ("user@.com", "the domain label before the dot is empty"),
        ("user@example.", "nothing after the dot"),
        ("user@@example.com", "two @ signs"),
    ],
)
def test_rejects_a_malformed_address(address: str, why: str) -> None:
    assert _is_valid_email(address) is False, why


def test_refuses_a_second_at_sign_where_the_regex_accepted_one() -> None:
    """The one deliberate behaviour change, pinned so it reads as a choice.

    ``re.match`` anchors only at the start, so ``"a@b.c@d"`` matched: the pattern
    was satisfied by the ``"a@b.c"`` prefix and the trailing ``"@d"`` was never
    examined. An unquoted local part may not carry a second ``@``, so refusing it
    is a correction -- but it is still a change, and an address a tenant admin
    could previously submit is now rejected.
    """
    assert _is_valid_email("a@b.c@d") is False
