// The admin UI's client for a tenant's publisher partners (/tenant/<id>/publisher-partners),
// shared by every page that lists them: the inventory page's Publishers tab and the settings
// page's Publishers section. Load it after escape_html.js and admin_answer.js.
//
// A page renders the list container <div id="publishersList" data-tenant-id="..."> and calls
// loadPublishers(); the counters #totalPublishers, #verifiedPublishers and #pendingPublishers
// are filled when the page has them. The fields read here are the ones
// src/admin/blueprints/publisher_partners.py returns.
//
// What differs by page is an option, not a copy:
// - data-sync-button="header" on the container: the page has its own sync button, which
//   calls syncAllPublishers(), so the list does not add one under itself;
// - addPublisher(domain, displayName): a page with a form passes both; called with neither,
//   it asks for the domain and names the publisher after it.

function publisherPartnersUrl(path) {
    const tenantId = document.getElementById('publishersList').dataset.tenantId;
    return `${scriptRoot}/tenant/${tenantId}/publisher-partners${path}`;
}

async function loadPublishers() {
    const publishersList = document.getElementById('publishersList');
    if (!publishersList) return;

    try {
        const response = await fetch(publisherPartnersUrl(''), {
            credentials: 'same-origin'
        });

        if (!response.ok) throw new Error(adminErrorText(await response.json()));

        const data = await response.json();

        // Update stats (only if elements exist)
        const totalEl = document.getElementById('totalPublishers');
        const verifiedEl = document.getElementById('verifiedPublishers');
        const pendingEl = document.getElementById('pendingPublishers');

        if (totalEl) totalEl.textContent = data.total || 0;
        if (verifiedEl) verifiedEl.textContent = data.authorizing || 0;
        if (pendingEl) pendingEl.textContent = data.never_fetched || 0;

        renderPublishers(data.partners || []);
    } catch (error) {
        console.error('Error loading publishers:', error);
        publishersList.innerHTML = `
            <div class="alert alert-danger">
                Error loading publishers: ${escapeHtml(error.message)}
            </div>
        `;
    }
}

function renderPublishers(publishers) {
    const container = document.getElementById('publishersList');
    if (!container) return;

    if (publishers.length === 0) {
        container.innerHTML = `
            <div class="text-center text-muted py-4">
                <i class="fas fa-globe fa-3x mb-3"></i>
                <p>No publishers added yet.</p>
                <p class="small">Click "Add Publisher" to start discovering properties.</p>
            </div>
        `;
        return;
    }

    let listHtml = '<div class="list-group">';

    publishers.forEach(pub => {
        // Authorization is read from the stored adagents.json: fresh, with a property in effect.
        const statusIconHtml = pub.authorizes ? '<i class="fas fa-check-circle text-success"></i>' :
            pub.fetch_status === 'never_fetched' ? '<i class="fas fa-clock text-muted"></i>' :
            pub.fetch_status === 'ok' ? '<i class="fas fa-times-circle text-warning"></i>' :
            '<i class="fas fa-exclamation-circle text-danger"></i>';

        const statusText = pub.authorizes ? 'Authorizes this agent' :
            pub.fetch_status === 'never_fetched' ? 'Not fetched yet' :
            pub.fetch_status === 'ok' ? 'Not authorized' : 'Fetch failed';

        listHtml += `
            <div class="list-group-item">
                <div class="d-flex justify-content-between align-items-start">
                    <div class="flex-grow-1">
                        <div class="d-flex align-items-center mb-2">
                            ${statusIconHtml}
                            <h6 class="mb-0 ms-2">${escapeHtml(pub.display_name || pub.publisher_domain)}</h6>
                            <span class="badge bg-${pub.authorizes ? 'success' : 'secondary'} ms-2">${escapeHtml(statusText)}</span>
                        </div>
                        <div class="small text-muted">
                            <div><strong>Domain:</strong> ${escapeHtml(pub.publisher_domain)}</div>
                            ${pub.property_count > 0 ? `<div><strong>Properties:</strong> ${escapeHtml(pub.property_count)} authorized</div>` : ''}
                            ${pub.format_option_count > 0 ? `<div><strong>Format options:</strong> ${escapeHtml(pub.format_option_count)}</div>` : ''}
                            ${pub.fetched_at ? `<div><strong>Last fetched:</strong> ${escapeHtml(new Date(pub.fetched_at).toLocaleString())}</div>` : ''}
                            ${pub.fetch_status !== 'ok' && pub.fetch_error ? `<div class="text-danger"><strong>Last fetch failed:</strong> ${escapeHtml(pub.fetch_error)}</div>` : ''}
                        </div>
                    </div>
                    <div class="btn-group btn-group-sm">
                        ${pub.authorizes ? `
                            <button class="btn btn-outline-primary" onclick="viewPublisherProperties(${jsArg(pub.publisher_domain)})" title="View Properties">
                                <i class="fas fa-list"></i>
                            </button>
                        ` : ''}
                        <button class="btn btn-outline-danger" onclick="deletePublisher(${jsArg(pub.publisher_domain)})" title="Delete">
                            <i class="fas fa-trash"></i>
                        </button>
                    </div>
                </div>
            </div>
        `;
    });

    listHtml += '</div>';

    if (container.dataset.syncButton !== 'header') {
        listHtml += `
            <div class="mt-3 text-center">
                <button class="btn btn-primary" onclick="syncAllPublishers()">
                    <i class="fas fa-sync"></i> Sync All Publishers
                </button>
            </div>
        `;
    }

    container.innerHTML = listHtml;
}

