# Price Scraping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add automated flight price scraping from El Al's booking engine, with price storage, price-based alerts, and dashboard display.

**Architecture:** Independent price crawler module (`crawler/price_crawler.py`) using Playwright to navigate the booking flow at `booking.elal.com`. Prices stored in a new `flight_prices` table, queried via new API endpoints, displayed in the existing dashboard. Runs on a separate 6-hour APScheduler cycle.

**Tech Stack:** Python 3.11+, Playwright (headless Chromium), SQLite, Flask, APScheduler, vanilla JS

**Spec:** `docs/superpowers/specs/2026-03-23-price-scraping-design.md`

---

## File Map

| Action | File | Responsibility |
|--------|------|---------------|
| Modify | `models.py` | Add `FlightPrice` dataclass |
| Modify | `config.py` | Add `PRICE_POLL_INTERVAL_MINUTES`, `PRICE_MARKET` |
| Modify | `database.py` | Add `flight_prices` table, index, ALTER existing tables |
| Create | `crawler/price_crawler.py` | Playwright-based price scraping from booking engine |
| Modify | `scheduler.py` | Add `run_price_crawl()` job on 6-hour interval |
| Modify | `web/routes.py` | Add `/api/prices`, `/api/refresh-prices`, extend existing endpoints |
| Modify | `services/email_notifier.py` | Add price info to alert emails, price-threshold alert logic |
| Modify | `web/static/js/app.js` | Display prices in flights table, add refresh-prices button |
| Modify | `web/templates/index.html` | Add price column header, refresh-prices button |
| Modify | `CLAUDE.md` | Update Known Limitations, API table, schema docs |
| Delete | `test_price_api.py` | Remove investigation script (no longer needed) |

---

### Task 1: Add FlightPrice Model and Config

**Files:**
- Modify: `models.py:55-64` (after CrawlLog)
- Modify: `config.py:22-23` (after existing scheduling vars)

- [ ] **Step 1: Add FlightPrice dataclass to models.py**

Add after the `CrawlLog` class at the end of the file:

```python
@dataclass
class FlightPrice:
    flight_number: str
    flight_date: str
    origin_code: str
    cabin_class: str
    price_amount: float
    price_currency: str
    fare_name: Optional[str] = None
    seats_in_fare: Optional[int] = None
    is_cheapest: bool = False
    fetched_at: Optional[str] = None
    id: Optional[int] = None
```

- [ ] **Step 2: Add config variables to config.py**

Add after line 23 (`NEWS_POLL_INTERVAL_MINUTES`):

```python
PRICE_POLL_INTERVAL_MINUTES = int(os.getenv("PRICE_POLL_INTERVAL_MINUTES", "360"))
PRICE_MARKET = os.getenv("PRICE_MARKET", "US")
```

- [ ] **Step 3: Commit**

```bash
git add models.py config.py
git commit -m "feat: add FlightPrice model and price config variables"
```

---

### Task 2: Database Schema Changes

**Files:**
- Modify: `database.py:28-96`

- [ ] **Step 1: Add flight_prices table and index to init_db()**

Add inside the `executescript` block, after the `crawl_log` CREATE TABLE (before the closing `"""`):

```sql
CREATE TABLE IF NOT EXISTS flight_prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    flight_number TEXT NOT NULL,
    flight_date TEXT NOT NULL,
    origin_code TEXT NOT NULL,
    cabin_class TEXT NOT NULL,
    fare_name TEXT,
    price_amount REAL NOT NULL,
    price_currency TEXT NOT NULL,
    seats_in_fare INTEGER,
    is_cheapest INTEGER DEFAULT 0,
    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(flight_number, flight_date, fare_name)
);

CREATE INDEX IF NOT EXISTS idx_flight_prices_lookup
    ON flight_prices(flight_number, flight_date, is_cheapest);
```

- [ ] **Step 2: Add ALTER TABLE migrations after executescript**

Add after `conn.commit()` at the end of `init_db()`:

```python
    # Migrations for existing tables
    _add_column_if_missing(conn, "crawl_log", "crawl_type", "TEXT DEFAULT 'seats'")
    _add_column_if_missing(conn, "alert_configs", "max_price", "REAL")
    _add_column_if_missing(conn, "alert_configs", "price_currency", "TEXT DEFAULT 'USD'")
```

