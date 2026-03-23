import os
from dotenv import load_dotenv

load_dotenv()

# API Endpoints
SEAT_AVAILABILITY_URL = "https://www.elal.com/api/SeatAvailability/lang/eng/flights"
DESTINATIONS_URL = "https://www.elal.com/api/destinations/lang/eng/direct"
NEWS_CONTENT_URL = "https://www.elal.com/api/contentPage/path/x00xengx00xabout-elalx00xnewsx00xrecent-updates"

# Database
DATABASE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "flights.db")

# Email / SMTP
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
EMAIL_FROM = os.getenv("EMAIL_FROM", "")

# Scheduling
POLL_INTERVAL_MINUTES = int(os.getenv("POLL_INTERVAL_MINUTES", "60"))
NEWS_POLL_INTERVAL_MINUTES = int(os.getenv("NEWS_POLL_INTERVAL_MINUTES", "30"))
PRICE_POLL_INTERVAL_MINUTES = int(os.getenv("PRICE_POLL_INTERVAL_MINUTES", "360"))
PRICE_MARKET = os.getenv("PRICE_MARKET", "US")

# Flask
FLASK_PORT = int(os.getenv("FLASK_PORT", "5000"))
FLASK_HOST = "127.0.0.1"

# Request settings
REQUEST_TIMEOUT = 30
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
