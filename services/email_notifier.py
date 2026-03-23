"""
Email notification service for El Al Rescue Flight Finder.

Sends email alerts when new flights match user-configured alerts,
and when news/operational status changes are detected.
"""

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape as html_escape

import config
from database import get_connection

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration check
# ---------------------------------------------------------------------------

def is_email_configured() -> bool:
    """Return True if SMTP credentials are present."""
    return bool(config.SMTP_USERNAME) and bool(config.SMTP_PASSWORD)


# ---------------------------------------------------------------------------
# Low-level send
# ---------------------------------------------------------------------------

def send_email(to_address: str, subject: str, html_body: str) -> bool:
    """Send a multipart (plain-text + HTML) email via STARTTLS.

    Returns True on success, False on failure.
    """
    if not is_email_configured():
        logger.warning("Email not configured - skipping send to %s", to_address)
        return False

    from_address = config.EMAIL_FROM if config.EMAIL_FROM else config.SMTP_USERNAME

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_address
    msg["To"] = to_address

    # Build a simple plain-text fallback by stripping tags naively
    plain_text = _html_to_plain(html_body)
    msg.attach(MIMEText(plain_text, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP(config.SMTP_SERVER, config.SMTP_PORT, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(config.SMTP_USERNAME, config.SMTP_PASSWORD)
            server.sendmail(from_address, [to_address], msg.as_string())
        logger.info("Email sent to %s: %s", to_address, subject)
        return True
    except smtplib.SMTPAuthenticationError:
        logger.error("SMTP authentication failed for %s", config.SMTP_USERNAME)
        return False
    except smtplib.SMTPConnectError as exc:
        logger.error("Could not connect to SMTP server %s:%s - %s",
                      config.SMTP_SERVER, config.SMTP_PORT, exc)
        return False
    except smtplib.SMTPException as exc:
        logger.error("SMTP error sending to %s: %s", to_address, exc)
        return False
    except OSError as exc:
        logger.error("Network error sending email: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Email builders
# ---------------------------------------------------------------------------

_ELAL_BLUE = "#003087"

_EMAIL_STYLE = f"""
<style>
    body {{ font-family: Arial, Helvetica, sans-serif; margin: 0; padding: 0; background: #f4f4f4; }}
    .header {{ background: {_ELAL_BLUE}; color: #ffffff; padding: 20px 24px; text-align: center; }}
    .header h1 {{ margin: 0; font-size: 22px; }}
    .content {{ background: #ffffff; padding: 24px; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 16px; }}
    th {{ background: {_ELAL_BLUE}; color: #ffffff; padding: 10px 12px; text-align: left; font-size: 14px; }}
    td {{ padding: 10px 12px; border-bottom: 1px solid #ddd; font-size: 14px; }}
    tr:nth-child(even) td {{ background: #f9f9f9; }}
    .footer {{ text-align: center; padding: 16px 24px; font-size: 12px; color: #888888; }}
</style>
"""


def build_flight_alert_email(flights: list[dict]) -> tuple[str, str]:
    """Build email content for new flight alerts.

    Parameters
    ----------
    flights : list of dict
        Each dict has keys: origin_city, origin_code, flight_number,
        flight_time, flight_date, seats_available.

    Returns
    -------
    (subject, html_body)
    """
    cities = sorted({f.get("origin_city", "Unknown") for f in flights})
    if len(cities) == 1:
        city_label = cities[0]
    else:
        city_label = "multiple cities"
    subject = f"New El Al Flight(s) Available from {city_label}"

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

    html_body = f"""\
<!DOCTYPE html>
<html>
<head><meta charset="utf-8">{_EMAIL_STYLE}</head>
<body>
<div class="header">
    <h1>El Al Rescue Flight Alert</h1>
</div>
<div class="content">
    <p>New rescue flight(s) to Tel Aviv (TLV) have been detected:</p>
    <table>
        <thead>
            <tr>
                <th>Origin</th>
                <th>Flight #</th>
                <th>Date</th>
                <th>Time</th>
                <th>Seats</th>
                <th>Price</th>
            </tr>
        </thead>
        <tbody>
{rows_html}
        </tbody>
    </table>
    <p style="margin-top:20px;">
        Visit the El Al website to book:
        <a href="https://www.elal.com">www.elal.com</a>
    </p>
</div>
<div class="footer">
    Sent by El Al Rescue Flight Finder
</div>
</body>
</html>"""

    return subject, html_body


def build_news_change_email(snapshot_data: dict) -> tuple[str, str]:
    """Build email content for a news/operational-status change.

    Parameters
    ----------
    snapshot_data : dict
        Keys: last_update_text, non_operational_destinations,
        recovery_flight_origins.

    Returns
    -------
    (subject, html_body)
    """
    last_update = snapshot_data.get("last_update_text", "Unknown")
    subject = f"El Al News Update - {last_update}"

    non_op = snapshot_data.get("non_operational_destinations", "")
    recovery = snapshot_data.get("recovery_flight_origins", "")

    sections_html = ""
    if non_op:
        sections_html += (
            "<h3 style='color:#c0392b;'>Non-Operational Destinations</h3>"
            f"<p>{html_escape(str(non_op))}</p>"
        )
    if recovery:
        sections_html += (
            f"<h3 style='color:{_ELAL_BLUE};'>Recovery Flight Origins</h3>"
            f"<p>{html_escape(str(recovery))}</p>"
        )

    html_body = f"""\
<!DOCTYPE html>
<html>
<head><meta charset="utf-8">{_EMAIL_STYLE}</head>
<body>
<div class="header">
    <h1>El Al Operational Update</h1>
</div>
<div class="content">
    <p><strong>Last update:</strong> {html_escape(str(last_update))}</p>
    {sections_html}
    <p style="margin-top:20px;">
        For full details visit:
        <a href="https://www.elal.com">www.elal.com</a>
    </p>
</div>
<div class="footer">
    Sent by El Al Rescue Flight Finder
</div>
</body>
</html>"""

    return subject, html_body


# ---------------------------------------------------------------------------
# Alert processing
# ---------------------------------------------------------------------------

def process_alerts(new_flights: list[dict]) -> int:
    """Match new flights against active alert configs and send emails.

    Parameters
    ----------
    new_flights : list of dict
        Each dict must include at least: id (flight DB id), origin_code,
        origin_city, flight_number, flight_time, flight_date, seats_available.

    Returns
    -------
    int
        Number of alert emails sent.
    """
    if not new_flights:
        return 0

    if not is_email_configured():
        logger.debug("Email not configured - skipping alert processing")
        return 0

    conn = get_connection()

    # Fetch active alert configs
    alert_rows = conn.execute(
        "SELECT id, destination_code, destination_city, trigger_date, email_address "
        "FROM alert_configs WHERE is_active = 1"
    ).fetchall()

    if not alert_rows:
        return 0

    # Fetch existing alert history to avoid duplicates
    existing_pairs: set[tuple[int, int]] = set()
    history_rows = conn.execute(
        "SELECT alert_config_id, flight_id FROM alert_history"
    ).fetchall()
    for row in history_rows:
        existing_pairs.add((row["alert_config_id"], row["flight_id"]))

    sent_count = 0

    for alert in alert_rows:
        alert_id = alert["id"]
        dest_code = alert["destination_code"]
        trigger_date = alert["trigger_date"]
        email_address = alert["email_address"]

        # Find matching flights for this alert
        matched = []
        for flight in new_flights:
            flight_id = flight.get("id")
            if flight_id is None:
                continue

            # destination_code in alert matches the flight's origin_code
            # (user wants flights FROM that destination TO Israel)
            if flight.get("origin_code") != dest_code:
                continue

            # flight_date must be on or after the trigger_date
            if flight.get("flight_date", "") < trigger_date:
                continue

            # Only alert for flights with available seats
            seats = flight.get("seats_available")
            if seats is None or seats <= 0:
                continue

            # Skip if already sent
            if (alert_id, flight_id) in existing_pairs:
                continue

            matched.append(flight)

        if not matched:
            continue

        # Build and send email
        subject, html_body = build_flight_alert_email(matched)
        success = send_email(email_address, subject, html_body)

        if success:
            # Record in alert_history
            for flight in matched:
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO alert_history (alert_config_id, flight_id) "
                        "VALUES (?, ?)",
                        (alert_id, flight["id"]),
                    )
                    existing_pairs.add((alert_id, flight["id"]))
                except Exception:
                    logger.exception("Failed to record alert history for alert %s, flight %s",
                                     alert_id, flight["id"])
            conn.commit()
            sent_count += 1
            logger.info("Alert email sent to %s for %d flight(s) from %s",
                         email_address, len(matched), dest_code)

    return sent_count


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

        # Check dedup
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _html_to_plain(html: str) -> str:
    """Crude HTML-to-plain-text conversion for the plain-text email part."""
    import re
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    text = re.sub(r"</tr>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</td>", " | ", text, flags=re.IGNORECASE)
    text = re.sub(r"</th>", " | ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