- [ ] **Step 3: Add _add_column_if_missing helper**

Add before `init_db()`:

```python
def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, col_type: str):
    """Add a column to a table if it doesn't already exist (SQLite has no ADD COLUMN IF NOT EXISTS)."""
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
        conn.commit()
    except sqlite3.OperationalError as e:
        if "duplicate column name" not in str(e).lower():
            raise
```

- [ ] **Step 4: Verify DB initializes cleanly**

Run: `cd C:/Users/manorz/ElAlRescueFlightFinder && venv/Scripts/python -c "from database import init_db; init_db(); print('OK')"`

Expected: `OK` (no errors)

- [ ] **Step 5: Commit**

```bash
git add database.py
git commit -m "feat: add flight_prices table and schema migrations"
```

---

### Task 3: Price Crawler Core

**Files:**
- Create: `crawler/price_crawler.py`

This is the largest task. The crawler navigates the El Al booking flow via Playwright, intercepts the pricing API responses, and stores them.

- [ ] **Step 1: Create crawler/price_crawler.py**

```python
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
```

- [ ] **Step 2: Verify the module imports cleanly**

Run: `cd C:/Users/manorz/ElAlRescueFlightFinder && venv/Scripts/python -c "from crawler.price_crawler import parse_price_response, store_prices; print('OK')"`

Expected: `OK`

- [ ] **Step 3: Test parse_price_response with captured data**

Save the real API response we captured during investigation to a test fixture, then verify parsing. Run:

```bash
cd C:/Users/manorz/ElAlRescueFlightFinder && venv/Scripts/python -c "
import json
from crawler.price_crawler import parse_price_response

# Minimal test with structure matching real API
data = {
    'data': {'trip': {'outbound': {
        'directBounds': {'bounds': [{
            'segments': [{'carrier': 'LY', 'flightNumber': '418'}],
            'fares': [{
                'name': 'FFECOLTL1',
                'bookingClassName': 'economy',
                'netPrice': {'cash': {'amount': 298.79, 'currencyCode': 'GBP'}},
                'nbSeatLeft': 9,
                'cheapest': True,
            }]
        }]},
        'indirectBounds': {'bounds': []}
    }}}
}

prices = parse_price_response(data, 'LHR', '2026-03-28')
assert len(prices) == 1
p = prices[0]
assert p.flight_number == 'LY418'
assert p.price_amount == 298.79
assert p.price_currency == 'GBP'
assert p.cabin_class == 'economy'
assert p.is_cheapest is True
assert p.seats_in_fare == 9
print(f'OK: parsed {len(prices)} price(s) - LY418 economy £{p.price_amount}')
"
```

Expected: `OK: parsed 1 price(s) - LY418 economy £298.79`

- [ ] **Step 4: Test store_prices writes to DB**

Run:

```bash
cd C:/Users/manorz/ElAlRescueFlightFinder && venv/Scripts/python -c "
from database import init_db, get_connection
from crawler.price_crawler import store_prices
from models import FlightPrice

init_db()
conn = get_connection()

prices = [FlightPrice(
    flight_number='LY418', flight_date='2026-03-28', origin_code='LHR',
    cabin_class='economy', fare_name='FFECOLTL1', price_amount=298.79,
    price_currency='GBP', seats_in_fare=9, is_cheapest=True,
)]
count = store_prices(prices)
assert count == 1

row = conn.execute('SELECT * FROM flight_prices WHERE flight_number = ?', ('LY418',)).fetchone()
assert row is not None
assert row['price_amount'] == 298.79
print(f'OK: stored and retrieved price - £{row[\"price_amount\"]}')

# Clean up test data
conn.execute('DELETE FROM flight_prices WHERE flight_number = ?', ('LY418',))
conn.commit()
"
```

Expected: `OK: stored and retrieved price - £298.79`

- [ ] **Step 5: Commit**

```bash
git add crawler/price_crawler.py
git commit -m "feat: add price crawler module with Playwright booking flow"
```

---

### Task 4: Scheduler Integration

**Files:**
- Modify: `scheduler.py:19-84` (add run_price_crawl), `scheduler.py:95-128` (add job)

- [ ] **Step 1: Add run_price_crawl function**

Add after `run_full_crawl()` function (after line 84):

