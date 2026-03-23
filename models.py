from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime


@dataclass
class Flight:
    origin_code: str
    origin_city: str
    flight_number: str
    flight_time: str
    flight_date: str
    origin_country: Optional[str] = None
    destination_code: str = "TLV"
    seats_available: Optional[int] = None
    id: Optional[int] = None
    first_seen_at: Optional[str] = None
    last_seen_at: Optional[str] = None
    is_new: bool = True


@dataclass
class Destination:
    code: str
    city_name: str
    country_name: Optional[str] = None
    continent: Optional[str] = None
    is_operational: bool = True
    is_recovery_flight_origin: bool = False
    last_updated: Optional[str] = None


@dataclass
class AlertConfig:
    destination_code: str
    destination_city: str
    trigger_date: str
    email_address: str
    id: Optional[int] = None
    is_active: bool = True
    created_at: Optional[str] = None


@dataclass
class NewsSnapshot:
    content_hash: str
    last_update_text: Optional[str] = None
    non_operational_destinations: Optional[str] = None
    recovery_flight_origins: Optional[str] = None
    raw_content: Optional[str] = None
    id: Optional[int] = None
    captured_at: Optional[str] = None


@dataclass
class CrawlLog:
    started_at: str
    status: str = "running"
    completed_at: Optional[str] = None
    flights_found: int = 0
    new_flights: int = 0
    errors: Optional[str] = None
    id: Optional[int] = None


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
