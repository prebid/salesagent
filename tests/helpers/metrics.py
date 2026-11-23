"""Prometheus counter readers shared by the test suites.

The registry readers used to live in ``tests/helpers/signing.py``. They read
every counter, not just the signing ones, so the status-sweep scenarios use
them instead of matching a scrape line by substring.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager


def counter_samples(sample_name: str) -> dict[tuple[tuple[str, str], ...], float]:
    """All Prometheus samples named *sample_name*, keyed by their label set."""
    from prometheus_client import REGISTRY

    out: dict[tuple[tuple[str, str], ...], float] = {}
    for family in REGISTRY.collect():
        for sample in family.samples:
            if sample.name == sample_name:
                out[tuple(sorted(sample.labels.items()))] = sample.value
    return out


#: The scheduler-isolation counter and the status sweep's ``scheduler`` label, named once for every test.
ISOLATION_METRIC = "scheduler_isolation_errors_total"
STATUS_SCHEDULER = "media_buy_status"


def counter_total(sample_name: str, **labels: str) -> float:
    """Sum of the *sample_name* samples whose labels include *labels*. A missing series is zero."""
    return sum(samples_with(sample_name, **labels).values())


def isolation_error_count(tenant_id: str, error_type: str, *, scheduler: str = STATUS_SCHEDULER) -> float:
    """The isolation counter for one scheduler, tenant, and error type. Reading it does not create the series."""
    return counter_total(ISOLATION_METRIC, scheduler=scheduler, tenant_id=tenant_id, error_type=error_type)


def samples_with(sample_name: str, **labels: str) -> dict[tuple[tuple[str, str], ...], float]:
    """Samples of *sample_name* whose labels are a superset of *labels*."""
    wanted = labels.items()
    return {key: value for key, value in counter_samples(sample_name).items() if wanted <= dict(key).items()}


@contextmanager
def assert_counter_delta(metric: str, expected: int, *, why: str = "", **labels: str) -> Iterator[None]:
    """Assert *metric* moved by exactly *expected* across the block.

    With *labels*, only the samples whose labels include them are summed, so a
    test can scope the delta to one tenant or one error type. Without them the
    whole counter is read.

    ``expected=0`` is a first-class case. "Production did not count this" is as
    much of an observable as "it counted once".
    """
    before = counter_total(metric, **labels)
    yield
    after = counter_total(metric, **labels)
    scope = f"{{{', '.join(f'{k}={v}' for k, v in labels.items())}}}" if labels else ""
    detail = f" {why}" if why else ""
    assert after == before + expected, (
        f"{metric}{scope} must move by {expected} across this block; it went {before} -> {after}.{detail}"
    )


def assert_isolation_delta(expected: int, **labels: str) -> AbstractContextManager[None]:
    """:func:`assert_counter_delta` on the isolation counter, scoped to the status sweep."""
    return assert_counter_delta(ISOLATION_METRIC, expected, scheduler=STATUS_SCHEDULER, **labels)
