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


def _send_initial_alerts(conn, trigger_date, email_address, origin_codes):
    """Check existing flights against newly created alerts and send email notifications."""
    from services.email_notifier import is_email_configured, send_email, build_flight_alert_email

    if not is_email_configured():
        print("Email not configured - skipping initial alert emails.")
        print("Configure email in .env or via the dashboard, then alerts will fire on next crawl.")
        return

    # Get alert IDs for these origins
    placeholders = ",".join(["?" for _ in origin_codes])
    alert_rows = conn.execute(
        f"SELECT id, destination_code FROM alert_configs WHERE destination_code IN ({placeholders}) "
        "AND trigger_date = ? AND email_address = ?",
        (*origin_codes, trigger_date, email_address),
    ).fetchall()
    alert_by_code = {row["destination_code"]: row["id"] for row in alert_rows}

    if not alert_by_code:
        return

    # Find all matching flights with available seats
    matching = conn.execute(
        f"""SELECT * FROM flights
        WHERE origin_code IN ({placeholders}) AND flight_date >= ? AND seats_available > 0
        ORDER BY origin_code, flight_date, flight_time""",
        (*origin_codes, trigger_date),
    ).fetchall()

    if not matching:
        print("No existing flights with available seats match these alerts.")
        return

    # Exclude flights already notified (in alert_history)
    already_notified = set()
    for row in conn.execute("SELECT alert_config_id, flight_id FROM alert_history").fetchall():
        already_notified.add((row["alert_config_id"], row["flight_id"]))

    flights = []
    for row in matching:
        f = dict(row)
        alert_id = alert_by_code.get(f["origin_code"])
        if alert_id and (alert_id, f["id"]) not in already_notified:
            flights.append(f)

    if not flights:
        print("No new flights to notify about (all matching flights already notified).")
        return

    print(f"Found {len(flights)} new flight(s) with available seats - sending alert email...")

    subject, html_body = build_flight_alert_email(flights)
    success = send_email(email_address, subject, html_body)

    if success:
        # Record in alert_history to prevent duplicate emails on next crawl/run
        for flight in flights:
            alert_id = alert_by_code.get(flight["origin_code"])
            if alert_id:
                conn.execute(
                    "INSERT OR IGNORE INTO alert_history (alert_config_id, flight_id) VALUES (?, ?)",
                    (alert_id, flight["id"]),
                )
        conn.commit()
        print(f"Alert email sent to {email_address}")
    else:
        print("Failed to send alert email. Check your email configuration.")


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

    origin_codes = [o["code"] for o in origins]

    # Check existing flights and send initial alert emails
    # Always run — alert_history prevents duplicate emails for already-notified flights
    _send_initial_alerts(conn, args.date, args.email, origin_codes)

    # Write dashboard defaults
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
