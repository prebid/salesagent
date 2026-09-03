"""Guard: no code under src/ rebuilds a URL or rewrites its destination fields.

Routing every request through the egress seam
(``src/core/security/outbound_http.py``) is not enough if code in front of the
seam may still change WHERE the request goes. A host rewrite ahead of ``asend``
means the URL the seam validates and pins is not the URL the caller supplied —
the latent SSRF shape the #1589 follow-up removed
(``_normalize_localhost_for_docker`` in ``protocol_webhook_service.py`` rewrote
``localhost`` to ``host.docker.internal`` before delivery, failing open on
parse errors).

The scan flags URL reconstruction anywhere under ``src/``: ``urlunparse(...)``
/ ``urlunsplit(...)`` calls and ``._replace(netloc=...)`` /
``._replace(scheme=...)`` (#1589). Nothing is exempt — not even the seam:
address policy there is delegated to ``adcp.signing``, which pins the resolved
IP without ever rewriting the URL, so a netloc rewrite inside the seam would be
just as wrong. The scan set is empty and there is no allowlist to grow.

Scope, stated precisely: this detector matches the stdlib REASSEMBLY spellings.
It deliberately does not try to catch every way a destination can change hands
— ``src/core/creative_agent_registry.py``'s ``_connection_agent_url`` swaps the
transport URL for the env-configured ``CREATIVE_AGENT_URL`` alias by returning
a different STRING (sanctioned, GH #1802), which no spelling scan can
distinguish from hostile code statically. That swap's bounds are graded
behaviourally instead: ``tests/unit/test_creative_agent_connection_alias.py``
asserts it applies only to the public default agent, only when the env var is
set, and that identity/cache-key URLs stay byte-identical.

Sibling guards for the other egress properties: the raw-lib and SDK-client
import bans formerly in ``test_architecture_no_raw_egress.py`` now live in
``ruff-egress.toml`` (TID251, run over ``src/`` by ``make quality-ci``), with
``tests/unit/test_ruff_egress_bans.py`` as their executable non-vacuity proof;
the seam's own construction is graded by
``tests/integration/test_mcp_client_egress.py`` over a real socket.
"""

from __future__ import annotations

import ast

import pytest

from tests.unit._architecture_helpers import (
    assert_detector_catches_ast_snippets,
    iter_call_expressions,
    parse_module,
    repo_root,
    scan_src,
)

# The egress seam — scanned like everything else (see module docstring).
SEAM_FILE = "src/core/security/outbound_http.py"

# Rebuilding a URL from parts is how a destination gets rewritten in front of
# the seam. Both spellings of the stdlib reassembler are banned under src/.
URL_REBUILD_FUNCTIONS = frozenset({"urlunparse", "urlunsplit"})

# ``ParseResult._replace`` keywords that change WHERE a request goes. ``path``
# / ``query`` / ``fragment`` rewrites are content, not destination, and are
# deliberately not matched.
DESTINATION_REPLACE_KEYWORDS = frozenset({"netloc", "scheme"})


def _call_is_destination_rewrite(call: ast.Call) -> bool:
    """True when *call* rebuilds a URL or replaces its destination fields.

    Matches ``urlunparse(...)`` / ``urlunsplit(...)`` in both bare and dotted
    spellings, and ``<expr>._replace(netloc=...)`` / ``<expr>._replace(scheme=...)``.
    A ``._replace`` without a destination keyword (e.g. on a datetime or a
    NamedTuple that has nothing to do with URLs) is not matched.
    """
    func = call.func
    if isinstance(func, ast.Name) and func.id in URL_REBUILD_FUNCTIONS:
        return True
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr in URL_REBUILD_FUNCTIONS:
        return True
    if func.attr == "_replace":
        return any(kw.arg in DESTINATION_REPLACE_KEYWORDS for kw in call.keywords)
    return False


def find_destination_rewrite_violations(tree: ast.Module) -> list[int]:
    """Line numbers of destination-rewrite violations in *tree*.

    Shaped as a ``(tree) -> list[int]`` detector so the meta-tests can feed it
    synthetic sources directly.
    """
    return sorted(call.lineno for call in iter_call_expressions(tree) if _call_is_destination_rewrite(call))