// Whether the publisher was added.
async function addPublisher(domain = prompt('Enter publisher domain (e.g., nytimes.com):'), displayName = '') {
    if (!domain || !domain.trim()) return false;
    const publisherDomain = domain.trim();

    try {
        const response = await fetch(publisherPartnersUrl(''), {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            credentials: 'same-origin',
            body: JSON.stringify({
                publisher_domain: publisherDomain,
                display_name: displayName.trim() || publisherDomain
            })
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(adminErrorText(error));
        }

        alert('Publisher added successfully');
        loadPublishers();
        return true;
    } catch (error) {
        alert('Error adding publisher: ' + error.message);
        return false;
    }
}

async function deletePublisher(domain) {
    if (!confirm(`Delete publisher ${domain}?`)) return;

    try {
        const response = await fetch(publisherPartnersUrl('/' + encodeURIComponent(domain)), {
            method: 'DELETE',
            credentials: 'same-origin'
        });

        if (!response.ok) throw new Error(adminErrorText(await response.json()));

        loadPublishers();
    } catch (error) {
        alert('Error deleting publisher: ' + error.message);
    }
}

async function syncAllPublishers() {
    const btn = event.target.closest('button');
    const originalHtml = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Syncing...';

    try {
        const response = await fetch(publisherPartnersUrl('/sync'), {
            method: 'POST',
            credentials: 'same-origin'
        });

        if (!response.ok) throw new Error(adminErrorText(await response.json()));

        const data = await response.json();
        alert(`Sync completed!\nFetched: ${data.ok}\nFailed: ${data.errors}`);
        loadPublishers();
    } catch (error) {
        alert('Error syncing publishers: ' + error.message);
    } finally {
        btn.disabled = false;
        btn.innerHTML = originalHtml;
    }
}

async function viewPublisherProperties(domain) {
    const modal = document.createElement('div');
    modal.className = 'modal fade';
    modal.id = 'publisherPropertiesModal';
    modal.innerHTML = `
        <div class="modal-dialog modal-lg">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title">Properties for ${escapeHtml(domain)}</h5>
                    <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                </div>
                <div class="modal-body" id="propertiesContent">
                    <div class="text-center"><i class="fas fa-spinner fa-spin fa-2x"></i></div>
                </div>
                <div class="modal-footer">
                    <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Close</button>
                </div>
            </div>
        </div>
    `;

    document.body.appendChild(modal);
    const bsModal = new bootstrap.Modal(modal);
    bsModal.show();

    modal.addEventListener('hidden.bs.modal', () => modal.remove());

    try {
        const url = publisherPartnersUrl('/' + encodeURIComponent(domain) + '/properties');
        const response = await fetch(url, { credentials: 'same-origin' });

        if (!response.ok) throw new Error(adminErrorText(await response.json()));

        const data = await response.json();

        let propertiesHtml = '';

        if (data.error) {
            propertiesHtml = `<div class="alert alert-danger">${escapeHtml(adminErrorText(data))}</div>`;
        } else if (!data.is_authorized) {
            propertiesHtml = `<div class="alert alert-warning">Not authorized for this publisher.</div>`;
        } else {
            propertiesHtml = `
                <div class="alert alert-success">
                    <i class="fas fa-check-circle"></i> Authorized for this publisher
                </div>

                <h6>Property IDs (${escapeHtml(data.property_ids.length)})</h6>
                <ul class="list-unstyled">
                    ${data.property_ids.map(id => `<li><code>${escapeHtml(id)}</code></li>`).join('')}
                </ul>

                <h6 class="mt-3">Property Tags (${escapeHtml(data.property_tags.length)})</h6>
                <div class="d-flex flex-wrap gap-2">
                    ${data.property_tags.map(tag => `<span class="badge bg-primary">${escapeHtml(tag)}</span>`).join('')}
                </div>
            `;
        }

        document.getElementById('propertiesContent').innerHTML = propertiesHtml;
    } catch (error) {
        document.getElementById('propertiesContent').innerHTML = `
            <div class="alert alert-danger">Error: ${escapeHtml(error.message)}</div>
        `;
    }
}
