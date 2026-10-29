/**
 * Targeting Widget - GAM-style nested groups targeting selector
 *
 * Features:
 * - Multiple groups connected by OR logic
 * - Within each group, criteria connected by AND logic
 * - Each criterion: key selection, values (OR'd together), optional exclude
 * - Backward compatible with legacy and enhanced formats
 *
 * Data Structure (groups format):
 * {
 *   key_value_pairs: {
 *     groups: [
 *       {
 *         criteria: [
 *           { keyId: '123', values: ['v1', 'v2'] },
 *           { keyId: '456', values: ['v3'], exclude: true }
 *         ]
 *       }
 *     ]
 *   }
 * }
 */

class TargetingWidget {
    constructor(tenantId, containerId = 'targeting-widget', scriptRoot = '') {
        this.tenantId = tenantId;
        this.container = document.getElementById(containerId);
        this.scriptRoot = scriptRoot;
        this.selectedTargeting = {
            key_value_pairs: {
                groups: []
            }
        };
        this.keyMetadata = {};        // { keyId: { name, display_name } }
        this.valueMetadata = {};      // { keyId: { valueId: displayName } }
        this.editingCriterion = null; // { groupIndex, criterionIndex }
        this.loadedSuccessfully = false; // Track if existing targeting loaded OK

        if (!this.container) {
            console.error(`Targeting widget container '#${containerId}' not found`);
            return;
        }

        this.init();
    }

    async init() {
        try {
            await this.loadTargetingData();
            this.loadExistingTargeting();
            await this.loadValueMetadataForExistingCriteria();
            this.render();
            this.attachEventListeners();
            this.updateHiddenField();
        } catch (error) {
            console.error('Error initializing targeting widget:', error);
            this.container.innerHTML = `<div class="alert alert-error">Failed to load targeting options: ${error.message}</div>`;
        }
    }

    /**
     * Load existing targeting from hidden form field.
     * Handles legacy, enhanced, and groups formats.
     */
    loadExistingTargeting() {
        const hiddenField = document.getElementById('targeting-data');
        if (!hiddenField || !hiddenField.value || hiddenField.value === '{}') {
            // No existing data - this is OK for new products
            this.loadedSuccessfully = true;
            return;
        }

        try {
            const existingData = JSON.parse(hiddenField.value);
            const kvPairs = existingData.key_value_pairs;

            if (!kvPairs || Object.keys(kvPairs).length === 0) {
                // Empty key_value_pairs - this is OK
                this.loadedSuccessfully = true;
                return;
            }

            // Groups format
            if ('groups' in kvPairs && Array.isArray(kvPairs.groups)) {
                this.selectedTargeting.key_value_pairs.groups = kvPairs.groups;
                this.loadedSuccessfully = true;
                console.log('[TargetingWidget] Loaded groups format:', this.selectedTargeting);
                return;
            }

            // Enhanced format (include/exclude) - convert to groups
            if ('include' in kvPairs || 'exclude' in kvPairs) {
                const criteria = [];
                const include = kvPairs.include || {};
                const exclude = kvPairs.exclude || {};

                for (const [keyId, values] of Object.entries(include)) {
                    if (values && values.length > 0) {
                        criteria.push({ keyId, values: [...values] });
                    }
                }
                for (const [keyId, values] of Object.entries(exclude)) {
                    if (values && values.length > 0) {
                        criteria.push({ keyId, values: [...values], exclude: true });
                    }
                }

                if (criteria.length > 0) {
                    this.selectedTargeting.key_value_pairs.groups = [{ criteria }];
                }
                this.loadedSuccessfully = true;
                console.log('[TargetingWidget] Converted enhanced format to groups:', this.selectedTargeting);
                return;
            }

            // Legacy format { keyId: value } - convert to groups
            const criteria = [];
            for (const [keyId, value] of Object.entries(kvPairs)) {
                if (typeof value === 'string') {
                    criteria.push({ keyId, values: [value] });
                } else if (Array.isArray(value)) {
                    criteria.push({ keyId, values: value });
                }
            }
            if (criteria.length > 0) {
                this.selectedTargeting.key_value_pairs.groups = [{ criteria }];
            }
            this.loadedSuccessfully = true;
            console.log('[TargetingWidget] Converted legacy format to groups:', this.selectedTargeting);

        } catch (error) {
            console.error('[TargetingWidget] Error loading existing targeting:', error);
            // Don't set loadedSuccessfully - hidden field will be preserved
        }
    }

