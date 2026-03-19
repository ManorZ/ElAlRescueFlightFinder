import sqlite3
import os
import threading
import config

_local = threading.local()


def get_connection() -> sqlite3.Connection:
    """Get a thread-local database connection."""
    if not hasattr(_local, "connection") or _local.connection is None:
        os.makedirs(os.path.dirname(config.DATABASE_PATH), exist_ok=True)
        _local.connection = sqlite3.connect(config.DATABASE_PATH)
        _local.connection.row_factory = sqlite3.Row
        _local.connection.execute("PRAGMA journal_mode=WAL")
        _local.connection.execute("PRAGMA foreign_keys=ON")
    return _local.connection


def close_connection():
    """Close the thread-local database connection."""
    if hasattr(_local, "connection") and _local.connection is not None:
        _local.connection.close()
        _local.connection = None


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
    """)
    conn.commit()
