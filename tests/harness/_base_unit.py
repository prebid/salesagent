"""Base test environment for _impl function testing.

Subclasses override class attributes and methods to create domain-specific
test environments. The base handles patch lifecycle (enter/exit) and provides
a dict of active mocks keyed by short name.

Design decision: explicit classes, not pytest fixtures.
- Discoverability: ``grep "class DeliveryPollEnv"`` finds the API
- Self-documentation: one file, one class, full API visible
- Testability: harness classes can be tested without pytest fixture machinery
- Token efficiency: agent prompt includes class docstring, not 110 lines of helpers
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, _patch


class ImplTestEnv:
    """Base test environment for _impl function testing.

    Subclasses override:
        MODULE: str                       -- e.g. "src.core.tools.media_buy_delivery"
        _patch_targets() -> dict          -- {short_name: full_patch_path}
        _configure_defaults() -> None     -- wire up happy-path return values
        call_impl(**kwargs) -> Any        -- call the production function

    Usage::

        with DeliveryPollEnv() as env:
            env.add_buy(media_buy_id="mb_001")
            env.set_adapter_response("mb_001", impressions=5000)
            response = env.call_impl(media_buy_ids=["mb_001"])
            assert response.aggregated_totals.impressions == 5000.0

    Attributes:
        mock: dict[str, MagicMock]  -- active mocks keyed by short name
        identity: ResolvedIdentity  -- default identity (override via constructor)
    """

    MODULE: str = ""  # Override in subclass

    def __init__(
        self,
        principal_id: str = "test_principal",
        tenant_id: str = "test_tenant",
        dry_run: bool = False,
    ) -> None:
        self._principal_id = principal_id
        self._tenant_id = tenant_id
        self._dry_run = dry_run
        self.mock: dict[str, MagicMock] = {}
        self._patchers: list[_patch[Any]] = []
        self._identity: Any = None  # Lazy-built on first access

    @property
    def identity(self) -> Any:
        """ResolvedIdentity with sane defaults. Built lazily to avoid import at class level."""
        if self._identity is None:
            from src.core.resolved_identity import ResolvedIdentity
            from src.core.testing_hooks import AdCPTestContext

            self._identity = ResolvedIdentity(
                principal_id=self._principal_id,
                tenant_id=self._tenant_id,
                tenant={"tenant_id": self._tenant_id, "name": "Test Tenant"},
                protocol="mcp",
                testing_context=AdCPTestContext(
                    dry_run=self._dry_run,
                    mock_time=None,
                    jump_to_event=None,
                    test_session_id=None,
                ),
            )
        return self._identity

    def _patch_targets(self) -> dict[str, str]:
        """Return {short_name: full.dotted.patch.path}.

        Override in subclass. Example::

            return {
                "uow": f"{self.MODULE}.MediaBuyUoW",
                "adapter": f"{self.MODULE}.get_adapter",
            }
        """
        raise NotImplementedError

    def _configure_defaults(self) -> None:
        """Wire up happy-path return values on self.mock entries.

        Called automatically after all patches are started.
        Override in subclass.
        """

    def call_impl(self, **kwargs: Any) -> Any:
        """Call the production function under test.

        Override in subclass. Should construct the request object
        and call the _impl function.
        """
        raise NotImplementedError

    # -- Context manager protocol ------------------------------------------

    def __enter__(self) -> ImplTestEnv:
        from unittest.mock import patch

        targets = self._patch_targets()
        for short_name, target_path in targets.items():
            patcher = patch(target_path)
            mock_obj = patcher.start()
            self.mock[short_name] = mock_obj
            self._patchers.append(patcher)

        self._configure_defaults()
        return self

    def __exit__(self, *exc: object) -> bool:
        for patcher in reversed(self._patchers):
            patcher.stop()
        self._patchers.clear()
        self.mock.clear()
        return False
