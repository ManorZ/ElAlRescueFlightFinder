import json
import logging
import threading
from flask import Blueprint, request, jsonify, render_template

from database import get_connection
import config

logger = logging.getLogger(__name__)

api = Blueprint('api', __name__, url_prefix='/api')


def _send_initial_alert(alert_id, destination_code, trigger_date, email_address):
    """Check existing flights and send an alert for a newly created alert config."""
    def _do_send():
        from services.email_notifier import is_email_configured, send_email, build_flight_alert_email
        if not is_email_configured():
            logger.info("Email not configured - skipping initial alert for alert %s", alert_id)
            return

        conn = get_connection()
        matching = conn.execute(
            """SELECT * FROM flights
            WHERE origin_code = ? AND flight_date >= ? AND seats_available > 0
            ORDER BY flight_date, flight_time""",
            (destination_code, trigger_date),
        ).fetchall()

        if not matching:
            logger.info("No existing flights match new alert %s", alert_id)
            return

        flights = [dict(row) for row in matching]
        subject, html_body = build_flight_alert_email(flights)
        success = send_email(email_address, subject, html_body)

        if success:
            for flight in flights:
                conn.execute(
                    "INSERT OR IGNORE INTO alert_history (alert_config_id, flight_id) VALUES (?, ?)",
                    (alert_id, flight["id"]),
                )
            conn.commit()
            logger.info("Initial alert sent to %s for %d flights from %s",
                        email_address, len(flights), destination_code)

    threading.Thread(target=_do_send, daemon=True).start()
main = Blueprint('main', __name__)


def row_to_dict(row):
    """Convert a sqlite3.Row to a plain dict."""
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@main.route('/')
def index():
    return render_template('index.html')


# ---------------------------------------------------------------------------
# Flights
# ---------------------------------------------------------------------------

@api.route('/flights')
def get_flights():
    """Return all flights TO Israel, with optional filters."""
    conn = get_connection()
    query = "SELECT * FROM flights WHERE 1=1"
    params = []

    origin = request.args.get('origin')
    if origin:
        origins = [o.strip().upper() for o in origin.split(',') if o.strip()]
        if len(origins) == 1:
            query += " AND origin_code = ?"
            params.append(origins[0])
        elif origins:
            placeholders = ','.join(['?' for _ in origins])
            query += f" AND origin_code IN ({placeholders})"
            params.extend(origins)

    date_from = request.args.get('date_from')
    if date_from:
        query += " AND flight_date >= ?"
        params.append(date_from)

    date_to = request.args.get('date_to')
    if date_to:
        query += " AND flight_date <= ?"
        params.append(date_to)

    available_only = request.args.get('available_only')
    if available_only and available_only.lower() in ('1', 'true', 'yes'):
        query += " AND seats_available > 0"

    query += " ORDER BY flight_date ASC, flight_time ASC"

    try:
        rows = conn.execute(query, params).fetchall()
        return jsonify([row_to_dict(r) for r in rows])
    except Exception as e:
        logger.error("Error fetching flights: %s", e)
        return jsonify({"error": "Failed to fetch flights"}), 500


@api.route('/flights/new')
def get_new_flights():
    """Return newly discovered flights (is_new = 1), with optional filters."""
    conn = get_connection()
    query = "SELECT * FROM flights WHERE is_new = 1"
    params = []

    origin = request.args.get('origin')
    if origin:
        origins = [o.strip().upper() for o in origin.split(',') if o.strip()]
        if len(origins) == 1:
            query += " AND origin_code = ?"
            params.append(origins[0])
        elif origins:
            placeholders = ','.join(['?' for _ in origins])
            query += f" AND origin_code IN ({placeholders})"
            params.extend(origins)

    date_from = request.args.get('date_from')
    if date_from:
        query += " AND flight_date >= ?"
        params.append(date_from)

    date_to = request.args.get('date_to')
    if date_to:
        query += " AND flight_date <= ?"
        params.append(date_to)

    available_only = request.args.get('available_only')
    if available_only and available_only.lower() in ('1', 'true', 'yes'):
        query += " AND seats_available > 0"

    query += " ORDER BY flight_date ASC, flight_time ASC"

    try:
        rows = conn.execute(query, params).fetchall()
        return jsonify([row_to_dict(r) for r in rows])
    except Exception as e:
        logger.error("Error fetching new flights: %s", e)
        return jsonify({"error": "Failed to fetch new flights"}), 500


# ---------------------------------------------------------------------------
# Destinations
# ---------------------------------------------------------------------------

