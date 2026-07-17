"""Safety checks for finalization concurrency duration configuration."""

import importlib

import pytest


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("MEDIA_BUY_FINALIZE_LEASE_TTL", "0"),
        ("MEDIA_BUY_FINALIZE_LEASE_TTL", "-1"),
        ("MEDIA_BUY_FINALIZE_LEASE_TTL", "not-a-number"),
        ("MEDIA_BUY_FINALIZE_AMBIGUOUS_COOLDOWN", "0"),
        ("MEDIA_BUY_FINALIZE_AMBIGUOUS_COOLDOWN", "-1"),
        ("MEDIA_BUY_FINALIZE_AMBIGUOUS_COOLDOWN", "not-a-number"),
    ],
)
def test_finalization_durations_must_be_positive(monkeypatch, name, value):
    """A non-positive or noninteger lease/cool-down must fail startup, never disable its fence."""
    import src.admin.services.media_buy_completion as completion

    with monkeypatch.context() as scoped:
        scoped.setenv(name, value)
        with pytest.raises(ValueError, match=f"{name} must be a positive integer"):
            importlib.reload(completion)
    importlib.reload(completion)
