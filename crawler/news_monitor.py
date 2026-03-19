"""
Monitor El Al's news page for operational updates about rescue/recovery flights.

Fetches content from the El Al news API, decodes base64-encoded HTML blocks,
parses them for non-operational destinations and recovery flight origins,
and stores snapshots in the database when changes are detected.
"""

import base64
import hashlib
import json
import logging
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

import config
from database import get_connection
from models import Destination, NewsSnapshot

logger = logging.getLogger(__name__)


def fetch_news_content() -> Optional[dict]:
    """Fetch raw JSON response from the El Al news content API."""
    try:
        response = requests.get(
            config.NEWS_CONTENT_URL,
            headers={"User-Agent": config.USER_AGENT},
            timeout=config.REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        logger.info("Successfully fetched news content from %s", config.NEWS_CONTENT_URL)
        return data
    except requests.RequestException as e:
        logger.error("Failed to fetch news content: %s", e)
        return None
    except ValueError as e:
        logger.error("Failed to parse news content JSON: %s", e)
        return None


def decode_content_blocks(data: Optional[dict]) -> str:
    """
    Parse the JSON response and decode base64-encoded HTML content blocks.

    The API response may nest content blocks in various structures.
    This function tries multiple paths defensively and returns
    the combined decoded HTML as a single string.
    """
    if not data:
        return ""

    decoded_parts: List[str] = []

    # Try top-level "content" key
    content = data.get("content") or data.get("Content")

    # If content is a string, it might be base64 directly
    if isinstance(content, str):
        try:
            decoded_parts.append(base64.b64decode(content).decode("utf-8"))
        except Exception:
            # Maybe it's already HTML
            decoded_parts.append(content)

    # If content is a list of blocks
    if isinstance(content, list):
        for block in content:
            _decode_block(block, decoded_parts)

    # Try "sections" key
    sections = data.get("sections") or data.get("Sections") or []
    if isinstance(sections, list):
        for section in sections:
            if isinstance(section, dict):
                block_content = (
                    section.get("content")
                    or section.get("Content")
                    or section.get("htmlContent")
                    or section.get("HtmlContent")
                )
                if isinstance(block_content, str):
                    try:
                        decoded_parts.append(base64.b64decode(block_content).decode("utf-8"))
                    except Exception:
                        decoded_parts.append(block_content)
                # Nested blocks within sections
                blocks = section.get("blocks") or section.get("Blocks") or []
                if isinstance(blocks, list):
                    for block in blocks:
                        _decode_block(block, decoded_parts)

    # Try "blocks" at top level
    blocks = data.get("blocks") or data.get("Blocks") or []
    if isinstance(blocks, list):
        for block in blocks:
            _decode_block(block, decoded_parts)

    # Try "components" key
    components = data.get("components") or data.get("Components") or []
    if isinstance(components, list):
        for comp in components:
            if isinstance(comp, dict):
                _decode_block(comp, decoded_parts)
                # Components may have nested content/blocks
                inner = comp.get("content") or comp.get("Content")
                if isinstance(inner, list):
                    for block in inner:
                        _decode_block(block, decoded_parts)

    if not decoded_parts:
        # Last resort: walk the entire dict looking for base64 strings
        _walk_for_base64(data, decoded_parts)

    combined = "\n".join(decoded_parts)
    if combined:
        logger.info("Decoded %d content block(s), total %d characters", len(decoded_parts), len(combined))
    else:
        logger.warning("No content blocks found in news response")
    return combined


def _decode_block(block, decoded_parts: List[str]):
    """Try to decode a single content block dict."""
    if not isinstance(block, dict):
        return
    value = (
        block.get("content")
        or block.get("Content")
        or block.get("htmlContent")
        or block.get("HtmlContent")
        or block.get("value")
        or block.get("Value")
        or block.get("text")
        or block.get("Text")
    )
    if isinstance(value, str):
        try:
            decoded_parts.append(base64.b64decode(value).decode("utf-8"))
        except Exception:
            decoded_parts.append(value)


def _walk_for_base64(obj, decoded_parts: List[str], depth: int = 0):
    """Recursively walk a data structure looking for base64-encoded strings."""
    if depth > 10:
        return
    if isinstance(obj, dict):
        for key, value in obj.items():
            _walk_for_base64(value, decoded_parts, depth + 1)
    elif isinstance(obj, list):
        for item in obj:
            _walk_for_base64(item, decoded_parts, depth + 1)
    elif isinstance(obj, str) and len(obj) > 100:
        # Heuristic: long strings that look like base64
        if re.match(r'^[A-Za-z0-9+/=\s]+$', obj[:200]):
            try:
                decoded = base64.b64decode(obj).decode("utf-8")
                if "<" in decoded and ">" in decoded:
                    decoded_parts.append(decoded)
            except Exception:
                pass


def parse_news_html(html: str) -> Dict:
    """
    Parse decoded HTML from the news page to extract operational information.

    Returns a dict with:
        - last_update_text: str or None
        - non_operational_destinations: list of str
        - recovery_flight_origins: list of str
    """
    result = {
        "last_update_text": None,
        "non_operational_destinations": [],
        "recovery_flight_origins": [],
    }

    if not html:
        return result

    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator="\n")

    # --- Extract last update timestamp ---
    # Look for patterns like "March 18, 2026", "18 March 2026", "Updated: ...", etc.
    date_patterns = [
        r'(?:updated|update|as of|effective)[:\s]*(\w+ \d{1,2},?\s*\d{4})',
        r'(?:updated|update|as of|effective)[:\s]*(\d{1,2}\s+\w+\s*,?\s*\d{4})',
        r'(\w+ \d{1,2},?\s*\d{4}(?:\s*(?:at\s*)?\d{1,2}:\d{2}(?:\s*[AP]M)?)?)',
        r'(\d{1,2}\s+\w+\s*,?\s*\d{4})',
        r'(\d{1,2}[./]\d{1,2}[./]\d{2,4})',
    ]
    for pattern in date_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            result["last_update_text"] = match.group(1).strip()
            logger.info("Found last update text: %s", result["last_update_text"])
            break

    # --- Extract non-operational destinations ---
    # Look for bold tags or headings mentioning "non-operational" followed by lists
    non_op_patterns = [
        r'(?:non[- ]?operational|suspended|cancelled|not operating)[^:]*[:]\s*([^\n]+)',
        r'(?:non[- ]?operational|suspended|cancelled|not operating)[^.]*\.\s*([^\n]+)',
    ]
    for pattern in non_op_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            destinations_text = match.group(1).strip()
            # Split on commas, semicolons, or " and "
            destinations = re.split(r'[,;]|\band\b', destinations_text)
            result["non_operational_destinations"] = [
                d.strip().rstrip(".") for d in destinations if d.strip() and len(d.strip()) > 1
            ]
            break

    # Also look for bold elements containing non-operational info
    if not result["non_operational_destinations"]:
        for bold in soup.find_all(["b", "strong"]):
            bold_text = bold.get_text()
            if re.search(r'non[- ]?operational|suspended|not operating', bold_text, re.IGNORECASE):
                # Get the next sibling text or parent text after the bold
                parent_text = bold.parent.get_text() if bold.parent else ""
                after_bold = parent_text.split(bold_text, 1)[-1] if bold_text in parent_text else ""
                if after_bold:
                    destinations = re.split(r'[,;]|\band\b', after_bold.split('\n')[0])
                    result["non_operational_destinations"] = [
                        d.strip().rstrip(".") for d in destinations if d.strip() and len(d.strip()) > 1
                    ]
                    break

    if result["non_operational_destinations"]:
        logger.info(
            "Found %d non-operational destinations: %s",
            len(result["non_operational_destinations"]),
            result["non_operational_destinations"],
        )

    # --- Extract recovery/rescue flight origins ---
    recovery_patterns = [
        r'(?:recovery|rescue|repatriation|emergency|special)\s*(?:flight|route|service)s?[^:]*[:]\s*([^\n]+)',
        r'(?:recovery|rescue|repatriation|emergency|special)\s*(?:flight|route|service)s?\s*(?:from|originating)[^:]*[:]\s*([^\n]+)',
    ]
    for pattern in recovery_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            origins_text = match.group(1).strip()
            origins = re.split(r'[,;]|\band\b', origins_text)
            result["recovery_flight_origins"] = [
                o.strip().rstrip(".") for o in origins if o.strip() and len(o.strip()) > 1
            ]
            break

    # Also scan for structured sections about recovery flights (by region)
    if not result["recovery_flight_origins"]:
        recovery_section = False
        origins = []
        for line in text.split("\n"):
            line = line.strip()
            if re.search(r'recovery|rescue|repatriation', line, re.IGNORECASE):
                recovery_section = True
                continue
            if recovery_section:
                if not line or re.match(r'^[A-Z].*:', line):
                    # Could be a region header like "Europe:", "North America:"
                    continue
                if re.match(r'^[-\u2022*]\s*', line):
                    city = re.sub(r'^[-\u2022*]\s*', '', line).strip()
                    if city:
                        origins.append(city)
                elif line and len(line) < 100:
                    # Could be a city name or comma-separated list
                    parts = re.split(r'[,;]', line)
                    for part in parts:
                        part = part.strip().rstrip(".")
                        if part and len(part) > 1 and not re.search(r'(?:click|visit|more|detail)', part, re.IGNORECASE):
                            origins.append(part)
                else:
                    # End of recovery section
                    recovery_section = False
        if origins:
            result["recovery_flight_origins"] = origins

    if result["recovery_flight_origins"]:
        logger.info(
            "Found %d recovery flight origins: %s",
            len(result["recovery_flight_origins"]),
            result["recovery_flight_origins"],
        )

    return result


