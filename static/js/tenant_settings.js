/**
 * Tenant Settings Page JavaScript
 *
 * Configuration is passed via data attributes on #settings-config element:
 * - data-script-name: Flask script_name for URL routing
 * - data-tenant-id: Current tenant ID
 * - data-active-adapter: Active adapter name
 * - data-a2a-port: A2A server port
 * - data-is-production: Production environment flag
 * - data-virtual-host: Virtual host for production
 * - data-subdomain: Subdomain for production
 */

// Get configuration from data attributes
const config = (function() {
    const configEl = document.getElementById('settings-config');
    if (!configEl) {
        console.error('Settings config element not found');
        return {};
    }

    return {
        scriptName: configEl.dataset.scriptName || '',
        tenantId: configEl.dataset.tenantId || '',
        activeAdapter: configEl.dataset.activeAdapter || '',
        a2aPort: configEl.dataset.a2aPort || '8091',
        isProduction: configEl.dataset.isProduction === 'true',
        virtualHost: configEl.dataset.virtualHost || '',
        subdomain: configEl.dataset.subdomain || ''
    };
})();

// Navigation
document.querySelectorAll('.settings-nav-item').forEach(item => {
    item.addEventListener('click', function(e) {
        // If this is a real link (has href attribute and no data-section), let it navigate
        const sectionId = this.dataset.section;
        if (!sectionId) {
            return; // Let the browser handle the navigation
        }

        e.preventDefault();

        // Update active nav
        document.querySelectorAll('.settings-nav-item').forEach(i => i.classList.remove('active'));
        this.classList.add('active');

        // Show corresponding section
        document.querySelectorAll('.settings-section').forEach(s => s.classList.remove('active'));
        const section = document.getElementById(sectionId);
        if (section) {
            section.classList.add('active');
        }

        // Update URL without reload
        history.pushState(null, '', `#${sectionId}`);
    });
});

// Load section from URL hash
window.addEventListener('load', function() {
    const hash = window.location.hash.substring(1);
    if (hash) {
        const navItem = document.querySelector(`[data-section="${hash}"]`);
        if (navItem) {
            navItem.click();
        }
    }
});

// Helper function to switch to a specific section
function switchSettingsSection(sectionId) {
    const navItem = document.querySelector(`[data-section="${sectionId}"]`);
    if (navItem) {
        navItem.click();
    }
}

// Copy to clipboard
function copyToClipboard(buttonOrText) {
    let textToCopy;
    let buttonElement;

    if (typeof buttonOrText === 'string') {
        // Direct text passed
        textToCopy = buttonOrText;
        buttonElement = event.target; // Get the button that was clicked
    } else {
        // Button element passed (existing behavior)
        textToCopy = buttonOrText.parentElement.querySelector('pre').textContent;
        buttonElement = buttonOrText;
    }

    navigator.clipboard.writeText(textToCopy).then(() => {
        const originalText = buttonElement.textContent;
        buttonElement.textContent = 'Copied!';
        setTimeout(() => {
            buttonElement.textContent = originalText;
        }, 2000);
    });
}

// Format JSON
function formatJSON() {
    const textarea = document.getElementById('raw_config');
    try {
        const json = JSON.parse(textarea.value);
        textarea.value = JSON.stringify(json, null, 2);
    } catch (e) {
        alert('Invalid JSON: ' + e.message);
    }
}

// Test Slack
function testSlack() {
    const webhookUrl = document.getElementById('slack_webhook_url').value;
    if (!webhookUrl) {
        alert('Please enter a webhook URL first');
        return;
    }

    fetch(`${config.scriptName}/tenant/${config.tenantId}/test_slack`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            webhook_url: webhookUrl
        })
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            alert('✅ Test notification sent successfully!');
        } else {
            alert('❌ Test failed: ' + (data.error || data.message || 'Unknown error'));
        }
    })
    .catch(error => {
        alert('❌ Error: ' + error.message);
    });
}

