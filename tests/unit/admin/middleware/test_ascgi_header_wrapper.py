"""Unit tests for ``build_header_wrapper`` — shared ASGI header-mutation helper.

Covers the DRY extraction used by ``RequestIDMiddleware``, ``ServedByMiddleware``,
and ``SecurityHeadersMiddleware`` (and L1c's ``LegacyAdminRedirectMiddleware``).

These tests pin the wrapper's contract directly — the consumer middlewares
have their own behavioral tests (``test_request_id.py``, etc.) that validate
end-to-end behavior through the refactor. If the wrapper changes in a way
that breaks a consumer, both tests suites should fail.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.admin.middleware._ascgi_headers import build_header_wrapper


async def _collect(sends: list[dict[str, Any]], message: dict[str, Any]) -> None:
    sends.append(message)


def _start(status: int = 200, headers: list[tuple[bytes, bytes]] | None = None) -> dict[str, Any]:
    return {"type": "http.response.start", "status": status, "headers": list(headers or [])}


@pytest.mark.asyncio
class TestBuildHeaderWrapperReplace:
    async def test_replace_without_prior_appends(self) -> None:
        sends: list[dict[str, Any]] = []

        async def send(m: dict[str, Any]) -> None:
            await _collect(sends, m)

        wrapped = build_header_wrapper(send, to_set=[(b"x-req-id", b"abc")], mode="replace")
        await wrapped(_start(headers=[]))
        assert sends[0]["headers"] == [(b"x-req-id", b"abc")]

    async def test_replace_strips_collision_then_appends(self) -> None:
        """Existing header with same name is stripped; wrapper value wins."""
        sends: list[dict[str, Any]] = []

        async def send(m: dict[str, Any]) -> None:
            sends.append(m)

        wrapped = build_header_wrapper(send, to_set=[(b"x-req-id", b"new")], mode="replace")
        await wrapped(_start(headers=[(b"x-req-id", b"old"), (b"content-type", b"text/html")]))

        names = [n for n, _ in sends[0]["headers"]]
        assert names.count(b"x-req-id") == 1
        assert dict(sends[0]["headers"])[b"x-req-id"] == b"new"
        # Unrelated headers preserved.
        assert (b"content-type", b"text/html") in sends[0]["headers"]

    async def test_replace_does_not_strip_unrelated_names(self) -> None:
        sends: list[dict[str, Any]] = []

        async def send(m: dict[str, Any]) -> None:
            sends.append(m)

        wrapped = build_header_wrapper(send, to_set=[(b"x-a", b"1")], mode="replace")
        await wrapped(_start(headers=[(b"x-b", b"keep")]))
        pairs = sends[0]["headers"]
        assert (b"x-b", b"keep") in pairs
        assert (b"x-a", b"1") in pairs

    async def test_replace_duplicate_names_in_to_set_last_wins(self) -> None:
        """When `to_set` lists the same name twice, the last occurrence wins."""
        sends: list[dict[str, Any]] = []

        async def send(m: dict[str, Any]) -> None:
            sends.append(m)

        wrapped = build_header_wrapper(
            send,
            to_set=[(b"x-req-id", b"first"), (b"x-req-id", b"second")],
            mode="replace",
        )
        await wrapped(_start(headers=[]))
        # In replace mode both land — the consumer's responsibility to not
        # pass duplicates. Document the semantic with this test.
        values = [v for n, v in sends[0]["headers"] if n == b"x-req-id"]
        assert values == [b"first", b"second"]


@pytest.mark.asyncio
class TestBuildHeaderWrapperAppendIfMissing:
    async def test_appends_when_absent(self) -> None:
        sends: list[dict[str, Any]] = []

        async def send(m: dict[str, Any]) -> None:
            sends.append(m)

        wrapped = build_header_wrapper(send, to_set=[(b"x-frame-options", b"DENY")], mode="append_if_missing")
        await wrapped(_start(headers=[]))
        assert (b"x-frame-options", b"DENY") in sends[0]["headers"]

    async def test_skips_when_handler_already_set(self) -> None:
        """Handler wins — don't overwrite an explicit handler-set header."""
        sends: list[dict[str, Any]] = []

        async def send(m: dict[str, Any]) -> None:
            sends.append(m)

        wrapped = build_header_wrapper(send, to_set=[(b"x-frame-options", b"DENY")], mode="append_if_missing")
        await wrapped(_start(headers=[(b"x-frame-options", b"SAMEORIGIN")]))
        pairs = sends[0]["headers"]
        # Handler's SAMEORIGIN preserved; DENY not appended.
        assert (b"x-frame-options", b"SAMEORIGIN") in pairs
        assert (b"x-frame-options", b"DENY") not in pairs

    async def test_duplicate_names_in_to_set_dedup(self) -> None:
        """First matching name in to_set wins; subsequent dups are skipped."""
        sends: list[dict[str, Any]] = []

        async def send(m: dict[str, Any]) -> None:
            sends.append(m)

        wrapped = build_header_wrapper(
            send,
            to_set=[(b"x-a", b"first"), (b"x-a", b"second")],
            mode="append_if_missing",
        )
        await wrapped(_start(headers=[]))
        values = [v for n, v in sends[0]["headers"] if n == b"x-a"]
        assert values == [b"first"]