def fetch_destinations() -> List[Destination]:
    """Fetch destinations from the El Al destinations API."""
    try:
        response = requests.get(
            config.DESTINATIONS_URL,
            headers={"User-Agent": config.USER_AGENT},
            timeout=config.REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        logger.info("Successfully fetched destinations from %s", config.DESTINATIONS_URL)
    except requests.RequestException as e:
        logger.error("Failed to fetch destinations: %s", e)
        return []
    except ValueError as e:
        logger.error("Failed to parse destinations JSON: %s", e)
        return []

    destinations = []
    items = data if isinstance(data, list) else data.get("destinations", data.get("Destinations", []))

    for item in items:
        if not isinstance(item, dict):
            continue
        code = (item.get("destinationCode") or item.get("code") or item.get("Code")
                or item.get("airportCode") or item.get("AirportCode") or "")
        city_name = item.get("cityName") or item.get("CityName") or item.get("city") or item.get("City") or ""
        if not code or not city_name:
            continue
        dest = Destination(
            code=code,
            city_name=city_name,
            country_name=item.get("countryName") or item.get("CountryName") or item.get("country") or None,
            continent=item.get("continent") or item.get("Continent") or item.get("region") or None,
            is_operational=True,
            is_recovery_flight_origin=False,
            last_updated=datetime.utcnow().isoformat(),
        )
        destinations.append(dest)

    logger.info("Parsed %d destinations from API", len(destinations))
    return destinations


def crawl_news() -> Tuple[bool, Optional[NewsSnapshot]]:
    """
    Main entry point: fetch, decode, parse news content and detect changes.

    Returns:
        (has_changed, snapshot) - has_changed is True if the content differs
        from the last stored snapshot. snapshot is the new NewsSnapshot if
        changed, otherwise None.
    """
    logger.info("Starting news crawl")

    # Fetch and decode news content
    raw_data = fetch_news_content()
    html_content = decode_content_blocks(raw_data)

    if not html_content:
        logger.warning("No HTML content decoded from news page")
        return False, None

    # Compute hash of the decoded content
    content_hash = hashlib.sha256(html_content.encode("utf-8")).hexdigest()

    # Check against latest snapshot
    conn = get_connection()
    row = conn.execute(
        "SELECT content_hash FROM news_snapshots ORDER BY id DESC LIMIT 1"
    ).fetchone()

    if row and row["content_hash"] == content_hash:
        logger.info("News content unchanged (hash=%s)", content_hash[:12])
        return False, None

    logger.info("News content changed (hash=%s), parsing updates", content_hash[:12])

    # Parse the HTML
    parsed = parse_news_html(html_content)

    non_op_json = json.dumps(parsed["non_operational_destinations"])
    recovery_json = json.dumps(parsed["recovery_flight_origins"])

    # Store new snapshot
    cursor = conn.execute(
        """
        INSERT INTO news_snapshots
            (content_hash, last_update_text, non_operational_destinations,
             recovery_flight_origins, raw_content)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            content_hash,
            parsed["last_update_text"],
            non_op_json,
            recovery_json,
            html_content,
        ),
    )
    conn.commit()

    snapshot = NewsSnapshot(
        content_hash=content_hash,
        last_update_text=parsed["last_update_text"],
        non_operational_destinations=non_op_json,
        recovery_flight_origins=recovery_json,
        raw_content=html_content,
        id=cursor.lastrowid,
    )
    logger.info("Stored news snapshot id=%d", snapshot.id)

    # Update destinations table with non-operational status
    non_op_names = [name.lower() for name in parsed["non_operational_destinations"]]
    recovery_names = [name.lower() for name in parsed["recovery_flight_origins"]]

    if non_op_names or recovery_names:
        existing = conn.execute("SELECT code, city_name FROM destinations").fetchall()
        now = datetime.utcnow().isoformat()
        for dest_row in existing:
            city_lower = dest_row["city_name"].lower()
            is_non_op = any(name in city_lower or city_lower in name for name in non_op_names)
            is_recovery = any(name in city_lower or city_lower in name for name in recovery_names)
            conn.execute(
                """
                UPDATE destinations
                SET is_operational = ?,
                    is_recovery_flight_origin = ?,
                    last_updated = ?
                WHERE code = ?
                """,
                (0 if is_non_op else 1, 1 if is_recovery else 0, now, dest_row["code"]),
            )
        conn.commit()
        logger.info("Updated operational status for existing destinations")

    # Also fetch and upsert destinations from the destinations API
    api_destinations = fetch_destinations()
    if api_destinations:
        now = datetime.utcnow().isoformat()
        for dest in api_destinations:
            city_lower = dest.city_name.lower()
            is_non_op = any(name in city_lower or city_lower in name for name in non_op_names)
            is_recovery = any(name in city_lower or city_lower in name for name in recovery_names)
            conn.execute(
                """
                INSERT INTO destinations (code, city_name, country_name, continent,
                                          is_operational, is_recovery_flight_origin, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(code) DO UPDATE SET
                    city_name = excluded.city_name,
                    country_name = excluded.country_name,
                    continent = excluded.continent,
                    is_operational = excluded.is_operational,
                    is_recovery_flight_origin = excluded.is_recovery_flight_origin,
                    last_updated = excluded.last_updated
                """,
                (
                    dest.code,
                    dest.city_name,
                    dest.country_name,
                    dest.continent,
                    0 if is_non_op else 1,
                    1 if is_recovery else 0,
                    now,
                ),
            )
        conn.commit()
        logger.info("Upserted %d destinations from API", len(api_destinations))

    return True, snapshot