// Save adapter settings
function saveAdapter() {
    const adapterType = document.querySelector('select[name="adapter_type"]').value;

    fetch(`${config.scriptName}/tenant/${config.tenantId}/settings/adapter`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/x-www-form-urlencoded',
        },
        body: new URLSearchParams({
            adapter_type: adapterType
        })
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            alert('Adapter settings saved successfully!');
            location.reload();
        } else {
            alert('Error: ' + (data.error || data.message || 'Unknown error'));
        }
    })
    .catch(error => {
        alert('Error: ' + error.message);
    });
}

// Check OAuth status (for GAM)
function checkOAuthStatus() {
    fetch(`${config.scriptName}/api/oauth/status`)
        .then(response => response.json())
        .then(data => {
            const statusBadge = document.getElementById('oauth-status-badge');
            const statusText = document.getElementById('oauth-status-text');

            if (data.authenticated) {
                statusBadge.textContent = 'Connected';
                statusBadge.className = 'badge badge-success';
                statusText.textContent = `Authenticated as ${data.user_email}`;
            } else {
                statusBadge.textContent = 'Not Connected';
                statusBadge.className = 'badge badge-danger';
                statusText.textContent = 'Not authenticated';
            }
        })
        .catch(error => {
            console.error('Error checking OAuth status:', error);
        });
}

// Initiate GAM OAuth
function initiateGAMAuth() {
    const tenantId = config.tenantId;
    const oauthUrl = `${config.scriptName}/auth/gam/authorize/${tenantId}`;

    // Open OAuth flow in popup
    const width = 600;
    const height = 700;
    const left = (screen.width - width) / 2;
    const top = (screen.height - height) / 2;

    const popup = window.open(
        oauthUrl,
        'GAM OAuth',
        `width=${width},height=${height},left=${left},top=${top}`
    );

    // Poll for completion
    const pollTimer = setInterval(() => {
        if (popup.closed) {
            clearInterval(pollTimer);
            checkOAuthStatus();
        }
    }, 1000);
}

// Detect GAM network code
function detectGAMNetwork() {
    const button = document.querySelector('button[onclick="detectGAMNetwork()"]');
    const originalText = button.textContent;
    button.disabled = true;
    button.textContent = 'Detecting...';

    fetch(`${config.scriptName}/tenant/${config.tenantId}/gam/detect-network`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        }
    })
    .then(response => response.json())
    .then(data => {
        button.disabled = false;
        button.textContent = originalText;

        if (data.success) {
            document.getElementById('gam_network_code').value = data.network_code;
            alert(`✅ Network code detected: ${data.network_code}`);
        } else {
            alert('❌ ' + (data.error || data.message || 'Unknown error'));
        }
    })
    .catch(error => {
        button.disabled = false;
        button.textContent = originalText;
        alert('❌ Error: ' + error.message);
    });
}

// Save business rules
function saveBusinessRules() {
    const form = document.getElementById('business-rules-form');
    const formData = new FormData(form);

    fetch(`${config.scriptName}/tenant/${config.tenantId}/settings/business-rules`, {
        method: 'POST',
        body: formData
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            alert('Business rules saved successfully!');
        } else {
            alert('Error: ' + (data.error || data.message || 'Unknown error'));
        }
    })
    .catch(error => {
        alert('Error: ' + error.message);
    });
}

// Configure GAM
function configureGAM() {
    const form = document.getElementById('gam-config-form');
    const formData = new FormData(form);

    fetch(`${config.scriptName}/tenant/${config.tenantId}/gam/configure`, {
        method: 'POST',
        body: formData
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            alert('✅ GAM configuration saved successfully!');
            location.reload();
        } else {
            alert('❌ Error: ' + (data.error || data.message || 'Unknown error'));
        }
    })
    .catch(error => {
        alert('❌ Error: ' + error.message);
    });
}

// Test GAM connection
function testGAMConnection() {
    const button = document.querySelector('button[onclick="testGAMConnection()"]');
    const originalText = button.textContent;
    button.disabled = true;
    button.textContent = 'Testing...';

    fetch(`${config.scriptName}/api/gam/test-connection`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            network_code: document.getElementById('gam_network_code').value
        })
    })
    .then(response => response.json())
    .then(data => {
        button.disabled = false;
        button.textContent = originalText;

        if (data.success) {
            alert('✅ Connection successful!');
        } else {
            alert('❌ Connection failed: ' + (data.error || data.message || 'Unknown error'));
        }
    })
    .catch(error => {
        button.disabled = false;
        button.textContent = originalText;
        alert('❌ Error: ' + error.message);
    });
}

