# Price Scraping Design

## Overview

Add flight price data to the El Al Rescue Flight Finder by scraping the booking engine at `booking.elal.com`. Prices are ballpark estimates (economy base fare) — not exact totals with taxes. This enables price-aware decision-making and price-threshold alerts.

## Data Source

The El Al seat availability API (`/api/SeatAvailability/lang/eng/flights`) does not include prices. Prices come from the separate booking engine at `booking.elal.com`, accessed via a multi-step Playwright flow:

1. Navigate to `elal.com/eng/book-a-flight/` (establishes session cookies)
2. Fill the search form: one-way, origin -> TLV, specific date, 1 adult
3. Submit -> browser navigates to `booking.elal.com/booking/flights`
4. Intercept two API responses via Playwright's `page.route()` (intercept-and-passthrough pattern: `route.fetch()` to get the response, read the body, then `route.fulfill({ response })` to forward it to the page):
   - `POST /bfm/service/extly/booking/search/cash/fast` — initial search. Used to detect "no flights on this date" (error code 102 = "availability found for closest dates") vs. success (empty errors array). When error 102 is returned, the `closestDateFound` field indicates where flights exist but we skip — we only price-check the exact date requested.
   - `GET /bfm/service/extly/booking/search/cash/outbound` — detailed flight+fare results with prices. This is the primary data source.

Direct access to `booking.elal.com` is blocked — the session must flow from the main site's search widget.

### API Response Structure

The `cash/outbound` endpoint returns:
```json
{
  "data": {
    "trip": {
      "outbound": {
        "directBounds": {
          "bounds": [{
            "id": "20260328_LHR_1",
            "flightId": 1,
            "duration": 16500,
            "segments": [{
              "flightNumber": "418",
              "carrier": "LY",
              "departureDate": "2026-03-28T22:20:00.000Z",
              "departureAirport": { "code": "LHR" },
              "arrivalAirport": { "code": "TLV" }
            }],
            "fares": [{
              "name": "FFECOLTL1",
              "bookingClassName": "economy",
              "cabinTypeName": "cabin_economy",
              "netPrice": {
                "cash": { "amount": 298.79, "currencyCode": "GBP" }
              },
              "nbSeatLeft": 9,
              "cheapest": true
            }]
          }]
        },
        "indirectBounds": { "bounds": [...] }
      }
    }
  }
}
```

Each bound has multiple `fares` with different cabin classes (economy, premium, business) and fare families.

## Architecture

### New Module: `crawler/price_crawler.py`

Fully independent from `crawler/seat_availability.py`. Own Playwright lifecycle, own crawl cycle.

**Crawl cycle:**
1. Query DB for distinct `(origin_code, flight_date)` pairs where `seats_available > 0`
2. For each pair, run the booking search flow (reusing browser context between lookups)
3. Parse fares from the `cash/outbound` response
4. Store in `flight_prices` table
5. Check price-based alert conditions
6. Log to `crawl_log` with `crawl_type = 'price'`

**Per-lookup flow:**
1. Navigate to `elal.com/eng/book-a-flight/`
2. Select one-way, set origin, destination TLV, date, 1 adult
3. Submit search
4. Intercept `cash/fast` and `cash/outbound` via `page.route()` (intercept-and-passthrough)
5. Parse fares, return structured data
6. Handle errors: timeout after 30s per lookup, log and skip on failure, continue with next origin+date

**Error handling:**
- Search form changes (field IDs, structure): log warning, skip lookup, flag for manual investigation
- "No flights found" for a valid origin+date: `cash/fast` returns error code 102 — log as info, skip
- Session cookie expires mid-crawl: detect via HTTP 401/520 response, restart browser context
- `cash/outbound` never arrives: timeout after 30s, log warning, skip
- Playwright crashes: catch exception, log error, return partial results collected so far

**Rate/volume:** ~25 seconds per origin+date lookup. With 15 origins x 3 dates = ~19 minutes max. Acceptable for a 6-hour cycle.

### On-Demand Trigger

`POST /api/refresh-prices` with optional `origin` and `date` query params. Without params, runs the full cycle. With params, does a single lookup.

**Async execution:** The full price crawl can take up to 19 minutes. The endpoint runs the crawl in a background thread (same pattern as the existing scheduler jobs) and returns immediately with `{"status": "started"}`. The crawl status is visible via `GET /api/status`.

## Database Changes

