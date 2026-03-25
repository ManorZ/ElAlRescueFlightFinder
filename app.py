"""
El Al Rescue Flight Finder - Main Entry Point

Starts the Flask web dashboard, APScheduler crawler, and system tray icon.
"""

import logging
import threading
import webbrowser
import sys
import os
from datetime import datetime

# Ensure the project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from database import init_db
from web import create_app
import scheduler

HEADLESS = os.environ.get("HEADLESS", "").lower() in ("1", "true", "yes") or (
    os.name != "nt" and not os.environ.get("DISPLAY")
)

LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.log")

# Clear previous log and start fresh each session
with open(LOG_FILE, "w", encoding="utf-8") as f:
    f.write(f"{'=' * 72}\n")
    f.write(f"  SESSION START: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    f.write(f"{'=' * 72}\n\n")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


def main():
    logger.info("Starting El Al Rescue Flight Finder...")

    # Step 1: Initialize database
    logger.info("Initializing database...")
    init_db()

    # Step 2: Create Flask app
    app = create_app()

    # Step 3: Start scheduler (runs initial crawl in background thread)
    logger.info("Starting scheduler...")
    scheduler.start_scheduler()

    # Step 4: Start Flask server in a daemon thread
    flask_thread = threading.Thread(
        target=lambda: app.run(
            host=config.FLASK_HOST,
            port=config.FLASK_PORT,
            debug=False,
            use_reloader=False,
        ),
        daemon=True,
    )
    flask_thread.start()
    logger.info("Dashboard available at http://%s:%d", config.FLASK_HOST, config.FLASK_PORT)

    # Step 5: Open browser to dashboard (skip in headless mode)
    if not HEADLESS:
        webbrowser.open(f"http://{config.FLASK_HOST}:{config.FLASK_PORT}")

    # Step 6: Keep the app alive
    shutdown_event = threading.Event()
    try:
        if HEADLESS:
            logger.info("Running in headless mode (no system tray)")
            shutdown_event.wait()
        else:
            from tray_app import run_tray
            try:
                logger.info("Starting system tray icon...")
                run_tray(shutdown_event)
            except Exception:
                logger.exception("Tray icon failed, falling back to console mode")
                logger.info("Press Ctrl+C to exit...")
                shutdown_event.wait()
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
    finally:
        logger.info("Shutting down...")
        scheduler.stop_scheduler()
        logger.info("Goodbye!")


if __name__ == "__main__":
    main()