def _scan_src() -> dict[str, list[int]]:
    """Map every offending module under src/ to its violation lines. No exemptions."""
    return scan_src(find_destination_rewrite_violations)


class TestNoDestinationRewrite:
    """No code under src/ rebuilds a URL or rewrites its destination fields.

    There is no allowlist and no exemption: the scan set was emptied by the
    #1589 follow-up and stays empty. Even the seam is scanned — it pins
    addresses via ``adcp.signing`` without rewriting URLs.
    """

    @pytest.mark.arch_guard
    def test_no_destination_rewrites_anywhere(self):
        """Any urlunparse/urlunsplit or _replace(netloc/scheme=...) under src/ fails."""
        offenders = _scan_src()

        if offenders:
            lines = ["URL destination rewrite found under src/:", ""]
            lines.extend(f"  {module} ({len(count)} call site(s))" for module, count in sorted(offenders.items()))
            lines += [
                "",
                "The URL a caller supplies must reach the egress seam byte-for-byte; nothing may",
                "rebuild it or swap its netloc/scheme in front of (or inside) the seam. If a test",
                "stack needs a reachable callback host, register a reachable hostname instead",
                "(webhooks.adcp.test — see tests/e2e/_webhook_capture.py, GH #1802).",
                "There is no allowlist.",
            ]
            raise AssertionError("\n".join(lines))


class TestDestinationRewriteDetector:
    """The destination-rewrite detector's own correctness, on synthetic sources."""

    @pytest.mark.arch_guard
    def test_detector_catches_known_bad(self):
        """Every destination-rewrite form is reported."""
        assert_detector_catches_ast_snippets(
            find_destination_rewrite_violations,
            snippets={
                "bare urlunparse": (
                    "from urllib.parse import urlparse, urlunparse\n"
                    "u = urlunparse(urlparse(url)._replace(netloc='evil'))\n"
                ),
                "dotted urlunparse": "import urllib.parse\nu = urllib.parse.urlunparse(parts)\n",
                "bare urlunsplit": "from urllib.parse import urlunsplit\nu = urlunsplit(parts)\n",
                "netloc replace alone": (
                    "from urllib.parse import urlparse\np = urlparse(url)._replace(netloc='host.docker.internal')\n"
                ),
                "scheme replace alone": (
                    "from urllib.parse import urlparse\np = urlparse(url)._replace(scheme='http')\n"
                ),
                "the removed rewrite's exact shape": (
                    "from urllib.parse import urlparse, urlunparse\n\n\n"
                    "def _normalize(url):\n"
                    "    parsed = urlparse(url)\n"
                    "    return urlunparse(parsed._replace(netloc='host.docker.internal'))\n"
                ),
            },
        )

    @pytest.mark.arch_guard
    @pytest.mark.parametrize(
        ("label", "source"),
        [
            (
                "urlparse alone",
                "from urllib.parse import urlparse\n\nhost = urlparse(url).hostname\n",
            ),
            (
                "_replace without destination keyword",
                "from datetime import datetime\n\nd = datetime.now().replace(microsecond=0)\n",
            ),
            (
                "_replace on a non-URL namedtuple",
                "def f(point):\n    return point._replace(x=1, y=2)\n",
            ),
            (
                "_replace(path=...) is content, not destination",
                "from urllib.parse import urlparse\n\np = urlparse(url)._replace(path='/new', query='')\n",
            ),
            (
                "str.replace on a URL",
                "def f(url):\n    return url.replace('http://', 'https://')\n",
            ),
            (
                "bare name reference, no call",
                "from urllib.parse import urlunparse\n\nalias = urlunparse\n",
            ),
        ],
    )
    def test_detector_ignores_non_rewrites(self, label, source):
        """Parsing a URL or replacing non-destination fields is not a violation."""
        assert find_destination_rewrite_violations(ast.parse(source)) == [], f"false positive on {label}"

    @pytest.mark.arch_guard
    def test_seam_is_scanned_and_clean(self):
        """The seam is NOT exempt from this scan, and is clean.

        The import bans in ``ruff-egress.toml`` exempt the seam by noqa because
        issuing egress is its job. Rewriting destinations is nobody's job —
        address policy lives in ``adcp.signing``, which pins IPs without
        touching the URL — so this asserts the seam passes the scan it is
        subject to.
        """
        seam = repo_root() / SEAM_FILE
        assert find_destination_rewrite_violations(parse_module(seam)) == []


