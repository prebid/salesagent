"""Automatic link validation for integration tests.

This module provides utilities to automatically extract and validate all links
from HTML responses during integration testing. It catches broken links that
would otherwise only be discovered in production.

Usage:
    # In your integration test
    response = client.get('/some/page')
    validator = LinkValidator(client)
    broken_links = validator.validate_response(response)
    assert not broken_links, f"Broken links found: {broken_links}"

Features:
- Extracts href attributes from <a> tags
- Extracts src attributes from <img>, <script>, <link> tags
- Validates internal links (routes within the app)
- Skips external links (http://, https://, mailto:, etc.)
- Skips JavaScript links (javascript:)
- Skips anchor-only links (#section)
- Records line numbers for easy debugging
- Provides detailed error messages
"""

from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse


@dataclass
class Link:
    """A link extracted from HTML."""

    url: str
    tag: str
    line: int
    type: str  # "href" or "src"


@dataclass
class BrokenLink:
    """A broken link with validation details."""

    url: str
    normalized_url: str
    tag: str
    line: int
    type: str
    status_code: int
    error: str

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for backwards compatibility."""
        return {
            "url": self.url,
            "normalized_url": self.normalized_url,
            "tag": self.tag,
            "line": self.line,
            "type": self.type,
            "status_code": self.status_code,
            "error": self.error,
        }


class LinkExtractor(HTMLParser):
    """HTML parser that extracts all links and their line numbers."""

    def __init__(self):
        super().__init__()
        self.links: list[Link] = []
        self._current_line = 1

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Extract links from href and src attributes."""
        attrs_dict = dict(attrs)

        # Extract href from <a> tags
        if tag == "a" and "href" in attrs_dict:
            href = attrs_dict["href"]
            if href:
                self.links.append(Link(url=href, tag=tag, line=self.getpos()[0], type="href"))

        # Extract src from <img>, <script>, <link> tags
        elif tag in ("img", "script", "link") and "src" in attrs_dict:
            src = attrs_dict["src"]
            if src:
                self.links.append(Link(url=src, tag=tag, line=self.getpos()[0], type="src"))

        # Extract href from <link> tags (CSS, icons, etc.)
        elif tag == "link" and "href" in attrs_dict:
            href = attrs_dict["href"]
            if href:
                self.links.append(Link(url=href, tag=tag, line=self.getpos()[0], type="href"))


