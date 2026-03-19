"""
Crawler for El Al seat availability API.

Uses Playwright (headless Chromium) to bypass bot protection, with a plain
HTTP fallback. The API returns JSON with this structure:

{
  "flightsToIsrael": [
    {
      "origin": "SOF",
      "flights": [{
        "flightNumber": "LY452", "routeFrom": "SOF", "routeTo": "TLV",
        "segmentDepTime": "12:40", "isFlightAvailable": false,
        "originDetails": {"cityName": "Sofia", "countryName": "Bulgaria"},
        "flightsDates": [
          {"flightsDate": "19.03", "seatCount": 0},
          {"flightsDate": "20.03"},  // no seatCount = no flight this date
        ]
      }]
    }
  ]
}
"""

import json
import logging
import sqlite3
from datetime import datetime
from typing import List, Optional, Tuple

import requests

import config
from database import get_connection
from models import Flight

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Fetch (Playwright primary, HTTP fallback)
# ---------------------------------------------------------------------------

def fetch_via_playwright() -> Optional[dict]:
    """Fetch seat availability by loading the page in headless Chromium
    and calling the API from within the browser context (bypasses bot protection).
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("Playwright not installed, skipping browser-based fetch")
        return None

    logger.info("Fetching seat availability via Playwright...")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-features=IsolateOrigins,site-per-process",
                ],
            )
            context = browser.new_context(
                user_agent=config.USER_AGENT,
                viewport={"width": 1280, "height": 800},
            )

            # Intercept the API response directly from network traffic
            api_data = {}

            def handle_response(response):
                if "SeatAvailability/lang/eng/flights" in response.url:
                    ct = response.headers.get("content-type", "")
                    logger.info("Intercepted API response: status=%d, content-type=%s", response.status, ct)
                    if "json" in ct or "javascript" not in ct:
                        try:
                            body = response.json()
                            if isinstance(body, dict) and "flightsToIsrael" in body:
                                api_data["result"] = body
                                logger.info("Got valid flight data!")
                        except Exception:
                            logger.debug("Response was not valid JSON")

            page = context.new_page()
            page.on("response", handle_response)

            # Navigate to seat availability page - the page will call the API
            page.goto(
                "https://www.elal.com/eng/seat-availability?d=1",
                wait_until="domcontentloaded",
                timeout=60000,
            )

            # Wait for the API response to be intercepted (up to 30s)
            for _ in range(30):
                if "result" in api_data:
                    break
                page.wait_for_timeout(1000)

            # If still not captured, try fetching from within the page
            if "result" not in api_data:
                logger.info("API not intercepted, trying in-page fetch...")
                page.wait_for_timeout(2000)
                data_str = page.evaluate("""async () => {
                    try {
                        const resp = await fetch('/api/SeatAvailability/lang/eng/flights');
                        if (!resp.ok) return null;
                        const text = await resp.text();
                        return text;
                    } catch(e) { return null; }
                }""")
                if data_str and isinstance(data_str, str) and data_str.startswith("{"):
                    api_data["result"] = json.loads(data_str)

            data = api_data.get("result")

            browser.close()

            if data and isinstance(data, dict) and "flightsToIsrael" in data:
                logger.info(
                    "Playwright fetch successful: %d origins",
                    len(data.get("flightsToIsrael", [])),
                )
                return data
            else:
                logger.warning("Playwright fetch returned unexpected data: %s", type(data))
                return None

    except Exception:
        logger.exception("Playwright fetch failed")
        return None


def fetch_via_http() -> Optional[dict]:
    """Fetch seat availability via plain HTTP (may fail due to bot protection)."""
    headers = {
        "User-Agent": config.USER_AGENT,
        "Accept": "application/json",
    }
    try:
        logger.info("Fetching seat availability via HTTP...")
        response = requests.get(
            config.SEAT_AVAILABILITY_URL,
            headers=headers,
            timeout=config.REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict) and "flightsToIsrael" in data:
            logger.info("HTTP fetch successful")
            return data
        else:
            logger.warning("HTTP fetch returned non-flight data (likely bot challenge)")
            return None
    except (requests.RequestException, ValueError) as exc:
        logger.warning("HTTP fetch failed: %s", exc)
        return None


def fetch_seat_availability() -> Optional[dict]:
    """Fetch seat availability data, trying Playwright first then HTTP fallback."""
    data = fetch_via_playwright()
    if data is not None:
        return data

    logger.info("Falling back to HTTP fetch...")
    return fetch_via_http()


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------

def _convert_date(date_str: str) -> str:
    """Convert 'DD.MM' format to 'YYYY-MM-DD' using the current year."""
    try:
        day, month = date_str.split(".")
        year = datetime.now().year
        return f"{year}-{month}-{day}"
    except (ValueError, AttributeError):
        return date_str


def parse_flights(data) -> List[Flight]:
    """Parse the API response into Flight objects (only flights TO Israel)."""
    if data is None:
        return []

    flights: List[Flight] = []

    try:
        routes = data.get("flightsToIsrael", [])
        if not routes:
            logger.warning("No 'flightsToIsrael' in API response")
            return []

        for route in routes:
            for flight_data in route.get("flights", []):
                origin_code = flight_data.get("routeFrom", route.get("origin", ""))
                flight_number = flight_data.get("flightNumber", "")
                flight_time = flight_data.get("segmentDepTime", "")

                origin_details = flight_data.get("originDetails", {})
                origin_city = origin_details.get("cityName", "")
                origin_country = origin_details.get("countryName", "")

                for date_entry in flight_data.get("flightsDates", []):
                    if "seatCount" not in date_entry:
                        continue

                    flight_date = _convert_date(date_entry.get("flightsDate", ""))
                    seats = date_entry.get("seatCount", None)

                    flights.append(Flight(
                        origin_code=origin_code,
                        origin_city=origin_city,
                        origin_country=origin_country,
                        flight_number=flight_number,
                        flight_time=flight_time,
                        flight_date=flight_date,
                        seats_available=seats,
                    ))

    except Exception:
        logger.exception("Error parsing seat availability data")

    logger.info("Parsed %d flight+date records to Israel", len(flights))
    return flights


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

def store_flights(flights: List[Flight]) -> Tuple[int, int]:
    """Upsert flights into the database. Returns (total, new_count)."""
    if not flights:
        return (0, 0)

    conn = get_connection()
    new_count = 0

    for flight in flights:
        try:
            conn.execute(
                """INSERT INTO flights
                    (origin_code, origin_city, origin_country, destination_code,
                     flight_number, flight_time, flight_date, seats_available, is_new)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)""",
                (
                    flight.origin_code, flight.origin_city, flight.origin_country,
                    flight.destination_code, flight.flight_number, flight.flight_time,
                    flight.flight_date, flight.seats_available,
                ),
            )
            new_count += 1
        except sqlite3.IntegrityError:
            conn.execute(
                """UPDATE flights
                SET seats_available = ?, last_seen_at = CURRENT_TIMESTAMP
                WHERE flight_number = ? AND flight_date = ?""",
                (flight.seats_available, flight.flight_number, flight.flight_date),
            )
        except sqlite3.Error as exc:
            logger.error("DB error storing %s on %s: %s",
                         flight.flight_number, flight.flight_date, exc)

    conn.commit()
    return (len(flights), new_count)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def crawl_seat_availability() -> Tuple[int, int]:
    """Run the full crawl cycle. Returns (total, new_count)."""
    data = fetch_seat_availability()
    if data is None:
        logger.warning("Seat availability fetch returned no data")
        return (0, 0)

    flights = parse_flights(data)
    total, new_count = store_flights(flights)

    # Also update destinations table from the flight data
    _update_destinations(data)

    logger.info("Crawl complete: %d total, %d new", total, new_count)
    return (total, new_count)


def load_from_json(data: dict) -> Tuple[int, int]:
    """Load from pre-fetched JSON (for manual imports)."""
    flights = parse_flights(data)
    return store_flights(flights)


def _update_destinations(data: dict):
    """Update destinations table from seat availability API data."""
    conn = get_connection()
    for route in data.get("flightsToIsrael", []):
        for flight in route.get("flights", []):
            od = flight.get("originDetails", {})
            code = flight.get("routeFrom", "")
            if code:
                conn.execute(
                    """INSERT OR REPLACE INTO destinations
                    (code, city_name, country_name, continent,
                     is_operational, is_recovery_flight_origin, last_updated)
                    VALUES (?, ?, ?, ?, 1, 1, datetime('now'))""",
                    (code, od.get("cityName", ""), od.get("countryName", ""),
                     od.get("continentName", "")),
                )
    conn.commit()