    async loadTargetingData() {
        const url = `${this.scriptRoot}/api/tenant/${this.tenantId}/targeting/all`;
        const response = await fetch(url, { credentials: 'same-origin' });
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        this.targetingData = await response.json();

        if (this.targetingData.customKeys && !this.targetingData.custom_targeting_keys) {
            this.targetingData.custom_targeting_keys = this.targetingData.customKeys;
        }

        (this.targetingData.custom_targeting_keys || []).forEach(key => {
            const metadata = {
                name: key.name,
                display_name: key.display_name || key.name
            };
            // Index by GAM ID (for new data)
            this.keyMetadata[key.id] = metadata;
            // Also index by key name (for legacy data migration)
            if (key.name && key.name !== key.id) {
                this.keyMetadata[key.name] = metadata;
            }
        });
    }

    /**
     * Fetch a key's current values from GAM and record their display names.
     * Never cached: values can be added in GAM while this page is open (#1045).
     * Throws on a failed request; each caller decides how to surface it.
     */
    async fetchValuesForKey(keyId) {
        const url = `${this.scriptRoot}/api/tenant/${this.tenantId}/targeting/values/${keyId}`;
        const response = await fetch(url, { credentials: 'same-origin', cache: 'no-store' });
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        const values = (await response.json()).values || [];

        const labels = (this.valueMetadata[keyId] ??= {});
        values.forEach(val => {
            labels[val.id] = val.display_name || val.name || val.id;
        });
        return values;
    }

    /**
     * Load value metadata (display names) for all keys used in existing targeting criteria.
     * This ensures values are displayed with human-readable names instead of IDs.
     */
    async loadValueMetadataForExistingCriteria() {
        const groups = this.selectedTargeting.key_value_pairs.groups;
        if (!groups || groups.length === 0) {
            return;
        }

        // Collect unique key IDs from all criteria
        const keyIds = new Set();
        groups.forEach(group => {
            (group.criteria || []).forEach(criterion => {
                if (criterion.keyId) {
                    keyIds.add(criterion.keyId);
                }
            });
        });

        if (keyIds.size === 0) {
            return;
        }

        // Load values for each key in parallel
        const loadPromises = Array.from(keyIds).map(async (keyId) => {
            try {
                await this.fetchValuesForKey(keyId);
            } catch (error) {
                console.warn(`[TargetingWidget] Error loading values for key ${keyId}:`, error);
            }
        });

        await Promise.all(loadPromises);
        console.log('[TargetingWidget] Loaded value metadata for existing criteria:', this.valueMetadata);
    }

    render() {
        const keys = this.targetingData.custom_targeting_keys || [];

        if (keys.length === 0) {
            this.container.innerHTML = '<p class="empty-state">No custom targeting keys available. Sync inventory to load targeting options.</p>';
            return;
        }

        this.container.innerHTML = `
            <div class="groups-container" id="groups-container">
                ${this.renderGroups()}
            </div>
            <button type="button" class="add-group-btn" id="add-group-btn">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <line x1="12" y1="5" x2="12" y2="19"></line>
                    <line x1="5" y1="12" x2="19" y2="12"></line>
                </svg>
                Or
            </button>
            ${this.renderValueSelector()}
        `;
    }

