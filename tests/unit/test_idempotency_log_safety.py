"""CWE-117: the shared replay engine scrubs buyer-supplied values before logging.

``cache_and_return`` logs the idempotency_key/tenant/principal on the cache-write race path
(``on_race=None``) via ``log_safety.loggable()``; without it a buyer could embed a newline
in the key and forge operator log lines. CodeQL flags these sinks (it does not recognize
``loggable`` as a sanitizer) — this is the behavioral oracle that proves the sanitization
actually happens, and that a revert of the sink to a raw ``%``-arg would redden.

It drives the REAL ``cache_and_return`` log statement with a stub policy that forces the
race (``record_success`` raises ``IntegrityError`` — no DB) and a mocked module logger
(captures the emitted args regardless of the logging configuration, which the in-network
container reconfigures). Mutation-verified: dropping ``loggable()`` reddens it.
"""

from unittest.mock import patch

from sqlalchemy.exc import IntegrityError

from src.services import idempotency_replay


class _RaisingAttempts:
    """A cache write that always collides — forces cache_and_return's race branch."""

    def record_success(self, **kwargs: object) -> None:
        raise IntegrityError("INSERT INTO idempotency_attempts ...", {}, Exception("duplicate key"))


class _RaisingUoW:
    """Minimal UoW context whose cache write raises, standing in for the real AccountUoW."""

    idempotency_attempts = _RaisingAttempts()

    def __enter__(self) -> "_RaisingUoW":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False  # never suppress the IntegrityError


# eviction_probability=0.0 makes _maybe_evict_expired return before opening a second UoW.
_RACE_POLICY: idempotency_replay.IdempotencyReplayPolicy = idempotency_replay.IdempotencyReplayPolicy(
    tool_name="sync_accounts",
    make_uow=lambda _tenant_id: _RaisingUoW(),
    replay_from_envelope=lambda _envelope: None,
    to_cacheable=lambda result: (result, "completed"),
    eviction_probability=lambda: 0.0,
    find_backstop_anchor=None,
)


def _rendered_messages(mock_logger) -> list[str]:
    """Render every message the mocked logger was asked to emit (fmt % args)."""
    out: list[str] = []
    for level in ("info", "warning", "error"):
        for call in getattr(mock_logger, level).call_args_list:
            args = call.args
            if args:
                out.append(args[0] % args[1:] if len(args) > 1 else args[0])
    return out


def test_cache_write_race_log_scrubs_injected_idempotency_key():
    injected = "k\r\nINJECTED-FORGED-LOG-LINE"

    with patch.object(idempotency_replay, "logger") as mock_logger:
        idempotency_replay.cache_and_return(
            _RACE_POLICY,
            object(),  # any result; to_cacheable wraps it, record_success raises before use
            tenant_id="tenant_x",
            principal_id="agent_x",
            account_id=None,
            idempotency_key=injected,
            request_hash="hash-A",
            on_race=None,  # the resource-backstop path that LOGS the race instead of resolving it
        )

    rendered = _rendered_messages(mock_logger)
    assert any("INJECTED-FORGED-LOG-LINE" in m for m in rendered), (
        f"the cache-write race log sink must fire, else this oracle proves nothing: {rendered}"
    )
    for m in rendered:
        assert injected not in m, f"the raw CR/LF-bearing key must never reach the log (CWE-117): {m!r}"
        assert "\r" not in m and "\n" not in m, f"no control char may reach an idempotency log line: {m!r}"
