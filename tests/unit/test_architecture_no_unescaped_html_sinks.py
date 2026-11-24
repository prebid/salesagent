"""Guard: the admin UI never builds HTML from an unescaped value.

The admin UI builds much of its markup as JavaScript template literals assigned to
``innerHTML``, and values in them came from tenant users, buyers, ad servers and
publishers' files. One of them, a publisher's display name, let a caller run script in
an admin's session. ``static/js/escape_html.js`` is the one helper for this; this guard
refuses markup that does not use it.

Four rules, each a shrink-only count per file in a baseline dict below. Every baseline is
empty: the sweep that added this guard converted every site it found.

1. **Template literals.** In a JavaScript template literal that contains an HTML tag or
   is written straight into a sink (in ``templates/`` and ``static/js/``), every ``${...}``
   must be one of:

   * ``escapeHtml(...)`` -- element text or a quoted attribute;
   * ``safeUrl(...)`` -- required where the value starts an ``href``/``src``/``action``;
   * ``jsArg(...)`` -- required inside an inline handler (``onclick="..."``), where the
     browser decodes entities before running the code;
   * a nested template literal (scanned on its own), such as ``${rows.map(r => `...`)}``;
   * a ternary choosing between two string literals;
   * a variable whose name ends in ``Html``, holding markup built under these rules.

2. **Sink operands.** A value written into ``innerHTML``/``outerHTML``/
   ``insertAdjacentHTML`` outside a template literal (``'<b>' + name``, or a bare
   variable) must be a string literal, a helper call, or a ``...Html`` variable.

3. **Jinja inline handlers.** A ``{{ ... }}`` inside an ``on...="..."`` attribute must be
   ``|tojson|forceescape``. Autoescape turns ``'`` into ``&#39;``, which the browser turns
   back into ``'`` before the handler runs, so ``onclick="f('{{ name }}')"`` lets a name
   end the string.

4. **Escaping turned off.** ``|safe``, ``Markup(`` and ``{% autoescape false %}`` appear
   only where ``_SAFE_ALLOWED`` says why.

The scanner is a small tokenizer, not a JavaScript parser: it knows strings, comments,
regex literals and nested template literals, which is what these files contain. It
decides a ``/`` opens a regex from the token before it, as a JavaScript lexer does. A
``...Html`` name is trusted by convention, so a reviewer checks how such a variable is built.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import pytest

from tests.unit._architecture_helpers import repo_root

ROOT = repo_root()
#: The landing pages are served on the same origin as the admin UI, so they are scanned too.
SCAN_GLOBS = ("templates/**/*.html", "src/landing/templates/**/*.html", "static/*.js", "static/js/**/*.js")
HELPER = "static/js/escape_html.js"

_TAG = re.compile(r"<[a-zA-Z/!]")
_HANDLER_OPEN = re.compile(r"""\son[a-z]+\s*=\s*(["'])""", re.I)
#: Values the deployment sets, never a user: the reverse-proxy prefix (CLAUDE.md pattern 6),
#: assigned once, as ``window.scriptRoot`` in templates/base.html. Trusted only because that is
#: its one binding; ``test_the_trusted_prefix_is_bound_only_in_base_html`` refuses any other.
_TRUSTED_NAMES = {"scriptRoot"}
_TRUSTED_DEFINITION = ("templates/base.html", "window.scriptRoot = {{ request.script_root|tojson }} || '';")
_URL_ATTRIBUTES = "href|src|srcset|action|formaction|data|xlink:href|poster|background|ping|values|to|from"
_URL_ATTR_OPEN = re.compile(r"\s(?:" + _URL_ATTRIBUTES + r")\s*=\s*([\"'])", re.I)

#: Unescaped template-literal interpolations left, per file. Shrink-only: fix a site and
#: lower its count (the stale-entry test says so). An entry here needs a FIXME at the site.
_BASELINE: dict[str, int] = {}

#: Jinja expressions in inline handlers that are not ``|tojson|forceescape``, per file.
_JINJA_HANDLER_BASELINE: dict[str, int] = {}

#: Values written into a sink outside a template literal (concatenation, a bare variable), per file.
_SINK_OPERAND_BASELINE: dict[str, int] = {}

#: Jinja values in ``<script>`` blocks that are not ``|tojson``, per file.
_JINJA_SCRIPT_BASELINE: dict[str, int] = {}

#: Jinja values that start a URL attribute unchecked, per file.
_JINJA_URL_BASELINE: dict[str, int] = {}

#: Jinja values written as an unquoted attribute value, per file.
_JINJA_UNQUOTED_BASELINE: dict[str, int] = {}

#: ``|tojson`` inside a double-quoted attribute without ``|forceescape``, per file.
_TOJSON_ATTRIBUTE_BASELINE: dict[str, int] = {}

#: ``|safe`` / ``Markup(`` / ``autoescape false`` sites, per file, with the reason each is safe.
_SAFE_ALLOWED: dict[str, int] = {}


class _Literal:
    """One template literal: its text with each ``${...}`` replaced by ``\\0``, and the expressions."""

    def __init__(self, start: int) -> None:
        self.start = start  # offset of the opening backtick
        self.text: list[str] = []
        self.exprs: list[tuple[str, int, str]] = []  # (expression, offset in source, text before it)


def _skip_string(src: str, i: int) -> int:
    quote = src[i]
    i += 1
    while i < len(src) and src[i] != quote and src[i] != "\n":
        i += 2 if src[i] == "\\" else 1
    return i + 1


_REGEX_PRECEDERS = set("(,=:[!&|?{};+-*%<>~^")


def _regex_starts_at(src: str, i: int) -> bool:
    """Whether the ``/`` at ``src[i]`` opens a regex literal rather than dividing."""
    if src.startswith(("//", "/*"), i):
        return False
    j = i - 1
    while j >= 0 and src[j].isspace():
        j -= 1
    if j < 0 or src[j] in _REGEX_PRECEDERS:
        return True
    word = re.search(r"[A-Za-z_$][\w$]*$", src[: j + 1])
    return bool(word) and word.group(0) in {"return", "typeof", "case", "in", "of", "void"}


def _skip_regex(src: str, i: int) -> int:
    """The index after the regex literal opening at ``src[i]`` (its body and flags)."""
    j, in_class = i + 1, False
    while j < len(src) and src[j] != "\n":
        if src[j] == "\\":
            j += 2
            continue
        if src[j] == "[":
            in_class = True
        elif src[j] == "]":
            in_class = False
        elif src[j] == "/" and not in_class:
            j += 1
            while j < len(src) and src[j].isalpha():
                j += 1
            return j
        j += 1
    return i + 1  # not a regex after all: step over the slash


def _parse_template(src: str, i: int, out: list[_Literal]) -> int:
    """Parse the template literal opening at ``src[i]``; append it and every nested one to *out*."""
    literal = _Literal(i)
    out.append(literal)
    i += 1
    while i < len(src):
        ch = src[i]
        if ch == "\\":
            literal.text.append(src[i : i + 2])
            i += 2
        elif ch == "`":
            return i + 1
        elif src.startswith("${", i):
            start = j = i + 2
            depth = 1
            while j < len(src) and depth:
                if src[j] == "`":
                    j = _parse_template(src, j, out)
                    continue
                if src[j] in "'\"":
                    j = _skip_string(src, j)
                    continue
                if src[j] == "/" and _regex_starts_at(src, j):
                    j = _skip_regex(src, j)
                    continue
                depth += {"{": 1, "}": -1}.get(src[j], 0)
                j += 1
            literal.exprs.append((src[start : j - 1], start, "".join(literal.text)))
            literal.text.append("\0")
            i = j
        else:
            literal.text.append(ch)
            i += 1
    return i


def _comment_spans(src: str) -> list[tuple[int, int]]:
    """(start, end) of every ``//`` and ``/* */`` comment, found the way :func:`template_literals` walks."""
    spans, i = [], 0
    while i < len(src):
        ch = src[i]
        if ch == "`":
            i = _parse_template(src, i, [])
        elif ch in "'\"":
            i = _skip_string(src, i)
        elif src.startswith("//", i) and (i == 0 or src[i - 1] not in ":\\"):
            end = src.find("\n", i)
            end = len(src) if end < 0 else end
            spans.append((i, end))
            i = end
        elif src.startswith("/*", i):
            end = src.find("*/", i)
            end = len(src) if end < 0 else end + 2
            spans.append((i, end))
            i = end
        elif ch == "/" and _regex_starts_at(src, i):
            i = _skip_regex(src, i)
        else:
            i += 1
    return spans


def template_literals(src: str) -> list[_Literal]:
    out: list[_Literal] = []
    i = 0
    while i < len(src):
        ch = src[i]
        if ch == "`":
            i = _parse_template(src, i, out)
        elif ch in "'\"":
            i = _skip_string(src, i)
        elif src.startswith("//", i) and (i == 0 or src[i - 1] not in ":\\"):
            end = src.find("\n", i)
            i = len(src) if end < 0 else end
        elif src.startswith("/*", i):
            end = src.find("*/", i)
            i = len(src) if end < 0 else end + 2
        elif ch == "/" and _regex_starts_at(src, i):
            i = _skip_regex(src, i)
        else:
            i += 1
    return out


_HELPERS = ("escapeHtml", "safeUrl", "jsArg")
_IDENTIFIER = re.compile(r"[A-Za-z_$][\w$]*")
_HTML_NAME = re.compile(r"^[A-Za-z_$][\w$]*Html$")
_STRING_LITERAL = re.compile(r"""^(?:'(?:[^'\\\n]|\\.)*'|"(?:[^"\\\n]|\\.)*")$""")
_UNQUOTED_ATTRIBUTE = re.compile(r"=\s*$")
_SRCDOC_OPEN = re.compile(r"""\ssrcdoc\s*=\s*(["'])""", re.I)

#: Where a value is written as markup. Each match ends where the written value starts.
_SINKS = re.compile(
    r"(?:\.|\[\s*['\"])(?:inner|outer)HTML(?:['\"]\s*\])?\s*\+?=(?!=)\s*"
    r"|\.srcdoc\s*=(?!=)\s*"
    r"|\bdocument\s*\.\s*write(?:ln)?\(\s*"
    r"|\bcreateContextualFragment\(\s*"
    r"|\.html\(\s*(?=\S)(?!\))"
)
_INSERT_ADJACENT = re.compile(r"\binsertAdjacentHTML\(\s*")


def _continues(src: str, start: int, i: int) -> bool:
    """Whether the expression goes on past the line end at ``src[i]`` (an operator ends or starts a line)."""
    if src[i] == ";":
        return False
    ends = src[start:i].rstrip().endswith(("+", "?", ":", "(", ","))
    return ends or src[i:].lstrip().startswith(("+", ".", "?", ":"))


def _expression_from(src: str, i: int) -> str:
    """The expression starting at ``src[i]``: up to a ``;``, a closing bracket, or a line end, at depth 0.

    Strings, regex literals and template literals are stepped over whole, so a ``;`` or a
    newline inside one does not end the expression.
    """
    start, depth = i, 0
    while i < len(src):
        ch = src[i]
        if ch == "`":
            i = _parse_template(src, i, [])
            continue
        if ch in "'\"":
            i = _skip_string(src, i)
            continue
        if ch == "/" and _regex_starts_at(src, i):
            i = _skip_regex(src, i)
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            if depth == 0:
                break
            depth -= 1
        elif depth == 0 and ch in ";\n" and not _continues(src, start, i):
            break
        i += 1
    return src[start:i]


def _matching_close(src: str, i: int) -> int:
    """The index of the bracket closing the one at ``src[i]``, stepping over strings and literals; -1 if none."""
    depth = 0
    while i < len(src):
        ch = src[i]
        if ch == "`":
            i = _parse_template(src, i, [])
            continue
        if ch in "'\"":
            i = _skip_string(src, i)
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _strip_parens(expr: str) -> tuple[str, int]:
    """*expr* without surrounding whitespace and wrapping parentheses, and how far its start moved."""
    shift = len(expr) - len(expr.lstrip())
    expr = expr.strip()
    while expr.startswith("(") and _matching_close(expr, 0) == len(expr) - 1:
        inner = expr[1:-1]
        shift += 1 + len(inner) - len(inner.lstrip())
        expr = inner.strip()
    return expr, shift


def _single_call(expr: str, names: tuple[str, ...]) -> bool:
    """Whether *expr* is exactly one call to one of *names*: ``escapeHtml(a)``, never ``escapeHtml(a) + b``."""
    match = _IDENTIFIER.match(expr)
    if not match or match.group(0) not in names or expr[match.end() : match.end() + 1] != "(":
        return False
    return _matching_close(expr, match.end()) == len(expr) - 1


def _split_top(expr: str, separators: tuple[str, ...]) -> list[tuple[int, str]]:
    """(offset, part) for *expr* split on any of *separators* outside strings, literals and brackets."""
    parts, depth, start, i = [], 0, 0, 0
    while i < len(expr):
        ch = expr[i]
        if ch == "`":
            i = _parse_template(expr, i, [])
            continue
        if ch in "'\"":
            i = _skip_string(expr, i)
            continue
        if ch == "/" and _regex_starts_at(expr, i):
            i = _skip_regex(expr, i)
            continue
        depth += {"(": 1, "[": 1, "{": 1, ")": -1, "]": -1, "}": -1}.get(ch, 0)
        if depth == 0:
            for sep in separators:
                if (
                    expr.startswith(sep, i)
                    and not expr.startswith(sep * 2, i)
                    and not expr.startswith(("+=", "?."), i)
                    and not (i > 0 and expr[i - 1] == sep[0])
                    and not (sep == "?" and expr.startswith("?.", i))
                ):
                    parts.append((start, expr[start:i]))
                    start = i + len(sep)
                    i = start - 1
                    break
        i += 1
    parts.append((start, expr[start:]))
    return parts


def _depth(expr: str, index: int) -> int:
    """How many brackets are open at ``expr[index]``, outside strings and literals."""
    depth, i = 0, 0
    while i < index:
        ch = expr[i]
        if ch == "`":
            i = _parse_template(expr, i, [])
            continue
        if ch in "'\"":
            i = _skip_string(expr, i)
            continue
        depth += {"(": 1, "[": 1, "{": 1, ")": -1, "]": -1, "}": -1}.get(ch, 0)
        i += 1
    return depth


def _ternary(expr: str) -> list[tuple[int, str]] | None:
    """(offset, branch) for the two branches of a top-level ``c ? a : b``, or None."""
    question = _split_top(expr, ("?",))
    if len(question) < 2:
        return None
    first = question[1][0]
    branches = _split_top(expr[first:], (":",))
    if len(branches) < 2:
        return None
    (a_off, a), (b_off, _) = branches[0], branches[1]
    return [(first + a_off, a), (first + b_off, expr[first + b_off :])]


class _Markup:
    """What one file builds as markup, and where it writes URLs.

    It follows each value written into a sink: string literals, helper calls, template
    literals (whose start it records, with the markup text before them, so they are
    scanned as markup in context), ``+``, ternary, ``||``/``&&`` and ``.map().join()``
    combinations, calls to functions defined in the file (through their returns), a read
    of an element's own ``innerHTML``, and ``...Html`` names.

    The markup before a value decides what it may be: inside an inline handler only a
    whole ``jsArg()``, at the start of a URL attribute only a whole ``safeUrl()``, in an
    unquoted attribute or ``srcdoc`` nothing. That context is carried across ``+``
    pieces and into template literals, so a handler split over a concatenation is still a
    handler.

    A ``...Html`` name is trusted only if every assignment to it and every ``push`` onto
    it is itself safe, and never where it is a function parameter (unless it is the
    parameter of a ``.map`` over a trusted name).
    """

    def __init__(self, src: str) -> None:
        self.src = src
        self.literal_starts: set[int] = set()
        self.literal_bindings: dict[int, set[str]] = {}  # .map parameters trusted inside a literal
        self.literal_prefix: dict[int, str] = {}  # markup written before a literal, in its piece chain
        self._checking: set[str] = set()
        self._bound: set[str] = set()
        self._comments = _comment_spans(src)

    def in_comment(self, index: int) -> bool:
        return any(start <= index < end for start, end in self._comments)

    def _definitions(self, name: str) -> list[tuple[int, str]]:
        """(offset, value) for each ``name = value``, ``name += value`` and ``name.push(value)``."""
        pattern = re.compile(r"(?<![\w$.])" + re.escape(name) + r"\s*(?:\+?=(?!=)|\.push\()")
        return [(m.end(), _expression_from(self.src, m.end())) for m in pattern.finditer(self.src)]

    def _function_returns(self, name: str) -> list[tuple[int, str]] | None:
        """(offset, value) for each return of the function or method *name* defined here; None if not defined."""
        pattern = re.compile(
            r"(?:function\s+" + re.escape(name) + r"|^[ \t]*(?:async\s+)?" + re.escape(name) + r")\s*\([^)]*\)\s*\{",
            re.M,
        )
        match = pattern.search(self.src)
        if match is None:
            return None
        start, end = match.end(), _matching_close(self.src, match.end() - 1)
        return self._own_returns(start, end)

    def _own_returns(self, start: int, end: int) -> list[tuple[int, str]]:
        """(offset, value) for each ``return`` in ``src[start:end]`` outside a nested function's body."""
        body = self.src[start:end]
        nested = []
        for opener in re.finditer(r"=>\s*\{|\bfunction\b[^{(]*\([^)]*\)\s*\{", body):
            close = _matching_close(body, opener.end() - 1)
            nested.append((opener.end(), close))
        return [
            (start + m.end(), _expression_from(self.src, start + m.end()))
            for m in re.finditer(r"\breturn\b", body)
            if not any(a <= m.start() < b for a, b in nested)
        ]

    def _is_parameter(self, name: str, offset: int) -> bool:
        """Whether *name* at *offset* is a parameter of a function whose body holds *offset*."""
        headers = re.finditer(
            r"(?:\bfunction\b[^(]*|^[ \t]*(?:async\s+)?(?!(?:if|for|while|switch|catch|with|return)\b)[\w$]+\s*)"
            r"\(([^()]*)\)\s*\{|\(([^()]*)\)\s*=>\s*\{?|([\w$]+)\s*=>\s*\{?",
            self.src,
            re.M,
        )
        for header in headers:
            params = header.group(1) or header.group(2) or header.group(3) or ""
            if name not in _IDENTIFIER.findall(params) or header.end() > offset:
                continue
            if self.src[header.end() - 1] == "{":
                end = _matching_close(self.src, header.end() - 1)
            else:
                end = header.end() + len(_expression_from(self.src, header.end()))
            if offset < end:
                return True
        return False

    def _trusted_name(self, name: str, offset: int | None = None) -> bool:
        if name in self._bound:
            return True  # a .map parameter over a trusted name
        if offset is not None and self._is_parameter(name, offset):
            return False  # whatever a caller passed: a same-named variable elsewhere says nothing
        if name in self._checking:
            return True  # decided by its other definitions
        self._checking.add(name)
        try:
            definitions = self._definitions(name)
            return bool(definitions) and all(self.value_is_safe(value, offset) for offset, value in definitions)
        finally:
            self._checking.discard(name)

    def _all_safe(self, parts: list[tuple[int, str]], offset: int, before: str = "") -> bool:
        return all(self.value_is_safe(part, offset + part_offset, before) for part_offset, part in parts)

    def _concatenation_is_safe(self, parts: list[tuple[int, str]], offset: int, before: str) -> bool:
        """Each ``+`` piece checked in the markup context the pieces before it built."""
        safe = True
        for part_offset, part in parts:
            safe = self.value_is_safe(part, offset + part_offset, before) and safe
            before += _piece_text(part)
        return safe

    def value_is_safe(self, raw: str, offset: int, before: str = "") -> bool:  # noqa: PLR0911, PLR0912, C901 - one case per JavaScript form
        """Whether *raw*, found at *offset* and written as markup after *before*, carries only helper-made markup."""
        expr, shift = _strip_parens(raw)
        offset += shift
        if not expr or expr in ("[]", "''", '""', "``", "null", "undefined"):
            return True
        if _STRING_LITERAL.match(expr):
            return True
        if expr.startswith("`") and _parse_template(expr, 0, []) == len(expr):
            self.literal_starts.add(offset)
            self.literal_bindings.setdefault(offset, set()).update(self._bound)
            self.literal_prefix[offset] = before
            return True
        if (branches := _ternary(expr)) is not None:
            return self._all_safe(branches, offset, before)
        if len(parts := _split_top(expr, ("||",))) > 1:
            return self._all_safe(parts, offset, before)
        if len(parts := _split_top(expr, ("&&",))) > 1:
            return self._all_safe(parts[-1:], offset, before)
        if len(parts := _split_top(expr, ("+",))) > 1:
            return self._concatenation_is_safe(parts, offset, before)
        if not _context_allows(expr, before):
            return False
        if expr.startswith("[") and _matching_close(expr, 0) == len(expr) - 1:
            return self._all_safe(_split_top(expr[1:-1], (",",)), offset + 1)
        if expr in _TRUSTED_NAMES or _single_call(expr, _HELPERS):
            return True
        if re.fullmatch(r"""[\w$.\[\] -]+\.innerHTML(?:\.replace\(\s*'[^']*'\s*,\s*'[^']*'\s*\))?""", expr):
            return True  # an element's own markup, put back (with one literal swapped)
        if _HTML_NAME.match(expr):
            return self._trusted_name(expr, offset)
        join = re.match(r"^([\w$.]+)\.join\(", expr)
        if join and _matching_close(expr, join.end() - 1) == len(expr) - 1:
            separator = expr[join.end() : -1]
            return self.value_is_safe(join.group(1), offset) and self.value_is_safe(separator, offset + join.end())
        mapped = next((m for m in re.finditer(r"\??\.map\(", expr) if _depth(expr, m.start()) == 0), None)
        if mapped:
            close = _matching_close(expr, mapped.end() - 1)
            tail = expr[close + 1 :].strip()
            join_tail = re.fullmatch(r"""\.join\(\s*('[^']*'|"[^"]*")?\s*\)""", tail)
            if close > 0 and (join_tail or not tail):
                receiver, _ = _strip_parens(expr[: mapped.start()])
                return self._map_is_safe(receiver, expr[mapped.end() : close], offset + mapped.end())
        call = re.match(r"^(?:this\.)?([A-Za-z_$][\w$]*)\(", expr)
        if call and _matching_close(expr, call.end() - 1) == len(expr) - 1:
            if call.group(1) in self._checking:
                return True  # a recursive call: the function's other returns decide
            returns = self._function_returns(call.group(1))
            if returns is not None:
                self._checking.add(call.group(1))
                try:
                    return bool(returns) and all(self.value_is_safe(value, off) for off, value in returns)
                finally:
                    self._checking.discard(call.group(1))
        return False

    def _map_is_safe(self, receiver: str, callback: str, offset: int) -> bool:
        """Whether ``receiver.map(callback)`` yields only safe markup."""
        if callback.strip() in _HELPERS:
            return True  # .map(escapeHtml)
        params, arrow, _ = callback.partition("=>")
        if not arrow:
            return False
        body_offset = offset + len(params) + 2
        body = callback[len(params) + 2 :]
        bound = set()
        if _HTML_NAME.match(receiver) and self._trusted_name(receiver):
            bound = set(_IDENTIFIER.findall(params.split(",")[0]))
        self._bound |= bound
        try:
            stripped, shift = _strip_parens(body)
            if stripped.startswith("{"):
                start = body_offset + shift + 1
                returns = self._own_returns(start, start + len(stripped) - 2)
                return bool(returns) and all(self.value_is_safe(value, off) for off, value in returns)
            return self.value_is_safe(body, body_offset)
        finally:
            self._bound -= bound

    def _matches(self, pattern: re.Pattern[str]) -> list[re.Match[str]]:
        return [m for m in pattern.finditer(self.src) if not self.in_comment(m.start())]

    def _argument(self, start: int, index: int) -> tuple[int, str] | None:
        """(offset, text) of argument *index* of the call whose arguments start at *start*."""
        arguments = _split_top(_expression_from(self.src, start), (",",))
        if index >= len(arguments):
            return None
        offset = start + arguments[index][0]
        return offset, arguments[index][1]

    def sink_values(self) -> list[tuple[int, str]]:
        """(offset, value) for every value written into a markup sink."""
        out = [(m.end(), _expression_from(self.src, m.end())) for m in self._matches(_SINKS)]
        for match in self._matches(_INSERT_ADJACENT):
            if (argument := self._argument(match.end(), 1)) is not None:
                out.append(argument)
        for match in self._matches(_JQUERY_INSERT):
            argument = (match.end(), _expression_from(self.src, match.end()))
            if _holds_markup_literal(argument[1]):
                out.append(argument)
        for match in self._matches(_SETTER_SINK):
            if (argument := self._argument(match.end(), 0)) is not None:
                out.append(argument)
        return out

    def url_values(self) -> list[tuple[int, str]]:
        """(offset, value) for every value written as a URL through the DOM."""
        out = [(m.end(), _expression_from(self.src, m.end())) for m in self._matches(_URL_WRITES)]
        for match in self._matches(_URL_CALLS):
            if (argument := self._argument(match.end(), 0)) is not None:
                out.append(argument)
        for match in self._matches(_URL_SETTERS):
            if (argument := self._argument(match.end(), 0)) is not None:
                out.append(argument)
        return out

    def refused_writes(self) -> list[tuple[int, str]]:
        """(offset, value) for a non-literal value set as an event handler or srcdoc attribute."""
        out = []
        for match in self._matches(_HANDLER_SETTERS):
            argument = self._argument(match.end(), 0)
            if argument is not None and not _STRING_LITERAL.match(argument[1].strip()):
                out.append(argument)
        return out


#: jQuery methods that parse a string argument as markup. Only a call whose argument holds a
#: markup literal is a sink: the same names on DOM elements insert text.
_JQUERY_INSERT = re.compile(r"(?:\.(?:append|prepend|after|before|replaceWith)|(?<![\w$.])(?:\$|jQuery))\(\s*")
#: Sinks whose first argument is markup.
_SETTER_SINK = re.compile(
    r"\bsetHTMLUnsafe\(\s*|\.attr\(\s*['\"]srcdoc['\"]\s*,\s*|(?<![\w$])(?:inner|outer)HTML\s*:\s*(?=\S)"
)
#: Property and variable writes that navigate or load a URL.
_URL_WRITES = re.compile(
    r"(?:\.(?:href|src|action|formAction)|(?<![\w$.])(?:(?:window|document|self|top)\.)?location)\s*=(?!=)\s*"
)
#: Calls whose first argument is a URL to navigate to or open.
_URL_CALLS = re.compile(r"\blocation\.(?:assign|replace)\(\s*|\bwindow\.open\(\s*")
#: Attribute setters for URL attributes; the value is the second argument, so these match up to it.
_URL_SETTERS = re.compile(
    r"""(?:\.setAttribute|\.attr)\(\s*['"](?:href|src|action|formaction|xlink:href|poster|data)['"]\s*,\s*"""
)
#: Attribute setters for event handlers and srcdoc, which take no computed value at all.
_HANDLER_SETTERS = re.compile(r"""(?:\.setAttribute|\.attr)\(\s*['"](?:on[a-z]+|srcdoc)['"]\s*,\s*""")
#: Calls whose result is a URL safe to load: the helper, and a blob: URL for a local file.
_URL_HELPERS = ("checkedUrl", "safeUrl")


def _holds_markup_literal(expr: str) -> bool:
    return bool(re.search(r"""['"`]\s*<[a-zA-Z!/]""", expr))


def _piece_text(piece: str) -> str:
    """The markup a ``+`` piece contributes to the context after it: a literal's text, else a placeholder."""
    piece, _ = _strip_parens(piece)
    if _STRING_LITERAL.match(piece):
        return piece[1:-1].replace("\\'", "'").replace('\\"', '"')
    if piece.startswith("`") and _parse_template(piece, 0, []) == len(piece):
        literals: list[_Literal] = []
        _parse_template(piece, 0, literals)
        return "".join(literals[0].text)
    return "\0"


def _open_attribute(pattern: re.Pattern[str], before: str) -> re.Match[str] | None:
    """The last attribute matching *pattern* that *before* opens and does not close."""
    opened = list(pattern.finditer(before))
    if opened and opened[-1].group(1) not in before[opened[-1].end() :]:
        return opened[-1]
    return None


def _in_handler(before: str) -> bool:
    return _open_attribute(_HANDLER_OPEN, before) is not None


def _in_srcdoc(before: str) -> bool:
    return _open_attribute(_SRCDOC_OPEN, before) is not None


def _at_url_start(before: str) -> bool:
    """Whether a value placed after *before* starts a URL attribute's value.

    Only literal path text before it makes it a later part of the URL: ``href="${a}${b}"``
    puts ``b`` at the start whenever ``a`` is empty, as ``scriptRoot`` often is.
    """
    opened = _open_attribute(_URL_ATTR_OPEN, before)
    return opened is not None and re.fullmatch(r"[\s\0]*", before[opened.end() :]) is not None


def _context_allows(expr: str, before: str) -> bool:
    """Whether a computed value *expr* may be written after the markup *before*."""
    if _in_srcdoc(before):
        return False  # srcdoc is a whole document: nothing computed goes in it
    if _UNQUOTED_ATTRIBUTE.search(before):
        return False  # an unquoted attribute ends at a space, escaped or not
    if _in_handler(before):
        return _single_call(expr, ("jsArg",)) and not before.rstrip("\0 ").endswith(("'", '"', "`"))
    if _at_url_start(before):
        return _single_call(expr, ("safeUrl",)) or expr in _TRUSTED_NAMES
    return True


#: A Jinja-rendered route or whole URL: ``{{ url_for(...)|tojson }}``, ``{{ (script_name ~ '/x')|tojson }}``.
#: The root alone is not one: a script reaches it as ``scriptRoot`` (``_TRUSTED_NAMES``) and no other way.
_TRUSTED_JINJA_ROOT = re.compile(
    r"^\{\{\s*(?:url_for\(.*\)|\(script_name\s*~\s*'/[^']*'.*\))"
    r"\s*\|\s*tojson(?:\s*\|\s*forceescape)?\s*\}\}$",
    re.S,
)


def _trusted_root(expr: str) -> bool:
    expr, _ = _strip_parens(expr)
    return expr in _TRUSTED_NAMES or bool(_TRUSTED_JINJA_ROOT.match(expr))


def _url_is_safe(expr: str, markup: _Markup | None = None, depth: int = 0) -> bool:  # noqa: PLR0911, C901 - one case per form
    """Whether *expr*, written as a URL through the DOM, cannot carry a script scheme or another origin's path.

    Safe: a literal (not ``javascript:``/``data:``), ``checkedUrl()``/``safeUrl()``, a blob
    URL, the script root, or a URL that starts with a fixed prefix (a literal path, query,
    fragment or http(s) origin, optionally after the script root). A plain name is followed
    to its definitions in the file; a function parameter is not trusted.
    """
    expr, _ = _strip_parens(expr)
    if expr.startswith("{{") and "}}" in expr:
        expr = expr[: expr.index("}}") + 2]  # in an inline handler the scan runs on into the markup after it
    if depth > 4:
        return False
    if _STRING_LITERAL.match(expr):
        return not re.match(r"""['"]\s*(?:javascript|data|vbscript):""", expr, re.I)
    if _trusted_root(expr) or _single_call(expr, _URL_HELPERS):
        return True
    replaced = re.match(r"^(.*)\.replace\(", expr, re.S)
    if (
        replaced
        and _matching_close(expr, replaced.end() - 1) == len(expr) - 1
        and _url_is_safe(replaced.group(1), markup, depth + 1)
    ):
        return True  # swapping a placeholder inside a safe URL keeps its prefix
    if re.fullmatch(r"(?:window\.)?URL\.createObjectURL\(.*\)", expr, re.S):
        return True
    if (branches := _ternary(expr)) is not None:
        return all(_url_is_safe(branch, markup, depth + 1) for _, branch in branches)
    if len(parts := _split_top(expr, ("||",))) > 1:
        return all(_url_is_safe(part, markup, depth + 1) for _, part in parts)
    if expr.startswith("`") and _parse_template(expr, 0, []) == len(expr):
        literals: list[_Literal] = []
        _parse_template(expr, 0, literals)
        text, exprs = "".join(literals[0].text), literals[0].exprs
        if text.startswith("\0") and exprs and _trusted_root(exprs[0][0]):
            text = text[1:]
            if not text:
                return True  # the root or route alone
        return _fixed_prefix(text)
    pieces = [piece for _, piece in _split_top(expr, ("+",))]
    while len(pieces) > 1 and _strip_parens(pieces[0])[0] in ("''", '""'):
        pieces = pieces[1:]
    if len(pieces) > 1:
        head, _ = _strip_parens(pieces[0])
        if _trusted_root(head):
            return _fixed_prefix(_piece_text(pieces[1])) or _url_is_safe(pieces[1], markup, depth + 1)
        if _STRING_LITERAL.match(head) or head.startswith("`"):
            return _fixed_prefix(_piece_text(head))
        return markup is not None and _resolves_to_fixed_prefix(head, markup, depth)
    if markup is not None and re.fullmatch(r"[A-Za-z_$][\w$]*", expr):
        return _name_is_safe_url(expr, markup, depth)
    return False


def _name_is_safe_url(name: str, markup: _Markup, depth: int) -> bool:
    definitions = markup._definitions(name)
    if not definitions or any(markup._is_parameter(name, offset) for offset, _ in definitions):
        return False
    return all(_url_is_safe(value, markup, depth + 1) for _, value in definitions)


def _resolves_to_fixed_prefix(name: str, markup: _Markup, depth: int) -> bool:
    """Whether every definition of *name* is a literal with a fixed prefix (``const base = 'https://...'``)."""
    if not re.fullmatch(r"[A-Za-z_$][\w$]*", name):
        return False
    definitions = markup._definitions(name)
    return bool(definitions) and all(
        _STRING_LITERAL.match(value.strip()) and _fixed_prefix(_piece_text(value.strip())) for _, value in definitions
    )


def _fixed_prefix(text: str) -> bool:
    """Whether a URL starting with *text* has its scheme and origin fixed: a path, query, fragment or http(s) origin."""
    return bool(re.match(r"(?:/(?![/\\])|\?|#|https?://[^/\0]+(?:/|$))", text, re.I))


def _interpolation_is_safe(markup: _Markup, raw: str, offset: int, before: str) -> bool:
    return markup.value_is_safe(raw, offset, before)


def unescaped_sink_operands(src: str) -> list[tuple[int, str]]:
    """(line, value) for a value written into an HTML sink, or as a URL, that the helpers did not make.

    Markup sinks: ``innerHTML``/``outerHTML`` (assigned, by index, or in an object
    literal), ``srcdoc``, ``insertAdjacentHTML``, ``setHTMLUnsafe``, ``document.write``,
    ``createContextualFragment``, jQuery ``.html()`` and its insertion methods given a
    markup literal. URL writes: ``.href``/``.src``/``.action``/``.formAction`` and
    ``location`` assignments, ``location.assign/replace``, ``window.open``, and
    ``setAttribute``/``.attr`` of a URL attribute; each takes a literal, a same-origin
    path, or ``checkedUrl()``. ``setAttribute('on...')`` and ``('srcdoc')`` take a literal only.
    """
    markup = _Markup(src)
    found = [(offset, value) for offset, value in markup.sink_values() if not markup.value_is_safe(value, offset)]
    found += [(offset, value) for offset, value in markup.url_values() if not _url_is_safe(value, markup)]
    found += markup.refused_writes()
    return sorted((src.count("\n", 0, offset) + 1, value.strip()) for offset, value in found)


def unescaped_interpolations(src: str) -> list[tuple[int, str]]:
    """(line, expression) for every interpolation into markup that skips the helpers.

    A template literal is markup when it holds a tag or is part of a value written into a
    sink (directly, through a ``...Html`` variable, or returned by a function that is). Its
    interpolations are checked in the context of the markup before them, including any
    concatenated ahead of the literal.
    """
    markup = _Markup(src)
    for offset, value in markup.sink_values():
        markup.value_is_safe(value, offset)
    found: set[tuple[int, str]] = set()
    pending = [literal for literal in template_literals(src) if not markup.in_comment(literal.start)]
    while True:  # checking one literal's interpolations can mark a nested literal as markup
        ready = [lit for lit in pending if _TAG.search("".join(lit.text)) or lit.start in markup.literal_starts]
        if not ready:
            return sorted(found)
        for literal in ready:
            pending.remove(literal)
            markup._bound = set(markup.literal_bindings.get(literal.start, ()))
            prefix = markup.literal_prefix.get(literal.start, "")
            for expr, offset, before in literal.exprs:
                if not _interpolation_is_safe(markup, expr, offset, prefix + before):
                    found.add((src.count("\n", 0, offset) + 1, expr.strip()))


_JINJA_IN_HANDLER = re.compile(r"""\son[a-z]+\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.I)
_JINJA_EXPR = re.compile(r"\{\{(.*?)\}\}", re.S)


def unescaped_jinja_handlers(src: str) -> list[tuple[int, str]]:
    found = []
    for handler in _JINJA_IN_HANDLER.finditer(src):
        for expr in _JINJA_EXPR.finditer(handler.group(1) or handler.group(2) or ""):
            if not re.search(r"\|\s*tojson\s*\|\s*forceescape\s*$", expr.group(1).strip()):
                found.append((src.count("\n", 0, handler.start()) + 1, expr.group(0)))
    return found


#: A ``<script>`` block with a body: one with a real ``src`` attribute (not ``data-src``) has none.
_SCRIPT_BLOCK = re.compile(r"<script\b(?![^>]*(?<![\w-])src\s*=)[^>]*>(.*?)</script>", re.S | re.I)

#: A Jinja value at the start of a URL attribute must be one of these, or end in |safe_url.
_TRUSTED_JINJA_URL = re.compile(r"^(?:url_for\(.*\)|script_name|request\.script_root)$", re.S)
_JINJA_URL_ATTRIBUTE = re.compile(r"\s(" + _URL_ATTRIBUTES + r")\s*=\s*([\"'])(.*?)\2", re.I | re.S)


def unescaped_jinja_urls(src: str) -> list[tuple[int, str]]:
    """(line, expression) for a Jinja value that starts a URL attribute unchecked, or sits in a javascript: URL.

    Autoescape stops a value leaving the attribute, but not ``javascript:alert(1)`` being
    the attribute. A value at the start of the URL must be ``url_for(...)``, the script
    root, or pass through ``|safe_url``; only literal path text before it makes it a
    later part of the URL.
    """
    found = []
    for attribute in _JINJA_URL_ATTRIBUTE.finditer(src):
        value = re.sub(r"\{%.*?%\}", "", attribute.group(3), flags=re.S)
        line = src.count("\n", 0, attribute.start()) + 1
        if re.match(r"\s*javascript:", value, re.I) and "{{" in value:
            found.append((line, value.strip()))
            continue
        literal, position = "", 0
        for expr in _JINJA_EXPR.finditer(value):
            literal += value[position : expr.start()]
            position = expr.end()
            text = expr.group(1).strip()
            trusted = bool(_TRUSTED_JINJA_URL.match(text) or re.search(r"\|\s*safe_url\s*$", text))
            if not literal.strip() and not trusted:
                found.append((line, expr.group(0)))
            if not trusted:
                break  # whatever follows an unchecked value is a later part of the URL
    return found


def unescaped_jinja_in_scripts(src: str) -> list[tuple[int, str]]:
    """(line, expression) for a Jinja value in a ``<script>`` block that is not ``|tojson``.

    Autoescape writes HTML entities, which a script does not decode: a backslash or a
    ``${`` in the value, or two values on one line, can still end a JavaScript string.
    ``|tojson`` writes a JavaScript literal instead.
    """
    found = []
    for block in _SCRIPT_BLOCK.finditer(src):
        for expr in _JINJA_EXPR.finditer(block.group(1)):
            if not re.search(r"\|\s*tojson\s*$", expr.group(1).strip()):
                found.append((src.count("\n", 0, block.start(1) + expr.start()) + 1, expr.group(0)))
    return found


def _blank(match: re.Match[str]) -> str:
    """*match* replaced by spaces, keeping its newlines so line numbers still count."""
    return re.sub(r"[^\n]", " ", match.group(0))


#: A start tag; a quoted attribute value may hold ``>``.
_START_TAG = re.compile(r"""<[a-zA-Z](?:[^<>"']|"[^"]*"|'[^']*')*>""")
#: Inside a tag with its quoted values blanked: an attribute whose value is a Jinja expression.
_UNQUOTED_JINJA_ATTRIBUTE = re.compile(r"""\s[^\s=<>"'/]+\s*=\s*(\{\{.*?\}\})""", re.S)


_QUOTED_VALUE = re.compile(r""""[^"]*"|'[^']*'""")


def _start_tags(src: str) -> tuple[str, list[re.Match[str]]]:
    """*src* with script bodies and ``{% %}``/``{# #}`` blanked, and its start tags.

    The tags are matched in a copy whose ``{{ }}`` bodies are masked (same length), so an
    expression's own quotes and ``==`` do not read as markup; offsets are shared.
    """
    markup = _SCRIPT_BLOCK.sub(_blank, src)
    markup = re.sub(r"\{%.*?%\}|\{#.*?#\}", _blank, markup, flags=re.S)
    masked = _JINJA_EXPR.sub(lambda m: "{{" + "x" * (len(m.group(0)) - 4) + "}}", markup)
    return markup, list(_START_TAG.finditer(masked))


def unescaped_jinja_unquoted_attributes(src: str) -> list[tuple[int, str]]:
    """(line, expression) for a Jinja value that is an attribute's whole, unquoted value.

    Autoescape encodes ``<``, ``>``, ``&`` and quotes, but an unquoted value ends at a
    space, so ``title={{ name }}`` lets a name holding ``x onmouseover=alert(1)`` add an
    attribute. Quote the attribute. Script bodies and ``{% %}`` tags are blanked first,
    so ``const a = {{ b|tojson }}`` and ``{% if a > b %}`` are not read as markup.
    """
    markup, tags = _start_tags(src)
    found = []
    for tag in tags:
        inner = _QUOTED_VALUE.sub(lambda q: q[0][0] + "q" * (len(q[0]) - 2) + q[0][-1], tag.group(0))
        for m in _UNQUOTED_JINJA_ATTRIBUTE.finditer(inner):
            start = tag.start() + m.start(1)
            found.append((markup.count("\n", 0, start) + 1, markup[start : tag.start() + m.end(1)]))
    return found


def tojson_in_double_quoted_attributes(src: str) -> list[tuple[int, str]]:
    """(line, expression) for ``|tojson`` inside a double-quoted attribute value without ``|forceescape``.

    Flask's ``tojson`` returns markup that escapes ``<``, ``>``, ``&`` and ``'`` but not
    ``"``, and autoescape leaves markup alone, so a JSON string ends the attribute: a value
    can add attributes, and a real object is cut to ``{``. ``|forceescape`` writes ``&#34;``,
    which the browser decodes back to ``"`` inside the value.
    """
    markup, tags = _start_tags(src)
    found = []
    for tag in tags:
        for value in _QUOTED_VALUE.finditer(tag.group(0)):
            if not value.group(0).startswith('"'):
                continue
            offset = tag.start() + value.start()
            for expr in _JINJA_EXPR.finditer(markup[offset : tag.start() + value.end()]):
                text = expr.group(1).strip()
                if re.search(r"\|\s*tojson\b", text) and not re.search(r"\|\s*forceescape\s*$", text):
                    found.append((markup.count("\n", 0, offset + expr.start()) + 1, expr.group(0)))
    return found


_ESCAPING_OFF = re.compile(r"\|\s*safe\b|\bMarkup\(|autoescape\s+false")


def escaping_off(src: str) -> list[tuple[int, str]]:
    return [(src.count("\n", 0, m.start()) + 1, m.group(0)) for m in _ESCAPING_OFF.finditer(src)]


def _scanned_files() -> list[Path]:
    files = {p for pattern in SCAN_GLOBS for p in ROOT.glob(pattern)}
    return sorted(p for p in files if p.relative_to(ROOT).as_posix() != HELPER)


def _counts(detector) -> Counter[str]:
    counts: Counter[str] = Counter()
    for path in _scanned_files():
        found = detector(path.read_text(encoding="utf-8"))
        if found:
            counts[path.relative_to(ROOT).as_posix()] = len(found)
    return counts


def _escaping_off_counts() -> Counter[str]:
    counts = _counts(escaping_off)
    for path in sorted((ROOT / "src" / "admin").rglob("*.py")):
        found = escaping_off(path.read_text(encoding="utf-8"))
        if found:
            counts[path.relative_to(ROOT).as_posix()] = len(found)
    return counts


@pytest.mark.parametrize(
    ("detector", "baseline", "what"),
    [
        (
            unescaped_interpolations,
            _BASELINE,
            "template-literal interpolations into markup without escapeHtml/safeUrl/jsArg",
        ),
        (unescaped_sink_operands, _SINK_OPERAND_BASELINE, "values written into an HTML sink without the helpers"),
        (
            unescaped_jinja_handlers,
            _JINJA_HANDLER_BASELINE,
            "Jinja values in inline handlers without |tojson|forceescape",
        ),
        (unescaped_jinja_in_scripts, _JINJA_SCRIPT_BASELINE, "Jinja values in <script> blocks without |tojson"),
        (unescaped_jinja_urls, _JINJA_URL_BASELINE, "Jinja values starting a URL attribute without |safe_url"),
        (
            unescaped_jinja_unquoted_attributes,
            _JINJA_UNQUOTED_BASELINE,
            "Jinja values written as an unquoted attribute value",
        ),
        (
            tojson_in_double_quoted_attributes,
            _TOJSON_ATTRIBUTE_BASELINE,
            "|tojson in a double-quoted attribute without |forceescape",
        ),
        (None, _SAFE_ALLOWED, "|safe / Markup( / autoescape false sites"),
    ],
    ids=[
        "template-literals",
        "sink-operands",
        "jinja-handlers",
        "jinja-in-scripts",
        "jinja-urls",
        "jinja-unquoted",
        "tojson-in-attribute",
        "escaping-off",
    ],
)
def test_no_new_unescaped_html_sinks(detector, baseline, what):
    counts = _escaping_off_counts() if detector is None else _counts(detector)
    grown = {f: n for f, n in counts.items() if n > baseline.get(f, 0)}
    shrunk = {f: n for f, n in baseline.items() if counts.get(f, 0) < n}
    assert not grown, (
        f"New {what} (file: found, allowed). Use the helpers in {HELPER}, see its header:\n  "
        + "\n  ".join(f"{f}: {counts[f]}, {baseline.get(f, 0)}" for f in sorted(grown))
    )
    assert not shrunk, "Fixed sites; lower these baseline counts to match:\n  " + "\n  ".join(
        f"{f}: {counts.get(f, 0)} (baseline {n})" for f, n in sorted(shrunk.items())
    )


#: A binding of a trusted name: a declaration, an assignment (``window.x =`` included), or a
#: function parameter (``function f(x)``, ``(a, x) =>``, ``x =>``, ``constructor(x = '')``).
def _trusted_bindings(src: str) -> list[tuple[int, str]]:
    names = "|".join(re.escape(n) for n in _TRUSTED_NAMES)
    name = rf"(?<![\w$.])(?P<n>{names})\b"
    patterns = [
        rf"\b(?:const|let|var)\s+{name}",
        rf"(?<![\w$.])(?:window\.)?(?P<n>{names})\s*=(?![=>])",
        rf"(?P<head>[\w$]*)\s*\([^()]*{name}[^()]*\)\s*(?:=>|\{{)",
        rf"{name}\s*=>",
    ]
    comments = _comment_spans(src)
    found: dict[int, str] = {}
    for pattern in patterns:
        for m in re.finditer(pattern, src):
            if "head" in m.groupdict() and m.group("head") in ("if", "for", "while", "switch", "catch", "with"):
                continue  # a condition, not a parameter list
            at = m.start("n")
            if not any(a <= at < b for a, b in comments):
                found.setdefault(at, m.group("n"))
    return sorted((src.count("\n", 0, at) + 1, text) for at, text in found.items())


def test_the_trusted_prefix_is_bound_only_in_base_html():
    """A per-page copy of scriptRoot may be bound to anything, and the guard would still trust it."""
    path, definition = _TRUSTED_DEFINITION
    found = {
        (p.relative_to(ROOT).as_posix(), line, text)
        for p in _scanned_files()
        for line, text in _trusted_bindings(p.read_text(encoding="utf-8"))
    }
    base = (ROOT / path).read_text(encoding="utf-8")
    assert base.count(definition) == 1
    assert [entry for entry in found if entry[0] != path] == []
    assert len([entry for entry in found if entry[0] == path]) == 1


def test_every_static_script_is_scanned():
    scanned = {p.relative_to(ROOT).as_posix() for p in _scanned_files()}
    assert "static/validation.js" in scanned
    assert "static/js/tenant_settings.js" in scanned
    assert "src/landing/templates/tenant_landing.html" in scanned
