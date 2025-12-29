/**
 * Format Template Picker - AdCP 2.5 Parameterized Format Selection
 *
 * This module provides a reusable UI component for selecting creative format templates
 * with configurable sizes (dimensions) instead of pre-defined format variations.
 *
 * Key features:
 * - Template-based selection mapped to creative agent format IDs
 * - Size parameters stored in FormatId: {agent_url, id, width, height, duration_ms}
 * - Auto-populate sizes from inventory metadata
 * - Backward compatible with legacy format_ids
 * - Support for custom formats from tenant creative agents
 *
 * @see https://github.com/adcontextprotocol/salesagent/issues/782
 */

/**
 * Escape HTML entities to prevent XSS attacks.
 * @param {string} str - The string to escape
 * @returns {string} - The escaped string safe for HTML interpolation
 */
function escapeHtml(str) {
    if (typeof str !== 'string') return String(str);
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

/**
 * Format templates mapped to creative agent format IDs.
 * These IDs must match what the creative agent returns in list_creative_formats.
 *
 * Creative agent parameterized formats (from https://creative.adcontextprotocol.org):
 * - display_image: accepts dimensions (width/height) - static images
 * - display_html: accepts dimensions - HTML5 creatives
 * - display_js: accepts dimensions - JavaScript creatives
 * - display_generative: accepts dimensions - AI-generated
 * - video_standard: accepts duration
 * - video_vast: accepts duration - VAST redirect
 * - video_dimensions: accepts dimensions
 * - native_standard: no parameters
 *
 * Note: "display" and "video" templates are virtual templates that expand to
 * multiple format types so products accept any creative variation.
 * The actual creative type is auto-detected at upload time.
 */
const FORMAT_TEMPLATES = {
    // Unified display template - expands to display_image, display_html, display_js
    display: {
        id: "display",  // Virtual ID - expands to multiple format IDs
        name: "Display",
        description: "Display banner ads (image, HTML5, or JavaScript - auto-detected at upload)",
        type: "display",
        parameterType: "dimensions",  // width/height
        // Maps to these actual creative agent format IDs
        expandsTo: ["display_image", "display_html", "display_js"],
        commonSizes: [
            { width: 300, height: 250, name: "Medium Rectangle" },
            { width: 728, height: 90, name: "Leaderboard" },
            { width: 160, height: 600, name: "Wide Skyscraper" },
            { width: 300, height: 600, name: "Half Page" },
            { width: 320, height: 50, name: "Mobile Banner" },
            { width: 970, height: 250, name: "Billboard" },
            { width: 336, height: 280, name: "Large Rectangle" },
            { width: 468, height: 60, name: "Banner" },
            { width: 320, height: 100, name: "Large Mobile Banner" },
            { width: 970, height: 90, name: "Large Leaderboard" }
        ],
        gamSupported: true
    },
    // Unified video template - expands to video_standard, video_vast
    video: {
        id: "video",  // Virtual ID - expands to multiple format IDs
        name: "Video",
        description: "Video ads (hosted or VAST - auto-detected at upload)",
        type: "video",
        parameterType: "duration",  // duration_ms parameter
        // Maps to these actual creative agent format IDs
        expandsTo: ["video_standard", "video_vast"],
        commonDurations: [
            { ms: 6000, name: "6s (Bumper)" },
            { ms: 15000, name: "15s (Standard)" },
            { ms: 30000, name: "30s (Standard)" },
            { ms: 60000, name: "60s (Long)" }
        ],
        gamSupported: true
    },
    native_standard: {
        id: "native_standard",
        name: "Native",
        description: "Native content ads that match the look of the site",
        type: "native",
        parameterType: "none",  // No dimension parameters
        gamSupported: true
    }
};

/**
 * Default creative agent URL for AdCP reference formats.
 */
const DEFAULT_CREATIVE_AGENT_URL = "https://creative.adcontextprotocol.org";

/**
 * Format Template Picker Component
 *
 * Usage:
 *   const picker = new FormatTemplatePicker({
 *       containerId: 'format-picker-container',
 *       hiddenInputId: 'formats-data',
 *       tenantId: 'tenant_123',
 *       adapterType: 'gam',  // or 'mock'
 *       initialFormats: [],  // Existing format_ids for edit mode
 *       onSelectionChange: (formats) => console.log('Selected:', formats)
 *   });
 */
class FormatTemplatePicker {
    constructor(options) {
        this.containerId = options.containerId;
        this.hiddenInputId = options.hiddenInputId;
        this.tenantId = options.tenantId;
        this.adapterType = options.adapterType || 'mock';
        this.scriptRoot = options.scriptRoot || '';
        this.onSelectionChange = options.onSelectionChange || (() => {});

        // State
        this.selectedTemplates = new Map();  // templateId -> Set of {width, height} or {duration_ms}
        this.customFormats = [];  // Legacy/custom formats: [{agent_url, id, width?, height?}]
        this.inventorySizes = new Set();  // Sizes from inventory: "300x250", "728x90"
        this.agentFormats = {};  // Formats from creative agents: {agent_url: [Format, ...]}
        this.agentsLoaded = false;  // Track if agent formats have been loaded

        // Initialize
        this.container = document.getElementById(this.containerId);
        this.hiddenInput = document.getElementById(this.hiddenInputId);

        if (!this.container) {
            console.error(`[FormatTemplatePicker] Container #${this.containerId} not found`);
            return;
        }

        // Load initial formats if provided (edit mode)
        if (options.initialFormats && options.initialFormats.length > 0) {
            this._parseInitialFormats(options.initialFormats);
            // Update hidden input with initial formats so form submission works
            this._updateHiddenInput();
        }

        this.render();
        this._loadAgentFormats();
    }

    /**
     * Parse existing format_ids into template selections.
     * Handles both new parameterized formats and legacy formats.
     */
    _parseInitialFormats(formats) {
        for (const fmt of formats) {
            const id = fmt.id || fmt.format_id;
            const agentUrl = fmt.agent_url || DEFAULT_CREATIVE_AGENT_URL;

            // Check if this is a known template
            if (FORMAT_TEMPLATES[id]) {
                if (!this.selectedTemplates.has(id)) {
                    this.selectedTemplates.set(id, new Set());
                }

                // Add size/duration if present
                if (fmt.width && fmt.height) {
                    this.selectedTemplates.get(id).add(`${fmt.width}x${fmt.height}`);
                }
                if (fmt.duration_ms) {
                    this.selectedTemplates.get(id).add(`d:${fmt.duration_ms}`);
                }
            } else {
                // Legacy or custom format
                this.customFormats.push({
                    agent_url: agentUrl,
                    id: id,
                    width: fmt.width,
                    height: fmt.height,
                    duration_ms: fmt.duration_ms
                });
            }
        }
    }

    /**
     * Load formats from creative agents for the dropdown selection.
     * Fetches from /api/formats/list which returns all formats from tenant's creative agents.
     */
    async _loadAgentFormats() {
        try {
            const response = await fetch(`${this.scriptRoot}/api/formats/list?tenant_id=${encodeURIComponent(this.tenantId)}`);
            if (!response.ok) {
                console.warn('[FormatTemplatePicker] HTTP error loading agent formats:', response.status);
                return;
            }
            const data = await response.json();

            if (data.error) {
                console.warn('[FormatTemplatePicker] Error loading agent formats:', data.error);
                return;
            }

            // Store formats grouped by agent
            this.agentFormats = data.agents || {};
            this.agentsLoaded = true;

            // Log what we got
            const totalFormats = Object.values(this.agentFormats).reduce((sum, fmts) => sum + fmts.length, 0);
            const agentCount = Object.keys(this.agentFormats).length;
            console.log(`[FormatTemplatePicker] Loaded ${totalFormats} formats from ${agentCount} creative agent(s)`);

            // Re-render to show the dropdown
            this.render();
        } catch (error) {
            console.error('[FormatTemplatePicker] Failed to load agent formats:', error);
        }
    }

    /**
     * Add sizes from inventory metadata.
     * Called when user selects ad units/placements from inventory.
     */
    addSizesFromInventory(sizes) {
        if (!sizes || !Array.isArray(sizes)) return;

        sizes.forEach(sizeStr => {
            if (typeof sizeStr === 'string' && sizeStr.includes('x')) {
                this.inventorySizes.add(sizeStr);

                // Auto-select the unified "display" template and add these sizes
                // This template expands to display_image, display_html, display_js
                if (!this.selectedTemplates.has('display')) {
                    this.selectedTemplates.set('display', new Set());
                }
                this.selectedTemplates.get('display').add(sizeStr);
            }
        });

        this.render();
        this._updateHiddenInput();
    }

    /**
     * Clear inventory-derived sizes.
     */
    clearInventorySizes() {
        // Remove inventory sizes from selections
        for (const sizeStr of this.inventorySizes) {
            for (const [templateId, sizes] of this.selectedTemplates) {
                sizes.delete(sizeStr);
            }
        }
        this.inventorySizes.clear();
        this.render();
        this._updateHiddenInput();
    }

    /**
     * Remove a single inventory size.
     */
    removeInventorySize(sizeStr) {
        if (!this.inventorySizes.has(sizeStr)) return;

        // Remove from inventory sizes set
        this.inventorySizes.delete(sizeStr);

        // Remove from all template selections
        for (const [templateId, sizes] of this.selectedTemplates) {
            sizes.delete(sizeStr);
        }

        this.render();
        this._updateHiddenInput();
    }

    /**
     * Toggle a template selection.
     */
    toggleTemplate(templateId) {
        if (this.selectedTemplates.has(templateId)) {
            this.selectedTemplates.delete(templateId);
        } else {
            this.selectedTemplates.set(templateId, new Set());
        }
        this.render();
        this._updateHiddenInput();
    }

    /**
     * Toggle a size for a template.
     */
    toggleSize(templateId, width, height) {
        const sizeKey = `${width}x${height}`;

        if (!this.selectedTemplates.has(templateId)) {
            this.selectedTemplates.set(templateId, new Set());
        }

        const sizes = this.selectedTemplates.get(templateId);
        if (sizes.has(sizeKey)) {
            sizes.delete(sizeKey);
        } else {
            sizes.add(sizeKey);
        }

        this.render();
        this._updateHiddenInput();
    }

    /**
     * Toggle a duration for a template.
     */
    toggleDuration(templateId, durationMs) {
        const durationKey = `d:${durationMs}`;

        if (!this.selectedTemplates.has(templateId)) {
            this.selectedTemplates.set(templateId, new Set());
        }

        const params = this.selectedTemplates.get(templateId);
        if (params.has(durationKey)) {
            params.delete(durationKey);
        } else {
            params.add(durationKey);
        }

        this.render();
        this._updateHiddenInput();
    }

    /**
     * Add a custom size to a template.
     */
    addCustomSize(templateId, width, height) {
        if (!width || !height || width <= 0 || height <= 0) {
            alert('Please enter valid width and height values.');
            return;
        }

        const sizeKey = `${width}x${height}`;

        if (!this.selectedTemplates.has(templateId)) {
            this.selectedTemplates.set(templateId, new Set());
        }

        this.selectedTemplates.get(templateId).add(sizeKey);
        this.render();
        this._updateHiddenInput();
    }

    /**
     * Add a custom/agent format.
     */
    addCustomFormat(agentUrl, formatId, width, height, durationMs) {
        if (!agentUrl || !formatId) {
            alert('Agent URL and Format ID are required.');
            return;
        }

        this.customFormats.push({
            agent_url: agentUrl,
            id: formatId,
            width: width || null,
            height: height || null,
            duration_ms: durationMs || null
        });

        this.render();
        this._updateHiddenInput();
    }

    /**
     * Remove a custom format.
     */
    removeCustomFormat(index) {
        this.customFormats.splice(index, 1);
        this.render();
        this._updateHiddenInput();
    }

    /**
     * Get all selected formats as FormatId objects.
     *
     * Templates with `expandsTo` property (like "display") will emit multiple
     * format IDs for each size, so products accept any creative type.
     */
    getSelectedFormats() {
        const formats = [];

        // Template-based formats
        for (const [templateId, params] of this.selectedTemplates) {
            const template = FORMAT_TEMPLATES[templateId];
            if (!template) continue;

            // Get the actual format IDs to emit
            // Templates with expandsTo emit multiple format IDs per size
            const formatIds = template.expandsTo || [templateId];

            if (params.size === 0) {
                // Template selected but no sizes - include without params
                for (const fmtId of formatIds) {
                    formats.push({
                        agent_url: DEFAULT_CREATIVE_AGENT_URL,
                        id: fmtId
                    });
                }
            } else {
                // Include each size/duration as separate format
                for (const paramKey of params) {
                    // For each parameter (size/duration), emit all format IDs
                    for (const fmtId of formatIds) {
                        const format = {
                            agent_url: DEFAULT_CREATIVE_AGENT_URL,
                            id: fmtId
                        };

                        if (paramKey.startsWith('d:')) {
                            format.duration_ms = parseInt(paramKey.substring(2), 10);
                        } else if (paramKey.includes('x')) {
                            const [w, h] = paramKey.split('x').map(Number);
                            format.width = w;
                            format.height = h;
                        }

                        formats.push(format);
                    }
                }
            }
        }

        // Custom/legacy formats
        for (const customFmt of this.customFormats) {
            formats.push({
                agent_url: customFmt.agent_url,
                id: customFmt.id,
                width: customFmt.width || undefined,
                height: customFmt.height || undefined,
                duration_ms: customFmt.duration_ms || undefined
            });
        }

        return formats;
    }

    /**
     * Update the hidden input with selected formats.
     */
    _updateHiddenInput() {
        const formats = this.getSelectedFormats();
        this.hiddenInput.value = JSON.stringify(formats);
        this.onSelectionChange(formats);
    }

    /**
     * Render the picker UI.
     */
    render() {
        const isGAM = this.adapterType === 'gam';

        // Filter templates for GAM (no audio)
        const availableTemplates = Object.values(FORMAT_TEMPLATES).filter(t =>
            !isGAM || t.gamSupported
        );

        let html = `
            <div class="format-template-picker">
                <!-- Standard Format Templates -->
                <div class="template-section">
                    <h4 style="margin-bottom: 1rem; color: #2c3e50; border-bottom: 1px solid #ddd; padding-bottom: 0.5rem;">
                        Standard Format Templates
                    </h4>
                    <div class="template-grid" style="display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 1rem; margin-bottom: 1.5rem;">
        `;

        // Render template cards
        for (const template of availableTemplates) {
            const isSelected = this.selectedTemplates.has(template.id);
            const selectedParams = this.selectedTemplates.get(template.id) || new Set();
            const sizeCount = [...selectedParams].filter(p => !p.startsWith('d:')).length;
            const durationCount = [...selectedParams].filter(p => p.startsWith('d:')).length;

            html += this._renderTemplateCard(template, isSelected, sizeCount, durationCount);
        }

        html += `
                    </div>
                </div>
        `;

        // Size configuration for selected templates
        for (const [templateId, params] of this.selectedTemplates) {
            const template = FORMAT_TEMPLATES[templateId];
            if (!template) continue;

            html += this._renderSizeConfiguration(template, params);
        }

        // Inventory sizes section (if any)
        if (this.inventorySizes.size > 0) {
            html += this._renderInventorySizesSection();
        }

        // Custom formats section
        html += this._renderCustomFormatsSection();

        // Selected formats summary
        html += this._renderSelectedSummary();

        html += `</div>`;

        this.container.innerHTML = html;
        this._attachEventListeners();
    }

    _renderTemplateCard(template, isSelected, sizeCount, durationCount) {
        const borderColor = isSelected ? '#0066cc' : '#e0e0e0';
        const bgColor = isSelected ? '#f0f7ff' : '#f9f9f9';
        const checkIcon = isSelected ? '&#10003; ' : '';

        let paramInfo = '';
        if (isSelected) {
            if (sizeCount > 0) paramInfo += `${sizeCount} size${sizeCount > 1 ? 's' : ''}`;
            if (durationCount > 0) {
                if (paramInfo) paramInfo += ', ';
                paramInfo += `${durationCount} duration${durationCount > 1 ? 's' : ''}`;
            }
        }

        return `
            <div class="template-card"
                 data-template-id="${template.id}"
                 style="border: 2px solid ${borderColor}; border-radius: 8px; padding: 1rem; background: ${bgColor}; cursor: pointer; transition: all 0.2s;">
                <div style="font-weight: 600; color: #333; margin-bottom: 0.5rem;">
                    ${checkIcon}${template.name}
                </div>
                <div style="color: #666; font-size: 0.85rem; margin-bottom: 0.5rem;">
                    ${template.description}
                </div>
                <div style="display: flex; gap: 0.5rem; flex-wrap: wrap;">
                    <span style="background: #e9ecef; padding: 0.2rem 0.5rem; border-radius: 3px; font-size: 0.75rem;">
                        ${template.type}
                    </span>
                    ${paramInfo ? `<span style="background: #0066cc; color: white; padding: 0.2rem 0.5rem; border-radius: 3px; font-size: 0.75rem;">${paramInfo}</span>` : ''}
                </div>
            </div>
        `;
    }

    _renderSizeConfiguration(template, selectedParams) {
        let html = `
            <div class="size-config" data-template-id="${template.id}" style="margin-bottom: 1.5rem; padding: 1rem; background: #f8f9fa; border: 1px solid #dee2e6; border-radius: 8px;">
                <h5 style="margin: 0 0 1rem 0; color: #333;">
                    Configure: ${template.name}
                </h5>
        `;

        // Common sizes (quick-pick buttons)
        if (template.commonSizes && template.commonSizes.length > 0) {
            html += `
                <div style="margin-bottom: 1rem;">
                    <label style="display: block; font-weight: 600; margin-bottom: 0.5rem; color: #555;">
                        Common Sizes:
                    </label>
                    <div style="display: flex; flex-wrap: wrap; gap: 0.5rem;">
            `;

            for (const size of template.commonSizes) {
                const sizeKey = `${size.width}x${size.height}`;
                const isSelected = selectedParams.has(sizeKey);
                const isFromInventory = this.inventorySizes.has(sizeKey);
                const bgColor = isSelected ? '#0066cc' : '#fff';
                const textColor = isSelected ? '#fff' : '#333';
                const border = isFromInventory ? '2px solid #28a745' : '1px solid #ddd';

                html += `
                    <button type="button"
                            class="size-btn"
                            data-template-id="${template.id}"
                            data-width="${size.width}"
                            data-height="${size.height}"
                            style="padding: 0.5rem 0.75rem; border: ${border}; border-radius: 4px; background: ${bgColor}; color: ${textColor}; cursor: pointer; font-size: 0.9rem;">
                        ${sizeKey}
                        <span style="font-size: 0.75rem; display: block; color: ${isSelected ? '#ddd' : '#888'};">${size.name}</span>
                    </button>
                `;
            }

            html += `</div></div>`;
        }

        // Common durations for video/audio
        if (template.commonDurations && template.commonDurations.length > 0) {
            html += `
                <div style="margin-bottom: 1rem;">
                    <label style="display: block; font-weight: 600; margin-bottom: 0.5rem; color: #555;">
                        Durations:
                    </label>
                    <div style="display: flex; flex-wrap: wrap; gap: 0.5rem;">
            `;

            for (const dur of template.commonDurations) {
                const durKey = `d:${dur.ms}`;
                const isSelected = selectedParams.has(durKey);
                const bgColor = isSelected ? '#0066cc' : '#fff';
                const textColor = isSelected ? '#fff' : '#333';

                html += `
                    <button type="button"
                            class="duration-btn"
                            data-template-id="${template.id}"
                            data-duration-ms="${dur.ms}"
                            style="padding: 0.5rem 0.75rem; border: 1px solid #ddd; border-radius: 4px; background: ${bgColor}; color: ${textColor}; cursor: pointer; font-size: 0.9rem;">
                        ${dur.name}
                    </button>
                `;
            }

            html += `</div></div>`;
        }

        // Custom size input
        if (template.parameterType === 'dimensions' || template.parameterType === 'both') {
            html += `
                <div style="margin-top: 1rem;">
                    <label style="display: block; font-weight: 600; margin-bottom: 0.5rem; color: #555;">
                        Custom Size:
                    </label>
                    <div style="display: flex; gap: 0.5rem; align-items: center;">
                        <input type="number" class="custom-width" data-template-id="${template.id}"
                               placeholder="Width" style="width: 80px; padding: 0.5rem; border: 1px solid #ddd; border-radius: 4px;">
                        <span>x</span>
                        <input type="number" class="custom-height" data-template-id="${template.id}"
                               placeholder="Height" style="width: 80px; padding: 0.5rem; border: 1px solid #ddd; border-radius: 4px;">
                        <button type="button" class="add-custom-size-btn" data-template-id="${template.id}"
                                style="padding: 0.5rem 1rem; background: #6c757d; color: white; border: none; border-radius: 4px; cursor: pointer;">
                            Add
                        </button>
                    </div>
                </div>
            `;
        }

        // Selected sizes as removable tags
        const selectedSizes = [...selectedParams].filter(p => !p.startsWith('d:'));
        if (selectedSizes.length > 0) {
            html += `
                <div style="margin-top: 1rem;">
                    <label style="display: block; font-weight: 600; margin-bottom: 0.5rem; color: #555;">
                        Selected Sizes:
                    </label>
                    <div style="display: flex; flex-wrap: wrap; gap: 0.5rem;">
            `;

            for (const sizeKey of selectedSizes) {
                const isFromInventory = this.inventorySizes.has(sizeKey);
                const escapedSizeKey = escapeHtml(sizeKey);
                html += `
                    <span style="background: #0066cc; color: white; padding: 0.4rem 0.6rem; border-radius: 4px; display: inline-flex; align-items: center; gap: 0.5rem; font-size: 0.9rem;">
                        ${escapedSizeKey}
                        ${isFromInventory ? '<span title="From inventory" style="font-size: 0.7rem;">&#x1F4E6;</span>' : ''}
                        <span class="remove-size" data-template-id="${escapeHtml(template.id)}" data-size="${escapedSizeKey}"
                              style="cursor: pointer; font-weight: bold; margin-left: 0.25rem;">&times;</span>
                    </span>
                `;
            }

            html += `</div></div>`;
        }

        html += `</div>`;
        return html;
    }

    _renderInventorySizesSection() {
        const sizes = [...this.inventorySizes];

        return `
            <div style="margin-bottom: 1.5rem; padding: 1rem; background: #e8f5e9; border: 1px solid #c8e6c9; border-radius: 8px;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.5rem;">
                    <h5 style="margin: 0; color: #2e7d32;">
                        &#x1F4E6; Sizes from Inventory
                    </h5>
                    <button type="button" class="clear-inventory-sizes-btn"
                            style="padding: 0.25rem 0.5rem; background: #d32f2f; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 0.75rem;">
                        Clear All
                    </button>
                </div>
                <p style="color: #666; font-size: 0.85rem; margin-bottom: 0.5rem;">
                    These sizes were automatically added based on your selected inventory. Click × to remove.
                </p>
                <div style="display: flex; flex-wrap: wrap; gap: 0.5rem;">
                    ${sizes.map(s => {
                        const escaped = escapeHtml(s);
                        return `
                        <span class="inventory-size-chip" data-size="${escaped}"
                              style="background: #4caf50; color: white; padding: 0.3rem 0.6rem; border-radius: 4px; font-size: 0.85rem; display: inline-flex; align-items: center; gap: 0.3rem; cursor: default;">
                            ${escaped}
                            <span class="remove-inventory-size" data-size="${escaped}"
                                  style="cursor: pointer; font-weight: bold; margin-left: 0.2rem;">&times;</span>
                        </span>
                    `;}).join('')}
                </div>
            </div>
        `;
    }

    _renderCustomFormatsSection() {
        // Build dropdown options from creative agent formats
        const hasAgentFormats = this.agentsLoaded && Object.keys(this.agentFormats).length > 0;

        let html = `
            <div class="custom-formats-section" style="margin-top: 2rem; padding: 1rem; background: #e3f2fd; border: 1px solid #90caf9; border-radius: 8px;">
                <h5 style="margin: 0 0 1rem 0; color: #1565c0;">
                    Additional Formats from Creative Agents
                </h5>
                <p style="color: #666; font-size: 0.85rem; margin-bottom: 1rem;">
                    ${hasAgentFormats
                        ? 'Select formats from your registered creative agents. These are parameterized formats that work with your ad server.'
                        : 'Loading formats from creative agents...'}
                </p>
        `;

        // Existing custom formats
        if (this.customFormats.length > 0) {
            html += `<div style="margin-bottom: 1rem;">`;
            for (let i = 0; i < this.customFormats.length; i++) {
                const fmt = this.customFormats[i];
                let label = fmt.id;
                if (fmt.width && fmt.height) label += ` (${fmt.width}x${fmt.height})`;
                if (fmt.duration_ms) label += ` (${fmt.duration_ms / 1000}s)`;
                html += `
                    <span style="background: #1976d2; color: white; padding: 0.4rem 0.6rem; border-radius: 4px; display: inline-flex; align-items: center; gap: 0.5rem; margin: 0.25rem; font-size: 0.85rem;">
                        ${label}
                        <span class="remove-custom" data-index="${i}"
                              style="cursor: pointer; font-weight: bold;">&times;</span>
                    </span>
                `;
            }
            html += `</div>`;
        }

        // Format selection dropdown (only if formats are loaded)
        if (hasAgentFormats) {
            html += `
                <div style="display: flex; gap: 0.5rem; align-items: end; flex-wrap: wrap;">
                    <div style="flex: 1; min-width: 300px;">
                        <label style="display: block; font-size: 0.85rem; margin-bottom: 0.25rem;">Select Format</label>
                        <select id="agent-format-select"
                                style="width: 100%; padding: 0.5rem; border: 1px solid #ddd; border-radius: 4px; font-size: 0.9rem;">
                            <option value="">-- Select a format --</option>
            `;

            // Group formats by agent
            for (const [agentUrl, formats] of Object.entries(this.agentFormats)) {
                // Get short name for agent
                const agentName = agentUrl.includes('adcontextprotocol.org')
                    ? 'AdCP Standard'
                    : new URL(agentUrl).hostname;

                html += `<optgroup label="${agentName}">`;

                for (const fmt of formats) {
                    const formatId = typeof fmt.format_id === 'object' ? fmt.format_id.id : fmt.format_id;
                    const dimsStr = fmt.dimensions ? ` (${fmt.dimensions})` : '';
                    const typeStr = fmt.type ? ` [${fmt.type}]` : '';

                    // Store full format info as data attribute (JSON encoded)
                    const formatData = JSON.stringify({
                        agent_url: agentUrl,
                        id: formatId,
                        name: fmt.name,
                        type: fmt.type,
                        dimensions: fmt.dimensions
                    });

                    html += `
                        <option value="${formatId}" data-format='${formatData.replace(/'/g, "&#39;")}'>
                            ${fmt.name || formatId}${dimsStr}${typeStr}
                        </option>
                    `;
                }

                html += `</optgroup>`;
            }

            html += `
                        </select>
                    </div>
                    <div>
                        <button type="button" id="add-agent-format-btn"
                                style="padding: 0.5rem 1rem; background: #1976d2; color: white; border: none; border-radius: 4px; cursor: pointer; white-space: nowrap;">
                            + Add Format
                        </button>
                    </div>
                </div>
            `;

            // Show parameter inputs for parameterized formats
            html += `
                <div id="format-params-section" style="margin-top: 1rem; padding: 0.75rem; background: #f5f5f5; border-radius: 4px; display: none;">
                    <div style="display: flex; gap: 1rem; flex-wrap: wrap;">
                        <div id="dims-params" style="display: none;">
                            <label style="display: block; font-size: 0.85rem; margin-bottom: 0.25rem;">Dimensions (optional)</label>
                            <div style="display: flex; gap: 0.25rem; align-items: center;">
                                <input type="number" id="param-width" placeholder="Width" style="width: 80px; padding: 0.4rem; border: 1px solid #ddd; border-radius: 4px;">
                                <span>x</span>
                                <input type="number" id="param-height" placeholder="Height" style="width: 80px; padding: 0.4rem; border: 1px solid #ddd; border-radius: 4px;">
                            </div>
                        </div>
                        <div id="duration-params" style="display: none;">
                            <label style="display: block; font-size: 0.85rem; margin-bottom: 0.25rem;">Duration (optional)</label>
                            <select id="param-duration" style="padding: 0.4rem; border: 1px solid #ddd; border-radius: 4px;">
                                <option value="">No duration</option>
                                <option value="6000">6s (Bumper)</option>
                                <option value="15000">15s (Standard)</option>
                                <option value="30000">30s (Standard)</option>
                                <option value="60000">60s (Long)</option>
                            </select>
                        </div>
                    </div>
                </div>
            `;
        }

        html += `</div>`;
        return html;
    }

    _renderSelectedSummary() {
        const formats = this.getSelectedFormats();

        if (formats.length === 0) {
            return `
                <div style="margin-top: 1.5rem; padding: 1rem; background: #fff3cd; border: 1px solid #ffc107; border-radius: 8px; color: #856404;">
                    <strong>No formats selected.</strong> Select at least one format template or add a custom format.
                </div>
            `;
        }

        let html = `
            <div style="margin-top: 1.5rem; padding: 1rem; background: #d4edda; border: 1px solid #c3e6cb; border-radius: 8px;">
                <h5 style="margin: 0 0 0.5rem 0; color: #155724;">
                    &#10003; ${formats.length} Format${formats.length > 1 ? 's' : ''} Selected
                </h5>
                <div style="display: flex; flex-wrap: wrap; gap: 0.5rem;">
        `;

        for (const fmt of formats) {
            let label = fmt.id;
            if (fmt.width && fmt.height) {
                label += ` (${fmt.width}x${fmt.height})`;
            }
            if (fmt.duration_ms) {
                label += ` (${fmt.duration_ms / 1000}s)`;
            }

            html += `
                <span style="background: #28a745; color: white; padding: 0.3rem 0.6rem; border-radius: 4px; font-size: 0.85rem;">
                    ${label}
                </span>
            `;
        }

        html += `</div></div>`;
        return html;
    }

    /**
     * Attach event listeners after rendering.
     */
    _attachEventListeners() {
        // Template card clicks
        this.container.querySelectorAll('.template-card').forEach(card => {
            card.addEventListener('click', () => {
                this.toggleTemplate(card.dataset.templateId);
            });
        });

        // Size button clicks
        this.container.querySelectorAll('.size-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                this.toggleSize(btn.dataset.templateId, parseInt(btn.dataset.width), parseInt(btn.dataset.height));
            });
        });

        // Duration button clicks
        this.container.querySelectorAll('.duration-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                this.toggleDuration(btn.dataset.templateId, parseInt(btn.dataset.durationMs));
            });
        });

        // Remove size clicks
        this.container.querySelectorAll('.remove-size').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const [w, h] = btn.dataset.size.split('x').map(Number);
                this.toggleSize(btn.dataset.templateId, w, h);
            });
        });

        // Add custom size
        this.container.querySelectorAll('.add-custom-size-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const templateId = btn.dataset.templateId;
                const widthInput = this.container.querySelector(`.custom-width[data-template-id="${templateId}"]`);
                const heightInput = this.container.querySelector(`.custom-height[data-template-id="${templateId}"]`);
                this.addCustomSize(templateId, parseInt(widthInput.value), parseInt(heightInput.value));
                widthInput.value = '';
                heightInput.value = '';
            });
        });

        // Remove inventory size (individual)
        this.container.querySelectorAll('.remove-inventory-size').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                this.removeInventorySize(btn.dataset.size);
            });
        });

        // Clear all inventory sizes
        const clearInventoryBtn = this.container.querySelector('.clear-inventory-sizes-btn');
        if (clearInventoryBtn) {
            clearInventoryBtn.addEventListener('click', () => {
                this.clearInventorySizes();
            });
        }

        // Remove custom format
        this.container.querySelectorAll('.remove-custom').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                this.removeCustomFormat(parseInt(btn.dataset.index));
            });
        });

        // Add agent format button (dropdown-based)
        const addAgentFormatBtn = this.container.querySelector('#add-agent-format-btn');
        if (addAgentFormatBtn) {
            addAgentFormatBtn.addEventListener('click', () => {
                const select = this.container.querySelector('#agent-format-select');
                const selectedOption = select.options[select.selectedIndex];

                if (!selectedOption || !selectedOption.value) {
                    alert('Please select a format from the dropdown.');
                    return;
                }

                // Parse format data from data attribute
                let formatData;
                try {
                    formatData = JSON.parse(selectedOption.dataset.format);
                } catch (e) {
                    console.error('[FormatTemplatePicker] Invalid format data:', e);
                    alert('Invalid format selected. Please try again.');
                    return;
                }

                // Get optional parameters
                const width = parseInt(this.container.querySelector('#param-width')?.value) || null;
                const height = parseInt(this.container.querySelector('#param-height')?.value) || null;
                const durationMs = parseInt(this.container.querySelector('#param-duration')?.value) || null;

                this.addCustomFormat(
                    formatData.agent_url,
                    formatData.id,
                    width,
                    height,
                    durationMs
                );

                // Reset form
                select.value = '';
                const paramsSection = this.container.querySelector('#format-params-section');
                if (paramsSection) paramsSection.style.display = 'none';
                const widthInput = this.container.querySelector('#param-width');
                const heightInput = this.container.querySelector('#param-height');
                const durationSelect = this.container.querySelector('#param-duration');
                if (widthInput) widthInput.value = '';
                if (heightInput) heightInput.value = '';
                if (durationSelect) durationSelect.value = '';
            });
        }

        // Format select change handler - show/hide parameter inputs
        const formatSelect = this.container.querySelector('#agent-format-select');
        if (formatSelect) {
            formatSelect.addEventListener('change', () => {
                const selectedOption = formatSelect.options[formatSelect.selectedIndex];
                const paramsSection = this.container.querySelector('#format-params-section');
                const dimsParams = this.container.querySelector('#dims-params');
                const durationParams = this.container.querySelector('#duration-params');

                if (!selectedOption || !selectedOption.value) {
                    if (paramsSection) paramsSection.style.display = 'none';
                    return;
                }

                let formatData;
                try {
                    formatData = JSON.parse(selectedOption.dataset.format);
                } catch (e) {
                    console.error('[FormatTemplatePicker] Invalid format data:', e);
                    if (paramsSection) paramsSection.style.display = 'none';
                    return;
                }
                const formatType = formatData.type || '';

                // Determine which params apply to this format type
                const showDims = formatType === 'display' || formatType === 'video';
                const showDuration = formatType === 'video' || formatType === 'audio';

                // Only show params section if at least one param type is relevant
                if (paramsSection) {
                    paramsSection.style.display = (showDims || showDuration) ? 'block' : 'none';
                }

                // Show appropriate parameter inputs based on format type
                if (dimsParams) {
                    dimsParams.style.display = showDims ? 'block' : 'none';
                }
                if (durationParams) {
                    durationParams.style.display = showDuration ? 'block' : 'none';
                }
            });
        }
    }
}

// Export for use in other modules
if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        FORMAT_TEMPLATES,
        DEFAULT_CREATIVE_AGENT_URL,
        FormatTemplatePicker
    };
}
