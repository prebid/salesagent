"""Regression tests for product form action controls."""

import json
import subprocess
import textwrap
from pathlib import Path

import pytest

from src.admin.app import create_app
from tests.factories import (
    AuthorizedPropertyFactory,
    GAMInventoryFactory,
    PricingOptionFactory,
    PrincipalFactory,
    ProductFactory,
    PropertyTagFactory,
    TenantAuthConfigFactory,
    TenantFactory,
)

pytestmark = [pytest.mark.admin, pytest.mark.requires_db]


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["SESSION_COOKIE_PATH"] = "/"
    with app.test_client() as client:
        yield client


def _auth_session(client, tenant_id: str) -> None:
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess["user"] = {"email": "test@example.com", "is_super_admin": True}
        sess["email"] = "test@example.com"
        sess["tenant_id"] = tenant_id
        sess["test_user"] = "test@example.com"
        sess["test_user_role"] = "super_admin"
        sess["test_user_name"] = "Test User"
        sess["test_tenant_id"] = tenant_id


def test_gam_create_product_create_new_profile_links_to_existing_route(client, factory_session):
    """The Create New inventory-profile action should navigate to the existing creator."""
    tenant = TenantFactory(ad_server="google_ad_manager")
    PropertyTagFactory(tenant=tenant, tenant_id=tenant.tenant_id, tag_id="all_inventory", name="All Inventory")
    AuthorizedPropertyFactory(tenant=tenant, tenant_id=tenant.tenant_id)
    PrincipalFactory(tenant=tenant, tenant_id=tenant.tenant_id)
    TenantAuthConfigFactory(tenant=tenant, tenant_id=tenant.tenant_id, oidc_enabled=True)
    GAMInventoryFactory(tenant=tenant, tenant_id=tenant.tenant_id)
    factory_session.commit()

    _auth_session(client, tenant.tenant_id)

    response = client.get(f"/tenant/{tenant.tenant_id}/products/add")

    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_data(as_text=True)
    assert f'href="/tenant/{tenant.tenant_id}/inventory-profiles/add"' in body
    assert 'target="_blank"' in body
    assert 'rel="noopener"' in body
    assert 'onclick="openInventoryProfileCreator()"' not in body


def test_gam_edit_product_create_new_profile_links_to_existing_route(client, factory_session):
    """The shared GAM product template should expose the profile creator when editing."""
    tenant = TenantFactory(ad_server="google_ad_manager")
    PropertyTagFactory(tenant=tenant, tenant_id=tenant.tenant_id, tag_id="all_inventory", name="All Inventory")
    AuthorizedPropertyFactory(tenant=tenant, tenant_id=tenant.tenant_id)
    PrincipalFactory(tenant=tenant, tenant_id=tenant.tenant_id)
    TenantAuthConfigFactory(tenant=tenant, tenant_id=tenant.tenant_id, oidc_enabled=True)
    GAMInventoryFactory(tenant=tenant, tenant_id=tenant.tenant_id)
    product = ProductFactory(tenant=tenant, tenant_id=tenant.tenant_id)
    PricingOptionFactory(product=product, tenant_id=tenant.tenant_id, product_id=product.product_id)
    factory_session.commit()

    _auth_session(client, tenant.tenant_id)

    response = client.get(f"/tenant/{tenant.tenant_id}/products/{product.product_id}/edit")

    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_data(as_text=True)
    assert f'href="/tenant/{tenant.tenant_id}/inventory-profiles/add"' in body
    assert 'target="_blank"' in body
    assert 'rel="noopener"' in body
    assert 'onclick="openInventoryProfileCreator()"' not in body


def test_publishers_copy_button_preserves_default_label_across_repeated_clicks():
    """The copy feedback timer should not restore the transient Copied label."""
    script_path = Path(__file__).resolve().parents[2] / "static/js/publishers.js"
    node_script = textwrap.dedent(
        f"""
        const assert = require('assert');
        const fs = require('fs');
        const vm = require('vm');

        const source = fs.readFileSync({json.dumps(str(script_path))}, 'utf8');
        const button = {{ textContent: 'Copy', dataset: {{}} }};
        const publicUrl = {{ textContent: ' https://example.test/agent ' }};
        const timers = [];
        const clipboardWrites = [];
        let nextTimerId = 1;

        const context = {{
            console: {{ error() {{}} }},
            document: {{
                getElementById(id) {{
                    if (id === 'settings-config') {{
                        return {{ dataset: {{ scriptName: '', tenantId: 'tenant_1', isEmbedded: 'false' }} }};
                    }}
                    if (id === 'public-agent-url-display') {{
                        return publicUrl;
                    }}
                    return null;
                }},
                createElement() {{
                    return {{
                        textContent: '',
                        innerHTML: '',
                    }};
                }},
                createRange() {{
                    return {{ selectNode() {{}} }};
                }},
                addEventListener() {{}},
            }},
            window: {{
                getSelection() {{
                    return {{
                        removeAllRanges() {{}},
                        addRange() {{}},
                    }};
                }},
            }},
            navigator: {{
                clipboard: {{
                    writeText(text) {{
                        clipboardWrites.push(text);
                        return Promise.resolve();
                    }},
                }},
            }},
            setTimeout(callback, delay) {{
                const id = nextTimerId++;
                timers.push({{ id, callback, delay, cleared: false }});
                return id;
            }},
            clearTimeout(id) {{
                const timer = timers.find((entry) => entry.id === id);
                if (timer) {{
                    timer.cleared = true;
                }}
            }},
        }};
        context.globalThis = context;

        vm.createContext(context);
        vm.runInContext(source, context);

        async function flushPromises() {{
            await Promise.resolve();
            await Promise.resolve();
        }}

        function runTimer(timer) {{
            if (!timer.cleared) {{
                timer.callback();
            }}
        }}

        (async () => {{
            context.copyAgentUrlToClipboard(button);
            await flushPromises();

            assert.strictEqual(button.textContent, 'Copied!');
            assert.strictEqual(button.dataset.defaultLabel, 'Copy');
            assert.strictEqual(timers.length, 1);
            const firstTimer = timers[0];

            context.copyAgentUrlToClipboard(button);
            await flushPromises();

            assert.strictEqual(button.textContent, 'Copied!');
            assert.strictEqual(button.dataset.defaultLabel, 'Copy');
            assert.strictEqual(timers.length, 2);
            assert.strictEqual(firstTimer.cleared, true);
            const secondTimer = timers[1];

            runTimer(firstTimer);
            assert.strictEqual(button.textContent, 'Copied!');

            runTimer(secondTimer);
            assert.strictEqual(button.textContent, 'Copy');
            assert.deepStrictEqual(clipboardWrites, [
                'https://example.test/agent',
                'https://example.test/agent',
            ]);
        }})().catch((error) => {{
            console.error(error.stack || error);
            process.exit(1);
        }});
        """
    )

    subprocess.run(["node", "-e", node_script], check=True)
