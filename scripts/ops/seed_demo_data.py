#!/usr/bin/env python3
"""Seed the default tenant with the data needed to serve a working agent.

Run after ``migrate.py`` (which creates the empty default tenant) to populate
the rows that otherwise have to be configured through the admin UI:

- ``tenants`` row: ad_server, virtual_host, public_agent_url
- ``currency_limits``: one USD row
- ``property_tags``: ``all_inventory``
- ``principals``: ``ci-test-principal`` with ``ci-test-token`` (mock advertiser)
- ``publisher_partners``: one verified demo publisher (satisfies the
  "Authorized Properties" setup gate)
- ``products`` + ``pricing_options``: CPM display products for demo and storyboard runs
- ``tenant_signing_credentials``: local webhook-signing key for SDK receiver storyboards

The result is a single-tenant stack that ``./scripts/storyboard-check.sh``
can drive end-to-end with ``ALLOW_HTTP=1``.

Idempotent — inserts use ``ON CONFLICT DO NOTHING`` / ``DO UPDATE`` or
``WHERE NOT EXISTS`` guards, so re-running is safe and won't clobber tenant
edits made through the admin UI.

The companion environment variables ``ADCP_TESTING=true`` and
``ADCP_MULTI_TENANT=true`` (set in ``docker-compose.yml`` for dev) clear
the SSO and "real ad-server" setup gates so the mock adapter qualifies
as fully configured.

Usage::

    docker compose exec adcp-server python scripts/ops/seed_demo_data.py
    # or, locally:
    DATABASE_URL=postgresql://... python scripts/ops/seed_demo_data.py
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import sys

from sqlalchemy import text

from src.core.database.database_session import get_db_session

logger = logging.getLogger(__name__)

DEFAULT_TENANT_ID = "default"
TEST_PRINCIPAL_ID = "ci-test-principal"
TEST_PRINCIPAL_TOKEN = "ci-test-token"  # noqa: S105 — fixed dev/CI token


def _seed(session) -> None:
    # 1) Configure the tenant for mock-adapter dev. ``virtual_host`` must be a
    # valid hostname (no port) — the publisher_domain pattern validation
    # rejects ``localhost:8000``.
    session.execute(
        text(
            """
            UPDATE tenants
            SET ad_server = COALESCE(ad_server, 'mock'),
                virtual_host = COALESCE(virtual_host, 'localhost'),
                public_agent_url = COALESCE(public_agent_url, 'http://localhost:8000')
            WHERE tenant_id = :tid
            """
        ),
        {"tid": DEFAULT_TENANT_ID},
    )
    if os.environ.get("SEED_DEMO_AUTO_APPROVE") == "1":
        session.execute(
            text(
                """
                UPDATE tenants
                SET human_review_required = false
                WHERE tenant_id = :tid
                """
            ),
            {"tid": DEFAULT_TENANT_ID},
        )

    # 2) USD currency limit (required gate once ad_server is configured).
    session.execute(
        text(
            """
            INSERT INTO currency_limits (tenant_id, currency_code, min_package_budget, max_daily_package_spend)
            VALUES (:tid, 'USD', 0, 100000)
            ON CONFLICT (tenant_id, currency_code) DO NOTHING
            """
        ),
        {"tid": DEFAULT_TENANT_ID},
    )

    # 3) ``all_inventory`` property tag (referenced by the demo product).
    session.execute(
        text(
            """
            INSERT INTO property_tags (tag_id, tenant_id, name, description)
            VALUES ('all_inventory', :tid, 'All Inventory', 'All publisher inventory')
            ON CONFLICT (tag_id, tenant_id) DO NOTHING
            """
        ),
        {"tid": DEFAULT_TENANT_ID},
    )

    # 4) Test principal with a stable bearer token (matches the value used in
    # ``scripts/storyboard-check.sh`` examples and the integration test suite).
    session.execute(
        text(
            """
            INSERT INTO principals (tenant_id, principal_id, name, platform_mappings, access_token)
            VALUES (:tid, :pid, 'CI Test Principal', CAST(:mappings AS jsonb), :token)
            ON CONFLICT (tenant_id, principal_id) DO UPDATE SET access_token = EXCLUDED.access_token
            """
        ),
        {
            "tid": DEFAULT_TENANT_ID,
            "pid": TEST_PRINCIPAL_ID,
            "mappings": json.dumps({"mock": {"advertiser_id": "test-advertiser"}}),
            "token": TEST_PRINCIPAL_TOKEN,
        },
    )

    # 5) Publisher partner (satisfies the "Authorized Properties" setup gate).
    session.execute(
        text(
            """
            INSERT INTO publisher_partners (
                tenant_id, publisher_domain, display_name, is_verified,
                sync_status, total_properties, authorized_properties
            )
            VALUES (:tid, 'demo.example.com', 'Demo Publisher', true, 'success', 1, 1)
            ON CONFLICT DO NOTHING
            """
        ),
        {"tid": DEFAULT_TENANT_ID},
    )

    # 6) Local webhook-signing key so SDK receiver storyboards can register
    # RFC 9421 webhooks on a fresh Docker stack. Preserve any operator-created
    # active key on reseed.
    existing_signing_key = session.execute(
        text(
            """
            SELECT key_id
            FROM tenant_signing_credentials
            WHERE tenant_id = :tid
              AND purpose = 'webhook-signing'
              AND is_active = true
            LIMIT 1
            """
        ),
        {"tid": DEFAULT_TENANT_ID},
    ).scalar_one_or_none()
    if existing_signing_key is None:
        from adcp.signing.keygen import generate_signing_keypair

        from src.services.webhook_signing import _resolve_signing_keys_dir

        pem_bytes, jwk = generate_signing_keypair(alg="ed25519", purpose="webhook-signing")
        kid = jwk["kid"]
        keys_dir = _resolve_signing_keys_dir()
        keys_dir.mkdir(parents=True, exist_ok=True)
        pem_path = (keys_dir / f"{DEFAULT_TENANT_ID}-{kid}.pem").resolve()
        if not pem_path.is_relative_to(keys_dir.resolve()):
            raise RuntimeError(f"Computed webhook signing key path {pem_path} escapes {keys_dir}")
        fd = os.open(str(pem_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, pem_bytes)
        finally:
            os.close(fd)
        session.execute(
            text(
                """
                INSERT INTO tenant_signing_credentials (
                    tenant_id, purpose, backend, backend_ref, public_jwk, key_id, is_active
                )
                VALUES (
                    :tid, 'webhook-signing', 'local_pem', :backend_ref,
                    CAST(:public_jwk AS jsonb), :key_id, true
                )
                ON CONFLICT DO NOTHING
                """
            ),
            {
                "tid": DEFAULT_TENANT_ID,
                "backend_ref": str(pem_path),
                "public_jwk": json.dumps(jwk),
                "key_id": kid,
            },
        )

    # 7) Products so ``get_products`` returns something non-empty and SDK
    # storyboards that use their generic fixture ID can create media buys.
    seeded_products = [
        {
            "product_id": "demo_display_300x250",
            "name": "Demo Display 300x250",
            "description": "Demo run-of-network display product (300x250)",
            "delivery_type": "guaranteed",
            "rate": 5.00,
        },
        {
            "product_id": "test-product",
            "name": "Storyboard Test Product",
            "description": "Fixture product used by AdCP SDK storyboards",
            "delivery_type": "non_guaranteed",
            "rate": 5.00,
        },
    ]
    product_insert = text(
        """
        INSERT INTO products (
            tenant_id, product_id, name, description, format_ids, targeting_template,
            delivery_type, property_tags, delivery_measurement
        ) VALUES (
            :tid, :product_id, :name, :description,
            CAST(:fmt_ids AS jsonb), CAST('{}' AS jsonb), :delivery_type,
            CAST(:tags AS jsonb), CAST(:dm AS jsonb)
        )
        ON CONFLICT (tenant_id, product_id) DO NOTHING
        """
    )
    pricing_insert = text(
        """
        INSERT INTO pricing_options (tenant_id, product_id, pricing_model, rate, currency, is_fixed)
        SELECT :tid, :product_id, 'cpm', :rate, 'USD', true
        WHERE NOT EXISTS (
            SELECT 1
            FROM pricing_options
            WHERE tenant_id = :tid
              AND product_id = :product_id
              AND pricing_model = 'cpm'
              AND currency = 'USD'
              AND is_fixed = true
        )
        """
    )
    shared_product_fields = {
        "tid": DEFAULT_TENANT_ID,
        "fmt_ids": json.dumps([{"agent_url": "https://creative.adcontextprotocol.org", "id": "display_300x250"}]),
        "tags": json.dumps(["all_inventory"]),
        "dm": json.dumps({"provider": "publisher", "notes": "Demo measurement"}),
    }
    for product in seeded_products:
        session.execute(product_insert, {**shared_product_fields, **product})
        session.execute(
            pricing_insert,
            {
                "tid": DEFAULT_TENANT_ID,
                "product_id": product["product_id"],
                "rate": product["rate"],
            },
        )

    # 8) Tenant management API key (superadmin_config). Without this row the
    # tenant-management API returns 503 on a fresh stack. ON CONFLICT DO NOTHING
    # preserves a key already issued via the admin UI or
    # ``scripts/initialize_tenant_mgmt_api_key.py``.
    session.execute(
        text(
            """
            INSERT INTO superadmin_config (config_key, config_value, description, updated_by)
            VALUES ('api_key', :api_key, 'Tenant management API key for programmatic access', 'seed_demo_data')
            ON CONFLICT (config_key) DO NOTHING
            """
        ),
        {"api_key": f"sk_{secrets.token_urlsafe(32)}"},
    )


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    with get_db_session() as session:
        _seed(session)
        session.commit()  # get_db_session does not auto-commit
    logger.info("Demo data seeded for tenant '%s'.", DEFAULT_TENANT_ID)
    logger.info("Test bearer token: %s", TEST_PRINCIPAL_TOKEN)
    return 0


if __name__ == "__main__":
    sys.exit(main())