```python
def run_price_crawl():
    """Execute a price crawl cycle: look up prices for flights with available seats."""
    from crawler.price_crawler import crawl_prices
    from services.email_notifier import process_price_alerts

    conn = get_connection()
    started_at = datetime.now(timezone.utc).isoformat()

    cursor = conn.execute(
        "INSERT INTO crawl_log (started_at, status, crawl_type) VALUES (?, ?, ?)",
        (started_at, "running", "price"),
    )
    crawl_id = cursor.lastrowid
    conn.commit()

    try:
        lookups, prices_stored = crawl_prices()

        # Process price-based alerts
        process_price_alerts()

        completed_at = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """UPDATE crawl_log
               SET completed_at = ?, flights_found = ?, new_flights = ?, status = ?
               WHERE id = ?""",
            (completed_at, lookups, prices_stored, "success", crawl_id),
        )
        conn.commit()
        logger.info("Price crawl completed: %d lookups, %d prices stored", lookups, prices_stored)

    except Exception:
        logger.exception("Error during price crawl")
        completed_at = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """UPDATE crawl_log
               SET completed_at = ?, status = ?, errors = ?
               WHERE id = ?""",
            (completed_at, "error", traceback.format_exc(), crawl_id),
        )
        conn.commit()
```

- [ ] **Step 2: Add price crawl scheduler job**

In `start_scheduler()`, add after the `check_refresh` job (after line 112):

```python
    scheduler.add_job(
        run_price_crawl,
        "interval",
        minutes=config.PRICE_POLL_INTERVAL_MINUTES,
        id="price_crawl",
        replace_existing=True,
    )
```

And update the log message at line 115-118:

```python
    logger.info(
        "Scheduler started (seats every %d min, prices every %d min, refresh check every 30s)",
        config.POLL_INTERVAL_MINUTES,
        config.PRICE_POLL_INTERVAL_MINUTES,
    )
```

- [ ] **Step 3: Update get_status() to include price crawl info**

In `get_status()`, add price job info. Update the return dict:

```python
    # Price crawl timing
    price_job = scheduler.get_job("price_crawl") if scheduler.running else None
    next_price_time = price_job.next_run_time.isoformat() if price_job and price_job.next_run_time else None

    return {
        "last_crawl_time": last_crawl_time.isoformat() if last_crawl_time else None,
        "next_crawl_time": next_crawl_time.isoformat() if next_crawl_time else None,
        "next_price_crawl_time": next_price_time,
    }
```

- [ ] **Step 4: Commit**

```bash
git add scheduler.py
git commit -m "feat: add price crawl scheduler job on 6-hour interval"
```

---

### Task 5: Email Notifier - Price Alerts

**Files:**
- Modify: `services/email_notifier.py:99-167` (update email template), add `process_price_alerts()`

- [ ] **Step 1: Add process_price_alerts function**

Add after `process_alerts()` (after line 330):

```python
def process_price_alerts() -> int:
    """Check price-based alert conditions and send emails.

    Finds alerts with max_price set where the cheapest economy fare
    is at or below the threshold.

    Returns number of alert emails sent.
    """
    if not is_email_configured():
        return 0

    conn = get_connection()

    # Find alerts with price thresholds
    alerts = conn.execute(
        """SELECT id, destination_code, destination_city, trigger_date,
                  email_address, max_price, price_currency
           FROM alert_configs
           WHERE is_active = 1 AND max_price IS NOT NULL"""
    ).fetchall()

    if not alerts:
        return 0

    sent_count = 0
    for alert in alerts:
        alert_id = alert["id"]
        dest_code = alert["destination_code"]
        trigger_date = alert["trigger_date"]
        max_price = alert["max_price"]
        alert_currency = alert["price_currency"] or "USD"

        # Find flights with seats AND prices below threshold
        rows = conn.execute(
            """SELECT f.*, fp.price_amount, fp.price_currency, fp.cabin_class, fp.fare_name
               FROM flights f
               JOIN flight_prices fp ON f.flight_number = fp.flight_number
                                    AND f.flight_date = fp.flight_date
               WHERE f.origin_code = ?
                 AND f.flight_date >= ?
                 AND f.seats_available > 0
                 AND fp.is_cheapest = 1
                 AND fp.price_amount <= ?
                 AND fp.price_currency = ?
               ORDER BY f.flight_date, f.flight_time""",
            (dest_code, trigger_date, max_price, alert_currency),
        ).fetchall()

        if not rows:
            continue

        flights = [dict(row) for row in rows]

        # Check dedup - use alert_history with flight_id
        unsent = []
        for flight in flights:
            flight_id = flight.get("id")
            existing = conn.execute(
                "SELECT 1 FROM alert_history WHERE alert_config_id = ? AND flight_id = ?",
                (alert_id, flight_id),
            ).fetchone()
            if not existing:
                unsent.append(flight)

        if not unsent:
            continue

        subject, html_body = build_flight_alert_email(unsent)
        success = send_email(alert["email_address"], subject, html_body)

        if success:
            for flight in unsent:
                conn.execute(
                    "INSERT OR IGNORE INTO alert_history (alert_config_id, flight_id) VALUES (?, ?)",
                    (alert_id, flight["id"]),
                )
            conn.commit()
            sent_count += 1
            logger.info("Price alert sent to %s: %d flight(s) from %s under %s %s",
                       alert["email_address"], len(unsent), dest_code,
                       max_price, alert_currency)

    return sent_count
```