@api.route('/destinations')
def get_destinations():
    """Return all destinations with operational status."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT code, city_name, country_name, is_operational, "
            "is_recovery_flight_origin FROM destinations"
        ).fetchall()
        return jsonify([row_to_dict(r) for r in rows])
    except Exception as e:
        logger.error("Error fetching destinations: %s", e)
        return jsonify({"error": "Failed to fetch destinations"}), 500


@api.route('/all-destinations')
def get_all_destinations():
    """Fetch all El Al destinations from the external API, cache in DB, and return."""
    import requests as http_requests
    try:
        resp = http_requests.get(
            config.DESTINATIONS_URL,
            headers={"User-Agent": config.USER_AGENT},
            timeout=config.REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning("Could not fetch destinations from El Al API: %s", e)
        # Fall back to whatever is in the DB
        conn = get_connection()
        rows = conn.execute(
            "SELECT code, city_name, country_name, continent, is_operational, "
            "is_recovery_flight_origin FROM destinations ORDER BY city_name"
        ).fetchall()
        return jsonify([row_to_dict(r) for r in rows])

    items = data if isinstance(data, list) else data.get("destinations", data.get("Destinations", []))

    conn = get_connection()
    results = []
    for item in items:
        if not isinstance(item, dict):
            continue
        code = (item.get("destinationCode") or item.get("code") or
                item.get("Code") or item.get("airportCode") or "")
        city_name = (item.get("cityName") or item.get("CityName") or
                     item.get("city") or "")
        country_name = (item.get("countryName") or item.get("CountryName") or
                        item.get("country") or "")
        continent = (item.get("continentName") or item.get("continent") or
                     item.get("Continent") or "")
        if not code or not city_name:
            continue

        # Upsert into destinations - preserve existing is_recovery_flight_origin
        conn.execute(
            """INSERT INTO destinations (code, city_name, country_name, continent,
                                        is_operational, is_recovery_flight_origin, last_updated)
            VALUES (?, ?, ?, ?, 1, 0, datetime('now'))
            ON CONFLICT(code) DO UPDATE SET
                city_name = excluded.city_name,
                country_name = excluded.country_name,
                continent = excluded.continent,
                last_updated = excluded.last_updated
            """,
            (code, city_name, country_name, continent),
        )
        results.append({
            "code": code,
            "city_name": city_name,
            "country_name": country_name,
            "continent": continent,
        })
    conn.commit()

    # Now return all destinations from DB (includes is_recovery_flight_origin status)
    rows = conn.execute(
        "SELECT code, city_name, country_name, continent, is_operational, "
        "is_recovery_flight_origin FROM destinations ORDER BY city_name"
    ).fetchall()
    return jsonify([row_to_dict(r) for r in rows])


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------

@api.route('/alerts', methods=['GET'])
def list_alerts():
    """Return all alert configurations."""
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM alert_configs ORDER BY created_at DESC").fetchall()
        return jsonify([row_to_dict(r) for r in rows])
    except Exception as e:
        logger.error("Error fetching alerts: %s", e)
        return jsonify({"error": "Failed to fetch alerts"}), 500


@api.route('/alerts', methods=['POST'])
def create_alert():
    """Create a new alert configuration."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    required_fields = ['destination_code', 'destination_city', 'trigger_date', 'email_address']
    missing = [f for f in required_fields if not data.get(f)]
    if missing:
        return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400

    conn = get_connection()
    try:
        cursor = conn.execute(
            "INSERT INTO alert_configs (destination_code, destination_city, trigger_date, email_address) "
            "VALUES (?, ?, ?, ?)",
            (data['destination_code'], data['destination_city'],
             data['trigger_date'], data['email_address'])
        )
        conn.commit()
        alert_id = cursor.lastrowid
        row = conn.execute(
            "SELECT * FROM alert_configs WHERE id = ?", (alert_id,)
        ).fetchone()

        # Immediately check existing flights that match this new alert
        _send_initial_alert(alert_id, data['destination_code'], data['trigger_date'], data['email_address'])

        return jsonify(row_to_dict(row)), 201
    except Exception as e:
        logger.error("Error creating alert: %s", e)
        return jsonify({"error": "Failed to create alert"}), 500


@api.route('/alerts/<int:alert_id>', methods=['DELETE'])
def delete_alert(alert_id):
    """Delete an alert configuration."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT id FROM alert_configs WHERE id = ?", (alert_id,)).fetchone()
        if not row:
            return jsonify({"error": "Alert not found"}), 404

        conn.execute("DELETE FROM alert_history WHERE alert_config_id = ?", (alert_id,))
        conn.execute("DELETE FROM alert_configs WHERE id = ?", (alert_id,))
        conn.commit()
        return '', 204
    except Exception as e:
        logger.error("Error deleting alert %s: %s", alert_id, e)
        return jsonify({"error": "Failed to delete alert"}), 500


@api.route('/alerts/<int:alert_id>', methods=['PUT'])
def toggle_alert(alert_id):
    """Toggle an alert active/inactive."""
    data = request.get_json(silent=True)
    if data is None or 'is_active' not in data:
        return jsonify({"error": "Request body must contain 'is_active' (0 or 1)"}), 400

    is_active = data['is_active']
    if is_active not in (0, 1):
        return jsonify({"error": "'is_active' must be 0 or 1"}), 400

    conn = get_connection()
    try:
        row = conn.execute("SELECT id FROM alert_configs WHERE id = ?", (alert_id,)).fetchone()
        if not row:
            return jsonify({"error": "Alert not found"}), 404

        conn.execute(
            "UPDATE alert_configs SET is_active = ? WHERE id = ?",
            (is_active, alert_id)
        )
        conn.commit()
        updated = conn.execute(
            "SELECT * FROM alert_configs WHERE id = ?", (alert_id,)
        ).fetchone()
        return jsonify(row_to_dict(updated))
    except Exception as e:
        logger.error("Error toggling alert %s: %s", alert_id, e)
        return jsonify({"error": "Failed to update alert"}), 500


# ---------------------------------------------------------------------------
# News
# ---------------------------------------------------------------------------

@api.route('/news')
def get_news():
    """Return the latest news snapshot with parsed JSON fields."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM news_snapshots ORDER BY captured_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            return jsonify({"error": "No news data available"}), 404

        result = row_to_dict(row)

        # Parse JSON string fields into actual lists
        for field in ('non_operational_destinations', 'recovery_flight_origins'):
            raw = result.get(field)
            if raw:
                try:
                    result[field] = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    pass  # keep original string if parsing fails

        return jsonify(result)
    except Exception as e:
        logger.error("Error fetching news: %s", e)
        return jsonify({"error": "Failed to fetch news"}), 500


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

