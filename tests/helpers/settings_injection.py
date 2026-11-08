"""State a setting a test depends on, in typed form, on the object production reads.

No production code reads the environment. ``ruff-environment.toml`` bans ``os.environ``
and ``os.getenv`` under ``src/`` and ``scripts/`` outside the settings loader, so every
knob reaches production as a NAMED FACT on the settings object --
``get_settings().limits.adcp_outbound_backoff_base_seconds``
(``src/core/security/outbound_http.py``), never as a string in the environ.

A test that wrote ``monkeypatch.setenv("ADCP_OUTBOUND_BACKOFF_BASE_SECONDS", "0.001")``
was therefore exercising the LOADER'S STRING PARSING and only reaching the seam as a side
effect of it -- and only when nothing had already built the settings, because
:func:`src.core.config.get_settings` caches. That made the mechanism order-dependent in a
way no call site could see: a fixture that constructed anything reading settings (a
``CreativeAgentRegistry()`` reads one in ``__init__``) froze the defaults, the later
``setenv`` landed nowhere, and the test ran against a posture it had explicitly asked to
change. Two whole test files were failing that way, and a third was passing only because
its own hatch flip did nothing.

So: construct the fact. :func:`inject_limits` writes the typed value onto the settings
object the process reads, which is what the test actually depends on, and says so in the
test rather than in a string a loader has to interpret.

ACCUMULATING, on purpose. Each call replaces only the fields it names, on top of whatever
is already there, so two calls in one test (an open egress hatch, then a fast backoff base)
compose instead of the second one silently reverting the first. That is the whole ordering
hazard the environment route had, removed rather than patched.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pytest

    from src.core.config import LimitSettings, RuntimeSettings


def inject_limits(monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> LimitSettings:
    """Put ``overrides`` on the cached settings' :class:`LimitSettings` and return it.

    Args:
        monkeypatch: the test's monkeypatch, so the previous settings object is restored
            at teardown and nothing leaks to the next test.
        overrides: settings-field names with the values to inject, TYPED --
            ``adcp_outbound_backoff_base_seconds=0.001``, not ``"0.001"``. A name that is
            not a field is refused here rather than being ignored.

    Returns:
        The injected ``LimitSettings``, for a caller that wants to read a shipped default
        off it without a second import.
    """
    return _inject(monkeypatch, "limits", overrides)


def inject_runtime(monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> RuntimeSettings:
    """Put ``overrides`` on the cached settings' :class:`RuntimeSettings` and return it.

    The same contract as :func:`inject_limits`, for the deployment facts --
    ``production=True`` makes ``RuntimeSettings.is_production`` answer True.
    """
    return _inject(monkeypatch, "runtime", overrides)


def _inject(monkeypatch: pytest.MonkeyPatch, section: str, overrides: dict[str, Any]) -> Any:
    """Replace the named *section* of the cached settings with a copy carrying *overrides*."""
    # Compose the app first. src.app calls load_settings() when it is imported, which
    # replaces the settings object, so an injection made before a later first import of it (an
    # env entered after this call, say) would be discarded.
    import src.app  # noqa: F401
    import src.core.config as config_module
    from src.core.config import get_settings

    current = get_settings()
    model = getattr(current, section)
    unknown = set(overrides) - set(type(model).model_fields)
    if unknown:
        raise AttributeError(f"not {type(model).__name__} fields: {sorted(unknown)}")

    # ``model_copy`` rather than a re-validated construction: the value is already typed,
    # and re-running validation here would make this helper's behaviour depend on the
    # constraints of fields the caller did not name.
    injected = model.model_copy(update=overrides)
    monkeypatch.setattr(config_module, "_settings", replace(current, **{section: injected}))
    return injected
