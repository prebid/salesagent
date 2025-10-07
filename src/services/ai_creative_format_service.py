#!/usr/bin/env python3
"""AI-driven creative format discovery service.

This service discovers and analyzes creative format specifications from:
1. URLs provided by users (publisher spec pages)
2. Standard formats from adcontextprotocol.org
3. Natural language descriptions
"""

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiohttp
import google.generativeai as genai
from bs4 import BeautifulSoup
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import CreativeFormat
from src.core.retry_utils import http_retry

logger = logging.getLogger(__name__)


@dataclass
class FormatSpecification:
    """Creative format specification details."""

    format_id: str
    name: str
    type: str  # display, video, audio, native
    description: str
    extends: str | None = None  # Reference to foundational format
    assets: list[dict[str, Any]] = None  # List of required assets
    source_url: str | None = None


class AICreativeFormatService:
    """Service that uses AI to discover and analyze creative formats."""

    STANDARD_FORMATS_URL = "https://adcontextprotocol.org/formats.json"

    def __init__(self):
        # Initialize Gemini
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY environment variable is required")

        genai.configure(api_key=api_key)
        # Using latest Gemini Flash model for better performance
        self.model = genai.GenerativeModel("gemini-flash-latest")

        # Initialize foundational formats manager if available
        try:
            from foundational_formats import FoundationalFormatsManager

            self.formats_manager = FoundationalFormatsManager()
        except ImportError:
            logger.info("Foundational formats not available")
            self.formats_manager = None

        # Load parsing examples for few-shot learning
        self.parsing_examples = self._load_parsing_examples()

    def _load_parsing_examples(self) -> dict[str, Any]:
        """Load parsing examples from test directory for few-shot learning."""
        examples = {}
        examples_dir = Path(__file__).parent / "creative_format_parsing_examples"

        if examples_dir.exists():
            for publisher_dir in examples_dir.iterdir():
                if publisher_dir.is_dir() and not publisher_dir.name.startswith("."):
                    expected_dir = publisher_dir / "expected_output"
                    if expected_dir.exists():
                        for json_file in expected_dir.glob("*.json"):
                            try:
                                with open(json_file) as f:
                                    data = json.load(f)
                                    examples[f"{publisher_dir.name}_{json_file.stem}"] = data
                            except Exception as e:
                                logger.warning(f"Failed to load example {json_file}: {e}")

        logger.info(f"Loaded {len(examples)} parsing examples for few-shot learning")
        return examples

    @http_retry
    async def fetch_standard_formats(self) -> list[FormatSpecification]:
        """Fetch standard formats from adcontextprotocol.org."""
        formats = []

        try:
            async with aiohttp.ClientSession() as session:
                # First try the JSON endpoint
                try:
                    async with session.get(self.STANDARD_FORMATS_URL) as response:
                        if response.status == 200:
                            data = await response.json()
                            for fmt in data.get("formats", []):
                                formats.append(
                                    FormatSpecification(
                                        format_id=fmt["format_id"],
                                        name=fmt["name"],
                                        type=fmt["type"],
                                        description=fmt.get("description", ""),
                                        width=fmt.get("width"),
                                        height=fmt.get("height"),
                                        duration_seconds=fmt.get("duration_seconds"),
                                        max_file_size_kb=fmt.get("max_file_size_kb"),
                                        specs=fmt.get("specs", {}),
                                        source_url=self.STANDARD_FORMATS_URL,
                                    )
                                )
                except:
                    # Fallback: scrape the HTML page
                    base_url = "https://adcontextprotocol.org"
                    async with session.get(f"{base_url}/creative-formats") as response:
                        if response.status == 200:
                            html = await response.text()
                            formats.extend(await self._parse_standard_formats_html(html, base_url))

        except Exception as e:
            logger.error(f"Error fetching standard formats: {e}")
            # Return some default standard formats as fallback
            formats = self._get_default_standard_formats()

        return formats

    def _get_default_standard_formats(self) -> list[FormatSpecification]:
        """Return default standard formats as fallback."""
        return [
            FormatSpecification(
                format_id="display_300x250",
                name="Medium Rectangle",
                type="display",
                description="IAB standard medium rectangle banner",
                width=300,
                height=250,
                max_file_size_kb=200,
                specs={"file_types": ["jpg", "png", "gif", "html5"], "animation": "max 30s"},
            ),
            FormatSpecification(
                format_id="display_728x90",
                name="Leaderboard",
                type="display",
                description="IAB standard leaderboard banner",
                width=728,
                height=90,
                max_file_size_kb=200,
                specs={"file_types": ["jpg", "png", "gif", "html5"], "animation": "max 30s"},
            ),
            FormatSpecification(
                format_id="display_300x600",
                name="Half Page",
                type="display",
                description="IAB standard half page banner",
                width=300,
                height=600,
                max_file_size_kb=300,
                specs={"file_types": ["jpg", "png", "gif", "html5"], "animation": "max 30s"},
            ),
            FormatSpecification(
                format_id="video_instream",
                name="In-Stream Video",
                type="video",
                description="Standard in-stream video ad",
                assets=[
                    {
                        "asset_id": "video_file",
                        "asset_type": "video",
                        "required": True,
                        "name": "Video File",
                        "acceptable_formats": ["mp4", "webm"],
                        "duration_seconds": 30,
                        "max_file_size_mb": 10,
                        "additional_specs": {"codecs": ["h264", "vp9"], "max_bitrate_kbps": 2500},
                    }
                ],
            ),
        ]

    def _extract_structured_data(self, html: str) -> dict[str, Any]:
        """Extract structured data from HTML for better parsing."""
        soup = BeautifulSoup(html, "html.parser")

        structured_data = {"tables": [], "lists": [], "headings": [], "spec_sections": []}

        # Extract tables (often contain specifications)
        for table in soup.find_all("table"):
            table_data = []
            for row in table.find_all("tr"):
                cells = [cell.get_text(strip=True) for cell in row.find_all(["td", "th"])]
                if cells:
                    table_data.append(cells)
            if table_data:
                structured_data["tables"].append(table_data)

        # Extract specification lists
        for ul in soup.find_all("ul"):
            text = ul.get_text().lower()
            if any(keyword in text for keyword in ["dimension", "size", "format", "spec", "pixel", "resolution"]):
                items = [li.get_text(strip=True) for li in ul.find_all("li")]
                structured_data["lists"].append(items)

        # Extract headings with their content
        for heading in soup.find_all(["h1", "h2", "h3", "h4"]):
            heading_text = heading.get_text(strip=True)
            if any(
                keyword in heading_text.lower()
                for keyword in ["format", "spec", "dimension", "requirement", "size", "video", "image"]
            ):
                # Get the next few siblings
                content = []
                sibling = heading.find_next_sibling()
                count = 0
                while sibling and count < 5 and sibling.name not in ["h1", "h2", "h3", "h4"]:
                    content.append(sibling.get_text(strip=True))
                    sibling = sibling.find_next_sibling()
                    count += 1

                structured_data["headings"].append({"title": heading_text, "content": " ".join(content)})

        return structured_data

    def _prepare_examples_for_prompt(self) -> str:
        """Prepare few-shot examples for the prompt."""
        if not self.parsing_examples:
            return ""

        examples_text = []
        # Use up to 2 examples for few-shot learning
        for key, data in list(self.parsing_examples.items())[:2]:
            if "formats" in data and data["formats"]:
                publisher = key.split("_")[0]
                examples_text.append(f"\nExample from {publisher}:")
                # Show first format as example
                examples_text.append(json.dumps(data["formats"][0], indent=2))

        return "\n".join(examples_text)

    def _get_publisher_hints(self, source_url: str) -> str:
        """Get publisher-specific parsing hints based on URL."""
        hints = []
        url_lower = source_url.lower()

        if "yahoo" in url_lower:
            hints.append("- Yahoo uses 'E2E' (Edge-to-Edge) for immersive full-viewport formats")
            hints.append("- Yahoo E2E Lighthouse formats extend foundation_immersive_canvas")
            hints.append("- Look for mobile-specific dimensions like 720x1280 (9:16 ratio)")
        elif "nytimes" in url_lower or "nyt" in url_lower:
            hints.append("- NYTimes 'Slideshow' formats are carousels with multiple images")
            hints.append("- NYTimes Slideshow Flex XL extends foundation_product_showcase_carousel")
            hints.append("- Look for split-screen layouts and image counts (3-5)")
        elif "google" in url_lower:
            hints.append("- Google formats often include AMP specifications")
            hints.append("- Look for responsive design requirements")

        if hints:
            return "Publisher-specific hints:\n" + "\n".join(hints)
        return ""

    async def discover_formats_from_html(self, html: str, source_url: str = "") -> list[FormatSpecification]:
        """Discover creative format specifications from HTML content."""
        formats = []

        try:
            # Extract structured data for better parsing
            structured_data = self._extract_structured_data(html)

            # Build content for AI processing
            content_parts = []

            # Add tables (often contain specifications)
            if structured_data["tables"]:
                content_parts.append("=== SPECIFICATION TABLES ===")
                for i, table in enumerate(structured_data["tables"][:3]):  # Limit to first 3 tables
                    content_parts.append(f"Table {i+1}:")
                    for row in table[:10]:  # Limit rows
                        content_parts.append(" | ".join(row))

            # Add relevant lists
            if structured_data["lists"]:
                content_parts.append("\n=== SPECIFICATION LISTS ===")
                for i, list_items in enumerate(structured_data["lists"][:3]):
                    content_parts.append(f"List {i+1}:")
                    for item in list_items[:5]:
                        content_parts.append(f"- {item}")

            # Add headings with content
            if structured_data["headings"]:
                content_parts.append("\n=== RELEVANT SECTIONS ===")
                for heading in structured_data["headings"][:5]:
                    content_parts.append(f"{heading['title']}:")
                    content_parts.append(heading["content"][:300])

            # Fallback: extract text if no structured data found
            if not content_parts:
                soup = BeautifulSoup(html, "html.parser")
                for script in soup(["script", "style"]):
                    script.decompose()

                # Look for relevant sections
                for tag in soup.find_all(["table", "div", "section"], limit=20):
                    text = tag.get_text()
                    if any(
                        keyword in text.lower()
                        for keyword in ["format", "size", "dimension", "spec", "ad", "banner", "video", "creative"]
                    ):
                        content_parts.append(text[:500])

            # Increase limit to 5000 chars for better context
            content_for_ai = "\n".join(content_parts)[:5000]

            # Use AI to extract format information
            foundational_info = ""
            if self.formats_manager:
                foundational_info = """
            For each format, also determine if it extends one of these foundational formats:
            - foundation_immersive_canvas: Premium responsive format for full viewport experiences (edge-to-edge, immersive)
            - foundation_product_showcase_carousel: Interactive display with 3-10 products/images (carousel, slideshow)
            - foundation_expandable_display: Banner with expandable canvas
            - foundation_scroll_triggered_experience: Mobile-first scroll reveal format
            - foundation_universal_video: Standard video specifications

            If the format extends a foundational format, include: "extends": "foundation_format_id"
            """

            # Get few-shot examples
            examples_text = self._prepare_examples_for_prompt()

            # Get publisher-specific hints
            publisher_hints = self._get_publisher_hints(source_url)

            prompt = f"""
            Analyze this structured content from a creative specification page and extract all creative format specifications.

            {publisher_hints}

            {examples_text}

            Look for:
            - Ad format names and types (display, video, native, audio)
            - Dimensions (width x height) for display ads
            - Duration limits for video/audio
            - File size limits
            - Accepted file formats (jpg, png, gif, mp4, etc.)
            - Mobile vs desktop specifications
            - Interactive features (expandable, carousel, scroll-triggered)

            URL: {source_url}

            Structured Content:
            {content_for_ai}

            {foundational_info}

            Return a JSON array of format objects with these fields:
            - name: format name (required)
            - type: "display", "video", "audio", or "native" (required)
            - description: brief description
            - extends: foundational format ID if applicable (optional)
            - assets: array of asset objects with fields:
              - asset_id: unique identifier for the asset (e.g., "main_video", "product_images")
              - asset_type: "video", "image", "text", "url", "audio", or "html"
              - required: boolean indicating if required
              - name: human-readable name
              - description: what the asset is for
              - acceptable_formats: array of file extensions (e.g., ["mp4", "webm"])
              - max_file_size_mb: maximum file size in megabytes
              - width/height: for images/video (optional)
              - duration_seconds: for video/audio (optional)
              - max_length: for text assets (optional)
              - additional_specs: object with any other requirements

            CRITICAL when creating assets for foundational formats:
            - Assets should be self-contained with all requirements
            - For carousel formats: create separate assets for images, titles, descriptions, and URLs
            - For video formats: include video file, captions, and optional companion banner assets
            - For expandable formats: create separate assets for collapsed and expanded states
            - Use descriptive asset_id values like "main_video", "product_images", "collapsed_creative"

            Important:
            - Extract ALL formats found on the page
            - Include mobile-specific formats (9:16 ratio like 720x1280)
            - Detect carousel/slideshow features and map to foundation_product_showcase_carousel
            - Detect immersive/edge-to-edge formats and map to foundation_immersive_canvas

            Return ONLY valid JSON array, no explanation or markdown.
            """

            try:
                response = self.model.generate_content(prompt)

                # Check if we got a valid response
                if not response or not hasattr(response, "text") or not response.text:
                    logger.warning("Empty response from Gemini for HTML content")
                    raise ValueError("Empty response from AI model")

                response_text = response.text.strip()

                # Debug logging to see what AI returned
                logger.info("AI response for HTML content:")
                logger.info(f"Raw response text: {response_text}")

                # Remove markdown code blocks if present
                if response_text.startswith("```json"):
                    response_text = response_text.replace("```json", "").replace("```", "")
                elif response_text.startswith("```"):
                    response_text = response_text.replace("```", "")

                response_text = response_text.strip()

                if not response_text:
                    logger.warning("Empty response text after cleaning for HTML content")
                    raise ValueError("Empty response text after cleaning")

                formats_data = json.loads(response_text)

                if not isinstance(formats_data, list):
                    logger.warning(f"Expected list but got {type(formats_data)} for HTML content")
                    raise ValueError("Response is not a list")

                for fmt in formats_data:
                    if not isinstance(fmt, dict) or not fmt.get("name") or not fmt.get("type"):
                        logger.warning(f"Skipping invalid format entry: {fmt}")
                        continue

                    # Generate format ID
                    if fmt.get("width") and fmt.get("height"):
                        format_id = f"{fmt['type']}_{fmt['width']}x{fmt['height']}"
                    elif fmt.get("duration_seconds"):
                        format_id = f"{fmt['type']}_{fmt['duration_seconds']}s"
                    else:
                        format_id = f"{fmt['type']}_{fmt['name'].lower().replace(' ', '_')}"

                    # If no extends field from AI, try to suggest one
                    extends = fmt.get("extends")
                    if not extends and self.formats_manager:
                        # Create specs dict for suggestion
                        suggest_specs = {"type": fmt["type"], "name": fmt["name"]}
                        if "carousel" in fmt["name"].lower() or "slideshow" in fmt["name"].lower():
                            suggest_specs["carousel"] = True
                        if "expandable" in fmt["name"].lower():
                            suggest_specs["expandable"] = True
                        if "scroll" in fmt["name"].lower():
                            suggest_specs["scroll"] = True
                        if "edge" in fmt["name"].lower() or "immersive" in fmt["name"].lower():
                            suggest_specs["responsive"] = True

                        extends = self.formats_manager.suggest_base_format(suggest_specs)

                    formats.append(
                        FormatSpecification(
                            format_id=format_id,
                            name=fmt["name"],
                            type=fmt["type"],
                            description=fmt.get("description", ""),
                            extends=extends,
                            assets=fmt.get("assets", []),
                            source_url=source_url,
                        )
                    )

            except (json.JSONDecodeError, ValueError) as e:
                logger.error(f"Failed to parse AI response for HTML content: {e}")
                # Try to extract basic information manually
                formats.extend(self._extract_formats_manually(html, source_url))
            except Exception as e:
                logger.error(f"AI model error for HTML content: {e}")
                # Try to extract basic information manually
                formats.extend(self._extract_formats_manually(html, source_url))

        except Exception as e:
            logger.error(f"Error discovering formats from HTML: {e}")

        return formats

    @http_retry
    async def discover_format_from_url(self, url: str) -> list[FormatSpecification]:
        """Discover creative format specifications from a URL."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    html = await response.text()

            return await self.discover_formats_from_html(html, url)

        except Exception as e:
            logger.error(f"Error fetching URL {url}: {e}")

        return []

    def _extract_formats_manually(self, html: str, url: str) -> list[FormatSpecification]:
        """Manually extract format information as fallback."""
        formats = []
        soup = BeautifulSoup(html, "html.parser")

        # Look for common patterns
        # Pattern 1: Tables with format specifications
        tables = soup.find_all("table")
        for table in tables:
            # Check if table contains format info
            headers = [th.text.lower() for th in table.find_all("th")]
            if any(word in " ".join(headers) for word in ["size", "dimension", "format", "spec"]):
                for row in table.find_all("tr")[1:]:  # Skip header
                    cells = row.find_all(["td", "th"])
                    if len(cells) >= 2:
                        # Try to extract format info
                        text = " ".join(cell.text for cell in cells)
                        # Look for dimensions
                        dim_match = re.search(r"(\d{2,4})\s*x\s*(\d{2,4})", text)
                        if dim_match:
                            width, height = int(dim_match.group(1)), int(dim_match.group(2))
                            name = cells[0].text.strip() if cells[0].text else f"{width}x{height}"
                            formats.append(
                                FormatSpecification(
                                    format_id=f"display_{width}x{height}",
                                    name=name,
                                    type="display",
                                    description=f"Display ad {width}x{height}",
                                    assets=[
                                        {
                                            "asset_id": "main_image",
                                            "asset_type": "image",
                                            "required": True,
                                            "width": width,
                                            "height": height,
                                        }
                                    ],
                                    source_url=url,
                                )
                            )

        # Pattern 2: Lists with format specifications
        for ul in soup.find_all(["ul", "ol"]):
            for li in ul.find_all("li"):
                text = li.text
                dim_match = re.search(r"(\d{2,4})\s*x\s*(\d{2,4})", text)
                if dim_match:
                    width, height = int(dim_match.group(1)), int(dim_match.group(2))
                    # Extract format name if present
                    name_match = re.search(r"^([^:]+):", text)
                    name = name_match.group(1).strip() if name_match else f"{width}x{height}"
                    formats.append(
                        FormatSpecification(
                            format_id=f"display_{width}x{height}",
                            name=name,
                            type="display",
                            description=text[:200],
                            assets=[
                                {
                                    "asset_id": "main_image",
                                    "asset_type": "image",
                                    "required": True,
                                    "width": width,
                                    "height": height,
                                }
                            ],
                            source_url=url,
                        )
                    )

        return formats

    async def _parse_standard_formats_html(self, html: str, base_url: str) -> list[FormatSpecification]:
        """Parse standard formats from HTML page."""
        formats = []
        BeautifulSoup(html, "html.parser")

        # Use AI to parse the page structure
        prompt = f"""
        Parse this HTML from the AdContext Protocol creative formats page.
        Extract all standard creative format specifications.

        HTML (first 5000 chars):
        {html[:5000]}

        Return JSON array with format specifications as described previously.
        Focus on IAB standard formats and commonly used sizes.
        """

        try:
            response = self.model.generate_content(prompt)

            # Debug logging to see what AI returned
            logger.info("AI response for standard formats HTML:")
            logger.info(f"Raw response text: {response.text}")

            formats_data = json.loads(response.text)

            for fmt in formats_data:
                if fmt.get("width") and fmt.get("height"):
                    format_id = f"display_{fmt['width']}x{fmt['height']}"
                elif fmt.get("duration_seconds"):
                    format_id = f"{fmt['type']}_{fmt.get('name', '').lower().replace(' ', '_')}"
                else:
                    format_id = f"{fmt['type']}_{fmt.get('name', '').lower().replace(' ', '_')}"

                formats.append(
                    FormatSpecification(
                        format_id=format_id,
                        name=fmt["name"],
                        type=fmt["type"],
                        description=fmt.get("description", ""),
                        assets=fmt.get("assets", self._create_assets_from_analysis(fmt)),
                        source_url=f"{base_url}/creative-formats",
                    )
                )

        except Exception as e:
            logger.error(f"Error parsing standard formats HTML: {e}")

        return formats

    async def analyze_format_description(
        self, name: str, description: str, type_hint: str | None = None
    ) -> FormatSpecification:
        """Analyze a natural language description to create a format specification."""

        prompt = f"""
        Create a creative format specification from this description:

        Name: {name}
        Description: {description}
        Type hint: {type_hint or 'auto-detect'}

        Analyze the description and determine:
        1. Type (display, video, audio, or native)
        2. Dimensions if applicable
        3. Duration if applicable
        4. File size limits
        5. Technical specifications

        Common patterns:
        - "banner" usually means display
        - Dimensions like "300x250" indicate display format
        - Duration mentions indicate video/audio
        - "native" or "sponsored content" indicate native format

        Return a JSON object with:
        - type: "display", "video", "audio", or "native"
        - description: enhanced description
        - width/height: for display formats
        - duration_seconds: for video/audio
        - max_file_size_kb: reasonable limit based on format
        - specs: technical specifications object

        Return ONLY valid JSON, no explanation.
        """

        response = self.model.generate_content(prompt)

        # Check if we got a valid response
        if not response or not hasattr(response, "text") or not response.text:
            logger.warning(f"Empty response from Gemini for format description '{name}'")
            raise ValueError("Empty response from AI model")

        response_text = response.text.strip()

        # Debug logging to see what AI returned
        logger.info(f"AI response for format description '{name}':")
        logger.info(f"Raw response text: {response_text}")

        # Remove markdown code blocks if present
        if response_text.startswith("```json"):
            response_text = response_text.replace("```json", "").replace("```", "")
        elif response_text.startswith("```"):
            response_text = response_text.replace("```", "")

        response_text = response_text.strip()

        if not response_text:
            logger.warning(f"Empty response text after cleaning for format description '{name}'")
            raise ValueError("Empty response text after cleaning")

        try:
            data = json.loads(response_text)

            # Generate format ID
            if data.get("width") and data.get("height"):
                format_id = f"{data['type']}_{data['width']}x{data['height']}"
            elif data.get("duration_seconds"):
                format_id = f"{data['type']}_{name.lower().replace(' ', '_')}"
            else:
                format_id = f"{data['type']}_{name.lower().replace(' ', '_')}"

            return FormatSpecification(
                format_id=format_id,
                name=name,
                type=data["type"],
                description=data.get("description", description),
                assets=self._create_assets_from_analysis(data),
            )

        except Exception as e:
            logger.error(f"Error analyzing format description: {e}")
            # Return a basic format
            return FormatSpecification(
                format_id=f"custom_{name.lower().replace(' ', '_')}",
                name=name,
                type=type_hint or "display",
                description=description,
                assets=[],
            )

    def _create_assets_from_analysis(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        """Create assets array from AI analysis data."""
        assets = []
        format_type = data.get("type", "display")

        if format_type == "video":
            assets.append(
                {
                    "asset_id": "video_file",
                    "asset_type": "video",
                    "required": True,
                    "name": "Video File",
                    "acceptable_formats": data.get("specs", {}).get("file_types", ["mp4", "webm"]),
                    "duration_seconds": data.get("duration_seconds"),
                    "max_file_size_mb": (
                        data.get("max_file_size_kb", 10240) / 1024 if data.get("max_file_size_kb") else None
                    ),
                }
            )
        elif format_type == "display":
            assets.append(
                {
                    "asset_id": "main_image",
                    "asset_type": "image",
                    "required": True,
                    "name": "Display Image",
                    "acceptable_formats": data.get("specs", {}).get("file_types", ["jpg", "png", "gif"]),
                    "width": data.get("width"),
                    "height": data.get("height"),
                    "max_file_size_mb": (
                        data.get("max_file_size_kb", 200) / 1024 if data.get("max_file_size_kb") else None
                    ),
                }
            )
        elif format_type == "audio":
            assets.append(
                {
                    "asset_id": "audio_file",
                    "asset_type": "audio",
                    "required": True,
                    "name": "Audio File",
                    "acceptable_formats": ["mp3", "aac"],
                    "duration_seconds": data.get("duration_seconds"),
                    "max_file_size_mb": (
                        data.get("max_file_size_kb", 5120) / 1024 if data.get("max_file_size_kb") else None
                    ),
                }
            )

        return assets


async def discover_creative_format(
    tenant_id: str | None,
    name: str,
    description: str | None = None,
    url: str | None = None,
    type_hint: str | None = None,
) -> dict[str, Any]:
    """Main entry point for discovering creative formats."""

    service = AICreativeFormatService()

    if url:
        # Discover from URL
        formats = await service.discover_format_from_url(url)
        if formats:
            # Return the first/best match
            fmt = formats[0]
        else:
            # Fallback to description analysis
            fmt = await service.analyze_format_description(name, description or "", type_hint)
    else:
        # Analyze description
        fmt = await service.analyze_format_description(name, description or "", type_hint)

    # Convert to dict for storage
    return {
        "format_id": fmt.format_id,
        "tenant_id": tenant_id,
        "name": fmt.name,
        "type": fmt.type,
        "description": fmt.description,
        "width": fmt.width,
        "height": fmt.height,
        "duration_seconds": fmt.duration_seconds,
        "max_file_size_kb": fmt.max_file_size_kb,
        "specs": json.dumps(fmt.specs or {}),
        "is_standard": False,  # Custom formats are not standard
        "source_url": fmt.source_url,
    }


async def sync_standard_formats():
    """Sync standard formats from adcontextprotocol.org to database."""
    service = AICreativeFormatService()
    formats = await service.fetch_standard_formats()

    with get_db_session() as session:
        for fmt in formats:
            try:
                # Check if format already exists
                stmt = select(CreativeFormat).filter_by(format_id=fmt.format_id)
                existing = session.scalars(stmt).first()

                if not existing:
                    # Insert new format
                    new_format = CreativeFormat(
                        format_id=fmt.format_id,
                        tenant_id=None,  # Standard formats have no tenant
                        name=fmt.name,
                        type=fmt.type,
                        description=fmt.description,
                        width=getattr(fmt, "width", None),
                        height=getattr(fmt, "height", None),
                        duration_seconds=getattr(fmt, "duration_seconds", None),
                        max_file_size_kb=getattr(fmt, "max_file_size_kb", None),
                        specs=getattr(fmt, "specs", {}) or {},
                        is_standard=True,
                        extends=fmt.extends,
                        source_url=fmt.source_url,
                    )
                    session.add(new_format)

            except Exception as e:
                logger.error(f"Error syncing format {fmt.format_id}: {e}")

        session.commit()

    return len(formats)
