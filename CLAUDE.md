# El Al Rescue Flight Finder

## Overview

Windows desktop app that monitors El Al's website for available rescue/recovery flights TO Israel during Operation "Roaring Lion" (Iran conflict, March 2026). Aggregates seat availability in a local web dashboard and sends email alerts when flights with available seats appear from user-specified origins.

## Quick Start

```bash
# Setup (first time)
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium

# Run
python app.py
# Or: run.bat
```

Dashboard opens at `http://127.0.0.1:5000`. System tray icon provides quick access.

### Star Alliance Setup (Tokyo → Israel)

```bash
python setup_alerts.py --date 2026-03-20 --email you@gmail.com
# Or combined with app launch:
setup_and_run.bat
```

Auto-creates 25 alerts for El Al origins reachable from Tokyo via Star Alliance connections. On creation, immediately checks existing flights and sends email alerts for any with available seats.

## Architecture

```
app.py (entry point)
  ├── Flask web dashboard (localhost:5000)
  │     ├── web/routes.py        - API endpoints + serves index.html
  │     ├── web/templates/       - Dashboard HTML
  │     └── web/static/          - CSS + vanilla JS
  ├── APScheduler (hourly crawls)
  │     └── scheduler.py         - Orchestrates crawl cycle
  ├── Crawlers
  │     ├── crawler/seat_availability.py  - Playwright-based API fetch
  │     └── crawler/news_monitor.py       - News page content monitor
  ├── Services
  │     └── services/email_notifier.py    - Gmail SMTP alerts
  ├── System tray (pystray)
  │     └── tray_app.py
  ├── Setup scripts
  │     ├── setup_alerts.py      - Star Alliance bulk alert creator
  │     └── setup_and_run.bat    - Setup + launch combined
  └── SQLite database
        └── data/flights.db
```

## Key Technical Decisions

### Bot Protection Bypass

The El Al API (`/api/SeatAvailability/lang/eng/flights`) has JavaScript bot protection that blocks plain HTTP requests. The crawler uses **Playwright headless Chromium** as the primary fetch method:

1. Launches headless Chromium with `--disable-blink-features=AutomationControlled`
2. Navigates to the seat availability page (triggers the Angular app)
3. Intercepts the API response from network traffic via `page.on("response")`
4. Falls back to in-page `fetch()` if interception misses it
5. Falls back to plain HTTP as a last resort

This is implemented in `crawler/seat_availability.py:fetch_via_playwright()`.

### API Response Structure

The seat availability API (`/api/SeatAvailability/lang/eng/flights`) returns `flightsToIsrael[]` and `flightsFromIsrael[]`. Each origin has `flights[]`, each flight has `flightsDates[]` with `{flightsDate, seatCount?}`.

Parsing rules:
- Only process `flightsToIsrael` array
- A `flightsDates` entry with no `seatCount` means no flight on that date
- Dates are in `DD.MM` format, converted to `YYYY-MM-DD` for storage
- `seatCount: 9` typically means "9+" (display shows "9+" on elal.com)

### Alert System

- Alerts trigger on flights with `seats_available > 0` only
- When a new alert is created, existing matching flights are checked immediately (not just on next crawl)
- Alert deduplication via `alert_history` table prevents repeat emails
- Email settings configurable from dashboard UI (persisted to `.env`)

## Database Schema (SQLite)

6 tables in `data/flights.db`:
- **flights** - Each flight+date combination with seat count. UNIQUE(flight_number, flight_date).
- **destinations** - All El Al destinations with operational status
- **alert_configs** - User alert rules (origin + trigger_date + email)
- **alert_history** - Tracks which alerts were sent to prevent duplicates
- **news_snapshots** - Historical news page content with change detection
- **crawl_log** - Crawl run history for status display

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Dashboard HTML |
| GET | `/api/flights` | All flights (filters: origin, date_from, date_to, available_only) |
| GET | `/api/flights/new` | Newly discovered flights (is_new=1) |
| GET | `/api/destinations` | All destinations with operational status |
| GET | `/api/alerts` | List alert configurations |
| POST | `/api/alerts` | Create alert (sends immediate check) |
| PUT | `/api/alerts/<id>` | Toggle alert active/inactive |
| DELETE | `/api/alerts/<id>` | Delete alert |
| GET | `/api/news` | Latest news snapshot |
| GET | `/api/status` | App status (crawl times, counts) |
| POST | `/api/refresh` | Trigger immediate crawl |
| POST | `/api/clear-log` | Clear app.log and start fresh |
| GET | `/api/email-settings` | Email config status (no secrets) |
| POST | `/api/email-settings` | Update SMTP credentials |

## Configuration

All settings in `.env` (loaded by `config.py`):
- `SMTP_SERVER`, `SMTP_PORT` - Gmail SMTP (default: smtp.gmail.com:587)
- `SMTP_USERNAME`, `SMTP_PASSWORD` - Gmail credentials (App Password required)
- `POLL_INTERVAL_MINUTES` - Crawl frequency (default: 60)
- `NEWS_POLL_INTERVAL_MINUTES` - News check frequency (default: 30)
- `FLASK_PORT` - Dashboard port (default: 5000)

## Tech Stack

- Python 3.11+
- Flask 3.1 - web dashboard
- APScheduler 3.10 - periodic crawling
- Playwright 1.52 - headless browser for API access
- SQLite - local persistence
- BeautifulSoup4 - news HTML parsing
- pystray + Pillow - Windows system tray
- Vanilla JS - dashboard frontend (no frameworks)

## Known Limitations

1. **Bot protection** - Direct HTTP requests to El Al APIs are blocked. Playwright is required for automated crawling. If Playwright breaks, the app gracefully degrades (shows stale data).
2. **Seat count cap** - The API reports max 9 seats ("9+"). Actual availability may be higher.
3. **News parser** - The news page HTML structure may change; regex-based parsing is brittle.
4. **No price data** - Only seat availability, not prices. Booking must be done on elal.com.
5. **Single user** - Designed for local single-user use, not multi-tenant.