    renderGroups() {
        const groups = this.selectedTargeting.key_value_pairs.groups;

        if (groups.length === 0) {
            return `
                <div class="group-card" data-group-index="0">
                    <div class="group-header">
                        <span class="group-label">Custom Targeting</span>
                    </div>
                    <div class="criteria-list" data-group-index="0">
                        <div class="add-criterion-row">
                            <button type="button" class="add-criterion-btn" data-group-index="0">
                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                    <line x1="12" y1="5" x2="12" y2="19"></line>
                                    <line x1="5" y1="12" x2="19" y2="12"></line>
                                </svg>
                                Add targeting
                            </button>
                        </div>
                    </div>
                </div>
            `;
        }

        return groups.map((group, groupIndex) => `
            <div class="group-card" data-group-index="${groupIndex}">
                ${groupIndex > 0 ? '<div class="or-divider"><span>Or</span></div>' : ''}
                <div class="group-header">
                    <span class="group-label">Group ${groupIndex + 1}</span>
                    ${groups.length > 1 ? `
                        <button type="button" class="remove-group-btn" data-group-index="${groupIndex}" title="Remove group">
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <line x1="18" y1="6" x2="6" y2="18"></line>
                                <line x1="6" y1="6" x2="18" y2="18"></line>
                            </svg>
                        </button>
                    ` : ''}
                </div>
                <div class="criteria-list" data-group-index="${groupIndex}">
                    ${this.renderCriteria(group.criteria || [], groupIndex)}
                    <div class="add-criterion-row">
                        <button type="button" class="add-criterion-btn" data-group-index="${groupIndex}">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <line x1="12" y1="5" x2="12" y2="19"></line>
                                <line x1="5" y1="12" x2="19" y2="12"></line>
                            </svg>
                            And
                        </button>
                    </div>
                </div>
            </div>
        `).join('');
    }

    renderCriteria(criteria, groupIndex) {
        return criteria.map((criterion, criterionIndex) => {
            const keyMeta = this.keyMetadata[criterion.keyId] || { display_name: criterion.keyId };
            const valueNames = criterion.values.map(v =>
                this.valueMetadata[criterion.keyId]?.[v] || v
            );
            const isEditing = this.editingCriterion &&
                this.editingCriterion.groupIndex === groupIndex &&
                this.editingCriterion.criterionIndex === criterionIndex;

            return `
                ${criterionIndex > 0 ? '<div class="and-connector"><span>And</span></div>' : ''}
                <div class="criterion-row ${criterion.exclude ? 'excluded' : ''} ${isEditing ? 'editing' : ''}"
                     data-group-index="${groupIndex}"
                     data-criterion-index="${criterionIndex}">
                    <div class="criterion-key" title="${keyMeta.display_name}">
                        ${keyMeta.display_name}
                    </div>
                    <select class="criterion-operator-select"
                            data-group-index="${groupIndex}"
                            data-criterion-index="${criterionIndex}">
                        <option value="is" ${!criterion.exclude ? 'selected' : ''}>is any of</option>
                        <option value="is_not" ${criterion.exclude ? 'selected' : ''}>is none of</option>
                    </select>
                    <div class="criterion-values" data-group-index="${groupIndex}" data-criterion-index="${criterionIndex}">
                        ${valueNames.map((name, valIndex) => `
                            <span class="value-chip ${criterion.exclude ? 'exclude' : 'include'}">
                                ${name}
                                <button type="button" class="chip-remove"
                                        data-group-index="${groupIndex}"
                                        data-criterion-index="${criterionIndex}"
                                        data-value-index="${valIndex}">×</button>
                            </span>
                        `).join('')}
                        <button type="button" class="add-value-btn"
                                data-group-index="${groupIndex}"
                                data-criterion-index="${criterionIndex}"
                                title="Add value">+</button>
                    </div>
                    <div class="criterion-actions">
                        <button type="button" class="remove-criterion-btn"
                                data-group-index="${groupIndex}"
                                data-criterion-index="${criterionIndex}"
                                title="Remove criterion">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <line x1="18" y1="6" x2="6" y2="18"></line>
                                <line x1="6" y1="6" x2="18" y2="18"></line>
                            </svg>
                        </button>
                    </div>
                </div>
            `;
        }).join('');
    }

