/**
 * Format Utilities - Type-safe helpers for AdCP format handling
 *
 * This module provides defensive utilities for working with AdCP format objects
 * that may have either legacy (string) or current (nested object) format_id structure.
 *
 * Format Types (JSDoc for IDE autocomplete):
 * @typedef {Object} FormatId
 * @property {string} id - Format identifier (e.g., "display_300x250")
 * @property {string} agent_url - Agent URL defining the format namespace
 *
 * @typedef {Object} Format
 * @property {FormatId|string} format_id - Format identifier (nested object or legacy string)
 * @property {string} name - Human-readable format name
 * @property {string} type - Format type (display, video, audio, native)
 * @property {string} [description] - Optional format description
 * @property {string} [agent_url] - Convenience field (duplicates format_id.agent_url)
 * @property {string} [dimensions] - Dimension string like "300x250"
 * @property {number} [width] - Width in pixels
 * @property {number} [height] - Height in pixels
 */

/**
 * Extract format ID string from a format object, handling both nested and legacy structures.
 *
 * This is the canonical way to get a format's ID string for use in:
 * - Set/Map keys
 * - DOM element IDs
 * - API parameters
 * - Comparisons
 *
 * @param {Format} format - Format object from API
 * @returns {string} Format ID string (e.g., "display_300x250")
 *
 * @example
 * // Nested structure (current)
 * const format = { format_id: { id: "display_300x250", agent_url: "https://..." } };
 * getFormatId(format); // "display_300x250"
 *
 * // Legacy string (backward compatible)
 * const oldFormat = { format_id: "display_300x250" };
 * getFormatId(oldFormat); // "display_300x250"
 */
function getFormatId(format) {
    if (!format) {
        console.warn('[getFormatId] Null/undefined format object');
        return '';
    }

    const formatId = format.format_id;

    // Nested object (current structure)
    if (formatId && typeof formatId === 'object' && formatId.id) {
        return formatId.id;
    }

    // Legacy string or object without .id
    if (typeof formatId === 'string') {
        return formatId;
    }

    // Fallback: try to extract from object
    if (formatId && formatId.id) {
        return formatId.id;
    }

    console.warn('[getFormatId] Could not extract format_id from:', format);
    return String(formatId || '');
}

/**
 * Extract agent URL from a format object.
 *
 * @param {Format} format - Format object from API
 * @returns {string|null} Agent URL or null if not available
 *
 * @example
 * const format = { format_id: { id: "display_300x250", agent_url: "https://creative.example.com" } };
 * getFormatAgentUrl(format); // "https://creative.example.com"
 */
function getFormatAgentUrl(format) {
    if (!format) return null;

    // Check convenience field first
    if (format.agent_url) {
        return format.agent_url;
    }

    // Check nested format_id object
    const formatId = format.format_id;
    if (formatId && typeof formatId === 'object' && formatId.agent_url) {
        return formatId.agent_url;
    }

    return null;
}

/**
 * Create a FormatId object for API submission.
 *
 * @param {string} id - Format ID string
 * @param {string} agentUrl - Agent URL
 * @returns {FormatId} FormatId object
 *
 * @example
 * const formatId = createFormatId("display_300x250", "https://creative.example.com");
 * // { id: "display_300x250", agent_url: "https://creative.example.com" }
 */
function createFormatId(id, agentUrl) {
    return {
        id: id,
        agent_url: agentUrl
    };
}

/**
 * Search formats by query string, matching against multiple fields.
 *
 * @param {Format[]} formats - Array of format objects
 * @param {string} query - Search query (case-insensitive)
 * @returns {Format[]} Filtered array of formats
 *
 * @example
 * const results = searchFormats(allFormats, "300x250");
 */
function searchFormats(formats, query) {
    if (!query || !query.trim()) {
        return formats;
    }

    const lowerQuery = query.toLowerCase().trim();

    return formats.filter(fmt => {
        const formatId = getFormatId(fmt).toLowerCase();
        const name = (fmt.name || '').toLowerCase();
        const description = (fmt.description || '').toLowerCase();
        const type = (fmt.type || '').toLowerCase();
        const dimensions = (fmt.dimensions || '').toLowerCase();

        return formatId.includes(lowerQuery) ||
               name.includes(lowerQuery) ||
               description.includes(lowerQuery) ||
               type.includes(lowerQuery) ||
               dimensions.includes(lowerQuery);
    });
}

/**
 * Find a format by its ID string.
 *
 * @param {Format[]} formats - Array of format objects
 * @param {string} formatIdString - Format ID to search for
 * @returns {Format|undefined} Format object or undefined if not found
 */
function findFormatById(formats, formatIdString) {
    return formats.find(f => getFormatId(f) === formatIdString);
}

/**
 * Get dimensions string from format (e.g., "300x250").
 *
 * @param {Format} format - Format object
 * @returns {string|null} Dimensions string or null
 */
function getFormatDimensions(format) {
    if (!format) return null;

    // Check dimensions field first
    if (format.dimensions) {
        return format.dimensions;
    }

    // Fallback to width/height
    if (format.width && format.height) {
        return `${format.width}x${format.height}`;
    }

    return null;
}

/**
 * Validate that a format object has required fields.
 *
 * @param {Format} format - Format object to validate
 * @returns {boolean} True if valid
 */
function isValidFormat(format) {
    if (!format) return false;
    if (!format.format_id) return false;
    if (!format.name) return false;

    // Verify we can extract an ID
    const id = getFormatId(format);
    return !!id && id.length > 0;
}

/**
 * Log format object structure for debugging (with truncation for readability).
 *
 * @param {Format} format - Format to log
 * @param {string} [prefix] - Optional prefix for log message
 */
function logFormat(format, prefix = '') {
    const id = getFormatId(format);
    const agentUrl = getFormatAgentUrl(format);
    const dims = getFormatDimensions(format);

    console.log(`${prefix}Format:`, {
        id,
        name: format?.name,
        type: format?.type,
        agentUrl,
        dimensions: dims,
        format_id_structure: format?.format_id
    });
}

// Export for use in other modules (if needed)
if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        getFormatId,
        getFormatAgentUrl,
        createFormatId,
        searchFormats,
        findFormatById,
        getFormatDimensions,
        isValidFormat,
        logFormat
    };
}
