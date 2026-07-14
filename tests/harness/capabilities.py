"""CapabilitiesEnv — integration test environment for get_adcp_capabilities.

Wired for the BR-UC-010 version-negotiation scenarios: every dispatch can
carry the AdCP version-negotiation envelope (``adcp_version`` /
``adcp_major_version``), so each transport exercises its own boundary
validation — the MCP RequestCompatMiddleware (Step 2), the A2A explicit-skill
dispatch, and the REST router dependency — before the tool runs.

Requires: integration_db fixture (creates test PostgreSQL DB). The tool's
DB reads (principal lookup, TenantConfigUoW) run for real against
factory-created rows from ``setup_default_data()``.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import patch

from adcp.types import GetAdcpCapabilitiesResponse

from src.core.request_compat import ADCP_NEGOTIATION_FIELDS
from tests.harness._base import IntegrationEnv
from tests.harness.transport import Transport


class CapabilitiesEnv(IntegrationEnv):
    """Integration test environment for _get_adcp_capabilities_impl.

    No external services to mock — capabilities is a pure discovery read.
    """

    REST_ENDPOINT = "/api/v1/capabilities"
    REST_METHOD = "get"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        from src.core.adcp_version import adcp_build_version, supported_adcp_versions

        self._version_policy_lease = uuid.uuid4().hex
        self._configured_supported_versions = supported_adcp_versions()
        self._configured_build_version = adcp_build_version()
        self._e2e_version_policy_lease_attempted = False

    def configure_version_policy(
        self,
        transport: Transport,
        *,
        supported_versions: tuple[str, ...] | None = None,
        build_version: str | None = None,
    ) -> None:
        """Realize seller-policy setup on the selected transport's true surface.

        In-process boundaries use managed patchers. ``e2e_rest`` cannot see
        runner-process patches, so it installs the same complete snapshot on
        the isolated live server through the secret-gated test-control API.
        The subsequent buyer request carries no setup override.
        """
        if supported_versions is not None:
            self._configured_supported_versions = supported_versions
        if build_version is not None:
            self._configured_build_version = build_version

        if transport == Transport.E2E_REST:
            self._install_e2e_version_policy()
            return

        patch_targets: list[tuple[str, Any]] = []
        if supported_versions is not None:
            patch_targets.append(("src.core.adcp_version.supported_adcp_versions", supported_versions))
        if build_version is not None:
            patch_targets.append(("src.core.adcp_version.adcp_build_version", build_version))
        for target, value in patch_targets:
            patcher = patch(target, return_value=value)
            patcher.start()
            self._patchers.append(patcher)

    def _test_control_headers(self) -> dict[str, str]:
        if self.e2e_config is None or not self.e2e_config.test_control_token:
            raise RuntimeError("E2E version-policy setup requires a per-run ADCP_TEST_CONTROL_TOKEN")
        return {"x-adcp-test-control-token": self.e2e_config.test_control_token}

    def _install_e2e_version_policy(self) -> None:
        # A timeout can occur after the server atomically installs the policy
        # but before the response reaches the runner. Mark the lease before the
        # call so teardown always issues an owner-scoped, idempotent reset.
        self._e2e_version_policy_lease_attempted = True
        self._call_e2e_test_control(
            "PUT",
            "/_internal/testing/adcp-version-policy",
            payload={
                "lease_id": self._version_policy_lease,
                "supported_versions": list(self._configured_supported_versions),
                "build_version": self._configured_build_version,
            },
        )

    def _reset_e2e_version_policy(self) -> None:
        self._call_e2e_test_control(
            "DELETE",
            f"/_internal/testing/adcp-version-policy/{self._version_policy_lease}",
        )
        self._e2e_version_policy_lease_attempted = False

    def _call_e2e_test_control(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Call one authenticated setup operation on the isolated test server."""
        import httpx

        assert self.e2e_config is not None
        request_kwargs: dict[str, Any] = {"headers": self._test_control_headers()}
        if payload is not None:
            request_kwargs["json"] = payload
        with httpx.Client(base_url=self.e2e_config.base_url, timeout=10) as client:
            response = client.request(method, path, **request_kwargs)
            response.raise_for_status()

    def __exit__(self, *exc: object) -> bool:
        reset_error: Exception | None = None
        if self._e2e_version_policy_lease_attempted:
            try:
                self._reset_e2e_version_policy()
            except Exception as error:
                reset_error = error

        try:
            result = super().__exit__(*exc)
        except Exception as cleanup_error:
            if reset_error is not None:
                raise ExceptionGroup(
                    "E2E version-policy reset and harness teardown failed", [reset_error, cleanup_error]
                )
            raise
        if reset_error is not None:
            raise reset_error
        return result

    # setup_default_data: inherited from BaseTestEnv — its get-or-create form is
    # required over e2e_rest, where the env's __enter__ auto-seed
    # (_seed_e2e_identity) already created the tenant/principal and a blind
    # factory insert would violate tenants_pkey (#1546 CI).

    def call_impl(self, **kwargs: Any) -> GetAdcpCapabilitiesResponse:
        """Call _get_adcp_capabilities_impl.

        Negotiation-envelope kwargs are dropped: version negotiation is a
        transport-boundary concern and production ``_impl`` never sees the
        fields (they are validated and stripped by each wrapper).
        """
        from src.core.tools.capabilities import _get_adcp_capabilities_impl

        self._commit_factory_data()
        for field in ADCP_NEGOTIATION_FIELDS:
            kwargs.pop(field, None)
        kwargs.setdefault("identity", self.identity)
        kwargs.setdefault("req", None)
        return _get_adcp_capabilities_impl(**kwargs)

    def call_a2a(self, **kwargs: Any) -> GetAdcpCapabilitiesResponse:
        """Call get_adcp_capabilities via real AdCPRequestHandler — full A2A pipeline."""
        return self._run_a2a_handler("get_adcp_capabilities", GetAdcpCapabilitiesResponse, **kwargs)

    def call_mcp(self, **kwargs: Any) -> GetAdcpCapabilitiesResponse:
        """Call get_adcp_capabilities via Client(mcp) — full pipeline dispatch."""
        return self._run_mcp_client("get_adcp_capabilities", GetAdcpCapabilitiesResponse, **kwargs)

    def build_rest_body(self, **kwargs: Any) -> dict[str, Any]:
        """GET /capabilities has no body — flat kwargs become the query string.

        The negotiation pin travels as query params on REST (there is no JSON
        payload on a GET), which is exactly what the api_v1 router dependency
        validates.
        """
        return {k: v for k, v in kwargs.items() if v is not None}

    def parse_rest_response(self, data: dict[str, Any]) -> GetAdcpCapabilitiesResponse:
        """Parse REST JSON into GetAdcpCapabilitiesResponse."""
        return GetAdcpCapabilitiesResponse(**data)
