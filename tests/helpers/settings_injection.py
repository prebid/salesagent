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

import os
from dataclasses import replace
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

if TYPE_CHECKING:
    from types import TracebackType

    import pytest

    from src.core.config import LimitSettings

# Where the platform's AI credential comes from. With PYDANTIC_AI_PROVIDER unset the
# platform provider is Google, so GEMINI_API_KEY is the key that counts; the others are
# removed so an ambient provider/model/key from the developer's shell cannot make a
# different vendor the platform's.
PLATFORM_AI_ENV: tuple[str, ...] = (
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "ANTHROPIC_API_KEY",
    "PYDANTIC_AI_PROVIDER",
    "PYDANTIC_AI_MODEL",
)


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
    # Compose the app first. src.app calls load_settings() when it is imported, which
    # replaces the settings object, so an injection made before a later first import of it (an
    # env entered after this call, say) would be discarded.
    import src.app  # noqa: F401
    import src.core.config as config_module
    from src.core.config import LimitSettings, get_settings

    unknown = set(overrides) - set(LimitSettings.model_fields)
    if unknown:
        raise AttributeError(f"not LimitSettings fields: {sorted(unknown)}")

    current = get_settings()
    # ``model_copy`` rather than a re-validated construction: the value is already typed,
    # and re-running validation here would make this helper's behaviour depend on the
    # constraints of fields the caller did not name.
    limits = current.limits.model_copy(update=overrides)
    monkeypatch.setattr(config_module, "_settings", replace(current, limits=limits))
    return limits


class PlatformAICredential:
    """The platform's AI credential, stated for the length of a test: none, or one Gemini key.

    ``tests/conftest.py`` sets ``GEMINI_API_KEY`` for every test, and
    ``AIServiceFactory.build`` falls back to the platform key, so a test about the
    SELLER's own configuration has to remove it or every tenant looks AI-enabled.

    Pinned in the ENVIRONMENT, unlike :func:`inject_limits`: a composition root rebuilds
    the settings from the environment whenever it starts (the REST leg imports
    ``src.app``, which calls ``load_settings()``), so a value put only on the settings
    object is undone by the first REST dispatch. The settings are reloaded on every
    change and again after the environment is restored.

    A harness env calls :meth:`start` and registers :meth:`stop` with its guard; a test
    uses it as a context manager.
    """

    def __init__(self, gemini_api_key: str | None = None) -> None:
        """``gemini_api_key`` is the platform key :meth:`start` states; ``None`` is none."""
        self._environ = patch.dict(os.environ)
        self._gemini_api_key = gemini_api_key

    def start(self) -> None:
        self._environ.start()
        self.set(self._gemini_api_key)

    def set(self, gemini_api_key: str | None) -> None:
        """Replace the platform credential: ``None`` removes it."""
        from src.core.config import load_settings

        for var in PLATFORM_AI_ENV:
            os.environ.pop(var, None)
        if gemini_api_key is not None:
            os.environ["GEMINI_API_KEY"] = gemini_api_key
        load_settings()

    def stop(self) -> None:
        from src.core.config import load_settings

        self._environ.stop()
        load_settings()

    def __enter__(self) -> PlatformAICredential:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.stop()