# ---------------------------------------------------------------------------
# Env-sourced destination guard (GH #1802) — the class this file's
# own module docstring admits the sibling detector above is blind to: it
# "matches the stdlib REASSEMBLY spellings" only, so an env read placed in
# front of a credential-bearing endpoint — ``APPROXIMATED_BASE_URL =
# os.environ.get("APPROXIMATED_BASE_URL", "https://cloud.approximated.app")``,
# the live shape at ``src/services/approximated_client.py:23`` today — never
# rebuilds a URL from parts, so ``find_destination_rewrite_violations`` above
# has nothing to flag. A caller could redirect a credentialed vendor client's
# destination at import time without a single ``urlunparse``/``._replace``
# call anywhere (triage F11 second half, R1 absence 2). This second detector
# closes that gap: a module-level assignment sourced from
# ``os.environ.get(...)``/``os.getenv(...)`` with a URL-shaped default is
# flagged, wherever it sits in the assignment's expression tree (a bare
# assignment or one nested inside a constructor kwarg).
# ---------------------------------------------------------------------------

_ENV_READ_FUNCTIONS = frozenset({"get", "getenv"})  # os.environ.get / os.getenv


def _is_url_shaped(value: object) -> bool:
    return isinstance(value, str) and value.startswith(("http://", "https://"))


def _env_read_default(call: ast.Call) -> ast.expr | None:
    """The default-value AST node of an os.environ.get(...)/os.getenv(...) call, or None if not one."""
    func = call.func
    if not isinstance(func, ast.Attribute) or func.attr not in _ENV_READ_FUNCTIONS:
        return None
    # os.environ.get(...): func.value is `os.environ` (an Attribute); os.getenv(...): func.value is `os` (a Name).
    is_os_environ_get = func.attr == "get" and isinstance(func.value, ast.Attribute) and func.value.attr == "environ"
    is_os_getenv = func.attr == "getenv" and isinstance(func.value, ast.Name) and func.value.id == "os"
    if not (is_os_environ_get or is_os_getenv):
        return None
    if len(call.args) >= 2:
        return call.args[1]
    for kw in call.keywords:
        if kw.arg == "default":
            return kw.value
    return None


def find_env_sourced_destination_violations(tree: ast.Module) -> list[int]:
    """Line numbers of MODULE-LEVEL assignments sourced from an env-read with a URL-shaped default.

    Matches both a bare ``X = os.environ.get(...)`` and one nested inside a
    constructor kwarg, e.g. ``X = SomeClass(field=os.environ.get(...))``.
    Deliberately module-scope only — a function-local env read cannot become
    an import-time credential-redirection knob the way a module attribute can.
    """
    violations: list[int] = []
    for stmt in tree.body:  # top-level only -- module scope, not function-local
        if not isinstance(stmt, (ast.Assign, ast.AnnAssign)) or stmt.value is None:
            continue
        for call in iter_call_expressions(stmt.value):
            default = _env_read_default(call)
            if default is not None and isinstance(default, ast.Constant) and _is_url_shaped(default.value):
                violations.append(call.lineno)
    return sorted(violations)


