"""Reusable thread-pool bulkheads whose permits outlive caller cancellation."""

from __future__ import annotations

import asyncio
import threading
from collections import deque
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from time import monotonic
from typing import Any


def _submit_with_held_release[R](
    submit: Callable[..., Future[R]],
    release: Callable[[], None],
    function: Callable[..., R],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Future[R]:
    """Submit work and release capacity only when its concurrent future ends."""
    try:
        future = submit(function, *args, **kwargs)
    except BaseException:
        release()
        raise
    future.add_done_callback(lambda _future: release())
    return future


class _AsyncAdmissionGate:
    """FIFO executor-wide permits that can be awaited from any event loop."""

    def __init__(self, capacity: int) -> None:
        self._capacity = capacity
        self._available = capacity
        self._lock = threading.Lock()
        self._waiters: deque[Future[None]] = deque()

    async def acquire(self) -> None:
        """Acquire without a polling loop or a blocking helper thread."""
        with self._lock:
            if self._available:
                self._available -= 1
                return
            waiter: Future[None] = Future()
            self._waiters.append(waiter)

        try:
            await asyncio.wrap_future(waiter)
        except BaseException:
            # Cancellation can race a worker completion. If this waiter was
            # still pending, cancel/remove it and no permit was transferred.
            # If release already made the future non-cancellable, this caller
            # owns a permit and must hand it to the next waiter.
            release_granted_permit = False
            with self._lock:
                if waiter.cancelled() or waiter.cancel():
                    try:
                        self._waiters.remove(waiter)
                    except ValueError:
                        pass
                else:
                    release_granted_permit = True
            if release_granted_permit:
                self.release()
            raise

    def release(self) -> None:
        """Transfer one permit to the oldest live waiter, or return it."""
        with self._lock:
            while self._waiters:
                waiter = self._waiters.popleft()
                # Atomically wins against Future.cancel(). Once RUNNING, the
                # cancelled await path knows it received a permit and releases
                # it again rather than leaking capacity.
                if waiter.set_running_or_notify_cancel():
                    waiter.set_result(None)
                    return
            self._available += 1
            if self._available > self._capacity:
                raise ValueError("Async admission gate released too many times")


class AsyncThreadPoolBulkhead:
    """Run blocking work off-loop with executor-wide admission capacity.

    One instance can be called from many event loops (FastAPI/A2A loops and
    Flask paths that create short-lived loops with ``asyncio.run``). Capacity
    therefore belongs to the executor, not to any individual loop.
    """

    def __init__(self, *, max_workers: int, thread_name_prefix: str) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix=thread_name_prefix,
        )
        self._limiter = _AsyncAdmissionGate(max_workers)

    def submit[R](self, function: Callable[..., R], /, *args: Any, **kwargs: Any) -> Future[R]:
        """Submit through the owned executor (exposed for structural test spies)."""
        return self._executor.submit(function, *args, **kwargs)

    async def run[R](self, function: Callable[..., R], /, *args: Any, **kwargs: Any) -> R:
        """Await one worker while retaining its permit after cancellation.

        Admission uses a FIFO executor-wide gate. Waiters are concurrent
        futures, so they can be completed safely from worker threads and
        awaited from unrelated event loops without polling or helper threads.
        A cancellation race either removes the pending waiter or returns an
        already-granted permit to the next caller.
        """
        await self._limiter.acquire()

        future = _submit_with_held_release(
            self.submit,
            self._limiter.release,
            function,
            args,
            kwargs,
        )
        return await asyncio.wrap_future(future)


class SyncThreadPoolBulkhead:
    """Bound synchronous blocking work by both capacity and wall-clock deadline."""

    def __init__(self, *, max_workers: int, thread_name_prefix: str) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix=thread_name_prefix,
        )
        self._limiter = threading.BoundedSemaphore(max_workers)

    def submit[R](self, function: Callable[..., R], /, *args: Any, **kwargs: Any) -> Future[R]:
        """Submit through the owned executor."""
        return self._executor.submit(function, *args, **kwargs)

    def run[R](
        self,
        function: Callable[..., R],
        /,
        *args: Any,
        timeout_seconds: float,
        **kwargs: Any,
    ) -> R:
        """Run within one total admission+execution deadline.

        Timing out never releases the permit early. The completion callback owns
        release, so a stuck libc resolver cannot be replaced by an unbounded
        sequence of newly submitted workers.
        """
        deadline = monotonic() + timeout_seconds
        if not self._limiter.acquire(timeout=max(0.0, timeout_seconds)):
            raise TimeoutError("Thread-pool bulkhead admission timed out")

        future = _submit_with_held_release(
            self.submit,
            self._limiter.release,
            function,
            args,
            kwargs,
        )
        remaining = max(0.0, deadline - monotonic())
        try:
            return future.result(timeout=remaining)
        except FutureTimeoutError as exc:
            raise TimeoutError("Thread-pool worker timed out") from exc
