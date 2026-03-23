"""
Crawler for El Al flight prices via the booking engine.

Uses Playwright to navigate the booking search flow at booking.elal.com,
intercepts the pricing API responses, and stores fare data. This is
independent of the seat availability crawler — different data source,
different Playwright flow, different failure modes.

Booking API endpoints:
- POST /bfm/service/extly/booking/search/cash/fast   (initial search)
- GET  /bfm/service/extly/booking/search/cash/outbound (detailed fares)
"""

import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional

import config
from database import get_connection
from models import FlightPrice

logger = logging.getLogger(__name__)

SEARCH_PAGE_URL = "https://www.elal.com/eng/book-a-flight/"
ROUTE_INTERCEPT_PATTERN = "**/bfm/service/**"


# ---------------------------------------------------------------------------
# Parse booking API response into FlightPrice objects
# ---------------------------------------------------------------------------

def parse_price_response(data: dict, origin_code: str, flight_date: str) -> list[FlightPrice]:
    """Parse the cash/outbound API response into FlightPrice objects.

    Extracts fares from both directBounds and indirectBounds.
    Only includes fares for flights operated by or connecting through El Al (LY).
    """
    prices: list[FlightPrice] = []

    try:
        outbound = data.get("data", {}).get("trip", {}).get("outbound", {})

        for bound_group in ("directBounds", "indirectBounds"):
            bounds = outbound.get(bound_group, {}).get("bounds", [])
            for bound in bounds:
                # Extract flight number from segments
                segments = bound.get("segments", [])
                if not segments:
                    continue

                # Use the first El Al segment's flight number, or first segment
                flight_number = None
                for seg in segments:
                    carrier = seg.get("carrier", "")
                    fn = seg.get("flightNumber", "")
                    if carrier == "LY":
                        flight_number = f"LY{fn}"
                        break
                if not flight_number:
                    # Use first segment
                    seg = segments[0]
                    flight_number = f"{seg.get('carrier', '')}{seg.get('flightNumber', '')}"

                for fare in bound.get("fares", []):
                    net_price = fare.get("netPrice", {}).get("cash", {})
                    amount = net_price.get("amount")
                    currency = net_price.get("currencyCode")
                    if amount is None or currency is None:
                        continue

                    prices.append(FlightPrice(
                        flight_number=flight_number,
                        flight_date=flight_date,
                        origin_code=origin_code,
                        cabin_class=fare.get("bookingClassName", "unknown"),
                        fare_name=fare.get("name", ""),
                        price_amount=float(amount),
                        price_currency=currency,
                        seats_in_fare=fare.get("nbSeatLeft"),
                        is_cheapest=bool(fare.get("cheapest", False)),
                    ))

    except Exception:
        logger.exception("Error parsing price response for %s on %s", origin_code, flight_date)

    return prices


# ---------------------------------------------------------------------------
# Store prices in DB
# ---------------------------------------------------------------------------

def store_prices(prices: list[FlightPrice]) -> int:
    """Upsert prices into flight_prices table. Returns count stored."""
    if not prices:
        return 0

    conn = get_connection()
    count = 0
    for p in prices:
        try:
            conn.execute(
                """INSERT INTO flight_prices
                    (flight_number, flight_date, origin_code, cabin_class,
                     fare_name, price_amount, price_currency, seats_in_fare,
                     is_cheapest, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(flight_number, flight_date, fare_name)
                DO UPDATE SET
                    price_amount = excluded.price_amount,
                    price_currency = excluded.price_currency,
                    seats_in_fare = excluded.seats_in_fare,
                    is_cheapest = excluded.is_cheapest,
                    fetched_at = CURRENT_TIMESTAMP""",
                (p.flight_number, p.flight_date, p.origin_code, p.cabin_class,
                 p.fare_name, p.price_amount, p.price_currency, p.seats_in_fare,
                 int(p.is_cheapest)),
            )
            count += 1
        except Exception:
            logger.exception("Error storing price for %s on %s fare %s",
                           p.flight_number, p.flight_date, p.fare_name)
    conn.commit()
    return count


# ---------------------------------------------------------------------------
# Playwright booking flow
# ---------------------------------------------------------------------------

