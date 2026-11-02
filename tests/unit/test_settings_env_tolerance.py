"""The settings loader reads the environment the way the helpers it replaced did.

Three facts, the first two reproduced as startup crashes on checked-in inputs before they
were graded:

- An EMPTY value is an unset value. ``.github/workflows/ci.yml`` sets ``ADCP_TESTING: ""``
  on the e2e host and the compose files use ``${VAR:-}``, so a container gets ``''`` for a
  variable the host left unset. The replaced helpers read ``''`` as the default.
- The six group names are not environment variables. ``Settings`` is a plain composite,
  so a shell with ``TESTING=1`` or ``DATABASE=x`` starts the process.
- A NON-NUMERIC value for a numeric knob is refused here, naming the field. This is where
  ``db_config.int_env`` went: that helper parsed ``DB_PORT`` itself and raised
  ``Invalid integer value for DB_PORT``, and commit 3d6bd0593 deleted it along with every
  other direct ``os.environ`` read under ``src/`` — "a bad numeric knob now fails at
  startup instead of being logged and ignored". The other three branches it graded (a valid
  value parses, a missing one defaults, an empty one defaults) are the two tests above;
  this is the branch that had no successor test.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from pydantic_settings import BaseSettings

from src.core.config import Settings, TestingSettings, load_settings


@pytest.mark.parametrize(
    ("name", "read"),
    [
        ("ADCP_TESTING", lambda s: s.testing.adcp_testing),
        ("DB_PORT", lambda s: s.database.db_port),
        ("DELIVERY_WEBHOOK_INTERVAL", lambda s: s.limits.delivery_webhook_interval),
    ],
)
def test_empty_value_is_the_default(monkeypatch: pytest.MonkeyPatch, name: str, read) -> None:
    monkeypatch.delenv(name, raising=False)
    expected = read(load_settings())

    monkeypatch.setenv(name, "")

    assert read(load_settings()) == expected


def test_empty_values_are_the_defaults_together(monkeypatch: pytest.MonkeyPatch) -> None:
    """The acceptance command, as a test: all three empty at once."""
    for name in ("ADCP_TESTING", "DB_PORT", "DELIVERY_WEBHOOK_INTERVAL"):
        monkeypatch.setenv(name, "")

    settings = load_settings()

    assert settings.testing.adcp_testing is False
    assert settings.database.db_port == 5432
    assert settings.limits.delivery_webhook_interval == 3600


@pytest.mark.parametrize("name", ["DB_PORT", "DELIVERY_WEBHOOK_INTERVAL"])
def test_a_non_numeric_value_is_refused_at_load_naming_the_field(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    """A knob that cannot be a number fails where the environment is read.

    The deleted ``int_env`` raised a ``ValueError`` naming the variable at the first
    read; the loader raises a ``ValidationError`` naming the field at ``load_settings()``.
    Either way the process does not run on a value nobody can honour, and the message
    says which knob is wrong — the assertion below is on that attribution, because a
    bare "validation error" would not tell an operator what to fix.
    """
    monkeypatch.setenv(name, "not-a-number")

    with pytest.raises(ValidationError, match=name.lower()):
        load_settings()


def test_group_names_are_not_environment_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in (
        ("TESTING", "1"),
        ("DATABASE", "x"),
        ("RUNTIME", "y"),
        ("AUTH", "z"),
        ("INTEGRATIONS", "1"),
        ("LIMITS", "2"),
    ):
        monkeypatch.setenv(name, value)

    settings = load_settings()

    assert not issubclass(Settings, BaseSettings)
    assert isinstance(settings.testing, TestingSettings)