// Sync GAM inventory
function syncGAMInventory() {
    const button = document.querySelector('button[onclick="syncGAMInventory()"]');
    const originalText = button.textContent;
    button.disabled = true;
    button.textContent = 'Syncing...';

    fetch(`${config.scriptName}/tenant/${config.tenantId}/gam/sync-inventory`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        }
    })
    .then(response => response.json())
    .then(data => {
        button.disabled = false;
        button.textContent = originalText;

        if (data.success) {
            alert(`✅ Synced ${data.count} ad units successfully!`);
            location.reload();
        } else {
            alert('❌ Sync failed: ' + (data.error || data.message || 'Unknown error'));
        }
    })
    .catch(error => {
        button.disabled = false;
        button.textContent = originalText;
        alert('❌ Error: ' + error.message);
    });
}

// Check OAuth token status
function checkTokenStatus() {
    fetch(`${config.scriptName}/api/oauth/status`)
        .then(response => response.json())
        .then(data => {
            const statusDiv = document.getElementById('token-status');
            if (data.authenticated) {
                statusDiv.innerHTML = `
                    <div class="alert alert-success">
                        ✅ Authenticated as ${data.user_email}
                        <button onclick="revokeToken()" class="btn btn-sm btn-danger ml-2">Revoke</button>
                    </div>
                `;
            } else {
                statusDiv.innerHTML = `
                    <div class="alert alert-warning">
                        ⚠️ Not authenticated
                    </div>
                `;
            }
        });
}

// Generate A2A registration code
function generateA2ACode() {
    const agentUri = config.isProduction
        ? `https://${config.virtualHost}`
        : `http://localhost:${config.a2aPort}`;

    const agentUriAlt = config.isProduction
        ? `https://${config.subdomain}.sales-agent.scope3.com`
        : `http://localhost:${config.a2aPort}`;

    const code = `
# A2A Registration Code
# Paste this into your AI agent's configuration

{
  "agent_uri": "${agentUri}",
  "protocol": "a2a",
  "version": "1.0"
}
    `.trim();

    document.getElementById('a2a-code-output').textContent = code;
}

// Delete principal
function deletePrincipal(principalId) {
    if (!confirm('Are you sure you want to delete this principal? This action cannot be undone.')) {
        return;
    }

    fetch(`${config.scriptName}/tenant/${config.tenantId}/principals/${principalId}/delete`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        }
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            alert('Principal deleted successfully');
            location.reload();
        } else {
            alert('Error: ' + (data.error || data.message || 'Unknown error'));
        }
    })
    .catch(error => {
        alert('Error: ' + error.message);
    });
}

// Test signals endpoint
function testSignalsEndpoint() {
    const url = document.getElementById('signals_discovery_agent_uri').value;
    if (!url) {
        alert('Please enter a signals discovery agent URL first');
        return;
    }

    fetch(`${config.scriptName}/tenant/${config.tenantId}/settings/test_signals`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            url: url
        })
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            alert('✅ Connection successful! Found ' + data.signal_count + ' signals');
        } else {
            alert('❌ Connection failed: ' + (data.error || data.message || 'Unknown error'));
        }
    })
    .catch(error => {
        alert('❌ Error: ' + error.message);
    });
}

// Debug log for adapter detection
console.log('Backend says active_adapter is:', config.activeAdapter);
console.log('Backend says tenant.ad_server is:', document.querySelector('[data-tenant-ad-server]')?.dataset.tenantAdServer);

// Update principal
function updatePrincipal(principalId) {
    const name = document.getElementById(`principal_name_${principalId}`).value;
    const advertiserIds = document.getElementById(`advertiser_ids_${principalId}`).value;

    fetch(`${config.scriptName}/tenant/${config.tenantId}/principal/${principalId}`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            name: name,
            advertiser_ids: advertiserIds
        })
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            alert('Principal updated successfully');
        } else {
            alert('Error: ' + (data.error || data.message || 'Unknown error'));
        }
    })
    .catch(error => {
        alert('Error: ' + error.message);
    });
}

