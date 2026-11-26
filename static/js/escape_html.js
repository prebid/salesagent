/**
 * The one way the admin UI puts a value into HTML markup it builds as a string.
 *
 * Any value that reaches innerHTML or insertAdjacentHTML through a template literal goes
 * through escapeHtml(), whatever its source: names, descriptions, domains, ad server
 * names, creative fields and error messages all come from tenant users, buyers, ad servers
 * or publishers' files. Prefer textContent when building a single text node.
 *
 * escapeHtml() makes a value safe as element text and inside a QUOTED attribute. It does
 * not make a URL safe: use safeUrl() for an href or src in markup, and checkedUrl() for a
 * URL set through the DOM (el.href = ..., window.open(...)). It does not make a value safe
 * inside an inline event handler (onclick="..."): use jsArg() there.
 *
 * tests/unit/test_architecture_no_unescaped_html_sinks.py refuses a new interpolation
 * into an HTML template literal that does not go through one of these.
 */
(function (root) {
    'use strict';

    var ENTITIES = {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;',
        '`': '&#96;'
    };

    function escapeHtml(value) {
        if (value === null || value === undefined) {
            return '';
        }
        return String(value).replace(/[&<>"'`]/g, function (ch) {
            return ENTITIES[ch];
        });
    }

    // Relative URLs and these schemes only; anything else (javascript:, data:, vbscript:)
    // becomes '#'. The scheme is read after stripping the characters a browser ignores.
    var SAFE_SCHEME = /^(?:https?|mailto):/i;
    var HAS_SCHEME = /^[a-z][a-z0-9+.-]*:/i;

    // A URL to set through the DOM (el.href = ..., window.open(...)): unchanged if relative
    // or http(s)/mailto, else '#'. Not escaped, since the DOM takes it as a value.
    function checkedUrl(value) {
        if (value === null || value === undefined) {
            return '';
        }
        var url = String(value).trim();
        // eslint-disable-next-line no-control-regex
        var probe = url.replace(/[\u0000- \u007f]/g, '');
        if (HAS_SCHEME.test(probe) && !SAFE_SCHEME.test(probe)) {
            return '#';
        }
        return url;
    }

    // The same URL, escaped for an attribute in markup built as a string.
    function safeUrl(value) {
        return escapeHtml(checkedUrl(value));
    }

    // A value as a JavaScript literal inside a double-quoted inline handler, for example
    // onclick="remove(${jsArg(item.id)})". The browser decodes the attribute's entities
    // before it runs the handler, so escapeHtml() alone would let a quote end the string.
    // The JSON carries no quote or angle bracket of its own (\u0027, \u003c, \u003e), so a
    // stray quote placed around it by mistake cannot be closed by the value.
    function jsArg(value) {
        var json = JSON.stringify(value === undefined ? null : value);
        if (json === undefined) {
            // A function or Symbol has no JSON form (a BigInt makes stringify throw itself).
            throw new TypeError('jsArg: ' + typeof value + ' cannot be written as a JavaScript literal');
        }
        return escapeHtml(json.replace(/'/g, '\\u0027').replace(/</g, '\\u003c').replace(/>/g, '\\u003e'));
    }

    root.escapeHtml = escapeHtml;
    root.safeUrl = safeUrl;
    root.checkedUrl = checkedUrl;
    root.jsArg = jsArg;
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = { escapeHtml: escapeHtml, safeUrl: safeUrl, checkedUrl: checkedUrl, jsArg: jsArg };
    }
})(typeof window !== 'undefined' ? window : globalThis);
