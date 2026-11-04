"""Prometheus counter readers shared by the test suites.

The registry readers used to live in ``tests/helpers/signing.py``. They read
every counter, not just the signing ones, so the status-sweep scenarios use
them instead of matching a scrape line by substring.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager


def counter_samples(sample_name: str) -> dict[tuple[tuple[str, str], ...], float]:
    """All Prometheus samples named *sample_name*, keyed by their label set."""
    from prometheus_client import REGISTRY

    out: dict[tuple[tuple[str, str], ...], float] = {}
    for family in REGISTRY.collect():
        for sample in family.samples:
            if sample.name == sample_name:
                out[tuple(sorted(sample.labels.items()))] = sample.value
    return out


def counter_total(sample_name: str) -> float:
    return sum(counter_samples(sample_name).values())


def samples_with(sample_name: str, **labels: str) -> dict[tuple[tuple[str, str], ...], float]:
    """Samples of *sample_name* whose labels are a superset of *labels*."""
    wanted = labels.items()
    return {key: value for key, value in counter_samples(sample_name).items() if wanted <= dict(key).items()}


@contextmanager
def assert_counter_delta(metric: str, expected: int, *, why: str = "") -> Iterator[None]:
    """Assert *metric* moved by exactly *expected* across the block.

    ``expected=0`` is a first-class case. "Production did not count this" is as
    much of an observable as "it counted once".
    """
    before = counter_total(metric)
    yield
    after = counter_total(metric)
    detail = f" {why}" if why else ""
    assert after == before + expected, (
        f"{metric} must move by {expected} across this block; it went {before} -> {after}.{detail}"
    )
