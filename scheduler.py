import logging
import threading
import traceback
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler

import config
from database import get_connection

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()
_refresh_requested = threading.Event()
last_crawl_time: datetime | None = None
next_crawl_time: datetime | None = None


def run_full_crawl():
    """Execute a full crawl cycle: seat availability, news, and alerts."""
    global last_crawl_time

    # Lazy imports to avoid circular dependencies
    from crawler.seat_availability import crawl_seat_availability
    from crawler.news_monitor import crawl_news
    from services.email_notifier import process_alerts

    conn = get_connection()
    started_at = datetime.now(timezone.utc).isoformat()

    # Record crawl start
    cursor = conn.execute(
        "INSERT INTO crawl_log (started_at, status) VALUES (?, ?)",
        (started_at, "running"),
    )
    crawl_id = cursor.lastrowid
    conn.commit()

    try:
        # Step 1: Crawl seat availability
        logger.info("Starting seat availability crawl...")
        total, new_count = crawl_seat_availability()
        logger.info("Seat availability crawl complete: %d total, %d new", total, new_count)

        # Step 2: Crawl news page
        logger.info("Starting news crawl...")
        has_changed, snapshot = crawl_news()
        if has_changed:
            logger.info("News page content has changed")

        # Step 3: If new flights found, process email alerts
        if new_count > 0:
            logger.info("Processing alerts for %d new flights...", new_count)
            new_flights = conn.execute(
                "SELECT * FROM flights WHERE is_new = 1"
            ).fetchall()
            process_alerts(new_flights)

        # Step 4: Update crawl log with results
        completed_at = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """UPDATE crawl_log
               SET completed_at = ?, flights_found = ?, new_flights = ?, status = ?
               WHERE id = ?""",
            (completed_at, total, new_count, "success", crawl_id),
        )

        # Step 5: Mark all flights as no longer new
        conn.execute("UPDATE flights SET is_new = 0 WHERE is_new = 1")
        conn.commit()

        last_crawl_time = datetime.now(timezone.utc)
        logger.info("Full crawl completed successfully")

    except Exception:
        logger.exception("Error during full crawl")
        completed_at = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """UPDATE crawl_log
               SET completed_at = ?, status = ?, errors = ?
               WHERE id = ?""",
            (completed_at, "error", traceback.format_exc(), crawl_id),
        )
        conn.commit()


def check_refresh():
    """Check if a manual refresh has been requested and run a crawl if so."""
    if _refresh_requested.is_set():
        _refresh_requested.clear()
        logger.info("Manual refresh triggered, starting crawl...")
        run_full_crawl()


def start_scheduler():
    """Configure and start the background scheduler."""
    global next_crawl_time

    scheduler.add_job(
        run_full_crawl,
        "interval",
        minutes=config.POLL_INTERVAL_MINUTES,
        id="full_crawl",
        replace_existing=True,
    )
    scheduler.add_job(
        check_refresh,
        "interval",
        seconds=30,
        id="check_refresh",
        replace_existing=True,
    )

    scheduler.start()
    logger.info(
        "Scheduler started (crawl every %d min, refresh check every 30s)",
        config.POLL_INTERVAL_MINUTES,
    )

    # Update next_crawl_time from scheduler
    job = scheduler.get_job("full_crawl")
    if job and job.next_run_time:
        next_crawl_time = job.next_run_time

    # Run initial crawl immediately in a background thread
    initial_thread = threading.Thread(target=run_full_crawl, daemon=True)
    initial_thread.start()


def stop_scheduler():
    """Shut down the scheduler gracefully."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")


def trigger_refresh():
    """Signal that a manual refresh is requested (used by /api/refresh)."""
    _refresh_requested.set()
    logger.info("Refresh requested")


def get_status() -> dict:
    """Return current scheduler status information."""
    global next_crawl_time

    # Update next_crawl_time from scheduler if running
    if scheduler.running:
        job = scheduler.get_job("full_crawl")
        if job and job.next_run_time:
            next_crawl_time = job.next_run_time

    return {
        "last_crawl_time": last_crawl_time.isoformat() if last_crawl_time else None,
        "next_crawl_time": next_crawl_time.isoformat() if next_crawl_time else None,
    }