# Two sanctioned exemptions, each excluded by FILE (not a growable per-symbol
# allowlist) with its own distinct reason -- these are not one undifferentiated
# escape hatch:
#
# * ``src/core/creative_agent_registry.py`` -- ``_connection_agent_url``'s
#   ``CREATIVE_AGENT_URL`` alias is a deliberate transport-connection swap,
#   bounded behaviourally by ``tests/unit/test_creative_agent_connection_alias.py``,
#   not by this structural scan (exactly how the sibling detector above
#   already documents its own one exemption: "That swap's bounds are graded
#   behaviourally instead").
# * ``src/app.py`` -- ``_cors_origins = os.getenv("ALLOWED_ORIGINS", "http://
#   localhost:8000").split(",")`` configures ``CORSMiddleware.allow_origins``,
#   an INBOUND allowlist of origins permitted to make cross-origin requests TO
#   this application. It matches this detector's AST shape (env-read,
#   URL-shaped default, module-level) but is out of the Destination concept's
#   scope on the merits: it is neither credential-bearing (a public allowlist,
#   not a secret-bearing endpoint) nor reached through the egress seam
#   (``send``/``asend``) at all -- CORS configuration is a different subsystem
#   than "where a URL this application DIALS comes from" (GH #1802
#   design correction, found by this atom's own detector run).
# Only ``app.py`` remains. ``creative_agent_registry.py`` was carried here too
# until the shared scanner started raising on exemptions that suppress nothing:
# the detector found no violation in it at all, so the entry read as a
# considered decision while doing nothing — and would have silently
# pre-authorized a violation the file ever acquired.
_ENV_SOURCED_DESTINATION_EXEMPT_FILES = frozenset({"src/app.py"})


def _scan_env_sourced_destinations() -> dict[str, list[int]]:
    """Offending modules under src/, minus the sanctioned per-file exemptions.

    ``scan_src`` raises on an exemption that suppresses nothing, so an entry here
    is a live sanctioned violation by construction rather than by review.
    """
    return scan_src(find_env_sourced_destination_violations, exempt=_ENV_SOURCED_DESTINATION_EXEMPT_FILES)


class TestNoEnvSourcedDestination:
    """No module-level destination constant under src/ is sourced from an env read.

    A URL that must never become silently env-overridable is a typed
    ``VendorConstant`` (``src/core/security/egress/destination.py``), never a
    bare string built from ``os.environ.get(...)``/``os.getenv(...)``. The one
    sanctioned exception (``CREATIVE_AGENT_URL``) is excluded by file, pointing
    at its own bounding behavioral test — there is no growable allowlist.
    """

    @pytest.mark.arch_guard
    def test_no_env_sourced_destinations_anywhere(self):
        offenders = _scan_env_sourced_destinations()

        if offenders:
            lines = ["Env-sourced URL destination found under src/:", ""]
            lines.extend(
                f"  {module}: line(s) {violation_lines}" for module, violation_lines in sorted(offenders.items())
            )
            lines += [
                "",
                "A URL that must never become silently env-overridable is a typed VendorConstant",
                "(src/core/security/egress/destination.py), never a bare string built from",
                "os.environ.get(...)/os.getenv(...). The one sanctioned exception (CREATIVE_AGENT_URL)",
                "is excluded by file, bounded by tests/unit/test_creative_agent_connection_alias.py —",
                "there is no growable allowlist.",
            ]
            raise AssertionError("\n".join(lines))