- [ ] **Step 2: Update build_flight_alert_email to show prices**

In the `rows_html` loop (lines 120-130), add a price column:

```python
    rows_html = ""
    for f in flights:
        price_str = ""
        if f.get("price_amount") is not None:
            price_str = f"{f['price_currency']} {f['price_amount']:.0f}"
        rows_html += (
            "<tr>"
            f"<td>{html_escape(str(f.get('origin_city', '')))}"
            f" ({html_escape(str(f.get('origin_code', '')))})</td>"
            f"<td>{html_escape(str(f.get('flight_number', '')))}</td>"
            f"<td>{html_escape(str(f.get('flight_date', '')))}</td>"
            f"<td>{html_escape(str(f.get('flight_time', '')))}</td>"
            f"<td>{html_escape(str(f.get('seats_available', 'N/A')))}</td>"
            f"<td>{html_escape(price_str) if price_str else '—'}</td>"
            "</tr>\n"
        )
```

And update the table header in the `html_body` template to add the Price column:

```html
            <tr>
                <th>Origin</th>
                <th>Flight #</th>
                <th>Date</th>
                <th>Time</th>
                <th>Seats</th>
                <th>Price</th>
            </tr>
```

- [ ] **Step 3: Commit**

```bash
git add services/email_notifier.py
git commit -m "feat: add price-threshold alerts and price column in alert emails"
```

---

### Task 6: API Endpoints

**Files:**
- Modify: `web/routes.py`

- [ ] **Step 1: Add GET /api/prices endpoint**

Add after the flights section (after line 151):

```python
# ---------------------------------------------------------------------------
# Prices
# ---------------------------------------------------------------------------

@api.route('/prices')
def get_prices():
    """Return latest flight prices, with optional filters."""
    conn = get_connection()
    query = "SELECT * FROM flight_prices WHERE 1=1"
    params = []

    origin = request.args.get('origin')
    if origin:
        query += " AND origin_code = ?"
        params.append(origin.upper())

    date = request.args.get('date')
    if date:
        query += " AND flight_date = ?"
        params.append(date)

    flight_number = request.args.get('flight_number')
    if flight_number:
        query += " AND flight_number = ?"
        params.append(flight_number.upper())

    query += " ORDER BY flight_date, flight_number, price_amount"

    try:
        rows = conn.execute(query, params).fetchall()
        return jsonify([row_to_dict(r) for r in rows])
    except Exception as e:
        logger.error("Error fetching prices: %s", e)
        return jsonify({"error": "Failed to fetch prices"}), 500
```

- [ ] **Step 2: Add POST /api/refresh-prices endpoint**

Add after the new prices endpoint:

```python
@api.route('/refresh-prices', methods=['POST'])
def refresh_prices():
    """Trigger a price crawl. Optionally for a specific origin+date."""
    origin = request.args.get('origin') or (request.get_json(silent=True) or {}).get('origin')
    date = request.args.get('date') or (request.get_json(silent=True) or {}).get('date')

    def _run():
        from crawler.price_crawler import crawl_prices
        crawl_prices(origin=origin, date=date)

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "started", "origin": origin, "date": date})
```

- [ ] **Step 3: Update GET /api/flights to include cheapest price**

Replace the query in `get_flights()` (line 74):