    renderValueSelector() {
        const keys = this.targetingData.custom_targeting_keys || [];
        // Sort keys alphabetically by display name
        const sortedKeys = [...keys].sort((a, b) => {
            const nameA = (a.display_name || a.name || '').toLowerCase();
            const nameB = (b.display_name || b.name || '').toLowerCase();
            return nameA.localeCompare(nameB);
        });

        return `
            <div class="value-selector-modal" id="value-selector-modal" style="display: none;">
                <div class="value-selector-content">
                    <div class="value-selector-header">
                        <h5 id="value-selector-title">Select Values</h5>
                        <button type="button" class="close-selector-btn" id="close-selector-btn">×</button>
                    </div>
                    <div class="value-selector-body">
                        <div class="key-selector-section">
                            <label>Key:</label>
                            <input type="search" id="key-search" placeholder="Search keys..." class="key-search-input">
                            <div class="key-list" id="key-list">
                                ${sortedKeys.map(k => `
                                    <div class="key-option" data-key-id="${k.id}" data-key-name="${(k.display_name || k.name).toLowerCase()}">
                                        ${k.display_name || k.name}
                                    </div>
                                `).join('')}
                            </div>
                            <input type="hidden" id="key-selector" value="">
                            <div id="selected-key-display" class="selected-key-display" style="display: none;">
                                <span id="selected-key-name"></span>
                                <button type="button" id="change-key-btn" class="change-key-btn">Change</button>
                            </div>
                        </div>
                        <div class="operator-selector-section" id="operator-section" style="display: none; margin-top: 1rem;">
                            <label>Operator:</label>
                            <select id="operator-selector" class="key-dropdown">
                                <option value="is" selected>is any of</option>
                                <option value="is_not">is none of</option>
                            </select>
                        </div>
                        <div class="values-section" id="values-section" style="display: none;">
                            <label>Values:</label>
                            <input type="search" id="value-search" placeholder="Search values..." class="value-search-input">
                            <div class="values-list" id="values-list"></div>
                        </div>
                    </div>
                    <div class="value-selector-footer">
                        <button type="button" class="cancel-btn" id="cancel-selector-btn">Cancel</button>
                        <button type="button" class="apply-btn" id="apply-selector-btn" disabled>Apply</button>
                    </div>
                </div>
            </div>
        `;
    }

    attachEventListeners() {
        // Add group button
        this.container.addEventListener('click', (e) => {
            if (e.target.closest('#add-group-btn')) {
                this.addGroup();
            }
        });

        // Remove group button
        this.container.addEventListener('click', (e) => {
            const btn = e.target.closest('.remove-group-btn');
            if (btn) {
                const groupIndex = parseInt(btn.dataset.groupIndex);
                this.removeGroup(groupIndex);
            }
        });

        // Add criterion button
        this.container.addEventListener('click', (e) => {
            const btn = e.target.closest('.add-criterion-btn');
            if (btn) {
                const groupIndex = parseInt(btn.dataset.groupIndex);
                this.openValueSelector(groupIndex, null);
            }
        });

        // Add value to existing criterion
        this.container.addEventListener('click', (e) => {
            const btn = e.target.closest('.add-value-btn');
            if (btn) {
                const groupIndex = parseInt(btn.dataset.groupIndex);
                const criterionIndex = parseInt(btn.dataset.criterionIndex);
                this.openValueSelector(groupIndex, criterionIndex);
            }
        });

        // Remove value chip
        this.container.addEventListener('click', (e) => {
            const btn = e.target.closest('.chip-remove');
            if (btn) {
                const groupIndex = parseInt(btn.dataset.groupIndex);
                const criterionIndex = parseInt(btn.dataset.criterionIndex);
                const valueIndex = parseInt(btn.dataset.valueIndex);
                this.removeValue(groupIndex, criterionIndex, valueIndex);
            }
        });

        // Operator dropdown change
        this.container.addEventListener('change', (e) => {
            if (e.target.classList.contains('criterion-operator-select')) {
                const groupIndex = parseInt(e.target.dataset.groupIndex);
                const criterionIndex = parseInt(e.target.dataset.criterionIndex);
                const isExclude = e.target.value === 'is_not';
                this.setExclude(groupIndex, criterionIndex, isExclude);
            }
        });

        // Remove criterion
        this.container.addEventListener('click', (e) => {
            const btn = e.target.closest('.remove-criterion-btn');
            if (btn) {
                const groupIndex = parseInt(btn.dataset.groupIndex);
                const criterionIndex = parseInt(btn.dataset.criterionIndex);
                this.removeCriterion(groupIndex, criterionIndex);
            }
        });

        // Modal close
        this.container.addEventListener('click', (e) => {
            if (e.target.closest('#close-selector-btn') || e.target.closest('#cancel-selector-btn')) {
                this.closeValueSelector();
            }
        });

        // Key search filter
        this.container.addEventListener('input', (e) => {
            if (e.target.id === 'key-search') {
                this.filterKeys(e.target.value);
            }
        });

        // Key option click
        this.container.addEventListener('click', async (e) => {
            const keyOption = e.target.closest('.key-option');
            if (keyOption) {
                const keyId = keyOption.dataset.keyId;
                const keyName = keyOption.textContent.trim();
                await this.selectKey(keyId, keyName);
            }
        });

        // Change key button
        this.container.addEventListener('click', (e) => {
            if (e.target.id === 'change-key-btn') {
                this.resetKeySelection();
            }
        });

        // Value search
        this.container.addEventListener('input', (e) => {
            if (e.target.id === 'value-search') {
                this.filterValues(e.target.value);
            }
        });

        // Value checkbox change
        this.container.addEventListener('change', (e) => {
            if (e.target.classList.contains('value-checkbox')) {
                this.updateApplyButton();
            }
        });

        // Apply button
        this.container.addEventListener('click', (e) => {
            if (e.target.closest('#apply-selector-btn')) {
                this.applyValueSelection();
            }
        });
    }