@api.route('/status')
def get_status():
    """Return application status summary."""
    conn = get_connection()
    try:
        # Last crawl info
        crawl_row = conn.execute(
            "SELECT completed_at FROM crawl_log WHERE status = 'success' "
            "ORDER BY completed_at DESC LIMIT 1"
        ).fetchone()
        last_crawl = crawl_row['completed_at'] if crawl_row else None

        # Flight counts
        total_row = conn.execute("SELECT COUNT(*) AS cnt FROM flights").fetchone()
        new_row = conn.execute("SELECT COUNT(*) AS cnt FROM flights WHERE is_new = 1").fetchone()

        # Email configured?
        email_configured = bool(config.SMTP_USERNAME and config.SMTP_PASSWORD)

        # Get scheduler times
        try:
            import scheduler as sched
            sched_status = sched.get_status()
        except Exception:
            sched_status = {"last_crawl_time": None, "next_crawl_time": None}

        return jsonify({
            "last_crawl_time": sched_status.get("last_crawl_time") or last_crawl,
            "next_crawl_time": sched_status.get("next_crawl_time"),
            "total_flights": total_row['cnt'] if total_row else 0,
            "new_flights": new_row['cnt'] if new_row else 0,
            "email_configured": email_configured,
        })
    except Exception as e:
        logger.error("Error fetching status: %s", e)
        return jsonify({"error": "Failed to fetch status"}), 500


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------

@api.route('/refresh', methods=['POST'])
def trigger_refresh():
    """Trigger an immediate crawl via the scheduler."""
    import scheduler as sched
    sched.trigger_refresh()
    logger.info("Manual refresh requested via API")
    return jsonify({"message": "Refresh triggered"})


# ---------------------------------------------------------------------------
# Email Settings
# ---------------------------------------------------------------------------

@api.route('/email-settings', methods=['GET'])
def get_email_settings():
    """Return current email configuration status (no secrets)."""
    return jsonify({
        "configured": bool(config.SMTP_USERNAME and config.SMTP_PASSWORD),
        "smtp_server": config.SMTP_SERVER,
        "smtp_port": config.SMTP_PORT,
        "username": config.SMTP_USERNAME[:3] + "***" if config.SMTP_USERNAME else "",
    })


@api.route('/email-settings', methods=['POST'])
def update_email_settings():
    """Update email settings at runtime (also persists to .env)."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    username = data.get("smtp_username", "").strip()
    password = data.get("smtp_password", "").strip()

    if not username or not password:
        return jsonify({"error": "Both smtp_username and smtp_password are required"}), 400

    # Update runtime config
    config.SMTP_USERNAME = username
    config.SMTP_PASSWORD = password
    config.EMAIL_FROM = data.get("email_from", username).strip()

    # Persist to .env file
    import os
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")
    try:
        lines = []
        if os.path.exists(env_path):
            with open(env_path, "r") as f:
                lines = f.readlines()

        new_lines = []
        keys_written = set()
        for line in lines:
            key = line.split("=")[0].strip()
            if key == "SMTP_USERNAME":
                new_lines.append(f"SMTP_USERNAME={username}\n")
                keys_written.add("SMTP_USERNAME")
            elif key == "SMTP_PASSWORD":
                new_lines.append(f"SMTP_PASSWORD={password}\n")
                keys_written.add("SMTP_PASSWORD")
            elif key == "EMAIL_FROM":
                new_lines.append(f"EMAIL_FROM={config.EMAIL_FROM}\n")
                keys_written.add("EMAIL_FROM")
            else:
                new_lines.append(line)

        for key, val in [("SMTP_USERNAME", username), ("SMTP_PASSWORD", password), ("EMAIL_FROM", config.EMAIL_FROM)]:
            if key not in keys_written:
                new_lines.append(f"{key}={val}\n")

        with open(env_path, "w") as f:
            f.writelines(new_lines)
    except Exception as e:
        logger.warning("Could not persist email settings to .env: %s", e)

    logger.info("Email settings updated for %s", username)
    return jsonify({"message": "Email settings saved", "configured": True})