class LinkValidator:
    """Validates links extracted from HTML responses."""

    def __init__(self, client: Any, base_url: str = "http://localhost"):
        """Initialize validator with a Flask test client.

        Args:
            client: Flask test client (from authenticated_admin_session fixture)
            base_url: Base URL for resolving relative links
        """
        self.client = client
        self.base_url = base_url

    def should_validate_link(self, url: str) -> bool:
        """Determine if a link should be validated.

        Returns:
            True if link should be validated, False if it should be skipped
        """
        # Skip empty links
        if not url or url.strip() == "":
            return False

        # Skip JavaScript links
        if url.startswith("javascript:"):
            return False

        # Skip mailto links
        if url.startswith("mailto:"):
            return False

        # Skip tel links
        if url.startswith("tel:"):
            return False

        # Skip anchor-only links (these don't navigate to a new page)
        if url.startswith("#"):
            return False

        # Skip external links (http://, https://, //)
        if url.startswith(("http://", "https://", "//")):
            return False

        # Skip data URLs (inline images, etc.)
        if url.startswith("data:"):
            return False

        # Validate all internal links (relative or absolute paths)
        return True

    def normalize_url(self, url: str, current_page: str) -> str:
        """Normalize a URL for testing.

        Args:
            url: URL to normalize (may be relative)
            current_page: Current page URL (for resolving relative links)

        Returns:
            Normalized absolute path (e.g., /tenant/123/products)
        """
        # Handle relative URLs
        if not url.startswith("/"):
            url = urljoin(current_page, url)

        # Parse and return just the path (no query params for basic validation)
        parsed = urlparse(url)
        return parsed.path

    def validate_link(self, url: str) -> tuple[bool, int, str]:
        """Validate a single link by making a HEAD request.

        Args:
            url: Normalized URL path to validate

        Returns:
            Tuple of (is_valid, status_code, error_message)
        """
        try:
            # Try HEAD request first (faster, doesn't load full page)
            response = self.client.head(url, follow_redirects=True)

            # Some routes don't support HEAD, fallback to GET
            if response.status_code == 405:
                response.close()  # Close HEAD response before making GET
                response = self.client.get(url, follow_redirects=True)

            try:
                # Consider 200, 302, 304 as valid
                # 302: Redirect (common for auth flows)
                # 304: Not Modified (common for cached resources)
                # 401: Unauthorized (route exists but needs auth)
                # 403: Forbidden (route exists but user lacks permission)
                if response.status_code in (200, 302, 304, 401, 403):
                    return True, response.status_code, ""

                # 404: Not Found (broken link!)
                # 500: Internal Server Error (broken code!)
                # 501: Not Implemented (expected for some routes)
                return False, response.status_code, f"Status {response.status_code}"
            finally:
                response.close()  # Ensure response is always closed

        except Exception as e:
            return False, 0, f"Exception: {str(e)}"

    def validate_response(
        self,
        response: Any,
        current_page: str = "/",
        allow_404: bool = False,
        allow_501: bool = True,
    ) -> list[dict[str, Any]]:
        """Extract and validate all links from an HTML response.

        Args:
            response: Flask response object
            current_page: Current page URL (for resolving relative links)
            allow_404: If True, don't report 404s as broken (for optional routes)
            allow_501: If True, don't report 501s as broken (for unimplemented routes)

        Returns:
            List of broken links as dicts (for backwards compatibility with tests).
            Each dict contains: url, normalized_url, tag, line, type, status_code, error
        """
        # Only validate HTML responses
        if "text/html" not in response.content_type:
            return []

        # Extract all links from HTML
        html = response.data.decode("utf-8")
        extractor = LinkExtractor()
        extractor.feed(html)

        broken_links = []

        for link in extractor.links:
            # Skip links that shouldn't be validated
            if not self.should_validate_link(link.url):
                continue

            # Normalize URL
            normalized_url = self.normalize_url(link.url, current_page)

            # Validate link
            is_valid, status_code, error = self.validate_link(normalized_url)

            # Filter out allowed status codes
            if not is_valid:
                if allow_404 and status_code == 404:
                    continue
                if allow_501 and status_code == 501:
                    continue

                broken = BrokenLink(
                    url=link.url,
                    normalized_url=normalized_url,
                    tag=link.tag,
                    line=link.line,
                    type=link.type,
                    status_code=status_code,
                    error=error,
                )
                broken_links.append(broken.to_dict())

        return broken_links

    def validate_page(self, url: str, **kwargs) -> list[dict[str, Any]]:
        """Fetch a page and validate all its links.

        Args:
            url: Page URL to fetch and validate
            **kwargs: Additional arguments passed to validate_response()

        Returns:
            List of broken links (see validate_response())
        """
        response = self.client.get(url, follow_redirects=True)
        return self.validate_response(response, current_page=url, **kwargs)


def format_broken_links_report(broken_links: list[dict[str, Any]], page_url: str) -> str:
    """Format broken links into a readable error report.

    Args:
        broken_links: List of broken links from validate_response()
        page_url: URL of the page being validated

    Returns:
        Formatted error message suitable for assertion messages
    """
    if not broken_links:
        return ""

    report = [f"\nBroken links found on {page_url}:\n"]

    for link in broken_links:
        report.append(
            f"  [{link['status_code']}] {link['normalized_url']} "
            f"(line {link['line']}, <{link['tag']} {link['type']}=...>)"
        )
        if link["error"]:
            report.append(f"       Error: {link['error']}")

    report.append(f"\nTotal: {len(broken_links)} broken link{'s' if len(broken_links) != 1 else ''}")

    return "\n".join(report)
