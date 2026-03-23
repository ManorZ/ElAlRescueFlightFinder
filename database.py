import sqlite3
import os
import threading
import config

_local = threading.local()


def get_connection() -> sqlite3.Connection:
    """Get a thread-local database connection."""
    if not hasattr(_local, "connection") or _local.connection is None:
        os.makedirs(os.path.dirname(config.DATABASE_PATH), exist_ok=True)
        _local.connection = sqlite3.connect(config.DATABASE_PATH, timeout=10)
        _local.connection.row_factory = sqlite3.Row
        _local.connection.execute("PRAGMA journal_mode=WAL")
        _local.connection.execute("PRAGMA busy_timeout=5000")
        _local.connection.execute("PRAGMA foreign_keys=ON")
    return _local.connection


def close_connection():
    """Close the thread-local database connection."""
    if hasattr(_local, "connection") and _local.connection is not None:
        _local.connection.close()
        _local.connection = None


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, col_type: str):
    """Add a column to a table if it doesn't already exist."""
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
        conn.commit()
    except sqlite3.OperationalError as e:
        if "duplicate column name" not in str(e).lower():
            raise


def init_db():
    """Create all tables if they don't exist."""
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS flights (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            origin_code TEXT NOT NULL,
            origin_city TEXT NOT NULL,
            origin_country TEXT,
            destination_code TEXT DEFAULT 'TLV',
            flight_number TEXT NOT NULL,
            flight_time TEXT NOT NULL,
            flight_date TEXT NOT NULL,
            seats_available INTEGER,
            first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_new INTEGER DEFAULT 1,
            UNIQUE(flight_number, flight_date)
        );

        CREATE TABLE IF NOT EXISTS destinations (
            code TEXT PRIMARY KEY,
            city_name TEXT NOT NULL,
            country_name TEXT,
            continent TEXT,
            is_operational INTEGER DEFAULT 1,
            is_recovery_flight_origin INTEGER DEFAULT 0,
            last_updated TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS alert_configs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            destination_code TEXT NOT NULL,
            destination_city TEXT NOT NULL,
            trigger_date TEXT NOT NULL,
            email_address TEXT NOT NULL,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS alert_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_config_id INTEGER REFERENCES alert_configs(id),
            flight_id INTEGER REFERENCES flights(id),
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(alert_config_id, flight_id)
        );

        CREATE TABLE IF NOT EXISTS news_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content_hash TEXT NOT NULL,
            last_update_text TEXT,
            non_operational_destinations TEXT,
            recovery_flight_origins TEXT,
            raw_content TEXT,
            captured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS crawl_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TIMESTAMP,
            completed_at TIMESTAMP,
            flights_found INTEGER,
            new_flights INTEGER,
            errors TEXT,
            status TEXT
        );

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
    """)
    conn.commit()

    # Migrations for existing tables
    _add_column_if_missing(conn, "crawl_log", "crawl_type", "TEXT DEFAULT 'seats'")
    _add_column_if_missing(conn, "alert_configs", "max_price", "REAL")
    _add_column_if_missing(conn, "alert_configs", "price_currency", "TEXT DEFAULT 'USD'")