### New Table: `flight_prices`

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
```

Uses `INSERT OR REPLACE` semantics — each fare is upserted with the latest price. The `fetched_at` timestamp tracks when the price was last updated. This keeps the table bounded (one row per flight+date+fare) while still showing how fresh the data is.

**Index** for the flights LEFT JOIN and API queries:
```sql
CREATE INDEX IF NOT EXISTS idx_flight_prices_lookup
    ON flight_prices(flight_number, flight_date, is_cheapest);
```

**New model** in `models.py`:
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

### Modified Table: `crawl_log`

Add `crawl_type` column:
```sql
ALTER TABLE crawl_log ADD COLUMN crawl_type TEXT DEFAULT 'seats';
```

### Modified Table: `alert_configs`

Add optional price threshold:
```sql
ALTER TABLE alert_configs ADD COLUMN max_price REAL;
ALTER TABLE alert_configs ADD COLUMN price_currency TEXT DEFAULT 'USD';
```

### Migration Strategy

All schema changes run in `init_db()` in `database.py`. Since SQLite lacks `ADD COLUMN IF NOT EXISTS`, use `try/except` blocks around each `ALTER TABLE` to handle "duplicate column name" errors gracefully. The new `CREATE TABLE` and `CREATE INDEX` statements use `IF NOT EXISTS` as the existing tables do.

## Scheduler Integration

New APScheduler job: price crawl every 6 hours (configurable via `PRICE_POLL_INTERVAL_MINUTES` in `.env`, default 360).

Runs alongside existing jobs:
- Seat availability: every 60 min
- News monitor: every 30 min
- **Price crawl: every 360 min**

New top-level function `run_price_crawl()` in `scheduler.py`, separate from `run_full_crawl()`. It calls `crawl_prices()` from the price crawler module and then runs alert processing for price-based alerts. Registered as its own APScheduler interval job.

## Alert System Changes

Alert trigger logic extends from:
- seats > 0 for matching origin+date

To:
- seats > 0 AND (max_price IS NULL OR cheapest economy price <= max_price)

When `max_price` is NULL, behavior is unchanged (backward-compatible).

**Currency handling:** Price comparisons only apply when the crawled price currency matches `alert_configs.price_currency`. The price crawler uses a fixed market (default `US` = USD). Configurable via `PRICE_MARKET` in `.env`. This avoids cross-currency comparison bugs.

Email template adds a price line when available: "Economy from £298.79 GBP (9 seats)".

## API Changes

### New Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/prices` | Latest prices (filters: origin, date, flight_number) |
| POST | `/api/refresh-prices` | Trigger on-demand price crawl (optional origin, date) |

### Modified Endpoints

| Method | Path | Change |
|--------|------|--------|
| GET | `/api/flights` | Include `cheapest_price`, `price_currency` via LEFT JOIN on `(flight_number, flight_date)` where `is_cheapest = 1` |
| POST | `/api/alerts` | Accept `max_price`, `price_currency` |
| PUT | `/api/alerts/<id>` | Extend from toggle-only to accept `max_price`, `price_currency` alongside `is_active` |
| GET | `/api/status` | Include last price crawl time |

## Dashboard UI Changes

- Flights table: new "Price" column showing cheapest economy fare (or "—" if not fetched)
- Alert form: optional "Max price" field
- Status bar: "Last price check: X ago"
- "Refresh Prices" button next to existing "Refresh"

## Configuration

New `.env` variables:
- `PRICE_POLL_INTERVAL_MINUTES` — price crawl frequency (default: 360)
- `PRICE_MARKET` — market code for booking API, determines currency (default: `US` = USD)

## Known Limitations

1. **Prices are ballpark** — `netPrice.cash.amount` is the base fare displayed in the booking flow. Final price with taxes/fees may differ. Booking must still be done on elal.com.
2. **Session-dependent** — the booking engine requires navigating from elal.com's search widget. Direct API access is blocked. If El Al changes the search widget or booking flow, the price crawler will break independently of the seat crawler.
3. **Currency fixed per deployment** — the API returns prices in the market's currency. We use a single market (`PRICE_MARKET` env var, default `US` = USD) for all price lookups, so all prices are in the same currency. Changing market requires reconfiguring `.env`.
4. **One lookup per origin+date** — no batch API. Each price check requires a full search form submission (~25s).
5. **No historical price trends** — we store snapshots but don't yet analyze trends or predict price changes.