    addGroup() {
        const groups = this.selectedTargeting.key_value_pairs.groups;
        groups.push({ criteria: [] });
        this.refreshGroups();
        this.updateHiddenField();

        // Open value selector for the new group
        this.openValueSelector(groups.length - 1, null);
    }

    removeGroup(groupIndex) {
        const groups = this.selectedTargeting.key_value_pairs.groups;
        groups.splice(groupIndex, 1);
        this.refreshGroups();
        this.updateHiddenField();
    }

    addCriterion(groupIndex, keyId, values, exclude = false) {
        const groups = this.selectedTargeting.key_value_pairs.groups;

        // Ensure group exists
        while (groups.length <= groupIndex) {
            groups.push({ criteria: [] });
        }

        groups[groupIndex].criteria.push({
            keyId,
            values,
            ...(exclude ? { exclude: true } : {})
        });

        this.refreshGroups();
        this.updateHiddenField();
    }

    removeCriterion(groupIndex, criterionIndex) {
        const groups = this.selectedTargeting.key_value_pairs.groups;
        if (groups[groupIndex]) {
            groups[groupIndex].criteria.splice(criterionIndex, 1);

            // Remove empty groups (but keep at least one)
            if (groups[groupIndex].criteria.length === 0 && groups.length > 1) {
                groups.splice(groupIndex, 1);
            }

            this.refreshGroups();
            this.updateHiddenField();
        }
    }

    removeValue(groupIndex, criterionIndex, valueIndex) {
        const groups = this.selectedTargeting.key_value_pairs.groups;
        const criterion = groups[groupIndex]?.criteria[criterionIndex];

        if (criterion && criterion.values.length > valueIndex) {
            criterion.values.splice(valueIndex, 1);

            // Remove criterion if no values left
            if (criterion.values.length === 0) {
                this.removeCriterion(groupIndex, criterionIndex);
            } else {
                this.refreshGroups();
                this.updateHiddenField();
            }
        }
    }

    setExclude(groupIndex, criterionIndex, isExclude) {
        const groups = this.selectedTargeting.key_value_pairs.groups;
        const criterion = groups[groupIndex]?.criteria[criterionIndex];

        if (criterion) {
            if (isExclude) {
                criterion.exclude = true;
            } else {
                delete criterion.exclude;
            }
            this.refreshGroups();
            this.updateHiddenField();
        }
    }