```python
    query = """SELECT f.*,
                      fp.price_amount AS cheapest_price,
                      fp.price_currency
               FROM flights f
               LEFT JOIN flight_prices fp
                   ON f.flight_number = fp.flight_number
                   AND f.flight_date = fp.flight_date
                   AND fp.is_cheapest = 1
               WHERE 1=1"""
```

**Important:** Also update all filter WHERE clauses in this function to use `f.` prefix to avoid ambiguous column names (both `flights` and `flight_prices` have `origin_code`, `flight_date`):
- `AND origin_code = ?` → `AND f.origin_code = ?`
- `AND origin_code IN (...)` → `AND f.origin_code IN (...)`
- `AND flight_date >= ?` → `AND f.flight_date >= ?`
- `AND flight_date <= ?` → `AND f.flight_date <= ?`
- `AND seats_available > 0` → `AND f.seats_available > 0`
- `ORDER BY flight_date ASC, flight_time ASC` → `ORDER BY f.flight_date ASC, f.flight_time ASC`

- [ ] **Step 4: Update PUT /api/alerts to accept max_price**

Replace `toggle_alert()` (lines 311-339) to also handle `max_price` and `price_currency`:

```python
@api.route('/alerts/<int:alert_id>', methods=['PUT'])
def update_alert(alert_id):
    """Update an alert: toggle active/inactive and/or set price threshold."""
    data = request.get_json(silent=True)
    if data is None:
        return jsonify({"error": "Request body must be JSON"}), 400

    conn = get_connection()
    try:
        row = conn.execute("SELECT id FROM alert_configs WHERE id = ?", (alert_id,)).fetchone()
        if not row:
            return jsonify({"error": "Alert not found"}), 404

        if 'is_active' in data:
            is_active = data['is_active']
            if is_active not in (0, 1):
                return jsonify({"error": "'is_active' must be 0 or 1"}), 400
            conn.execute("UPDATE alert_configs SET is_active = ? WHERE id = ?", (is_active, alert_id))

        if 'max_price' in data:
            max_price = data['max_price']
            if max_price is not None and not isinstance(max_price, (int, float)):
                return jsonify({"error": "'max_price' must be a number or null"}), 400
            conn.execute("UPDATE alert_configs SET max_price = ? WHERE id = ?", (max_price, alert_id))

        if 'price_currency' in data:
            conn.execute("UPDATE alert_configs SET price_currency = ? WHERE id = ?",
                        (data['price_currency'], alert_id))

        conn.commit()
        updated = conn.execute("SELECT * FROM alert_configs WHERE id = ?", (alert_id,)).fetchone()
        return jsonify(row_to_dict(updated))
    except Exception as e:
        logger.error("Error updating alert %s: %s", alert_id, e)
        return jsonify({"error": "Failed to update alert"}), 500
```

- [ ] **Step 5: Update POST /api/alerts to accept max_price**

In `create_alert()`, update the INSERT to include `max_price` and `price_currency` (around line 272-278):

```python
        max_price = data.get('max_price')
        price_currency = data.get('price_currency', 'USD')
        cursor = conn.execute(
            """INSERT INTO alert_configs
               (destination_code, destination_city, trigger_date, email_address, max_price, price_currency)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (data['destination_code'], data['destination_city'],
             data['trigger_date'], data['email_address'], max_price, price_currency)
        )
```

- [ ] **Step 6: Update GET /api/status to include price crawl time**

In `get_status()`, update the existing crawl_log query (around line 384-387) to filter by seat crawl type:

```python
        crawl_row = conn.execute(
            "SELECT completed_at FROM crawl_log WHERE status = 'success' "
            "AND (crawl_type = 'seats' OR crawl_type IS NULL) "
            "ORDER BY completed_at DESC LIMIT 1"
        ).fetchone()
```

Then add after it:

```python
        # Last price crawl
        price_crawl_row = conn.execute(
            "SELECT completed_at FROM crawl_log WHERE status = 'success' AND crawl_type = 'price' "
            "ORDER BY completed_at DESC LIMIT 1"
        ).fetchone()
        last_price_crawl = price_crawl_row['completed_at'] if price_crawl_row else None
```

And add `last_price_crawl` and `next_price_crawl_time` to the returned dict.

- [ ] **Step 7: Commit**

```bash
git add web/routes.py
git commit -m "feat: add price API endpoints, extend flights/alerts/status with price data"
```

---