class TestEnvSourcedDestinationDetector:
    """The env-sourced-destination detector's own correctness, on synthetic sources."""

    @pytest.mark.arch_guard
    def test_detector_catches_known_bad(self):
        """Every env-sourced-destination form is reported."""
        assert_detector_catches_ast_snippets(
            find_env_sourced_destination_violations,
            snippets={
                "bare os.environ.get with URL default": ('X_URL = os.environ.get("X_URL", "https://vendor.example")\n'),
                "os.getenv spelling": ('X_URL = os.getenv("X_URL", "https://vendor.example")\n'),
                "aliased os import, os.environ.get spelling": (
                    'import os as o\nX_URL = o.environ.get("X_URL", "https://vendor.example")\n'
                ),
                "DEFAULT_AGENT-shaped nested-kwarg form": (
                    "import os\n\n\n"
                    "DEFAULT_AGENT = CreativeAgent(\n"
                    '    agent_url=os.environ.get("CREATIVE_AGENT_URL", "https://creative.adcontextprotocol.org"),\n'
                    '    name="AdCP Standard Creative Agent",\n'
                    "    enabled=True,\n"
                    "    priority=1,\n"
                    ")\n"
                ),
            },
        )

    @pytest.mark.arch_guard
    @pytest.mark.parametrize(
        ("label", "source"),
        [
            (
                "non-URL env default",
                'X = os.environ.get("X", "not-a-url")\n',
            ),
            (
                "URL literal with no env read",
                'X_URL = "https://vendor.example"\n',
            ),
            (
                "function-local env read",
                'def f():\n    return os.environ.get("X_URL", "https://vendor.example")\n',
            ),
        ],
    )
    def test_detector_ignores_non_violations(self, label, source):
        """A non-URL default, a plain literal, or a function-local read is not a violation."""
        assert find_env_sourced_destination_violations(ast.parse(source)) == [], f"false positive on {label}"

    @pytest.mark.arch_guard
    def test_raw_detector_still_catches_the_exempted_shape(self):
        """The CREATIVE_AGENT_URL exemption is FILE-level, not shape-level.

        A standalone snippet reproducing ``creative_agent_registry.py``'s own
        ``DEFAULT_AGENT`` line is still flagged by the raw detector function
        given no knowledge of which file it came from — proving the exemption
        below suppresses it by FILE, not because this shape is invisible to
        the detector. A second, DIFFERENT env-sourced-URL bug introduced
        elsewhere in a NEW module would not get this pass for free.
        """
        snippet = (
            "import os\n\n\n"
            "DEFAULT_AGENT = CreativeAgent(\n"
            '    agent_url=os.environ.get("CREATIVE_AGENT_URL", "https://creative.adcontextprotocol.org"),\n'
            '    name="AdCP Standard Creative Agent",\n'
            "    enabled=True,\n"
            "    priority=1,\n"
            ")\n"
        )
        assert find_env_sourced_destination_violations(ast.parse(snippet)) != []

    @pytest.mark.arch_guard
    def test_a_dead_exemption_cannot_be_written(self):
        """An entry the detector never flags RAISES rather than sitting there inert.

        This replaces a meta-test that asserted ``creative_agent_registry.py``
        stayed out of the offenders — which it did, by being exempt, whether or
        not it had anything to exempt. That assertion passed identically on a
        live exemption and a dead one, so it could not tell them apart.
        """
        with pytest.raises(AssertionError, match="dead exemption"):
            scan_src(
                find_env_sourced_destination_violations,
                exempt=_ENV_SOURCED_DESTINATION_EXEMPT_FILES | {"src/core/creative_agent_registry.py"},
            )

    @pytest.mark.arch_guard
    def test_raw_detector_still_catches_the_cors_shape(self):
        """The ``app.py`` exemption is FILE-level too, not shape-level.

        A standalone snippet reproducing ``app.py``'s own ``_cors_origins``
        line is still flagged by the raw detector given no knowledge of which
        file it came from — the CORS default is out of the Destination
        concept's scope on the MERITS (an inbound allowlist, not an outbound
        dial destination), not because its syntactic shape is invisible.
        """
        snippet = 'import os\n\n_cors_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:8000").split(",")\n'
        assert find_env_sourced_destination_violations(ast.parse(snippet)) != []

    @pytest.mark.arch_guard
    def test_cors_exempted_file_is_skipped_by_the_full_scan(self):
        """``app.py`` is exempt AND still flagged — a live exemption, not a dead one.

        The absence half alone would pass on an exemption that suppresses
        nothing. ``scan_src`` raising on a dead entry is what makes this
        assertion mean "suppressed", rather than merely "not present".
        """
        offenders = _scan_env_sourced_destinations()
        assert "src/app.py" not in offenders
        assert find_env_sourced_destination_violations(parse_module(repo_root() / "src" / "app.py")), (
            "app.py no longer trips the detector, so its exemption is dead — delete it"
        )
