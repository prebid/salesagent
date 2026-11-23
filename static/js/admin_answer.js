// What the operator is shown for a failed admin JSON answer (src/admin/utils/operator_errors.py).
//
// A refusal answers {success: false, error, issues?, details?}: `error` is the refusal's
// fixed text, each of `issues` names a refused form field with what is wrong with it, and
// `details` are the lines a refusal adds beside its text. The text alone does not say which
// value to change, so every line is shown, one per line.
function adminErrorText(data) {
    const issues = (data.issues || []).map(issue => issue.text);
    return [data.error || data.message || 'Unknown error', ...issues, ...(data.details || [])].join('\n');
}