### Task 7: Dashboard UI Updates

**Files:**
- Modify: `web/static/js/app.js` (flights table rendering, refresh button)
- Modify: `web/templates/index.html` (price column header, refresh button)

- [ ] **Step 1: Update flights table to show price column**

In `app.js`, find the function that renders the flights table rows and add a price cell. The flights data now includes `cheapest_price` and `price_currency` from the API. Add after the seats column:

```javascript
const priceCell = cheapest_price != null
    ? `${price_currency} ${Math.round(cheapest_price)}`
    : '—';
```

And add `<td>${priceCell}</td>` in the row template.

- [ ] **Step 2: Add "Refresh Prices" button**

In `index.html`, add a button next to the existing refresh button. In `app.js`, add the click handler:

```javascript
async function refreshPrices() {
    const btn = document.getElementById('refresh-prices-btn');
    btn.disabled = true;
    btn.textContent = 'Checking prices...';
    try {
        await fetch('/api/refresh-prices', { method: 'POST' });
        btn.textContent = 'Price check started';
        setTimeout(() => {
            btn.disabled = false;
            btn.textContent = 'Refresh Prices';
        }, 5000);
    } catch (e) {
        btn.textContent = 'Refresh Prices';
        btn.disabled = false;
    }
}
```

- [ ] **Step 3: Update status display to show price crawl time**

In the status rendering function, add:

```javascript
if (data.last_price_crawl) {
    // Display "Last price check: X ago" in the status area
}
```

- [ ] **Step 4: Add Price column header in index.html**

Add `<th data-sort="cheapest_price">Price</th>` after the Seats column header in the flights table.

Also update all `colspan="7"` to `colspan="8"` in both `index.html` and `app.js` (empty row, loading row, no-flights-found row).

- [ ] **Step 5: Add numeric sort handling for price column**

In `app.js`, in the `sortFlights()` function, add `cheapest_price` to the numeric sort handling alongside `seats_available`. Null prices should sort to the end.

- [ ] **Step 6: Add max_price field to alert creation form**

In `index.html`, add an optional "Max price (USD)" number input field in the alert creation form. In `app.js`, include `max_price` and `price_currency` in the POST body when creating alerts (only when the field has a value).

- [ ] **Step 7: Commit**

```bash
git add web/static/js/app.js web/templates/index.html
git commit -m "feat: show prices in dashboard, add refresh-prices button"
```

---

### Task 8: Documentation Updates

**Files:**
- Modify: `CLAUDE.md`
- Delete: `test_price_api.py`

- [ ] **Step 1: Update CLAUDE.md**

Update the following sections:
- **Database Schema**: Add `flight_prices` table description
- **API Endpoints**: Add `/api/prices` and `/api/refresh-prices` rows
- **Configuration**: Add `PRICE_POLL_INTERVAL_MINUTES` and `PRICE_MARKET`
- **Known Limitations**: Replace item 4 ("No price data") with the new ballpark pricing limitation
- **Architecture**: Add price crawler to the tree
- **Key Technical Decisions**: Add a "Price Scraping" section explaining the booking engine approach

- [ ] **Step 2: Delete test_price_api.py**

```bash
git rm test_price_api.py 2>/dev/null; rm -f test_price_api.py
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md with price scraping docs, remove test script"
```

---

### Task 9: End-to-End Verification

- [ ] **Step 1: Start the app and verify no errors**

```bash
cd C:/Users/manorz/ElAlRescueFlightFinder && venv/Scripts/python app.py
```

Verify: App starts, dashboard loads at http://127.0.0.1:5000, no crash on startup.

- [ ] **Step 2: Verify API endpoints work**

```bash
curl http://127.0.0.1:5000/api/prices
curl http://127.0.0.1:5000/api/status
curl http://127.0.0.1:5000/api/flights | python -m json.tool | head -20
```

Verify: `/api/prices` returns `[]`, `/api/status` includes `last_price_crawl` field, `/api/flights` includes `cheapest_price` field (null for now).

- [ ] **Step 3: Test on-demand price refresh**

```bash
curl -X POST http://127.0.0.1:5000/api/refresh-prices
```

Verify: Returns `{"status": "started"}`. Check app.log for price crawl activity.

- [ ] **Step 4: Commit any fixes needed**

```bash
git add -A && git commit -m "fix: end-to-end verification fixes"
```
