"""Escape values that reach a log line from outside this process.

A value carrying a line break forges a second log entry: the reader — an
operator, or a log pipeline splitting on newlines — cannot tell the injected
line from one the application wrote. CodeQL reports this as CWE-117.

The escape set is wider than the obvious two. ``str.splitlines`` and the log
readers that matter treat ten characters as line boundaries, and
``urllib.parse`` strips only three of them, so stripping ``\\n`` and ``\\r``
leaves seven ways through.

Escaped rather than deleted, deliberately: a dropped character silently changes
the value an operator is reading, and an operator seeing ``\\n`` in a tenant
identifier learns something a silent deletion would have hidden.
"""

_LINE_BREAKING = "\n\r\v\f\x1c\x1d\x1e\x85  "
_LOG_SAFE = str.maketrans({c: c.encode("unicode_escape").decode("ascii") for c in _LINE_BREAKING})


def log_safe(value: object) -> str:
    """Return ``value`` as text that cannot span more than one log line.

    Accepts any object because call sites interpolate identifiers, exceptions
    and rejected configuration values alike; an exception's text is the case
    that matters most, since it can carry a remote response body verbatim.
    """
    return str(value).translate(_LOG_SAFE)
