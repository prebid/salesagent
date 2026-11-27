"""Removing a tenant's favicon deletes every favicon file an upload left on disk.

Uploads once accepted SVG, so a tenant's favicon directory can still hold a ``favicon.svg``
beside the raster file in use. Uploading a new favicon cleared it; removing the favicon
left it behind. Both go through one cleanup now.
"""

import shutil
import uuid
from pathlib import Path

import pytest

from src.admin.blueprints.tenants import _get_favicon_upload_dir
from tests.factories import TenantFactory

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


@pytest.fixture
def tenant_with_a_legacy_svg(bound_factory_session):
    # A unique id: the favicon directory is shared by every worker on the machine.
    tenant_id = f"favicon_{uuid.uuid4().hex[:12]}"
    tenant_dir = Path(_get_favicon_upload_dir()) / tenant_id
    tenant_dir.mkdir()
    (tenant_dir / "favicon.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>')
    (tenant_dir / "favicon.png").write_bytes(bytes([0x89]) + b"PNG\r\n\x1a\n")
    tenant = TenantFactory(tenant_id=tenant_id, favicon_url=f"/static/favicons/{tenant_id}/favicon.png")
    yield tenant, tenant_dir
    shutil.rmtree(tenant_dir, ignore_errors=True)


def test_removing_a_favicon_deletes_a_legacy_svg_beside_it(authenticated_admin_session, tenant_with_a_legacy_svg):
    tenant, tenant_dir = tenant_with_a_legacy_svg

    response = authenticated_admin_session.post(f"/tenant/{tenant.tenant_id}/remove_favicon")

    assert response.status_code == 302, response.get_data(as_text=True)[:500]
    assert sorted(path.name for path in tenant_dir.iterdir()) == []