@pytest.mark.asyncio
class TestBuildHeaderWrapperPassthrough:
    async def test_body_message_passes_through_unchanged(self) -> None:
        sends: list[dict[str, Any]] = []

        async def send(m: dict[str, Any]) -> None:
            sends.append(m)

        wrapped = build_header_wrapper(send, to_set=[(b"x-a", b"1")], mode="replace")
        body_msg: dict[str, Any] = {"type": "http.response.body", "body": b"hello", "more_body": False}
        await wrapped(body_msg)
        assert sends == [body_msg]

    async def test_trailer_message_passes_through_unchanged(self) -> None:
        sends: list[dict[str, Any]] = []

        async def send(m: dict[str, Any]) -> None:
            sends.append(m)

        wrapped = build_header_wrapper(send, to_set=[(b"x-a", b"1")], mode="replace")
        trailer_msg: dict[str, Any] = {"type": "http.response.trailers", "headers": [(b"x-trailing", b"v")]}
        await wrapped(trailer_msg)
        assert sends == [trailer_msg]

    async def test_empty_to_set_is_noop(self) -> None:
        sends: list[dict[str, Any]] = []

        async def send(m: dict[str, Any]) -> None:
            sends.append(m)

        wrapped = build_header_wrapper(send, to_set=[], mode="replace")
        start = _start(headers=[(b"x-keep", b"v")])
        await wrapped(start)
        # Entire message forwarded verbatim — no mutation.
        assert sends[0]["headers"] == [(b"x-keep", b"v")]


@pytest.mark.asyncio
class TestBuildHeaderWrapperThunk:
    async def test_thunk_resolved_lazily(self) -> None:
        """Thunk is called exactly when the wrapper needs the headers."""
        calls = {"n": 0}

        def thunk() -> list[tuple[bytes, bytes]]:
            calls["n"] += 1
            return [(b"x-lazy", b"v")]

        sends: list[dict[str, Any]] = []

        async def send(m: dict[str, Any]) -> None:
            sends.append(m)

        wrapped = build_header_wrapper(send, to_set=thunk, mode="append_if_missing")
        # Body message: thunk MUST NOT be invoked.
        await wrapped({"type": "http.response.body", "body": b"", "more_body": False})
        assert calls["n"] == 0
        # Start message: thunk invoked once.
        await wrapped(_start(headers=[]))
        assert calls["n"] == 1
        assert (b"x-lazy", b"v") in sends[1]["headers"]

    async def test_thunk_returning_empty_is_noop(self) -> None:
        sends: list[dict[str, Any]] = []

        async def send(m: dict[str, Any]) -> None:
            sends.append(m)

        wrapped = build_header_wrapper(send, to_set=lambda: [], mode="append_if_missing")
        await wrapped(_start(headers=[(b"x-keep", b"v")]))
        assert sends[0]["headers"] == [(b"x-keep", b"v")]


@pytest.mark.asyncio
class TestBuildHeaderWrapperCaseSensitivity:
    async def test_name_byte_compare_is_case_sensitive(self) -> None:
        """ASGI spec requires lowercase header names; helper does byte-exact compare.

        A caller passing an uppercase name would NOT strip/match an existing
        lowercase entry with the same semantic name. This test documents that
        invariant so a future change that adds case-folding would be caught.
        """
        sends: list[dict[str, Any]] = []

        async def send(m: dict[str, Any]) -> None:
            sends.append(m)

        wrapped = build_header_wrapper(
            send,
            to_set=[(b"X-Frame-Options", b"DENY")],  # intentionally uppercase
            mode="append_if_missing",
        )
        # Response already has the lowercase form — helper does NOT see it as
        # a collision (different bytes).
        await wrapped(_start(headers=[(b"x-frame-options", b"SAMEORIGIN")]))
        pairs = sends[0]["headers"]
        assert (b"x-frame-options", b"SAMEORIGIN") in pairs
        assert (b"X-Frame-Options", b"DENY") in pairs