def _lookup_price(page, origin: str, date: str) -> Optional[dict]:
    """Run a single price lookup via the booking search flow.

    Navigates to the search page, fills the form, submits, and intercepts
    the cash/outbound response. Returns the parsed JSON or None on failure.

    The page should already have a browser context with cookies from prior lookups.
    """
    captured = {}

    def intercept_handler(route):
        """Intercept bfm/service requests, read body, pass through."""
        try:
            response = route.fetch()
            body = response.text()
            url = route.request.url

            if "cash/outbound" in url:
                try:
                    captured["outbound"] = json.loads(body)
                except json.JSONDecodeError:
                    logger.warning("cash/outbound response was not valid JSON")

            elif "cash/fast" in url:
                try:
                    fast_data = json.loads(body)
                    errors = fast_data.get("errors", [])
                    if errors:
                        captured["fast_error"] = errors[0].get("code", "")
                        captured["fast_desc"] = errors[0].get("desc", "")
                except json.JSONDecodeError:
                    pass

            route.fulfill(response=response)
        except Exception as e:
            logger.debug("Route intercept error: %s", e)
            try:
                route.continue_()
            except Exception:
                pass

    try:
        page.route(ROUTE_INTERCEPT_PATTERN, intercept_handler)

        # Navigate to search page
        page.goto(SEARCH_PAGE_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(4000)

        # Select "One way"
        page.evaluate("""() => {
            for (const d of document.querySelectorAll('div')) {
                if (d.textContent.trim() === '| One way') { d.click(); return; }
            }
        }""")
        page.wait_for_timeout(500)

        # Set origin
        page.evaluate("""() => {
            const input = document.getElementById('outbound-origin-location-input');
            if (input) { input.click(); input.value = ''; input.dispatchEvent(new Event('input', {bubbles:true})); }
        }""")
        page.wait_for_timeout(300)
        page.keyboard.type(origin, delay=80)
        page.wait_for_timeout(2000)
        page.evaluate("""() => {
            const opt = document.querySelector('[role="option"]');
            if (opt) opt.click();
        }""")
        page.wait_for_timeout(500)

        # Destination should default to TLV. If not, set it.
        dest_value = page.evaluate("""() => {
            const input = document.getElementById('outbound-destination-location-input');
            return input ? input.value : '';
        }""")
        if "TLV" not in (dest_value or "").upper():
            page.evaluate("""() => {
                const label = document.querySelector('#outbound-destination-location-input-describedby');
                if (label) label.click();
            }""")
            page.wait_for_timeout(300)
            page.keyboard.type("TLV", delay=80)
            page.wait_for_timeout(1500)
            page.evaluate("""() => {
                const opt = document.querySelector('[role="option"]');
                if (opt) opt.click();
            }""")
            page.wait_for_timeout(500)

        # Set departure date
        day = int(date.split("-")[2])
        page.evaluate("""() => {
            const depLabel = document.querySelector('#outbound-date-input-describedby');
            if (depLabel) depLabel.click();
        }""")
        page.wait_for_timeout(1000)

        # Click the correct day in the calendar
        page.evaluate(f"""() => {{
            const cells = document.querySelectorAll('[class*="calendar-day"], [class*="calendar"] td div');
            for (const c of cells) {{
                const text = c.textContent.trim();
                if (text === '{day}' && c.offsetParent !== null) {{
                    c.click();
                    return;
                }}
            }}
        }}""")
        page.wait_for_timeout(500)

        # Click Done on calendar
        page.evaluate("""() => {
            const btn = document.querySelector('button[aria-label*="calendar"], button[aria-label*="submit"]');
            if (btn) btn.click();
        }""")
        page.wait_for_timeout(500)

        # Submit search
        page.evaluate("""() => {
            const btn = document.querySelector('button[type="submit"], button[aria-label="search.ctaLabel"]');
            if (btn) { btn.disabled = false; btn.click(); }
        }""")

        # Wait for booking engine to load and API to respond
        for _ in range(30):
            page.wait_for_timeout(1000)
            if "outbound" in captured or "fast_error" in captured:
                break

        page.unroute(ROUTE_INTERCEPT_PATTERN)

        if "outbound" in captured:
            return captured["outbound"]

        if captured.get("fast_error") == "102":
            logger.info("No flights from %s on %s (closest date available elsewhere)", origin, date)
        elif "fast_error" in captured:
            logger.warning("Booking search error for %s on %s: %s",
                         origin, date, captured.get("fast_desc", "unknown"))
        else:
            logger.warning("No pricing data captured for %s on %s (timeout)", origin, date)

        return None

    except Exception:
        logger.exception("Price lookup failed for %s on %s", origin, date)
        try:
            page.unroute(ROUTE_INTERCEPT_PATTERN)
        except Exception:
            pass
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def lookup_price(origin: str, date: str) -> list[FlightPrice]:
    """Look up prices for a single origin+date. Returns list of FlightPrice objects."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("Playwright not installed, cannot fetch prices")
        return []

    logger.info("Looking up prices: %s -> TLV on %s", origin, date)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent=config.USER_AGENT,
            viewport={"width": 1280, "height": 800},
        )
        page = context.new_page()

        data = _lookup_price(page, origin, date)
        browser.close()

    if data is None:
        return []

    prices = parse_price_response(data, origin, date)
    if prices:
        stored = store_prices(prices)
        logger.info("Stored %d price records for %s on %s", stored, origin, date)

    return prices


def crawl_prices(origin: str = None, date: str = None) -> tuple[int, int]:
    """Run a price crawl cycle.

    If origin and date are given, fetches prices for that specific combination.
    Otherwise, fetches prices for all origins with available seats.

    Returns (lookups_attempted, prices_stored).
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("Playwright not installed, cannot fetch prices")
        return (0, 0)

    conn = get_connection()

    # Determine which origin+date pairs to look up
    if origin and date:
        pairs = [(origin, date)]
    else:
        rows = conn.execute(
            """SELECT DISTINCT origin_code, flight_date
               FROM flights
               WHERE seats_available > 0
               ORDER BY flight_date, origin_code"""
        ).fetchall()
        pairs = [(row["origin_code"], row["flight_date"]) for row in rows]

    if not pairs:
        logger.info("No origin+date pairs with available seats to price-check")
        return (0, 0)

    logger.info("Starting price crawl for %d origin+date pairs", len(pairs))

    total_stored = 0
    lookups = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent=config.USER_AGENT,
            viewport={"width": 1280, "height": 800},
        )

        try:
            page = context.new_page()

            for pair_origin, pair_date in pairs:
                lookups += 1
                logger.info("Price lookup %d/%d: %s on %s",
                          lookups, len(pairs), pair_origin, pair_date)

                try:
                    data = _lookup_price(page, pair_origin, pair_date)
                    if data:
                        prices = parse_price_response(data, pair_origin, pair_date)
                        stored = store_prices(prices)
                        total_stored += stored
                        logger.info("  -> %d fares stored", stored)
                    else:
                        logger.info("  -> no price data")
                except Exception:
                    logger.exception("  -> lookup failed, continuing")

                    # Session may be broken — create new page
                    try:
                        page.close()
                    except Exception:
                        pass
                    page = context.new_page()

        finally:
            browser.close()

    logger.info("Price crawl complete: %d lookups, %d prices stored", lookups, total_stored)
    return (lookups, total_stored)