// Fetch GAM advertisers for principal mapping
function fetchGAMAdvertisers() {
    const activeAdapter = config.activeAdapter;

    if (activeAdapter !== 'google_ad_manager') {
        alert('GAM advertiser sync is only available when Google Ad Manager adapter is active');
        return;
    }

    const button = event.target;
    const originalText = button.textContent;
    button.disabled = true;
    button.textContent = 'Fetching...';

    fetch(`${config.scriptName}/tenant/${config.tenantId}/api/gam/get-advertisers`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        }
    })
    .then(response => response.json())
    .then(data => {
        button.disabled = false;
        button.textContent = originalText;

        if (data.success) {
            displayGAMAdvertisers(data.advertisers);
        } else {
            alert('❌ Failed to fetch advertisers: ' + (data.error || data.message || 'Unknown error'));
        }
    })
    .catch(error => {
        button.disabled = false;
        button.textContent = originalText;
        alert('❌ Error: ' + error.message);
    });
}

// Display GAM advertisers in a modal or section
function displayGAMAdvertisers(advertisers) {
    const container = document.getElementById('gam-advertisers-list');
    if (!container) {
        alert(`Found ${advertisers.length} advertisers. Check the console for details.`);
        console.table(advertisers);
        return;
    }

    container.innerHTML = '<h4>Available GAM Advertisers</h4>';
    const list = document.createElement('ul');
    list.className = 'list-group';

    advertisers.forEach(adv => {
        const item = document.createElement('li');
        item.className = 'list-group-item';
        item.innerHTML = `
            <strong>${adv.name}</strong>
            <br>
            <small class="text-muted">ID: ${adv.id}</small>
            <button class="btn btn-sm btn-primary float-right" onclick="selectAdvertiser('${adv.id}', '${adv.name}')">
                Select
            </button>
        `;
        list.appendChild(item);
    });

    container.appendChild(list);
    container.style.display = 'block';
}

// Select advertiser and populate form
function selectAdvertiser(advertiserId, advertiserName) {
    // Find the active principal form and populate it
    const activeForm = document.querySelector('.principal-form.active');
    if (activeForm) {
        const idField = activeForm.querySelector('[id^="advertiser_ids_"]');
        if (idField) {
            idField.value = advertiserId;
            alert(`Selected: ${advertiserName} (ID: ${advertiserId})`);
        }
    }
}

// Update approval mode UI (show/hide descriptions and AI config)
function updateApprovalModeUI() {
    const approvalMode = document.getElementById('approval_mode').value;

    // Hide all descriptions
    document.getElementById('desc-auto-approve').style.display = 'none';
    document.getElementById('desc-require-human').style.display = 'none';
    document.getElementById('desc-ai-powered').style.display = 'none';

    // Show selected description
    document.getElementById(`desc-${approvalMode}`).style.display = 'block';

    // Show/hide AI configuration section
    const aiConfigSection = document.getElementById('ai-config-section');
    if (aiConfigSection) {
        aiConfigSection.style.display = (approvalMode === 'ai-powered') ? 'block' : 'none';
    }
}

// Update advertising policy UI (show/hide config when checkbox toggled)
function updateAdvertisingPolicyUI() {
    const policyCheckEnabled = document.getElementById('policy_check_enabled');
    const policyConfigSection = document.getElementById('advertising-policy-config');

    if (policyCheckEnabled && policyConfigSection) {
        policyConfigSection.style.display = policyCheckEnabled.checked ? 'block' : 'none';
    }
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', function() {
    // Check OAuth status if GAM adapter is active
    if (config.activeAdapter === 'google_ad_manager') {
        checkOAuthStatus();
    }

    // Generate A2A code if section exists
    if (document.getElementById('a2a-code-output')) {
        generateA2ACode();
    }

    // Initialize approval mode UI
    if (document.getElementById('approval_mode')) {
        updateApprovalModeUI();
    }

    // Initialize advertising policy UI
    if (document.getElementById('policy_check_enabled')) {
        updateAdvertisingPolicyUI();
        // Add event listener for checkbox toggle
        document.getElementById('policy_check_enabled').addEventListener('change', updateAdvertisingPolicyUI);
    }
});
