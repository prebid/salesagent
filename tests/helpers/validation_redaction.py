"""Helpers for grading ``extra_forbidden`` credential-redaction (#1329).

The redact-ALL policy in ``format_validation_error`` has two halves that every redaction
test must grade together: the offending value is WITHHELD (``Received value: [redacted]``,
and the secret string never appears) AND the actionable field PATH survives (the buyer's one
pointer, since the value is gone). ``extra_forbidden_error`` builds the single-error
``ValidationError`` uniformly; ``assert_redacted`` grades BOTH halves off one call, so the
per-case tests are a parametrized table rather than N hand-rolled scaffolds that each grade a
different subset (the round-13 finding: some graded both halves, some only ``[redacted]``, one
graded no field path at all).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from pydantic import ValidationError


def extra_forbidden_error(model: str, loc: tuple[str | int, ...], input: Any) -> ValidationError:  # noqa: A002
    """A single-error Pydantic ``ValidationError`` of type ``extra_forbidden`` at ``loc``.

    ``model`` names the offending schema (cosmetic — surfaces in the error title); ``loc`` is
    the Pydantic location tuple; ``input`` is the rejected value (the thing the redact-ALL
    policy must withhold). One builder so every redaction case constructs its error the same
    way (#1329 finding 6).
    """
    return ValidationError.from_exception_data(
        model,
        [{"type": "extra_forbidden", "loc": loc, "msg": "Extra inputs are not permitted", "input": input}],
    )


def assert_redacted(msg: str, *, field_path: str, secret: str | Iterable[str]) -> None:
    """Grade BOTH halves of the redact-ALL contract on a formatted validation ``msg``.

    * value WITHHELD — ``Received value: [redacted]`` present AND every ``secret`` string
      absent (accepts one string or an iterable of value fragments for a structured input);
    * field path SURVIVES — the actionable ``<field_path>: Extra field not allowed by AdCP
      spec`` pointer is present (``field_path`` may be the tail of the full bracket path — a
      substring match, so ``authentication.credential`` matches
      ``accounts[0]...authentication.credential``).
    """
    assert "Received value: [redacted]" in msg, f"value not withheld — missing '[redacted]' in: {msg!r}"
    assert f"{field_path}: Extra field not allowed by AdCP spec" in msg, (
        f"field path {field_path!r} did not survive redaction in: {msg!r}"
    )
    secrets = [secret] if isinstance(secret, str) else list(secret)
    for s in secrets:
        assert s not in msg, f"value fragment {s!r} leaked into the redacted message: {msg!r}"
