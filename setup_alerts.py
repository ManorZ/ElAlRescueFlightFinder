"""
Setup script to pre-configure alerts for Star Alliance origins reachable from Tokyo.

Usage:
    python setup_alerts.py --date 2026-03-20 --email you@gmail.com
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import init_db, get_connection


def main():
    parser = argparse.ArgumentParser(description="Setup alerts for Star Alliance origins from Tokyo")
    parser.add_argument("--date", required=True, help="Trigger date for alerts (YYYY-MM-DD)")
    parser.add_argument("--email", required=True, help="Email address for alerts")
    parser.add_argument("--config", default="data/star_alliance_origins.json",
                        help="Path to Star Alliance origins JSON")
    args = parser.parse_args()

    # Load origins
    with open(args.config, "r", encoding="utf-8") as f:
        config = json.load(f)

    origins = config["origins"]
    print(f"Loaded {len(origins)} Star Alliance origins from {args.config}")

    # Init DB
    init_db()
    conn = get_connection()

    # Ensure all origins exist in destinations table
    for origin in origins:
        conn.execute(
            """INSERT OR IGNORE INTO destinations (code, city_name, country_name, is_operational, is_recovery_flight_origin, last_updated)
            VALUES (?, ?, ?, 1, 0, datetime('now'))""",
            (origin["code"], origin["city"], origin["country"]),
        )

    # Create alerts (skip if identical alert already exists)
    created = 0
    skipped = 0
    for origin in origins:
        existing = conn.execute(
            "SELECT id FROM alert_configs WHERE destination_code = ? AND trigger_date = ? AND email_address = ?",
            (origin["code"], args.date, args.email),
        ).fetchone()

        if existing:
            skipped += 1
            continue

        conn.execute(
            "INSERT INTO alert_configs (destination_code, destination_city, trigger_date, email_address, is_active) "
            "VALUES (?, ?, ?, ?, 1)",
            (origin["code"], origin["city"], args.date, args.email),
        )
        created += 1

    conn.commit()

    # Write dashboard defaults
    origin_codes = [o["code"] for o in origins]
    defaults = {
        "selected_origins": origin_codes,
        "available_only": True,
    }
    defaults_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "dashboard_defaults.json")
    with open(defaults_path, "w", encoding="utf-8") as f:
        json.dump(defaults, f, indent=2)

    print(f"Created {created} alerts, skipped {skipped} duplicates")
    print(f"Dashboard defaults saved to {defaults_path}")
    print(f"Origin filter will pre-select: {', '.join(origin_codes)}")
    print(f"\nRun 'python app.py' to start the dashboard.")


if __name__ == "__main__":
    main()
