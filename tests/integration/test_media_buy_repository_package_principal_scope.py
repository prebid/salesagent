"""Integration tests for MediaBuyRepository.find_package_with_media_buy principal scope.

A package hangs off a media buy one principal owns. The lookup feeds the
sync_creatives assignment path, which inserts a creative_assignments row under the
CALLER's principal on the buy the lookup returned (src/core/tools/creatives/
_assignments.py:213, :342), so a tenant-only lookup let one principal write an
assignment onto another principal's buy -- holding the owner's buy in
pending_creatives, moving an approved draft, and blocking the owner's own later
assignment of a creative with the same id (the unique constraint carries no
principal_id). GH #2417.

AdCP 3.1.1 ``enums/error-code.json`` MEDIA_BUY_NOT_FOUND: "Referenced media buy does
not exist or is not accessible to the requesting agent." A buy the caller cannot
reach is not found, and neither are its packages -- so the lookup answers None and
the existing not-found path (:222-241) reports PACKAGE_NOT_FOUND in strict mode and
the package in assignment_errors otherwise.
"""

import pytest

from tests.harness._base import IntegrationEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class _RepoEnv(IntegrationEnv):
    """Bare integration env for repository tests — no external patches."""

    EXTERNAL_PATCHES: dict[str, str] = {}

    def get_session(self):
        """Expose session for direct repository construction."""
        self._commit_factory_data()
        return self._session


class TestFindPackageWithMediaBuyPrincipalScope:
    """The lookup resolves the caller's packages and no one else's."""

    def test_returns_the_callers_own_package(self, integration_db):
        from src.core.database.repositories.media_buy import MediaBuyRepository
        from tests.factories import MediaBuyFactory, MediaPackageFactory, PrincipalFactory, TenantFactory

        with _RepoEnv() as env:
            tenant = TenantFactory(tenant_id="pkg_scope_own")
            owner = PrincipalFactory(tenant=tenant, principal_id="prin_owner")
            buy = MediaBuyFactory(tenant=tenant, principal=owner, media_buy_id="mb_owner")
            MediaPackageFactory(media_buy=buy, package_id="pkg_owned")
            session = env.get_session()
            repo = MediaBuyRepository(session, "pkg_scope_own")
            result = repo.find_package_with_media_buy("pkg_owned", "prin_owner")

        assert result is not None
        package, media_buy = result
        assert package.package_id == "pkg_owned"
        assert media_buy.media_buy_id == "mb_owner"

    def test_returns_none_for_another_principals_package(self, integration_db):
        """The #2417 regression: the package exists in the tenant, on someone else's buy."""
        from src.core.database.repositories.media_buy import MediaBuyRepository
        from tests.factories import MediaBuyFactory, MediaPackageFactory, PrincipalFactory, TenantFactory

        with _RepoEnv() as env:
            tenant = TenantFactory(tenant_id="pkg_scope_cross")
            principal_a = PrincipalFactory(tenant=tenant, principal_id="prin_a")
            PrincipalFactory(tenant=tenant, principal_id="prin_b")
            buy_a = MediaBuyFactory(tenant=tenant, principal=principal_a, media_buy_id="mb_a")
            MediaPackageFactory(media_buy=buy_a, package_id="pkg_example_a")
            session = env.get_session()
            repo = MediaBuyRepository(session, "pkg_scope_cross")
            as_b = repo.find_package_with_media_buy("pkg_example_a", "prin_b")
            as_a = repo.find_package_with_media_buy("pkg_example_a", "prin_a")

        assert as_b is None, "principal B must not resolve principal A's package"
        assert as_a is not None, "principal A still resolves their own package"

    def test_returns_none_for_a_package_that_does_not_exist(self, integration_db):
        from src.core.database.repositories.media_buy import MediaBuyRepository
        from tests.factories import PrincipalFactory, TenantFactory

        with _RepoEnv() as env:
            tenant = TenantFactory(tenant_id="pkg_scope_missing")
            PrincipalFactory(tenant=tenant, principal_id="prin_missing")
            session = env.get_session()
            repo = MediaBuyRepository(session, "pkg_scope_missing")
            result = repo.find_package_with_media_buy("pkg_absent", "prin_missing")

        assert result is None
