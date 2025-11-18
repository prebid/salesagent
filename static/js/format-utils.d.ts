/**
 * TypeScript type definitions for format-utils.js
 *
 * Place this file alongside format-utils.js to provide IDE autocomplete and type checking.
 * Modern IDEs (VSCode, WebStorm) will automatically pick up these definitions.
 *
 * To enable type checking in JavaScript files, add this to the top of your .js file:
 * // @ts-check
 */

/**
 * Format identifier object (AdCP library schema)
 */
export interface FormatId {
    /** Format identifier string (e.g., "display_300x250") */
    id: string;
    /** Agent URL defining the format namespace */
    agent_url: string;
}

/**
 * AdCP Format object structure
 */
export interface Format {
    /** Format identifier - can be nested object (current) or string (legacy) */
    format_id: FormatId | string;
    /** Human-readable format name */
    name: string;
    /** Format type */
    type: 'display' | 'video' | 'audio' | 'native' | string;
    /** Optional format description */
    description?: string;
    /** Convenience field (duplicates format_id.agent_url) */
    agent_url?: string;
    /** Dimension string like "300x250" */
    dimensions?: string;
    /** Width in pixels */
    width?: number;
    /** Height in pixels */
    height?: number;
    /** Format category */
    category?: 'standard' | 'custom' | 'generative' | null;
    /** Whether this is a standard format */
    is_standard?: boolean | null;
}

/**
 * Extract format ID string from a format object, handling both nested and legacy structures.
 *
 * @param format - Format object from API
 * @returns Format ID string (e.g., "display_300x250")
 *
 * @example
 * ```javascript
 * // Nested structure (current)
 * const format = { format_id: { id: "display_300x250", agent_url: "https://..." } };
 * getFormatId(format); // "display_300x250"
 *
 * // Legacy string (backward compatible)
 * const oldFormat = { format_id: "display_300x250" };
 * getFormatId(oldFormat); // "display_300x250"
 * ```
 */
export function getFormatId(format: Format | null | undefined): string;

/**
 * Extract agent URL from a format object.
 *
 * @param format - Format object from API
 * @returns Agent URL or null if not available
 */
export function getFormatAgentUrl(format: Format | null | undefined): string | null;

/**
 * Create a FormatId object for API submission.
 *
 * @param id - Format ID string
 * @param agentUrl - Agent URL
 * @returns FormatId object
 */
export function createFormatId(id: string, agentUrl: string): FormatId;

/**
 * Search formats by query string, matching against multiple fields.
 *
 * @param formats - Array of format objects
 * @param query - Search query (case-insensitive)
 * @returns Filtered array of formats
 */
export function searchFormats(formats: Format[], query: string): Format[];

/**
 * Find a format by its ID string.
 *
 * @param formats - Array of format objects
 * @param formatIdString - Format ID to search for
 * @returns Format object or undefined if not found
 */
export function findFormatById(formats: Format[], formatIdString: string): Format | undefined;

/**
 * Get dimensions string from format (e.g., "300x250").
 *
 * @param format - Format object
 * @returns Dimensions string or null
 */
export function getFormatDimensions(format: Format | null | undefined): string | null;

/**
 * Validate that a format object has required fields.
 *
 * @param format - Format object to validate
 * @returns True if valid
 */
export function isValidFormat(format: Format | null | undefined): boolean;

/**
 * Log format object structure for debugging (with truncation for readability).
 *
 * @param format - Format to log
 * @param prefix - Optional prefix for log message
 */
export function logFormat(format: Format | null | undefined, prefix?: string): void;