    openValueSelector(groupIndex, criterionIndex) {
        this.editingCriterion = { groupIndex, criterionIndex };
        const modal = document.getElementById('value-selector-modal');
        const keySelector = document.getElementById('key-selector');
        const keySearch = document.getElementById('key-search');
        const keyList = document.getElementById('key-list');
        const selectedKeyDisplay = document.getElementById('selected-key-display');
        const selectedKeyName = document.getElementById('selected-key-name');
        const operatorSelector = document.getElementById('operator-selector');
        const title = document.getElementById('value-selector-title');

        // Reset state - show key search, hide selected key display
        keySelector.value = '';
        keySearch.value = '';
        keySearch.style.display = '';
        keyList.style.display = '';
        selectedKeyDisplay.style.display = 'none';
        operatorSelector.value = 'is';
        document.getElementById('values-section').style.display = 'none';
        document.getElementById('operator-section').style.display = 'none';
        document.getElementById('apply-selector-btn').disabled = true;

        // Reset key filter to show all keys
        this.filterKeys('');

        // If editing existing criterion, pre-select the key and operator
        if (criterionIndex !== null) {
            const groups = this.selectedTargeting.key_value_pairs.groups;
            const criterion = groups[groupIndex]?.criteria[criterionIndex];
            if (criterion) {
                const keyName = this.keyMetadata[criterion.keyId]?.display_name || criterion.keyId;
                // Set the key as selected
                keySelector.value = criterion.keyId;
                selectedKeyName.textContent = keyName;
                keySearch.style.display = 'none';
                keyList.style.display = 'none';
                selectedKeyDisplay.style.display = 'flex';

                operatorSelector.value = criterion.exclude ? 'is_not' : 'is';
                title.textContent = `Add values to ${keyName}`;
                // Load values for this key
                this.loadValuesForKey(criterion.keyId);
            }
        } else {
            title.textContent = 'Add targeting';
        }

        modal.style.display = 'flex';
    }

    closeValueSelector() {
        const modal = document.getElementById('value-selector-modal');
        modal.style.display = 'none';
        this.editingCriterion = null;
    }

    async loadValuesForKey(keyId) {
        const valuesSection = document.getElementById('values-section');
        const valuesList = document.getElementById('values-list');
        const operatorSection = document.getElementById('operator-section');

        valuesSection.style.display = 'block';
        operatorSection.style.display = 'block';
        valuesList.innerHTML = '<div class="loading">Loading values...</div>';

        try {
            const values = await this.fetchValuesForKey(keyId);

            if (values.length === 0) {
                valuesList.innerHTML = '<div class="empty-state">No values available for this key</div>';
                return;
            }

            // Get currently selected values for this criterion
            const selectedValues = new Set();
            if (this.editingCriterion && this.editingCriterion.criterionIndex !== null) {
                const groups = this.selectedTargeting.key_value_pairs.groups;
                const criterion = groups[this.editingCriterion.groupIndex]?.criteria[this.editingCriterion.criterionIndex];
                if (criterion && criterion.keyId === keyId) {
                    criterion.values.forEach(v => selectedValues.add(v));
                }
            }

            valuesList.innerHTML = values.map(val => `
                <label class="value-option">
                    <input type="checkbox" class="value-checkbox"
                           value="${val.id}"
                           data-name="${val.display_name || val.name}"
                           ${selectedValues.has(val.id) ? 'checked' : ''}>
                    <span class="value-label">${val.display_name || val.name}</span>
                </label>
            `).join('');

            this.updateApplyButton();

        } catch (error) {
            valuesList.innerHTML = `<div class="error-state">Failed to load values: ${error.message}</div>`;
        }
    }

    filterValues(query) {
        const items = this.container.querySelectorAll('.value-option');
        const lowerQuery = query.toLowerCase();

        items.forEach(item => {
            const text = item.textContent.toLowerCase();
            item.style.display = text.includes(lowerQuery) ? '' : 'none';
        });
    }

    filterKeys(query) {
        const items = this.container.querySelectorAll('.key-option');
        const lowerQuery = query.toLowerCase();

        items.forEach(item => {
            const keyName = item.dataset.keyName || item.textContent.toLowerCase();
            item.style.display = keyName.includes(lowerQuery) ? '' : 'none';
        });
    }

