"""Oracle: the webhook circuit breaker's three env knobs actually configure it.

``_configured_breaker`` and its three knobs
(``ADCP_WEBHOOK_BREAKER_FAILURE_THRESHOLD``, ``_SUCCESS_THRESHOLD``,
``_TIMEOUT_SECONDS``) shipped with no test at all — ``grep -rn
"ADCP_WEBHOOK_BREAKER" tests/`` returned zero before this file.

The unset branch is the one that matters most, and it is the reason this is not
ceremony. ``docker-compose.e2e.yml`` supplies a shorter recovery timeout so the
e2e stack can reach HALF_OPEN without spending 60 real seconds per scenario. If
a knob stopped being read — renamed, typo'd, or moved to an import-time read
that freezes the first value — the stack would go on looking configured while
running the shipped 60-second default, and every breaker scenario would either
slow down or silently stop reaching the state it grades. Nothing would fail.

The three knobs are now read by the settings loader (``src/core/config.py``,
``LimitSettings``) rather than by a local ``env_float`` helper, which commit
3d6bd0593 deleted along with every other direct ``os.environ`` read under
``src/``. That moved the rejection branches too, and CHANGED them on purpose:
``env_float`` fell back to the shipped default on a non-positive or non-numeric
value, while ``Field(gt=0)`` on an ``int`` refuses it at ``load_settings()`` —
"a bad numeric knob now fails at startup instead of being logged and ignored".
Both readings agree on what must never happen (a breaker that trips on nothing
or never recovers), so that obligation is graded below against the refusal.
An EMPTY value stays the default: CI and the compose files hand a container
``${VAR:-}`` for a variable the host left unset (``env_ignore_empty``).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.core.config import LimitSettings, load_settings
from src.services.webhook_delivery_service import _configured_breaker

pytestmark = pytest.mark.unit

_BREAKER_FAILURE_THRESHOLD_ENV = "ADCP_WEBHOOK_BREAKER_FAILURE_THRESHOLD"
_BREAKER_SUCCESS_THRESHOLD_ENV = "ADCP_WEBHOOK_BREAKER_SUCCESS_THRESHOLD"
_BREAKER_TIMEOUT_ENV = "ADCP_WEBHOOK_BREAKER_TIMEOUT_SECONDS"


def _shipped_default(field: str) -> int:
    """The default declared on the settings field, read off the model rather than copied.

    A literal here would be a second statement of the shipped policy and could drift from
    ``LimitSettings``; reading the field's default cannot. It is also the only way to name
    the shipped value in the unset-branch test without asking the loader — which is the
    thing under test.
    """
    return LimitSettings.model_fields[field].default


# (env var, settings field, breaker attribute, a distinct value to set).
# The set values are deliberately unequal to each other AND to every default, so
# a breaker that wired two knobs to the same field cannot pass.
_KNOBS = [
    (_BREAKER_FAILURE_THRESHOLD_ENV, "adcp_webhook_breaker_failure_threshold", "failure_threshold", 11),
    (_BREAKER_SUCCESS_THRESHOLD_ENV, "adcp_webhook_breaker_success_threshold", "success_threshold", 7),
    (_BREAKER_TIMEOUT_ENV, "adcp_webhook_breaker_timeout_seconds", "timeout_seconds", 13),
]


@pytest.mark.parametrize(("env_var", "field", "attribute", "configured"), _KNOBS)
def test_a_set_knob_reaches_the_breaker(env_var, field, attribute, configured, monkeypatch) -> None:
    """Setting the variable changes the field it names, and only that field."""
    monkeypatch.setenv(env_var, str(configured))
    load_settings()

    breaker = _configured_breaker()

    assert getattr(breaker, attribute) == configured, (
        f"{env_var}={configured} did not reach breaker.{attribute} "
        f"(got {getattr(breaker, attribute)!r}) — the knob is not wired"
    )
    # Every OTHER field keeps its default: a knob wired to the wrong attribute
    # would satisfy the assertion above on some other run and never be caught.
    for other_var, other_field, other_attr, _ in _KNOBS:
        if other_var == env_var:
            continue
        assert getattr(breaker, other_attr) == _shipped_default(other_field), (
            f"setting {env_var} also changed breaker.{other_attr} — the knobs are crossed"
        )


@pytest.mark.parametrize(("env_var", "field", "attribute", "_configured"), _KNOBS)
def test_an_unset_knob_falls_back_to_the_shipped_default(env_var, field, attribute, _configured, monkeypatch) -> None:
    """With nothing set, the breaker carries the shipped policy."""
    monkeypatch.delenv(env_var, raising=False)
    load_settings()

    assert getattr(_configured_breaker(), attribute) == _shipped_default(field)


@pytest.mark.parametrize(("env_var", "field", "attribute", "_configured"), _KNOBS)
@pytest.mark.parametrize("rejected", ["0", "-1", "not-a-number"])
def test_a_rejected_value_is_refused_rather_than_honoured(
    env_var, field, attribute, _configured, rejected, monkeypatch
) -> None:
    """A non-positive or non-numeric knob fails at startup, not at the first delivery.

    Honouring ``0`` would give a breaker with a zero failure threshold (opens on
    nothing) or a zero recovery timeout (never stays open) — both worse than the
    default, and both invisible without this branch. ``Field(gt=0)`` on the settings
    field refuses it where the environment is read, so the process never reaches
    ``_configured_breaker`` with a value it would have to second-guess.
    """
    monkeypatch.setenv(env_var, rejected)

    with pytest.raises(ValidationError, match=field):
        load_settings()


@pytest.mark.parametrize(("env_var", "field", "attribute", "_configured"), _KNOBS)
def test_an_empty_value_is_the_shipped_default(env_var, field, attribute, _configured, monkeypatch) -> None:
    """An empty value is an unset value: the compose files hand over ``${VAR:-}``."""
    monkeypatch.setenv(env_var, "")
    load_settings()

    assert getattr(_configured_breaker(), attribute) == _shipped_default(field)