    async selectKey(keyId, keyName) {
        const keySelector = this.container.querySelector('#key-selector');
        const keySearch = this.container.querySelector('#key-search');
        const keyList = this.container.querySelector('#key-list');
        const selectedKeyDisplay = this.container.querySelector('#selected-key-display');
        const selectedKeyName = this.container.querySelector('#selected-key-name');

        // Update hidden input and display
        keySelector.value = keyId;
        selectedKeyName.textContent = keyName;
        keySearch.style.display = 'none';
        keyList.style.display = 'none';
        selectedKeyDisplay.style.display = 'flex';

        // Load values for the selected key
        await this.loadValuesForKey(keyId);
    }

    resetKeySelection() {
        const keySelector = this.container.querySelector('#key-selector');
        const keySearch = this.container.querySelector('#key-search');
        const keyList = this.container.querySelector('#key-list');
        const selectedKeyDisplay = this.container.querySelector('#selected-key-display');
        const valuesSection = this.container.querySelector('#values-section');
        const operatorSection = this.container.querySelector('#operator-section');

        // Reset state
        keySelector.value = '';
        keySearch.value = '';
        keySearch.style.display = '';
        keyList.style.display = '';
        selectedKeyDisplay.style.display = 'none';
        valuesSection.style.display = 'none';
        operatorSection.style.display = 'none';

        // Show all keys again
        this.filterKeys('');
        this.updateApplyButton();
    }

    updateApplyButton() {
        const checkboxes = this.container.querySelectorAll('.value-checkbox:checked');
        const applyBtn = document.getElementById('apply-selector-btn');
        const keySelector = document.getElementById('key-selector');

        applyBtn.disabled = !keySelector.value || checkboxes.length === 0;
    }

    applyValueSelection() {
        const keySelector = document.getElementById('key-selector');
        const operatorSelector = document.getElementById('operator-selector');
        const keyId = keySelector.value;
        const isExclude = operatorSelector.value === 'is_not';
        const checkboxes = this.container.querySelectorAll('.value-checkbox:checked');

        if (!keyId || checkboxes.length === 0) return;

        const values = Array.from(checkboxes).map(cb => cb.value);
        const { groupIndex, criterionIndex } = this.editingCriterion;

        if (criterionIndex !== null) {
            // Adding values to existing criterion
            const groups = this.selectedTargeting.key_value_pairs.groups;
            const criterion = groups[groupIndex]?.criteria[criterionIndex];

            if (criterion && criterion.keyId === keyId) {
                // Add only new values
                const existingValues = new Set(criterion.values);
                values.forEach(v => {
                    if (!existingValues.has(v)) {
                        criterion.values.push(v);
                    }
                });
                // Update the operator based on modal selection
                if (isExclude) {
                    criterion.exclude = true;
                } else {
                    delete criterion.exclude;
                }
            }
        } else {
            // Creating new criterion with selected operator
            this.addCriterion(groupIndex, keyId, values, isExclude);
        }

        this.closeValueSelector();
        this.refreshGroups();
        this.updateHiddenField();
    }

    refreshGroups() {
        const groupsContainer = document.getElementById('groups-container');
        if (groupsContainer) {
            groupsContainer.innerHTML = this.renderGroups();
        }
    }

    updateHiddenField() {
        // Only update hidden field if we successfully loaded existing data
        // This prevents accidentally clearing targeting if there was a load error
        if (!this.loadedSuccessfully) {
            console.warn('[TargetingWidget] Skipping hidden field update - data not loaded successfully');
            return;
        }

        const groups = this.selectedTargeting.key_value_pairs.groups;
        const hiddenField = document.getElementById('targeting-data');

        // Filter out empty groups
        const nonEmptyGroups = groups.filter(g => g.criteria && g.criteria.length > 0);

        if (hiddenField) {
            if (nonEmptyGroups.length > 0) {
                hiddenField.value = JSON.stringify({
                    key_value_pairs: { groups: nonEmptyGroups }
                });
            } else {
                hiddenField.value = '';
            }
        }
    }
}

window.TargetingWidget = TargetingWidget;
